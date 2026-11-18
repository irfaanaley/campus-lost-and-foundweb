"""Campus Lost & Found - API (Vercel + Postgres). Single handler, every route lives under /api/index?r=<route>."""
import http.server, json, os, hashlib, secrets, time, base64, re, threading, sys
from urllib.parse import urlparse, parse_qs
try:
    import psycopg2, psycopg2.extras
    IMPERR = ''
except Exception as e:
    psycopg2 = None; IMPERR = str(e)
SEC = '; Secure'
LOCK = threading.Lock(); FAILS = {}
IDT = re.compile(r'\s*INSERT INTO (items|claims|lost|notifs|categories|locations|support|activity)\b')

class R:
    def __init__(s, cur, lid=None): s.cur, s.lastrowid = cur, lid
    def fetchone(s): return s.cur.fetchone()
    def __iter__(s): return iter(s.cur.fetchall())
class DB:
    c = None
    def execute(s, sql, a=()):
        if psycopg2 is None: raise RuntimeError('psycopg2 missing: ' + IMPERR)
        sql = sql.replace('?', '%s'); ret = bool(IDT.match(sql))
        if ret: sql += ' RETURNING id'
        for attempt in (1, 2):
            try:
                if s.c is None or s.c.closed: s.c = psycopg2.connect(os.environ.get('DATABASE_URL') or os.environ['POSTGRES_URL'])
                cur = s.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute(sql, a)
                lid = None
                if ret:
                    r = cur.fetchone(); lid = r['id'] if r else None
                return R(cur, lid)
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                s.c = None
                if attempt == 2: raise
            except Exception:
                if s.c: s.c.rollback()
                raise
    def commit(s): s.c.commit()
db = DB()

CATS0 = ['Mobile Phones', 'Laptops', 'Books', 'Bags', 'Wallets', 'Keys', 'ID Cards', 'Electronics', 'Accessories', 'Other Items']
LOCS0 = [('Library', 'Building'), ('Cafeteria', 'Facility'), ('Labs', 'Building'), ('Classrooms', 'Building'), ('Parking', 'Outdoor'), ('Sports Ground', 'Outdoor'),
         ('Admin Block', 'Building'), ('Computer Science Department', 'Department'), ('Engineering Department', 'Department'), ('Business School', 'Department'), ('Main Gate', 'Outdoor')]
LEGACY = {'Wallet': 'Wallets', 'Bag': 'Bags', 'ID Card': 'ID Cards', 'Other': 'Other Items'}
DEFSET = {'site_name': 'Campus Lost & Found', 'contact_email': 'lostandfound@campus.edu', 'contact_phone': '+92 300 0000000', 'contact_address': 'Student Affairs Office, Admin Block'}
SCHEMA = [
 "CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY, username TEXT UNIQUE, \"full\" TEXT, role TEXT, contact TEXT, salt TEXT, hash TEXT)",
 "CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER, exp DOUBLE PRECISION)",
 "CREATE TABLE IF NOT EXISTS items(id SERIAL PRIMARY KEY, name TEXT, category TEXT, location TEXT, date TEXT, finder TEXT, status TEXT, color TEXT, brand TEXT, marks TEXT, photo TEXT)",
 "CREATE TABLE IF NOT EXISTS claims(id SERIAL PRIMARY KEY, item_id INTEGER, uid INTEGER, claimant TEXT, contact TEXT, color TEXT, brand TEXT, marks TEXT, proof TEXT, cnic TEXT, selfie TEXT, extra TEXT, status TEXT, detail TEXT, code TEXT, created DOUBLE PRECISION, decided DOUBLE PRECISION)",
 "CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY, data BYTEA)",
 "ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT",
 "ALTER TABLE users ADD COLUMN IF NOT EXISTS active BOOLEAN DEFAULT TRUE",
 "ALTER TABLE users ADD COLUMN IF NOT EXISTS created DOUBLE PRECISION",
 "ALTER TABLE users ADD COLUMN IF NOT EXISTS prefs TEXT",
 "ALTER TABLE items ADD COLUMN IF NOT EXISTS description TEXT",
 "ALTER TABLE items ADD COLUMN IF NOT EXISTS uid INTEGER",
 "ALTER TABLE items ADD COLUMN IF NOT EXISTS created DOUBLE PRECISION",
 "CREATE TABLE IF NOT EXISTS lost(id SERIAL PRIMARY KEY, uid INTEGER, name TEXT, description TEXT, category TEXT, location TEXT, date TEXT, contact TEXT, photo TEXT, status TEXT, created DOUBLE PRECISION)",
 "CREATE TABLE IF NOT EXISTS notifs(id SERIAL PRIMARY KEY, uid INTEGER, kind TEXT, text TEXT, seen BOOLEAN DEFAULT FALSE, created DOUBLE PRECISION)",
 "CREATE TABLE IF NOT EXISTS categories(id SERIAL PRIMARY KEY, name TEXT UNIQUE)",
 "CREATE TABLE IF NOT EXISTS locations(id SERIAL PRIMARY KEY, name TEXT UNIQUE, kind TEXT)",
 "CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT)",
 "CREATE TABLE IF NOT EXISTS support(id SERIAL PRIMARY KEY, uid INTEGER, kind TEXT, subject TEXT, message TEXT, status TEXT, created DOUBLE PRECISION)",
 "CREATE TABLE IF NOT EXISTS activity(id SERIAL PRIMARY KEY, text TEXT, created DOUBLE PRECISION)"]
INIT = []
def init():
    if INIT: return
    for q in SCHEMA: db.execute(q)
    if not db.execute("SELECT 1 FROM categories").fetchone():
        for n in CATS0: db.execute("INSERT INTO categories(name) VALUES(?) ON CONFLICT DO NOTHING", (n,))
    if not db.execute("SELECT 1 FROM locations").fetchone():
        for n, k in LOCS0: db.execute("INSERT INTO locations(name,kind) VALUES(?,?) ON CONFLICT DO NOTHING", (n, k))
    for a, b in LEGACY.items(): db.execute("UPDATE items SET category=? WHERE category=?", (b, a))
    db.execute("UPDATE items SET location=l.name FROM locations l WHERE lower(items.location)=lower(l.name) AND items.location<>l.name")
    for k, v in DEFSET.items(): db.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT DO NOTHING", (k, v))
    db.commit(); INIT.append(1)

# ---------- helpers ----------
def now(): return time.time()
def hp(pw, salt): return hashlib.pbkdf2_hmac('sha256', pw.encode(), bytes.fromhex(salt), 200000).hex()
def clean(s, n=200): return re.sub(r'\s+', ' ', str(s or '')).strip()[:n]
def toks(s): return re.findall(r'[a-z0-9]+', s.lower())
def fuzzy(a, b):
    a, b = a.strip().lower(), b.strip().lower()
    if not a or not b: return False
    if a == b or a in b or b in a: return True
    return any(len(t) >= 3 and t in toks(b) for t in toks(a))
def save_img(data):
    m = re.match(r'^data:image/jpeg;base64,(.+)$', data or '')
    if not m: return None
    raw = base64.b64decode(m.group(1))
    if len(raw) > 2_500_000 or raw[:2] != b'\xff\xd8': return None
    n = secrets.token_hex(16) + '.jpg'
    db.execute("INSERT INTO files VALUES(?,?)", (n, psycopg2.Binary(raw))); return n
def drop_files(*names):
    for n in names:
        if n: db.execute("DELETE FROM files WHERE name=?", (n,))
BAD_TOKENS = {'sex','sexy','porn','porno','nude','nudes','nsfw','xxx','dick','cock','penis','vagina','pussy','boob','boobs','fuck','fucking','shit','bitch','asshole','slut','whore','rape','condom','condum','condoms','dildo','dildos','vibrator','lund','chut','gaand','harami','kutta','kamina','randi','bhosdi','madarchod','behenchod'}
BAD_PARTS = ('dildo', 'condom', 'condum', 'vibrator', 'porn', 'fuck', 'nigg')
def bad_text(label, v, minlen=2, strict=True):
    """Returns an error string if v is not a sensible description, else None."""
    t = toks(v)
    if len(v) < minlen or not re.search(r'[^\W\d_]', v): return '%s must be real text (letters, at least %d characters).' % (label, minlen)
    if re.search(r'(.)\1{3,}', v) or (strict and not re.match(r"^[\w \-'&.,()/+#:]+$", v)): return '%s contains invalid characters or looks like random text.' % label
    if any(x in BAD_TOKENS for x in t) or any(b in x for x in t for b in BAD_PARTS): return '%s contains inappropriate words. Only genuine campus lost and found items can be reported.' % label
    return None
def rows(sql, *a): return [dict(r) for r in db.execute(sql, a)]
def one(sql, *a):
    r = db.execute(sql, a).fetchone(); return dict(r) if r else None
def isadm(u): return bool(u) and u['role'] in ('admin', 'owner')
def log(text): db.execute("INSERT INTO activity(text,created) VALUES(?,?)", (text, now()))
def notify(uid, kind, text):
    if not uid: return
    u = one("SELECT prefs FROM users WHERE id=?", uid)
    if not u: return
    try: pr = json.loads(u['prefs'] or '{}')
    except Exception: pr = {}
    key = {'claim': 'claims', 'item': 'items', 'match': 'matches'}.get(kind)
    if key and pr.get(key) is False: return
    db.execute("INSERT INTO notifs(uid,kind,text,seen,created) VALUES(?,?,?,FALSE,?)", (uid, kind, text, now()))
def notify_admins(kind, text):
    for a in rows("SELECT id FROM users WHERE role IN('admin','owner') AND active IS NOT FALSE"): notify(a['id'], kind, text)
def sid(n): return '%04d' % n
def today_limit(): return time.strftime('%Y-%m-%d', time.gmtime(now() + 86400))
def common_fields(b, label):
    """Validates name / description / category / location / date shared by found and lost reports."""
    name, desc, cat, loc, date = clean(b.get('name'), 60), clean(b.get('description'), 500), clean(b.get('category'), 40), clean(b.get('location'), 60), clean(b.get('date'), 10)
    e = bad_text(label + ' name', name, 3) or bad_text('Description', desc, 3, False)
    if e: return None, e
    if not one("SELECT 1 x FROM categories WHERE name=?", cat): return None, 'Please choose a valid category.'
    lr = one("SELECT name FROM locations WHERE lower(name)=lower(?)", loc)
    if not lr: return None, 'Please choose a valid campus location.'
    if not re.match(r'^\d{4}-\d{2}-\d{2}$', date) or date > today_limit(): return None, 'Please choose a valid date (not in the future).'
    return dict(name=name, description=desc, category=cat, location=lr['name'], date=date), None
def private_fields(b):
    need = [clean(b.get(k)) for k in ('color', 'brand', 'marks')]
    if not all(need): return None, 'Please fill in the private details (color, brand / model, marks). They are used to verify the real owner.'
    for lbl, v in zip(('Color', 'Brand / model', 'Marks / contents'), need):
        e = bad_text(lbl, v, 2)
        if e: return None, e
    return need, None
def pub_settings(): return {r['k']: r['v'] for r in db.execute("SELECT k,v FROM settings")}
def parse_prefs(u):
    try: pr = json.loads(u.get('prefs') or '{}')
    except Exception: pr = {}
    return {k: pr.get(k) is not False for k in ('claims', 'items', 'matches')}
def month_key(ts): return time.strftime('%Y-%m', time.gmtime(ts + 5 * 3600))

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def out(self, code, obj=None, raw=None, ctype='application/json', cookie=None):
        body = raw if raw is not None else json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff'); self.send_header('Cache-Control', 'no-store')
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers(); self.wfile.write(body)
    def user(self):
        m = re.search(r'sid=([0-9a-f]+)', self.headers.get('Cookie', ''))
        if not m: return None
        s = one("SELECT * FROM sessions WHERE token=? AND exp>?", m.group(1), now())
        if not s: return None
        u = one("SELECT * FROM users WHERE id=?", s['uid'])
        return u if u and u.get('active') is not False else None
    def do_GET(self): self.safe('GET')
    def do_POST(self): self.safe('POST')
    def do_DELETE(self): self.safe('DELETE')
    def safe(self, m):
        try: self.go(m)
        except Exception as e:
            try: self.out(500, {'error': 'Server error: %s' % e})
            except Exception: pass
    def go(self, method):
        path = self.path.split('?')[0]; q = parse_qs(urlparse(self.path).query)
        if 'r' in q: path = '/api/' + q['r'][0]
        if path == '/api/ping':
            info = {'python': sys.version[:6], 'psycopg2': 'ok' if psycopg2 else IMPERR, 'db_env': bool(os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL'))}
            try: init(); info['db'] = 'ok'
            except Exception as e: info['db'] = str(e)[:300]
            return self.out(200, info)
        body = {}
        if method == 'POST':
            try: body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            except Exception: return self.out(400, {'error': 'Bad request'})
        with LOCK:
            try: self.route(method, path, body)
            except (ValueError, IndexError): self.out(400, {'error': 'Bad request.'})
            except Exception as e: self.out(500, {'error': 'Server error: %s' % e})

    # ---------- routing ----------
    def route(self, m, p, b):
        if not (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')): return self.out(500, {'error': 'Database not connected. In Vercel open Storage, connect a Postgres database, then Redeploy.'})
        init()
        u = self.user(); P = p.split('/')[2:]; r0 = P[0] if P else ''
        has_admin = db.execute("SELECT 1 FROM users WHERE role IN('admin','owner')").fetchone() is not None
        if p == '/api/me':
            if not u: return self.out(200, {'user': None, 'setup': not has_admin})
            un = db.execute("SELECT COUNT(*) n FROM notifs WHERE uid=? AND seen=FALSE", (u['id'],)).fetchone()['n']
            pend = db.execute("SELECT COUNT(*) n FROM claims WHERE status='PENDING'").fetchone()['n'] if isadm(u) else 0
            return self.out(200, {'user': {k: u[k] for k in ('id', 'username', 'full', 'role', 'contact', 'email', 'created')}, 'setup': False, 'unread': un, 'pending': pend, 'prefs': parse_prefs(u), 'meta': self.meta()})
        if p in ('/api/setup', '/api/register') and m == 'POST':
            role = 'user'
            if p == '/api/setup':
                if has_admin: return self.out(403, {'error': 'Owner setup is closed.'})
                role = 'owner'
            return self.make_user(b, role)
        if p == '/api/login' and m == 'POST':
            un = clean(b.get('username')).lower(); key = (un, (self.headers.get('X-Forwarded-For') or self.client_address[0]).split(',')[0].strip()); f = FAILS.get(key, [0, 0])
            if now() < f[1]: return self.out(429, {'error': 'Too many attempts. Wait %d seconds.' % (f[1] - now())})
            r = one("SELECT * FROM users WHERE username=?", un)
            if not r or hp(str(b.get('password', '')), r['salt']) != r['hash']:
                f[0] += 1
                if f[0] >= 5: f = [0, now() + 30]
                FAILS[key] = f; return self.out(401, {'error': 'Incorrect username or password.'})
            if r.get('active') is False: return self.out(403, {'error': 'This account has been deactivated. Please contact the admin.'})
            FAILS.pop(key, None); t = secrets.token_hex(24)
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (t, r['id'], now() + 86400 * 7)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800%s' % (t, SEC))
        if not u: return self.out(401, {'error': 'Please log in.'})
        if p == '/api/logout':
            db.execute("DELETE FROM sessions WHERE uid=?", (u['id'],)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=; Max-Age=0; Path=/; HttpOnly' + SEC)
        fn = getattr(self, 'r_' + r0, None)
        if not fn: return self.out(404, {'error': 'Not found'})
        fn(m, P, b, u)

    def meta(self):
        return {'categories': [r['name'] for r in db.execute("SELECT name FROM categories ORDER BY name")], 'locations': rows("SELECT name,kind FROM locations ORDER BY name"), 'settings': pub_settings()}
    def r_meta(self, m, P, b, u): self.out(200, self.meta())

    def r_file(self, m, P, b, u):   # item / lost photos: any member. Claim proofs: admins and the claimant only
        n = os.path.basename(P[1]); ok = db.execute("SELECT 1 FROM items WHERE photo=? UNION SELECT 1 FROM lost WHERE photo=?", (n, n)).fetchone()
        if not ok:
            c = db.execute("SELECT uid FROM claims WHERE cnic=? OR selfie=? OR extra=?", (n, n, n)).fetchone()
            ok = c and (isadm(u) or c['uid'] == u['id'])
        fr = db.execute("SELECT data FROM files WHERE name=?", (n,)).fetchone() if ok else None
        return self.out(200, raw=bytes(fr['data']), ctype='image/jpeg') if fr else self.out(404, {'error': 'Not found'})

    # ---------- found items ----------
    def own_item(self, it, u): return it['uid'] == u['id'] or (it['uid'] is None and it['finder'] == u['full'])
    def r_items(self, m, P, b, u):
        if len(P) == 1:
            if m == 'GET': return self.out(200, rows("SELECT id,name,description,category,location,date,finder,status,photo,uid FROM items ORDER BY id"))
            if m != 'POST': return self.out(405, {'error': 'Method not allowed.'})
            f, e = common_fields(b, 'Item')
            if e: return self.out(400, {'error': e})
            pv, e = private_fields(b)
            if e: return self.out(400, {'error': e})
            ph = None
            if b.get('photo'):
                ph = save_img(b['photo'])
                if not ph: return self.out(400, {'error': 'Invalid photo. Upload a normal JPG/PNG picture under 2.5 MB, or leave it empty.'})
            c = db.execute("INSERT INTO items(name,description,category,location,date,finder,uid,status,color,brand,marks,photo,created) VALUES(?,?,?,?,?,?,?,'FOUND',?,?,?,?,?)",
                (f['name'], f['description'], f['category'], f['location'], f['date'], u['full'], u['id'], *pv, ph, now()))
            log('%s reported a found item: %s (ID %s)' % (u['full'], f['name'], sid(c.lastrowid)))
            for l in rows("SELECT id,uid,name FROM lost WHERE status='OPEN' AND uid<>?", u['id']):
                if fuzzy(f['name'], l['name']): notify(l['uid'], 'match', 'A found item that may match your lost report "%s" was added: %s (ID %s, %s).' % (l['name'], f['name'], sid(c.lastrowid), f['location']))
            db.commit(); return self.out(200, {'id': c.lastrowid})
        it = one("SELECT * FROM items WHERE id=?", int(P[1]))
        if not it: return self.out(404, {'error': 'Item not found.'})
        if not (isadm(u) or (self.own_item(it, u) and it['status'] == 'FOUND')): return self.out(403, {'error': 'You can only change your own report while the item is still waiting for its owner.'})
        if m == 'DELETE':
            names = [it['photo']]
            for c in db.execute("SELECT cnic,selfie,extra FROM claims WHERE item_id=?", (it['id'],)): names += [c['cnic'], c['selfie'], c['extra']]
            db.execute("DELETE FROM claims WHERE item_id=?", (it['id'],)); db.execute("DELETE FROM items WHERE id=?", (it['id'],)); drop_files(*names)
            if not self.own_item(it, u): notify(it['uid'], 'item', 'Your found report "%s" was removed by an admin.' % it['name'])
            log('%s removed item %s (%s)' % (u['full'], sid(it['id']), it['name'])); db.commit(); return self.out(200, {'ok': 1})
        if m == 'POST':
            f, e = common_fields(b, 'Item')
            if e: return self.out(400, {'error': e})
            db.execute("UPDATE items SET name=?,description=?,category=?,location=?,date=? WHERE id=?", (f['name'], f['description'], f['category'], f['location'], f['date'], it['id']))
            log('%s edited item %s' % (u['full'], sid(it['id']))); db.commit(); return self.out(200, {'ok': 1})
        self.out(405, {'error': 'Method not allowed.'})

    # ---------- lost reports ----------
    def r_lost(self, m, P, b, u):
        if len(P) == 1:
            if m == 'GET':
                if isadm(u): return self.out(200, rows("SELECT l.*,u.\"full\" reporter FROM lost l LEFT JOIN users u ON u.id=l.uid ORDER BY l.id DESC"))
                return self.out(200, rows("SELECT * FROM lost WHERE uid=? ORDER BY id DESC", u['id']))
            if m != 'POST': return self.out(405, {'error': 'Method not allowed.'})
            f, e = common_fields(b, 'Lost item')
            if e: return self.out(400, {'error': e})
            ct = clean(b.get('contact'), 80)
            if len(ct) < 5: return self.out(400, {'error': 'Please enter a phone number or email where you can be reached.'})
            ph = None
            if b.get('photo'):
                ph = save_img(b['photo'])
                if not ph: return self.out(400, {'error': 'Invalid photo. Upload a normal JPG/PNG picture under 2.5 MB, or leave it empty.'})
            c = db.execute("INSERT INTO lost(uid,name,description,category,location,date,contact,photo,status,created) VALUES(?,?,?,?,?,?,?,?,'OPEN',?)", (u['id'], f['name'], f['description'], f['category'], f['location'], f['date'], ct, ph, now()))
            log('%s reported a lost item: %s' % (u['full'], f['name']))
            hits = [i for i in rows("SELECT id,name,location,uid,finder FROM items WHERE status='FOUND'") if fuzzy(f['name'], i['name']) and not self.own_item(i, u)]
            if hits: notify(u['id'], 'match', 'Good news: %d found item(s) may match "%s", e.g. %s (ID %s). Check Search Item.' % (len(hits), f['name'], hits[0]['name'], sid(hits[0]['id'])))
            db.commit(); return self.out(200, {'id': c.lastrowid, 'matches': len(hits)})
        l = one("SELECT * FROM lost WHERE id=?", int(P[1]))
        if not l: return self.out(404, {'error': 'Report not found.'})
        if not (isadm(u) or l['uid'] == u['id']): return self.out(403, {'error': 'Not allowed.'})
        if m == 'DELETE':
            db.execute("DELETE FROM lost WHERE id=?", (l['id'],)); drop_files(l['photo']); log('%s removed lost report %s' % (u['full'], l['name'])); db.commit(); return self.out(200, {'ok': 1})
        if m == 'POST' and len(P) > 2 and P[2] == 'resolve':
            ns = 'OPEN' if l['status'] == 'RESOLVED' else 'RESOLVED'
            db.execute("UPDATE lost SET status=? WHERE id=?", (ns, l['id'])); db.commit(); return self.out(200, {'status': ns})
        if m == 'POST':
            f, e = common_fields(b, 'Lost item')
            if e: return self.out(400, {'error': e})
            ct = clean(b.get('contact'), 80) or l['contact']
            db.execute("UPDATE lost SET name=?,description=?,category=?,location=?,date=?,contact=? WHERE id=?", (f['name'], f['description'], f['category'], f['location'], f['date'], ct, l['id'])); db.commit(); return self.out(200, {'ok': 1})
        self.out(405, {'error': 'Method not allowed.'})

    # ---------- claims ----------
    def r_claims(self, m, P, b, u):
        if m == 'POST' and len(P) == 1:
            it = one("SELECT * FROM items WHERE id=?", int(b.get('item_id') or 0))
            if not it: return self.out(404, {'error': 'No item found with this ID.'})
            if it['status'] != 'FOUND': return self.out(400, {'error': 'This item is no longer available.'})
            if self.own_item(it, u): return self.out(400, {'error': 'You reported this item yourself, so you cannot claim it.'})
            if db.execute("SELECT 1 FROM claims WHERE item_id=? AND uid=? AND status IN('PENDING','APPROVED')", (it['id'], u['id'])).fetchone():
                return self.out(400, {'error': 'You already have an active claim for this item.'})
            a = [clean(b.get(k)) for k in ('color', 'brand', 'marks')]
            if not all(a): return self.out(400, {'error': 'Please answer color, brand/model and marks.'})
            cn, se, ex = save_img(b.get('cnic')), save_img(b.get('selfie')), save_img(b.get('extra')) if b.get('extra') else None
            if not cn or not se: return self.out(400, {'error': 'Owner proof is required: a photo of your CNIC / student card and a clear photo of yourself.'})
            c = db.execute("INSERT INTO claims(item_id,uid,claimant,contact,color,brand,marks,proof,cnic,selfie,extra,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,'PENDING',?)",
                (it['id'], u['id'], u['full'], clean(b.get('contact'), 60) or u['contact'], *a, clean(b.get('proof')), cn, se, ex, now()))
            log('%s submitted claim %d for %s' % (u['full'], c.lastrowid, it['name']))
            notify_admins('claim', 'New claim #%d by %s for "%s" (ID %s) is waiting for review.' % (c.lastrowid, u['full'], it['name'], sid(it['id'])))
            db.commit(); return self.out(200, {'id': c.lastrowid})
        if len(P) > 1 and P[1] == 'mine':
            return self.out(200, rows("SELECT c.id,c.status,c.detail,c.code,c.item_id,c.created,c.decided,i.name,i.category,i.location,i.photo FROM claims c JOIN items i ON i.id=c.item_id WHERE c.uid=? ORDER BY c.id DESC", u['id']))
        if not isadm(u): return self.out(403, {'error': 'Admins only.'})
        if m == 'GET':
            out = rows("SELECT c.*,i.name iname,i.category icategory,i.photo iphoto,i.color icolor,i.brand ibrand,i.marks imarks,i.finder,i.location,i.date FROM claims c JOIN items i ON i.id=c.item_id ORDER BY c.id")
            for c in out: c['score'] = sum(fuzzy(c[x], c['i' + x]) for x in ('color', 'brand', 'marks')); c.pop('uid')
            return self.out(200, out)
        c = one("SELECT * FROM claims WHERE id=?", int(P[1]))
        if not c: return self.out(404, {'error': 'Claim not found.'})
        it = one("SELECT * FROM items WHERE id=?", c['item_id'])
        if c['uid'] == u['id']: return self.out(403, {'error': 'You cannot decide or hand over your own claim. Ask another admin.'})
        if P[2] == 'decide':
            if c['status'] != 'PENDING': return self.out(400, {'error': 'Already decided.'})
            if b.get('approve') and it['status'] != 'FOUND': return self.out(400, {'error': 'Item is not available.'})
            if b.get('approve'):
                code = '%06d' % secrets.randbelow(10**6)
                db.execute("UPDATE claims SET status='APPROVED',detail='Approved. Bring your original ID to the admin office and show the handover code.',code=?,decided=? WHERE id=?", (code, now(), c['id']))
                db.execute("UPDATE items SET status='CLAIMED' WHERE id=?", (it['id'],))
                for o in rows("SELECT id,uid FROM claims WHERE item_id=? AND status='PENDING' AND id<>?", it['id'], c['id']):
                    db.execute("UPDATE claims SET status='REJECTED',detail='Another claim for this item was approved.',decided=? WHERE id=?", (now(), o['id']))
                    notify(o['uid'], 'claim', 'Your claim #%d for "%s" was rejected: another claim was approved.' % (o['id'], it['name']))
                notify(c['uid'], 'claim', 'Your claim #%d for "%s" was APPROVED. Visit the admin office with your original ID and handover code %s.' % (c['id'], it['name'], code))
                notify(it['uid'], 'item', 'A claim for your found item "%s" was approved. The owner will collect it from the admin office.' % it['name'])
                log('%s approved claim %d (%s)' % (u['full'], c['id'], it['name']))
            else:
                note = clean(b.get('note')) or 'details did not match.'
                db.execute("UPDATE claims SET status='REJECTED',detail=?,decided=? WHERE id=?", ('Rejected: ' + note, now(), c['id']))
                notify(c['uid'], 'claim', 'Your claim #%d for "%s" was REJECTED. Reason: %s' % (c['id'], it['name'], note))
                log('%s rejected claim %d (%s)' % (u['full'], c['id'], it['name']))
            db.commit(); return self.out(200, {'ok': 1})
        if P[2] == 'handover':   # item leaves only when code + ID are verified in person
            if c['status'] != 'APPROVED' or clean(b.get('code')) != c['code'] or not b.get('id_checked'): return self.out(400, {'error': 'Wrong code, or ID not confirmed.'})
            db.execute("UPDATE claims SET status='RETURNED',detail='Item handed over to the verified owner.' WHERE id=?", (c['id'],))
            db.execute("UPDATE items SET status='RETURNED' WHERE id=?", (c['item_id'],))
            notify(c['uid'], 'item', '"%s" was handed over to you. Thank you!' % it['name']); notify(it['uid'], 'item', 'Your found item "%s" was returned to its owner. Thank you for helping!' % it['name'])
            log('%s handed over %s to %s' % (u['full'], it['name'], c['claimant'])); db.commit(); return self.out(200, {'ok': 1})
        self.out(404, {'error': 'Not found'})

    # ---------- notifications ----------
    def r_notifs(self, m, P, b, u):
        if m == 'GET': return self.out(200, rows("SELECT id,kind,text,seen,created FROM notifs WHERE uid=? ORDER BY id DESC LIMIT 100", u['id']))
        if m == 'POST' and len(P) > 1 and P[1] == 'read':
            if b.get('all'): db.execute("UPDATE notifs SET seen=TRUE WHERE uid=?", (u['id'],))
            else: db.execute("UPDATE notifs SET seen=TRUE WHERE uid=? AND id=?", (u['id'], int(b.get('id') or 0)))
            db.commit(); return self.out(200, {'ok': 1})
        self.out(405, {'error': 'Method not allowed.'})

    # ---------- categories & campus locations ----------
    def r_locations(self, m, P, b, u): self.lookup('locations', m, P, b, u)
    def r_categories(self, m, P, b, u): self.lookup('categories', m, P, b, u)
    def lookup(self, tbl, m, P, b, u):
        loc = tbl == 'locations'; col = 'location' if loc else 'category'
        if m == 'GET':
            out = rows("SELECT * FROM %s ORDER BY name" % tbl)
            f = {}; l = {}
            for r in rows("SELECT %s k,status FROM items" % col):
                k = (r['k'] or '').lower(); f[k] = f.get(k, 0) + 1
            for r in rows("SELECT %s k,status FROM lost" % col):
                k = (r['k'] or '').lower(); l[k] = l.get(k, 0) + 1
            for r in out: r['found'] = f.get(r['name'].lower(), 0); r['lost'] = l.get(r['name'].lower(), 0)
            return self.out(200, out)
        if not isadm(u): return self.out(403, {'error': 'Admins only.'})
        kinds = ('Building', 'Department', 'Facility', 'Outdoor')
        if m == 'POST' and len(P) == 1:
            n = clean(b.get('name'), 40); e = bad_text('Name', n, 2)
            if e: return self.out(400, {'error': e})
            if one("SELECT 1 x FROM %s WHERE lower(name)=lower(?)" % tbl, n): return self.out(400, {'error': 'That name already exists.'})
            if loc: db.execute("INSERT INTO locations(name,kind) VALUES(?,?)", (n, b.get('kind') if b.get('kind') in kinds else 'Building'))
            else: db.execute("INSERT INTO categories(name) VALUES(?)", (n,))
            log('%s added %s "%s"' % (u['full'], col, n)); db.commit(); return self.out(200, {'ok': 1})
        row = one("SELECT * FROM %s WHERE id=?" % tbl, int(P[1]))
        if not row: return self.out(404, {'error': 'Not found.'})
        if m == 'DELETE':
            used = db.execute("SELECT (SELECT COUNT(*) FROM items WHERE lower(%s)=lower(?)) + (SELECT COUNT(*) FROM lost WHERE lower(%s)=lower(?)) n" % (col, col), (row['name'], row['name'])).fetchone()['n']
            if used: return self.out(400, {'error': 'Cannot delete: %d report(s) still use "%s". Rename it instead.' % (used, row['name'])})
            db.execute("DELETE FROM %s WHERE id=?" % tbl, (row['id'],)); log('%s deleted %s "%s"' % (u['full'], col, row['name'])); db.commit(); return self.out(200, {'ok': 1})
        if m == 'POST':
            n = clean(b.get('name'), 40); e = bad_text('Name', n, 2)
            if e: return self.out(400, {'error': e})
            if one("SELECT 1 x FROM %s WHERE lower(name)=lower(?) AND id<>?" % tbl, n, row['id']): return self.out(400, {'error': 'That name already exists.'})
            db.execute("UPDATE %s SET name=? WHERE id=?" % tbl, (n, row['id']))
            if loc and b.get('kind') in kinds: db.execute("UPDATE locations SET kind=? WHERE id=?", (b['kind'], row['id']))
            db.execute("UPDATE items SET %s=? WHERE lower(%s)=lower(?)" % (col, col), (n, row['name'])); db.execute("UPDATE lost SET %s=? WHERE lower(%s)=lower(?)" % (col, col), (n, row['name']))
            db.commit(); return self.out(200, {'ok': 1})
        self.out(405, {'error': 'Method not allowed.'})

    # ---------- users ----------
    def r_users(self, m, P, b, u):
        if not isadm(u): return self.out(403, {'error': 'Admins only.'})
        if m == 'GET': return self.out(200, rows("SELECT username,\"full\",role,contact,email,active,created,(SELECT COUNT(*) FROM claims WHERE uid=users.id) n FROM users ORDER BY id"))
        if m == 'POST' and len(P) == 1: return self.make_user(b, 'admin')
        t = one("SELECT * FROM users WHERE username=?", P[1])
        if not t or t['id'] == u['id'] or t['role'] == 'owner': return self.out(400, {'error': 'Not allowed on this account.'})
        if m == 'DELETE':
            if t['role'] == 'admin' and u['role'] != 'owner': return self.out(403, {'error': 'Only the owner can delete an admin.'})
            db.execute("DELETE FROM users WHERE id=?", (t['id'],)); db.execute("DELETE FROM sessions WHERE uid=?", (t['id'],)); log('%s deleted account %s' % (u['full'], t['username']))
        elif 'active' in b:
            if t['role'] == 'admin' and u['role'] != 'owner': return self.out(403, {'error': 'Only the owner can change an admin.'})
            db.execute("UPDATE users SET active=? WHERE id=?", (bool(b['active']), t['id']))
            if not b['active']: db.execute("DELETE FROM sessions WHERE uid=?", (t['id'],))
            log('%s %s account %s' % (u['full'], 'activated' if b['active'] else 'deactivated', t['username']))
        else:
            nr = b.get('role')
            if nr == 'user' and u['role'] != 'owner': return self.out(403, {'error': 'Only the owner can remove admin rights.'})
            if nr not in ('user', 'admin'): return self.out(400, {'error': 'Unknown role.'})
            db.execute("UPDATE users SET role=? WHERE id=?", (nr, t['id'])); log('%s changed %s to %s' % (u['full'], t['username'], nr))
        db.commit(); return self.out(200, {'ok': 1})

    def make_user(self, b, role):
        cu = self.user()
        if role == 'admin' and not isadm(cu): return self.out(403, {'error': 'Only admins can create admins.'})
        full, un, ct, em, pw = clean(b.get('full'), 60), clean(b.get('username')).lower(), clean(b.get('contact'), 60), clean(b.get('email'), 80).lower(), str(b.get('password', ''))
        if not full or not un or not pw: return self.out(400, {'error': 'Please fill in all the fields.'})
        if role == 'user' and not ct: return self.out(400, {'error': 'Please enter a phone number or student ID.'})
        if (role == 'user' or em) and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', em): return self.out(400, {'error': 'Please enter a valid email address.'})
        if not re.match(r'^[a-z0-9_.]{3,20}$', un): return self.out(400, {'error': 'Username must be 3-20 characters: letters, numbers, _ or .'})
        if len(pw) < 6: return self.out(400, {'error': 'Password must be at least 6 characters.'})
        if pw != b.get('password2'): return self.out(400, {'error': 'The two passwords do not match.'})
        if db.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone(): return self.out(400, {'error': 'That username is already taken.'})
        s = secrets.token_hex(16)
        db.execute("INSERT INTO users(username,\"full\",role,contact,email,active,created,salt,hash) VALUES(?,?,?,?,?,TRUE,?,?,?)", (un, full, role, ct, em, now(), s, hp(pw, s)))
        log('New %s account: %s' % ('student' if role == 'user' else role, full)); db.commit()
        self.out(200, {'ok': 1, 'username': un})

    # ---------- own profile, password, preferences, account ----------
    def r_profile(self, m, P, b, u):
        full, ct, em = clean(b.get('full'), 60), clean(b.get('contact'), 60), clean(b.get('email'), 80).lower()
        if not full: return self.out(400, {'error': 'Name cannot be empty.'})
        if em and not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', em): return self.out(400, {'error': 'Please enter a valid email address.'})
        db.execute("UPDATE users SET \"full\"=?,contact=?,email=? WHERE id=?", (full, ct, em, u['id'])); db.commit(); self.out(200, {'ok': 1})
    def r_password(self, m, P, b, u):
        if hp(str(b.get('old', '')), u['salt']) != u['hash']: return self.out(400, {'error': 'Your current password is incorrect.'})
        n = str(b.get('new', ''))
        if len(n) < 6: return self.out(400, {'error': 'New password must be at least 6 characters.'})
        if n != b.get('new2'): return self.out(400, {'error': 'The two new passwords do not match.'})
        s = secrets.token_hex(16); db.execute("UPDATE users SET salt=?,hash=? WHERE id=?", (s, hp(n, s), u['id'])); db.commit(); self.out(200, {'ok': 1})
    def r_prefs(self, m, P, b, u):
        db.execute("UPDATE users SET prefs=? WHERE id=?", (json.dumps({k: bool(b.get(k)) for k in ('claims', 'items', 'matches')}), u['id'])); db.commit(); self.out(200, {'ok': 1})
    def r_account(self, m, P, b, u):
        if m != 'DELETE': return self.out(405, {'error': 'Method not allowed.'})
        if u['role'] == 'owner': return self.out(400, {'error': 'The owner account cannot be deleted.'})
        db.execute("DELETE FROM sessions WHERE uid=?", (u['id'],)); db.execute("DELETE FROM notifs WHERE uid=?", (u['id'],)); db.execute("DELETE FROM users WHERE id=?", (u['id'],)); db.commit()
        self.out(200, {'ok': 1}, cookie='sid=; Max-Age=0; Path=/; HttpOnly' + SEC)

    # ---------- site settings & support ----------
    def r_settings(self, m, P, b, u):
        if m == 'GET': return self.out(200, pub_settings())
        if not isadm(u): return self.out(403, {'error': 'Admins only.'})
        for k in DEFSET:
            if k in b:
                v = clean(b[k], 120)
                if not v: return self.out(400, {'error': 'Settings cannot be empty.'})
                db.execute("INSERT INTO settings(k,v) VALUES(?,?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (k, v))
        log('%s updated website settings' % u['full']); db.commit(); self.out(200, pub_settings())
    def r_support(self, m, P, b, u):
        if m == 'GET':
            if not isadm(u): return self.out(403, {'error': 'Admins only.'})
            return self.out(200, rows("SELECT s.*,u.\"full\" sender FROM support s LEFT JOIN users u ON u.id=s.uid ORDER BY s.id DESC LIMIT 100"))
        if m == 'POST' and len(P) > 2 and P[2] == 'close':
            if not isadm(u): return self.out(403, {'error': 'Admins only.'})
            db.execute("UPDATE support SET status='CLOSED' WHERE id=?", (int(P[1]),)); db.commit(); return self.out(200, {'ok': 1})
        kind, sub, msg = b.get('kind'), clean(b.get('subject'), 100), clean(b.get('message'), 1000)
        if kind not in ('support', 'problem'): return self.out(400, {'error': 'Choose a request type.'})
        if len(sub) < 3 or len(msg) < 10: return self.out(400, {'error': 'Please add a subject and a message of at least 10 characters.'})
        db.execute("INSERT INTO support(uid,kind,subject,message,status,created) VALUES(?,?,?,?,'OPEN',?)", (u['id'], kind, sub, msg, now()))
        notify_admins('system', '%s from %s: %s' % ('Problem report' if kind == 'problem' else 'Support request', u['full'], sub)); db.commit(); self.out(200, {'ok': 1})

    # ---------- analytics / admin dashboard ----------
    def r_stats(self, m, P, b, u):
        if not isadm(u): return self.out(403, {'error': 'Admins only.'})
        items = rows("SELECT category,location,date,status FROM items"); lost = rows("SELECT category,location,date,status FROM lost"); claims = rows("SELECT status,created FROM claims")
        ts = now(); keys = []
        for i in range(5, -1, -1):
            lt = time.gmtime(ts + 5 * 3600); y, mo = lt.tm_year, lt.tm_mon - i
            while mo < 1: mo += 12; y -= 1
            keys.append('%04d-%02d' % (y, mo))
        mon = {k: {'m': k, 'found': 0, 'lost': 0, 'claims': 0} for k in keys}
        for r in items:
            if (r['date'] or '')[:7] in mon: mon[r['date'][:7]]['found'] += 1
        for r in lost:
            if (r['date'] or '')[:7] in mon: mon[r['date'][:7]]['lost'] += 1
        for r in claims:
            if r['created'] and month_key(r['created']) in mon: mon[month_key(r['created'])]['claims'] += 1
        def group(key, known):
            g = {k: {'name': k, 'found': 0, 'lost': 0} for k in known}; low = {k.lower(): k for k in known}
            for src, fld in ((items, 'found'), (lost, 'lost')):
                for r in src:
                    n = low.get((r[key] or '').lower(), r[key] or 'Unknown'); g.setdefault(n, {'name': n, 'found': 0, 'lost': 0})[fld] += 1
            return sorted(g.values(), key=lambda x: -(x['found'] + x['lost']))
        cs = lambda *s: sum(1 for c in claims if c['status'] in s)
        us = rows("SELECT role,active FROM users")
        self.out(200, {'found': len(items), 'lost': len(lost), 'claims': len(claims), 'approved': cs('APPROVED', 'RETURNED'), 'pending': cs('PENDING'), 'rejected': cs('REJECTED'), 'returned': cs('RETURNED'),
            'items_waiting': sum(1 for r in items if r['status'] == 'FOUND'), 'lost_open': sum(1 for r in lost if r['status'] == 'OPEN'),
            'users': len(us), 'students': sum(1 for r in us if r['role'] == 'user'), 'admins': sum(1 for r in us if r['role'] != 'user'), 'inactive': sum(1 for r in us if r['active'] is False),
            'monthly': [mon[k] for k in keys], 'by_category': group('category', [r['name'] for r in rows("SELECT name FROM categories")]), 'by_location': group('location', [r['name'] for r in rows("SELECT name FROM locations")]),
            'activity': rows("SELECT text,created FROM activity ORDER BY id DESC LIMIT 15"), 'tickets': db.execute("SELECT COUNT(*) n FROM support WHERE status='OPEN'").fetchone()['n']})

handler = H   # Vercel looks for this name (api/index.py subclasses it)

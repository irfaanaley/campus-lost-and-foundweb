"""Campus Lost & Found - web app (Vercel + Postgres)."""
import http.server, json, os, hashlib, secrets, time, base64, re, threading
from urllib.parse import urlparse, parse_qs
import sys
try:
    import psycopg2, psycopg2.extras
    IMPERR = ''
except Exception as e:
    psycopg2 = None; IMPERR = str(e)
SEC = '; Secure'
LOCK = threading.Lock(); FAILS = {}
class R:
    def __init__(s, cur, lid=None): s.cur, s.lastrowid = cur, lid
    def fetchone(s): return s.cur.fetchone()
    def __iter__(s): return iter(s.cur.fetchall())
class DB:
    c = None
    def execute(s, sql, a=()):
        if psycopg2 is None: raise RuntimeError('psycopg2 missing: ' + IMPERR)
        sql = sql.replace('?', '%s'); ret = bool(re.match(r'\s*INSERT INTO (items|claims)', sql))
        if ret: sql += ' RETURNING id'
        for attempt in (1, 2):
            try:
                if s.c is None or s.c.closed: s.c = psycopg2.connect(os.environ.get('DATABASE_URL') or os.environ['POSTGRES_URL'])
                cur = s.c.cursor(cursor_factory=psycopg2.extras.RealDictCursor); cur.execute(sql, a)
                return R(cur, cur.fetchone()['id'] if ret else None)
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                s.c = None
                if attempt == 2: raise
            except Exception:
                if s.c: s.c.rollback()
                raise
    def commit(s): s.c.commit()
db = DB()
INIT = []
def init():
    if INIT: return
    for q in ["CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY, username TEXT UNIQUE, \"full\" TEXT, role TEXT, contact TEXT, salt TEXT, hash TEXT)",
      "CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER, exp DOUBLE PRECISION)",
      "CREATE TABLE IF NOT EXISTS items(id SERIAL PRIMARY KEY, name TEXT, category TEXT, location TEXT, date TEXT, finder TEXT, status TEXT, color TEXT, brand TEXT, marks TEXT, photo TEXT)",
      "CREATE TABLE IF NOT EXISTS claims(id SERIAL PRIMARY KEY, item_id INTEGER, uid INTEGER, claimant TEXT, contact TEXT, color TEXT, brand TEXT, marks TEXT, proof TEXT, cnic TEXT, selfie TEXT, extra TEXT, status TEXT, detail TEXT, code TEXT, created DOUBLE PRECISION, decided DOUBLE PRECISION)",
      "CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY, data BYTEA)"]: db.execute(q)
    db.commit()
    INIT.append(1)

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
BAD_TOKENS = {'sex','sexy','porn','porno','nude','nudes','nsfw','xxx','dick','cock','penis','vagina','pussy','boob','boobs','fuck','fucking','shit','bitch','asshole','slut','whore','rape','condom','condum','condoms','dildo','dildos','vibrator','lund','chut','gaand','harami','kutta','kamina','randi','bhosdi','madarchod','behenchod'}
BAD_PARTS = ('dildo', 'condom', 'condum', 'vibrator', 'porn', 'fuck', 'nigg')
CATS = ('Wallet', 'Electronics', 'Books', 'ID Card', 'Bag', 'Other')
def bad_text(label, v, minlen=2):
    """Returns an error string if v is not a sensible item description, else None."""
    t = toks(v)
    if len(v) < minlen or not re.search(r'[^\W\d_]', v): return '%s must be a real word (letters, at least %d characters).' % (label, minlen)
    if re.search(r'(.)\1{3,}', v) or not re.match(r"^[\w \-'&.,()/+#:]+$", v): return '%s contains invalid characters or looks like random text.' % label
    if any(x in BAD_TOKENS for x in t) or any(b in x for x in t for b in BAD_PARTS): return '%s contains inappropriate words. Only genuine lost items can be reported.' % label
    return None
def rows(sql, *a): return [dict(r) for r in db.execute(sql, a)]
def isadm(u): return u and u['role'] in ('admin', 'owner')

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
        s = db.execute("SELECT * FROM sessions WHERE token=? AND exp>?", (m.group(1), time.time())).fetchone()
        if not s: return None
        u = db.execute("SELECT * FROM users WHERE id=?", (s['uid'],)).fetchone()
        return dict(u) if u else None
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
            except Exception as e: self.out(500, {'error': 'Server error: %s' % e})

    def route(self, m, p, b):
        if not (os.environ.get('DATABASE_URL') or os.environ.get('POSTGRES_URL')): return self.out(500, {'error': 'Database not connected. In Vercel open Storage, connect a Postgres database, then Redeploy.'})
        init()
        u = self.user(); P = p.split('/')[2:]
        has_admin = db.execute("SELECT 1 FROM users WHERE role IN('admin','owner')").fetchone() is not None
        if p == '/api/me': return self.out(200, {'user': u and {k: u[k] for k in ('username', 'full', 'role', 'contact')}, 'setup': not has_admin})
        if p in ('/api/setup', '/api/register') and m == 'POST':
            role = 'user'
            if p == '/api/setup':
                if has_admin: return self.out(403, {'error': 'Owner setup is closed.'})
                role = 'owner'
            return self.make_user(b, role)
        if p == '/api/login' and m == 'POST':
            un = clean(b.get('username')).lower(); key = (un, (self.headers.get('X-Forwarded-For') or self.client_address[0]).split(',')[0].strip()); f = FAILS.get(key, [0, 0])
            if time.time() < f[1]: return self.out(429, {'error': 'Too many attempts. Wait %d seconds.' % (f[1] - time.time())})
            r = db.execute("SELECT * FROM users WHERE username=?", (un,)).fetchone()
            if not r or hp(str(b.get('password', '')), r['salt']) != r['hash']:
                f[0] += 1
                if f[0] >= 5: f = [0, time.time() + 30]
                FAILS[key] = f; return self.out(401, {'error': 'Incorrect username or password.'})
            FAILS.pop(key, None); t = secrets.token_hex(24)
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (t, r['id'], time.time() + 86400 * 7)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800%s' % (t, SEC))
        if not u: return self.out(401, {'error': 'Please log in.'})
        if p == '/api/logout': 
            db.execute("DELETE FROM sessions WHERE uid=?", (u['id'],)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=; Max-Age=0; Path=/; HttpOnly' + SEC)
        if P[0] == 'file':   # item photos: any member. Claim proofs: admins and the claimant only
            n = os.path.basename(P[1]); ok = db.execute("SELECT 1 FROM items WHERE photo=?", (n,)).fetchone()
            if not ok:
                c = db.execute("SELECT uid FROM claims WHERE cnic=? OR selfie=? OR extra=?", (n, n, n)).fetchone()
                ok = c and (isadm(u) or c['uid'] == u['id'])
            fr = db.execute("SELECT data FROM files WHERE name=?", (n,)).fetchone() if ok else None
            return self.out(200, raw=bytes(fr['data']), ctype='image/jpeg') if fr else self.out(404, {'error': 'Not found'})
        if P[0] == 'items':
            if m == 'GET': return self.out(200, rows("SELECT id,name,category,location,date,finder,status,photo FROM items ORDER BY id"))
            if m == 'DELETE':   # admin removes a wrong / inappropriate report completely
                if not isadm(u): return self.out(403, {'error': 'Admins only.'})
                try: iid = int(P[1])
                except Exception: return self.out(400, {'error': 'Bad item id.'})
                it = db.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
                if not it: return self.out(404, {'error': 'Item not found.'})
                names = [it['photo']]
                for c in db.execute("SELECT cnic,selfie,extra FROM claims WHERE item_id=?", (iid,)): names += [c['cnic'], c['selfie'], c['extra']]
                db.execute("DELETE FROM claims WHERE item_id=?", (iid,)); db.execute("DELETE FROM items WHERE id=?", (iid,))
                for n in names:
                    if n: db.execute("DELETE FROM files WHERE name=?", (n,))
                db.commit(); return self.out(200, {'ok': 1})
            if m != 'POST': return self.out(405, {'error': 'Method not allowed.'})
            need = [clean(b.get(k)) for k in ('name', 'location', 'color', 'brand', 'marks')]
            if not all(need): return self.out(400, {'error': 'Please fill in every field, including the private details.'})
            for lbl, v, ml in zip(('Item name', 'Location', 'Color', 'Brand / model', 'Marks / contents'), need, (3, 2, 2, 2, 2)):
                e = bad_text(lbl, v, ml)
                if e: return self.out(400, {'error': e})
            cat = clean(b.get('category'), 30)
            if cat not in CATS: return self.out(400, {'error': 'Please choose a valid category.'})
            ph = None
            if b.get('photo'):
                ph = save_img(b['photo'])
                if not ph: return self.out(400, {'error': 'Invalid photo. Upload a normal JPG/PNG picture under 2.5 MB, or leave it empty.'})
            c = db.execute("INSERT INTO items(name,category,location,date,finder,status,color,brand,marks,photo) VALUES(?,?,?,?,?,'FOUND',?,?,?,?)",
                (need[0], cat, need[1], clean(b.get('date'), 10), u['full'], need[2], need[3], need[4], ph)); db.commit()
            return self.out(200, {'id': c.lastrowid})
        if P[0] == 'claims':
            if m == 'POST' and len(P) == 1:
                if u['role'] != 'user': return self.out(403, {'error': 'Only user accounts can submit claims.'})
                it = db.execute("SELECT * FROM items WHERE id=?", (int(b.get('item_id') or 0),)).fetchone()
                if not it: return self.out(404, {'error': 'No item found with this ID.'})
                if it['status'] != 'FOUND': return self.out(400, {'error': 'This item is no longer available.'})
                if db.execute("SELECT 1 FROM claims WHERE item_id=? AND uid=? AND status='PENDING'", (it['id'], u['id'])).fetchone():
                    return self.out(400, {'error': 'You already have a pending claim for this item.'})
                a = [clean(b.get(k)) for k in ('color', 'brand', 'marks')]
                if not all(a): return self.out(400, {'error': 'Please answer color, brand/model and marks.'})
                cn, se, ex = save_img(b.get('cnic')), save_img(b.get('selfie')), save_img(b.get('extra')) if b.get('extra') else None
                if not cn or not se: return self.out(400, {'error': 'Owner proof is required: a photo of your CNIC / student card and a clear photo of yourself.'})
                c = db.execute("INSERT INTO claims(item_id,uid,claimant,contact,color,brand,marks,proof,cnic,selfie,extra,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,'PENDING',?)",
                    (it['id'], u['id'], u['full'], clean(b.get('contact'), 60) or u['contact'], *a, clean(b.get('proof')), cn, se, ex, time.time())); db.commit()
                return self.out(200, {'id': c.lastrowid})
            if len(P) > 1 and P[1] == 'mine':
                return self.out(200, rows("SELECT c.id,c.status,c.detail,c.code,c.item_id,i.name FROM claims c JOIN items i ON i.id=c.item_id WHERE c.uid=? ORDER BY c.id DESC", u['id']))
            if not isadm(u): return self.out(403, {'error': 'Admins only.'})
            if m == 'GET':
                out = rows("SELECT c.*,i.name iname,i.color icolor,i.brand ibrand,i.marks imarks,i.finder,i.location,i.date FROM claims c JOIN items i ON i.id=c.item_id ORDER BY c.id")
                for c in out:
                    c['score'] = sum(fuzzy(c[x], c['i' + x]) for x in ('color', 'brand', 'marks')); c.pop('uid')
                return self.out(200, out)
            c = db.execute("SELECT * FROM claims WHERE id=?", (int(P[1]),)).fetchone()
            if not c: return self.out(404, {'error': 'Claim not found.'})
            if P[2] == 'decide':
                if c['status'] != 'PENDING': return self.out(400, {'error': 'Already decided.'})
                it = db.execute("SELECT * FROM items WHERE id=?", (c['item_id'],)).fetchone()
                if b.get('approve') and it['status'] != 'FOUND': return self.out(400, {'error': 'Item is not available.'})
                if b.get('approve'):
                    code = '%06d' % secrets.randbelow(10**6)
                    db.execute("UPDATE claims SET status='APPROVED',detail='Approved. Bring your original ID to the admin office and show the handover code.',code=?,decided=? WHERE id=?", (code, time.time(), c['id']))
                    db.execute("UPDATE items SET status='CLAIMED' WHERE id=?", (it['id'],))
                    db.execute("UPDATE claims SET status='REJECTED',detail='Another claim for this item was approved.',decided=? WHERE item_id=? AND status='PENDING'", (time.time(), it['id']))
                else: db.execute("UPDATE claims SET status='REJECTED',detail=?,decided=? WHERE id=?", ('Rejected: ' + (clean(b.get('note')) or 'details did not match.'), time.time(), c['id']))
                db.commit(); return self.out(200, {'ok': 1})
            if P[2] == 'handover':   # item leaves only when code + ID are verified in person
                if c['status'] != 'APPROVED' or clean(b.get('code')) != c['code'] or not b.get('id_checked'):
                    return self.out(400, {'error': 'Wrong code, or ID not confirmed.'})
                db.execute("UPDATE claims SET status='RETURNED',detail='Item handed over to the verified owner.' WHERE id=?", (c['id'],))
                db.execute("UPDATE items SET status='RETURNED' WHERE id=?", (c['item_id'],)); db.commit(); return self.out(200, {'ok': 1})
        if P[0] == 'users':
            if not isadm(u): return self.out(403, {'error': 'Admins only.'})
            if m == 'GET': return self.out(200, rows("SELECT username,\"full\",role,contact,(SELECT COUNT(*) FROM claims WHERE uid=users.id) n FROM users"))
            if m == 'POST' and len(P) == 1: return self.make_user(b, 'admin')
            t = db.execute("SELECT * FROM users WHERE username=?", (P[1],)).fetchone()
            if not t or t['id'] == u['id'] or t['role'] == 'owner': return self.out(400, {'error': 'Not allowed on this account.'})
            if m == 'DELETE':
                if t['role'] == 'admin' and u['role'] != 'owner': return self.out(403, {'error': 'Only the owner can delete an admin.'})
                db.execute("DELETE FROM users WHERE id=?", (t['id'],)); db.execute("DELETE FROM sessions WHERE uid=?", (t['id'],))
            else:
                nr = b.get('role')
                if nr == 'user' and u['role'] != 'owner': return self.out(403, {'error': 'Only the owner can remove admin rights.'})
                if nr not in ('user', 'admin'): return self.out(400, {'error': 'Unknown role.'})
                db.execute("UPDATE users SET role=? WHERE id=?", (nr, t['id']))
            db.commit(); return self.out(200, {'ok': 1})
        self.out(404, {'error': 'Not found'})

    def make_user(self, b, role):
        if role == 'admin' and not isadm(self.user()): return self.out(403, {'error': 'Only admins can create admins.'})
        full, un, ct, pw = clean(b.get('full')), clean(b.get('username')).lower(), clean(b.get('contact'), 60), str(b.get('password', ''))
        if not full or not un or not pw: return self.out(400, {'error': 'Please fill in all the fields.'})
        if role == 'user' and not ct: return self.out(400, {'error': 'Please enter a phone number or student ID.'})
        if not re.match(r'^[a-z0-9_.]{3,20}$', un): return self.out(400, {'error': 'Username must be 3-20 characters: letters, numbers, _ or .'})
        if len(pw) < 6: return self.out(400, {'error': 'Password must be at least 6 characters.'})
        if pw != b.get('password2'): return self.out(400, {'error': 'The two passwords do not match.'})
        if db.execute("SELECT 1 FROM users WHERE username=?", (un,)).fetchone(): return self.out(400, {'error': 'That username is already taken.'})
        s = secrets.token_hex(16); db.execute("INSERT INTO users(username,\"full\",role,contact,salt,hash) VALUES(?,?,?,?,?,?)", (un, full, role, ct, s, hp(pw, s))); db.commit()
        self.out(200, {'ok': 1, 'username': un})

handler = H   # Vercel looks for this name

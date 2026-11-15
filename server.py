"""Campus Lost & Found - web app. Run: python server.py  ->  http://localhost:8000  (Python 3.8+, no packages needed)"""
import http.server, sqlite3, json, os, hashlib, secrets, time, base64, re, threading, mimetypes
ROOT = os.path.dirname(os.path.abspath(__file__)); DATA = os.path.join(ROOT, 'data'); UP = os.path.join(DATA, 'uploads')
os.makedirs(UP, exist_ok=True)
db = sqlite3.connect(os.path.join(DATA, 'clf.db'), check_same_thread=False); db.row_factory = sqlite3.Row
LOCK = threading.Lock(); FAILS = {}
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE, full TEXT, role TEXT, contact TEXT, salt TEXT, hash TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, uid INTEGER, exp REAL);
CREATE TABLE IF NOT EXISTS items(id INTEGER PRIMARY KEY, name, category, location, date, finder, status, color, brand, marks, photo);
CREATE TABLE IF NOT EXISTS claims(id INTEGER PRIMARY KEY, item_id, uid, claimant, contact, color, brand, marks, proof, cnic, selfie, extra,
  status, detail, code, created REAL, decided REAL);""")
if not db.execute("SELECT 1 FROM items").fetchone() and not os.environ.get('CLF_NO_DEMO'):
    for r in [(998,'Brown Wallet','Wallet','Cafeteria','2026-11-12','Bilal','RETURNED','Brown','Hidesign','Initials R.K. inside'),
      (1001,'Black Wallet','Wallet','Library','2026-11-15','Ali','FOUND','Black','Fossil','CNIC and 500 Rs inside'),
      (1002,'Blue Water Bottle','Other','Gym','2026-11-13','Hina','FOUND','Blue','Milton','Dent near the lid'),
      (1003,'Casio Calculator','Electronics','Room C-12','2026-11-14','Usman','FOUND','Grey','Casio fx-991','Name sticker on the back'),
      (1004,'USB 32GB','Electronics','Computer Lab','2026-11-14','Zara','FOUND','Black','SanDisk','Folder named DB Project'),
      (1005,'Student ID Card','ID Card','Admin Block','2026-11-11','Kamran','FOUND','White','Student card','Photo of a boy with glasses')]:
        db.execute("INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,NULL)", r)
    db.execute("INSERT INTO claims(id,item_id,uid,claimant,contact,color,brand,marks,proof,status,created) VALUES(5001,1001,0,'Ahmed (demo)','0300-1234567','Black','Fossil','CNIC and 500 Rs inside','','PENDING',?)", (time.time(),))
    db.execute("INSERT INTO claims(id,item_id,uid,claimant,contact,color,brand,marks,proof,status,created) VALUES(5002,1004,0,'Sara (demo)','CS-2023-045','Red','Kingston','Cat sticker','','PENDING',?)", (time.time(),))
    db.commit()

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
    open(os.path.join(UP, n), 'wb').write(raw); return n
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
    def do_GET(self): self.go('GET')
    def do_POST(self): self.go('POST')
    def do_DELETE(self): self.go('DELETE')
    def go(self, method):
        path = self.path.split('?')[0]
        if method == 'GET' and not path.startswith('/api/'):
            f = os.path.join(ROOT, 'static', 'index.html' if path == '/' else os.path.basename(path))
            if not os.path.isfile(f): return self.out(404, {'error': 'Not found'})
            return self.out(200, raw=open(f, 'rb').read(), ctype=mimetypes.guess_type(f)[0] or 'text/plain')
        body = {}
        if method == 'POST':
            try: body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            except Exception: return self.out(400, {'error': 'Bad request'})
        with LOCK:
            try: self.route(method, path, body)
            except Exception as e: self.out(500, {'error': 'Server error: %s' % e})

    def route(self, m, p, b):
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
            un = clean(b.get('username')).lower(); key = (un, self.client_address[0]); f = FAILS.get(key, [0, 0])
            if time.time() < f[1]: return self.out(429, {'error': 'Too many attempts. Wait %d seconds.' % (f[1] - time.time())})
            r = db.execute("SELECT * FROM users WHERE username=?", (un,)).fetchone()
            if not r or hp(str(b.get('password', '')), r['salt']) != r['hash']:
                f[0] += 1
                if f[0] >= 5: f = [0, time.time() + 30]
                FAILS[key] = f; return self.out(401, {'error': 'Incorrect username or password.'})
            FAILS.pop(key, None); t = secrets.token_hex(24)
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (t, r['id'], time.time() + 86400 * 7)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=%s; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800' % t)
        if not u: return self.out(401, {'error': 'Please log in.'})
        if p == '/api/logout': 
            db.execute("DELETE FROM sessions WHERE uid=?", (u['id'],)); db.commit()
            return self.out(200, {'ok': 1}, cookie='sid=; Max-Age=0; Path=/')
        if P[0] == 'file':   # item photos: any member. Claim proofs: admins and the claimant only
            n = os.path.basename(P[1]); ok = db.execute("SELECT 1 FROM items WHERE photo=?", (n,)).fetchone()
            if not ok:
                c = db.execute("SELECT uid FROM claims WHERE cnic=? OR selfie=? OR extra=?", (n, n, n)).fetchone()
                ok = c and (isadm(u) or c['uid'] == u['id'])
            f = os.path.join(UP, n)
            return self.out(200, raw=open(f, 'rb').read(), ctype='image/jpeg') if ok and os.path.isfile(f) else self.out(404, {'error': 'Not found'})
        if P[0] == 'items':
            if m == 'GET': return self.out(200, rows("SELECT id,name,category,location,date,finder,status,photo FROM items ORDER BY id"))
            need = [clean(b.get(k)) for k in ('name', 'location', 'color', 'brand', 'marks')]
            if not all(need): return self.out(400, {'error': 'Please fill in every field, including the private details.'})
            ph = save_img(b['photo']) if b.get('photo') else None
            c = db.execute("INSERT INTO items(name,category,location,date,finder,status,color,brand,marks,photo) VALUES(?,?,?,?,?,'FOUND',?,?,?,?)",
                (need[0], clean(b.get('category'), 30), need[1], clean(b.get('date'), 10), u['full'], need[2], need[3], need[4], ph)); db.commit()
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
            if m == 'GET': return self.out(200, rows("SELECT username,full,role,contact,(SELECT COUNT(*) FROM claims WHERE uid=users.id) n FROM users"))
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
        s = secrets.token_hex(16); db.execute("INSERT INTO users(username,full,role,contact,salt,hash) VALUES(?,?,?,?,?,?)", (un, full, role, ct, s, hp(pw, s))); db.commit()
        self.out(200, {'ok': 1, 'username': un})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000)); print('Campus Lost & Found running at http://localhost:%d' % port)
    http.server.ThreadingHTTPServer(('0.0.0.0', port), H).serve_forever()

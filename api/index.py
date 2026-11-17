import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core
from _core import H

# Root cause of the "site keeps freezing" problem: a finished GET request leaves its connection
# "idle in transaction", which keeps table locks. The next cold start then waits forever in
# init() on ALTER TABLE ... Fixes: short timeouts, release locks after every request, skip the
# DDL once the schema is current, and clear stale idle-in-transaction sessions before DDL.
_pg = _core.psycopg2
if _pg is not None:
    _orig = _pg.connect
    def _connect(*a, **k):
        k.setdefault('connect_timeout', 10)
        k.setdefault('keepalives', 1); k.setdefault('keepalives_idle', 20)
        k.setdefault('keepalives_interval', 5); k.setdefault('keepalives_count', 3)
        c = _orig(*a, **k)
        try:
            cur = c.cursor()
            cur.execute("SET statement_timeout = 20000")
            cur.execute("SET idle_in_transaction_session_timeout = 15000")
            c.commit(); cur.close()
        except Exception:
            try: c.rollback()
            except Exception: pass
        return c
    _pg.connect = _connect

_init_full = _core.init
SCHEMA_V = '2'
def _init():
    if _core.INIT: return
    db = _core.db
    try:
        r = db.execute("SELECT v FROM settings WHERE k='schema_v'").fetchone()
        db.c.rollback()
        if r and r['v'] == SCHEMA_V:
            _core.INIT.append(1); return
    except Exception:
        try: db.c.rollback()
        except Exception: pass
    try:   # free locks held by stale sessions of dead/old instances
        db.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() "
                   "AND pid<>pg_backend_pid() AND state='idle in transaction' AND state_change < now() - interval '5 seconds'")
        db.commit()
    except Exception:
        try: db.c.rollback()
        except Exception: pass
    _init_full()
    db.execute("INSERT INTO settings(k,v) VALUES('schema_v',?) ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v", (SCHEMA_V,))
    db.commit()
_core.init = _init

class handler(H):
    # route() runs inside the global LOCK, so touching the connection here can never cut off
    # another request that is still using it.
    def route(self, m, p, b):
        try:
            if _core.db.c is not None: _core.db.c.close()
        except Exception: pass
        _core.db.c = None
        try:
            return super().route(m, p, b)
        finally:
            try:
                if _core.db.c is not None and not _core.db.c.closed: _core.db.c.rollback()   # releases read locks
            except Exception: pass

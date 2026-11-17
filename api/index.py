import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _core
from _core import H

# Neon closes idle connections; a stale one can hang a request until Vercel kills it (300 s).
# Use short timeouts and a fresh connection per request.
_pg = _core.psycopg2
if _pg is not None:
    _orig = _pg.connect
    def _connect(*a, **k):
        k.setdefault('connect_timeout', 10)
        k.setdefault('keepalives', 1); k.setdefault('keepalives_idle', 20)
        k.setdefault('keepalives_interval', 5); k.setdefault('keepalives_count', 3)
        c = _orig(*a, **k)
        try:
            cur = c.cursor(); cur.execute("SET statement_timeout = 20000"); c.commit(); cur.close()
        except Exception:
            try: c.rollback()
            except Exception: pass
        return c
    _pg.connect = _connect

class handler(H):
    def go(self, method):
        try:
            if _core.db.c is not None: _core.db.c.close()
        except Exception: pass
        _core.db.c = None
        return super().go(method)

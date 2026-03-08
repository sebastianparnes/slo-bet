"""
database.py — Turso (libSQL) como base de datos centralizada
Cada apuesta tiene username + league para historial multi-usuario y multi-liga.
"""
import os
import httpx
from typing import Any

_raw_url = os.getenv("TURSO_URL", "libsql://apuestas-sebastianparnes.aws-us-east-2.turso.io")
TURSO_URL = _raw_url.replace("libsql://", "https://")
TURSO_TOKEN = os.getenv("TURSO_TOKEN", "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJhIjoicnciLCJpYXQiOjE3NzI5MzQxOTgsImlkIjoiMDE5Y2NiMWMtYmYwMS03ODkzLWEzMDgtYTMwNjRhM2E5YTQ4IiwicmlkIjoiNDJjNTI4MzQtNjc4Yi00MmI4LTlhY2YtOGJhYmE4NjMwNTQwIn0.zdHB_1L8_mDbSalG1L4H8aGhH_0diw5jDHs2pN_TQR5TzmL1qrw9T-dLufp2edaNpwVorb5GVBc8jDmIn8yZBg")


def _headers():
    return {
        "Authorization": f"Bearer {TURSO_TOKEN}",
        "Content-Type": "application/json",
    }


def _build_stmt(sql: str, params: list = None) -> dict:
    if params:
        return {"type": "execute", "stmt": {"sql": sql, "args": [
            {"type": "text", "value": str(p)} if p is not None else {"type": "null"}
            for p in params
        ]}}
    return {"type": "execute", "stmt": {"sql": sql}}


def _run(*stmts) -> list:
    """Run one or more statements via Turso HTTP API. Returns list of results."""
    payload = {"requests": list(stmts) + [{"type": "close"}]}
    r = httpx.post(
        f"{TURSO_URL}/v2/pipeline",
        headers=_headers(),
        json=payload,
        timeout=15,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results


def _rows(result: dict) -> list[dict]:
    """Parse Turso result into list of dicts."""
    try:
        rs = result.get("response", {}).get("result", {})
        cols = [c["name"] for c in rs.get("cols", [])]
        return [dict(zip(cols, [v.get("value") for v in row])) for row in rs.get("rows", [])]
    except Exception:
        return []


def _last_insert_id(result: dict) -> int:
    try:
        return int(result.get("response", {}).get("result", {}).get("last_insert_rowid", 0))
    except Exception:
        return 0


def init_db():
    """Create tables if they don't exist."""
    _run(
        _build_stmt("""
            CREATE TABLE IF NOT EXISTS bet_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT DEFAULT (datetime('now')),
                username TEXT NOT NULL DEFAULT 'default',
                match_id TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                league TEXT NOT NULL,
                match_date TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                bet_selection TEXT NOT NULL,
                odds REAL,
                stake REAL NOT NULL,
                potential_win REAL,
                actual_win REAL DEFAULT 0,
                result TEXT DEFAULT 'pending',
                match_result TEXT,
                confidence_score REAL,
                recommendation TEXT,
                notes TEXT
            )
        """),
        _build_stmt("""
            CREATE TABLE IF NOT EXISTS analysis_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT UNIQUE,
                cached_at TEXT DEFAULT (datetime('now')),
                expires_at TEXT,
                analysis_json TEXT
            )
        """),
        _build_stmt("""
            CREATE INDEX IF NOT EXISTS idx_bh_username ON bet_history(username)
        """),
        _build_stmt("""
            CREATE INDEX IF NOT EXISTS idx_bh_league ON bet_history(league)
        """),
        _build_stmt("""
            CREATE INDEX IF NOT EXISTS idx_bh_result ON bet_history(result)
        """),
    )
    print("✅ Turso DB initialized")


try:
    init_db()
except Exception as _e:
    print(f"⚠️  Turso init warning: {_e}")


# ── Compatibility alias (usado por result_poller.py y otros) ───────────────
class _FakeConn:
    """Stub SQLite-like connection que delega a Turso via _run()."""
    def cursor(self): return self
    def close(self): pass
    def commit(self): pass

    def execute(self, sql: str, params=None):
        results = _run(_build_stmt(sql, list(params) if params else None))
        self._rows = _rows(results[0]) if results else []
        return self

    def executescript(self, sql: str):
        for stmt in sql.split(";"):
            s = stmt.strip()
            if s:
                try: _run(_build_stmt(s))
                except: pass
        return self

    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None
    @property
    def lastrowid(self): return 0
    @property
    def rowcount(self): return len(getattr(self, "_rows", []))

    class Row(dict):
        def __getitem__(self, k):
            if isinstance(k, int):
                return list(self.values())[k]
            return super().__getitem__(k)

    row_factory = None

def get_connection() -> _FakeConn:
    """Compatibilidad con código que usa sqlite3. Delega a Turso."""
    return _FakeConn()

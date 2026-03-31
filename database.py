"""
database.py — Flare DeFi Model
SQLite backend with WAL mode and thread-local connection pooling.

Stores structured scan results, arbitrage opportunities, and AI feedback
for historical querying. Complements (does not replace) the existing
JSON-based history.json and positions.json files.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config import DB_FILE

logger = logging.getLogger(__name__)

_write_lock    = threading.RLock()
_thread_local  = threading.local()
_db_initialized = False


# ─── Connection Pool ──────────────────────────────────────────────────────────

def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA cache_size=-32768")   # 32 MB page cache
    conn.execute("PRAGMA mmap_size=134217728")  # 128 MB mmap
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.row_factory = sqlite3.Row
    return conn


class _PooledConn:
    """Proxy — close() rolls back instead of destroying the pooled connection."""
    def __init__(self, conn: sqlite3.Connection):
        self.__dict__["_c"] = conn

    def close(self):
        try:
            self.__dict__["_c"].rollback()
        except Exception:
            pass

    def __enter__(self):       return self.__dict__["_c"].__enter__()
    def __exit__(self, *a):    return self.__dict__["_c"].__exit__(*a)
    def __getattr__(self, n):  return getattr(self.__dict__["_c"], n)
    def __setattr__(self, n, v): setattr(self.__dict__["_c"], n, v)


def _get_conn() -> _PooledConn:
    w = getattr(_thread_local, "conn", None)
    if w is None:
        w = _PooledConn(_make_conn())
        _thread_local.conn = w
    else:
        try:
            w.execute("SELECT 1")
        except (sqlite3.DatabaseError, sqlite3.ProgrammingError):
            w = _PooledConn(_make_conn())
            _thread_local.conn = w
    return w


def _get_conn_and_init() -> _PooledConn:
    """Return a connection, running init_db() exactly once on first call (upgrade #31)."""
    global _db_initialized
    conn = _get_conn()
    if not _db_initialized:
        _db_initialized = True   # set before init_db to prevent re-entry on success
        try:
            init_db()
        except Exception:
            _db_initialized = False  # reset so next call retries init
            raise
    return conn


# ─── Schema ───────────────────────────────────────────────────────────────────

def init_db():
    """Create all tables. Idempotent — safe to call every startup."""
    with _write_lock:
        conn = _get_conn()
        conn.executescript("""
            -- Scan runs: one row per scheduled scan execution
            CREATE TABLE IF NOT EXISTS scan_runs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                profile      TEXT    NOT NULL,
                n_opps       INTEGER DEFAULT 0,
                duration_s   REAL,
                status       TEXT    DEFAULT 'ok'
            );

            -- Opportunities: one row per opportunity per scan
            CREATE TABLE IF NOT EXISTS opportunities (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id          INTEGER REFERENCES scan_runs(id),
                timestamp        TEXT    NOT NULL,
                profile          TEXT    NOT NULL,
                protocol         TEXT    NOT NULL,
                asset_or_pool    TEXT,
                opportunity_type TEXT,
                strategy         TEXT,
                estimated_apy    REAL,
                confidence       REAL,
                risk_score       REAL,
                tvl_usd          REAL,
                urgency          TEXT,
                raw_json         TEXT
            );

            -- Arbitrage opportunities: one row per detected arb
            CREATE TABLE IF NOT EXISTS arb_opportunities (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT    NOT NULL,
                profile          TEXT,
                strategy_label   TEXT,
                leg_a_protocol   TEXT,
                leg_b_protocol   TEXT,
                asset_a          TEXT,
                asset_b          TEXT,
                estimated_profit REAL,
                min_capital_usd  REAL,
                urgency          TEXT,
                is_active        INTEGER DEFAULT 1,
                raw_json         TEXT
            );

            -- AI feedback: prediction vs actual outcome tracking
            CREATE TABLE IF NOT EXISTS ai_feedback (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT    NOT NULL,
                profile          TEXT    NOT NULL,
                protocol         TEXT,
                asset_or_pool    TEXT,
                predicted_apy    REAL,
                actual_apy       REAL,
                was_correct      INTEGER,   -- 1 = within 20%, 0 = miss
                direction_match  INTEGER,   -- 1 = directional match
                actual_pnl_pct   REAL,      -- realized PnL if tracked
                evaluated_at     TEXT
            );

            -- Scan status (latest scanner state for UI polling)
            CREATE TABLE IF NOT EXISTS scan_status (
                id          INTEGER PRIMARY KEY CHECK (id = 1),
                is_running  INTEGER DEFAULT 0,
                progress    INTEGER DEFAULT 0,
                current_task TEXT,
                timestamp   TEXT,
                error       TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_opps_timestamp  ON opportunities(timestamp);
            CREATE INDEX IF NOT EXISTS idx_opps_profile    ON opportunities(profile);
            CREATE INDEX IF NOT EXISTS idx_opps_protocol   ON opportunities(protocol);
            CREATE INDEX IF NOT EXISTS idx_arb_timestamp   ON arb_opportunities(timestamp);
            CREATE INDEX IF NOT EXISTS idx_arb_active      ON arb_opportunities(is_active);
            CREATE INDEX IF NOT EXISTS idx_feedback_ts     ON ai_feedback(timestamp);
            CREATE INDEX IF NOT EXISTS idx_feedback_profile ON ai_feedback(profile);
        """)
        # Compound indexes for common profile+timestamp query patterns (upgrade #29)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_opps_profile_ts "
            "ON opportunities(profile, timestamp DESC)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_feedback_profile_ts "
            "ON ai_feedback(profile, timestamp DESC)"
        )
        conn.commit()
        logger.info("[DB] Schema initialized")


# ─── Scan Status ──────────────────────────────────────────────────────────────

def write_scan_status(is_running: bool, progress: int = 0,
                      current_task: str = "", error: Optional[str] = None,
                      timestamp: Optional[str] = None):
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    with _write_lock:
        conn = _get_conn_and_init()
        try:
            conn.execute("""
                INSERT INTO scan_status (id, is_running, progress, current_task, timestamp, error)
                VALUES (1, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    is_running=excluded.is_running,
                    progress=excluded.progress,
                    current_task=excluded.current_task,
                    timestamp=excluded.timestamp,
                    error=excluded.error
            """, (1 if is_running else 0, progress, current_task, ts, error))
            conn.commit()
        except Exception as e:
            logger.error("[DB] write_scan_status: %s", e)


def get_scan_status() -> dict:
    conn = _get_conn_and_init()
    try:
        row = conn.execute("SELECT * FROM scan_status WHERE id=1").fetchone()
        if row:
            return dict(row)
    except Exception as e:
        logger.error("[DB] get_scan_status: %s", e)
    return {"is_running": 0, "progress": 0, "current_task": "", "timestamp": None, "error": None}


# ─── Scan Runs ────────────────────────────────────────────────────────────────

def save_scan_run(profile: str, n_opps: int, duration_s: float = None,
                  status: str = "ok") -> int:
    """Insert a scan run record and return its ID."""
    ts = datetime.now(timezone.utc).isoformat()
    with _write_lock:
        conn = _get_conn_and_init()
        try:
            cur = conn.execute(
                "INSERT INTO scan_runs (timestamp, profile, n_opps, duration_s, status) "
                "VALUES (?,?,?,?,?)",
                (ts, profile, n_opps, duration_s, status),
            )
            conn.commit()
            return cur.lastrowid
        except Exception as e:
            logger.error("[DB] save_scan_run: %s", e)
            return -1


# ─── Opportunities ────────────────────────────────────────────────────────────

def save_opportunities(profile: str, opportunities: List[dict], scan_id: int = None):
    """Persist a list of opportunity dicts for a given risk profile."""
    if not opportunities:
        return
    ts = datetime.now(timezone.utc).isoformat()
    with _write_lock:
        conn = _get_conn_and_init()
        try:
            rows = []
            for o in opportunities:
                rows.append((
                    scan_id,
                    ts,
                    profile,
                    o.get("protocol") or "",
                    o.get("asset_or_pool") or "",
                    o.get("opportunity_type") or "",
                    o.get("strategy") or "",
                    o.get("estimated_apy"),
                    o.get("confidence"),
                    o.get("risk_score"),
                    o.get("tvl_usd"),
                    o.get("urgency") or "normal",
                    json.dumps(o),
                ))
            conn.executemany("""
                INSERT INTO opportunities
                (scan_id, timestamp, profile, protocol, asset_or_pool, opportunity_type,
                 strategy, estimated_apy, confidence, risk_score, tvl_usd, urgency, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
            conn.commit()
        except Exception as e:
            logger.error("[DB] save_opportunities: %s", e)


def get_opportunities(profile: str = None, limit: int = 200) -> pd.DataFrame:
    """Return recent opportunities as a DataFrame."""
    conn = _get_conn_and_init()
    try:
        if profile:
            return pd.read_sql_query(
                "SELECT * FROM opportunities WHERE profile=? ORDER BY timestamp DESC LIMIT ?",
                conn, params=(profile, limit),
            )
        return pd.read_sql_query(
            "SELECT * FROM opportunities ORDER BY timestamp DESC LIMIT ?",
            conn, params=(limit,),
        )
    except Exception as e:
        logger.error("[DB] get_opportunities: %s", e)
        return pd.DataFrame()


# ─── Arbitrage Opportunities ──────────────────────────────────────────────────

def save_arb_opportunities(arb_results: dict):
    """
    Persist arbitrage opportunities from a scan.

    Args:
        arb_results: dict of {profile: [arb_opportunity, ...]} or flat list
    """
    if not arb_results:
        return
    ts  = datetime.now(timezone.utc).isoformat()
    rows: list = []

    if isinstance(arb_results, list):
        items = [("all", a) for a in arb_results]
    else:
        items = []
        for profile, arbs in arb_results.items():
            if isinstance(arbs, list):
                for a in arbs:
                    items.append((profile, a))

    for profile, a in items:
        rows.append((
            ts,
            profile,
            a.get("strategy_label") or a.get("opportunity_type") or "",
            a.get("leg_a_protocol") or a.get("asset_a") or "",
            a.get("leg_b_protocol") or a.get("asset_b") or "",
            a.get("asset_a") or "",
            a.get("asset_b") or "",
            a.get("estimated_profit"),
            a.get("capital_needed") or a.get("min_capital_usd"),
            a.get("urgency") or "normal",
            1,
            json.dumps(a),
        ))

    if not rows:
        return

    with _write_lock:
        conn = _get_conn_and_init()
        try:
            # Prune stale inactive records older than 7 days to prevent unbounded growth
            conn.execute(
                "DELETE FROM arb_opportunities "
                "WHERE is_active = 0 AND timestamp < datetime('now', '-7 days')"
            )
            # Mark previous arbs inactive
            conn.execute("UPDATE arb_opportunities SET is_active=0")
            conn.executemany("""
                INSERT INTO arb_opportunities
                (timestamp, profile, strategy_label, leg_a_protocol, leg_b_protocol,
                 asset_a, asset_b, estimated_profit, min_capital_usd, urgency, is_active, raw_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, rows)
            conn.commit()
        except Exception as e:
            logger.error("[DB] save_arb_opportunities: %s", e)


def get_active_arb_opportunities(limit: int = 100) -> pd.DataFrame:
    """Return currently active arbitrage opportunities."""
    conn = _get_conn_and_init()
    try:
        return pd.read_sql_query(
            "SELECT * FROM arb_opportunities WHERE is_active=1 "
            "ORDER BY estimated_profit DESC LIMIT ?",
            conn, params=(limit,),
        )
    except Exception as e:
        logger.error("[DB] get_active_arb_opportunities: %s", e)
        return pd.DataFrame()


# ─── AI Feedback ──────────────────────────────────────────────────────────────

def save_ai_feedback(profile: str, entries: List[dict]):
    """Persist AI prediction vs actual outcome records."""
    if not entries:
        return
    ts = datetime.now(timezone.utc).isoformat()
    with _write_lock:
        conn = _get_conn_and_init()
        try:
            rows = []
            for e in entries:
                rows.append((
                    e.get("timestamp") or ts,
                    profile,
                    e.get("protocol") or "",
                    e.get("asset_or_pool") or "",
                    e.get("predicted_apy"),
                    e.get("actual_apy"),
                    1 if e.get("was_correct") else 0,
                    1 if e.get("direction_match") else 0,
                    e.get("actual_pnl_pct"),
                    e.get("evaluated_at") or ts,
                ))
            conn.executemany("""
                INSERT INTO ai_feedback
                (timestamp, profile, protocol, asset_or_pool, predicted_apy,
                 actual_apy, was_correct, direction_match, actual_pnl_pct, evaluated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, rows)
            conn.commit()
        except Exception as e:
            logger.error("[DB] save_ai_feedback: %s", e)


def get_ai_feedback(profile: str = None, days: int = 90) -> pd.DataFrame:
    """Return AI feedback records within the lookback window."""
    conn = _get_conn_and_init()
    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        if profile:
            return pd.read_sql_query(
                "SELECT * FROM ai_feedback WHERE profile=? AND timestamp>=? ORDER BY timestamp DESC",
                conn, params=(profile, cutoff),
            )
        return pd.read_sql_query(
            "SELECT * FROM ai_feedback WHERE timestamp>=? ORDER BY timestamp DESC",
            conn, params=(cutoff,),
        )
    except Exception as e:
        logger.error("[DB] get_ai_feedback: %s", e)
        return pd.DataFrame()


# ─── DB Integrity Check (#14) ─────────────────────────────────────────────────

def check_db_integrity(db_path: str = None) -> bool:
    """
    Run PRAGMA quick_check on the app database.
    Returns True if the database passes the check, False otherwise.
    Called at startup via st.cache_resource in app.py.
    """
    if db_path is None:
        db_path = str(DB_FILE)
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10)
        result = conn.execute("PRAGMA quick_check").fetchone()
        conn.close()
        ok = result is not None and result[0] == "ok"
        if not ok:
            logger.warning("[DB] Integrity check failed: %s", result)
        return ok
    except Exception as e:
        logger.warning("[DB] Integrity check failed: %s", e)
        return False


# ─── Auto-init ────────────────────────────────────────────────────────────────
# Schema is initialised lazily on the first DB operation via _get_conn_and_init()
# (upgrade #31). This prevents blocking app startup if the DB file is locked or
# slow to open. Callers that need the schema immediately may call init_db() directly.

"""
agents/audit_log.py — Append-only SQLite audit trail.

Every agent action is recorded here — decisions approved, decisions rejected,
trades executed, errors, emergency stops. Nothing is ever deleted or modified.
This is the complete verifiable record of everything the agent has done.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agents.config import AGENT_DB_FILE

_lock = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(AGENT_DB_FILE), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db() -> None:
    """Create all tables if they don't exist. Safe to call multiple times."""
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS agent_audit (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp     TEXT    NOT NULL,
                event_type    TEXT    NOT NULL,
                chain         TEXT    DEFAULT '',
                protocol      TEXT    DEFAULT '',
                pool          TEXT    DEFAULT '',
                action        TEXT    DEFAULT '',
                size_usd      REAL    DEFAULT 0,
                approved      INTEGER DEFAULT 0,
                reason        TEXT    DEFAULT '',
                wallet_usd    REAL    DEFAULT 0,
                daily_pnl_usd REAL    DEFAULT 0,
                trade_id      TEXT    DEFAULT '',
                extra         TEXT    DEFAULT '{}'
            );

            CREATE TABLE IF NOT EXISTS agent_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        TEXT    NOT NULL UNIQUE,
                timestamp       TEXT    NOT NULL,
                mode            TEXT    NOT NULL,
                chain           TEXT    NOT NULL,
                protocol        TEXT    NOT NULL,
                pool            TEXT    NOT NULL,
                action          TEXT    NOT NULL,
                token_in        TEXT    DEFAULT '',
                token_out       TEXT    DEFAULT '',
                size_usd        REAL    NOT NULL,
                fill_price      REAL    DEFAULT 0,
                slippage_pct    REAL    DEFAULT 0,
                gas_usd         REAL    DEFAULT 0,
                expected_apy    REAL    DEFAULT 0,
                confidence      REAL    DEFAULT 0,
                reasoning       TEXT    DEFAULT '',
                status          TEXT    DEFAULT 'open',
                exit_timestamp  TEXT    DEFAULT '',
                exit_price      REAL    DEFAULT 0,
                realized_pnl    REAL    DEFAULT 0,
                tx_hash         TEXT    DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS agent_positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        TEXT    NOT NULL UNIQUE,
                chain           TEXT    NOT NULL,
                protocol        TEXT    NOT NULL,
                pool            TEXT    NOT NULL,
                token_in        TEXT    DEFAULT '',
                token_out       TEXT    DEFAULT '',
                size_usd        REAL    NOT NULL,
                entry_price     REAL    DEFAULT 0,
                entry_timestamp TEXT    NOT NULL,
                current_value   REAL    DEFAULT 0,
                unrealized_pnl  REAL    DEFAULT 0,
                expected_apy    REAL    DEFAULT 0,
                status          TEXT    DEFAULT 'open'
            );

            CREATE INDEX IF NOT EXISTS idx_audit_ts    ON agent_audit(timestamp);
            CREATE INDEX IF NOT EXISTS idx_audit_type  ON agent_audit(event_type);
            CREATE INDEX IF NOT EXISTS idx_trades_ts   ON agent_trades(timestamp);
            CREATE INDEX IF NOT EXISTS idx_positions_s ON agent_positions(status);
        """)
        conn.commit()
        conn.close()


# Initialise on import
try:
    init_db()
except Exception:
    pass  # never crash on import; UI will show "DB unavailable"


class AuditLog:
    """Append-only audit trail. Every method is thread-safe."""

    # ── Event type constants ───────────────────────────────────────────────────
    DECISION_APPROVED  = "DECISION_APPROVED"
    DECISION_REJECTED  = "DECISION_REJECTED"
    TRADE_OPENED       = "TRADE_OPENED"
    TRADE_CLOSED       = "TRADE_CLOSED"
    EMERGENCY_STOP     = "EMERGENCY_STOP"
    AGENT_STARTED      = "AGENT_STARTED"
    AGENT_STOPPED      = "AGENT_STOPPED"
    AGENT_ERROR        = "AGENT_ERROR"
    PHASE_GATE_UPDATE  = "PHASE_GATE_UPDATE"
    LIVE_UNLOCKED      = "LIVE_UNLOCKED"

    def _write(self, event_type: str, **kwargs: Any) -> None:
        extra = kwargs.pop("extra", {})
        row = {
            "timestamp":     _utcnow(),
            "event_type":    event_type,
            "chain":         kwargs.get("chain", ""),
            "protocol":      kwargs.get("protocol", ""),
            "pool":          kwargs.get("pool", ""),
            "action":        kwargs.get("action", ""),
            "size_usd":      kwargs.get("size_usd", 0.0),
            "approved":      1 if kwargs.get("approved", False) else 0,
            "reason":        kwargs.get("reason", ""),
            "wallet_usd":    kwargs.get("wallet_usd", 0.0),
            "daily_pnl_usd": kwargs.get("daily_pnl_usd", 0.0),
            "trade_id":      kwargs.get("trade_id", ""),
            "extra":         json.dumps(extra),
        }
        with _lock:
            conn = _get_conn()
            try:
                conn.execute("""
                    INSERT INTO agent_audit
                    (timestamp, event_type, chain, protocol, pool, action,
                     size_usd, approved, reason, wallet_usd, daily_pnl_usd,
                     trade_id, extra)
                    VALUES
                    (:timestamp, :event_type, :chain, :protocol, :pool, :action,
                     :size_usd, :approved, :reason, :wallet_usd, :daily_pnl_usd,
                     :trade_id, :extra)
                """, row)
                conn.commit()
            finally:
                conn.close()

    def log_decision(self, decision: dict, approved: bool, reason: str,
                     wallet_usd: float = 0, daily_pnl_usd: float = 0) -> None:
        self._write(
            self.DECISION_APPROVED if approved else self.DECISION_REJECTED,
            chain=decision.get("chain", ""),
            protocol=decision.get("protocol", ""),
            pool=decision.get("pool", ""),
            action=decision.get("action", ""),
            size_usd=decision.get("size_usd", 0),
            approved=approved,
            reason=reason,
            wallet_usd=wallet_usd,
            daily_pnl_usd=daily_pnl_usd,
            extra={"confidence": decision.get("confidence", 0),
                   "reasoning": decision.get("reasoning", "")},
        )

    def log_trade_opened(self, trade: dict) -> None:
        self._write(self.TRADE_OPENED, trade_id=trade.get("trade_id", ""),
                    chain=trade.get("chain", ""), protocol=trade.get("protocol", ""),
                    pool=trade.get("pool", ""), action=trade.get("action", ""),
                    size_usd=trade.get("size_usd", 0), approved=True,
                    reason="trade executed", extra=trade)

    def log_trade_closed(self, trade_id: str, realized_pnl: float,
                         reason: str = "") -> None:
        self._write(self.TRADE_CLOSED, trade_id=trade_id,
                    action="EXIT_POSITION", approved=True,
                    reason=reason, extra={"realized_pnl": realized_pnl})

    def log_emergency_stop(self, reason: str) -> None:
        self._write(self.EMERGENCY_STOP, reason=reason, approved=False,
                    extra={"triggered_at": _utcnow()})

    def log_error(self, error: str, context: dict | None = None) -> None:
        self._write(self.AGENT_ERROR, reason=error,
                    extra={"context": context or {}})

    def log_agent_start(self, mode: str) -> None:
        self._write(self.AGENT_STARTED, reason=f"mode={mode}",
                    extra={"mode": mode})

    def log_agent_stop(self, reason: str = "user request") -> None:
        self._write(self.AGENT_STOPPED, reason=reason)

    def log_live_unlocked(self, paper_days: int) -> None:
        self._write(self.LIVE_UNLOCKED, reason="manual unlock by user",
                    extra={"paper_days": paper_days})

    # ── Query helpers ──────────────────────────────────────────────────────────

    def get_recent_audit(self, limit: int = 100) -> list[dict]:
        with _lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_audit ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_trades(self, status: str = "all", limit: int = 200) -> list[dict]:
        with _lock:
            conn = _get_conn()
            try:
                if status == "all":
                    rows = conn.execute(
                        "SELECT * FROM agent_trades ORDER BY id DESC LIMIT ?", (limit,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM agent_trades WHERE status=? ORDER BY id DESC LIMIT ?",
                        (status, limit)
                    ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_daily_pnl_usd(self) -> float:
        """Sum of realized P&L from trades closed today (UTC)."""
        today = _utcnow()[:10]
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) as pnl "
                    "FROM agent_trades WHERE status='closed' AND exit_timestamp LIKE ?",
                    (f"{today}%",)
                ).fetchone()
                return float(row["pnl"]) if row else 0.0
            finally:
                conn.close()

    def get_paper_trade_days(self) -> int:
        """Number of distinct UTC days with at least one paper trade."""
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(DISTINCT substr(timestamp,1,10)) as days "
                    "FROM agent_trades WHERE mode='PAPER'"
                ).fetchone()
                return int(row["days"]) if row else 0
            finally:
                conn.close()

    def get_paper_stats(self) -> dict:
        """Summary stats for paper trading performance."""
        with _lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT realized_pnl, size_usd FROM agent_trades "
                    "WHERE mode='PAPER' AND status='closed'"
                ).fetchall()
                if not rows:
                    return {"total_trades": 0, "win_rate": 0.0,
                            "total_pnl": 0.0, "avg_pnl_pct": 0.0}
                total = len(rows)
                wins  = sum(1 for r in rows if r["realized_pnl"] > 0)
                total_pnl = sum(r["realized_pnl"] for r in rows)
                avg_pnl_pct = (
                    sum(r["realized_pnl"] / max(r["size_usd"], 0.01) for r in rows)
                    / total * 100
                )
                return {
                    "total_trades": total,
                    "win_rate": wins / total * 100,
                    "total_pnl": total_pnl,
                    "avg_pnl_pct": avg_pnl_pct,
                }
            finally:
                conn.close()

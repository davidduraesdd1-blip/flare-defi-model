"""
agents/position_monitor.py — Open position tracker + P&L + drawdown.

Tracks all paper and live positions in SQLite.
The RiskGuard reads from this to enforce max open positions and drawdown limits.
"""

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from agents.config import AGENT_DB_FILE, PAPER_STARTING_BALANCE_USD
from agents.audit_log import AuditLog, init_db

_lock = threading.Lock()
_audit = AuditLog()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(AGENT_DB_FILE), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


class PositionMonitor:
    """
    Tracks open positions, calculates unrealized P&L, and records peak balance
    for drawdown calculation. Thread-safe.
    """

    def open_position(
        self,
        chain: str,
        protocol: str,
        pool: str,
        action: str,
        token_in: str,
        token_out: str,
        size_usd: float,
        entry_price: float,
        expected_apy: float,
        confidence: float,
        reasoning: str,
        mode: str,
        fill_price: float = 0.0,
        slippage_pct: float = 0.0,
        gas_usd: float = 0.0,
        tx_hash: str = "",
    ) -> str:
        """Record a new open position. Returns trade_id."""
        trade_id = str(uuid.uuid4())[:12]
        ts = _utcnow()
        with _lock:
            conn = _get_conn()
            try:
                conn.execute("""
                    INSERT INTO agent_trades
                    (trade_id, timestamp, mode, chain, protocol, pool, action,
                     token_in, token_out, size_usd, fill_price, slippage_pct,
                     gas_usd, expected_apy, confidence, reasoning, status, tx_hash)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'open',?)
                """, (trade_id, ts, mode, chain, protocol, pool, action,
                      token_in, token_out, size_usd, fill_price or size_usd,
                      slippage_pct, gas_usd, expected_apy, confidence,
                      reasoning, tx_hash))
                conn.execute("""
                    INSERT OR REPLACE INTO agent_positions
                    (trade_id, chain, protocol, pool, token_in, token_out,
                     size_usd, entry_price, entry_timestamp,
                     current_value, unrealized_pnl, expected_apy, status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,0,?,'open')
                """, (trade_id, chain, protocol, pool, token_in, token_out,
                      size_usd, entry_price or size_usd, ts, size_usd, expected_apy))
                conn.commit()
            finally:
                conn.close()
        _audit.log_trade_opened({
            "trade_id": trade_id, "chain": chain, "protocol": protocol,
            "pool": pool, "action": action, "size_usd": size_usd,
            "mode": mode, "confidence": confidence,
        })
        return trade_id

    def close_position(
        self,
        trade_id: str,
        exit_price: float,
        realized_pnl: float,
        reason: str = "model signal",
        tx_hash: str = "",
    ) -> None:
        """Mark a position closed and record realized P&L."""
        ts = _utcnow()
        with _lock:
            conn = _get_conn()
            try:
                conn.execute("""
                    UPDATE agent_trades
                    SET status='closed', exit_timestamp=?, exit_price=?,
                        realized_pnl=?, tx_hash=?
                    WHERE trade_id=?
                """, (ts, exit_price, realized_pnl, tx_hash, trade_id))
                conn.execute("""
                    UPDATE agent_positions SET status='closed' WHERE trade_id=?
                """, (trade_id,))
                conn.commit()
            finally:
                conn.close()
        _audit.log_trade_closed(trade_id, realized_pnl, reason)

    def update_position_value(self, trade_id: str, current_value: float) -> None:
        """Update mark-to-market value of an open position."""
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT size_usd FROM agent_positions WHERE trade_id=?",
                    (trade_id,)
                ).fetchone()
                if row:
                    unrealized = current_value - row["size_usd"]
                    conn.execute("""
                        UPDATE agent_positions
                        SET current_value=?, unrealized_pnl=?
                        WHERE trade_id=?
                    """, (current_value, unrealized, trade_id))
                    conn.commit()
            except Exception as _e:
                logger.warning("[PositionMonitor] update_position_value failed for %s: %s", trade_id, _e)
            finally:
                conn.close()

    def get_open_positions(self) -> list[dict]:
        """Return all currently open positions."""
        with _lock:
            conn = _get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM agent_positions WHERE status='open' ORDER BY entry_timestamp"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def get_open_position_count(self) -> int:
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) as n FROM agent_positions WHERE status='open'"
                ).fetchone()
                return int(row["n"]) if row else 0
            finally:
                conn.close()

    def get_total_deployed_usd(self) -> float:
        """Sum of all open position sizes."""
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(size_usd),0) as total "
                    "FROM agent_positions WHERE status='open'"
                ).fetchone()
                return float(row["total"]) if row else 0.0
            finally:
                conn.close()

    def get_total_unrealized_pnl(self) -> float:
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(unrealized_pnl),0) as pnl "
                    "FROM agent_positions WHERE status='open'"
                ).fetchone()
                return float(row["pnl"]) if row else 0.0
            finally:
                conn.close()

    def get_last_loss_timestamp(self) -> float:
        """Unix timestamp of the most recent losing closed trade (0 if none)."""
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT exit_timestamp FROM agent_trades "
                    "WHERE status='closed' AND realized_pnl < 0 "
                    "ORDER BY exit_timestamp DESC LIMIT 1"
                ).fetchone()
                if not row or not row["exit_timestamp"]:
                    return 0.0
                try:
                    dt = datetime.strptime(row["exit_timestamp"], "%Y-%m-%dT%H:%M:%SZ")
                    return dt.replace(tzinfo=timezone.utc).timestamp()
                except Exception:
                    return 0.0
            finally:
                conn.close()

    # ── Balance / drawdown tracking ────────────────────────────────────────────

    def get_paper_balance(self) -> float:
        """
        Paper balance = starting balance + all realized P&L + unrealized P&L.
        In paper mode this is the effective 'wallet balance'.
        """
        unrealized = self.get_total_unrealized_pnl()
        # Compute total realized P&L (not just today)
        with _lock:
            conn = _get_conn()
            try:
                row = conn.execute(
                    "SELECT COALESCE(SUM(realized_pnl),0) as total "
                    "FROM agent_trades WHERE mode='PAPER' AND status='closed'"
                ).fetchone()
                total_realized = float(row["total"]) if row else 0.0
            finally:
                conn.close()
        return PAPER_STARTING_BALANCE_USD + total_realized + unrealized

    def get_peak_balance(self, starting: float) -> float:
        """
        Peak balance = highest balance recorded. Approximated from trade history.
        In paper mode this is the max of starting balance and current balance.
        """
        current = self.get_paper_balance()
        return max(starting, current)

    def get_position_summary(self) -> dict:
        """Full summary for the UI and risk guard."""
        positions = self.get_open_positions()
        return {
            "open_count":      len(positions),
            "deployed_usd":    sum(p["size_usd"] for p in positions),
            "unrealized_pnl":  sum(p["unrealized_pnl"] for p in positions),
            "positions":       positions,
        }

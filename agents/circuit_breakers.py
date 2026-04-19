"""
agents/circuit_breakers.py — Level-C (24/7 autonomous agent) safety.

Gate 1: DRAWDOWN    — total portfolio drop exceeds threshold → halt
Gate 2: LOSS-RATE   — too many losing trades in window → halt
Gate 3: VELOCITY    — too many trades in a short window (fat-finger / loop) → halt
Gate 4: HEARTBEAT   — last-scan timestamp too old → halt (data-stale)
Gate 5: API-HEALTH  — critical API is failing → halt
Gate 6: VARIANCE    — realized volatility exceeds baseline + margin → halt
Gate 7: HUMAN-OVERRIDE — emergency stop flag from UI / phone webhook → halt

Any gate tripping sets `state.halted = True` and writes a HALT event to
the audit log. A manual `resume(reason)` action is required to unhalt.

All gates run in every agent cycle via `check_all(context)`. Gates are
pure (no side effects) except for audit-logging; they read from:
- position_monitor (open positions, P&L, peak balance)
- audit_log (daily_pnl, trade history)
- data/circuit_state.json (halted flag, last-trip reason, timestamps)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Default thresholds (overridable per app) ────────────────────────────────

DEFAULTS = {
    "drawdown_pct":          0.15,   # halt if drawdown from peak > 15%
    "daily_loss_pct":        0.03,   # halt if daily loss > 3% of peak
    "loss_rate_window_n":    10,     # look at last 10 trades
    "loss_rate_max":         0.70,   # halt if >70% of them lost
    "velocity_window_sec":   300,    # 5-min window
    "velocity_max_trades":   20,     # halt if >20 trades in 5 min
    "heartbeat_max_age_sec": 1800,   # halt if last scan > 30 min old
    "api_fail_consecutive":  5,      # halt if 5 consecutive API failures
    "variance_mult":         3.0,    # halt if realized-vol > 3x baseline
}

_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "circuit_state.json"
_LOCK = threading.Lock()


@dataclass
class CircuitState:
    halted:         bool = False
    halted_reason:  str  = ""
    halted_at:      Optional[str] = None
    halted_gate:    str  = ""
    last_check_at:  Optional[str] = None
    resume_count:   int  = 0
    resume_history: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "halted":         self.halted,
            "halted_reason":  self.halted_reason,
            "halted_at":      self.halted_at,
            "halted_gate":    self.halted_gate,
            "last_check_at":  self.last_check_at,
            "resume_count":   self.resume_count,
            "resume_history": list(self.resume_history),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CircuitState":
        return cls(
            halted=bool(d.get("halted", False)),
            halted_reason=str(d.get("halted_reason", "")),
            halted_at=d.get("halted_at"),
            halted_gate=str(d.get("halted_gate", "")),
            last_check_at=d.get("last_check_at"),
            resume_count=int(d.get("resume_count", 0)),
            resume_history=list(d.get("resume_history", [])),
        )


def _load_state() -> CircuitState:
    try:
        if _STATE_FILE.exists():
            return CircuitState.from_dict(json.loads(_STATE_FILE.read_text(encoding="utf-8")))
    except Exception as e:
        logger.debug("[CircuitBreakers] load failed: %s", e)
    return CircuitState()


def _save_state(state: CircuitState) -> None:
    try:
        _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _STATE_FILE.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("[CircuitBreakers] save failed: %s", e)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Individual gates ────────────────────────────────────────────────────────

def _gate_drawdown(context: dict, cfg: dict) -> Optional[str]:
    """Gate 1: halt if drawdown from peak exceeds threshold."""
    peak = float(context.get("peak_balance_usd", 0) or 0)
    cur  = float(context.get("current_balance_usd", 0) or 0)
    if peak <= 0 or cur <= 0:
        return None
    dd = (peak - cur) / peak
    lim = float(cfg.get("drawdown_pct", DEFAULTS["drawdown_pct"]))
    if dd >= lim:
        return f"Drawdown {dd*100:.1f}% exceeds {lim*100:.0f}% limit from peak ${peak:,.0f}"
    return None


def _gate_daily_loss(context: dict, cfg: dict) -> Optional[str]:
    """Gate 2: halt if today's realized loss exceeds daily_loss_pct of peak."""
    daily_pnl = float(context.get("daily_pnl_usd", 0) or 0)
    peak = float(context.get("peak_balance_usd", 1) or 1)
    if daily_pnl >= 0:
        return None
    loss_pct = abs(daily_pnl) / peak
    lim = float(cfg.get("daily_loss_pct", DEFAULTS["daily_loss_pct"]))
    if loss_pct >= lim:
        return f"Daily loss ${abs(daily_pnl):,.0f} ({loss_pct*100:.1f}%) exceeds {lim*100:.0f}% of peak"
    return None


def _gate_loss_rate(context: dict, cfg: dict) -> Optional[str]:
    """Gate 3: halt if last N trades had loss_rate_max losers."""
    trades = context.get("recent_trade_pnls", []) or []
    window = int(cfg.get("loss_rate_window_n", DEFAULTS["loss_rate_window_n"]))
    if len(trades) < window:
        return None
    _recent = trades[-window:]
    losers = sum(1 for t in _recent if float(t or 0) < 0)
    rate = losers / window
    lim = float(cfg.get("loss_rate_max", DEFAULTS["loss_rate_max"]))
    if rate >= lim:
        return f"Loss rate {rate*100:.0f}% ({losers}/{window}) exceeds {lim*100:.0f}% ceiling"
    return None


def _gate_velocity(context: dict, cfg: dict) -> Optional[str]:
    """Gate 4: halt if trade velocity is suspiciously high."""
    timestamps = context.get("recent_trade_timestamps", []) or []
    if not timestamps:
        return None
    window = float(cfg.get("velocity_window_sec", DEFAULTS["velocity_window_sec"]))
    now = time.time()
    _recent = [t for t in timestamps if (now - float(t)) <= window]
    max_n = int(cfg.get("velocity_max_trades", DEFAULTS["velocity_max_trades"]))
    if len(_recent) > max_n:
        return f"Trade velocity {len(_recent)} in last {int(window/60)}m exceeds {max_n}"
    return None


def _gate_heartbeat(context: dict, cfg: dict) -> Optional[str]:
    """Gate 5: halt if last successful scan is too old (data-stale)."""
    last_scan = context.get("last_scan_unix")
    if last_scan is None:
        return None
    age = time.time() - float(last_scan)
    lim = float(cfg.get("heartbeat_max_age_sec", DEFAULTS["heartbeat_max_age_sec"]))
    if age > lim:
        return f"Last scan {int(age/60)}m ago exceeds {int(lim/60)}m heartbeat threshold"
    return None


def _gate_api_health(context: dict, cfg: dict) -> Optional[str]:
    """Gate 6: halt if consecutive API failures exceed threshold."""
    consecutive = int(context.get("consecutive_api_failures", 0) or 0)
    lim = int(cfg.get("api_fail_consecutive", DEFAULTS["api_fail_consecutive"]))
    if consecutive >= lim:
        return f"{consecutive} consecutive API failures exceed {lim} threshold"
    return None


def _gate_variance(context: dict, cfg: dict) -> Optional[str]:
    """Gate 7: halt if realized vol is wildly above recent baseline."""
    cur_vol = context.get("realized_vol_24h")
    baseline = context.get("baseline_vol_30d")
    if cur_vol is None or baseline is None or baseline <= 0:
        return None
    mult = float(cur_vol) / float(baseline)
    lim = float(cfg.get("variance_mult", DEFAULTS["variance_mult"]))
    if mult >= lim:
        return f"Realized vol {cur_vol*100:.1f}% is {mult:.1f}x the 30d baseline {baseline*100:.1f}%"
    return None


def _gate_human_override(context: dict, cfg: dict) -> Optional[str]:
    """Gate 8: respect manual emergency stop flag from UI or phone webhook."""
    if context.get("emergency_stop_active"):
        return "EMERGENCY STOP flag set by human — manual resume required"
    return None


# ── Public API ──────────────────────────────────────────────────────────────

GATES = [
    ("DRAWDOWN",       _gate_drawdown),
    ("DAILY_LOSS",     _gate_daily_loss),
    ("LOSS_RATE",      _gate_loss_rate),
    ("VELOCITY",       _gate_velocity),
    ("HEARTBEAT",      _gate_heartbeat),
    ("API_HEALTH",     _gate_api_health),
    ("VARIANCE",       _gate_variance),
    ("HUMAN_OVERRIDE", _gate_human_override),
]


def check_all(context: dict, cfg: Optional[dict] = None) -> tuple[bool, str, str]:
    """
    Run every gate. If ANY trips, halt the circuit and persist state.
    Returns (can_proceed, reason, gate_name).
    """
    cfg = cfg or DEFAULTS
    with _LOCK:
        state = _load_state()
        state.last_check_at = _now_iso()

        # If already halted, short-circuit and don't re-run gates.
        if state.halted:
            _save_state(state)
            return False, state.halted_reason, state.halted_gate

        for gate_name, gate_fn in GATES:
            try:
                _trip = gate_fn(context, cfg)
            except Exception as e:
                logger.warning("[CircuitBreakers] gate %s raised: %s", gate_name, e)
                _trip = None
            if _trip:
                state.halted        = True
                state.halted_reason = _trip
                state.halted_gate   = gate_name
                state.halted_at     = _now_iso()
                _save_state(state)
                logger.warning("[CircuitBreakers] HALT tripped by %s: %s", gate_name, _trip)
                return False, _trip, gate_name

        _save_state(state)
        return True, "", ""


def resume(reason: str = "manual resume") -> bool:
    """
    Clear the halted flag. Should require an explicit human action at the
    caller layer (UI button + confirm dialog).
    Returns True if the circuit was halted and is now cleared.
    """
    with _LOCK:
        state = _load_state()
        if not state.halted:
            return False
        state.resume_history.append({
            "resumed_at": _now_iso(),
            "reason":     str(reason)[:500],
            "prior_halt": {
                "gate":    state.halted_gate,
                "reason":  state.halted_reason,
                "at":      state.halted_at,
            },
        })
        state.halted        = False
        state.halted_reason = ""
        state.halted_gate   = ""
        state.halted_at     = None
        state.resume_count += 1
        _save_state(state)
    logger.info("[CircuitBreakers] RESUME: %s", reason)
    return True


def get_state() -> dict:
    """Read the current circuit state (for UI display)."""
    with _LOCK:
        return _load_state().to_dict()


def force_halt(reason: str, gate_name: str = "MANUAL_HALT") -> None:
    """Manually halt the circuit (UI kill switch / phone webhook)."""
    with _LOCK:
        state = _load_state()
        state.halted        = True
        state.halted_reason = str(reason)[:500]
        state.halted_gate   = gate_name
        state.halted_at     = _now_iso()
        _save_state(state)
    logger.warning("[CircuitBreakers] MANUAL HALT: %s (gate=%s)", reason, gate_name)


__all__ = [
    "DEFAULTS", "CircuitState", "GATES",
    "check_all", "resume", "get_state", "force_halt",
]

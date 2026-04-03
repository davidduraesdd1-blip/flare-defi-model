"""
agents/agent_runner.py — Main autonomous decision loop.

Integrates with APScheduler (already used by the DeFi Model scan scheduler).
Runs every DECISION_LOOP_INTERVAL_SECONDS (5 minutes).

Loop:
  1. Load agent state (mode, stop flags, phase gate)
  2. Collect market context (data_feed)
  3. Ask Claude for a decision (decision_engine)
  4. Validate decision (risk_guard)
  5. Execute: paper_trader (PAPER) or flare/xrpl_executor (LIVE)
  6. Log everything to audit_log
  7. Update position_monitor
"""

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents import config as C
from agents.audit_log import AuditLog
from agents.data_feed import get_agent_context
from agents.decision_engine import DecisionEngine
from agents.position_monitor import PositionMonitor
from agents.risk_guard import RiskGuard

_audit   = AuditLog()
_monitor = PositionMonitor()
_guard   = RiskGuard()
_engine  = DecisionEngine()

_stop_event = threading.Event()
_lock       = threading.Lock()


# ─── Agent state (persisted to JSON) ─────────────────────────────────────────

def _load_state() -> dict:
    try:
        if C.AGENT_STATE_FILE.exists():
            return json.loads(C.AGENT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        C.PHASE_GATE_KEY:     0,
        C.LIVE_UNLOCK_KEY:    False,
        C.EMERGENCY_STOP_KEY: False,
        "running":            False,
        "mode":               C.OPERATING_MODE,
        "last_decision_ts":   "",
        "last_decision":      {},
        "consecutive_errors": 0,
        "started_at":         "",
    }


def _save_state(state: dict) -> None:
    try:
        C.AGENT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        C.AGENT_STATE_FILE.write_text(
            json.dumps(state, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass


def get_state() -> dict:
    """Public accessor for UI — returns current agent state."""
    return _load_state()


def set_emergency_stop(active: bool, reason: str = "user request") -> None:
    """Activate or deactivate emergency stop. Thread-safe."""
    with _lock:
        state = _load_state()
        state[C.EMERGENCY_STOP_KEY] = active
        _save_state(state)
    if active:
        _audit.log_emergency_stop(reason)
        _stop_event.set()


def set_running(running: bool) -> None:
    """Start or pause the agent loop."""
    with _lock:
        state = _load_state()
        state["running"] = running
        if running:
            state[C.EMERGENCY_STOP_KEY] = False
            state["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            _stop_event.clear()
        _save_state(state)
    if running:
        _audit.log_agent_start(C.OPERATING_MODE)
    else:
        _audit.log_agent_stop("user paused")


def unlock_live_mode() -> None:
    """Manually unlock live mode (requires paper gate to be satisfied first)."""
    with _lock:
        state = _load_state()
        paper_days = _audit.get_paper_trade_days()
        if paper_days < C.PAPER_TRADING_GATE_DAYS:
            raise ValueError(
                f"Cannot unlock: only {paper_days}/{C.PAPER_TRADING_GATE_DAYS} "
                "paper trading days completed"
            )
        state[C.LIVE_UNLOCK_KEY]  = True
        state[C.PHASE_GATE_KEY]   = paper_days
        _save_state(state)
    _audit.log_live_unlocked(_audit.get_paper_trade_days())


# ─── Core decision cycle ──────────────────────────────────────────────────────

def _run_one_cycle(force: bool = False) -> None:
    """
    Execute a single agent decision cycle.
    force=True: bypass the running check (used by "Run One Cycle Now" from the UI).
    Emergency stop is always respected, even when forced.
    """
    # Apply any user config overrides before reading any C.* constants
    C._apply_overrides()

    state = _load_state()

    if state.get(C.EMERGENCY_STOP_KEY, False):
        return  # always respect emergency stop
    if not force and not state.get("running", False):
        return  # only skip when paused AND not a forced UI cycle

    operating_mode       = state.get("mode", C.OPERATING_MODE)
    paper_days           = _audit.get_paper_trade_days()
    live_unlocked        = state.get(C.LIVE_UNLOCK_KEY, False)
    emergency_stop       = state.get(C.EMERGENCY_STOP_KEY, False)
    last_loss_ts         = _monitor.get_last_loss_timestamp()
    open_positions       = _monitor.get_open_positions()
    open_count           = len(open_positions)

    # Determine wallet balance
    if operating_mode == "PAPER":
        wallet_usd = _monitor.get_paper_balance()
    else:
        wallet_usd = 0.0  # live balance requires wallet_manager — injected by caller

    daily_pnl = _audit.get_daily_pnl_usd()
    peak_usd  = _monitor.get_peak_balance(C.PAPER_STARTING_BALANCE_USD)

    # Collect context for Claude
    ctx = get_agent_context(
        wallet_balance_usd = wallet_usd,
        daily_pnl_usd      = daily_pnl,
        open_positions     = open_positions,
        operating_mode     = operating_mode,
    )

    # Get Claude's decision
    decision = _engine.decide(ctx)

    # Validate with RiskGuard (independent of Claude)
    risk_result = _guard.validate(
        decision               = decision.to_dict(),
        wallet_balance_usd     = wallet_usd,
        daily_pnl_usd          = daily_pnl,
        open_position_count    = open_count,
        last_loss_timestamp    = last_loss_ts,
        peak_balance_usd       = peak_usd,
        operating_mode         = operating_mode,
        paper_days_completed   = paper_days,
        live_manually_unlocked = live_unlocked,
        emergency_stop_active  = emergency_stop,
    )

    # Capture a snapshot of the active config limits at cycle time so the audit
    # log proves exactly which settings governed this decision.
    _config_snapshot = {
        "MAX_TRADE_SIZE_PCT":          C.MAX_TRADE_SIZE_PCT,
        "MAX_DAILY_LOSS_PCT":          C.MAX_DAILY_LOSS_PCT,
        "MAX_DRAWDOWN_PCT":            C.MAX_DRAWDOWN_PCT,
        "MIN_CONFIDENCE":              C.MIN_CONFIDENCE,
        "MAX_OPEN_POSITIONS":          C.MAX_OPEN_POSITIONS,
        "COOLDOWN_AFTER_LOSS_SECONDS": C.COOLDOWN_AFTER_LOSS_SECONDS,
        "MIN_TRADE_SIZE_USD":          C.MIN_TRADE_SIZE_USD,
        "MAX_REASONABLE_APY":          C.MAX_REASONABLE_APY,
        "forced_cycle":                force,
    }
    _audit.log_decision(
        decision.to_dict(),
        approved       = risk_result.approved,
        reason         = risk_result.reason,
        wallet_usd     = wallet_usd,
        daily_pnl_usd  = daily_pnl,
        config_snapshot = _config_snapshot,
    )

    # Update state with last decision for UI display
    with _lock:
        s = _load_state()
        s["last_decision_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s["last_decision"]    = {
            "action":    decision.action,
            "protocol":  decision.protocol,
            "pool":      decision.pool,
            "approved":  risk_result.approved,
            "reason":    risk_result.reason,
            "reasoning": decision.reasoning,
            "size_usd":  risk_result.adjusted_size_usd,
        }
        s[C.PHASE_GATE_KEY] = _audit.get_paper_trade_days()
        _save_state(s)

    if not risk_result.approved:
        return  # audited, nothing more to do

    # Execute
    if operating_mode == "PAPER":
        from agents.paper_trader import execute_paper_trade
        execute_paper_trade(decision, risk_result.adjusted_size_usd)

    # LIVE execution: flare_executor and xrpl_executor need wallet passwords
    # which are injected at session level — not implemented in the background
    # loop for security. Live trades are triggered from the UI with wallet
    # password input, not from the background loop.
    # (This is intentional: autonomous live execution is Phase 3 only,
    #  after live key management infrastructure is production-ready.)


def run_agent_loop_once(force: bool = False) -> None:
    """
    Single cycle — called by APScheduler every DECISION_LOOP_INTERVAL_SECONDS.
    Wrapped in full exception handling so a crash never stops the scheduler.
    force=True passed through from run_cycle_now() for UI-triggered cycles.
    """
    try:
        _run_one_cycle(force=force)
        # Reset error counter on success
        with _lock:
            s = _load_state()
            s["consecutive_errors"] = 0
            _save_state(s)
    except Exception as e:
        _audit.log_error(f"agent_runner cycle error: {e}")
        with _lock:
            s = _load_state()
            s["consecutive_errors"] = s.get("consecutive_errors", 0) + 1
            if s["consecutive_errors"] >= C.MAX_CONSECUTIVE_ERRORS:
                s[C.EMERGENCY_STOP_KEY] = True
                _audit.log_emergency_stop(
                    f"Auto-stopped after {C.MAX_CONSECUTIVE_ERRORS} consecutive errors. "
                    f"Last error: {e}"
                )
            _save_state(s)


def schedule_agent_loop(scheduler) -> None:
    """
    Register the agent loop with the existing APScheduler instance.
    Called from scheduler.py alongside the scan scheduler.
    """
    from apscheduler.triggers.interval import IntervalTrigger

    scheduler.add_job(
        func        = run_agent_loop_once,
        trigger     = IntervalTrigger(seconds=C.DECISION_LOOP_INTERVAL_SECONDS),
        id          = "agent_decision_loop",
        name        = "Agent Decision Loop",
        replace_existing = True,
        max_instances    = 1,   # never run two cycles simultaneously
    )


class AgentRunner:
    """Convenience wrapper used by pages/5_Agent.py for UI integration."""

    def get_state(self) -> dict:
        return get_state()

    def start(self) -> None:
        set_running(True)

    def stop(self) -> None:
        set_running(False)

    def emergency_stop(self, reason: str = "user triggered emergency stop") -> None:
        set_emergency_stop(True, reason)

    def reset_emergency_stop(self) -> None:
        set_emergency_stop(False)

    def unlock_live(self) -> None:
        unlock_live_mode()

    def run_cycle_now(self) -> None:
        """Force one cycle immediately — used for testing from the UI.
        Bypasses the running check so it works even when the agent is paused.
        The resulting decision is recorded in the audit log as normal."""
        run_agent_loop_once(force=True)

    def get_paper_days(self) -> int:
        return _audit.get_paper_trade_days()

    def get_paper_stats(self) -> dict:
        return _audit.get_paper_stats()

    def get_recent_audit(self, limit: int = 50) -> list:
        return _audit.get_recent_audit(limit)

    def get_trades(self, status: str = "all") -> list:
        return _audit.get_trades(status)

    def get_open_positions(self) -> list:
        return _monitor.get_open_positions()

    def get_risk_limits(self, wallet_usd: float) -> dict:
        return _guard.get_limits_summary(wallet_usd)

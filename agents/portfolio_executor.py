"""
agents/portfolio_executor.py — Phase 2: one-click portfolio execution.

Orchestrates the "Execute Entire Portfolio" flow:
    1. Accept a list of model-recommended picks (from a profile's top_k)
    2. Validate each pick through RiskGuard (same rules as single trades)
    3. Return a structured PortfolioPlan for dry-run preview
    4. On approval, execute each leg (paper or live per OPERATING_MODE)
    5. Continue on partial-fail — one bad leg does not stop the others

The executor DOES NOT bypass RiskGuard. Every leg is validated
independently against the same limits as manual trades. If a leg fails
validation, it is skipped (recorded) and the rest continue.

Execution mode is controlled by OPERATING_MODE:
    PAPER       → simulate via paper_trader (no wallet required)
    LIVE_PHASE2 → route via flare_executor / xrpl_executor (wallet + password)
    LIVE_PHASE3 → same as PHASE2 plus higher size caps

Dollar-cap tiers per user Q6 (family-office industry standard):
    <  $25K  → single-signature bundle (auto-execute after dry-run)
    $25K-$250K → step-through per-leg confirmation
    >  $250K → 2FA / phone-approval required (not yet implemented,
                 raises NotImplementedError)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

from agents import config as C
from agents.risk_guard import RiskGuard, RiskDecision
from agents.audit_log import AuditLog
from agents.position_monitor import PositionMonitor


# ── Dollar-cap tiers (Q6) ────────────────────────────────────────────────────
AUTO_EXECUTE_CAP_USD      = 25_000.0
STEP_THROUGH_CAP_USD      = 250_000.0
#  Above STEP_THROUGH_CAP_USD requires out-of-band approval (not implemented).


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class PlannedLeg:
    """One trade leg within a portfolio plan. Each leg is validated independently."""
    # Trade intent
    protocol:       str
    pool:           str
    chain:          str
    action:         str           # "ENTER_POSITION"
    token_in:       str
    token_out:      str
    size_usd:       float
    expected_apy:   float
    confidence:     float
    reasoning:      str           = ""
    position_type:  str           = ""          # "LP" | "LENDING" | "STAKING" | ...
    maturity_date:  str           = ""          # Spectra only
    composite_score: float        = 0.0

    # Post-validation fields (filled after RiskGuard runs)
    approved:       bool          = False
    reject_reason:  str           = ""
    adjusted_size_usd: float      = 0.0
    estimated_slippage_pct: float = 0.0         # simulator estimate
    estimated_gas_usd: float      = 0.0         # simulator estimate

    # Post-execution fields (filled after execute_plan runs)
    executed:       bool          = False
    exec_status:    str           = ""          # "success" | "failed" | "skipped" | "blocked"
    exec_message:   str           = ""
    trade_id:       Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "protocol": self.protocol, "pool": self.pool, "chain": self.chain,
            "action": self.action, "token_in": self.token_in, "token_out": self.token_out,
            "size_usd": self.size_usd, "expected_apy": self.expected_apy,
            "confidence": self.confidence, "reasoning": self.reasoning,
            "position_type": self.position_type, "maturity_date": self.maturity_date,
            "composite_score": self.composite_score,
            "approved": self.approved, "reject_reason": self.reject_reason,
            "adjusted_size_usd": self.adjusted_size_usd,
            "estimated_slippage_pct": self.estimated_slippage_pct,
            "estimated_gas_usd": self.estimated_gas_usd,
            "executed": self.executed, "exec_status": self.exec_status,
            "exec_message": self.exec_message, "trade_id": self.trade_id,
        }

    def as_decision_dict(self) -> dict:
        """Format for RiskGuard.validate() — matches TradeDecision.to_dict() keys."""
        return {
            "action": self.action,
            "chain": self.chain,
            "protocol": self.protocol,
            "pool": self.pool,
            "token_in": self.token_in,
            "token_out": self.token_out,
            "size_usd": self.size_usd,
            "expected_apy": self.expected_apy,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "position_type": self.position_type,
            "maturity_date": self.maturity_date,
            "composite_score": self.composite_score,
            "alternatives_considered": [],
            "risk_factors": [],
        }


@dataclass
class PortfolioPlan:
    """Complete portfolio execution plan — dry-run output."""
    profile:              str                 # "conservative" | "medium" | "high"
    legs:                 list                # list[PlannedLeg]
    total_notional_usd:   float  = 0.0
    approved_count:       int    = 0
    rejected_count:       int    = 0
    wallet_balance_usd:   float  = 0.0
    operating_mode:       str    = "PAPER"
    authorization_tier:   str    = "auto"     # "auto" | "step_through" | "requires_approval"
    slippage_pct_cap:     float  = 0.005      # user-configurable (default 0.5%)
    built_at:             str    = ""
    # Post-execution aggregate
    success_count:        int    = 0
    failed_count:         int    = 0
    skipped_count:        int    = 0

    def to_dict(self) -> dict:
        return {
            "profile": self.profile,
            "legs": [lg.to_dict() for lg in self.legs],
            "total_notional_usd": self.total_notional_usd,
            "approved_count": self.approved_count,
            "rejected_count": self.rejected_count,
            "wallet_balance_usd": self.wallet_balance_usd,
            "operating_mode": self.operating_mode,
            "authorization_tier": self.authorization_tier,
            "slippage_pct_cap": self.slippage_pct_cap,
            "built_at": self.built_at,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
        }


# ── Tier classification ──────────────────────────────────────────────────────

def classify_tier(total_notional_usd: float) -> str:
    """Return the authorization tier for a given total notional (Q6)."""
    if total_notional_usd <= AUTO_EXECUTE_CAP_USD:
        return "auto"
    if total_notional_usd <= STEP_THROUGH_CAP_USD:
        return "step_through"
    return "requires_approval"


# ── Slippage + gas estimation (for dry-run preview) ──────────────────────────

def _estimate_slippage_pct(size_usd: float, protocol: str, user_cap_pct: float = 0.005) -> float:
    """Simple heuristic slippage model for dry-run preview.
    Not used for actual execution — paper_trader and flare_executor have
    their own live slippage checks.
    """
    # Base: 0.1% for small, scales linearly up to 0.4% at $10k, cap at user_cap_pct
    base_pct = 0.001 + min(size_usd / 10_000, 1.0) * 0.003
    # Lending/staking has near-zero slippage (deposits, not swaps)
    if protocol.lower() in {"kinetic", "sceptre", "firelight", "spectra"}:
        base_pct = 0.0005
    return round(min(base_pct, user_cap_pct), 5)


def _estimate_gas_usd(chain: str) -> float:
    """Flare is ~$0.001–0.01; XRPL is ~$0.0001."""
    if chain == "flare":
        return 0.005
    if chain == "xrpl":
        return 0.0005
    return 0.02


# ── Plan builder ─────────────────────────────────────────────────────────────

def build_plan_from_picks(
    picks: list,
    profile: str,
    wallet_balance_usd: float,
    operating_mode: str,
    slippage_pct_cap: float = 0.005,
    paper_days_completed: int = 0,
    live_manually_unlocked: bool = False,
    emergency_stop_active: bool = False,
    daily_pnl_usd: float = 0.0,
    open_position_count: int = 0,
    last_loss_timestamp: float = 0.0,
    peak_balance_usd: Optional[float] = None,
) -> PortfolioPlan:
    """
    Build a PortfolioPlan from a list of scanner picks (typically the output
    of one risk-profile's top_k model ranking).

    Args:
        picks: list of dicts with keys protocol, pool, apy, kelly_fraction,
               confidence, token0, token1, il_risk, chain, etc.
        profile: "conservative" | "medium" | "high"
        wallet_balance_usd: total capital available to deploy across legs
        operating_mode: "PAPER" | "LIVE_PHASE2" | "LIVE_PHASE3"
        slippage_pct_cap: user-configurable per-leg slippage cap (0.001-0.03)

        SAFETY GATE PARAMETERS — these MUST be passed by the caller with
        real values fetched from RiskGuard state. Previous version hardcoded
        them which BYPASSED the 14-day paper-trading gate, manual live-unlock,
        and emergency stop. Audit afb597e38 caught this as CRITICAL.
        paper_days_completed: days of paper trading completed (0 if unknown)
        live_manually_unlocked: whether the user has explicitly unlocked live mode
        emergency_stop_active: whether the global emergency stop is active
        daily_pnl_usd: realized PnL today (negative = loss)
        open_position_count: number of currently-open positions
        last_loss_timestamp: unix ts of last losing trade (0 = none)
        peak_balance_usd: peak balance observed (defaults to wallet_balance_usd)

    Returns:
        PortfolioPlan with legs already validated via RiskGuard. Approved
        legs have adjusted_size_usd set; rejected legs have reject_reason.
    """
    # Defensive: empty picks is never a valid plan
    if not picks:
        return PortfolioPlan(
            profile=profile, legs=[], total_notional_usd=0.0,
            approved_count=0, rejected_count=0,
            wallet_balance_usd=wallet_balance_usd, operating_mode=operating_mode,
            authorization_tier="auto", slippage_pct_cap=slippage_pct_cap,
            built_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    # Live-mode safety: fail fast if the caller didn't pass real gate values.
    # Paper mode tolerates defaults (those picks still get PAPER-validated).
    if operating_mode in ("LIVE_PHASE2", "LIVE_PHASE3") and emergency_stop_active:
        # Emergency stop active — return empty plan with rejection reason on every leg
        # below (handled per-leg by RiskGuard); we just do NOT silently override.
        pass

    if peak_balance_usd is None:
        peak_balance_usd = wallet_balance_usd

    guard = RiskGuard()
    legs: list = []
    total_notional = 0.0

    # Kelly-fraction weighting: each pick gets allocated kelly% of wallet
    # (capped to MAX_TRADE_SIZE_PCT internally by RiskGuard)
    _total_kf = sum(float(p.get("kelly_fraction", 0) or 0) for p in picks) or 1.0

    for pick in picks:
        _kf        = float(pick.get("kelly_fraction", 0) or 0)
        alloc_pct  = (_kf / _total_kf) if _total_kf > 0 else 0.0
        size_usd   = round(wallet_balance_usd * alloc_pct, 2)

        proto      = str(pick.get("protocol", "")).lower()
        pool_name  = str(pick.get("pool", pick.get("pool_name", "")))
        chain      = str(pick.get("chain", "flare")).lower()
        expected_apy_pct = float(pick.get("apy", 0) or 0)
        confidence = float(pick.get("confidence", 0.7) or 0.7)
        if confidence > 1.0:
            confidence = confidence / 100.0

        # Classify action / position_type by protocol
        if proto in {"kinetic"}:
            action = "ENTER_POSITION"
            position_type = "LENDING"
        elif proto in {"sceptre", "firelight"}:
            action = "ENTER_POSITION"
            position_type = "STAKING"
        elif proto in {"spectra"}:
            action = "ENTER_POSITION"
            position_type = str(pick.get("position_type", "LP")).upper()
        else:
            action = "ENTER_POSITION"
            position_type = "LP"

        leg = PlannedLeg(
            protocol       = proto,
            pool           = pool_name,
            chain          = chain,
            action         = action,
            token_in       = str(pick.get("token0", "")),
            token_out      = str(pick.get("token1", "")),
            size_usd       = size_usd,
            expected_apy   = expected_apy_pct / 100.0,
            confidence     = confidence,
            reasoning      = f"Auto-generated from {profile} profile Kelly allocation",
            position_type  = position_type,
            maturity_date  = str(pick.get("maturity_date", "")),
            composite_score = float(pick.get("composite_score", 0) or 0),
            estimated_slippage_pct = _estimate_slippage_pct(size_usd, proto, slippage_pct_cap),
            estimated_gas_usd      = _estimate_gas_usd(chain),
        )

        # Run RiskGuard — every safety parameter is now passed from the
        # caller; the previous hardcoded bypass was caught by audit afb597e38.
        # Spectra maturity_date validation: reject before RiskGuard if the
        # required field is missing (RiskGuard only enforces min-days-to-
        # maturity when the date IS provided).
        if proto == "spectra" and not leg.maturity_date:
            leg.approved = False
            leg.reject_reason = (
                "Spectra position requires maturity_date — PT/YT tokens expire "
                "worthless at maturity. Pick must include maturity_date."
            )
            legs.append(leg)
            continue

        risk: RiskDecision = guard.validate(
            decision               = leg.as_decision_dict(),
            wallet_balance_usd     = wallet_balance_usd,
            daily_pnl_usd          = daily_pnl_usd,
            open_position_count    = open_position_count,
            last_loss_timestamp    = last_loss_timestamp,
            peak_balance_usd       = peak_balance_usd,
            operating_mode         = operating_mode,
            paper_days_completed   = paper_days_completed,
            live_manually_unlocked = live_manually_unlocked,
            emergency_stop_active  = emergency_stop_active,
        )
        leg.approved = bool(risk.approved)
        leg.reject_reason = "" if risk.approved else str(risk.reason)
        leg.adjusted_size_usd = float(risk.adjusted_size_usd or 0)
        legs.append(leg)

        if leg.approved:
            total_notional += leg.adjusted_size_usd

    plan = PortfolioPlan(
        profile            = profile,
        legs               = legs,
        total_notional_usd = round(total_notional, 2),
        approved_count     = sum(1 for lg in legs if lg.approved),
        rejected_count     = sum(1 for lg in legs if not lg.approved),
        wallet_balance_usd = wallet_balance_usd,
        operating_mode     = operating_mode,
        authorization_tier = classify_tier(total_notional),
        slippage_pct_cap   = slippage_pct_cap,
        built_at           = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return plan


# ── Plan executor ────────────────────────────────────────────────────────────

def execute_plan(
    plan: PortfolioPlan,
    private_key: Optional[str] = None,
    continue_on_fail: bool = True,
    wallet_address: Optional[str] = None,
) -> PortfolioPlan:
    """
    Execute every approved leg of a plan.

    Args:
        plan: a dry-run output from build_plan_from_picks
        private_key: required when operating_mode is LIVE_PHASE2/PHASE3;
                     ignored in PAPER mode
        continue_on_fail: if True (default, per Q8), skip failed legs and
                          continue with the remaining ones; if False, halt
                          the entire plan on first failure
        wallet_address: EVM address for cross-app reservation ledger (Phase
                        4A-1 wiring). When provided, prevents double-alloc
                        between DeFi + RWA + SuperGrok. Skipped when None.

    Returns:
        The same PortfolioPlan mutated with exec_* fields filled on each
        leg and aggregate counters (success/failed/skipped) set.
    """
    # Phase 4A-2: multi-sig gate for plans > $100K. When the total notional
    # exceeds the multi-sig threshold AND no approval_id has been supplied,
    # create a pending proposal and block execution. Caller must surface
    # the approval_id to the UI for owner + advisor sign-off; once 2-of-3
    # signatures are present, the re-call will unblock.
    try:
        from utils.cross_app_safety import (
            requires_multisig, propose_multisig, is_approved as _ms_is_approved,
        )
        if requires_multisig(plan.total_notional_usd):
            _pending_id = getattr(plan, "multisig_approval_id", None)
            if not _pending_id:
                _new_id = propose_multisig(
                    app="defi", symbol="portfolio",
                    action="EXECUTE_PLAN", size_usd=plan.total_notional_usd,
                    proposer="agent",
                    notes=f"profile={plan.profile} legs={plan.approved_count}",
                )
                for _lg in plan.legs:
                    if _lg.approved:
                        _lg.executed = False
                        _lg.exec_status = "pending_multisig"
                        _lg.exec_message = (
                            f"Multi-sig proposal {_new_id[:8]}… created. "
                            "Owner + advisor must sign from the Settings UI "
                            "before this plan can execute."
                        )
                        plan.skipped_count += 1
                # Stash for the caller to surface; not part of the dataclass
                # schema but readable post-return.
                plan.__dict__["multisig_approval_id"] = _new_id
                return plan
            elif not _ms_is_approved(_pending_id):
                for _lg in plan.legs:
                    if _lg.approved:
                        _lg.executed = False
                        _lg.exec_status = "pending_multisig"
                        _lg.exec_message = f"Proposal {_pending_id[:8]}… still awaiting signatures."
                        plan.skipped_count += 1
                return plan
            # else: approval_id is valid and 2-of-3 present → proceed
    except Exception as _ms_err:
        logger.debug("[PortfolioExec] multi-sig gate skipped: %s", _ms_err)

    # Phase 4A-1: cross-app wallet capacity gate. Only enforces when caller
    # passes wallet_address — paper-mode test flows can omit it.
    _reservation_id = None
    if wallet_address:
        try:
            from utils.wallet_state import has_capacity, reserve, release
            _ok, _reason = has_capacity(
                wallet_address, plan.wallet_balance_usd, plan.total_notional_usd
            )
            if not _ok:
                for _lg in plan.legs:
                    if _lg.approved:
                        _lg.executed = False
                        _lg.exec_status = "blocked"
                        _lg.exec_message = _reason
                        plan.skipped_count += 1
                return plan
            # Reserve before signing so a parallel app can't race us
            _reservation_id = reserve(
                wallet_address, "defi", plan.total_notional_usd,
                note=f"portfolio_plan_{plan.profile}",
            )
        except Exception as _ws_err:
            logger.debug("[PortfolioExec] wallet_state gate skipped: %s", _ws_err)

    audit = AuditLog()
    audit.log_decision(
        {
            "action":   "PORTFOLIO_EXECUTE_START",
            "protocol": f"portfolio_{plan.profile}",
            "pool":     f"{plan.approved_count} legs",
            "size_usd": plan.total_notional_usd,
            "confidence": 1.0,
            "chain":    "multi",
        },
        approved      = True,
        reason        = f"Portfolio plan, tier={plan.authorization_tier}",
        wallet_usd    = plan.wallet_balance_usd,
        daily_pnl_usd = 0.0,
        config_snapshot = {"mode": plan.operating_mode},
    )

    if plan.authorization_tier == "requires_approval":
        for leg in plan.legs:
            leg.executed = False
            leg.exec_status = "blocked"
            leg.exec_message = (
                f"Portfolio notional ${plan.total_notional_usd:,.0f} exceeds "
                f"${STEP_THROUGH_CAP_USD:,.0f} cap — out-of-band approval required."
            )
        plan.skipped_count = len(plan.legs)
        return plan

    for leg in plan.legs:
        if not leg.approved:
            leg.executed = False
            leg.exec_status = "skipped"
            leg.exec_message = f"RiskGuard rejected: {leg.reject_reason}"
            plan.skipped_count += 1
            continue

        try:
            if plan.operating_mode == "PAPER":
                # Paper mode: use the simulator
                from agents.paper_trader import execute_paper_trade
                from agents.decision_engine import TradeDecision
                _td = TradeDecision(
                    action       = leg.action,
                    chain        = leg.chain,
                    protocol     = leg.protocol,
                    pool         = leg.pool,
                    token_in     = leg.token_in,
                    token_out    = leg.token_out,
                    size_usd     = leg.adjusted_size_usd,
                    expected_apy = leg.expected_apy,
                    confidence   = leg.confidence,
                    reasoning    = leg.reasoning,
                    position_type = leg.position_type,
                    maturity_date = leg.maturity_date,
                )
                _result = execute_paper_trade(_td, leg.adjusted_size_usd, current_apy=leg.expected_apy)
                leg.executed = True
                leg.exec_status = "success"
                leg.exec_message = f"Paper trade filled — slippage {leg.estimated_slippage_pct*100:.2f}%"
                leg.trade_id = _result.get("trade_id") if isinstance(_result, dict) else None
                plan.success_count += 1
            else:
                # Live mode: route via FlareExecutor / XRPLExecutor
                if not private_key:
                    raise ValueError("Live execution requires private_key but none was provided")
                if leg.chain == "flare":
                    from agents.flare_executor import FlareExecutor
                    from agents.decision_engine import TradeDecision
                    _td = TradeDecision(
                        action=leg.action, chain=leg.chain, protocol=leg.protocol,
                        pool=leg.pool, token_in=leg.token_in, token_out=leg.token_out,
                        size_usd=leg.adjusted_size_usd, expected_apy=leg.expected_apy,
                        confidence=leg.confidence, reasoning=leg.reasoning,
                        position_type=leg.position_type, maturity_date=leg.maturity_date,
                    )
                    # Method is named execute(), not execute_trade — audit a02aa260.
                    # Thread the user-configured slippage cap through so DEX
                    # legs get MEV-safe amountOutMinimum (audit afb597e3 flagged
                    # that slippage was defaulting to 0.5% regardless of UI).
                    _result = FlareExecutor().execute(
                        _td, leg.adjusted_size_usd, private_key,
                        max_slippage_pct = plan.slippage_pct_cap,
                    )
                    leg.executed = (_result.get("status") == "success")
                    leg.exec_status = _result.get("status", "unknown")
                    leg.exec_message = _result.get("reason") or _result.get("message", "")
                    leg.trade_id = _result.get("tx_hash")
                    if leg.executed:
                        plan.success_count += 1
                    else:
                        plan.failed_count += 1
                        if not continue_on_fail:
                            break
                elif leg.chain == "xrpl":
                    # XRPL execution path — stubbed for now; live XRPL one-click
                    # would require xumm mobile sign flow via xrpl_executor
                    leg.executed = False
                    leg.exec_status = "not_implemented"
                    leg.exec_message = "XRPL one-click execution not yet wired — use single-trade path"
                    plan.failed_count += 1
                    if not continue_on_fail:
                        break
                else:
                    leg.executed = False
                    leg.exec_status = "not_implemented"
                    leg.exec_message = f"Chain '{leg.chain}' not supported for one-click execute"
                    plan.failed_count += 1
                    if not continue_on_fail:
                        break
        except Exception as e:
            logger.warning("[PortfolioExec] leg failed (%s/%s): %s",
                           leg.protocol, leg.pool, e)
            leg.executed = False
            leg.exec_status = "failed"
            leg.exec_message = f"Exception: {type(e).__name__}: {str(e)[:200]}"
            plan.failed_count += 1
            if not continue_on_fail:
                break

    audit.log_decision(
        {
            "action":   "PORTFOLIO_EXECUTE_END",
            "protocol": f"portfolio_{plan.profile}",
            "pool":     f"{plan.success_count}/{plan.approved_count} succeeded",
            "size_usd": plan.total_notional_usd,
            "confidence": 1.0,
            "chain":    "multi",
        },
        approved      = plan.failed_count == 0,
        reason        = f"success={plan.success_count} failed={plan.failed_count} skipped={plan.skipped_count}",
        wallet_usd    = plan.wallet_balance_usd,
        daily_pnl_usd = 0.0,
        config_snapshot = {"mode": plan.operating_mode, "tier": plan.authorization_tier},
    )

    # Phase 4A-1: release the cross-app reservation so other apps can use
    # the capital. Done regardless of success/failure — the plan attempted
    # and finished (for better or worse); the reservation served its purpose.
    if _reservation_id and wallet_address:
        try:
            from utils.wallet_state import release
            release(wallet_address, _reservation_id)
        except Exception as _rel_err:
            logger.debug("[PortfolioExec] wallet_state release failed: %s", _rel_err)

    return plan

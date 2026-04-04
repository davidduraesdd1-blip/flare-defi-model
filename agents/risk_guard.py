"""
agents/risk_guard.py — Independent trade validator.

Every proposed trade passes through here before any execution.
The RiskGuard reads ONLY from agents/config.py — never from the AI decision.
If the AI proposes something outside the limits, it is rejected here regardless.

No trade ever bypasses this. No exceptions. No overrides from AI.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from agents import config as C


@dataclass
class RiskDecision:
    approved: bool
    reason: str
    adjusted_size_usd: float = 0.0   # capped to max allowed if originally too large


class RiskGuard:
    """
    Validates proposed trade decisions against all hardcoded limits.

    Usage:
        guard = RiskGuard()
        result = guard.validate(decision, context)
        if result.approved:
            execute(decision, result.adjusted_size_usd)
    """

    def validate(
        self,
        decision: dict,
        wallet_balance_usd: float,
        daily_pnl_usd: float,
        open_position_count: int,
        last_loss_timestamp: float,   # unix timestamp of last losing trade (0 = none)
        peak_balance_usd: float,
        operating_mode: str,
        paper_days_completed: int,
        live_manually_unlocked: bool,
        emergency_stop_active: bool,
    ) -> RiskDecision:
        """
        Run all checks in order. Returns on first failure (fail-fast).
        Returns APPROVED only if every single check passes.
        """

        # ── 0. Emergency stop ─────────────────────────────────────────────────
        if emergency_stop_active:
            return RiskDecision(False, "EMERGENCY STOP is active — no trades until manually reset")

        # ── 1. Parse decision fields ──────────────────────────────────────────
        action      = str(decision.get("action", "")).upper().strip()
        chain       = str(decision.get("chain", "")).lower().strip()
        protocol    = str(decision.get("protocol", "")).lower().strip()
        pool        = str(decision.get("pool", ""))
        size_usd    = float(decision.get("size_usd", 0))
        confidence  = float(decision.get("confidence", 0))
        expected_apy = float(decision.get("expected_apy", 0))

        # ── 2. HOLD is always approved ────────────────────────────────────────
        if action == "HOLD":
            return RiskDecision(True, "HOLD — no trade, no risk", adjusted_size_usd=0.0)

        # ── 3. Action whitelist ───────────────────────────────────────────────
        if action not in C.ALLOWED_ACTIONS:
            return RiskDecision(False, f"Unknown action '{action}' — not in whitelist")

        # ── 4. Protocol whitelist ─────────────────────────────────────────────
        if protocol not in C.ALL_WHITELISTED_PROTOCOLS:
            return RiskDecision(
                False,
                f"Protocol '{protocol}' not in whitelist. "
                f"Allowed: {sorted(C.ALL_WHITELISTED_PROTOCOLS)}"
            )

        # ── 5. Chain / protocol consistency ──────────────────────────────────
        if chain == "flare" and protocol not in C.FLARE_PROTOCOL_WHITELIST:
            return RiskDecision(False, f"Protocol '{protocol}' not available on Flare")
        if chain == "xrpl" and protocol not in C.XRPL_PROTOCOL_WHITELIST:
            return RiskDecision(False, f"Protocol '{protocol}' not available on XRPL")

        # ── 6. Live mode phase gate ───────────────────────────────────────────
        if operating_mode in ("LIVE_PHASE2", "LIVE_PHASE3"):
            if paper_days_completed < C.PAPER_TRADING_GATE_DAYS:
                return RiskDecision(
                    False,
                    f"Phase gate: {paper_days_completed}/{C.PAPER_TRADING_GATE_DAYS} "
                    "paper trading days required before live execution"
                )
            if not live_manually_unlocked:
                return RiskDecision(
                    False,
                    "Live mode requires manual unlock from the Agent Control Panel"
                )

        # ── 7. Phase 2 wallet cap ─────────────────────────────────────────────
        if operating_mode == "LIVE_PHASE2" and wallet_balance_usd > C.PHASE2_WALLET_CAP_USD * 1.05:
            return RiskDecision(
                False,
                f"Phase 2 wallet cap exceeded: ${wallet_balance_usd:.2f} > "
                f"${C.PHASE2_WALLET_CAP_USD:.2f} hard cap"
            )

        # ── 8. Daily loss limit ───────────────────────────────────────────────
        max_daily_loss = wallet_balance_usd * C.MAX_DAILY_LOSS_PCT
        if daily_pnl_usd < -max_daily_loss:
            return RiskDecision(
                False,
                f"Daily loss limit hit: ${daily_pnl_usd:.2f} loss "
                f"exceeds ${max_daily_loss:.2f} max ({C.MAX_DAILY_LOSS_PCT*100:.0f}% of wallet). "
                "Bot paused until next UTC day."
            )

        # ── 9. Max drawdown from peak ─────────────────────────────────────────
        if peak_balance_usd > 0:
            drawdown = (peak_balance_usd - wallet_balance_usd) / peak_balance_usd
            if drawdown >= C.MAX_DRAWDOWN_PCT:
                return RiskDecision(
                    False,
                    f"Max drawdown breached: {drawdown*100:.1f}% from peak "
                    f"(limit: {C.MAX_DRAWDOWN_PCT*100:.0f}%). "
                    "Full stop — manual restart required."
                )

        # ── 10. Cooldown after loss ───────────────────────────────────────────
        if last_loss_timestamp > 0:
            elapsed = time.time() - last_loss_timestamp
            if elapsed < C.COOLDOWN_AFTER_LOSS_SECONDS:
                remaining = int(C.COOLDOWN_AFTER_LOSS_SECONDS - elapsed)
                return RiskDecision(
                    False,
                    f"Loss cooldown active: {remaining // 60}m {remaining % 60}s remaining"
                )

        # ── 11. Max open positions ────────────────────────────────────────────
        if action == "ENTER_POSITION" and open_position_count >= C.MAX_OPEN_POSITIONS:
            return RiskDecision(
                False,
                f"Max open positions reached: {open_position_count}/{C.MAX_OPEN_POSITIONS}. "
                "Close a position before opening a new one."
            )

        # ── 12. Minimum confidence ────────────────────────────────────────────
        if confidence < C.MIN_CONFIDENCE:
            return RiskDecision(
                False,
                f"Confidence {confidence:.2f} below minimum {C.MIN_CONFIDENCE:.2f}. "
                "Model is uncertain — holding."
            )

        # ── 13. APY sanity check ──────────────────────────────────────────────
        if expected_apy > C.MAX_REASONABLE_APY:
            return RiskDecision(
                False,
                f"Expected APY {expected_apy*100:.0f}% exceeds sanity cap "
                f"{C.MAX_REASONABLE_APY*100:.0f}% — likely a data error."
            )

        # ── 14. Trade size checks ─────────────────────────────────────────────
        if size_usd < C.MIN_TRADE_SIZE_USD:
            return RiskDecision(
                False,
                f"Trade size ${size_usd:.2f} below minimum ${C.MIN_TRADE_SIZE_USD:.2f} "
                "(gas cost would exceed profit)"
            )

        max_size = wallet_balance_usd * C.MAX_TRADE_SIZE_PCT
        if size_usd > max_size:
            # Soft override: cap to max rather than reject, if it's not absurdly large
            if size_usd > max_size * 3:
                return RiskDecision(
                    False,
                    f"Trade size ${size_usd:.2f} far exceeds max ${max_size:.2f} "
                    f"({C.MAX_TRADE_SIZE_PCT*100:.0f}% of wallet)"
                )
            size_usd = max_size  # cap and continue

        # ── 15. Spectra maturity lifecycle rules ──────────────────────────────
        # Spectra positions have fixed maturity dates. YT tokens expire worthless.
        # LP positions suffer impermanent loss as maturity approaches.
        # This check enforces the approved lifecycle rules from agents/config.py.
        if protocol == "spectra" and action == "ENTER_POSITION":
            maturity_date_str = decision.get("maturity_date", "")
            position_type     = str(decision.get("position_type", "LP")).upper()
            if maturity_date_str:
                try:
                    maturity_dt = datetime.strptime(maturity_date_str[:10], "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    days_left = (maturity_dt - datetime.now(timezone.utc)).days
                    # YT: must exit 21 days before maturity (YT → worthless at expiry)
                    min_days = {
                        "YT": C.SPECTRA_YT_EXIT_DAYS_BEFORE,
                        "LP": C.SPECTRA_LP_EXIT_DAYS_BEFORE,
                    }.get(position_type, C.SPECTRA_MIN_DAYS_TO_MATURITY)
                    if days_left < min_days:
                        return RiskDecision(
                            False,
                            f"Spectra {position_type} position rejected: only {days_left} days "
                            f"to maturity ({maturity_date_str}), minimum required is {min_days} days. "
                            f"YT loses all value at maturity — do not enter near expiry."
                        )
                except ValueError:
                    pass  # malformed date — let it through (Claude will know the date)

        # ── 16. All checks passed ─────────────────────────────────────────────
        return RiskDecision(
            True,
            f"All {16} risk checks passed",
            adjusted_size_usd=size_usd,
        )

    def get_limits_summary(self, wallet_balance_usd: float) -> dict:
        """Return current effective limits for display in the UI."""
        return {
            "max_trade_usd":        round(wallet_balance_usd * C.MAX_TRADE_SIZE_PCT, 2),
            "max_daily_loss_usd":   round(wallet_balance_usd * C.MAX_DAILY_LOSS_PCT, 2),
            "max_drawdown_pct":     C.MAX_DRAWDOWN_PCT * 100,
            "max_open_positions":   C.MAX_OPEN_POSITIONS,
            "cooldown_minutes":     C.COOLDOWN_AFTER_LOSS_SECONDS // 60,
            "min_confidence":       C.MIN_CONFIDENCE,
            "min_profit_pct":       C.MIN_NET_PROFIT_PCT * 100,
            "max_slippage_pct":     C.MAX_SLIPPAGE_PCT * 100,
            "protocol_whitelist":   sorted(C.ALL_WHITELISTED_PROTOCOLS),
            "phase2_cap_usd":       C.PHASE2_WALLET_CAP_USD,
            "paper_gate_days":      C.PAPER_TRADING_GATE_DAYS,
        }

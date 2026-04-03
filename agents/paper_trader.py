"""
agents/paper_trader.py — Phase 1 paper trading executor.

Receives an approved TradeDecision, simulates realistic execution
(with slippage model), records the trade, and updates position state.
Zero real transactions. Zero real wallets needed.
"""

import random
import time
from datetime import datetime, timezone

from agents.config import MAX_SLIPPAGE_PCT, OPERATING_MODE
from agents.decision_engine import TradeDecision
from agents.position_monitor import PositionMonitor
from agents.audit_log import AuditLog

_monitor = PositionMonitor()
_audit   = AuditLog()


def _simulate_slippage(size_usd: float) -> float:
    """
    Realistic slippage model for Flare DEXs.
    Small trades: 0.05–0.20%. Large trades: scale up.
    Never exceeds MAX_SLIPPAGE_PCT (0.5%) — RiskGuard would have rejected otherwise.
    """
    base = 0.001  # 0.1% base
    size_factor = min(size_usd / 10_000, 1.0) * 0.003  # up to +0.3% for large trades
    noise = random.uniform(-0.0005, 0.0005)
    return max(0, min(MAX_SLIPPAGE_PCT, base + size_factor + noise))


def _simulate_gas(chain: str) -> float:
    """
    Realistic gas cost in USD.
    Flare: ~$0.001–0.01 (very cheap EVM).
    XRPL:  ~$0.0001–0.001 (near-zero).
    """
    if chain == "flare":
        return random.uniform(0.001, 0.008)
    elif chain == "xrpl":
        return random.uniform(0.0001, 0.001)
    return 0.0


def execute_paper_trade(
    decision: TradeDecision,
    adjusted_size_usd: float,
    entry_price: float = 1.0,
) -> dict:
    """
    Simulate executing the approved decision in paper mode.

    Args:
        decision:          Approved TradeDecision from decision_engine
        adjusted_size_usd: Size after RiskGuard cap adjustment
        entry_price:       Current asset price (for position tracking)

    Returns:
        dict with trade_id, fill details, and updated paper P&L
    """
    slippage = _simulate_slippage(adjusted_size_usd)
    gas_usd  = _simulate_gas(decision.chain)

    # Effective cost after slippage and gas
    effective_size = adjusted_size_usd * (1 + slippage) + gas_usd
    fill_price     = entry_price * (1 + slippage)

    if decision.action == "ENTER_POSITION":
        trade_id = _monitor.open_position(
            chain        = decision.chain,
            protocol     = decision.protocol,
            pool         = decision.pool,
            action       = decision.action,
            token_in     = decision.token_in,
            token_out    = decision.token_out,
            size_usd     = adjusted_size_usd,
            entry_price  = fill_price,
            expected_apy = decision.expected_apy,
            confidence   = decision.confidence,
            reasoning    = decision.reasoning,
            mode         = "PAPER",
            fill_price   = fill_price,
            slippage_pct = slippage,
            gas_usd      = gas_usd,
        )
        return {
            "trade_id":     trade_id,
            "action":       "ENTER_POSITION",
            "status":       "simulated_open",
            "size_usd":     adjusted_size_usd,
            "fill_price":   fill_price,
            "slippage_pct": round(slippage * 100, 4),
            "gas_usd":      round(gas_usd, 6),
            "effective_cost_usd": round(effective_size, 4),
            "chain":        decision.chain,
            "protocol":     decision.protocol,
            "pool":         decision.pool,
            "reasoning":    decision.reasoning,
        }

    elif decision.action == "EXIT_POSITION":
        # Find oldest open position on same protocol/pool
        positions = _monitor.get_open_positions()
        matched = [
            p for p in positions
            if p["protocol"] == decision.protocol and p["pool"] == decision.pool
        ]
        if not matched:
            # Exit any position on the protocol if pool doesn't match exactly
            matched = [p for p in positions if p["protocol"] == decision.protocol]
        if not matched:
            return {"action": "EXIT_POSITION", "status": "no_position_to_close",
                    "reasoning": decision.reasoning}

        pos = matched[0]
        # Simulate P&L: use expected APY to estimate accrued yield
        entry_ts_str = pos.get("entry_timestamp", "")
        try:
            entry_ts = datetime.strptime(entry_ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc
            ).timestamp()
            hours_held = (time.time() - entry_ts) / 3600
        except Exception:
            hours_held = 1.0

        apy = pos.get("expected_apy", 0.05)
        # In paper mode, simulate yield accrual: P&L = size × APY × (hours/8760)
        accrued_yield = pos["size_usd"] * apy * (hours_held / 8760)
        exit_slippage = _simulate_slippage(pos["size_usd"])
        exit_gas      = _simulate_gas(pos["chain"])
        realized_pnl  = accrued_yield - (pos["size_usd"] * exit_slippage) - exit_gas

        _monitor.close_position(
            trade_id     = pos["trade_id"],
            exit_price   = fill_price,
            realized_pnl = realized_pnl,
            reason       = decision.reasoning,
        )
        return {
            "trade_id":      pos["trade_id"],
            "action":        "EXIT_POSITION",
            "status":        "simulated_closed",
            "size_usd":      pos["size_usd"],
            "hours_held":    round(hours_held, 2),
            "accrued_yield": round(accrued_yield, 4),
            "realized_pnl":  round(realized_pnl, 4),
            "chain":         pos["chain"],
            "protocol":      pos["protocol"],
            "pool":          pos["pool"],
            "reasoning":     decision.reasoning,
        }

    elif decision.action == "HOLD":
        return {
            "action":    "HOLD",
            "status":    "no_trade",
            "reasoning": decision.reasoning,
        }

    return {"action": decision.action, "status": "unsupported_in_paper_mode"}

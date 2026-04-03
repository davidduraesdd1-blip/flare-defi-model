"""
agents/ — Autonomous Trading Agent for the Flare DeFi Model.

Architecture:
  config.py         → Hardcoded risk limits (AI cannot modify)
  audit_log.py      → Append-only SQLite audit trail
  risk_guard.py     → Validates every decision against all limits
  wallet_manager.py → AES-256-GCM encrypted key storage + wallet generation
  position_monitor.py → Open position tracking + P&L + drawdown
  data_feed.py      → Aggregates market context for Claude
  decision_engine.py → Claude claude-sonnet-4-6 decision maker
  paper_trader.py   → Phase 1: simulate trades, no real execution
  flare_executor.py → Phase 2/3: real execution on Flare (web3.py)
  xrpl_executor.py  → Phase 2/3: real execution on XRPL (xrpl-py)
  agent_runner.py   → Main decision loop (APScheduler integration)

Operating modes (set in config.py):
  PAPER        → Simulate only, no real transactions (default)
  LIVE_PHASE2  → Real execution, $1,000 hard cap, 14-day paper gate required
  LIVE_PHASE3  → Real execution at scale, limits scale proportionally

Phase gate: LIVE modes cannot activate until 14 days of paper trading
complete AND the user manually unlocks via the UI. The bot cannot
promote itself.
"""

from agents.config import OPERATING_MODE, PHASE2_WALLET_CAP_USD, PAPER_TRADING_GATE_DAYS
from agents.risk_guard import RiskGuard, RiskDecision
from agents.audit_log import AuditLog
from agents.position_monitor import PositionMonitor
from agents.agent_runner import AgentRunner

__all__ = [
    "OPERATING_MODE",
    "PHASE2_WALLET_CAP_USD",
    "PAPER_TRADING_GATE_DAYS",
    "RiskGuard",
    "RiskDecision",
    "AuditLog",
    "PositionMonitor",
    "AgentRunner",
]

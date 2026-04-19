"""
utils/audit_schema.py — Canonical cross-app audit schema.

All 3 apps (DeFi, SuperGrok, RWA) ship an identical copy so a
family-office reporting layer can reconcile audit events across them
under a single rubric.

Schema:
    event_id     : str     UUIDv4, unique across all apps
    timestamp    : str     ISO-8601 UTC, '%Y-%m-%dT%H:%M:%SZ'
    app          : str     'defi' | 'supergrok' | 'rwa'
    event_type   : str     one of EVENT_TYPES below
    canonical_risk_level : int | None    1-5 scale (see 3A-2)
    user_level   : str     'beginner' | 'intermediate' | 'advanced'
    mode         : str     'paper' | 'live' (for trade events)
    size_usd     : float   notional amount in USD (0 for non-trade events)
    approved     : bool    was this validated / approved by risk gates
    reason       : str     human-readable outcome string
    correlation_id : str | None  links related legs in one plan
    extras       : dict    app-specific overflow (stored as JSON blob)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


# ── Canonical event types (shared across apps) ──────────────────────────────

EVT_PLAN_BUILD     = "PLAN_BUILD"       # portfolio/signal plan constructed
EVT_PLAN_EXECUTE   = "PLAN_EXECUTE"     # execute_plan started
EVT_PLAN_COMPLETE  = "PLAN_COMPLETE"    # execute_plan finished
EVT_TRADE_OPEN     = "TRADE_OPEN"       # single leg opened
EVT_TRADE_CLOSE    = "TRADE_CLOSE"      # single leg closed
EVT_TRADE_BLOCK    = "TRADE_BLOCK"      # risk-guard rejected / tier blocked
EVT_AGENT_DECISION = "AGENT_DECISION"   # AI decided an action
EVT_EMERGENCY_STOP = "EMERGENCY_STOP"   # emergency stop activated / released
EVT_MODE_CHANGE    = "MODE_CHANGE"      # paper <-> live transition

EVENT_TYPES = frozenset({
    EVT_PLAN_BUILD, EVT_PLAN_EXECUTE, EVT_PLAN_COMPLETE,
    EVT_TRADE_OPEN, EVT_TRADE_CLOSE, EVT_TRADE_BLOCK,
    EVT_AGENT_DECISION, EVT_EMERGENCY_STOP, EVT_MODE_CHANGE,
})


APP_DEFI      = "defi"
APP_SUPERGROK = "supergrok"
APP_RWA       = "rwa"

APP_NAMES = frozenset({APP_DEFI, APP_SUPERGROK, APP_RWA})


# ── Event builder ───────────────────────────────────────────────────────────

def make_event(
    app: str,
    event_type: str,
    *,
    canonical_risk_level: Optional[int] = None,
    user_level: str = "beginner",
    mode: str = "paper",
    size_usd: float = 0.0,
    approved: bool = True,
    reason: str = "",
    correlation_id: Optional[str] = None,
    extras: Optional[dict] = None,
) -> dict:
    """
    Construct a canonical audit event dict. Callers should pass this to
    their app-local persistence layer (AuditLog in DeFi, database.log_trade
    in RWA/SuperGrok) alongside app-specific fields.

    Returns a dict with the unified schema (plus event_id + timestamp).
    """
    if app not in APP_NAMES:
        logger.debug("[AuditSchema] unknown app=%r; accepting anyway", app)
    if event_type not in EVENT_TYPES:
        logger.debug("[AuditSchema] unknown event_type=%r; accepting anyway", event_type)

    return {
        "event_id":             str(uuid.uuid4()),
        "timestamp":            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "app":                  str(app),
        "event_type":           str(event_type),
        "canonical_risk_level": canonical_risk_level,
        "user_level":           str(user_level),
        "mode":                 str(mode),
        "size_usd":             float(size_usd or 0.0),
        "approved":             bool(approved),
        "reason":               str(reason)[:500],
        "correlation_id":       correlation_id,
        "extras":               dict(extras or {}),
    }


def serialize_event(event: dict) -> str:
    """JSON-serialize an audit event for line-based persistence (JSONL)."""
    try:
        return json.dumps(event, default=str, separators=(",", ":"))
    except Exception as _err:
        logger.warning("[AuditSchema] serialize failed: %s", _err)
        return json.dumps({"event_id": event.get("event_id"),
                           "error": f"serialize_failed: {_err}"})


__all__ = [
    "EVT_PLAN_BUILD", "EVT_PLAN_EXECUTE", "EVT_PLAN_COMPLETE",
    "EVT_TRADE_OPEN", "EVT_TRADE_CLOSE", "EVT_TRADE_BLOCK",
    "EVT_AGENT_DECISION", "EVT_EMERGENCY_STOP", "EVT_MODE_CHANGE",
    "EVENT_TYPES", "APP_DEFI", "APP_SUPERGROK", "APP_RWA", "APP_NAMES",
    "make_event", "serialize_event",
]

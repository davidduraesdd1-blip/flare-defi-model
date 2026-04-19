"""
utils/cross_app_safety.py — Cross-app position overlap + multi-sig gating.

Two related safety layers for Level-C (24/7 autonomous) execution:

1. Position overlap detection (3C-9)
   Two apps can each hold a "USDC" position from different angles
   (DeFi lending kUSDT0 vs RWA Circle wrapper). If both apps decide to
   exit simultaneously, we'd liquidate the same capital twice. This
   module catches overlaps by token symbol + chain before any execute
   greenlight.

2. Multi-sig recovery for >$100K (3C-10)
   Per family-office standard, positions above $100K require human-
   in-the-loop approval even if the agent's confidence is high.
   Implemented as a pending-approval ledger with:
     - agent proposes → ledger entry created with unique approval_id
     - owner + advisor each sign off via a simple "approve X" action
     - when 2 of 3 signatures present, position unlocks for execute
     - expires after 24 hours if not fully approved

Both functions are pure Python + JSON file persistence — no external
services needed. Identical module ships to all 3 apps.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────────────

MULTISIG_THRESHOLD_USD   = 100_000.0   # positions above this require multi-sig
MULTISIG_REQUIRED_SIGS   = 2           # 2 of 3: agent + owner + advisor
MULTISIG_EXPIRY_SECONDS  = 24 * 3600   # 24-hour pending window
MULTISIG_TIMELOCK_SECONDS = 3600       # 1hr delay between last-sig and execute (4B-7)
MULTISIG_VOTE_COOLDOWN_SECONDS = 300   # 5-min per-role vote cooldown (4B-8)
_OVERLAP_FILE = Path(__file__).resolve().parent.parent / "data" / "cross_app_positions.json"
_MULTISIG_FILE = Path(__file__).resolve().parent.parent / "data" / "pending_multisig.json"
_LOCK = threading.Lock()


def _now() -> float:
    return time.time()


def _load(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("[CrossApp] load %s failed: %s", path.name, e)
    return {}


def _save(path: Path, data: dict) -> None:
    """Atomic write: tmp + os.replace to survive crash-during-write (4B-10).

    Retries os.replace briefly to defeat transient file-locks from OneDrive
    /Dropbox sync or Windows Defender AM scan (PermissionError / WinError 5).
    """
    try:
        import os as _os
        path.parent.mkdir(parents=True, exist_ok=True)
        _tmp = path.with_suffix(".tmp")
        _tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        _last_exc = None
        for _i in range(5):
            try:
                _os.replace(_tmp, path)
                _last_exc = None
                break
            except PermissionError as e:
                _last_exc = e
                time.sleep(0.1 * (_i + 1))
        if _last_exc is not None:
            raise _last_exc
    except Exception as e:
        logger.warning("[CrossApp] save %s failed: %s", path.name, e)


# ── 3C-9: Position overlap detection ────────────────────────────────────────

def register_position(
    app: str, symbol: str, chain: str,
    size_usd: float, protocol: str = "",
    correlation_id: Optional[str] = None,
) -> str:
    """Register a new open position in the cross-app ledger. Returns position_id."""
    _pid = str(uuid.uuid4())
    with _LOCK:
        state = _load(_OVERLAP_FILE)
        _list = state.setdefault(symbol.upper(), [])
        _list.append({
            "position_id":    _pid,
            "app":            app,
            "symbol":         symbol.upper(),
            "chain":          chain.lower(),
            "protocol":       protocol.lower(),
            "size_usd":       float(size_usd),
            "correlation_id": correlation_id,
            "opened_at":      datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        _save(_OVERLAP_FILE, state)
    return _pid


def close_position(symbol: str, position_id: str) -> bool:
    """Remove a position from the ledger (called on close/exit)."""
    with _LOCK:
        state = _load(_OVERLAP_FILE)
        _list = state.get(symbol.upper(), [])
        _kept = [p for p in _list if p.get("position_id") != position_id]
        if len(_kept) != len(_list):
            if _kept:
                state[symbol.upper()] = _kept
            else:
                state.pop(symbol.upper(), None)
            _save(_OVERLAP_FILE, state)
            return True
    return False


def get_overlapping_positions(symbol: str, exclude_app: str = "") -> list:
    """Return all positions on `symbol` held by apps OTHER than exclude_app.
    Used before executing a close/exit — if another app holds the same
    symbol, warn the user or require confirmation.
    """
    with _LOCK:
        state = _load(_OVERLAP_FILE)
        _list = state.get(symbol.upper(), [])
        return [p for p in _list if p.get("app") != exclude_app]


def total_exposure_usd(symbol: str) -> float:
    """Sum of all cross-app position sizes for a single symbol."""
    with _LOCK:
        state = _load(_OVERLAP_FILE)
        _list = state.get(symbol.upper(), [])
        return sum(float(p.get("size_usd", 0) or 0) for p in _list)


# ── 3C-10: Multi-sig recovery for >$100K positions ──────────────────────────

def requires_multisig(size_usd: float) -> bool:
    """Return True if a position size requires multi-sig approval."""
    return float(size_usd) > MULTISIG_THRESHOLD_USD


def propose_multisig(
    app: str, symbol: str, action: str, size_usd: float,
    proposer: str = "agent", notes: str = "",
) -> str:
    """
    Propose a >$100K action for multi-sig approval. Returns approval_id.
    The proposer (agent/owner/advisor) automatically counts as the first signature.
    """
    approval_id = str(uuid.uuid4())
    with _LOCK:
        state = _load(_MULTISIG_FILE)
        state[approval_id] = {
            "approval_id":  approval_id,
            "app":          app,
            "symbol":       symbol.upper(),
            "action":       action.upper(),
            "size_usd":     float(size_usd),
            "notes":        str(notes)[:500],
            "signatures":   [{"role": proposer, "signed_at": _now()}],
            "created_at":   _now(),
            "created_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _save(_MULTISIG_FILE, state)
    return approval_id


def sign_multisig(approval_id: str, role: str) -> tuple[bool, str]:
    """
    Add a signature to a pending multi-sig proposal. role should be one
    of 'agent', 'owner', 'advisor'. Returns (approved_now, reason).

    4B-8: enforces per-role vote cooldown (MULTISIG_VOTE_COOLDOWN_SECONDS)
    to prevent an actor from spamming votes across multiple proposals.
    """
    role = str(role).lower().strip()
    if role not in ("agent", "owner", "advisor"):
        return False, f"Invalid role '{role}' — must be agent/owner/advisor"
    with _LOCK:
        state = _load(_MULTISIG_FILE)
        entry = state.get(approval_id)
        if not entry:
            return False, "Unknown approval_id"
        if _now() - float(entry.get("created_at", 0)) > MULTISIG_EXPIRY_SECONDS:
            state.pop(approval_id, None)
            _save(_MULTISIG_FILE, state)
            return False, "Proposal expired (24h window)"
        # 4B-8: per-role vote cooldown across ALL proposals — prevents rapid
        # mass-approval if a session is compromised or a button is held.
        _global_role_ts = state.get("_last_vote_by_role", {}) or {}
        _last = float(_global_role_ts.get(role, 0) or 0)
        _elapsed = _now() - _last
        if _last > 0 and _elapsed < MULTISIG_VOTE_COOLDOWN_SECONDS:
            _wait = int(MULTISIG_VOTE_COOLDOWN_SECONDS - _elapsed)
            return False, f"Vote cooldown: {role} must wait {_wait}s before next approval"
        sigs = entry.setdefault("signatures", [])
        # De-dupe by role so one user can't sign twice
        if any(s.get("role") == role for s in sigs):
            return False, f"Already signed by {role}"
        sigs.append({"role": role, "signed_at": _now()})
        _global_role_ts[role] = _now()
        state["_last_vote_by_role"] = _global_role_ts
        _save(_MULTISIG_FILE, state)
        approved = len(sigs) >= MULTISIG_REQUIRED_SIGS
        return approved, (
            f"Approved ({len(sigs)}/{MULTISIG_REQUIRED_SIGS} sigs)"
            if approved else
            f"Pending ({len(sigs)}/{MULTISIG_REQUIRED_SIGS} sigs)"
        )


def is_approved(approval_id: str) -> bool:
    """Check if a proposal has enough signatures, hasn't expired, AND has
    completed the MULTISIG_TIMELOCK_SECONDS (4B-7) window since the last
    signature. The time-lock gives the human owner/advisor a 1hr window
    to review + veto before the plan becomes executable."""
    with _LOCK:
        state = _load(_MULTISIG_FILE)
        entry = state.get(approval_id)
        if not entry:
            return False
        if _now() - float(entry.get("created_at", 0)) > MULTISIG_EXPIRY_SECONDS:
            return False
        sigs = entry.get("signatures", [])
        if len(sigs) < MULTISIG_REQUIRED_SIGS:
            return False
        # 4B-7: time-lock — last signature must be older than TIMELOCK_SECONDS
        _last_sig_ts = max(float(s.get("signed_at", 0) or 0) for s in sigs)
        if _now() - _last_sig_ts < MULTISIG_TIMELOCK_SECONDS:
            return False
        return True


def approval_time_remaining(approval_id: str) -> tuple[bool, int]:
    """Return (is_fully_approved_and_unlocked, seconds_remaining_until_unlock).
    When the 2nd sig was just placed, seconds_remaining will be close to
    MULTISIG_TIMELOCK_SECONDS. UI can use this to show a countdown."""
    with _LOCK:
        state = _load(_MULTISIG_FILE)
        entry = state.get(approval_id)
        if not entry:
            return False, 0
        sigs = entry.get("signatures", [])
        if len(sigs) < MULTISIG_REQUIRED_SIGS:
            return False, 0
        _last = max(float(s.get("signed_at", 0) or 0) for s in sigs)
        _remaining = int(MULTISIG_TIMELOCK_SECONDS - (_now() - _last))
        if _remaining <= 0:
            return True, 0
        return False, _remaining


def list_pending() -> list:
    """Return all pending (not yet approved, not expired) proposals — for UI."""
    _out = []
    with _LOCK:
        state = _load(_MULTISIG_FILE)
        now = _now()
        for _id, entry in state.items():
            if now - float(entry.get("created_at", 0)) > MULTISIG_EXPIRY_SECONDS:
                continue
            if len(entry.get("signatures", [])) < MULTISIG_REQUIRED_SIGS:
                _out.append(entry)
    return sorted(_out, key=lambda e: e.get("created_at", 0), reverse=True)


def gate_execution(size_usd: float) -> tuple[bool, str, Optional[str]]:
    """
    Top-level gate for execute_plan callers.
    Returns (can_proceed, reason, approval_id_if_needed).

    - size_usd <= $100K → (True, "", None)  # proceed freely
    - size_usd >  $100K → (False, "requires approval", approval_id)
      Caller must surface approval_id to user and wait for sigs, then
      re-call gate_execution(approval_id=...) to unblock.
    """
    if not requires_multisig(size_usd):
        return True, "", None
    return False, (
        f"Position size ${size_usd:,.0f} exceeds the ${MULTISIG_THRESHOLD_USD:,.0f} "
        f"multi-sig threshold. {MULTISIG_REQUIRED_SIGS} of 3 approvals needed "
        f"(agent + owner + advisor)."
    ), None


__all__ = [
    "MULTISIG_THRESHOLD_USD", "MULTISIG_REQUIRED_SIGS",
    "register_position", "close_position", "get_overlapping_positions",
    "total_exposure_usd",
    "requires_multisig", "propose_multisig", "sign_multisig",
    "is_approved", "list_pending", "gate_execution",
]

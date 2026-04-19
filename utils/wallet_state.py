"""
utils/wallet_state.py — Unified cross-app wallet state.

Prevents double-allocation of the same capital across apps (DeFi + RWA
could each try to deploy the full wallet balance; SuperGrok could open
OKX positions while DeFi executes on-chain). Single source of truth via
Zerion (already wired in DeFi) + local reservation ledger.

Architecture:
    1. Zerion API returns the live on-chain USD balance per address
    2. A local reservation ledger tracks "committed but not yet
       settled" capital (e.g. a DeFi plan in-flight)
    3. available_usd() = zerion_balance - active_reservations
    4. Reservations auto-expire after 15 min (timeout safety)

All 3 apps ship this module and write to a shared JSON file at
    data/wallet_reservations.json
under the user's wallet address. On multi-app hosts, a file lock
serializes read-modify-write.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────────────

_RESERVATION_TTL_SECONDS = 15 * 60   # 15-minute max hold — auto-expires
_RESERVATION_FILE = Path(__file__).resolve().parent.parent / "data" / "wallet_reservations.json"
_LOCK = threading.Lock()


# ── Low-level state file IO ─────────────────────────────────────────────────

def _load_state() -> dict:
    try:
        if _RESERVATION_FILE.exists():
            return json.loads(_RESERVATION_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("[WalletState] load failed: %s", e)
    return {}


def _save_state(state: dict) -> None:
    """Atomic write: tmp + os.replace to survive crash-during-write (4B-10)."""
    try:
        import os as _os
        _RESERVATION_FILE.parent.mkdir(parents=True, exist_ok=True)
        _tmp = _RESERVATION_FILE.with_suffix(".tmp")
        _tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        _os.replace(_tmp, _RESERVATION_FILE)
    except Exception as e:
        logger.warning("[WalletState] save failed: %s", e)


def _now() -> float:
    return time.time()


def _prune_expired(state: dict) -> dict:
    """Remove reservations older than TTL. 4B-9: track expired entries in
    a separate '_expired' list so a UI widget can surface them as alerts
    for a short window (auto-aged out 10 minutes after expiry)."""
    now = _now()
    _expired_alerts = state.setdefault("_expired", [])
    # Age out old alert entries (keep for 10 min so UI has time to show them)
    _expired_alerts = [
        e for e in _expired_alerts
        if isinstance(e, dict) and (now - float(e.get("expired_at", 0) or 0)) < 600
    ]
    state["_expired"] = _expired_alerts
    for addr, _reservations in list(state.items()):
        if addr == "_expired" or not isinstance(_reservations, list):
            continue
        kept = []
        for r in _reservations:
            if not isinstance(r, dict):
                continue
            _age = now - float(r.get("created_at", 0))
            if _age < _RESERVATION_TTL_SECONDS:
                kept.append(r)
            else:
                _expired_alerts.append({
                    "reservation_id": r.get("reservation_id", ""),
                    "app":            r.get("app", ""),
                    "amount_usd":     float(r.get("amount_usd", 0)),
                    "note":           r.get("note", ""),
                    "address":        addr,
                    "expired_at":     now,
                })
        if kept:
            state[addr] = kept
        else:
            state.pop(addr, None)
    return state


def recent_expired_alerts(address: str) -> list:
    """Return reservations that auto-expired in the last 10 minutes, scoped
    to `address`. Used by the sidebar to surface 'your RWA plan timed out'
    type warnings to the user (4B-9)."""
    if not address:
        return []
    _addr_lower = address.lower()
    with _LOCK:
        state = _prune_expired(_load_state())
        return [e for e in state.get("_expired", []) if e.get("address") == _addr_lower]


# ── Public API ──────────────────────────────────────────────────────────────

def reserve(address: str, app: str, amount_usd: float, note: str = "",
            correlation_id: Optional[str] = None) -> str:
    """
    Reserve a slice of the wallet for an in-flight plan. Returns the
    reservation_id. Reservations auto-expire after 15 minutes so a
    crashed/forgotten reservation can't lock funds forever.
    """
    if amount_usd <= 0 or not address:
        return ""
    _res_id = f"{app}:{int(_now())}:{hash(note) & 0xFFFFFF:06x}"
    with _LOCK:
        state = _prune_expired(_load_state())
        _list = state.setdefault(address.lower(), [])
        _list.append({
            "reservation_id": _res_id,
            "app":            str(app),
            "amount_usd":     float(amount_usd),
            "note":           str(note)[:200],
            "correlation_id": correlation_id,
            "created_at":     _now(),
            "created_at_iso": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        })
        _save_state(state)
    return _res_id


def release(address: str, reservation_id: str) -> bool:
    """Release a specific reservation. Called when plan completes (success or failure)."""
    if not reservation_id or not address:
        return False
    with _LOCK:
        state = _prune_expired(_load_state())
        _list = state.get(address.lower(), [])
        _kept = [r for r in _list if r.get("reservation_id") != reservation_id]
        if len(_kept) != len(_list):
            state[address.lower()] = _kept
            _save_state(state)
            return True
    return False


def active_reservations_usd(address: str) -> float:
    """Return the sum of non-expired reservations for an address (across all apps)."""
    if not address:
        return 0.0
    with _LOCK:
        state = _prune_expired(_load_state())
        _list = state.get(address.lower(), [])
        return sum(float(r.get("amount_usd", 0) or 0) for r in _list)


def list_reservations(address: str) -> list:
    """Return the live reservation list for an address (for UI display)."""
    if not address:
        return []
    with _LOCK:
        state = _prune_expired(_load_state())
        return list(state.get(address.lower(), []))


def available_usd(address: str, total_wallet_usd: float) -> float:
    """
    Compute available capital after active reservations are subtracted
    from the Zerion-reported total.
    """
    _reserved = active_reservations_usd(address)
    return max(0.0, float(total_wallet_usd) - _reserved)


def has_capacity(address: str, total_wallet_usd: float, amount_usd: float) -> tuple[bool, str]:
    """
    Check if a plan of `amount_usd` fits within available capital.
    Returns (ok, reason). Used by each app's portfolio_executor to gate
    execution when another app has already reserved the funds.
    """
    if amount_usd <= 0:
        return True, ""
    avail = available_usd(address, total_wallet_usd)
    if amount_usd > avail:
        return False, (
            f"Only ${avail:,.0f} available — ${active_reservations_usd(address):,.0f} "
            f"is currently reserved by another app's in-flight plan. "
            f"Wait for that to settle or release reservations manually."
        )
    return True, ""


__all__ = [
    "reserve", "release", "available_usd", "active_reservations_usd",
    "list_reservations", "has_capacity", "recent_expired_alerts",
]

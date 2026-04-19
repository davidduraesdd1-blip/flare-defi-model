"""
tests/test_phase3_modules.py — Unit tests for Phase 3 shared modules.

Run with: pytest tests/test_phase3_modules.py -v

Modules under test:
- utils/format.py (format_usd, format_pct, format_large_number, ...)
- utils/audit_schema.py (make_event, serialize_event)
- utils/wallet_state.py (reserve/release, has_capacity, expiry)
- utils/cross_app_safety.py (multi-sig flow with time-lock + cooldown)
- utils/family_office_report.py (build_summary_context, render_pdf)
- agents/circuit_breakers.py (7-gate safety)
- agents/eip5792_bundler.py (wallet_sendCalls payload builder)
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── utils/format.py ─────────────────────────────────────────────────────────

def test_format_usd_basic():
    from utils.format import format_usd
    assert format_usd(15_950) == "$15,950.00"
    assert format_usd(15_950, 0) == "$15,950"
    assert format_usd(None) == "—"
    assert format_usd(float("nan")) == "—"


def test_format_usd_compact_transitions():
    from utils.format import format_usd
    # Audit aba91f63 — K->M transition at 950K avoids '$1000.00K' ambiguity
    assert format_usd(999_999, compact=True) == "$1.00M"
    assert format_usd(800_000, compact=True) == "$800.00K"
    assert format_usd(950_000, compact=True) == "$0.95M"


def test_format_usd_negative_decimals_clamped():
    """Audit aba91f63 caught that negative decimals crashed f-string."""
    from utils.format import format_usd
    assert format_usd(100, decimals=-5) == "$100"


def test_format_pct_heuristic():
    from utils.format import format_pct
    assert format_pct(12.3) == "12.3%"
    assert format_pct(0.123) == "12.3%"
    assert format_pct(-5.2, signed=True) == "-5.2%"
    assert format_pct(None) == "—"


def test_format_delta_color():
    from utils.format import format_delta_color
    assert format_delta_color(1.5) == "#22c55e"
    assert format_delta_color(-2) == "#ef4444"
    assert format_delta_color(0) == "#64748b"
    assert format_delta_color(None) == "#64748b"


# ── utils/audit_schema.py ───────────────────────────────────────────────────

def test_audit_schema_make_event():
    from utils.audit_schema import make_event, EVT_TRADE_OPEN, APP_DEFI
    e = make_event(APP_DEFI, EVT_TRADE_OPEN, canonical_risk_level=1,
                  user_level="advanced", mode="paper", size_usd=1000.0)
    assert e["app"] == APP_DEFI
    assert e["event_type"] == EVT_TRADE_OPEN
    assert e["canonical_risk_level"] == 1
    assert e["approved"] is True   # default
    assert len(e["event_id"]) == 36   # UUIDv4 length


def test_audit_schema_serialize():
    from utils.audit_schema import make_event, serialize_event, APP_DEFI
    e = make_event(APP_DEFI, "PLAN_BUILD")
    s = serialize_event(e)
    assert isinstance(s, str)
    assert '"app":"defi"' in s


# ── utils/wallet_state.py ───────────────────────────────────────────────────

def test_wallet_state_reservation_lifecycle():
    from utils.wallet_state import reserve, release, available_usd, active_reservations_usd, has_capacity

    addr = "0xtest1"
    r1 = reserve(addr, "defi", 5000.0, "test A")
    r2 = reserve(addr, "rwa",  3000.0, "test B")
    assert r1 and r2
    assert active_reservations_usd(addr) == 8000.0

    # Capacity check
    ok, _ = has_capacity(addr, 10_000, 3000)   # 2000 free, need 3000 → block
    assert ok is False
    ok, _ = has_capacity(addr, 10_000, 1500)   # 2000 free, need 1500 → ok
    assert ok is True

    # available = total - reserved
    assert available_usd(addr, 10_000) == 2000.0

    # Release restores capacity
    release(addr, r1)
    assert available_usd(addr, 10_000) == 7000.0
    release(addr, r2)


# ── utils/cross_app_safety.py ───────────────────────────────────────────────

def test_multisig_time_lock_and_cooldown():
    from utils.cross_app_safety import (
        propose_multisig, sign_multisig, is_approved, approval_time_remaining,
        MULTISIG_TIMELOCK_SECONDS, requires_multisig,
    )
    assert requires_multisig(50_000) is False
    assert requires_multisig(200_000) is True

    aid = propose_multisig("defi", "X", "EXECUTE", 200_000)
    # 4B-7: 2-of-2 sigs + time-lock means NOT approved immediately
    ok, _ = sign_multisig(aid, "owner")
    assert ok is True    # means 2-of-2 reached; not means fully-unlocked
    assert is_approved(aid) is False    # time-locked for 1hr
    unlocked, remaining = approval_time_remaining(aid)
    assert unlocked is False
    assert remaining > MULTISIG_TIMELOCK_SECONDS - 5   # within a few seconds of full lock


def test_multisig_vote_cooldown():
    """Audit aba91f63 — 4B-8 per-role vote cooldown."""
    from utils.cross_app_safety import propose_multisig, sign_multisig
    # Two proposals, both signed by 'owner' in rapid succession — 2nd blocks
    aid1 = propose_multisig("defi", "X", "EXECUTE", 200_000)
    aid2 = propose_multisig("defi", "Y", "EXECUTE", 200_000)
    _ok1, _ = sign_multisig(aid1, "owner")
    _ok2, msg2 = sign_multisig(aid2, "owner")
    assert _ok2 is False and "cooldown" in msg2.lower()


# ── agents/circuit_breakers.py ──────────────────────────────────────────────

def test_circuit_breakers_drawdown_trip():
    from agents.circuit_breakers import check_all, resume
    resume("test cleanup")   # clear any prior state
    ctx = {
        "peak_balance_usd": 10_000, "current_balance_usd": 8_000,  # 20% drawdown > 15% limit
        "daily_pnl_usd": 0, "recent_trade_pnls": [],
        "recent_trade_timestamps": [], "last_scan_unix": time.time(),
        "consecutive_api_failures": 0, "emergency_stop_active": False,
    }
    ok, reason, gate = check_all(ctx)
    assert ok is False
    assert gate == "DRAWDOWN"
    resume("test cleanup")


def test_circuit_breakers_fail_safe_on_gate_exception():
    """Audit aabf0e41 — crashed gate must HALT the circuit, not proceed."""
    from agents.circuit_breakers import check_all, resume
    resume("test cleanup")
    ctx = {
        "peak_balance_usd": 10_000, "current_balance_usd": 9_500,
        "recent_trade_pnls": "not_a_list",   # will crash _gate_loss_rate
        "recent_trade_timestamps": [], "last_scan_unix": time.time(),
    }
    ok, reason, gate = check_all(ctx)
    assert ok is False
    assert gate == "LOSS_RATE"
    assert "crashed" in reason.lower()
    resume("test cleanup")


# ── agents/eip5792_bundler.py ───────────────────────────────────────────────

def test_eip5792_bundle_construction():
    from agents.eip5792_bundler import (
        build_call, build_approve_call, build_bundle, parse_send_response,
    )
    addr = "0x" + "a" * 40
    token = "0x" + "b" * 40
    ktoken = "0x" + "c" * 40
    approve = build_approve_call(token, ktoken, 1_000_000_000, chain_id_hex="0xe")
    mint    = build_call(ktoken, data="0xa0712d68" + f"{1_000_000_000:064x}", chain_id_hex="0xe")
    bundle  = build_bundle([approve, mint], from_address=addr, chain_id_hex="0xe")
    assert bundle["method"] == "wallet_sendCalls"
    assert len(bundle["params"][0]["calls"]) == 2
    assert bundle["params"][0]["atomicRequired"] is True


def test_eip5792_uint256_overflow_rejected():
    """Audit aba91f63 — amount_raw >= 2^256 must raise ValueError."""
    from agents.eip5792_bundler import build_approve_call
    try:
        build_approve_call("0x" + "a" * 40, "0x" + "b" * 40, 1 << 256)
    except ValueError:
        return
    raise AssertionError("build_approve_call should reject 2^256")


if __name__ == "__main__":
    import traceback
    _tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    _pass = _fail = 0
    for t in _tests:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
            _pass += 1
        except Exception as e:
            print(f"  [FAIL] {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
            _fail += 1
    print(f"\n{_pass} passed, {_fail} failed")

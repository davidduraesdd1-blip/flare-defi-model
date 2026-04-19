"""
agents/eip5792_bundler.py — EIP-5792 wallet_sendCalls multi-leg bundler.

Implements the 2024 Ethereum standard for atomic multi-call bundles:
https://eips.ethereum.org/EIPS/eip-5792

The UX win: user signs ONCE → wallet (MetaMask, Rabby, Safe) executes
N transactions atomically. No persistent approvals, no N signatures.

Supported by: MetaMask (v11.16+), Rabby, Safe, Rainbow. Unsupported
wallets fall back to sequential individual transactions.

This module BUILDS the bundle payload. Actual submission is wallet-
native — Streamlit can't open a browser wallet directly, so the flow is:
    1. Agent constructs bundle via build_bundle(...)
    2. Export as JSON + QR code / deep-link
    3. User opens their wallet on mobile/desktop
    4. Wallet shows all N calls, user signs once
    5. Wallet submits and returns bundle tx_hash
    6. Agent polls receipt via get_bundle_status(bundle_id)

For same-browser wallet support, future commit can wire WalletConnect.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


# ── Config ──────────────────────────────────────────────────────────────────

# EIP-5792 capability flags the wallet must return from wallet_getCapabilities
REQUIRED_CAPABILITIES = {"atomicBatch"}


# ── Call construction ──────────────────────────────────────────────────────

def build_call(
    to: str, value_wei: int = 0, data: str = "0x",
    chain_id_hex: Optional[str] = None,
) -> dict:
    """
    Build one call within a wallet_sendCalls payload.

    Args:
        to:           target contract or EOA address (0x...)
        value_wei:    native-token amount attached (wei)
        data:         calldata hex string (0x-prefixed)
        chain_id_hex: '0xe' for Flare, '0x1' for Ethereum, etc.
    """
    call = {
        "to":   str(to),
        "data": str(data) if data.startswith("0x") else f"0x{data}",
    }
    if value_wei > 0:
        call["value"] = hex(int(value_wei))
    if chain_id_hex:
        call["chainId"] = chain_id_hex
    return call


def build_bundle(
    calls: list,
    *,
    from_address: str,
    chain_id_hex: str,
    atomic_required: bool = True,
    version: str = "2.0.0",
) -> dict:
    """
    Assemble an EIP-5792 wallet_sendCalls RPC request payload.

    Args:
        calls:          list of dicts from build_call(...)
        from_address:   user's wallet address (checksummed)
        chain_id_hex:   hex chainId (e.g. '0xe' = Flare)
        atomic_required: True = require wallet to execute all-or-nothing
        version:        EIP-5792 version (2.0.0 is current)

    Returns:
        Full JSON-RPC request dict ready to ship to the wallet.
        Fields: jsonrpc, method, params[], id
    """
    bundle_id = str(uuid.uuid4())
    params = [{
        "version":   version,
        "from":      from_address,
        "chainId":   chain_id_hex,
        "atomicRequired": bool(atomic_required),
        "calls":     list(calls),
    }]
    return {
        "jsonrpc":   "2.0",
        "method":    "wallet_sendCalls",
        "params":    params,
        "id":        bundle_id,
    }


def serialize_bundle(bundle: dict) -> str:
    """JSON-serialize a bundle for QR / deep-link / clipboard."""
    return json.dumps(bundle, separators=(",", ":"))


# ── Helpers for common approve+call pattern ─────────────────────────────────

def build_approve_call(
    token_address: str, spender: str, amount_raw: int,
    chain_id_hex: Optional[str] = None,
) -> dict:
    """
    Build an ERC20 approve(spender, amount) call using the standard
    4-byte selector + abi-encoded args.
    approve(address,uint256) = 0x095ea7b3
    """
    _selector = "0x095ea7b3"
    _spender_padded = str(spender).lower().replace("0x", "").rjust(64, "0")
    _amount_padded  = f"{int(amount_raw):064x}"
    _data = _selector + _spender_padded + _amount_padded
    return build_call(to=token_address, value_wei=0, data=_data, chain_id_hex=chain_id_hex)


# ── Response parsing ───────────────────────────────────────────────────────

def parse_send_response(response: dict) -> dict:
    """
    Parse a wallet_sendCalls response. Successful responses return
    {id: <string>, capabilities?: {...}}.
    """
    if not isinstance(response, dict):
        return {"ok": False, "error": "non-dict response"}
    if "error" in response:
        _err = response["error"]
        return {
            "ok": False,
            "error": str(_err.get("message", _err)) if isinstance(_err, dict) else str(_err),
        }
    _result = response.get("result")
    if isinstance(_result, dict) and "id" in _result:
        return {
            "ok": True,
            "bundle_id":   str(_result["id"]),
            "capabilities": _result.get("capabilities", {}),
        }
    if isinstance(_result, str):
        return {"ok": True, "bundle_id": _result}
    return {"ok": False, "error": "unexpected response shape"}


def build_status_request(bundle_id: str) -> dict:
    """
    Build a wallet_getCallsStatus RPC request to poll a bundle's receipts.
    """
    return {
        "jsonrpc": "2.0",
        "method":  "wallet_getCallsStatus",
        "params":  [str(bundle_id)],
        "id":      str(uuid.uuid4()),
    }


def parse_status_response(response: dict) -> dict:
    """
    Parse wallet_getCallsStatus response. Shape:
      {id, status: 'PENDING' | 'CONFIRMED' | 'REVERTED',
       atomic, receipts: [tx_hash list]}
    """
    if not isinstance(response, dict):
        return {"status": "UNKNOWN", "error": "non-dict"}
    if "error" in response:
        _err = response["error"]
        return {"status": "ERROR",
                "error": str(_err.get("message", _err)) if isinstance(_err, dict) else str(_err)}
    _result = response.get("result") or {}
    _status_code = _result.get("status")
    # EIP-5792 uses numeric codes:
    #   100 = pending, 200 = confirmed, 500 = failed off-chain,
    #   600 = reverted on-chain
    _status_map = {100: "PENDING", 200: "CONFIRMED", 500: "FAILED", 600: "REVERTED"}
    return {
        "status":    _status_map.get(_status_code, "UNKNOWN"),
        "status_code": _status_code,
        "atomic":    bool(_result.get("atomic", False)),
        "receipts":  _result.get("receipts", []),
    }


# ── Fallback: sequential submission when wallet lacks EIP-5792 ─────────────

def can_fallback_sequentially(calls: list) -> bool:
    """Sequential fallback is viable if each call has a `to` and `data`."""
    return all(c.get("to") and c.get("data") for c in calls)


__all__ = [
    "REQUIRED_CAPABILITIES",
    "build_call", "build_approve_call", "build_bundle", "serialize_bundle",
    "parse_send_response", "build_status_request", "parse_status_response",
    "can_fallback_sequentially",
]

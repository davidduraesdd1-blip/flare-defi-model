"""
scanners/wallet.py — Wallet portfolio data via Zerion public API (#111).

Fetches read-only wallet positions, chain breakdown, and DeFi protocol exposure.
5-minute TTL cache per address.
"""
from __future__ import annotations

import base64
import logging
import time
logger = logging.getLogger(__name__)

# ── 5-minute TTL cache keyed by lowercased address ────────────────────────────
_zerion_cache: dict[str, dict] = {}
_TTL = 300   # 5 minutes


def fetch_zerion_portfolio(address: str) -> dict:
    """Fetch wallet portfolio from Zerion public API.

    Args:
        address: EVM wallet address (0x-prefixed, 42 chars).

    Returns:
        {
          "address": str,
          "total_value_usd": float,
          "positions": [{"name", "value_usd", "quantity", "price", "change_1d_pct", "chain"}],
          "chain_breakdown": {"ethereum": float, "arbitrum": float, ...},
          "defi_protocols": list[str],
          "timestamp": str,
          "error": str | None,
        }
    """
    addr_key = address.lower()
    now = time.time()

    # Check cache
    cached = _zerion_cache.get(addr_key)
    if cached and now - cached.get("_ts", 0) < _TTL:
        return {k: v for k, v in cached.items() if not k.startswith("_")}

    result: dict = {
        "address":         address,
        "total_value_usd": 0.0,
        "positions":       [],
        "chain_breakdown": {},
        "defi_protocols":  [],
        "timestamp":       time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error":           None,
    }

    try:
        from utils.http import is_safe_url

        url = f"https://api.zerion.io/v1/wallets/{address}/positions"
        params = {
            "filter[position_types]": "wallet,deposit,staked",
            "currency": "usd",
            "sort": "value",
        }

        # Zerion: use ZERION_API_KEY env var for Basic auth (format: base64(key:))
        # Falls back gracefully on 401 (anonymous access may still work for some endpoints)
        import os as _os
        _raw_key = _os.environ.get("ZERION_API_KEY", "").strip()
        _auth_str = f"{_raw_key}:" if _raw_key else "zerion_api_key:"
        _b64 = base64.b64encode(_auth_str.encode()).decode()
        headers = {
            "Accept": "application/json",
            "Authorization": f"Basic {_b64}",
        }

        if not is_safe_url(url):
            result["error"] = "SSRF: api.zerion.io blocked"
            return result

        # Use the shared retry-aware session from utils.http
        from utils.http import _SESSION
        try:
            resp = _SESSION.get(url, params=params, headers=headers, timeout=12)
            if resp.status_code == 401:
                # Try without auth header (some Zerion endpoints work anonymously)
                resp = _SESSION.get(url, params=params, timeout=12)
            resp.raise_for_status()
            data = resp.json()
        except Exception as http_err:
            result["error"] = f"Zerion API error: {http_err}"
            logger.warning("[Zerion] fetch failed for %s: %s", address[:10], http_err)
            _cache_result(addr_key, result, now)
            return result

        positions_raw = data.get("data") or []
        positions: list[dict] = []
        chain_breakdown: dict[str, float] = {}
        protocols: set[str] = set()
        total_value = 0.0

        for pos in positions_raw:
            attrs = pos.get("attributes") or {}
            name  = attrs.get("name") or ""
            value = float(attrs.get("value") or 0)
            qty_info = attrs.get("quantity") or {}
            quantity = float(qty_info.get("float") or 0) if isinstance(qty_info, dict) else 0.0
            price    = float(attrs.get("price") or 0)
            changes  = attrs.get("changes") or {}
            change_1d = float(changes.get("percent_1d") or 0) if isinstance(changes, dict) else 0.0

            rels  = pos.get("relationships") or {}
            chain_data = (rels.get("chain") or {}).get("data") or {}
            chain = str(chain_data.get("id") or "unknown")

            # Protocol from fungible_info
            fi = attrs.get("fungible_info") or {}
            protocol_name = (fi.get("implementations") or [{}])[0] if isinstance(fi.get("implementations"), list) and fi.get("implementations") else {}
            if isinstance(protocol_name, dict):
                pname = protocol_name.get("protocol") or ""
                if pname:
                    protocols.add(pname)

            positions.append({
                "name":         name,
                "value_usd":    round(value, 4),
                "quantity":     round(quantity, 8),
                "price":        round(price, 6),
                "change_1d_pct": round(change_1d, 4),
                "chain":        chain,
            })

            total_value += value
            chain_breakdown[chain] = chain_breakdown.get(chain, 0.0) + value

        # Sort by value descending
        positions.sort(key=lambda x: x["value_usd"], reverse=True)

        result["total_value_usd"] = round(total_value, 4)
        result["positions"]       = positions[:50]   # cap at 50
        result["chain_breakdown"] = {k: round(v, 4) for k, v in
                                     sorted(chain_breakdown.items(), key=lambda x: x[1], reverse=True)}
        result["defi_protocols"]  = sorted(protocols)
        result["error"]           = None
        logger.info(
            "[Zerion] %s: $%.2f total, %d positions, %d chains",
            address[:10], total_value, len(positions), len(chain_breakdown),
        )

    except Exception as e:
        result["error"] = f"Unexpected error: {e}"
        logger.warning("[Zerion] unexpected error for %s: %s", address[:10], e)

    _cache_result(addr_key, result, now)
    return result


def _cache_result(addr_key: str, result: dict, ts: float) -> None:
    """Store result in module-level cache with timestamp."""
    _zerion_cache[addr_key] = {**result, "_ts": ts}

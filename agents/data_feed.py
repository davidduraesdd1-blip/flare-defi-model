"""
agents/data_feed.py — Market data aggregator for the Claude decision engine.

Collects live data from existing DeFi Model scanners and packages it into
a clean context dict that Claude receives before making a decision.
Cached for DECISION_LOOP_INTERVAL_SECONDS to avoid hammering APIs.
"""

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import (
    DECISION_LOOP_INTERVAL_SECONDS, FLARE_PROTOCOL_WHITELIST,
    XRPL_PROTOCOL_WHITELIST, MAX_TRADE_SIZE_PCT,
    MAX_DAILY_LOSS_PCT, MAX_OPEN_POSITIONS,
)

# Use existing scanners — they already have caching + rate limiting
try:
    from scanners.defillama import fetch_yields_pools
    _LLAMA_OK = True
except ImportError:
    _LLAMA_OK = False

try:
    from config import FALLBACK_PRICES, FLARE_RPC_URLS
    _CONFIG_OK = True
except ImportError:
    _CONFIG_OK = False
    FALLBACK_PRICES = {"FLR": 0.018, "XRP": 2.30, "FXRP": 2.297}

# Fear & Greed from existing macro_feeds
try:
    from macro_feeds import get_fear_greed_cached
    _FG_OK = True
except (ImportError, Exception):
    _FG_OK = False


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_live_prices() -> dict:
    """Fetch live FLR and XRP prices from CoinGecko fallback."""
    import requests
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "flare-networks,ripple", "vs_currencies": "usd"},
            timeout=6,
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "FLR":  _safe_float(data.get("flare-networks", {}).get("usd"), FALLBACK_PRICES.get("FLR", 0.018)),
                "XRP":  _safe_float(data.get("ripple", {}).get("usd"), FALLBACK_PRICES.get("XRP", 2.30)),
                "FXRP": _safe_float(data.get("ripple", {}).get("usd"), FALLBACK_PRICES.get("XRP", 2.30)) * 0.998,
            }
    except Exception:
        pass
    return {k: FALLBACK_PRICES.get(k, 0) for k in ("FLR", "XRP", "FXRP")}


def _get_top_opportunities(n: int = 5) -> list[dict]:
    """Pull top whitelisted opportunities from DeFiLlama scanner."""
    if not _LLAMA_OK:
        return []
    try:
        pools = fetch_yields_pools(chain="Flare", min_tvl=1_000_000) or []
        out = []
        for p in pools[:50]:
            proto = str(p.get("project") or p.get("protocol") or "").lower()
            if proto not in FLARE_PROTOCOL_WHITELIST:
                continue
            apy = _safe_float(p.get("apy") or p.get("estimated_apy"))
            if apy <= 0 or apy > 200:
                continue
            out.append({
                "protocol":    proto,
                "pool":        str(p.get("symbol") or p.get("pool") or ""),
                "chain":       "flare",
                "apy":         round(apy, 2),
                "tvl_usd":     _safe_float(p.get("tvlUsd") or p.get("tvl_usd")),
                "apy_7d":      _safe_float(p.get("apyBase7d") or p.get("apy_7d") or apy),
                "il_risk":     str(p.get("il_risk") or "unknown"),
            })
        # Sort by APY descending, cap at n
        out.sort(key=lambda x: x["apy"], reverse=True)
        return out[:n]
    except Exception:
        return []


def _get_fear_greed() -> dict:
    if _FG_OK:
        try:
            fg = get_fear_greed_cached()
            if fg:
                return {
                    "value":      int(fg.get("value", 50)),
                    "label":      str(fg.get("value_classification", "Neutral")),
                    "updated_at": str(fg.get("timestamp", "")),
                }
        except Exception:
            pass
    return {"value": 50, "label": "Neutral (data unavailable)", "updated_at": ""}


# ─── Simple in-process cache ──────────────────────────────────────────────────
_cache: dict = {"data": None, "expires": 0.0}


def get_agent_context(
    wallet_balance_usd: float,
    daily_pnl_usd: float,
    open_positions: list[dict],
    operating_mode: str,
) -> dict:
    """
    Build and return the full context dict that goes to Claude.
    Cached for DECISION_LOOP_INTERVAL_SECONDS.

    Args:
        wallet_balance_usd: current wallet value (paper or live)
        daily_pnl_usd:      today's realized P&L so far
        open_positions:     list of dicts from PositionMonitor
        operating_mode:     "PAPER" | "LIVE_PHASE2" | "LIVE_PHASE3"
    """
    now = time.time()
    if _cache["data"] and _cache["expires"] > now:
        # Update dynamic fields even on cache hit
        ctx = dict(_cache["data"])
        ctx["wallet"]["balance_usd"]  = round(wallet_balance_usd, 2)
        ctx["daily_pnl"]["usd"]       = round(daily_pnl_usd, 2)
        ctx["daily_pnl"]["pct"]       = round(daily_pnl_usd / max(wallet_balance_usd, 1) * 100, 3)
        ctx["open_positions"]         = open_positions
        ctx["limits"]["remaining_daily_loss_usd"] = round(
            max(0, wallet_balance_usd * MAX_DAILY_LOSS_PCT + daily_pnl_usd), 2
        )
        ctx["limits"]["max_trade_usd"] = round(wallet_balance_usd * MAX_TRADE_SIZE_PCT, 2)
        ctx["limits"]["open_slots"]    = max(0, MAX_OPEN_POSITIONS - len(open_positions))
        return ctx

    # Fresh fetch
    prices      = _get_live_prices()
    opps        = _get_top_opportunities(n=5)
    fear_greed  = _get_fear_greed()

    ctx = {
        "timestamp":       _utcnow(),
        "operating_mode":  operating_mode,
        "wallet": {
            "balance_usd":   round(wallet_balance_usd, 2),
            "currency":      "USD",
            "flr_price":     prices.get("FLR", 0),
            "xrp_price":     prices.get("XRP", 0),
        },
        "daily_pnl": {
            "usd": round(daily_pnl_usd, 2),
            "pct": round(daily_pnl_usd / max(wallet_balance_usd, 1) * 100, 3),
        },
        "open_positions": open_positions,
        "top_opportunities": opps,
        "market_context": {
            "fear_greed":        fear_greed,
            "flr_price_usd":     prices.get("FLR", 0),
            "xrp_price_usd":     prices.get("XRP", 0),
            "fxrp_price_usd":    prices.get("FXRP", 0),
        },
        "limits": {
            "max_trade_usd":            round(wallet_balance_usd * MAX_TRADE_SIZE_PCT, 2),
            "max_daily_loss_usd":       round(wallet_balance_usd * MAX_DAILY_LOSS_PCT, 2),
            "remaining_daily_loss_usd": round(
                max(0, wallet_balance_usd * MAX_DAILY_LOSS_PCT + daily_pnl_usd), 2
            ),
            "open_slots":               max(0, MAX_OPEN_POSITIONS - len(open_positions)),
            "max_open_positions":       MAX_OPEN_POSITIONS,
        },
        "whitelisted_protocols": {
            "flare": sorted(FLARE_PROTOCOL_WHITELIST),
            "xrpl":  sorted(XRPL_PROTOCOL_WHITELIST),
        },
    }
    _cache["data"]    = ctx
    _cache["expires"] = now + DECISION_LOOP_INTERVAL_SECONDS
    return ctx


def format_context_for_claude(ctx: dict) -> str:
    """Convert context dict to a clean JSON string for the Claude prompt."""
    return json.dumps(ctx, indent=2, default=str)

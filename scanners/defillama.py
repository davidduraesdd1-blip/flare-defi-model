"""
scanners/defillama.py — Flare DeFi Model
DeFiLlama protocol TVL and chain health data.

Supplements the pool-level yield fetching in flare_scanner.py with:
  - Protocol-level TVL and 7d/30d change tracking
  - Flare chain aggregate TVL and ranking
  - TVL-based confidence adjustment for opportunity scoring
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "FlareDeFiModel/1.0",
})

_DEFILLAMA_API    = "https://api.llama.fi"
_DEFILLAMA_YIELDS = "https://yields.llama.fi"
_REQUEST_TIMEOUT  = 12

# Flare protocol slugs on DeFiLlama
_FLARE_PROTOCOL_SLUGS = [
    "clearpool-lending",
    "mystic-finance-lending",
    "sceptre-liquid",
    "spectra-v2",
    "kinetic-finance",
    "enosys",
    "upshift",
    "kinza-finance",      # Aave V3 fork on Flare — slug may resolve once indexed
    "blazeswap",          # Primary Flare AMM DEX
    "firelight-finance",  # stXRP liquid staking
]

_cache: dict = {}
_cache_lock = threading.Lock()
_PROTOCOL_TVL_TTL = 3600   # 1 hour — TVL changes slowly
_CHAIN_TVL_TTL    = 1800   # 30 minutes


def _get(url: str, timeout: int = _REQUEST_TIMEOUT) -> Optional[dict]:
    """Simple GET with error handling."""
    try:
        resp = _SESSION.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("[DeFiLlama] %s → HTTP %d", url, resp.status_code)
    except Exception as e:
        logger.debug("[DeFiLlama] %s error: %s", url, e)
    return None


def fetch_flare_chain_tvl() -> dict:
    """
    Fetch aggregate TVL and recent trend for the Flare chain.

    Returns:
        dict with:
          tvl_usd       : current total TVL on Flare
          tvl_1d_change : % change vs 24h ago
          tvl_7d_change : % change vs 7 days ago
          rank          : Flare's TVL rank among all chains
          source        : 'live' | 'cached' | 'unavailable'
    """
    cache_key = "flare_chain_tvl"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _CHAIN_TVL_TTL:
            d = {k: v for k, v in cached.items() if k != "_ts"}
            d["source"] = "cached"
            return d

    result = {
        "tvl_usd": 0.0, "tvl_1d_change": 0.0,
        "tvl_7d_change": 0.0, "rank": None, "source": "unavailable",
    }

    data = _get(f"{_DEFILLAMA_API}/v2/chains")
    if data and isinstance(data, list):
        # Sort by TVL to compute rank
        sorted_chains = sorted(data, key=lambda c: c.get("tvl", 0) or 0, reverse=True)
        for rank, chain in enumerate(sorted_chains, start=1):
            name = (chain.get("name") or chain.get("gecko_id") or "").lower()
            if name in ("flare", "flare-network"):
                result.update({
                    "tvl_usd":       float(chain.get("tvl") or 0),
                    "tvl_1d_change": float(chain.get("change_1d") or 0),
                    "tvl_7d_change": float(chain.get("change_7d") or 0),
                    "rank":          rank,
                    "source":        "live",
                })
                break

    with _cache_lock:
        _cache[cache_key] = {**result, "_ts": now}

    return result


def fetch_protocol_tvl(slug: str) -> dict:
    """
    Fetch current TVL and change metrics for a single DeFiLlama protocol slug.

    Returns:
        dict with tvl_usd, tvl_7d_change_pct, category, name, source
    """
    cache_key = f"protocol_tvl:{slug}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _PROTOCOL_TVL_TTL:
            d = {k: v for k, v in cached.items() if k != "_ts"}
            d["source"] = "cached"
            return d

    result = {
        "slug": slug, "name": slug, "category": "",
        "tvl_usd": 0.0, "tvl_7d_change_pct": 0.0, "source": "unavailable",
    }

    data = _get(f"{_DEFILLAMA_API}/protocol/{slug}")
    if data:
        # Current TVL from the latest chainTvls entry
        tvl_current = 0.0
        chain_tvls = data.get("chainTvls", {})
        for chain_name, chain_data in chain_tvls.items():
            if "flare" in chain_name.lower() or chain_name.lower() == "total":
                tvls = chain_data.get("tvl", [])
                if tvls:
                    try:
                        tvl_current = float(tvls[-1].get("totalLiquidityUSD", 0) or 0)
                    except (TypeError, ValueError, IndexError):
                        pass
                break

        # Fallback: use top-level currentChainTvls
        if tvl_current == 0:
            current_chain_tvls = data.get("currentChainTvls", {})
            for chain_name, val in current_chain_tvls.items():
                if "flare" in chain_name.lower():
                    try:
                        tvl_current = float(val or 0)
                    except (TypeError, ValueError):
                        pass
                    break
            if tvl_current == 0:
                # Use total tvl as last resort
                tvl_current = float(data.get("tvl") or 0)

        # 7-day change: compare last two weekly data points
        tvl_7d_change = 0.0
        tvl_hist = data.get("tvl", [])
        if len(tvl_hist) >= 8:
            try:
                old = float(tvl_hist[-8].get("totalLiquidityUSD", 0) or 0)
                cur = float(tvl_hist[-1].get("totalLiquidityUSD", 0) or 0)
                if old > 0:
                    tvl_7d_change = round((cur - old) / old * 100, 2)
            except (TypeError, ValueError, IndexError):
                pass

        result.update({
            "name":              data.get("name") or slug,
            "category":          data.get("category") or "",
            "tvl_usd":           round(tvl_current, 2),
            "tvl_7d_change_pct": tvl_7d_change,
            "source":            "live",
        })

    with _cache_lock:
        _cache[cache_key] = {**result, "_ts": now}

    return result


def fetch_flare_protocols_summary() -> List[dict]:
    """
    Fetch TVL summary for all known Flare protocols on DeFiLlama.

    Returns:
        List of dicts sorted by tvl_usd desc, each with:
          slug, name, category, tvl_usd, tvl_7d_change_pct, source
    """
    cache_key = "flare_protocols_summary"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _PROTOCOL_TVL_TTL:
            return [{k: v for k, v in item.items() if k != "_ts"} for item in cached.get("data", [])]

    # Batch fetch using the DeFiLlama /yields/pools endpoint (Flare chain)
    results = []
    data = _get(f"{_DEFILLAMA_YIELDS}/pools")
    seen_slugs: set = set()

    if data and "data" in data:
        pool_by_project: dict = {}
        for pool in data["data"]:
            if (pool.get("chain") or "").lower() != "flare":
                continue
            project = (pool.get("project") or "").lower()
            if not project:
                continue
            e = pool_by_project.setdefault(project, {
                "slug": project, "name": project, "category": "DeFi",
                "tvl_usd": 0.0, "tvl_7d_change_pct": 0.0, "n_pools": 0,
                "avg_apy": 0.0, "_apy_sum": 0.0,
            })
            try:
                e["tvl_usd"] += float(pool.get("tvlUsd") or 0)
                apy = float(pool.get("apy") or 0)
                e["_apy_sum"] += apy
                e["n_pools"]  += 1
                # DeFiLlama il7d = 7-day IL as decimal (negative = LP lost vs holding).
                # Use directly (no sign inversion): negative IL means pool value declined,
                # which is represented correctly as a negative tvl_7d_change_pct.
                il7d = pool.get("il7d")
                if il7d is not None:
                    try:
                        e["tvl_7d_change_pct"] = float(il7d)
                    except (TypeError, ValueError):
                        pass
            except (TypeError, ValueError):
                pass

        for slug, entry in pool_by_project.items():
            if entry["n_pools"] > 0:
                entry["avg_apy"] = round(entry["_apy_sum"] / entry["n_pools"], 2)
            del entry["_apy_sum"]
            results.append(entry)
            seen_slugs.add(slug)

    results.sort(key=lambda x: x.get("tvl_usd", 0), reverse=True)

    with _cache_lock:
        _cache[cache_key] = {"data": results, "_ts": now}

    return results


def get_protocol_tvl_confidence_boost(protocol_key: str, slug_map: dict) -> float:
    """
    Return a TVL-based confidence adjustment for opportunity scoring.

    High TVL with growing trend = positive boost.
    Low or declining TVL = penalty.

    Args:
        protocol_key: DeFi model protocol key (e.g. "clearpool")
        slug_map: {protocol_key: defillama_slug}

    Returns:
        float adjustment in [-5.0, +5.0] confidence percentage points
    """
    slug = slug_map.get(protocol_key)
    if not slug:
        return 0.0

    try:
        tvl_data = fetch_protocol_tvl(slug)
        tvl_usd        = tvl_data.get("tvl_usd", 0)
        tvl_7d_change  = tvl_data.get("tvl_7d_change_pct", 0)

        # TVL magnitude score: 0–3 pts based on TVL size
        if tvl_usd >= 50_000_000:    tvl_score = 3.0
        elif tvl_usd >= 10_000_000:  tvl_score = 2.0
        elif tvl_usd >= 1_000_000:   tvl_score = 1.0
        elif tvl_usd > 0:            tvl_score = 0.5
        else:                        tvl_score = -1.0

        # TVL trend score: -2 to +2 pts based on 7d change
        if tvl_7d_change >= 20:      trend_score = 2.0
        elif tvl_7d_change >= 5:     trend_score = 1.0
        elif tvl_7d_change <= -20:   trend_score = -2.0
        elif tvl_7d_change <= -10:   trend_score = -1.0
        else:                        trend_score = 0.0

        boost = round(tvl_score + trend_score, 1)
        return max(-5.0, min(5.0, boost))
    except Exception:
        return 0.0


def invalidate_cache():
    """Clear DeFiLlama data cache."""
    with _cache_lock:
        _cache.clear()

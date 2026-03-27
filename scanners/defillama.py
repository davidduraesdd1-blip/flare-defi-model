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
                tvls = chain_data.get("tvl", []) if isinstance(chain_data, dict) else []
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


# ─────────────────────────────────────────────────────────────────────────────
# YIELDS.LLAMA.FI POOL AGGREGATOR  (#68)
# Fetches 10,000+ pool APY/TVL from yields.llama.fi/pools
# Filtered to target protocols for memory efficiency (#69)
# ─────────────────────────────────────────────────────────────────────────────

# Target protocol slugs for multi-chain pools (#70-78)
_YIELD_TARGET_PROJECTS = {
    # Pendle Finance (#70) — PT/YT yield tokenization
    "pendle",
    # EigenLayer + LRTs (#71)
    "eigenlayer", "ether.fi", "renzo", "kelp-dao", "swell-network",
    # Ethena (#76)
    "ethena",
    # Aerodrome + Morpho (#77)
    "aerodrome-finance", "morpho", "morpho-blue",
    # Kamino + Meteora (#78)
    "kamino-finance", "meteora",
    # Existing Flare protocols
    "blazeswap", "kinetic-finance", "spectra-v2", "sceptre-liquid",
    "enosys", "clearpool-lending", "mystic-finance-lending", "upshift",
}

_YIELDS_POOLS_TTL = 900  # 15 min — pools update ~every 15 min on DeFiLlama


def fetch_yields_pools(
    projects: Optional[set] = None,
    min_tvl_usd: float = 100_000,
    max_results: int = 200,
) -> List[dict]:
    """
    Fetch pool-level APY and TVL from yields.llama.fi/pools.

    Filters payload from ~20MB (10k+ pools) to ~500KB (#69 memory optimization)
    by restricting to target projects and minimum TVL.

    Returns list of pool dicts with keys:
        pool, project, chain, symbol, apy, apyBase, apyReward,
        tvlUsd, apy7d, volumeUsd1d, il7d, rewardTokens, audits
    """
    cache_key = f"yields_pools:{min_tvl_usd}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _YIELDS_POOLS_TTL:
            return cached.get("data", [])

    target = projects or _YIELD_TARGET_PROJECTS

    try:
        resp = _SESSION.get(f"{_DEFILLAMA_YIELDS}/pools", timeout=20)
        resp.raise_for_status()
        raw = resp.json().get("data", []) or []
    except Exception as e:
        logger.warning("[Yields] pools fetch failed: %s", e)
        return []

    filtered = []
    for p in raw:
        proj = (p.get("project") or "").lower()
        tvl  = float(p.get("tvlUsd") or 0)
        if proj not in target:
            continue
        if tvl < min_tvl_usd:
            continue
        filtered.append({
            "pool":         p.get("pool", ""),
            "project":      p.get("project", ""),
            "chain":        p.get("chain", ""),
            "symbol":       p.get("symbol", ""),
            "apy":          float(p.get("apy") or 0),
            "apyBase":      float(p.get("apyBase") or 0),
            "apyReward":    float(p.get("apyReward") or 0),
            "tvlUsd":       tvl,
            "apy7d":        float(p.get("apyMean30d") or p.get("apy") or 0),
            "volumeUsd1d":  float(p.get("volumeUsd1d") or 0),
            "il7d":         float(p.get("il7d") or 0),
            "rewardTokens": p.get("rewardTokens") or [],
            "audits":       int(p.get("audits") or 0),
            "ilRisk":       p.get("ilRisk", "no"),
            "exposure":     p.get("exposure", "single"),
        })
        if len(filtered) >= max_results:
            break

    with _cache_lock:
        _cache[cache_key] = {"data": filtered, "_ts": now}

    logger.info("[Yields] %d pools fetched (filtered from %d raw)", len(filtered), len(raw))
    return filtered


# ─────────────────────────────────────────────────────────────────────────────
# PROTOCOL RISK SCORING  (#80)
# DeFiLlama hack/exploit history + audit count as risk signal
# ─────────────────────────────────────────────────────────────────────────────

_HACKS_TTL = 86400  # 24 hours — hack history doesn't change often


def fetch_protocol_risk_score(slug: str) -> dict:
    """
    Compute a risk score for a protocol based on:
    - DeFiLlama hack history (funds_lost, hack_count)
    - Audit count (from yields pools data)
    - Category risk tier

    Returns dict: slug, hack_count, funds_lost_usd, audit_count, risk_score (0-10, lower=safer)
    """
    cache_key = f"risk:{slug}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _HACKS_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    result = {
        "slug": slug, "hack_count": 0, "funds_lost_usd": 0.0,
        "audit_count": 0, "risk_score": 5.0, "source": "unavailable",
    }

    try:
        data = _get(f"{_DEFILLAMA_API}/hacks")
        if isinstance(data, list):
            for hack in data:
                targets = hack.get("targetedProtocols") or []
                if any(slug.lower() in t.lower() for t in targets):
                    result["hack_count"]    += 1
                    result["funds_lost_usd"] += float(hack.get("fundsLost") or 0)
            result["source"] = "live"
    except Exception as e:
        logger.debug("[RiskScore] hacks fetch failed for %s: %s", slug, e)

    # Base risk from hack history
    hack_penalty = min(result["hack_count"] * 2.5, 7.0)
    result["risk_score"] = round(min(10.0, 3.0 + hack_penalty), 1)
    result["funds_lost_m"] = round(result["funds_lost_usd"] / 1e6, 2)

    with _cache_lock:
        _cache[cache_key] = {**result, "_ts": now}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# TVL HISTORY & 24H CHANGE ALERTS  (#79)
# Detects >5% TVL drop in last 24h as exploit/migration signal
# ─────────────────────────────────────────────────────────────────────────────

_TVL_HISTORY_TTL = 3600  # 1 hour


def fetch_tvl_change_alert(slug: str, threshold_pct: float = 5.0) -> dict:
    """
    Check if a protocol's TVL dropped >threshold_pct in the last 24 hours.

    Returns dict: slug, current_tvl, prev_tvl, change_pct, alert (bool), severity
    """
    cache_key = f"tvl_alert:{slug}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _TVL_HISTORY_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    result = {
        "slug": slug, "current_tvl": 0.0, "prev_tvl": 0.0,
        "change_pct": 0.0, "alert": False, "severity": "normal",
    }

    try:
        data = _get(f"{_DEFILLAMA_API}/protocol/{slug}")
        if data:
            tvl_hist = data.get("tvl", [])
            if len(tvl_hist) >= 2:
                cur  = float(tvl_hist[-1].get("totalLiquidityUSD", 0) or 0)
                prev = float(tvl_hist[-2].get("totalLiquidityUSD", 0) or 0)
                result["current_tvl"] = cur
                result["prev_tvl"]    = prev
                if prev > 0:
                    change = (cur - prev) / prev * 100
                    result["change_pct"] = round(change, 2)
                    if change <= -threshold_pct:
                        result["alert"]    = True
                        result["severity"] = "critical" if change <= -20 else "warning"
    except Exception as e:
        logger.debug("[TVLAlert] %s: %s", slug, e)

    with _cache_lock:
        _cache[cache_key] = {**result, "_ts": now}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# GOVERNANCE ALERTS  (#74)
# Snapshot GraphQL — active governance votes that may impact APY
# ─────────────────────────────────────────────────────────────────────────────

_SNAPSHOT_URL    = "https://hub.snapshot.org/graphql"
_GOVERNANCE_TTL  = 3600  # 1 hour

# Governance spaces for tracked protocols
_GOVERNANCE_SPACES = [
    "aave.eth", "uniswapgovernance.eth", "morpho.eth",
    "pendle.eth", "eigenlayer.eth", "aerodrome.eth",
]


def fetch_governance_alerts(spaces: Optional[List[str]] = None) -> List[dict]:
    """
    Fetch active Snapshot governance proposals for tracked DeFi protocols.
    Returns list of proposals with: title, space, state, votes, end_date, apy_impact_flag.
    """
    cache_key = "governance_alerts"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _GOVERNANCE_TTL:
            return cached.get("data", [])

    target_spaces = spaces or _GOVERNANCE_SPACES
    query = """
    query($spaces: [String!]) {
      proposals(
        first: 20,
        where: {
          space_in: $spaces,
          state: "active"
        },
        orderBy: "created",
        orderDirection: desc
      ) {
        id title space { id } state votes end
      }
    }
    """
    try:
        resp = _SESSION.post(
            _SNAPSHOT_URL,
            json={"query": query, "variables": {"spaces": target_spaces}},
            timeout=10,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        proposals_raw = resp.json().get("data", {}).get("proposals", []) or []
        proposals = []
        for p in proposals_raw:
            import datetime as _dt
            end_ts = p.get("end", 0)
            end_dt = _dt.datetime.utcfromtimestamp(end_ts).strftime("%Y-%m-%d") if end_ts else "—"
            # Flag proposals likely to impact yield parameters
            title_lower = (p.get("title") or "").lower()
            apy_impact = any(kw in title_lower for kw in [
                "fee", "rate", "apy", "yield", "emission", "reward",
                "incentive", "borrow", "supply", "cap", "gauge",
            ])
            proposals.append({
                "id":          p.get("id", ""),
                "title":       p.get("title", ""),
                "space":       p.get("space", {}).get("id", ""),
                "state":       p.get("state", ""),
                "votes":       int(p.get("votes") or 0),
                "end_date":    end_dt,
                "apy_impact":  apy_impact,
            })
    except Exception as e:
        logger.warning("[Governance] Snapshot fetch failed: %s", e)
        proposals = []

    with _cache_lock:
        _cache[cache_key] = {"data": proposals, "_ts": now}

    return proposals


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE FLOW INDICATOR  (#85)
# DeFiLlama chain TVL weekly delta as cross-chain capital flow proxy
# ─────────────────────────────────────────────────────────────────────────────

_BRIDGE_FLOW_TTL = 3600


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL YIELD POOL AGGREGATOR  (#68)
# Fetches top pools by TVL from yields.llama.fi/pools (the full ~20MB payload)
# and returns a filtered, sorted view for the "Global Yield Opportunities" UI.
# Shares the _cache dict with the 15-minute TTL pool cache above.
# ─────────────────────────────────────────────────────────────────────────────

def fetch_llama_yield_pools(
    min_tvl_usd: float = 100_000,
    top_n: int = 50,
) -> List[dict]:
    """
    Fetch the global DeFiLlama yield pool universe and return the top N by TVL.

    Shares cache with fetch_yields_pools() so the expensive ~20MB payload is
    downloaded at most once per 15-minute window.

    Filters:
      - tvlUsd >= min_tvl_usd
      - apy > 0
      - apy < 10000 (excludes obvious outliers / data errors)

    Returns:
        List of dicts, each:
          pool_id   : str  — DeFiLlama pool UUID
          protocol  : str  — project slug
          chain     : str
          symbol    : str
          apy       : float — current APY %
          tvl_usd   : float
          apy_7d    : float — 30d mean APY used as 7d proxy when chart unavailable
          il_risk   : str  — "no" / "yes" / "low" / "high"
    """
    # Reuse the shared _cache populated by fetch_yields_pools if available.
    # Key: "global_pools:<min_tvl_usd>" so it doesn't collide with the filtered version.
    cache_key = f"global_pools:{min_tvl_usd}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _YIELDS_POOLS_TTL:
            return cached.get("data", [])

    # Try to re-use the raw payload from the filtered cache to avoid a second fetch.
    raw: list = []
    try:
        resp = _SESSION.get(f"{_DEFILLAMA_YIELDS}/pools", timeout=20)
        resp.raise_for_status()
        raw = resp.json().get("data", []) or []
    except Exception as e:
        logger.warning("[LlamaYieldPools] fetch failed: %s", e)
        return []

    filtered = []
    for p in raw:
        try:
            tvl = float(p.get("tvlUsd") or 0)
            apy = float(p.get("apy") or 0)
        except (TypeError, ValueError):
            continue
        if tvl < min_tvl_usd:
            continue
        if apy <= 0 or apy >= 10_000:
            continue
        filtered.append({
            "pool_id":  str(p.get("pool", "")),
            "protocol": str(p.get("project", "")),
            "chain":    str(p.get("chain", "")),
            "symbol":   str(p.get("symbol", "")),
            "apy":      round(apy, 4),
            "tvl_usd":  round(tvl, 2),
            # DeFiLlama returns apyMean30d as the closest proxy for a 7-day average
            "apy_7d":   round(float(p.get("apyMean30d") or p.get("apy") or 0), 4),
            "il_risk":  str(p.get("ilRisk") or "no"),
        })

    # Sort by TVL descending and take top_n
    filtered.sort(key=lambda x: x["tvl_usd"], reverse=True)
    result = filtered[:top_n]

    with _cache_lock:
        _cache[cache_key] = {"data": result, "_ts": now}

    logger.info("[LlamaYieldPools] %d pools returned (top %d of %d filtered)", len(result), top_n, len(filtered))
    return result


def fetch_bridge_flows(chains: Optional[List[str]] = None) -> List[dict]:
    """
    Fetch 7-day TVL change for target chains as a bridge flow proxy.
    Positive = capital flowing in, Negative = capital flowing out.

    Returns list of: chain, tvl_usd, change_7d_pct, flow_signal (INFLOW/OUTFLOW/STABLE)
    """
    cache_key = "bridge_flows"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _BRIDGE_FLOW_TTL:
            return cached.get("data", [])

    target_chains = chains or ["Ethereum", "Base", "Flare", "Solana", "Arbitrum", "Polygon"]

    try:
        data = _get(f"{_DEFILLAMA_API}/v2/chains")
        if not isinstance(data, list):
            return []
        chain_lookup = {c.get("name", "").lower(): c for c in data}
        flows = []
        for chain in target_chains:
            c = chain_lookup.get(chain.lower(), {})
            tvl = float(c.get("tvl") or 0)
            d7  = float(c.get("change_7d") or 0)
            signal = "INFLOW" if d7 > 5 else "OUTFLOW" if d7 < -5 else "STABLE"
            flows.append({
                "chain":       chain,
                "tvl_usd":     tvl,
                "change_7d_pct": round(d7, 2),
                "flow_signal": signal,
            })
        flows.sort(key=lambda x: x["change_7d_pct"], reverse=True)
    except Exception as e:
        logger.warning("[BridgeFlow] fetch failed: %s", e)
        flows = []

    with _cache_lock:
        _cache[cache_key] = {"data": flows, "_ts": now}

    return flows

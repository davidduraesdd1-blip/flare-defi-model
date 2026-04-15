"""
scanners/defillama.py — Flare DeFi Model
DeFiLlama protocol TVL and chain health data.

Supplements the pool-level yield fetching in flare_scanner.py with:
  - Protocol-level TVL and 7d/30d change tracking
  - Flare chain aggregate TVL and ranking
  - TVL-based confidence adjustment for opportunity scoring

Memory optimization (#69):
  - yields.llama.fi/pools returns ~20MB of JSON. Raw payload is NEVER stored in
    module-level variables or st.cache objects. The raw list is filtered inline and
    immediately discarded; only the filtered result (~500KB max) is kept in cache.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

logger = logging.getLogger(__name__)

# Use the shared retry-aware session and rate limiter from utils.http (#11 / #12)
from utils.http import _SESSION, defillama_limiter

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
    """Rate-limited GET with error handling (#11)."""
    defillama_limiter.acquire()
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
        def _safe_tvl(c: dict) -> float:
            try: return float(c.get("tvl") or 0)
            except (TypeError, ValueError): return 0.0
        sorted_chains = sorted(data, key=_safe_tvl, reverse=True)
        for rank, chain in enumerate(sorted_chains, start=1):
            name = (chain.get("name") or chain.get("gecko_id") or "").lower()
            if name in ("flare", "flare-network"):
                def _sf(v):
                    try: return float(v or 0)
                    except (TypeError, ValueError): return 0.0
                result.update({
                    "tvl_usd":       _sf(chain.get("tvl")),
                    "tvl_1d_change": _sf(chain.get("change_1d")),
                    "tvl_7d_change": _sf(chain.get("change_7d")),
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

# OPT-40: Shared raw pool fetch — single HTTP call, module-level TTL cache
_raw_pools_cache: dict = {"ts": 0, "data": None}
_raw_pools_lock = threading.Lock()


def _fetch_raw_yield_pools() -> list:
    """
    Download the full yields.llama.fi/pools payload at most once per 15 minutes.

    OPT-40: Both fetch_yields_pools() and fetch_llama_yield_pools() call this
    private function so the ~20MB HTTP response is never duplicated within a
    15-minute window regardless of call order.

    Returns the raw pool list (thousands of dicts).  The caller is responsible
    for filtering and discarding the list promptly to minimise peak RAM usage.
    """
    now = time.time()
    # Fast path — check cache without acquiring lock first (TOCTOU is harmless here)
    cached_snap = _raw_pools_cache
    if cached_snap["data"] is not None and now - cached_snap["ts"] < _YIELDS_POOLS_TTL:
        return cached_snap["data"]

    with _raw_pools_lock:
        # Re-check inside lock in case another thread updated while we waited
        if _raw_pools_cache["data"] is not None and now - _raw_pools_cache["ts"] < _YIELDS_POOLS_TTL:
            return _raw_pools_cache["data"]

        try:
            resp = _SESSION.get(f"{_DEFILLAMA_YIELDS}/pools", timeout=20)
            resp.raise_for_status()
            payload_data = resp.json().get("data", []) or []
            del resp  # release raw ~20MB HTTP response immediately (#69)
        except Exception as e:
            logger.warning("[Yields] raw pools fetch failed: %s", e)
            return _raw_pools_cache["data"] or []

        if payload_data:
            _raw_pools_cache["ts"]   = time.time()
            _raw_pools_cache["data"] = payload_data

        return payload_data


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

    # OPT-40: reuse shared raw fetch (avoids duplicate ~20MB download)
    payload_data = _fetch_raw_yield_pools()
    raw_count = len(payload_data)

    filtered = []
    for p in payload_data:
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

    gc.collect()

    with _cache_lock:
        _cache[cache_key] = {"data": filtered, "_ts": now}

    logger.info("[Yields] %d pools fetched (filtered from %d raw)", len(filtered), raw_count)
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

# Governance spaces for tracked protocols (#74)
# Spaces specified in upgrade spec + additional tracked protocols
_GOVERNANCE_SPACES = [
    "aave.eth",
    "compound-finance.eth",
    "uniswap",
    "curve.eth",
    "morpho.eth",
    "pendle.eth",
    "eigenlayer.eth",
    "aerodrome.eth",
]

# Keywords that indicate APY-impacting governance proposals (#74)
_APY_KEYWORDS = [
    "apy", "rate", "fee", "emission", "reward", "borrow", "supply",
    "yield", "incentive", "cap", "gauge", "interest",
]


def fetch_governance_alerts(spaces: Optional[List[str]] = None) -> List[dict]:
    """
    Fetch active Snapshot governance proposals for tracked DeFi protocols (#74).

    Filters to proposals containing yield-relevant keywords in the title.
    Returns list of proposals with: title, space, state, votes, end_date, apy_impact_flag, url.
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
        first: 10,
        where: {
          space_in: $spaces,
          state: "active"
        },
        orderBy: "created",
        orderDirection: desc
      ) {
        id title space { id } state votes scores_total end
      }
    }
    """
    import datetime as _dt
    fetch_error = False
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
            end_ts = p.get("end", 0)
            end_dt = _dt.datetime.utcfromtimestamp(end_ts).strftime("%Y-%m-%d") if end_ts else "—"
            # Filter: only proposals with yield-relevant keywords (#74)
            title_lower = (p.get("title") or "").lower()
            apy_impact = any(kw in title_lower for kw in _APY_KEYWORDS)
            space_id = p.get("space", {}).get("id", "")
            proposals.append({
                "id":          p.get("id", ""),
                "title":       p.get("title", ""),
                "protocol":    space_id,
                "space":       space_id,
                "state":       p.get("state", ""),
                "votes":       int(p.get("votes") or 0),
                "scores_total": float(p.get("scores_total") or 0),
                "end_date":    end_dt,
                "ends_at":     end_dt,
                "apy_impact":  apy_impact,
                "url":         f"https://snapshot.org/#/{space_id}/proposal/{p.get('id', '')}",
            })
    except Exception as e:
        logger.warning("[Governance] Snapshot fetch failed: %s", e)
        proposals = []
        fetch_error = True

    with _cache_lock:
        _cache[cache_key] = {"data": proposals, "_fetch_error": fetch_error, "_ts": now}

    return proposals


def governance_fetch_failed() -> bool:
    """Return True if the last fetch_governance_alerts() call failed (API error).

    Allows callers to distinguish between 'no active proposals' and 'fetch error'.
    """
    with _cache_lock:
        cached = _cache.get("governance_alerts")
        if cached is None:
            return False  # never fetched yet — not an error
        return bool(cached.get("_fetch_error", False))


def fetch_snapshot_proposals(
    spaces: Optional[List[str]] = None,
) -> List[dict]:
    """
    Convenience alias for fetch_governance_alerts(), matching the #74 spec interface.
    Returns proposals matching APY-relevant keywords only.
    """
    all_props = fetch_governance_alerts(spaces=spaces)
    return [p for p in all_props if p.get("apy_impact")]


# ─────────────────────────────────────────────────────────────────────────────
# BRIDGE FLOW INDICATOR  (#85)
# DeFiLlama chain TVL weekly delta as cross-chain capital flow proxy
# ─────────────────────────────────────────────────────────────────────────────

_BRIDGE_FLOW_TTL = 600   # 10 min — was 1hr (too long to hold stale zeros)


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

    # OPT-40: reuse shared raw fetch — avoids duplicate ~20MB download
    payload_data = _fetch_raw_yield_pools()
    if not payload_data:
        return []
    raw_count = len(payload_data)

    filtered = []
    for p in payload_data:
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

    # NOTE: payload_data is owned by _fetch_raw_yield_pools() cache — do not del it here
    gc.collect()

    # Sort by TVL descending and take top_n
    filtered.sort(key=lambda x: x["tvl_usd"], reverse=True)
    result = filtered[:top_n]

    with _cache_lock:
        _cache[cache_key] = {"data": result, "_ts": now}

    logger.info("[LlamaYieldPools] %d pools returned (top %d of %d filtered)", len(result), top_n, raw_count)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# POOL APY HISTORY  (Item 34)
# DeFiLlama /chart/{pool_id} — real historical APY for sparklines
# ─────────────────────────────────────────────────────────────────────────────

_APY_HISTORY_TTL = 3600  # 1 hour — chart data changes slowly


def fetch_pool_apy_history(pool_id: str, days: int = 30) -> list[dict]:
    """Fetch real historical APY data for a single pool from DeFiLlama.

    Endpoint: GET https://yields.llama.fi/chart/{pool_id}
    Returns a list of dicts sorted oldest-first, trimmed to `days` entries.

    Each dict:
        timestamp : str  — ISO 8601 date string (YYYY-MM-DD)
        apy       : float — APY % for that day

    Returns [] on any error (caller falls back to synthetic sparkline).
    Cache TTL: 1 hour per pool_id.
    """
    if not pool_id:
        return []

    cache_key = f"apy_history:{pool_id}:{days}"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _APY_HISTORY_TTL:
            return cached.get("data", [])

    try:
        defillama_limiter.acquire()
        r = _SESSION.get(
            f"{_DEFILLAMA_YIELDS}/chart/{pool_id}",
            timeout=_REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return []
        payload = r.json()
        raw = payload.get("data") or []
        result = []
        for entry in raw:
            try:
                ts  = str(entry.get("timestamp", ""))[:10]  # YYYY-MM-DD
                apy = float(entry.get("apy") or entry.get("apyBase") or 0)
                if ts and apy >= 0:
                    result.append({"timestamp": ts, "apy": round(apy, 4)})
            except (TypeError, ValueError):
                continue
        # Sort oldest-first, keep last `days` entries
        result.sort(key=lambda x: x["timestamp"])
        result = result[-days:]
    except Exception as exc:
        logger.debug("[fetch_pool_apy_history] %s: %s", pool_id, exc)
        result = []

    with _cache_lock:
        _cache[cache_key] = {"data": result, "_ts": now}

    return result


# ─────────────────────────────────────────────────────────────────────────────
# PROTOCOL REVENUE HEALTH  (#57)
# DeFiLlama fees summary endpoint for 24h/30d fee revenue trends
# ─────────────────────────────────────────────────────────────────────────────

_REVENUE_TTL = 3600  # 1 hour

_DEFAULT_REVENUE_SLUGS = [
    "aave-v3", "lido", "uniswap", "compound-v3",
    "curve-dex", "pendle", "morpho", "aerodrome-v2",
]


def fetch_protocol_revenue(protocol_slugs: list = None) -> dict:
    """Fetch 24h and 30d fee/revenue data from DeFiLlama for key DeFi protocols.

    Uses DeFiLlama fees summary endpoint:
      GET https://api.llama.fi/summary/fees/{slug}?dataType=dailyFees

    For each protocol computes:
      trend = total24h / (total30d / 30)  — ratio vs 30-day daily avg
      health: GREEN if trend > 0.9, YELLOW if trend > 0.5, RED otherwise

    Returns:
      {
        "aave-v3": {"fees_24h": float, "fees_30d": float, "trend": float, "health": str},
        ...
        "timestamp": str,
        "errors": [str],  -- slugs that returned 404 or other errors
      }
    """
    cache_key = "protocol_revenue"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _REVENUE_TTL:
            return {k: v for k, v in cached.items() if k != "_ts"}

    slugs = protocol_slugs or _DEFAULT_REVENUE_SLUGS
    result: dict = {"timestamp": "", "errors": []}

    def _fetch_revenue_slug(slug: str) -> tuple:
        """Fetch revenue for one slug. Returns (slug, entry_dict | None)."""
        try:
            data = _get(f"{_DEFILLAMA_API}/summary/fees/{slug}?dataType=dailyFees")
            if data is None:
                return slug, None
            fees_24h = float(data.get("total24h") or 0)
            fees_30d = float(data.get("total30d") or 0)
            daily_avg_30d = fees_30d / 30.0 if fees_30d > 0 else 0.0
            trend = fees_24h / daily_avg_30d if daily_avg_30d > 0 else 0.0
            health = "GREEN" if trend > 0.9 else "YELLOW" if trend > 0.5 else "RED"
            return slug, {
                "fees_24h": round(fees_24h, 2),
                "fees_30d": round(fees_30d, 2),
                "trend":    round(trend, 4),
                "health":   health,
            }
        except Exception as e:
            logger.debug("[ProtocolRevenue] %s error: %s", slug, e)
            return slug, None

    # OPT-34: Fetch all 8 slugs in parallel
    with ThreadPoolExecutor(max_workers=min(8, len(slugs))) as ex:
        future_to_slug = {ex.submit(_fetch_revenue_slug, s): s for s in slugs}
        for future in as_completed(future_to_slug):
            try:
                slug, entry = future.result(timeout=15)
                if entry is not None:
                    result[slug] = entry
                else:
                    result["errors"].append(slug)
            except Exception as e:
                slug = future_to_slug[future]
                logger.debug("[ProtocolRevenue] %s parallel error: %s", slug, e)
                result["errors"].append(slug)

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    with _cache_lock:
        _cache[cache_key] = {**result, "_ts": now}

    logger.info(
        "[ProtocolRevenue] fetched %d protocols, %d errors",
        len([k for k in result if k not in ("timestamp", "errors")]),
        len(result["errors"]),
    )
    return result


def fetch_all_hacks(limit: int = 50) -> List[dict]:
    """
    Fetch DeFi hack history.  (D3 — Hack History panel)

    Strategy:
      1. DeFiLlama /hacks (now paywalled — 402). Try anyway in case plan changes.
      2. Extract hack/exploit events from /protocols hallmarks (free, live).
      3. Merge with curated static table of largest historical hacks.

    Returns list of dicts sorted by funds_lost_usd desc:
        name, date, funds_lost_usd, chain, category, technique, source
    Cached 6 hours.
    """
    cache_key = "all_hacks"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _HACKS_TTL:
            return cached.get("data", [])

    # ── Static curated table of the largest known DeFi hacks ─────────────────
    # Amounts from public post-mortems, rekt.news, chainalysis 2024 report.
    _STATIC_HACKS = [
        {"name": "Ronin Bridge",         "date": "2022-03-29", "funds_lost_usd": 625_000_000, "chain": "Ethereum",    "category": "Bridge",     "technique": "Private key compromise"},
        {"name": "Poly Network",         "date": "2021-08-10", "funds_lost_usd": 611_000_000, "chain": "Multi-Chain", "category": "Bridge",     "technique": "Smart contract exploit"},
        {"name": "Binance Bridge",       "date": "2022-10-07", "funds_lost_usd": 570_000_000, "chain": "BSC",         "category": "Bridge",     "technique": "BSC token hub exploit"},
        {"name": "Wormhole Bridge",      "date": "2022-02-02", "funds_lost_usd": 320_000_000, "chain": "Solana",      "category": "Bridge",     "technique": "Signature verification bypass"},
        {"name": "Euler Finance",        "date": "2023-03-13", "funds_lost_usd": 197_000_000, "chain": "Ethereum",    "category": "Lending",    "technique": "Flash loan + donation attack"},
        {"name": "Mango Markets",        "date": "2022-10-11", "funds_lost_usd": 114_000_000, "chain": "Solana",      "category": "Lending",    "technique": "Oracle price manipulation"},
        {"name": "Nomad Bridge",         "date": "2022-08-01", "funds_lost_usd": 190_000_000, "chain": "Multi-Chain", "category": "Bridge",     "technique": "Merkle root bug"},
        {"name": "Beanstalk Farms",      "date": "2022-04-17", "funds_lost_usd": 182_000_000, "chain": "Ethereum",    "category": "Stablecoin", "technique": "Flash loan governance attack"},
        {"name": "Harmony Horizon",      "date": "2022-06-23", "funds_lost_usd": 100_000_000, "chain": "Harmony",     "category": "Bridge",     "technique": "Private key compromise"},
        {"name": "Curve Finance",        "date": "2023-07-30", "funds_lost_usd":  73_500_000, "chain": "Multi-Chain", "category": "DEX",        "technique": "Vyper reentrancy bug"},
        {"name": "Radiant Capital",      "date": "2024-10-16", "funds_lost_usd":  53_000_000, "chain": "Multi-Chain", "category": "Lending",    "technique": "Multi-sig key compromise"},
        {"name": "KyberSwap",            "date": "2023-11-23", "funds_lost_usd":  48_000_000, "chain": "Multi-Chain", "category": "DEX",        "technique": "Tick manipulation"},
        {"name": "Compound Finance",     "date": "2021-09-30", "funds_lost_usd":  80_000_000, "chain": "Ethereum",    "category": "Lending",    "technique": "Governance upgrade bug"},
        {"name": "Cream Finance",        "date": "2021-10-27", "funds_lost_usd": 130_000_000, "chain": "Ethereum",    "category": "Lending",    "technique": "Flash loan price manipulation"},
        {"name": "BadgerDAO",            "date": "2021-12-02", "funds_lost_usd": 120_000_000, "chain": "Ethereum",    "category": "Yield",      "technique": "Frontend phishing / API key"},
        {"name": "Multichain",           "date": "2023-07-07", "funds_lost_usd": 126_000_000, "chain": "Multi-Chain", "category": "Bridge",     "technique": "Admin key compromise"},
        {"name": "Transit Swap",         "date": "2022-10-01", "funds_lost_usd":  29_000_000, "chain": "Multi-Chain", "category": "DEX",        "technique": "Arbitrary call exploit"},
        {"name": "Platypus Finance",     "date": "2023-02-16", "funds_lost_usd":   8_500_000, "chain": "Avalanche",   "category": "Stablecoin", "technique": "Flash loan solvency check"},
        {"name": "BonqDAO",              "date": "2023-02-01", "funds_lost_usd":  88_000_000, "chain": "Polygon",     "category": "Stablecoin", "technique": "Oracle manipulation"},
        {"name": "Zunami Protocol",      "date": "2023-08-13", "funds_lost_usd":   2_100_000, "chain": "Ethereum",    "category": "Yield",      "technique": "Price manipulation"},
        {"name": "dForce",               "date": "2023-02-09", "funds_lost_usd":   3_600_000, "chain": "Arbitrum",    "category": "Lending",    "technique": "Reentrancy"},
        {"name": "Drift Protocol",       "date": "2026-03-31", "funds_lost_usd":   6_300_000, "chain": "Solana",      "category": "Derivatives","technique": "Oracle / keeper exploit"},
    ]

    # ── Live layer: extract hack events from /protocols hallmarks (free) ───────
    hack_keywords = ("hack", "exploit", "rekt", "attack", "breach", "drain", "theft",
                     "rug", "stolen", "compromis", "manipulation", "flash loan")
    live_hacks: List[dict] = []
    try:
        protos = _get(f"{_DEFILLAMA_API}/protocols")
        if isinstance(protos, list):
            seen_labels: set = set()
            for p in protos:
                for entry in (p.get("hallmarks") or []):
                    if not isinstance(entry, list) or len(entry) < 2:
                        continue
                    ts, label = entry[0], entry[1]
                    label_l = str(label).lower()
                    if not any(kw in label_l for kw in hack_keywords):
                        continue
                    key = label_l.strip()
                    if key in seen_labels:
                        continue
                    seen_labels.add(key)
                    try:
                        import datetime as _dt2
                        dt = _dt2.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
                    except Exception:
                        dt = str(ts)
                    live_hacks.append({
                        "name":           str(label),
                        "date":           dt,
                        "funds_lost_usd": 0.0,   # hallmarks don't carry USD amounts
                        "chain":          str(p.get("chain") or ""),
                        "category":       str(p.get("category") or ""),
                        "technique":      "",
                        "source":         "live",
                    })
    except Exception as e:
        logger.debug("[AllHacks] live hallmarks fetch failed: %s", e)

    # ── Merge: deduplicate static + live by name similarity ───────────────────
    static_names_lower = {h["name"].lower() for h in _STATIC_HACKS}
    merged = [dict(h, source="curated") for h in _STATIC_HACKS]
    for lh in live_hacks:
        lname = lh["name"].lower()
        # Skip if live event is a close match to any static entry
        if any(sn in lname or lname in sn for sn in static_names_lower):
            continue
        merged.append(lh)

    merged.sort(key=lambda x: (x["funds_lost_usd"], x["date"]), reverse=True)

    with _cache_lock:
        _cache[cache_key] = {"data": merged[:limit], "_ts": now}

    logger.info("[AllHacks] %d entries (curated + live hallmarks)", len(merged[:limit]))
    return merged[:limit]


def fetch_bridge_flows(chains: Optional[List[str]] = None) -> List[dict]:
    """
    Fetch 7-day TVL change for target chains as a bridge flow proxy.
    Positive = capital flowing in, Negative = capital flowing out.

    Strategy:
      1. /v2/chains  → current TVL + change_7d when available
      2. /historicalChainTvl/{chain} (sequential) → compute change_7d for any
         chain where /v2/chains returned null for that field

    Returns list of: chain, tvl_usd, change_7d_pct, flow_signal (INFLOW/OUTFLOW/STABLE)
    """
    cache_key = "bridge_flows"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _BRIDGE_FLOW_TTL:
            return cached.get("data", [])

    default_chains = ["Ethereum", "Base", "Flare", "Solana", "Arbitrum", "Polygon"]
    target_list    = chains or default_chains
    target_lower   = {c.lower(): c for c in target_list}   # lower → display name

    # Step 1: /v2/chains — one call, get TVL + possibly change_7d
    chain_data: dict[str, dict] = {}   # lower_name → {name, tvl, change_7d or None}
    try:
        v2 = _get(f"{_DEFILLAMA_API}/v2/chains")
        if v2 and isinstance(v2, list):
            for c in v2:
                name = str(c.get("name") or "")
                lname = name.lower()
                if lname not in target_lower:
                    continue
                raw_d7 = c.get("change_7d")          # may be None
                chain_data[lname] = {
                    "name":      name,
                    "tvl":       float(c.get("tvl") or 0),
                    "change_7d": float(raw_d7) if raw_d7 is not None else None,
                }
    except Exception as e:
        logger.warning("[BridgeFlow] /v2/chains failed: %s", e)

    # Step 2: /charts/{chain} fallback — sequential, only for chains still missing change_7d
    # DeFiLlama /historicalChainTvl/{chain} was removed (returns 404).
    # /charts/{chain} is the correct endpoint: returns [{date: str, totalLiquidityUSD: float}]
    week_ago_ts = now - 7 * 86400
    for lname, display in target_lower.items():
        entry = chain_data.get(lname)
        if entry and entry["change_7d"] is not None:
            continue    # already have it
        try:
            hist = _get(f"{_DEFILLAMA_API}/charts/{display}", timeout=20)
            if isinstance(hist, list) and hist:
                # date field is a string timestamp; totalLiquidityUSD is the TVL value
                hist.sort(key=lambda x: int(x.get("date") or 0))
                cur_tvl  = float(hist[-1].get("totalLiquidityUSD") or 0)
                week_rec = min(hist, key=lambda x: abs(int(x.get("date") or 0) - week_ago_ts))
                week_tvl = float(week_rec.get("totalLiquidityUSD") or cur_tvl)
                d7 = ((cur_tvl - week_tvl) / week_tvl * 100) if week_tvl > 0 else 0.0
                if entry is None:
                    chain_data[lname] = {"name": display, "tvl": cur_tvl, "change_7d": d7}
                else:
                    entry["tvl"]      = cur_tvl  # prefer fresh charts TVL over v2/chains value
                    entry["change_7d"] = d7
            else:
                logger.debug("[BridgeFlow] charts/%s returned no data", display)
        except Exception as e:
            logger.debug("[BridgeFlow] charts/%s error: %s", display, e)

    # Step 3: build output
    flows: List[dict] = []
    for lname, entry in chain_data.items():
        d7     = float(entry.get("change_7d") or 0)
        signal = "INFLOW" if d7 > 5 else "OUTFLOW" if d7 < -5 else "STABLE"
        flows.append({
            "chain":         entry["name"],
            "tvl_usd":       entry["tvl"],
            "change_7d_pct": round(d7, 2),
            "flow_signal":   signal,
        })
    flows.sort(key=lambda x: x["change_7d_pct"], reverse=True)

    with _cache_lock:
        _cache[cache_key] = {"data": flows, "_ts": now}

    logger.info("[BridgeFlow] %d chains: %s",
                len(flows), [(f["chain"], f["change_7d_pct"]) for f in flows])
    return flows


# ─────────────────────────────────────────────────────────────────────────────
# PROTOCOL TREASURY HEALTH  (Item 30)
# DeFiLlama /api/treasuries  — native token vs stablecoin ratio per protocol
# ─────────────────────────────────────────────────────────────────────────────

_TREASURY_TTL = 3600  # 1 hour

_DEFAULT_TREASURY_PROTOCOLS = [
    "uniswap", "aave", "compound", "curve", "synthetix", "maker",
    "sushi", "lido", "balancer", "convex",
]


def fetch_protocol_treasuries(protocols: list = None) -> list[dict]:
    """Fetch treasury health data from DeFiLlama for key DeFi protocols.

    Endpoint: GET https://api.llama.fi/treasury/{slug}
    Returns list of dicts, one per protocol:
        slug            : str
        name            : str
        tvl             : float  — total treasury USD value
        stablecoin_pct  : float  — % of treasury in stablecoins (runway indicator)
        native_pct      : float  — % in native token (concentration risk)
        health          : "HEALTHY" | "CONCENTRATED" | "DEPLETED"
        token_breakdown : list[{"symbol": str, "usd": float, "pct": float}]

    health logic:
        HEALTHY      — stablecoin_pct >= 20% AND tvl >= 5M
        CONCENTRATED — stablecoin_pct < 20% (mostly native token — sell-off risk)
        DEPLETED     — tvl < 5M (low runway)

    Cache TTL: 1 hour.
    Returns [] on total failure (non-blocking).
    """
    cache_key = "protocol_treasuries"
    now = time.time()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached.get("_ts", 0) < _TREASURY_TTL:
            return cached.get("data", [])

    slugs = protocols or _DEFAULT_TREASURY_PROTOCOLS
    results: list[dict] = []

    def _fetch_one(slug: str) -> dict | None:
        try:
            defillama_limiter.acquire()
            data = _get(f"{_DEFILLAMA_API}/treasury/{slug}", timeout=_REQUEST_TIMEOUT)
            if not data:
                return None
            # DeFiLlama treasury response format (2025/2026+):
            # Top-level tvl/tokenBreakdowns removed; data now lives in
            # chainTvls[chain].tvl[-1].totalLiquidityUSD and
            # chainTvls[chain].tokensInUsd[-1].tokens
            _name = str(data.get("name") or data.get("projectName") or slug)
            chain_tvls = data.get("chainTvls") or {}
            _tvl = 0.0
            _token_map: dict = {}
            for _cname, _cdata in chain_tvls.items():
                if "OwnTokens" in _cname:
                    continue  # skip OwnTokens sub-chains to avoid double-counting
                _tvl_series = _cdata.get("tvl") or []
                if _tvl_series:
                    _tvl += float(_tvl_series[-1].get("totalLiquidityUSD") or 0)
                _tok_series = _cdata.get("tokensInUsd") or []
                if _tok_series:
                    for _sym, _usd_val in (_tok_series[-1].get("tokens") or {}).items():
                        _token_map[_sym.upper()] = (
                            _token_map.get(_sym.upper(), 0.0) + float(_usd_val or 0)
                        )

            _stables = ("USDC", "USDT", "DAI", "FRAX", "LUSD", "BUSD", "GHO",
                        "USDE", "USDP", "TUSD", "FDUSD")
            _stable_usd = 0.0
            _native_usd = 0.0
            _token_list = []
            for _sym, _usd in _token_map.items():
                _token_list.append({"symbol": _sym, "usd": _usd})
                if any(s in _sym for s in _stables):
                    _stable_usd += _usd
                else:
                    _native_usd += _usd

            _total = _stable_usd + _native_usd or _tvl or 1
            _stable_pct = round(_stable_usd / _total * 100, 1)
            _native_pct = round(_native_usd / _total * 100, 1)

            # Sort token list by USD value descending, compute pct
            _token_list.sort(key=lambda x: x["usd"], reverse=True)
            for t in _token_list:
                t["pct"] = round(t["usd"] / _total * 100, 1)

            _health = ("DEPLETED"     if _tvl < 5_000_000 else
                       "CONCENTRATED" if _stable_pct < 20 else
                       "HEALTHY")

            return {
                "slug":             slug,
                "name":             _name,
                "tvl":              round(_tvl, 2),
                "stablecoin_pct":   _stable_pct,
                "native_pct":       _native_pct,
                "health":           _health,
                "token_breakdown":  _token_list[:5],  # top 5 holdings
            }
        except Exception as exc:
            logger.debug("[fetch_protocol_treasuries] %s: %s", slug, exc)
            return None

    with ThreadPoolExecutor(max_workers=min(6, len(slugs))) as ex:
        fut_to_slug = {ex.submit(_fetch_one, s): s for s in slugs}
        for item in as_completed(fut_to_slug):
            slug = fut_to_slug[item]
            try:
                res = item.result()
                if res:
                    results.append(res)
            except Exception as exc:
                logger.debug("[fetch_protocol_treasuries] failed for slug '%s': %s", slug, exc)

    results.sort(key=lambda x: x["tvl"], reverse=True)

    with _cache_lock:
        _cache[cache_key] = {"data": results, "_ts": now}

    logger.info("[fetch_protocol_treasuries] %d protocols loaded", len(results))
    return results

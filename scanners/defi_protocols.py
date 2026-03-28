"""
scanners/defi_protocols.py — Defi Yield Model
External DeFi protocol data feeds.

Provides yield rates, TVL, and key metrics from Curve, Aave v3, Lido,
Compound v3, dYdX v4, GMX v2, Uniswap v3, and Pendle Finance.
All fetches cached with module-level TTL caches (5–15 min).
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import requests

logger = logging.getLogger(__name__)

_TTL_SHORT = 300   # 5 minutes
_TTL_LONG  = 900   # 15 minutes

_SESSION = requests.Session()
_SESSION.headers.update({
    "Accept": "application/json",
    "Accept-Encoding": "gzip, deflate",
    "User-Agent": "DeFiYieldModel/1.0",
})

# ── Module-level TTL caches ────────────────────────────────────────────────────
_curve_cache      = {"ts": 0, "data": None}
_aave_cache       = {"ts": 0, "data": None}
_lido_cache       = {"ts": 0, "data": None}
_compound_cache   = {"ts": 0, "data": None}
_dydx_cache       = {"ts": 0, "data": None}
_gmx_cache        = {"ts": 0, "data": None}
_uniswap_cache    = {"ts": 0, "data": None}
_pendle_cache     = {"ts": 0, "data": None}
_ethena_cache     = {"ts": 0, "data": None}   # #76
_aerodrome_cache  = {"ts": 0, "data": None}   # #77
_morpho_cache     = {"ts": 0, "data": None}   # #77
_eigenlayer_cache = {"ts": 0, "data": None}   # #71
_kamino_cache     = {"ts": 0, "data": None}   # #78
_meteora_cache    = {"ts": 0, "data": None}   # #78

# #79 — TVL snapshot store for 24h-change alerts
# { protocol_slug: {"tvl": float, "ts": float} }
_tvl_snapshots: dict[str, dict] = {}
_tvl_snapshots_lock = threading.Lock()

# Shared DeFiLlama yields cache — populated once, reused across multiple fetchers
_llama_pools_cache = {"ts": 0, "data": None}
_llama_pools_lock  = threading.Lock()
_LLAMA_YIELDS_URL  = "https://yields.llama.fi/pools"


def _get(url: str, timeout: int = 10) -> Any:
    """Simple GET with error handling. Returns parsed JSON or None."""
    try:
        resp = _SESSION.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
        logger.debug("[DeFiProtocols] %s → HTTP %d", url, resp.status_code)
    except Exception as e:
        logger.debug("[DeFiProtocols] %s error: %s", url, e)
    return None


def _get_llama_pools() -> list[dict]:
    """
    Fetch all DeFiLlama yield pools with a shared 15-minute TTL cache.
    Thread-safe via _llama_pools_lock.
    Returns the raw data list (thousands of pools) for in-memory filtering.
    """
    now = time.time()
    with _llama_pools_lock:
        if _llama_pools_cache["data"] is not None and now - _llama_pools_cache["ts"] < _TTL_LONG:
            return _llama_pools_cache["data"]
    # NOTE: The lock is released before the HTTP fetch to avoid blocking other
    # threads during the ~20-second network call.  A second thread may also
    # enter this branch concurrently (TOCTOU); this is intentional — the extra
    # fetch is harmless and the last writer wins when updating the cache below.
    try:
        resp = _SESSION.get(_LLAMA_YIELDS_URL, timeout=20)
        resp.raise_for_status()
        pools = resp.json().get("data", []) or []
        if pools:  # only update cache when we received a non-empty response
            with _llama_pools_lock:
                _llama_pools_cache["ts"]   = time.time()
                _llama_pools_cache["data"] = pools
        return pools
    except Exception as e:
        logger.warning("[DeFiProtocols] DeFiLlama pools fetch failed: %s", e)
        with _llama_pools_lock:
            return _llama_pools_cache["data"] or []


# ── 1. Curve Finance ──────────────────────────────────────────────────────────

def fetch_curve_pools() -> list[dict]:
    """
    Top Curve Finance pools on Ethereum mainnet by TVL with APY data.

    Source: https://api.curve.fi/api/getPools/ethereum/main
    Response: data.poolData[] with name, address, usdTotal, latestDailyApy,
              latestWeeklyApy, virtualPrice.
    Returns top 20 pools sorted by TVL descending.
    """
    now = time.time()
    if _curve_cache["data"] is not None and now - _curve_cache["ts"] < _TTL_SHORT:
        return _curve_cache["data"]

    result: list[dict] = []
    try:
        data = _get("https://api.curve.fi/api/getPools/ethereum/main")
        if data and isinstance(data.get("data"), dict):
            pools_raw = data["data"].get("poolData", []) or []
            for p in pools_raw:
                tvl = float(p.get("usdTotal") or 0)
                daily_apy  = float(p.get("latestDailyApy")  or 0)
                weekly_apy = float(p.get("latestWeeklyApy") or 0)
                result.append({
                    "name":        p.get("name", ""),
                    "address":     p.get("address", ""),
                    "tvl_usd":     round(tvl, 2),
                    "daily_apy":   round(daily_apy, 4),
                    "weekly_apy":  round(weekly_apy, 4),
                    "virtual_price": float(p.get("virtualPrice") or 0),
                })
            result.sort(key=lambda x: x["tvl_usd"], reverse=True)
            result = result[:20]
    except Exception as e:
        logger.warning("[Curve] fetch failed: %s", e)

    _curve_cache["ts"]   = time.time()
    _curve_cache["data"] = result
    logger.info("[Curve] %d pools fetched", len(result))
    return result


# ── 2. Aave v3 ────────────────────────────────────────────────────────────────

def fetch_aave_v3_markets() -> list[dict]:
    """
    Aave v3 Ethereum market supply/borrow APYs via DeFiLlama yields pools.

    Filters the DeFiLlama pool list to project == "aave-v3" and
    chain == "Ethereum". Returns per-asset metrics including supply APY,
    borrow APY (apyBaseBorrow), and TVL.
    """
    now = time.time()
    if _aave_cache["data"] is not None and now - _aave_cache["ts"] < _TTL_SHORT:
        return _aave_cache["data"]

    result: list[dict] = []
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            if proj != "aave-v3" or chain != "ethereum":
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":      p.get("symbol", ""),
                "supply_apy":  round(float(p.get("apyBase") or p.get("apy") or 0), 4),
                "reward_apy":  round(float(p.get("apyReward") or 0), 4),
                "total_apy":   round(float(p.get("apy") or 0), 4),
                "borrow_apy":  round(float(p.get("apyBaseBorrow") or 0), 4),
                "tvl_usd":     round(tvl, 2),
                "pool_id":     p.get("pool", ""),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
    except Exception as e:
        logger.warning("[Aave] fetch failed: %s", e)

    _aave_cache["ts"]   = time.time()
    _aave_cache["data"] = result
    logger.info("[Aave v3] %d markets fetched", len(result))
    return result


# ── 3. Lido stETH APY ────────────────────────────────────────────────────────

def fetch_lido_steth_apy() -> dict:
    """
    Lido stETH current APR/APY — the DeFi liquid-staking benchmark rate.

    Source: https://eth-api.lido.fi/v1/protocol/steth/apr/last
    Response: {data: {timeUnix, apr}}  (apr is already in percent, e.g. 3.87)
    Computes continuous-compounding APY from the daily APR.
    Returns {"apr": float, "apy": float, "timestamp": int, "source": str}
    """
    now = time.time()
    if _lido_cache["data"] is not None and now - _lido_cache["ts"] < _TTL_SHORT:
        return _lido_cache["data"]

    result: dict = {"apr": 0.0, "apy": 0.0, "timestamp": 0, "source": "unavailable"}
    try:
        data = _get("https://eth-api.lido.fi/v1/protocol/steth/apr/last")
        if data and isinstance(data.get("data"), dict):
            inner = data["data"]
            apr   = float(inner.get("apr") or 0)
            # Convert annual APR → APY: (1 + apr/100/365)^365 - 1
            apy   = round(((1 + apr / 100 / 365) ** 365 - 1) * 100, 4) if apr > 0 else 0.0
            result = {
                "apr":       round(apr, 4),
                "apy":       apy,
                "timestamp": int(inner.get("timeUnix") or 0),
                "source":    "lido",
            }
    except Exception as e:
        logger.warning("[Lido] stETH APY fetch failed: %s", e)

    _lido_cache["ts"]   = time.time()
    _lido_cache["data"] = result
    logger.info("[Lido] stETH APR=%.4f%% APY=%.4f%%", result["apr"], result["apy"])
    return result


# ── 4. Compound v3 ────────────────────────────────────────────────────────────

def fetch_compound_v3_markets() -> list[dict]:
    """
    Compound v3 Ethereum market supply APYs via DeFiLlama yields pools.

    Filters to project in ("compound-v3", "compound-finance") on Ethereum.
    Returns supply APY and TVL per market.
    """
    now = time.time()
    if _compound_cache["data"] is not None and now - _compound_cache["ts"] < _TTL_SHORT:
        return _compound_cache["data"]

    result: list[dict] = []
    _COMPOUND_PROJECTS = {"compound-v3", "compound-finance", "compound"}
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            if proj not in _COMPOUND_PROJECTS or chain != "ethereum":
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":     p.get("symbol", ""),
                "project":    p.get("project", ""),
                "supply_apy": round(float(p.get("apyBase") or p.get("apy") or 0), 4),
                "total_apy":  round(float(p.get("apy") or 0), 4),
                "tvl_usd":    round(tvl, 2),
                "pool_id":    p.get("pool", ""),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
    except Exception as e:
        logger.warning("[Compound] fetch failed: %s", e)

    _compound_cache["ts"]   = time.time()
    _compound_cache["data"] = result
    logger.info("[Compound v3] %d markets fetched", len(result))
    return result


# ── 5. dYdX v4 ────────────────────────────────────────────────────────────────

def fetch_dydx_v4_funding() -> list[dict]:
    """
    dYdX v4 perpetual funding rates and open interest.

    Source: https://indexer.dydx.trade/v4/perpetuals?limit=30
    Response: perpetuals[] with ticker, nextFundingRate (decimal 8-hour rate,
              e.g. "0.0001" = 0.01% per 8 h), openInterest (in base asset
              units), atomicResolution.
    Converts funding rate to annualized % (8h_rate × 3 periods/day × 365 days × 100).
    Returns top 10 by open interest USD value.
    """
    now = time.time()
    if _dydx_cache["data"] is not None and now - _dydx_cache["ts"] < _TTL_SHORT:
        return _dydx_cache["data"]

    result: list[dict] = []
    try:
        data = _get("https://indexer.dydx.trade/v4/perpetuals?limit=30")
        if data and isinstance(data.get("perpetuals"), list):
            for p in data["perpetuals"]:
                # Response structure:
                # {"perpetual": {"params": {"ticker": str, "atomicResolution": int,
                #                           "openInterest": str, ...},
                #                "openInterest": str},
                #  "nextFundingRate": str}
                perp_obj   = p.get("perpetual") or {}
                params_obj = perp_obj.get("params") or {}
                ticker     = params_obj.get("ticker") or perp_obj.get("ticker") or p.get("ticker", "")
                # nextFundingRate is at the top-level item: decimal 8-hour rate
                # e.g. "0.0001" means 0.01% per 8-hour period.
                funding_8h = float(p.get("nextFundingRate") or 0)
                # Annualised: 8h_rate × 3 periods/day × 365 days × 100 → percent
                funding_ann_pct = round(funding_8h * 3 * 365 * 100, 4)
                # openInterest in base asset; atomicResolution adjusts decimal places
                # openInterest lives inside perpetual.params or perpetual (string)
                oi_raw     = float(
                    params_obj.get("openInterest") or perp_obj.get("openInterest") or
                    p.get("openInterest") or 0
                )
                atomic_res = int(
                    params_obj.get("atomicResolution") or p.get("atomicResolution") or 0
                )
                oi_adjusted = oi_raw * (10 ** atomic_res)  # normalize to whole units
                status = params_obj.get("status") or p.get("status", "")
                result.append({
                    "ticker":              ticker,
                    "funding_rate_8h_pct": round(funding_8h * 100, 6),   # 8-hour rate as %
                    "funding_rate_annual": funding_ann_pct,
                    "open_interest":       round(oi_adjusted, 2),
                    "status":              status,
                })
            # Sort by open interest descending, return top 10
            result.sort(key=lambda x: x["open_interest"], reverse=True)
            result = result[:10]
    except Exception as e:
        logger.warning("[dYdX] funding rates fetch failed: %s", e)

    _dydx_cache["ts"]   = time.time()
    _dydx_cache["data"] = result
    logger.info("[dYdX v4] %d perpetuals fetched", len(result))
    return result


# ── 6. GMX v2 ────────────────────────────────────────────────────────────────

def fetch_gmx_v2_pools() -> list[dict]:
    """
    GMX v2 pool data (GM pools) via DeFiLlama yields pools.

    Filters to project in ("gmx", "gmx-v2") across all chains.
    Returns APY and TVL per pool sorted by TVL descending.
    """
    now = time.time()
    if _gmx_cache["data"] is not None and now - _gmx_cache["ts"] < _TTL_SHORT:
        return _gmx_cache["data"]

    result: list[dict] = []
    _GMX_PROJECTS = {"gmx", "gmx-v2"}
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            if proj not in _GMX_PROJECTS:
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":   p.get("symbol", ""),
                "project":  p.get("project", ""),
                "chain":    p.get("chain", ""),
                "apy":      round(float(p.get("apy") or 0), 4),
                "apy_base": round(float(p.get("apyBase") or 0), 4),
                "tvl_usd":  round(tvl, 2),
                "pool_id":  p.get("pool", ""),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
    except Exception as e:
        logger.warning("[GMX] fetch failed: %s", e)

    _gmx_cache["ts"]   = time.time()
    _gmx_cache["data"] = result
    logger.info("[GMX v2] %d pools fetched", len(result))
    return result


# ── 7. Uniswap v3 ────────────────────────────────────────────────────────────

def fetch_uniswap_v3_pools() -> list[dict]:
    """
    Top Uniswap v3 pools by TVL from DeFiLlama yields pools.

    Filters to project == "uniswap-v3" across all chains.
    Returns top 20 by TVL with symbol, APY, chain, fee tier.
    """
    now = time.time()
    if _uniswap_cache["data"] is not None and now - _uniswap_cache["ts"] < _TTL_SHORT:
        return _uniswap_cache["data"]

    result: list[dict] = []
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            if proj != "uniswap-v3":
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":      p.get("symbol", ""),
                "chain":       p.get("chain", ""),
                "apy":         round(float(p.get("apy") or 0), 4),
                "apy_base":    round(float(p.get("apyBase") or 0), 4),
                "volume_1d":   round(float(p.get("volumeUsd1d") or 0), 2),
                "tvl_usd":     round(tvl, 2),
                "pool_id":     p.get("pool", ""),
                "il_risk":     p.get("ilRisk", "no"),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
        result = result[:20]
    except Exception as e:
        logger.warning("[Uniswap] fetch failed: %s", e)

    _uniswap_cache["ts"]   = time.time()
    _uniswap_cache["data"] = result
    logger.info("[Uniswap v3] %d pools fetched", len(result))
    return result


# ── 8. Pendle Finance ────────────────────────────────────────────────────────

def fetch_pendle_markets() -> list[dict]:
    """
    Pendle Finance yield markets on Ethereum mainnet.

    Source: https://api-v2.pendle.finance/core/v1/chains/1/markets
            ?limit=20&order_by=liquidity%3Adesc
    Response: results[] with name, pt{address}, impliedApy, fixedApy,
              liquidity{usd}, underlyingApy, tradingVolume{usd}.
    Returns top 20 markets sorted by liquidity descending.
    """
    now = time.time()
    if _pendle_cache["data"] is not None and now - _pendle_cache["ts"] < _TTL_SHORT:
        return _pendle_cache["data"]

    result: list[dict] = []
    try:
        url  = "https://api-v2.pendle.finance/core/v1/chains/1/markets"
        params = "?limit=20&order_by=liquidity%3Adesc"
        data = _get(url + params)
        if data and isinstance(data.get("results"), list):
            for m in data["results"]:
                liq     = m.get("liquidity") or {}
                vol     = m.get("tradingVolume") or {}
                liq_usd = float(liq.get("usd") or 0)
                result.append({
                    "name":            m.get("name", ""),
                    "address":         m.get("address", ""),
                    "implied_apy":     round(float(m.get("impliedApy")    or 0) * 100, 4),
                    "fixed_apy":       round(float(m.get("fixedApy")      or 0) * 100, 4),
                    "underlying_apy":  round(float(m.get("underlyingApy") or 0) * 100, 4),
                    "liquidity_usd":   round(liq_usd, 2),
                    "volume_24h_usd":  round(float(vol.get("usd") or 0), 2),
                    "expiry":          m.get("expiry", ""),
                })
            result.sort(key=lambda x: x["liquidity_usd"], reverse=True)
    except Exception as e:
        logger.warning("[Pendle] fetch failed: %s", e)

    _pendle_cache["ts"]   = time.time()
    _pendle_cache["data"] = result
    logger.info("[Pendle] %d markets fetched", len(result))
    return result


# ── Aggregator ────────────────────────────────────────────────────────────────

def fetch_all_protocol_benchmarks() -> dict:
    """
    Fetch all 14 protocol data sources in parallel.

    Uses ThreadPoolExecutor(max_workers=4) to run fetches concurrently.
    Individual fetch failures return empty list/dict — does not propagate errors.

    Returns combined benchmark dict:
        lido_steth_apy    : dict  — {"apr": float, "apy": float}
        curve_top_pools   : list  — top 20 Curve pools by TVL
        aave_markets      : list  — Aave v3 Ethereum markets
        compound_markets  : list  — Compound v3 Ethereum markets
        dydx_funding      : list  — dYdX v4 top 10 perps by OI
        gmx_pools         : list  — GMX v2 pools across chains
        uniswap_pools     : list  — top 20 Uniswap v3 pools by TVL
        pendle_markets    : list  — top 20 Pendle markets by liquidity
        ethena_yield      : dict  — Ethena sUSDe APY (#76)
        aerodrome_pools   : list  — top 10 Aerodrome pools on Base (#77)
        morpho_vaults     : list  — top 10 Morpho Blue vaults (#77)
        eigenlayer_lrt    : dict  — EigenLayer + LRT restaking yields (#71)
        kamino_yields     : dict  — Kamino Finance Solana vaults (#78)
        meteora_yields    : dict  — Meteora DLMM Solana pools (#78)
        _timestamp        : float — unix timestamp of this fetch
    """
    _TASKS = {
        "lido_steth_apy":   fetch_lido_steth_apy,
        "curve_top_pools":  fetch_curve_pools,
        "aave_markets":     fetch_aave_v3_markets,
        "compound_markets": fetch_compound_v3_markets,
        "dydx_funding":     fetch_dydx_v4_funding,
        "gmx_pools":        fetch_gmx_v2_pools,
        "uniswap_pools":    fetch_uniswap_v3_pools,
        "pendle_markets":   fetch_pendle_markets,
        "ethena_yield":     fetch_ethena_yield,         # #76
        "aerodrome_pools":  fetch_aerodrome_pools,      # #77
        "morpho_vaults":    fetch_morpho_vaults,        # #77
        "eigenlayer_lrt":   fetch_eigenlayer_lrt_yields, # #71
        "kamino_yields":    fetch_kamino_yields,         # #78
        "meteora_yields":   fetch_meteora_yields,        # #78
    }

    _DICT_KEYS = {"lido_steth_apy", "ethena_yield", "eigenlayer_lrt", "kamino_yields", "meteora_yields"}
    benchmarks: dict = {
        k: ({} if k in _DICT_KEYS else [])
        for k in _TASKS
    }

    with ThreadPoolExecutor(max_workers=4) as ex:
        future_map = {ex.submit(fn): key for key, fn in _TASKS.items()}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                benchmarks[key] = fut.result(timeout=30)
            except Exception as e:
                logger.warning("[DeFiProtocols] %s fetch error: %s", key, e)

    benchmarks["_timestamp"] = time.time()

    # Derive convenience scalar: Lido stETH APY as the DeFi benchmark rate
    lido = benchmarks.get("lido_steth_apy") or {}
    benchmarks["lido_steth_apy_pct"] = float(lido.get("apy") or 0)

    logger.info(
        "[DeFiProtocols] benchmarks fetched — Lido=%.2f%% Curve=%d Aave=%d "
        "Compound=%d dYdX=%d GMX=%d Uniswap=%d Pendle=%d "
        "Ethena_sUSDe=%.2f%% Aerodrome=%d Morpho=%d "
        "EigenLayer/LRT=ok Kamino=%d Meteora=%d",
        benchmarks["lido_steth_apy_pct"],
        len(benchmarks["curve_top_pools"]),
        len(benchmarks["aave_markets"]),
        len(benchmarks["compound_markets"]),
        len(benchmarks["dydx_funding"]),
        len(benchmarks["gmx_pools"]),
        len(benchmarks["uniswap_pools"]),
        len(benchmarks["pendle_markets"]),
        float((benchmarks.get("ethena_yield") or {}).get("susde_apy") or 0),
        len(benchmarks.get("aerodrome_pools") or []),
        len(benchmarks.get("morpho_vaults") or []),
        len((benchmarks.get("kamino_yields") or {}).get("pools") or []),
        len((benchmarks.get("meteora_yields") or {}).get("pools") or []),
    )
    return benchmarks


# ── 9. Ethena USDe / sUSDe (#76) ─────────────────────────────────────────────

_ETHENA_API_URL = "https://ethena.fi/api/yields/protocol-and-staking-yield"


def fetch_ethena_yield() -> dict:
    """
    Fetch Ethena sUSDe APY.

    Primary source: https://ethena.fi/api/yields/protocol-and-staking-yield
    Fallback:       DeFiLlama yields pools filtered to project=="ethena".

    Returns:
        dict with keys:
          susde_apy  : float — current sUSDe APY %
          protocol   : "ethena"
          mechanism  : "delta_neutral"
          source     : "ethena_api" | "defillama" | "unavailable"
    """
    now = time.time()
    if _ethena_cache["data"] is not None and now - _ethena_cache["ts"] < _TTL_LONG:
        return _ethena_cache["data"]

    result: dict = {
        "susde_apy": 0.0,
        "protocol":  "ethena",
        "mechanism": "delta_neutral",
        "source":    "unavailable",
    }

    # Primary: Ethena public API
    try:
        data = _get(_ETHENA_API_URL, timeout=10)
        # Ethena API may return a dict or a list of daily objects.
        # If a list, take the most recent entry (last element).
        if isinstance(data, list) and data:
            data = data[-1]
        if isinstance(data, dict):
            # The response typically has "stakingYield" or "susdeApy" fields.
            # Field names observed: stakingYield.value or susdeApy (percent).
            # stakingYield.value may be a decimal (0.275) — convert to percent if < 1.0.
            staking = data.get("stakingYield") or {}
            apy_val = (
                staking.get("value")
                or data.get("susdeApy")
                or data.get("sUSDe_apy")
                or data.get("apy")
            )
            if apy_val is not None:
                apy_float = float(apy_val)
                # Normalise: if value looks like a decimal (< 1.0 and > 0), convert to percent
                if 0 < apy_float < 1.0:
                    apy_float *= 100.0
                result["susde_apy"] = round(apy_float, 4)
                result["source"]    = "ethena_api"
    except Exception as e:
        logger.debug("[Ethena] primary API failed: %s", e)

    # Fallback: DeFiLlama
    if result["source"] == "unavailable":
        try:
            pools = _get_llama_pools()
            best_apy = 0.0
            for p in pools:
                proj = (p.get("project") or "").lower()
                sym  = (p.get("symbol")  or "").lower()
                if proj == "ethena" and "susde" in sym:
                    apy = float(p.get("apy") or 0)
                    if apy > best_apy:
                        best_apy = apy
            if best_apy > 0:
                result["susde_apy"] = round(best_apy, 4)
                result["source"]    = "defillama"
        except Exception as e:
            logger.debug("[Ethena] DeFiLlama fallback failed: %s", e)

    _ethena_cache["ts"]   = time.time()
    _ethena_cache["data"] = result
    logger.info("[Ethena] sUSDe APY=%.2f%% (source=%s)", result["susde_apy"], result["source"])
    return result


# ── 10. Aerodrome Finance (#77) ───────────────────────────────────────────────

def fetch_aerodrome_pools() -> list[dict]:
    """
    Top Aerodrome Finance pools on Base by TVL from DeFiLlama yields pools.

    Filters to project in ("aerodrome-v2", "aerodrome", "aerodrome-finance")
    and chain == "Base". Returns top 10 by TVL.

    Returns list with keys: symbol, project, chain, apy, tvl_usd, pool_id.
    """
    now = time.time()
    if _aerodrome_cache["data"] is not None and now - _aerodrome_cache["ts"] < _TTL_LONG:
        return _aerodrome_cache["data"]

    result: list[dict] = []
    _AERO_PROJECTS = {"aerodrome-v2", "aerodrome", "aerodrome-finance"}
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            if proj not in _AERO_PROJECTS or chain != "base":
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":  p.get("symbol", ""),
                "project": p.get("project", ""),
                "chain":   "Base",
                "apy":     round(float(p.get("apy") or 0), 4),
                "apy_7d":  round(float(p.get("apyMean30d") or p.get("apy") or 0), 4),
                "tvl_usd": round(tvl, 2),
                "pool_id": p.get("pool", ""),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
        result = result[:10]
    except Exception as e:
        logger.warning("[Aerodrome] fetch failed: %s", e)

    _aerodrome_cache["ts"]   = time.time()
    _aerodrome_cache["data"] = result
    logger.info("[Aerodrome] %d pools fetched", len(result))
    return result


# ── 11. Morpho Blue (#77) ─────────────────────────────────────────────────────

def fetch_morpho_vaults() -> list[dict]:
    """
    Top Morpho Blue vaults across all chains from DeFiLlama yields pools.

    Filters to project in ("morpho", "morpho-blue"). Returns top 10 by TVL.

    Returns list with keys: symbol, project, chain, apy, tvl_usd, pool_id.
    """
    now = time.time()
    if _morpho_cache["data"] is not None and now - _morpho_cache["ts"] < _TTL_LONG:
        return _morpho_cache["data"]

    result: list[dict] = []
    _MORPHO_PROJECTS = {"morpho", "morpho-blue"}
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            if proj not in _MORPHO_PROJECTS:
                continue
            tvl = float(p.get("tvlUsd") or 0)
            result.append({
                "symbol":  p.get("symbol", ""),
                "project": p.get("project", ""),
                "chain":   p.get("chain", ""),
                "apy":     round(float(p.get("apy") or 0), 4),
                "apy_7d":  round(float(p.get("apyMean30d") or p.get("apy") or 0), 4),
                "tvl_usd": round(tvl, 2),
                "pool_id": p.get("pool", ""),
            })
        result.sort(key=lambda x: x["tvl_usd"], reverse=True)
        result = result[:10]
    except Exception as e:
        logger.warning("[Morpho] fetch failed: %s", e)

    _morpho_cache["ts"]   = time.time()
    _morpho_cache["data"] = result
    logger.info("[Morpho] %d vaults fetched", len(result))
    return result


# ── 12. EigenLayer + LRT Ecosystem (#71) ─────────────────────────────────────

_LRT_PROJECTS  = {"eigenlayer", "ether.fi", "renzo", "kelp", "swell", "puffer"}
_LRT_SYMBOLS   = {"eETH", "ezETH", "rsETH", "swETH", "pufETH", "weETH"}
_ETHERFI_URL   = "https://www.ether.fi/api/portfolio/v3/portfolio-page"


def fetch_eigenlayer_lrt_yields() -> dict:
    """Fetch restaking yields from EigenLayer and major LRT protocols.

    Primary source: DeFiLlama yields pools filtered to known LRT project names
    and symbols.  Also attempts direct ether.fi API for weETH APY.

    Returns a dict with keys:
      eigenlayer_native, etherfi_weETH, renzo_ezETH, kelp_rsETH, timestamp.
    Each sub-dict: {"apy": float, "tvl_usd": float, "source": str}.
    """
    now = time.time()
    if _eigenlayer_cache["data"] is not None and now - _eigenlayer_cache["ts"] < _TTL_LONG:
        return _eigenlayer_cache["data"]

    result: dict = {
        "eigenlayer_native": {"apy": 0.0, "tvl_usd": 0.0, "source": "unavailable"},
        "etherfi_weETH":     {"apy": 0.0, "tvl_usd": 0.0, "source": "unavailable"},
        "renzo_ezETH":       {"apy": 0.0, "tvl_usd": 0.0, "source": "unavailable"},
        "kelp_rsETH":        {"apy": 0.0, "tvl_usd": 0.0, "source": "unavailable"},
        "timestamp":         "",
    }

    # ── Step 1: DeFiLlama pools scan ───────────────────────────────────────────
    _mapping = {
        "eigenlayer":  "eigenlayer_native",
        "ether.fi":    "etherfi_weETH",
        "renzo":       "renzo_ezETH",
        "kelp":        "kelp_rsETH",
        "swell":       "eigenlayer_native",   # swell re-staking maps to eigenlayer bucket
        "puffer":      "eigenlayer_native",
    }
    _sym_mapping = {
        "eETH":   "etherfi_weETH",
        "weETH":  "etherfi_weETH",
        "ezETH":  "renzo_ezETH",
        "rsETH":  "kelp_rsETH",
        "swETH":  "eigenlayer_native",
        "pufETH": "eigenlayer_native",
    }
    # Best per-bucket: track highest TVL seen
    _best_tvl: dict[str, float] = {k: 0.0 for k in result if k != "timestamp"}

    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            sym  = (p.get("symbol")  or "")
            tvl  = float(p.get("tvlUsd") or 0)
            apy  = float(p.get("apy") or 0)

            # Determine which result bucket this pool maps to
            bucket = None
            for _proj_key, _bucket in _mapping.items():
                if _proj_key in proj:
                    bucket = _bucket
                    break
            if bucket is None:
                for _sym_key, _bucket in _sym_mapping.items():
                    if _sym_key in sym:
                        bucket = _bucket
                        break

            if bucket and tvl > _best_tvl[bucket]:
                _best_tvl[bucket] = tvl
                result[bucket] = {"apy": round(apy, 4), "tvl_usd": round(tvl, 2), "source": "defillama"}
    except Exception as e:
        logger.debug("[EigenLayer/LRT] DeFiLlama scan failed: %s", e)

    # ── Step 2: Direct ether.fi API (best-effort) ──────────────────────────────
    try:
        data = _get(_ETHERFI_URL, timeout=8)
        if isinstance(data, dict):
            apy_val = (
                data.get("totalApy")
                or data.get("apy")
                or (data.get("stakingApy") if isinstance(data.get("stakingApy"), (int, float)) else None)
            )
            if apy_val is not None:
                apy_float = float(apy_val)
                if 0 < apy_float < 1.0:
                    apy_float *= 100.0
                # Only override if the direct API gives a more precise value
                prev = result["etherfi_weETH"]
                result["etherfi_weETH"] = {
                    "apy":     round(apy_float, 4),
                    "tvl_usd": prev["tvl_usd"],
                    "source":  "etherfi_api",
                }
    except Exception as e:
        logger.debug("[EigenLayer/LRT] ether.fi direct API failed (using DeFiLlama): %s", e)

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _eigenlayer_cache["ts"]   = time.time()
    _eigenlayer_cache["data"] = result
    logger.info(
        "[EigenLayer/LRT] eigenlayer=%.2f%% etherfi=%.2f%% renzo=%.2f%% kelp=%.2f%%",
        result["eigenlayer_native"]["apy"],
        result["etherfi_weETH"]["apy"],
        result["renzo_ezETH"]["apy"],
        result["kelp_rsETH"]["apy"],
    )
    return result


# ── 13. Kamino Finance (#78) ──────────────────────────────────────────────────

def fetch_kamino_yields() -> dict:
    """Fetch Kamino Finance top vault yields on Solana via DeFiLlama.

    Filters DeFiLlama pools to project in ("kamino", "kamino-lending",
    "kamino-liquidity") on chain Solana with TVL ≥ $500k.
    Returns top 5 by APY.
    """
    now = time.time()
    if _kamino_cache["data"] is not None and now - _kamino_cache["ts"] < _TTL_LONG:
        return _kamino_cache["data"]

    result: dict = {"pools": [], "total_tvl": 0.0, "timestamp": ""}
    _KAMINO_PROJS = {"kamino", "kamino-lending", "kamino-liquidity"}
    try:
        pools = _get_llama_pools()
        hits: list[dict] = []
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            tvl   = float(p.get("tvlUsd") or 0)
            if proj not in _KAMINO_PROJS or chain != "solana" or tvl < 500_000:
                continue
            hits.append({
                "symbol":  p.get("symbol", ""),
                "apy":     round(float(p.get("apy") or 0), 4),
                "tvl_usd": round(tvl, 2),
                "chain":   "Solana",
                "project": p.get("project", ""),
            })
        hits.sort(key=lambda x: x["apy"], reverse=True)
        result["pools"]     = hits[:5]
        result["total_tvl"] = round(sum(h["tvl_usd"] for h in hits), 2)
    except Exception as e:
        logger.warning("[Kamino] fetch failed: %s", e)

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _kamino_cache["ts"]   = time.time()
    _kamino_cache["data"] = result
    logger.info("[Kamino] %d pools fetched, total_tvl=%.0f", len(result["pools"]), result["total_tvl"])
    return result


# ── 14. Meteora DLMM (#78) ────────────────────────────────────────────────────

def fetch_meteora_yields() -> dict:
    """Fetch Meteora DLMM pool yields on Solana via DeFiLlama.

    Filters DeFiLlama pools to project in ("meteora", "meteora-dlmm") on
    Solana with TVL ≥ $100k.  Excludes outliers >10 000% APY.
    Returns top 5 by APY.
    """
    now = time.time()
    if _meteora_cache["data"] is not None and now - _meteora_cache["ts"] < _TTL_LONG:
        return _meteora_cache["data"]

    result: dict = {"pools": [], "total_tvl": 0.0, "timestamp": ""}
    _METEORA_PROJS = {"meteora", "meteora-dlmm"}
    try:
        pools = _get_llama_pools()
        hits: list[dict] = []
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            tvl   = float(p.get("tvlUsd") or 0)
            apy   = float(p.get("apy") or 0)
            if proj not in _METEORA_PROJS or chain != "solana" or tvl < 100_000:
                continue
            if apy > 10_000:    # exclude extreme outliers
                continue
            hits.append({
                "symbol":  p.get("symbol", ""),
                "apy":     round(apy, 4),
                "tvl_usd": round(tvl, 2),
                "chain":   "Solana",
                "project": p.get("project", ""),
            })
        hits.sort(key=lambda x: x["apy"], reverse=True)
        result["pools"]     = hits[:5]
        result["total_tvl"] = round(sum(h["tvl_usd"] for h in hits), 2)
    except Exception as e:
        logger.warning("[Meteora] fetch failed: %s", e)

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _meteora_cache["ts"]   = time.time()
    _meteora_cache["data"] = result
    logger.info("[Meteora] %d pools fetched, total_tvl=%.0f", len(result["pools"]), result["total_tvl"])
    return result


# ── 12. 24-Hour TVL Change Alerts (#79) ───────────────────────────────────────

def fetch_tvl_change_alerts(threshold_pct: float = 5.0) -> list[dict]:
    """
    Check every protocol returned by fetch_all_protocol_benchmarks() for a
    significant TVL drop in the last ~24 hours.

    Uses a module-level dict (_tvl_snapshots) to persist the previous TVL reading
    with a timestamp.  On the first call (no snapshot) the current TVL is stored
    and no alert is emitted.  Subsequent calls compare against the stored value.

    A new snapshot is only written when the stored snapshot is >20 minutes old
    so that rapid back-to-back calls don't overwrite the baseline.

    Args:
        threshold_pct: minimum percentage drop to trigger a WARNING (default 5%).
                       Drops >15% trigger CRITICAL.

    Returns:
        List of alert dicts, each with:
          protocol    : str
          tvl_now     : float
          tvl_24h     : float  — stored baseline TVL
          change_pct  : float  — signed, negative = drop
          severity    : "WARNING" | "CRITICAL"
    """
    _SNAPSHOT_MAX_AGE    = 86_400  # 24 h — baseline we compare against
    _SNAPSHOT_MIN_WRITE  = 1_200   # 20 min — minimum gap before refreshing snapshot

    # Collect current TVL for each protocol via DeFiLlama yields pool aggregate
    protocol_tvls: dict[str, float] = {}
    try:
        pools = _get_llama_pools()
        agg: dict[str, float] = {}
        for p in pools:
            proj = (p.get("project") or "").lower()
            tvl  = float(p.get("tvlUsd") or 0)
            agg[proj] = agg.get(proj, 0.0) + tvl
        protocol_tvls = agg
    except Exception as e:
        logger.warning("[TVLAlerts] pool fetch failed: %s", e)
        return []

    now    = time.time()
    alerts = []

    with _tvl_snapshots_lock:
        for protocol, tvl_now in protocol_tvls.items():
            if tvl_now <= 0:
                continue
            snap = _tvl_snapshots.get(protocol)

            if snap is None:
                # First observation — store baseline, no alert yet
                _tvl_snapshots[protocol] = {"tvl": tvl_now, "ts": now}
                continue

            snap_age = now - snap["ts"]
            tvl_24h  = snap["tvl"]

            # Compute change vs baseline
            change_pct = (tvl_now - tvl_24h) / tvl_24h * 100 if tvl_24h > 0 else 0.0

            # Refresh the snapshot if it's older than 24h (so we always compare ~1 day back)
            # but only if the minimum write interval has passed (avoid overwriting too often)
            if snap_age > _SNAPSHOT_MAX_AGE and snap_age > _SNAPSHOT_MIN_WRITE:
                _tvl_snapshots[protocol] = {"tvl": tvl_now, "ts": now}

            # Emit alert on significant drops
            if change_pct <= -threshold_pct:
                severity = "CRITICAL" if change_pct <= -15.0 else "WARNING"
                alerts.append({
                    "protocol":   protocol,
                    "tvl_now":    round(tvl_now, 2),
                    "tvl_24h":    round(tvl_24h, 2),
                    "change_pct": round(change_pct, 2),
                    "severity":   severity,
                })
                logger.warning(
                    "[TVLAlerts] %s TVL drop %.1f%% ($%.0fM → $%.0fM) — %s",
                    protocol, change_pct, tvl_24h / 1e6, tvl_now / 1e6, severity,
                )

    alerts.sort(key=lambda x: x["change_pct"])   # most severe first
    return alerts

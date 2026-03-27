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
_curve_cache    = {"ts": 0, "data": None}
_aave_cache     = {"ts": 0, "data": None}
_lido_cache     = {"ts": 0, "data": None}
_compound_cache = {"ts": 0, "data": None}
_dydx_cache     = {"ts": 0, "data": None}
_gmx_cache      = {"ts": 0, "data": None}
_uniswap_cache  = {"ts": 0, "data": None}
_pendle_cache   = {"ts": 0, "data": None}

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
    Response: perpetuals[] with ticker, nextFundingRate (decimal per hour),
              openInterest (in base asset units), atomicResolution.
    Converts funding rate to annualized % (rate/hr × 8760 × 100).
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
                # nextFundingRate is at the top-level item (decimal per hour)
                funding_hr = float(p.get("nextFundingRate") or 0)
                # Annualised: hourly_rate × 8760 hours × 100 → percent
                funding_ann_pct = round(funding_hr * 8760 * 100, 4)
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
                    "funding_rate_hourly": round(funding_hr * 100, 6),   # as %
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
    Fetch all 8 protocol data sources in parallel.

    Uses ThreadPoolExecutor(max_workers=4) to run fetches concurrently.
    Individual fetch failures return empty list/dict — does not propagate errors.

    Returns combined benchmark dict:
        lido_steth_apy   : dict  — {"apr": float, "apy": float}
        curve_top_pools  : list  — top 20 Curve pools by TVL
        aave_markets     : list  — Aave v3 Ethereum markets
        compound_markets : list  — Compound v3 Ethereum markets
        dydx_funding     : list  — dYdX v4 top 10 perps by OI
        gmx_pools        : list  — GMX v2 pools across chains
        uniswap_pools    : list  — top 20 Uniswap v3 pools by TVL
        pendle_markets   : list  — top 20 Pendle markets by liquidity
        _timestamp       : float — unix timestamp of this fetch
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
    }

    benchmarks: dict = {k: ([] if k != "lido_steth_apy" else {}) for k in _TASKS}

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
        "Compound=%d dYdX=%d GMX=%d Uniswap=%d Pendle=%d",
        benchmarks["lido_steth_apy_pct"],
        len(benchmarks["curve_top_pools"]),
        len(benchmarks["aave_markets"]),
        len(benchmarks["compound_markets"]),
        len(benchmarks["dydx_funding"]),
        len(benchmarks["gmx_pools"]),
        len(benchmarks["uniswap_pools"]),
        len(benchmarks["pendle_markets"]),
    )
    return benchmarks

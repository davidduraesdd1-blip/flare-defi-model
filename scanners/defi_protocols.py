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
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

# Use shared retry-aware session from utils.http (#12 — defi_protocols.py migrated in Batch 6 post-audit)
from utils.http import _SESSION, default_limiter as _DEFAULT_LIMITER

_TTL_SHORT = 300   # 5 minutes
_TTL_LONG  = 900   # 15 minutes

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
_llama_pools_event = threading.Event()   # set when data is ready; prevents TOCTOU stampede
_llama_pools_event.set()                  # initially set (no fetch in progress)
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
    Thread-safe: uses an Event sentinel so only the first thread performs the
    HTTP fetch while others block; eliminates the TOCTOU stampede where up to
    7 concurrent callers would each download the ~20MB response on a cold cache.

    is_fetcher is determined INSIDE the lock so the decision is atomic.
    """
    now = time.time()
    with _llama_pools_lock:
        if _llama_pools_cache["data"] is not None and now - _llama_pools_cache["ts"] < _TTL_LONG:
            return _llama_pools_cache["data"]
        if not _llama_pools_event.is_set():
            # Another thread already claimed the fetch — we will wait
            is_fetcher = False
        else:
            # We claim the fetch by clearing the event before releasing the lock
            _llama_pools_event.clear()
            is_fetcher = True

    if not is_fetcher:
        # Block until the fetching thread finishes (max 30s), then return its result
        _llama_pools_event.wait(timeout=30)
        with _llama_pools_lock:
            return _llama_pools_cache["data"] or []

    # We are the designated fetching thread
    try:
        resp = _SESSION.get(_LLAMA_YIELDS_URL, timeout=45)   # 20MB response; 45s on cloud
        resp.raise_for_status()
        pools = resp.json().get("data", []) or []
        if pools:
            with _llama_pools_lock:
                _llama_pools_cache["ts"]   = time.time()
                _llama_pools_cache["data"] = pools
        return pools
    except Exception as e:
        logger.warning("[DeFiProtocols] DeFiLlama pools fetch failed: %s", e)
        with _llama_pools_lock:
            return _llama_pools_cache["data"] or []
    finally:
        _llama_pools_event.set()  # always unblock waiting threads


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


# ── Token Unlock Schedule (#84) ──────────────────────────────────────────────
# Known token unlock schedules (approximate dates and amounts)
_TOKEN_UNLOCK_SCHEDULE = {
    "ARB": [
        {"date": "2024-03-16", "amount_pct": 11.62, "type": "Investor/Team", "cliff": False},
        {"date": "2025-03-16", "amount_pct": 8.1,   "type": "Investor/Team", "cliff": False},
        {"date": "2026-09-16", "amount_pct": 6.5,   "type": "Investor/Team", "cliff": False},
    ],
    "OP": [
        {"date": "2025-05-31", "amount_pct": 5.0, "type": "Core Contributors", "cliff": False},
        {"date": "2026-05-31", "amount_pct": 5.0, "type": "Core Contributors", "cliff": False},
    ],
    "PENDLE": [
        {"date": "2025-06-30", "amount_pct": 3.5, "type": "Team", "cliff": False},
        {"date": "2026-06-30", "amount_pct": 3.5, "type": "Team", "cliff": False},
    ],
    "WIF": [],  # Community token, no scheduled unlocks
    "JUP": [
        {"date": "2025-01-31", "amount_pct": 25.0, "type": "Team/Investors", "cliff": True},
        {"date": "2026-01-31", "amount_pct": 25.0, "type": "Team/Investors", "cliff": False},
        {"date": "2027-01-31", "amount_pct": 25.0, "type": "Team/Investors", "cliff": False},
    ],
    "PYTH": [
        {"date": "2025-05-20", "amount_pct": 8.0, "type": "Early Contributors", "cliff": False},
        {"date": "2026-05-20", "amount_pct": 8.0, "type": "Early Contributors", "cliff": False},
    ],
    "EIGEN": [
        {"date": "2026-09-30", "amount_pct": 5.0, "type": "Investor", "cliff": False},
    ],
}

# Date-based cache key (refreshes daily)
_unlock_cache: dict = {}
_unlock_cache_lock = threading.Lock()


def fetch_token_unlock_alerts(within_days: int = 30) -> list:
    """
    Return token unlocks happening within the next N days.

    Parses _TOKEN_UNLOCK_SCHEDULE and filters to events where
    0 <= days_until <= within_days. Results are sorted by days_until ascending.

    Returns list of dicts:
        token, date, amount_pct, type, days_until, severity, is_cliff
    Severity:
        CRITICAL — amount_pct >= 10 or days_until <= 7
        WARNING  — amount_pct >= 3  or days_until <= 14
        INFO     — everything else in the window
    """
    today_str = str(date.today())
    cache_key = f"unlock_alerts:{today_str}:{within_days}"
    with _unlock_cache_lock:
        if cache_key in _unlock_cache:
            return _unlock_cache[cache_key]

    today = date.today()
    alerts = []
    for token, unlocks in _TOKEN_UNLOCK_SCHEDULE.items():
        for u in unlocks:
            try:
                unlock_date = date.fromisoformat(u["date"])
            except (ValueError, KeyError):
                continue
            days_until = (unlock_date - today).days
            if days_until < 0 or days_until > within_days:
                continue
            amount_pct = float(u.get("amount_pct", 0))
            is_cliff   = bool(u.get("cliff", False))
            if amount_pct >= 10 or days_until <= 7:
                severity = "CRITICAL"
            elif amount_pct >= 3 or days_until <= 14:
                severity = "WARNING"
            else:
                severity = "INFO"
            alerts.append({
                "token":      token,
                "date":       u["date"],
                "amount_pct": amount_pct,
                "type":       u.get("type", ""),
                "days_until": days_until,
                "severity":   severity,
                "is_cliff":   is_cliff,
            })

    alerts.sort(key=lambda x: x["days_until"])

    with _unlock_cache_lock:
        _unlock_cache[cache_key] = alerts
    return alerts


# ── Aggregator ────────────────────────────────────────────────────────────────

def fetch_all_protocol_benchmarks() -> dict:
    """
    Fetch all 14 protocol data sources in parallel.

    Uses ThreadPoolExecutor(max_workers=14) to run all 14 fetches concurrently.
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
    # token_unlock_alerts is computed synchronously (no I/O) — added after parallel block
    benchmarks: dict = {
        k: ({} if k in _DICT_KEYS else [])
        for k in _TASKS
    }

    with ThreadPoolExecutor(max_workers=min(14, len(_TASKS))) as ex:
        future_map = {ex.submit(fn): key for key, fn in _TASKS.items()}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                benchmarks[key] = fut.result(timeout=30)
            except Exception as e:
                logger.warning("[DeFiProtocols] %s fetch error: %s", key, e)

    benchmarks["_timestamp"] = time.time()

    # Token unlock alerts (#84) — synchronous (no network I/O)
    try:
        benchmarks["token_unlock_alerts"] = fetch_token_unlock_alerts(within_days=30)
    except Exception as e:
        logger.warning("[DeFiProtocols] token_unlock_alerts error: %s", e)
        benchmarks["token_unlock_alerts"] = []

    # RWA credit health (#58) — run after parallel block to avoid adding to pool
    try:
        benchmarks["rwa_credit_health"] = fetch_rwa_credit_health()
    except Exception as e:
        logger.warning("[DeFiProtocols] rwa_credit_health error: %s", e)
        benchmarks["rwa_credit_health"] = {"timestamp": ""}

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
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            # Broad match: catches aerodrome-v2, aerodrome, aerodrome-finance, aerodrome-cl etc.
            if "aerodrome" not in proj or chain != "base":
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

    # Only update cache on non-empty results; empty means DeFiLlama timed out —
    # don't lock out retries for 15 minutes.
    if result:
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
    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            # Broad match: catches morpho, morpho-blue, morpho-v2, morpho-blue-v3 etc.
            if "morpho" not in proj:
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

    # Only update cache on non-empty results; empty means DeFiLlama timed out —
    # don't lock out retries for 15 minutes.
    if result:
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
    # Symbol mapping uses lowercase keys so the comparison is case-insensitive
    _sym_mapping = {
        "eeth":   "etherfi_weETH",
        "weeth":  "etherfi_weETH",
        "ezeth":  "renzo_ezETH",
        "rseth":  "kelp_rsETH",
        "sweth":  "eigenlayer_native",
        "pufeth": "eigenlayer_native",
    }
    # Best per-bucket: track highest TVL seen
    _best_tvl: dict[str, float] = {k: 0.0 for k in result if k != "timestamp"}

    try:
        pools = _get_llama_pools()
        for p in pools:
            proj = (p.get("project") or "").lower()
            sym  = (p.get("symbol")  or "").lower()   # normalise to lowercase for matching
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
    _has_lrt_data = any(
        v.get("apy", 0) > 0 or v.get("tvl_usd", 0) > 0
        for k, v in result.items() if isinstance(v, dict)
    )
    if _has_lrt_data:   # don't lock out retries when DeFiLlama returned nothing
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
    try:
        pools = _get_llama_pools()
        hits: list[dict] = []
        for p in pools:
            proj  = (p.get("project") or "").lower()
            chain = (p.get("chain")   or "").lower()
            tvl   = float(p.get("tvlUsd") or 0)
            # Broad match: kamino, kamino-lending, kamino-liquidity, kamino-v2 etc.
            if "kamino" not in proj or chain != "solana" or tvl < 500_000:
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
    if result["pools"]:   # don't lock out retries on a failed/empty fetch
        _kamino_cache["ts"]   = time.time()
        _kamino_cache["data"] = result
    logger.info("[Kamino] %d pools fetched, total_tvl=%.0f", len(result["pools"]), result["total_tvl"])
    return result


# ── 14. Meteora DLMM (#78) ────────────────────────────────────────────────────

# Average Meteora DLMM fee rate used to estimate APY from 24h volume
_METEORA_FEE_RATE = 0.0025   # 0.25% — conservative midpoint across DLMM tiers


def _gecko_meteora_hits(dex_slug: str) -> list[dict]:
    """Fetch top Meteora pools via GeckoTerminal DEX endpoint.  Returns [] on any failure."""
    url = f"https://api.geckoterminal.com/api/v2/networks/solana/dexes/{dex_slug}/pools"
    try:
        resp = _SESSION.get(url, params={"page": 1, "sort": "h24_volume_usd_desc"},
                            headers={"Accept": "application/json"}, timeout=20)
        resp.raise_for_status()
        raw_pools = resp.json().get("data") or []
        hits = []
        for pool in raw_pools:
            attrs  = pool.get("attributes") or {}
            name   = attrs.get("name") or ""
            tvl    = float(attrs.get("reserve_in_usd") or 0)
            vol24h = float((attrs.get("volume_usd") or {}).get("h24") or 0)
            if tvl < 10_000:
                continue
            apy = round((vol24h * _METEORA_FEE_RATE / max(tvl, 1)) * 365 * 100, 4)
            hits.append({
                "symbol":  name.replace(" / ", "-"),
                "apy":     min(apy, 50_000),
                "tvl_usd": round(tvl, 2),
                "chain":   "Solana",
                "project": "meteora-dlmm",
            })
        # WARNING so this is visible in Streamlit Cloud logs
        logger.warning("[Meteora] dex=%s HTTP 200 — %d raw pools, %d above $10k TVL",
                       dex_slug, len(raw_pools), len(hits))
        return hits
    except Exception as e:
        logger.warning("[Meteora] GeckoTerminal dex=%s failed: %s", dex_slug, e)
        return []


def _gecko_meteora_network_scan() -> list[dict]:
    """Scan GeckoTerminal Solana top pools (pages 1-3) sorted by volume, filter for Meteora."""
    hits = []
    all_dex_ids: set[str] = set()
    for page in (1, 2, 3):
        try:
            resp = _SESSION.get(
                "https://api.geckoterminal.com/api/v2/networks/solana/pools",
                params={"page": page, "sort": "h24_volume_usd_desc", "include": "dex"},
                headers={"Accept": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            body = resp.json()
            dex_map = {
                item["id"]: (item.get("attributes") or {}).get("name", "")
                for item in (body.get("included") or [])
                if item.get("type") == "dex"
            }
            page_pools = body.get("data") or []
            for pool in page_pools:
                dex_id = ((pool.get("relationships") or {})
                          .get("dex", {}).get("data") or {}).get("id", "")
                all_dex_ids.add(dex_id)
                dex_name = dex_map.get(dex_id, dex_id).lower()
                if "meteora" not in dex_name and "meteora" not in dex_id.lower():
                    continue
                attrs  = pool.get("attributes") or {}
                name   = attrs.get("name") or ""
                tvl    = float(attrs.get("reserve_in_usd") or 0)
                vol24h = float((attrs.get("volume_usd") or {}).get("h24") or 0)
                if tvl < 10_000:
                    continue
                apy = round((vol24h * _METEORA_FEE_RATE / max(tvl, 1)) * 365 * 100, 4)
                hits.append({
                    "symbol":  name.replace(" / ", "-"),
                    "apy":     min(apy, 50_000),
                    "tvl_usd": round(tvl, 2),
                    "chain":   "Solana",
                    "project": "meteora-dlmm",
                })
            logger.warning("[Meteora] network scan page=%d — %d pools checked, %d dexes seen: %s",
                           page, len(page_pools), len(all_dex_ids),
                           sorted(all_dex_ids)[:15])  # show first 15 dex IDs
        except Exception as e:
            logger.warning("[Meteora] network scan page=%d failed: %s", page, e)
            break
    logger.warning("[Meteora] network scan done — %d meteora pools found", len(hits))
    return hits


def fetch_meteora_yields() -> dict:
    """Fetch Meteora DLMM pool yields.

    Strategy (first success wins):
      1. GeckoTerminal DEX endpoint — tries meteora-dlmm, meteora, meteora-amm slugs
      2. GeckoTerminal network scan — pages 1-3 sorted by volume, filter by dex id
      3. DeFiLlama yields pool search — substring match on "meteora"
    Returns top 5 pools by TVL.
    """
    now = time.time()
    if _meteora_cache["data"] is not None and now - _meteora_cache["ts"] < _TTL_LONG:
        return _meteora_cache["data"]

    result: dict = {"pools": [], "total_tvl": 0.0, "timestamp": ""}
    hits: list[dict] = []

    # ── Method 1: GeckoTerminal DEX endpoint (try every known slug) ──────────
    for slug in ("meteora-dlmm", "meteora", "meteora-amm"):
        hits = _gecko_meteora_hits(slug)
        if hits:
            logger.warning("[Meteora] success via GeckoTerminal dex=%s (%d pools)", slug, len(hits))
            break

    # ── Method 2: GeckoTerminal network scan ─────────────────────────────────
    if not hits:
        logger.warning("[Meteora] all DEX slugs empty — starting network scan")
        hits = _gecko_meteora_network_scan()

    # ── Method 3: DeFiLlama fallback ─────────────────────────────────────────
    if not hits:
        logger.warning("[Meteora] network scan empty — trying DeFiLlama")
        try:
            pools = _get_llama_pools()
            solana_meteora = [p for p in pools
                              if "meteora" in (p.get("project") or "").lower()
                              and (p.get("chain") or "").lower() == "solana"]
            logger.warning("[Meteora] DeFiLlama solana+meteora pools found: %d", len(solana_meteora))
            for p in solana_meteora:
                tvl = float(p.get("tvlUsd") or 0)
                if tvl < 10_000:
                    continue
                hits.append({
                    "symbol":  p.get("symbol", ""),
                    "apy":     round(float(p.get("apy") or 0), 4),
                    "tvl_usd": round(tvl, 2),
                    "chain":   "Solana",
                    "project": p.get("project", ""),
                })
        except Exception as e:
            logger.warning("[Meteora] DeFiLlama fallback failed: %s", e)

    hits.sort(key=lambda x: x["tvl_usd"], reverse=True)
    result["pools"]     = hits[:5]
    result["total_tvl"] = round(sum(h["tvl_usd"] for h in hits), 2)
    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    if result["pools"]:
        _meteora_cache["ts"]   = time.time()
        _meteora_cache["data"] = result
    logger.warning("[Meteora] FINAL: %d pools, total_tvl=%.0f", len(result["pools"]), result["total_tvl"])
    return result


# ── 15. RWA Credit Protocol Health (#58) ──────────────────────────────────────

_rwa_credit_cache = {"ts": 0, "data": None}

_RWA_CREDIT_PROTOCOLS = {
    "centrifuge":    "centrifuge",
    "maple":         "maple-finance",
    "clearpool":     "clearpool",
    "goldfinch":     "goldfinch",
}

_DEFILLAMA_API_BASE = "https://api.llama.fi"


def fetch_rwa_credit_health() -> dict:
    """Fetch credit protocol health metrics from DeFiLlama for RWA lending protocols.

    Protocols: Centrifuge, Maple Finance, Clearpool, Goldfinch.

    For each protocol extracts:
      - currentChainTvls: TVL by chain
      - tvl array: historical TVL for 30-day and 7-day trend computation

    Returns:
      {
        "centrifuge": {
            "tvl_usd": float,
            "tvl_7d_change_pct": float,
            "tvl_30d_change_pct": float,
            "chains": list[str],
            "health": "GROWING" | "STABLE" | "DECLINING",
        },
        ...
        "timestamp": str,
      }

    Health: GROWING if 30d change > +5%, DECLINING if < -10%, else STABLE.
    """
    now = time.time()
    if _rwa_credit_cache["data"] is not None and now - _rwa_credit_cache["ts"] < _TTL_LONG:
        return _rwa_credit_cache["data"]

    result: dict = {"timestamp": ""}

    def _fetch_rwa_protocol(name_slug: tuple) -> tuple[str, dict]:
        """Fetch a single RWA credit protocol entry. Returns (name, entry)."""
        name, slug = name_slug
        entry = {
            "tvl_usd":            0.0,
            "tvl_7d_change_pct":  0.0,
            "tvl_30d_change_pct": 0.0,
            "chains":             [],
            "health":             "STABLE",
        }
        try:
            data = _get(f"{_DEFILLAMA_API_BASE}/protocol/{slug}")
            if not data:
                return name, entry

            # Current TVL from currentChainTvls
            current_chain_tvls = data.get("currentChainTvls") or {}
            total_tvl = 0.0
            chains_list = []
            for chain_name, val in current_chain_tvls.items():
                if "staking" in chain_name.lower() or "pool2" in chain_name.lower():
                    continue
                try:
                    chain_tvl = float(val or 0)
                    total_tvl += chain_tvl
                    if chain_tvl > 0:
                        chains_list.append(chain_name)
                except (TypeError, ValueError):
                    pass

            # Fallback: top-level tvl field
            if total_tvl == 0:
                total_tvl = float(data.get("tvl") or 0)

            entry["tvl_usd"] = round(total_tvl, 2)
            entry["chains"]  = chains_list

            # Historical TVL for change computation
            tvl_hist = data.get("tvl") or []
            if isinstance(tvl_hist, list) and len(tvl_hist) >= 2:
                cur_tvl = float((tvl_hist[-1] or {}).get("totalLiquidityUSD") or 0)

                # 7-day change (8 datapoints ≈ 7 days of daily data)
                if len(tvl_hist) >= 8:
                    old_7d = float((tvl_hist[-8] or {}).get("totalLiquidityUSD") or 0)
                    if old_7d > 0:
                        entry["tvl_7d_change_pct"] = round((cur_tvl - old_7d) / old_7d * 100, 2)

                # 30-day change (31 datapoints)
                if len(tvl_hist) >= 31:
                    old_30d = float((tvl_hist[-31] or {}).get("totalLiquidityUSD") or 0)
                    if old_30d > 0:
                        entry["tvl_30d_change_pct"] = round((cur_tvl - old_30d) / old_30d * 100, 2)

            # Health classification
            chg_30d = entry["tvl_30d_change_pct"]
            if chg_30d > 5.0:
                entry["health"] = "GROWING"
            elif chg_30d < -10.0:
                entry["health"] = "DECLINING"
            else:
                entry["health"] = "STABLE"

        except Exception as e:
            logger.warning("[RWACreditHealth] %s (%s) error: %s", name, slug, e)

        return name, entry

    # Fetch all 4 protocols in parallel (OPT-37)
    with ThreadPoolExecutor(max_workers=min(4, len(_RWA_CREDIT_PROTOCOLS))) as ex:
        future_map = {ex.submit(_fetch_rwa_protocol, item): item[0]
                      for item in _RWA_CREDIT_PROTOCOLS.items()}
        for fut in as_completed(future_map):
            try:
                name, entry = fut.result(timeout=20)
                result[name] = entry
            except Exception as e:
                name = future_map[fut]
                logger.warning("[RWACreditHealth] %s parallel fetch error: %s", name, e)
                result[name] = {"tvl_usd": 0.0, "tvl_7d_change_pct": 0.0,
                                "tvl_30d_change_pct": 0.0, "chains": [], "health": "STABLE"}

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _rwa_credit_cache["ts"]   = time.time()
    _rwa_credit_cache["data"] = result
    logger.info(
        "[RWACreditHealth] fetched %d protocols",
        len([k for k in result if k != "timestamp"]),
    )
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


# ── 16. ERC-4626 Vault Reads (#103) ───────────────────────────────────────────

# OPT-45: Lazy-init web3 — deferred until first ERC-4626 call instead of at
# module import time.  web3 pulls ~50+ submodules; skipping it at startup saves
# ~200–400 ms on pages that never call fetch_erc4626_yield_data() or
# fetch_multicall_erc4626().  The globals are initialised once on first use.
_Web3       = None   # type: ignore[assignment]
_W3         = None
_WEB3_AVAIL = False
_WEB3_INIT_DONE = False   # guard so we only attempt init once
_WEB3_INIT_LOCK = threading.Lock()


def _ensure_web3() -> None:
    """Lazily import and connect web3 on first use.  Thread-safe via _WEB3_INIT_LOCK."""
    global _Web3, _W3, _WEB3_AVAIL, _WEB3_INIT_DONE
    if _WEB3_INIT_DONE:
        return
    with _WEB3_INIT_LOCK:
        if _WEB3_INIT_DONE:
            return
        try:
            from web3 import Web3 as _Web3Cls
            _Web3 = _Web3Cls
            _W3   = _Web3Cls(_Web3Cls.HTTPProvider(
                "https://eth.llamarpc.com", request_kwargs={"timeout": 5}
            ))
            try:
                _WEB3_AVAIL = _W3.is_connected()
            except Exception:
                _WEB3_AVAIL = False
        except Exception:
            _Web3       = None
            _W3         = None
            _WEB3_AVAIL = False
        finally:
            _WEB3_INIT_DONE = True

# ERC-4626 minimal ABI — pricePerShare, totalAssets, decimals
_ERC4626_ABI = [
    {"inputs": [], "name": "pricePerShare",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalAssets",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals",
     "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    # convertToAssets(1e18) — Aave v3 aTokens use this instead of pricePerShare
    {"inputs": [{"name": "shares", "type": "uint256"}],
     "name": "convertToAssets",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# Major ERC-4626 compatible vaults on Ethereum mainnet (verified addresses)
_ERC4626_VAULTS: dict[str, dict] = {
    "Morpho USDC (Re7)": {
        "address":       "0x8eB67A509616cd6A7c1B3c8C21D48FF57df3d458",
        "decimals":      6,
        "asset_symbol":  "USDC",
        "yield_source":  "morpho_vault",
    },
    "Morpho WETH (Gauntlet)": {
        "address":       "0x4881Ef0BF6d2365D3dd6499ccd7532bcdBCE0658",
        "decimals":      18,
        "asset_symbol":  "WETH",
        "yield_source":  "morpho_vault",
    },
    "Aave aUSDC v3": {
        "address":       "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
        "decimals":      6,
        "asset_symbol":  "USDC",
        "yield_source":  "aave_v3",
    },
    "Aave aWETH v3": {
        "address":       "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8",
        "decimals":      18,
        "asset_symbol":  "WETH",
        "yield_source":  "aave_v3",
    },
}

_erc4626_cache     = {"ts": 0, "data": None}
_multicall_cache   = {"ts": 0, "data": None}
_TTL_ERC4626       = 300   # 5 minutes

# ── Multicall3 (#109) ─────────────────────────────────────────────────────────
_MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

_MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target",       "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData",     "type": "bytes"},
                ],
                "name": "calls",
                "type": "tuple[]",
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success",    "type": "bool"},
                    {"name": "returnData", "type": "bytes"},
                ],
                "name": "returnData",
                "type": "tuple[]",
            }
        ],
        "stateMutability": "view",
        "type": "function",
    }
]

_TOTAL_ASSETS_ABI = [
    {
        "inputs": [],
        "name": "totalAssets",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }
]


def fetch_multicall_erc4626(vault_addresses: list) -> dict:
    """Batch-read totalAssets from multiple ERC-4626 vaults via Multicall3.

    Returns:
        {"0xVault1": {"total_assets_usd": float, "success": bool}, ...}
        or {} if web3 is unavailable or multicall fails.
    """
    _ensure_web3()
    if not _WEB3_AVAIL or not _W3 or not _Web3:
        return {}

    now = time.time()
    if _multicall_cache["data"] is not None and now - _multicall_cache["ts"] < _TTL_ERC4626:
        cached = _multicall_cache["data"]
        # Return only entries for the requested addresses
        return {k: v for k, v in cached.items() if k in vault_addresses}

    try:
        mc_addr = _Web3.to_checksum_address(_MULTICALL3_ADDRESS)
        multicall = _W3.eth.contract(address=mc_addr, abi=_MULTICALL3_ABI)

        # Build a temporary contract just for ABI encoding
        _dummy_addr = _Web3.to_checksum_address("0x0000000000000000000000000000000000000001")
        _ta_contract = _W3.eth.contract(address=_dummy_addr, abi=_TOTAL_ASSETS_ABI)
        totalAssets_calldata = _ta_contract.encodeABI(fn_name="totalAssets")

        calls = []
        checksum_addrs = []
        for addr in vault_addresses:
            try:
                cs = _Web3.to_checksum_address(addr)
                calls.append((cs, True, totalAssets_calldata))
                checksum_addrs.append(cs)
            except Exception as e:
                logger.debug("[Multicall3] Invalid address %s: %s", addr, e)

        if not calls:
            return {}

        raw_results = multicall.functions.aggregate3(calls).call()

        output: dict = {}
        for cs_addr, (success, return_data) in zip(checksum_addrs, raw_results):
            if success and return_data and len(return_data) >= 32:
                try:
                    total_assets_raw = int.from_bytes(return_data[:32], "big")
                    output[cs_addr] = {"total_assets_usd": float(total_assets_raw), "success": True}
                except Exception:
                    output[cs_addr] = {"total_assets_usd": 0.0, "success": False}
            else:
                output[cs_addr] = {"total_assets_usd": 0.0, "success": False}

        _multicall_cache["ts"]   = time.time()
        _multicall_cache["data"] = output
        logger.info("[Multicall3] Batch-read %d ERC-4626 vaults", len(output))
        return output

    except Exception as e:
        logger.warning("[Multicall3] aggregate3 failed: %s", e)
        return {}


def fetch_erc4626_yield_data() -> dict:
    """Read live pricePerShare from major ERC-4626 yield vaults via web3.py.

    If web3 is unavailable or all RPC calls fail, falls back to DeFiLlama
    pool data for Morpho and Aave.

    Returns:
        {
          vault_name: {
            "price_per_share": float,
            "total_assets_usd": float,   # 0 when unavailable
            "yield_source": str,
            "data_source": "vault_read" | "defillama_fallback",
          },
          "timestamp": str,
        }
    """
    _ensure_web3()   # OPT-45: lazy web3 init
    now = time.time()
    if _erc4626_cache["data"] is not None and now - _erc4626_cache["ts"] < _TTL_ERC4626:
        return _erc4626_cache["data"]

    result: dict = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    _web3_succeeded = False  # track whether any vault read actually succeeded
    if _WEB3_AVAIL and _W3 and _Web3:
        # ── Try Multicall3 batch first (#109) ────────────────────────────────
        _vault_addrs = [cfg["address"] for cfg in _ERC4626_VAULTS.values() if cfg.get("address")]
        _multicall_results = fetch_multicall_erc4626(_vault_addrs)
        _multicall_succeeded = bool(_multicall_results)

        for vault_name, cfg in _ERC4626_VAULTS.items():
            addr = cfg.get("address", "")
            if not addr:
                continue
            dec = cfg.get("decimals", 18)

            # Use multicall totalAssets result if available, skip individual call
            try:
                cs_addr = _Web3.to_checksum_address(addr)
            except Exception:
                cs_addr = addr

            if _multicall_succeeded and cs_addr in _multicall_results:
                mc_entry = _multicall_results[cs_addr]
                if mc_entry["success"]:
                    # Still need pricePerShare individually (not in Multicall3 batch)
                    # totalAssets comes from multicall, pps from individual call
                    total_assets = mc_entry["total_assets_usd"] / (10 ** dec)
                    pps_raw = None
                    try:
                        contract = _W3.eth.contract(address=cs_addr, abi=_ERC4626_ABI)
                        try:
                            pps_raw = contract.functions.pricePerShare().call()
                        except Exception:
                            pass
                        if pps_raw is None:
                            pps_raw = contract.functions.convertToAssets(10 ** dec).call()
                    except Exception:
                        pps_raw = 10 ** dec
                    pps = (pps_raw or 10 ** dec) / (10 ** dec)
                    result[vault_name] = {
                        "price_per_share": round(pps, 8),
                        "total_assets_usd": round(total_assets, 2),
                        "yield_source": cfg.get("yield_source", "vault_read"),
                        "data_source": "vault_read",
                    }
                    _web3_succeeded = True
                    logger.debug("[ERC4626/MC] %s pps=%.6f totalAssets=%.0f", vault_name, pps, total_assets)
                    continue  # skip individual fallback for this vault

            # ── Individual call fallback (when multicall failed/skipped) ─────
            try:
                contract = _W3.eth.contract(address=cs_addr, abi=_ERC4626_ABI)

                # Try pricePerShare first, then convertToAssets(10**dec)
                pps_raw = None
                try:
                    pps_raw = contract.functions.pricePerShare().call()
                except Exception:
                    pass
                if pps_raw is None:
                    try:
                        pps_raw = contract.functions.convertToAssets(10 ** dec).call()
                    except Exception:
                        pps_raw = 10 ** dec   # fallback: par (1:1)

                pps = pps_raw / (10 ** dec)

                # totalAssets (optional — swallow errors)
                total_assets_raw = 0
                try:
                    total_assets_raw = contract.functions.totalAssets().call()
                except Exception:
                    pass
                total_assets = total_assets_raw / (10 ** dec)

                result[vault_name] = {
                    "price_per_share": round(pps, 8),
                    "total_assets_usd": round(total_assets, 2),
                    "yield_source": cfg.get("yield_source", "vault_read"),
                    "data_source": "vault_read",
                }
                _web3_succeeded = True
                logger.debug("[ERC4626] %s pps=%.6f totalAssets=%.0f", vault_name, pps, total_assets)
            except Exception as e:
                logger.debug("[ERC4626] %s read failed: %s", vault_name, e)
                result[vault_name] = {
                    "price_per_share": 1.0,
                    "total_assets_usd": 0.0,
                    "yield_source": cfg.get("yield_source", "vault_read"),
                    "data_source": "unavailable",
                }

        logger.info("[ERC4626] %d vault(s) read via web3", len(_ERC4626_VAULTS))

    # Use DeFiLlama fallback when web3 is unavailable OR when all vault reads failed
    # (e.g. web3 was available at import but the RPC is now blocked on Streamlit Cloud)
    if not _WEB3_AVAIL or not _web3_succeeded:
        # Fallback: DeFiLlama pool data for Morpho and Aave
        logger.info("[ERC4626] web3 unavailable — using DeFiLlama fallback")
        try:
            pools = _get_llama_pools()
            _FALLBACK_SLUGS = {
                "morpho": "Morpho (DeFiLlama)",
                "aave-v3": "Aave v3 (DeFiLlama)",
            }
            for proj_key, label in _FALLBACK_SLUGS.items():
                best_apy = 0.0
                best_tvl = 0.0
                for p in pools:
                    proj  = (p.get("project") or "").lower()
                    chain = (p.get("chain")   or "").lower()
                    if proj != proj_key or chain != "ethereum":
                        continue
                    apy = float(p.get("apy") or 0)
                    tvl = float(p.get("tvlUsd") or 0)
                    if apy > best_apy:
                        best_apy = apy
                        best_tvl = tvl
                if best_apy > 0:
                    result[label] = {
                        "price_per_share": round(1.0 + best_apy / 100 / 365, 8),
                        "total_assets_usd": round(best_tvl, 2),
                        "yield_source": proj_key,
                        "data_source": "defillama_fallback",
                    }
        except Exception as e:
            logger.warning("[ERC4626] DeFiLlama fallback failed: %s", e)

    result["timestamp"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _erc4626_cache["ts"]   = time.time()
    _erc4626_cache["data"] = result
    return result

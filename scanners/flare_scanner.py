"""
Flare Network Scanner
Fetches live data from all Flare DeFi protocols.
Falls back to baseline research data when live APIs are unavailable.
"""

import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Optional

from web3 import Web3

from config import APIS, PROTOCOLS, TOKENS, FLARE_RPC_URLS, FALLBACK_PRICES
from utils.http import http_get as _get, http_post as _post

logger = logging.getLogger(__name__)

# ─── Baseline Token Prices (for TVL calculation when live prices are unavailable) ─
# Used only in Kinetic TVL calc to convert token amounts to USD.
# FLR/WFLR/sFLR mirror FALLBACK_PRICES so both stale-data paths use the same values.
_BASELINE_TOKEN_PRICES = {
    "FLR":    FALLBACK_PRICES["FLR"],
    "WFLR":   FALLBACK_PRICES["FLR"],   # WFLR = wrapped FLR, same price
    "sFLR":   FALLBACK_PRICES["FLR"],   # sFLR ≈ FLR (liquid staked, negligible premium)
    "wETH":   2500.0,
    "USDT0":  1.0,
    "USDC.e": 1.0,
    "USDT":   1.0,
}

# ─── Web3 / On-chain Helpers ──────────────────────────────────────────────────

# Flare C-chain targets ~2-second blocks → ~15.78 M blocks/year
_FLARE_BLOCKS_PER_YEAR = 15_778_800

# Sceptre sFLR liquid staking contract ABI (upgrade #12)
_SFLR_ABI = [
    {"inputs": [{"type": "uint256", "name": "_sharesAmount"}],
     "name": "getPooledFlrByShares", "outputs": [{"type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalPooledFlr",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "totalShares",
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]
_SFLR_ADDRESS = "0x12e605bc104e93B45e1aD99F9e555f659051c2BB"  # Sceptre sFLR on Flare mainnet
_BLOCKS_30D   = int(_FLARE_BLOCKS_PER_YEAR * 30 / 365)        # ~1,297,000 blocks


def fetch_sceptre_onchain_rate() -> Optional[float]:
    """
    Upgrade #12: Compute sFLR APY from on-chain exchange rate change.

    Reads getPooledFlrByShares(1e18) at current block and ~30 days ago.
    Falls back to totalPooledFlr/totalShares ratio method if needed.
    Returns annualised APY % or None if RPC unavailable.
    """
    w3 = _get_web3()
    if w3 is None:
        return None
    try:
        contract = w3.eth.contract(
            address=Web3.to_checksum_address(_SFLR_ADDRESS),
            abi=_SFLR_ABI,
        )
        shares_1e18 = 10 ** 18
        current_block = w3.eth.block_number
        past_block    = max(1, current_block - _BLOCKS_30D)

        try:
            rate_now  = contract.functions.getPooledFlrByShares(shares_1e18).call(block_identifier=current_block)
            rate_past = contract.functions.getPooledFlrByShares(shares_1e18).call(block_identifier=past_block)
        except Exception:
            # Fallback: totalPooledFlr / totalShares
            total_now   = contract.functions.totalPooledFlr().call(block_identifier=current_block)
            shares_now  = contract.functions.totalShares().call(block_identifier=current_block)
            total_past  = contract.functions.totalPooledFlr().call(block_identifier=past_block)
            shares_past = contract.functions.totalShares().call(block_identifier=past_block)
            if shares_now == 0 or shares_past == 0:
                return None
            rate_now  = total_now  * shares_1e18 // shares_now
            rate_past = total_past * shares_1e18 // shares_past

        if rate_past <= 0:
            return None
        growth_30d = (rate_now - rate_past) / rate_past
        apy = round(growth_30d * (365 / 30) * 100, 2)
        return apy if 0.5 <= apy <= 50.0 else None   # sanity bounds
    except Exception as exc:
        logger.warning(f"Sceptre on-chain rate failed: {exc}")
        return None


# Minimal ABI for Compound V2-style kToken contracts
_KTOKEN_ABI = [
    {"inputs": [], "name": "supplyRatePerBlock",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "borrowRatePerBlock",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "getCash",             "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "totalBorrows",        "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "exchangeRateStored",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "totalSupply",         "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
]

_w3_cache: Optional[Web3] = None

def _get_web3() -> Optional[Web3]:
    """Return a connected Web3 instance, trying each RPC URL in order. Result is cached."""
    global _w3_cache
    if _w3_cache is not None and _w3_cache.is_connected():
        return _w3_cache
    for url in FLARE_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
            if w3.is_connected():
                _w3_cache = w3
                return w3
        except Exception:
            continue
    _w3_cache = None
    return None

def _rate_to_apy(rate_per_block: int) -> float:
    """Convert Compound-style rate-per-block (1e18 mantissa) to annualised APY %."""
    r = rate_per_block / 1e18
    # Guard against absurd rates from bad API data (> ~0.000002/block ≈ 250% APY)
    # which would cause (1 + r)^15_000_000 to overflow to infinity.
    if r <= 0:
        return 0.0
    if r > 2e-5:
        r = 2e-5   # cap at ~250% APY max
    return round(((1 + r) ** _FLARE_BLOCKS_PER_YEAR - 1) * 100, 2)

# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PoolData:
    protocol:    str
    pool_name:   str
    apr:         float          # annualised yield % (fee_apr + reward_apr)
    tvl_usd:     float
    token0:      str
    token1:      str
    il_risk:     str            # low / medium / high
    reward_token: str
    data_source: str            # "live" or "baseline"
    reward_apr:  float = 0.0   # incentive-only portion (RFLR/SPRK); subject to decay
    fetched_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())

@dataclass
class LendingRate:
    protocol:    str
    asset:       str
    supply_apy:  float
    borrow_apy:  float
    utilisation: float          # 0–1
    tvl_usd:     float
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())

@dataclass
class StakingYield:
    protocol:    str
    token:       str
    apy:         float
    apy_low:     float
    apy_high:    float
    tvl_usd:     float
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())

@dataclass
class TokenPrice:
    symbol:      str
    price_usd:   float
    change_24h:  float          # %
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())

@dataclass
class ScanResult:
    timestamp:     str
    prices:        list
    pools:         list
    lending:       list
    staking:       list
    scan_duration: float        # seconds
    warnings:      list = field(default_factory=list)
    ftso_prices:   dict = field(default_factory=dict)   # Upgrade #3: {symbol: ftso_price_usd}

# ─── Price Fetcher ────────────────────────────────────────────────────────────

# Module-level cache to prevent redundant CoinGecko calls when options_scanner
# and multi_scanner also need prices within the same 5-minute scan window.
_price_cache: list = []
_price_cache_ts: float = 0.0
_PRICE_CACHE_TTL: int = 300   # seconds


def fetch_prices() -> list:
    """
    Fetch current USD prices for FLR, FXRP, XRP, USD0.
    Results are cached for 5 minutes so that options_scanner and multi_scanner
    can reuse them without hitting CoinGecko a second or third time per scan.
    Uses CoinGecko free tier — no API key needed.
    """
    global _price_cache, _price_cache_ts
    if _price_cache and (time.time() - _price_cache_ts) < _PRICE_CACHE_TTL:
        logger.debug("fetch_prices: returning cached prices (TTL not expired)")
        return _price_cache

    ids = "flare-networks,ripple,tether"
    url = f"{APIS['coingecko']}/simple/price"
    data = _get(url, params={
        "ids": ids,
        "vs_currencies": "usd",
        "include_24hr_change": "true"
    })

    results = []

    if data:
        mapping = {
            "flare-networks": ("FLR",  "live"),
            "ripple":         ("XRP",  "live"),
            "tether":         ("USD0", "live"),
        }
        for cg_id, (symbol, src) in mapping.items():
            if cg_id in data:
                results.append(TokenPrice(
                    symbol=symbol,
                    price_usd=data[cg_id].get("usd", 0),
                    change_24h=data[cg_id].get("usd_24h_change", 0),
                    data_source=src,
                ))
        # FXRP tracks XRP price (1:1 peg minus small fee)
        xrp = next((p for p in results if p.symbol == "XRP"), None)
        if xrp:
            results.append(TokenPrice(
                symbol="FXRP",
                price_usd=xrp.price_usd * 0.998,   # ~0.2% bridge fee
                change_24h=xrp.change_24h,
                data_source="derived",
            ))
    else:
        # Fallback: last known reasonable estimates (config.FALLBACK_PRICES)
        logger.warning("CoinGecko unavailable — using price estimates")
        results = [
            TokenPrice(sym, price, 0.0, "estimate")
            for sym, price in FALLBACK_PRICES.items()
        ]

    _price_cache    = results
    _price_cache_ts = time.time()
    return results

# ─── FTSO Price Oracle Fetcher (Upgrade #3) ──────────────────────────────────

# FTSOv2 feed IDs — hex-encoded "FLR/USD" and "XRP/USD"
_FTSO_FEEDS = {
    "FLR": "0x01464c522f555344000000000000000000000000",
    "XRP": "0x015852502f555344000000000000000000000000",
}

def fetch_ftso_prices() -> dict:
    """
    Fetch current FTSO oracle prices for FLR and XRP from the Flare data availability layer.
    Returns {symbol: price_usd} or {} on failure (non-blocking — graceful degradation).
    Used as a conviction multiplier in risk models: if FTSO agrees with CoinGecko,
    confidence in the data is higher; large divergence signals potential arb opportunity.
    """
    base = APIS.get("ftso_data", "https://flr-data-availability.flare.network")
    results = {}
    try:
        # Try FTSOv2 REST API — GET /api/v0/feeds/collection returns all active feeds
        data = _get(f"{base}/api/v0/feeds/collection", timeout=6)
        if data and isinstance(data, dict):
            feeds = data.get("feeds", data.get("data", []))
            for feed in feeds:
                name = feed.get("name", feed.get("feedId", ""))
                price = feed.get("value", feed.get("price"))
                if name and price is not None:
                    # Match "FLR/USD" → "FLR", "XRP/USD" → "XRP"
                    for sym in ("FLR", "XRP"):
                        if sym in str(name).upper():
                            try:
                                results[sym] = float(price)
                            except (TypeError, ValueError):
                                pass
    except Exception as exc:
        logger.debug(f"FTSO collection endpoint failed: {exc}")

    # Fallback: try individual feed endpoints if collection failed
    if not results:
        for sym, feed_id in _FTSO_FEEDS.items():
            try:
                data = _get(f"{base}/api/v0/feeds/{feed_id}", timeout=5)
                if data and isinstance(data, dict):
                    price = data.get("value", data.get("price"))
                    if price is not None:
                        results[sym] = float(price)
            except Exception:
                pass

    if results:
        logger.info(f"FTSO prices fetched: {results}")
    else:
        logger.debug("FTSO prices unavailable — continuing without oracle signal")
    return results


# ─── DeFiLlama Yields Integration ────────────────────────────────────────────

# DeFiLlama project slug → our protocol key
# Slugs verified against https://yields.llama.fi/pools (Flare chain)
_DL_PROTOCOL_MAP = {
    "clearpool-lending":       "clearpool",
    "mystic-finance-lending":  "mystic",
    "sceptre-liquid":          "sceptre",
    "spectra-v2":              "spectra",
    "spectra-metavaults":      "spectra",
    # upshift, firelight, cyclo, enosys, blazeswap not yet listed on DeFiLlama
}

_defillama_cache: dict = {}
_defillama_cache_ts: float = 0.0
_DEFILLAMA_CACHE_TTL: int = 300   # seconds


def _fetch_defillama_raw() -> dict:
    """
    Fetch DeFiLlama yields for the Flare chain. Cached for 5 minutes.
    Returns {protocol_key: [pool_dict, ...]} — each pool dict has:
      symbol, apy, apy_base, apy_reward, tvl_usd, il_7d
    """
    global _defillama_cache, _defillama_cache_ts
    if _defillama_cache and (time.time() - _defillama_cache_ts) < _DEFILLAMA_CACHE_TTL:
        return _defillama_cache

    data = _get("https://yields.llama.fi/pools", timeout=15)
    result: dict = {}

    if data and "data" in data:
        for pool in data["data"]:
            if (pool.get("chain") or "").lower() != "flare":
                continue
            project   = (pool.get("project") or "").lower()
            proto_key = _DL_PROTOCOL_MAP.get(project)
            if not proto_key:
                continue
            def _sf(val, default=0.0):
                try:
                    return float(val) if val is not None else default
                except (TypeError, ValueError):
                    return default
            result.setdefault(proto_key, []).append({
                "symbol":     pool.get("symbol", ""),
                "apy":        _sf(pool.get("apy")),
                "apy_base":   _sf(pool.get("apyBase")),
                "apy_reward": _sf(pool.get("apyReward")),
                "tvl_usd":    _sf(pool.get("tvlUsd")),
                "il_7d":      pool.get("il7d"),
            })
        _defillama_cache    = result
        _defillama_cache_ts = time.time()
        total = sum(len(v) for v in result.values())
        if total:
            logger.info(f"DeFiLlama: fetched {total} Flare pool(s) across {len(result)} protocol(s)")
    else:
        logger.warning("DeFiLlama yields API unavailable — protocols will use baseline data")

    return result


# ─── DEX Pool Fallback Helper ─────────────────────────────────────────────────

def _baseline_pools(protocol_key: str) -> list:
    """Return config baseline pools when the subgraph API is unavailable."""
    logger.warning(f"{PROTOCOLS[protocol_key]['name']} subgraph unavailable — using baseline data")
    pools = []
    for name, cfg in PROTOCOLS[protocol_key]["pools"].items():
        try:
            t0, t1 = name.split("-", 1)
        except ValueError:
            t0, t1 = name, ""
        # Support both key names: old DEXes use "baseline_apr", new GT-based DEXes use "reward_apr"
        rwd_apr      = cfg.get("reward_apr", 0)
        fallback_apr = cfg.get("baseline_apr", rwd_apr)
        pools.append(PoolData(
            protocol=protocol_key,
            pool_name=name,
            apr=fallback_apr,
            tvl_usd=0,
            token0=t0,
            token1=t1,
            il_risk=cfg["il_risk"],
            reward_token=cfg.get("reward_token", ""),
            data_source="baseline",
            reward_apr=rwd_apr,
        ))
    return pools


# ─── Blazeswap Pool Scanner ───────────────────────────────────────────────────

_BLAZESWAP_POOLS_QUERY = """
{
  pairs(first: 20, orderBy: reserveUSD, orderDirection: desc) {
    id
    token0 { symbol }
    token1 { symbol }
    reserveUSD
    token0Price
    token1Price
    pairDayDatas(first: 7, orderBy: date, orderDirection: desc) {
      dailyVolumeUSD
    }
  }
}
"""

def fetch_blazeswap_pools() -> list:
    # 1 — GeckoTerminal (pre-warmed cache, live fee APR + reward APR from config)
    gt_pools = []
    for dex_id in _BLAZESWAP_DEX_IDS:
        gt_pools.extend(_fetch_gt_dex_pools(dex_id, "blazeswap"))
    if gt_pools:
        return _dedup_pools(gt_pools)

    # 2 — Subgraph fallback (may 404 — GraphQL endpoint is unreliable)
    data  = _post(APIS["blazeswap_graph"], {"query": _BLAZESWAP_POOLS_QUERY})
    pools = []
    if data and "data" in data and "pairs" in data["data"]:
        for pair in data["data"]["pairs"]:
            t0 = (pair.get("token0") or {}).get("symbol", "?")
            t1 = (pair.get("token1") or {}).get("symbol", "?")
            name = f"{t0}-{t1}"
            tvl  = float(pair.get("reserveUSD") or 0)

            day_vols   = pair.get("pairDayDatas", [])
            weekly_vol = sum(float(d.get("dailyVolumeUSD") or 0) for d in day_vols)
            fee_apr    = (weekly_vol * 0.003 * 52 / tvl * 100) if tvl > 0 else 0

            cfg_pools  = PROTOCOLS["blazeswap"]["pools"]
            cfg_key  = f"{t0}-{t1}" if f"{t0}-{t1}" in cfg_pools else f"{t1}-{t0}"
            baseline = cfg_pools.get(cfg_key, {})
            # reward_apr in config is the incentive-only portion — add directly to fee APR.
            # Only fall back to deriving from baseline_apr (total) when reward_apr is absent.
            if "reward_apr" in baseline:
                reward_apr = baseline["reward_apr"]
            else:
                reward_apr = max(0, baseline.get("baseline_apr", fee_apr) - fee_apr)
            total_apr  = fee_apr + reward_apr

            pools.append(PoolData(
                protocol="blazeswap",
                pool_name=name,
                apr=round(total_apr, 2),
                tvl_usd=round(tvl, 0),
                token0=t0,
                token1=t1,
                il_risk=baseline.get("il_risk", "medium"),
                reward_token=baseline.get("reward_token", "RFLR"),
                data_source="live",
                reward_apr=round(reward_apr, 2),
            ))

    # 3 — Hardcoded baseline if both live sources fail
    return pools if pools else _baseline_pools("blazeswap")

# ─── SparkDEX Pool Scanner ────────────────────────────────────────────────────

# ─── GeckoTerminal Pool Scanner (SparkDEX V3.1, V4 + Enosys) ────────────────
# The old Goldsky subgraph URLs are no longer active.
# GeckoTerminal provides free live TVL + 24h volume for all Flare DEX pools.

_GT_BASE    = "https://api.geckoterminal.com/api/v2"
_GT_HEADERS = {"Accept": "application/json;version=20230302"}

# SparkDEX has two active versions — both covered
_SPARKDEX_DEX_IDS  = ["sparkdex-v3-1", "sparkdex-v4"]
_ENOSYS_DEX_IDS    = ["enosys-v3-flare"]
_BLAZESWAP_DEX_IDS = ["blazeswap-flare"]
_ALL_GT_DEX_IDS    = _SPARKDEX_DEX_IDS + _ENOSYS_DEX_IDS + _BLAZESWAP_DEX_IDS

# Module-level GeckoTerminal cache (TTL 5 min) — pre-warmed before parallel threads
_gt_cache: dict = {}           # {dex_id: [raw pool data, ...]}
_gt_cache_ts: float = 0.0
_GT_CACHE_TTL: int = 600


def _prewarm_gt_cache() -> None:
    """
    Fetch all DEX pools from GeckoTerminal sequentially with a 1.2s gap between
    requests (respects the 30 req/min free-tier limit). Stores results in the
    module-level cache so parallel threads can reuse without extra HTTP calls.
    """
    global _gt_cache, _gt_cache_ts
    if _gt_cache and (time.time() - _gt_cache_ts) < _GT_CACHE_TTL:
        return
    result = {}
    for i, dex_id in enumerate(_ALL_GT_DEX_IDS):
        if i > 0:
            time.sleep(1.2)
        url  = (f"{_GT_BASE}/networks/flare/dexes/{dex_id}/pools"
                f"?page=1&order=h24_volume_usd_desc")
        data = _get(url, timeout=15, headers=_GT_HEADERS)
        result[dex_id] = (data or {}).get("data", [])
        if data is None:
            logger.warning(f"GeckoTerminal pre-warm {dex_id} failed")
        else:
            logger.debug(f"GT cache: {dex_id} → {len(result[dex_id])} pools")
    _gt_cache = result
    _gt_cache_ts = time.time()

# Normalize GeckoTerminal token symbols to the names used elsewhere in the app
_GT_TOKEN_NORM = {
    "USD₮0": "USDT0",
    "USD?0": "USDT0",   # ASCII fallback from encoding issues
}

# Pairs whose tokens move together — low impermanent-loss
_LOW_IL_PAIRS = {
    frozenset(["sFLR",  "WFLR"]),   # liquid staked FLR vs wrapped FLR — tightly correlated
    frozenset(["stFLR", "WFLR"]),   # staked FLR vs wrapped FLR
    frozenset(["stXRP", "FXRP"]),   # liquid staked XRP vs FAsset XRP
    frozenset(["flrETH","WETH"]),   # Flare liquid staked ETH vs WETH
    frozenset(["cyWETH","WETH"]),   # Cyclo wrapped ETH vs WETH
}
_STABLECOINS = {"USDT0", "USDC.e", "USD0", "DAI", "FRAX", "eUSDT", "USDX"}


def _gt_il_risk(t0: str, t1: str) -> str:
    if {t0, t1} <= _STABLECOINS:
        return "none"
    if frozenset([t0, t1]) in _LOW_IL_PAIRS:
        return "low"
    if t0 in _STABLECOINS or t1 in _STABLECOINS:
        return "medium"
    return "high"


def _fetch_gt_dex_pools(dex_id: str, protocol: str,
                         min_tvl: float = 5_000.0) -> list:
    """
    Fetch all pools for one GeckoTerminal DEX identifier on Flare.
    Returns a list of PoolData with live fee APR computed from 24h volume.
    """
    # Use pre-warmed cache if available, otherwise fetch live
    raw_pools = _gt_cache.get(dex_id)
    if raw_pools is None:
        url  = (f"{_GT_BASE}/networks/flare/dexes/{dex_id}/pools"
                f"?page=1&order=h24_volume_usd_desc")
        data = _get(url, timeout=15, headers=_GT_HEADERS)
        if data is None:
            logger.warning(f"GeckoTerminal {dex_id} fetch failed")
            return []
        raw_pools = data.get("data", [])

    cfg_pools  = PROTOCOLS[protocol]["pools"]
    reward_tok = "SPRK" if protocol == "sparkdex" else "RFLR"
    results    = []

    for p in raw_pools:
        attr    = p.get("attributes", {})
        name_gt = attr.get("name", "")
        tvl     = float(attr.get("reserve_in_usd", 0) or 0)
        vol_24h = float((attr.get("volume_usd") or {}).get("h24", 0) or 0)

        if tvl < min_tvl:
            continue

        # Extract fee tier from name suffix e.g. "FXRP / USD₮0 0.05%"
        fee_match = re.search(r'(\d+\.?\d*)%\s*$', name_gt)
        fee       = float(fee_match.group(1)) / 100 if fee_match else 0.003

        # Parse token pair
        pair_part = name_gt[:fee_match.start()].strip() if fee_match else name_gt
        tokens    = [_GT_TOKEN_NORM.get(t.strip(), t.strip())
                     for t in pair_part.split("/")]
        t0 = tokens[0]
        t1 = tokens[1] if len(tokens) > 1 else "?"
        pool_name = f"{t0}-{t1}"

        # Fee APR from live 24h volume, annualised
        fee_apr = (vol_24h * fee * 365 / tvl * 100) if tvl > 0 else 0

        # Look up config for reward incentives; try both orderings
        cfg_key  = pool_name if pool_name in cfg_pools else f"{t1}-{t0}"
        baseline = cfg_pools.get(cfg_key, {})
        # Total = fee APR + reward incentive APR (additive, not max)
        reward_apr = baseline.get("reward_apr", 0)
        total_apr  = fee_apr + reward_apr

        results.append(PoolData(
            protocol=protocol,
            pool_name=pool_name,
            apr=round(total_apr, 2),
            tvl_usd=round(tvl, 0),
            token0=t0,
            token1=t1,
            il_risk=baseline.get("il_risk", _gt_il_risk(t0, t1)),
            reward_token=baseline.get("reward_token", reward_tok),
            data_source="live",
            reward_apr=round(reward_apr, 2),
        ))

    return results


def _dedup_pools(pools: list) -> list:
    """
    When the same token pair appears from multiple DEX versions (e.g. V3.1 + V4),
    keep only the instance with the highest TVL to avoid duplicate recommendations.
    Uses frozenset key so "FXRP-USD0" and "USD0-FXRP" are treated as the same pair.
    """
    best: dict = {}
    for p in pools:
        key = frozenset([p.token0, p.token1])
        if key not in best or p.tvl_usd > best[key].tvl_usd:
            best[key] = p
    return list(best.values())


def fetch_sparkdex_pools() -> list:
    """SparkDEX V3.1 + V4 via GeckoTerminal (pre-warmed cache), deduplicated by pair."""
    pools = []
    for dex_id in _SPARKDEX_DEX_IDS:
        pools.extend(_fetch_gt_dex_pools(dex_id, "sparkdex"))
    return _dedup_pools(pools) if pools else _baseline_pools("sparkdex")


# ─── Enosys Pool Scanner ──────────────────────────────────────────────────────

def fetch_enosys_pools() -> list:
    """Enosys V3 via GeckoTerminal."""
    pools = []
    for dex_id in _ENOSYS_DEX_IDS:
        pools.extend(_fetch_gt_dex_pools(dex_id, "enosys"))
    return pools if pools else _baseline_pools("enosys")

# ─── Kinetic Lending Scanner ──────────────────────────────────────────────────

def fetch_kinetic_rates() -> list:
    """
    Fetch live Kinetic lending rates directly from on-chain kToken contracts
    (Compound V2 fork on Flare mainnet).  Falls back to config baselines if
    the RPC is unreachable or a call fails.
    """
    w3 = _get_web3()
    k_tokens = PROTOCOLS["kinetic"]["kTokens"]
    rates = []

    for asset, cfg in k_tokens.items():
        supply_apy = borrow_apy = utilisation = tvl_usd = None
        data_source = "live"

        try:
            if w3 is None:
                raise ConnectionError("No Flare RPC reachable")

            contract = w3.eth.contract(
                address=Web3.to_checksum_address(cfg["address"]),
                abi=_KTOKEN_ABI,
            )

            supply_rate = contract.functions.supplyRatePerBlock().call()
            borrow_rate = contract.functions.borrowRatePerBlock().call()
            cash         = contract.functions.getCash().call()
            total_borrows = contract.functions.totalBorrows().call()

            supply_apy = _rate_to_apy(supply_rate)
            borrow_apy = _rate_to_apy(borrow_rate)

            # Utilisation = borrows / (cash + borrows)
            denom = cash + total_borrows
            utilisation = round(total_borrows / denom, 4) if denom > 0 else 0.0

            # TVL: convert raw token units → USD using baseline price for non-stablecoins
            underlying_decimals = cfg["decimals"]
            if not (0 <= underlying_decimals <= 30):
                logger.warning(f"Kinetic: invalid decimals {underlying_decimals} for {asset} — defaulting to 18")
                underlying_decimals = 18
            token_amount = (cash + total_borrows) / (10 ** underlying_decimals)
            token_price  = _BASELINE_TOKEN_PRICES.get(asset, 1.0)
            tvl_usd = round(token_amount * token_price, 2)

        except Exception as e:
            logger.warning(f"Kinetic on-chain fetch failed for {asset}: {e} — using baseline")
            supply_apy  = cfg["baseline_supply"]
            borrow_apy  = cfg["baseline_borrow"]
            utilisation = 0.0   # unknown when using baseline; do not fabricate a value
            tvl_usd     = PROTOCOLS["kinetic"]["tvl_usd"] / max(1, len(k_tokens))
            data_source = "baseline"

        rates.append(LendingRate(
            protocol="kinetic",
            asset=asset,
            supply_apy=supply_apy,
            borrow_apy=borrow_apy,
            utilisation=utilisation,
            tvl_usd=tvl_usd,
            data_source=data_source,
        ))

    return rates

# ─── Clearpool Rates Scanner ──────────────────────────────────────────────────

def fetch_clearpool_rates() -> list:
    """Clearpool lending rates. Tries DeFiLlama first, then Clearpool API, then baseline."""
    # 1 — DeFiLlama (live)
    dl = _fetch_defillama_raw()
    cp_pools = dl.get("clearpool", [])
    if cp_pools:
        return [LendingRate(
            protocol="clearpool",
            asset=p["symbol"] or "USD0",
            supply_apy=p["apy"],
            borrow_apy=0,
            utilisation=0.0,
            tvl_usd=p["tvl_usd"],
            data_source="live",
        ) for p in cp_pools if p["apy"] > 0]

    # 2 — Clearpool public REST API
    try:
        cp_data = _get("https://api.clearpool.finance/pools", timeout=8)
        if isinstance(cp_data, list):
            rates = []
            for pool in cp_data:
                chain = ((pool.get("network") or {}).get("name") or "").lower()
                if "flare" not in chain:
                    continue
                raw_apr = float(pool.get("apr", 0))
                apr     = raw_apr * 100 if raw_apr < 1 else raw_apr   # normalise 0-1 vs percent
                rates.append(LendingRate(
                    protocol="clearpool",
                    asset=pool.get("currencySymbol", "USD0"),
                    supply_apy=apr,
                    borrow_apy=0,
                    utilisation=float(pool.get("utilization", 0)),
                    tvl_usd=float(pool.get("poolSize", 0)),
                    data_source="live",
                ))
            if rates:
                return rates
    except Exception as e:
        logger.debug(f"Clearpool REST API failed: {e}")

    # 3 — Baseline fallback
    return [LendingRate(
        protocol="clearpool",
        asset=cfg["asset"],
        supply_apy=cfg["apr"],
        borrow_apy=0,
        utilisation=0.0,
        tvl_usd=PROTOCOLS["clearpool"]["tvl_usd"] / 2,
        data_source="baseline",
    ) for cfg in PROTOCOLS["clearpool"]["pools"].values()]

# ─── Mystic (Morpho) Rates Scanner ───────────────────────────────────────────

def fetch_mystic_rates() -> list:
    """Mystic Finance lending rates. Tries DeFiLlama first, then baseline."""
    dl = _fetch_defillama_raw()
    mystic_pools = dl.get("mystic", [])
    if mystic_pools:
        return [LendingRate(
            protocol="mystic",
            asset=p["symbol"] or "USD0",
            supply_apy=p["apy"],
            borrow_apy=0,
            utilisation=0.0,
            tvl_usd=p["tvl_usd"],
            data_source="live",
        ) for p in mystic_pools if p["apy"] > 0]

    return [LendingRate(
        protocol="mystic",
        asset=cfg["asset"],
        supply_apy=cfg["supply_apy"],
        borrow_apy=0,
        utilisation=0.0,
        tvl_usd=0,
        data_source="baseline",
    ) for cfg in PROTOCOLS["mystic"]["vaults"].values()]

# ─── Staking Yields ───────────────────────────────────────────────────────────

def fetch_staking_yields() -> list:
    """Staking/vault yields. Uses DeFiLlama for live data; falls back to research."""
    yields = []
    dl = _fetch_defillama_raw()

    def _dl_pick(proto_key: str, symbol_hint: str):
        """Return best matching DeFiLlama pool for a protocol+symbol hint."""
        pools = dl.get(proto_key, [])
        hint  = symbol_hint.lower()
        return next((p for p in pools if p.get("symbol") and hint in p["symbol"].lower() and p.get("apy", 0) > 0), None)

    # ─── sFLR via Sceptre — on-chain → DeFiLlama → baseline ─────────────────
    # Upgrade #12: try on-chain exchange-rate diff first
    onchain_apy = fetch_sceptre_onchain_rate()
    if onchain_apy is not None:
        sp = _dl_pick("sceptre", "sflr")
        tvl = sp.get("tvl_usd", 0) if sp else 0
        yields.append(StakingYield(
            protocol="sceptre", token="sFLR",
            apy=onchain_apy,
            apy_low=onchain_apy * 0.85,
            apy_high=onchain_apy * 1.15,
            tvl_usd=tvl, data_source="on-chain",
        ))
    else:
        sp = _dl_pick("sceptre", "sflr")
        if sp:
            yields.append(StakingYield(
                protocol="sceptre", token="sFLR",
                apy=sp["apy"], apy_low=sp["apy"] * 0.85, apy_high=sp["apy"] * 1.15,
                tvl_usd=sp["tvl_usd"], data_source="live",
            ))
        else:
            _sflr_cfg = PROTOCOLS["sceptre"]["tokens"]["sFLR"]
            _sflr_mid = (_sflr_cfg["base_apy_low"] + _sflr_cfg["base_apy_high"]) / 2
            yields.append(StakingYield(
                protocol="sceptre", token="sFLR",
                apy=_sflr_mid,
                apy_low=_sflr_cfg["base_apy_low"],
                apy_high=_sflr_cfg["base_apy_high"],
                tvl_usd=0, data_source="baseline",
            ))

    # ─── stXRP via Firelight ─────────────────────────────────────────────────
    fp = _dl_pick("firelight", "xrp")
    if fp:
        yields.append(StakingYield(
            protocol="firelight", token="stXRP",
            apy=fp["apy"], apy_low=fp["apy"] * 0.70, apy_high=fp["apy"] * 1.30,
            tvl_usd=fp["tvl_usd"], data_source="live",
        ))
    else:
        yields.append(StakingYield(
            protocol="firelight", token="stXRP",
            apy=5.0, apy_low=4.0, apy_high=7.0,
            tvl_usd=0, data_source="baseline",
        ))

    # ─── Spectra sFLR markets (PT fixed-rate + LP) ───────────────────────────
    # DeFiLlama uses "SW-SFLR" for both; lower APY = fixed-rate PT, higher = LP.
    spectra_sflr = sorted(
        [p for p in dl.get("spectra", []) if p.get("symbol") and "sflr" in p["symbol"].lower() and p.get("apy", 0) > 0],
        key=lambda x: x.get("apy", 0),
    )
    if len(spectra_sflr) >= 1:
        pt_pool = spectra_sflr[0]   # lowest APY = fixed-rate PT
        yields.append(StakingYield(
            protocol="spectra", token="PT-sFLR",
            apy=pt_pool["apy"], apy_low=pt_pool["apy"], apy_high=pt_pool["apy"] * 1.05,
            tvl_usd=pt_pool["tvl_usd"], data_source="live",
        ))
    else:
        yields.append(StakingYield(
            protocol="spectra", token="PT-sFLR",
            apy=10.79, apy_low=10.79, apy_high=19.59,
            tvl_usd=291_762, data_source="research",
        ))

    if len(spectra_sflr) >= 2:
        lp_pool = spectra_sflr[-1]  # highest APY = LP market
        yields.append(StakingYield(
            protocol="spectra", token="LP-sFLR",
            apy=lp_pool["apy"], apy_low=lp_pool["apy"] * 0.75, apy_high=lp_pool["apy"] * 1.35,
            tvl_usd=lp_pool["tvl_usd"], data_source="live",
        ))
    else:
        yields.append(StakingYield(
            protocol="spectra", token="LP-sFLR",
            apy=36.74, apy_low=30.0, apy_high=45.0,
            tvl_usd=291_762, data_source="research",
        ))

    # ─── Upshift earnXRP ─────────────────────────────────────────────────────
    up = _dl_pick("upshift", "xrp")
    if up:
        yields.append(StakingYield(
            protocol="upshift", token="earnXRP",
            apy=up["apy"], apy_low=up["apy"] * 0.70, apy_high=up["apy"] * 1.30,
            tvl_usd=up["tvl_usd"], data_source="live",
        ))
    else:
        yields.append(StakingYield(
            protocol="upshift", token="earnXRP",
            apy=7.0, apy_low=4.0, apy_high=10.0,
            tvl_usd=33_900_000, data_source="research",
        ))

    return yields


# ─── Cyclo Finance Scanner ────────────────────────────────────────────────────

def fetch_cyclo_rates() -> list:
    """
    Cyclo Finance: sFLR → cysFLR leveraged yield.
    cysFLR trades at a discount to sFLR, amplifying effective yield.
    Tries DeFiLlama first; falls back to research baseline.
    """
    dl = _fetch_defillama_raw()
    cyclo_pools = [p for p in dl.get("cyclo", []) if p.get("apy", 0) > 0]
    if cyclo_pools:
        return [StakingYield(
            protocol="cyclo",
            token=p["symbol"] or "cysFLR",
            apy=p["apy"],
            apy_low=max(0.0, p["apy"] * 0.60),
            apy_high=p["apy"] * 1.50,
            tvl_usd=p["tvl_usd"],
            data_source="live",
        ) for p in cyclo_pools]

    # Research baseline: sFLR base (~9%) + rFLR incentives + discount mechanism
    return [StakingYield(
        protocol="cyclo",
        token="cysFLR",
        apy=22.0,
        apy_low=12.0,
        apy_high=38.0,
        tvl_usd=0,
        data_source="baseline",
    )]

# ─── FAsset System Data Fetcher (Upgrade #4) ─────────────────────────────────

# Research-based FAsset baselines (updated March 2026)
# Used when the FAsset API is unavailable.
_FASSET_BASELINE = {
    "FXRP": {
        "mint_fee_bips": 25,       # 0.25% mint fee
        "redeem_fee_bips": 20,     # 0.20% redemption fee
        "min_cr_bips": 16000,      # 160% min collateral ratio (CCB)
        "safety_cr_bips": 20000,   # 200% safety CR
        "circulating": 12_500_000, # ~12.5M FXRP circulating (est)
        "collateral_token": "FLR",
        "note": "First FAsset live on mainnet. XRP bridged via Flare bridge.",
    },
    "FBTC": {
        "mint_fee_bips": 25,
        "redeem_fee_bips": 20,
        "min_cr_bips": 16000,
        "safety_cr_bips": 20000,
        "circulating": 0,
        "collateral_token": "FLR",
        "note": "Beta / limited minting as of Mar 2026.",
    },
    "FDOGE": {
        "mint_fee_bips": 25,
        "redeem_fee_bips": 20,
        "min_cr_bips": 16000,
        "safety_cr_bips": 20000,
        "circulating": 0,
        "collateral_token": "FLR",
        "note": "Beta / very limited minting as of Mar 2026.",
    },
}


def fetch_fasset_data() -> dict:
    """
    Fetch live FAsset system data.  Tries multiple public endpoints in order,
    then enriches circulating-supply figures from DeFiLlama as a fallback.

    Keys in return dict:
      data_source: "live" | "baseline"
      assets: {symbol: {mint_fee_pct, redeem_fee_pct, cr_pct, circulating, ...}}
      system_health: "healthy" | "caution" | "unknown"
      premium_discount: {symbol: pct}
      agent_count: int
    """
    result = {
        "data_source": "baseline",
        "assets": {},
        "system_health": "unknown",
        "premium_discount": {},
        "agent_count": 0,
        "fetched_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }

    def _parse_fasset_response(data: dict) -> bool:
        """Parse a successful API response into result. Returns True on success."""
        assets_raw = data.get("fassets", data.get("data", data.get("assets", {})))
        if not isinstance(assets_raw, dict) or not assets_raw:
            return False
        for sym, info in assets_raw.items():
            sym_upper = sym.upper()
            base_info = _FASSET_BASELINE.get(sym_upper, {})
            minting_fee   = info.get("mintingFee",     info.get("minting_fee",   base_info.get("mint_fee_bips",   25)))
            redeem_fee    = info.get("redemptionFee",  info.get("redemption_fee",base_info.get("redeem_fee_bips", 20)))
            collat_ratio  = info.get("collateralRatio",info.get("collateral_ratio",base_info.get("min_cr_bips",16000)))
            circulating   = info.get("circulatingSupply", info.get("circulating_supply", base_info.get("circulating", 0)))
            result["assets"][sym_upper] = {
                "mint_fee_pct":     minting_fee / 100 if minting_fee > 1 else minting_fee,
                "redeem_fee_pct":   redeem_fee  / 100 if redeem_fee  > 1 else redeem_fee,
                "cr_pct":           collat_ratio / 100 if collat_ratio > 100 else collat_ratio,
                "circulating":      float(circulating or 0),
                "collateral_token": base_info.get("collateral_token", "FLR"),
                "note":             base_info.get("note", ""),
            }
        agents = data.get("agents", data.get("agentCount", data.get("agent_count", 0)))
        result["agent_count"] = agents if isinstance(agents, int) else (len(agents) if isinstance(agents, list) else 0)
        health = data.get("systemHealth", data.get("health", data.get("system_health", "")))
        result["system_health"] = health.lower() if health else "healthy"
        return True

    # ── Attempt 1: Flare API portal (several known path variants) ─────────────
    base = APIS.get("flare_api_portal", "https://api-portal.flare.network")
    _api_paths = [
        f"{base}/fassets/api/v1/state",
        f"{base}/fassets/api/v1/fassets",
        f"{base}/fassets/api/v2/state",
        "https://api.flare.network/fassets/api/v1/state",
        "https://api.flare.network/fassets/api/v1/fassets",
    ]
    for _url in _api_paths:
        try:
            data = _get(_url, timeout=8)
            if data and isinstance(data, dict) and _parse_fasset_response(data):
                result["data_source"] = "live"
                logger.info(f"FAsset data fetched live from {_url}")
                break
        except Exception as exc:
            logger.warning(f"FAsset API {_url} failed: {exc}")

    # ── Attempt 2: DeFiLlama — enrich circulating supply for FXRP ────────────
    if result["data_source"] == "baseline" or (result["assets"].get("FXRP") or {}).get("circulating", 0) == 0:
        try:
            dl = _get("https://api.llama.fi/protocol/flare-fassets", timeout=8)
            if dl and isinstance(dl, dict):
                # Pull latest TVL figures and back-calculate FXRP circulating supply
                current_tvl = dl.get("currentChainTvls", {})
                fxrp_tvl = current_tvl.get("Flare", dl.get("tvl", 0))
                if isinstance(fxrp_tvl, list) and fxrp_tvl:
                    fxrp_tvl = fxrp_tvl[-1].get("totalLiquidityUSD", 0)
                if fxrp_tvl and fxrp_tvl > 0:
                    result["data_source"] = "live"
                    # Estimate circulating from TVL ÷ XRP price (best effort)
                    xrp_price = FALLBACK_PRICES.get("XRP", 2.0)
                    fxrp_circ = int(fxrp_tvl / xrp_price)
                    if "FXRP" not in result["assets"]:
                        result["assets"]["FXRP"] = {k: v for k, v in {
                            **{k: _FASSET_BASELINE["FXRP"][k] for k in ("collateral_token", "note")},
                            "mint_fee_pct":   _FASSET_BASELINE["FXRP"]["mint_fee_bips"] / 100,
                            "redeem_fee_pct": _FASSET_BASELINE["FXRP"]["redeem_fee_bips"] / 100,
                            "cr_pct":         _FASSET_BASELINE["FXRP"]["min_cr_bips"] / 100,
                        }.items()}
                    result["assets"]["FXRP"]["circulating"] = fxrp_circ
                    result["system_health"] = result["system_health"] if result["system_health"] != "unknown" else "healthy"
                    logger.info(f"FAsset FXRP supply enriched from DeFiLlama: {fxrp_circ:,}")
        except Exception as exc:
            logger.warning(f"DeFiLlama FAssets fallback failed: {exc}")

    # ── Fill any missing assets from static baselines ─────────────────────────
    for sym, base_info in _FASSET_BASELINE.items():
        if sym not in result["assets"]:
            result["assets"][sym] = {
                "mint_fee_pct":     base_info["mint_fee_bips"] / 100,
                "redeem_fee_pct":   base_info["redeem_fee_bips"] / 100,
                "cr_pct":           base_info["min_cr_bips"] / 100,
                "circulating":      base_info["circulating"],
                "collateral_token": base_info["collateral_token"],
                "note":             base_info["note"],
            }

    if result["data_source"] == "baseline":
        result["system_health"] = "unknown"

    return result


# ─── Main Scan Orchestrator ───────────────────────────────────────────────────

def run_flare_scan() -> ScanResult:
    """
    Run a complete scan of all Flare DeFi protocols.
    Returns a ScanResult with all data normalised and ready for the models.
    """
    start = time.time()
    warnings = []

    logger.info("Starting Flare network scan (parallel fetch)...")

    # Pre-warm caches in parallel so main threads reuse data without redundant fetches.
    # DeFiLlama (~2s) and GeckoTerminal pre-warm (~4s) are independent and can overlap.
    with ThreadPoolExecutor(max_workers=2) as _warmup:
        _f_dl = _warmup.submit(_fetch_defillama_raw)
        _f_gt = _warmup.submit(_prewarm_gt_cache)
        _f_dl.result()
        _f_gt.result()

    _fetch_map = {
        "prices":    fetch_prices,
        "ftso":      fetch_ftso_prices,   # Upgrade #3: FTSO oracle prices
        "blazeswap": fetch_blazeswap_pools,
        "sparkdex":  fetch_sparkdex_pools,
        "enosys":    fetch_enosys_pools,
        "kinetic":   fetch_kinetic_rates,
        "clearpool": fetch_clearpool_rates,
        "mystic":    fetch_mystic_rates,
        "staking":   fetch_staking_yields,
        "cyclo":     fetch_cyclo_rates,
    }
    raw: dict = {}
    with ThreadPoolExecutor(max_workers=8) as _pool:
        future_to_key = {_pool.submit(fn): key for key, fn in _fetch_map.items()}
        for future in as_completed(future_to_key):
            key = future_to_key[future]
            try:
                raw[key] = future.result()
            except Exception as _e:
                logger.error(f"Parallel fetch failed for '{key}': {_e}")
                raw[key] = []

    prices      = raw.get("prices", [])
    ftso_prices = raw.get("ftso", {}) if isinstance(raw.get("ftso"), dict) else {}
    pools   = raw.get("blazeswap", []) + raw.get("sparkdex", []) + raw.get("enosys", [])
    lending = raw.get("kinetic", [])   + raw.get("clearpool", []) + raw.get("mystic", [])
    staking = raw.get("staking", [])   + raw.get("cyclo", [])

    # Flag non-live data points so users know which values may be stale
    non_live = [p for p in pools + lending + staking if p.data_source != "live"]
    baseline_count  = sum(1 for p in non_live if p.data_source in ("baseline", "estimate"))
    research_count  = sum(1 for p in non_live if p.data_source == "research")
    if baseline_count:
        warnings.append(
            f"{baseline_count} data point(s) using hardcoded baselines "
            f"(live API unavailable). Values may not reflect current market."
        )
    if research_count:
        warnings.append(
            f"{research_count} data point(s) using research estimates "
            f"(no live API available for these protocols yet)."
        )

    duration = round(time.time() - start, 2)
    logger.info(f"Flare scan complete in {duration}s — "
                f"{len(pools)} pools, {len(lending)} lending rates, {len(staking)} staking yields")

    return ScanResult(
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        prices=[asdict(p) for p in prices],
        pools=[asdict(p) for p in pools],
        lending=[asdict(p) for p in lending],
        staking=[asdict(p) for p in staking],
        scan_duration=duration,
        warnings=warnings,
        ftso_prices=ftso_prices,   # Upgrade #3: {symbol: ftso_price_usd}
    )

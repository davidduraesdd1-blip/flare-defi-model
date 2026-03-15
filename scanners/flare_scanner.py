"""
Flare Network Scanner
Fetches live data from all Flare DeFi protocols.
Falls back to baseline research data when live APIs are unavailable.
"""

import requests
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

from web3 import Web3

from config import APIS, PROTOCOLS, TOKENS, FLARE_RPC_URLS

logger = logging.getLogger(__name__)

# ─── Baseline Token Prices (for TVL calculation when live prices are unavailable) ─
# Used only in Kinetic TVL calc to convert token amounts to USD.
# Stablecoins use $1; volatile assets use conservative research estimates.
_BASELINE_TOKEN_PRICES = {
    "FLR":    0.020,
    "WFLR":   0.020,
    "sFLR":   0.020,
    "wETH":   2500.0,
    "USDT0":  1.0,
    "USDC.e": 1.0,
    "USDT":   1.0,
}

# ─── Web3 / On-chain Helpers ──────────────────────────────────────────────────

# Flare C-chain targets ~2-second blocks → ~15.78 M blocks/year
_FLARE_BLOCKS_PER_YEAR = 15_778_800

# Minimal ABI for Compound V2-style kToken contracts
_KTOKEN_ABI = [
    {"inputs": [], "name": "supplyRatePerBlock",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "borrowRatePerBlock",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "getCash",             "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "totalBorrows",        "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "exchangeRateStored",  "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
    {"inputs": [], "name": "totalSupply",         "outputs": [{"type": "uint256"}], "stateMutability": "view",      "type": "function"},
]

def _get_web3() -> Optional[Web3]:
    """Return a connected Web3 instance, trying each RPC URL in order."""
    for url in FLARE_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
            if w3.is_connected():
                return w3
        except Exception:
            continue
    return None

def _rate_to_apy(rate_per_block: int) -> float:
    """Convert Compound-style rate-per-block (1e18 mantissa) to annualised APY %."""
    r = rate_per_block / 1e18
    return round(((1 + r) ** _FLARE_BLOCKS_PER_YEAR - 1) * 100, 2)

# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class PoolData:
    protocol:    str
    pool_name:   str
    apr:         float          # annualised yield %
    tvl_usd:     float
    token0:      str
    token1:      str
    il_risk:     str            # low / medium / high
    reward_token: str
    data_source: str            # "live" or "baseline"
    fetched_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class LendingRate:
    protocol:    str
    asset:       str
    supply_apy:  float
    borrow_apy:  float
    utilisation: float          # 0–1
    tvl_usd:     float
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class StakingYield:
    protocol:    str
    token:       str
    apy:         float
    apy_low:     float
    apy_high:    float
    tvl_usd:     float
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class TokenPrice:
    symbol:      str
    price_usd:   float
    change_24h:  float          # %
    data_source: str
    fetched_at:  str = field(default_factory=lambda: datetime.utcnow().isoformat())

@dataclass
class ScanResult:
    timestamp:     str
    prices:        list
    pools:         list
    lending:       list
    staking:       list
    scan_duration: float        # seconds
    warnings:      list = field(default_factory=list)

# ─── HTTP Helper ──────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, timeout: int = 10, retries: int = 1) -> Optional[dict]:
    """Safe GET with timeout, error swallowing, and one automatic retry."""
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            logger.debug(f"GET {url} failed after {retries + 1} attempt(s): {e}")
            return None

def _post(url: str, payload: dict, timeout: int = 10, retries: int = 1) -> Optional[dict]:
    """Safe POST for GraphQL queries with one automatic retry."""
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            logger.debug(f"POST {url} failed after {retries + 1} attempt(s): {e}")
            return None

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
        # Fallback: last known reasonable estimates
        logger.warning("CoinGecko unavailable — using price estimates")
        results = [
            TokenPrice("FLR",  0.020, 0.0, "estimate"),
            TokenPrice("XRP",  2.20,  0.0, "estimate"),
            TokenPrice("FXRP", 2.195, 0.0, "estimate"),
            TokenPrice("USD0", 1.00,  0.0, "estimate"),
        ]

    _price_cache    = results
    _price_cache_ts = time.time()
    return results

# ─── DEX Pool Fallback Helper ─────────────────────────────────────────────────

def _baseline_pools(protocol_key: str) -> list:
    """Return config baseline pools when the subgraph API is unavailable."""
    logger.warning(f"{PROTOCOLS[protocol_key]['name']} subgraph unavailable — using baseline data")
    pools = []
    for name, cfg in PROTOCOLS[protocol_key]["pools"].items():
        t0, t1 = name.split("-")
        pools.append(PoolData(
            protocol=protocol_key,
            pool_name=name,
            apr=cfg["baseline_apr"],
            tvl_usd=0,
            token0=t0,
            token1=t1,
            il_risk=cfg["il_risk"],
            reward_token=cfg.get("reward_token", ""),
            data_source="baseline",
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
    volumeUSD
    token0Price
    token1Price
  }
}
"""

def fetch_blazeswap_pools() -> list:
    data = _post(APIS["blazeswap_graph"], {"query": _BLAZESWAP_POOLS_QUERY})
    pools = []

    if data and "data" in data and "pairs" in data["data"]:
        for pair in data["data"]["pairs"]:
            t0 = pair["token0"]["symbol"]
            t1 = pair["token1"]["symbol"]
            name = f"{t0}-{t1}"
            tvl  = float(pair.get("reserveUSD", 0))

            # APR estimate from volume: (7-day vol * 0.003 * 52) / TVL
            vol = float(pair.get("volumeUSD", 0))
            fee_apr = (vol * 0.003 * 52 / tvl * 100) if tvl > 0 else 0

            # Look up reward APR from config baseline
            cfg_pools = PROTOCOLS["blazeswap"]["pools"]
            cfg_key   = f"{t0}-{t1}" if f"{t0}-{t1}" in cfg_pools else f"{t1}-{t0}"
            baseline  = cfg_pools.get(cfg_key, {})
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
            ))
    else:
        pools = _baseline_pools("blazeswap")

    return pools

# ─── SparkDEX Pool Scanner ────────────────────────────────────────────────────

_SPARKDEX_POOLS_QUERY = """
{
  pools(first: 10, orderBy: totalValueLockedUSD, orderDirection: desc) {
    id
    token0 { symbol }
    token1 { symbol }
    totalValueLockedUSD
    volumeUSD
    feeTier
  }
}
"""

def fetch_sparkdex_pools() -> list:
    data = _post(APIS["sparkdex_graph"], {"query": _SPARKDEX_POOLS_QUERY})
    pools = []

    if data and "data" in data and "pools" in data["data"]:
        for pool in data["data"]["pools"]:
            t0  = pool["token0"]["symbol"]
            t1  = pool["token1"]["symbol"]
            tvl = float(pool.get("totalValueLockedUSD", 0))
            vol = float(pool.get("volumeUSD", 0))
            fee = int(pool.get("feeTier", 3000)) / 1_000_000

            fee_apr = (vol * fee * 52 / tvl * 100) if tvl > 0 else 0
            name = f"{t0}-{t1}"
            cfg_key = name if name in PROTOCOLS["sparkdex"]["pools"] else f"{t1}-{t0}"
            baseline = PROTOCOLS["sparkdex"]["pools"].get(cfg_key, {})
            total_apr = max(fee_apr, baseline.get("baseline_apr", fee_apr))

            pools.append(PoolData(
                protocol="sparkdex",
                pool_name=name,
                apr=round(total_apr, 2),
                tvl_usd=round(tvl, 0),
                token0=t0,
                token1=t1,
                il_risk=baseline.get("il_risk", "medium"),
                reward_token=baseline.get("reward_token", "SPRK"),
                data_source="live",
            ))
    else:
        pools = _baseline_pools("sparkdex")

    return pools

# ─── Enosys Pool Scanner ──────────────────────────────────────────────────────

def fetch_enosys_pools() -> list:
    data = _post(APIS["enosys_graph"], {"query": _SPARKDEX_POOLS_QUERY})
    pools = []

    if data and "data" in data and "pools" in data["data"]:
        for pool in data["data"]["pools"]:
            t0  = pool["token0"]["symbol"]
            t1  = pool["token1"]["symbol"]
            tvl = float(pool.get("totalValueLockedUSD", 0))
            vol = float(pool.get("volumeUSD", 0))
            fee = int(pool.get("feeTier", 500)) / 1_000_000

            fee_apr = (vol * fee * 52 / tvl * 100) if tvl > 0 else 0
            name = f"{t0}-{t1}"
            cfg_key = name if name in PROTOCOLS["enosys"]["pools"] else f"{t1}-{t0}"
            baseline = PROTOCOLS["enosys"]["pools"].get(cfg_key, {})
            total_apr = max(fee_apr, baseline.get("baseline_apr", fee_apr))

            pools.append(PoolData(
                protocol="enosys",
                pool_name=name,
                apr=round(total_apr, 2),
                tvl_usd=round(tvl, 0),
                token0=t0,
                token1=t1,
                il_risk=baseline.get("il_risk", "low"),
                reward_token=baseline.get("reward_token", "RFLR"),
                data_source="live",
            ))
    else:
        pools = _baseline_pools("enosys")

    return pools

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
            token_amount = (cash + total_borrows) / (10 ** underlying_decimals)
            token_price  = _BASELINE_TOKEN_PRICES.get(asset, 1.0)
            tvl_usd = round(token_amount * token_price, 2)

        except Exception as e:
            logger.debug(f"Kinetic on-chain fetch failed for {asset}: {e} — using baseline")
            supply_apy  = cfg["baseline_supply"]
            borrow_apy  = cfg["baseline_borrow"]
            utilisation = 0.0   # unknown when using baseline; do not fabricate a value
            tvl_usd     = PROTOCOLS["kinetic"]["tvl_usd"] / len(k_tokens)
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
    rates = []
    for pool_name, cfg in PROTOCOLS["clearpool"]["pools"].items():
        rates.append(LendingRate(
            protocol="clearpool",
            asset=cfg["asset"],
            supply_apy=cfg["apr"],
            borrow_apy=0,           # Clearpool is lender-side only
            utilisation=0.0,        # no live data — unknown, do not fabricate
            tvl_usd=PROTOCOLS["clearpool"]["tvl_usd"] / 2,
            data_source="baseline",
        ))
    return rates

# ─── Mystic (Morpho) Rates Scanner ───────────────────────────────────────────

def fetch_mystic_rates() -> list:
    rates = []
    for vault_name, cfg in PROTOCOLS["mystic"]["vaults"].items():
        rates.append(LendingRate(
            protocol="mystic",
            asset=cfg["asset"],
            supply_apy=cfg["supply_apy"],
            borrow_apy=0,
            utilisation=0.0,        # no live data — unknown, do not fabricate
            tvl_usd=0,
            data_source="baseline",
        ))
    return rates

# ─── Staking Yields ───────────────────────────────────────────────────────────

def fetch_staking_yields() -> list:
    yields = []

    # sFLR via Sceptre
    yields.append(StakingYield(
        protocol="sceptre",
        token="sFLR",
        apy=9.0,        # midpoint of 7–11%
        apy_low=7.0,
        apy_high=11.0,
        tvl_usd=0,
        data_source="baseline",
    ))

    # stXRP via Firelight
    yields.append(StakingYield(
        protocol="firelight",
        token="stXRP",
        apy=5.0,        # Phase 2 estimate
        apy_low=4.0,
        apy_high=7.0,
        tvl_usd=0,
        data_source="baseline",
    ))

    # Spectra sFLR fixed-rate market (PT)
    yields.append(StakingYield(
        protocol="spectra",
        token="PT-sFLR",
        apy=10.79,
        apy_low=10.79,
        apy_high=19.59,
        tvl_usd=291_762,
        data_source="research",
    ))

    # Spectra sFLR LP market
    yields.append(StakingYield(
        protocol="spectra",
        token="LP-sFLR",
        apy=36.74,
        apy_low=30.0,
        apy_high=45.0,
        tvl_usd=291_762,
        data_source="research",
    ))

    # Upshift earnXRP
    yields.append(StakingYield(
        protocol="upshift",
        token="earnXRP",
        apy=7.0,        # midpoint of 4–10%
        apy_low=4.0,
        apy_high=10.0,
        tvl_usd=33_900_000,
        data_source="research",
    ))

    return yields

# ─── Main Scan Orchestrator ───────────────────────────────────────────────────

def run_flare_scan() -> ScanResult:
    """
    Run a complete scan of all Flare DeFi protocols.
    Returns a ScanResult with all data normalised and ready for the models.
    """
    start = time.time()
    warnings = []

    logger.info("Starting Flare network scan (parallel fetch)...")

    _fetch_map = {
        "prices":    fetch_prices,
        "blazeswap": fetch_blazeswap_pools,
        "sparkdex":  fetch_sparkdex_pools,
        "enosys":    fetch_enosys_pools,
        "kinetic":   fetch_kinetic_rates,
        "clearpool": fetch_clearpool_rates,
        "mystic":    fetch_mystic_rates,
        "staking":   fetch_staking_yields,
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

    prices  = raw.get("prices", [])
    pools   = raw.get("blazeswap", []) + raw.get("sparkdex", []) + raw.get("enosys", [])
    lending = raw.get("kinetic", [])   + raw.get("clearpool", []) + raw.get("mystic", [])
    staking = raw.get("staking", [])

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
        timestamp=datetime.utcnow().isoformat(),
        prices=[asdict(p) for p in prices],
        pools=[asdict(p) for p in pools],
        lending=[asdict(p) for p in lending],
        staking=[asdict(p) for p in staking],
        scan_duration=duration,
        warnings=warnings,
    )

"""
Central configuration for the Flare DeFi Model.
All protocol data, risk profiles, API endpoints, and yield baselines live here.
Update this file when new protocols launch or yields change.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
_DATA_DIR_PREFERRED = BASE_DIR / "data"
try:
    _DATA_DIR_PREFERRED.mkdir(exist_ok=True)
    # Verify the directory is actually writable (Streamlit Cloud mounts are read-only)
    _write_test = _DATA_DIR_PREFERRED / ".write_test"
    _write_test.touch()
    _write_test.unlink()
    DATA_DIR = _DATA_DIR_PREFERRED
except (PermissionError, OSError):
    # Streamlit Cloud: /mount/src is a read-only git mount — use /tmp instead
    DATA_DIR = Path("/tmp/defi_data")
    DATA_DIR.mkdir(exist_ok=True, parents=True)

HISTORY_FILE        = DATA_DIR / "history.json"
POSITIONS_FILE      = DATA_DIR / "positions.json"
WALLETS_FILE        = DATA_DIR / "wallets.json"
QUICK_CACHE_FILE    = DATA_DIR / "quick_check_cache.json"
MONITOR_DIGEST_FILE = DATA_DIR / "monitor_digest.json"
DB_FILE             = DATA_DIR / "defi_model.db"
AGENTKIT_WALLET_FILE = DATA_DIR / "agentkit_wallet.json"

# ─── Coinbase AgentKit (CDP) ──────────────────────────────────────────────────
# Get a free CDP API key at https://portal.cdp.coinbase.com/
# Then set these env vars (or add to .env):
#   CDP_API_KEY_NAME=<your key name>
#   CDP_API_KEY_PRIVATE_KEY=<your private key>
#   AGENTKIT_NETWORK=base-mainnet  (or flare-mainnet for Flare EVM)
CDP_API_KEY_NAME    = os.environ.get("CDP_API_KEY_NAME", "")
CDP_API_KEY_PRIVATE = os.environ.get("CDP_API_KEY_PRIVATE_KEY", "")
AGENTKIT_NETWORK    = os.environ.get("AGENTKIT_NETWORK", "base-mainnet")

# ─── Flare Network RPC ────────────────────────────────────────────────────────
FLARE_RPC_URLS = [
    "https://flare-api.flare.network/ext/C/rpc",   # Official
    "https://rpc.ankr.com/flare",                   # Ankr free tier
    "https://flare.public-rpc.com",                 # Public fallback
]
FLARE_CHAIN_ID  = 14
SONGBIRD_RPC    = "https://songbird-api.flare.network/ext/C/rpc"

# ─── Free Public API Endpoints ────────────────────────────────────────────────
APIS = {
    "flare_api_portal":  "https://api-portal.flare.network",
    "flare_explorer":    "https://flare-explorer.flare.network/api",
    "flaremetrics":      "https://flaremetrics.io",
    "coingecko":         "https://api.coingecko.com/api/v3",
    "blazeswap_graph":   "https://subgraph.blazeswap.xyz/subgraphs/name/blazeswap/exchange",
    # sparkdex_graph and enosys_graph (Goldsky) are no longer active — removed.
    # SparkDEX and Enosys data now comes from GeckoTerminal (live) only.
    "hyperliquid_info":  "https://api.hyperliquid.xyz/info",
    "ftso_data":         "https://flr-data-availability.flare.network",
}

# ─── Token Addresses (Flare Mainnet) ─────────────────────────────────────────
# Verified from Flare explorer and protocol documentation
TOKENS = {
    "WFLR":  "0x1D80c49BbBCd1C0911346656B529DF9E5c2F783d",
    "FXRP":  "0x1502FA4be69d526124D453619276FacCab275d3D",
    "USD0":  "0x4A771CA9f10fEf2F73f5dC99339e01FEE1dAF09e",  # USDT0 on Flare
    "sFLR":  "0x12e605bc104e93B45e1aD99F9e555f659051c2BB",
    "stFLR": "0x1a77D13B87B8e5F58cf9bCDaCae6d3CDdA4f4344",  # SparkDEX V4 stFLR (verify addr)
    "stXRP": "0xFcB23FA1d5b4652D0A0B48F0E42697D7Bca07A0c",  # Firelight stXRP
    "FBTC":  "",  # FAssets FBTC  — address TBD (not yet live on mainnet as of Apr 2026)
    "FDOGE": "",  # FAssets FDOGE — address TBD (beta, limited minting as of Apr 2026)
    "HLN":   "0x7D3c9C6566375d6F11D9B00b06A14eaF5a2f4e75",
}

# ─── Protocol Registry ────────────────────────────────────────────────────────
# Baseline yields from research (as of April 2026)
# These are used as fallbacks when live data is unavailable.
# "live": True means the scanner actively fetches this protocol's data.
PROTOCOLS = {
    "blazeswap": {
        "name":     "Blazeswap",
        "type":     "DEX",
        "url":      "https://app.blazeswap.xyz",
        "live":     True,
        "risk":     "medium",
        # baseline_apr = total research estimate (fee + reward); used by subgraph fallback path.
        # reward_apr   = RFLR incentive portion only (~80% of total); used by GeckoTerminal path
        #                where live fee APR is computed from 24h volume and added separately.
        "pools": {
            "WFLR-USD0":  {"baseline_apr": 133.0, "reward_apr": 106.0, "reward_token": "RFLR", "il_risk": "medium"},
            "FXRP-USD0":  {"baseline_apr": 142.0, "reward_apr": 114.0, "reward_token": "RFLR", "il_risk": "medium"},
            "sFLR-WFLR":  {"baseline_apr":  37.0, "reward_apr":  30.0, "reward_token": "RFLR", "il_risk": "low"},
            "WFLR-FXRP":  {"baseline_apr": 148.0, "reward_apr": 118.0, "reward_token": "RFLR", "il_risk": "high"},
            "HLN-FXRP":   {"baseline_apr": 168.0, "reward_apr": 134.0, "reward_token": "RFLR", "il_risk": "high"},
        },
    },
    "sparkdex": {
        "name":     "SparkDEX",
        "type":     "DEX + Perps",
        "url":      "https://sparkdex.ai",
        "live":     True,
        "risk":     "medium-high",
        # reward_apr = SPRK incentive portion only; fee APR added live from GeckoTerminal
        # Pools exist on V3.1 and/or V4; scanner deduplicates by highest TVL at runtime.
        "pools": {
            "FXRP-USDT0":    {"reward_apr": 12.0, "reward_token": "SPRK", "il_risk": "medium"},
            "USDT0-WFLR":    {"reward_apr": 10.0, "reward_token": "SPRK", "il_risk": "medium"},
            "USDC.e-WFLR":   {"reward_apr": 10.0, "reward_token": "SPRK", "il_risk": "medium"},
            "FXRP-WFLR":     {"reward_apr":  8.0, "reward_token": "SPRK", "il_risk": "medium"},
            "USDT0-USDC.e":  {"reward_apr":  3.0, "reward_token": "SPRK", "il_risk": "none"},
            "sFLR-WFLR":     {"reward_apr":  8.0, "reward_token": "SPRK", "il_risk": "low"},
            "stXRP-FXRP":    {"reward_apr":  8.0, "reward_token": "SPRK", "il_risk": "low"},   # V4 rate (higher of V3.1=6, V4=8)
            "flrETH-WETH":   {"reward_apr":  5.0, "reward_token": "SPRK", "il_risk": "low"},
            "WETH-USDT0":    {"reward_apr":  6.0, "reward_token": "SPRK", "il_risk": "medium"},
            "sFLR-flrETH":   {"reward_apr":  5.0, "reward_token": "SPRK", "il_risk": "low"},
            "stFLR-WFLR":    {"reward_apr":  8.0, "reward_token": "SPRK", "il_risk": "low"},
            "WETH-FXRP":     {"reward_apr":  5.0, "reward_token": "SPRK", "il_risk": "high"},
        },
        "perps": {
            "max_leverage": 100,
            "fxrp_max_leverage": 20,
            "funding_interval_hours": 8,
        },
    },
    "enosys": {
        "name":     "Enosys DEX",
        "type":     "DEX",
        "url":      "https://v3.dex.enosys.global",
        "live":     True,
        "risk":     "low-medium",
        # reward_apr = RFLR incentive portion only; fee APR added live from GeckoTerminal
        "pools": {
            "FXRP-WFLR":     {"reward_apr": 12.0, "reward_token": "RFLR", "il_risk": "medium"},
            "FXRP-USDT0":    {"reward_apr": 12.0, "reward_token": "RFLR", "il_risk": "medium"},
            "USDT0-WFLR":    {"reward_apr": 10.0, "reward_token": "RFLR", "il_risk": "medium"},
            "sFLR-WFLR":     {"reward_apr":  8.0, "reward_token": "RFLR", "il_risk": "low"},
            "stXRP-FXRP":    {"reward_apr":  6.0, "reward_token": "RFLR", "il_risk": "low"},
            "CDP-USDT0":     {"reward_apr":  5.0, "reward_token": "RFLR", "il_risk": "high"},
            "HLN-FXRP":      {"reward_apr":  5.0, "reward_token": "RFLR", "il_risk": "high"},
            "HLN-USDT0":     {"reward_apr":  5.0, "reward_token": "RFLR", "il_risk": "high"},
            "HLN-WFLR":      {"reward_apr":  5.0, "reward_token": "RFLR", "il_risk": "high"},
            "USDT0-APS":     {"reward_apr":  4.0, "reward_token": "RFLR", "il_risk": "high"},
            "FXRP-APS":      {"reward_apr":  4.0, "reward_token": "RFLR", "il_risk": "high"},
        },
        "daily_rflr_incentives": 333333,
    },
    "kinetic": {
        "name":     "Kinetic",
        "type":     "Lending",
        "url":      "https://app.kinetic.market",
        "live":     True,
        "risk":     "low",
        # On-chain contract addresses (Flare mainnet) — verified from docs.kinetic.market
        "comptroller": "0xeC7e541375D70c37262f619162502dB9131d6db5",
        # kTokens: each entry is {address, underlying_decimals, baseline_supply_apr, baseline_borrow_apr}
        # baseline values used only when RPC is unavailable
        "kTokens": {
            "FLR":    {"address": "0xb84F771305d10607Dd086B2f89712c0CeD379407", "decimals": 18, "baseline_supply": 6.0,  "baseline_borrow": 10.0},
            "sFLR":   {"address": "0x291487beC339c2fE5D83DD45F0a15EFC9Ac45656", "decimals": 18, "baseline_supply": 5.0,  "baseline_borrow":  8.0},
            "USDT0":  {"address": "0x76809aBd690B77488Ffb5277e0a8300a7e77B779", "decimals":  6, "baseline_supply": 8.0,  "baseline_borrow": 12.0},
            "USDC.e": {"address": "0xDEeBaBe05BDA7e8C1740873abF715f16164C29B8", "decimals":  6, "baseline_supply": 7.0,  "baseline_borrow": 11.0},
            "USDT":   {"address": "0x1e5bBC19E0B17D7d38F318C79401B3D16F2b93bb", "decimals":  6, "baseline_supply": 7.0,  "baseline_borrow": 11.0},
            "wETH":   {"address": "0x5C2400019017AE61F811D517D088Df732642DbD0", "decimals": 18, "baseline_supply": 3.0,  "baseline_borrow":  5.0},
        },
        "tvl_usd": 64_000_000,   # fallback estimate; actual TVL read from chain
    },
    "clearpool": {
        "name":     "Clearpool",
        "type":     "Lending",
        "url":      "https://clearpool.finance",
        "live":     True,
        "risk":     "low",
        "pools": {
            "T-Pool":    {"apr": 3.5,  "asset": "USD0",  "strategy": "treasury"},
            "X-Pool":    {"apr": 11.5, "asset": "USD0",  "strategy": "arb+tbill"},
            "USDX-Pool": {"apr": 9.1,  "asset": "USDX",  "strategy": "t-bill",   "tvl_usd": 38_000_000},
            "RLUSD-Pool": {"apr": 5.0, "asset": "RLUSD", "strategy": "rlusd_tbill", "tvl_usd": 5_000_000, "note": "Ripple USD — regulated stablecoin pool (launched 2026)"},
        },
        "tvl_usd": 46_000_000,   # updated Apr 2026 to include RLUSD pool
    },
    "spectra": {
        "name":     "Spectra Finance",
        "type":     "Yield Tokenization",
        "url":      "https://app.spectra.finance",
        "live":     True,
        "risk":     "low-medium",
        "markets": {
            "sFLR-MAY2026": {
                "fixed_apy":    18.6,    # Updated Apr 2026 — was 10.79, now ~18.6–19.59% Max APY
                "lp_apy":       36.74,
                "lp_fees_apy":   0.75,
                "lp_rewards_apy": 8.43,
                "maturity":     "2026-05-17",
                "asset":        "sFLR",
            },
        },
    },
    "upshift": {
        "name":     "Upshift / EarnXRP",
        "type":     "Yield Vault",
        "url":      "https://app.upshift.finance",
        "live":     True,
        "risk":     "low",
        "vaults": {
            "earnXRP": {
                "target_apy_low":  4.0,
                "target_apy_high": 10.0,
                "asset":           "FXRP",
                "cap_fxrp":        25_000_000,
                "strategy":        "conc_liquidity + carry_trade",
            },
        },
    },
    "mystic": {
        "name":     "Mystic Finance (Morpho)",
        "type":     "Lending",
        "url":      "https://app.mysticfinance.xyz",
        "live":     True,
        "risk":     "low",
        "vaults": {
            "FXRP-vault":  {"supply_apy": 5.0,  "asset": "FXRP"},
            "FLR-vault":   {"supply_apy": 7.0,  "asset": "WFLR"},
            "USD0-vault":  {"supply_apy": 9.0,  "asset": "USD0"},
        },
    },
    "cyclo": {
        "name":     "Cyclo Finance",
        "type":     "Leveraged Yield",
        "url":      "https://cyclo.finance",
        "live":     True,
        "risk":     "high",
        "mechanism": "sFLR → cysFLR (1:1), cysFLR trades $0–$1 vs $1 sFLR value",
        "yield_sources": ["FTSO delegation", "FLR staking", "rFLR incentives"],
        "liquidation_free": True,
    },
    "enosys_loans": {
        "name":     "Enosys Loans",
        "type":     "CDP / Stablecoin",
        "url":      "https://loans.enosys.global",
        "live":     False,   # No subgraph available yet; monitor manually
        "risk":     "medium",
        "mechanism": "Deposit FXRP as collateral → mint CDP stablecoin at low borrow rate",
        "note":     "First XRP-backed CDP stablecoin on Flare. Launched early 2026.",
    },
    "firelight": {
        "name":     "Firelight Finance",
        "type":     "Liquid Staking",
        "url":      "https://app.firelight.finance",
        "live":     True,
        "risk":     "low",
        "tokens": {
            "stXRP": {
                "peg":             "1:1 FXRP",
                "unstake_days":    2,
                "phase":          "Phase 1 (points)",
                "phase2_apy_est":  5.0,
            },
        },
    },
    "sceptre": {
        "name":     "Sceptre (sFLR)",
        "type":     "Liquid Staking",
        "url":      "https://sceptre.fi",
        "live":     True,
        "risk":     "low",
        "tokens": {
            "sFLR": {
                "base_apy_low":  4.0,    # Reduced post-FlareDrop (ended Jan 30 2026); was 7–11%
                "base_apy_high": 5.0,
                "sources":       ["FTSO delegation", "FLR staking"],
            },
        },
    },
    "hyperliquid": {
        "name":     "Hyperliquid",
        "type":     "Perps (Cross-chain)",
        "url":      "https://app.hyperliquid.xyz",
        "live":     True,
        "risk":     "high",
        "pairs": ["FXRP/USDC", "FXRP/USDH", "HYPE/USDC"],   # FXRP/USDH added Jan 2026; HYPE native token added Q1 2026
    },
    "flamix": {
        "name":     "Flamix",
        "type":     "Perp DEX",
        "url":      "https://flamix.trade",
        "live":     False,   # No public API yet; data manually monitored
        "risk":     "high",
        "note":     "Native perpetuals DEX on Flare. Up to 500x leverage. Any asset as collateral. FTSO pricing. $100M+ volume as of Q4 2025.",
        "pairs": {
            "FLR-USD":  {"max_leverage": 500, "reward_token": "FLMX"},
            "XRP-USD":  {"max_leverage": 500, "reward_token": "FLMX"},
            "BTC-USD":  {"max_leverage": 500, "reward_token": "FLMX"},
            "ETH-USD":  {"max_leverage": 500, "reward_token": "FLMX"},
        },
        "volume_30d_usd": 100_000_000,
        "open_interest_usd": 1_500_000,
    },
    "kinza": {
        "name":     "Kinza Finance",
        "type":     "Lending",
        "url":      "https://app.kinza.finance",
        "live":     False,   # Aave V3 fork on Flare — baseline data only until live API confirmed
        "risk":     "low",
        "note":     "Aave V3-based lending market on Flare Network. Supports FXRP, WFLR, USDT0, USDC.e as collateral.",
        "markets": {
            "FXRP":   {"supply_apy": 4.5,  "borrow_apy":  7.0},
            "WFLR":   {"supply_apy": 5.0,  "borrow_apy":  8.5},
            "USDT0":  {"supply_apy": 7.5,  "borrow_apy": 11.0},
            "USDC.e": {"supply_apy": 6.5,  "borrow_apy": 10.0},
        },
    },
    "orbitalx": {
        "name":     "OrbitalX",
        "type":     "DEX",
        "url":      "https://orbitalx.xyz",
        "live":     False,   # Order book DEX on Flare — monitoring until public API is available
        "risk":     "medium",
        "note":     "Order book DEX on Flare Network with FTSO price feeds. Spot and perpetuals trading.",
    },
}

# ─── Fallback Prices (last-resort hardcoded values) ─────────────────────────
# Updated conservatively whenever prices move >20%. These are used only when
# the full cascade (CMC → CoinGecko → OKX) fails.
FALLBACK_PRICES = {
    # Flare ecosystem
    "FLR":   0.0076,  # updated 2026-04-15 (live: ~$0.008)
    "FXRP":  1.317,   # XRP * 0.998 bridge discount — kept in sync with XRP below
    "USD0":  1.00,
    "RLUSD": 1.00,    # Ripple USD — regulated stablecoin
    "SPRK":  0.05,    # SparkDEX token
    "HYPE":  15.00,   # Hyperliquid native token
    # 7 must-have coins (CLAUDE.md §13) — updated 2026-04-15
    "XRP":   2.07,
    "XLM":   0.28,
    "XDC":   0.075,
    "CC":    0.18,    # Canton Network (Bybit: CC/USDT)
    "HBAR":  0.18,    # Hedera
    "SHX":   0.012,   # Stronghold
    "ZBCN":  0.018,   # Zebec Network
}

# ─── Live Price Refresher: CMC → CoinGecko → OKX cascade ────────────────────
def refresh_fallback_prices(timeout: float = 4.0) -> bool:
    """
    Fetch live prices via a 3-tier cascade and update FALLBACK_PRICES in-place.

    Tier 1 — CoinMarketCap (primary, broadest coverage for must-have coins)
    Tier 2 — CoinGecko     (secondary, fills any gaps CMC missed or if no CMC key)
    Tier 3 — OKX REST      (tertiary, no auth required, catches remaining gaps)

    Updates the dict in-place so all modules that imported FALLBACK_PRICES
    by reference see new prices immediately.
    Returns True if at least one price was refreshed.
    """
    import requests as _rq
    _updated = 0

    # ── Tier 1: CoinMarketCap ────────────────────────────────────────────────
    # Uses symbol-based lookup. Note: CMC returns a dict keyed by symbol when
    # symbol is unique; when ambiguous it returns a list — we take the first item.
    _CMC_SYMBOLS = {
        "FLR": "FLR", "XRP": "XRP", "XLM": "XLM", "XDC": "XDC",
        "CC": "CC", "HBAR": "HBAR", "SHX": "SHX", "ZBCN": "ZBCN",
        "HYPE": "HYPE", "SPRK": "SPRK",
    }
    _fetched_from_cmc: set = set()
    if COINMARKETCAP_API_KEY:
        try:
            _sym_str = ",".join(_CMC_SYMBOLS.keys())
            _resp = _rq.get(
                "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest",
                params={"symbol": _sym_str, "convert": "USD"},
                headers={"X-CMC_PRO_API_KEY": COINMARKETCAP_API_KEY,
                         "Accept": "application/json"},
                timeout=timeout,
            )
            if _resp.status_code == 200:
                _raw = _resp.json().get("data", {})
                for _sym, _map_sym in _CMC_SYMBOLS.items():
                    _entry = _raw.get(_sym)
                    if _entry is None:
                        continue
                    # CMC returns either a dict or a list; take first if list
                    if isinstance(_entry, list):
                        _entry = _entry[0] if _entry else {}
                    _px = (_entry.get("quote", {}).get("USD", {}).get("price") or 0)
                    if _px and float(_px) > 0:
                        FALLBACK_PRICES[_map_sym] = round(float(_px), 6)
                        _fetched_from_cmc.add(_map_sym)
                        _updated += 1
        except Exception:
            pass

    # ── Tier 2: CoinGecko ────────────────────────────────────────────────────
    # Fetch everything; CMC-sourced coins get overwritten only if CG succeeds,
    # so we always try CG even when CMC had results (CG is good cross-check).
    _CG_IDS = {
        "flare-networks":   "FLR",
        "ripple":           "XRP",
        "stellar":          "XLM",
        "xdce-crowd-sale":  "XDC",
        "canton":           "CC",
        "hedera-hashgraph": "HBAR",
        "stronghold-token": "SHX",
        "zebec-protocol":   "ZBCN",
        "sparkdex-ai":      "SPRK",
        "hyperliquid":      "HYPE",
    }
    # Only call CG if CMC missed at least some coins or no CMC key
    _cg_needed = [_cg for _cg, _sym in _CG_IDS.items()
                  if _sym not in _fetched_from_cmc]
    if _cg_needed:
        try:
            _url = APIS.get("coingecko", "https://api.coingecko.com/api/v3") + "/simple/price"
            _headers: dict = {}
            if COINGECKO_API_KEY:
                _hdr_key = ("x-cg-demo-api-key" if COINGECKO_API_KEY.startswith("CG-")
                            else "x-cg-pro-api-key")
                _headers[_hdr_key] = COINGECKO_API_KEY
            _resp = _rq.get(
                _url,
                params={"ids": ",".join(_cg_needed), "vs_currencies": "usd"},
                headers=_headers,
                timeout=timeout,
            )
            if _resp.status_code == 200:
                _data = _resp.json()
                for _cg_id, _sym in _CG_IDS.items():
                    if _cg_id in _data:
                        _px = _data[_cg_id].get("usd", 0)
                        if _px and float(_px) > 0:
                            FALLBACK_PRICES[_sym] = round(float(_px), 6)
                            _updated += 1
        except Exception:
            pass

    # ── Tier 3: OKX REST (no auth, spot last-price) ──────────────────────────
    # Only for coins still missing or at hardcoded zero after Tiers 1+2.
    _OKX_PAIRS = {
        "CC-USDT":   "CC",
        "XDC-USDT":  "XDC",
        "HBAR-USDT": "HBAR",
        "XLM-USDT":  "XLM",
        "XRP-USDT":  "XRP",
        "SHX-USDT":  "SHX",
    }
    _still_zero = [_pair for _pair, _sym in _OKX_PAIRS.items()
                   if FALLBACK_PRICES.get(_sym, 0) == 0]
    if _still_zero:
        try:
            for _pair in _still_zero:
                _sym = _OKX_PAIRS[_pair]
                _resp = _rq.get(
                    "https://www.okx.com/api/v5/market/ticker",
                    params={"instId": _pair},
                    timeout=timeout,
                )
                if _resp.status_code == 200:
                    _tickers = _resp.json().get("data", [])
                    if _tickers:
                        _px = float(_tickers[0].get("last", 0) or 0)
                        if _px > 0:
                            FALLBACK_PRICES[_sym] = round(_px, 6)
                            _updated += 1
        except Exception:
            pass

    # ── Derived price: FXRP tracks XRP with bridge discount ──────────────────
    if "XRP" in FALLBACK_PRICES and FALLBACK_PRICES["XRP"] > 0:
        FALLBACK_PRICES["FXRP"] = round(FALLBACK_PRICES["XRP"] * 0.998, 6)

    return _updated > 0


# ─── Model Parameters ─────────────────────────────────────────────────────────
RISK_FREE_RATE    = 0.045   # 4.5% risk-free (US 10-year T-bill proxy); update periodically
HISTORY_MAX_RUNS  = 14      # keep ~7 days of history at 2 scans/day; older runs archived
ACCURACY_LOOKBACK_DAYS = 30 # rolling window for AI accuracy scoring
MAX_KELLY_FRACTION = 0.10   # hard cap on Kelly criterion position size (safety margin)

# ─── Scheduler ────────────────────────────────────────────────────────────────
SCHEDULER = {
    "run_times": ["00:00", "06:00", "12:00", "18:00"],   # 4x/day every 6 hours
    "timezone":  (os.environ.get("SCHEDULER_TZ") or "America/Denver").strip(),  # override via env var
    "quick_check_interval_hours": 3,   # lightweight intraday alert check
    "web_monitor_hour": 8,             # daily web monitor run time (local, 24h)
    "quick_check_thresholds": {
        "kinetic_utilization_spike": 0.90,   # alert if any asset utilization exceeds 90%
        "cross_dex_apr_gap_pct":     5.0,    # alert if same-pair APR diverges > 5% across DEXes
        "fassets_price_gap_pct":     1.0,    # alert if FXRP vs XRP spot gap exceeds 1%
        "price_change_pct":          8.0,    # alert if any major token moves > 8% since last check
        "funding_rate_annual_pct":   15.0,   # alert if Hyperliquid funding rate > 15% annualised
    },
}

# ─── Risk Profile Definitions ─────────────────────────────────────────────────
RISK_PROFILE_NAMES = ("conservative", "medium", "high")

RISK_PROFILES = {
    "conservative": {
        "label":           "Ultra Conservative",
        "color":           "#2ECC71",   # green
        "emoji":           "SAFE",
        # Realistic post-decay Monte Carlo P15/P85 targets (updated 2026-04-04)
        # Old static targets (15-40%) were aspirational pre-decay; these match actual achievable yield
        "target_apy_low":  10.0,
        "target_apy_high": 15.0,
        "max_il_risk":     "low",
        "leverage":        False,
        # IL multiplier: how aggressively IL is penalised in the allocation weight formula.
        # Conservative investors need maximum protection → 3.0x base IL estimate applied.
        "il_multiplier":   3.0,
        "allowed_types":   ["Lending", "Liquid Staking", "Yield Vault", "Yield Tokenization"],
        "allowed_protocols": [
            "sceptre", "kinetic", "clearpool", "upshift",
            "firelight", "spectra", "mystic", "kinza"
        ],
        "allowed_arb": ["lending_rate"],
        "description": (
            "Capital protection first. Uses only lending, staking, and fixed-rate vaults. "
            "Near-zero impermanent loss. Best for first-time DeFi users."
        ),
        "max_single_position_pct": 30,
    },
    "medium": {
        "label":           "Medium Risk",
        "color":           "#F39C12",   # orange
        "emoji":           "BALANCED",
        # Realistic post-decay Monte Carlo P15/P85 targets (updated 2026-04-04)
        "target_apy_low":  18.0,
        "target_apy_high": 30.0,
        "max_il_risk":     "medium",
        "leverage":        False,
        # IL multiplier: moderate sensitivity — accepts IL as cost of higher DEX yield
        "il_multiplier":   2.0,
        "allowed_types":   ["DEX", "Lending", "Liquid Staking", "Yield Vault", "Yield Tokenization", "DEX + Perps"],
        "allowed_protocols": [
            "sceptre", "kinetic", "clearpool", "upshift", "firelight",
            "spectra", "mystic", "blazeswap", "enosys", "sparkdex",
            "kinza", "orbitalx"
        ],
        "allowed_arb": ["lending_rate", "cross_dex", "fassets_mint_redeem", "funding_rate_neutral"],
        "description": (
            "Balanced growth. Adds LP pools and delta-neutral perpetual strategies. "
            "Moderate impermanent loss risk. Best for investors with some DeFi experience."
        ),
        "max_single_position_pct": 25,
    },
    "high": {
        "label":           "High Risk",
        "color":           "#E74C3C",   # red
        "emoji":           "AGGRESSIVE",
        # Realistic post-decay Monte Carlo P15/P85 targets (updated 2026-04-04)
        "target_apy_low":  22.0,
        "target_apy_high": 38.0,
        "max_il_risk":     "high",
        "leverage":        True,
        # IL multiplier: aggressive investors treat IL as acceptable cost of high-APY pools
        "il_multiplier":   1.5,
        "allowed_types":   ["DEX", "Lending", "Liquid Staking", "Yield Vault",
                            "Yield Tokenization", "DEX + Perps", "Leveraged Yield",
                            "Perps (Cross-chain)"],
        "allowed_protocols": [
            "sceptre", "kinetic", "clearpool", "upshift", "firelight",
            "spectra", "mystic", "blazeswap", "enosys", "sparkdex",
            "cyclo", "hyperliquid", "kinza", "orbitalx"
        ],
        "allowed_arb": [
            "lending_rate", "cross_dex", "fassets_mint_redeem",
            "funding_rate_neutral", "triangular", "ftso_oracle_window",
            "liquidation_snipe", "lp_intrinsic", "cyclo_cysflr"
        ],
        "description": (
            "Maximum yield potential. Uses high-APR LP pools, leveraged positions, "
            "and advanced arbitrage. High impermanent loss risk. For experienced users only."
        ),
        "max_single_position_pct": 20,
    },
}

# ─── Critical Alert: Incentive Program Expiry ─────────────────────────────────
# The FlareDrop (2.2B FLR airdrop to wFLR holders) ended January 30, 2026.
# DEX LP incentives (RFLR rewards) are a separate program still running until ~July 2026.
# sFLR staking yields have dropped significantly post-FlareDrop.
INCENTIVE_PROGRAM = {
    "total_flr":       2_200_000_000,
    "flaredrop_ended": "2026-01-30",   # FlareDrop monthly airdrop ended
    "expires":         "2026-07-01",   # DEX LP incentive (RFLR) program expected end
    "note": (
        "⚠️ RFLR incentive program expires July 1, 2026 (~82 days remaining — currently ~9% of program remaining). "
        "DEX LP pool APYs now reflect this decay. Base fee yields remain. "
        "Plan exit or rebalance strategy for LP positions before June 2026."
    ),
    # Reward display thresholds (QC — base/reward separation)
    "reward_hide_below_pct":  2.0,   # hide reward column when current reward APY < 2%
    "reward_warn_below_days": 90,    # show ⚠ warning badge when < 90 days remaining
    "reward_gray_below_days": 30,    # gray out reward display when < 30 days remaining
}

# ─── Impermanent Loss Thresholds ─────────────────────────────────────────────
IL_THRESHOLDS = {
    "low":    0.05,   # up to 5% IL acceptable
    "medium": 0.15,   # up to 15% IL acceptable
    "high":   0.50,   # up to 50% IL acceptable
}

# ─── Risk Letter Grade Map ────────────────────────────────────────────────────
# Maps our 0-10 risk score to an A-F letter grade + display color.
# Used in all opportunity tables and cards for instant beginner-friendly clarity.
# Matches Exponential.fi industry standard (A = safest, F = most risky).
RISK_GRADE_BANDS = [
    (2.0,  "A", "#22c55e"),   # 0.0–2.0 → A  (excellent — green)
    (3.5,  "B", "#10b981"),   # 2.0–3.5 → B  (good — teal)
    (5.0,  "C", "#f59e0b"),   # 3.5–5.0 → C  (average — amber)
    (7.0,  "D", "#f97316"),   # 5.0–7.0 → D  (below average — orange)
    (10.1, "F", "#ef4444"),   # 7.0+    → F  (high risk — red)
]

def risk_letter_grade(score: float) -> tuple[str, str]:
    """Return (letter, hex_color) for a 0-10 risk score."""
    for threshold, letter, color in RISK_GRADE_BANDS:
        if score < threshold:
            return letter, color
    return "F", "#ef4444"


# ─── Protocol Security Audits ────────────────────────────────────────────────
# Audit data sourced from protocol docs, Flare ecosystem announcements,
# and public audit reports (Apr 2026). Used for the audit shield badge.
PROTOCOL_AUDITS = {
    "kinetic": {
        "auditors":   ["Hacken"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Smart contract audit by Hacken — no critical issues found.",
    },
    "clearpool": {
        "auditors":   ["Zellic", "Coinspect"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Audited by Zellic and Coinspect. Institutional-grade credit protocol.",
    },
    "firelight": {
        "auditors":   ["OpenZeppelin", "Coinspect"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Audited by OpenZeppelin and Coinspect. Flare-native liquid staking.",
    },
    "spectra": {
        "auditors":   ["Spearbit", "Cantina", "Code4rena"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Multiple audits via Spearbit and competitive audit on Cantina/Code4rena.",
    },
    "blazeswap": {
        "auditors":   ["Solidified"],
        "year":       2023,
        "score":      "Passed",
        "note":       "Solidified audit. Based on Uniswap V2 architecture with FTSOv2 integration.",
    },
    "sparkdex": {
        "auditors":   ["Zellic"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Zellic audit of SparkDEX V3 contracts. Built on UniswapV3 base.",
    },
    "sceptre": {
        "auditors":   ["Flare Foundation", "External"],
        "year":       2023,
        "score":      "Passed",
        "note":       "Reviewed by Flare Foundation security team. sFLR liquid staking.",
    },
    "upshift": {
        "auditors":   ["Clearstar Labs"],
        "year":       2024,
        "score":      "Institutional",
        "note":       "Clearstar Labs institutional risk management. ERC-4626 vault architecture.",
    },
    "mystic": {
        "auditors":   ["Morpho Labs", "Spearbit"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Built on Morpho Protocol (Spearbit-audited). Mystic curators are separate.",
    },
    "enosys": {
        "auditors":   ["External"],
        "year":       2024,
        "score":      "Community",
        "note":       "Enosys smart contracts reviewed. Open-source Uniswap V3 fork on Flare.",
    },
    "kinza": {
        "auditors":   ["Certik", "Peckshield"],
        "year":       2024,
        "score":      "Passed",
        "note":       "Aave V3 fork — inherits Aave's extensive audit history (OpenZeppelin, Trail of Bits). Kinza-specific changes audited by Certik and Peckshield.",
    },
}


# ─── Protocol Dependency Graph ────────────────────────────────────────────────
# Maps each protocol to its shared underlying dependencies.
# Used to warn users when their recommended portfolio has correlated risk.
# If 2+ of a user's top allocations share a dependency, show a correlated-risk warning.
PROTOCOL_DEPENDENCIES = {
    "blazeswap":  {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": True},
    "sparkdex":   {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": True},
    "enosys":     {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": True},
    "kinetic":    {"fxrp_collateral": True,  "ftso_oracle": True,  "fxrp_liquidity": False},
    "clearpool":  {"fxrp_collateral": False, "ftso_oracle": False, "fxrp_liquidity": False},
    "spectra":    {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": False},
    "upshift":    {"fxrp_collateral": False, "ftso_oracle": False, "fxrp_liquidity": True},
    "mystic":     {"fxrp_collateral": True,  "ftso_oracle": True,  "fxrp_liquidity": False},
    "cyclo":      {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": False},
    "firelight":  {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": False},
    "sceptre":    {"fxrp_collateral": False, "ftso_oracle": True,  "fxrp_liquidity": False},
    "hyperliquid":{"fxrp_collateral": False, "ftso_oracle": False, "fxrp_liquidity": True},
    "enosys_loans":{"fxrp_collateral": True, "ftso_oracle": True,  "fxrp_liquidity": False},
    "kinza":      {"fxrp_collateral": True,  "ftso_oracle": True,  "fxrp_liquidity": False},
}

# ─── Your Current Positions (migrated from Excel) ────────────────────────────
# Edit positions.json to update — this is the starting seed
INITIAL_POSITIONS = [
    {
        "id":             35399,
        "protocol":       "blazeswap",
        "pool":           "WFLR-USD0",
        "liquidity_usd":  14790,
        "token0_balance": "231.16 WFLR",
        "token1_balance": "0.05441 USD0",
        "unclaimed_fees": 32.74,
        "rewards":        "11,640 RFLR",
        "current_value":  14816,
        # TODO: Replace with actual entry data from your records.
        # Using current value as baseline to prevent P&L calc errors.
        "entry_value":    14816,
        "entry_date":     "2025-09-01",   # estimated open date — update with real date
        "entry_apy":      133.0,          # BlazeSwap WFLR-USD0 baseline APR at entry
    },
    {
        "id":             36910,
        "protocol":       "blazeswap",
        "pool":           "FXRP-WFLR",
        "liquidity_usd":  1130,
        "token0_balance": "231.16 WFLR",
        "token1_balance": "0 FXRP",
        "unclaimed_fees": 0.04,
        "rewards":        "0.04 RFLR",
        "current_value":  1134,
        # TODO: Replace with actual entry data from your records.
        "entry_value":    1134,
        "entry_date":     "2026-01-01",   # estimated open date — update with real date
        "entry_apy":      148.0,          # BlazeSwap FXRP-WFLR baseline APR at entry
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE FLAGS — auto-enabled by API key presence (zero code changes needed)
# ─────────────────────────────────────────────────────────────────────────────

SENTRY_DSN: str | None = os.environ.get("DEFI_SENTRY_DSN")
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")

# ─── Anthropic / AI master switch ────────────────────────────────────────────
# Reads from ANTHROPIC_ENABLED env var if set; defaults to True so AI features
# are live when an API key is present. Set env var to "false" to disable.
ANTHROPIC_ENABLED: bool = os.environ.get("ANTHROPIC_ENABLED", "true").lower() not in ("false", "0", "no")
# Claude model IDs — single source of truth for all AI files in this app
CLAUDE_MODEL:       str = "claude-sonnet-4-6"
CLAUDE_HAIKU_MODEL: str = "claude-haiku-4-5-20251001"
COINGECKO_API_KEY: str | None = os.environ.get("DEFI_COINGECKO_API_KEY")
COINMETRICS_API_KEY: str | None = os.environ.get("DEFI_COINMETRICS_API_KEY")  # coinmetrics.io free community key
COINMARKETCAP_API_KEY: str | None = os.environ.get("DEFI_COINMARKETCAP_API_KEY")  # coinmarketcap.com — primary price source
DEFI_WEBHOOK_URL: str = os.environ.get("DEFI_WEBHOOK_URL", "")       # Discord / Telegram / generic webhook
DEFI_TELEGRAM_CHAT_ID: str = os.environ.get("DEFI_TELEGRAM_CHAT_ID", "")  # Telegram chat ID for webhook delivery

FEATURES: dict = {
    # Legacy keys — kept for backward compatibility
    "ai_analysis":      ANTHROPIC_ENABLED and bool(ANTHROPIC_API_KEY),
    "coingecko_pro":    bool(COINGECKO_API_KEY),
    "coinmetrics":      bool(COINMETRICS_API_KEY),
    "coinmarketcap":    bool(COINMARKETCAP_API_KEY),
    "cdp_agentkit":     bool(CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE),
    "sentry":           bool(SENTRY_DSN),
    "flare_rpc":        True,        # always available (public RPC)
    "hyperliquid":      True,        # free public API
    "defillama":        True,        # free public API
    "coingecko_free":   True,        # always available
    # Batch 8 feature flags — auto-enabled by API key presence AND master switch
    "anthropic_ai":     ANTHROPIC_ENABLED and bool(ANTHROPIC_API_KEY),
    "fred":             bool(os.environ.get("FRED_API_KEY", "")),
    "coinmetrics_pro":  bool(COINMETRICS_API_KEY),
    "zerion":           bool(os.environ.get("ZERION_API_KEY", "")),
    "web3":             False,       # updated at runtime after web3 import
    "demo_mode":        False,       # runtime flag
    "pro_mode":         False,       # runtime flag
}


def feature_enabled(name: str) -> bool:
    """Return True if the named feature is available."""
    return FEATURES.get(name, False)


# ─── SSRF Allowlist — only these domains can be fetched ──────────────────────
ALLOWED_DOMAINS: frozenset = frozenset({
    "api.coingecko.com",
    "pro-api.coingecko.com",
    "api.llama.fi",
    "yields.llama.fi",
    "api.alternative.me",
    "api.geckoterminal.com",
    "api.hyperliquid.xyz",
    "flare-api.flare.network",
    "rpc.ankr.com",
    "flare.public-rpc.com",
    "flare-explorer.flare.network",
    "api-portal.flare.network",
    "flaremetrics.io",
    "flr-data-availability.flare.network",
    "subgraph.blazeswap.xyz",
    "api.portals.fi",
    "api.clearpool.finance",
    "app.sceptre.fi",
    "api.kinetic.market",
    "api.upshift.fi",
    "api.coinmetrics.io",
    "community-api.coinmetrics.io",
    "ethena.fi",                    # Ethena sUSDe yield (#76)
    "hub.snapshot.org",             # Snapshot governance GraphQL (#74)
    "www.deribit.com",              # Deribit options chain (macro_feeds.py)
    "www.ether.fi",                 # ether.fi direct APY API (#71)
    "app.renzoprotocol.com",        # Renzo protocol points API (#71)
    "bridges.llama.fi",             # DeFiLlama bridge flows API (#85)
    "api.zerion.io",                # Zerion wallet portfolio API (#111)
    "api.telegram.org",             # Telegram bot API — webhook alert delivery (#18)
    "discord.com",                  # Discord webhook alerts (#18)
    "pro-api.coinmarketcap.com",    # CoinMarketCap — primary price source (triple-backup chain)
    "www.okx.com",                  # OKX REST — tertiary price fallback (no auth required)
})

# ─── Coin Universe (Phase 2, item 16) ────────────────────────────────────────
# 7 must-have coins always included in scanner, benchmarks, and correlation views.
# Top 30 dynamic coins are fetched at scan time via fetch_coin_universe() in ui/common.py.
MUST_HAVE_COINS: list[str] = ["XRP", "XLM", "XDC", "CC", "HBAR", "SHX", "ZBCN"]

# Exchange fallback chain for coins without primary listing
EXCHANGE_FALLBACK: list[str] = ["binance", "okx", "gate", "bybit", "kucoin", "coingecko"]

# ─── Branding ─────────────────────────────────────────────────────────────────
# Set env vars to activate: DEFI_BRAND_NAME="My App"  DEFI_BRAND_LOGO_PATH="logo.png"
# When unset (default), the app shows a clean placeholder header.
# 2-line rebrand when ready — no restructuring required.
BRAND_NAME: str = os.environ.get("DEFI_BRAND_NAME", "Family Office · DeFi Intelligence")
BRAND_LOGO_PATH: str = os.environ.get("DEFI_BRAND_LOGO_PATH", "")

# ─── RIA / Advisor Integration ────────────────────────────────────────────────
# EMBED_MODE: when True, hides sidebar, navigation chrome, and top branding header
#             for clean iframe embedding into advisor platforms (e.g. UX Wealth Partners).
#             Activate: DEFI_EMBED_MODE=1 (env var) or ?embed=1 query param.
# GIPS_MODE:  when True, adds GIPS-compatible disclosures to all performance figures,
#             adjusts return labels to time-weighted terminology (TWR), and adds
#             required disclaimer banners for RIA/advisor usage.
#             Activate: DEFI_GIPS_MODE=1 (env var).
_embed_env = os.environ.get("DEFI_EMBED_MODE", "0")
EMBED_MODE: bool = _embed_env in ("1", "true", "True", "yes")
_gips_env  = os.environ.get("DEFI_GIPS_MODE", "0")
GIPS_MODE: bool  = _gips_env in ("1", "true", "True", "yes")

"""
Central configuration for the Flare DeFi Model.
All protocol data, risk profiles, API endpoints, and yield baselines live here.
Update this file when new protocols launch or yields change.
"""

import os
from pathlib import Path

# ─── Paths ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

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
    "FBTC":  "",  # FAssets FBTC  — address TBD (not yet live on mainnet as of Mar 2026)
    "FDOGE": "",  # FAssets FDOGE — address TBD (beta, limited minting as of Mar 2026)
    "HLN":   "0x7D3c9C6566375d6F11D9B00b06A14eaF5a2f4e75",
}

# ─── Protocol Registry ────────────────────────────────────────────────────────
# Baseline yields from research (as of March 2026)
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
            "RLUSD-Pool": {"apr": 5.0, "asset": "RLUSD", "strategy": "rlusd_tbill", "tvl_usd": 5_000_000, "note": "Ripple USD — regulated stablecoin pool (Mar 2026 launch)"},
        },
        "tvl_usd": 46_000_000,   # updated Mar 2026 to include RLUSD pool
    },
    "spectra": {
        "name":     "Spectra Finance",
        "type":     "Yield Tokenization",
        "url":      "https://app.spectra.finance",
        "live":     True,
        "risk":     "low-medium",
        "markets": {
            "sFLR-MAY2026": {
                "fixed_apy":    18.6,    # Updated Mar 2026 — was 10.79, now ~18.6–19.59% Max APY
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
        "pairs": ["FXRP/USDC", "FXRP/USDH", "HYPE/USDC"],   # FXRP/USDH added Jan 2026; HYPE native token added Mar 2026
    },
    "flamix": {
        "name":     "Flamix",
        "type":     "Perp DEX",
        "url":      "https://flamix.trade",
        "live":     False,   # No public API yet; data manually monitored
        "risk":     "high",
        "note":     "Native perpetuals DEX on Flare. Up to 500x leverage. Any asset as collateral. FTSO pricing. $100M+ volume as of Dec 2025.",
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

# ─── Fallback Prices (used when CoinGecko is unavailable) ────────────────────
# Update these conservatively whenever prices move significantly (>20%).
FALLBACK_PRICES = {
    "FLR":   0.0088,
    "XRP":   1.53,
    "FXRP":  1.527,   # XRP * 0.998 bridge discount
    "USD0":  1.00,
    "RLUSD": 1.00,    # Ripple USD — regulated stablecoin, maintains $1 peg
    "SPRK":  0.05,    # SparkDEX token — placeholder; updated live from CoinGecko
    "HYPE":  10.0,    # Hyperliquid native token — placeholder; updated live from CoinGecko
}

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
        "target_apy_low":  15.0,
        "target_apy_high": 40.0,
        "max_il_risk":     "low",
        "leverage":        False,
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
        "target_apy_low":  50.0,
        "target_apy_high": 130.0,
        "max_il_risk":     "medium",
        "leverage":        False,
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
        "target_apy_low":  150.0,
        "target_apy_high": 265.0,
        "max_il_risk":     "high",
        "leverage":        True,
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
        "NOTE: The FlareDrop (monthly FLR airdrop) ended January 30, 2026 — sFLR staking yields have dropped. "
        "DEX LP incentives (RFLR rewards) continue until ~July 2026. "
        "Plan to rebalance LP positions before June 2026 as RFLR incentives wind down."
    ),
}

# ─── Impermanent Loss Thresholds ─────────────────────────────────────────────
IL_THRESHOLDS = {
    "low":    0.05,   # up to 5% IL acceptable
    "medium": 0.15,   # up to 15% IL acceptable
    "high":   0.50,   # up to 50% IL acceptable
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
        "entry_value":    None,
        "entry_date":     None,
        "entry_apy":      None,
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
        "entry_value":    None,
        "entry_date":     None,
        "entry_apy":      None,
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE FLAGS — auto-enabled by API key presence (zero code changes needed)
# ─────────────────────────────────────────────────────────────────────────────

SENTRY_DSN: str | None = os.environ.get("DEFI_SENTRY_DSN")
DEFI_SENTRY_DSN: str = os.environ.get("DEFI_SENTRY_DSN", "")
ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
COINGECKO_API_KEY: str | None = os.environ.get("DEFI_COINGECKO_API_KEY")
COINMETRICS_API_KEY: str | None = os.environ.get("DEFI_COINMETRICS_API_KEY")  # coinmetrics.io free community key
DEFI_WEBHOOK_URL: str = os.environ.get("DEFI_WEBHOOK_URL", "")       # Discord / Telegram / generic webhook
DEFI_TELEGRAM_CHAT_ID: str = os.environ.get("DEFI_TELEGRAM_CHAT_ID", "")  # Telegram chat ID for webhook delivery

FEATURES: dict = {
    # Legacy keys — kept for backward compatibility
    "ai_analysis":      bool(ANTHROPIC_API_KEY),
    "coingecko_pro":    bool(COINGECKO_API_KEY),
    "coinmetrics":      bool(COINMETRICS_API_KEY),
    "cdp_agentkit":     bool(CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE),
    "sentry":           bool(SENTRY_DSN),
    "flare_rpc":        True,        # always available (public RPC)
    "hyperliquid":      True,        # free public API
    "defillama":        True,        # free public API
    "coingecko_free":   True,        # always available
    # Batch 8 feature flags — auto-enabled by API key presence
    "anthropic_ai":     bool(ANTHROPIC_API_KEY),
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
})

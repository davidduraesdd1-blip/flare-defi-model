"""
data/demo_data.py — Static demo/sandbox data for Demo Mode (#67).

Used when st.session_state["defi_demo_mode"] is True so the app renders
correctly without requiring any API keys or live network calls.
"""

DEMO_PORTFOLIO = {
    "holdings": [
        {"protocol": "Aave v3",    "asset": "USDC",       "amount_usd": 10000, "apy": 0.052},
        {"protocol": "Lido",       "asset": "stETH",      "amount_usd": 25000, "apy": 0.038},
        {"protocol": "Pendle",     "asset": "PT-sUSDe",   "amount_usd": 5000,  "apy": 0.128},
        {"protocol": "Aerodrome",  "asset": "USDC/ETH",   "amount_usd": 8000,  "apy": 0.185},
    ],
    "total_value_usd": 48000,
}

DEMO_OPPORTUNITIES = [
    {
        "protocol": "Morpho",   "symbol": "USDC",          "apy": 0.0891,
        "tvl_usd": 850_000_000, "chain": "ethereum",       "risk_score": 15,
    },
    {
        "protocol": "Kamino",   "symbol": "SOL/USDC",      "apy": 0.142,
        "tvl_usd": 125_000_000, "chain": "Solana",         "risk_score": 35,
    },
    {
        "protocol": "Aerodrome","symbol": "USDC/ETH",      "apy": 0.185,
        "tvl_usd": 62_000_000,  "chain": "base",           "risk_score": 28,
    },
    {
        "protocol": "Pendle",   "symbol": "PT-sUSDe-Mar26","apy": 0.128,
        "tvl_usd": 340_000_000, "chain": "ethereum",       "risk_score": 22,
    },
    {
        "protocol": "Ethena",   "symbol": "sUSDe",         "apy": 0.275,
        "tvl_usd": 2_100_000_000,"chain": "ethereum",      "risk_score": 30,
    },
]

DEMO_MACRO = {
    "fear_greed":         62,
    "fear_greed_label":   "Greed",
    "btc_dominance":      58.3,
    "total_defi_tvl":     87_500_000_000,
    "eth_gas_gwei":       12.4,
}

DEMO_TOKEN_UNLOCKS = [
    {
        "token": "JUP", "date": "2026-04-06", "amount_pct": 25.0,
        "type": "Team/Investors", "days_until": 10, "severity": "CRITICAL", "is_cliff": False,
    },
    {
        "token": "PYTH", "date": "2026-05-20", "amount_pct": 8.0,
        "type": "Early Contributors", "days_until": 54, "severity": "WARNING", "is_cliff": False,
    },
    {
        "token": "OP", "date": "2026-05-31", "amount_pct": 5.0,
        "type": "Core Contributors", "days_until": 65, "severity": "WARNING", "is_cliff": False,
    },
]

DEMO_BRIDGE_FLOWS = [
    {"chain": "Base",     "tvl_usd": 7_400_000_000, "change_7d_pct": 18.2,  "flow_signal": "INFLOW"},
    {"chain": "Ethereum", "tvl_usd": 50_000_000_000,"change_7d_pct":  2.1,  "flow_signal": "STABLE"},
    {"chain": "Solana",   "tvl_usd": 8_200_000_000, "change_7d_pct": -6.3,  "flow_signal": "OUTFLOW"},
    {"chain": "Arbitrum", "tvl_usd": 4_800_000_000, "change_7d_pct":  7.4,  "flow_signal": "INFLOW"},
    {"chain": "Optimism", "tvl_usd": 1_200_000_000, "change_7d_pct":  0.8,  "flow_signal": "STABLE"},
    {"chain": "Polygon",  "tvl_usd": 1_100_000_000, "change_7d_pct": -2.1,  "flow_signal": "STABLE"},
    {"chain": "Flare",    "tvl_usd":    85_000_000, "change_7d_pct":  3.1,  "flow_signal": "STABLE"},
]

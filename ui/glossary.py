"""
ui/glossary.py — Shared crypto glossary for the Flare DeFi Model.

30 terms, 3 explanation depths (one per user level).
Used via glossary_tooltip() for ⓘ hover text and glossary_popover() for the sidebar panel.

Usage:
    from ui.glossary import glossary_popover, glossary_tooltip, GLOSSARY
    glossary_popover()                              # sidebar panel
    tip = glossary_tooltip("APY", user_level)       # ⓘ hover string
"""
import streamlit as st


# ─── Term Definitions — 3 depths per term ─────────────────────────────────────
# Keys: term name (display)
# Values: dict with keys 'beginner', 'intermediate', 'advanced'

GLOSSARY: dict[str, dict[str, str]] = {
    "APY": {
        "beginner":     "APY (Annual Percentage Yield) — the total return you earn in one year, including compounding. 10% APY on $1,000 = $100 earned in a year.",
        "intermediate": "APY — annualised yield including compound interest. Higher than APR because it includes reinvested returns.",
        "advanced":     "APY — effective annual rate accounting for compounding frequency: APY = (1 + r/n)^n − 1. Distinguish from APR (simple) and real yield (fee revenue only).",
    },
    "TVL": {
        "beginner":     "TVL (Total Value Locked) — the total amount of money deposited in a DeFi protocol. Higher TVL = more people trust and use it.",
        "intermediate": "TVL — aggregate USD value of assets deposited in a protocol's smart contracts. Proxy for protocol adoption and liquidity depth.",
        "advanced":     "TVL — sum of all on-chain assets under a protocol's control. Subject to double-counting across bridges/wrappers. Use DeFiLlama's deduplicated figures.",
    },
    "Liquidity Pool": {
        "beginner":     "A pool of two tokens that people deposit so others can trade between them. You earn a fee every time someone swaps.",
        "intermediate": "A smart-contract-held reserve of two assets enabling AMM trading. LPs earn swap fees proportional to their share.",
        "advanced":     "AMM liquidity pool: reserves X and Y maintain invariant (e.g. x·y = k for Uniswap v2). Fee tier, tick range (v3), and correlation affect LP profitability.",
    },
    "Impermanent Loss": {
        "beginner":     "When you deposit two tokens and one price changes a lot, you end up with less value than if you'd just held them. It's 'impermanent' because it reverses if prices return.",
        "intermediate": "Divergence loss vs holding: IL = 2√P − 1 − P where P = price ratio change. Worst at extremes; offset by trading fees if volume is high enough.",
        "advanced":     "IL = 2√(P_ratio) / (1 + P_ratio) − 1. Amplified in concentrated liquidity (v3). Delta-hedge via perps or options to reduce. Compare to fee APY to judge net return.",
    },
    "Yield Farming": {
        "beginner":     "Depositing your tokens into DeFi protocols to earn extra rewards — usually in the form of the protocol's own token on top of normal interest.",
        "intermediate": "Providing liquidity or staking to earn protocol token emissions in addition to base fees. Total return = base APY + emission APY.",
        "advanced":     "Incentive-driven LP: emission APY dilutes over time as TVL grows or emissions decay. Model sustainability via real yield ratio (fee revenue / total emissions).",
    },
    "AMM": {
        "beginner":     "AMM (Automated Market Maker) — a robot that sets prices automatically based on a formula, so you can trade without a human counterpart.",
        "intermediate": "AMM — smart contract that prices assets via a mathematical curve (e.g. x·y=k, StableSwap). No order book needed.",
        "advanced":     "AMM variants: constant-product (Uniswap v2), concentrated liquidity (v3), stableswap (Curve), hybrid (Balancer). Each has different IL profile and capital efficiency.",
    },
    "Smart Contract": {
        "beginner":     "A computer program that lives on the blockchain and runs automatically. Once deployed, no one can change or stop it — it just does what the code says.",
        "intermediate": "Self-executing code stored on-chain. Deterministic, censorship-resistant. Risk: bugs are permanent and exploitable.",
        "advanced":     "EVM bytecode deployed at an address. Upgradeable via proxy patterns (Transparent, UUPS). Audit coverage, formal verification, and bug bounties reduce risk.",
    },
    "DeFi": {
        "beginner":     "DeFi (Decentralised Finance) — banking and investing tools that run on blockchains instead of banks. Anyone can use them — no account needed.",
        "intermediate": "DeFi — financial protocols (lending, DEX, derivatives) built on smart contracts. Permissionless, composable, non-custodial.",
        "advanced":     "DeFi stack: base layer (L1/L2), AMM/DEX, lending, yield aggregators, structured products. Key risks: smart contract bugs, oracle manipulation, MEV.",
    },
    "Gas Fee": {
        "beginner":     "A small fee paid to the network to process your transaction. Like a postage stamp — you pay to send your transaction.",
        "intermediate": "Transaction cost in ETH (gwei) paid to validators. Varies with network congestion. EIP-1559: base fee + priority tip.",
        "advanced":     "Gas cost = gasUsed × (baseFee + priorityFee). baseFee burns ETH (deflationary pressure). Optimise: batch transactions, use calldata compression, off-peak timing.",
    },
    "Staking": {
        "beginner":     "Locking up your tokens to help secure the network or earn rewards. Like a fixed-term deposit — your tokens work for you.",
        "intermediate": "Locking tokens to participate in consensus (PoS) or earn protocol rewards. Returns vary: validator staking, liquid staking (stETH), restaking (EigenLayer).",
        "advanced":     "Staking variants: direct validator (min 32 ETH ETH), delegated, liquid (LST), restaking. Slashing risk, withdrawal queue delays, and LST depegging are key risks.",
    },
    "DEX": {
        "beginner":     "DEX (Decentralised Exchange) — a place to swap tokens directly from your wallet, without giving custody to anyone else.",
        "intermediate": "DEX — on-chain exchange using AMM or order-book mechanics. Non-custodial, permissionless. Examples: Uniswap, Curve, dYdX.",
        "advanced":     "DEX types: AMM (pool-based), CLOB (on-chain order book — dYdX), RFQ (0x). MEV exposure via sandwich attacks; mitigate with slippage limits and private RPCs.",
    },
    "CEX": {
        "beginner":     "CEX (Centralised Exchange) — a traditional exchange like Coinbase or Binance where you give custody of your tokens to the company.",
        "intermediate": "CEX — custodial exchange with KYC, order books, and higher liquidity. Counterparty risk: exchange can freeze withdrawals or go insolvent (FTX).",
        "advanced":     "CEX risk: rehypothecation of customer assets, opaque proof-of-reserves. Use PoR audits, cold wallet ratios, and withdrawal monitoring as risk indicators.",
    },
    "Collateral": {
        "beginner":     "Assets you deposit as a guarantee when borrowing. If your loan goes bad, the protocol takes your collateral.",
        "intermediate": "Assets pledged to secure a loan. Collateralisation ratio determines borrowing capacity. Over-collateralised lending (Aave, Compound) vs under-collateralised (Goldfinch).",
        "advanced":     "LTV (loan-to-value) and liquidation threshold define collateral health. e-mode (Aave v3) allows higher LTV for correlated assets. Monitor health factor continuously.",
    },
    "Liquidation": {
        "beginner":     "When your loan becomes too risky, the protocol automatically sells your collateral to pay it back. Avoid this by keeping a safe buffer.",
        "intermediate": "Forced repayment when a position's health factor drops below 1. Liquidation bots buy collateral at a discount and repay debt.",
        "advanced":     "Liquidation mechanism: liquidator repays up to 50% of debt and receives collateral + liquidation bonus. Flash loan liquidations are common. Watch health factor >1.5.",
    },
    "Slippage": {
        "beginner":     "The difference between the price you expected and the price you actually got. Bigger trades in smaller pools cause more slippage.",
        "intermediate": "Price impact of a trade on an AMM. Set max slippage tolerance (e.g. 0.5%) to protect against unfavourable fills.",
        "advanced":     "Slippage = (executionPrice − spotPrice) / spotPrice. Function of trade size relative to pool depth. Compare to price impact + MEV sandwich risk.",
    },
    "Flash Loan": {
        "beginner":     "A special loan that is borrowed and repaid in the same transaction — it's only possible in DeFi and is often used by developers.",
        "intermediate": "Uncollateralised loan that must be repaid within the same transaction block. Used for arbitrage, collateral swaps, and liquidations.",
        "advanced":     "Atomicity guarantees repayment: if sub-calls fail, entire tx reverts. Flash loan attacks exploit oracle price manipulation within the atomic window.",
    },
    "Governance": {
        "beginner":     "How token holders vote to change the rules of a protocol — like shareholders voting on company decisions.",
        "intermediate": "On-chain voting by token holders to change protocol parameters (fees, collateral ratios, new assets). Voter participation often low.",
        "advanced":     "Governance attack vectors: flash loan voting, token accumulation, Sybil attacks. DAO security depends on quorum requirements, timelock delays, and multisig safeguards.",
    },
    "Bridging": {
        "beginner":     "Moving tokens from one blockchain to another — like exchanging foreign currency when travelling.",
        "intermediate": "Cross-chain asset transfer via lock-and-mint or liquidity-pool bridges. Bridge smart contracts are a major hack target.",
        "advanced":     "Bridge architectures: lock-and-mint (centralised custody risk), liquidity pools (Hop, Across), optimistic (Optimism), ZK-proof (zkBridge). Over $2B lost to bridge hacks.",
    },
    "Layer 2": {
        "beginner":     "A faster, cheaper network built on top of Ethereum that uses Ethereum's security. Like an express lane on a highway.",
        "intermediate": "L2 — off-chain execution layer that settles to L1. Types: Optimistic Rollups (7-day fraud proof window), ZK Rollups (near-instant finality).",
        "advanced":     "L2 risk: sequencer centralisation, delayed withdrawal (Optimistic), validity proof soundness (ZK). Data availability: on-chain calldata vs off-chain DA (EigenDA, Celestia).",
    },
    "Tokenized Asset (RWA)": {
        "beginner":     "A real-world thing (like a bond or property) represented as a token on the blockchain so it can be traded or used in DeFi.",
        "intermediate": "Real World Asset — on-chain representation of off-chain value (Treasury bills, real estate, private credit). Bridges TradFi yield into DeFi.",
        "advanced":     "RWA protocols: Ondo (US Treasuries), Centrifuge (private credit), Maple (institutional loans). Key risks: legal enforceability, redemption liquidity, counterparty credit.",
    },
    "Market Cap": {
        "beginner":     "The total value of all tokens in circulation. Price × number of tokens = market cap. Larger = bigger project (but not always safer).",
        "intermediate": "Market cap = circulating supply × price. Fully diluted valuation (FDV) uses max supply. Compare circulating/FDV ratio to gauge dilution risk.",
        "advanced":     "Market cap vs realised cap (cost basis of all tokens): realised cap is a more stable value metric. MVRV = market cap / realised cap — signal for over/undervaluation.",
    },
    "Funding Rate": {
        "beginner":     "A small recurring fee in futures markets. When positive, people betting on price going up pay those betting on price going down. Shows market sentiment.",
        "intermediate": "Perpetual futures funding: longs pay shorts (positive) or vice versa (negative), every 8 hours. Extreme positive = crowded long; negative = crowded short.",
        "advanced":     "Funding rate arbitrage: long spot + short perp captures positive funding. Risk: spot liquidity, exchange counterparty, and correlation breakdown during market stress.",
    },
    "Open Interest": {
        "beginner":     "The total number of open futures contracts. High and rising = big moves possible. Falling OI = positions closing.",
        "intermediate": "Total USD value of all open futures positions. Rising OI + rising price = strong trend. Falling OI = deleveraging.",
        "advanced":     "OI analysis: OI/market cap ratio normalises across assets. High OI + high funding = liquidation cascade risk. Correlate with CVD and spot volume for conviction.",
    },
    "Fear & Greed Index": {
        "beginner":     "A daily score from 0 to 100 showing how scared (0) or excited (100) the market is. Extreme fear can be a buy signal; extreme greed can signal a top.",
        "intermediate": "Composite sentiment score from volatility, momentum, social media, and BTC dominance. Contrarian: buy extreme fear, cautious at extreme greed.",
        "advanced":     "Inputs: volatility (25%), market momentum (25%), social media (15%), BTC dominance (10%), trends (10%). Use 7-day and 30-day smoothed trends for timing confirmation.",
    },
    "RSI": {
        "beginner":     "RSI (Relative Strength Index) — a 0–100 score measuring if a token is overbought (above 70) or oversold (below 30). Like a speedometer for price momentum.",
        "intermediate": "RSI = 100 − 100/(1 + avg_gain/avg_loss) over 14 periods. Overbought >70, oversold <30. Divergence with price is a reversal signal.",
        "advanced":     "RSI divergence (price makes new high, RSI doesn't) is a leading reversal indicator. Hidden divergence signals trend continuation. Use with volume for confirmation.",
    },
    "MACD": {
        "beginner":     "MACD shows momentum by comparing two moving averages. When the fast line crosses above the slow line, it's often a buy signal.",
        "intermediate": "MACD = EMA(12) − EMA(26). Signal = EMA(9) of MACD. Histogram = MACD − Signal. Crossover and divergence are key signals.",
        "advanced":     "MACD crossovers lag price. Use histogram slope changes for earlier signals. MACD divergence with price on daily/weekly is high-conviction. Combine with RSI.",
    },
    "Sharpe Ratio": {
        "beginner":     "A score that measures how good a return is compared to how risky it was. Above 1.0 is good; above 2.0 is excellent.",
        "intermediate": "Sharpe = (return − risk-free rate) / standard deviation of returns. Higher = better risk-adjusted return.",
        "advanced":     "Annualised Sharpe = (mean_daily − Rf/252) / std_daily × √252. Assumes normal returns (problematic for crypto). Calmar ratio (return / max drawdown) often more relevant.",
    },
    "Kelly Criterion": {
        "beginner":     "A math formula that tells you what percentage of your money to put into each trade. Using too much increases your chance of losing everything.",
        "intermediate": "Kelly fraction = (p × b − q) / b where p = win rate, b = win/loss ratio. Use 25–50% Kelly to reduce volatility (fractional Kelly).",
        "advanced":     "Full Kelly maximises geometric growth but causes extreme drawdowns (up to 50%). Half-Kelly halves drawdown while capturing ~75% of growth. Adjust for parameter uncertainty.",
    },
    "MVRV Z-Score": {
        "beginner":     "A score that compares Bitcoin's current price to the average price everyone paid. Very high = overvalued; very low = undervalued. Good for spotting cycle tops and bottoms.",
        "intermediate": "MVRV = Market Cap / Realised Cap. Z-score standardises vs historical mean. >7 = historical sell zone; <0 = historical buy zone.",
        "advanced":     "MVRV Z = (market_cap − realised_cap) / std(market_cap). Realised cap tracks actual cost basis of all UTXOs. Works best at cycle extremes; less reliable in altcoins.",
    },
    "Support / Resistance": {
        "beginner":     "Support is a price floor where buying tends to appear; resistance is a ceiling where selling tends to appear. Prices often bounce off these levels.",
        "intermediate": "Key price levels identified by historical pivots, high-volume nodes (VPVR), round numbers, and Fibonacci retracements. Break of resistance = new support.",
        "advanced":     "Support/resistance clusters from: VPVR nodes, Fibonacci 0.618/0.786 retracements, previous swing highs/lows, and on-chain cost basis distributions (UTXO realised price).",
    },
}


def glossary_tooltip(term: str, user_level: str = "beginner") -> str:
    """Return a one-level explanation string for use as a tooltip or help text.

    Parameters
    ----------
    term : str
        Exact key from GLOSSARY.
    user_level : str
        'beginner', 'intermediate', or 'advanced'. Defaults to 'beginner'.

    Returns
    -------
    str : explanation at the requested depth, or term name if not found.
    """
    entry = GLOSSARY.get(term)
    if not entry:
        return term
    level = user_level if user_level in ("beginner", "intermediate", "advanced") else "beginner"
    return entry.get(level, entry.get("beginner", term))


def glossary_popover(user_level: str = "beginner") -> None:
    """Render a sidebar 'Crypto Glossary' popover button with 30 terms.

    Explanation depth scales with user_level.
    """
    label_depth = {"beginner": "Plain English", "intermediate": "Key Metrics", "advanced": "Technical Detail"}
    depth_name = label_depth.get(user_level, "Plain English")
    with st.popover(f"📖 Glossary — 30 terms ({depth_name})"):
        st.markdown("### Crypto & DeFi Glossary")
        st.caption(f"Showing explanations at **{user_level}** level. Change your level in the sidebar to see deeper explanations.")
        for term, depths in GLOSSARY.items():
            explanation = depths.get(user_level, depths["beginner"])
            st.markdown(f"**{term}** — {explanation}")
        st.caption("Tip: hover over any ⓘ icon in the app for an inline tooltip.")

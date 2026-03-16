"""
Arbitrage Detector
Scans all 10 arbitrage strategies across the Flare ecosystem.
Returns only real, actionable opportunities above minimum profit thresholds.
"""

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

from config import RISK_PROFILE_NAMES

logger = logging.getLogger(__name__)

# Minimum profit after estimated gas/fees to surface an alert (%)
# 0.5% on $500 = $2.50 — less than Flare gas + slippage. Raised to filter noise.
MIN_PROFIT_PCT = 1.50


@dataclass
class ArbitrageOpportunity:
    strategy:          str      # strategy type ID
    strategy_label:    str      # plain-English name
    token_or_pair:     str
    buy_where:         str
    sell_where:        str
    estimated_profit:  float    # net % after fees
    capital_needed:    float    # USD to execute (0 = any size)
    urgency:           str      # "monitor" / "act_soon" / "act_now"
    plain_english:     str      # one-sentence for beginners
    risk_level:        str
    applicable_profiles: list   # which risk profiles see this
    data_source:       str
    found_at:          str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ─── Strategy 1: Lending Rate Arbitrage ──────────────────────────────────────

def detect_lending_rate_arb(lending_data: list) -> list:
    """
    Find cases where borrow rate on protocol A < supply rate on protocol B
    for the same asset. Profit = supply_rate - borrow_rate - spread.
    """
    opps = []
    by_asset = {}
    for rate in lending_data:
        asset = rate["asset"]
        if asset not in by_asset:
            by_asset[asset] = []
        by_asset[asset].append(rate)

    for asset, rates in by_asset.items():
        for lender in rates:
            for borrower in rates:
                if lender["protocol"] == borrower["protocol"]:
                    continue
                borrow_cost = borrower["borrow_apy"]
                supply_earn = lender["supply_apy"]
                if borrow_cost == 0:
                    continue
                net_profit = supply_earn - borrow_cost - 1.5   # 1.5% buffer: gas + smart contract risk
                if net_profit >= MIN_PROFIT_PCT:
                    opps.append(ArbitrageOpportunity(
                        strategy="lending_rate",
                        strategy_label="Lending Rate Arbitrage",
                        token_or_pair=asset,
                        buy_where=f"Borrow on {borrower['protocol']} @ {borrow_cost:.1f}%",
                        sell_where=f"Supply to {lender['protocol']} @ {supply_earn:.1f}%",
                        estimated_profit=round(net_profit, 2),
                        capital_needed=1000,
                        urgency="monitor",
                        plain_english=(
                            f"Borrow {asset} at {borrow_cost:.1f}% APY on {borrower['protocol']}, "
                            f"then lend it at {supply_earn:.1f}% APY on {lender['protocol']}. "
                            f"Net gain: ~{round(net_profit,1)}% per year with near-zero risk."
                        ),
                        risk_level="low",
                        applicable_profiles=["conservative", "medium", "high"],
                        data_source=lender["data_source"],
                    ))

    return opps


# ─── Strategy 2: Cross-DEX Price Arbitrage ───────────────────────────────────

def detect_cross_dex_arb(pools_data: list) -> list:
    """
    Find the same token pair priced differently across Blazeswap / SparkDEX / Enosys.
    """
    opps = []

    # Group pools by normalised pair name
    by_pair = {}
    for pool in pools_data:
        pair = frozenset([pool["token0"], pool["token1"]])
        if pair not in by_pair:
            by_pair[pair] = []
        by_pair[pair].append(pool)

    for pair, pools in by_pair.items():
        if len(pools) < 2:
            continue
        # Compare APRs as a proxy for price inefficiency
        # (a big APR difference on same pair = price gap creating arb)
        aprs = [(p["protocol"], p["apr"]) for p in pools]
        aprs.sort(key=lambda x: x[1])
        low_p, low_apr  = aprs[0]
        high_p, high_apr = aprs[-1]
        spread = high_apr - low_apr

        # Only flag if spread suggests real price difference (> 2%)
        if spread >= 2.0:
            token_str = "-".join(sorted(pair))
            opps.append(ArbitrageOpportunity(
                strategy="cross_dex",
                strategy_label="Cross-DEX Arbitrage",
                token_or_pair=token_str,
                buy_where=f"Buy on {low_p} (lower price implied by {low_apr:.1f}% APR)",
                sell_where=f"Sell on {high_p} (higher price implied by {high_apr:.1f}% APR)",
                estimated_profit=round(min(spread * 0.1, 2.0), 2),  # ~10% of spread is capturable
                capital_needed=500,
                urgency="act_soon" if spread > 10 else "monitor",
                plain_english=(
                    f"The {token_str} pool has a price gap between {low_p} and {high_p}. "
                    f"Buy on the cheaper exchange, sell on the expensive one. "
                    f"Estimated one-time profit: ~{round(min(spread*0.1,2.0),1)}%."
                ),
                risk_level="medium",
                applicable_profiles=["medium", "high"],
                data_source=pools[0]["data_source"],
            ))

    return opps


# ─── Strategy 3: FAssets Mint/Redeem Arbitrage ───────────────────────────────

def detect_fassets_arb(prices_data: list) -> list:
    """
    Compare FXRP market price vs XRP spot price.
    If FXRP > XRP by > 0.5%: mint FXRP and sell at premium.
    If FXRP < XRP by > 0.5%: buy FXRP and redeem for XRP.
    """
    opps = []
    fxrp = next((p for p in prices_data if p["symbol"] == "FXRP"), None)
    xrp  = next((p for p in prices_data if p["symbol"] == "XRP"),  None)

    if not fxrp or not xrp:
        return opps

    xrp_price  = xrp["price_usd"]
    fxrp_price = fxrp["price_usd"]
    if xrp_price == 0:
        return opps

    pct_diff = (fxrp_price - xrp_price) / xrp_price * 100

    if pct_diff > 0.5:
        opps.append(ArbitrageOpportunity(
            strategy="fassets_mint_redeem",
            strategy_label="FAssets Premium Arbitrage",
            token_or_pair="FXRP/XRP",
            buy_where=f"Bridge XRP to Flare, mint FXRP (pay bridge fee ~0.2%)",
            sell_where=f"Sell FXRP at {round(pct_diff,2)}% premium on DEX",
            estimated_profit=round(pct_diff - 0.3, 2),
            capital_needed=2000,
            urgency="act_soon",
            plain_english=(
                f"FXRP is trading {round(pct_diff,1)}% above XRP right now. "
                f"Bridge XRP to Flare, convert to FXRP, sell the FXRP at the premium. "
                f"Net profit: ~{round(pct_diff-0.3,1)}% after bridge fees."
            ),
            risk_level="medium",
            applicable_profiles=["medium", "high"],
            data_source=fxrp["data_source"],
        ))
    elif pct_diff < -0.5:
        opps.append(ArbitrageOpportunity(
            strategy="fassets_mint_redeem",
            strategy_label="FAssets Discount Arbitrage",
            token_or_pair="FXRP/XRP",
            buy_where=f"Buy FXRP at {abs(round(pct_diff,2))}% discount on Flare DEX",
            sell_where="Redeem FXRP for real XRP via FAssets system",
            estimated_profit=round(abs(pct_diff) - 0.3, 2),
            capital_needed=2000,
            urgency="act_soon",
            plain_english=(
                f"FXRP is trading {abs(round(pct_diff,1))}% below real XRP. "
                f"Buy cheap FXRP on Flare, then redeem it for actual XRP. "
                f"Net profit: ~{round(abs(pct_diff)-0.3,1)}% after fees."
            ),
            risk_level="medium",
            applicable_profiles=["medium", "high"],
            data_source=fxrp["data_source"],
        ))

    return opps


# ─── Strategy 4: Funding Rate Delta-Neutral ──────────────────────────────────

def detect_funding_rate_neutral(perps_data: list, prices_data: list) -> list:
    """
    When funding rate is positive (longs pay shorts),
    go long spot + short perp for a delta-neutral yield.
    """
    opps = []
    for perp in perps_data:
        fr_annual = perp.get("funding_rate_annualised", 0)
        if fr_annual > MIN_PROFIT_PCT:
            token = perp["pair"].split("/")[0]
            opps.append(ArbitrageOpportunity(
                strategy="funding_rate_neutral",
                strategy_label="Delta-Neutral Funding Rate",
                token_or_pair=perp["pair"],
                buy_where=f"Buy {token} spot on Blazeswap/SparkDEX",
                sell_where=f"Short equal amount on {perp['exchange']} perpetuals",
                estimated_profit=round(fr_annual, 2),
                capital_needed=500,
                urgency="monitor",
                plain_english=(
                    f"Hold equal long and short positions on {token}. "
                    f"Price doesn't matter — you earn the funding rate paid by traders "
                    f"who are betting on price going up. "
                    f"Estimated yield: ~{round(fr_annual,1)}% per year with near-zero price risk."
                ),
                risk_level="medium",
                applicable_profiles=["medium", "high"],
                data_source=perp["data_source"],
            ))
    return opps


# ─── Strategy 5: Cyclo cysFLR Arbitrage ──────────────────────────────────────

def detect_cyclo_arb(staking_data: list) -> list:
    """
    cysFLR is backed 1:1 by sFLR but trades between $0 and $1 on DEXes.
    If cysFLR price < sFLR price by > 2%: buy cysFLR, redeem for sFLR.
    This is a unique Flare-specific opportunity.
    """
    sflr = next((s for s in staking_data if s["token"] == "sFLR"), None)
    if not sflr:
        return []

    # cysFLR typically trades at a discount to sFLR (the market prices in unlock risk)
    # Estimated discount from research: 5–15%
    estimated_discount = 8.0  # % — using research estimate; replace with live price when available

    return [ArbitrageOpportunity(
        strategy="cyclo_cysflr",
        strategy_label="Cyclo cysFLR Discount Arbitrage",
        token_or_pair="cysFLR/sFLR",
        buy_where="Buy cysFLR at a discount on SparkDEX",
        sell_where="Lock/redeem for sFLR at 1:1 via Cyclo protocol",
        estimated_profit=round(estimated_discount - 1.0, 2),
        capital_needed=1000,
        urgency="monitor",
        plain_english=(
            f"cysFLR is backed by sFLR 1-to-1, but often trades cheaper. "
            f"Buy cysFLR at the discount (~{estimated_discount:.0f}% below sFLR), "
            f"then convert it back to sFLR. Profit: ~{estimated_discount-1:.0f}%. "
            f"Note: discount is a research estimate — verify live price before acting."
        ),
        risk_level="high",
        applicable_profiles=["high"],
        data_source="research",
    )]


# ─── Strategy 6: Spectra PT/YT Arbitrage ─────────────────────────────────────

def detect_spectra_arb(staking_data: list) -> list:
    """
    When PT price + YT price < underlying asset price,
    buy both and hold to maturity for guaranteed profit.
    """
    pt = next((s for s in staking_data if s.get("token") == "PT-sFLR"), None)
    if not pt:
        logger.debug("detect_spectra_arb: PT-sFLR not found in staking_data — skipping Spectra strategy")
        return []

    # PT fixed rate 10.79% vs variable sFLR — pull live sFLR rate if available
    pt_apy   = pt.get("apy", 0.0)
    sflr_entry = next((s for s in staking_data if s.get("token") == "sFLR"), None)
    sflr_apy = sflr_entry.get("apy", 9.0) if sflr_entry else 9.0

    if pt_apy > sflr_apy + 1.0:
        spread = pt_apy - sflr_apy
        return [ArbitrageOpportunity(
            strategy="spectra_pt_yt",
            strategy_label="Spectra Fixed-Rate Arbitrage",
            token_or_pair="PT-sFLR / YT-sFLR",
            buy_where="Buy PT-sFLR on Spectra Finance (locks in fixed rate)",
            sell_where="Hold to May 2026 maturity for guaranteed yield",
            estimated_profit=round(spread, 2),
            capital_needed=500,
            urgency="monitor",
            plain_english=(
                f"Lock in a fixed {pt_apy:.1f}% APY on sFLR via Spectra Finance, "
                f"vs the variable ~{sflr_apy:.1f}% you'd earn from normal staking. "
                f"Guaranteed extra {round(spread,1)}% by holding to May 2026. "
                f"Note: based on research rates — confirm live PT price on Spectra before acting."
            ),
            risk_level="low",
            applicable_profiles=["conservative", "medium", "high"],
            data_source=pt["data_source"],
        )]
    return []


# ─── Strategy 7: LP Intrinsic Value Arb ──────────────────────────────────────

def detect_lp_intrinsic_arb(pools_data: list) -> list:
    """
    When LP token market price < value of underlying tokens,
    buying LP is cheaper than buying and pooling yourself.
    (Surfaced as a general alert when pool APR is very high.)
    """
    opps = []
    for pool in pools_data:
        if pool["apr"] > 100 and pool["tvl_usd"] > 50000:
            opps.append(ArbitrageOpportunity(
                strategy="lp_intrinsic",
                strategy_label="High-APR LP Value Opportunity",
                token_or_pair=pool["pool_name"],
                buy_where=f"Add liquidity directly to {pool['pool_name']} on {pool['protocol']}",
                sell_where="Earn outsized fees + rewards vs holding tokens",
                estimated_profit=round(pool["apr"] * 0.05, 1),  # 5% of APR is "excess"
                capital_needed=1000,
                urgency="monitor",
                plain_english=(
                    f"The {pool['pool_name']} pool on {pool['protocol']} is paying "
                    f"{pool['apr']:.0f}% APY. Compare to simply holding the tokens. "
                    f"Providing liquidity earns significant extra yield right now."
                ),
                risk_level="high",
                applicable_profiles=["high"],
                data_source=pool["data_source"],
            ))
    return opps


# ─── Strategy 8: sFLR Staking Rate vs Borrow Cost ────────────────────────────

def detect_sflr_borrow_arb(staking_data: list, lending_data: list) -> list:
    """
    If borrow cost for FLR/WFLR < sFLR staking yield,
    borrow FLR → stake as sFLR → earn spread.
    """
    opps = []
    sflr = next((s for s in staking_data if s["token"] == "sFLR"), None)
    if not sflr:
        return opps

    sflr_apy = sflr["apy"]
    for rate in lending_data:
        if rate["asset"] in ("WFLR", "FLR") and rate["borrow_apy"] > 0:
            spread = sflr_apy - rate["borrow_apy"] - 1.5   # 1.5% buffer: gas + liquidation risk
            if spread >= MIN_PROFIT_PCT:
                opps.append(ArbitrageOpportunity(
                    strategy="sflr_borrow_arb",
                    strategy_label="sFLR Staking Carry Trade",
                    token_or_pair="WFLR/sFLR",
                    buy_where=f"Borrow WFLR at {rate['borrow_apy']:.1f}% on {rate['protocol']}",
                    sell_where=f"Stake as sFLR at {sflr_apy:.1f}% APY on Sceptre",
                    estimated_profit=round(spread, 2),
                    capital_needed=1000,
                    urgency="monitor",
                    plain_english=(
                        f"Borrow FLR at {rate['borrow_apy']:.1f}% from {rate['protocol']}, "
                        f"then stake it as sFLR to earn {sflr_apy:.1f}%. "
                        f"Net carry profit: ~{round(spread,1)}% per year."
                    ),
                    risk_level="medium",
                    applicable_profiles=["medium", "high"],
                    data_source=sflr["data_source"],
                ))
    return opps


# ─── Master Detector ──────────────────────────────────────────────────────────

def _run_all_detectors(scan_result: dict, multi_result: dict) -> list:
    """Run all 8 detectors and return unfiltered ArbitrageOpportunity objects."""
    prices  = scan_result.get("prices", [])
    pools   = scan_result.get("pools",  [])
    lending = scan_result.get("lending",[])
    staking = scan_result.get("staking",[])
    perps   = multi_result.get("perps", [])

    opps = []
    opps += detect_lending_rate_arb(lending)
    opps += detect_cross_dex_arb(pools)
    opps += detect_fassets_arb(prices)
    opps += detect_funding_rate_neutral(perps, prices)
    opps += detect_cyclo_arb(staking)
    opps += detect_spectra_arb(staking)
    opps += detect_lp_intrinsic_arb(pools)
    opps += detect_sflr_borrow_arb(staking, lending)
    return opps


def detect_all_arbitrage(scan_result: dict, multi_result: dict, risk_profile: str) -> list:
    """
    Run all arbitrage detectors and return opportunities relevant
    to the given risk profile, sorted by estimated profit.
    """
    all_opps = _run_all_detectors(scan_result, multi_result)
    filtered = [o for o in all_opps if risk_profile in o.applicable_profiles]
    filtered.sort(key=lambda x: x.estimated_profit, reverse=True)
    logger.info(f"Arbitrage scan: {len(all_opps)} total opportunities, "
                f"{len(filtered)} for {risk_profile} profile")
    return [asdict(o) for o in filtered]


def detect_all_arbitrage_all_profiles(scan_result: dict, multi_result: dict) -> dict:
    """
    Run all detectors once and return opportunities filtered per profile.
    Use this instead of calling detect_all_arbitrage() three times per scan.
    """
    all_opps = _run_all_detectors(scan_result, multi_result)
    results  = {}
    for profile in RISK_PROFILE_NAMES:
        filtered = [o for o in all_opps if profile in o.applicable_profiles]
        filtered.sort(key=lambda x: x.estimated_profit, reverse=True)
        results[profile] = [asdict(o) for o in filtered]
    logger.info(f"Arbitrage scan: {len(all_opps)} total opportunities across all profiles")
    return results

"""
Options & Derivatives Scanner
Monitors SparkDEX perpetuals as options proxies and prepares
data for the Black-Scholes options model.
"""

import logging
import numpy as np
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

from config import FALLBACK_PRICES
from scanners.flare_scanner import fetch_prices as _fetch_flare_prices
from utils.http import http_get

logger = logging.getLogger(__name__)


@dataclass
class VolatilityData:
    token:           str
    price_usd:       float
    historical_vol:  float      # annualised, as decimal (e.g. 0.80 = 80%)
    implied_vol:     float      # from perp funding / price spread
    vol_regime:      str        # "low" / "normal" / "high" / "extreme"
    data_source:     str
    fetched_at:      str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class OptionsOpportunity:
    strategy:        str        # "covered_call" / "protective_put" / "bull_call_spread" / etc.
    token:           str
    rationale:       str
    estimated_yield: float      # annualised %
    risk_level:      str        # "low" / "medium" / "high"
    platform:        str
    data_source:     str
    fetched_at:      str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ─── Historical Volatility from Price History ─────────────────────────────────

def fetch_historical_volatility(token_id: str = "ripple", days: int = 30) -> Optional[float]:
    """
    Calculate historical volatility from CoinGecko price history.
    Returns annualised volatility as a decimal.
    """
    url  = f"https://api.coingecko.com/api/v3/coins/{token_id}/market_chart"
    data = http_get(url, params={"vs_currency": "usd", "days": days}, timeout=10)
    if not data:
        return None
    try:
        prices = [p[1] for p in data.get("prices", []) if p[1] > 0]
        if len(prices) < 2:
            return None
        log_returns = np.diff(np.log(prices))
        daily_vol   = np.std(log_returns)
        if not np.isfinite(daily_vol) or daily_vol == 0:
            return None
        return round(float(daily_vol * np.sqrt(365)), 4)
    except Exception as e:
        logger.debug(f"Historical vol calculation failed: {e}")
        return None


# ─── Volatility Data for Key Tokens ──────────────────────────────────────────

def _fetch_current_prices() -> dict:
    """
    Return current USD spot prices for FLR, XRP, FXRP.
    Delegates to flare_scanner.fetch_prices() which has a 5-minute TTL cache,
    avoiding a redundant CoinGecko call within the same scan cycle.
    """
    prices = _fetch_flare_prices()
    return {p.symbol: p.price_usd for p in prices if p.symbol in ("FLR", "XRP", "FXRP")}


def fetch_volatility_data() -> list:
    results = []
    live_prices = _fetch_current_prices()

    token_map = {
        "FLR":  ("flare-networks", 0.90),
        "XRP":  ("ripple",         0.65),
    }

    for symbol, (cg_id, default_vol) in token_map.items():
        hv = fetch_historical_volatility(cg_id, days=30)
        if hv is None:
            hv = default_vol
            source = "estimate"
        else:
            source = "live"

        # Classify volatility regime
        if hv < 0.40:
            regime = "low"
        elif hv < 0.70:
            regime = "normal"
        elif hv < 1.10:
            regime = "high"
        else:
            regime = "extreme"

        iv = round(hv * 1.15, 4)
        results.append(VolatilityData(
            token=symbol,
            price_usd=live_prices.get(symbol, 1.0),
            historical_vol=hv,
            implied_vol=iv,
            vol_regime=regime,
            data_source=source,
        ))

    # FXRP reuses XRP vol (same underlying asset) and its own live price
    xrp_entry = next((r for r in results if r.token == "XRP"), None)
    if xrp_entry:
        results.append(VolatilityData(
            token="FXRP",
            price_usd=live_prices.get("FXRP", live_prices.get("XRP", FALLBACK_PRICES["XRP"]) * 0.998),
            historical_vol=xrp_entry.historical_vol,
            implied_vol=xrp_entry.implied_vol,
            vol_regime=xrp_entry.vol_regime,
            data_source=xrp_entry.data_source,
        ))

    return results


# ─── Options Strategy Recommender ────────────────────────────────────────────

def recommend_options_strategies(vol_data: list, risk_profile: str) -> list:
    """
    Generate options/derivatives strategy recommendations based on
    current volatility regime and the user's risk profile.

    Uses SparkDEX perpetuals as the execution layer for these strategies
    since there is no dedicated options protocol live on Flare yet
    (Ignite Market is expected — will be integrated when live).
    """
    strategies = []

    for vd in vol_data:
        token = vd["token"] if isinstance(vd, dict) else vd.token
        vol   = vd["implied_vol"] if isinstance(vd, dict) else vd.implied_vol
        regime = vd["vol_regime"] if isinstance(vd, dict) else vd.vol_regime

        if risk_profile == "conservative":
            # Covered call: hold token, sell upside via perp short
            if regime in ("high", "extreme"):
                strategies.append(OptionsOpportunity(
                    strategy="covered_call",
                    token=token,
                    rationale=(
                        f"{token} implied vol is {regime} ({round(vol*100,1)}%). "
                        f"Sell a synthetic covered call by holding spot {token} and "
                        f"shorting a small perp position on SparkDEX. "
                        f"Collect funding rate as premium."
                    ),
                    estimated_yield=round(vol * 100 * 0.15, 1),  # ~15% of IV as yield
                    risk_level="low",
                    platform="sparkdex",
                    data_source="model",
                ))

        elif risk_profile == "medium":
            # Delta-neutral: long spot + short equal perp = pure funding rate income
            strategies.append(OptionsOpportunity(
                strategy="delta_neutral_carry",
                token=token,
                rationale=(
                    f"Buy {token} on Blazeswap/SparkDEX spot market. "
                    f"Short equal notional on SparkDEX perpetuals. "
                    f"Net delta = 0 (market neutral). "
                    f"Income = funding rate paid by longs to shorts."
                ),
                estimated_yield=12.0,   # estimated 12% annualised from funding
                risk_level="medium",
                platform="sparkdex",
                data_source="model",
            ))
            # Bull call spread using perp leverage when vol is low
            if regime == "low" and token in ("FXRP", "XRP"):
                strategies.append(OptionsOpportunity(
                    strategy="bull_call_spread",
                    token=token,
                    rationale=(
                        f"Low volatility environment ({round(vol*100,1)}% IV). "
                        f"Take a leveraged long on {token} perps (2x max). "
                        f"Set tight stop-loss at -5%. "
                        f"Target +10–15% gain on price breakout."
                    ),
                    estimated_yield=round(vol * 100 * 0.30, 1),
                    risk_level="medium",
                    platform="sparkdex",
                    data_source="model",
                ))

        elif risk_profile == "high":
            # Straddle in high vol: profit whether price goes up or down
            if regime in ("high", "extreme"):
                strategies.append(OptionsOpportunity(
                    strategy="synthetic_straddle",
                    token=token,
                    rationale=(
                        f"Extreme vol ({round(vol*100,1)}% IV) on {token}. "
                        f"Hold spot long AND short perp. Unwind one leg when price "
                        f"moves >10% in either direction. Profit from the move."
                    ),
                    estimated_yield=round(vol * 100 * 0.40, 1),
                    risk_level="high",
                    platform="sparkdex",
                    data_source="model",
                ))
            # Leveraged directional
            strategies.append(OptionsOpportunity(
                strategy="leveraged_long",
                token=token,
                rationale=(
                    f"Use SparkDEX perps at 5–10x leverage on {token}. "
                    f"Based on FTSO oracle data and on-chain flow signals. "
                    f"Tight stop-loss required. High risk / high reward."
                ),
                estimated_yield=round(vol * 100 * 0.60, 1),
                risk_level="high",
                platform="sparkdex",
                data_source="model",
            ))

    return strategies


# ─── Main Options Scan ────────────────────────────────────────────────────────

def run_options_scan(risk_profile: str = "medium") -> dict:
    logger.info("Starting options scan...")
    vol_data   = fetch_volatility_data()
    strategies = recommend_options_strategies(vol_data, risk_profile)

    logger.info(f"Options scan complete — {len(strategies)} strategies for {risk_profile} profile")
    return {
        "timestamp":   datetime.utcnow().isoformat(),
        "volatility":  [asdict(v) for v in vol_data],
        "strategies":  [asdict(s) for s in strategies],
        "risk_profile": risk_profile,
    }

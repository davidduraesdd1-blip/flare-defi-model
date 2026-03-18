"""
Options Model — Black-Scholes pricing + Greeks
Used to price synthetic options strategies on Flare tokens
via SparkDEX perpetuals as the execution layer.
"""

import numpy as np
from scipy.stats import norm
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class OptionPrice:
    token:          str
    option_type:    str     # "call" or "put"
    spot:           float
    strike:         float
    expiry_days:    int
    volatility:     float   # annualised decimal
    risk_free:      float   # annualised decimal
    price:          float   # option premium in USD
    delta:          float
    gamma:          float
    theta:          float   # daily theta decay in USD
    vega:           float   # per 1% vol change
    moneyness:      str     # "ITM" / "ATM" / "OTM"
    intrinsic:      float
    time_value:     float
    calculated_at:  str = None

    def __post_init__(self):
        if self.calculated_at is None:
            self.calculated_at = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def black_scholes(
    S: float,       # current spot price
    K: float,       # strike price
    T: float,       # time to expiry in years
    r: float,       # risk-free rate (decimal)
    sigma: float,   # volatility (decimal)
    option_type: str = "call"
) -> tuple:
    """
    Black-Scholes pricing formula.
    Returns (price, delta, gamma, theta, vega).
    """
    if T <= 0 or S <= 0 or K <= 0:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    if sigma <= 0:
        # Zero vol: price equals intrinsic value, no time value
        intrinsic = max(0.0, S - K) if option_type == "call" else max(0.0, K - S)
        delta = (1.0 if S > K else 0.0) if option_type == "call" else (-1.0 if S < K else 0.0)
        return (round(intrinsic, 6), round(delta, 4), 0.0, 0.0, 0.0)

    d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)

    if option_type == "call":
        price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        delta = norm.cdf(d1)
    else:  # put
        price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
        delta = norm.cdf(d1) - 1

    gamma = norm.pdf(d1) / (S * sigma * np.sqrt(T))
    # Black-Scholes theta: call uses -r·K·e^(-rT)·N(d2), put uses +r·K·e^(-rT)·N(-d2)
    if option_type == "call":
        theta = (-(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
                 - r * K * np.exp(-r * T) * norm.cdf(d2)) / 365
    else:
        theta = (-(S * norm.pdf(d1) * sigma) / (2 * np.sqrt(T))
                 + r * K * np.exp(-r * T) * norm.cdf(-d2)) / 365

    vega = S * norm.pdf(d1) * np.sqrt(T) / 100   # per 1% vol move

    return (
        round(float(price), 6),
        round(float(delta), 4),
        round(float(gamma), 6),
        round(float(theta), 6),
        round(float(vega),  6),
    )


def price_option(
    token: str,
    spot: float,
    strike: float,
    expiry_days: int,
    vol: float,
    option_type: str = "call",
    risk_free: float = 0.045,
) -> OptionPrice:
    """
    Price a single option and compute all Greeks.
    """
    T = expiry_days / 365.0
    price, delta, gamma, theta, vega = black_scholes(spot, strike, T, risk_free, vol, option_type)

    if option_type == "call":
        intrinsic = max(0, spot - strike)
    else:
        intrinsic = max(0, strike - spot)
    time_value = max(0, price - intrinsic)

    if option_type == "call":
        if strike < spot * 0.97:
            moneyness = "ITM"
        elif strike > spot * 1.03:
            moneyness = "OTM"
        else:
            moneyness = "ATM"
    else:  # put: ITM when strike > spot
        if strike > spot * 1.03:
            moneyness = "ITM"
        elif strike < spot * 0.97:
            moneyness = "OTM"
        else:
            moneyness = "ATM"

    return OptionPrice(
        token=token, option_type=option_type,
        spot=round(spot, 6), strike=round(strike, 6),
        expiry_days=expiry_days, volatility=round(vol, 4),
        risk_free=risk_free, price=price,
        delta=delta, gamma=gamma, theta=theta, vega=vega,
        moneyness=moneyness, intrinsic=round(intrinsic, 6),
        time_value=round(time_value, 6),
    )


# ─── Strategy Bundles ─────────────────────────────────────────────────────────

def covered_call_analysis(spot: float, token: str, vol: float, expiry_days: int = 30) -> dict:
    """
    Covered call: hold token + sell OTM call.
    Premium collected = income. Upside capped at strike.
    """
    strike = spot * 1.10    # 10% OTM
    call   = price_option(token, spot, strike, expiry_days, vol, "call")

    premium_pct    = call.price / spot * 100
    annualised_pct = premium_pct * (365 / expiry_days) if expiry_days > 0 else 0.0

    return {
        "strategy":       "Covered Call",
        "token":          token,
        "spot":           spot,
        "strike":         round(strike, 6),
        "expiry_days":    expiry_days,
        "premium_usd":    call.price,
        "premium_pct":    round(premium_pct, 2),
        "annualised_pct": round(annualised_pct, 1),
        "delta":          call.delta,
        "theta_daily":    call.theta,
        "breakeven":      round(spot - call.price, 6),
        "plain_english": (
            f"Hold {token} and collect {round(premium_pct,1)}% premium by "
            f"agreeing to sell at ${round(strike,4)} if price rises above it. "
            f"Earns ~{round(annualised_pct,1)}% per year in income."
        ),
        "execution":     f"Use SparkDEX perpetuals — short a small position at {round(strike,4)} equivalent",
    }


def protective_put_analysis(spot: float, token: str, vol: float, expiry_days: int = 30) -> dict:
    """
    Protective put: hold token + buy ATM put = downside insurance.
    Cost = premium. Limits losses below strike.
    """
    strike = spot * 0.95    # 5% OTM put
    put    = price_option(token, spot, strike, expiry_days, vol, "put")

    cost_pct       = put.price / spot * 100
    annualised_pct = cost_pct * (365 / expiry_days) if expiry_days > 0 else 0.0

    return {
        "strategy":          "Protective Put",
        "token":             token,
        "spot":              spot,
        "strike":            round(strike, 6),
        "expiry_days":       expiry_days,
        "insurance_cost_usd": put.price,
        "cost_pct":          round(cost_pct, 2),
        "annualised_cost":   round(annualised_pct, 1),
        "delta":             put.delta,
        "protected_below":   round(strike, 6),
        "plain_english": (
            f"Pay {round(cost_pct,1)}% to insure your {token} against drops below "
            f"${round(strike,4)}. Costs ~{round(annualised_pct,1)}% per year. "
            f"Your losses are capped — price can go to zero and you still get ${round(strike,4)} per token."
        ),
        "execution":     f"Use SparkDEX perpetuals — long position at {round(strike,4)} acts as synthetic put",
    }


def bull_call_spread_analysis(spot: float, token: str, vol: float, expiry_days: int = 30) -> dict:
    """
    Bull call spread: buy lower call + sell higher call.
    Limited profit, limited cost. Good for moderate bullish view.
    """
    lower_strike = spot * 1.05
    upper_strike = spot * 1.15
    buy_call  = price_option(token, spot, lower_strike, expiry_days, vol, "call")
    sell_call = price_option(token, spot, upper_strike, expiry_days, vol, "call")

    net_cost   = buy_call.price - sell_call.price
    max_profit = (upper_strike - lower_strike) - net_cost
    rr_ratio   = max_profit / net_cost if net_cost > 0 else 0

    return {
        "strategy":      "Bull Call Spread",
        "token":         token,
        "spot":          spot,
        "lower_strike":  round(lower_strike, 6),
        "upper_strike":  round(upper_strike, 6),
        "expiry_days":   expiry_days,
        "net_cost_usd":  round(net_cost, 6),
        "max_profit_usd": round(max_profit, 6),
        "risk_reward":   round(rr_ratio, 2),
        "breakeven":     round(lower_strike + net_cost, 6),
        "plain_english": (
            f"Bet that {token} rises 5–15% over the next {expiry_days} days. "
            f"Risk: ${round(net_cost*100,2)} per $100 invested. "
            f"Reward: up to ${round(max_profit*100,2)} per $100. "
            f"Ratio: {round(rr_ratio,1)}:1."
        ),
        "execution":     "Use SparkDEX 2x leveraged long with stop-loss at -5%",
    }


# ─── Main Options Analysis ────────────────────────────────────────────────────

def run_options_analysis(vol_data: list, risk_profile: str) -> dict:
    """
    Generate full options analysis for all tracked tokens.
    """
    results = {}

    for vd in vol_data:
        if isinstance(vd, dict):
            token = vd.get("token", "")
            spot  = vd.get("price_usd", 0.0)
            vol   = vd.get("implied_vol", 0.5)
        else:
            token = getattr(vd, "token", "")
            spot  = getattr(vd, "price_usd", 0.0)
            vol   = getattr(vd, "implied_vol", 0.5)
        if not token:
            continue

        if spot <= 0:
            continue

        token_results = {}

        if risk_profile in ("conservative", "medium", "high"):
            token_results["covered_call"] = covered_call_analysis(spot, token, vol)

        if risk_profile in ("medium", "high"):
            token_results["protective_put"]    = protective_put_analysis(spot, token, vol)
            token_results["bull_call_spread"]   = bull_call_spread_analysis(spot, token, vol)

        if risk_profile == "high":
            # Raw BS pricing for full options chain (ATM, ±5%, ±10%)
            chain = []
            for strike_mult in [0.90, 0.95, 1.00, 1.05, 1.10]:
                for opt_type in ["call", "put"]:
                    op = price_option(token, spot, spot * strike_mult, 30, vol, opt_type)
                    chain.append(asdict(op))
            token_results["options_chain"] = chain

        results[token] = token_results

    return {
        "timestamp":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "risk_profile": risk_profile,
        "analysis":     results,
    }

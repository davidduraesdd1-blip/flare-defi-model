"""
Risk Models — Ultra Conservative / Medium / High
Mathematical portfolio optimiser using:
  - Sharpe Ratio maximisation
  - Impermanent Loss (IL) formula
  - Kelly Criterion position sizing
  - Yield scoring with risk-adjusted return
"""

import numpy as np
import logging
from dataclasses import dataclass, field, asdict, replace
from datetime import datetime
from typing import Optional

from config import RISK_PROFILES, RISK_PROFILE_NAMES, PROTOCOLS, INCENTIVE_PROGRAM, RISK_FREE_RATE, MAX_KELLY_FRACTION

logger = logging.getLogger(__name__)


# ─── Incentive Decay ──────────────────────────────────────────────────────────

def _incentive_decay_factor() -> float:
    """
    Linear decay multiplier (0→1) representing how much of Flare's 2.2B FLR
    incentive program remains, from program start (Jan 2024) to expiry (Jul 2026).
    Returns 1.0 well before expiry, 0.0 at/after expiry.
    """
    expiry        = datetime(2026, 7, 1)
    program_start = datetime(2024, 1, 1)
    now           = datetime.utcnow()
    if now >= expiry:
        return 0.0
    total_days     = max(1, (expiry - program_start).days)
    remaining_days = (expiry - now).days
    return round(min(1.0, remaining_days / total_days), 4)


# ─── Output Structure ─────────────────────────────────────────────────────────

@dataclass
class Opportunity:
    rank:             int
    protocol:         str
    strategy:         str           # plain-English strategy name
    asset_or_pool:    str
    action:           str           # what the user should actually DO
    estimated_apy:    float         # %
    apy_low:          float
    apy_high:         float
    il_risk:          str           # "none" / "low" / "medium" / "high"
    il_estimate_pct:  float         # expected IL %
    risk_score:       float         # 0 (safe) → 10 (risky)
    sharpe_ratio:     float
    kelly_fraction:   float         # suggested % of capital to allocate
    confidence:       float         # 0–100 — model confidence in this pick
    risk_profile:     str
    plain_english:    str           # one-sentence beginner explanation
    data_source:      str
    # APY decomposition (Upgrade #2)
    fee_apy:          float = 0.0   # fee/base yield component
    reward_apy:       float = 0.0   # token incentive component (RFLR/SPRK)
    # TVL velocity (Upgrade #1)
    tvl_velocity:     float = 0.0   # 7-day TVL change %
    tvl_trend:        str  = ""     # "up" / "stable" / "down" / ""
    generated_at:     str = field(default_factory=lambda: datetime.utcnow().isoformat())


# ─── Impermanent Loss Formula ─────────────────────────────────────────────────

def calculate_il(price_ratio: float) -> float:
    """
    Standard IL formula for a 50/50 AMM pool.
    price_ratio = new_price / entry_price

    Returns IL as a positive percentage (e.g. 5.7 = 5.7% loss vs holding).
    """
    if price_ratio <= 0:
        return 0.0
    il = 2 * np.sqrt(price_ratio) / (1 + price_ratio) - 1
    return round(abs(il) * 100, 2)


def estimate_il_for_pool(il_risk: str, is_v3: bool = False) -> tuple:
    """
    Given an IL risk label, return (expected_il_pct, scenario_il_pct).
    Based on historical crypto volatility assumptions.

    is_v3: True for Uniswap V3-style concentrated LP (SparkDEX, Enosys).
           Concentrated positions amplify IL by ~3x for typical ±20-30% price ranges.
    """
    il_table = {
        "none":   (0.0,   0.0),
        "low":    (1.5,   5.0),    # ~25% price divergence
        "medium": (6.0,  15.0),    # ~50% price divergence
        "high":  (15.0,  40.0),    # ~100% price divergence (2x)
    }
    exp_il, worst_il = il_table.get(il_risk, (6.0, 15.0))
    if is_v3 and il_risk != "none":
        # V3 concentrated positions amplify IL ~3x vs full-range V2 AMM
        exp_il   = min(exp_il   * 3.0, 50.0)
        worst_il = min(worst_il * 3.0, 80.0)
    return exp_il, worst_il


# ─── Sharpe Ratio ─────────────────────────────────────────────────────────────

def sharpe_ratio(expected_return: float, risk_free_rate: float, std_dev: float) -> float:
    """
    Sharpe = (E[R] - Rf) / std_dev
    All inputs as decimals (e.g. 0.50 for 50%).
    """
    if std_dev <= 0:
        return 0.0
    return round((expected_return - risk_free_rate) / std_dev, 3)


# ─── Kelly Criterion ──────────────────────────────────────────────────────────

def kelly_fraction(win_prob: float, win_pct: float, loss_pct: float) -> float:
    """
    Kelly f* = (win_prob * win_pct - loss_prob * loss_pct) / win_pct
    Returns suggested fraction of capital as a decimal (capped at 0.25 for safety).
    """
    loss_prob = 1 - win_prob
    if win_pct <= 0:
        return 0.0
    k = (win_prob * win_pct - loss_prob * loss_pct) / win_pct
    k = max(0.0, min(k, MAX_KELLY_FRACTION))   # cap Kelly fraction for safety
    return round(k, 4)


# ─── Risk Score Calculator ────────────────────────────────────────────────────

def compute_risk_score(
    il_risk: str,
    protocol_type: str,
    data_freshness: str,
    tvl_usd: float = 0.0,
) -> float:
    """
    Composite risk score 0–10.
    Lower = safer.
    """
    base = {
        "none":   0.5,
        "low":    2.0,
        "medium": 4.5,
        "high":   7.5,
    }.get(il_risk, 4.0)

    type_add = {
        "Lending":              0.0,
        "Liquid Staking":       0.5,
        "Yield Vault":          0.5,
        "Yield Tokenization":   1.0,
        "DEX":                  2.0,
        "DEX + Perps":          3.0,
        "Leveraged Yield":      4.0,
        "Perps (Cross-chain)":  4.5,
    }.get(protocol_type, 2.0)

    _leveraged_types = {"Leveraged Yield", "DEX + Perps", "Perps (Cross-chain)"}
    leverage_add = 2.0 if protocol_type in _leveraged_types else 0.0
    stale_add    = 0.5 if data_freshness in ("baseline", "estimate") else 0.0
    # Thin-liquidity penalty: pools under $100K TVL carry higher smart-contract/exit risk
    tvl_add      = 1.5 if 0 < tvl_usd < 100_000 else 0.0

    score = min(10.0, base + type_add + leverage_add + stale_add + tvl_add)
    return round(score, 1)


# Volatility priors by protocol type — used in Sharpe ratio calculation.
# Lending rates are stable; LP/perps vol is driven by underlying price moves.
_TYPE_STD = {
    "Lending":              0.05,
    "Liquid Staking":       0.08,
    "Yield Vault":          0.10,
    "Yield Tokenization":   0.12,
    "DEX":                  0.25,
    "DEX + Perps":          0.35,
    "Leveraged Yield":      0.45,
    "Perps (Cross-chain)":  0.50,
}

# ─── Opportunity Builder ──────────────────────────────────────────────────────

def build_opportunity(
    rank:          int,
    protocol_key:  str,
    pool_or_asset: str,
    apr:           float,
    il_risk:       str,
    protocol_type: str,
    reward_token:  str,
    tvl_usd:       float,
    risk_profile:  str,
    data_source:   str,
    is_v3:            bool  = False,
    apy_history:      list  = None,
    reward_apr:       float = 0.0,
    profile_win_rate: float = None,
    tvl_history:      list  = None,   # Upgrade #1: TVL velocity
    ftso_signal:      float = 0.0,    # Upgrade #3: FTSO oracle confidence boost (0–10)
) -> Opportunity:

    profile = RISK_PROFILES[risk_profile]
    rf_rate = RISK_FREE_RATE

    apr = max(0.0, apr)  # guard: negative APY is not a valid yield opportunity

    # Incentive decay: RFLR/SPRK reward APY declines linearly to 0 by July 2026.
    # Use the actual known reward_apr split when available; fall back to 40/60 estimate.
    # APY Decomposition (Upgrade #2): track fee vs reward components separately.
    if reward_token in ("RFLR", "rFLR", "SPRK"):
        decay          = _incentive_decay_factor()
        if reward_apr > 0 and reward_apr <= apr:
            fee_part       = apr - reward_apr
            incentive_part = reward_apr
        else:
            fee_part       = apr * 0.40
            incentive_part = apr * 0.60
        apr = max(0.0, fee_part + incentive_part * decay)
        _fee_apy    = round(fee_part, 2)
        _reward_apy = round(incentive_part * decay, 2)
    else:
        _fee_apy    = round(apr, 2)   # lending/staking: all base yield, no token reward
        _reward_apy = 0.0

    # TVL Velocity (Upgrade #1)
    _tvl_velocity, _tvl_trend = _compute_tvl_velocity(tvl_history)

    # IL calculation (V3 concentrated positions amplify IL)
    il_exp, il_worst = estimate_il_for_pool(il_risk, is_v3)
    net_apy = max(0, apr - il_exp)

    # Use observed APY std dev when enough history exists; fall back to protocol-type prior.
    # Floor at the type prior so near-zero historical std on stable lending pools never
    # inflates Sharpe to unrealistic levels (measurement-precision artifact).
    std = _TYPE_STD.get(protocol_type, 0.20)
    if apy_history and len(apy_history) >= 5:
        hist_std = float(np.std([h / 100 for h in apy_history]))
        if hist_std > 0:
            std = max(hist_std, _TYPE_STD.get(protocol_type, 0.20))
    sr  = sharpe_ratio(net_apy / 100, rf_rate, std)   # rf_rate already a decimal

    # Kelly sizing — use feedback-loop win rate when available, else IL-based prior
    il_prior = 0.65 if il_risk in ("none", "low") else 0.55
    win_p    = profile_win_rate if profile_win_rate is not None else il_prior
    win_p    = max(0.35, min(0.80, win_p))   # clamp to sane bounds
    win_r    = net_apy / 100
    loss_r = il_worst / 100
    kf = kelly_fraction(win_p, win_r, loss_r)
    # Per-profile position cap is applied in optimise_portfolio after normalisation

    # Risk score (includes TVL thin-liquidity penalty)
    rs = compute_risk_score(il_risk, protocol_type, data_source, tvl_usd)

    # Confidence: higher TVL + live data + FTSO oracle agreement = higher confidence
    tvl_score   = min(50, tvl_usd / 2_000_000) if tvl_usd else 0
    fresh_score = 40 if data_source in ("live", "on-chain") else (25 if data_source == "research" else 15)
    # Upgrade #3: FTSO oracle signal adds up to 10 points when oracle confirms price data
    confidence  = max(0, min(100, round(tvl_score + fresh_score + (10 - rs) * 2 + ftso_signal, 1)))

    # APY range: use historical std dev if 3+ data points available, else ±20%
    if apy_history and len(apy_history) >= 3:
        apy_std  = float(np.std(apy_history))
        apy_low  = round(max(0.0, apr - 1.5 * apy_std), 1)
        apy_high = round(apr + 1.5 * apy_std, 1)
    else:
        apy_low  = round(apr * 0.80, 1)
        apy_high = round(apr * 1.20, 1)

    # Plain English explanation
    il_note = f" (small risk of losing value if token prices diverge)" if il_risk in ("medium", "high") else ""
    plain = (
        f"Earn ~{round(apr,1)}% per year on {pool_or_asset} "
        f"via {PROTOCOLS[protocol_key]['name']}{il_note}. "
        f"Suggested allocation: {round(kf*100,0):.0f}% of your portfolio."
    )

    # Human-readable action
    type_actions = {
        "Lending":            f"Deposit {pool_or_asset} into {PROTOCOLS[protocol_key]['name']} to earn {round(apr,1)}% APY",
        "Liquid Staking":     f"Stake your {pool_or_asset} on {PROTOCOLS[protocol_key]['name']} to earn {round(apr,1)}% APY",
        "Yield Vault":        f"Deposit {pool_or_asset} into the {PROTOCOLS[protocol_key]['name']} vault to earn {round(apr,1)}% APY",
        "Yield Tokenization": f"Split your {pool_or_asset} into fixed + variable yield on {PROTOCOLS[protocol_key]['name']}",
        "DEX":                f"Add liquidity to the {pool_or_asset} pool on {PROTOCOLS[protocol_key]['name']} to earn {round(apr,1)}% APY",
        "DEX + Perps":        f"Provide liquidity to {pool_or_asset} on {PROTOCOLS[protocol_key]['name']} to earn {round(apr,1)}% APY",
    }
    action = type_actions.get(protocol_type, f"Invest in {pool_or_asset} on {PROTOCOLS[protocol_key]['name']}")

    return Opportunity(
        rank=rank,
        protocol=PROTOCOLS[protocol_key]["name"],
        strategy=f"{protocol_type} — {pool_or_asset}",
        asset_or_pool=pool_or_asset,
        action=action,
        estimated_apy=round(apr, 2),
        apy_low=apy_low,
        apy_high=apy_high,
        il_risk=il_risk,
        il_estimate_pct=il_exp,
        risk_score=rs,
        sharpe_ratio=sr,
        kelly_fraction=kf,
        confidence=confidence,
        risk_profile=risk_profile,
        plain_english=plain,
        data_source=data_source,
        fee_apy=_fee_apy,
        reward_apy=_reward_apy,
        tvl_velocity=_tvl_velocity,
        tvl_trend=_tvl_trend,
    )


# ─── Portfolio Optimiser ──────────────────────────────────────────────────────

def optimise_portfolio(candidates: list, risk_profile: str) -> list:
    """
    Given a list of Opportunity objects, allocate capital across them
    using a simplified mean-variance (Sharpe-ranked) approach.
    Returns the top N opportunities with dollar allocations attached.
    """
    if not candidates:
        return []

    profile = RISK_PROFILES[risk_profile]

    # Filter to allowed protocols for this risk profile
    allowed_names = {PROTOCOLS[p]["name"] for p in profile["allowed_protocols"]}
    allowed = [o for o in candidates if o.protocol in allowed_names]

    # Filter IL risk
    il_ok = {"low": ["none", "low"],
              "medium": ["none", "low", "medium"],
              "high": ["none", "low", "medium", "high"]}
    il_allowed = il_ok.get(profile["max_il_risk"], ["none", "low"])
    filtered   = [o for o in allowed if o.il_risk in il_allowed]

    # Sort by profile-specific metric:
    #   High risk  → raw APY first, to surface the highest-yielding (and highest-IL) pools
    #                that are exclusive to this profile and would otherwise never outrank
    #                the lower-IL pools that also pass the medium filter.
    #   All others → Sharpe ratio (risk-adjusted return).
    if risk_profile == "high":
        ranked = sorted(filtered, key=lambda x: x.estimated_apy, reverse=True)
    else:
        ranked = sorted(filtered, key=lambda x: x.sharpe_ratio, reverse=True)

    # Protocol concentration cap: max 2 picks from the same protocol
    proto_counts: dict = {}
    diversified  = []
    for o in ranked:
        n = proto_counts.get(o.protocol, 0)
        if n < 2:
            diversified.append(o)
            proto_counts[o.protocol] = n + 1
    ranked = diversified

    # Take top opportunities based on Kelly fractions (normalised)
    top_n = min(6, len(ranked))
    top   = ranked[:top_n]

    # Normalise Kelly fractions to sum to ≤1
    total_kelly = sum(o.kelly_fraction for o in top)
    if total_kelly > 1.0:
        for o in top:
            o.kelly_fraction = round(o.kelly_fraction / total_kelly, 4)

    # Apply per-profile single-position cap AFTER normalisation (correct Kelly order)
    max_pos = profile["max_single_position_pct"] / 100
    for o in top:
        o.kelly_fraction = min(o.kelly_fraction, max_pos)

    # Re-rank after normalisation
    for i, o in enumerate(top):
        o.rank = i + 1

    return top


# ─── Three Risk Model Entry Points ───────────────────────────────────────────

def _load_history_data() -> tuple:
    """
    Load last 14 scans from history.json.
    Returns (apy_map, tvl_map) where:
      apy_map: {(protocol_name, asset_or_pool): [apy, ...]}
      tvl_map: {(protocol_name, asset_or_pool): [tvl_usd, ...]}  (Upgrade #1)
    """
    try:
        import json
        from config import HISTORY_FILE
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
        runs = history.get("runs", [])[-14:]
        apy_map: dict = {}
        tvl_map: dict = {}
        for run in runs:
            # APY from model output
            for profile_key in RISK_PROFILE_NAMES:
                for opp in run.get("models", {}).get(profile_key, []):
                    key = (opp.get("protocol", ""), opp.get("asset_or_pool", ""))
                    apy_map.setdefault(key, []).append(float(opp.get("estimated_apy", 0)))
            # TVL from raw pool scan data
            for pool in run.get("pools", []):
                proto = pool.get("protocol", "")
                proto_name = PROTOCOLS.get(proto, {}).get("name", proto)
                pool_name  = pool.get("pool_name", "")
                tvl        = pool.get("tvl_usd", 0.0)
                if proto_name and pool_name and tvl:
                    tvl_map.setdefault((proto_name, pool_name), []).append(float(tvl))
            # TVL from lending/staking entries
            for entry in run.get("lending", []) + run.get("staking", []):
                proto = entry.get("protocol", "")
                proto_name = PROTOCOLS.get(proto, {}).get("name", proto)
                name  = entry.get("asset") or entry.get("token", "")
                tvl   = entry.get("tvl_usd", 0.0)
                if proto_name and name and tvl:
                    tvl_map.setdefault((proto_name, name), []).append(float(tvl))
        return apy_map, tvl_map
    except Exception:
        return {}, {}


def _compute_tvl_velocity(tvl_history: list) -> tuple:
    """
    Given a list of TVL snapshots (oldest first), compute 7-day velocity.
    Returns (velocity_pct, trend) where trend is 'up' / 'stable' / 'down'.
    """
    if not tvl_history or len(tvl_history) < 2:
        return 0.0, ""
    oldest = tvl_history[0]
    latest = tvl_history[-1]
    if oldest <= 0:
        return 0.0, ""
    velocity = (latest - oldest) / oldest * 100
    if velocity > 5:
        trend = "up"
    elif velocity < -5:
        trend = "down"
    else:
        trend = "stable"
    return round(velocity, 1), trend


def _compute_ftso_signal(scan_result: dict) -> float:
    """
    Upgrade #3: Compare FTSO oracle prices to CoinGecko prices.
    Returns a confidence boost (0–10) when FTSO data is available and agrees with CoinGecko.
    - 0:  no FTSO data available
    - 5:  FTSO available, prices within 1% of CoinGecko
    - 8:  FTSO available, prices within 0.3% (very tight agreement = high-quality signal)
    - 10: FTSO matches exactly (extremely rare — indicates fresh oracle update)
    """
    ftso_prices = scan_result.get("ftso_prices", {})
    if not ftso_prices:
        return 0.0

    cg_lookup = {p.get("symbol", ""): p.get("price_usd", 0)
                 for p in scan_result.get("prices", []) if p.get("price_usd", 0) > 0}
    if not cg_lookup:
        return 5.0   # FTSO available but no CoinGecko to compare — mild boost

    deviations = []
    for sym, ftso_price in ftso_prices.items():
        cg_price = cg_lookup.get(sym, 0)
        if cg_price > 0 and ftso_price > 0:
            dev = abs(ftso_price - cg_price) / cg_price
            deviations.append(dev)

    if not deviations:
        return 5.0
    avg_dev = sum(deviations) / len(deviations)
    if avg_dev <= 0.001:   # within 0.1%
        return 10.0
    elif avg_dev <= 0.003:  # within 0.3%
        return 8.0
    elif avg_dev <= 0.01:   # within 1%
        return 5.0
    else:
        return 2.0   # FTSO available but diverging — weak signal


def _build_candidate_list(
    scan_result: dict,
    apy_history_map: dict = None,
    tvl_history_map: dict = None,
    win_rate_map: dict = None,
    ftso_signal: float = 0.0,
) -> list:
    """
    Convert raw scan data into candidate Opportunity objects.
    apy_history_map: {(protocol_name, pool_name): [apy, ...]} for dynamic APY ranges.
    tvl_history_map: {(protocol_name, pool_name): [tvl, ...]} for TVL velocity (Upgrade #1).
    win_rate_map:    {profile: win_rate_decimal} from feedback loop (used for Kelly).
    ftso_signal:     confidence boost from FTSO oracle agreement (Upgrade #3).
    """
    apy_history_map = apy_history_map or {}
    tvl_history_map = tvl_history_map or {}
    win_rate_map    = win_rate_map    or {}
    # Candidates are built at "high" profile then cloned per profile in run_all_models;
    # use the "high" profile win rate for initial Kelly sizing (conservatively)
    candidate_win_rate = win_rate_map.get("high")
    candidates = []
    rank = 1

    # LP Pools
    for pool in scan_result.get("pools", []):
        proto = pool.get("protocol", "")
        if not proto:
            continue
        proto_name = PROTOCOLS.get(proto, {}).get("name", proto)
        pool_name  = pool.get("pool_name", "Unknown Pool")
        is_v3      = proto in ("sparkdex", "enosys")
        candidates.append(build_opportunity(
            rank=rank,
            protocol_key=proto,
            pool_or_asset=pool_name,
            apr=pool.get("apr", 0.0),
            il_risk=pool.get("il_risk", "medium"),
            protocol_type=PROTOCOLS.get(proto, {}).get("type", "DEX"),
            reward_token=pool.get("reward_token", ""),
            tvl_usd=pool.get("tvl_usd", 0.0),
            risk_profile="high",        # scored at highest, filtered per profile
            data_source=pool.get("data_source", "estimate"),
            is_v3=is_v3,
            apy_history=apy_history_map.get((proto_name, pool_name), []),
            reward_apr=pool.get("reward_apr", 0.0),
            profile_win_rate=candidate_win_rate,
            tvl_history=tvl_history_map.get((proto_name, pool_name), []),
            ftso_signal=ftso_signal,
        ))
        rank += 1

    # Lending rates (supply side)
    for rate in scan_result.get("lending", []):
        proto = rate.get("protocol", "")
        if not proto:
            continue
        proto_name = PROTOCOLS.get(proto, {}).get("name", proto)
        asset      = rate.get("asset", "Unknown Asset")
        candidates.append(build_opportunity(
            rank=rank,
            protocol_key=proto,
            pool_or_asset=asset,
            apr=rate.get("supply_apy", 0.0),
            il_risk="none",
            protocol_type=PROTOCOLS.get(proto, {}).get("type", "Lending"),
            reward_token="",
            tvl_usd=rate.get("tvl_usd", 0.0),
            risk_profile="conservative",
            data_source=rate.get("data_source", "estimate"),
            apy_history=apy_history_map.get((proto_name, asset), []),
            tvl_history=tvl_history_map.get((proto_name, asset), []),
            ftso_signal=ftso_signal,
        ))
        rank += 1

    # Staking yields
    for stake in scan_result.get("staking", []):
        proto = stake.get("protocol", "")
        if not proto:
            continue
        proto_name = PROTOCOLS.get(proto, {}).get("name", proto)
        token      = stake.get("token", "Unknown Token")
        candidates.append(build_opportunity(
            rank=rank,
            protocol_key=proto,
            pool_or_asset=token,
            apr=stake.get("apy", 0.0),
            il_risk="none",
            protocol_type=PROTOCOLS.get(proto, {}).get("type", "Liquid Staking"),
            reward_token="",
            tvl_usd=stake.get("tvl_usd", 0.0),
            risk_profile="conservative",
            data_source=stake.get("data_source", "estimate"),
            apy_history=apy_history_map.get((proto_name, token), []),
            tvl_history=tvl_history_map.get((proto_name, token), []),
            ftso_signal=ftso_signal,
        ))
        rank += 1

    return candidates


def run_all_models(scan_result: dict) -> dict:
    """
    Run all three models and return results keyed by profile name.
    Candidates are built once and shared across profiles to avoid triple work.
    """
    logger.info("Running all three risk models...")
    apy_history_map, tvl_history_map = _load_history_data()

    # Pull empirical win rates from the feedback loop (returns {} before enough data)
    try:
        from ai.feedback_loop import get_profile_win_rates
        win_rate_map = get_profile_win_rates()
        if win_rate_map:
            logger.debug(f"Feedback win rates: {win_rate_map}")
    except Exception:
        win_rate_map = {}

    # Upgrade #3: compute FTSO oracle signal from scan data
    ftso_signal = _compute_ftso_signal(scan_result)
    if ftso_signal > 0:
        logger.info(f"FTSO oracle signal: {ftso_signal:.1f} confidence boost")

    base_candidates = _build_candidate_list(scan_result, apy_history_map, tvl_history_map, win_rate_map, ftso_signal)
    results = {}
    for profile in RISK_PROFILE_NAMES:
        profiled = [replace(c, risk_profile=profile) for c in base_candidates]
        results[profile] = [asdict(o) for o in optimise_portfolio(profiled, profile)]
    results["generated_at"]      = datetime.utcnow().isoformat()
    results["incentive_warning"] = INCENTIVE_PROGRAM["note"]
    logger.info(
        f"Models complete — conservative: {len(results['conservative'])} picks, "
        f"medium: {len(results['medium'])} picks, high: {len(results['high'])} picks"
    )
    return results

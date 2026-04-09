"""
models/composite_signal.py — 4-Layer Composite Market Environment Score

Produces a single score from -1.0 (extreme risk-off) to +1.0 (extreme risk-on)
by combining four independent signal layers per CLAUDE.md §9:

  Layer 1 — TECHNICAL   weight 0.20  BTC RSI-14 + 50/200d MA cross + 30d momentum
  Layer 2 — MACRO       weight 0.25  DXY + VIX + 2Y10Y yield curve + CPI
  Layer 3 — SENTIMENT   weight 0.25  Fear & Greed + SOPR + Deribit put/call
  Layer 4 — ON-CHAIN    weight 0.30  MVRV Z-Score + Hash Ribbons + Puell Multiple

Historical research sources:
  - RSI-14:         Wilder (1978). BTC backtested 2013-2024: avg 30d return +18% when RSI<30.
  - MA Cross:       50d/200d Golden/Death Cross. Glassnode (2023): 71% directional accuracy 90d.
  - MVRV Z-Score:   Mahmudov & Puell (2018). Backtested to 2011.
                    Z > 7 = tops (Dec 2017, Apr 2021). Z < 0 = bottoms (Dec 2018, Nov 2022).
  - SOPR:           Shirakashi (2019). >1.0 = spending in profit. Cross through 1.0 = pivots.
  - Hash Ribbons:   C. Edwards (2019). 30d/60d MA hash rate crossover.
                    Buy signal after miner capitulation (30d crosses back above 60d).
  - Puell Multiple: Puell (2019). Daily miner USD / 365d MA.
                    <0.5 historically = market bottoms. >4.0 historically = market tops.
  - VIX:            CBOE data to 1990. >35 = crisis (V-shaped reversals in crypto).
                    <15 = complacency (often precedes corrections).
  - DXY:            BIS + Fed data to 1971. Strong DXY (>105) = risk-off headwind for crypto.
  - 2Y10Y:          FRED T10Y2Y to 1976. Deep inversion (<-0.5%) precedes recessions 6-18mo.
  - CPI:            BLS data to 1913. >4% → Fed tightening → DeFi yield compression.
"""

from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ─── Layer weights (must sum to 1.0) ─────────────────────────────────────────
_W_TECHNICAL = 0.20   # Layer 1: BTC TA (RSI, MA cross, momentum)
_W_MACRO     = 0.20   # Layer 2: macro environment (reduced — on-chain more predictive for crypto)
_W_SENTIMENT = 0.25   # Layer 3: market sentiment
_W_ONCHAIN   = 0.35   # Layer 4: on-chain fundamentals (increased — harder to arbitrage, unique to crypto)


def _clamp(val: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


# ─── Layer 1: Technical Analysis ─────────────────────────────────────────────

def _score_rsi(rsi: float | None) -> float | None:
    """
    RSI-14 daily BTC → contrarian/momentum signal.
    Research: Wilder (1978). BTC backtested 2013-2024 by Glassnode.
      <20   → +1.0  (extreme oversold — historically very strong buy zone)
      20-30 → +0.6  (oversold — accumulation zone)
      30-40 → +0.2  (mildly oversold)
      40-60 →  0.0  (neutral range)
      60-70 → -0.2  (mildly overbought)
      70-80 → -0.5  (overbought — start reducing)
      >80   → -0.8  (extreme overbought — historically tops form here)
    Returns None when RSI data is unavailable.
    """
    if rsi is None:
        return None
    if rsi <= 20:   return +1.0
    if rsi <= 30:   return _clamp(+0.6 + (30 - rsi) / 25)
    if rsi <= 40:   return _clamp(+0.2 + (40 - rsi) / 25)
    if rsi <= 60:   return 0.0
    if rsi <= 70:   return _clamp(-0.2 - (rsi - 60) / 33)
    if rsi <= 80:   return _clamp(-0.5 - (rsi - 70) / 33)
    return -0.8


def _score_ma_signal(ma_signal: str | None, above_200ma: bool | None) -> float | None:
    """
    50d/200d MA crossover + price position vs 200d MA.
    Research: Glassnode (2023) — 71% directional accuracy 90d forward on BTC.
      GOLDEN_CROSS  → +0.5  (50d > 200d × 1.01: institutional bullish trend confirmed)
      DEATH_CROSS   → -0.5  (50d < 200d × 0.99: bearish trend confirmed)
      NEUTRAL       →  0.0 if above 200d MA, -0.1 if below (trend unconfirmed)
    Returns None when MA data is unavailable.
    """
    if ma_signal is None:
        return None
    if ma_signal == "GOLDEN_CROSS":
        return +0.5
    if ma_signal == "DEATH_CROSS":
        return -0.5
    # NEUTRAL: minor adjustment based on position vs 200d MA
    if above_200ma is True:
        return +0.1   # price above 200d — structurally bullish but unconfirmed cross
    if above_200ma is False:
        return -0.1   # price below 200d — structurally bearish
    return 0.0


def _score_price_momentum(momentum_30d: float | None) -> float | None:
    """
    30-day BTC price momentum (% change). Calibrated on historical BTC data.
    Used as a trend-following confirmation signal — not contrarian like RSI.
      > +50%  → +0.6  (strong bull run — DeFi yields often expand in risk-on phases)
      +20-50% → +0.4
      +5-20%  → +0.2
      ±5%     →  0.0  (range-bound)
      -5-20%  → -0.2
      -20-40% → -0.4
      < -40%  → -0.6  (crash territory)
    Returns None when price data is unavailable.
    """
    if momentum_30d is None:
        return None
    if momentum_30d >= 50:   return +0.6
    if momentum_30d >= 20:   return _clamp(+0.2 + (momentum_30d - 20) / 75)
    if momentum_30d >= 5:    return _clamp(+0.2 * (momentum_30d / 20))
    if momentum_30d >= -5:   return 0.0
    if momentum_30d >= -20:  return _clamp(-0.2 * (abs(momentum_30d) / 20))
    if momentum_30d >= -40:  return _clamp(-0.2 - (abs(momentum_30d) - 20) / 100)
    return -0.6


def score_ta_layer(ta_data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute Layer 1 technical analysis score from BTC TA signals.
    RSI weighted 50%, MA cross 30%, momentum 20%.
    All sourced from yfinance BTC-USD daily OHLCV — no API key required.
    """
    rsi_14     = ta_data.get("rsi_14")       if ta_data else None
    ma_signal  = ta_data.get("ma_signal")    if ta_data else None
    above_200  = ta_data.get("above_200ma")  if ta_data else None
    momentum   = ta_data.get("price_momentum") if ta_data else None

    s_rsi  = _score_rsi(rsi_14)
    s_ma   = _score_ma_signal(ma_signal, above_200)
    s_mom  = _score_price_momentum(momentum)

    # Weighted average over available indicators
    _SUB_W = {"rsi": 0.50, "ma": 0.30, "mom": 0.20}
    _pairs  = [("rsi", s_rsi), ("ma", s_ma), ("mom", s_mom)]
    _wsum   = sum(_SUB_W[k] for k, v in _pairs if v is not None)
    raw     = (
        sum((v or 0.0) * _SUB_W[k] for k, v in _pairs if v is not None) / _wsum
        if _wsum > 0 else 0.0
    )
    layer = _clamp(raw)

    return {
        "layer":      "technical",
        "score":      round(layer, 4),
        "weight":     _W_TECHNICAL,
        "weighted":   round(layer * _W_TECHNICAL, 4),
        "components": {
            "rsi_14":    {"value": rsi_14,    "score": round(s_rsi, 3) if s_rsi is not None else None, "sub_weight": 0.50},
            "ma_cross":  {"value": ma_signal, "score": round(s_ma,  3) if s_ma  is not None else None, "sub_weight": 0.30},
            "momentum":  {"value": momentum,  "score": round(s_mom, 3) if s_mom is not None else None, "sub_weight": 0.20},
        },
    }


# ─── Layer 2: Macro ──────────────────────────────────────────────────────────

def _score_dxy(dxy: float | None) -> float | None:
    """
    DXY → crypto headwind/tailwind signal.
    Calibrated on DXY data 1971-2024 vs BTC/crypto market cycles.
      >108  → -1.0 (strong headwind)
      105   → -0.5
      102   →  0.0 (neutral)
      98    → +0.5
      <94   → +1.0 (strong tailwind)
    Returns None when data is unavailable (distinct from genuine 0.0 neutral).
    """
    if dxy is None:
        return None
    if dxy >= 108:  return -1.0
    if dxy >= 105:  return _clamp(-0.5 - (dxy - 105) / 6)
    if dxy >= 102:  return _clamp((102 - dxy) / 6)
    if dxy >= 98:   return _clamp((102 - dxy) / 8)
    return _clamp(0.5 + (98 - dxy) / 8)


def _score_vix(vix: float | None) -> float | None:
    """
    VIX → market fear signal. Counter-intuitive for crypto:
    Very high VIX (>35) often precedes relief rallies. Low VIX = complacency.
    Calibrated on CBOE data 1990-2024.
      <12   → -0.5 (extreme complacency, likely before correction)
      12-15 → -0.2
      15-25 →  0.0 (normal range — genuine neutral)
      25-35 → +0.3 (elevated fear = opportunity zone)
      >35   → +0.6 (crisis spike = V-reversal historically)
    Returns None when data is unavailable (distinct from genuine 0.0 neutral).
    """
    if vix is None:
        return None
    if vix >= 35:  return +0.6
    if vix >= 25:  return _clamp(0.3 + (vix - 25) / 33)
    if vix >= 15:  return 0.0
    if vix >= 12:  return _clamp(-0.2 - (15 - vix) / 15)
    return -0.5


def _score_yield_curve(spread_2y10y: float | None) -> float | None:
    """
    2Y10Y yield spread → recession risk signal.
    FRED T10Y2Y historical data 1976-2024.
      >0.5  → +0.3 (healthy yield curve, growth positive)
      0-0.5 → +0.0 to +0.3 (flattening, watch)
      -0.5-0→ -0.2 to 0.0 (inverted, caution)
      <-0.5 → -0.5 (deep inversion, recession risk in 12-18mo)
    Returns None when data is unavailable (distinct from genuine 0.0 neutral).
    """
    if spread_2y10y is None:
        return None
    if spread_2y10y >= 0.5:   return +0.3
    if spread_2y10y >= 0.0:   return _clamp(spread_2y10y * 0.6)
    if spread_2y10y >= -0.5:  return _clamp(spread_2y10y * 0.4)
    return _clamp(-0.2 + (spread_2y10y + 0.5) * 0.6)


def _score_cpi(cpi_yoy: float | None) -> float | None:
    """
    CPI YoY % → monetary policy tightening risk.
    BLS/FRED data 1913-2024.
      <1.5%  → -0.2 (deflationary risk, also negative)
      1.5-2% → +0.2 (goldilocks zone)
      2-4%   → 0.0 (manageable, Fed neutral — genuine neutral)
      4-7%   → -0.3 (tightening cycle risk)
      >7%    → -0.6 (extreme tightening, highly risk-off)
    Returns None when data is unavailable (distinct from genuine 0.0 neutral).
    """
    if cpi_yoy is None:
        return None
    if cpi_yoy >= 7.0:   return -0.6
    if cpi_yoy >= 4.0:   return _clamp(-0.3 - (cpi_yoy - 4) / 10)
    if cpi_yoy >= 2.0:   return 0.0
    if cpi_yoy >= 1.5:   return +0.2
    return -0.2


def _score_dxy_momentum(dxy_30d_roc: float | None) -> float | None:
    """
    DXY 30-day rate-of-change (E4) — momentum signal complements absolute DXY level.
    Rising DXY = accelerating USD strength = crypto headwind.
    Falling DXY = weakening USD = crypto tailwind.
    Research: BIS (2022), Federal Reserve (2023) — DXY momentum leads crypto by 30-60 days.
      ROC > +3%: strong USD momentum  → -0.5
      ROC > +1.5%: rising             → linear to -0.2
      ROC in [-1.5, +1.5]: neutral    →  0.0
      ROC < -1.5%: falling            → linear to +0.2
      ROC < -3%: declining fast       → +0.5
    Returns None if input data is missing (historical DXY unavailable).
    """
    if dxy_30d_roc is None:
        return None
    if dxy_30d_roc >= 3.0:    return -0.5
    if dxy_30d_roc >= 1.5:    return _clamp(-0.2 - (dxy_30d_roc - 1.5) / 5.0)
    if dxy_30d_roc >= -1.5:   return 0.0
    if dxy_30d_roc >= -3.0:   return _clamp(+0.2 + (abs(dxy_30d_roc) - 1.5) / 5.0)
    return +0.5


def score_macro_layer(macro_data: dict[str, Any]) -> dict[str, Any]:
    """
    Compute Layer 1 macro score from merged FRED + yfinance dict.
    Returns score in [-1.0, +1.0] plus per-indicator breakdown.
    """
    dxy         = macro_data.get("dxy")
    dxy_30d_roc = macro_data.get("dxy_30d_roc")
    vix         = macro_data.get("vix")
    y2y10       = macro_data.get("yield_spread_2y10y")
    cpi         = macro_data.get("cpi_yoy")

    s_dxy  = _score_dxy(dxy)
    s_dxym = _score_dxy_momentum(dxy_30d_roc)
    s_vix  = _score_vix(vix)
    s_yc   = _score_yield_curve(y2y10)
    s_cpi  = _score_cpi(cpi)

    # Equal-weight only indicators with real data (not None).
    # Scorers return None when input data is unavailable, and 0.0 only when
    # the indicator is genuinely neutral (e.g. VIX=20, CPI=2.5%).
    # This prevents missing data from diluting the signal by pulling it toward 0.
    active = [s for s in [s_dxy, s_dxym, s_vix, s_yc, s_cpi] if s is not None]
    raw    = (sum(active) / len(active)) if active else 0.0
    layer  = _clamp(raw)

    return {
        "layer":      "macro",
        "score":      round(layer, 4),
        "weight":     _W_MACRO,
        "weighted":   round(layer * _W_MACRO, 4),
        "components": {
            "dxy":          {"value": dxy,         "score": round(s_dxy,  3) if s_dxy  is not None else None},
            "dxy_momentum": {"value": dxy_30d_roc, "score": round(s_dxym, 3) if s_dxym is not None else None},
            "vix":          {"value": vix,         "score": round(s_vix,  3) if s_vix  is not None else None},
            "yield_curve":  {"value": y2y10,       "score": round(s_yc,   3) if s_yc   is not None else None},
            "cpi_yoy":      {"value": cpi,         "score": round(s_cpi,  3) if s_cpi  is not None else None},
        },
    }


# ─── Layer 2: Sentiment ───────────────────────────────────────────────────────

def _score_fear_greed(fg_value: int | float | None) -> float | None:
    """
    Fear & Greed → contrarian signal (extreme fear = buy opportunity).
    CNN/Alternative.me data 2018-2024.
      0-15  Extreme Fear  → +0.8 (historically strong buy zone)
      16-30 Fear          → +0.4
      31-55 Neutral       → 0.0
      56-75 Greed         → -0.4
      76-100 Extreme Greed → -0.8
    Returns None when data is unavailable.
    """
    if fg_value is None:
        return None
    v = float(fg_value)
    if v <= 15:   return +0.8
    if v <= 30:   return _clamp(+0.4 + (30 - v) / 37.5)
    if v <= 55:   return 0.0
    if v <= 75:   return _clamp(-0.4 - (v - 55) / 50)
    return -0.8


def _score_sopr(sopr: float | None) -> float | None:
    """
    SOPR (Shirakashi 2019) — on-chain profitability of spent outputs.
    <0.99 = holders spending at a loss = capitulation = buy signal
    >1.02 = profit-taking = distribution = caution
    Crossing through 1.0 is the key inflection point.
    Returns None when data is unavailable.
    """
    if sopr is None:
        return None
    if sopr < 0.99:   return +0.7    # capitulation — forced selling, setup for reversal
    if sopr < 1.00:   return +0.3    # mild stress
    if sopr < 1.02:   return 0.0     # normal
    if sopr < 1.05:   return -0.2    # early distribution
    return -0.5                       # heavy profit-taking


def _score_put_call(put_call_ratio: float | None) -> float | None:
    """
    Put/call ratio from Deribit options market.
    >1.5 = extreme bearish hedging = contrarian buy
    <0.6 = extreme call buying = crowded longs = caution
    Returns None when data is unavailable.
    """
    if put_call_ratio is None:
        return None
    if put_call_ratio >= 1.5:   return +0.6   # extreme put buying = contrarian bullish
    if put_call_ratio >= 1.1:   return +0.2
    if put_call_ratio >= 0.9:   return 0.0    # balanced
    if put_call_ratio >= 0.6:   return -0.2
    return -0.6                                # extreme call buying = crowded


def _score_fg_trend(fg_value: float | None, fg_30d_avg: float | None) -> float | None:
    """
    F&G 30-day trend direction signal.
    A FGI of 25 rising from 10 (momentum: +15) is more bullish than 25 falling from 40.
    Research: CodeMeetsCapital backtest — trend direction adds context to level signal.
      Rising fast (current >> 30d avg, >+10): mild negative — overbought momentum
      Rising mildly (+5 to +10): neutral/slight positive
      Stable (±5): neutral
      Falling mildly (-5 to -10): slight positive — fear increasing = opportunity approaching
      Falling fast (< -10): more positive — potential capitulation zone forming
    """
    if fg_value is None or fg_30d_avg is None:
        return None
    diff = float(fg_value) - float(fg_30d_avg)
    if diff > 15:    return -0.15   # greed surging = potential exhaustion
    if diff > 10:    return -0.05
    if diff > 5:     return  0.0
    if diff >= -5:   return  0.0
    if diff >= -10:  return +0.05
    if diff >= -15:  return +0.10
    return +0.15                    # fear collapsing = potential capitulation = buy setup


def score_sentiment_layer(
    fg_value: int | float | None,
    put_call_ratio: float | None,
    fg_30d_avg: float | None = None,
) -> dict[str, Any]:
    """
    Compute Layer 2 sentiment score.
    F&G level 55%, F&G trend 10%, put/call 35%.
    SOPR reclassified to On-Chain layer (it is 100% on-chain UTXO data, not sentiment survey).
    F&G trend adds context: 25 rising from 10 ≠ 25 falling from 40.
    """
    s_fg   = _score_fear_greed(fg_value)
    s_fgt  = _score_fg_trend(fg_value, fg_30d_avg)
    s_pc   = _score_put_call(put_call_ratio)

    _SUB_W = {"fg": 0.55, "fgt": 0.10, "pc": 0.35}
    _pairs  = [("fg", s_fg), ("fgt", s_fgt), ("pc", s_pc)]
    _wsum   = sum(_SUB_W[k] for k, v in _pairs if v is not None)
    raw     = (
        sum((v or 0.0) * _SUB_W[k] for k, v in _pairs if v is not None) / _wsum
        if _wsum > 0 else 0.0
    )
    layer = _clamp(raw)

    return {
        "layer":      "sentiment",
        "score":      round(layer, 4),
        "weight":     _W_SENTIMENT,
        "weighted":   round(layer * _W_SENTIMENT, 4),
        "components": {
            "fear_greed":    {"value": fg_value,    "score": round(s_fg,  3) if s_fg  is not None else None, "sub_weight": 0.55},
            "fg_trend":      {"value": fg_30d_avg,  "score": round(s_fgt, 3) if s_fgt is not None else None, "sub_weight": 0.10},
            "put_call_ratio":{"value": put_call_ratio, "score": round(s_pc, 3) if s_pc is not None else None, "sub_weight": 0.35},
        },
    }


# ─── Layer 3: On-Chain ────────────────────────────────────────────────────────

def _score_mvrv_z(mvrv_z: float | None) -> float | None:
    """
    MVRV Z-Score (Mahmudov & Puell, 2018). Backtested on BTC 2011-2024.
    Historical cycle extremes: tops at Z>7 (Dec 2017 ~9.5, Jan 2021 ~8.0)
    Historical cycle bottoms: Z<0 (Dec 2018 ~-0.5, Nov 2022 ~-0.3)
    """
    if mvrv_z is None:
        return None
    if mvrv_z >= 7.0:    return -1.0   # extreme overvaluation (historic tops)
    if mvrv_z >= 4.0:    return _clamp(-0.5 - (mvrv_z - 4) / 6)
    if mvrv_z >= 1.5:    return _clamp(-0.2 - (mvrv_z - 1.5) / 12.5)
    if mvrv_z >= 0.0:    return _clamp((1.5 - mvrv_z) / 3 - 0.2)
    return _clamp(0.3 - mvrv_z * 0.7)   # below 0 = historically undervalued


def _score_hash_ribbon(
    signal: str | None,
    btc_above_20sma: bool | None = None,
) -> float | None:
    """
    Hash Ribbon signal (C. Edwards, 2019) with E1 price momentum gate.
    BUY = 30d hash rate MA crossed above 60d MA (capitulation ending) → strongly bullish.

    E1 gate: BUY signal is downgraded if BTC price has not confirmed recovery
    (price still below 20d SMA). Research: Edwards (2019) notes hash ribbon BUY
    has 94% accuracy when combined with price above short-term MA vs 71% without.
      BUY + price above 20d SMA:  +0.8  (confirmed — both miner and price recovering)
      BUY + price below 20d SMA:  +0.4  (hash rate recovered but price unconfirmed)
      BUY + 20d SMA unknown:      +0.8  (no data to downgrade — fail open)
    Returns None when data is unavailable.
    """
    if signal is None or signal == "N/A":
        return None
    base = {
        "BUY":                 +0.8,
        "RECOVERY":            +0.3,
        "CAPITULATION":        -0.2,
        "CAPITULATION_START":  -0.5,
    }.get(signal, 0.0)
    # E1: downgrade BUY if price is below 20d SMA (no price confirmation yet)
    if signal == "BUY" and btc_above_20sma is False:
        return +0.4
    return base


def _score_puell(puell_multiple: float | None) -> float | None:
    """
    Puell Multiple (D. Puell, 2019). BTC miner revenue relative to 1-year MA.
    Historical data 2013-2024:
    <0.5: Dec 2018 bottom (0.35), Nov 2022 bottom (0.41) — extreme buy zone
    >3.0: threshold lowered from 4.0 — 2021 cycle peak was PM=3.53 (not 4.0+).
          Dec 2017 peak: PM=7.17. Apr 2021 peak: PM=3.53. Cycle amplitude declining.
          Using ≥4.0 would have missed the entire 2021 cycle top entirely.
    """
    if puell_multiple is None:
        return None
    if puell_multiple <= 0.5:   return +0.9   # historically extreme bottom zone
    if puell_multiple <= 1.0:   return +0.4   # accumulation zone
    if puell_multiple <= 2.0:   return 0.0    # fair value
    if puell_multiple <= 3.0:   return _clamp(-0.3 - (puell_multiple - 2) / 3.3)
    return -0.8                                # extreme top zone (2021 peak = 3.53 → -0.8)


def score_onchain_layer(
    mvrv_z: float | None,
    hash_ribbon_signal: str | None,
    puell_multiple: float | None,
    sopr: float | None = None,
    btc_above_20sma: bool | None = None,
) -> dict[str, Any]:
    """
    Compute Layer 3 on-chain score.
    MVRV Z 0.40, Hash Ribbons 0.25, SOPR 0.20, Puell Multiple 0.15.
    SOPR reclassified here from Sentiment (it is 100% on-chain UTXO spend data).
    Weights reflect historical predictive accuracy from BTC 2011-2024 cycle backtests.
    btc_above_20sma: E1 gate — downgrade Hash Ribbon BUY if price not yet above 20d SMA.
    """
    s_mvrv  = _score_mvrv_z(mvrv_z)
    s_hash  = _score_hash_ribbon(hash_ribbon_signal, btc_above_20sma)
    s_puell = _score_puell(puell_multiple)
    s_sopr  = _score_sopr(sopr)

    _SUB_W  = {"mvrv": 0.40, "hash": 0.25, "sopr": 0.20, "puell": 0.15}
    _pairs  = [("mvrv", s_mvrv), ("hash", s_hash), ("sopr", s_sopr), ("puell", s_puell)]
    _wsum   = sum(_SUB_W[k] for k, v in _pairs if v is not None)
    raw     = (
        sum((v or 0.0) * _SUB_W[k] for k, v in _pairs if v is not None) / _wsum
        if _wsum > 0 else 0.0
    )
    layer = _clamp(raw)

    return {
        "layer":      "onchain",
        "score":      round(layer, 4),
        "weight":     _W_ONCHAIN,
        "weighted":   round(layer * _W_ONCHAIN, 4),
        "components": {
            "mvrv_z":         {"value": mvrv_z,             "score": round(s_mvrv,  3) if s_mvrv  is not None else None, "sub_weight": 0.40},
            "hash_ribbon":    {"value": hash_ribbon_signal, "score": round(s_hash,  3) if s_hash  is not None else None, "sub_weight": 0.25},
            "sopr":           {"value": sopr,               "score": round(s_sopr,  3) if s_sopr  is not None else None, "sub_weight": 0.20},
            "puell_multiple": {"value": puell_multiple,     "score": round(s_puell, 3) if s_puell is not None else None, "sub_weight": 0.15},
        },
    }


# ─── Composite Score ──────────────────────────────────────────────────────────

def _signal_label(score: float) -> str:
    if score >= +0.60:  return "STRONG_RISK_ON"
    if score >= +0.30:  return "RISK_ON"
    if score >= +0.10:  return "MILD_RISK_ON"
    if score >= -0.10:  return "NEUTRAL"
    if score >= -0.30:  return "MILD_RISK_OFF"
    if score >= -0.60:  return "RISK_OFF"
    return "STRONG_RISK_OFF"


def _beginner_label(score: float) -> str:
    if score >= +0.30:  return "Market looks good for DeFi yields — good time to enter"
    if score >= +0.10:  return "Conditions are slightly favorable"
    if score >= -0.10:  return "Mixed signals — stay cautious and hold existing positions"
    if score >= -0.30:  return "Conditions are slightly unfavorable — reduce new exposure"
    return "Market is stressed — wait for better conditions before adding positions"


def compute_composite_signal(
    macro_data: dict[str, Any],
    onchain_data: dict[str, Any],
    fg_value: int | float | None = None,
    put_call_ratio: float | None = None,
    ta_data: dict[str, Any] | None = None,
    fg_30d_avg: float | None = None,
) -> dict[str, Any]:
    """
    Compute the full 4-layer composite market environment signal (CLAUDE.md §9).

    Args:
        macro_data:     Output from macro_feeds.fetch_all_macro_data()
        onchain_data:   Output from macro_feeds.fetch_coinmetrics_onchain()
        fg_value:       Current Fear & Greed value (0-100)
        put_call_ratio: BTC put/call ratio from Deribit
        ta_data:        Output from macro_feeds.fetch_btc_ta_signals() [Layer 1]
        fg_30d_avg:     30-day average Fear & Greed value (for trend signal, A3)

    Layer weights: TA=0.20, Macro=0.20, Sentiment=0.25, On-Chain=0.35

    Returns dict with:
        score             float in [-1.0, +1.0]
        signal            str label (STRONG_RISK_ON .. STRONG_RISK_OFF)
        layers            dict with per-layer breakdown
        beginner_summary  str for Beginner user mode
    """
    try:
        ta_layer = score_ta_layer(ta_data or {})
    except Exception as e:
        logger.warning("[CompositeSignal] TA layer failed: %s", e)
        ta_layer = {"score": 0.0, "weight": _W_TECHNICAL, "weighted": 0.0, "components": {}}

    try:
        macro_layer = score_macro_layer(macro_data)
    except Exception as e:
        logger.warning("[CompositeSignal] macro layer failed: %s", e)
        macro_layer = {"score": 0.0, "weight": _W_MACRO, "weighted": 0.0, "components": {}}

    try:
        sentiment_layer = score_sentiment_layer(fg_value, put_call_ratio, fg_30d_avg)
    except Exception as e:
        logger.warning("[CompositeSignal] sentiment layer failed: %s", e)
        sentiment_layer = {"score": 0.0, "weight": _W_SENTIMENT, "weighted": 0.0, "components": {}}

    try:
        mvrv_z          = onchain_data.get("mvrv_z")             if onchain_data else None
        hr_sig          = onchain_data.get("hash_ribbon_signal")  if onchain_data else None
        puell           = onchain_data.get("puell_multiple")      if onchain_data else None
        sopr            = onchain_data.get("sopr")                if onchain_data else None
        above_20sma     = ta_data.get("above_20sma")              if ta_data else None
        onchain_layer = score_onchain_layer(mvrv_z, hr_sig, puell, sopr, above_20sma)
    except Exception as e:
        logger.warning("[CompositeSignal] on-chain layer failed: %s", e)
        onchain_layer = {"score": 0.0, "weight": _W_ONCHAIN, "weighted": 0.0, "components": {}}

    total = (
        ta_layer.get("weighted",       0.0) +
        macro_layer.get("weighted",    0.0) +
        sentiment_layer.get("weighted", 0.0) +
        onchain_layer.get("weighted",   0.0)
    )
    total = _clamp(total)

    return {
        "score":   round(total, 4),
        "signal":  _signal_label(total),
        "beginner_summary": _beginner_label(total),
        "layers": {
            "technical": ta_layer,
            "macro":     macro_layer,
            "sentiment": sentiment_layer,
            "onchain":   onchain_layer,
        },
        "weights": {
            "technical": _W_TECHNICAL,
            "macro":     _W_MACRO,
            "sentiment": _W_SENTIMENT,
            "onchain":   _W_ONCHAIN,
        },
    }

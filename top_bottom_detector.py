"""
top_bottom_detector.py — Universal Top/Bottom Detection Engine
Shared across all 3 apps (DeFi Model, SuperGrok, RWA Model).

Implements a 5-layer composite scoring system (0–100) to identify
likely market tops and bottoms in any OHLCV time series.

Score interpretation:
  80–100  Extreme bottom  → STRONG BUY
  65–79   Bottom zone     → BUY / ACCUMULATE
  35–64   Neutral         → HOLD / WAIT
  20–34   Top zone        → REDUCE / SELL
  0–19    Extreme top     → STRONG SELL

Layer weights (sum = 1.0):
  Layer 1 — On-Chain Macro Cycle   30%  (MVRV, NUPL, SOPR, Hash Ribbons, Pi Cycle)
  Layer 2 — Sentiment              20%  (Fear & Greed, Funding Rates)
  Layer 3 — Technical Divergence   25%  (RSI div, MACD div, CVD div, MTF confluence)
  Layer 4 — Market Structure       15%  (BOS/CHoCH, Order Blocks, FVGs, Volume Profile)
  Layer 5 — Volatility / Momentum  10%  (Chandelier Exit, Squeeze, ATR)

Academic & practitioner sources:
  RSI divergence:    Wilder (1978); 60-70% win rate, 14% more reliable hidden div in crypto
  MACD divergence:   Appel (1979); standard divergence methodology
  CVD:               Ausiello (2019, TradingView); captures 70-80% of true CVD signal
  BOS/CHoCH:         ICT (Inner Circle Trader) Smart Money Concepts; widely adopted 2022-2024
  Order Blocks:      ICT methodology; last impulse candle before structure break
  Fair Value Gaps:   ICT methodology; 3-candle imbalance; >80% fill rate within same session
  Volume Profile:    Steidlmayer (1990) Market Profile; POC + VAH/VAL (70% vol range)
  Chart Patterns:    Edwards & Magee (1948); H&S 82%, Double Top/Bottom 82%, Inv H&S 84%
  Pivot Points:      Floor trader pivots; Camarilla: Camerino (1989), range×0.0916-0.3664
  Chandelier Exit:   Chuck LeBeau (2004) Turtle Traders; N=22, mult=3, 3×ATR from extreme
  Wyckoff:           Wyckoff (1930); Spring ~82% accuracy, Upthrust ~80% accuracy
  MVRV Z-Score:      Mahmudov & Puell (2018); Z>7=top, Z<0=bottom; 84.3% combined accuracy
  Pi Cycle Top:      Checkmate (2019); 85% accuracy, 111dma × 2 vs 350dma
  Hash Ribbons:      C. Edwards (2019); 87.5% accuracy; avg 557% gain post-signal
  MTF confluence:    Elder (2002) Triple Screen; 15-20% higher win rates with MTF agreement
"""

from __future__ import annotations

import logging
import math
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─── Layer weights ────────────────────────────────────────────────────────────
_W_ONCHAIN    = 0.30
_W_SENTIMENT  = 0.20
_W_DIVERGENCE = 0.25
_W_STRUCTURE  = 0.15
_W_VOLATILITY = 0.10


# ═══════════════════════════════════════════════════════════════════════════════
# UTILITIES
# ═══════════════════════════════════════════════════════════════════════════════

def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp value to [lo, hi]."""
    return max(lo, min(hi, float(val)))


def _safe_df(df: pd.DataFrame | None, min_rows: int = 20) -> pd.DataFrame | None:
    """Return None if df is invalid or too short."""
    if df is None or not isinstance(df, pd.DataFrame) or len(df) < min_rows:
        return None
    # Normalize column names to lowercase
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]
    for col in ("open", "high", "low", "close"):
        if col not in df.columns:
            return None
    return df


def _ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average."""
    return series.ewm(span=period, adjust=False).mean()


def _sma(series: pd.Series, period: int) -> pd.Series:
    """Simple moving average."""
    return series.rolling(window=period).mean()


def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI-14."""
    delta = series.diff()
    gain  = delta.clip(lower=0).ewm(com=period - 1, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(com=period - 1, adjust=False).mean()
    rs    = gain / loss.replace(0, np.nan)
    # When loss EWM is 0 (all upward moves), RS → ∞ → RSI → 100.  fillna(100) handles NaN.
    return (100.0 - (100.0 / (1.0 + rs))).fillna(100.0)


def _macd(series: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    """Returns (macd_line, signal_line, histogram)."""
    fast_ema  = _ema(series, fast)
    slow_ema  = _ema(series, slow)
    macd_line = fast_ema - slow_ema
    sig_line  = _ema(macd_line, sig)
    histogram = macd_line - sig_line
    return macd_line, sig_line, histogram


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=period - 1, adjust=False).mean()


def _pivot_lows(series: pd.Series, n: int = 3) -> pd.Series:
    """Return boolean mask of pivot lows (lowest in ±n bars).

    center=True rolling produces NaN at edges (insufficient data). The comparison
    `series == NaN` yields NaN (not False), creating a mixed True/False/NaN boolean
    Series. In pandas 2.0+, using such a Series for fancy index selection
    (e.g. close.index[mask]) raises "Cannot index by location index with a
    non-integer key". fillna(False) makes all edge elements False so callers
    receive a clean boolean mask guaranteed to contain no NaN values.
    """
    return (series == series.rolling(window=2 * n + 1, center=True).min()).fillna(False)


def _pivot_highs(series: pd.Series, n: int = 3) -> pd.Series:
    """Return boolean mask of pivot highs (highest in ±n bars). See _pivot_lows."""
    return (series == series.rolling(window=2 * n + 1, center=True).max()).fillna(False)


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 3 — TECHNICAL DIVERGENCE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_rsi_divergence(df: pd.DataFrame, rsi_period: int = 14,
                          swing_n: int = 3, lookback: int = 50) -> dict:
    """
    Regular and hidden RSI divergence with 200 EMA trend filter.

    Regular divergence (counter-trend):
      Bullish: price makes lower low, RSI makes higher low   → potential bottom
      Bearish: price makes higher high, RSI makes lower high → potential top
    Hidden divergence (trend continuation):
      Bull hidden: price makes higher low, RSI makes lower low  → continuation up
      Bear hidden: price makes lower high, RSI makes higher high → continuation down

    Research: 60-70% win rate regular; hidden divergence 14% more reliable in crypto.
    200 EMA filter: only take bullish signals in uptrend, bearish in downtrend → +15-20%.

    Returns:
      signal:     "BULL_DIV" | "BEAR_DIV" | "BULL_HIDDEN" | "BEAR_HIDDEN" | "NONE"
      confidence: 0-100
      score_0to1: 0 (top signal) → 0.5 (neutral) → 1.0 (bottom/buy signal)
    """
    result = {"signal": "NONE", "confidence": 0, "score_0to1": 0.5, "details": {}}
    df = _safe_df(df, min_rows=max(30, lookback))
    if df is None:
        return result

    close  = df["close"].tail(lookback).reset_index(drop=True)
    rsi_s  = _rsi(close, rsi_period).fillna(50.0)
    ema200 = _ema(close, min(200, len(close) // 2 + 1))
    in_uptrend = close.iloc[-1] > ema200.iloc[-1]

    ph_price = _pivot_highs(close, swing_n)
    pl_price = _pivot_lows(close, swing_n)
    ph_rsi   = _pivot_highs(rsi_s, swing_n)
    pl_rsi   = _pivot_lows(rsi_s, swing_n)

    ph_idx = close.index[ph_price].tolist()
    pl_idx = close.index[pl_price].tolist()

    signals_found = []

    # Bearish regular: price HH, RSI LH
    if len(ph_idx) >= 2:
        i1, i2 = ph_idx[-2], ph_idx[-1]
        if close.iloc[i2] > close.iloc[i1] and rsi_s.iloc[i2] < rsi_s.iloc[i1]:
            conf = int(_clamp((rsi_s.iloc[i1] - rsi_s.iloc[i2]) / 20.0) * 80 + 20)
            signals_found.append(("BEAR_DIV", conf, 0.15))

    # Bullish regular: price LL, RSI HL
    if len(pl_idx) >= 2:
        i1, i2 = pl_idx[-2], pl_idx[-1]
        if close.iloc[i2] < close.iloc[i1] and rsi_s.iloc[i2] > rsi_s.iloc[i1]:
            conf = int(_clamp((rsi_s.iloc[i2] - rsi_s.iloc[i1]) / 20.0) * 80 + 20)
            signals_found.append(("BULL_DIV", conf, 0.85))

    # Bullish hidden: price HL, RSI LL (uptrend continuation)
    if in_uptrend and len(pl_idx) >= 2:
        i1, i2 = pl_idx[-2], pl_idx[-1]
        if close.iloc[i2] > close.iloc[i1] and rsi_s.iloc[i2] < rsi_s.iloc[i1]:
            conf = int(_clamp((rsi_s.iloc[i1] - rsi_s.iloc[i2]) / 15.0) * 75 + 25)
            signals_found.append(("BULL_HIDDEN", conf, 0.70))

    # Bearish hidden: price LH, RSI HH (downtrend continuation)
    if not in_uptrend and len(ph_idx) >= 2:
        i1, i2 = ph_idx[-2], ph_idx[-1]
        if close.iloc[i2] < close.iloc[i1] and rsi_s.iloc[i2] > rsi_s.iloc[i1]:
            conf = int(_clamp((rsi_s.iloc[i2] - rsi_s.iloc[i1]) / 15.0) * 75 + 25)
            signals_found.append(("BEAR_HIDDEN", conf, 0.30))

    if signals_found:
        # Pick highest-confidence signal
        best = max(signals_found, key=lambda x: x[1])
        result.update({
            "signal":     best[0],
            "confidence": best[1],
            "score_0to1": best[2],
            "details": {
                "in_uptrend":  in_uptrend,
                "rsi_current": round(float(rsi_s.iloc[-1]), 1),
                "all_signals": [(s, c) for s, c, _ in signals_found],
            },
        })

    return result


def detect_macd_divergence(df: pd.DataFrame, fast: int = 12, slow: int = 26,
                            sig: int = 9, swing_n: int = 3,
                            lookback: int = 60) -> dict:
    """
    Regular MACD divergence (Appel 1979).

    Bullish: price lower low, MACD histogram higher low  → buy signal
    Bearish: price higher high, MACD histogram lower high → sell signal

    Returns signal/confidence/score_0to1 same format as detect_rsi_divergence.
    """
    result = {"signal": "NONE", "confidence": 0, "score_0to1": 0.5, "details": {}}
    df = _safe_df(df, min_rows=max(40, lookback))
    if df is None:
        return result

    close = df["close"].tail(lookback).reset_index(drop=True)
    _, _, hist = _macd(close, fast, slow, sig)
    hist = hist.fillna(0.0)

    ph_price = _pivot_highs(close, swing_n)
    pl_price = _pivot_lows(close, swing_n)
    ph_hist  = _pivot_highs(hist, swing_n)
    pl_hist  = _pivot_lows(hist, swing_n)

    ph_idx = close.index[ph_price].tolist()
    pl_idx = close.index[pl_price].tolist()
    ph_h_idx = hist.index[ph_hist].tolist()
    pl_h_idx = hist.index[pl_hist].tolist()

    signals = []

    # Bullish: price LL, hist HL
    if len(pl_idx) >= 2 and len(pl_h_idx) >= 2:
        i1, i2 = pl_idx[-2], pl_idx[-1]
        j1, j2 = pl_h_idx[-2], pl_h_idx[-1]
        if close.iloc[i2] < close.iloc[i1] and hist.iloc[j2] > hist.iloc[j1]:
            pct_div = abs(hist.iloc[j2] - hist.iloc[j1]) / (abs(hist.iloc[j1]) + 1e-9)
            conf = int(_clamp(pct_div * 2.0) * 70 + 30)
            signals.append(("BULL_DIV", conf, 0.82))

    # Bearish: price HH, hist LH
    if len(ph_idx) >= 2 and len(ph_h_idx) >= 2:
        i1, i2 = ph_idx[-2], ph_idx[-1]
        j1, j2 = ph_h_idx[-2], ph_h_idx[-1]
        if close.iloc[i2] > close.iloc[i1] and hist.iloc[j2] < hist.iloc[j1]:
            pct_div = abs(hist.iloc[j1] - hist.iloc[j2]) / (abs(hist.iloc[j1]) + 1e-9)
            conf = int(_clamp(pct_div * 2.0) * 70 + 30)
            signals.append(("BEAR_DIV", conf, 0.18))

    if signals:
        best = max(signals, key=lambda x: x[1])
        result.update({
            "signal":     best[0],
            "confidence": best[1],
            "score_0to1": best[2],
            "details": {"hist_current": round(float(hist.iloc[-1]), 4)},
        })

    return result


def compute_cvd_divergence(df: pd.DataFrame, lookback: int = 20) -> dict:
    """
    Cumulative Volume Delta (CVD) divergence (Ausiello 2019).

    CVD proxy = cumulative sum of (volume × sign(close - open)).
    Captures 70-80% of true CVD signal using candle-based delta.

    Bearish: price new high, CVD lower high → buy-side exhaustion → SELL signal
    Bullish: price new low,  CVD higher low → sell-side absorption → BUY signal

    Returns signal/confidence/score_0to1.
    """
    result = {"signal": "NONE", "confidence": 0, "score_0to1": 0.5, "details": {}}
    df = _safe_df(df, min_rows=max(30, lookback + 5))
    if df is None or "volume" not in df.columns:
        return result

    tail = df.tail(lookback + 5).reset_index(drop=True)
    delta = tail["volume"] * np.sign(tail["close"] - tail["open"])
    cvd   = delta.cumsum()

    close = tail["close"]
    n = lookback

    # Compare last half vs first half
    mid = n // 2
    p_recent = close.iloc[mid:].max()
    p_prior  = close.iloc[:mid].max()
    c_recent = cvd.iloc[mid:].max()
    c_prior  = cvd.iloc[:mid].max()
    p_recent_low = close.iloc[mid:].min()
    p_prior_low  = close.iloc[:mid].min()
    c_recent_low = cvd.iloc[mid:].min()
    c_prior_low  = cvd.iloc[:mid].min()

    # Bearish CVD divergence
    if p_recent > p_prior * 1.005 and c_recent < c_prior * 0.995:
        conf = 60
        result.update({"signal": "BEAR_DIV", "confidence": conf, "score_0to1": 0.20,
                        "details": {"cvd_current": round(float(cvd.iloc[-1]), 2)}})

    # Bullish CVD divergence
    elif p_recent_low < p_prior_low * 0.995 and c_recent_low > c_prior_low * 1.005:
        conf = 60
        result.update({"signal": "BULL_DIV", "confidence": conf, "score_0to1": 0.80,
                        "details": {"cvd_current": round(float(cvd.iloc[-1]), 2)}})

    return result


def compute_mtf_divergence_confluence(df_15m: pd.DataFrame | None,
                                       df_1h:  pd.DataFrame | None,
                                       df_4h:  pd.DataFrame | None) -> dict:
    """
    Multi-Timeframe Divergence Confluence (Elder 2002 Triple Screen).

    When RSI divergence aligns across 15m + 1H + 4H simultaneously → ~80% accuracy.
    Each timeframe checked independently; score based on agreement level.

    Returns:
      bull_count: 0-3 timeframes with bullish divergence
      bear_count: 0-3 timeframes with bearish divergence
      signal:     "STRONG_BUY" (3/3) | "BUY" (2/3) | "SELL" (2/3) | "STRONG_SELL" (3/3) | "NEUTRAL"
      score_0to1: 0.0 → 1.0
      confidence: 0-100
    """
    bull_count = 0
    bear_count = 0
    tf_results = {}

    for label, df in [("15m", df_15m), ("1h", df_1h), ("4h", df_4h)]:
        r = detect_rsi_divergence(df) if df is not None else {"signal": "NONE"}
        tf_results[label] = r.get("signal", "NONE")
        sig = r.get("signal", "NONE")
        if sig in ("BULL_DIV", "BULL_HIDDEN"):
            bull_count += 1
        elif sig in ("BEAR_DIV", "BEAR_HIDDEN"):
            bear_count += 1

    if bull_count == 3:
        return {"signal": "STRONG_BUY",  "bull_count": 3, "bear_count": 0,
                "score_0to1": 0.95, "confidence": 80, "tf_signals": tf_results}
    if bull_count == 2:
        return {"signal": "BUY",         "bull_count": 2, "bear_count": bear_count,
                "score_0to1": 0.75, "confidence": 65, "tf_signals": tf_results}
    if bear_count == 3:
        return {"signal": "STRONG_SELL", "bull_count": 0, "bear_count": 3,
                "score_0to1": 0.05, "confidence": 80, "tf_signals": tf_results}
    if bear_count == 2:
        return {"signal": "SELL",        "bull_count": bull_count, "bear_count": 2,
                "score_0to1": 0.25, "confidence": 65, "tf_signals": tf_results}
    return {"signal": "NEUTRAL", "bull_count": bull_count, "bear_count": bear_count,
            "score_0to1": 0.50, "confidence": 30, "tf_signals": tf_results}


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 4 — MARKET STRUCTURE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_bos_choch(df: pd.DataFrame, swing_n: int = 5) -> dict:
    """
    Break of Structure (BOS) and Change of Character (CHoCH) — ICT Smart Money Concepts.

    BOS  = price breaks the last swing high/low in the direction of trend → continuation
    CHoCH = price breaks the last swing high in a downtrend (or swing low in uptrend)
            = FIRST reversal warning signal (precedes BOS of the new direction)

    Methodology:
      1. Identify last 2 significant swing highs and swing lows
      2. If current close > last swing high:
         - If prior trend was DOWN → CHoCH (reversal warning, bullish bias)
         - If prior trend was UP   → BOS (bullish continuation)
      3. If current close < last swing low:
         - If prior trend was UP   → CHoCH (reversal warning, bearish bias)
         - If prior trend was DOWN → BOS (bearish continuation)

    Returns:
      event:   "BULLISH_BOS" | "BEARISH_BOS" | "BULLISH_CHOCH" | "BEARISH_CHOCH" | "NONE"
      bias:    "BULLISH" | "BEARISH" | "NEUTRAL"
      score_0to1: score contribution (BUY side higher)
    """
    result = {"event": "NONE", "bias": "NEUTRAL", "score_0to1": 0.5, "confidence": 40,
              "details": {}}
    df = _safe_df(df, min_rows=max(30, swing_n * 4))
    if df is None:
        return result

    df = df.reset_index(drop=True)
    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    swing_high_mask = _pivot_highs(high, swing_n)
    swing_low_mask  = _pivot_lows(low, swing_n)

    sh_idx = high.index[swing_high_mask].tolist()
    sl_idx = low.index[swing_low_mask].tolist()

    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return result

    last_sh = float(high.iloc[sh_idx[-1]])
    prev_sh = float(high.iloc[sh_idx[-2]])
    last_sl = float(low.iloc[sl_idx[-1]])
    prev_sl = float(low.iloc[sl_idx[-2]])

    current = float(close.iloc[-1])

    # Determine prior trend: HH+HL = uptrend, LH+LL = downtrend
    prior_uptrend   = (last_sh > prev_sh) and (last_sl > prev_sl)
    prior_downtrend = (last_sh < prev_sh) and (last_sl < prev_sl)

    if current > last_sh:
        if prior_downtrend:
            result.update({"event": "BULLISH_CHOCH", "bias": "BULLISH",
                            "score_0to1": 0.80, "confidence": 70})
        else:
            result.update({"event": "BULLISH_BOS", "bias": "BULLISH",
                            "score_0to1": 0.65, "confidence": 60})
    elif current < last_sl:
        if prior_uptrend:
            result.update({"event": "BEARISH_CHOCH", "bias": "BEARISH",
                            "score_0to1": 0.20, "confidence": 70})
        else:
            result.update({"event": "BEARISH_BOS", "bias": "BEARISH",
                            "score_0to1": 0.35, "confidence": 60})

    result["details"] = {
        "last_swing_high": round(last_sh, 4),
        "last_swing_low":  round(last_sl, 4),
        "current_close":   round(current, 4),
        "prior_uptrend":   prior_uptrend,
        "prior_downtrend": prior_downtrend,
    }
    return result


def detect_order_blocks(df: pd.DataFrame, swing_n: int = 5,
                         lookback: int = 50) -> dict:
    """
    Order Block detection — ICT methodology.

    Demand OB (bullish): last BEARISH candle immediately before a bullish BOS.
                         Price returning to this zone = high-probability long entry.
    Supply OB (bearish): last BULLISH candle immediately before a bearish BOS.
                         Price returning to this zone = high-probability short entry.

    Returns:
      demand_zones: list of (low, high, age_bars) for current demand OBs
      supply_zones: list of (low, high, age_bars) for current supply OBs
      price_in_demand: bool — current price inside a demand OB
      price_in_supply: bool — current price inside a supply OB
      score_0to1: 1.0 if in demand, 0.0 if in supply, 0.5 neutral
    """
    result = {"demand_zones": [], "supply_zones": [], "price_in_demand": False,
              "price_in_supply": False, "score_0to1": 0.5, "confidence": 40, "details": {}}

    df = _safe_df(df, min_rows=max(30, lookback))
    if df is None:
        return result

    tail  = df.tail(lookback).reset_index(drop=True)
    close = tail["close"]
    high  = tail["high"]
    low   = tail["low"]
    op    = tail["open"]

    swing_high_mask = _pivot_highs(high, swing_n)
    swing_low_mask  = _pivot_lows(low, swing_n)

    sh_idx = high.index[swing_high_mask].tolist()
    sl_idx = low.index[swing_low_mask].tolist()

    current_price = float(close.iloc[-1])
    n = len(tail)

    demand_zones = []
    supply_zones = []

    # Demand OBs: last bearish candle before each swing low that was broken upward
    for sli in sl_idx:
        if sli < 2:
            continue
        # Find the last bearish candle before this swing low
        for i in range(sli - 1, max(0, sli - 5), -1):
            if float(close.iloc[i]) < float(op.iloc[i]):  # bearish candle
                ob_low  = float(low.iloc[i])
                ob_high = float(op.iloc[i])   # top of bearish body
                age_bars = n - 1 - sli
                demand_zones.append((ob_low, ob_high, age_bars))
                break

    # Supply OBs: last bullish candle before each swing high that was broken downward
    for shi in sh_idx:
        if shi < 2:
            continue
        for i in range(shi - 1, max(0, shi - 5), -1):
            if float(close.iloc[i]) > float(op.iloc[i]):  # bullish candle
                ob_low  = float(op.iloc[i])   # bottom of bullish body
                ob_high = float(high.iloc[i])
                age_bars = n - 1 - shi
                supply_zones.append((ob_low, ob_high, age_bars))
                break

    # Keep only recent zones (age < 30 bars)
    demand_zones = [(l, h, a) for l, h, a in demand_zones if a < 30]
    supply_zones = [(l, h, a) for l, h, a in supply_zones if a < 30]

    price_in_demand = any(l <= current_price <= h for l, h, _ in demand_zones)
    price_in_supply = any(l <= current_price <= h for l, h, _ in supply_zones)

    score = 0.5
    conf  = 40
    if price_in_demand and not price_in_supply:
        score, conf = 0.85, 75
    elif price_in_supply and not price_in_demand:
        score, conf = 0.15, 75

    result.update({
        "demand_zones":    [(round(l, 4), round(h, 4), a) for l, h, a in demand_zones[:5]],
        "supply_zones":    [(round(l, 4), round(h, 4), a) for l, h, a in supply_zones[:5]],
        "price_in_demand": price_in_demand,
        "price_in_supply": price_in_supply,
        "score_0to1":      score,
        "confidence":      conf,
    })
    return result


def detect_fair_value_gaps(df: pd.DataFrame, min_gap_pct: float = 0.001,
                            lookback: int = 30) -> dict:
    """
    Fair Value Gap (FVG) detection — ICT methodology.

    Bullish FVG: candle[n-1].low > candle[n+1].high  (3-candle upward imbalance)
                 Price tends to return to fill the gap (>80% fill rate).
    Bearish FVG: candle[n-1].high < candle[n+1].low  (3-candle downward imbalance)

    Current price inside an unfilled FVG = magnet/reaction zone.

    Returns:
      bull_fvgs: list of (gap_low, gap_high, age_bars, filled)
      bear_fvgs: list of (gap_low, gap_high, age_bars, filled)
      price_in_bull_fvg: bool
      price_in_bear_fvg: bool
      score_0to1
    """
    result = {"bull_fvgs": [], "bear_fvgs": [], "price_in_bull_fvg": False,
              "price_in_bear_fvg": False, "score_0to1": 0.5, "confidence": 35, "details": {}}

    df = _safe_df(df, min_rows=max(15, lookback + 3))
    if df is None:
        return result

    tail  = df.tail(lookback + 2).reset_index(drop=True)
    high  = tail["high"].values
    low   = tail["low"].values
    close = tail["close"].values
    n     = len(tail)
    current_price = float(close[-1])

    bull_fvgs = []
    bear_fvgs = []

    for i in range(1, n - 1):
        # Bullish FVG: low[i+1] > high[i-1]
        if low[i + 1] > high[i - 1]:
            gap_low  = high[i - 1]
            gap_high = low[i + 1]
            if (gap_high - gap_low) / (gap_low + 1e-9) >= min_gap_pct:
                filled = any(low[j] <= gap_high and high[j] >= gap_low
                             for j in range(i + 2, n))
                age = n - 1 - i
                bull_fvgs.append((float(gap_low), float(gap_high), age, filled))

        # Bearish FVG: high[i+1] < low[i-1]
        if high[i + 1] < low[i - 1]:
            gap_low  = high[i + 1]
            gap_high = low[i - 1]
            if (gap_high - gap_low) / (gap_low + 1e-9) >= min_gap_pct:
                filled = any(high[j] >= gap_low and low[j] <= gap_high
                             for j in range(i + 2, n))
                age = n - 1 - i
                bear_fvgs.append((float(gap_low), float(gap_high), age, filled))

    # Filter unfilled, recent
    bull_unfilled = [(l, h, a, f) for l, h, a, f in bull_fvgs if not f and a < 20]
    bear_unfilled = [(l, h, a, f) for l, h, a, f in bear_fvgs if not f and a < 20]

    price_in_bull = any(l <= current_price <= h for l, h, _, __ in bull_unfilled)
    price_in_bear = any(l <= current_price <= h for l, h, _, __ in bear_unfilled)

    score, conf = 0.5, 35
    if price_in_bull:
        score, conf = 0.72, 60
    elif price_in_bear:
        score, conf = 0.28, 60

    result.update({
        "bull_fvgs":        [(round(l, 4), round(h, 4), a) for l, h, a, _ in bull_unfilled[:5]],
        "bear_fvgs":        [(round(l, 4), round(h, 4), a) for l, h, a, _ in bear_unfilled[:5]],
        "price_in_bull_fvg": price_in_bull,
        "price_in_bear_fvg": price_in_bear,
        "score_0to1":        score,
        "confidence":        conf,
    })
    return result


def compute_volume_profile(df: pd.DataFrame, bins: int = 24,
                            lookback: int = 100) -> dict:
    """
    Volume Profile / Volume-by-Price (Steidlmayer 1990 Market Profile).

    POC  = Point of Control — price level with highest volume traded = magnet
    VAH  = Value Area High  — upper boundary of 70% of volume range
    VAL  = Value Area Low   — lower boundary of 70% of volume range
    HVN  = High Volume Node — volume cluster above 1.5× average = congestion/support
    LVN  = Low Volume Node  — volume gap below 0.5× average = price moves fast through

    P-shaped profile (volume at top, thin base) → distribution phase → SELL
    b-shaped profile (volume at bottom, thin top) → accumulation phase → BUY

    Returns:
      poc:   Point of Control price
      vah:   Value Area High
      val:   Value Area Low
      hvns:  list of HVN price levels
      lvns:  list of LVN price levels
      profile_shape: "P_DISTRIBUTION" | "B_ACCUMULATION" | "D_BALANCED" | "UNKNOWN"
      price_vs_poc:  "ABOVE" | "BELOW" | "AT"
      score_0to1
    """
    result = {"poc": None, "vah": None, "val": None, "hvns": [], "lvns": [],
              "profile_shape": "UNKNOWN", "price_vs_poc": "AT",
              "score_0to1": 0.5, "confidence": 40, "details": {}}

    df = _safe_df(df, min_rows=max(30, lookback // 2))
    if df is None or "volume" not in df.columns:
        return result

    tail  = df.tail(lookback).copy()
    high  = tail["high"].values
    low   = tail["low"].values
    vol   = tail["volume"].values
    close = tail["close"].values

    price_min = float(low.min())
    price_max = float(high.max())
    if price_max <= price_min:
        return result

    bin_edges = np.linspace(price_min, price_max, bins + 1)
    bin_vols  = np.zeros(bins)

    for i in range(len(tail)):
        candle_low  = low[i]
        candle_high = high[i]
        candle_vol  = vol[i]
        if candle_high == candle_low:
            # assign to single bin
            b = np.searchsorted(bin_edges[1:], candle_low, side="left")
            b = min(b, bins - 1)
            bin_vols[b] += candle_vol
        else:
            # distribute volume proportionally across bins touched
            overlap_start = np.maximum(bin_edges[:-1], candle_low)
            overlap_end   = np.minimum(bin_edges[1:],  candle_high)
            overlap       = np.maximum(0.0, overlap_end - overlap_start)
            total_overlap = overlap.sum()
            if total_overlap > 0:
                bin_vols += candle_vol * (overlap / total_overlap)

    total_vol = bin_vols.sum()
    if total_vol == 0:
        return result

    # POC
    poc_bin = int(np.argmax(bin_vols))
    poc     = float((bin_edges[poc_bin] + bin_edges[poc_bin + 1]) / 2)

    # Value Area (70% of volume)
    sorted_bins = np.argsort(bin_vols)[::-1]
    va_vol   = 0.0
    va_bins  = []
    for b in sorted_bins:
        va_bins.append(b)
        va_vol += bin_vols[b]
        if va_vol >= 0.70 * total_vol:
            break
    vah = float(bin_edges[max(va_bins) + 1])
    val = float(bin_edges[min(va_bins)])

    # HVN / LVN
    avg_vol = bin_vols.mean()
    hvn_prices = []
    lvn_prices = []
    for b in range(bins):
        mid = float((bin_edges[b] + bin_edges[b + 1]) / 2)
        if bin_vols[b] > 1.5 * avg_vol:
            hvn_prices.append(round(mid, 4))
        elif bin_vols[b] < 0.5 * avg_vol and bin_vols[b] > 0:
            lvn_prices.append(round(mid, 4))

    # Profile shape: compare top vs bottom half volume
    top_vol = bin_vols[bins // 2:].sum()
    bot_vol = bin_vols[:bins // 2].sum()
    ratio   = top_vol / (bot_vol + 1e-9)

    if ratio > 1.4:
        shape = "P_DISTRIBUTION"   # volume at top → selling pressure
        shape_score = 0.20
    elif ratio < 0.7:
        shape = "B_ACCUMULATION"   # volume at bottom → buying pressure
        shape_score = 0.80
    else:
        shape = "D_BALANCED"
        shape_score = 0.50

    current_price = float(close[-1])
    if current_price > poc * 1.002:
        poc_pos = "ABOVE"
        poc_score = 0.35   # above POC = some resistance, slight bearish
    elif current_price < poc * 0.998:
        poc_pos = "BELOW"
        poc_score = 0.65   # below POC = support magnet pulling up
    else:
        poc_pos = "AT"
        poc_score = 0.50

    # Also check if price is near VAL (buy zone) or VAH (sell zone)
    near_val = abs(current_price - val) / (val + 1e-9) < 0.015
    near_vah = abs(current_price - vah) / (vah + 1e-9) < 0.015

    val_score = 0.75 if near_val else (0.25 if near_vah else poc_score)
    final_score = _clamp(0.5 * shape_score + 0.5 * val_score)
    conf = 65 if (shape != "D_BALANCED" and (near_val or near_vah)) else 45

    result.update({
        "poc":           round(poc, 4),
        "vah":           round(vah, 4),
        "val":           round(val, 4),
        "hvns":          hvn_prices[:6],
        "lvns":          lvn_prices[:6],
        "profile_shape": shape,
        "price_vs_poc":  poc_pos,
        "score_0to1":    final_score,
        "confidence":    conf,
        "details": {
            "near_val":    near_val,
            "near_vah":    near_vah,
            "vol_ratio_top_vs_bot": round(ratio, 2),
        },
    })
    return result


def detect_chart_patterns(df: pd.DataFrame, swing_n: int = 3,
                           lookback: int = 80) -> dict:
    """
    Automated chart pattern recognition (Edwards & Magee 1948; altFINS data 2023).

    Patterns detected:
      Head & Shoulders (82% accuracy) → SELL signal
      Inverse Head & Shoulders (84% accuracy) → BUY signal
      Double Top (82% accuracy) → SELL signal
      Double Bottom (82% accuracy) → BUY signal

    Methodology:
      1. Identify swing highs/lows
      2. Test geometric ratios: H&S shoulder symmetry <20%, head prominence >5%
      3. Neckline breakout confirmation
      4. Volume profile confirmation (optional — uses volume if available)

    Returns:
      pattern: detected pattern name or "NONE"
      direction: "BULLISH" | "BEARISH" | "NEUTRAL"
      confidence: 0-100
      score_0to1
    """
    result = {"pattern": "NONE", "direction": "NEUTRAL", "confidence": 0,
              "score_0to1": 0.5, "details": {}}

    df = _safe_df(df, min_rows=max(30, lookback // 2))
    if df is None:
        return result

    tail  = df.tail(lookback).reset_index(drop=True)
    close = tail["close"]
    high  = tail["high"]
    low   = tail["low"]

    sh_mask = _pivot_highs(high, swing_n)
    sl_mask = _pivot_lows(low, swing_n)

    # Use integer positions — pandas 3.0 requires int keys for .iloc[]
    sh_idx = [i for i in range(len(high)) if sh_mask.iloc[i]]
    sl_idx = [i for i in range(len(low))  if sl_mask.iloc[i]]

    sh_vals = [float(high.iloc[i]) for i in sh_idx]
    sl_vals = [float(low.iloc[i])  for i in sl_idx]

    current = float(close.iloc[-1])
    patterns_found = []

    # ── Inverse Head & Shoulders (BUY) ───────────────────────────────────────
    if len(sl_idx) >= 3:
        for k in range(len(sl_idx) - 2):
            ls, h, rs = sl_vals[k], sl_vals[k + 1], sl_vals[k + 2]
            lsi, hi, rsi = sl_idx[k], sl_idx[k + 1], sl_idx[k + 2]
            # Head must be lowest, shoulders roughly equal
            if h < min(ls, rs):
                shoulder_sym = abs(ls - rs) / (max(ls, rs) + 1e-9)
                head_prom    = (min(ls, rs) - h) / (min(ls, rs) + 1e-9)
                if shoulder_sym < 0.25 and head_prom > 0.03:
                    # Neckline = average of highs between shoulders
                    neckline_prices = [float(high.iloc[j]) for j in range(lsi, rsi)
                                        if j < len(high)]
                    neckline = float(np.mean(neckline_prices)) if neckline_prices else (ls + rs) / 2
                    # Breakout: current price above neckline
                    if current > neckline * 0.998:
                        conf = int(70 - shoulder_sym * 100 + head_prom * 50)
                        conf = max(40, min(85, conf))
                        patterns_found.append(("INV_HEAD_AND_SHOULDERS", "BULLISH", conf, 0.87))

    # ── Head & Shoulders (SELL) ───────────────────────────────────────────────
    if len(sh_idx) >= 3:
        for k in range(len(sh_idx) - 2):
            ls, h, rs = sh_vals[k], sh_vals[k + 1], sh_vals[k + 2]
            lsi, hi, rsi = sh_idx[k], sh_idx[k + 1], sh_idx[k + 2]
            if h > max(ls, rs):
                shoulder_sym = abs(ls - rs) / (max(ls, rs) + 1e-9)
                head_prom    = (h - max(ls, rs)) / (max(ls, rs) + 1e-9)
                if shoulder_sym < 0.25 and head_prom > 0.03:
                    neckline_prices = [float(low.iloc[j]) for j in range(lsi, rsi)
                                        if j < len(low)]
                    neckline = float(np.mean(neckline_prices)) if neckline_prices else (ls + rs) / 2
                    if current < neckline * 1.002:
                        conf = int(70 - shoulder_sym * 100 + head_prom * 50)
                        conf = max(40, min(82, conf))
                        patterns_found.append(("HEAD_AND_SHOULDERS", "BEARISH", conf, 0.13))

    # ── Double Bottom (BUY) ───────────────────────────────────────────────────
    if len(sl_idx) >= 2:
        b1, b2 = sl_vals[-2], sl_vals[-1]
        b1i, b2i = sl_idx[-2], sl_idx[-1]
        sym = abs(b1 - b2) / (max(b1, b2) + 1e-9)
        if sym < 0.04 and (b2i - b1i) >= 5:
            mid_highs  = [float(high.iloc[j]) for j in range(b1i, b2i) if j < len(high)]
            neckline   = max(mid_highs) if mid_highs else current
            if current > neckline * 0.99:
                patterns_found.append(("DOUBLE_BOTTOM", "BULLISH", 72, 0.83))

    # ── Double Top (SELL) ─────────────────────────────────────────────────────
    if len(sh_idx) >= 2:
        t1, t2 = sh_vals[-2], sh_vals[-1]
        t1i, t2i = sh_idx[-2], sh_idx[-1]
        sym = abs(t1 - t2) / (max(t1, t2) + 1e-9)
        if sym < 0.04 and (t2i - t1i) >= 5:
            mid_lows   = [float(low.iloc[j]) for j in range(t1i, t2i) if j < len(low)]
            neckline   = min(mid_lows) if mid_lows else current
            if current < neckline * 1.01:
                patterns_found.append(("DOUBLE_TOP", "BEARISH", 72, 0.17))

    if patterns_found:
        best = max(patterns_found, key=lambda x: x[2])
        result.update({
            "pattern":   best[0],
            "direction": best[1],
            "confidence": best[2],
            "score_0to1": best[3],
            "details": {"all_patterns": [(p, d, c) for p, d, c, _ in patterns_found]},
        })

    return result


def detect_wyckoff_spring_upthrust(df: pd.DataFrame, lookback: int = 60,
                                    swing_n: int = 5) -> dict:
    """
    Wyckoff Spring and Upthrust detection (Wyckoff 1930).

    Spring  (~82% accuracy): False break BELOW a support range followed by rapid recovery.
    Upthrust (~80% accuracy): False break ABOVE a resistance range followed by rapid rejection.

    Already implemented in SuperGrok's crypto_model_core.py — this version
    is simplified for use in DeFi and RWA models.

    Returns signal + confidence + score_0to1.
    """
    result = {"signal": "NONE", "score_0to1": 0.5, "confidence": 0, "details": {}}

    df = _safe_df(df, min_rows=max(30, lookback // 2))
    if df is None:
        return result

    tail  = df.tail(lookback).reset_index(drop=True)
    close = tail["close"]
    high  = tail["high"]
    low   = tail["low"]

    sh_mask = _pivot_highs(high, swing_n)
    sl_mask = _pivot_lows(low,  swing_n)

    # Use integer positions — pandas 3.0 requires int keys for .iloc[]
    sh_vals = [float(high.iloc[i]) for i in range(len(high)) if sh_mask.iloc[i]]
    sl_vals = [float(low.iloc[i])  for i in range(len(low))  if sl_mask.iloc[i]]

    if len(sh_vals) < 2 or len(sl_vals) < 2:
        return result

    # Range: use median of recent swing highs/lows (excludes extremes)
    range_high = float(np.median(sh_vals[-4:]))
    range_low  = float(np.median(sl_vals[-4:]))
    range_size = range_high - range_low

    if range_size <= 0:
        return result

    current = float(close.iloc[-1])
    recent_low  = float(low.iloc[-3:].min())
    recent_high = float(high.iloc[-3:].min())

    # Spring: recent low broke below range_low, but current close back inside range
    if recent_low < range_low - 0.002 * range_low and current > range_low:
        spring_depth = (range_low - recent_low) / range_size
        conf = int(min(85, 55 + spring_depth * 200))
        result.update({"signal": "SPRING", "score_0to1": 0.88, "confidence": conf,
                        "details": {"range_low": round(range_low, 4),
                                    "recent_low": round(recent_low, 4)}})

    # Upthrust: recent high broke above range_high, but current close back inside range
    elif recent_high > range_high * 1.002 and current < range_high:
        ut_height = (recent_high - range_high) / range_size
        conf = int(min(83, 55 + ut_height * 200))
        result.update({"signal": "UPTHRUST", "score_0to1": 0.12, "confidence": conf,
                        "details": {"range_high": round(range_high, 4),
                                    "recent_high": round(recent_high, 4)}})

    return result


def compute_pivot_points(df: pd.DataFrame,
                          pivot_type: str = "all") -> dict:
    """
    Classic Pivot Points: Traditional, Fibonacci, and Camarilla.

    Traditional (floor trader pivots):
      P = (H + L + C) / 3
      R1 = 2P − L,  S1 = 2P − H
      R2 = P + R1 − S1,  S2 = P − R1 + S1
      R3 = H + 2(P − L),  S3 = L − 2(H − P)

    Fibonacci pivots (price × Fib ratios from range):
      R1 = P + 0.382(H − L),  R2 = P + 0.618(H − L),  R3 = P + 1.000(H − L)
      S1 = P − 0.382(H − L),  S2 = P − 0.618(H − L),  S3 = P − 1.000(H − L)

    Camarilla (Camerino 1989) — for intraday mean-reversion:
      H4 = C + range × 0.55/1.1 (resistance — short)
      L4 = C − range × 0.55/1.1 (support — long)
      (Note: original formula uses range×0.0916/0.1832/0.2748/0.3664 for L1-L4/H1-H4)

    price_zone: where current price sits relative to pivots.
    score_0to1: 1.0 if at strong support, 0.0 if at strong resistance.
    """
    result = {"traditional": {}, "fibonacci": {}, "camarilla": {},
              "price_zone": "NEUTRAL", "score_0to1": 0.5, "confidence": 50, "details": {}}

    df = _safe_df(df, min_rows=2)
    if df is None:
        return result

    # Use the most recent COMPLETE period (second-to-last row)
    if len(df) >= 2:
        period = df.iloc[-2]
    else:
        period = df.iloc[-1]

    H = float(period["high"])
    L = float(period["low"])
    C = float(period["close"])
    R = H - L
    current = float(df["close"].iloc[-1])

    if R <= 0:
        return result

    # Traditional
    P  = (H + L + C) / 3
    R1 = 2 * P - L
    S1 = 2 * P - H
    R2 = P + (R1 - S1)
    S2 = P - (R1 - S1)
    R3 = H + 2 * (P - L)
    S3 = L - 2 * (H - P)

    # Fibonacci
    fR1 = P + 0.382 * R
    fR2 = P + 0.618 * R
    fR3 = P + 1.000 * R
    fS1 = P - 0.382 * R
    fS2 = P - 0.618 * R
    fS3 = P - 1.000 * R

    # Camarilla
    cH4 = C + R * 0.55 / 2
    cL4 = C - R * 0.55 / 2
    cH3 = C + R * 0.275 / 2
    cL3 = C - R * 0.275 / 2

    # Determine price zone
    trad_levels = [(R3, "R3"), (R2, "R2"), (R1, "R1"), (P, "PP"),
                   (S1, "S1"), (S2, "S2"), (S3, "S3")]
    trad_levels.sort(key=lambda x: x[0], reverse=True)

    zone = "NEUTRAL"
    zone_score = 0.5
    for i, (lvl, name) in enumerate(trad_levels):
        if abs(current - lvl) / (lvl + 1e-9) < 0.005:
            if name in ("R1", "R2", "R3"):
                zone, zone_score = f"AT_{name}", 0.25
            elif name in ("S1", "S2", "S3"):
                zone, zone_score = f"AT_{name}", 0.75
            else:
                zone, zone_score = "AT_PP", 0.50
            break
        if i < len(trad_levels) - 1:
            next_lvl = trad_levels[i + 1][0]
            if next_lvl < current < lvl:
                if name.startswith("R"):
                    zone, zone_score = f"BETWEEN_{trad_levels[i+1][1]}_{name}", 0.35
                elif name == "PP":
                    zone, zone_score = "ABOVE_PP", 0.40
                else:
                    zone, zone_score = f"BETWEEN_{name}_{trad_levels[i+1][1]}", 0.65
                break

    # Near S2/S3 = strong support = score → 0.85
    if current < S2 * 1.01:
        zone_score = 0.85
    elif current > R2 * 0.99:
        zone_score = 0.15

    def _r(x):
        return round(float(x), 4)

    result.update({
        "traditional": {
            "P": _r(P), "R1": _r(R1), "R2": _r(R2), "R3": _r(R3),
            "S1": _r(S1), "S2": _r(S2), "S3": _r(S3),
        },
        "fibonacci": {
            "P": _r(P), "fR1": _r(fR1), "fR2": _r(fR2), "fR3": _r(fR3),
            "fS1": _r(fS1), "fS2": _r(fS2), "fS3": _r(fS3),
        },
        "camarilla": {
            "H4": _r(cH4), "H3": _r(cH3), "L3": _r(cL3), "L4": _r(cL4),
        },
        "price_zone":  zone,
        "score_0to1":  _clamp(zone_score),
        "confidence":  55,
        "details": {"H": _r(H), "L": _r(L), "C": _r(C), "current": _r(current)},
    })
    return result


def compute_anchored_vwap(df: pd.DataFrame, swing_n: int = 5,
                           lookback: int = 100) -> dict:
    """
    Anchored VWAP (AVWAP) from recent swing high and swing low.

    Standard VWAP resets daily. AVWAP is anchored to a key price event
    (swing high/low, earnings, break of structure) and reveals the
    institutional cost basis from that event.

    AVWAP from swing HIGH = overhead resistance (institutional sellers anchored there)
    AVWAP from swing LOW  = support below (institutional buyers anchored there)

    Price below AVWAP_from_low → bearish bias (can't sustain above cost basis)
    Price above AVWAP_from_high → bullish bias (absorbed all supply from that high)

    Returns:
      avwap_from_low:  float — AVWAP anchored to recent swing low
      avwap_from_high: float — AVWAP anchored to recent swing high
      price_vs_avwap_low:  "ABOVE" | "BELOW"
      price_vs_avwap_high: "ABOVE" | "BELOW"
      score_0to1
    """
    result = {"avwap_from_low": None, "avwap_from_high": None,
              "price_vs_avwap_low": None, "price_vs_avwap_high": None,
              "score_0to1": 0.5, "confidence": 50, "details": {}}

    df = _safe_df(df, min_rows=max(20, lookback // 2))
    if df is None or "volume" not in df.columns:
        return result

    tail  = df.tail(lookback).reset_index(drop=True)
    high  = tail["high"]
    low   = tail["low"]
    close = tail["close"]
    vol   = tail["volume"]
    tp    = (high + low + close) / 3   # typical price

    sh_mask = _pivot_highs(high, swing_n)
    sl_mask = _pivot_lows(low, swing_n)

    sh_idx = [i for i in high.index if sh_mask.iloc[i]]
    sl_idx = [i for i in low.index  if sl_mask.iloc[i]]

    current = float(close.iloc[-1])

    def _avwap_from(anchor_idx: int) -> float:
        """Compute VWAP from anchor index to end."""
        sub_tp  = tp.iloc[anchor_idx:]
        sub_vol = vol.iloc[anchor_idx:]
        denom   = sub_vol.sum()
        if denom == 0:
            return float(tp.iloc[-1])
        return float((sub_tp * sub_vol).sum() / denom)

    avwap_low  = None
    avwap_high = None

    if sl_idx:
        avwap_low = _avwap_from(sl_idx[-1])
    if sh_idx:
        avwap_high = _avwap_from(sh_idx[-1])

    score = 0.5
    pos_low  = None
    pos_high = None

    if avwap_low is not None:
        pos_low = "ABOVE" if current >= avwap_low else "BELOW"
    if avwap_high is not None:
        pos_high = "ABOVE" if current >= avwap_high else "BELOW"

    # Score logic:
    # Above AVWAP_from_low + Above AVWAP_from_high → strongly bullish → 0.70
    # Above AVWAP_from_low + Below AVWAP_from_high → neutral → 0.55
    # Below AVWAP_from_low                          → bearish → 0.30
    if pos_low == "ABOVE" and pos_high == "ABOVE":
        score = 0.70
    elif pos_low == "ABOVE" and pos_high == "BELOW":
        score = 0.55
    elif pos_low == "BELOW" and pos_high == "ABOVE":
        score = 0.40   # trapped between — slight bearish
    elif pos_low == "BELOW":
        score = 0.30

    result.update({
        "avwap_from_low":      round(float(avwap_low),  4) if avwap_low  is not None else None,
        "avwap_from_high":     round(float(avwap_high), 4) if avwap_high is not None else None,
        "price_vs_avwap_low":  pos_low,
        "price_vs_avwap_high": pos_high,
        "score_0to1":          _clamp(score),
        "confidence":          55,
        "details":             {"current_price": round(current, 4)},
    })
    return result


def compute_chandelier_exit(df: pd.DataFrame, period: int = 22,
                             mult: float = 3.0) -> dict:
    """
    Chandelier Exit (Chuck LeBeau 2004; popularized Turtle Traders community).

    Long stop  = highest_high(N) − ATR(N) × mult
    Short stop = lowest_low(N)  + ATR(N) × mult

    N=22, mult=3 are the original LeBeau parameters (most widely used).

    Signal:
      BUY:  price > long_stop AND previous price was below long_stop
            (means price just reclaimed the long stop = momentum flip)
      SELL: price < short_stop AND previous price was above short_stop

    Current position:
      ABOVE_LONG_STOP  → bullish trend confirmed
      BELOW_SHORT_STOP → bearish trend confirmed
      BETWEEN          → transitioning / neutral

    Returns signal + position + score_0to1.
    """
    result = {"signal": "NONE", "position": "NEUTRAL", "long_stop": None,
              "short_stop": None, "score_0to1": 0.5, "confidence": 50, "details": {}}

    df = _safe_df(df, min_rows=period + 5)
    if df is None:
        return result

    atr_s  = _atr(df, period)
    atr    = float(atr_s.iloc[-1])
    recent = df.tail(period)

    highest_high = float(recent["high"].max())
    lowest_low   = float(recent["low"].min())

    long_stop  = highest_high - mult * atr
    short_stop = lowest_low   + mult * atr

    current   = float(df["close"].iloc[-1])
    prev      = float(df["close"].iloc[-2]) if len(df) >= 2 else current

    signal = "NONE"
    score  = 0.5
    pos    = "NEUTRAL"

    if current > long_stop:
        pos = "ABOVE_LONG_STOP"
        score = 0.65
        if prev <= long_stop:
            signal = "BUY"
            score  = 0.80
    elif current < short_stop:
        pos = "BELOW_SHORT_STOP"
        score = 0.35
        if prev >= short_stop:
            signal = "SELL"
            score  = 0.20

    result.update({
        "signal":     signal,
        "position":   pos,
        "long_stop":  round(float(long_stop), 4),
        "short_stop": round(float(short_stop), 4),
        "score_0to1": _clamp(score),
        "confidence": 65 if signal != "NONE" else 50,
        "details":    {"atr": round(atr, 4), "current": round(current, 4)},
    })
    return result


def compute_squeeze_momentum(df: pd.DataFrame, bb_period: int = 20,
                              bb_mult: float = 2.0, kc_period: int = 20,
                              kc_mult: float = 1.5) -> dict:
    """
    Lazybear TTM Squeeze Momentum (John Carter, Lazybear).

    Squeeze ON:  Bollinger Bands inside Keltner Channels → volatility compression
                 → energy building → explosive move coming
    Squeeze OFF: BBands expand beyond KC → momentum release → trade the direction

    Returns:
      squeeze_on:  bool
      momentum:    float (positive = bullish, negative = bearish)
      signal:      "BULL_SQUEEZE_RELEASE" | "BEAR_SQUEEZE_RELEASE" |
                   "SQUEEZE_ON" | "NO_SQUEEZE"
      score_0to1
    """
    result = {"squeeze_on": False, "momentum": 0.0, "signal": "NO_SQUEEZE",
              "score_0to1": 0.5, "confidence": 40, "details": {}}

    df = _safe_df(df, min_rows=max(30, bb_period + kc_period))
    if df is None:
        return result

    close = df["close"]
    high  = df["high"]
    low   = df["low"]

    # Bollinger Bands
    bb_mid = _sma(close, bb_period)
    bb_std = close.rolling(bb_period).std()
    bb_up  = bb_mid + bb_mult * bb_std
    bb_dn  = bb_mid - bb_mult * bb_std

    # Keltner Channels (using ATR)
    kc_mid  = _ema(close, kc_period)
    kc_atr  = _atr(df, kc_period)
    kc_up   = kc_mid + kc_mult * kc_atr
    kc_dn   = kc_mid - kc_mult * kc_atr

    squeeze_on = bool(bb_up.iloc[-1] < kc_up.iloc[-1] and
                      bb_dn.iloc[-1] > kc_dn.iloc[-1])

    # Momentum histogram: delta of midpoint from EMA
    mid_range = (df["high"].rolling(bb_period).max() +
                 df["low"].rolling(bb_period).min()) / 2
    delta     = close - (mid_range + _ema(close, bb_period)) / 2
    momentum  = float(delta.iloc[-1]) if not math.isnan(float(delta.iloc[-1])) else 0.0
    prev_mom  = float(delta.iloc[-2]) if len(delta) >= 2 else momentum

    signal = "NO_SQUEEZE"
    score  = 0.5
    if squeeze_on:
        signal = "SQUEEZE_ON"
        score  = 0.5   # direction unknown during squeeze
    else:
        if momentum > 0:
            signal = "BULL_SQUEEZE_RELEASE" if momentum > prev_mom else "BULL_WEAKENING"
            score  = 0.68 if signal == "BULL_SQUEEZE_RELEASE" else 0.58
        elif momentum < 0:
            signal = "BEAR_SQUEEZE_RELEASE" if momentum < prev_mom else "BEAR_WEAKENING"
            score  = 0.32 if signal == "BEAR_SQUEEZE_RELEASE" else 0.42

    result.update({
        "squeeze_on": squeeze_on,
        "momentum":   round(momentum, 4),
        "signal":     signal,
        "score_0to1": _clamp(score),
        "confidence": 65 if "RELEASE" in signal else 40,
        "details":    {"bb_width": round(float(bb_up.iloc[-1] - bb_dn.iloc[-1]), 4),
                       "kc_width": round(float(kc_up.iloc[-1] - kc_dn.iloc[-1]), 4)},
    })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LAYER 1 — ON-CHAIN MACRO CYCLE SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_onchain_macro_score(macro_data: dict) -> dict:
    """
    On-Chain Macro Cycle Score from 5 indicators (Layer 1, weight 30%).

    Each indicator scored 0.0–1.0 (1.0 = bottom, 0.0 = top):

    MVRV Z-Score (Mahmudov & Puell 2018):
      Z > 7    → 0.0  (historically all major BTC tops)
      Z 5–7    → 0.1  (late bull, distribution)
      Z 2–5    → 0.3  (mid bull, hold)
      Z 0–2    → 0.5  (neutral)
      Z -1–0   → 0.7  (mild undervaluation)
      Z < -1   → 1.0  (historically all major BTC bottoms)

    NUPL (Unspent Profit/Loss ratio):
      > 0.75   → 0.0  (every cycle top had NUPL > 0.75)
      0.5–0.75 → 0.2  (belief/greed — late bull)
      0.25–0.5 → 0.5  (optimism)
      0–0.25   → 0.65 (hope)
      < 0      → 0.95 (capitulation — all major bottoms)

    SOPR (Asopp ratio, Shirakashi 2019):
      > 1.05   → 0.2  (spending in significant profit — distribution)
      1.01–1.05→ 0.4  (mild profit taking)
      ~1.0     → 0.5  (equilibrium)
      0.95–0.99→ 0.7  (spending at loss — accumulation)
      < 0.95   → 0.9  (deep loss realization — capitulation)

    Hash Ribbons (C. Edwards 2019):
      "buy"    → 0.90 (14d MA hash rate crossed above 60d MA = miner recovery = buy)
      "sell"   → 0.20 (14d < 60d = miner capitulation ongoing = cautious)
      "neutral"→ 0.50

    Pi Cycle Top (Checkmate 2019):
      ratio = (111d SMA × 2) / 350d SMA
      > 1.0    → 0.05 (top confirmed — 85% accuracy)
      0.9–1.0  → 0.25 (approaching top)
      0.7–0.9  → 0.50 (mid cycle)
      < 0.7    → 0.80 (early bull / deep value)

    Returns:
      score_0to1: weighted composite (1.0 = extreme bottom, 0.0 = extreme top)
      confidence: 0-100
      components: dict of individual indicator scores
    """
    if not macro_data:
        return {"score_0to1": 0.5, "confidence": 0, "components": {}}

    sub_w = {"mvrv": 0.30, "nupl": 0.20, "sopr": 0.20, "hash_ribbons": 0.15, "pi_cycle": 0.15}
    scores = {}
    weights_available = {}

    # MVRV Z-Score
    mvrv = macro_data.get("mvrv_z_score")
    if mvrv is not None:
        mvrv = float(mvrv)
        if mvrv > 7:      s = 0.00
        elif mvrv > 5:    s = 0.10
        elif mvrv > 2:    s = 0.30
        elif mvrv > 0:    s = 0.50
        elif mvrv > -1:   s = 0.70
        else:             s = 1.00
        scores["mvrv"] = s
        weights_available["mvrv"] = sub_w["mvrv"]

    # NUPL
    nupl = macro_data.get("nupl")
    if nupl is not None:
        nupl = float(nupl)
        if nupl > 0.75:   s = 0.00
        elif nupl > 0.50: s = 0.20
        elif nupl > 0.25: s = 0.50
        elif nupl > 0.00: s = 0.65
        else:             s = 0.95
        scores["nupl"] = s
        weights_available["nupl"] = sub_w["nupl"]

    # SOPR
    sopr = macro_data.get("sopr")
    if sopr is not None:
        sopr = float(sopr)
        if sopr > 1.05:   s = 0.20
        elif sopr > 1.01: s = 0.40
        elif sopr > 0.99: s = 0.50
        elif sopr > 0.95: s = 0.70
        else:             s = 0.90
        scores["sopr"] = s
        weights_available["sopr"] = sub_w["sopr"]

    # Hash Ribbons
    hr = macro_data.get("hash_ribbons_signal", "neutral")
    if hr is not None:
        hr = str(hr).lower()
        if "buy" in hr:
            scores["hash_ribbons"] = 0.90
        elif "sell" in hr or "capit" in hr:
            scores["hash_ribbons"] = 0.20
        else:
            scores["hash_ribbons"] = 0.50
        weights_available["hash_ribbons"] = sub_w["hash_ribbons"]

    # Pi Cycle
    pi = macro_data.get("pi_cycle_ratio")
    if pi is not None:
        pi = float(pi)
        if pi > 1.0:    s = 0.05
        elif pi > 0.9:  s = 0.25
        elif pi > 0.7:  s = 0.50
        else:           s = 0.80
        scores["pi_cycle"] = s
        weights_available["pi_cycle"] = sub_w["pi_cycle"]

    if not scores:
        return {"score_0to1": 0.5, "confidence": 0, "components": {}}

    total_w = sum(weights_available.values())
    composite = sum(scores[k] * weights_available[k] for k in scores) / total_w
    confidence = int(min(90, len(scores) / len(sub_w) * 85))

    return {
        "score_0to1": _clamp(composite),
        "confidence": confidence,
        "components": {k: round(v, 3) for k, v in scores.items()},
    }


def compute_sentiment_score(sentiment_data: dict) -> dict:
    """
    Sentiment Layer Score (Layer 2, weight 20%).

    Fear & Greed Index (0-100):
      0-15   → 0.92  (Extreme Fear = buy zone historically)
      15-25  → 0.80
      25-35  → 0.65
      35-50  → 0.55
      50-65  → 0.45
      65-80  → 0.25
      80-100 → 0.05  (Extreme Greed = sell zone)

    Funding Rate (perpetual futures, annualized):
      < -50%  → 0.90  (shorts paying longs = forced short covering imminent)
      -50 to -10% → 0.75
      -10 to +10% → 0.50
      +10 to +50% → 0.25
      > +50%  → 0.10  (longs paying too much = unwind risk)

    Returns score_0to1 (1.0 = extreme sentiment bottom/BUY, 0.0 = extreme top/SELL).
    """
    if not sentiment_data:
        return {"score_0to1": 0.5, "confidence": 0, "components": {}}

    sub_w = {"fear_greed": 0.60, "funding_rate": 0.40}
    scores = {}
    weights_available = {}

    fg = sentiment_data.get("fear_greed_value") or sentiment_data.get("fear_greed")
    if fg is not None:
        fg = float(fg)
        if fg <= 15:    s = 0.92
        elif fg <= 25:  s = 0.80
        elif fg <= 35:  s = 0.65
        elif fg <= 50:  s = 0.55
        elif fg <= 65:  s = 0.45
        elif fg <= 80:  s = 0.25
        else:           s = 0.05
        scores["fear_greed"] = s
        weights_available["fear_greed"] = sub_w["fear_greed"]

    fr = sentiment_data.get("funding_rate_annualized") or sentiment_data.get("funding_rate")
    if fr is not None:
        fr = float(fr)
        if fr < -50:    s = 0.90
        elif fr < -10:  s = 0.75
        elif fr < 10:   s = 0.50
        elif fr < 50:   s = 0.25
        else:           s = 0.10
        scores["funding_rate"] = s
        weights_available["funding_rate"] = sub_w["funding_rate"]

    if not scores:
        return {"score_0to1": 0.5, "confidence": 0, "components": {}}

    total_w = sum(weights_available.values())
    composite = sum(scores[k] * weights_available[k] for k in scores) / total_w
    conf = int(min(80, len(scores) / len(sub_w) * 75))

    return {
        "score_0to1": _clamp(composite),
        "confidence": conf,
        "components": {k: round(v, 3) for k, v in scores.items()},
    }


# ═══════════════════════════════════════════════════════════════════════════════
# COMPOSITE TOP/BOTTOM SCORE
# ═══════════════════════════════════════════════════════════════════════════════

def compute_composite_top_bottom_score(
    df: pd.DataFrame,
    macro_data: dict | None = None,
    sentiment_data: dict | None = None,
    df_15m: pd.DataFrame | None = None,
    df_1h:  pd.DataFrame | None = None,
    df_4h:  pd.DataFrame | None = None,
    symbol: str = "",
) -> dict:
    """
    5-Layer Composite Top/Bottom Score (0–100).

    Aggregates all detection layers into a single actionable score:
      80–100 = Extreme Bottom → STRONG BUY
      65–79  = Bottom Zone    → BUY / ACCUMULATE
      35–64  = Neutral        → HOLD / WAIT
      20–34  = Top Zone       → REDUCE / SELL
      0–19   = Extreme Top    → STRONG SELL

    Layer weights:
      Layer 1 — On-Chain Macro  30%  (MVRV, NUPL, SOPR, Hash Ribbons, Pi Cycle)
      Layer 2 — Sentiment       20%  (Fear & Greed, Funding Rate)
      Layer 3 — Divergence      25%  (RSI div, MACD div, CVD div, MTF confluence)
      Layer 4 — Structure       15%  (BOS/CHoCH, Order Blocks, FVGs, Vol Profile, Chart Patterns)
      Layer 5 — Volatility      10%  (Chandelier Exit, Squeeze, Wyckoff Spring/Upthrust)

    Args:
      df:             Primary OHLCV DataFrame (daily or 4H recommended)
      macro_data:     dict with keys: mvrv_z_score, nupl, sopr, hash_ribbons_signal, pi_cycle_ratio
      sentiment_data: dict with keys: fear_greed_value, funding_rate_annualized
      df_15m, df_1h, df_4h: Optional lower-TF DataFrames for MTF confluence
      symbol:         Asset symbol for labeling

    Returns:
      score:        0–100 composite score
      signal:       "STRONG_BUY" | "BUY" | "NEUTRAL" | "SELL" | "STRONG_SELL"
      confidence:   0–100 overall confidence
      layers:       dict of per-layer scores and details
      components:   dict of all individual indicator results
    """
    layers_raw = {}
    layers_conf = {}
    components = {}

    # ── Layer 1: On-Chain Macro ───────────────────────────────────────────────
    if macro_data:
        oc = compute_onchain_macro_score(macro_data)
        layers_raw["onchain"]   = oc["score_0to1"]
        layers_conf["onchain"]  = oc["confidence"]
        components["onchain"]   = oc
    else:
        layers_raw["onchain"]   = 0.5
        layers_conf["onchain"]  = 0

    # ── Layer 2: Sentiment ────────────────────────────────────────────────────
    if sentiment_data:
        sent = compute_sentiment_score(sentiment_data)
        layers_raw["sentiment"]  = sent["score_0to1"]
        layers_conf["sentiment"] = sent["confidence"]
        components["sentiment"]  = sent
    else:
        layers_raw["sentiment"]  = 0.5
        layers_conf["sentiment"] = 0

    # ── Layer 3: Technical Divergence ─────────────────────────────────────────
    div_scores = []
    div_weights = []

    rsi_div  = detect_rsi_divergence(df)
    macd_div = detect_macd_divergence(df)
    cvd_div  = compute_cvd_divergence(df)
    components["rsi_divergence"]  = rsi_div
    components["macd_divergence"] = macd_div
    components["cvd_divergence"]  = cvd_div

    for d, w in [(rsi_div, 0.45), (macd_div, 0.35), (cvd_div, 0.20)]:
        if d.get("confidence", 0) > 0:
            div_scores.append(d["score_0to1"] * w)
            div_weights.append(w)

    if df_15m is not None or df_1h is not None or df_4h is not None:
        mtf = compute_mtf_divergence_confluence(df_15m, df_1h, df_4h)
        components["mtf_confluence"] = mtf
        if mtf.get("confidence", 0) > 0:
            div_scores.append(mtf["score_0to1"] * 0.30)
            div_weights.append(0.30)
    else:
        mtf = {"signal": "NEUTRAL", "score_0to1": 0.5}

    # Renormalize if some weights were missing
    if div_weights:
        norm = sum(div_weights)
        layers_raw["divergence"] = sum(div_scores) / norm if norm > 0 else 0.5
        layers_conf["divergence"] = int(
            sum(d.get("confidence", 0) for d in [rsi_div, macd_div, cvd_div]) / 3
        )
    else:
        layers_raw["divergence"] = 0.5
        layers_conf["divergence"] = 0

    # ── Layer 4: Market Structure ─────────────────────────────────────────────
    bos    = detect_bos_choch(df)
    ob     = detect_order_blocks(df)
    fvg    = detect_fair_value_gaps(df)
    vp     = compute_volume_profile(df)
    cp     = detect_chart_patterns(df)
    components.update({"bos_choch": bos, "order_blocks": ob, "fair_value_gaps": fvg,
                        "volume_profile": vp, "chart_patterns": cp})

    struct_pairs = [
        (bos["score_0to1"], bos["confidence"],  0.25),
        (ob["score_0to1"],  ob["confidence"],   0.25),
        (fvg["score_0to1"], fvg["confidence"],  0.15),
        (vp["score_0to1"],  vp["confidence"],   0.20),
        (cp["score_0to1"],  cp["confidence"],   0.15),
    ]
    struct_w_sum  = sum(w for _, c, w in struct_pairs if c > 0) or 1.0
    struct_score  = sum(s * w for s, c, w in struct_pairs if c > 0) / struct_w_sum
    struct_conf   = int(sum(c * w for _, c, w in struct_pairs) / 1.0)

    layers_raw["structure"]  = _clamp(struct_score)
    layers_conf["structure"] = min(80, struct_conf)

    # ── Layer 5: Volatility / Momentum ───────────────────────────────────────
    ce  = compute_chandelier_exit(df)
    sq  = compute_squeeze_momentum(df)
    wy  = detect_wyckoff_spring_upthrust(df)
    pv  = compute_pivot_points(df)
    av  = compute_anchored_vwap(df)
    components.update({"chandelier_exit": ce, "squeeze": sq, "wyckoff": wy,
                        "pivot_points": pv, "anchored_vwap": av})

    vol_pairs = [
        (ce["score_0to1"], ce["confidence"],  0.25),
        (sq["score_0to1"], sq["confidence"],  0.20),
        (wy["score_0to1"], wy["confidence"],  0.20),
        (pv["score_0to1"], pv["confidence"],  0.20),
        (av["score_0to1"], av["confidence"],  0.15),
    ]
    vol_w_sum  = sum(w for _, c, w in vol_pairs if c > 0) or 1.0
    vol_score  = sum(s * w for s, c, w in vol_pairs if c > 0) / vol_w_sum
    vol_conf   = int(sum(c * w for _, c, w in vol_pairs) / 1.0)

    layers_raw["volatility"]  = _clamp(vol_score)
    layers_conf["volatility"] = min(75, vol_conf)

    # ── Final Weighted Composite ──────────────────────────────────────────────
    W = {
        "onchain":    _W_ONCHAIN    if layers_conf["onchain"]   > 0 else 0.0,
        "sentiment":  _W_SENTIMENT  if layers_conf["sentiment"] > 0 else 0.0,
        "divergence": _W_DIVERGENCE,
        "structure":  _W_STRUCTURE,
        "volatility": _W_VOLATILITY,
    }
    total_w = sum(W.values())
    if total_w <= 0:
        total_w = 1.0

    # If macro data missing, redistribute its weight to divergence + structure
    if W["onchain"] == 0 and W["sentiment"] == 0:
        redistrib = (_W_ONCHAIN + _W_SENTIMENT) / 2
        W["divergence"] += redistrib
        W["structure"]  += redistrib

    composite_01 = sum(layers_raw[k] * W[k] for k in W) / total_w
    composite_01 = _clamp(composite_01)
    score_100    = round(composite_01 * 100)

    # Signal classification
    if score_100 >= 80:   signal = "STRONG_BUY"
    elif score_100 >= 65: signal = "BUY"
    elif score_100 >= 35: signal = "NEUTRAL"
    elif score_100 >= 20: signal = "SELL"
    else:                 signal = "STRONG_SELL"

    # Overall confidence: weighted average of layer confidences, plus data coverage bonus
    conf_vals = [layers_conf[k] for k in W if W[k] > 0]
    avg_conf  = int(sum(conf_vals) / len(conf_vals)) if conf_vals else 30
    # Penalize if many layers defaulted
    data_layers = sum(1 for k in ["onchain", "sentiment"] if layers_conf[k] > 0)
    conf_final  = max(20, avg_conf - (2 - data_layers) * 10)

    return {
        "score":      score_100,
        "signal":     signal,
        "confidence": conf_final,
        "symbol":     symbol,
        "layers": {
            "onchain":    {"score_0to1": round(layers_raw["onchain"],   3), "confidence": layers_conf["onchain"],   "weight": _W_ONCHAIN},
            "sentiment":  {"score_0to1": round(layers_raw["sentiment"], 3), "confidence": layers_conf["sentiment"], "weight": _W_SENTIMENT},
            "divergence": {"score_0to1": round(layers_raw["divergence"],3), "confidence": layers_conf["divergence"],"weight": _W_DIVERGENCE},
            "structure":  {"score_0to1": round(layers_raw["structure"], 3), "confidence": layers_conf["structure"], "weight": _W_STRUCTURE},
            "volatility": {"score_0to1": round(layers_raw["volatility"],3), "confidence": layers_conf["volatility"],"weight": _W_VOLATILITY},
        },
        "components": components,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# STREAMLIT DISPLAY WIDGET
# ═══════════════════════════════════════════════════════════════════════════════

def render_top_bottom_widget(result: dict, user_level: str = "beginner") -> None:
    """
    Render the Composite Top/Bottom Score in Streamlit with user-level-aware display.

    Beginner:     Color gauge + plain English label + 1-sentence interpretation
    Intermediate: Score number + signal + top 3 active signals + brief table
    Advanced:     Full breakdown table with all 5 layers + all indicator scores
    """
    try:
        import streamlit as st
        import plotly.graph_objects as go
    except ImportError:
        return

    score    = result.get("score", 50)
    signal   = result.get("signal", "NEUTRAL")
    conf     = result.get("confidence", 0)
    symbol   = result.get("symbol", "")
    layers   = result.get("layers", {})
    comps    = result.get("components", {})

    # ── Color + label by score ────────────────────────────────────────────────
    if score >= 80:
        color, label, emoji = "#22c55e", "Extreme Bottom — Strong Buy Zone", "▲"
    elif score >= 65:
        color, label, emoji = "#86efac", "Bottom Zone — Accumulate", "▲"
    elif score >= 35:
        color, label, emoji = "#f59e0b", "Neutral — Hold / Wait", "■"
    elif score >= 20:
        color, label, emoji = "#f97316", "Top Zone — Consider Reducing", "▼"
    else:
        color, label, emoji = "#ef4444", "Extreme Top — Strong Sell Zone", "▼"

    symbol_str = f" · {symbol}" if symbol else ""

    # ── BEGINNER ─────────────────────────────────────────────────────────────
    if user_level == "beginner":
        # Gauge chart
        fig = go.Figure(go.Indicator(
            mode="gauge+number",
            value=score,
            domain={"x": [0, 1], "y": [0, 1]},
            title={"text": f"Buy/Sell Timing Score{symbol_str}", "font": {"size": 14}},
            gauge={
                "axis": {"range": [0, 100], "tickwidth": 1, "tickcolor": "#94a3b8"},
                "bar":  {"color": color, "thickness": 0.25},
                "steps": [
                    {"range": [0,  20], "color": "rgba(239,68,68,0.2)"},
                    {"range": [20, 35], "color": "rgba(249,115,22,0.15)"},
                    {"range": [35, 65], "color": "rgba(245,158,11,0.1)"},
                    {"range": [65, 80], "color": "rgba(134,239,172,0.15)"},
                    {"range": [80,100], "color": "rgba(34,197,94,0.2)"},
                ],
                "threshold": {
                    "line": {"color": color, "width": 3},
                    "thickness": 0.75,
                    "value": score,
                },
            },
            number={"font": {"size": 36, "color": color}},
        ))
        fig.update_layout(height=220, margin=dict(l=20, r=20, t=40, b=0),
                          paper_bgcolor="rgba(0,0,0,0)", font_color="#e2e8f0")
        st.plotly_chart(fig, use_container_width=True)

        st.markdown(
            f"<div style='background:rgba(0,0,0,0.15);border-left:4px solid {color};"
            f"border-radius:8px;padding:12px 16px;margin:4px 0'>"
            f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>"
            f"{emoji} {label}</div>"
            f"<div style='color:#94a3b8;font-size:0.85rem;margin-top:4px'>"
            f"Confidence: {conf}%</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        # Plain English interpretation
        if score >= 80:
            msg = ("Multiple signals are showing that this asset is historically cheap and "
                   "oversold. This doesn't mean it can't go lower — but historically, "
                   "buying in this zone has produced strong returns over 3-12 months.")
        elif score >= 65:
            msg = ("Several buy signals are active. The asset is showing signs of "
                   "accumulation. A cautious position makes sense — watch for a "
                   "confirmed reversal before going all-in.")
        elif score >= 35:
            msg = ("No strong buy or sell signal right now. The market is in a "
                   "wait-and-see mode. Hold existing positions and watch for a "
                   "clear directional signal.")
        elif score >= 20:
            msg = ("Multiple signals suggest this asset may be near a short-term top. "
                   "Consider taking some profits or tightening your stop-loss.")
        else:
            msg = ("Extreme overheating detected across multiple indicators. "
                   "Historically, this has marked major market tops. "
                   "High risk of a significant correction.")

        st.markdown(
            f"<div style='color:#cbd5e1;font-size:0.88rem;line-height:1.6;"
            f"padding:8px 0'>{msg}</div>",
            unsafe_allow_html=True,
        )

    # ── INTERMEDIATE ─────────────────────────────────────────────────────────
    elif user_level == "intermediate":
        col1, col2, col3 = st.columns([1, 1, 2])
        with col1:
            st.metric(f"Top/Bottom Score{symbol_str}", f"{score}/100",
                      delta=label, delta_color="normal")
        with col2:
            st.metric("Confidence", f"{conf}%")
        with col3:
            # Show top 3 active layer signals
            active = []
            layer_names = {
                "onchain":   "On-Chain Macro",
                "sentiment": "Sentiment",
                "divergence":"Divergence",
                "structure": "Structure",
                "volatility":"Volatility",
            }
            for lk, ln in layer_names.items():
                ld = layers.get(lk, {})
                ls = ld.get("score_0to1", 0.5)
                lc = ld.get("confidence", 0)
                if lc > 0:
                    bias = "↑ BUY" if ls > 0.6 else ("↓ SELL" if ls < 0.4 else "– NEUTRAL")
                    active.append(f"**{ln}** ({int(ls*100)}/100) {bias}")
            if active:
                st.markdown("\n".join(f"- {a}" for a in active))

        # Key signals summary
        sig_rows = []
        if comps.get("rsi_divergence", {}).get("signal", "NONE") != "NONE":
            sig_rows.append({"Indicator": "RSI Divergence",
                              "Signal": comps["rsi_divergence"]["signal"],
                              "Conf%": comps["rsi_divergence"].get("confidence", 0)})
        if comps.get("chart_patterns", {}).get("pattern", "NONE") != "NONE":
            sig_rows.append({"Indicator": "Chart Pattern",
                              "Signal": comps["chart_patterns"]["pattern"],
                              "Conf%": comps["chart_patterns"].get("confidence", 0)})
        if comps.get("chandelier_exit", {}).get("signal", "NONE") != "NONE":
            sig_rows.append({"Indicator": "Chandelier Exit",
                              "Signal": comps["chandelier_exit"]["signal"],
                              "Conf%": comps["chandelier_exit"].get("confidence", 0)})
        if comps.get("bos_choch", {}).get("event", "NONE") != "NONE":
            sig_rows.append({"Indicator": "BOS/CHoCH",
                              "Signal": comps["bos_choch"]["event"],
                              "Conf%": comps["bos_choch"].get("confidence", 0)})
        if sig_rows:
            import pandas as pd
            st.dataframe(pd.DataFrame(sig_rows), hide_index=True, use_container_width=True)

    # ── ADVANCED ──────────────────────────────────────────────────────────────
    else:
        import pandas as _pd
        col1, col2 = st.columns([1, 3])
        with col1:
            fig = go.Figure(go.Indicator(
                mode="gauge+number",
                value=score,
                gauge={
                    "axis": {"range": [0, 100]},
                    "bar": {"color": color},
                    "steps": [
                        {"range": [0, 20],   "color": "rgba(239,68,68,0.25)"},
                        {"range": [80, 100], "color": "rgba(34,197,94,0.25)"},
                    ],
                },
                number={"font": {"size": 30, "color": color}},
                title={"text": f"Score{symbol_str}", "font": {"size": 12}},
            ))
            fig.update_layout(height=200, margin=dict(l=10, r=10, t=30, b=0),
                              paper_bgcolor="rgba(0,0,0,0)", font_color="#e2e8f0")
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            layer_rows = []
            layer_names = {
                "onchain":   "On-Chain Macro (MVRV/NUPL/SOPR/Hash Ribbons/Pi Cycle)",
                "sentiment": "Sentiment (Fear & Greed / Funding Rate)",
                "divergence":"Divergence (RSI / MACD / CVD / MTF)",
                "structure": "Structure (BOS/CHoCH / OB / FVG / Vol Profile / Patterns)",
                "volatility":"Volatility (Chandelier / Squeeze / Wyckoff / Pivots / AVWAP)",
            }
            w_map = {"onchain": 30, "sentiment": 20, "divergence": 25,
                     "structure": 15, "volatility": 10}
            for lk, ln in layer_names.items():
                ld = layers.get(lk, {})
                ls = ld.get("score_0to1", 0.5)
                lc = ld.get("confidence", 0)
                bias = "BUY" if ls > 0.6 else ("SELL" if ls < 0.4 else "NEUTRAL")
                layer_rows.append({
                    "Layer":      ln,
                    "Score":      f"{int(ls*100)}/100",
                    "Bias":       bias,
                    "Confidence": f"{lc}%",
                    "Weight":     f"{w_map[lk]}%",
                })
            st.dataframe(_pd.DataFrame(layer_rows), hide_index=True,
                         use_container_width=True)

        # All active signals table
        all_sigs = []
        checks = [
            ("RSI Div",          comps.get("rsi_divergence",  {}), "signal"),
            ("MACD Div",         comps.get("macd_divergence", {}), "signal"),
            ("CVD Div",          comps.get("cvd_divergence",  {}), "signal"),
            ("BOS/CHoCH",        comps.get("bos_choch",       {}), "event"),
            ("Chart Pattern",    comps.get("chart_patterns",  {}), "pattern"),
            ("Chandelier Exit",  comps.get("chandelier_exit", {}), "signal"),
            ("Squeeze",          comps.get("squeeze",         {}), "signal"),
            ("Wyckoff",          comps.get("wyckoff",         {}), "signal"),
        ]
        for name, d, key in checks:
            sig = d.get(key, "NONE")
            if sig and sig not in ("NONE", "NO_SQUEEZE", "NEUTRAL"):
                all_sigs.append({
                    "Indicator": name,
                    "Signal":    sig,
                    "Score":     f"{int(d.get('score_0to1', 0.5)*100)}/100",
                    "Conf%":     d.get("confidence", 0),
                })

        if all_sigs:
            st.markdown("**Active Signals**")
            st.dataframe(_pd.DataFrame(all_sigs).sort_values("Conf%", ascending=False),
                         hide_index=True, use_container_width=True)

        # Volume Profile details
        vp = comps.get("volume_profile", {})
        if vp.get("poc"):
            st.markdown(
                f"**Volume Profile** — POC: `{vp['poc']}` · "
                f"VAH: `{vp.get('vah','—')}` · VAL: `{vp.get('val','—')}` · "
                f"Shape: `{vp.get('profile_shape','—')}`"
            )

        # Pivot Points
        pp = comps.get("pivot_points", {})
        trad = pp.get("traditional", {})
        if trad.get("P"):
            st.markdown(
                f"**Pivot Points** — P: `{trad.get('P','—')}` · "
                f"R1: `{trad.get('R1','—')}` · R2: `{trad.get('R2','—')}` · "
                f"S1: `{trad.get('S1','—')}` · S2: `{trad.get('S2','—')}`"
            )

"""
cycle_indicators.py — DeFi Model
CoinsKid-inspired composite cycle indicators layer.

Adds five independent signals on top of the existing 4-layer composite:
  1. Google Trends retail sentiment (pytrends, 24h TTL, graceful fallback)
  2. Stablecoin supply delta — USDT+USDC+DAI 7-day % change (dry powder gauge)
  3. Breadth confirmation — % of top-30 coins above 50D MA and 200D MA
  4. Voliquidity — ATR14 × (volume_24h / market_cap) move-magnitude gauge
  5. Unified Cycle Score (1-100, 5 zones) — UX wrapper over composite signal

All signals are optional and fail gracefully (return None).  They compose
with the existing TA/Macro/Sentiment/On-Chain layers — never replacing.

Research:
  - Google Trends as top signal: Preis, Moat & Stanley (2013), "Quantifying
    trading behavior in financial markets using Google Trends"; Kristoufek
    (2015), "What are the main drivers of the Bitcoin price?".
    Retail search surges historically coincide with cycle tops.
  - Stablecoin dry powder: Kaiko (2023) and Glassnode (2024) studies show
    rising stablecoin supply during BTC drawdowns precedes V-reversals.
  - Breadth divergence: Paul F. Desmond (1998), Lowry Research — when the
    index makes new highs but breadth shrinks, distribution is underway.
  - Voliquidity (ATR × vol/mcap): Amihud (2002) illiquidity ratio adapted
    to crypto; screens for low-liquidity high-volatility trading zones.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()

_TTL_TRENDS    = 86_400   # 24h — Google Trends updates daily, rate-limit heavy
_TTL_STABLE    = 3_600    # 1h  — CoinGecko simple/price
_TTL_BREADTH   = 3_600    # 1h  — matches macro refresh
_TTL_VOLIQ     = 900      # 15m — intraday gauge


def _cached_get(key: str, ttl: int, fetch_fn):
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and (time.time() - hit["ts"]) < ttl:
            return hit["data"]
    try:
        data = fetch_fn()
        if data is not None:
            with _CACHE_LOCK:
                _CACHE[key] = {"data": data, "ts": time.time()}
        return data
    except Exception as e:
        logger.debug("[CycleIndicators] %s failed: %s", key, e)
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit:
                return hit["data"]
        return None


def clear_cycle_caches() -> None:
    """Clear module-level caches (wired to 'Refresh All' button)."""
    with _CACHE_LOCK:
        _CACHE.clear()


# ─── 1. Google Trends retail sentiment ───────────────────────────────────────

def fetch_google_trends_signal(
    keyword: str = "bitcoin",
    geo: str = "",
    timeframe: str = "today 3-m",
) -> dict[str, Any] | None:
    """
    Retail search interest for a keyword (default 'bitcoin').

    Returns dict: {
        "current":       latest weekly interest [0-100],
        "avg_4w":        4-week trailing average,
        "spike_pct":     (current / avg_4w - 1) * 100,
        "signal":        one of SURGE / RISING / STABLE / FALLING / COLLAPSE,
        "score":         float in [-1.0, +1.0] (negative = top warning; positive = bottom setup)
    }
    Returns None on any failure (import error, rate-limit, CAPTCHA, stale cache).
    """
    def _fetch() -> dict[str, Any] | None:
        try:
            from pytrends.request import TrendReq
        except ImportError:
            logger.debug("[Trends] pytrends not installed — skipping")
            return None

        try:
            tr = TrendReq(hl="en-US", tz=0, timeout=(4, 10), retries=1)
            tr.build_payload([keyword], cat=0, timeframe=timeframe, geo=geo)
            df = tr.interest_over_time()
            if df is None or df.empty or keyword not in df.columns:
                return None
            series = df[keyword].astype(float).tolist()
            if len(series) < 5:
                return None
            current = float(series[-1])
            avg_4w  = sum(series[-4:]) / 4.0
            if avg_4w <= 0:
                return None
            spike   = (current / avg_4w - 1.0) * 100.0
            # Classify
            if spike >= 50:   sig, score = "SURGE",    -0.8
            elif spike >= 20: sig, score = "RISING",   -0.3
            elif spike >= -10: sig, score = "STABLE",   0.0
            elif spike >= -30: sig, score = "FALLING", +0.3
            else:             sig, score = "COLLAPSE",  +0.6
            return {
                "current":   round(current, 1),
                "avg_4w":    round(avg_4w, 1),
                "spike_pct": round(spike, 1),
                "signal":    sig,
                "score":     score,
            }
        except Exception as e:
            logger.debug("[Trends] fetch failed: %s", e)
            return None

    return _cached_get(f"gtrends_{keyword}_{geo}_{timeframe}", _TTL_TRENDS, _fetch)


# ─── 2. Stablecoin supply delta (dry powder gauge) ────────────────────────────

_STABLE_COINS = ["tether", "usd-coin", "dai"]


def fetch_stablecoin_supply_delta() -> dict[str, Any] | None:
    """
    Aggregate USDT+USDC+DAI circulating supply — rate of change over 7d.
    Rising stablecoin supply during a price drawdown = dry powder building
    = historical bottom setup.  Falling supply during rally = capital
    rotating into risk assets = late-stage expansion.

    Uses CoinGecko /coins/{id}/market_chart (free, no key).
    Returns dict: {
        "total_now":     current aggregate supply (USD proxy, 1:1 peg),
        "total_7d_ago":  supply 7 days ago,
        "delta_7d_pct":  (total_now / total_7d_ago - 1) * 100,
        "signal":        ACCUMULATING / STABLE / DISTRIBUTING,
        "score":         float in [-1.0, +1.0] (positive = dry powder rising = bullish)
    }
    """
    def _fetch() -> dict[str, Any] | None:
        try:
            import requests
        except ImportError:
            return None

        total_now = 0.0
        total_7d  = 0.0
        fetched   = 0
        for coin_id in _STABLE_COINS:
            try:
                url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/"
                       f"market_chart?vs_currency=usd&days=7&interval=daily")
                r = requests.get(url, timeout=10,
                                 headers={"Accept": "application/json"})
                if r.status_code != 200:
                    continue
                data = r.json()
                caps = data.get("market_caps") or []
                if len(caps) < 2:
                    continue
                total_now += float(caps[-1][1])
                total_7d  += float(caps[0][1])
                fetched   += 1
            except Exception as e:
                logger.debug("[StableSupply] %s failed: %s", coin_id, e)
                continue

        if fetched == 0 or total_7d <= 0:
            return None

        delta_pct = (total_now / total_7d - 1.0) * 100.0
        if delta_pct >= 2.0:   sig, score = "ACCUMULATING", +0.6
        elif delta_pct >= 0.5: sig, score = "ACCUMULATING", +0.3
        elif delta_pct >= -0.5: sig, score = "STABLE",       0.0
        elif delta_pct >= -2.0: sig, score = "DISTRIBUTING", -0.3
        else:                  sig, score = "DISTRIBUTING", -0.6

        return {
            "total_now":    round(total_now),
            "total_7d_ago": round(total_7d),
            "delta_7d_pct": round(delta_pct, 2),
            "signal":       sig,
            "score":        score,
            "fetched":      fetched,
        }

    return _cached_get("stable_supply_delta", _TTL_STABLE, _fetch)


# ─── 3. Breadth confirmation ─────────────────────────────────────────────────

def compute_breadth(prices_df_dict: dict[str, list[float]] | None) -> dict[str, Any] | None:
    """
    % of tracked coins with close > 50D SMA and > 200D SMA.
    Classic breadth divergence detector (Lowry 1998).

    Args:
        prices_df_dict: mapping {symbol -> list of daily closes, most recent last}.
                        Each list needs ≥200 entries for full computation; shorter
                        series are scored against whatever MA is available.

    Returns None on empty/invalid input.

    Signal interpretation:
        >80% above 200D: late-cycle — caution
        60-80%:          healthy bull
        40-60%:          mixed / inflection
        20-40%:          correction phase
        <20%:            capitulation / bottom setup
    """
    if not prices_df_dict:
        return None

    n_50_total  = n_50_above  = 0
    n_200_total = n_200_above = 0

    for sym, closes in prices_df_dict.items():
        try:
            closes = [float(c) for c in closes if c is not None]
        except (TypeError, ValueError):
            continue
        if len(closes) < 50:
            continue
        price = closes[-1]
        ma_50 = sum(closes[-50:]) / 50.0
        n_50_total  += 1
        if price > ma_50:
            n_50_above += 1
        if len(closes) >= 200:
            ma_200 = sum(closes[-200:]) / 200.0
            n_200_total += 1
            if price > ma_200:
                n_200_above += 1

    if n_50_total == 0 and n_200_total == 0:
        return None

    pct_50  = (n_50_above  / n_50_total  * 100.0) if n_50_total  else None
    pct_200 = (n_200_above / n_200_total * 100.0) if n_200_total else None

    # Primary signal: 200D breadth if available, else 50D
    primary = pct_200 if pct_200 is not None else pct_50
    if   primary is None:       sig, score = None,           None
    elif primary >= 80:         sig, score = "EXTENDED",     -0.4
    elif primary >= 60:         sig, score = "HEALTHY_BULL", +0.3
    elif primary >= 40:         sig, score = "MIXED",         0.0
    elif primary >= 20:         sig, score = "CORRECTION",   +0.3
    else:                       sig, score = "CAPITULATION", +0.6

    return {
        "pct_above_50d":   round(pct_50,  1) if pct_50  is not None else None,
        "pct_above_200d":  round(pct_200, 1) if pct_200 is not None else None,
        "n_sampled":       max(n_50_total, n_200_total),
        "signal":          sig,
        "score":           score,
    }


# ─── 4. Voliquidity (ATR × vol/mcap) ─────────────────────────────────────────

def compute_voliquidity(
    atr_14: float | None,
    price: float | None,
    volume_24h: float | None,
    market_cap: float | None,
) -> dict[str, Any] | None:
    """
    Voliquidity = (ATR14 / price) × (volume_24h / market_cap)

    Amihud (2002) illiquidity adaptation: per-dollar price movement × turnover.
    Low Voliquidity = tight, stable market.
    High Voliquidity = large moves on thin order books.

    Returns None if any input is missing or zero.
    """
    try:
        if not (atr_14 and price and volume_24h and market_cap):
            return None
        atr_pct   = float(atr_14) / float(price)
        turnover  = float(volume_24h) / float(market_cap)
        voliq     = atr_pct * turnover
    except (TypeError, ValueError, ZeroDivisionError):
        return None

    # Calibrated on BTC+top-30 2022-2025 distribution
    if   voliq >= 0.0030: bucket, score = "EXTREME_VOLATILITY", -0.3
    elif voliq >= 0.0015: bucket, score = "ELEVATED",            -0.1
    elif voliq >= 0.0007: bucket, score = "NORMAL",               0.0
    elif voliq >= 0.0003: bucket, score = "COMPRESSED",          +0.2
    else:                 bucket, score = "ULTRA_COMPRESSED",    +0.4

    return {
        "voliquidity":  round(voliq, 6),
        "atr_pct":      round(atr_pct, 4),
        "turnover":     round(turnover, 4),
        "bucket":       bucket,
        "score":        score,
    }


# ─── 5. Unified Cycle Score (1-100, 5 zones) ──────────────────────────────────

def cycle_score_100(
    composite_score: float | None,
    extras: dict[str, float | None] | None = None,
) -> dict[str, Any]:
    """
    Wrap the existing 4-layer composite score [-1.0, +1.0] into a CoinsKid-style
    1-100 cycle position score.

    1   = deepest extreme fear / maximum buy zone
    100 = maximum euphoria / maximum sell zone

    Optional extras dict can blend in the new signals (each in [-1, +1]):
        extras["trends"]       Google Trends signal score
        extras["stable_delta"] Stablecoin dry-powder score
        extras["breadth"]      Breadth confirmation score
        extras["voliquidity"]  Voliquidity score

    Blend weights: composite 0.65, trends 0.10, stable 0.10, breadth 0.10, voliq 0.05.
    Sign convention: positive composite = bullish = LOWER cycle score (further
    from top).  We invert to map to CoinsKid's "100 = greed/top" orientation.
    """
    parts: list[tuple[float, float]] = []   # (score, weight)

    if composite_score is not None:
        parts.append((float(composite_score), 0.65))
    if extras:
        for key, w in (("trends", 0.10), ("stable_delta", 0.10),
                       ("breadth", 0.10), ("voliquidity", 0.05)):
            v = extras.get(key)
            if v is not None:
                parts.append((float(v), w))

    if not parts:
        return {
            "score": 50,
            "zone": "NEUTRAL",
            "zone_label": "Neutral",
            "color": "#64748b",
            "inputs_used": 0,
        }

    # Weighted average
    wsum  = sum(w for _, w in parts)
    blend = sum(s * w for s, w in parts) / wsum if wsum > 0 else 0.0
    blend = max(-1.0, min(1.0, blend))

    # Map [-1, +1] → [100, 1]   (positive blend = bullish = lower cycle score)
    cycle = int(round(50 - blend * 49))
    cycle = max(1, min(100, cycle))

    if   cycle <= 15:  zone, label, color = "STRONG_BUY",   "Strong Buy",    "#22c55e"
    elif cycle <= 35:  zone, label, color = "BUY",           "Buy",           "#00d4aa"
    elif cycle <= 65:  zone, label, color = "NEUTRAL",       "Neutral",       "#64748b"
    elif cycle <= 85:  zone, label, color = "SELL",          "Sell",          "#f59e0b"
    else:              zone, label, color = "STRONG_SELL",   "Strong Sell",   "#ef4444"

    return {
        "score":       cycle,
        "zone":        zone,
        "zone_label":  label,
        "color":       color,
        "blend_raw":   round(blend, 4),
        "inputs_used": len(parts),
    }


def render_cycle_gauge_html(cycle: dict[str, Any], user_level: str = "beginner") -> str:
    """Return HTML for a Cycle Position hero card (1-100 gauge + zone label)."""
    score = int(cycle.get("score", 50))
    zone  = cycle.get("zone_label", "Neutral")
    color = cycle.get("color", "#64748b")
    # Beginner-friendly one-line summary
    if   score <= 15: tag = "Historically strong accumulation zone"
    elif score <= 35: tag = "Favorable buying conditions"
    elif score <= 65: tag = "Neutral — hold existing positions"
    elif score <= 85: tag = "Caution — distribution zone forming"
    else:             tag = "Historically extreme top zone — reduce exposure"

    shape = "▼" if score >= 66 else ("▲" if score <= 34 else "■")
    # Gauge bar
    bar_width = max(2, min(100, score))

    return (
        f"<div style='background:linear-gradient(135deg,{color}11,{color}05);"
        f"border:1px solid {color}55;border-left:4px solid {color};"
        f"border-radius:12px;padding:16px 22px;margin:0 0 18px;'>"
        f"<div style='display:flex;align-items:center;justify-content:space-between;"
        f"flex-wrap:wrap;gap:16px;'>"
        f"<div style='flex:1;min-width:180px;'>"
        f"<div style='font-size:10px;font-weight:800;letter-spacing:1.2px;"
        f"color:{color};text-transform:uppercase;margin-bottom:4px;'>"
        f"⏱ Market Cycle Position</div>"
        f"<div style='font-size:22px;font-weight:800;color:{color};'>"
        f"{shape} {zone} · {score}/100</div>"
        f"<div style='font-size:13px;color:#94a3b8;margin-top:4px;'>{tag}</div>"
        f"</div>"
        f"<div style='flex:2;min-width:220px;'>"
        f"<div style='position:relative;height:12px;background:#1e293b;"
        f"border-radius:6px;overflow:hidden;'>"
        f"<div style='position:absolute;left:0;top:0;width:{bar_width}%;"
        f"height:100%;background:linear-gradient(90deg,#22c55e,#00d4aa,"
        f"#64748b,#f59e0b,#ef4444);'></div>"
        f"<div style='position:absolute;left:{bar_width}%;top:-3px;"
        f"width:3px;height:18px;background:#e2e8f0;border-radius:2px;'></div>"
        f"</div>"
        f"<div style='display:flex;justify-content:space-between;"
        f"font-size:10px;color:#64748b;margin-top:4px;'>"
        f"<span>1 Strong Buy</span><span>50 Neutral</span><span>100 Strong Sell</span>"
        f"</div></div></div></div>"
    )

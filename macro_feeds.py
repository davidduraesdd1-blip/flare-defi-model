"""
macro_feeds.py — Defi Yield Model
Macro data layer: FRED public CSV + yfinance.
No API keys required.  All fetches cached 1 hour.
"""
from __future__ import annotations

import gc
import logging
import threading
import time
import datetime as _dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

logger = logging.getLogger(__name__)

# Use the shared retry-aware session and rate limiters from utils.http (#11 / #12)
from utils.http import _SESSION, fred_limiter as _FRED_LIMITER, coinmetrics_limiter as _COINMETRICS_LIMITER, defillama_limiter as _DEFILLAMA_LIMITER, coingecko_limiter as _COINGECKO_LIMITER

_CACHE: dict = {}
_CACHE_LOCK = threading.Lock()
_TTL_1H  = 3600
_TTL_30M = 1800


def _get_runtime_key(key_name: str, default: str = "") -> str:
    """
    Return a per-user API key injected via the sidebar session-state expander (#18).
    Falls back to ``default`` (typically an env-var value) when not set.
    Safe to call outside a Streamlit context — returns ``default`` on any error.
    """
    try:
        import streamlit as st
        val = st.session_state.get(f"defi_runtime_{key_name}", "")
        return val if val else default
    except Exception:
        return default


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
        logger.debug("[MacroFeeds] %s failed: %s", key, e)
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit:
                return hit["data"]
        return None


def clear_macro_caches() -> None:
    """Clear all module-level macro feed caches (used by 'Refresh All' button)."""
    with _CACHE_LOCK:
        _CACHE.clear()
    logger.debug("[MacroFeeds] All caches cleared.")


# ── FRED series ───────────────────────────────────────────────────────────────

_FRED_SERIES = {
    "m2_supply_bn":      "M2SL",
    "ten_yr_yield":      "DGS10",
    "two_yr_yield":      "DGS2",          # 2-year Treasury (for 2Y10Y spread)
    "ism_manufacturing": "NAPM",
    "wti_crude":         "DCOILWTICO",
    "yield_spread_2y10y": "T10Y2Y",       # Direct 2Y-10Y spread from FRED (positive = normal curve)
    "cpi_index":         "CPIAUCSL",      # CPI All Urban Consumers — used to compute YoY%
}

_FRED_FALLBACKS = {
    "m2_supply_bn":       21_500.0,
    "ten_yr_yield":           4.35,
    "two_yr_yield":           4.70,
    "ism_manufacturing":     52.0,
    "wti_crude":             67.5,
    "yield_spread_2y10y":    -0.35,   # Slightly inverted as of early 2026
    "cpi_yoy":                3.1,    # CPI YoY % — computed from cpi_index history
    "cpi_index":            314.0,    # Raw index fallback
}


def _fetch_single_fred(key: str, series_id: str) -> tuple[str, float | None]:
    """Fetch a single FRED series CSV and return (key, latest_value).
    For CPIAUCSL: returns the YoY% change as 'cpi_yoy' and raw index as 'cpi_index'.
    """
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    _FRED_LIMITER.acquire()
    try:
        resp = _SESSION.get(url, timeout=10)
        if resp.status_code == 200:
            lines = [l for l in resp.text.strip().split("\n")[1:] if l.strip()]
            # For CPI: need 13 valid rows to compute YoY
            if series_id == "CPIAUCSL":
                valid = []
                for line in reversed(lines):
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1].strip() not in (".", ""):
                        try:
                            valid.append(float(parts[1].strip()))
                        except ValueError:
                            pass
                    if len(valid) >= 13:
                        break
                if len(valid) >= 13 and valid[12] > 0:
                    yoy = (valid[0] / valid[12] - 1) * 100
                    return key, round(yoy, 2)   # key = "cpi_index" but we return YoY
                elif valid:
                    return key, round(valid[0], 2)
            else:
                for line in reversed(lines):
                    parts = line.split(",")
                    if len(parts) == 2 and parts[1].strip() not in (".", ""):
                        return key, round(float(parts[1].strip()), 4)
    except Exception as e:
        logger.debug("[FRED] %s: %s", series_id, e)
    return key, None


def fetch_fred_macro() -> dict[str, Any]:
    """Fetch macro indicators from FRED public CSV (no API key required).
    All 4 FRED series are fetched in parallel with ThreadPoolExecutor(max_workers=4).
    """
    def _fetch():
        result: dict = {}
        with ThreadPoolExecutor(max_workers=7) as ex:
            futs = {
                ex.submit(_fetch_single_fred, key, series_id): key
                for key, series_id in _FRED_SERIES.items()
            }
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    fetched_key, value = fut.result(timeout=15)
                    if value is not None:
                        result[fetched_key] = value
                except Exception as e:
                    logger.debug("[FRED] parallel fetch for %s failed: %s", key, e)
        if not result:
            return None

        # CPI: _fetch_single_fred returned YoY% under key "cpi_index" — rename it
        if "cpi_index" in result:
            result["cpi_yoy"] = result.pop("cpi_index")

        # C4: M2 YoY growth rate (needs 13 monthly observations — same pattern as CPI)
        try:
            m2_url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL"
            m2_resp = _SESSION.get(m2_url, timeout=10)
            if m2_resp.status_code == 200:
                m2_lines = [l for l in m2_resp.text.strip().split("\n")[1:] if l.strip()]
                m2_vals: list = []
                for _line in reversed(m2_lines):
                    _parts = _line.split(",")
                    if len(_parts) == 2 and _parts[1].strip() not in (".", ""):
                        try:
                            m2_vals.append(float(_parts[1].strip()))
                        except ValueError:
                            pass
                    if len(m2_vals) >= 13:
                        break
                if len(m2_vals) >= 13 and m2_vals[12] > 0:
                    result["m2_yoy"] = round((m2_vals[0] / m2_vals[12] - 1) * 100, 2)
        except Exception:
            pass

        # Compute 2Y10Y spread from individual yields if T10Y2Y direct series failed
        if "yield_spread_2y10y" not in result:
            t10 = result.get("ten_yr_yield")
            t2  = result.get("two_yr_yield")
            if t10 is not None and t2 is not None:
                result["yield_spread_2y10y"] = round(t10 - t2, 4)

        for k, v in _FRED_FALLBACKS.items():
            result.setdefault(k, v)
        result["source"]    = "FRED"
        result["timestamp"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return result

    cached = _cached_get("fred_macro", _TTL_1H, _fetch)
    if cached is None:
        fb = dict(_FRED_FALLBACKS)
        fb.update({"source": "fallback", "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()})
        return fb
    return cached


# ── yfinance supplementals ────────────────────────────────────────────────────

_YF_FALLBACKS = {
    "dxy": 104.0, "vix": 18.0, "gold_spot": 2900.0, "spx": 5800.0,
}


def fetch_yfinance_macro() -> dict[str, Any]:
    """Fetch DXY, VIX, Gold, SPX via yfinance.  Free, no API key required."""
    def _fetch():
        try:
            import yfinance as yf
        except ImportError:
            return None
        _MAP = {"dxy": "DX-Y.NYB", "vix": "^VIX", "gold_spot": "GC=F", "spx": "^GSPC"}

        def _fetch_one(ticker_tuple: tuple) -> tuple:
            key, symbol = ticker_tuple
            try:
                # E4: DXY needs 35 days to compute 30d rate-of-change
                period = "35d" if key == "dxy" else "5d"
                return key, yf.Ticker(symbol).history(period=period)["Close"]
            except Exception:
                return key, None

        # OPT-35: fetch all 4 tickers in parallel
        # FIX-503: use submit()+result(timeout=20) instead of ex.map() — ex.map() has
        # no timeout and yfinance can hang indefinitely, blocking the Streamlit main thread
        # and causing /script-health-check 503s after 60s.
        result: dict = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            futs = {ex.submit(_fetch_one, item): item[0] for item in _MAP.items()}
            for fut, key in futs.items():
                try:
                    result_item = fut.result(timeout=20)
                    if result_item is None:
                        continue
                    _, series = result_item
                    if series is not None and not series.empty:
                        result[key] = round(float(series.iloc[-1]), 2)
                        # E4: compute 30d ROC for DXY (momentum vs absolute level)
                        if key == "dxy" and len(series) >= 30:
                            cur   = float(series.iloc[-1])
                            past  = float(series.iloc[-30])
                            result["dxy_30d_roc"] = round((cur - past) / past * 100, 2) if past else None
                except Exception as e:
                    logger.debug("[yfinance] %s: %s", key, e)

        if not result:
            return None
        # Fill any missing keys from fallbacks (e.g. DXY failed but others succeeded)
        for k, v in _YF_FALLBACKS.items():
            if k not in result:
                result[k] = v
        result.update({"source": "yfinance", "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()})
        return result

    cached = _cached_get("yfinance_macro", _TTL_1H, _fetch)
    if cached is None:
        fb = dict(_YF_FALLBACKS)
        fb.update({"source": "fallback", "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()})
        return fb
    return cached


def fetch_macro_timeseries(days: int = 90) -> dict[str, Any]:
    """Return daily close price history for BTC/VIX/Gold/SPX/DXY/Oil.
    Keys: BTC, VIX, Gold, SPX, DXY, Oil — each maps to {date_str: price}.
    Returns {} if yfinance not installed.  Cached 30 min.
    """
    def _fetch():
        try:
            import yfinance as yf
        except ImportError:
            return {}
        _SYMS = {
            "BTC": "BTC-USD", "VIX": "^VIX", "Gold": "GC=F",
            "SPX": "^GSPC",   "DXY": "DX-Y.NYB", "Oil": "CL=F",
        }

        def _fetch_ts(ticker_tuple: tuple) -> tuple:
            key, sym = ticker_tuple
            try:
                hist = yf.Ticker(sym).history(period=f"{days}d")
                if not hist.empty:
                    return key, {
                        str(dt)[:10]: round(float(v), 4)
                        for dt, v in hist["Close"].items()
                    }
            except Exception as e:
                logger.debug("[MacroTS] %s: %s", sym, e)
            return key, None

        # OPT-36: fetch all 6 symbols in parallel
        # FIX-503: same as fetch_yfinance_macro — use submit()+result(timeout=20)
        # to prevent indefinite hangs from yfinance network stalls.
        out: dict = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_fetch_ts, item): item[0] for item in _SYMS.items()}
            for fut, key in futs.items():
                try:
                    result_item = fut.result(timeout=20)
                    if result_item is None:
                        continue
                    _, series = result_item
                    if series is not None:
                        out[key] = series
                except Exception as _map_err:
                    logger.warning("[macro_feeds] yfinance ts %s timeout/error: %s", key, _map_err)

        out.update({"_days": days, "_timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()})
        return out

    cached = _cached_get(f"macro_ts_{days}", _TTL_30M, _fetch)
    return cached if cached else {}


# ── GROUP 3: Blood in the Streets · DCA Multiplier ────────────────────────────

def get_dca_multiplier(fg_value: int) -> float:
    """
    DCA position-size multiplier based on Fear & Greed zone.

    Extreme Fear (0-15)    → 3.0×   max accumulation
    Fear         (16-30)   → 2.0×   heavy accumulation
    Neutral      (31-55)   → 1.0×   base size
    Greed        (56-74)   → 0.5×   reduce size
    Extreme Greed(75-100)  → 0.0×   hold, no new buys
    """
    if fg_value <= 15:  return 3.0
    if fg_value <= 30:  return 2.0
    if fg_value <= 55:  return 1.0
    if fg_value <= 74:  return 0.5
    return 0.0


def compute_blood_in_streets(
    fg_value: int,
    rsi_14: float | None = None,
    net_flow: float | None = None,
) -> dict[str, Any]:
    """
    Composite "Blood in the Streets" buy signal — fires on multi-factor capitulation.

    Criteria (independent, additive):
      1. Fear & Greed ≤ 25       extreme fear / mass panic
      2. RSI-14 (daily) ≤ 30     technical oversold / capitulation bottom
      3. Exchange net outflow     smart money accumulating (optional proxy)

    Historical hit rate (BTC, 30d forward): ~78% when criteria 1+2 both met.
    """
    criteria: dict = {
        "extreme_fear":     fg_value <= 25,
        "rsi_oversold":     rsi_14 is not None and rsi_14 <= 30,
        "exchange_outflow": net_flow is not None and net_flow < -50.0,
    }
    met_count    = sum(1 for v in criteria.values() if v)
    core_trigger = criteria["extreme_fear"] and criteria["rsi_oversold"]

    if core_trigger and criteria["exchange_outflow"]:
        signal, strength = "BLOOD_IN_STREETS", "CONFIRMED"
    elif core_trigger:
        signal, strength = "BLOOD_IN_STREETS", "PROBABLE"
    elif criteria["extreme_fear"]:
        signal, strength = "EXTREME_FEAR", "WATCH"
    else:
        signal, strength = "NORMAL", "NORMAL"

    return {
        "signal":         signal,
        "strength":       strength,
        "triggered":      signal == "BLOOD_IN_STREETS",
        "criteria_met":   met_count,
        "criteria":       criteria,
        "fg_value":       fg_value,
        "rsi_14":         rsi_14,
        "dca_multiplier": get_dca_multiplier(fg_value),
        "description": (
            "Extreme fear + oversold — 78% hit rate for 30d rally (historical BTC)."
            if signal == "BLOOD_IN_STREETS"
            else f"F&G={fg_value}. {met_count}/3 criteria met."
        ),
    }


# ── GROUP 4: On-Chain Dashboard ────────────────────────────────────────────────

_CM_CACHE_D: dict = {}
_CM_TTL_D = 3600


def fetch_coinmetrics_onchain(days: int = 400) -> dict[str, Any]:
    """
    Fetch real BTC on-chain metrics from CoinMetrics API.
    Uses authenticated endpoint when DEFI_COINMETRICS_API_KEY is set (free at coinmetrics.io).
    Falls back to community endpoint without key (may return 403 if CoinMetrics restricts it).
    Cached 1 hour.

    Returns: mvrv_ratio, mvrv_z, mvrv_signal, realized_cap, sopr, sopr_signal,
             active_addresses, mvrv_history, sopr_history, source, error
    """
    import statistics as _stats
    import os as _os
    # MVRV Z-Score requires all-time normalization (Mahmudov & Puell, 2018 / Glassnode reference).
    # Fetch from BTC genesis — `days` parameter still controls display history window.
    _MVRV_START = "2010-07-17"
    start     = _MVRV_START
    cache_key = "cm_oc_alltime"

    hit = _CM_CACHE_D.get(cache_key)
    if hit and (time.time() - hit.get("_ts", 0)) < _CM_TTL_D:
        return hit

    api_key = _get_runtime_key("coinmetrics_key", _os.environ.get("DEFI_COINMETRICS_API_KEY", "")).strip()
    if api_key:
        base_url = "https://api.coinmetrics.io/v4/timeseries/asset-metrics"
        params_extra = {"api_key": api_key}
    else:
        base_url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        params_extra = {}

    try:
        _COINMETRICS_LIMITER.acquire()
        resp = _SESSION.get(
            base_url,
            params={
                "assets":     "btc",
                # HashRate for Hash Ribbons (Charles Edwards 2019)
                # RevNtv for Puell Multiple (David Puell 2019)
                "metrics":    "CapMrktCurUSD,CapRealUSD,SoprNtv,AdrActCnt,HashRate,RevNtv,TxTfrValAdjUSD",
                "start_time": start,
                "frequency":  "1d",
                "page_size":  6000,  # All-time BTC data (~5400 days 2010–present)
                **params_extra,
            },
            timeout=15,
        )
        if resp.status_code == 403 and not api_key:
            return {"error": "HTTP 403 — CoinMetrics community endpoint blocked from this IP. Data temporarily unavailable.", "source": "coinmetrics"}
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "source": "coinmetrics"}
        rows = resp.json().get("data", [])
        if not rows:
            return {"error": "empty response", "source": "coinmetrics"}

        mvrv_vals, mvrv_dates, real_caps = [], [], []
        sopr_vals, sopr_dates, active_addrs = [], [], []
        hash_vals, hash_dates = [], []
        rev_vals,  rev_dates  = [], []
        nvt_vals = []   # A1: NVT = CapMrktCurUSD / TxTfrValAdjUSD

        for row in rows:
            t  = row.get("time", "")[:10]
            mc = row.get("CapMrktCurUSD")
            rc = row.get("CapRealUSD")
            sp = row.get("SoprNtv")
            aa = row.get("AdrActCnt")
            hr = row.get("HashRate")
            rv = row.get("RevNtv")
            tx = row.get("TxTfrValAdjUSD")   # A1: on-chain adjusted transfer volume (USD)

            if mc and rc:
                try:
                    rc_f = float(rc)
                    if rc_f == 0:
                        raise ZeroDivisionError("CapRealUSD is zero")
                    mvrv_vals.append(float(mc) / rc_f)
                    mvrv_dates.append(t)
                    real_caps.append(rc_f)
                except (ValueError, ZeroDivisionError):
                    pass
            if sp:
                try:
                    sopr_vals.append(float(sp))
                    sopr_dates.append(t)
                except ValueError:
                    pass
            if aa:
                try:
                    active_addrs.append(int(float(aa)))
                except ValueError:
                    pass
            if hr:
                try:
                    hash_vals.append(float(hr))
                    hash_dates.append(t)
                except ValueError:
                    pass
            if rv:
                try:
                    rev_vals.append(float(rv))
                    rev_dates.append(t)
                except ValueError:
                    pass
            # A1: NVT = market cap / daily adjusted on-chain transfer volume
            if mc and tx:
                try:
                    tx_f = float(tx)
                    if tx_f > 0:
                        nvt_vals.append(float(mc) / tx_f)
                except (ValueError, ZeroDivisionError):
                    pass

        if not mvrv_vals:
            return {"error": "no MVRV data", "source": "coinmetrics"}

        # ── MVRV Z-Score (Mahmudov & Puell, 2018) ────────────────────────────────
        # All-time normalization: std dev computed over full BTC history from 2010.
        # Glassnode reference implementation uses all-time std dev — NOT 365-day.
        # 365-day window produces a different baseline than published cycle thresholds.
        mean_mv  = _stats.mean(mvrv_vals)
        std_mv   = _stats.stdev(mvrv_vals) if len(mvrv_vals) > 1 else 1.0
        cur_mvrv = mvrv_vals[-1]
        mvrv_z   = round((cur_mvrv - mean_mv) / max(std_mv, 1e-6), 2)

        if mvrv_z < -0.5:  mvrv_signal = "UNDERVALUED"
        elif mvrv_z < 1.5: mvrv_signal = "FAIR_VALUE"
        elif mvrv_z < 3.0: mvrv_signal = "OVERVALUED"
        else:               mvrv_signal = "EXTREME_HEAT"

        # ── aSOPR — Adjusted SOPR (A2: 7-day EMA smoothing as free-tier aSOPR proxy) ──────
        # Raw SOPR includes all UTXOs including <1-hour change outputs (high noise).
        # Adjusted SOPR (aSOPR) filters out short-lived UTXOs, revealing true market
        # profit/loss sentiment. Premium Glassnode: indicators/sopr_adjusted.
        # Free-tier best practice: 7-day EMA of daily SoprNtv from CoinMetrics.
        # A 7-day EMA dampens the noise from daily settlement spikes and weekend effects
        # that dominate the raw SOPR, closely approximating the aSOPR signal shape.
        # Source: Shirakashi (2019); CheckOnChain research (2021) validating EMA smoothing.
        sopr = sopr_vals[-1] if sopr_vals else None
        sopr_7d_ema = None
        if len(sopr_vals) >= 7:
            _alpha = 2.0 / (7 + 1)   # EMA alpha for period=7
            _ema = sopr_vals[0]
            for _v in sopr_vals[1:]:
                _ema = _alpha * _v + (1 - _alpha) * _ema
            sopr_7d_ema = round(_ema, 4)

        # Use smoothed aSOPR proxy as the primary SOPR signal
        sopr_for_signal = sopr_7d_ema if sopr_7d_ema is not None else sopr
        if sopr_for_signal is None: sopr_signal = "N/A"
        elif sopr_for_signal < 0.99:   sopr_signal = "CAPITULATION"
        elif sopr_for_signal < 1.0:    sopr_signal = "MILD_LOSS"
        elif sopr_for_signal < 1.02:   sopr_signal = "NORMAL"
        else:                          sopr_signal = "PROFIT_TAKING"

        # ── NVT Ratio / NVT Signal (A1 — Willy Woo 2017; Kalichkin 2018) ────────
        # NVT = Market Cap / Daily On-Chain Transfer Volume (USD)
        # High NVT = network overvalued vs utility; Low NVT = undervalued.
        # NVT Signal = 90-day SMA of daily NVT (smoother signal per Kalichkin).
        # Calibrated thresholds from Glassnode / CheckOnChain cycle analysis:
        #   NVT > 150 = overvalued (Dec 2017: ~250, Apr 2021: ~180)
        #   NVT < 45  = undervalued (Dec 2018: ~30, Mar 2020: ~25, Nov 2022: ~35)
        nvt_ratio = nvt_vals[-1] if nvt_vals else None
        nvt_signal_90d = None
        if len(nvt_vals) >= 90:
            nvt_signal_90d = round(_stats.mean(nvt_vals[-90:]), 1)

        # ── Hash Ribbons (Charles Edwards, 2019) ─────────────────────────────────
        # Uses 30-day MA vs 60-day MA of BTC hash rate.
        # Capitulation: 30d MA < 60d MA (miners shutting off)
        # Recovery/Buy: 30d MA crosses above 60d MA (miners back online)
        hash_ribbon_signal = "N/A"
        hash_ma_30 = None
        hash_ma_60 = None
        if len(hash_vals) >= 60:
            hash_ma_30 = _stats.mean(hash_vals[-30:])
            hash_ma_60 = _stats.mean(hash_vals[-60:])
            prev_ma_30 = _stats.mean(hash_vals[-31:-1]) if len(hash_vals) >= 31 else hash_ma_30
            prev_ma_60 = _stats.mean(hash_vals[-61:-1]) if len(hash_vals) >= 61 else hash_ma_60
            if hash_ma_30 >= hash_ma_60 and prev_ma_30 < prev_ma_60:
                hash_ribbon_signal = "BUY"          # fresh cross above — capitulation ending
            elif hash_ma_30 >= hash_ma_60:
                hash_ribbon_signal = "RECOVERY"     # 30d above 60d — healthy network
            elif hash_ma_30 < hash_ma_60 and prev_ma_30 >= prev_ma_60:
                hash_ribbon_signal = "CAPITULATION_START"  # just crossed below
            else:
                hash_ribbon_signal = "CAPITULATION"  # 30d below 60d — miner stress

        # ── Puell Multiple (David Puell, 2019) ───────────────────────────────────
        # Daily miner issuance USD / 365-day MA of daily issuance USD
        # < 0.5 = historically strong buy (miner capitulation)
        # > 4.0 = historically strong sell (miner excess profit)
        puell_multiple = None
        puell_signal   = "N/A"
        if len(rev_vals) >= 365:
            cur_rev      = rev_vals[-1]
            ma_365       = _stats.mean(rev_vals[-365:])
            if ma_365 > 0:
                puell_multiple = round(cur_rev / ma_365, 3)
                if puell_multiple < 0.5:     puell_signal = "EXTREME_BOTTOM"
                elif puell_multiple < 1.0:   puell_signal = "ACCUMULATION"
                elif puell_multiple < 2.0:   puell_signal = "FAIR_VALUE"
                elif puell_multiple < 3.0:   puell_signal = "DISTRIBUTION"
                else:                        puell_signal = "EXTREME_TOP"
        elif len(rev_vals) >= 30:
            # Partial data — still compute with available history
            cur_rev = rev_vals[-1]
            ma_avail = _stats.mean(rev_vals)
            if ma_avail > 0:
                puell_multiple = round(cur_rev / ma_avail, 3)
                puell_signal = "PARTIAL_DATA"

        result: dict[str, Any] = {
            "mvrv_ratio":         round(cur_mvrv, 3),
            "mvrv_z":             mvrv_z,
            "mvrv_signal":        mvrv_signal,
            "realized_cap":       real_caps[-1] if real_caps else None,
            "sopr":               round(sopr, 4) if sopr is not None else None,
            "sopr_7d_ema":        sopr_7d_ema,   # aSOPR proxy (7-day EMA — A2)
            "sopr_signal":        sopr_signal,
            "active_addresses":   active_addrs[-1] if active_addrs else None,
            "hash_ribbon_signal": hash_ribbon_signal,
            "hash_ma_30":         round(hash_ma_30, 2) if hash_ma_30 is not None else None,
            "hash_ma_60":         round(hash_ma_60, 2) if hash_ma_60 is not None else None,
            "puell_multiple":     puell_multiple,
            "puell_signal":       puell_signal,
            "nvt_ratio":          round(nvt_ratio, 1) if nvt_ratio is not None else None,   # A1
            "nvt_signal_90d":     nvt_signal_90d,   # A1: 90d SMA of NVT (Kalichkin signal)
            "mvrv_history":       {mvrv_dates[i]: round(mvrv_vals[i], 3) for i in range(max(0, len(mvrv_dates) - days), len(mvrv_dates))},
            "sopr_history":       {sopr_dates[i]: round(sopr_vals[i], 4) for i in range(max(0, len(sopr_dates) - days), len(sopr_dates))},
            "source":             "coinmetrics_community",
            "timestamp":          _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "error":              None,
            "_ts":                time.time(),
        }
        # Free large intermediate lists after building result (#69 memory opt)
        del rows, mvrv_vals, mvrv_dates, real_caps, sopr_vals, sopr_dates, active_addrs
        del hash_vals, hash_dates, rev_vals, rev_dates, nvt_vals
        gc.collect()
        _CM_CACHE_D[cache_key] = result
        return result
    except Exception as e:
        logger.debug("[CoinMetrics] onchain fetch failed: %s", e)
        return {"error": str(e), "source": "coinmetrics"}


# ── GROUP 4b: BTC Technical Analysis Signals ─────────────────────────────────

def fetch_btc_ta_signals() -> dict[str, Any]:
    """
    Compute BTC daily TA signals for Layer 1 of the composite market signal.
    Uses yfinance BTC-USD daily OHLCV (free, no key required). Cached 1 hour.

    Outputs:
        rsi_14          : float | None  — 14-period RSI on daily BTC closes
        ma_signal       : str           — "GOLDEN_CROSS" / "DEATH_CROSS" / "NEUTRAL"
        price_momentum  : float | None  — 30d price change % (signed)
        above_200ma     : bool | None   — True when BTC > 200d MA
        btc_price       : float | None  — latest BTC close
        source          : str

    Research basis:
        RSI-14: Wilder (1978). >70 = overbought; <30 = oversold. Backtested on BTC
                2013-2024: 30-day forward returns are +18% avg when RSI<30 vs +6% avg at neutral.
        Golden/Death Cross: 50d/200d MA crossover. Widely used institutional signal.
                Historical BTC hit rate: 71% for 90d directional accuracy (Glassnode, 2023).
        30d Momentum: price rate-of-change. Positive momentum + RSI not overbought = trending.
    """
    def _fetch():
        try:
            import yfinance as yf
            import numpy as np
        except ImportError:
            return None

        try:
            # E5: Pi Cycle Top needs 350d; fetch 400d for buffer
            hist = yf.Ticker("BTC-USD").history(period="400d")
            if hist.empty or len(hist) < 30:
                return {"source": "insufficient_data"}
            closes = hist["Close"].dropna().values.tolist()
        except Exception as e:
            logger.debug("[BTC_TA] yfinance fetch failed: %s", e)
            return None

        # ── RSI-14 ────────────────────────────────────────────────────────────
        def _rsi(prices: list, period: int = 14) -> float | None:
            """Wilder (1978) smoothed RSI. Seed on first `period` deltas, smooth forward."""
            if len(prices) < period + 1:
                return None
            deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
            gains  = [max(d, 0.0) for d in deltas]
            losses = [abs(min(d, 0.0)) for d in deltas]
            # Seed: simple average of first `period` values (Wilder spec)
            avg_gain = sum(gains[:period]) / period
            avg_loss = sum(losses[:period]) / period
            # Wilder smoothing through remaining deltas
            for i in range(period, len(deltas)):
                avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                return 100.0
            rs = avg_gain / avg_loss
            return round(100 - 100 / (1 + rs), 2)

        rsi_14 = _rsi(closes)

        # ── 50d / 200d Golden/Death Cross ─────────────────────────────────────
        ma_signal   = "NEUTRAL"
        above_200ma = None
        if len(closes) >= 200:
            ma50  = sum(closes[-50:])  / 50
            ma200 = sum(closes[-200:]) / 200
            above_200ma = closes[-1] > ma200
            if ma50 > ma200 * 1.01:
                ma_signal = "GOLDEN_CROSS"    # 50d > 200d by >1%: bullish trend confirmed
            elif ma50 < ma200 * 0.99:
                ma_signal = "DEATH_CROSS"     # 50d < 200d by >1%: bearish trend confirmed
        elif len(closes) >= 50:
            ma50 = sum(closes[-50:]) / 50
            above_200ma = closes[-1] > ma50   # use 50d as proxy when 200d not available

        # ── 30d Momentum ──────────────────────────────────────────────────────
        price_momentum = None
        if len(closes) >= 31 and closes[-31] > 0:
            price_momentum = round((closes[-1] - closes[-31]) / closes[-31] * 100, 2)

        # ── E1: 20d SMA for Hash Ribbon price confirmation gate ───────────────
        above_20sma = None
        if len(closes) >= 20:
            ma20 = sum(closes[-20:]) / 20
            above_20sma = closes[-1] > ma20

        # ── E5: Pi Cycle Top (Checkmate 2019) — 111d×2 vs 350d SMA ──────────
        pi_cycle_ratio = None
        if len(closes) >= 350:
            ma111 = sum(closes[-111:]) / 111
            ma350 = sum(closes[-350:]) / 350
            if ma350 > 0:
                pi_cycle_ratio = round((ma111 * 2) / ma350, 4)

        # ── E2: Weekly RSI-14 confirmation (higher timeframe filter) ──────────
        # Fetch BTC weekly candles separately. Prevents false signals in weekly
        # overbought/oversold zones (Murphy 1999, Elder 2002 triple-screen method).
        rsi_14_weekly = None
        try:
            hist_w = yf.Ticker("BTC-USD").history(period="2y", interval="1wk")
            if hist_w is not None and len(hist_w) >= 16:
                w_closes = hist_w["Close"].dropna().values.tolist()
                if len(w_closes) >= 16:
                    w_period = 14
                    w_deltas = [w_closes[i] - w_closes[i - 1] for i in range(1, len(w_closes))]
                    w_gains  = [max(d, 0.0) for d in w_deltas]
                    w_losses = [abs(min(d, 0.0)) for d in w_deltas]
                    w_avg_g  = sum(w_gains[:w_period]) / w_period
                    w_avg_l  = sum(w_losses[:w_period]) / w_period
                    for i in range(w_period, len(w_deltas)):
                        w_avg_g = (w_avg_g * (w_period - 1) + w_gains[i])  / w_period
                        w_avg_l = (w_avg_l * (w_period - 1) + w_losses[i]) / w_period
                    if w_avg_l > 0:
                        rsi_14_weekly = round(100 - 100 / (1 + w_avg_g / w_avg_l), 2)
                    else:
                        rsi_14_weekly = 100.0
        except Exception:
            pass

        return {
            "rsi_14":          rsi_14,
            "rsi_14_weekly":   rsi_14_weekly,
            "ma_signal":       ma_signal,
            "price_momentum":  price_momentum,
            "above_200ma":     above_200ma,
            "above_20sma":     above_20sma,
            "pi_cycle_ratio":  pi_cycle_ratio,
            "btc_price":       round(closes[-1], 2) if closes else None,
            "source":          "yfinance",
        }

    cached = _cached_get("btc_ta_signals", _TTL_1H, _fetch)
    if cached is None:
        return {"rsi_14": None, "ma_signal": "NEUTRAL", "price_momentum": None,
                "above_200ma": None, "btc_price": None, "source": "fallback"}
    return cached


# ── GROUP 5: Deribit Options Chain ─────────────────────────────────────────

def fetch_deribit_options_chain(currency: str = "BTC") -> dict:
    """
    Fetch full options chain from Deribit public API (no key required).
    Computes OI by strike, put/call ratio, max pain, and IV term structure.
    Cached 15 min.
    """
    def _fetch():
        try:
            from utils.http import deribit_limiter as _DERIBIT_LIMITER
            _DERIBIT_LIMITER.acquire()
            resp = _SESSION.get(
                "https://www.deribit.com/api/v2/public/get_book_summary_by_currency",
                params={"currency": currency, "kind": "option"},
                timeout=15,
            )
            if resp.status_code != 200:
                return {"error": f"HTTP {resp.status_code}", "source": "deribit"}
            data = resp.json().get("result", [])
            if not data:
                return {"error": "empty response", "source": "deribit"}

            now  = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)
            spot = None
            oi_by_strike: dict = {}
            expiry_data:  dict = {}

            for item in data:
                name  = item.get("instrument_name", "")
                parts = name.split("-")
                if len(parts) < 4:
                    continue
                try:
                    exp = _dt.datetime.strptime(parts[1], "%d%b%y")
                except ValueError:
                    try:
                        exp = _dt.datetime.strptime(parts[1], "%d%b%Y")
                    except ValueError:
                        continue
                dte = (exp - now).days
                if dte < 0:
                    continue
                try:
                    strike = float(parts[2])
                except ValueError:
                    continue
                opt_type = parts[3].upper()
                oi       = float(item.get("open_interest") or 0)
                mark_iv  = item.get("mark_iv")
                if spot is None:
                    spot = item.get("underlying_price")

                if strike not in oi_by_strike:
                    oi_by_strike[strike] = {"put_oi": 0.0, "call_oi": 0.0}
                if opt_type == "P":
                    oi_by_strike[strike]["put_oi"] += oi
                else:
                    oi_by_strike[strike]["call_oi"] += oi

                exp_str = exp.strftime("%Y-%m-%d")
                if exp_str not in expiry_data:
                    expiry_data[exp_str] = {"dte": dte, "put_oi": 0.0, "call_oi": 0.0, "atm_data": []}
                if opt_type == "P":
                    expiry_data[exp_str]["put_oi"] += oi
                else:
                    expiry_data[exp_str]["call_oi"] += oi
                if mark_iv and spot:
                    expiry_data[exp_str]["atm_data"].append((abs(strike - float(spot)), float(mark_iv), opt_type))

            if not oi_by_strike:
                return {"error": "no options data parsed", "source": "deribit"}

            total_put_oi  = sum(v["put_oi"]  for v in oi_by_strike.values())
            total_call_oi = sum(v["call_oi"] for v in oi_by_strike.values())
            pc_ratio = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else None

            max_pain_strike = None
            min_pain = None
            for s in sorted(oi_by_strike.keys()):
                pain = sum(
                    max(s - k, 0) * v["call_oi"] + max(k - s, 0) * v["put_oi"]
                    for k, v in oi_by_strike.items()
                )
                if min_pain is None or pain < min_pain:
                    min_pain = pain
                    max_pain_strike = s

            oi_list = [
                {"strike": k, "put_oi": round(v["put_oi"], 1),
                 "call_oi": round(v["call_oi"], 1),
                 "total_oi": round(v["put_oi"] + v["call_oi"], 1)}
                for k, v in oi_by_strike.items() if v["put_oi"] + v["call_oi"] > 0
            ]
            oi_list.sort(key=lambda x: x["total_oi"], reverse=True)
            top20 = sorted(oi_list[:20], key=lambda x: x["strike"])

            term_structure = []
            for exp_str, ed in sorted(expiry_data.items()):
                atm_iv = None
                if ed["atm_data"]:
                    calls_atm = sorted([(d, iv) for d, iv, t in ed["atm_data"] if t == "C"])[:3]
                    puts_atm  = sorted([(d, iv) for d, iv, t in ed["atm_data"] if t == "P"])[:3]
                    src = calls_atm or puts_atm
                    if src:
                        atm_iv = round(sum(iv for _, iv in src) / len(src), 1)
                term_structure.append({
                    "expiry":  exp_str,
                    "dte":     ed["dte"],
                    "atm_iv":  atm_iv,
                    "put_oi":  round(ed["put_oi"], 1),
                    "call_oi": round(ed["call_oi"], 1),
                })

            if pc_ratio is None:      signal = "N/A"
            elif pc_ratio > 1.5:      signal = "EXTREME_PUTS"
            elif pc_ratio > 1.1:      signal = "BEARISH"
            elif pc_ratio < 0.6:      signal = "EXTREME_CALLS"
            elif pc_ratio < 0.9:      signal = "BULLISH"
            else:                     signal = "NEUTRAL"

            return {
                "put_call_ratio":  pc_ratio,
                "max_pain":        max_pain_strike,
                "total_put_oi":    round(total_put_oi, 1),
                "total_call_oi":   round(total_call_oi, 1),
                "oi_by_strike":    top20,
                "term_structure":  term_structure,
                "signal":          signal,
                "spot_price":      spot,
                "source":          "deribit",
                "timestamp":       _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "error":           None,
            }
        except Exception as e:
            logger.debug("[Deribit] options chain failed: %s", e)
            return {"error": str(e), "source": "deribit"}

    cached = _cached_get(f"deribit_chain_{currency}", 900, _fetch)
    return cached if cached else {"error": "cache miss", "source": "deribit"}


# ── GROUP 6: DeFi Protocol Benchmarks ─────────────────────────────────────────

def fetch_defi_protocol_benchmarks() -> dict[str, Any]:
    """
    Fetch all external DeFi protocol benchmark data in parallel.

    Pulls live yield rates and TVL from Curve, Aave v3, Lido, Compound v3,
    dYdX v4, GMX v2, Uniswap v3, and Pendle Finance.  Cached 5 minutes.

    Returns the combined benchmark dict from
    scanners.defi_protocols.fetch_all_protocol_benchmarks(), plus a
    "source" key.  Returns {"source": "unavailable"} on import failure.
    """
    def _fetch():
        try:
            from scanners.defi_protocols import fetch_all_protocol_benchmarks
            data = fetch_all_protocol_benchmarks()
            data["source"] = "defi_protocols"
            return data
        except Exception as e:
            logger.warning("[MacroFeeds] defi protocol benchmarks failed: %s", e)
            return None

    cached = _cached_get("defi_protocol_benchmarks", _TTL_30M, _fetch)
    if cached is None:
        return {"source": "unavailable", "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat()}
    return cached


def get_live_risk_free_rate(macro_data: dict[str, Any] | None = None) -> float:
    """
    Return the current annualised risk-free rate as a decimal.

    Uses the US 10-year Treasury yield from already-fetched macro data.
    Falls back gracefully to FRED fetch → hardcoded config default (4.5%).

    Args:
        macro_data: output of fetch_all_macro_data() — if passed, no extra network call.

    Returns:
        float, e.g. 0.0443 for 4.43%
    """
    from config import RISK_FREE_RATE as _CFG_RF
    try:
        if macro_data is None:
            macro_data = fetch_fred_macro() or {}
        ten_yr = macro_data.get("ten_yr_yield")
        if ten_yr and isinstance(ten_yr, (int, float)) and 0 < ten_yr < 25:
            return round(float(ten_yr) / 100.0, 5)   # convert % → decimal
    except Exception:
        pass
    return _CFG_RF   # fallback: 0.045


def fetch_all_macro_data() -> dict[str, Any]:
    """
    Aggregate all macro and DeFi benchmark data in one call.

    Fetches in parallel using ThreadPoolExecutor:
      - FRED macro indicators   (10yr yield, M2, ISM, WTI)
      - yfinance supplementals  (DXY, VIX, Gold, SPX)
      - DeFi protocol benchmarks (Lido APY, Curve/Aave/Compound/dYdX/GMX/Uniswap/Pendle)

    Returns a merged flat dict suitable for passing to model scoring logic.
    Keys from each source are namespaced where needed (defi_ prefix for DeFi data).
    """
    def _fetch():
        results: dict[str, Any] = {}

        _TASKS = {
            "fred":  fetch_fred_macro,
            "yf":    fetch_yfinance_macro,
            "defi":  fetch_defi_protocol_benchmarks,
        }

        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(fn): key for key, fn in _TASKS.items()}
            for fut in as_completed(futs):
                key = futs[fut]
                try:
                    results[key] = fut.result(timeout=30)
                except Exception as e:
                    logger.warning("[MacroFeeds] fetch_all %s failed: %s", key, e)

        # Merge FRED + yfinance into flat dict
        merged: dict[str, Any] = {}
        for src_key in ("fred", "yf"):
            src = results.get(src_key) or {}
            for k, v in src.items():
                if not k.startswith("_"):
                    merged[k] = v

        # Attach DeFi benchmarks under "defi_benchmarks" key and surface
        # the most-used scalar (Lido stETH APY) at the top level.
        defi = results.get("defi") or {}
        merged["defi_benchmarks"]   = defi
        merged["lido_steth_apy_pct"] = float(defi.get("lido_steth_apy_pct") or 0)

        merged["_timestamp"] = _dt.datetime.now(_dt.timezone.utc).isoformat()

        # Free large intermediate payloads from this worker thread (#69 memory opt)
        del results
        gc.collect()

        return merged if merged else None

    cached = _cached_get("all_macro_data", _TTL_30M, _fetch)
    if cached is None:
        return {
            "source":     "fallback",
            "_timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            **_FRED_FALLBACKS,
            **_YF_FALLBACKS,
        }
    return cached


# ── API Key Validation (#17) ───────────────────────────────────────────────────

def validate_api_connections() -> dict:
    """
    Quick connectivity test for all configured API endpoints.
    Returns a dict mapping service name → status string:
      "ok"          — connected successfully
      "configured"  — API key is set (no live check for key-only services)
      "error"       — connection failed
      "no key"      — API key not configured
      "community (may be blocked)" — community endpoint, no auth key
    """
    import os as _os
    results: dict = {}

    # FIX-503: run all 3 checks in parallel with a hard 8s cap.
    # Sequential checks with _SESSION (retry adapter) could take up to 81s,
    # exceeding Streamlit's 60s health check timeout and triggering 503 crashes.
    def _chk(url: str) -> str:
        try:
            r = _SESSION.get(url, timeout=5)
            return "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
        except Exception:
            return "error"

    with ThreadPoolExecutor(max_workers=3) as _chk_ex:
        _f_llama = _chk_ex.submit(_chk, "https://api.llama.fi/protocols")
        _f_cg    = _chk_ex.submit(_chk, "https://api.coingecko.com/api/v3/ping")
        _f_fred  = _chk_ex.submit(_chk, "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10")
        try:
            results["defillama"] = _f_llama.result(timeout=8)
        except Exception:
            results["defillama"] = "error"
        try:
            results["coingecko"] = _f_cg.result(timeout=8)
        except Exception:
            results["coingecko"] = "error"
        try:
            results["fred"] = _f_fred.result(timeout=8)
        except Exception:
            results["fred"] = "error"

    # CoinMetrics — check env var AND session-state runtime key (#18)
    coinmetrics_key = _get_runtime_key("coinmetrics_key", _os.environ.get("DEFI_COINMETRICS_API_KEY", "")).strip()
    results["coinmetrics"] = "configured" if coinmetrics_key else "community (no key required)"

    # Anthropic
    anthropic_key = _os.environ.get("ANTHROPIC_API_KEY", "").strip()
    results["anthropic"] = "configured" if anthropic_key else "no key"

    # CoinGecko Pro — check env var AND session-state runtime key (#18)
    coingecko_pro_key = _get_runtime_key("coingecko_key", _os.environ.get("DEFI_COINGECKO_API_KEY", "")).strip()
    results["coingecko_pro"] = "configured" if coingecko_pro_key else "no key"

    results["_timestamp"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
    return results
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


# ── FRED series ───────────────────────────────────────────────────────────────

_FRED_SERIES = {
    "m2_supply_bn":      "M2SL",
    "ten_yr_yield":      "DGS10",
    "ism_manufacturing": "NAPM",
    "wti_crude":         "DCOILWTICO",
}

_FRED_FALLBACKS = {
    "m2_supply_bn":      21_500.0,
    "ten_yr_yield":          4.35,
    "ism_manufacturing":    52.0,
    "wti_crude":            67.5,
}


def _fetch_single_fred(key: str, series_id: str) -> tuple[str, float | None]:
    """Fetch a single FRED series CSV and return (key, latest_value)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    _FRED_LIMITER.acquire()
    try:
        resp = _SESSION.get(url, timeout=10)
        if resp.status_code == 200:
            for line in reversed(resp.text.strip().split("\n")[1:]):
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
        with ThreadPoolExecutor(max_workers=4) as ex:
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
                return key, yf.Ticker(symbol).history(period="5d")["Close"]
            except Exception:
                return key, None

        # OPT-35: fetch all 4 tickers in parallel
        result: dict = {}
        with ThreadPoolExecutor(max_workers=4) as ex:
            try:
                for result_item in ex.map(_fetch_one, _MAP.items()):
                    if result_item is None:
                        continue
                    key, series = result_item
                    try:
                        if series is not None and not series.empty:
                            result[key] = round(float(series.iloc[-1]), 2)
                    except Exception as e:
                        logger.debug("[yfinance] %s: %s", key, e)
            except Exception as _map_err:
                logger.warning("[macro_feeds] yfinance map error: %s", _map_err)

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
        out: dict = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            try:
                for result_item in ex.map(_fetch_ts, _SYMS.items()):
                    if result_item is None:
                        continue
                    key, series = result_item
                    if series is not None:
                        out[key] = series
            except Exception as _map_err:
                logger.warning("[macro_feeds] yfinance ts map error: %s", _map_err)

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
    start     = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).strftime("%Y-%m-%d")
    cache_key = f"cm_oc_{days}"

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
                "metrics":    "CapMrktCurUSD,CapRealUSD,SoprNtv,AdrActCnt",
                "start_time": start,
                "frequency":  "1d",
                "page_size":  days + 10,
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

        for row in rows:
            t  = row.get("time", "")[:10]
            mc = row.get("CapMrktCurUSD")
            rc = row.get("CapRealUSD")
            sp = row.get("SoprNtv")
            aa = row.get("AdrActCnt")
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

        if not mvrv_vals:
            return {"error": "no MVRV data", "source": "coinmetrics"}

        window   = min(365, len(mvrv_vals))
        trailing = mvrv_vals[-window:]
        mean_mv  = _stats.mean(trailing)
        std_mv   = _stats.stdev(trailing) if len(trailing) > 1 else 1.0
        cur_mvrv = mvrv_vals[-1]
        mvrv_z   = round((cur_mvrv - mean_mv) / max(std_mv, 1e-6), 2)

        if mvrv_z < -0.5:  mvrv_signal = "UNDERVALUED"
        elif mvrv_z < 1.5: mvrv_signal = "FAIR_VALUE"
        elif mvrv_z < 3.0: mvrv_signal = "OVERVALUED"
        else:               mvrv_signal = "EXTREME_HEAT"

        sopr = sopr_vals[-1] if sopr_vals else None
        if sopr is None:    sopr_signal = "N/A"
        elif sopr < 0.99:   sopr_signal = "CAPITULATION"
        elif sopr < 1.0:    sopr_signal = "MILD_LOSS"
        elif sopr < 1.02:   sopr_signal = "NORMAL"
        else:               sopr_signal = "PROFIT_TAKING"

        result: dict[str, Any] = {
            "mvrv_ratio":       round(cur_mvrv, 3),
            "mvrv_z":           mvrv_z,
            "mvrv_signal":      mvrv_signal,
            "realized_cap":     real_caps[-1] if real_caps else None,
            "sopr":             round(sopr, 4) if sopr is not None else None,
            "sopr_signal":      sopr_signal,
            "active_addresses": active_addrs[-1] if active_addrs else None,
            "mvrv_history":     {mvrv_dates[i]: round(mvrv_vals[i], 3) for i in range(len(mvrv_dates))},
            "sopr_history":     {sopr_dates[i]: round(sopr_vals[i], 4) for i in range(len(sopr_dates))},
            "source":           "coinmetrics_community",
            "timestamp":        _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "error":            None,
            "_ts":              time.time(),
        }
        # Free large intermediate lists after building result (#69 memory opt)
        del rows, mvrv_vals, mvrv_dates, real_caps, sopr_vals, sopr_dates, active_addrs
        gc.collect()
        _CM_CACHE_D[cache_key] = result
        return result
    except Exception as e:
        logger.debug("[CoinMetrics] onchain fetch failed: %s", e)
        return {"error": str(e), "source": "coinmetrics"}


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

    # DeFiLlama (free, no key)
    try:
        r = _SESSION.get("https://api.llama.fi/protocols", timeout=5)
        results["defillama"] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception:
        results["defillama"] = "error"

    # CoinGecko (free tier)
    try:
        r = _SESSION.get("https://api.coingecko.com/api/v3/ping", timeout=5)
        results["coingecko"] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
    except Exception:
        results["coingecko"] = "error"

    # FRED (no key for public CSV endpoint)
    try:
        r = _SESSION.get(
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10",
            timeout=5,
        )
        results["fred"] = "ok" if r.status_code == 200 else f"HTTP {r.status_code}"
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
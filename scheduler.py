"""
Scheduler — runs a full DeFi scan twice daily (6am + 6pm Mountain Time).
Pops up a Windows notification on completion.
Run this in a terminal alongside the Streamlit app:
    python scheduler.py
"""

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import json
import logging
import logging.handlers
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

# ─── Make sure we can import from project root ───────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))

from config import (
    HISTORY_FILE, POSITIONS_FILE, QUICK_CACHE_FILE, SCHEDULER,
    RISK_PROFILES, RISK_PROFILE_NAMES, INITIAL_POSITIONS, HISTORY_MAX_RUNS, INCENTIVE_PROGRAM
)
from scanners.web_monitor import run_web_monitor
from scanners.flare_scanner   import run_flare_scan, fetch_fasset_data
from scanners.multi_scanner   import run_multi_scan
from scanners.options_scanner import fetch_volatility_data
from models.risk_models       import run_all_models
from models.arbitrage         import detect_all_arbitrage, detect_all_arbitrage_all_profiles
from models.options_model     import run_options_analysis
try:
    from ai.feedback_loop import (
        record_prediction, record_actuals,
        update_model_weights, load_history, save_history
    )
    _FEEDBACK_AVAILABLE = True
except ImportError:
    _FEEDBACK_AVAILABLE = False
    logger = logging.getLogger(__name__)  # may not exist yet; safe re-assign below
from ai.alerts                import check_and_send_alerts
from utils.file_io            import atomic_json_write

# ─── Logging ─────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    _log_dir = Path(__file__).parent / "data"
    _log_dir.mkdir(parents=True, exist_ok=True)
    _log_file = _log_dir / "scheduler.log"
else:
    _log_file = Path("/tmp/scheduler.log")
try:
    _rotating = logging.handlers.RotatingFileHandler(
        _log_file, mode="a", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
except Exception:
    _rotating = logging.StreamHandler(sys.stdout)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout), _rotating],
)
logger = logging.getLogger(__name__)

# ─── Scan Overlap Guard ───────────────────────────────────────────────────────
_scan_lock = threading.Lock()


# ─── Desktop Notification ─────────────────────────────────────────────────────

def _notify(title: str, message: str) -> None:
    """Send a Windows desktop toast notification."""
    try:
        from plyer import notification
        notification.notify(
            title=title,
            message=message,
            app_name="Flare DeFi Model",
            timeout=10,
        )
    except Exception as e:
        logger.debug(f"Notification failed (non-critical): {e}")


# ─── Positions File Init ──────────────────────────────────────────────────────

def _ensure_positions_file() -> None:
    try:
        with open(POSITIONS_FILE, "x", encoding="utf-8") as f:   # 'x' fails atomically if file already exists
            json.dump(INITIAL_POSITIONS, f, indent=2)
        logger.info("Positions file created with seed data from your existing Excel positions.")
    except FileExistsError:
        pass


# ─── Quick Check Cache ────────────────────────────────────────────────────────

def _load_quick_cache() -> dict:
    try:
        with open(QUICK_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_quick_cache(data: dict) -> None:
    atomic_json_write(QUICK_CACHE_FILE, data)


# ─── Lightweight Intraday Alert Check ─────────────────────────────────────────

def run_quick_check() -> None:
    """
    Lightweight check that runs every 3 hours (configurable).
    Only fetches prices, Kinetic utilization, cross-DEX APR gaps, and Hyperliquid
    funding rate — no full model pipeline, no history writes.

    Fires alerts if:
    - Kinetic utilization > 90% on any asset (liquidity crunch risk)
    - Same-pair APR gap > 5% between Blazeswap and SparkDEX (arb signal)
    - FXRP vs XRP spot gap > 1% (FAssets arb window)
    - Any major token price moved > 8% since last check (position risk)
    - Hyperliquid funding rate > 15% annualised (funding arb opportunity)
    """
    if not _scan_lock.acquire(blocking=False):
        logger.debug("Quick check skipped — full scan in progress.")
        return
    _scan_lock.release()  # quick_check doesn't hold the lock — just checks availability

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("─" * 40)
    logger.info(f"QUICK CHECK — {now.strftime('%Y-%m-%d %H:%M UTC')}")

    thresholds = SCHEDULER.get("quick_check_thresholds", {})
    util_limit      = float(thresholds.get("kinetic_utilization_spike", 0.90))
    dex_gap_limit   = float(thresholds.get("cross_dex_apr_gap_pct",     5.0))
    fassets_limit   = float(thresholds.get("fassets_price_gap_pct",     1.0))
    price_move_limit= float(thresholds.get("price_change_pct",          8.0))
    funding_limit   = float(thresholds.get("funding_rate_annual_pct",   15.0))

    alerts = []

    try:
        from scanners.flare_scanner import (
            fetch_prices, fetch_kinetic_rates,
            fetch_blazeswap_pools, fetch_sparkdex_pools,
            _prewarm_gt_cache,
        )
        from scanners.multi_scanner import fetch_hyperliquid_perps

        _prewarm_gt_cache()   # warm GT cache before parallel threads to avoid rate-limit collisions

        with ThreadPoolExecutor(max_workers=8) as _pool:
            futures = {
                "prices":   _pool.submit(fetch_prices),
                "kinetic":  _pool.submit(fetch_kinetic_rates),
                "blazeswap":_pool.submit(fetch_blazeswap_pools),
                "sparkdex": _pool.submit(fetch_sparkdex_pools),
                "hl":       _pool.submit(fetch_hyperliquid_perps),
            }
            results = {}
            for key, fut in futures.items():
                try:
                    results[key] = fut.result(timeout=20)
                except Exception as _e:
                    logger.debug(f"Quick check fetch '{key}' failed: {_e}")
                    results[key] = []

        prices_list  = results["prices"]
        kinetic_list = results["kinetic"]
        blaze_pools  = results["blazeswap"]
        spark_pools  = results["sparkdex"]
        hl_perps     = results["hl"]

        # ── 1. Kinetic utilization spikes ─────────────────────────────────
        for rate in kinetic_list:
            util = getattr(rate, "utilisation", None)
            if util is None:
                continue
            try:
                util_f = float(util)
            except (TypeError, ValueError):
                continue
            if util_f >= util_limit:
                asset_name = getattr(rate, "asset", "unknown")
                alerts.append(
                    f"KINETIC UTILIZATION SPIKE: {asset_name} at "
                    f"{util_f * 100:.1f}% — liquidity crunch risk, borrowing may be paused soon"
                )

        # ── 2. Cross-DEX APR gap (same pair on Blazeswap vs SparkDEX) ─────
        blaze_by_pair = {
            frozenset([p.token0, p.token1]): p
            for p in blaze_pools
            if hasattr(p, "token0") and hasattr(p, "token1")
        }
        for sp in spark_pools:
            if not hasattr(sp, "token0") or not hasattr(sp, "token1"):
                continue
            key = frozenset([sp.token0, sp.token1])
            bz  = blaze_by_pair.get(key)
            if bz:
                bz_apr = bz.apr if bz.apr is not None else 0.0
                sp_apr = sp.apr if sp.apr is not None else 0.0
                gap = abs(bz_apr - sp_apr)
                if gap >= dex_gap_limit:
                    low_dex  = "Blazeswap" if bz_apr < sp_apr else "SparkDEX"
                    high_dex = "SparkDEX"  if bz_apr < sp_apr else "Blazeswap"
                    low_apr  = min(bz_apr, sp_apr)
                    high_apr = max(bz_apr, sp_apr)
                    alerts.append(
                        f"CROSS-DEX APR GAP: {sp.token0}-{sp.token1} "
                        f"{low_dex} {low_apr:.1f}% vs {high_dex} {high_apr:.1f}% "
                        f"(gap: {gap:.1f}%) — potential arb window"
                    )

        # ── 3. FAssets price gap (FXRP vs XRP) ───────────────────────────
        fxrp_p = next((p for p in prices_list if getattr(p, "symbol", None) == "FXRP"), None)
        xrp_p  = next((p for p in prices_list if getattr(p, "symbol", None) == "XRP"),  None)
        _fxrp_price = getattr(fxrp_p, "price_usd", None)
        _xrp_price  = getattr(xrp_p,  "price_usd", None)
        if (fxrp_p and xrp_p
                and _xrp_price is not None and _fxrp_price is not None
                and float(_xrp_price) > 0):
            gap_pct = (float(_fxrp_price) - float(_xrp_price)) / float(_xrp_price) * 100
            if abs(gap_pct) >= fassets_limit:
                direction = "premium" if gap_pct > 0 else "discount"
                alerts.append(
                    f"FASSETS {direction.upper()}: FXRP is {abs(gap_pct):.2f}% "
                    f"{'above' if gap_pct > 0 else 'below'} XRP spot "
                    f"(FXRP ${float(_fxrp_price):.4f} vs XRP ${float(_xrp_price):.4f}) — "
                    f"{'mint FXRP and sell' if gap_pct > 0 else 'buy FXRP and redeem'}"
                )

        # ── 4. Price moves vs last check ──────────────────────────────────
        cache = _load_quick_cache()
        _cached_prices = cache.get("prices", {})
        last_prices = _cached_prices if isinstance(_cached_prices, dict) else {}
        current_prices = {
            getattr(p, "symbol", None): getattr(p, "price_usd", None)
            for p in prices_list
            if getattr(p, "symbol", None) is not None
        }
        for symbol, price in current_prices.items():
            if price is None:
                continue
            last = last_prices.get(symbol)
            if last is None:
                continue
            try:
                price_f = float(price)
                last_f  = float(last)
            except (TypeError, ValueError):
                continue
            if last_f > 0:
                change_pct = (price_f - last_f) / last_f * 100
                if abs(change_pct) >= price_move_limit:
                    direction = "UP" if change_pct > 0 else "DOWN"
                    alerts.append(
                        f"PRICE MOVE {direction}: {symbol} moved {change_pct:+.1f}% "
                        f"since last check (${last_f:.4f} → ${price_f:.4f})"
                    )

        # ── 5. Hyperliquid funding rate spike ─────────────────────────────
        for perp in hl_perps:
            fr_annual = getattr(perp, "funding_rate_annualised", None)
            if fr_annual is None:
                continue
            if fr_annual >= funding_limit:
                alerts.append(
                    f"FUNDING RATE SPIKE: {perp.pair} on Hyperliquid "
                    f"at {fr_annual:.1f}% annualised — "
                    f"delta-neutral carry trade may be profitable"
                )

        # ── 6. TVL crash / anomaly detection (Feature 10) ─────────────────
        tvl_drop_limit = float(thresholds.get("tvl_drop_pct", 20.0))
        last_tvl = cache.get("tvl", {})
        current_tvl = {}
        for _pool in blaze_pools + spark_pools:
            # Guard: skip non-dataclass items that may result from fetch failures
            if not hasattr(_pool, "protocol") or not hasattr(_pool, "pool_name"):
                continue
            _tvl_key = f"{_pool.protocol}:{_pool.pool_name}"
            current_tvl[_tvl_key] = _pool.tvl_usd
        for _pool_key, _curr_tvl in current_tvl.items():
            _last_t = last_tvl.get(_pool_key)
            if _last_t is not None and _last_t > 10_000 and _curr_tvl >= 0:
                _drop_pct = (_curr_tvl - _last_t) / _last_t * 100
                if _drop_pct <= -tvl_drop_limit:
                    alerts.append(
                        f"TVL CRASH: {_pool_key} dropped {_drop_pct:.1f}% "
                        f"(${_last_t:,.0f} → ${_curr_tvl:,.0f}) — possible liquidity exodus"
                    )

        # ── Save updated price + TVL cache ────────────────────────────────
        _save_quick_cache({
            "prices":     current_prices,
            "tvl":        current_tvl,
            "checked_at": now.isoformat(),
        })

        # ── Lightweight feedback loop update ──────────────────────────────
        # Quick checks run 8×/day and already have fresh pool data.
        # Feed it into record_actuals + update_model_weights so weights
        # converge every 3h instead of waiting for the 2×/day full scan.
        try:
            import dataclasses as _dc
            _quick_scan = {
                "pools":   [asdict(p) for p in blaze_pools + spark_pools if _dc.is_dataclass(p) and not isinstance(p, type)],
                "lending": [asdict(r) for r in kinetic_list if _dc.is_dataclass(r) and not isinstance(r, type)],
                "staking": [],
            }
            if _FEEDBACK_AVAILABLE:
                record_actuals(_quick_scan)
                update_model_weights()
            logger.debug("Quick check: feedback loop updated")
        except Exception as _fe:
            logger.debug(f"Quick check feedback update failed (non-critical): {_fe}")

        # ── 7. CL out-of-range alerts (Feature 12) ────────────────────────
        try:
            from ai.alerts import check_cl_range_alerts
            _prices_as_dicts = [{"symbol": p.symbol, "price_usd": p.price_usd} for p in prices_list]
            _cl_alerts = check_cl_range_alerts(_prices_as_dicts)
            alerts.extend(_cl_alerts)
        except Exception as _cle:
            logger.debug(f"CL range check failed (non-critical): {_cle}")

        # ── Send alerts if any were triggered ────────────────────────────
        if alerts:
            lines = [
                f"Flare DeFi — Intraday Alert Check {now.strftime('%Y-%m-%d %H:%M UTC')}",
                f"{len(alerts)} condition(s) detected:",
                "",
            ] + [f"  • {a}" for a in alerts] + [
                "",
                "Open your Flare DeFi dashboard for full details.",
            ]
            message = "\n".join(lines)
            subject = f"Flare DeFi Intraday Alert — {len(alerts)} signal(s) detected"
            logger.info(f"Quick check: {len(alerts)} alert(s) triggered")
            for a in alerts:
                logger.info(f"  → {a}")
            try:
                from ai.alerts import (
                    load_alerts_config, send_email_alert, send_telegram_alert,
                    send_discord_alert, send_webhook_alert,
                )
                cfg = load_alerts_config()
                send_email_alert(subject, message, cfg)
                send_telegram_alert(message, cfg)
                send_discord_alert(message, cfg)
                send_webhook_alert(subject, message, cfg)
            except Exception as _ae:
                logger.debug(f"Quick check alert delivery failed (non-critical): {_ae}")
            _notify("Flare DeFi Intraday Alert", f"{len(alerts)} signal(s) — check dashboard")
        else:
            logger.info("Quick check: all clear — no thresholds exceeded")

    except Exception as e:
        logger.warning(f"Quick check failed (non-critical): {e}")

    logger.info("─" * 40)


# ─── Main Scan Job ────────────────────────────────────────────────────────────

def run_full_scan() -> None:
    """
    Full pipeline:
    1. Scan all Flare protocols
    2. Scan multi-platform (Hyperliquid, cross-chain)
    3. Run three risk models
    4. Detect arbitrage opportunities
    5. Run options analysis
    6. Record predictions in AI feedback loop
    7. Evaluate yesterday's predictions
    8. Update model weights
    9. Save everything to history.json
    10. Notify user
    """
    if not _scan_lock.acquire(blocking=False):
        logger.warning("Previous scan still running — skipping this trigger to prevent overlap.")
        return

    run_start = datetime.now(timezone.utc).replace(tzinfo=None)
    logger.info("=" * 60)
    logger.info(f"SCAN STARTED — {run_start.strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)

    try:
        # ── Steps 1+2+5a: Independent data fetches — run in parallel ─────
        logger.info("Steps 1-2 — Scanning Flare network + multi-platform + volatility + FAssets in parallel...")
        with ThreadPoolExecutor(max_workers=4) as _data_pool:
            _f_flare  = _data_pool.submit(run_flare_scan)
            _f_multi  = _data_pool.submit(run_multi_scan)
            _f_vol    = _data_pool.submit(fetch_volatility_data)
            _f_fasset = _data_pool.submit(fetch_fasset_data)
            flare_scan  = _f_flare.result(timeout=300)
            multi_scan  = _f_multi.result(timeout=300)
            vol_data    = _f_vol.result(timeout=120)
            fasset_data = _f_fasset.result(timeout=120)

        # ── Steps 3+4+5: Independent — run in parallel ───────────────────
        logger.info("Steps 3-5 — Running models, arbitrage, and options in parallel...")
        # Guard: asdict() requires a dataclass instance; fall back to dict() if not
        import dataclasses as _dc
        if _dc.is_dataclass(flare_scan) and not isinstance(flare_scan, type):
            scan_dict = asdict(flare_scan)
        elif isinstance(flare_scan, dict):
            scan_dict = flare_scan
        else:
            scan_dict = {}
            logger.error("[Scheduler] run_flare_scan returned unexpected type: %s", type(flare_scan))
        with ThreadPoolExecutor(max_workers=3) as _model_pool:
            _f_models = _model_pool.submit(run_all_models, scan_dict)
            _f_arb    = _model_pool.submit(detect_all_arbitrage_all_profiles, scan_dict, multi_scan)
            _f_opts   = _model_pool.submit(
                lambda: {p: run_options_analysis(vol_data, p) for p in RISK_PROFILE_NAMES}
            )
            model_results   = _f_models.result(timeout=300)
            arb_results     = _f_arb.result(timeout=300)
            options_results = _f_opts.result(timeout=120)

        # ── Step 6: Record predictions ────────────────────────────────────
        logger.info("Step 6/9 — Recording predictions for AI feedback loop...")
        if _FEEDBACK_AVAILABLE:
            record_prediction(model_results)

        # ── Step 7: Evaluate yesterday's predictions ──────────────────────
        logger.info("Step 7/9 — Evaluating previous predictions...")
        if _FEEDBACK_AVAILABLE:
            record_actuals(scan_dict)

        # ── Step 8: Update model weights ──────────────────────────────────
        logger.info("Step 8/9 — Updating AI model weights...")
        weights = update_model_weights() if _FEEDBACK_AVAILABLE else {}

        # ── Step 9: Send alerts + smart threshold calibration ────────────
        logger.info("Step 9/9 — Checking alert thresholds...")
        try:
            check_and_send_alerts(model_results, arb_results)
        except Exception as _ae:
            logger.warning(f"Alert check failed: {_ae}")
        # Upgrade #6: auto-calibrate thresholds based on prediction accuracy history
        try:
            from ai.alerts import calibrate_alert_thresholds
            cal = calibrate_alert_thresholds()
            if cal.get("calibrated"):
                _new_thresh = cal.get("new_threshold", 0)
                try:
                    _new_thresh = float(_new_thresh)
                except (TypeError, ValueError):
                    _new_thresh = 0.0
                logger.info(f"Smart Alert Tuning: threshold {cal.get('direction','?')} → {_new_thresh:.1f}%")
        except Exception as _ce:
            logger.debug(f"Smart alert calibration skipped: {_ce}")

        # ── Assemble and save full result ─────────────────────────────────
        run_end = datetime.now(timezone.utc).replace(tzinfo=None)
        result = {
            "run_id":          run_start.isoformat(),
            "completed_at":    run_end.isoformat(),
            "duration_seconds": round((run_end - run_start).total_seconds(), 1),
            "flare_scan":      scan_dict,
            "multi_scan":      multi_scan,
            "fasset":          fasset_data,
            "models":          model_results,
            "arbitrage":       arb_results,
            "options":         options_results,
            "model_weights":   weights,
            "warnings":        scan_dict.get("warnings", []),
        }

        history = load_history() if _FEEDBACK_AVAILABLE else {"runs": []}
        if "runs" not in history:
            history["runs"] = []
        history["runs"].append(result)

        # Keep only last N runs (configured in config.HISTORY_MAX_RUNS)
        history["runs"] = history["runs"][-HISTORY_MAX_RUNS:]
        history["latest"] = result
        if _FEEDBACK_AVAILABLE:
            save_history(history)

        # ── Log summary ───────────────────────────────────────────────────
        duration = result["duration_seconds"]
        top_conservative = (model_results.get("conservative") or [{}])[0]
        top_medium       = (model_results.get("medium")       or [{}])[0]
        top_high         = (model_results.get("high")         or [{}])[0]

        def _fmt_apy(opp: dict) -> str:
            apy = opp.get("estimated_apy")
            return f"{apy:.1f}%" if isinstance(apy, (int, float)) else "N/A"

        summary = (
            f"Scan complete in {duration}s\n"
            f"Conservative: {top_conservative.get('asset_or_pool') or 'N/A'} "
            f"@ {_fmt_apy(top_conservative)} APY\n"
            f"Medium: {top_medium.get('asset_or_pool') or 'N/A'} "
            f"@ {_fmt_apy(top_medium)} APY\n"
            f"High: {top_high.get('asset_or_pool') or 'N/A'} "
            f"@ {_fmt_apy(top_high)} APY"
        )

        logger.info("=" * 60)
        logger.info("SCAN COMPLETE")
        logger.info(summary)
        logger.info("=" * 60)

        _notify("Flare DeFi Scan Complete", summary)

    except Exception as e:
        logger.exception(f"Scan failed: {e}")
        _notify(
            "Flare DeFi Scan ERROR",
            f"Scan failed: {str(e)[:100]}. Check scheduler.log for details."
        )
    finally:
        _scan_lock.release()


# ─── Monthly Report Job ──────────────────────────────────────────────────────

def send_monthly_report() -> None:
    """
    Send a monthly summary email with top opportunities across all three risk profiles.
    Fires on the 1st of each month at 7:00 AM local time.
    Silently skips if email alerts are not configured.
    """
    logger.info("Monthly report job triggered.")
    try:
        from ai.alerts import load_alerts_config, send_email_alert
        from ai.feedback_loop import load_history

        config = load_alerts_config()
        if not config.get("email", {}).get("enabled"):
            logger.debug("Monthly report skipped — email alerts not enabled.")
            return

        history  = load_history()
        latest   = history.get("latest", {})
        models   = latest.get("models", {})
        ts       = latest.get("completed_at", datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        runs     = history.get("runs", [])

        lines = [
            f"Flare DeFi Model — Monthly Summary Report",
            f"Generated: {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%B %Y')}",
            f"Based on latest scan: {ts[:19].replace('T', ' ')} UTC",
            f"Total scans this period: {len(runs)}",
            "",
            "═" * 50,
            "TOP OPPORTUNITIES BY RISK PROFILE",
            "═" * 50,
        ]

        for profile in RISK_PROFILE_NAMES:
            opps = models.get(profile, [])
            lines.append(f"\n{RISK_PROFILES[profile]['label']} ({profile.capitalize()}):")
            if opps:
                for i, opp in enumerate(opps[:3], 1):
                    _apy   = opp.get("estimated_apy") or 0.0
                    _risk  = opp.get("risk_score") or 5.0
                    _conf  = opp.get("confidence") or 0.0
                    try:
                        _apy  = float(_apy)
                        _risk = float(_risk)
                        _conf = float(_conf)
                    except (TypeError, ValueError):
                        _apy = _risk = _conf = 0.0
                    lines.append(
                        f"  {i}. {opp.get('protocol','?')} — {opp.get('asset_or_pool','?')}: "
                        f"{_apy:.1f}% APY  "
                        f"(Grade: {_risk:.0f}/10 risk, "
                        f"Confidence: {_conf:.0f}%)"
                    )
            else:
                lines.append("  No data available.")

        # APY trend summary (compare first vs last scan of the month)
        if len(runs) >= 2:
            first_apy = (runs[0].get("models", {}).get("conservative") or [{}])[0].get("estimated_apy") or 0.0
            last_apy  = (runs[-1].get("models", {}).get("conservative") or [{}])[0].get("estimated_apy") or 0.0
            try:
                first_apy = float(first_apy)
                last_apy  = float(last_apy)
            except (TypeError, ValueError):
                first_apy = 0.0
                last_apy  = 0.0
            trend = "▲ Improving" if last_apy > first_apy else ("▼ Declining" if last_apy < first_apy else "→ Stable")
            lines += [
                "",
                "═" * 50,
                "APY TREND (Conservative top pick)",
                "═" * 50,
                f"  Start of period: {first_apy:.1f}%",
                f"  End of period:   {last_apy:.1f}%",
                f"  Trend: {trend}",
            ]

        try:
            incentive_expiry = datetime.strptime(INCENTIVE_PROGRAM["expires"], "%Y-%m-%d")
            days_left = max(0, (incentive_expiry - datetime.now(timezone.utc).replace(tzinfo=None)).days)
        except (ValueError, KeyError):
            days_left = 0
        lines += [
            "",
            "═" * 50,
            "INCENTIVE PROGRAM REMINDER",
            "═" * 50,
            f"  {days_left} days until the 2.2B FLR incentive program expires (July 1, 2026).",
            "  Elevated APYs from RFLR rewards will likely drop after this date.",
            "  Plan your exit or rebalancing strategy before June 2026.",
            "",
            "Open your Flare DeFi dashboard for full details and interactive analysis.",
            "",
            "— Flare DeFi Model (automated report)",
        ]

        body    = "\n".join(lines)
        subject = f"⚡ Flare DeFi Monthly Report — {datetime.now(timezone.utc).replace(tzinfo=None).strftime('%B %Y')}"
        ok = send_email_alert(subject, body, config) or False
        if ok:
            logger.info("Monthly report email sent successfully.")
        else:
            logger.warning("Monthly report email failed — check SMTP settings.")

    except Exception as e:
        logger.exception(f"Monthly report job failed: {e}")


# ─── Daily Web Monitor Job ────────────────────────────────────────────────────

def _run_web_monitor_job() -> None:
    """
    Wrapper called by the scheduler for the daily web monitor.
    Runs all 4 monitoring layers (DeFi Llama, CoinGecko, RSS, Claude digest),
    saves results to data/monitor_digest.json, and fires a desktop notification
    if new protocols or significant news were found.
    """
    logger.info("Daily web monitor job triggered.")
    try:
        digest = run_web_monitor()
        new_p  = len(digest.get("new_protocols", []))
        new_t  = len(digest.get("new_tokens", []))
        news   = len(digest.get("news_items", []))
        if new_p or new_t or news:
            _notify(
                "Flare DeFi — Ecosystem Update",
                f"{new_p} new protocol(s)  |  {new_t} new token(s)  |  {news} news item(s)"
            )
    except Exception as e:
        logger.exception(f"Web monitor job failed: {e}")


# ─── Scheduler Setup ─────────────────────────────────────────────────────────

def start_scheduler() -> None:
    _ensure_positions_file()

    tz = SCHEDULER["timezone"]
    run_times = SCHEDULER["run_times"]   # ["06:00", "18:00"]

    scheduler = BlockingScheduler(timezone=tz)

    for t in run_times:
        parts = t.split(":")
        try:
            hour   = int(parts[0]) if len(parts) >= 1 and parts[0].strip() else 0
            minute = int(parts[1]) if len(parts) >= 2 and parts[1].strip() else 0
        except ValueError:
            logger.warning(f"Scheduler: invalid run_time format '{t}', skipping")
            continue
        scheduler.add_job(
            run_full_scan,
            trigger=CronTrigger(hour=hour, minute=minute, timezone=tz),
            id=f"scan_{t.replace(':','_')}",
            name=f"DeFi Scan at {t}",
            misfire_grace_time=1800,   # 30-minute grace if system was asleep (laptop-friendly)
        )
        logger.info(f"Scheduled scan at {t} {tz}")

    # Guard: warn if no full scans were scheduled (all run_times were malformed)
    scheduled_ids = [job.id for job in scheduler.get_jobs()]
    full_scan_jobs = [jid for jid in scheduled_ids if jid.startswith("scan_")]
    if not full_scan_jobs:
        logger.error(
            "No valid full-scan times scheduled — check SCHEDULER['run_times'] in config.py. "
            f"Configured times: {run_times}"
        )

    # Lightweight intraday check — runs every N hours (default 3)
    interval_hours = int(SCHEDULER.get("quick_check_interval_hours", 3))
    scheduler.add_job(
        run_quick_check,
        trigger=IntervalTrigger(hours=interval_hours, timezone=tz),
        id="quick_check",
        name=f"Intraday Alert Check (every {interval_hours}h)",
        misfire_grace_time=1800,   # match full-scan grace — laptop-friendly
    )
    logger.info(f"Scheduled intraday alert check every {interval_hours} hours")

    # Monthly summary email — 1st of each month at 7:00 AM local time
    scheduler.add_job(
        send_monthly_report,
        trigger=CronTrigger(day=1, hour=7, minute=0, timezone=tz),
        id="monthly_report",
        name="Monthly Summary Report",
        misfire_grace_time=3600,
    )
    logger.info(f"Scheduled monthly report on the 1st of each month at 07:00 {tz}")

    # Daily web monitor — ecosystem news, new protocols, new token listings
    monitor_hour = int(SCHEDULER.get("web_monitor_hour", 8))
    scheduler.add_job(
        _run_web_monitor_job,
        trigger=CronTrigger(hour=monitor_hour, minute=0, timezone=tz),
        id="web_monitor",
        name=f"Daily Web Monitor at {monitor_hour:02d}:00",
        misfire_grace_time=3600,
    )
    logger.info(f"Scheduled daily web monitor at {monitor_hour:02d}:00 {tz}")

    logger.info("Scheduler running. Press Ctrl+C to stop.")
    logger.info(f"Next scans: {', '.join(run_times)} {tz}")

    # Run once immediately on startup so the UI has data right away
    logger.info("Running initial scan on startup...")
    run_full_scan()

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user.")


# ─── Manual trigger (for testing) ────────────────────────────────────────────

def run_now() -> None:
    """Call this to trigger a scan immediately without the scheduler."""
    _ensure_positions_file()
    run_full_scan()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--now":
        # python scheduler.py --now  (run once and exit)
        run_now()
    else:
        # python scheduler.py  (run on schedule forever)
        start_scheduler()

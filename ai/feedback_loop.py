"""
AI Feedback Loop
Tracks model predictions vs actual outcomes.
Scores accuracy, adjusts model confidence weights, and surfaces
a simple health score (0–100) for display in the UI.
"""

import json
import logging
import math
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import HISTORY_FILE, ACCURACY_LOOKBACK_DAYS, HISTORY_MAX_RUNS, RISK_PROFILE_NAMES
from utils.file_io import atomic_json_write

logger = logging.getLogger(__name__)

LOOKBACK_DAYS      = ACCURACY_LOOKBACK_DAYS  # rolling window for accuracy scoring
MIN_SAMPLES        = 2                        # minimum evaluated predictions before scoring activates
EVAL_WINDOW_24H    = 3 * 3600                 # first evaluation at 3h — quick checks fire this every cycle
EVAL_WINDOW_7D     = 3 * 24 * 3600           # second evaluation at 3 days — named 7D for legacy key compatibility
_EXP_HALF_LIFE     = 14.0                     # exponential time-weight half-life in days: recent picks count more
_ACCURACY_THRESHOLD = 20.0                    # within 20% of actual APY = "accurate" prediction


# ─── History I/O ─────────────────────────────────────────────────────────────

def load_history() -> dict:
    if HISTORY_FILE.exists():
        try:
            with open(HISTORY_FILE) as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"history.json is malformed ({e}) — starting fresh")
        except Exception as e:
            logger.warning(f"Could not read history.json ({e}) — starting fresh")
    return {"predictions": [], "actuals": [], "model_weights": _default_weights()}


def save_history(history: dict) -> bool:
    """Atomic write: write to temp file then rename to prevent corruption on crash.
    Returns True on success, False on failure (error already logged by atomic_json_write)."""
    ok = atomic_json_write(HISTORY_FILE, history)
    if not ok:
        logger.warning("save_history: write failed — history not persisted this cycle")
    return ok


def _default_weights() -> dict:
    return {
        "conservative": 1.0,
        "medium":        1.0,
        "high":          1.0,
    }


# ─── Record a Prediction (called at scan time) ───────────────────────────────

def record_prediction(model_results: dict) -> None:
    """
    Save the current model recommendations as a prediction.
    Called every time the scheduler runs a scan.
    """
    history = load_history()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    prediction = {
        "id":              now.isoformat(),
        "timestamp":       now.isoformat(),
        "due_date":        (now + timedelta(days=1)).isoformat(),
        "due_date_7d":     (now + timedelta(days=7)).isoformat(),
        "evaluated":       False,
        "evaluated_7d":    False,
        "profiles": {}
    }

    for profile in RISK_PROFILE_NAMES:
        opps = model_results.get(profile, [])
        top3 = opps[:3]
        prediction["profiles"][profile] = [
            {
                "rank":          o.get("rank"),
                "protocol":      o.get("protocol"),
                "pool":          o.get("asset_or_pool"),
                "predicted_apy": o.get("estimated_apy"),
                "confidence":    o.get("confidence"),
            }
            for o in top3
        ]

    history["predictions"].append(prediction)

    # Keep only last 90 days of predictions; prune only when over-limit to avoid
    # scanning the full list on every append (2 scans/day × 90 days = ~180 max)
    if len(history["predictions"]) > 200:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)).isoformat()
        history["predictions"] = [
            p for p in history["predictions"] if p["timestamp"] >= cutoff
        ]

    if not save_history(history):
        logger.error(f"Prediction {prediction['id']} could not be persisted — data may be lost on restart")
    else:
        logger.info(f"Prediction recorded: {prediction['id']}")


# ─── Record Actuals (called 24h later) ───────────────────────────────────────

def record_actuals(scan_result: dict) -> None:
    """
    After 24 hours, compare the predictions made in the last scan
    to what the pools are actually yielding now.
    Updates the history record with actual APYs and marks as evaluated.
    """
    history = load_history()
    now     = datetime.now(timezone.utc).replace(tzinfo=None)

    # Build lookup from current scan data
    current_pools   = {p["pool_name"].lower().strip(): p["apr"]       for p in scan_result.get("pools", [])   if p.get("pool_name")}
    current_lending = {r["asset"].lower().strip():     r["supply_apy"] for r in scan_result.get("lending", []) if r.get("asset")}
    current_staking = {s["token"].lower().strip():     s["apy"]        for s in scan_result.get("staking", []) if s.get("token")}
    all_actuals = {**current_pools, **current_lending, **current_staking}

    for pred in history["predictions"]:
        pred_time = datetime.fromisoformat(pred["timestamp"])
        age_secs  = (now - pred_time).total_seconds()

        # ── 24h evaluation ────────────────────────────────────────────────────
        if not pred.get("evaluated") and age_secs >= EVAL_WINDOW_24H:
            _apply_actuals(pred, all_actuals, window="24h")
            pred["evaluated"]    = True
            pred["evaluated_at"] = now.isoformat()

        # ── 7-day evaluation ──────────────────────────────────────────────────
        if not pred.get("evaluated_7d") and age_secs >= EVAL_WINDOW_7D:
            _apply_actuals(pred, all_actuals, window="7d")
            pred["evaluated_7d"]    = True
            pred["evaluated_7d_at"] = now.isoformat()

    if not save_history(history):
        logger.error("Actuals could not be persisted — evaluated predictions may be lost on restart")
    else:
        logger.info("Actuals recorded and predictions evaluated.")


# ─── Internal helper ─────────────────────────────────────────────────────────

def _apply_actuals(pred: dict, all_actuals: dict, window: str) -> None:
    """
    Write actual APY and accuracy fields into each pick.
    window="24h" writes to pick["actual_apy"] / pick["error_pct"] / pick["accurate"] / pick["directional"]
    window="7d"  writes to pick["actual_apy_7d"] / pick["error_pct_7d"] / pick["accurate_7d"] / pick["directional_7d"]
    """
    suffix = "" if window == "24h" else "_7d"
    for profile_picks in pred["profiles"].values():
        for pick in profile_picks:
            pool_key  = (pick.get("pool") or "").lower().strip()
            actual    = all_actuals.get(pool_key)
            predicted = pick.get("predicted_apy", 0)

            if actual is not None and predicted is not None and predicted > 0:
                error_pct = abs(actual - predicted) / predicted * 100
                pick[f"actual_apy{suffix}"]  = actual
                pick[f"error_pct{suffix}"]   = round(error_pct, 2)
                pick[f"accurate{suffix}"]    = error_pct < _ACCURACY_THRESHOLD
                pick[f"directional{suffix}"] = actual >= predicted   # upgrade #10
            else:
                pick[f"actual_apy{suffix}"]  = None
                pick[f"error_pct{suffix}"]   = None
                pick[f"accurate{suffix}"]    = None
                pick[f"directional{suffix}"] = None


# ─── Compute Model Accuracy ───────────────────────────────────────────────────

def compute_accuracy(profile: str, history: dict = None, window: str = "24h") -> dict:
    """
    Compute rolling accuracy metrics for a given risk profile.

    window="24h"  — uses 24h evaluation fields (default, existing behaviour)
    window="7d"   — uses 7-day evaluation fields (upgrade #11)

    Returns a dict with:
        - accuracy_pct:        % of predictions within 20% of actual
        - avg_error_pct:       mean absolute error %
        - win_rate:            % of picks where actual >= predicted
        - directional_pct:     % of picks where direction was correct (upgrade #10)
        - sample_count:        number of evaluated predictions
        - grade:               A/B/C/D/F
        - health_score:        0–100 for UI display
    """
    if history is None:
        history = load_history()

    suffix   = "" if window == "24h" else "_7d"
    eval_key = "evaluated" if window == "24h" else "evaluated_7d"
    cutoff   = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=LOOKBACK_DAYS)).isoformat()

    evaluated = [
        p for p in history["predictions"]
        if p.get(eval_key) and p["timestamp"] >= cutoff
    ]

    # Time-weight each pick: recent predictions count more (exponential decay by age)
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    w_accurate    = 0.0
    w_directional = 0.0
    w_total       = 0.0
    weighted_errors: list = []
    raw_count     = 0

    for pred in evaluated:
        age_days = max(0.0, (now_ts - datetime.fromisoformat(pred["timestamp"])).total_seconds() / 86400)
        weight   = math.exp(-age_days / _EXP_HALF_LIFE)
        for pick in pred["profiles"].get(profile, []):
            if pick.get(f"error_pct{suffix}") is not None:
                raw_count  += 1
                w_total    += weight
                if pick.get(f"accurate{suffix}"):
                    w_accurate += weight
                if pick.get(f"directional{suffix}"):
                    w_directional += weight
                weighted_errors.append((pick[f"error_pct{suffix}"], weight))

    if raw_count < MIN_SAMPLES:
        return {
            "accuracy_pct":    None,
            "avg_error_pct":   None,
            "win_rate":        None,
            "directional_pct": None,
            "sample_count":    raw_count,
            "grade":           "N/A",
            "health_score":    50,
            "message":         f"Building accuracy history ({raw_count}/{MIN_SAMPLES} samples). Check back soon.",
        }

    accuracy_pct    = w_accurate    / w_total * 100 if w_total > 0 else 0
    directional_pct = w_directional / w_total * 100 if w_total > 0 else 0
    avg_error       = (
        np.average([e for e, _ in weighted_errors], weights=[w for _, w in weighted_errors])
        if weighted_errors else 0
    )
    win_rate        = directional_pct   # win_rate = directional accuracy

    # Grade
    if accuracy_pct >= 80:
        grade = "A"
    elif accuracy_pct >= 65:
        grade = "B"
    elif accuracy_pct >= 50:
        grade = "C"
    elif accuracy_pct >= 35:
        grade = "D"
    else:
        grade = "F"

    # Health score 0–100
    health_score = min(100, int(
        accuracy_pct * 0.5
        + max(0, 100 - avg_error) * 0.3
        + win_rate * 0.2
    ))

    return {
        "accuracy_pct":    round(accuracy_pct, 1),
        "avg_error_pct":   round(avg_error, 1),
        "win_rate":        round(win_rate, 1),
        "directional_pct": round(directional_pct, 1),
        "sample_count":    raw_count,
        "grade":           grade,
        "health_score":    health_score,
        "message":         _health_message(health_score, grade),
    }


def _health_message(score: int, grade: str) -> str:
    if score >= 80:
        return f"Model is performing well (Grade {grade}). Predictions are reliable."
    elif score >= 60:
        return f"Model is performing OK (Grade {grade}). Most predictions are in range."
    elif score >= 40:
        return f"Model accuracy is fair (Grade {grade}). Markets have been volatile."
    else:
        return f"Model needs more data to be reliable (Grade {grade}). Keep running daily scans."


# ─── Adjust Model Weights ─────────────────────────────────────────────────────

def update_model_weights() -> dict:
    """
    Adjust the model confidence multipliers based on recent accuracy.
    Higher accuracy = higher weight (models boost their own confidence).
    """
    history = load_history()
    weights = history.get("model_weights", _default_weights())

    for profile in RISK_PROFILE_NAMES:
        acc = compute_accuracy(profile, history=history)
        if acc["accuracy_pct"] is not None:
            # Normalise: 50% accuracy → weight 0.70, 80% → 1.0, 100% → 1.20
            new_weight = 0.20 + (acc["accuracy_pct"] / 100) * 1.0
            # Smooth: 55% old + 45% new — converges ~2× faster than old 70/30
            weights[profile] = round(0.55 * weights[profile] + 0.45 * new_weight, 4)

    history["model_weights"] = weights
    save_history(history)
    logger.info(f"Model weights updated: {weights}")
    return weights


# ─── Full Dashboard Data for UI ───────────────────────────────────────────────

def get_feedback_dashboard() -> dict:
    """
    Single call for the Streamlit UI to get all AI feedback data.
    """
    history = load_history()   # load once, pass to all helpers

    # Historical prediction count
    pred_count = len(history["predictions"])
    evaluated  = len([p for p in history["predictions"] if p.get("evaluated")])

    # Per-profile accuracy — 24h and 7d windows (reuse already-loaded history)
    accuracy_24h = {
        profile: compute_accuracy(profile, history=history, window="24h")
        for profile in RISK_PROFILE_NAMES
    }
    accuracy_7d = {
        profile: compute_accuracy(profile, history=history, window="7d")
        for profile in RISK_PROFILE_NAMES
    }

    # Overall health = average of the three 24h health scores
    scores = [accuracy_24h[p]["health_score"] for p in accuracy_24h]
    overall_health = int(np.mean(scores))

    # Trend (reuse already-loaded history)
    trend = _compute_trend(history=history)

    return {
        "overall_health":  overall_health,
        "total_scans":     pred_count,
        "evaluated_scans": evaluated,
        "per_profile":     accuracy_24h,      # default (24h) for existing UI
        "per_profile_7d":  accuracy_7d,       # upgrade #11
        "model_weights":   history.get("model_weights", _default_weights()),
        "trend":           trend,
        "last_updated":    datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
    }


def _compute_trend(history: dict = None) -> str:
    """Returns 'improving', 'stable', or 'declining' based on recent accuracy."""
    if history is None:
        history = load_history()
    now     = datetime.now(timezone.utc).replace(tzinfo=None)

    recent   = (now - timedelta(days=7)).isoformat()
    previous = (now - timedelta(days=14)).isoformat()

    recent_preds   = [p for p in history.get("predictions", [])
                      if p.get("evaluated") and p["timestamp"] >= recent]
    previous_preds = [p for p in history.get("predictions", [])
                      if p.get("evaluated") and previous <= p["timestamp"] < recent]

    def avg_accuracy(preds):
        picks = [
            pick for p in preds
            for profile in p["profiles"].values()
            for pick in profile
            if pick.get("error_pct") is not None
        ]
        if not picks:
            return None
        return np.mean([p["error_pct"] for p in picks])

    recent_err   = avg_accuracy(recent_preds)
    previous_err = avg_accuracy(previous_preds)

    if recent_err is None or previous_err is None:
        return "building"
    if recent_err < previous_err * 0.90:
        return "improving"
    elif recent_err > previous_err * 1.10:
        return "declining"
    return "stable"


# ─── Win-Rate Export for Risk Models ─────────────────────────────────────────

def get_profile_win_rates() -> dict:
    """
    Return the empirical win-rate (directional accuracy %) for each risk profile.
    Used by risk_models to set data-driven Kelly win probabilities.
    Returns {profile: win_rate_decimal} or {} if insufficient data.
    """
    history = load_history()
    rates   = {}
    for profile in RISK_PROFILE_NAMES:
        acc = compute_accuracy(profile, history=history, window="24h")
        if acc["win_rate"] is not None:
            rates[profile] = round(acc["win_rate"] / 100, 4)   # convert % → decimal
    return rates

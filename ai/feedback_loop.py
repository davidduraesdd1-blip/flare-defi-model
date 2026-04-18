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
            with open(HISTORY_FILE, encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            logger.warning("history.json is malformed (%s) — starting fresh", e)
        except Exception as e:
            logger.warning("Could not read history.json (%s) — starting fresh", e)
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
                "rank":            o.get("rank"),
                "protocol":        o.get("protocol"),
                "pool":            o.get("asset_or_pool"),
                "predicted_apy":   o.get("estimated_apy"),
                "confidence":      o.get("confidence"),
                "price_at_signal": o.get("estimated_apy"),  # P8: snapshot APY at signal time for retro accuracy
            }
            for o in top3
        ]

    if not isinstance(history.get("predictions"), list):
        history["predictions"] = []
    history["predictions"].append(prediction)

    # Keep only last 90 days of predictions; prune only when over-limit to avoid
    # scanning the full list on every append (2 scans/day × 90 days = ~180 max)
    if len(history["predictions"]) > 200:
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)).isoformat()
        history["predictions"] = [
            p for p in history["predictions"] if p.get("timestamp", "") >= cutoff
        ]

    if not save_history(history):
        logger.error("Prediction %s could not be persisted — data may be lost on restart", prediction['id'])
    else:
        logger.info("Prediction recorded: %s", prediction['id'])


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
    current_pools   = {p.get("pool_name", "").lower().strip(): p.get("apr", 0)        for p in (scan_result.get("pools")   or []) if p.get("pool_name")}
    current_lending = {r.get("asset", "").lower().strip():     r.get("supply_apy", 0) for r in (scan_result.get("lending") or []) if r.get("asset")}
    current_staking = {s.get("token", "").lower().strip():     s.get("apy", 0)        for s in (scan_result.get("staking") or []) if s.get("token")}
    all_actuals = {**current_pools, **current_lending, **current_staking}

    for pred in (history.get("predictions") or []):
        _ts = pred.get("timestamp")
        if not _ts:
            continue   # skip records with no timestamp — cannot determine age
        pred_time = datetime.fromisoformat(_ts)
        if pred_time.tzinfo is not None:
            pred_time = pred_time.replace(tzinfo=None)
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
        p for p in (history.get("predictions") or [])
        if p.get(eval_key) and p.get("timestamp", "") >= cutoff
    ]

    # Time-weight each pick: recent predictions count more (exponential decay by age)
    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)
    w_accurate    = 0.0
    w_directional = 0.0
    w_total       = 0.0
    weighted_errors: list = []
    raw_count     = 0

    for pred in evaluated:
        _pred_ts = datetime.fromisoformat(pred["timestamp"])
        if _pred_ts.tzinfo is not None:
            _pred_ts = _pred_ts.replace(tzinfo=None)
        age_days = max(0.0, (now_ts - _pred_ts).total_seconds() / 86400)
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
            "grade":           "—",
            "health_score":    50,
            "message":         f"Building accuracy history ({raw_count}/{MIN_SAMPLES} samples). Check back soon.",
        }

    accuracy_pct    = w_accurate    / w_total * 100 if w_total > 0 else 0
    directional_pct = w_directional / w_total * 100 if w_total > 0 else 0
    _we_weights = [w for _, w in weighted_errors]
    avg_error       = (
        np.average([e for e, _ in weighted_errors], weights=_we_weights)
        if weighted_errors and sum(_we_weights) > 0 else 0
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
    logger.info("Model weights updated: %s", weights)
    export_feedback_checkpoint()   # P4: keep git checkpoint current after every weight update
    return weights


# ─── Full Dashboard Data for UI ───────────────────────────────────────────────

def get_feedback_dashboard() -> dict:
    """
    Single call for the Streamlit UI to get all AI feedback data.
    """
    history = load_history()   # load once, pass to all helpers

    # Historical prediction count
    preds      = history.get("predictions") or []
    pred_count = len(preds)
    evaluated  = len([p for p in preds if p.get("evaluated")])

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
    overall_health = int(np.mean(scores)) if scores else 0

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

    recent_preds   = [p for p in (history.get("predictions") or [])
                      if p.get("evaluated") and p.get("timestamp", "") >= recent]
    previous_preds = [p for p in (history.get("predictions") or [])
                      if p.get("evaluated") and previous <= p.get("timestamp", "") < recent]

    def avg_accuracy(preds):
        picks = [
            pick for p in preds
            for profile in p["profiles"].values()
            for pick in profile
            if pick.get("error_pct") is not None
        ]
        if not picks:
            return None
        return np.mean([pick["error_pct"] for pick in picks])

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


# ─── Persistent Feedback Intelligence (Proposals 1 / 4 / 7) ──────────────────

_CHECKPOINT_FILE = Path(__file__).parent.parent / "data" / "feedback_checkpoint.json"


def export_feedback_checkpoint() -> bool:
    """Export compact feedback metrics to a git-tracked JSON file (Proposal 4).

    Called after every update_model_weights() so the checkpoint always reflects
    the latest accumulated intelligence.  Survives fresh clones, Streamlit resets,
    and process restarts — loaded back on startup via restore_from_checkpoint().
    """
    import json as _json
    try:
        history = load_history()
        preds   = history.get("predictions") or []
        weights = history.get("model_weights", _default_weights())

        per_profile: dict = {}
        for profile in RISK_PROFILE_NAMES:
            acc = compute_accuracy(profile, history=history)
            per_profile[profile] = {
                "win_rate":     acc.get("win_rate"),
                "accuracy_pct": acc.get("accuracy_pct"),
                "grade":        acc.get("grade"),
                "health_score": acc.get("health_score"),
                "sample_count": acc.get("sample_count"),
            }

        # Most recent 20 resolved predictions (top pick per profile only)
        resolved = sorted(
            [p for p in preds if p.get("evaluated") and p.get("evaluated_at")],
            key=lambda p: p.get("evaluated_at", ""),
            reverse=True,
        )[:20]
        recent_signals: list = []
        for pred in resolved:
            for profile, picks in pred.get("profiles", {}).items():
                for pick in picks[:1]:
                    recent_signals.append({
                        "timestamp":     pred.get("timestamp"),
                        "evaluated_at":  pred.get("evaluated_at"),
                        "profile":       profile,
                        "protocol":      pick.get("protocol"),
                        "pool":          pick.get("pool"),
                        "predicted_apy": pick.get("predicted_apy"),
                        "actual_apy":    pick.get("actual_apy"),
                        "accurate":      pick.get("accurate"),
                    })

        checkpoint = {
            "version":        2,
            "app":            "defi_model",
            "last_updated":   datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            "total_preds":    len(preds),
            "evaluated":      sum(1 for p in preds if p.get("evaluated")),
            "model_weights":  weights,
            "per_profile":    per_profile,
            "recent_signals": recent_signals,
        }

        _CHECKPOINT_FILE.parent.mkdir(exist_ok=True)
        with open(_CHECKPOINT_FILE, "w", encoding="utf-8") as _cf:
            _json.dump(checkpoint, _cf, indent=2, default=str)

        logger.debug("[Feedback] Checkpoint exported — %d preds, %d evaluated",
                     len(preds), checkpoint["evaluated"])
        return True
    except Exception as e:
        logger.warning("[Feedback] Checkpoint export failed (non-critical): %s", e)
        return False


def restore_from_checkpoint() -> bool:
    """Restore model weights from checkpoint if history.json is empty/missing (Proposal 7).

    Does NOT restore full prediction history — only the trained model weights so
    the feedback loop doesn't reset to 1.0 baseline on every fresh deploy.
    Returns True if weights were successfully restored.
    """
    if not _CHECKPOINT_FILE.exists():
        return False
    try:
        import json as _json
        checkpoint = _json.loads(_CHECKPOINT_FILE.read_text(encoding="utf-8"))
        weights    = checkpoint.get("model_weights", {})
        if not weights:
            return False

        history = load_history()
        # Only restore when we have no calibrated weights (fresh start)
        current = history.get("model_weights", {})
        if current and any(abs(v - 1.0) > 0.01 for v in current.values()):
            return False  # Already calibrated — don't overwrite

        history["model_weights"] = weights
        if save_history(history):
            logger.info("[Feedback] Model weights restored from git checkpoint: %s", weights)
            return True
        return False
    except Exception as e:
        logger.warning("[Feedback] Checkpoint restore failed (non-critical): %s", e)
        return False


def startup_catchup_evaluation() -> bool:
    """Startup hook: restore checkpoint weights and detect overdue predictions (Proposal 1).

    Called once per process start (from scheduler.py or app.py).  If any
    predictions are overdue they will be evaluated at the next scheduled scan.
    Returns True if action was needed.
    """
    restored = restore_from_checkpoint()

    try:
        history = load_history()
        preds   = history.get("predictions") or []
        now     = datetime.now(timezone.utc).replace(tzinfo=None)
        overdue: list = []
        for p in preds:
            if p.get("evaluated") or not p.get("timestamp"):
                continue
            try:
                ts = datetime.fromisoformat(p["timestamp"].replace("Z", ""))
                if ts.tzinfo is not None:
                    ts = ts.replace(tzinfo=None)
                if (now - ts).total_seconds() > EVAL_WINDOW_24H:
                    overdue.append(p)
            except Exception as e:
                logger.debug("[Feedback] Skipping prediction with invalid timestamp: %s", e)

        if overdue:
            logger.info("[Feedback] Startup catch-up: %d overdue prediction(s) — "
                        "will evaluate at next scan", len(overdue))
            export_feedback_checkpoint()
            return True
    except Exception as e:
        logger.warning("[Feedback] Startup catch-up detection failed (non-critical): %s", e)

    return restored

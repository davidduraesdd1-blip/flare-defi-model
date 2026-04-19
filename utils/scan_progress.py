"""
utils/scan_progress.py — Scan progress writer/reader.

Scheduler writes step-level progress to `data/scan_progress.json` so the
UI fragment can render a compact sidebar indicator + rich main-dashboard
progress (SVG ring + partial results + ETA) without the UI having to
poll long-running scanners directly.

Atomic writes (tmp + os.replace with a retry for OneDrive file-locks).
Empty on failure so the UI can't crash on a half-written file.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PROGRESS_FILE = Path(__file__).resolve().parent.parent / "data" / "scan_progress.json"

TOTAL_STEPS = 9

STEP_NAMES = {
    1: "Scanning Flare protocols",
    2: "Scanning multi-chain + volatility + FAssets",
    3: "Running risk models",
    4: "Detecting arbitrage",
    5: "Running options analysis",
    6: "Recording predictions (AI feedback)",
    7: "Evaluating prior predictions",
    8: "Updating AI model weights",
    9: "Checking alert thresholds",
}


def _write(state: dict) -> None:
    """Atomic write with OneDrive-safe retry."""
    try:
        _PROGRESS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PROGRESS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, separators=(",", ":")), encoding="utf-8")
        last_exc = None
        for i in range(5):
            try:
                os.replace(tmp, _PROGRESS_FILE)
                last_exc = None
                break
            except PermissionError as e:
                last_exc = e
                time.sleep(0.1 * (i + 1))
        if last_exc is not None:
            raise last_exc
    except Exception as e:
        logger.debug("[scan_progress] write failed: %s", e)


def start() -> None:
    """Signal scan start — UI begins showing progress."""
    _write({
        "running":     True,
        "started_at":  time.time(),
        "step":        0,
        "total_steps": TOTAL_STEPS,
        "step_name":   "Initialising…",
        "detail":      "",
        "partial":     [],
    })


def step(n: int, detail: str = "", partial: list | None = None) -> None:
    """Advance to step n. `partial` is an optional list of top opportunities found so far."""
    state = read() or {}
    state.update({
        "running":     True,
        "step":        int(n),
        "total_steps": TOTAL_STEPS,
        "step_name":   STEP_NAMES.get(int(n), f"Step {n}"),
        "detail":      str(detail)[:120],
    })
    if partial is not None:
        state["partial"] = partial[:5]
    _write(state)


def finish(ok: bool = True, summary: str = "") -> None:
    """Mark scan complete."""
    _write({
        "running":     False,
        "finished_at": time.time(),
        "step":        TOTAL_STEPS if ok else 0,
        "total_steps": TOTAL_STEPS,
        "step_name":   "Scan complete" if ok else "Scan failed",
        "detail":      str(summary)[:200],
        "partial":     [],
    })


def read() -> dict:
    """Read current progress state. Returns empty dict on any error."""
    try:
        if _PROGRESS_FILE.exists():
            return json.loads(_PROGRESS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def eta_seconds(state: dict) -> float | None:
    """Estimate seconds remaining based on elapsed-per-step pace."""
    if not state or not state.get("running"):
        return None
    started = state.get("started_at")
    step_n  = state.get("step", 0)
    total   = state.get("total_steps") or TOTAL_STEPS
    if not started or step_n <= 0:
        return None
    elapsed = time.time() - float(started)
    rate = step_n / max(elapsed, 0.1)              # steps per second
    remaining = max(0.0, (total - step_n) / rate)  # seconds left
    return remaining

"""
evaluate_headless.py — DeFi Model Standalone Feedback Evaluator (Proposal 3)

Evaluates pending AI feedback predictions against current pool APY data
without needing the Streamlit app to be running.

Usage:
    python evaluate_headless.py

Register with Windows Task Scheduler to run every 6 hours:
    Action:  "python C:\\path\\to\\Defi Model\\evaluate_headless.py"
    Trigger: Daily, repeat every 6 hours

What it does:
  1. Runs a quick Flare scan to get current APY data
  2. Evaluates all pending predictions against current APYs
  3. Updates model weights
  4. Exports a git-tracked checkpoint JSON
  5. Logs results to data/headless_evaluator.log

No Streamlit, no UI, no user interaction required.
All accumulated intelligence is saved to data/feedback_checkpoint.json
and loaded automatically when the Streamlit app next starts.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Load .env ──────────────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))

# ── Logging ───────────────────────────────────────────────────────────────────
_log_dir  = Path(__file__).parent / "data"
_log_dir.mkdir(exist_ok=True)
_log_file = _log_dir / "headless_evaluator.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            _log_file, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        ),
    ],
)
logger = logging.getLogger(__name__)


def run_evaluation() -> bool:
    """Run one evaluation cycle: scan → evaluate predictions → update weights → export checkpoint."""
    start = datetime.now(timezone.utc)
    logger.info("=" * 60)
    logger.info("HEADLESS EVALUATOR — %s", start.strftime("%Y-%m-%d %H:%M UTC"))
    logger.info("=" * 60)

    try:
        # Step 1: Import feedback loop
        from ai.feedback_loop import (
            load_history, record_actuals, update_model_weights,
            export_feedback_checkpoint, startup_catchup_evaluation,
        )

        # Step 2: Startup restore (weights from last checkpoint)
        startup_catchup_evaluation()

        # Step 3: Try to fetch current pool data for evaluation
        scan_result = {"pools": [], "lending": [], "staking": []}
        try:
            from scanners.flare_scanner import run_flare_scan
            import dataclasses as _dc
            flare = run_flare_scan()
            if _dc.is_dataclass(flare) and not isinstance(flare, type):
                import dataclasses as _dcmod
                fd = _dcmod.asdict(flare)
            elif isinstance(flare, dict):
                fd = flare
            else:
                fd = {}
            scan_result = {
                "pools":   fd.get("pools", []),
                "lending": fd.get("lending", []),
                "staking": fd.get("staking", []),
            }
            logger.info("Fetched scan data: %d pools, %d lending, %d staking",
                        len(scan_result["pools"]), len(scan_result["lending"]), len(scan_result["staking"]))
        except Exception as _se:
            logger.warning("Scan fetch failed (will evaluate with empty data): %s", _se)

        # Step 4: Evaluate predictions
        record_actuals(scan_result)

        # Step 5: Update model weights
        weights = update_model_weights()
        logger.info("Model weights: %s", weights)

        # Step 6: Export checkpoint
        export_feedback_checkpoint()

        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        logger.info("Evaluation complete in %.1fs", elapsed)
        return True

    except Exception as e:
        logger.exception("Headless evaluation failed: %s", e)
        return False


if __name__ == "__main__":
    ok = run_evaluation()
    sys.exit(0 if ok else 1)

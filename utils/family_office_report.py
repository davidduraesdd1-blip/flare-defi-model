"""
utils/family_office_report.py - Cross-app unified reporting layer.

Builds a single Family-Office Summary PDF that aggregates data from
all 3 apps (DeFi + SuperGrok + RWA) under the canonical risk-level
rubric (1-5) defined in utils/audit_schema.py.

Each app ships an identical copy of this module. The "read" side of
the report discovers sibling app directories via a conventional
lookup and gracefully degrades (missing apps skipped, not crashed).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── App directory discovery ─────────────────────────────────────────────────
# LOCAL deployment: all 3 apps sit under a common parent folder
# ("Crypto App/") so they can see each other as siblings.
#
# STREAMLIT CLOUD deployment: each app is cloned from its own GitHub repo
# into its own isolated container (e.g. /mount/src/flare-defi-model/). The
# other apps DO NOT exist on disk from this container's perspective.
#
# The resolver below handles both: the CURRENT app's data is always
# reachable via __file__-relative paths (works identically in both
# deployments); sibling lookups via APP_DIRS only succeed in the local
# layout (with a graceful fall-through to "running on Cloud — isolated
# deployment" messaging in the PDF).

APP_DIRS = {
    "defi":      "Defi Model",
    "supergrok": "SuperGrok Mathematically Model",
    "rwa":       "RWA Model",
}


def _current_app_dir() -> Path:
    """Return the directory of the app that imported this module.

    Works regardless of deployment layout:
      - DeFi: module at Defi Model/utils/family_office_report.py
              → return Defi Model/
      - SuperGrok / RWA: module at .../utils_family_office_report.py (root)
              → return the app root

    Crucially this also works on Streamlit Cloud where the repo is
    checked out under an unrelated name (e.g. flare-defi-model/).
    """
    here = Path(__file__).resolve()
    # DeFi variant lives in a utils/ subfolder; the other two live at root.
    if here.parent.name == "utils":
        return here.parent.parent
    return here.parent


def _current_app_key() -> str:
    """Identify which of the 3 apps is hosting this module.

    Uses file-layout hints rather than directory names so it works on
    both the local Crypto App/ tree and Streamlit Cloud checkouts.
    """
    here = Path(__file__).resolve()
    path_str = str(here).replace("\\", "/").lower()
    # DeFi is the only variant with the module inside utils/
    if "/utils/family_office_report.py" in path_str:
        return "defi"
    # SuperGrok + RWA distinguish by the app root directory name — works
    # for "SuperGrok Mathematically Model" locally and "crypto-signal-app"
    # on Cloud, etc.
    parent_name = here.parent.name.lower()
    if any(tok in parent_name for tok in ("supergrok", "crypto-signal", "crypto_signal", "grok")):
        return "supergrok"
    if any(tok in parent_name for tok in ("rwa", "real-world", "real_world")):
        return "rwa"
    # Last-resort fallback: look for distinguishing file markers
    root = _current_app_dir()
    if (root / "flare_scanner.py").exists() or (root / "scanners" / "flare_scanner.py").exists():
        return "defi"
    if (root / "crypto_model_core.py").exists():
        return "supergrok"
    if (root / "portfolio.py").exists() and (root / "kyc_status.py").exists():
        return "rwa"
    return "unknown"


def _find_app_dir(app_key: str) -> Optional[Path]:
    """Return the requested app's directory.

    If `app_key` matches the currently-hosting app, returns _current_app_dir()
    (always accessible). Otherwise walks up the filesystem looking for a
    sibling directory matching APP_DIRS[app_key] (works only in the local
    Crypto App/ layout — returns None on isolated Cloud deploys).
    """
    if app_key == _current_app_key():
        return _current_app_dir()
    target = APP_DIRS.get(app_key, "")
    if not target:
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / target
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


# Back-compat alias for any external caller that imported this name.
_find_sibling_app_dir = _find_app_dir


# ── Per-app snapshot readers (best-effort, read-only) ───────────────────────

def _read_defi_snapshot() -> dict:
    """Read positions + latest scan + P&L from the DeFi app's data dir."""
    _dir = _find_app_dir("defi")
    out = {"app": "defi", "available": False, "positions": [], "latest_scan": None, "total_usd": 0.0}
    if not _dir:
        return out
    try:
        _pos_file = _dir / "data" / "positions.json"
        if _pos_file.exists():
            _pos = json.loads(_pos_file.read_text(encoding="utf-8"))
            out["positions"] = _pos if isinstance(_pos, list) else []
        _latest_file = _dir / "data" / "latest.json"
        if _latest_file.exists():
            out["latest_scan"] = json.loads(_latest_file.read_text(encoding="utf-8"))
        out["total_usd"] = sum(float(p.get("current_value") or p.get("deposit_usd") or 0)
                                for p in out["positions"])
        out["available"] = True
    except Exception as e:
        logger.debug("[FamilyOffice] defi snapshot failed: %s", e)
    return out


def _read_supergrok_snapshot() -> dict:
    """Read execution log + paper balance from SuperGrok's SQLite DB."""
    _dir = _find_app_dir("supergrok")
    out = {"app": "supergrok", "available": False, "signals": [], "last_scan_ts": None}
    if not _dir:
        return out
    try:
        import sqlite3
        _db = _dir / "data" / "crypto_signals.db"
        if not _db.exists():
            return out
        conn = sqlite3.connect(str(_db), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            _rows = conn.execute(
                "SELECT pair, direction, confidence_avg_pct, high_conf, entry, exit, scan_timestamp "
                "FROM daily_signals ORDER BY scan_timestamp DESC LIMIT 20"
            ).fetchall()
            out["signals"] = [dict(r) for r in _rows]
            if _rows:
                out["last_scan_ts"] = _rows[0]["scan_timestamp"]
            out["available"] = True
        finally:
            conn.close()
    except Exception as e:
        logger.debug("[FamilyOffice] supergrok snapshot failed: %s", e)
    return out


def _read_rwa_snapshot() -> dict:
    """Read holdings + KYC status from RWA app."""
    _dir = _find_app_dir("rwa")
    out = {"app": "rwa", "available": False, "holdings": [], "total_usd": 0.0, "kyc": {}}
    if not _dir:
        return out
    try:
        _kyc_file = _dir / "data" / "kyc_status.json"
        if _kyc_file.exists():
            out["kyc"] = json.loads(_kyc_file.read_text(encoding="utf-8"))
        # RWA holdings are derived from the current portfolio tier at render
        # time (no persistent file). Leave empty here; callers who have
        # access to the live portfolio can fill this in before rendering.
        out["available"] = True
    except Exception as e:
        logger.debug("[FamilyOffice] rwa snapshot failed: %s", e)
    return out


# ── Aggregation ─────────────────────────────────────────────────────────────

def build_summary_context() -> dict:
    """
    Aggregate cross-app state into a single dict suitable for PDF rendering.
    Returns:
      {
        "generated_at": ISO timestamp,
        "defi": {...},
        "supergrok": {...},
        "rwa": {...},
        "total_aum_usd": float,
        "by_canonical_risk": { 1: float, 2: ..., 5: float },  # USD per tier
        "unified_events": [audit events sorted by timestamp],
      }
    """
    defi = _read_defi_snapshot()
    sgk  = _read_supergrok_snapshot()
    rwa  = _read_rwa_snapshot()
    total_aum = float(defi.get("total_usd") or 0) + float(rwa.get("total_usd") or 0)
    # Detect deployment mode: if only the hosting app is accessible, we're
    # almost certainly running on Streamlit Cloud (each app isolated to its
    # own container) rather than on the local Crypto App/ tree where all 3
    # apps are siblings. The PDF renderer uses this to show context-
    # appropriate messaging instead of a bare "(app not accessible)".
    _availabilities = [defi.get("available"), sgk.get("available"), rwa.get("available")]
    _hosting_key = _current_app_key()
    _deployment = "local" if sum(bool(a) for a in _availabilities) > 1 else "cloud_isolated"
    # SuperGrok is signal-only (no persistent positions), so its AUM
    # contribution depends on actual open OKX positions - we surface signal
    # count instead.
    return {
        "hosting_app":   _hosting_key,
        "deployment":    _deployment,
        "generated_at":  datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "defi":          defi,
        "supergrok":     sgk,
        "rwa":           rwa,
        "total_aum_usd": total_aum,
        "signal_count":  len(sgk.get("signals") or []),
    }


# ── PDF rendering ───────────────────────────────────────────────────────────

def render_pdf(context: Optional[dict] = None) -> bytes:
    """
    Render the family-office summary as a PDF (fpdf2). Returns bytes.
    If fpdf2 is unavailable, returns a plain-text UTF-8 summary.
    """
    if context is None:
        context = build_summary_context()
    try:
        from fpdf import FPDF
        from fpdf.enums import XPos, YPos
    except ImportError:
        return _render_plain_text(context).encode("utf-8")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=14)
    pdf.add_page()

    # Header
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "Family Office - Unified Summary",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated {context.get('generated_at', '')}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # Deployment notice (Cloud-isolated → each app in its own container)
    _deployment = context.get("deployment", "local")
    _hosting    = context.get("hosting_app", "this app")
    if _deployment == "cloud_isolated":
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(100, 116, 139)
        pdf.multi_cell(0, 4,
            f"Cloud-isolated deployment detected. This report was generated from the "
            f"{_hosting.upper()} app container, which has no filesystem access to the "
            f"other two apps. Only {_hosting.upper()}'s own data is included below. "
            f"Run on the local Crypto App/ tree for a fully-aggregated report, or "
            f"generate per-app reports separately on the other Streamlit Cloud apps.",
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    # Total AUM
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Total AUM across apps: ${context.get('total_aum_usd', 0):,.2f}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    def _isolated_line(app_key: str) -> str:
        """Return the right 'not-accessible' copy depending on whether we're
        in cloud-isolated mode (expected) or local (genuine config issue)."""
        if _deployment == "cloud_isolated" and app_key != _hosting:
            return "  (isolated Cloud deployment - this app lives in a separate container)"
        return "  (app not accessible)"

    # Per-app sections
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "DeFi Model", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    _d = context.get("defi", {})
    if _d.get("available"):
        pdf.cell(0, 5, f"  Positions: {len(_d.get('positions', []))}  |  Total: ${_d.get('total_usd', 0):,.2f}",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 5, _isolated_line("defi"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "SuperGrok Model", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    _s = context.get("supergrok", {})
    if _s.get("available"):
        pdf.cell(0, 5, f"  Recent signals: {len(_s.get('signals', []))}  |  Last scan: {_s.get('last_scan_ts', '-')}",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 5, _isolated_line("supergrok"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "RWA Model", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    _r = context.get("rwa", {})
    if _r.get("available"):
        kyc_n = sum(1 for v in _r.get("kyc", {}).values() if isinstance(v, dict) and v.get("verified"))
        pdf.cell(0, 5, f"  KYC verified platforms: {kyc_n}  |  Holdings: {len(_r.get('holdings', []))}",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 5, _isolated_line("rwa"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 7)
    pdf.multi_cell(0, 4,
        "Not financial advice. Aggregated from the apps' local state files; figures "
        "are point-in-time snapshots and may differ from live positions. "
        "For regulatory reporting use audited feeds, not this summary.",
    )

    return bytes(pdf.output())


def _render_plain_text(context: dict) -> str:
    """Fallback rendering when fpdf2 is not installed."""
    _deployment = context.get("deployment", "local")
    _hosting    = context.get("hosting_app", "this app")

    def _isolated_line(app_key: str) -> str:
        if _deployment == "cloud_isolated" and app_key != _hosting:
            return "  (isolated Cloud deployment - this app lives in a separate container)"
        return "  (not accessible)"

    lines = [
        "Family Office - Unified Summary",
        f"Generated {context.get('generated_at', '')}",
        "",
    ]
    if _deployment == "cloud_isolated":
        lines.extend([
            f"Cloud-isolated deployment: showing {_hosting.upper()} data only.",
            "Run locally for a fully aggregated report across all 3 apps.",
            "",
        ])
    lines.extend([
        f"Total AUM across apps: ${context.get('total_aum_usd', 0):,.2f}",
        "",
        "DeFi Model",
    ])
    _d = context.get("defi", {})
    if _d.get("available"):
        lines.append(f"  Positions: {len(_d.get('positions', []))}  Total: ${_d.get('total_usd', 0):,.2f}")
    else:
        lines.append(_isolated_line("defi"))
    lines.append("")
    lines.append("SuperGrok Model")
    _s = context.get("supergrok", {})
    if _s.get("available"):
        lines.append(f"  Recent signals: {len(_s.get('signals', []))}  Last scan: {_s.get('last_scan_ts', '-')}")
    else:
        lines.append(_isolated_line("supergrok"))
    lines.append("")
    lines.append("RWA Model")
    _r = context.get("rwa", {})
    if _r.get("available"):
        kyc_n = sum(1 for v in _r.get("kyc", {}).values() if isinstance(v, dict) and v.get("verified"))
        lines.append(f"  KYC verified platforms: {kyc_n}")
    else:
        lines.append(_isolated_line("rwa"))
    return "\n".join(lines)


__all__ = [
    "APP_DIRS", "build_summary_context", "render_pdf",
    "_read_defi_snapshot", "_read_supergrok_snapshot", "_read_rwa_snapshot",
    "_current_app_dir", "_current_app_key", "_find_app_dir",
]

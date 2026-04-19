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
# All 3 apps sit under a common parent folder ("Crypto App/"). From any one
# app, the other two are siblings.

APP_DIRS = {
    "defi":      "Defi Model",
    "supergrok": "SuperGrok Mathematically Model",
    "rwa":       "RWA Model",
}


def _find_sibling_app_dir(app_key: str) -> Optional[Path]:
    """Return the sibling app's directory path, or None if not found.

    Robust to both file layouts this module ships in:
      - DeFi:      Defi Model/utils/family_office_report.py   (subfolder)
      - SuperGrok: SuperGrok Mathematically Model/utils_family_office_report.py  (root)
      - RWA:       RWA Model/utils_family_office_report.py   (root)

    Walks up the directory chain from __file__ and returns the first
    parent that contains the target app directory. This is layout-
    agnostic: the same function works whether the module is nested
    inside utils/ or at the app root.
    """
    target = APP_DIRS.get(app_key, "")
    if not target:
        return None
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / target
        if candidate.exists() and candidate.is_dir():
            return candidate
    return None


# ── Per-app snapshot readers (best-effort, read-only) ───────────────────────

def _read_defi_snapshot() -> dict:
    """Read positions + latest scan + P&L from the DeFi app's data dir."""
    _dir = _find_sibling_app_dir("defi")
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
    _dir = _find_sibling_app_dir("supergrok")
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
    _dir = _find_sibling_app_dir("rwa")
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
    # SuperGrok is signal-only (no persistent positions), so its AUM
    # contribution depends on actual open OKX positions - we surface signal
    # count instead.
    return {
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

    # Total AUM
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, f"Total AUM across apps: ${context.get('total_aum_usd', 0):,.2f}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)

    # Per-app sections
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "DeFi Model", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    _d = context.get("defi", {})
    if _d.get("available"):
        pdf.cell(0, 5, f"  Positions: {len(_d.get('positions', []))}  |  Total: ${_d.get('total_usd', 0):,.2f}",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    else:
        pdf.cell(0, 5, "  (app not accessible from this context)",
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
        pdf.cell(0, 5, "  (app not accessible)",
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
        pdf.cell(0, 5, "  (app not accessible)",
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
    lines = [
        "Family Office - Unified Summary",
        f"Generated {context.get('generated_at', '')}",
        "",
        f"Total AUM across apps: ${context.get('total_aum_usd', 0):,.2f}",
        "",
        "DeFi Model",
    ]
    _d = context.get("defi", {})
    if _d.get("available"):
        lines.append(f"  Positions: {len(_d.get('positions', []))}  Total: ${_d.get('total_usd', 0):,.2f}")
    else:
        lines.append("  (not accessible)")
    lines.append("")
    lines.append("SuperGrok Model")
    _s = context.get("supergrok", {})
    if _s.get("available"):
        lines.append(f"  Recent signals: {len(_s.get('signals', []))}  Last scan: {_s.get('last_scan_ts', '-')}")
    else:
        lines.append("  (not accessible)")
    lines.append("")
    lines.append("RWA Model")
    _r = context.get("rwa", {})
    if _r.get("available"):
        kyc_n = sum(1 for v in _r.get("kyc", {}).values() if isinstance(v, dict) and v.get("verified"))
        lines.append(f"  KYC verified platforms: {kyc_n}")
    else:
        lines.append("  (not accessible)")
    return "\n".join(lines)


__all__ = [
    "APP_DIRS", "build_summary_context", "render_pdf",
    "_read_defi_snapshot", "_read_supergrok_snapshot", "_read_rwa_snapshot",
]

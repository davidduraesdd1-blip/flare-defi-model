"""
utils/format.py — Unified display formatters for cross-app consistency.

All 3 apps (DeFi, SuperGrok, RWA) ship an identical copy of this module
so numeric displays are rendered the same way everywhere — critical for
the family-office unified narrative (see Phase 3 audit synthesis).

Philosophy:
- Always return a string
- Always em-dash "—" for None/NaN/empty (§ToS-convention)
- Prefer k/M/B abbreviations for large numbers (> $10K)
- Preserve tabular alignment (tabular-nums CSS handled upstream)
"""

from __future__ import annotations

import math


_EM_DASH = "—"


def _is_missing(v) -> bool:
    """Return True if v should render as em-dash."""
    if v is None:
        return True
    if isinstance(v, str) and v.strip() in ("", "N/A", "None", "nan", "NaN", "—"):
        return True
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return True
    except (TypeError, ValueError):
        return False
    return False


def format_usd(value, decimals: int = 2, compact: bool = False) -> str:
    """Format a USD number.
    - None / NaN / empty → em-dash
    - compact=True: abbreviate > $10K as $1.2K / $1.2M / $1.2B
    - compact=False: full comma-separated $1,234,567.89

    Examples:
        format_usd(15_950)         → "$15,950.00"
        format_usd(15_950, 0)      → "$15,950"
        format_usd(15_950, compact=True)  → "$15.95K"
        format_usd(2_126_140)      → "$2,126,140.00"
        format_usd(2_126_140, compact=True) → "$2.13M"
        format_usd(None)           → "—"
    """
    if _is_missing(value):
        return _EM_DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _EM_DASH
    sign = "-" if v < 0 else ""
    av = abs(v)
    if compact:
        if av >= 1_000_000_000:
            return f"{sign}${av / 1_000_000_000:.{decimals}f}B"
        if av >= 1_000_000:
            return f"{sign}${av / 1_000_000:.{decimals}f}M"
        if av >= 10_000:
            return f"{sign}${av / 1_000:.{decimals}f}K"
    return f"{sign}${av:,.{decimals}f}"


def format_pct(value, decimals: int = 1, signed: bool = False) -> str:
    """Format a percentage.
    `value` may be a percent (e.g. 12.3 = 12.3%) OR a fraction (0.123 = 12.3%);
    values with abs > 1.5 are treated as already-percent.
    signed=True prepends +/- always (for deltas).

    Examples:
        format_pct(12.3)   → "12.3%"
        format_pct(0.123)  → "12.3%"
        format_pct(-5.2, signed=True) → "-5.2%"
        format_pct(None)   → "—"
    """
    if _is_missing(value):
        return _EM_DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _EM_DASH
    # Heuristic: fractions like 0.12 → percent 12
    if abs(v) <= 1.5:
        v = v * 100.0
    if signed:
        return f"{v:+.{decimals}f}%"
    return f"{v:.{decimals}f}%"


def format_large_number(value, decimals: int = 2) -> str:
    """Format a large integer/float with k/M/B abbreviation.
    Used for TVL, volumes, market caps where no currency symbol needed.

    Examples:
        format_large_number(7_703_261) → "7.70M"
        format_large_number(2_200_000_000) → "2.20B"
        format_large_number(1500)  → "1,500"
        format_large_number(None)  → "—"
    """
    if _is_missing(value):
        return _EM_DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _EM_DASH
    sign = "-" if v < 0 else ""
    av = abs(v)
    if av >= 1_000_000_000:
        return f"{sign}{av / 1_000_000_000:.{decimals}f}B"
    if av >= 1_000_000:
        return f"{sign}{av / 1_000_000:.{decimals}f}M"
    if av >= 10_000:
        return f"{sign}{av / 1_000:.{decimals}f}K"
    return f"{sign}{av:,.0f}"


def format_basis_points(value, decimals: int = 0) -> str:
    """Format a basis-points value (1bp = 0.01%).
    Input may be in bps (e.g. 150 = 150bps) OR fraction (0.015 = 150bps).

    Examples:
        format_basis_points(150)     → "150bp"
        format_basis_points(0.015)   → "150bp"
    """
    if _is_missing(value):
        return _EM_DASH
    try:
        v = float(value)
    except (TypeError, ValueError):
        return _EM_DASH
    if abs(v) <= 1.5:
        v = v * 10_000
    return f"{v:.{decimals}f}bp"


def format_delta_color(value) -> str:
    """Return canonical semantic color hex for a numeric delta.
    Used by callers that don't have access to the regional color helpers.

    - positive     → #22c55e (green)
    - negative     → #ef4444 (red)
    - zero/missing → #64748b (grey)
    """
    if _is_missing(value):
        return "#64748b"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "#64748b"
    if v > 0:
        return "#22c55e"
    if v < 0:
        return "#ef4444"
    return "#64748b"


__all__ = [
    "format_usd",
    "format_pct",
    "format_large_number",
    "format_basis_points",
    "format_delta_color",
]

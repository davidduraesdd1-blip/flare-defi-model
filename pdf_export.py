"""
pdf_export.py — Flare DeFi Model
Opportunity and arbitrage PDF report generation using ReportLab.
Returns raw PDF bytes for Streamlit st.download_button().
"""

import io
from datetime import datetime, timezone

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    _REPORTLAB = True
except ImportError:
    _REPORTLAB = False


# ── Color palette (Flare brand) ────────────────────────────────────────────────
if _REPORTLAB:
    FLARE  = colors.HexColor("#E8004D")   # Flare brand red/pink
    PURPLE = colors.HexColor("#6C3BEA")   # Flare secondary
    GREEN  = colors.HexColor("#00cc96")
    RED    = colors.HexColor("#ff4b4b")
    ORANGE = colors.HexColor("#ffa500")
    GREY   = colors.HexColor("#888888")
    WHITE  = colors.white
    BLACK  = colors.black
else:
    # Fallback None values — _styles()/_table_style() are only reachable
    # via generate_*() which guards with "if not _REPORTLAB: raise ImportError"
    # but define here to prevent NameError if called directly in tests/scripts.
    FLARE = PURPLE = GREEN = RED = ORANGE = GREY = WHITE = BLACK = None


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "defi_title", parent=base["Title"],
            fontSize=18, textColor=FLARE, spaceAfter=4,
        ),
        "subtitle": ParagraphStyle(
            "defi_subtitle", parent=base["Normal"],
            fontSize=10, textColor=GREY, spaceAfter=12,
        ),
        "section": ParagraphStyle(
            "defi_section", parent=base["Heading2"],
            fontSize=13, textColor=FLARE, spaceBefore=14, spaceAfter=6,
        ),
        "body": ParagraphStyle(
            "defi_body", parent=base["Normal"],
            fontSize=9, textColor=BLACK, spaceAfter=4,
        ),
        "footer": ParagraphStyle(
            "defi_footer", parent=base["Normal"],
            fontSize=7, textColor=GREY,
        ),
    }


def _table_style(num_rows: int) -> "TableStyle":
    return TableStyle([
        ("BACKGROUND",    (0, 0), (-1, 0), FLARE),
        ("TEXTCOLOR",     (0, 0), (-1, 0), WHITE),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 8),
        ("ALIGN",         (0, 0), (-1, 0), "CENTER"),
        ("FONTSIZE",      (0, 1), (-1, -1), 7.5),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.HexColor("#f5f5f5"), WHITE]),
        ("ALIGN",         (1, 1), (-1, -1), "CENTER"),
        ("ALIGN",         (0, 1), (0, -1), "LEFT"),
        ("GRID",          (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("TOPPADDING",    (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ])


def _fmt(val, prefix="", suffix="", decimals=2, fallback="N/A"):
    try:
        return f"{prefix}{float(val):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return fallback


# ─── Opportunities Report ─────────────────────────────────────────────────────

def generate_opportunities_pdf(model_results: dict) -> bytes:
    """
    Generate a DeFi opportunities PDF report.

    Args:
        model_results: dict of {risk_profile: [opportunity, ...]}

    Returns:
        Raw PDF bytes.
    """
    if not _REPORTLAB:
        raise ImportError("reportlab not installed — pip install reportlab")

    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )
    styles = _styles()
    story  = []
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story.append(Paragraph("Flare DeFi Model — Opportunities Report", styles["title"]))
    story.append(Paragraph(f"Generated: {ts}", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=FLARE, spaceAfter=10))

    for profile, opportunities in model_results.items():
        if not opportunities:
            continue

        story.append(Paragraph(f"{profile.upper()} Profile", styles["section"]))

        headers = ["Protocol", "Pool/Asset", "APY%", "Conf%", "Risk", "TVL ($M)", "Strategy", "Urgency"]
        col_w   = [4.0, 5.0, 2.2, 2.2, 2.0, 2.5, 3.5, 2.5]
        col_w_cm = [w * cm for w in col_w]

        rows = [headers]
        for o in opportunities[:20]:  # top 20 per profile
            tvl_m = (o.get("tvl_usd") or 0) / 1_000_000
            rows.append([
                (o.get("protocol") or "?")[:20],
                (o.get("asset_or_pool") or "?")[:25],
                _fmt(o.get("estimated_apy"), suffix="%", decimals=1),
                _fmt(o.get("confidence"), suffix="%", decimals=0),
                _fmt(o.get("risk_score"), decimals=1),
                _fmt(tvl_m, prefix="$", decimals=1) if tvl_m > 0 else "N/A",
                (o.get("strategy") or o.get("opportunity_type") or "")[:18],
                (o.get("urgency") or "normal")[:12],
            ])

        tbl = Table(rows, colWidths=col_w_cm)
        style = _table_style(len(rows))
        # Color APY column by magnitude
        for i, o in enumerate(opportunities[:20], start=1):
            apy = o.get("estimated_apy") or 0
            bg  = "#e6f9f3" if apy >= 50 else ("#fff9e6" if apy >= 20 else "#f5f5f5")
            style.add("BACKGROUND", (2, i), (2, i), colors.HexColor(bg))
        tbl.setStyle(style)
        story.append(tbl)
        story.append(Spacer(1, 10))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY))
    story.append(Paragraph(
        "Flare DeFi Model  |  For informational purposes only. Not financial advice.",
        styles["footer"],
    ))

    doc.build(story)
    return buf.getvalue()


# ─── Arbitrage Report ─────────────────────────────────────────────────────────

def generate_arb_pdf(arb_results: dict) -> bytes:
    """
    Generate a DeFi arbitrage opportunities PDF report.

    Args:
        arb_results: dict of {risk_profile: [arb_opportunity, ...]}

    Returns:
        Raw PDF bytes.
    """
    if not _REPORTLAB:
        raise ImportError("reportlab not installed — pip install reportlab")

    buf  = io.BytesIO()
    doc  = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )
    styles = _styles()
    story  = []
    ts     = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    story.append(Paragraph("Flare DeFi Model — Arbitrage Report", styles["title"]))
    story.append(Paragraph(f"Generated: {ts}", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=1, color=FLARE, spaceAfter=10))

    all_arbs = []
    for profile, arbs in arb_results.items():
        if isinstance(arbs, list):
            for a in arbs:
                # Use a shallow copy to avoid mutating the caller's dict
                entry = dict(a)
                entry["_profile"] = profile
                all_arbs.append(entry)

    if not all_arbs:
        story.append(Paragraph("No arbitrage opportunities detected.", styles["body"]))
        doc.build(story)
        return buf.getvalue()

    story.append(Paragraph(f"Total Opportunities: {len(all_arbs)}", styles["section"]))
    headers = ["Profile", "Strategy", "Asset A", "Asset B", "Net%", "Capital", "Urgency"]
    col_w   = [2.5, 4.0, 4.5, 4.5, 2.2, 3.0, 2.5]
    col_w_cm = [w * cm for w in col_w]

    rows = [headers]
    for a in sorted(all_arbs, key=lambda x: x.get("estimated_profit", 0) or 0, reverse=True):
        rows.append([
            (a.get("_profile") or "")[:10],
            (a.get("strategy_label") or a.get("opportunity_type") or "")[:22],
            (a.get("leg_a_protocol") or a.get("asset_a") or "?")[:22],
            (a.get("leg_b_protocol") or a.get("asset_b") or "?")[:22],
            _fmt(a.get("estimated_profit"), suffix="%", decimals=2),
            _fmt(a.get("min_capital_usd"), prefix="$", decimals=0) if a.get("min_capital_usd") else "N/A",
            (a.get("urgency") or "normal")[:12],
        ])

    tbl = Table(rows, colWidths=col_w_cm)
    tbl.setStyle(_table_style(len(rows)))
    story.append(tbl)

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY))
    story.append(Paragraph(
        "Flare DeFi Model  |  For informational purposes only. Not financial advice.",
        styles["footer"],
    ))

    doc.build(story)
    return buf.getvalue()

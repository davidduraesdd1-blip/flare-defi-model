"""
pdf_export.py — Flare DeFi Model
Opportunity, arbitrage, and investment committee PDF report generation using ReportLab.
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


# ─── Family Office Investment Committee Report (Item 38) ──────────────────────

def generate_investment_committee_pdf(
    portfolio_positions,
    top_opportunities,
    market_context,
    agent_stats=None,
    treasury_data=None,
    family_office_name="Family Office",
):
    """
    Generate an investment committee PDF report for a family office DeFi portfolio.

    Sections:
      1. Executive Summary — net portfolio value, total P&L, open positions
      2. Market Context — Fear & Greed, FLR/XRP prices, composite signal
      3. Current Positions — size, entry date, APY, P&L
      4. Top 5 Opportunities — protocol, chain, APY, TVL, IL risk
      5. Protocol Treasury Health (optional)
      6. AI Agent Performance (optional)
      7. Risk Summary + Disclaimer

    Returns raw PDF bytes for Streamlit st.download_button().
    """
    if not _REPORTLAB:
        raise ImportError("reportlab not installed — pip install reportlab")

    from reportlab.lib.pagesizes import A4
    TEAL = colors.HexColor("#00d4aa")

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=2.0 * cm, rightMargin=2.0 * cm,
        topMargin=2.0 * cm, bottomMargin=2.0 * cm,
    )
    styles = _styles()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    story = []

    # Cover
    story.append(Spacer(1, 40))
    story.append(Paragraph(family_office_name, styles["title"]))
    story.append(Paragraph("DeFi Investment Committee Report", ParagraphStyle(
        "ic_sub", parent=styles["subtitle"], fontSize=14, textColor=TEAL,
    )))
    story.append(Paragraph(f"Prepared: {ts}", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=2, color=TEAL, spaceAfter=20))
    story.append(Spacer(1, 10))

    # 1. Executive Summary
    story.append(Paragraph("1. Executive Summary", styles["section"]))
    _open = [p for p in portfolio_positions if p.get("status") in ("open", None, "")]
    _net_val   = sum(float(p.get("size_usd", 0)) for p in _open)
    _total_pnl = sum(float(p.get("realized_pnl", 0)) for p in portfolio_positions)
    _exec_data = [
        ["Metric", "Value"],
        ["Open Positions",     str(len(_open))],
        ["Deployed Capital",   f"${_net_val:,.0f}"],
        ["Total Realized P&L", f"${_total_pnl:+,.2f}"],
        ["Report Date",        datetime.now(timezone.utc).strftime("%d %b %Y")],
    ]
    _et = Table(_exec_data, colWidths=[8 * cm, 7 * cm])
    _et.setStyle(_table_style(len(_exec_data)))
    story.append(_et)
    story.append(Spacer(1, 12))

    # 2. Market Context
    story.append(Paragraph("2. Market Context", styles["section"]))
    _fg   = market_context.get("fear_greed", {})
    _pric = market_context.get("prices", {})
    _comp = market_context.get("composite_signal", {})
    _mkt_data = [
        ["Indicator", "Value", "Signal"],
        ["Fear & Greed",
         f"{_fg.get('value', 'N/A')} ({_fg.get('label', '-')})",
         f"7d avg: {_fg.get('avg_7d', '-')}"],
        ["FLR Price",
         _fmt(_pric.get("FLR", market_context.get("flr_price_usd", 0)), prefix="$", decimals=4),
         "-"],
        ["XRP Price",
         _fmt(_pric.get("XRP", market_context.get("xrp_price_usd", 0)), prefix="$", decimals=4),
         "-"],
        ["Composite Signal",
         str(_comp.get("regime", _comp.get("signal", "N/A"))),
         str(_comp.get("score", "-"))],
    ]
    _mt = Table(_mkt_data, colWidths=[6 * cm, 5 * cm, 4 * cm])
    _mt.setStyle(_table_style(len(_mkt_data)))
    story.append(_mt)
    story.append(Spacer(1, 12))

    # 3. Current Positions
    story.append(Paragraph("3. Current Positions", styles["section"]))
    if _open:
        _ph = ["Protocol", "Pool", "Chain", "Size ($)", "APY%", "Entry", "P&L ($)"]
        _pr = [_ph]
        for p in _open:
            _pv = float(p.get("unrealized_pnl", p.get("realized_pnl", 0)))
            _pr.append([
                str(p.get("protocol", "-"))[:18],
                str(p.get("pool", "-"))[:18],
                str(p.get("chain", "-"))[:8],
                _fmt(p.get("size_usd", 0), prefix="$", decimals=0),
                _fmt(p.get("expected_apy", p.get("apy", 0)), suffix="%", decimals=1),
                str(p.get("entry_timestamp", p.get("entry_date", "-")))[:10],
                f"${_pv:+,.2f}",
            ])
        _pt = Table(_pr, colWidths=[w * cm for w in [3.5, 3.5, 1.5, 2.5, 1.5, 2.5, 2.5]])
        _pt.setStyle(_table_style(len(_pr)))
        story.append(_pt)
    else:
        story.append(Paragraph("No open positions at time of report.", styles["body"]))
    story.append(Spacer(1, 12))

    # 4. Top Opportunities
    story.append(Paragraph("4. Top Yield Opportunities", styles["section"]))
    if top_opportunities:
        _oh = ["Protocol", "Pool", "Chain", "APY%", "TVL", "IL Risk"]
        _or = [_oh]
        for opp in top_opportunities[:5]:
            _ot = float(opp.get("tvl_usd", opp.get("tvlUsd", 0)))
            _or.append([
                str(opp.get("protocol", opp.get("project", "-")))[:18],
                str(opp.get("pool", opp.get("symbol", "-")))[:18],
                str(opp.get("chain", "-"))[:8],
                _fmt(opp.get("apy", 0), suffix="%", decimals=1),
                f"${_ot/1e6:.1f}M" if _ot >= 1e6 else f"${_ot:,.0f}",
                str(opp.get("il_risk", opp.get("ilRisk", "-"))).capitalize(),
            ])
        _ot2 = Table(_or, colWidths=[3.5*cm, 3.5*cm, 1.8*cm, 2.0*cm, 2.5*cm, 2.0*cm])
        _ot2.setStyle(_table_style(len(_or)))
        story.append(_ot2)
    else:
        story.append(Paragraph("No opportunities data available.", styles["body"]))
    story.append(Spacer(1, 12))

    # 5. Treasury Health (optional)
    if treasury_data:
        story.append(Paragraph("5. Protocol Treasury Health", styles["section"]))
        _th = ["Protocol", "Treasury", "Stablecoin %", "Health"]
        _tr = [_th]
        for t in treasury_data[:6]:
            _tv = float(t.get("tvl", 0))
            _tr.append([
                str(t.get("name", t.get("slug", "-")))[:22],
                f"${_tv/1e6:.0f}M" if _tv >= 1e6 else f"${_tv:,.0f}",
                f"{t.get('stablecoin_pct', 0):.0f}%",
                str(t.get("health", "-")),
            ])
        _tt2 = Table(_tr, colWidths=[6*cm, 3.5*cm, 3*cm, 4*cm])
        _tt2.setStyle(_table_style(len(_tr)))
        story.append(_tt2)
        story.append(Spacer(1, 12))

    # 6. Agent Performance (optional)
    if agent_stats:
        story.append(Paragraph("6. AI Agent Performance", styles["section"]))
        _ad = [
            ["Metric", "Value"],
            ["Paper Trading Days", str(agent_stats.get("paper_days", "-"))],
            ["Total Trades",       str(agent_stats.get("total_trades", "-"))],
            ["Win Rate",           _fmt(agent_stats.get("win_rate", 0), suffix="%", decimals=1)],
            ["Total P&L",          _fmt(agent_stats.get("total_pnl", 0), prefix="$", decimals=2)],
            ["Mode",               str(agent_stats.get("mode", "PAPER"))],
        ]
        _at = Table(_ad, colWidths=[8*cm, 7*cm])
        _at.setStyle(_table_style(len(_ad)))
        story.append(_at)
        story.append(Spacer(1, 12))

    # 7. Risk Summary + Disclaimer
    story.append(Paragraph("7. Risk Summary & Disclaimer", styles["section"]))
    story.append(Paragraph(
        "This report is generated automatically by the Flare DeFi Model AI system "
        "for internal family office use only. All positions and opportunities are "
        "based on real-time or recently cached market data. Paper trading P&L is "
        "simulated and does not reflect actual executed trades. APY figures are "
        "historical estimates. Smart contract, liquidity, oracle, and regulatory "
        "risks apply to all DeFi positions. Not financial advice.",
        styles["body"],
    ))
    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5, color=GREY))
    story.append(Paragraph(
        f"{family_office_name} | DeFi Investment Committee | {ts} | Confidential",
        styles["footer"],
    ))
    doc.build(story)
    return buf.getvalue()

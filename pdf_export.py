"""
pdf_export.py — Flare DeFi Model
Opportunity, arbitrage, and investment committee PDF report generation using fpdf2.
Returns raw PDF bytes for Streamlit st.download_button().
"""

import io
from datetime import datetime, timezone
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# ── Color palette ─────────────────────────────────────────────────────────────
_TEAL   = (0, 212, 170)     # #00d4aa
_RED    = (232, 0, 77)      # #E8004D Flare brand
_GREY   = (136, 136, 136)   # #888888
_LGREY  = (245, 245, 245)   # #f5f5f5
_WHITE  = (255, 255, 255)
_BLACK  = (0, 0, 0)
_DKBLUE = (17, 24, 39)      # dark header

# Page dimensions (A4 landscape in mm)
_LM = 15   # left margin
_RM = 15   # right margin
_A4_L_W = 297   # landscape width mm
_A4_W   = 210   # portrait width mm


def _ps(s: str) -> str:
    """Sanitize text for FPDF Helvetica (latin-1 only — no unicode special chars)."""
    return (str(s)
            .replace("\u2014", "-").replace("\u2013", "-")
            .replace("\u2018", "'").replace("\u2019", "'")
            .replace("\u201c", '"').replace("\u201d", '"')
            .replace("\u2022", "*").replace("\u00b7", ".")
            .encode("latin-1", errors="replace").decode("latin-1"))


def _fmt(val, prefix="", suffix="", decimals=2, fallback="—"):
    try:
        return f"{prefix}{float(val):,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return fallback


def _header_row(pdf: FPDF, cols: list[str], widths: list[float], row_h: float = 7):
    """Draw a single header row with teal background."""
    pdf.set_fill_color(*_TEAL)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 7)
    for c, w in zip(cols, widths):
        pdf.cell(w, row_h, _ps(c), border=1, fill=True, align="C")
    pdf.ln()


def _data_row(pdf: FPDF, vals: list[str], widths: list[float],
              row_h: float = 6, even: bool = True):
    """Draw a single data row with alternating background."""
    pdf.set_fill_color(*(_LGREY if even else _WHITE))
    pdf.set_text_color(*_BLACK)
    pdf.set_font("Helvetica", "", 6)
    for v, w in zip(vals, widths):
        pdf.cell(w, row_h, _ps(v), border=1, fill=True, align="L")
    pdf.ln()


def _section(pdf: FPDF, title: str):
    """Draw a section heading."""
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_TEAL)
    pdf.ln(4)
    pdf.cell(0, 8, _ps(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_TEAL)
    pdf.set_line_width(0.4)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)
    pdf.set_text_color(*_BLACK)
    pdf.set_draw_color(*_BLACK)
    pdf.set_line_width(0.2)


def _footer_line(pdf: FPDF, text: str):
    # Only add footer on the last page — avoid creating a blank extra page
    # by checking we're not already near the bottom (which would push to a new page)
    if pdf.get_y() < pdf.h - 25:
        pdf.set_y(-15)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(*_GREY)
    pdf.cell(0, 5, _ps(text), align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ─── Opportunities Report ─────────────────────────────────────────────────────

def generate_opportunities_pdf(model_results: dict) -> bytes:
    """
    Generate a DeFi opportunities PDF report (landscape A4).

    Args:
        model_results: dict of {risk_profile: [opportunity, ...]}
    Returns:
        Raw PDF bytes.
    """
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(_LM, 15, _RM)
    pdf.add_page()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_TEAL)
    pdf.cell(0, 10, "Flare DeFi Model - Opportunities Report",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GREY)
    pdf.cell(0, 6, f"Generated: {ts}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_TEAL)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(5)

    cols   = ["Protocol", "Pool/Asset", "APY%", "Conf%", "Risk", "TVL ($M)", "Strategy", "Urgency"]
    widths = [38, 52, 18, 18, 18, 22, 36, 22]   # total ~224 mm (fits landscape 297 - 30 margins)

    for profile, opportunities in model_results.items():
        if not opportunities:
            continue
        _section(pdf, f"{profile.upper()} Profile")
        _header_row(pdf, cols, widths)
        for i, o in enumerate(opportunities[:20]):
            tvl_m = (o.get("tvl_usd") or 0) / 1_000_000
            row = [
                str(o.get("protocol") or "?")[:22],
                str(o.get("asset_or_pool") or "?")[:28],
                _fmt(o.get("estimated_apy"), suffix="%", decimals=1),
                _fmt(o.get("confidence"), suffix="%", decimals=0),
                _fmt(o.get("risk_score"), decimals=1),
                _fmt(tvl_m, prefix="$", decimals=1) if tvl_m > 0 else "—",
                str(o.get("strategy") or o.get("opportunity_type") or "")[:20],
                str(o.get("urgency") or "normal")[:14],
            ]
            _data_row(pdf, row, widths, even=i % 2 == 0)

    _footer_line(pdf, "Flare DeFi Model  |  For informational purposes only. Not financial advice.")
    return bytes(pdf.output())


# ─── Arbitrage Report ─────────────────────────────────────────────────────────

def generate_arb_pdf(arb_results: dict) -> bytes:
    """
    Generate a DeFi arbitrage opportunities PDF report (landscape A4).

    Args:
        arb_results: dict of {risk_profile: [arb_opportunity, ...]}
    Returns:
        Raw PDF bytes.
    """
    pdf = FPDF(orientation="L", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(_LM, 15, _RM)
    pdf.add_page()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*_TEAL)
    pdf.cell(0, 10, "Flare DeFi Model - Arbitrage Report",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GREY)
    pdf.cell(0, 6, f"Generated: {ts}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(*_TEAL)
    pdf.set_line_width(0.6)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(5)

    all_arbs = []
    for profile, arbs in arb_results.items():
        if isinstance(arbs, list):
            for a in arbs:
                entry = dict(a)
                entry["_profile"] = profile
                all_arbs.append(entry)

    if not all_arbs:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(*_BLACK)
        pdf.cell(0, 8, "No arbitrage opportunities detected.",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        _footer_line(pdf, "Flare DeFi Model  |  For informational purposes only.")
        return bytes(pdf.output())

    _section(pdf, f"Total Opportunities: {len(all_arbs)}")
    cols   = ["Profile", "Strategy", "Asset A", "Asset B", "Net%", "Capital", "Urgency"]
    widths = [24, 44, 44, 44, 20, 30, 24]   # ~230 mm

    _header_row(pdf, cols, widths)
    sorted_arbs = sorted(all_arbs, key=lambda x: x.get("estimated_profit", 0) or 0, reverse=True)
    for i, a in enumerate(sorted_arbs):
        row = [
            str(a.get("_profile") or "")[:12],
            str(a.get("strategy_label") or a.get("opportunity_type") or "")[:24],
            str(a.get("leg_a_protocol") or a.get("asset_a") or "?")[:24],
            str(a.get("leg_b_protocol") or a.get("asset_b") or "?")[:24],
            _fmt(a.get("estimated_profit"), suffix="%", decimals=2),
            _fmt(a.get("min_capital_usd"), prefix="$", decimals=0) if a.get("min_capital_usd") else "—",
            str(a.get("urgency") or "normal")[:14],
        ]
        _data_row(pdf, row, widths, even=i % 2 == 0)

    _footer_line(pdf, "Flare DeFi Model  |  For informational purposes only. Not financial advice.")
    return bytes(pdf.output())


# ─── Family Office Investment Committee Report ────────────────────────────────

def generate_investment_committee_pdf(
    portfolio_positions,
    top_opportunities,
    market_context,
    agent_stats=None,
    treasury_data=None,
    family_office_name="Family Office",
) -> bytes:
    """
    Generate an investment committee PDF report (portrait A4).

    Sections:
      1. Executive Summary
      2. Market Context
      3. Current Positions
      4. Top 5 Yield Opportunities
      5. Protocol Treasury Health (optional)
      6. AI Agent Performance (optional)
      7. Risk Summary + Disclaimer

    Returns raw PDF bytes for Streamlit st.download_button().
    """
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    usable_w = _A4_W - 40   # 210 - 20l - 20r = 170 mm

    # ── Cover ──────────────────────────────────────────────────────────────────
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(*_TEAL)
    pdf.cell(0, 12, _ps(family_office_name), new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "B", 14)
    pdf.set_text_color(*_DKBLUE)
    pdf.cell(0, 8, "DeFi Investment Committee Report",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GREY)
    pdf.cell(0, 6, f"Prepared: {ts}",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_draw_color(*_TEAL)
    pdf.set_line_width(0.8)
    pdf.ln(2)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(8)

    # ── 1. Executive Summary ───────────────────────────────────────────────────
    _section(pdf, "1. Executive Summary")
    _open      = [p for p in portfolio_positions if p.get("status") in ("open", None, "")]
    _net_val   = sum(float(p.get("size_usd", 0)) for p in _open)
    _total_pnl = sum(float(p.get("realized_pnl", 0)) for p in portfolio_positions)

    exec_cols   = ["Metric", "Value"]
    exec_widths = [90, 80]
    exec_data   = [
        ["Open Positions",     str(len(_open))],
        ["Deployed Capital",   f"${_net_val:,.0f}"],
        ["Total Realized P&L", f"${_total_pnl:+,.2f}"],
        ["Report Date",        datetime.now(timezone.utc).strftime("%d %b %Y")],
    ]
    _header_row(pdf, exec_cols, exec_widths)
    for i, row in enumerate(exec_data):
        _data_row(pdf, row, exec_widths, even=i % 2 == 0)
    pdf.ln(4)

    # ── 2. Market Context ──────────────────────────────────────────────────────
    _section(pdf, "2. Market Context")
    _fg   = market_context.get("fear_greed", {})
    _pric = market_context.get("prices", {})
    _comp = market_context.get("composite_signal", {})
    mkt_cols   = ["Indicator", "Value", "Signal"]
    mkt_widths = [60, 55, 55]
    mkt_data   = [
        ["Fear & Greed",
         f"{_fg.get('value', '—')} ({_fg.get('label', '-')})",
         f"7d avg: {_fg.get('avg_7d', '-')}"],
        ["FLR Price",
         _fmt(_pric.get("FLR", market_context.get("flr_price_usd", 0)), prefix="$", decimals=4),
         "-"],
        ["XRP Price",
         _fmt(_pric.get("XRP", market_context.get("xrp_price_usd", 0)), prefix="$", decimals=4),
         "-"],
        ["Composite Signal",
         str(_comp.get("regime", _comp.get("signal", "—"))),
         str(_comp.get("score", "-"))],
    ]
    _header_row(pdf, mkt_cols, mkt_widths)
    for i, row in enumerate(mkt_data):
        _data_row(pdf, row, mkt_widths, even=i % 2 == 0)
    pdf.ln(4)

    # ── 3. Current Positions ───────────────────────────────────────────────────
    _section(pdf, "3. Current Positions")
    if _open:
        pos_cols   = ["Protocol", "Pool", "Chain", "Size ($)", "APY%", "Entry", "P&L ($)"]
        pos_widths = [30, 30, 16, 22, 16, 26, 22]   # 162 mm
        _header_row(pdf, pos_cols, pos_widths)
        for i, p in enumerate(_open):
            _pv = float(p.get("unrealized_pnl", p.get("realized_pnl", 0)))
            row = [
                str(p.get("protocol", "-"))[:20],
                str(p.get("pool", "-"))[:20],
                str(p.get("chain", "-"))[:8],
                _fmt(p.get("size_usd", 0), prefix="$", decimals=0),
                _fmt(p.get("expected_apy", p.get("apy", 0)), suffix="%", decimals=1),
                str(p.get("entry_timestamp", p.get("entry_date", "-")))[:10],
                f"${_pv:+,.2f}",
            ]
            _data_row(pdf, row, pos_widths, even=i % 2 == 0)
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 7, "No open positions at time of report.",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*_BLACK)
    pdf.ln(4)

    # ── 4. Top Opportunities ───────────────────────────────────────────────────
    _section(pdf, "4. Top Yield Opportunities")
    if top_opportunities:
        opp_cols   = ["Protocol", "Pool", "Chain", "APY%", "TVL", "IL Risk"]
        opp_widths = [36, 36, 18, 20, 28, 24]   # 162 mm
        _header_row(pdf, opp_cols, opp_widths)
        for i, opp in enumerate(top_opportunities[:5]):
            try:
                _ot = float(opp.get("tvl_usd", opp.get("tvlUsd", 0)) or 0)
            except (TypeError, ValueError):
                _ot = 0.0
            row = [
                str(opp.get("protocol", opp.get("project", "-")))[:20],
                str(opp.get("pool", opp.get("symbol", "-")))[:20],
                str(opp.get("chain", "-"))[:8],
                _fmt(opp.get("apy", 0), suffix="%", decimals=1),
                f"${_ot/1e6:.1f}M" if _ot >= 1e6 else f"${_ot:,.0f}",
                str(opp.get("il_risk", opp.get("ilRisk", "-"))).capitalize(),
            ]
            _data_row(pdf, row, opp_widths, even=i % 2 == 0)
    else:
        pdf.set_font("Helvetica", "I", 9)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 7, "No opportunities data available.",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_text_color(*_BLACK)
    pdf.ln(4)

    # ── 5. Treasury Health (optional) ─────────────────────────────────────────
    if treasury_data:
        _section(pdf, "5. Protocol Treasury Health")
        th_cols   = ["Protocol", "Treasury", "Stablecoin %", "Health"]
        th_widths = [60, 38, 30, 34]
        _header_row(pdf, th_cols, th_widths)
        for i, t in enumerate(treasury_data[:6]):
            _tv = float(t.get("tvl", 0))
            row = [
                str(t.get("name", t.get("slug", "-")))[:28],
                f"${_tv/1e6:.0f}M" if _tv >= 1e6 else f"${_tv:,.0f}",
                f"{t.get('stablecoin_pct', 0):.0f}%",
                str(t.get("health", "-")),
            ]
            _data_row(pdf, row, th_widths, even=i % 2 == 0)
        pdf.ln(4)

    # ── 6. Agent Performance (optional) ───────────────────────────────────────
    if agent_stats:
        _section(pdf, "6. AI Agent Performance")
        ag_cols   = ["Metric", "Value"]
        ag_widths = [90, 80]
        ag_data   = [
            ["Paper Trading Days", str(agent_stats.get("paper_days", "-"))],
            ["Total Trades",       str(agent_stats.get("total_trades", "-"))],
            ["Win Rate",           _fmt(agent_stats.get("win_rate", 0), suffix="%", decimals=1)],
            ["Total P&L",          _fmt(agent_stats.get("total_pnl", 0), prefix="$", decimals=2)],
            ["Mode",               str(agent_stats.get("mode", "PAPER"))],
        ]
        _header_row(pdf, ag_cols, ag_widths)
        for i, row in enumerate(ag_data):
            _data_row(pdf, row, ag_widths, even=i % 2 == 0)
        pdf.ln(4)

    # ── 7. Risk Summary + Disclaimer ──────────────────────────────────────────
    _section(pdf, "7. Risk Summary & Disclaimer")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_BLACK)
    disclaimer = (
        "This report is generated automatically by the Flare DeFi Model AI system "
        "for internal family office use only. All positions and opportunities are "
        "based on real-time or recently cached market data. Paper trading P&L is "
        "simulated and does not reflect actual executed trades. APY figures are "
        "historical estimates. Smart contract, liquidity, oracle, and regulatory "
        "risks apply to all DeFi positions. Not financial advice."
    )
    pdf.multi_cell(0, 5, _ps(disclaimer), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _footer_line(pdf, f"{family_office_name} | DeFi Investment Committee | {ts} | Confidential")
    return bytes(pdf.output())


# ─── RIA Advisor Report (GIPS-compatible, compliance-ready) ──────────────────

def generate_ria_advisor_pdf(
    model_results: dict,
    composite_signal: dict = None,
    advisor_name: str = "",
    client_name: str = "",
    brand_name: str = "",
) -> bytes:
    """
    Generate an RIA-grade advisor PDF suitable for client meetings and platform
    integration (e.g. UX Wealth Partners / TAMP embed).

    Compliant language:
      - "Suggested allocation" (not "buy" or "recommendation to trade")
      - GIPS-compatible return labeling: APY displayed as time-weighted yield
      - Risk score displayed with letter grade + compliance caveat
      - Full disclaimer section required for RIA use

    Args:
        model_results:    {risk_profile: [opportunity, ...]}
        composite_signal: composite signal dict (optional — adds market context)
        advisor_name:     name of the advisor preparing the report
        client_name:      client name (or blank for generic report)
        brand_name:       white-label brand override

    Returns:
        Raw PDF bytes for st.download_button()
    """
    pdf = FPDF(orientation="P", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    ts       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ts_short = datetime.now(timezone.utc).strftime("%d %b %Y")
    usable_w = _A4_W - 40   # 170mm usable

    _brand = _ps(brand_name or "DeFi Intelligence Platform")

    # ── Cover ─────────────────────────────────────────────────────────────────
    pdf.ln(10)
    pdf.set_font("Helvetica", "B", 20)
    pdf.set_text_color(*_TEAL)
    pdf.cell(0, 12, _brand, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*_DKBLUE)
    pdf.cell(0, 8, "DeFi Yield Opportunity Summary",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_GREY)
    report_line = f"Prepared: {ts_short}"
    if advisor_name:
        report_line += f"  |  Advisor: {_ps(advisor_name)}"
    if client_name:
        report_line += f"  |  Client: {_ps(client_name)}"
    pdf.cell(0, 6, report_line, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_draw_color(*_TEAL)
    pdf.set_line_width(0.8)
    pdf.ln(2)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(6)

    # ── GIPS / Compliance Header ───────────────────────────────────────────────
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(100, 100, 130)
    pdf.multi_cell(0, 4,
        _ps("GIPS Notice: APY figures represent annualized time-weighted yield rates observed "
            "from live protocol data. Past yield rates are not indicative of future performance. "
            "All figures are informational only and do not constitute a recommendation to buy, sell, "
            "or hold any asset. This report is prepared for qualified investors and RIA clients only."),
        new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_draw_color(200, 200, 220)
    pdf.set_line_width(0.2)
    pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
    pdf.ln(5)
    pdf.set_text_color(*_BLACK)

    # ── Market Environment (if composite signal available) ────────────────────
    if composite_signal and composite_signal.get("signal"):
        _section(pdf, "Market Environment Summary")
        _sig   = composite_signal.get("signal", "—")
        _score = composite_signal.get("score", 0)
        _summ  = composite_signal.get("beginner_summary", "Market conditions are mixed.")
        _layers = composite_signal.get("layers", {})

        env_cols   = ["Layer", "Score", "Interpretation"]
        env_widths = [42, 22, 106]
        _header_row(pdf, env_cols, env_widths)
        _layer_rows = [
            ("Technical (BTC RSI/MA)",  _layers.get("technical", {}).get("score", "-"),
             "Price momentum and trend direction"),
            ("Macro Environment",        _layers.get("macro",     {}).get("score", "-"),
             "DXY, VIX, yield curve, inflation"),
            ("Market Sentiment",         _layers.get("sentiment", {}).get("score", "-"),
             "Fear & Greed, SOPR, options positioning"),
            ("On-Chain Activity",        _layers.get("onchain",   {}).get("score", "-"),
             "MVRV Z-Score, Hash Ribbons, Puell Multiple"),
            ("Composite Score",          f"{_score:+.3f}" if isinstance(_score, float) else str(_score),
             f"Overall: {_sig}"),
        ]
        for i, (layer, score, interp) in enumerate(_layer_rows):
            _data_row(pdf, [_ps(layer), str(score), _ps(interp)], env_widths, even=i % 2 == 0)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_GREY)
        pdf.cell(0, 5, _ps(f"Summary: {_summ}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

    # ── Suggested Allocations by Risk Profile ─────────────────────────────────
    _PROFILE_LABELS = {
        "conservative": "Capital Preservation (Conservative)",
        "medium":       "Balanced Growth (Moderate Risk)",
        "high":         "Yield Maximization (Aggressive)",
    }
    cols   = ["Protocol", "Pool / Asset", "Suggested Alloc. APY", "Risk Grade", "IL Risk", "Confidence"]
    widths = [32, 42, 36, 22, 20, 18]   # ~170mm

    for profile_key in ("conservative", "medium", "high"):
        opps = model_results.get(profile_key, [])
        if not opps:
            continue
        _section(pdf, _PROFILE_LABELS.get(profile_key, profile_key.upper()))
        _header_row(pdf, cols, widths)
        for i, o in enumerate(opps[:10]):
            try:
                _risk_num = float(o.get("risk_score") or 5.0)
            except (TypeError, ValueError):
                _risk_num = 5.0
            _grade, _ = (("A", "#22c55e") if _risk_num < 2 else
                         ("B", "#10b981") if _risk_num < 3.5 else
                         ("C", "#f59e0b") if _risk_num < 5.0 else
                         ("D", "#f59e0b") if _risk_num < 7.0 else
                         ("F", "#ef4444"))
            row = [
                str(o.get("protocol") or "?")[:18],
                str(o.get("asset_or_pool") or "?")[:24],
                _fmt(o.get("estimated_apy"), suffix="% APY (TWR)", decimals=1),
                f"{_grade} ({_fmt(o.get('risk_score', 5), decimals=1)}/10)",
                str(o.get("il_risk", "—")).capitalize(),
                _fmt(o.get("confidence"), suffix="%", decimals=0),
            ]
            _data_row(pdf, row, widths, even=i % 2 == 0)
        pdf.ln(3)

    # ── Regulatory Disclaimer ─────────────────────────────────────────────────
    _section(pdf, "Regulatory Disclaimer & Disclosures")
    pdf.set_font("Helvetica", "", 7.5)
    pdf.set_text_color(80, 80, 100)
    disc = (
        "This document has been prepared for informational purposes only and does not "
        "constitute investment advice, a solicitation, or an offer to buy or sell any "
        "security or financial instrument. The information contained herein is based on "
        "sources believed to be reliable, but no representation or warranty, express or "
        "implied, is made as to its accuracy, completeness, or timeliness. "
        "DeFi protocols involve significant risks including smart contract vulnerabilities, "
        "impermanent loss, liquidity risk, oracle manipulation risk, and regulatory risk. "
        "Past yield rates are not a guarantee of future results. All 'suggested allocation' "
        "figures are model outputs only and must be reviewed by a licensed investment advisor "
        "before any client action is taken. APY figures labeled (TWR) represent annualized "
        "time-weighted rates of observed protocol yield. This report does not claim GIPS "
        "compliance but is formatted to be consistent with GIPS disclosure standards. "
        "For RIA use only. Not for distribution to retail investors without appropriate suitability review."
    )
    pdf.multi_cell(0, 4.5, _ps(disc), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    _footer_line(pdf, f"{_brand}  |  {ts}  |  For Qualified Investors / RIA Use Only  |  Confidential")
    return bytes(pdf.output())

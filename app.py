"""
Flare DeFi Model — Dashboard (Home)
Multi-page app entry point. Shows prices, top opportunities, and arb alerts.
Run with:  streamlit run app.py
"""

import sys
import html as _html
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv()

from ui.common import (
    page_setup, render_sidebar, load_latest, load_positions,
    render_price_strip, render_incentive_warning,
    render_yield_hero_cards, render_opportunity_card,
    render_urgency_badge, render_section_header, _ts_fmt, load_live_prices,
)
import streamlit as st

page_setup("Dashboard · Flare DeFi")

ctx           = render_sidebar()
profile       = ctx["profile"]
profile_cfg   = ctx["profile_cfg"]
color         = ctx["color"]
weight        = ctx["weight"]
portfolio_size = ctx["portfolio_size"]

latest    = load_latest()
positions = load_positions()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='margin-bottom:4px;'>Dashboard</h1>"
    "<div style='color:#475569; font-size:0.87rem; margin-bottom:20px; "
    "display:flex; align-items:center; gap:12px; flex-wrap:wrap;'>"
    "<span>Live prices</span>"
    "<span style='color:#1e293b;'>·</span>"
    "<span>Top opportunities</span>"
    "<span style='color:#1e293b;'>·</span>"
    "<span>Arbitrage alerts</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Incentive Warning ─────────────────────────────────────────────────────────
render_incentive_warning()

# ── Price Strip (live — refreshed every 2 min, not from stale scan data) ──────
flare_scan = latest.get("flare_scan", {})
prices     = load_live_prices() or flare_scan.get("prices", [])
render_price_strip(prices)

# ── Data Freshness ────────────────────────────────────────────────────────────
all_pts = (flare_scan.get("pools") or []) + (flare_scan.get("lending") or []) + (flare_scan.get("staking") or [])
if all_pts:
    total     = len(all_pts)
    live      = sum(1 for p in all_pts if p.get("data_source") == "live")
    estimated = sum(1 for p in all_pts if p.get("data_source") in ("baseline", "estimate"))
    live_pct  = live / total if total else 0
    pill_bg   = "rgba(34,197,94,0.10)"  if live_pct >= 0.7 else ("rgba(245,158,11,0.10)" if live > 0 else "rgba(239,68,68,0.10)")
    pill_border = "rgba(34,197,94,0.25)" if live_pct >= 0.7 else ("rgba(245,158,11,0.25)" if live > 0 else "rgba(239,68,68,0.25)")
    pill_color  = "#22c55e"  if live_pct >= 0.7 else ("#f59e0b" if live > 0 else "#ef4444")
    dot_cls     = "live-dot" if live_pct >= 0.7 else "stale-dot"
    fresh_label = f"{live}/{total} live" + (f" · {estimated} estimated" if estimated else "")
    st.markdown(
        f"<div style='display:inline-flex; align-items:center; gap:6px; "
        f"background:{pill_bg}; border:1px solid {pill_border}; "
        f"border-radius:20px; padding:3px 12px; margin:6px 0 16px; font-size:0.74rem;'>"
        f"<span class='{dot_cls}'></span>"
        f"<span style='color:{pill_color}; font-weight:600;'>{fresh_label}</span>"
        f"<span style='color:#334155;'>data points</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    for warn in flare_scan.get("warnings", []):
        st.markdown(
            f"<div class='warn-box' style='font-size:0.82rem; padding:10px 14px;'>⚠️ {_html.escape(str(warn))}</div>",
            unsafe_allow_html=True,
        )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Yield Hero Cards ──────────────────────────────────────────────────────────
model_data = latest.get("models") or {}
opps       = model_data.get(profile, [])

render_section_header("Estimated Yield", "Projected returns based on your top-3 ranked opportunities")
render_yield_hero_cards(positions, opps, portfolio_size)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Top Opportunities ─────────────────────────────────────────────────────────
render_section_header(f"Top Opportunities", f"{profile_cfg['label']} · {profile_cfg['target_apy_low']:.0f}–{profile_cfg['target_apy_high']:.0f}% target APY")

if not opps:
    st.info("No scan data yet. Run `python scheduler.py --now` to generate your first scan.")
else:
    for i, opp in enumerate(opps[:3]):
        render_opportunity_card(opp, i, color, portfolio_size, weight)

    if len(opps) > 3:
        with st.expander(f"Show all {len(opps)} opportunities"):
            for i, opp in enumerate(opps[3:], start=3):
                render_opportunity_card(opp, i, color, portfolio_size, weight)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Arbitrage Alerts ──────────────────────────────────────────────────────────
render_section_header("Arbitrage Alerts", "Real-time profit opportunities from price differences across platforms")

arb_data = (latest.get("arbitrage") or {}).get(profile, [])
if not arb_data:
    st.markdown(
        "<div style='color:#334155; font-size:0.88rem; padding:16px 0;'>"
        "No significant arbitrage detected right now.</div>",
        unsafe_allow_html=True,
    )
else:
    for arb in arb_data[:5]:
        profit        = arb.get("estimated_profit", 0)
        urgency       = arb.get("urgency", "monitor")
        label         = _html.escape(str(arb.get("strategy_label", arb.get("strategy", "Arb"))))
        desc          = _html.escape(str(arb.get("plain_english", "—")))
        token         = _html.escape(str(arb.get("token_or_pair", "—")))
        st.markdown(f"""
        <div class="arb-tag">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f1f5f9;">⚡ {label} · {token}</span>
                {render_urgency_badge(urgency)}
            </div>
            <div style="color:#94a3b8; font-size:0.88rem; margin-top:8px;">{desc}</div>
            <div style="color:#475569; font-size:0.8rem; margin-top:8px;">
                Estimated profit: <span style="color:#10b981; font-weight:700;">+{profit:.2f}%</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

warnings = latest.get("warnings", [])
if warnings:
    with st.expander("⚠️ Data Quality Notes"):
        for w in warnings:
            st.markdown(f"- {w}")

st.markdown(
    "<div style='color:#1e293b; font-size:0.70rem; text-align:center; padding-top:4px; line-height:1.7;'>"
    "Flare DeFi Model · Blazeswap · SparkDEX · Ēnosys · Kinetic · Clearpool · Spectra · Upshift · Mystic · Hyperliquid<br>"
    "<span style='color:#64748b;'>Not financial advice · Always DYOR</span>"
    "</div>",
    unsafe_allow_html=True,
)

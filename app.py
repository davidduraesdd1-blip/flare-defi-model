"""
Flare DeFi Model — Dashboard (Home)
Multi-page app entry point. Shows prices, top opportunities, and arb alerts.
Run with:  streamlit run app.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

from ui.common import (
    page_setup, render_sidebar, load_latest, load_positions,
    render_price_strip, render_incentive_warning,
    render_yield_hero_cards, render_opportunity_card,
    _ts_fmt,
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
st.markdown("# Dashboard")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:20px;'>"
    "Live prices · top opportunities · arbitrage alerts</div>",
    unsafe_allow_html=True,
)

# ── Incentive Warning ─────────────────────────────────────────────────────────
render_incentive_warning()

# ── Price Strip ───────────────────────────────────────────────────────────────
flare_scan = latest.get("flare_scan", {})
prices     = flare_scan.get("prices", [])
render_price_strip(prices)

# ── Data Freshness ────────────────────────────────────────────────────────────
all_pts = flare_scan.get("pools", []) + flare_scan.get("lending", []) + flare_scan.get("staking", [])
if all_pts:
    total     = len(all_pts)
    live      = sum(1 for p in all_pts if p.get("data_source") == "live")
    estimated = sum(1 for p in all_pts if p.get("data_source") in ("baseline", "estimate"))
    dot_color = "#10b981" if live / total >= 0.7 else ("#f59e0b" if live > 0 else "#ef4444")
    parts     = [f"<span style='color:{dot_color}'>● {live} live</span>"]
    if estimated:
        parts.append(f"<span style='color:#ef4444'>{estimated} estimated</span>")
    st.markdown(
        f"<div style='font-size:0.75rem; color:#475569; margin:8px 0 16px;'>"
        f"Data freshness: {' · '.join(parts)} of {total}</div>",
        unsafe_allow_html=True,
    )
    for warn in flare_scan.get("warnings", []):
        st.markdown(
            f"<div class='warn-box' style='font-size:0.82rem; padding:10px 14px;'>⚠️ {warn}</div>",
            unsafe_allow_html=True,
        )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Yield Hero Cards ──────────────────────────────────────────────────────────
model_data = latest.get("models", {})
opps       = model_data.get(profile, [])

st.markdown("### Estimated Yield")
render_yield_hero_cards(positions, opps, portfolio_size)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Top Opportunities ─────────────────────────────────────────────────────────
st.markdown(f"### Top Opportunities — {profile_cfg['label']}")

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
st.markdown("### Arbitrage Alerts")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:14px;'>"
    "Real-time profit opportunities from price differences across platforms.</div>",
    unsafe_allow_html=True,
)

arb_data = latest.get("arbitrage", {}).get(profile, [])
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
        label         = arb.get("strategy_label", arb.get("strategy", "Arb"))
        desc          = arb.get("plain_english", "—")
        token         = arb.get("token_or_pair", "—")
        urgency_color = {"act_now": "#ef4444", "act_soon": "#f59e0b", "monitor": "#3b82f6"}.get(urgency, "#3b82f6")
        urgency_label = {"act_now": "ACT NOW", "act_soon": "ACT SOON", "monitor": "MONITOR"}.get(urgency, urgency.upper())
        st.markdown(f"""
        <div class="arb-tag">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f1f5f9;">⚡ {label} · {token}</span>
                <span style="color:{urgency_color}; font-weight:700; font-size:0.78rem;
                      background:rgba(255,255,255,0.04); padding:3px 10px; border-radius:6px;">
                    {urgency_label}
                </span>
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
    "<div style='color:#1e293b; font-size:0.72rem; text-align:center; padding-top:8px;'>"
    "Flare DeFi Model · Blazeswap · SparkDEX · Ēnosys · Kinetic · Clearpool · Spectra · Upshift · Mystic · Hyperliquid"
    "</div>",
    unsafe_allow_html=True,
)

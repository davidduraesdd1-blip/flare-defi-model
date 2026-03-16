"""
Opportunities — Full opportunity tables, starter portfolios, sparklines, options strategies.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

from ui.common import (
    page_setup, render_sidebar, load_latest, load_history_runs,
    render_opportunity_card, risk_score_to_grade,
)
from config import RISK_PROFILES, RISK_PROFILE_NAMES

page_setup("Opportunities · Flare DeFi")

ctx            = render_sidebar()
profile        = ctx["profile"]
profile_cfg    = ctx["profile_cfg"]
color          = ctx["color"]
weight         = ctx["weight"]
portfolio_size = ctx["portfolio_size"]

latest     = load_latest()
runs       = load_history_runs()
model_data = latest.get("models", {})

st.markdown("# Opportunities")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "All ranked opportunities · starter portfolios · APY trends · options strategies</div>",
    unsafe_allow_html=True,
)


# ─── All Opportunities (all 3 profiles) ──────────────────────────────────────

tab_con, tab_med, tab_high = st.tabs(["🟢  Conservative", "🟡  Balanced", "🔴  Aggressive"])

for tab, p in [(tab_con, "conservative"), (tab_med, "medium"), (tab_high, "high")]:
    with tab:
        opps  = model_data.get(p, [])
        pcfg  = RISK_PROFILES[p]
        pcol  = pcfg["color"]
        w     = weight if p == profile else 1.0

        st.markdown(
            f"<div style='color:{pcol}; font-size:0.88rem; margin-bottom:14px;'>"
            f"{pcfg['label']} · Target {pcfg['target_apy_low']:.0f}–{pcfg['target_apy_high']:.0f}% APY</div>",
            unsafe_allow_html=True,
        )

        if not opps:
            st.info("No scan data yet. Run `python scheduler.py --now` first.")
            continue

        view = st.radio("View as", ["Cards", "Table"], key=f"view_{p}", horizontal=True)

        if view == "Cards":
            for i, opp in enumerate(opps[:6]):
                render_opportunity_card(opp, i, pcol, portfolio_size, w)
        else:
            rows = []
            for opp in opps[:8]:
                grade, _ = risk_score_to_grade(opp.get("risk_score", 5))
                kf       = opp.get("kelly_fraction", 0)
                rows.append({
                    "Protocol":    opp.get("protocol", "—"),
                    "Pool / Asset": opp.get("asset_or_pool", "—"),
                    "Est. APY":    f"{opp.get('estimated_apy', 0):.1f}%",
                    "Range":       f"{opp.get('apy_low', 0):.0f}–{opp.get('apy_high', 0):.0f}%",
                    "Grade":       grade,
                    "Alloc %":     f"{kf*100:.0f}%",
                    "$ Amount":    f"${kf*portfolio_size:,.0f}" if portfolio_size > 0 else "—",
                    "IL Risk":     opp.get("il_risk", "—").upper(),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Starter Portfolios ───────────────────────────────────────────────────────

st.markdown("### Starter Portfolios")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:14px;'>"
    "Pre-built Kelly-sized allocations for each risk profile.</div>",
    unsafe_allow_html=True,
)

for p in RISK_PROFILE_NAMES:
    opps = model_data.get(p, [])
    pcfg = RISK_PROFILES[p]
    pcol = pcfg["color"]
    if not opps:
        continue
    with st.expander(f"{pcfg['label']} — {pcfg['target_apy_low']:.0f}–{pcfg['target_apy_high']:.0f}% target"):
        rows = []
        for opp in opps[:6]:
            kf    = opp.get("kelly_fraction", 0)
            grade, _ = risk_score_to_grade(opp.get("risk_score", 5))
            rows.append({
                "Protocol":    opp.get("protocol", "—"),
                "Pool / Asset": opp.get("asset_or_pool", "—"),
                "Est. APY":    f"{opp.get('estimated_apy', 0):.1f}%",
                "Alloc %":     f"{kf*100:.0f}%",
                "$ Amount":    f"${kf*portfolio_size:,.0f}" if portfolio_size > 0 else "—",
                "Grade":       grade,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(pcfg.get("description", ""))

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── APY Sparklines ───────────────────────────────────────────────────────────

st.markdown("### APY Trend — Last 14 Scans")

opps = model_data.get(profile, [])
if not opps or len(runs) < 3:
    st.info("Need at least 3 scans to show sparklines.")
else:
    top_pools = [(o.get("protocol", ""), o.get("asset_or_pool", "")) for o in opps[:3]]
    cols      = st.columns(len(top_pools))

    for col, (proto, pool) in zip(cols, top_pools):
        history_apy = []
        for run in runs[-14:]:
            run_opps = run.get("models", {}).get(profile, [])
            match = next(
                (o for o in run_opps if o.get("protocol") == proto and o.get("asset_or_pool") == pool),
                None,
            )
            if match:
                history_apy.append(match.get("estimated_apy", 0))

        with col:
            st.markdown(
                f"<div style='font-size:0.78rem; color:#64748b; text-align:center; margin-bottom:6px;'>"
                f"{proto}<br><span style='color:#94a3b8; font-weight:600;'>{pool}</span></div>",
                unsafe_allow_html=True,
            )
            if len(history_apy) >= 2:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=history_apy, mode="lines",
                    line=dict(color=color, width=2),
                    fill="tozeroy", fillcolor=f"rgba(59,130,246,0.07)",
                ))
                fig.update_layout(
                    plot_bgcolor="#0d1321", paper_bgcolor="#0d1321",
                    xaxis=dict(visible=False),
                    yaxis=dict(gridcolor="#1e293b", tickfont=dict(size=9, color="#475569")),
                    margin=dict(l=28, r=8, t=4, b=4),
                    height=100,
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.markdown(
                    "<div style='color:#334155; font-size:0.78rem; text-align:center; padding:20px 0;'>Building…</div>",
                    unsafe_allow_html=True,
                )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Options & Derivatives ────────────────────────────────────────────────────

st.markdown("### Options & Derivatives Strategies")

opts_data = latest.get("options", {}).get(profile, {})
analysis  = opts_data.get("analysis", {}) if opts_data else {}

if not analysis:
    st.markdown(
        "<div style='color:#334155; font-size:0.88rem; padding:16px 0;'>"
        "Options analysis will appear here after the first scan.</div>",
        unsafe_allow_html=True,
    )
else:
    for token, strats in analysis.items():
        with st.expander(f"{token} Strategies"):
            for strat_name, strat_data in strats.items():
                if strat_name == "options_chain":
                    continue
                if isinstance(strat_data, dict):
                    plain    = strat_data.get("plain_english", "")
                    exec_note = strat_data.get("execution", "")
                    apy_str  = ""
                    if "annualised_pct" in strat_data:
                        apy_str = f" — **{strat_data['annualised_pct']:.1f}% annualised**"
                    elif "max_profit_usd" in strat_data:
                        apy_str = f" — **{strat_data.get('risk_reward', 0):.1f}:1 risk/reward**"
                    st.markdown(f"**{strat_data.get('strategy', strat_name)}**{apy_str}")
                    st.markdown(plain)
                    if exec_note:
                        st.markdown(
                            f"<span style='color:#475569; font-size:0.83rem;'>How: {exec_note}</span>",
                            unsafe_allow_html=True,
                        )
                    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

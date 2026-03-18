"""
Opportunities — Full opportunity tables, starter portfolios, sparklines, options strategies.
"""

import sys
import html as _html
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime

from ui.common import (
    page_setup, render_sidebar, load_latest, load_history_runs,
    render_opportunity_card, render_section_header, risk_score_to_grade,
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
    "<div style='color:#475569; font-size:0.87rem; margin-bottom:24px;'>"
    "Starter portfolios · APY trends · options strategies</div>",
    unsafe_allow_html=True,
)


# ─── Starter Portfolios ───────────────────────────────────────────────────────

render_section_header("Starter Portfolios", "Pre-built Kelly-sized allocations for each risk profile")

for p in RISK_PROFILE_NAMES:
    opps = model_data.get(p, [])
    pcfg = RISK_PROFILES[p]
    pcol = pcfg["color"]
    w    = weight if p == profile else 1.0
    if not opps:
        continue
    with st.expander(f"{pcfg['label']} — {pcfg['target_apy_low']:.0f}–{pcfg['target_apy_high']:.0f}% target"):
        view = st.radio("View as", ["Cards", "Table"], key=f"view_{p}", horizontal=True)
        if view == "Cards":
            for i, opp in enumerate(opps[:6]):
                render_opportunity_card(opp, i, pcol, portfolio_size, w)
        else:
            rows = []
            for opp in opps[:8]:
                kf        = opp.get("kelly_fraction", 0)
                grade, _  = risk_score_to_grade(opp.get("risk_score", 5))
                rows.append({
                    "Protocol":     opp.get("protocol", "—"),
                    "Pool / Asset": opp.get("asset_or_pool", "—"),
                    "Est. APY":     f"{opp.get('estimated_apy', 0):.1f}%",
                    "Range":        f"{opp.get('apy_low', 0):.0f}–{opp.get('apy_high', 0):.0f}%",
                    "Grade":        grade,
                    "Alloc %":      f"{kf*100:.0f}%",
                    "$ Amount":     f"${kf*portfolio_size:,.0f}" if portfolio_size > 0 else "—",
                    "IL Risk":      opp.get("il_risk", "—").upper(),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(pcfg.get("description", ""))

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── APY Sparklines ───────────────────────────────────────────────────────────

render_section_header("APY Trend", "Top 3 pools — last 14 scans")

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
                f"{_html.escape(str(proto))}<br><span style='color:#94a3b8; font-weight:600;'>{_html.escape(str(pool))}</span></div>",
                unsafe_allow_html=True,
            )
            if len(history_apy) >= 2:
                latest_apy = history_apy[-1]
                prev_apy   = history_apy[-2]
                trend_color = "#22c55e" if latest_apy >= prev_apy else "#ef4444"
                fill_color  = "rgba(34,197,94,0.08)" if latest_apy >= prev_apy else "rgba(239,68,68,0.08)"
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=history_apy, mode="lines",
                    line=dict(color=trend_color, width=2),
                    fill="tozeroy", fillcolor=fill_color,
                ))
                fig.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(visible=False),
                    yaxis=dict(
                        gridcolor="rgba(148,163,184,0.15)",
                        tickfont=dict(size=9, color="#475569"),
                        ticksuffix="%",
                    ),
                    margin=dict(l=32, r=6, t=4, b=4),
                    height=90,
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
                st.markdown(
                    f"<div style='text-align:center; font-size:0.75rem; color:{trend_color}; margin-top:-8px;'>"
                    f"{'▲' if latest_apy >= prev_apy else '▼'} {latest_apy:.1f}%</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    "<div class='skeleton' style='height:90px; margin:4px 0;'></div>"
                    "<div style='color:#334155; font-size:0.72rem; text-align:center; margin-top:6px;'>Building history…</div>",
                    unsafe_allow_html=True,
                )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Options & Derivatives ────────────────────────────────────────────────────

render_section_header("Options & Derivatives Strategies")

opts_data = (latest.get("options") or {}).get(profile, {})
analysis  = (opts_data or {}).get("analysis", {})

if not analysis:
    st.markdown(
        "<div style='color:#334155; font-size:0.88rem; padding:16px 0;'>"
        "Options analysis will appear here after the first scan.</div>",
        unsafe_allow_html=True,
    )
else:
    for token, strats in analysis.items():
        if not isinstance(strats, dict):
            continue
        with st.expander(f"{token} Strategies"):
            for strat_name, strat_data in strats.items():
                if strat_name == "options_chain":
                    if isinstance(strat_data, list) and strat_data:
                        st.markdown("**Options Chain** — Full strike grid (30-day expiry)")
                        chain_rows = [{
                            "Type":       op.get("option_type", "").upper(),
                            "Strike":     f"${op.get('strike', 0):.4f}",
                            "Moneyness":  op.get("moneyness", ""),
                            "Premium":    f"${op.get('price', 0):.6f}",
                            "Delta":      f"{op.get('delta', 0):.3f}",
                            "θ/day":      f"${op.get('theta', 0):.6f}",
                            "Vega":       f"{op.get('vega', 0):.6f}",
                        } for op in strat_data if isinstance(op, dict)]
                        st.dataframe(pd.DataFrame(chain_rows), use_container_width=True, hide_index=True)
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

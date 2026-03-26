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
import plotly.express as px

from ui.common import (
    page_setup, render_sidebar, load_latest, load_history_runs,
    render_opportunity_card, render_section_header, risk_score_to_grade,
)
from config import RISK_PROFILES, RISK_PROFILE_NAMES
from scanners.defillama import (
    fetch_yields_pools, fetch_protocol_risk_score, fetch_tvl_change_alert,
    fetch_governance_alerts, fetch_bridge_flows,
)

page_setup("Opportunities · Flare DeFi")

ctx            = render_sidebar()
profile        = ctx["profile"]
profile_cfg    = ctx["profile_cfg"]
color          = ctx["color"]
weight         = ctx["weight"]
portfolio_size = ctx["portfolio_size"]
demo_mode      = ctx.get("demo_mode", False)

# #82 Beginner/Pro toggle
_pro_mode = st.toggle(
    "Pro Mode",
    value=st.session_state.get("defi_pro_mode", True),
    key="defi_pro_mode",
    help="Pro: shows Real Yield Ratio, DeFi Sharpe, risk scoring details. Beginner: simplified card view.",
)

latest     = load_latest()
runs       = load_history_runs()
model_data = latest.get("models") or {}

st.markdown("# Opportunities")
st.markdown(
    "<div style='color:#475569; font-size:0.87rem; margin-bottom:24px;'>"
    "Starter portfolios · APY trends · options strategies</div>",
    unsafe_allow_html=True,
)


# ─── Starter Portfolios ───────────────────────────────────────────────────────

render_section_header("Starter Portfolios", "Pre-built Kelly-sized allocations for each risk profile")

for p in RISK_PROFILE_NAMES:
    opps = model_data.get(p) or []
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
                    "IL Risk":      (opp.get("il_risk") or "—").upper(),
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(pcfg.get("description", ""))

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── APY Sparklines ───────────────────────────────────────────────────────────

render_section_header("APY Trend", "Top 3 pools — last 14 scans")

opps = model_data.get(profile) or []
if not opps or len(runs) < 3:
    st.info("Need at least 3 scans to show sparklines.")
else:
    top_pools = [(o.get("protocol", ""), o.get("asset_or_pool", "")) for o in opps[:3]]
    cols      = st.columns(len(top_pools))

    for col, (proto, pool) in zip(cols, top_pools):
        history_apy = []
        for run in runs[-14:]:
            run_opps = (run.get("models") or {}).get(profile) or []
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


# ─── Protocol Comparison Radar Chart (Phase 10) ───────────────────────────────

render_section_header(
    "Protocol Comparison",
    "Multi-dimensional radar chart — APY · TVL · Confidence · Risk Grade · IL Risk",
)

# Gather all opportunities across all profiles and deduplicate by protocol
_all_opps: dict = {}
for _p in RISK_PROFILE_NAMES:
    for _opp in (model_data.get(_p) or []):
        _key = _opp.get("protocol", "")
        if _key and _key not in _all_opps:
            _all_opps[_key] = _opp

if len(_all_opps) < 2:
    st.info("Run a scan first to populate protocol comparison data.")
else:
    # Selectable protocols for comparison
    _proto_list = list(_all_opps.keys())
    _default_sel = _proto_list[:min(5, len(_proto_list))]
    _sel_protos = st.multiselect(
        "Select protocols to compare (max 6)",
        options=_proto_list,
        default=_default_sel,
        max_selections=6,
        key="proto_compare_sel",
    )

    if _sel_protos:
        # Normalise each dimension 0–10 (higher = better)
        def _norm(val: float, lo: float, hi: float) -> float:
            if hi == lo:
                return 5.0
            return round(max(0.0, min(10.0, (val - lo) / (hi - lo) * 10)), 2)

        _apy_vals  = [_all_opps[p].get("estimated_apy",  0) for p in _sel_protos]
        _conf_vals = [_all_opps[p].get("confidence",     50) for p in _sel_protos]
        _rs_vals   = [_all_opps[p].get("risk_score",     5) for p in _sel_protos]
        _tvl_vals  = [
            (lambda t: float(t) if t is not None else 0)(_all_opps[p].get("tvl_usd"))
            for p in _sel_protos
        ]
        _il_map    = {"none": 10, "low": 7, "medium": 4, "high": 1, "": 5}

        _apy_lo, _apy_hi   = min(_apy_vals),  max(_apy_vals)
        _conf_lo, _conf_hi = min(_conf_vals), max(_conf_vals)
        _tvl_lo,  _tvl_hi  = min(_tvl_vals),  max(_tvl_vals)

        _DIMENSIONS = ["APY", "Confidence", "Safety\n(10 - Risk)", "TVL Scale", "IL Safety"]
        _COLORS = [
            "#6366f1", "#22c55e", "#f59e0b", "#ec4899",
            "#14b8a6", "#8b5cf6",
        ]

        fig_radar = go.Figure()
        for _i, _proto in enumerate(_sel_protos):
            _opp = _all_opps[_proto]
            _il_score = _il_map.get((_opp.get("il_risk") or "none").lower(), 5)
            _scores = [
                _norm(_opp.get("estimated_apy",  0), _apy_lo,  _apy_hi),
                _norm(_opp.get("confidence",    50), _conf_lo, _conf_hi),
                _norm(10 - _opp.get("risk_score", 5), 0, 10),  # invert: lower risk → higher score
                _norm(
                    (lambda t: float(t) if t is not None else 0)(_opp.get("tvl_usd")),
                    _tvl_lo, _tvl_hi,
                ),
                _il_score,
            ]
            _col = _COLORS[_i % len(_COLORS)]
            fig_radar.add_trace(go.Scatterpolar(
                r=_scores + [_scores[0]],
                theta=_DIMENSIONS + [_DIMENSIONS[0]],
                fill="toself",
                fillcolor="rgba({},{},{},0.13)".format(int(_col[1:3],16),int(_col[3:5],16),int(_col[5:7],16)),
                line=dict(color=_col, width=2),
                name=_proto,
                hovertemplate=(
                    f"<b>{_proto}</b><br>"
                    f"APY: {_opp.get('estimated_apy', 0):.1f}%<br>"
                    f"Confidence: {_opp.get('confidence', 50):.0f}<br>"
                    f"Risk Score: {_opp.get('risk_score', 5):.1f}<br>"
                    "<extra></extra>"
                ),
            ))

        fig_radar.update_layout(
            polar=dict(
                radialaxis=dict(
                    visible=True, range=[0, 10],
                    gridcolor="rgba(148,163,184,0.15)",
                    tickfont=dict(size=9, color="#475569"),
                ),
                angularaxis=dict(
                    gridcolor="rgba(148,163,184,0.12)",
                    tickfont=dict(size=10, color="#94a3b8"),
                ),
                bgcolor="rgba(0,0,0,0)",
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            legend=dict(
                font=dict(size=11, color="#94a3b8"),
                bgcolor="rgba(0,0,0,0)",
                bordercolor="rgba(148,163,184,0.1)",
            ),
            margin=dict(l=40, r=40, t=20, b=20),
            height=380,
        )
        st.plotly_chart(fig_radar, use_container_width=True, config={"displayModeBar": False})

        # Summary comparison table
        _cmp_rows = []
        for _proto in _sel_protos:
            _opp = _all_opps[_proto]
            _grade, _ = risk_score_to_grade(_opp.get("risk_score", 5))
            _cmp_rows.append({
                "Protocol":    _proto,
                "Best Pool":   _opp.get("asset_or_pool", "—"),
                "Est. APY":    f"{_opp.get('estimated_apy', 0):.1f}%",
                "APY Range":   f"{_opp.get('apy_low', 0):.0f}–{_opp.get('apy_high', 0):.0f}%",
                "Confidence":  f"{_opp.get('confidence', 50):.0f}",
                "Risk Grade":  _grade,
                "IL Risk":     (_opp.get("il_risk") or "—").title(),
                "TVL":         (
                    f"${float(_opp.get('tvl_usd', 0))/1e6:.1f}M"
                    if _opp.get("tvl_usd") and float(_opp.get("tvl_usd", 0)) > 0
                    else "—"
                ),
            })
        st.dataframe(pd.DataFrame(_cmp_rows), use_container_width=True, hide_index=True)
        st.caption("Radar axes are normalised 0–10 within the selected set. IL Safety: None=10, Low=7, Medium=4, High=1.")

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


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 8 SECTIONS
# ═══════════════════════════════════════════════════════════════════════════

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Multi-Chain Pool Opportunities (#68 #70-78) ────────────────────────────

render_section_header(
    "Multi-Chain Pools",
    "Pendle · EigenLayer · Ethena · Aerodrome · Morpho · Kamino — via DeFiLlama yields API",
)

with st.spinner("Loading multi-chain pools…"):
    if demo_mode:
        _mc_pools = [
            {"project": "pendle", "chain": "Ethereum", "symbol": "PT-USDe 29Jun2025",
             "apy": 12.4, "apyBase": 8.2, "apyReward": 4.2, "tvlUsd": 420_000_000, "audits": 3, "ilRisk": "no"},
            {"project": "ether.fi", "chain": "Ethereum", "symbol": "eETH",
             "apy": 6.1, "apyBase": 6.1, "apyReward": 0.0, "tvlUsd": 6_200_000_000, "audits": 5, "ilRisk": "no"},
            {"project": "morpho", "chain": "Ethereum", "symbol": "USDC vault",
             "apy": 9.3, "apyBase": 9.3, "apyReward": 0.0, "tvlUsd": 1_800_000_000, "audits": 4, "ilRisk": "no"},
            {"project": "ethena", "chain": "Ethereum", "symbol": "sUSDe",
             "apy": 27.5, "apyBase": 14.0, "apyReward": 13.5, "tvlUsd": 3_900_000_000, "audits": 3, "ilRisk": "no"},
            {"project": "aerodrome-finance", "chain": "Base", "symbol": "USDC/WETH",
             "apy": 38.7, "apyBase": 12.0, "apyReward": 26.7, "tvlUsd": 580_000_000, "audits": 2, "ilRisk": "yes"},
        ]
    else:
        _mc_pools = fetch_yields_pools(min_tvl_usd=5_000_000, max_results=20)

if _mc_pools:
    # Display as table with pro/beginner columns
    _mc_rows = []
    for p in _mc_pools:
        _fee   = float(p.get("apyBase") or 0)
        _rew   = float(p.get("apyReward") or 0)
        _total = float(p.get("apy") or 0)
        _ry    = round(_fee / _total * 100) if _total > 0 else 0
        _row = {
            "Protocol":    p.get("project", "—").replace("-", " ").title(),
            "Chain":       p.get("chain", "—"),
            "Pool":        p.get("symbol", "—"),
            "APY %":       f"{_total:.1f}%",
            "TVL":         f"${float(p.get('tvlUsd', 0))/1e6:.0f}M" if p.get("tvlUsd", 0) >= 1e6 else f"${p.get('tvlUsd', 0):,.0f}",
        }
        if _pro_mode:
            _row["Base APY"] = f"{_fee:.1f}%"
            _row["Reward APY"] = f"{_rew:.1f}%"
            _row["Real Yield %"] = f"{_ry}%"
            _row["Audits"] = str(p.get("audits", "—"))
            _row["IL Risk"] = ("Yes" if p.get("ilRisk", "no") != "no" else "No")
        _mc_rows.append(_row)
    st.dataframe(pd.DataFrame(_mc_rows), use_container_width=True, hide_index=True)
else:
    st.info("Multi-chain pool data loading... Run a scan or check API connectivity.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Yield Curve Visualization (#86) ───────────────────────────────────────

if _pro_mode:
    render_section_header(
        "Yield Curve",
        "APY vs Risk Score scatter — higher-left = better risk-adjusted yield (efficient frontier)",
    )
    _all_opps_yc = []
    for _p in RISK_PROFILE_NAMES:
        for _opp in (model_data.get(_p) or []):
            if _opp not in _all_opps_yc:
                _all_opps_yc.append(_opp)
    if _all_opps_yc:
        _yc_df = pd.DataFrame([{
            "Protocol":   o.get("protocol", "—"),
            "Pool":       o.get("asset_or_pool", "—"),
            "APY":        float(o.get("estimated_apy", 0)),
            "Risk Score": float(o.get("risk_score", 5)),
            "TVL ($M)":   float(o.get("tvl_usd", 0) or 0) / 1e6,
            "Confidence": float(o.get("confidence", 50)),
        } for o in _all_opps_yc])
        _fig_yc = px.scatter(
            _yc_df, x="Risk Score", y="APY", size="TVL ($M)", color="Protocol",
            hover_data={"Pool": True, "Confidence": True, "TVL ($M)": True},
            labels={"Risk Score": "Risk Score (0=safest)", "APY": "Est. APY (%)"},
            title="",
        )
        _fig_yc.add_hline(y=5.0, line_dash="dash", line_color="#475569",
                          annotation_text="Risk-free (5%)")
        _fig_yc.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.8)",
            font_color="#94a3b8",
            xaxis=dict(gridcolor="rgba(148,163,184,0.1)", range=[0, 11]),
            yaxis=dict(gridcolor="rgba(148,163,184,0.1)", ticksuffix="%"),
            height=380, margin=dict(l=40, r=20, t=20, b=40),
            legend=dict(bgcolor="rgba(0,0,0,0)", font_size=10),
        )
        st.plotly_chart(_fig_yc, use_container_width=True, config={"displayModeBar": False})
    else:
        st.info("Run a scan to populate yield curve data.")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── TVL Change Alerts (#79) ───────────────────────────────────────────────

render_section_header(
    "TVL Change Alerts",
    "Protocols with significant 24h TVL changes — >5% drop may indicate exploit or capital migration",
)

_alert_slugs = ["kinetic-finance", "clearpool-lending", "morpho", "aave-v3", "eigenlayer"]
_tvl_alerts  = []
for _slug in _alert_slugs:
    try:
        _alert = fetch_tvl_change_alert(_slug, threshold_pct=5.0)
        if _alert.get("current_tvl", 0) > 0:
            _tvl_alerts.append(_alert)
    except Exception:
        pass

if _tvl_alerts:
    for _al in _tvl_alerts:
        _chg   = _al.get("change_pct", 0)
        _sev   = _al.get("severity", "normal")
        _col   = "#EF4444" if _sev == "critical" else "#F59E0B" if _sev == "warning" else "#34D399"
        _icon  = "🚨" if _sev == "critical" else "⚠️" if _sev == "warning" else "✅"
        _tvl_m = round(_al.get("current_tvl", 0) / 1e6, 1)
        st.markdown(
            f"<div style='background:rgba(0,0,0,0.2);border-left:3px solid {_col};"
            f"border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:0.85rem'>"
            f"{_icon} <b>{_al['slug']}</b> · TVL ${_tvl_m}M · "
            f"<span style='color:{_col}'>{_chg:+.1f}% 24h</span></div>",
            unsafe_allow_html=True,
        )
else:
    st.info("No significant TVL alerts at this time.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Governance Alerts (#74) ────────────────────────────────────────────────

render_section_header(
    "Governance Alerts",
    "Active Snapshot votes that may impact yield parameters — sourced from Snapshot GraphQL",
)

with st.spinner("Checking governance proposals…"):
    _proposals = [] if demo_mode else fetch_governance_alerts()

if demo_mode:
    _proposals = [
        {"title": "Adjust USDC lending rate parameters", "space": "aave.eth",
         "votes": 1842, "end_date": "2026-04-01", "apy_impact": True},
        {"title": "Enable new reward token for LPs", "space": "aerodrome.eth",
         "votes": 503, "end_date": "2026-03-30", "apy_impact": True},
    ]

if _proposals:
    for _prop in _proposals:
        _imp_badge = (" <span style='background:#1c1200;color:#FBBF24;font-size:0.68rem;"
                     "padding:1px 6px;border-radius:4px;border:1px solid #fbbf2444'>⚡ APY Impact</span>"
                     if _prop.get("apy_impact") else "")
        st.markdown(
            f"<div style='background:rgba(0,0,0,0.15);border:1px solid rgba(255,255,255,0.05);"
            f"border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:0.85rem'>"
            f"<b>{_html.escape(_prop['title'])}</b>{_imp_badge}<br>"
            f"<span style='color:#64748b;font-size:0.75rem'>{_prop['space']} · {_prop['votes']} votes · ends {_prop['end_date']}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
else:
    st.info("No active governance proposals at this time.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Bridge Flow Indicator (#85) ────────────────────────────────────────────

if _pro_mode:
    render_section_header(
        "Bridge Flow Monitor",
        "7-day TVL change per chain as capital flow proxy — INFLOW = capital entering, OUTFLOW = leaving",
    )

    with st.spinner("Fetching bridge flow data…"):
        _flows = [] if demo_mode else fetch_bridge_flows()

    if demo_mode:
        _flows = [
            {"chain": "Base", "tvl_usd": 7_400_000_000, "change_7d_pct": 18.2, "flow_signal": "INFLOW"},
            {"chain": "Ethereum", "tvl_usd": 50_000_000_000, "change_7d_pct": 2.1, "flow_signal": "STABLE"},
            {"chain": "Solana", "tvl_usd": 8_200_000_000, "change_7d_pct": -6.3, "flow_signal": "OUTFLOW"},
            {"chain": "Flare", "tvl_usd": 85_000_000, "change_7d_pct": 3.1, "flow_signal": "STABLE"},
        ]

    if _flows:
        _fl_cols = st.columns(min(len(_flows), 4))
        for _fi, _fl in enumerate(_flows[:4]):
            _fsig   = _fl["flow_signal"]
            _fcol   = "#34D399" if _fsig == "INFLOW" else "#EF4444" if _fsig == "OUTFLOW" else "#9CA3AF"
            _ficon  = "↑" if _fsig == "INFLOW" else "↓" if _fsig == "OUTFLOW" else "→"
            _ftvl_m = _fl["tvl_usd"] / 1e9 if _fl["tvl_usd"] >= 1e9 else _fl["tvl_usd"] / 1e6
            _funit  = "B" if _fl["tvl_usd"] >= 1e9 else "M"
            with _fl_cols[_fi % 4]:
                st.markdown(
                    f"<div style='background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.06);"
                    f"border-top:2px solid {_fcol};border-radius:8px;padding:10px 12px;text-align:center'>"
                    f"<div style='font-size:0.72rem;color:#64748b;text-transform:uppercase'>{_fl['chain']}</div>"
                    f"<div style='font-size:1.4rem;font-weight:700;color:{_fcol}'>{_ficon} {_fl['change_7d_pct']:+.1f}%</div>"
                    f"<div style='font-size:0.72rem;color:#475569'>TVL ${_ftvl_m:.1f}{_funit}</div>"
                    f"<div style='font-size:0.70rem;color:{_fcol};margin-top:2px'>{_fsig}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
    else:
        st.info("Bridge flow data unavailable.")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

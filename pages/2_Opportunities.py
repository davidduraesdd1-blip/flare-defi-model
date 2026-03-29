"""
Opportunities — Full opportunity tables, starter portfolios, sparklines, options strategies.
"""

import sys
import html as _html
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    fetch_llama_yield_pools,            # #68 global yield pools
    governance_fetch_failed,
)
from scanners.defi_protocols import (
    fetch_ethena_yield,                 # #76
    fetch_aerodrome_pools,              # #77
    fetch_morpho_vaults,                # #77
    fetch_eigenlayer_lrt_yields,        # #71
    fetch_kamino_yields,                # #78
    fetch_meteora_yields,               # #78
    fetch_token_unlock_alerts,          # #84
    fetch_erc4626_yield_data,           # #103
)
from models.risk_models import (
    compute_pool_sharpe,                # #72
    compute_real_yield_ratio,           # #73
    compute_protocol_risk_score,        # #80
)


# ── OPT-39: module-level @st.cache_data wrappers ──────────────────────────────

@st.cache_data(ttl=900)
def _cached_yields_pools(**kwargs):
    """Cached wrapper for fetch_yields_pools(). TTL=15 min."""
    return fetch_yields_pools(**kwargs)


@st.cache_data(ttl=600)
def _cached_eigenlayer_lrt_yields():
    """Cached wrapper for fetch_eigenlayer_lrt_yields(). TTL=10 min."""
    return fetch_eigenlayer_lrt_yields()


@st.cache_data(ttl=600)
def _cached_kamino_yields():
    """Cached wrapper for fetch_kamino_yields(). TTL=10 min."""
    return fetch_kamino_yields()


@st.cache_data(ttl=600)
def _cached_meteora_yields():
    """Cached wrapper for fetch_meteora_yields(). TTL=10 min."""
    return fetch_meteora_yields()


@st.cache_data(ttl=300)
def _cached_erc4626_yield_data():
    """Cached wrapper for fetch_erc4626_yield_data(). TTL=5 min."""
    return fetch_erc4626_yield_data()


@st.cache_data(ttl=600)
def _cached_ethena_yield():
    """Cached wrapper for fetch_ethena_yield(). TTL=10 min."""
    return fetch_ethena_yield()


@st.cache_data(ttl=600)
def _cached_aerodrome_pools():
    """Cached wrapper for fetch_aerodrome_pools(). TTL=10 min."""
    return fetch_aerodrome_pools()


@st.cache_data(ttl=600)
def _cached_morpho_vaults():
    """Cached wrapper for fetch_morpho_vaults(). TTL=10 min."""
    return fetch_morpho_vaults()


@st.cache_data(ttl=3600)
def _cached_token_unlock_alerts(within_days: int = 30):
    """Cached wrapper for fetch_token_unlock_alerts(). TTL=1 hour (date-based data)."""
    return fetch_token_unlock_alerts(within_days=within_days)


@st.cache_data(ttl=3600)
def _cached_governance_alerts_opp():
    """Cached wrapper for fetch_governance_alerts(). TTL=1 hour."""
    return fetch_governance_alerts()

page_setup("Opportunities · Flare DeFi")

ctx            = render_sidebar()
profile        = ctx["profile"]
profile_cfg    = ctx["profile_cfg"]
color          = ctx["color"]
weight         = ctx["weight"]
portfolio_size = ctx["portfolio_size"]
demo_mode      = ctx.get("demo_mode", False)

# #82 Beginner/Pro toggle — reads from sidebar session state (set in ui/common.py render_sidebar)
# The sidebar toggle is the canonical source; this reads it for page-level conditionals.
_pro_mode = st.session_state.get("defi_pro_mode", False)

@st.cache_data(ttl=600)
def _load_opp_data_cached(profile: str) -> dict:
    """Load and return model_data for all profiles, keyed by profile name.
    Cached for 10 minutes to avoid duplicate queries for radar chart and table.
    """
    _latest = load_latest()
    return _latest.get("models") or {}


model_data = _load_opp_data_cached(profile)
latest     = load_latest()   # uses _load_history_file cache — no duplicate disk read
runs       = load_history_runs()

st.title("🎯 Opportunities")
st.caption("Real-time yield opportunities across 10,000+ DeFi pools • Auto-refreshed every 15 minutes")
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
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
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
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
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
        st.plotly_chart(fig_radar, width="stretch", config={"displayModeBar": False})

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
        st.dataframe(pd.DataFrame(_cmp_rows), width="stretch", hide_index=True)
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
                        st.dataframe(pd.DataFrame(chain_rows), width="stretch", hide_index=True)
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

with st.spinner("Loading yield data from DeFiLlama..."):
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
        _mc_pools = _cached_yields_pools(min_tvl_usd=5_000_000, max_results=20)

if _mc_pools:
    # Beginner metric tooltips (#59)
    if not _pro_mode:
        _tt_col1, _tt_col2, _tt_col3 = st.columns(3)
        with _tt_col1:
            st.metric(
                "APY",
                "?",
                help="Annual Percentage Yield — the yearly return you'd earn. Includes both trading fees and token rewards. Higher isn't always better — check the risk score.",
            )
        with _tt_col2:
            st.metric(
                "TVL",
                "?",
                help="Total Value Locked — how much money is deposited in this protocol. Higher TVL generally means more trust and liquidity.",
            )
        with _tt_col3:
            st.metric(
                "IL Risk",
                "?",
                help="Impermanent Loss risk for liquidity providers. Stable pairs (USDC/USDT) have near-zero IL. Volatile pairs (ETH/USDC) can lose 5-20% vs just holding.",
            )
    # Display as table with pro/beginner columns
    # Sort by Sharpe ratio by default (#72)
    _mc_pools_with_sharpe = []
    for _mcp in _mc_pools:
        _mc_apy   = float(_mcp.get("apy") or 0)
        _mc_apy7d = float(_mcp.get("apy7d") or _mcp.get("apy") or 0)
        _mc_sh    = compute_pool_sharpe(_mc_apy, _mc_apy7d)
        _mc_pools_with_sharpe.append({**_mcp, "_sharpe": _mc_sh["sharpe"], "_rank": _mc_sh["risk_adjusted_rank"]})
    _mc_pools_with_sharpe.sort(key=lambda x: x["_sharpe"], reverse=True)

    _mc_rows = []
    for p in _mc_pools_with_sharpe:
        _fee   = float(p.get("apyBase") or 0)
        _rew   = float(p.get("apyReward") or 0)
        _total = float(p.get("apy") or 0)
        _ry    = min(100, round(_fee / _total * 100)) if _total > 0 else 0
        # Real Yield classification (#73)
        _real_info = compute_real_yield_ratio(total_apy=_total, emission_apy=_rew)
        # Protocol risk score (#80)
        _mc_rs = compute_protocol_risk_score(
            protocol_name=str(p.get("project", "")),
            tvl_usd=float(p.get("tvlUsd") or 0),
            chain=str(p.get("chain", "ethereum")),
        )
        _rs_val   = _mc_rs["risk_score"]
        _rs_label = _mc_rs["risk_label"]
        _rs_color = (
            "#22c55e" if _rs_val < 25 else
            "#f59e0b" if _rs_val < 50 else
            "#f97316" if _rs_val < 75 else
            "#ef4444"
        )
        _row = {
            "Protocol":    p.get("project", "—").replace("-", " ").title(),
            "Chain":       p.get("chain", "—"),
            "Pool":        p.get("symbol", "—"),
            "APY %":       f"{_total:.1f}%",
            "TVL":         f"${float(p.get('tvlUsd', 0))/1e6:.0f}M" if p.get("tvlUsd", 0) >= 1e6 else f"${p.get('tvlUsd', 0):,.0f}",
            "Risk Score":  f"{_rs_val} ({_rs_label})",
        }
        if _pro_mode:
            _row["Base APY"]   = f"{_fee:.1f}%"
            _row["Reward APY"] = f"{_rew:.1f}%"
            _row["Real Yield"] = f"{_ry}% · {_real_info['classification'].replace('_', ' ').title()}"
            _row["Sharpe"]     = f"{p['_sharpe']:.2f} ({p['_rank'].capitalize()})"
            _row["Audits"]     = str(p.get("audits", "—"))
            _row["IL Risk"]    = ("Yes" if p.get("ilRisk", "no") != "no" else "No")
        _mc_rows.append(_row)
    # Paginate when more than 25 rows (upgrade #33)
    if len(_mc_rows) > 25:
        _rows_per_page = st.select_slider(
            "Rows per page", options=[10, 25, 50], value=25, key="opp_rows_pp"
        )
        _page = st.number_input(
            "Page",
            min_value=1,
            max_value=max(1, -(-len(_mc_rows) // _rows_per_page)),
            value=1,
            key="opp_page",
        )
        _start     = (_page - 1) * _rows_per_page
        _paged_rows = _mc_rows[_start: _start + _rows_per_page]
        st.dataframe(pd.DataFrame(_paged_rows), width="stretch", hide_index=True)
    else:
        st.dataframe(pd.DataFrame(_mc_rows), width="stretch", hide_index=True)
else:
    st.info("No opportunities matching your filters. Try lowering the minimum TVL filter or switching chains.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Pendle Finance PT/YT Yield Mechanics (#70) ────────────────────────────

render_section_header(
    "Pendle Finance — PT vs YT Mechanics",
    "PT = fixed rate locked at purchase · YT = leveraged variable rate · choose your conviction",
)

from config import PROTOCOLS as _PROTOCOLS_CFG

_pendle_markets_raw = []
# Pull Pendle pools from DeFiLlama yields data (project = "pendle")
for _mcp in _mc_pools:
    if (str(_mcp.get("project") or "")).lower() == "pendle":
        _pendle_markets_raw.append(_mcp)

# Fallback: use Spectra markets (Flare-native PT equivalent) if no Pendle pools loaded
_spectra_cfg = _PROTOCOLS_CFG.get("spectra", {}).get("markets", {})
_spectra_markets = [
    {
        "symbol":    mkey,
        "maturity":  mdata.get("maturity", ""),
        "pt_apy":    mdata.get("fixed_apy", 0),
        "lp_apy":    mdata.get("lp_apy", 0),
        "asset":     mdata.get("asset", ""),
        "chain":     "Flare",
        "project":   "spectra-v2",
        "tvlUsd":    0,
        "_is_spectra": True,
    }
    for mkey, mdata in _spectra_cfg.items()
]

_pendle_display = _pendle_markets_raw or _spectra_markets

if _pendle_display:
    import math as _math
    from datetime import datetime as _dt_now, timezone as _tz_now

    _pendle_rows = []
    for _pm in _pendle_display:
        _sym        = str(_pm.get("symbol") or "")
        _apy        = float(_pm.get("apy") or _pm.get("pt_apy") or 0)
        _tvl        = float(_pm.get("tvlUsd") or _pm.get("tvl_usd") or 0)
        _chain      = str(_pm.get("chain") or "—")
        _protocol   = str(_pm.get("project") or "pendle").replace("-", " ").title()
        _maturity   = str(_pm.get("maturity") or "")
        _is_spectra = _pm.get("_is_spectra", False)

        # PT APY = fixed rate locked at purchase (the pool APY is PT APY for Pendle PT pools)
        _pt_apy = _apy

        # YT APY approximation: underlying_apy × (1 / pt_price - 1)
        # We approximate pt_price from APY: pt_price ≈ 1 / (1 + pt_apy/100) for 1-year equivalent
        # Use a simpler proxy: if pt_apy > 0, yt_apy ≈ pt_apy × leverage_factor
        # Leverage factor based on typical Pendle PT discount to par
        _pt_price_proxy = 1.0 / (1.0 + max(_pt_apy / 100.0, 0.01))
        _leverage       = max(0.0, 1.0 / max(_pt_price_proxy, 0.01) - 1.0)
        # underlying APY ≈ PT APY as base; YT APY = underlying × leverage
        _underlying_apy = float(_pm.get("apyBase") or _pt_apy)
        _yt_apy         = round(_underlying_apy * _leverage, 2) if _leverage > 0 else 0.0

        # Maturity countdown
        _days_left = None
        if _maturity:
            try:
                _mat_dt = _dt_now.strptime(_maturity[:10], "%Y-%m-%d").replace(tzinfo=_tz_now.utc)
                _days_left = max(0, (_mat_dt - _dt_now.now(_tz_now.utc)).days)
            except ValueError:
                pass

        # "Which is better?" recommendation
        _rec = ""
        if _yt_apy > 0 and _pt_apy > 0:
            if _underlying_apy > _pt_apy:
                _rec = "Buy YT — bet yields stay high or rise"
            else:
                _rec = "Buy PT — lock in the fixed rate"
        elif _pt_apy > 0:
            _rec = "Buy PT — lock in fixed APY"

        _row = {
            "Protocol":    _protocol,
            "Market":      _sym,
            "Chain":       _chain,
            "PT APY (Fixed)": f"{_pt_apy:.1f}%",
            "YT APY (Implied)": f"{_yt_apy:.1f}%" if _yt_apy > 0 else "N/A",
            "Maturity":    f"{_days_left}d" if _days_left is not None else (_maturity or "—"),
            "Recommendation": _rec,
        }
        if _tvl > 0:
            _row["TVL"] = (
                f"${_tvl/1e9:.2f}B" if _tvl >= 1e9
                else f"${_tvl/1e6:.0f}M" if _tvl >= 1e6
                else f"${_tvl/1e3:.0f}K"
            )
        _pendle_rows.append(_row)

    if _pendle_rows:
        st.dataframe(pd.DataFrame(_pendle_rows), width="stretch", hide_index=True)

        # PT vs YT explainer
        with st.expander("How PT vs YT works"):
            st.markdown("""
**Principal Token (PT)** — Fixed rate, locked at purchase.
- You buy PT at a discount and redeem at face value at maturity.
- Equivalent to a zero-coupon bond. Safe if you hold to maturity.
- Best when: you think yields will *fall* or you want predictable income.

**Yield Token (YT)** — Leveraged variable rate.
- You hold the right to collect all future yield on the underlying asset until maturity.
- Highly leveraged — YT price can go to zero if yields collapse.
- Best when: you think yields will *rise* or stay elevated.

**Formula (approximate)**:
- YT APY ≈ Underlying APY × (1 / PT_price − 1)
- A PT trading at $0.90 gives ~11% leverage on the yield stream.

**Quick rule**: If underlying APY > implied PT fixed rate → buy YT. Otherwise → buy PT.
            """)
    else:
        st.info("Pendle market data unavailable — run a scan or check connectivity.")
else:
    st.info("No Pendle/Spectra markets loaded. Run a scan to populate.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Yield Curve Visualization (#86) ───────────────────────────────────────

if _pro_mode:
    render_section_header(
        "Yield Curve",
        "APY vs Risk Score scatter — higher-left = better risk-adjusted yield · efficient frontier shown",
    )

    # Protocol-type base risk scores for the efficient frontier (#86)
    _TYPE_RISK_SCORES = {
        "Lending": 2, "Liquid Staking": 2, "Yield Vault": 3,
        "Yield Tokenization": 3, "DEX": 5, "DEX + Perps": 7,
        "Leveraged Yield": 9, "Perps (Cross-chain)": 8,
    }

    _all_opps_yc = []
    for _p in RISK_PROFILE_NAMES:
        for _opp in (model_data.get(_p) or []):
            if _opp not in _all_opps_yc:
                _all_opps_yc.append(_opp)

    # Also include multi-chain pools with inferred risk scores
    _mc_type_map = {
        "pendle": "Yield Tokenization", "morpho": "Lending", "morpho-blue": "Lending",
        "ether.fi": "Liquid Staking", "eigenlayer": "Liquid Staking",
        "ethena": "Yield Vault", "aerodrome-finance": "DEX",
        "blazeswap": "DEX", "kinetic-finance": "Lending",
    }
    for _mcp in (_mc_pools or []):
        _mc_proj  = (str(_mcp.get("project") or "")).lower()
        _mc_type  = _mc_type_map.get(_mc_proj, "DEX")
        _mc_rs    = _TYPE_RISK_SCORES.get(_mc_type, 5)
        _all_opps_yc.append({
            "protocol":      (_mcp.get("project") or "—").replace("-", " ").title(),
            "asset_or_pool": _mcp.get("symbol", "—"),
            "estimated_apy": float(_mcp.get("apy") or 0),
            "risk_score":    float(_mc_rs),
            "tvl_usd":       float(_mcp.get("tvlUsd") or 0),
            "confidence":    50,
            "_protocol_type": _mc_type,
        })

    if _all_opps_yc:
        _yc_rows = []
        for _o in _all_opps_yc:
            _yc_apy = float(_o.get("estimated_apy", 0))
            _yc_rs  = float(_o.get("risk_score", 5))
            _yc_tvl = max(0.1, float(_o.get("tvl_usd", 0) or 0) / 1e6)
            if _yc_apy <= 0:
                continue
            _yc_rows.append({
                "Protocol":    str(_o.get("protocol", "—")),
                "Pool":        str(_o.get("asset_or_pool", "—")),
                "APY":         _yc_apy,
                "Risk Score":  _yc_rs,
                "TVL ($M)":    _yc_tvl,
                "Confidence":  float(_o.get("confidence", 50)),
                "Type":        str(_o.get("_protocol_type", "DeFi")),
            })

        if _yc_rows:
            _yc_df = pd.DataFrame(_yc_rows)

            _fig_yc = px.scatter(
                _yc_df,
                x="Risk Score", y="APY",
                size="TVL ($M)", color="Protocol",
                hover_data={"Pool": True, "Confidence": True, "TVL ($M)": True, "Type": True},
                labels={"Risk Score": "Risk Score (0=safest)", "APY": "Est. APY (%)"},
                title="",
            )

            # Efficient frontier: for each integer risk level, max APY achievable (#86)
            _frontier_x, _frontier_y = [], []
            for _rs_level in range(0, 11):
                _pts_at_rs = [r["APY"] for r in _yc_rows
                              if abs(r["Risk Score"] - _rs_level) <= 1.0]
                if _pts_at_rs:
                    _frontier_x.append(_rs_level)
                    _frontier_y.append(max(_pts_at_rs))
            if len(_frontier_x) >= 2:
                _fig_yc.add_trace(go.Scatter(
                    x=_frontier_x, y=_frontier_y,
                    mode="lines",
                    name="Efficient Frontier",
                    line=dict(color="#f59e0b", width=2, dash="dot"),
                    hovertemplate="Risk %{x:.0f} · Max APY %{y:.1f}%<extra>Frontier</extra>",
                ))

            _fig_yc.add_hline(y=5.0, line_dash="dash", line_color="#475569",
                              annotation_text="Risk-free (5%)")
            _fig_yc.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.8)",
                font_color="#94a3b8",
                xaxis=dict(gridcolor="rgba(148,163,184,0.1)", range=[0, 11]),
                yaxis=dict(gridcolor="rgba(148,163,184,0.1)", ticksuffix="%"),
                height=400, margin=dict(l=40, r=20, t=20, b=40),
                legend=dict(bgcolor="rgba(0,0,0,0)", font_size=10),
            )
            st.plotly_chart(_fig_yc, width="stretch", config={"displayModeBar": False})
            st.caption(
                "Efficient frontier (dotted orange) = max APY at each risk level. "
                "Points above frontier = exceptionally good risk-adjusted yield. "
                "Size = TVL. Color = protocol."
            )
        else:
            st.info("Run a scan to populate yield curve data.")
    else:
        st.info("Run a scan to populate yield curve data.")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Restaking & LRT (#71) ────────────────────────────────────────────────────

render_section_header(
    "Restaking & LRT",
    "EigenLayer native restaking and Liquid Restaking Tokens — APY and TVL from DeFiLlama",
)

with st.spinner("Loading restaking data…"):
    if demo_mode:
        _lrt_data = {
            "eigenlayer_native": {"apy": 4.8, "tvl_usd": 12_500_000_000, "source": "demo"},
            "etherfi_weETH":     {"apy": 5.6, "tvl_usd":  6_200_000_000, "source": "demo"},
            "renzo_ezETH":       {"apy": 4.9, "tvl_usd":  1_800_000_000, "source": "demo"},
            "kelp_rsETH":        {"apy": 4.7, "tvl_usd":    950_000_000, "source": "demo"},
            "timestamp":         "2026-03-27T00:00:00Z",
        }
    else:
        try:
            _lrt_data = _cached_eigenlayer_lrt_yields()
        except Exception:
            _lrt_data = {}

_LRT_LABELS = {
    "eigenlayer_native": ("EigenLayer Native", "#8b5cf6"),
    "etherfi_weETH":     ("ether.fi weETH",    "#3b82f6"),
    "renzo_ezETH":       ("Renzo ezETH",        "#ec4899"),
    "kelp_rsETH":        ("Kelp rsETH",         "#14b8a6"),
}

_lrt_cards = []
for _key, (_label, _col) in _LRT_LABELS.items():
    _entry = _lrt_data.get(_key) or {}
    _apy   = float(_entry.get("apy", 0))
    _tvl   = float(_entry.get("tvl_usd", 0))
    _src   = _entry.get("source", "unavailable")
    if _apy > 0 or _tvl > 0:
        _lrt_cards.append({"key": _key, "label": _label, "color": _col,
                            "apy": _apy, "tvl": _tvl, "source": _src})

_lrt_cards.sort(key=lambda x: x["apy"], reverse=True)

if _lrt_cards:
    _lrt_ncols = min(len(_lrt_cards), 4)
    _lrt_cols  = st.columns(_lrt_ncols)
    for _lci, _lc in enumerate(_lrt_cards):
        with _lrt_cols[_lci % _lrt_ncols]:
            _tvl_str = (
                f"${_lc['tvl']/1e9:.2f}B" if _lc["tvl"] >= 1e9
                else f"${_lc['tvl']/1e6:.0f}M" if _lc["tvl"] >= 1e6
                else f"${_lc['tvl']:,.0f}"
            )
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.07);"
                f"border-left:3px solid {_lc['color']};border-radius:8px;padding:12px 14px;margin-bottom:8px'>"
                f"<div style='font-weight:700;font-size:0.92rem;color:#e2e8f0;margin-bottom:4px'>{_lc['label']}</div>"
                f"<div style='font-size:1.25rem;font-weight:700;color:#22c55e'>{_lc['apy']:.2f}% APY</div>"
                f"<div style='font-size:0.78rem;color:#64748b;margin-top:3px'>TVL: {_tvl_str}</div>"
                f"<div style='font-size:0.72rem;color:#f59e0b;margin-top:6px;border-top:1px solid rgba(255,255,255,0.05);padding-top:5px'>"
                f"⚠ Smart contract risk + slashing risk</div>"
                f"<div style='font-size:0.67rem;color:#334155;margin-top:2px'>Source: {_lc['source']}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
else:
    st.info("Restaking data unavailable. Check API connectivity.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Solana DeFi — Kamino + Meteora (#78) ─────────────────────────────────────

render_section_header(
    "Solana DeFi",
    "Kamino Finance lending vaults and Meteora DLMM pools — top yields on Solana via DeFiLlama",
)

with st.spinner("Loading Solana DeFi data…"):
    if demo_mode:
        _kamino = {
            "pools": [
                {"symbol": "USDC", "apy": 18.5, "tvl_usd": 42_000_000, "chain": "Solana", "project": "kamino-lending"},
                {"symbol": "SOL",  "apy": 12.3, "tvl_usd": 28_000_000, "chain": "Solana", "project": "kamino-lending"},
            ],
            "total_tvl": 70_000_000, "timestamp": "2026-03-27T00:00:00Z",
        }
        _meteora = {
            "pools": [
                {"symbol": "SOL-USDC", "apy": 142.0, "tvl_usd": 8_500_000, "chain": "Solana", "project": "meteora-dlmm"},
                {"symbol": "JUP-USDC", "apy": 87.4,  "tvl_usd": 3_200_000, "chain": "Solana", "project": "meteora-dlmm"},
            ],
            "total_tvl": 11_700_000, "timestamp": "2026-03-27T00:00:00Z",
        }
    else:
        try:
            _kamino  = _cached_kamino_yields()
        except Exception:
            _kamino  = {"pools": [], "total_tvl": 0.0, "timestamp": ""}
        try:
            _meteora = _cached_meteora_yields()
        except Exception:
            _meteora = {"pools": [], "total_tvl": 0.0, "timestamp": ""}

_sol_col1, _sol_col2 = st.columns(2)

with _sol_col1:
    st.markdown("**Kamino Finance (Solana)**")
    _kamino_pools = (_kamino or {}).get("pools") or []
    if _kamino_pools:
        _k_rows = []
        for _kp in _kamino_pools:
            _k_rows.append({
                "Symbol":   _kp.get("symbol", "—"),
                "Protocol": (_kp.get("project") or "").replace("-", " ").title(),
                "APY %":    f"{float(_kp.get('apy', 0)):.2f}%",
                "TVL":      (f"${float(_kp.get('tvl_usd', 0))/1e6:.1f}M"
                             if float(_kp.get('tvl_usd', 0)) >= 1e6
                             else f"${float(_kp.get('tvl_usd', 0)):,.0f}"),
                "Chain":    _kp.get("chain", "Solana"),
            })
        st.dataframe(pd.DataFrame(_k_rows), width="stretch", hide_index=True)
        _k_tvl = float((_kamino or {}).get("total_tvl") or 0)
        st.caption(f"Total Kamino TVL scanned: ${_k_tvl/1e6:.1f}M")
    else:
        st.info("Kamino data unavailable.")

with _sol_col2:
    st.markdown("**Meteora DLMM (Solana)**")
    _meteora_pools = (_meteora or {}).get("pools") or []
    if _meteora_pools:
        _m_rows = []
        for _mp in _meteora_pools:
            _m_rows.append({
                "Symbol":   _mp.get("symbol", "—"),
                "Protocol": (_mp.get("project") or "").replace("-", " ").title(),
                "APY %":    f"{float(_mp.get('apy', 0)):.2f}%",
                "TVL":      (f"${float(_mp.get('tvl_usd', 0))/1e6:.1f}M"
                             if float(_mp.get('tvl_usd', 0)) >= 1e6
                             else f"${float(_mp.get('tvl_usd', 0)):,.0f}"),
                "Chain":    _mp.get("chain", "Solana"),
            })
        st.dataframe(pd.DataFrame(_m_rows), width="stretch", hide_index=True)
        _m_tvl = float((_meteora or {}).get("total_tvl") or 0)
        st.caption(f"Total Meteora TVL scanned: ${_m_tvl/1e6:.1f}M · Outliers >10,000% APY excluded.")
    else:
        st.info("Meteora data unavailable.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── TVL Change Alerts (#79) ───────────────────────────────────────────────

render_section_header(
    "TVL Change Alerts",
    "Protocols with significant 24h TVL changes — >5% drop may indicate exploit or capital migration",
)

_alert_slugs = ["kinetic-finance", "clearpool-lending", "morpho", "aave-v3", "eigenlayer"]
_tvl_alerts  = []

# OPT-38: Fetch all 5 TVL alerts in parallel
def _fetch_tvl_alert(slug: str) -> dict | None:
    try:
        alert = fetch_tvl_change_alert(slug, threshold_pct=5.0)
        return alert if alert.get("current_tvl", 0) > 0 else None
    except Exception:
        return None

with ThreadPoolExecutor(max_workers=min(5, len(_alert_slugs))) as _tvl_ex:
    _tvl_futures = {_tvl_ex.submit(_fetch_tvl_alert, s): s for s in _alert_slugs}
    for _tvl_fut in as_completed(_tvl_futures):
        try:
            _result = _tvl_fut.result(timeout=15)
            if _result is not None:
                _tvl_alerts.append(_result)
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
    _proposals = [] if demo_mode else _cached_governance_alerts_opp()

if demo_mode:
    _proposals = [
        {"title": "Adjust USDC borrow rate parameters", "space": "aave.eth",
         "protocol": "aave.eth", "votes": 1842, "end_date": "2026-04-01", "apy_impact": True,
         "url": "https://snapshot.org/#/aave.eth"},
        {"title": "Enable new reward emission for LPs", "space": "aerodrome.eth",
         "protocol": "aerodrome.eth", "votes": 503, "end_date": "2026-03-30", "apy_impact": True,
         "url": "https://snapshot.org/#/aerodrome.eth"},
        {"title": "Adjust fee tier for USDC/USDT pool", "space": "uniswap",
         "protocol": "uniswap", "votes": 3210, "end_date": "2026-04-03", "apy_impact": True,
         "url": "https://snapshot.org/#/uniswap"},
    ]

if _proposals:
    # Show APY-impacting proposals first
    _apy_props = [p for p in _proposals if p.get("apy_impact")]
    _other_props = [p for p in _proposals if not p.get("apy_impact")]
    _sorted_props = _apy_props + _other_props

    for _prop in _sorted_props:
        _imp_badge = (" <span style='background:#1c1200;color:#FBBF24;font-size:0.68rem;"
                     "padding:1px 6px;border-radius:4px;border:1px solid #fbbf2444'>⚡ APY Impact</span>"
                     if _prop.get("apy_impact") else "")
        _vote_url  = _html.escape(str(_prop.get("url") or ""))
        _vote_link = (f" · <a href='{_vote_url}' target='_blank' "
                      f"style='color:#a78bfa;font-size:0.72rem;text-decoration:none;'>Vote ↗</a>"
                      if _vote_url else "")
        st.markdown(
            f"<div style='background:rgba(0,0,0,0.15);border:1px solid rgba(255,255,255,0.05);"
            f"border-left:3px solid {'#FBBF24' if _prop.get('apy_impact') else '#334155'};"
            f"border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:0.85rem'>"
            f"<b>{_html.escape(str(_prop.get('title', '')))}</b>{_imp_badge}<br>"
            f"<span style='color:#64748b;font-size:0.75rem'>"
            f"{_html.escape(str(_prop.get('space', _prop.get('protocol', '—'))))} · "
            f"{_prop.get('votes', 0):,} votes · ends {_prop.get('end_date', _prop.get('ends_at', '—'))}"
            f"{_vote_link}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    st.caption(
        f"{len(_proposals)} active proposals · {len(_apy_props)} flagged as APY-impacting. "
        "Source: Snapshot GraphQL · cached 1 hour."
    )
else:
    # Only show the "all clear" message when the fetch succeeded and returned
    # an empty list.  On API error, show a neutral info message instead.
    if demo_mode or not governance_fetch_failed():
        st.success("✓ No active governance votes affecting APY right now.")
    else:
        st.info("Governance data temporarily unavailable (Snapshot API). Check back later.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Token Unlock Calendar (#84) ────────────────────────────────────────────

render_section_header(
    "Token Unlock Calendar",
    "Upcoming token unlocks that may create sell pressure — shown for all users (risk-relevant)",
)

with st.spinner("Loading token unlock schedule…"):
    if demo_mode:
        try:
            from data.demo_data import DEMO_TOKEN_UNLOCKS as _unlock_alerts
        except Exception:
            _unlock_alerts = []
    else:
        try:
            _unlock_alerts = _cached_token_unlock_alerts(within_days=30)
        except Exception:
            _unlock_alerts = []

if _unlock_alerts:
    for _ul in _unlock_alerts:
        _ul_sev  = _ul.get("severity", "INFO")
        _ul_msg  = (
            f"**{_ul['token']}** — {_ul['amount_pct']:.1f}% unlock · "
            f"{_ul['type']} · {_ul['date']} "
            f"({'cliff (all-at-once)' if _ul.get('is_cliff') else 'linear'}) · "
            f"**{_ul['days_until']} days away**"
        )
        if _ul_sev == "CRITICAL":
            st.error(_ul_msg)
        else:
            st.warning(_ul_msg)

    # Summary table
    _ul_rows = []
    for _ul in _unlock_alerts:
        _ul_rows.append({
            "Token":      _ul["token"],
            "Date":       _ul["date"],
            "Amount %":   f"{_ul['amount_pct']:.1f}%",
            "Type":       _ul["type"],
            "Days Until": _ul["days_until"],
            "Cliff?":     "Yes (all-at-once)" if _ul.get("is_cliff") else "No (linear)",
            "Severity":   _ul["severity"],
        })
    st.dataframe(pd.DataFrame(_ul_rows), width="stretch", hide_index=True)
    st.caption(
        "Large unlocks can create sell pressure. "
        "Cliff unlocks (all-at-once) are higher risk than linear unlocks. "
        "CRITICAL = amount >= 10% supply or <= 7 days away."
    )
else:
    st.success("✓ No major token unlocks in the next 30 days.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Bridge Flow Indicator (#85) ────────────────────────────────────────────

if _pro_mode:
    render_section_header(
        "Bridge Flow Monitor",
        "7-day TVL change per chain as capital flow proxy — INFLOW = capital entering, OUTFLOW = leaving",
    )

    with st.spinner("Fetching bridge flow data…"):
        if demo_mode:
            try:
                from data.demo_data import DEMO_BRIDGE_FLOWS as _flows
            except Exception:
                _flows = []
        else:
            _flows = fetch_bridge_flows()

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


# ── Global Yield Opportunities (#68) ─────────────────────────────────────────

render_section_header(
    "Global Yield Opportunities",
    "Top 20 DeFi pools by TVL across all chains — filter by chain and min APY. "
    "Sorted by Sharpe ratio (risk-adjusted). Compare Flare vs the broader market.",
)

with st.spinner("Loading global yield pools…"):
    if demo_mode:
        _gy_pools = [
            {"pool_id": "1", "protocol": "aave-v3",        "chain": "Ethereum", "symbol": "USDC",         "apy": 5.2,  "tvl_usd": 2_800_000_000, "apy_7d": 5.0,  "il_risk": "no"},
            {"pool_id": "2", "protocol": "lido",            "chain": "Ethereum", "symbol": "stETH",        "apy": 3.8,  "tvl_usd": 18_000_000_000,"apy_7d": 3.7,  "il_risk": "no"},
            {"pool_id": "3", "protocol": "ethena",          "chain": "Ethereum", "symbol": "sUSDe",        "apy": 27.5, "tvl_usd": 3_900_000_000, "apy_7d": 24.0, "il_risk": "no"},
            {"pool_id": "4", "protocol": "morpho",          "chain": "Ethereum", "symbol": "USDC vault",   "apy": 9.3,  "tvl_usd": 1_800_000_000, "apy_7d": 9.1,  "il_risk": "no"},
            {"pool_id": "5", "protocol": "aerodrome-v2",    "chain": "Base",     "symbol": "USDC/WETH",    "apy": 38.7, "tvl_usd": 580_000_000,   "apy_7d": 32.0, "il_risk": "yes"},
            {"pool_id": "6", "protocol": "pendle",          "chain": "Ethereum", "symbol": "PT-USDe",      "apy": 12.4, "tvl_usd": 420_000_000,   "apy_7d": 11.8, "il_risk": "no"},
            {"pool_id": "7", "protocol": "uniswap-v3",      "chain": "Ethereum", "symbol": "USDC/ETH",     "apy": 8.1,  "tvl_usd": 310_000_000,   "apy_7d": 7.9,  "il_risk": "yes"},
            {"pool_id": "8", "protocol": "compound-v3",     "chain": "Ethereum", "symbol": "USDC",         "apy": 4.5,  "tvl_usd": 900_000_000,   "apy_7d": 4.4,  "il_risk": "no"},
            {"pool_id": "9", "protocol": "kinetic-finance",  "chain": "Flare",   "symbol": "USDT0",        "apy": 8.0,  "tvl_usd": 64_000_000,    "apy_7d": 7.8,  "il_risk": "no"},
            {"pool_id":"10", "protocol": "clearpool-lending","chain": "Flare",   "symbol": "USD0 X-Pool",  "apy": 11.5, "tvl_usd": 46_000_000,    "apy_7d": 11.2, "il_risk": "no"},
        ]
    else:
        _gy_pools = fetch_llama_yield_pools(min_tvl_usd=100_000, top_n=50)

# Filter controls
_gy_chains = sorted({p["chain"] for p in _gy_pools}) if _gy_pools else []
_gy_col1, _gy_col2 = st.columns([2, 1])
with _gy_col1:
    _gy_chain_filter = st.multiselect(
        "Filter by Chain", options=_gy_chains,
        default=[], key="gy_chain_filter",
        placeholder="All chains",
    )
with _gy_col2:
    _gy_min_apy = st.number_input(
        "Min APY %", min_value=0.0, max_value=500.0, value=0.0, step=0.5,
        key="gy_min_apy",
        help="Annual Percentage Yield — the yearly return you'd earn. Includes both trading fees and token rewards. Higher isn't always better — check the risk score.",
    )

# Apply filters
_gy_filtered = _gy_pools
if _gy_chain_filter:
    _gy_filtered = [p for p in _gy_filtered if p["chain"] in _gy_chain_filter]
if _gy_min_apy > 0:
    _gy_filtered = [p for p in _gy_filtered if p.get("apy", 0) >= _gy_min_apy]

# Compute Sharpe for each pool and sort by Sharpe descending
_gy_display = []
for _gp in _gy_filtered[:20]:
    _g_apy    = float(_gp.get("apy", 0))
    _g_apy_7d = float(_gp.get("apy_7d", _g_apy))
    _sharpe   = compute_pool_sharpe(_g_apy, _g_apy_7d)
    _gy_display.append({**_gp, "_sharpe_val": _sharpe["sharpe"], "_rank": _sharpe["risk_adjusted_rank"]})
_gy_display.sort(key=lambda x: x["_sharpe_val"], reverse=True)

if _gy_display:
    _gy_rows = []
    for _gp in _gy_display:
        _g_apy   = float(_gp.get("apy", 0))
        _g_apy7d = float(_gp.get("apy_7d", _g_apy))
        _g_tvl   = float(_gp.get("tvl_usd", 0))
        _g_sh    = _gp["_sharpe_val"]
        _g_rank  = _gp["_rank"]
        _sh_col  = {"excellent": "#22c55e", "good": "#84cc16", "fair": "#f59e0b", "poor": "#ef4444"}.get(_g_rank, "#9ca3af")
        _row = {
            "Protocol":   (_gp.get("protocol") or "—").replace("-", " ").title(),
            "Chain":      _gp.get("chain", "—"),
            "Pool":       _gp.get("symbol", "—"),
            "APY %":      f"{_g_apy:.2f}%",
            "7d Avg APY": f"{_g_apy7d:.2f}%",
            "TVL":        (f"${_g_tvl/1e9:.2f}B" if _g_tvl >= 1e9
                          else f"${_g_tvl/1e6:.1f}M" if _g_tvl >= 1e6
                          else f"${_g_tvl/1e3:.0f}K"),
            "Sharpe":     f"{_g_sh:.2f}",
            "Quality":    _g_rank.capitalize(),
            "IL Risk":    ("Yes" if _gp.get("il_risk", "no") not in ("no", "") else "No"),
        }
        _gy_rows.append(_row)
    st.dataframe(pd.DataFrame(_gy_rows), width="stretch", hide_index=True)
    st.caption(
        "Sorted by Sharpe ratio (risk-adjusted). "
        "Quality: Excellent >2.0, Good 1-2, Fair 0.5-1, Poor <0.5. "
        "Source: yields.llama.fi — updated every 15 minutes."
    )
else:
    st.info("No pools match the selected filters. Try reducing min APY or selecting more chains.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Cross-Chain DeFi — Ethena sUSDe (#76) ────────────────────────────────────

render_section_header(
    "Cross-Chain DeFi",
    "Ethena sUSDe (delta-neutral) · Aerodrome Finance (Base) · Morpho Blue vaults",
)

with st.spinner("Loading cross-chain protocol data…"):
    if demo_mode:
        _ethena  = {"susde_apy": 27.5, "protocol": "ethena", "mechanism": "delta_neutral", "source": "demo"}
        _aero    = [
            {"symbol": "USDC/WETH", "project": "aerodrome-v2", "chain": "Base", "apy": 38.7, "apy_7d": 32.0, "tvl_usd": 580_000_000},
            {"symbol": "WETH/cbBTC","project": "aerodrome-v2", "chain": "Base", "apy": 52.1, "apy_7d": 48.0, "tvl_usd": 290_000_000},
        ]
        _morpho  = [
            {"symbol": "USDC vault",  "project": "morpho", "chain": "Ethereum", "apy": 9.3, "apy_7d": 9.1, "tvl_usd": 1_800_000_000},
            {"symbol": "WETH vault",  "project": "morpho", "chain": "Ethereum", "apy": 4.2, "apy_7d": 4.1, "tvl_usd": 650_000_000},
        ]
    else:
        _ethena = _cached_ethena_yield()
        _aero   = _cached_aerodrome_pools()
        _morpho = _cached_morpho_vaults()

# Ethena sUSDe card
_eth_apy = float(_ethena.get("susde_apy", 0))
_eth_src  = _ethena.get("source", "—")
if _eth_apy > 0:
    _eth_sharpe = compute_pool_sharpe(_eth_apy, _eth_apy * 0.9)  # use 90% as 7d proxy
    _eth_real   = compute_real_yield_ratio(
        total_apy=_eth_apy,
        emission_apy=_eth_apy * 0.5,  # Ethena ~50% emission approximation
    )
    _eth_sh_col = {"excellent": "#22c55e", "good": "#84cc16", "fair": "#f59e0b", "poor": "#ef4444"}.get(
        _eth_sharpe["risk_adjusted_rank"], "#9ca3af"
    )
    _eth_cls    = _eth_real["classification"]
    _eth_cls_col = "#22c55e" if _eth_cls == "SUSTAINABLE" else ("#f59e0b" if _eth_cls == "MIXED" else "#ef4444")
    st.markdown(
        f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.07);"
        f"border-left:3px solid #6366f1;border-radius:8px;padding:12px 16px;margin-bottom:10px'>"
        f"<div style='display:flex;justify-content:space-between;align-items:center'>"
        f"<div>"
        f"<span style='font-weight:700;font-size:1.0rem;color:#e2e8f0'>Ethena sUSDe</span> "
        f"<span style='color:#64748b;font-size:0.75rem'>delta-neutral · {_eth_src}</span>"
        f"</div>"
        f"<div style='text-align:right'>"
        f"<span style='font-size:1.3rem;font-weight:700;color:#22c55e'>{_eth_apy:.1f}% APY</span>"
        f"</div>"
        f"</div>"
        f"<div style='margin-top:8px;display:flex;gap:8px;flex-wrap:wrap'>"
        f"<span style='background:rgba(0,0,0,0.2);border:1px solid {_eth_sh_col};color:{_eth_sh_col};"
        f"font-size:0.70rem;padding:2px 7px;border-radius:10px'>"
        f"Sharpe {_eth_sharpe['sharpe']:.2f} · {_eth_sharpe['risk_adjusted_rank'].capitalize()}</span>"
        f"<span style='background:rgba(0,0,0,0.2);border:1px solid {_eth_cls_col};color:{_eth_cls_col};"
        f"font-size:0.70rem;padding:2px 7px;border-radius:10px'>{_eth_cls}</span>"
        f"<span style='background:rgba(0,0,0,0.2);border:1px solid #475569;color:#94a3b8;"
        f"font-size:0.70rem;padding:2px 7px;border-radius:10px'>delta-neutral hedge</span>"
        f"</div>"
        f"<div style='color:#64748b;font-size:0.74rem;margin-top:6px'>"
        f"Mechanism: Short perpetual futures hedge offsets ETH price risk. Yield from funding rates + staking."
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )
else:
    st.info("Ethena sUSDe data unavailable.")

# Aerodrome + Morpho tables
_cc_col1, _cc_col2 = st.columns(2)

with _cc_col1:
    st.markdown("**Aerodrome Finance (Base)**")
    if _aero:
        _aero_rows = []
        for _ap in _aero:
            _a_apy   = float(_ap.get("apy", 0))
            _a_apy7d = float(_ap.get("apy_7d", _a_apy))
            _a_sh    = compute_pool_sharpe(_a_apy, _a_apy7d)
            _aero_rows.append({
                "Pool":   _ap.get("symbol", "—"),
                "APY %":  f"{_a_apy:.1f}%",
                "Sharpe": f"{_a_sh['sharpe']:.2f}",
                "TVL":    f"${float(_ap.get('tvl_usd',0))/1e6:.0f}M",
            })
        st.dataframe(pd.DataFrame(_aero_rows), width="stretch", hide_index=True)
    else:
        st.info("Aerodrome data unavailable.")

with _cc_col2:
    st.markdown("**Morpho Blue Vaults**")
    if _morpho:
        _morpho_rows = []
        for _mp in _morpho:
            _m_apy   = float(_mp.get("apy", 0))
            _m_apy7d = float(_mp.get("apy_7d", _m_apy))
            _m_sh    = compute_pool_sharpe(_m_apy, _m_apy7d)
            _real    = compute_real_yield_ratio(_m_apy, 0.0)  # Morpho is mostly fee-based
            _morpho_rows.append({
                "Vault":   _mp.get("symbol", "—"),
                "Chain":   _mp.get("chain", "—"),
                "APY %":   f"{_m_apy:.1f}%",
                "Sharpe":  f"{_m_sh['sharpe']:.2f}",
                "Real Yield": _real["classification"].replace("_", " ").title(),
                "TVL":     f"${float(_mp.get('tvl_usd',0))/1e6:.0f}M",
            })
        st.dataframe(pd.DataFrame(_morpho_rows), width="stretch", hide_index=True)
    else:
        st.info("Morpho data unavailable.")

# ── ERC-4626 Live Vault Rates (#103) ─────────────────────────────────────────

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

render_section_header(
    "Live Vault Rates (ERC-4626)",
    "On-chain pricePerShare reads from major yield vaults — Morpho & Aave · via public Ethereum RPC",
)

try:
    if demo_mode:
        _erc4626_data = {
            "Morpho USDC (Re7)":      {"price_per_share": 1.000823, "total_assets_usd": 48_200_000, "yield_source": "morpho_vault", "data_source": "vault_read"},
            "Aave aUSDC v3":          {"price_per_share": 1.000012, "total_assets_usd": 2_800_000_000, "yield_source": "aave_v3",   "data_source": "vault_read"},
            "Morpho WETH (Gauntlet)": {"price_per_share": 1.000034, "total_assets_usd": 420_000_000, "yield_source": "morpho_vault", "data_source": "vault_read"},
            "Aave aWETH v3":          {"price_per_share": 1.000009, "total_assets_usd": 980_000_000, "yield_source": "aave_v3",      "data_source": "vault_read"},
            "timestamp": "2026-03-27T00:00:00Z",
        }
    else:
        with st.spinner("Reading vault prices from Ethereum RPC…"):
            _erc4626_data = _cached_erc4626_yield_data()

    _vault_cards = [(k, v) for k, v in _erc4626_data.items() if k != "timestamp" and isinstance(v, dict)]
    _vault_cards.sort(key=lambda x: x[1].get("price_per_share", 1.0), reverse=True)

    if _vault_cards:
        _vc_cols = st.columns(min(len(_vault_cards), 4))
        for _vci, (_vname, _vdata) in enumerate(_vault_cards):
            _pps     = _vdata.get("price_per_share", 1.0)
            _ta      = _vdata.get("total_assets_usd", 0.0)
            _vsrc    = _vdata.get("data_source", "unavailable")
            _yld_src = _vdata.get("yield_source", "—")
            _live    = _vsrc == "vault_read"
            _badge   = "<span style='background:#164e63;color:#67e8f9;font-size:0.65rem;padding:1px 6px;border-radius:8px;margin-left:4px'>📡 Live</span>" if _live else "<span style='background:#1c1917;color:#a8a29e;font-size:0.65rem;padding:1px 6px;border-radius:8px;margin-left:4px'>📊 Estimated</span>"
            _tvl_str = (
                f"${_ta/1e9:.2f}B" if _ta >= 1e9
                else f"${_ta/1e6:.0f}M" if _ta >= 1e6
                else f"${_ta:,.0f}" if _ta > 0
                else "—"
            )
            with _vc_cols[_vci % len(_vc_cols)]:
                st.markdown(
                    f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.07);"
                    f"border-left:3px solid {'#22c55e' if _live else '#f59e0b'};"
                    f"border-radius:8px;padding:12px 14px;margin-bottom:8px'>"
                    f"<div style='font-weight:700;font-size:0.85rem;color:#e2e8f0;margin-bottom:4px'>{_html.escape(_vname)}{_badge}</div>"
                    f"<div style='font-size:1.1rem;font-weight:700;color:#22c55e'>pps: {_pps:.6f}</div>"
                    f"<div style='font-size:0.75rem;color:#64748b;margin-top:3px'>Total Assets: {_tvl_str}</div>"
                    f"<div style='font-size:0.67rem;color:#334155;margin-top:4px'>Source: {_html.escape(_yld_src)}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        st.caption(
            f"pricePerShare read at {_erc4626_data.get('timestamp', '')} · "
            "📡 Live = direct RPC call to vault contract · 📊 Estimated = DeFiLlama fallback. "
            "pps > 1.0 means yield has accrued since vault inception."
        )
    else:
        st.info("ERC-4626 vault data unavailable. Check RPC connectivity.")
except Exception as _e4626:
    st.warning(f"ERC-4626 vault read error: {_e4626}")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── 7-Day APY Sparklines for Multi-Chain Pools (#81) ─────────────────────────

render_section_header(
    "7-Day APY Sparklines",
    "Mini trend charts for top yield pools — green = trending up, red = trending down",
)

# Use the global yield pools already fetched; show sparklines for top 6 by Sharpe
_sp_pools = []
for _sp in (_gy_display or [])[:6]:
    _sp_apy   = float(_sp.get("apy", 0))
    _sp_apy7d = float(_sp.get("apy_7d", _sp_apy))
    if _sp_apy > 0:
        _sp_pools.append(_sp)

if _sp_pools:
    _sp_cols = st.columns(min(len(_sp_pools), 3))
    for _si, _sp in enumerate(_sp_pools[:6]):
        _col_idx   = _si % 3
        _sp_apy    = float(_sp.get("apy", 0))
        _sp_apy7d  = float(_sp.get("apy_7d", _sp_apy))
        _sp_proto  = (_sp.get("protocol") or "").replace("-", " ").title()
        _sp_sym    = _sp.get("symbol", "")
        _sp_chain  = _sp.get("chain", "")

        # Build a synthetic 7-point sparkline from current APY and 7d average
        # Interpolate linearly between apy_7d and apy across 7 points
        _spark_vals = [
            round(_sp_apy7d + (_sp_apy - _sp_apy7d) * (i / 6), 2)
            for i in range(7)
        ]
        _trending_up = _sp_apy >= _sp_apy7d
        _sp_line_col = "#22c55e" if _trending_up else "#ef4444"
        _sp_fill_col = "rgba(34,197,94,0.08)" if _trending_up else "rgba(239,68,68,0.08)"

        with _sp_cols[_col_idx]:
            st.markdown(
                f"<div style='font-size:0.72rem;color:#64748b;text-align:center;margin-bottom:4px'>"
                f"{_html.escape(_sp_proto)}<br>"
                f"<span style='color:#94a3b8;font-weight:600'>{_html.escape(_sp_sym)}</span> "
                f"<span style='color:#475569'>· {_html.escape(_sp_chain)}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            _fig_sp = go.Figure()
            _fig_sp.add_trace(go.Scatter(
                y=_spark_vals,
                mode="lines",
                line=dict(color=_sp_line_col, width=2),
                fill="tozeroy",
                fillcolor=_sp_fill_col,
                hovertemplate="%{y:.2f}%<extra></extra>",
            ))
            _fig_sp.update_layout(
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(visible=False),
                yaxis=dict(visible=False),
                margin=dict(l=0, r=0, t=0, b=0),
                height=60,
                showlegend=False,
            )
            st.plotly_chart(_fig_sp, width="stretch", config={"displayModeBar": False})
            _dir_sym = "▲" if _trending_up else "▼"
            st.markdown(
                f"<div style='text-align:center;font-size:0.73rem;color:{_sp_line_col};margin-top:-10px'>"
                f"{_dir_sym} {_sp_apy:.2f}% APY</div>",
                unsafe_allow_html=True,
            )
        # Add new row of columns every 3 pools
        if _col_idx == 2 and _si < len(_sp_pools) - 1:
            _sp_cols = st.columns(min(len(_sp_pools) - _si - 1, 3))
else:
    st.info("Load global yield pools above to see sparklines.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

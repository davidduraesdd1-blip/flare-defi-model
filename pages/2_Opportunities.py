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
    render_what_this_means, render_yield_sustainability, signal_badge_html,
    get_user_level,
)
# config imports consolidated below in models.risk_models import block
from scanners.defillama import (
    fetch_yields_pools, fetch_protocol_risk_score, fetch_tvl_change_alert,
    fetch_governance_alerts, fetch_bridge_flows,
    fetch_llama_yield_pools,            # #68 global yield pools
    fetch_protocol_revenue,             # D4 — protocol revenue trend
    fetch_all_hacks,                    # D3 — hack history panel
    fetch_pool_apy_history,             # Item 34 — real historical APY chart
    fetch_protocol_treasuries,          # Item 30 — protocol treasury health
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
    fetch_flare_gecko_pools,            # Flare network full discovery
)
from models.risk_models import (
    compute_pool_sharpe,                # #72
    compute_real_yield_ratio,           # #73
    compute_protocol_risk_score,        # #80
    run_portfolio_monte_carlo,          # Items 6/9/10 — Monte Carlo simulation
    compute_v3_suggested_range,         # Item 8    — V3 LP range suggestion
)
from config import (
    RISK_PROFILES, RISK_PROFILE_NAMES, INCENTIVE_PROGRAM,
    PROTOCOL_AUDITS, PROTOCOL_DEPENDENCIES, risk_letter_grade,
    PROTOCOLS as _PROTOCOLS_CFG,
)

try:
    from models.composite_signal import compute_composite_signal
    from macro_feeds import fetch_all_macro_data, fetch_coinmetrics_onchain, fetch_btc_ta_signals
    from ui.common import fetch_fear_greed_history as _fetch_fg_history
    _COMPOSITE_AVAIL = True
except ImportError:
    _COMPOSITE_AVAIL = False

# Agent-executable protocol sets (used to label Multi-Chain Pool rows)
try:
    from agents.config import FLARE_PROTOCOL_WHITELIST, XRPL_PROTOCOL_WHITELIST
    _AGENT_FLARE  = FLARE_PROTOCOL_WHITELIST
    _AGENT_XRPL   = XRPL_PROTOCOL_WHITELIST
except Exception:
    _AGENT_FLARE  = frozenset({"kinetic", "blazeswap", "sparkdex"})
    _AGENT_XRPL   = frozenset({"xrpl_dex", "xrpl_amm"})


def _agent_executable(project: str, chain: str) -> str:
    """Return badge text showing whether the agent can execute on this pool."""
    _p   = str(project).lower().replace("-", "").replace("_", "").replace(" ", "")
    _c   = str(chain).lower()
    # Check Flare whitelist (strip common suffixes)
    for _wl in _AGENT_FLARE:
        if _wl.replace("-", "").replace("_", "") in _p or _p in _wl.replace("-", "").replace("_", ""):
            return "▲ Agent"
    # Check XRPL whitelist
    for _wl in _AGENT_XRPL:
        if _wl.replace("-", "").replace("_", "") in _p:
            return "▲ Agent"
    return "— Info only"


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


@st.cache_data(ttl=900)
def _cached_flare_gecko_pools():
    """Cached wrapper for fetch_flare_gecko_pools(). TTL=15 min."""
    return fetch_flare_gecko_pools(pages=5, min_tvl_usd=5_000)


@st.cache_data(ttl=600, show_spinner=False)
def _cached_bridge_flows():
    """Streamlit-managed bridge flow cache (TTL=10 min).

    Explicitly pops the module-level _cache entry in defillama.py before
    calling fetch_bridge_flows() so that stale zeros from a previous
    (now-fixed) implementation never survive Streamlit hot-reloads.
    Streamlit's @st.cache_data is the authoritative cache for this data.
    """
    import scanners.defillama as _dl
    _dl._cache.pop("bridge_flows", None)
    return _dl.fetch_bridge_flows()


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


@st.cache_data(ttl=3600)
def _cached_protocol_revenue():
    """Cached wrapper for fetch_protocol_revenue(). TTL=1 hour. (D4)"""
    return fetch_protocol_revenue()


@st.cache_data(ttl=21600)
def _cached_all_hacks():
    """Cached wrapper for fetch_all_hacks(). TTL=6 hours. (D3)"""
    return fetch_all_hacks()


@st.cache_data(ttl=3600)
def _cached_composite_signal() -> dict:
    """Compute composite market environment signal (4-layer). Cached 1 hour."""
    if not _COMPOSITE_AVAIL:
        return {}
    try:
        macro_data   = fetch_all_macro_data()
        onchain_data = fetch_coinmetrics_onchain(days=400)
        ta_data      = fetch_btc_ta_signals()
        fg_val = None
        try:
            hist = _fetch_fg_history(7)
            fg_val = int(hist[0]["value"]) if hist else None
        except Exception:
            pass
        return compute_composite_signal(
            macro_data=macro_data,
            onchain_data=onchain_data,
            fg_value=fg_val,
            ta_data=ta_data,
        )
    except Exception:
        return {}


page_setup("Opportunities · Flare DeFi")

ctx            = render_sidebar()
profile        = ctx["profile"]
profile_cfg    = ctx["profile_cfg"]
color          = ctx["color"]
weight         = ctx["weight"]
portfolio_size = ctx["portfolio_size"]
demo_mode      = ctx.get("demo_mode", False)
_user_level    = ctx.get("user_level", get_user_level())

# #82 Beginner/Pro toggle — reads from sidebar session state (set in ui/common.py render_sidebar)
# The sidebar toggle is the canonical source; this reads it for page-level conditionals.
_pro_mode = st.session_state.get("defi_pro_mode", False)

@st.cache_data(ttl=600)
def _load_opp_data_cached() -> dict:
    """Load and return model_data for all profiles, keyed by profile name.
    Cached for 10 minutes to avoid duplicate queries for radar chart and table.
    No profile argument — returns ALL profiles so the same cache entry is
    reused regardless of which profile is currently selected.
    """
    _latest = load_latest()
    return _latest.get("models") or {}


model_data = _load_opp_data_cached()
latest     = load_latest()   # uses _load_history_file cache — no duplicate disk read
runs       = load_history_runs()

st.title("🎯 Opportunities")
st.caption("Real-time yield opportunities across Flare + DeFiLlama protocols • Auto-refreshed every 15 minutes")
st.markdown(
    "<div style='color:#475569; font-size:0.87rem; margin-bottom:16px;'>"
    "Starter portfolios · APY trends · options strategies</div>",
    unsafe_allow_html=True,
)

# ── Market Environment Banner (composite signal) ──────────────────────────────
_csig = _cached_composite_signal()
if _csig:
    _score  = _csig.get("score", 0.0)
    _signal = _csig.get("signal", "NEUTRAL")
    _layers = _csig.get("layers", {})

    # Color coding
    if _score >= 0.3:
        _sig_color, _sig_bg = "#22c55e", "rgba(34,197,94,0.07)"
    elif _score >= 0.1:
        _sig_color, _sig_bg = "#00d4aa", "rgba(0,212,170,0.07)"
    elif _score >= -0.1:
        _sig_color, _sig_bg = "#f59e0b", "rgba(245,158,11,0.07)"
    elif _score >= -0.3:
        _sig_color, _sig_bg = "#f97316", "rgba(249,115,22,0.07)"
    else:
        _sig_color, _sig_bg = "#ef4444", "rgba(239,68,68,0.07)"

    _signal_label = _signal.replace("_", " ")
    _score_pct    = int((_score + 1) / 2 * 100)   # convert -1..+1 to 0..100%

    if _user_level == "beginner":
        # Beginner: plain English + color
        _beginner_txt = _csig.get("beginner_summary", "")
        st.html(
            f"<div style='background:{_sig_bg};border:1px solid {_sig_color}33;"
            f"border-left:4px solid {_sig_color};border-radius:8px;padding:12px 18px;"
            f"margin-bottom:16px;'>"
            f"<span style='color:{_sig_color};font-weight:700;font-size:0.92rem;'>■ Market Conditions</span>"
            f"<span style='color:#94a3b8;font-size:0.85rem;margin-left:12px;'>{_beginner_txt}</span>"
            f"</div>"
        )
    else:
        # Intermediate/Advanced: signal score + 4-layer breakdown
        _ta_s      = _layers.get("technical", {}).get("score", 0)
        _macro_s   = _layers.get("macro",     {}).get("score", 0)
        _sent_s    = _layers.get("sentiment", {}).get("score", 0)
        _chain_s   = _layers.get("onchain",   {}).get("score", 0)
        def _s(v): return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"
        st.html(
            f"<div style='background:{_sig_bg};border:1px solid {_sig_color}33;"
            f"border-left:4px solid {_sig_color};border-radius:8px;padding:12px 18px;"
            f"margin-bottom:16px;display:flex;align-items:center;gap:24px;flex-wrap:wrap;'>"
            f"<div><span style='color:#64748b;font-size:0.75rem;text-transform:uppercase;letter-spacing:0.05em;'>Market Environment</span>"
            f"<div style='color:{_sig_color};font-weight:800;font-size:1.1rem;'>{_signal_label}</div>"
            f"<div style='color:#64748b;font-size:0.78rem;'>Score {_s(_score)}</div></div>"
            f"<div style='color:#475569;font-size:0.8rem;border-left:1px solid #1e293b;padding-left:20px;'>"
            f"<div>Technical <span style='color:{'#22c55e' if _ta_s>=0 else '#ef4444'};font-weight:600;'>{_s(_ta_s)}</span></div>"
            f"<div>Macro <span style='color:{'#22c55e' if _macro_s>=0 else '#ef4444'};font-weight:600;'>{_s(_macro_s)}</span></div>"
            f"<div>Sentiment <span style='color:{'#22c55e' if _sent_s>=0 else '#ef4444'};font-weight:600;'>{_s(_sent_s)}</span></div>"
            f"<div>On-Chain <span style='color:{'#22c55e' if _chain_s>=0 else '#ef4444'};font-weight:600;'>{_s(_chain_s)}</span></div>"
            f"</div></div>"
        )

# Agent scope banner — always visible so users understand what the agent can act on
st.html(
    "<div style='background:rgba(0,212,170,0.06);border:1px solid rgba(0,212,170,0.2);"
    "border-left:3px solid #00d4aa;border-radius:8px;padding:10px 16px;margin-bottom:20px;"
    "font-size:0.83rem;color:#94a3b8;'>"
    "<span style='color:#00d4aa;font-weight:700;'>▲ Agent-Executable</span>"
    " protocols on this page: <span style='color:#f1f5f9;font-weight:600;'>"
    "Kinetic · BlazeSwap · SparkDEX</span> (Flare) · "
    "<span style='color:#f1f5f9;font-weight:600;'>XRPL DEX · XRPL AMM</span> (XRP Ledger). "
    "All other protocols are <span style='color:#64748b;'>— Info only</span> "
    "— the agent cannot execute on Ethereum, Base, or Solana protocols.</div>"
)

# ─── Sub-tabs: Yield Opportunities | Protocol Intelligence ─────────────────────
_tab_yield, _tab_intel = st.tabs([
    "🌾 Yield Opportunities",
    "🛡️ Protocol Intelligence",
])

with _tab_yield:

    # ── Global Yield Summary — top of page (Item 15: yield table first) ──────────
    render_section_header(
        "Global Yield Table",
        "Top opportunities across all chains — sorted by risk-adjusted return",
    )
    @st.cache_data(ttl=900)
    def _cached_top_global_yields_compact() -> list:
        try:
            pools = fetch_llama_yield_pools(min_tvl_usd=10_000_000, top_n=100) or []
            return sorted(
                [p for p in pools if float(p.get("apy") or 0) > 0],
                key=lambda x: float(x.get("apy") or 0),
                reverse=True,
            )[:10]
        except Exception:
            return []

    _top_global = _cached_top_global_yields_compact()
    if _top_global:
        _gyl_rows = []
        for _gp in _top_global:
            _gapy = float(_gp.get("apy") or 0)
            _gtvl = float(_gp.get("tvlUsd") or 0)
            _gchain = str(_gp.get("chain") or "—")
            _gproto = str(_gp.get("project") or "—").replace("-", " ").title()
            _gsym   = str(_gp.get("symbol") or "—")
            _agent  = _agent_executable(_gp.get("project", ""), _gchain)
            _gyl_rows.append({
                "Protocol":  _gproto,
                "Chain":     _gchain,
                "Pool":      _gsym,
                "APY":       f"{_gapy:.1f}%",
                "TVL":       f"${_gtvl/1e6:.1f}M" if _gtvl >= 1e6 else f"${_gtvl:,.0f}",
                "Agent":     _agent,
            })
        st.dataframe(pd.DataFrame(_gyl_rows), width='stretch', hide_index=True, height=300)
        st.caption("Top 10 by APY · min $10M TVL · Full table in the Global Yield Opportunities section below")
    else:
        st.info("Global yield data loading…")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # Beginner orientation (Phase 2, item 8)
    render_what_this_means(
        "This page shows real yield opportunities on the Flare Network. "
        "Each card shows an APY (annual return), a risk rating (A–F), "
        "and how much of that return is from real trading fees vs. printed reward tokens. "
        "Higher APY usually = higher risk. Start with Grade A or B opportunities matching your risk profile.",
        title="How do I use this page?",
        intermediate_message="Live yield opportunities on Flare — APY, risk grade, fee sustainability, and model confidence shown per card.",
    )


    # ─── Starter Portfolios ───────────────────────────────────────────────────────

    render_section_header("Starter Portfolios", "Pre-built yield-weighted allocations for each risk profile")

    # Compute incentive program days remaining for expiry badge
    from datetime import datetime as _dt
    try:
        _exp_date    = _dt.strptime(INCENTIVE_PROGRAM["expires"], "%Y-%m-%d")
        _days_left   = max(0, (_exp_date - _dt.now()).days)
    except Exception:
        _days_left   = 999
    _reward_hide_pct  = float(INCENTIVE_PROGRAM.get("reward_hide_below_pct",  2.0))
    _reward_warn_days = int(INCENTIVE_PROGRAM.get("reward_warn_below_days", 90))
    _reward_gray_days = int(INCENTIVE_PROGRAM.get("reward_gray_below_days", 30))

    # Incentive expiry banner (Beginner: simple banner; Intermediate+: days shown)
    if _days_left <= _reward_gray_days:
        _exp_msg = "⚠️ Bonus rewards have ended — only base fee yield shown." if _days_left == 0 else f"⚠️ Bonus rewards expire in {_days_left} day{'s' if _days_left != 1 else ''}. Base fee yield will continue after expiry."
        st.warning(_exp_msg)
    elif _days_left <= _reward_warn_days:
        if _user_level == "beginner":
            st.info("ℹ️ Note: Bonus RFLR/SPRK rewards are ending soon. Returns will be lower after July 2026.")
        else:
            st.info(f"ℹ️ RFLR/SPRK incentive program expires in **{_days_left} days** (July 2026). Allocations are weighted on sustainable base yield only.")

    for p in RISK_PROFILE_NAMES:
        opps = model_data.get(p) or []
        pcfg = RISK_PROFILES[p]
        pcol = pcfg["color"]
        w    = weight if p == profile else 1.0
        if not opps:
            continue

        # Compute the actual portfolio weighted APY for the expander header (dynamic target)
        _total_kf     = sum(float(o.get("kelly_fraction", 0)) for o in opps)
        _wtd_fee_apy  = sum(float(o.get("fee_apy", 0))    * float(o.get("kelly_fraction", 0)) for o in opps)
        _wtd_rwd_apy  = sum(float(o.get("reward_apy", 0)) * float(o.get("kelly_fraction", 0)) for o in opps)
        _wtd_fee_apy  = round(_wtd_fee_apy  / _total_kf, 1) if _total_kf > 0 else 0.0
        _wtd_rwd_apy  = round(_wtd_rwd_apy  / _total_kf, 1) if _total_kf > 0 else 0.0
        _wtd_total    = round(_wtd_fee_apy + _wtd_rwd_apy, 1)

        # Dynamic target label: actual achievable weighted APY (not aspirational)
        if _wtd_rwd_apy >= _reward_hide_pct and _days_left > _reward_gray_days:
            _target_label = f"~{_wtd_fee_apy}% base + {_wtd_rwd_apy}% reward = {_wtd_total}% total today"
        else:
            _target_label = f"~{_wtd_fee_apy}% base yield (sustainable after July 2026)"

        with st.expander(f"{pcfg['label']} — {_target_label}"):
            view = st.radio("View as", ["Cards", "Table"], key=f"view_{p}", horizontal=True)
            if view == "Cards":
                for i, opp in enumerate(opps[:6]):
                    render_opportunity_card(opp, i, pcol, portfolio_size, w)
            else:
                rows = []
                for opp in opps[:8]:
                    kf         = float(opp.get("kelly_fraction", 0))
                    grade, _   = risk_score_to_grade(opp.get("risk_score", 5))
                    fee_apy    = float(opp.get("fee_apy", 0))
                    reward_apy = float(opp.get("reward_apy", 0))

                    # Reward badge: hide when tiny or expired; warn when expiring
                    if reward_apy < _reward_hide_pct or _days_left <= _reward_gray_days:
                        reward_label = "—"
                    elif _days_left <= _reward_warn_days:
                        reward_label = f"{reward_apy:.1f}% ⚠"
                    else:
                        reward_label = f"{reward_apy:.1f}%"

                        # Sustainability classification
                    _fee_a   = float(opp.get("fee_apy", 0))
                    _rwd_a   = float(opp.get("reward_apy", 0))
                    _sust    = compute_real_yield_ratio(_fee_a + _rwd_a, _rwd_a)
                    _sust_lbl = {"SUSTAINABLE": "✅ Sustainable", "MIXED": "⚡ Mixed", "EMISSION_DEPENDENT": "🔴 Incentive"}.get(_sust["classification"], "—")

                    # IL estimate %
                    _il_est  = float(opp.get("il_estimate_pct", 0.0))
                    _il_str  = f"{(opp.get('il_risk') or '—').upper()} (~{_il_est:.1f}%)" if _il_est > 0 else (opp.get("il_risk") or "—").upper()

                    # Audit info
                    _proto_k = str(opp.get("protocol", "")).lower().split()[0]
                    _aud     = PROTOCOL_AUDITS.get(_proto_k, {})
                    _aud_lbl = f"🛡 {_aud['auditors'][0]}" if _aud.get("auditors") else "—"

                    # Protocol URL
                    _p_url   = (_PROTOCOLS_CFG.get(_proto_k) or {}).get("url", "")

                    rows.append({
                        "Protocol":      opp.get("protocol", "—"),
                        "Pool / Asset":  opp.get("asset_or_pool", "—"),
                        "Base APY":      f"{fee_apy:.1f}%",
                        "Reward Bonus":  reward_label,
                        "Total APY":     f"{fee_apy + reward_apy:.1f}%",
                        "Grade":         grade,
                        "Price Risk":    _il_str,
                        "Yield Type":    _sust_lbl,
                        "Audit":         _aud_lbl,
                        "APY Range":     f"{opp.get('apy_low', 0):.0f}–{opp.get('apy_high', 0):.0f}%",
                        "Suggested $":   f"${kf * portfolio_size:,.0f}" if portfolio_size > 0 else "—",
                        "Alloc %":       f"{kf * 100:.0f}%",
                        "Action":        opp.get("action", opp.get("plain_english", "—")),
                        "Protocol URL":  _p_url,
                    })

                df_all = pd.DataFrame(rows)

                if _user_level == "beginner":
                    # Beginners: simple columns, no jargon
                    beginner_cols = ["Protocol", "Pool / Asset", "Base APY", "Grade", "Price Risk", "Yield Type", "Suggested $"]
                    st.dataframe(df_all[[c for c in beginner_cols if c in df_all.columns]], width='stretch', hide_index=True)
                    st.caption("Grade: A = safest, F = riskiest.  Yield Type: Sustainable = fee income that continues after rewards end.")
                elif _user_level == "intermediate":
                    inter_cols = ["Protocol", "Pool / Asset", "Base APY", "Reward Bonus", "Total APY", "Grade", "Price Risk", "Yield Type", "Audit", "Suggested $"]
                    st.dataframe(df_all[[c for c in inter_cols if c in df_all.columns]], width='stretch', hide_index=True)
                    if _days_left <= _reward_warn_days:
                        st.caption("⚠ = Reward bonus expires soon. Base APY continues indefinitely.")
                else:
                    # Advanced: full table (hide Protocol URL from display — too wide; show in tooltip)
                    adv_cols = ["Protocol", "Pool / Asset", "Base APY", "Reward Bonus", "Total APY", "Grade", "Price Risk", "Yield Type", "Audit", "APY Range", "Suggested $", "Alloc %", "Action"]
                    st.dataframe(df_all[[c for c in adv_cols if c in df_all.columns]], width='stretch', hide_index=True)
                    if _days_left <= _reward_warn_days:
                        st.caption(f"⚠ Reward Bonus expires in {_days_left} days. Alloc % weighted on Base APY only to survive post-July 2026.")

            # Portfolio footer: verifiable weighted APY numbers
            _footer_parts = [f"Weighted base yield: **{_wtd_fee_apy:.1f}%**"]
            if _wtd_rwd_apy >= _reward_hide_pct and _days_left > _reward_gray_days:
                _footer_parts.append(f"Reward bonus today: **{_wtd_rwd_apy:.1f}%**")
                _footer_parts.append(f"Total today: **{_wtd_total:.1f}%**")
            if portfolio_size > 0:
                _footer_parts.append(f"Monthly income: **${portfolio_size * _wtd_total / 100 / 12:,.0f}**")
            st.markdown(
                "<div style='margin-top:8px; padding:8px 12px; background:rgba(0,212,170,0.07); "
                "border-radius:6px; border-left:3px solid #00d4aa; font-size:0.82rem;'>"
                + "  ·  ".join(_footer_parts) + "</div>",
                unsafe_allow_html=True,
            )
            st.caption(pcfg.get("description", ""))

    # ─── Correlated Risk Warning (Item 7) ────────────────────────────────────────
    # Warn when 2+ recommended pools share the same underlying dependency.
    _cur_opps = model_data.get(profile) or []
    _cur_protos = [str(o.get("protocol", "")).lower().split()[0] for o in _cur_opps[:6]]
    _dep_counts: dict = {}
    for _cp in _cur_protos:
        _dep = PROTOCOL_DEPENDENCIES.get(_cp, {})
        for _dep_key, _dep_val in _dep.items():
            if _dep_val:
                _dep_counts[_dep_key] = _dep_counts.get(_dep_key, []) + [_cp]
    _dep_warnings = []
    _dep_labels = {
        "fxrp_collateral": "FXRP collateral risk",
        "ftso_oracle":      "FTSO oracle dependency",
        "fxrp_liquidity":   "FXRP liquidity exposure",
    }
    for _dk, _dprotos in _dep_counts.items():
        if len(_dprotos) >= 2:
            _dep_warnings.append(
                f"**{_dep_labels.get(_dk, _dk)}**: "
                + ", ".join(p.capitalize() for p in _dprotos[:4])
                + " all share this exposure."
            )
    if _dep_warnings:
        with st.expander("⚠ Correlated Risk Alert — read before investing", expanded=False):
            if _user_level == "beginner":
                st.warning(
                    "Some of the recommended pools are connected to the same underlying asset. "
                    "If that asset has a problem, multiple positions could be affected at once."
                )
            else:
                for _dw in _dep_warnings:
                    st.warning(_dw)
                st.caption(
                    "Correlated exposure means a single failure (FXRP depeg, FTSO oracle outage) "
                    "could simultaneously affect multiple portfolio positions. "
                    "Consider spreading some allocation to uncorrelated protocols (e.g. Clearpool stables)."
                )

    # ─── V3 Range Suggestions (Item 8) ───────────────────────────────────────────
    # For V3 concentrated liquidity pools, show a computed suggested price range.
    _v3_suggestions = []
    for _vo in (_cur_opps[:6]):
        _is_v3   = _vo.get("is_v3", False) or str(_vo.get("protocol", "")).lower() in ("sparkdex", "enosys")
        _v3_pool = _vo.get("asset_or_pool", "")
        _v3_kf   = float(_vo.get("kelly_fraction", 0))
        if _is_v3 and _v3_kf > 0 and _v3_pool:
            # Use a typical 3% daily vol for Flare ecosystem pairs
            _vol_map = {"WFLR": 4.5, "FXRP": 3.5, "sFLR": 3.0, "stXRP": 2.5, "USD0": 0.2, "USDT0": 0.2}
            _tokens_in_pool = [t.strip() for t in _v3_pool.replace("-", "/").split("/")]
            _avg_vol = sum(_vol_map.get(t, 3.0) for t in _tokens_in_pool) / max(len(_tokens_in_pool), 1)
            # Placeholder price of 1.0 — actual price would come from live data
            # We compute range width % rather than absolute prices for accuracy
            _rng = compute_v3_suggested_range(1.0, daily_vol_pct=_avg_vol, lookback_days=30, multiplier=1.5)
            if "error" not in _rng:
                _v3_suggestions.append({
                    "pool":      _v3_pool,
                    "protocol":  _vo.get("protocol", ""),
                    "range_pct": _rng["range_width_pct"],
                    "coverage":  _rng["coverage_pct"],
                    "daily_vol": _avg_vol,
                })
    if _v3_suggestions and _user_level in ("intermediate", "advanced"):
        with st.expander("📐 V3 LP Range Suggestions", expanded=False):
            st.caption("Suggested LP price ranges for concentrated liquidity pools in your allocation. Based on ±1.5σ of 30-day price volatility.")
            for _vs in _v3_suggestions:
                st.markdown(
                    f"**{_vs['protocol'].capitalize()} — {_vs['pool']}**: "
                    f"Set LP range to ±**{_vs['range_pct']:.1f}%** around current price "
                    f"(covers ~{_vs['coverage']:.0f}% of daily moves over 30 days). "
                    f"Est. daily volatility: {_vs['daily_vol']:.1f}%."
                )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ─── Portfolio Scenarios — Monte Carlo (Items 6, 9, 10) ──────────────────────
    render_section_header(
        "Portfolio Scenarios",
        "Monte Carlo simulation — Bear / Base / Bull case outcomes + probability range",
    )
    _mc_opps = model_data.get(profile) or []
    if _mc_opps:
        with st.spinner("Running portfolio simulation…"):
            _mc_result = run_portfolio_monte_carlo(_mc_opps, n_scenarios=2_000, days=365)

        if "error" not in _mc_result:
            # ── Item 6: Bull / Base / Bear named scenario metrics ──────────��──────
            _bear = _mc_result["bear_case"]
            _base = _mc_result["base_case"]
            _bull = _mc_result["bull_case"]
            _var  = _mc_result["var_95_annual"]
            _cvar = _mc_result["cvar_95_annual"]

            _sc1, _sc2, _sc3, _sc4 = st.columns(4)
            with _sc1:
                _bear_color = "#ef4444" if _bear < 0 else "#f59e0b"
                st.markdown(
                    f"<div style='text-align:center; padding:12px; background:rgba(239,68,68,0.07); "
                    f"border-radius:8px; border:1px solid rgba(239,68,68,0.2);'>"
                    f"<div style='font-size:0.72rem; color:#64748b; margin-bottom:4px;'>🐻 BEAR CASE</div>"
                    f"<div style='font-size:1.6rem; font-weight:800; color:{_bear_color};'>{_bear:+.1f}%</div>"
                    f"<div style='font-size:0.68rem; color:#475569; margin-top:2px;'>Worst 10% of scenarios</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _sc2:
                _base_color = "#22c55e" if _base >= 0 else "#ef4444"
                st.markdown(
                    f"<div style='text-align:center; padding:12px; background:rgba(34,197,94,0.07); "
                    f"border-radius:8px; border:1px solid rgba(34,197,94,0.2);'>"
                    f"<div style='font-size:0.72rem; color:#64748b; margin-bottom:4px;'>📊 BASE CASE</div>"
                    f"<div style='font-size:1.6rem; font-weight:800; color:{_base_color};'>{_base:+.1f}%</div>"
                    f"<div style='font-size:0.68rem; color:#475569; margin-top:2px;'>Median (50th percentile)</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _sc3:
                st.markdown(
                    f"<div style='text-align:center; padding:12px; background:rgba(0,212,170,0.07); "
                    f"border-radius:8px; border:1px solid rgba(0,212,170,0.2);'>"
                    f"<div style='font-size:0.72rem; color:#64748b; margin-bottom:4px;'>🐂 BULL CASE</div>"
                    f"<div style='font-size:1.6rem; font-weight:800; color:#00d4aa;'>{_bull:+.1f}%</div>"
                    f"<div style='font-size:0.68rem; color:#475569; margin-top:2px;'>Best 10% of scenarios</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            with _sc4:
                _var_color = "#ef4444" if _var < -10 else "#f59e0b"
                st.markdown(
                    f"<div style='text-align:center; padding:12px; background:rgba(239,68,68,0.05); "
                    f"border-radius:8px; border:1px solid rgba(239,68,68,0.15);'>"
                    f"<div style='font-size:0.72rem; color:#64748b; margin-bottom:4px;'>🛡 95% VALUE AT RISK</div>"
                    f"<div style='font-size:1.6rem; font-weight:800; color:{_var_color};'>{_var:+.1f}%</div>"
                    f"<div style='font-size:0.68rem; color:#475569; margin-top:2px;'>CVaR (Exp. Shortfall): {_cvar:+.1f}%</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            if _user_level == "beginner":
                _bear_text = f"lose {abs(_bear):.0f}%" if _bear < 0 else f"gain {_bear:.0f}%"
                st.info(
                    f"In the worst 10% of scenarios, this portfolio would **{_bear_text}** over 1 year. "
                    f"In the typical (median) scenario, it would **{'gain' if _base >= 0 else 'lose'} {abs(_base):.0f}%**. "
                    f"These are estimates based on current yields and historical volatility — not guarantees."
                )

            # ── Item 9: P15/P85 fan chart visual ──────────────────────────────────
            _p15 = _mc_result["p15"]
            _p85 = _mc_result["p85"]
            _p25 = _mc_result["p25"]
            _p75 = _mc_result["p75"]
            _p50 = _mc_result["p50"]

            # Build a simple fan chart: x = months (0-12), y = projected cumulative return
            _months = list(range(0, 13))
            def _monthly_path(annual_pct: float) -> list:
                monthly = (1 + annual_pct / 100) ** (1/12) - 1
                return [round(((1 + monthly) ** m - 1) * 100, 2) for m in _months]

            _fig_fan = go.Figure()
            # P15-P85 shaded band (outer)
            _fig_fan.add_trace(go.Scatter(
                x=_months + _months[::-1],
                y=_monthly_path(_p85) + _monthly_path(_p15)[::-1],
                fill="toself", fillcolor="rgba(0,212,170,0.10)",
                line=dict(width=0), name="P15–P85 range",
                hoverinfo="skip",
            ))
            # P25-P75 band (inner, darker)
            _fig_fan.add_trace(go.Scatter(
                x=_months + _months[::-1],
                y=_monthly_path(_p75) + _monthly_path(_p25)[::-1],
                fill="toself", fillcolor="rgba(0,212,170,0.20)",
                line=dict(width=0), name="P25–P75 range",
                hoverinfo="skip",
            ))
            # Bear case line (P10)
            _fig_fan.add_trace(go.Scatter(
                x=_months, y=_monthly_path(_bear),
                line=dict(color="#ef4444", width=1.5, dash="dot"),
                name=f"Bear Case ({_bear:+.1f}%)",
            ))
            # Median line (P50)
            _fig_fan.add_trace(go.Scatter(
                x=_months, y=_monthly_path(_p50),
                line=dict(color="#00d4aa", width=2.5),
                name=f"Base Case ({_p50:+.1f}%)",
            ))
            # Bull case line (P90)
            _fig_fan.add_trace(go.Scatter(
                x=_months, y=_monthly_path(_bull),
                line=dict(color="#22c55e", width=1.5, dash="dot"),
                name=f"Bull Case ({_bull:+.1f}%)",
            ))
            # Zero line
            _fig_fan.add_hline(y=0, line=dict(color="rgba(148,163,184,0.3)", width=1, dash="dot"))

            _fig_fan.update_layout(
                title=dict(text=f"{RISK_PROFILES[profile]['label']} — 1-Year Return Probability Range", font=dict(size=13, color="#94a3b8")),
                xaxis=dict(
                    title="Month",
                    tickvals=list(range(0, 13, 3)),
                    ticktext=["Now", "3m", "6m", "9m", "12m"],
                    gridcolor="rgba(148,163,184,0.1)",
                    tickfont=dict(size=10, color="#64748b"),
                ),
                yaxis=dict(
                    title="Return (%)",
                    ticksuffix="%",
                    gridcolor="rgba(148,163,184,0.1)",
                    zeroline=False,
                    tickfont=dict(size=10, color="#64748b"),
                ),
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                legend=dict(orientation="h", y=-0.18, x=0.0, font=dict(size=10, color="#94a3b8")),
                margin=dict(l=50, r=20, t=40, b=60),
                height=320,
            )
            st.plotly_chart(_fig_fan, width='stretch', config={"displayModeBar": False})
            st.caption(
                f"Fan chart: teal band = P15–P85 probability range (70% of simulations fall here). "
                f"Median (blue line) = base case. Based on {_mc_result['scenarios_run']:,} Monte Carlo scenarios."
            )

            if _user_level == "advanced":
                st.markdown(
                    f"**Simulation details:** {_mc_result['scenarios_run']:,} scenarios × 365 days · "
                    f"Mean annual return: {_mc_result['mean']:+.1f}% · Std dev: {_mc_result['std']:.1f}% · "
                    f"95% VaR: {_var:+.1f}% · CVaR: {_cvar:+.1f}% · "
                    f"Portfolio weighted APY input: {_mc_result['portfolio_apy_wtd']:.1f}%"
                )
    else:
        st.info("Run a scan first to generate portfolio scenario projections.")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


    # ─── APY Sparklines ───────────────────────────────────────────────────────���───

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
                    st.plotly_chart(fig, width='stretch', config={"displayModeBar": False})
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
            st.plotly_chart(fig_radar, width='stretch', config={"displayModeBar": False})
    
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
            st.dataframe(pd.DataFrame(_cmp_rows), width='stretch', hide_index=True)
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
                            st.dataframe(pd.DataFrame(chain_rows), width='stretch', hide_index=True)
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
    
    _MC_LOAD_TIMEOUT = 30  # seconds — aggregate timeout for all multi-chain fetches
    with st.spinner("Loading yield data from DeFiLlama..."):
        if demo_mode:
            _mc_pools = [
                {"project": "pendle", "chain": "Ethereum", "symbol": "PT-USDe 25Sep2026",
                 "apy": 11.8, "apyBase": 7.9, "apyReward": 3.9, "tvlUsd": 380_000_000, "audits": 3, "ilRisk": "no"},
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
            # Wrap in a 30s aggregate timeout so slow DeFiLlama responses never
            # block the page indefinitely.
            try:
                with ThreadPoolExecutor(max_workers=1) as _mc_ex:
                    _mc_fut = _mc_ex.submit(_cached_yields_pools, min_tvl_usd=5_000_000, max_results=20)
                    _mc_pools = _mc_fut.result(timeout=_MC_LOAD_TIMEOUT) or []
            except Exception:
                _mc_pools = []
                st.warning(
                    "Multi-Chain Pools data timed out or unavailable — showing cached data if available. "
                    "Refresh in 30 seconds.",
                    icon="⏱️",
                )
    
    if _mc_pools:
        # Beginner metric tooltips (#59) — show real computed stats from loaded data
        if not _pro_mode:
            _best_apy  = max((float(p.get("apy") or 0) for p in _mc_pools), default=0)
            _total_tvl = sum(float(p.get("tvlUsd") or 0) for p in _mc_pools)
            _il_yes    = sum(1 for p in _mc_pools if (p.get("ilRisk") or "no").lower() not in ("no", "none", ""))
            _tvl_disp  = (f"${_total_tvl/1e9:.1f}B" if _total_tvl >= 1e9
                          else f"${_total_tvl/1e6:.0f}M" if _total_tvl >= 1e6
                          else f"${_total_tvl:,.0f}")
            _tt_col1, _tt_col2, _tt_col3 = st.columns(3)
            with _tt_col1:
                st.metric(
                    "Top APY",
                    f"{_best_apy:.1f}%",
                    help="Annual Percentage Yield — the yearly return you'd earn. Includes both trading fees and token rewards. Higher isn't always better — check the risk score.",
                )
            with _tt_col2:
                st.metric(
                    "Total TVL",
                    _tvl_disp,
                    help="Total Value Locked — how much money is deposited in this protocol. Higher TVL generally means more trust and liquidity.",
                )
            with _tt_col3:
                st.metric(
                    "IL Risk Pools",
                    f"{_il_yes} of {len(_mc_pools)}",
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
                "Agent":       _agent_executable(p.get("project", ""), p.get("chain", "")),
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
            st.dataframe(pd.DataFrame(_paged_rows), width='stretch', hide_index=True)
        else:
            st.dataframe(pd.DataFrame(_mc_rows), width='stretch', hide_index=True)
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
            st.dataframe(pd.DataFrame(_pendle_rows), width='stretch', hide_index=True)
    
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
                st.plotly_chart(_fig_yc, width='stretch', config={"displayModeBar": False})
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
                # If Streamlit served a stale empty result, bust the cache and retry once
                if not (_meteora or {}).get("pools"):
                    _cached_meteora_yields.clear()
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
            st.dataframe(pd.DataFrame(_k_rows), width='stretch', hide_index=True)
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
            st.dataframe(pd.DataFrame(_m_rows), width='stretch', hide_index=True)
            _m_tvl = float((_meteora or {}).get("total_tvl") or 0)
            st.caption(f"Total Meteora TVL scanned: ${_m_tvl/1e6:.1f}M · APY estimated from 24h volume × fee rate. Source: GeckoTerminal.")
        else:
            st.info("Meteora data unavailable.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
with _tab_intel:
    # ── TVL Change Alerts (#79) ───────────────────────────────────────────────
    
    render_section_header(
        "TVL Change Alerts",
        "Protocols with significant 24h TVL changes — >5% drop may indicate exploit or capital migration",
    )
    
    _alert_slugs = ["kinetic-finance", "clearpool-lending", "morpho", "aave-v3", "eigenlayer"]
    _tvl_alerts  = []
    
    # OPT-38: Fetch all 5 TVL alerts in parallel
    def _fetch_tvl_alert(slug: str) -> "dict | None":
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
        st.dataframe(pd.DataFrame(_ul_rows), width='stretch', hide_index=True)
        st.caption(
            "Large unlocks can create sell pressure. "
            "Cliff unlocks (all-at-once) are higher risk than linear unlocks. "
            "CRITICAL = amount >= 10% supply or <= 7 days away."
        )
    else:
        st.success("✓ No major token unlocks in the next 30 days.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
with _tab_yield:
    
    # ── Flare Network Pool Discovery ───────────────────────────────────────────────
    # Scans GeckoTerminal for every active pool on Flare — including protocols
    # that DeFiLlama has not yet indexed — so nothing on-chain is missed.
    
    render_section_header(
        "Flare Network — Full Pool Discovery",
        "All active pools on Flare chain via GeckoTerminal · sorted by TVL · auto-discovers new protocols",
    )
    
    with st.spinner("Scanning Flare network pools…"):
        try:
            _fg_pools = _cached_flare_gecko_pools()
        except Exception:
            _fg_pools = []
    
    if _fg_pools:
        # Split known vs newly discovered protocols
        _fg_known = [p for p in _fg_pools if not p["is_new"]]
        _fg_new   = [p for p in _fg_pools if p["is_new"]]
    
        if _fg_new:
            st.info(
                f"**{len(_fg_new)} pool(s) from protocols not in our tracked list** — "
                "these may be newly launched DEXes or vaults on Flare. "
                "Expand below to review."
            )
    
        # ── Summary metrics row
        _fg_total_tvl = sum(p["tvl_usd"] for p in _fg_pools)
        _fg_dex_count = len({p["dex_id"] for p in _fg_pools})
        _fg_c1, _fg_c2, _fg_c3, _fg_c4 = st.columns(4)
        with _fg_c1:
            st.metric("Pools found", len(_fg_pools))
        with _fg_c2:
            st.metric("Unique DEXes", _fg_dex_count)
        with _fg_c3:
            st.metric("Total TVL", f"${_fg_total_tvl/1e6:.1f}M")
        with _fg_c4:
            st.metric("New protocols", len({p["dex_id"] for p in _fg_new}))
    
        # ── Full pool table in expander
        with st.expander("All Flare pools (click to expand)", expanded=False):
            _fg_df = pd.DataFrame([{
                "Pool":        p["symbol"],
                "DEX":         p["dex_name"],
                "TVL ($)":     p["tvl_usd"],
                "24h Vol ($)": p["vol_24h_usd"],
                "Fee %":       p["fee_rate_pct"],
                "APY est %":   p["apy_est"],
                "New?":        "🆕" if p["is_new"] else "",
            } for p in _fg_pools])
            st.dataframe(
                _fg_df.style.format({
                    "TVL ($)":     "${:,.0f}",
                    "24h Vol ($)": "${:,.0f}",
                    "Fee %":       "{:.3f}%",
                    "APY est %":   "{:.1f}%",
                }),
                width='stretch',
                hide_index=True,
            )
            st.caption(
                "APY estimated as (24h volume × fee rate / TVL) × 365. "
                "Source: GeckoTerminal · refreshed every 15 min."
            )
    
        # ── New protocol alert cards (if any)
        if _fg_new:
            st.markdown("#### Newly Discovered Protocols")
            for _p in _fg_new[:10]:
                st.markdown(
                    f"<div class='opp-card' style='border-left:3px solid #f59e0b;'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
                    f"<div><span style='font-weight:700; color:#f1f5f9;'>🆕 {_p['dex_name']}</span>"
                    f"<span style='color:#475569; margin:0 6px;'>·</span>"
                    f"<span style='color:#94a3b8; font-size:0.9rem;'>{_p['symbol']}</span></div>"
                    f"<div style='display:flex; gap:12px; font-size:0.82rem;'>"
                    f"<span style='color:#a78bfa; font-weight:700;'>{_p['apy_est']:.0f}% est. APY</span>"
                    f"<span style='color:#f1f5f9; font-weight:700;'>TVL ${_p['tvl_usd']/1e6:.2f}M</span>"
                    f"</div></div>"
                    f"<div style='color:#94a3b8; font-size:0.85rem; margin-top:6px;'>"
                    f"Pool address: <code>{_p['pool_address'][:12]}…</code> · "
                    f"24h vol: ${_p['vol_24h_usd']:,.0f} · Fee: {_p['fee_rate_pct']:.3f}%"
                    f"</div></div>",
                    unsafe_allow_html=True,
                )
            st.caption("New protocol = DEX slug not in our monitored list. Verify legitimacy before investing.")
    else:
        st.info("No Flare pool data available — GeckoTerminal may be rate-limiting. Try again in 60 seconds.")
    
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
                _flows = _cached_bridge_flows()
    
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
        st.dataframe(pd.DataFrame(_gy_rows), width='stretch', hide_index=True)
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
            st.dataframe(pd.DataFrame(_aero_rows), width='stretch', hide_index=True)
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
            st.dataframe(pd.DataFrame(_morpho_rows), width='stretch', hide_index=True)
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
            _sp_pid    = _sp.get("pool_id", "")

            # Attempt real historical APY from DeFiLlama /chart/{pool_id}
            # Falls back to synthetic 7-point interpolation if unavailable.
            _spark_real  = fetch_pool_apy_history(_sp_pid, days=30) if _sp_pid else []
            _is_real_data = len(_spark_real) >= 7
            if _is_real_data:
                # Use last 30 days of real data; x-axis is dates, y-axis is APY
                _spark_x    = [h["timestamp"] for h in _spark_real]
                _spark_vals = [h["apy"] for h in _spark_real]
                _data_label = f"{len(_spark_real)}d real"
            else:
                # Synthetic: 7-point linear interpolation between apy_7d and apy
                _spark_x    = None
                _spark_vals = [
                    round(_sp_apy7d + (_sp_apy - _sp_apy7d) * (i / 6), 2)
                    for i in range(7)
                ]
                _data_label = "7d est."

            _trending_up = _spark_vals[-1] >= _spark_vals[0] if _spark_vals else True
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
                _spark_trace_kwargs = dict(
                    y=_spark_vals,
                    mode="lines",
                    line=dict(color=_sp_line_col, width=2),
                    fill="tozeroy",
                    fillcolor=_sp_fill_col,
                    hovertemplate="%{x}: %{y:.2f}%<extra></extra>" if _is_real_data
                                  else "%{y:.2f}%<extra></extra>",
                )
                if _is_real_data:
                    _spark_trace_kwargs["x"] = _spark_x
                _fig_sp.add_trace(go.Scatter(**_spark_trace_kwargs))
                _fig_sp.update_layout(
                    plot_bgcolor="rgba(0,0,0,0)",
                    paper_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(visible=False),
                    yaxis=dict(visible=False),
                    margin=dict(l=0, r=0, t=0, b=0),
                    height=60,
                    showlegend=False,
                )
                st.plotly_chart(_fig_sp, width='stretch', config={"displayModeBar": False})
                _dir_sym = "▲" if _trending_up else "▼"
                st.markdown(
                    f"<div style='text-align:center;font-size:0.73rem;color:{_sp_line_col};margin-top:-10px'>"
                    f"{_dir_sym} {_sp_apy:.2f}% APY "
                    f"<span style='color:#475569;font-size:0.65rem'>({_data_label})</span></div>",
                    unsafe_allow_html=True,
                )
            # Add new row of columns every 3 pools
            if _col_idx == 2 and _si < len(_sp_pools) - 1:
                _sp_cols = st.columns(min(len(_sp_pools) - _si - 1, 3))
    else:
        st.info("Load global yield pools above to see sparklines.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
with _tab_intel:
    # ══════════════════════════════════════════════════════════════════════════════
    # D1 — PROTOCOL RISK SCORE BADGES
    # DeFiLlama hack history + audit count → A-F grade badge per protocol
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "Protocol Risk Grades",
        "A–F safety grade based on hack history, audit count, TVL, and age — higher grade = safer",
    )
    
    render_what_this_means(
        "These grades tell you how safe each protocol is. "
        "'A' means very safe — audited, never hacked, large TVL. "
        "'F' means high risk — multiple hacks or no audits. "
        "Always check the grade before putting money into a protocol.",
        title="What do these grades mean?",
        intermediate_message="Safety grade: A–F derived from hack history, audit count, TVL stability, and protocol age (DeFiLlama data).",
    )
    
    _D1_PROTOCOLS = [
        ("aave-v3",          "Aave v3",          "Lending",           5),
        ("morpho",           "Morpho",           "Lending",           4),
        ("uniswap-v3",       "Uniswap v3",       "DEX",               4),
        ("compound-v3",      "Compound v3",      "Lending",           4),
        ("lido",             "Lido",             "Liquid Staking",    3),
        ("eigenlayer",       "EigenLayer",       "Restaking",         2),
        ("pendle",           "Pendle Finance",   "Yield Tokenization",3),
        ("ethena",           "Ethena",           "Delta-Neutral",     2),
        ("aerodrome-v2",     "Aerodrome",        "DEX",               2),
        ("curve-dex",        "Curve",            "DEX",               3),
    ]
    
    def _d1_grade(hack_count: int, funds_lost_m: float, audit_count: int) -> tuple:
        """Return (letter_grade, color, description) from risk factors."""
        score = 100
        score -= min(hack_count * 25, 60)           # -25 per hack, max -60
        score -= min(int(funds_lost_m / 10) * 5, 30) # -5 per $10M lost, max -30
        score += min(audit_count * 5, 20)            # +5 per audit, max +20
        score = max(0, min(100, score))
        if score >= 85:  return "A", "#22c55e",  "Excellent — no hacks, audited, battle-tested"
        if score >= 70:  return "B", "#84cc16",  "Good — minor incidents or limited audit history"
        if score >= 55:  return "C", "#f59e0b",  "Moderate — some risk factors present"
        if score >= 35:  return "D", "#f97316",  "High risk — significant hack history"
        return             "F", "#ef4444",  "Very high risk — multiple large hacks"
    
    @st.cache_data(ttl=86400, show_spinner=False)
    def _load_d1_risk_scores() -> list:
        results = []
        for slug, name, category, audit_est in _D1_PROTOCOLS:
            try:
                rs = fetch_protocol_risk_score(slug)
                grade, color, desc = _d1_grade(
                    rs.get("hack_count", 0),
                    rs.get("funds_lost_m", 0.0),
                    rs.get("audit_count", audit_est),
                )
                results.append({
                    "slug": slug, "name": name, "category": category,
                    "hack_count": rs.get("hack_count", 0),
                    "funds_lost_m": rs.get("funds_lost_m", 0.0),
                    "audit_count": rs.get("audit_count", audit_est),
                    "grade": grade, "color": color, "desc": desc,
                })
            except Exception:
                grade, color, desc = _d1_grade(0, 0.0, audit_est)
                results.append({
                    "slug": slug, "name": name, "category": category,
                    "hack_count": 0, "funds_lost_m": 0.0, "audit_count": audit_est,
                    "grade": grade, "color": color, "desc": desc,
                })
        return results
    
    with st.spinner("Computing protocol risk grades…"):
        _d1_scores = _load_d1_risk_scores()
    
    if _d1_scores:
        # Display in a grid — 5 per row
        _d1_cols_per_row = 5
        for _d1_row_start in range(0, len(_d1_scores), _d1_cols_per_row):
            _d1_row_items = _d1_scores[_d1_row_start: _d1_row_start + _d1_cols_per_row]
            _d1_cols = st.columns(len(_d1_row_items))
            for _ci, _item in enumerate(_d1_row_items):
                with _d1_cols[_ci]:
                    _hacks_txt  = f"{_item['hack_count']} hack(s)" if _item["hack_count"] else "No hacks"
                    _audits_txt = f"{_item['audit_count']} audit(s)"
                    st.markdown(
                        f"<div style='background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.07);"
                        f"border-top:3px solid {_item['color']};border-radius:8px;"
                        f"padding:10px 12px;margin-bottom:8px;text-align:center'>"
                        f"<div style='font-size:2rem;font-weight:800;color:{_item['color']};line-height:1'>"
                        f"{_item['grade']}</div>"
                        f"<div style='font-size:0.78rem;font-weight:600;color:#e2e8f0;margin-top:4px'>"
                        f"{_html.escape(_item['name'])}</div>"
                        f"<div style='font-size:0.65rem;color:#64748b;margin-top:2px'>"
                        f"{_html.escape(_item['category'])}</div>"
                        f"<div style='font-size:0.65rem;color:#475569;margin-top:6px;border-top:1px solid rgba(255,255,255,0.05);padding-top:4px'>"
                        f"{_hacks_txt} · {_audits_txt}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        st.caption(
            "Grade A = score ≥85 (no hacks, audited). "
            "F = score <35 (multiple large hacks). "
            "Audit count estimated from DeFiLlama data. "
            "Source: DeFiLlama /hacks · cached 24 h."
        )
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
with _tab_yield:
    # ══════════════════════════════════════════════════════════════════════════════
    # D2 — IL BREAK-EVEN CALCULATOR
    # Interactive: entry price → current price → IL% → days to break-even
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "IL Break-Even Calculator",
        "How many days of fees does it take to recover from impermanent loss?",
    )
    
    render_what_this_means(
        "When you provide liquidity in a pool, price movements cause 'impermanent loss' (IL). "
        "This calculator tells you how much IL you've suffered and how many days it'll take "
        "for your fee income to cover it. If break-even is 200+ days, IL is a serious concern.",
        title="What is impermanent loss break-even?",
        intermediate_message="IL break-even = days at current fee APY to recover divergence losses. Formula: IL = 2√k/(1+k) − 1.",
    )
    
    with st.expander("Open IL Break-Even Calculator", expanded=False):
        _ilbe_c1, _ilbe_c2, _ilbe_c3 = st.columns(3)
        with _ilbe_c1:
            _ilbe_price_a0 = st.number_input(
                "Token A entry price ($)", min_value=0.0001, value=1.0, step=0.01, key="ilbe_pa0",
                help="Price of token A when you entered the pool.",
            )
            _ilbe_price_b0 = st.number_input(
                "Token B entry price ($)", min_value=0.0001, value=1.0, step=0.01, key="ilbe_pb0",
                help="Price of token B when you entered the pool.",
            )
        with _ilbe_c2:
            _ilbe_price_a1 = st.number_input(
                "Token A current price ($)", min_value=0.0001, value=1.2, step=0.01, key="ilbe_pa1",
                help="Current price of token A.",
            )
            _ilbe_price_b1 = st.number_input(
                "Token B current price ($)", min_value=0.0001, value=0.9, step=0.01, key="ilbe_pb1",
                help="Current price of token B.",
            )
        with _ilbe_c3:
            _ilbe_fee_apy = st.number_input(
                "Pool fee APY (%)", min_value=0.1, value=15.0, step=0.5, key="ilbe_fee_apy",
                help="Annual fee yield generated by the pool (base APY from fees only, not rewards).",
            )
            _ilbe_deposit = st.number_input(
                "Deposit amount ($)", min_value=0.0, value=10000.0, step=100.0, key="ilbe_dep",
            )
    
        # IL formula: IL = 2√k/(1+k) − 1 where k = price_ratio_change
        # k = (pa1/pb1) / (pa0/pb0)
        try:
            _ilbe_k  = (_ilbe_price_a1 / _ilbe_price_b1) / (_ilbe_price_a0 / _ilbe_price_b0)
            import math as _ilbe_math
            _ilbe_il = 2 * _ilbe_math.sqrt(_ilbe_k) / (1 + _ilbe_k) - 1   # negative number
            _ilbe_il_pct  = abs(_ilbe_il) * 100
            _ilbe_loss_usd = _ilbe_il_pct / 100 * _ilbe_deposit
    
            # Daily fee income
            _ilbe_daily_fee  = _ilbe_deposit * (_ilbe_fee_apy / 100) / 365
            _ilbe_breakeven_days = int(_ilbe_loss_usd / _ilbe_daily_fee) if _ilbe_daily_fee > 0 else 9999
    
            _il_color = "#22c55e" if _ilbe_il_pct < 1 else "#f59e0b" if _ilbe_il_pct < 5 else "#ef4444"
            _be_color = "#22c55e" if _ilbe_breakeven_days < 30 else "#f59e0b" if _ilbe_breakeven_days < 180 else "#ef4444"
            _be_verdict = (
                "Excellent — fees cover IL quickly" if _ilbe_breakeven_days < 30
                else "Acceptable — IL is manageable" if _ilbe_breakeven_days < 90
                else "Caution — IL takes a long time to recover" if _ilbe_breakeven_days < 365
                else "Warning — IL may never be recovered at this fee rate"
            )
    
            _r1, _r2, _r3, _r4 = st.columns(4)
            with _r1:
                st.metric("Price Ratio Change", f"{(_ilbe_k - 1) * 100:+.1f}%")
            with _r2:
                st.metric("Impermanent Loss", f"{_ilbe_il_pct:.2f}%",
                          delta=f"-${_ilbe_loss_usd:,.2f}", delta_color="inverse")
            with _r3:
                st.metric("Daily Fee Income", f"${_ilbe_daily_fee:.2f}")
            with _r4:
                st.metric("Break-Even Days", f"{_ilbe_breakeven_days:,}d",
                          help=f"Days at {_ilbe_fee_apy:.1f}% APY to recover ${_ilbe_loss_usd:,.2f} IL")
    
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.2);border-left:3px solid {_be_color};"
                f"border-radius:6px;padding:10px 14px;margin-top:8px;font-size:0.88rem'>"
                f"<span style='color:{_be_color};font-weight:700'>"
                f"{'▲ ' if _ilbe_breakeven_days < 90 else '■ ' if _ilbe_breakeven_days < 365 else '▼ '}"
                f"{_be_verdict}</span><br>"
                f"<span style='color:#64748b'>At {_ilbe_fee_apy:.1f}% fee APY you earn "
                f"${_ilbe_daily_fee:.2f}/day. You need <b style='color:{_be_color}'>"
                f"{_ilbe_breakeven_days:,} days</b> to recover ${_ilbe_loss_usd:,.2f} IL.</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        except Exception as _ilbe_err:
            st.warning(f"Calculator error — check inputs: {_ilbe_err}")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
with _tab_intel:
    # ══════════════════════════════════════════════════════════════════════════════
    # D3 — HACK HISTORY PANEL
    # DeFiLlama /hacks — top hacks by funds lost, category breakdown
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "DeFi Hack History",
        "Largest DeFi exploits on record — know where the money was lost and how",
    )
    
    render_what_this_means(
        "This panel shows the biggest DeFi hacks ever recorded. "
        "Understanding how protocols get hacked helps you avoid putting money "
        "into similar risks. Look for the 'technique' column — "
        "flash loan attacks and smart contract bugs are the most common.",
        title="Why look at hack history?",
        intermediate_message="Historical exploits by technique — use to assess smart contract risk vectors before allocating capital.",
    )
    
    with st.spinner("Loading hack history from DeFiLlama…"):
        _d3_hacks = [] if demo_mode else _cached_all_hacks()
    
    if demo_mode:
        _d3_hacks = [
            {"name": "Ronin Bridge",   "date": "2022-03-29", "funds_lost_usd": 624_000_000, "chain": "Ethereum",   "technique": "Private Key Compromise", "category": "Bridge"},
            {"name": "Poly Network",   "date": "2021-08-10", "funds_lost_usd": 611_000_000, "chain": "Multi-chain","technique": "Smart Contract Bug",       "category": "Bridge"},
            {"name": "BNB Chain",      "date": "2022-10-07", "funds_lost_usd": 566_000_000, "chain": "BNB",        "technique": "Bridge Exploit",           "category": "Bridge"},
            {"name": "FTX",            "date": "2022-11-11", "funds_lost_usd": 415_000_000, "chain": "Solana",     "technique": "Insider",                  "category": "Exchange"},
            {"name": "Wormhole",       "date": "2022-02-02", "funds_lost_usd": 320_000_000, "chain": "Solana",     "technique": "Smart Contract Bug",       "category": "Bridge"},
            {"name": "Nomad Bridge",   "date": "2022-08-01", "funds_lost_usd": 190_000_000, "chain": "Multi-chain","technique": "Smart Contract Bug",       "category": "Bridge"},
            {"name": "Euler Finance",  "date": "2023-03-13", "funds_lost_usd": 197_000_000, "chain": "Ethereum",   "technique": "Flash Loan",               "category": "Lending"},
            {"name": "Curve Finance",  "date": "2023-07-30", "funds_lost_usd": 61_000_000,  "chain": "Ethereum",   "technique": "Reentrancy",               "category": "DEX"},
        ]
    
    if _d3_hacks:
        # Category breakdown pie
        _d3_cats: dict = {}
        _d3_total_lost = 0.0
        for _h in _d3_hacks:
            _cat = _h.get("category") or "Unknown"
            _lost = float(_h.get("funds_lost_usd") or 0)
            _d3_cats[_cat] = _d3_cats.get(_cat, 0) + _lost
            _d3_total_lost += _lost
    
        _hack_col1, _hack_col2 = st.columns([2, 1])
        with _hack_col1:
            _d3_rows = []
            for _h in _d3_hacks[:20]:
                _lost_m = float(_h.get("funds_lost_usd") or 0) / 1e6
                _d3_rows.append({
                    "Protocol":     _h.get("name", "—"),
                    "Date":         _h.get("date", "—"),
                    "Funds Lost":   f"${_lost_m:.0f}M" if _lost_m >= 1 else f"${float(_h.get('funds_lost_usd',0)):,.0f}",
                    "Chain":        str(_h.get("chain") or "—")[:20],
                    "Technique":    str(_h.get("technique") or "—")[:30],
                    "Category":     str(_h.get("category") or "—"),
                })
            st.dataframe(pd.DataFrame(_d3_rows), width='stretch', hide_index=True)
            _total_lost_b = _d3_total_lost / 1e9
            st.caption(
                f"Top {len(_d3_rows)} hacks shown · Total lost: ${_total_lost_b:.1f}B · "
                "Source: DeFiLlama /hacks · cached 24 h."
            )
        with _hack_col2:
            if _d3_cats:
                _cat_labels = list(_d3_cats.keys())
                _cat_values = [_d3_cats[c] / 1e6 for c in _cat_labels]
                _fig_cats = go.Figure(data=[go.Pie(
                    labels=_cat_labels, values=_cat_values,
                    hole=0.55,
                    textinfo="percent",
                    marker=dict(colors=["#ef4444","#f97316","#f59e0b","#84cc16","#22c55e","#3b82f6","#8b5cf6","#ec4899"]),
                )])
                _fig_cats.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#94a3b8",
                    showlegend=True,
                    legend=dict(font=dict(size=9), bgcolor="rgba(0,0,0,0)"),
                    margin=dict(l=0, r=0, t=10, b=10),
                    height=240,
                    title=dict(text="By Category ($M)", font=dict(size=11, color="#64748b"), x=0.5),
                )
                st.plotly_chart(_fig_cats, width='stretch', config={"displayModeBar": False})
    else:
        st.info("Hack history unavailable — DeFiLlama API may be unreachable.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
    # ══════════════════════════════════════════════════════════════════════════════
    # D4 — PROTOCOL REVENUE TREND
    # DeFiLlama /summary/fees → 24h fee revenue vs 30-day daily average
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "Protocol Revenue Trend",
        "24h fee revenue vs 30-day daily average — growing revenue = healthier protocol",
    )
    
    render_what_this_means(
        "A protocol's fee revenue shows whether real users are actively using it. "
        "If fees are trending up, more trading is happening — that usually means higher yields too. "
        "Green = revenue above 30-day average. Red = below average (less activity).",
        title="Why does protocol revenue matter?",
        intermediate_message="Fee revenue trend: 24h vs 30d daily avg. >1.0× = above average activity = healthier yield sustainability.",
    )
    
    with st.spinner("Loading protocol revenue data…"):
        if demo_mode:
            _d4_rev = {
                "aave-v3":      {"fees_24h": 420_000, "fees_30d": 11_200_000, "trend": 1.13, "health": "GREEN"},
                "uniswap":      {"fees_24h": 2_800_000,"fees_30d": 73_000_000, "trend": 1.15, "health": "GREEN"},
                "lido":         {"fees_24h": 580_000,  "fees_30d": 16_200_000, "trend": 1.07, "health": "GREEN"},
                "compound-v3":  {"fees_24h": 48_000,   "fees_30d": 1_800_000,  "trend": 0.80, "health": "YELLOW"},
                "curve-dex":    {"fees_24h": 210_000,  "fees_30d": 9_100_000,  "trend": 0.69, "health": "YELLOW"},
                "pendle":       {"fees_24h": 95_000,   "fees_30d": 2_100_000,  "trend": 1.36, "health": "GREEN"},
                "morpho":       {"fees_24h": 31_000,   "fees_30d": 640_000,    "trend": 1.45, "health": "GREEN"},
                "aerodrome-v2": {"fees_24h": 810_000,  "fees_30d": 19_500_000, "trend": 1.24, "health": "GREEN"},
                "timestamp": "2026-04-02T00:00:00Z", "errors": [],
            }
        else:
            _d4_rev = _cached_protocol_revenue()
    
    _d4_items = [(k, v) for k, v in _d4_rev.items() if k not in ("timestamp", "errors") and isinstance(v, dict)]
    _d4_items.sort(key=lambda x: x[1].get("trend", 0), reverse=True)
    
    if _d4_items:
        _d4_names  = [k.replace("-", " ").title() for k, _ in _d4_items]
        _d4_trends = [v.get("trend", 0) for _, v in _d4_items]
        _d4_colors = [
            "#22c55e" if t > 0.9 else "#f59e0b" if t > 0.5 else "#ef4444"
            for t in _d4_trends
        ]
    
        _fig_d4 = go.Figure()
        _fig_d4.add_trace(go.Bar(
            x=_d4_names, y=_d4_trends,
            marker_color=_d4_colors,
            text=[f"{t:.2f}×" for t in _d4_trends],
            textposition="outside",
            hovertemplate="<b>%{x}</b><br>24h vs 30d avg: %{y:.2f}×<extra></extra>",
        ))
        _fig_d4.add_hline(y=1.0, line_dash="dash", line_color="#475569",
                          annotation_text="30d avg baseline", annotation_position="right")
        _fig_d4.update_layout(
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font_color="#94a3b8",
            xaxis=dict(gridcolor="rgba(148,163,184,0.1)"),
            yaxis=dict(title="24h fee / 30d daily avg", gridcolor="rgba(148,163,184,0.1)", tickformat=".2f"),
            height=280, margin=dict(l=40, r=20, t=30, b=60),
            showlegend=False,
        )
        st.plotly_chart(_fig_d4, width='stretch', config={"displayModeBar": False})
    
        # Summary table
        _d4_rows = []
        for k, v in _d4_items:
            _f24 = float(v.get("fees_24h") or 0)
            _f30 = float(v.get("fees_30d") or 0)
            _health = v.get("health", "—")
            _health_icon = "🟢" if _health == "GREEN" else "🟡" if _health == "YELLOW" else "🔴"
            _d4_rows.append({
                "Protocol":       k.replace("-", " ").title(),
                "24h Fees":       f"${_f24/1e3:.0f}K" if _f24 >= 1000 else f"${_f24:,.0f}",
                "30d Total Fees": f"${_f30/1e6:.2f}M" if _f30 >= 1e6 else f"${_f30/1e3:.0f}K",
                "Trend vs Avg":   f"{v.get('trend', 0):.2f}×",
                "Health":         f"{_health_icon} {_health}",
            })
        st.dataframe(pd.DataFrame(_d4_rows), width='stretch', hide_index=True)
        st.caption(
            "Trend = 24h fees ÷ (30d fees / 30). >1.0 = above average activity. "
            "Source: DeFiLlama /summary/fees · cached 1 hour."
        )
    else:
        st.info("Protocol revenue data unavailable.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
with _tab_yield:
    # ══════════════════════════════════════════════════════════════════════════════
    # D6 — YIELD GAP ALERT
    # Flag pools where current APY deviates significantly from 7d average
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "Yield Gap Alerts",
        "Pools where current APY deviates >20% from 7-day average — sudden spikes or crashes",
    )
    
    render_what_this_means(
        "When a pool's yield suddenly jumps much higher than normal, it's often a sign of "
        "a one-time event, extra token rewards, or a short-term anomaly. "
        "A sudden drop in yield can mean the protocol is losing users. "
        "These alerts help you spot unusual changes before they disappear.",
        title="What do yield gap alerts mean?",
        intermediate_message="APY deviation >20% from 7d avg — flag for reward spikes, liquidity exits, or protocol stress events.",
    )
    
    _d6_all_pools = list(_gy_display or []) + list(_mc_pools or [])
    _d6_gap_alerts = []
    for _d6p in _d6_all_pools:
        try:
            _d6_cur  = float(_d6p.get("apy") or _d6p.get("apy", 0))
            _d6_avg  = float(_d6p.get("apy_7d") or _d6p.get("apy7d") or _d6_cur)
            if _d6_avg <= 0 or _d6_cur <= 0:
                continue
            _d6_gap_pct = (_d6_cur - _d6_avg) / _d6_avg * 100
            if abs(_d6_gap_pct) >= 20:
                _d6_gap_alerts.append({
                    "proto":    str(_d6p.get("protocol") or _d6p.get("project") or "—"),
                    "pool":     str(_d6p.get("symbol") or "—"),
                    "chain":    str(_d6p.get("chain") or "—"),
                    "current":  _d6_cur,
                    "avg_7d":   _d6_avg,
                    "gap_pct":  _d6_gap_pct,
                })
        except Exception:
            continue
    
    _d6_gap_alerts.sort(key=lambda x: abs(x["gap_pct"]), reverse=True)
    
    if _d6_gap_alerts:
        for _d6a in _d6_gap_alerts[:10]:
            _gap   = _d6a["gap_pct"]
            _dir   = "▲ SPIKE" if _gap > 0 else "▼ DROP"
            _col   = "#22c55e" if _gap > 0 else "#ef4444"
            _badge = "▲ BUY signal?" if _gap > 40 else "▼ Exit signal?" if _gap < -40 else "■ Watch closely"
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.2);border-left:3px solid {_col};"
                f"border-radius:6px;padding:8px 12px;margin-bottom:6px;font-size:0.85rem'>"
                f"<b style='color:{_col}'>{_dir} {abs(_gap):.0f}%</b> · "
                f"<b>{_html.escape(_d6a['proto'].replace('-',' ').title())}</b> "
                f"<span style='color:#64748b'>{_html.escape(_d6a['pool'])} · {_html.escape(_d6a['chain'])}</span> · "
                f"Now: <b>{_d6a['current']:.1f}%</b> vs 7d avg: <b>{_d6a['avg_7d']:.1f}%</b> · "
                f"<span style='color:#f59e0b'>{_badge}</span>"
                f"</div>",
                unsafe_allow_html=True,
            )
        st.caption(f"{len(_d6_gap_alerts)} pools with >20% APY deviation from 7-day average.")
    else:
        st.success("▲ No major yield gap anomalies detected — all pools near their 7-day average.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
    # ══════════════════════════════════════════════════════════════════════════════
    # D7 — CONCENTRATED LIQUIDITY RANGE MONITOR
    # Interactive: enter tick range → see if position is in-range at current price
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "Concentrated Liquidity Range Monitor",
        "Uniswap v3 / Aerodrome CL — check if your position is in-range and earning fees",
    )

    # ── Item 39: LP In-Range Alert Tracker ───────────────────────────────────
    # Persistent session-state list of tracked CL positions with auto-alert banners
    if "cl_tracked_positions" not in st.session_state:
        st.session_state["cl_tracked_positions"] = []

    _tracked = st.session_state["cl_tracked_positions"]
    if _tracked:
        _oor_count = 0
        for _tp in _tracked:
            _tp_cur  = float(_tp.get("current_price", 0))
            _tp_low  = float(_tp.get("range_low", 0))
            _tp_high = float(_tp.get("range_high", float("inf")))
            _tp_in   = _tp_low <= _tp_cur <= _tp_high
            if not _tp_in:
                _oor_count += 1
                _dist = ((_tp_low - _tp_cur) / _tp_cur * 100 if _tp_cur < _tp_low
                         else (_tp_cur - _tp_high) / _tp_cur * 100)
                st.warning(
                    f"**{_html.escape(_tp.get('pair', 'Unknown'))}** out of range — "
                    f"price ${_tp_cur:,.2f} is {_dist:.1f}% outside range "
                    f"(${_tp_low:,.0f}–${_tp_high:,.0f}). Fee income stopped.",
                    icon="🚨",
                )
        if _oor_count == 0:
            st.success(
                f"All {len(_tracked)} tracked CL position(s) are in range and earning fees.",
                icon="✅",
            )
        with st.expander(f"Manage {len(_tracked)} tracked position(s)", expanded=False):
            for _i, _tp in enumerate(_tracked):
                _tc1, _tc2 = st.columns([5, 1])
                with _tc1:
                    _tp_in2 = (float(_tp.get("range_low", 0))
                               <= float(_tp.get("current_price", 0))
                               <= float(_tp.get("range_high", 0)))
                    _st_sym = "▲ In range" if _tp_in2 else "▼ Out of range"
                    st.write(
                        f"**{_tp.get('pair')}** | "
                        f"${_tp.get('range_low'):,.0f}–${_tp.get('range_high'):,.0f} | "
                        f"Current: ${_tp.get('current_price'):,.2f} | {_st_sym}"
                    )
                with _tc2:
                    if st.button("Remove", key=f"cl_remove_{_i}"):
                        st.session_state["cl_tracked_positions"].pop(_i)
                        st.rerun()
    
    render_what_this_means(
        "Concentrated liquidity (CL) pools let you earn higher fees but ONLY while the price "
        "stays inside your chosen range. If price moves outside your range, you stop earning — "
        "and you're stuck holding 100% of one token. This tool shows whether you're in-range right now.",
        title="What is a concentrated liquidity range?",
        intermediate_message="CL positions earn fees only while price is within your tick range — out-of-range = 0 fee income, full single-asset exposure.",
    )
    
    with st.expander("Open CL Range Monitor", expanded=False):
        _cl_c1, _cl_c2 = st.columns(2)
        with _cl_c1:
            _cl_range_low  = st.number_input("Range LOW price ($)",  min_value=0.0001, value=1500.0, step=10.0, key="cl_low")
            _cl_range_high = st.number_input("Range HIGH price ($)", min_value=0.0001, value=2500.0, step=10.0, key="cl_high")
            _cl_deposit    = st.number_input("Position size ($)",    min_value=0.0, value=10000.0, step=100.0, key="cl_dep")
        with _cl_c2:
            _cl_current    = st.number_input("Current price ($)",    min_value=0.0001, value=1900.0, step=10.0, key="cl_cur")
            _cl_fee_apy    = st.number_input("Pool fee APY (in-range) (%)", min_value=0.1, value=40.0, step=1.0, key="cl_apy")
            _cl_token_pair = st.text_input("Pool (for reference)", value="ETH/USDC", key="cl_pair")
    
        if _cl_range_low < _cl_range_high:
            _cl_in_range = _cl_range_low <= _cl_current <= _cl_range_high
            _cl_status_col   = "#22c55e" if _cl_in_range else "#ef4444"
            _cl_status_txt   = "▲ IN RANGE — earning fees now" if _cl_in_range else "▼ OUT OF RANGE — not earning fees"
            _cl_status_icon  = "✅" if _cl_in_range else "🚨"
    
            # Distance to nearest boundary
            if _cl_in_range:
                _dist_low  = abs(_cl_current - _cl_range_low)  / _cl_current * 100
                _dist_high = abs(_cl_range_high - _cl_current) / _cl_current * 100
                _nearest   = min(_dist_low, _dist_high)
                _detail    = f"{_nearest:.1f}% to nearest boundary"
            else:
                if _cl_current < _cl_range_low:
                    _dist_to_re_enter = (_cl_range_low - _cl_current) / _cl_current * 100
                    _detail = f"Price needs to rise {_dist_to_re_enter:.1f}% to re-enter range"
                else:
                    _dist_to_re_enter = (_cl_current - _cl_range_high) / _cl_current * 100
                    _detail = f"Price needs to fall {_dist_to_re_enter:.1f}% to re-enter range"
    
            # Range width
            _range_width_pct = (_cl_range_high - _cl_range_low) / _cl_range_low * 100
            _daily_fees_if_in = _cl_deposit * (_cl_fee_apy / 100) / 365 if _cl_in_range else 0.0
    
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.07);"
                f"border-top:3px solid {_cl_status_col};border-radius:8px;padding:14px 16px;margin:8px 0'>"
                f"<div style='font-size:1.1rem;font-weight:700;color:{_cl_status_col}'>"
                f"{_cl_status_icon} {_cl_status_txt}</div>"
                f"<div style='color:#94a3b8;font-size:0.85rem;margin-top:6px'>{_detail}</div>"
                f"<div style='display:flex;gap:16px;margin-top:10px;flex-wrap:wrap'>"
                f"<span style='color:#64748b'>Range: ${_cl_range_low:,.0f} – ${_cl_range_high:,.0f} "
                f"({_range_width_pct:.0f}% wide)</span>"
                f"<span style='color:#64748b'>Current: ${_cl_current:,.2f}</span>"
                f"{'<span style=\"color:#22c55e\">Daily fees: $' + f'{_daily_fees_if_in:.2f}' + '</span>' if _cl_in_range else ''}"
                f"</div></div>",
                unsafe_allow_html=True,
            )
    
            # Visual range bar
            _bar_min = min(_cl_range_low * 0.8, _cl_current * 0.9)
            _bar_max = max(_cl_range_high * 1.2, _cl_current * 1.1)
            _fig_cl  = go.Figure()
            # Range region (shaded)
            _fig_cl.add_vrect(x0=_cl_range_low, x1=_cl_range_high,
                              fillcolor="rgba(34,197,94,0.12)", layer="below", line_width=0)
            # Boundary lines
            _fig_cl.add_vline(x=_cl_range_low,  line_dash="dash", line_color="#22c55e", line_width=1,
                              annotation_text=f"Low ${_cl_range_low:,.0f}", annotation_position="top left")
            _fig_cl.add_vline(x=_cl_range_high, line_dash="dash", line_color="#22c55e", line_width=1,
                              annotation_text=f"High ${_cl_range_high:,.0f}", annotation_position="top right")
            # Current price
            _fig_cl.add_vline(x=_cl_current, line_color=_cl_status_col, line_width=2,
                              annotation_text=f"Now ${_cl_current:,.2f}", annotation_position="top left")
            _fig_cl.update_layout(
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(15,23,42,0.8)",
                xaxis=dict(range=[_bar_min, _bar_max], showgrid=False,
                           title=f"{_html.escape(_cl_token_pair)} Price"),
                yaxis=dict(visible=False),
                height=100, margin=dict(l=20, r=20, t=30, b=20),
                showlegend=False,
            )
            st.plotly_chart(_fig_cl, width='stretch', config={"displayModeBar": False})

            # Item 39: add to alert tracker
            if st.button("Track this position (add to alert list)", key="cl_track_btn"):
                _new_pos = {
                    "pair":          _cl_token_pair,
                    "range_low":     _cl_range_low,
                    "range_high":    _cl_range_high,
                    "current_price": _cl_current,
                    "deposit_usd":   _cl_deposit,
                    "fee_apy":       _cl_fee_apy,
                }
                # Avoid exact duplicates
                _already = any(
                    p["pair"] == _new_pos["pair"]
                    and p["range_low"] == _new_pos["range_low"]
                    and p["range_high"] == _new_pos["range_high"]
                    for p in st.session_state.get("cl_tracked_positions", [])
                )
                if not _already:
                    st.session_state.setdefault("cl_tracked_positions", []).append(_new_pos)
                    st.success(f"Position added to alert tracker. Alerts show at the top of this section.")
                else:
                    st.info("This position is already tracked.")
        else:
            st.warning("Range LOW must be less than Range HIGH.")
    
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    
    
    # ══════════════════════════════════════════════════════════════════════════════
    # D8 — RISK-ADJUSTED YIELD RANKING
    # Dedicated Sharpe-ranked leaderboard with full explanation
    # ══════════════════════════════════════════════════════════════════════════════
    
    render_section_header(
        "Risk-Adjusted Yield Ranking",
        "All pools ranked by Sharpe ratio — the truest measure of yield quality",
    )
    
    render_what_this_means(
        "Raw APY is misleading — a pool at 50% APY with wild swings may be worse than a stable 10% pool. "
        "The Sharpe ratio measures how much yield you get per unit of risk. "
        "Higher Sharpe = better quality yield. Aim for Sharpe > 1.0 (Good).",
        title="What is the Sharpe ratio?",
        intermediate_message="Sharpe = excess return ÷ volatility. >1.0 = good risk-adjusted yield. Adjusts raw APY for standard deviation.",
    )
    
    # Collect all pools from all sources
    _d8_pool_sources: list = []
    for _src_pool in (_gy_display or []):
        _d8_pool_sources.append({
            "protocol": str(_src_pool.get("protocol") or "—").replace("-", " ").title(),
            "pool":     str(_src_pool.get("symbol") or "—"),
            "chain":    str(_src_pool.get("chain") or "—"),
            "apy":      float(_src_pool.get("apy") or 0),
            "apy_7d":   float(_src_pool.get("apy_7d") or _src_pool.get("apy") or 0),
            "tvl":      float(_src_pool.get("tvl_usd") or 0),
            "sharpe":   _src_pool.get("_sharpe_val", 0),
            "rank":     _src_pool.get("_rank", "—"),
        })
    for _src_pool in (_mc_pools_with_sharpe if "_mc_pools_with_sharpe" in dir() else []):
        _d8_pool_sources.append({
            "protocol": str(_src_pool.get("project") or "—").replace("-", " ").title(),
            "pool":     str(_src_pool.get("symbol") or "—"),
            "chain":    str(_src_pool.get("chain") or "—"),
            "apy":      float(_src_pool.get("apy") or 0),
            "apy_7d":   float(_src_pool.get("apy7d") or _src_pool.get("apy") or 0),
            "tvl":      float(_src_pool.get("tvlUsd") or 0),
            "sharpe":   float(_src_pool.get("_sharpe") or 0),
            "rank":     str(_src_pool.get("_rank") or "—"),
        })
    
    # Deduplicate by (protocol, pool, chain) and sort by Sharpe
    _d8_seen: set = set()
    _d8_deduped: list = []
    for _dp in sorted(_d8_pool_sources, key=lambda x: x["sharpe"], reverse=True):
        _k = (_dp["protocol"], _dp["pool"], _dp["chain"])
        if _k not in _d8_seen and _dp["apy"] > 0:
            _d8_seen.add(_k)
            _d8_deduped.append(_dp)
    
    if _d8_deduped:
        _d8_rows = []
        for _ri, _dp in enumerate(_d8_deduped[:25], 1):
            _sh      = _dp["sharpe"]
            _rk      = _dp["rank"]
            _sh_col  = "#22c55e" if _sh >= 2 else "#84cc16" if _sh >= 1 else "#f59e0b" if _sh >= 0.5 else "#ef4444"
            _rk_icon = {"excellent": "▲", "good": "▲", "fair": "■", "poor": "▼"}.get(_rk, "■")
            _rk_col  = {"excellent": "#22c55e", "good": "#84cc16", "fair": "#f59e0b", "poor": "#ef4444"}.get(_rk, "#9ca3af")
            _tvl_str = (f"${_dp['tvl']/1e9:.1f}B" if _dp["tvl"] >= 1e9
                        else f"${_dp['tvl']/1e6:.0f}M" if _dp["tvl"] >= 1e6
                        else f"${_dp['tvl']/1e3:.0f}K" if _dp["tvl"] >= 1000 else "—")
            _d8_rows.append({
                "Rank":     f"#{_ri}",
                "Protocol": _dp["protocol"],
                "Pool":     _dp["pool"],
                "Chain":    _dp["chain"],
                "APY %":    f"{_dp['apy']:.2f}%",
                "7d Avg %": f"{_dp['apy_7d']:.2f}%",
                "Sharpe":   f"{_sh:.2f}",
                "Quality":  f"{_rk_icon} {_rk.capitalize()}",
                "TVL":      _tvl_str,
            })
        st.dataframe(pd.DataFrame(_d8_rows), width='stretch', hide_index=True)
        st.caption(
            "Sharpe = (APY − risk_free) / APY_volatility. "
            "Excellent ≥2.0 · Good 1–2 · Fair 0.5–1 · Poor <0.5. "
            "Volatility estimated from deviation of current vs 7d average APY. "
            "Source: DeFiLlama yields · all loaded pools."
        )
    else:
        st.info("Load yield pools above to see the risk-adjusted ranking.")

with _tab_intel:
    # ── FTSO Oracle Price Monitor ────────────────────────────────────────────
    st.divider()
    render_section_header(
        "FTSO Oracle Price Monitor",
        "Live Flare oracle prices vs CoinGecko — divergence signals arb opportunity",
    )
    @st.cache_data(ttl=120)
    def _cached_ftso_prices() -> dict:
        try:
            from scanners.flare_scanner import fetch_ftso_prices
            return fetch_ftso_prices()
        except Exception:
            return {}
    _ftso_c1, _ftso_c2 = st.columns([3, 1])
    with _ftso_c2:
        if st.button("Refresh FTSO", key="ftso_refresh_opp"):
            _cached_ftso_prices.clear()
    _ftso_data = _cached_ftso_prices()
    if not _ftso_data:
        st.info("FTSO oracle data unavailable — Flare data availability layer may be unreachable.")
    else:
        _latest_scan2 = load_latest()
        _cg_raw2 = _latest_scan2.get("prices") or []
        _cg_lkp: dict = {}
        if isinstance(_cg_raw2, list):
            for _pp in _cg_raw2:
                if isinstance(_pp, dict) and _pp.get("symbol") and _pp.get("price_usd") is not None:
                    _cg_lkp[_pp["symbol"]] = float(_pp["price_usd"])
        _ftso_rows = []
        _ftso_alts = []
        for _sym2, _fv in sorted(_ftso_data.items()):
            if _fv is None: continue
            _fv = float(_fv)
            _gv = _cg_lkp.get(_sym2)
            if _gv and _gv > 0:
                _dpct = (_fv - _gv) / _gv * 100
                _dstr = f"{_dpct:+.2f}%"
                _stat = "⚠️ Arb" if abs(_dpct) > 2 else ("✅ Aligned" if abs(_dpct) < 0.5 else "🔶 Watch")
                if abs(_dpct) > 2: _ftso_alts.append((_sym2, _dpct, _fv, _gv))
            else:
                _dstr, _stat = "—", "—"
            _ftso_rows.append({"Token": _sym2, "FTSO Oracle": f"${_fv:.5f}",
                "CoinGecko": f"${_gv:.5f}" if _gv else "—",
                "Divergence": _dstr, "Status": _stat})
        for _s2, _d2, _f2, _g2 in _ftso_alts:
            _dir2 = "above" if _d2 > 0 else "below"
            st.warning(f"**{_s2}** FTSO  is {abs(_d2):.2f}% {_dir2} CoinGecko  — potential arb window.")
        if _ftso_rows:
            st.dataframe(pd.DataFrame(_ftso_rows), width='stretch', hide_index=True)
        st.caption("FTSO prices refresh every 2 min. Divergence >2% may indicate arb opportunity. Source: Flare Data Availability Layer.")

with _tab_intel:
    # ── Protocol Treasury Health (Item 30) ───────────────────────────────────
    st.divider()
    render_section_header(
        "Protocol Treasury Health",
        "Runway analysis — stablecoin vs native token treasury mix per protocol",
    )

    @st.cache_data(ttl=3600)
    def _cached_treasuries() -> list:
        return fetch_protocol_treasuries()

    _treas_c1, _treas_c2 = st.columns([3, 1])
    with _treas_c2:
        if st.button("Refresh Treasuries", key="treas_refresh"):
            _cached_treasuries.clear()
    with st.spinner("Loading treasury data from DeFiLlama..."):
        _treas_data = _cached_treasuries()

    if not _treas_data:
        st.info("Treasury data unavailable — DeFiLlama /treasury endpoint may be unreachable.")
    else:
        _health_col = {"HEALTHY": "#22c55e", "CONCENTRATED": "#f59e0b", "DEPLETED": "#ef4444"}
        _health_sym = {"HEALTHY": "▲ Healthy", "CONCENTRATED": "■ Concentrated", "DEPLETED": "▼ Depleted"}

        # Summary metric cards
        _t_healthy   = sum(1 for t in _treas_data if t["health"] == "HEALTHY")
        _t_conc      = sum(1 for t in _treas_data if t["health"] == "CONCENTRATED")
        _t_dep       = sum(1 for t in _treas_data if t["health"] == "DEPLETED")
        _t_total_usd = sum(t["tvl"] for t in _treas_data)
        _tm1, _tm2, _tm3, _tm4 = st.columns(4)
        with _tm1:
            st.metric("Protocols Scanned", len(_treas_data))
        with _tm2:
            st.metric("Healthy", _t_healthy, help="Stablecoin mix ≥20% of treasury")
        with _tm3:
            st.metric("Concentrated Risk", _t_conc, help="<20% stablecoins — heavy native token exposure")
        with _tm4:
            _ttv = (f"${_t_total_usd/1e9:.1f}B" if _t_total_usd >= 1e9
                    else f"${_t_total_usd/1e6:.0f}M")
            st.metric("Total Treasury TVL", _ttv)

        # Treasury table
        _treas_rows = []
        for _tr in _treas_data:
            _hcol = _health_col.get(_tr["health"], "#94a3b8")
            _hsym = _health_sym.get(_tr["health"], _tr["health"])
            _tvl_str = (f"${_tr['tvl']/1e9:.2f}B" if _tr["tvl"] >= 1e9
                        else f"${_tr['tvl']/1e6:.0f}M" if _tr["tvl"] >= 1e6
                        else f"${_tr['tvl']:,.0f}")
            _treas_rows.append({
                "Protocol":       _tr["name"],
                "Treasury":       _tvl_str,
                "Stablecoin %":   f"{_tr['stablecoin_pct']:.0f}%",
                "Native Token %": f"{_tr['native_pct']:.0f}%",
                "Health":         _hsym,
            })
        st.dataframe(pd.DataFrame(_treas_rows), width='stretch', hide_index=True)

        # Detail expander for top 5 holding breakdowns
        with st.expander("Top Holdings Breakdown", expanded=False):
            for _tr in _treas_data[:5]:
                _hcol = _health_col.get(_tr["health"], "#94a3b8")
                st.markdown(
                    f"<div style='font-weight:600;color:{_hcol};margin:8px 0 2px'>"
                    f"{_html.escape(_tr['name'])} "
                    f"<span style='font-weight:400;font-size:0.8rem;color:#94a3b8'>"
                    f"(${_tr['tvl']/1e6:.0f}M treasury)</span></div>",
                    unsafe_allow_html=True,
                )
                _bk = _tr.get("token_breakdown", [])
                if _bk:
                    _bk_rows = [{"Token": b["symbol"],
                                  "USD Value": (f"${b['usd']/1e6:.1f}M" if b["usd"] >= 1e6
                                                else f"${b['usd']:,.0f}"),
                                  "% of Treasury": f"{b['pct']:.1f}%"}
                                 for b in _bk]
                    st.dataframe(pd.DataFrame(_bk_rows), width='stretch', hide_index=True)

        render_what_this_means(
            "A protocol's treasury is its emergency fund — like a company's cash reserves. "
            "Healthy treasuries hold at least 20% in stablecoins (USDC, DAI) so they can "
            "pay developers and cover costs even if their own token crashes. "
            "Concentrated treasuries hold mostly their own token — if the price drops, "
            "they can run out of money fast. Always check treasury health before depositing "
            "large amounts into a protocol.",
            title="Why treasury health matters",
            intermediate_message="Treasury: stablecoin mix indicates runway quality. <20% stables = sell-off risk.",
        )
        st.caption("Source: DeFiLlama /treasury/{slug} · refreshed hourly.")

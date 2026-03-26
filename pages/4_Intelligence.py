"""
Intelligence — Ecosystem monitor (What's New) and AI model health / accuracy.
"""

import sys
import html as _html
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd

from ui.common import (
    page_setup, render_sidebar, load_monitor_digest, render_section_header, _ts_fmt,
    load_latest,
)

page_setup("Intelligence · Flare DeFi")

ctx     = render_sidebar()
profile = ctx["profile"]

st.markdown("# Intelligence")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Ecosystem monitor · new protocols · news · AI model accuracy</div>",
    unsafe_allow_html=True,
)


# ─── What's New ───────────────────────────────────────────────────────────────

render_section_header("Ecosystem Monitor", "New protocols · recent news · on-chain activity")

digest = load_monitor_digest()
if not digest:
    st.info(
        "No monitor data yet. Trigger manually: "
        "`python -c \"from scanners.web_monitor import run_web_monitor; run_web_monitor()\"`"
    )
else:
    generated  = digest.get("generated_at", "")
    new_p      = len(digest.get("new_protocols") or [])
    news_n     = len(digest.get("news_items") or [])
    known_tvl  = digest.get("known_tvl") or {}

    # Status bar
    parts = []
    if generated:
        parts.append(f"Last checked: {_ts_fmt(generated)}")
    if new_p:
        parts.append(f"{new_p} new protocol(s)")
    if news_n:
        parts.append(f"{news_n} news item(s)")
    if parts:
        st.markdown(
            f"<div style='color:#475569; font-size:0.78rem; margin-bottom:14px;'>"
            f"{'  ·  '.join(parts)}</div>",
            unsafe_allow_html=True,
        )

    # AI summary
    ai_text = digest.get("ai_digest", "").strip()
    if ai_text:
        st.markdown(
            f"<div class='opp-card' style='border-left:3px solid #8b5cf6; "
            f"background:rgba(139,92,246,0.04);'>"
            f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:10px;'>"
            f"<span style='font-size:1rem;'>🤖</span>"
            f"<span class='badge-new'>AI Summary</span>"
            f"<span style='font-size:0.72rem; color:#334155; margin-left:auto;'>Claude AI · Not financial advice</span>"
            f"</div>"
            f"<div style='color:#c4cbdb; font-size:0.90rem; line-height:1.65;'>{_html.escape(ai_text)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='background:rgba(139,92,246,0.04); border:1px solid rgba(139,92,246,0.12); "
            "border-radius:10px; padding:12px 16px; font-size:0.83rem; color:#475569;'>"
            "🤖 Set <code style='background:rgba(255,255,255,0.06); padding:1px 6px; border-radius:4px;'>"
            "ANTHROPIC_API_KEY</code> to enable AI-generated ecosystem summaries.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # New protocols
    new_protocols = digest.get("new_protocols") or []
    if new_protocols:
        st.markdown(f"#### New Protocols on Flare ({len(new_protocols)})")
        for proto in new_protocols:
            tvl_str  = f"${proto['tvl_usd']:,.0f}" if proto.get("tvl_usd") else "TVL unknown"
            url_md   = f" · [Visit]({proto['url']})" if proto.get("url") else ""
            desc     = _html.escape(str(proto.get("description", "")))
            st.markdown(
                f"<div class='arb-tag'>"
                f"<span style='font-weight:700; color:#f1f5f9;'>{_html.escape(str(proto.get('name', '?')))}</span>"
                f"<span style='color:#475569;'> · {_html.escape(str(proto.get('category','?')))} · {tvl_str}{url_md}</span>"
                f"{'<div style=\"color:#64748b;font-size:0.82rem;margin-top:6px\">' + desc + '</div>' if desc else ''}"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>No new protocols detected since last check.</div>",
            unsafe_allow_html=True,
        )

    # TVL table
    if known_tvl:
        with st.expander(f"Live TVL — {len(known_tvl)} tracked protocols"):
            tvl_rows = [
                {"Protocol": name, "TVL (USD)": f"${data.get('tvl_usd', 0):,.0f}", "Category": data.get("category", "")}
                for name, data in sorted(
                    known_tvl.items(),
                    key=lambda x: x[1].get("tvl_usd", 0) if isinstance(x[1], dict) else 0,
                    reverse=True,
                )
                if isinstance(data, dict)
            ]
            st.dataframe(pd.DataFrame(tvl_rows), use_container_width=True, hide_index=True)

    # News
    news_items = digest.get("news_items") or []
    if news_items:
        st.markdown(
            f"<div style='font-size:0.78rem; font-weight:700; color:#94a3b8; "
            f"text-transform:uppercase; letter-spacing:1.2px; margin:16px 0 10px;'>"
            f"Recent News <span style='color:#334155; font-weight:400;'>({len(news_items)} articles)</span></div>",
            unsafe_allow_html=True,
        )
        for item in news_items[:10]:
            if not isinstance(item, dict):
                continue
            title    = _html.escape(str(item.get("title", "Untitled")))
            link     = item.get("link", "")
            title_md = f"<a href='{_html.escape(link)}' target='_blank' style='color:#c4cbdb; font-weight:600; text-decoration:none;'>{title} ↗</a>" if link else f"<span style='color:#94a3b8; font-weight:600;'>{title}</span>"
            summary  = _html.escape(str(item.get("summary", "")))
            src      = _html.escape(str(item.get("source", "")))
            pub      = _html.escape(str(item.get("published", "")))
            sum_html = f"<div style='color:#64748b; font-size:0.81rem; margin-top:5px; line-height:1.5;'>{summary}</div>" if summary else ""
            st.markdown(
                f"<div style='background:rgba(13,14,20,0.8); border-radius:12px; padding:13px 16px; "
                f"margin-bottom:8px; border:1px solid rgba(255,255,255,0.06); "
                f"transition: border-color 0.2s;'>"
                f"<div>{title_md}</div>"
                f"<div style='color:#334155; font-size:0.72rem; margin-top:4px; display:flex; gap:8px;'>"
                f"<span style='color:#475569;'>{src}</span>"
                f"{'<span style=\"color:#1e293b\">·</span>' if src and pub else ''}"
                f"<span>{pub}</span>"
                f"</div>"
                f"{sum_html}"
                f"</div>",
                unsafe_allow_html=True,
            )
        if len(news_items) > 10:
            with st.expander(f"Show all {len(news_items)} articles"):
                for item in news_items[10:]:
                    link_md = f"[{item.get('title', 'Untitled')}]({item['link']})" if item.get("link") else item.get("title", "Untitled")
                    st.markdown(f"- **{link_md}** — {item.get('source','')} · {item.get('published','')}")
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>No news in the last 48 hours.</div>",
            unsafe_allow_html=True,
        )

    errors = digest.get("errors") or []
    if errors:
        with st.expander("Monitor errors (non-critical)"):
            for err in errors:
                st.caption(err)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── AI Model Health ──────────────────────────────────────────────────────────

render_section_header("AI Model Health", "How accurately has the model predicted real yields? Updates after each scan")

try:
    from ai.feedback_loop import get_feedback_dashboard
    feedback = get_feedback_dashboard()
except Exception as e:
    st.warning(f"Could not load feedback data: {e}")
    feedback = None

if feedback:
    overall = feedback.get("overall_health", 50)
    trend   = feedback.get("trend", "building")
    trend_icon = {"improving": "📈", "stable": "➡️", "declining": "📉", "building": "🔧"}.get(trend, "➡️")
    health_color = "#10b981" if overall >= 70 else ("#f59e0b" if overall >= 45 else "#ef4444")

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Overall Health</div>
            <div class="big-number" style="color:{health_color};">{overall}<span style="font-size:1rem; color:#475569;">/100</span></div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Trend</div>
            <div class="big-number">{trend_icon}</div>
            <div style="color:#475569; font-size:0.82rem; margin-top:4px;">{trend.capitalize()}</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        scans     = feedback.get("total_scans", 0)
        evaluated = feedback.get("evaluated_scans", 0)
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Predictions</div>
            <div class="big-number">{evaluated}</div>
            <div style="color:#475569; font-size:0.82rem; margin-top:4px;">of {scans} evaluated</div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    st.markdown("#### Per-Profile Accuracy")

    from config import RISK_PROFILE_NAMES, RISK_PROFILES

    # Toggle between 24h and 7d accuracy windows (upgrade #11)
    acc_window = st.radio(
        "Evaluation window",
        ["24h", "7d"],
        horizontal=True,
        key="acc_window",
        help="24h: accuracy vs next-day actuals. 7d: accuracy vs 7-day actuals.",
    )
    profile_data = (
        (feedback.get("per_profile") or {})
        if acc_window == "24h"
        else (feedback.get("per_profile_7d") or feedback.get("per_profile") or {})
    )

    for p in RISK_PROFILE_NAMES:
        acc   = profile_data.get(p) or {}
        pcfg  = RISK_PROFILES[p]
        pcol  = pcfg["color"]
        grade = acc.get("grade", "N/A")
        score = acc.get("health_score", 50)
        msg   = acc.get("message", "Building history…")
        acc_pct = acc.get("accuracy_pct")
        err_pct = acc.get("avg_error_pct")
        dir_pct = acc.get("directional_pct")   # upgrade #10
        sc      = acc.get("sample_count", 0)

        sc_color = "#10b981" if score >= 70 else ("#f59e0b" if score >= 45 else "#ef4444")

        st.markdown(
            f"<div class='opp-card' style='border-left:3px solid {pcol};'>"
            f"<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
            f"<span style='font-weight:700; color:#f1f5f9;'>{pcfg['label']}</span>"
            f"<div style='display:flex; gap:16px; font-size:0.85rem; color:#475569;'>"
            f"<span>Grade: <span style='color:#f1f5f9; font-weight:700;'>{grade}</span></span>"
            f"<span>Score: <span style='color:{sc_color}; font-weight:700;'>{score}/100</span></span>"
            f"{'<span>Accuracy: <span style=\"color:#94a3b8\">' + str(acc_pct) + '%</span></span>' if acc_pct is not None else ''}"
            f"{'<span>Avg error: <span style=\"color:#94a3b8\">' + str(err_pct) + '%</span></span>' if err_pct is not None else ''}"
            f"{'<span>Directional: <span style=\"color:#94a3b8\">' + str(dir_pct) + '%</span></span>' if dir_pct is not None else ''}"
            f"<span style='color:#334155;'>{sc} samples</span>"
            f"</div></div>"
            f"<div style='color:#475569; font-size:0.82rem; margin-top:8px;'>{msg}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # Model weights
    weights = feedback.get("model_weights") or {}
    if weights:
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        st.markdown("#### Model Confidence Weights")
        st.markdown(
            "<div style='color:#475569; font-size:0.82rem; margin-bottom:10px;'>"
            "Higher accuracy = higher weight. Adjusted automatically after each scan.</div>",
            unsafe_allow_html=True,
        )
        rows = [{"Profile": p.capitalize(), "Weight": f"{w:.4f}",
                 "Effect": "Boosted" if w > 1.0 else ("Reduced" if w < 0.9 else "Neutral")}
                for p, w in weights.items()]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Macro Intelligence ────────────────────────────────────────────────────────

render_section_header("Macro Intelligence", "FRED + yfinance · 10Y yield · VIX · BTC rolling correlations")

try:
    import macro_feeds as _mf
    import plotly.graph_objects as go

    _fred = _mf.fetch_fred_macro()
    _yf   = _mf.fetch_yfinance_macro()

    _mc1, _mc2, _mc3, _mc4, _mc5, _mc6, _mc7 = st.columns(7)
    _mc1.metric("10Y Yield",  f"{_fred.get('ten_yr_yield', 4.35):.2f}%")
    _mc2.metric("M2 ($B)",    f"${_fred.get('m2_supply_bn', 21500):,.0f}B")
    _mc3.metric("ISM Mfg",    f"{_fred.get('ism_manufacturing', 52.0):.1f}")
    _mc4.metric("WTI Oil",    f"${_fred.get('wti_crude', 67.5):.1f}")
    _mc5.metric("DXY",        f"{_yf.get('dxy', 104.0):.1f}")
    _mc6.metric("VIX",        f"{_yf.get('vix', 18.0):.1f}")
    _mc7.metric("Gold",       f"${_yf.get('gold_spot', 2900.0):,.0f}")

    st.markdown(
        f"<div style='color:#475569; font-size:0.75rem; margin-bottom:14px;'>"
        f"FRED: {_fred.get('source','?')} · yfinance: {_yf.get('source','?')} · Cached 1 hour</div>",
        unsafe_allow_html=True,
    )

    _corr_w = st.select_slider(
        "BTC correlation window (days)",
        options=[14, 30, 60, 90],
        value=30,
        key="defi_macro_corr_days",
    )
    _ts = _mf.fetch_macro_timeseries(_corr_w + 20)

    if _ts and "BTC" in _ts:
        _frames: dict = {}
        for _key in ["BTC", "VIX", "Gold", "SPX", "DXY", "Oil"]:
            _s = _ts.get(_key)
            if _s and isinstance(_s, dict):
                _frames[_key] = pd.Series(_s)
        if len(_frames) >= 2:
            _dft = pd.DataFrame(_frames).sort_index()
            _dft.index = pd.to_datetime(_dft.index)
            _dfr = _dft.pct_change().dropna()
            _fig = go.Figure()
            _clrs = {"VIX": "#ef4444", "Gold": "#f59e0b", "SPX": "#10b981",
                     "DXY": "#6366f1", "Oil": "#f97316"}
            for _fac in [c for c in _dfr.columns if c != "BTC"]:
                if "BTC" in _dfr.columns:
                    _rc = _dfr["BTC"].rolling(_corr_w).corr(_dfr[_fac]).dropna()
                    if not _rc.empty:
                        _fig.add_trace(go.Scatter(
                            x=_rc.index, y=_rc.values, mode="lines", name=_fac,
                            line=dict(color=_clrs.get(_fac, "#888"), width=2),
                        ))
            _fig.add_hline(y=0, line_dash="dash", line_color="rgba(255,255,255,0.25)")
            _fig.update_layout(
                height=260,
                title=dict(text=f"BTC {_corr_w}-day Rolling Correlation", font=dict(size=13)),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e2e8f0", size=11),
                margin=dict(l=0, r=0, t=40, b=0),
                yaxis=dict(range=[-1, 1], gridcolor="rgba(255,255,255,0.07)"),
                xaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            )
            st.plotly_chart(_fig, use_container_width=True)
        else:
            st.info("Loading macro timeseries… (yfinance required)")
    else:
        st.info("Install yfinance for BTC macro correlations: `pip install yfinance`")

except Exception as _macro_err:
    st.caption(f"Macro data unavailable: {_macro_err}")

# ─── Blood in the Streets · DCA Multiplier (Group 3) ─────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
render_section_header("Blood in the Streets", "Multi-factor capitulation signal · DCA sizing guide")

try:
    import macro_feeds as _mf3
    _fred3 = _mf3.fetch_fred_macro()
    _yf3   = _mf3.fetch_yfinance_macro()

    # Use F&G from session state if available, else default to 50
    _fg_v3 = st.session_state.get("fear_greed_value", 50)
    _bits3 = _mf3.compute_blood_in_streets(_fg_v3)
    _dca3  = _bits3["dca_multiplier"]

    # Color maps
    _bc3 = {"BLOOD_IN_STREETS": "#ef4444", "EXTREME_FEAR": "#f59e0b", "NORMAL": "#6b7280"}.get(_bits3["signal"], "#6b7280")
    _bg3 = {"BLOOD_IN_STREETS": "#1f0000",  "EXTREME_FEAR": "#1c1200", "NORMAL": "#111827"}.get(_bits3["signal"], "#111827")
    _dc3 = {0.0: "#ef4444", 0.5: "#f97316", 1.0: "#9ca3af", 2.0: "#10b981", 3.0: "#00d4aa"}.get(_dca3, "#9ca3af")
    _dl3 = {0.0: "HOLD — no new buys", 0.5: "0.5× — reduce size", 1.0: "1× — base size", 2.0: "2× — accumulate", 3.0: "3× — max accumulate"}.get(_dca3, f"{_dca3}×")

    _col1, _col2 = st.columns(2)
    with _col1:
        st.markdown(f"""
<div style="background:{_bg3};border:1px solid {_bc3};border-top:3px solid {_bc3};
            border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Blood in Streets Signal</div>
  <div style="font-size:20px;font-weight:700;color:{_bc3}">{_bits3["signal"].replace("_", " ")}</div>
  <div style="font-size:12px;color:#9ca3af;margin-top:4px">{_bits3["strength"]} · {_bits3["criteria_met"]}/3 criteria met</div>
  <div style="font-size:11px;color:#6b7280;margin-top:8px">{_bits3["description"]}</div>
  <div style="margin-top:10px;font-size:11px;color:#6b7280">
    {"✅" if _bits3["criteria"]["extreme_fear"] else "❌"} F&amp;G≤25 &nbsp;
    {"✅" if _bits3["criteria"]["rsi_oversold"] else "❌"} RSI≤30 &nbsp;
    {"✅" if _bits3["criteria"]["exchange_outflow"] else "❌"} Exchange outflow
  </div>
</div>
""", unsafe_allow_html=True)
    with _col2:
        st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid {_dc3};
            border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">DCA Multiplier</div>
  <div style="font-size:36px;font-weight:700;color:{_dc3}">{_dca3}×</div>
  <div style="font-size:13px;color:#9ca3af;margin-top:4px">{_dl3}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:8px">
    F&amp;G: {_fg_v3}/100<br/>
    DXY {_yf3.get("dxy", "—")} · 10Y {_fred3.get("ten_yr_yield", "—")}%
  </div>
</div>
""", unsafe_allow_html=True)
except Exception as _bits_err:
    st.caption(f"Blood in Streets signal unavailable: {_bits_err}")

# ─── On-Chain Intelligence (Group 4) ─────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
render_section_header("On-Chain Intelligence", "CoinMetrics Community API · MVRV Z-Score · SOPR · no API key required")

try:
    import macro_feeds as _mf4
    _oc4 = _mf4.fetch_coinmetrics_onchain(days=400)

    if _oc4.get("error") and not _oc4.get("mvrv_z"):
        st.info(f"On-chain data loading… CoinMetrics Community API may be rate-limited. Error: {_oc4.get('error')}")
    else:
        _mz4  = _oc4.get("mvrv_z")
        _ms4  = _oc4.get("mvrv_signal", "N/A")
        _sp4  = _oc4.get("sopr")
        _ss4  = _oc4.get("sopr_signal", "N/A")
        _rc4  = _oc4.get("realized_cap")
        _mv4  = _oc4.get("mvrv_ratio")
        _aa4  = _oc4.get("active_addresses")

        _mvrv_color = {"UNDERVALUED": "#00d4aa", "FAIR_VALUE": "#10b981", "OVERVALUED": "#f59e0b", "EXTREME_HEAT": "#ef4444"}.get(_ms4, "#6b7280")
        _sc4 = {"CAPITULATION": "#00d4aa", "MILD_LOSS": "#10b981", "NORMAL": "#6b7280", "PROFIT_TAKING": "#f59e0b"}.get(_ss4, "#6b7280")

        def _fmtb(v):
            if v is None: return "—"
            if v >= 1e12: return f"${v/1e12:.2f}T"
            if v >= 1e9:  return f"${v/1e9:.1f}B"
            return f"${v/1e6:.0f}M"

        _c1, _c2, _c3, _c4 = st.columns(4)
        with _c1:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid {_mvrv_color};border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">MVRV Z-Score</div>
  <div style="font-size:30px;font-weight:700;color:{_mvrv_color}">{f"{_mz4:+.2f}" if _mz4 is not None else "—"}</div>
  <div style="font-size:13px;color:#9ca3af;margin-top:4px">{_ms4.replace("_", " ")}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">MVRV ratio: {f"{_mv4:.3f}" if _mv4 else "—"}</div>
</div>
""", unsafe_allow_html=True)
        with _c2:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid {_sc4};border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">SOPR</div>
  <div style="font-size:30px;font-weight:700;color:{_sc4}">{f"{_sp4:.4f}" if _sp4 is not None else "—"}</div>
  <div style="font-size:13px;color:#9ca3af;margin-top:4px">{_ss4.replace("_", " ")}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">&gt;1 profit-taking · &lt;1 capitulation</div>
</div>
""", unsafe_allow_html=True)
        with _c3:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid #6366f1;border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Realized Cap</div>
  <div style="font-size:22px;font-weight:700;color:#6366f1">{_fmtb(_rc4)}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:8px">BTC at last-moved price</div>
</div>
""", unsafe_allow_html=True)
        with _c4:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid #8b5cf6;border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Active Addresses</div>
  <div style="font-size:22px;font-weight:700;color:#8b5cf6">{f"{_aa4:,}" if _aa4 else "—"}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:8px">Unique BTC addresses today</div>
</div>
""", unsafe_allow_html=True)

        # MVRV Z-Score chart
        _mh4 = _oc4.get("mvrv_history", {})
        if _mh4:
            _mhs = pd.Series(_mh4).sort_index()
            _mhz = (_mhs - _mhs.rolling(365, min_periods=30).mean()) / _mhs.rolling(365, min_periods=30).std().clip(lower=1e-6)
            _fig_mz = go.Figure()
            _fig_mz.add_trace(go.Scatter(x=_mhz.index, y=_mhz.values, mode="lines",
                                         name="MVRV Z-Score", line=dict(color="#6366f1", width=2)))
            for _th, _tl, _tc in [(3.0, "Extreme >3", "#ef4444"), (1.5, "Overvalued", "#f59e0b"), (-0.5, "Undervalued", "#00d4aa")]:
                _fig_mz.add_hline(y=_th, line_dash="dash", line_color=_tc, opacity=0.4,
                                  annotation_text=_tl, annotation_font_size=9)
            _fig_mz.update_layout(
                height=240,
                title=dict(text="MVRV Z-Score (365-day rolling)", font=dict(size=13)),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e2e8f0", size=11), margin=dict(l=0, r=0, t=40, b=0),
                yaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
                xaxis=dict(gridcolor="rgba(255,255,255,0.07)"),
                showlegend=False,
            )
            st.plotly_chart(_fig_mz, use_container_width=True)

        _ts4 = _oc4.get("timestamp", "")[:19]
        st.caption(f"Source: CoinMetrics Community · {_ts4} UTC · Cached 1h")
except Exception as _oc_err:
    st.caption(f"On-chain data unavailable: {_oc_err}")

# ── GROUP 5: Options Flow ─────────────────────────────────────────────────────
st.markdown("---")
render_section_header("📐 Options Flow", "Deribit public API · OI by Strike · Put/Call Ratio · Max Pain · IV Term Structure · no key required")

try:
    import macro_feeds as _mf5
    _oc5 = _mf5.fetch_deribit_options_chain(currency="BTC")

    if _oc5.get("error") and not _oc5.get("oi_by_strike"):
        st.caption(f"Options data unavailable: {_oc5.get('error')}")
    else:
        _pc5   = _oc5.get("put_call_ratio")
        _mp5   = _oc5.get("max_pain")
        _tput5 = _oc5.get("total_put_oi", 0)
        _tcal5 = _oc5.get("total_call_oi", 0)
        _osig5 = _oc5.get("signal", "N/A")
        _spot5 = _oc5.get("spot_price")

        _sc5 = {
            "EXTREME_PUTS": "#ef4444", "BEARISH": "#f59e0b",
            "NEUTRAL": "#6b7280", "BULLISH": "#10b981", "EXTREME_CALLS": "#00d4aa",
        }.get(_osig5, "#6b7280")

        _d5a, _d5b, _d5c, _d5d = st.columns(4)
        with _d5a:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid {_sc5};border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Put/Call Ratio</div>
  <div style="font-size:28px;font-weight:700;color:{_sc5}">{f"{_pc5:.3f}" if _pc5 is not None else "—"}</div>
  <div style="font-size:13px;color:#9ca3af;margin-top:4px">{_osig5.replace("_", " ")}</div>
</div>
""", unsafe_allow_html=True)
        with _d5b:
            _mp5_d = f"{abs(_mp5 - _spot5) / _spot5 * 100:.1f}% {'below' if _mp5 < _spot5 else 'above'} spot" if _mp5 and _spot5 else ""
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid #6366f1;border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Max Pain</div>
  <div style="font-size:24px;font-weight:700;color:#6366f1">{f"${_mp5:,.0f}" if _mp5 else "—"}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">{_mp5_d}</div>
</div>
""", unsafe_allow_html=True)
        with _d5c:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid #ef4444;border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Total Put OI</div>
  <div style="font-size:24px;font-weight:700;color:#ef4444">{f"{_tput5:,.0f}" if _tput5 else "—"}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">contracts</div>
</div>
""", unsafe_allow_html=True)
        with _d5d:
            st.markdown(f"""
<div style="background:#111827;border:1px solid #1f2937;border-top:3px solid #10b981;border-radius:10px;padding:16px">
  <div style="font-size:11px;color:#6b7280;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Total Call OI</div>
  <div style="font-size:24px;font-weight:700;color:#10b981">{f"{_tcal5:,.0f}" if _tcal5 else "—"}</div>
  <div style="font-size:11px;color:#6b7280;margin-top:6px">contracts</div>
</div>
""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        _oi5d  = _oc5.get("oi_by_strike", [])
        _ts5d  = [t for t in _oc5.get("term_structure", []) if t.get("atm_iv") is not None and t.get("dte", 0) <= 365]
        _col5L, _col5R = st.columns([3, 2])

        with _col5L:
            if _oi5d:
                import plotly.graph_objects as go
                _fig5d = go.Figure()
                _sk5   = [str(int(r["strike"])) for r in _oi5d]
                _fig5d.add_trace(go.Bar(name="Puts", x=_sk5,
                    y=[r["put_oi"] for r in _oi5d], marker_color="rgba(239,68,68,0.8)"))
                _fig5d.add_trace(go.Bar(name="Calls", x=_sk5,
                    y=[r["call_oi"] for r in _oi5d], marker_color="rgba(16,185,129,0.8)"))
                # Use add_shape instead of add_vline — the x-axis uses string
                # categorical labels (strike prices as strings), so add_vline()
                # triggers Plotly's internal _mean() TypeError on string values.
                if _mp5:
                    _mp5_str = str(int(_mp5))
                    _fig5d.add_shape(
                        type="line",
                        x0=_mp5_str, x1=_mp5_str,
                        y0=0, y1=1,
                        xref="x", yref="paper",
                        line=dict(color="#6366f1", dash="dash", width=1.5),
                        opacity=0.8,
                    )
                    _fig5d.add_annotation(
                        x=_mp5_str, y=1,
                        xref="x", yref="paper",
                        text=f"Max Pain ${_mp5:,.0f}",
                        showarrow=False,
                        font=dict(color="#6366f1", size=10),
                        xanchor="left",
                        yanchor="top",
                    )
                if _spot5:
                    _spot5_str = str(int(_spot5))
                    _fig5d.add_shape(
                        type="line",
                        x0=_spot5_str, x1=_spot5_str,
                        y0=0, y1=1,
                        xref="x", yref="paper",
                        line=dict(color="#f59e0b", dash="dot", width=1.5),
                        opacity=0.6,
                    )
                    _fig5d.add_annotation(
                        x=_spot5_str, y=0.95,
                        xref="x", yref="paper",
                        text="Spot",
                        showarrow=False,
                        font=dict(color="#f59e0b", size=10),
                        xanchor="left",
                        yanchor="top",
                    )
                _fig5d.update_layout(
                    title="OI by Strike (Top 20)", barmode="stack",
                    height=300, paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e2e8f0", size=11),
                    margin=dict(l=0, r=0, t=40, b=60),
                    legend=dict(orientation="h", y=1.08),
                    xaxis=dict(tickangle=-45, gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(gridcolor="rgba(255,255,255,0.07)", title="OI (contracts)"),
                )
                st.plotly_chart(_fig5d, use_container_width=True)

        with _col5R:
            if _ts5d:
                import plotly.graph_objects as go
                _fig5e = go.Figure()
                _fig5e.add_trace(go.Scatter(
                    x=[t["dte"] for t in _ts5d], y=[t["atm_iv"] for t in _ts5d],
                    mode="lines+markers", name="ATM IV",
                    line=dict(color="#6366f1", width=2), marker=dict(size=6),
                    text=[t["expiry"] for t in _ts5d],
                    hovertemplate="%{text}<br>DTE: %{x}<br>IV: %{y:.1f}%<extra></extra>",
                ))
                _fig5e.update_layout(
                    title="IV Term Structure (ATM)",
                    height=300, paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    font=dict(color="#e2e8f0", size=11),
                    margin=dict(l=0, r=0, t=40, b=0),
                    xaxis=dict(title="Days to Expiry", gridcolor="rgba(255,255,255,0.05)"),
                    yaxis=dict(title="IV (%)", gridcolor="rgba(255,255,255,0.07)"),
                )
                st.plotly_chart(_fig5e, use_container_width=True)

        _ts5_txt = _oc5.get("timestamp", "")[:19]
        st.caption(f"Source: Deribit · {_ts5_txt} UTC · Cached 15 min")
except Exception as _opt_err:
    st.caption(f"Options data unavailable: {_opt_err}")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── FTSO Price Monitor ────────────────────────────────────────────────────────

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

_ftso_col1, _ftso_col2 = st.columns([3, 1])
with _ftso_col2:
    if st.button("Refresh FTSO", key="ftso_refresh"):
        _cached_ftso_prices.clear()

_ftso_prices = _cached_ftso_prices()

if not _ftso_prices:
    st.info("FTSO oracle data unavailable — Flare data availability layer may be unreachable.")
else:
    # Load CoinGecko prices from latest scan for comparison
    _latest_scan  = load_latest()
    _cg_prices_raw = _latest_scan.get("prices") or []
    _cg_lookup: dict = {}
    if isinstance(_cg_prices_raw, list):
        for _p in _cg_prices_raw:
            if isinstance(_p, dict):
                _sym = _p.get("symbol", "")
                _px  = _p.get("price_usd")
                if _sym and _px is not None:
                    _cg_lookup[_sym] = float(_px)

    _rows = []
    _alerts = []
    for _sym, _ftso_px in sorted(_ftso_prices.items()):
        if _ftso_px is None:
            continue
        _ftso_px = float(_ftso_px)
        _cg_px   = _cg_lookup.get(_sym)
        if _cg_px and _cg_px > 0:
            _div_pct = (_ftso_px - _cg_px) / _cg_px * 100
            _div_str = f"{_div_pct:+.2f}%"
            _status  = "⚠️ Arb" if abs(_div_pct) > 2 else ("✅ Aligned" if abs(_div_pct) < 0.5 else "🔶 Watch")
            if abs(_div_pct) > 2:
                _alerts.append((_sym, _div_pct, _ftso_px, _cg_px))
        else:
            _div_str = "—"
            _status  = "—"
        _rows.append({
            "Token":          _sym,
            "FTSO Oracle":    f"${_ftso_px:.6g}",
            "CoinGecko":      f"${_cg_px:.6g}" if _cg_px else "—",
            "Divergence":     _div_str,
            "Status":         _status,
        })

    if _alerts:
        for _sym, _div_pct, _ftso_px, _cg_px in _alerts:
            _dir = "above" if _div_pct > 0 else "below"
            st.warning(
                f"**{_sym}** FTSO price ${_ftso_px:.6g} is {abs(_div_pct):.2f}% {_dir} "
                f"CoinGecko ${_cg_px:.6g} — potential arbitrage window."
            )

    if _rows:
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
    st.caption("FTSO oracle prices refresh every 2 min. Divergence >2% may indicate arb opportunity. Source: Flare Data Availability Layer.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Claude DeFi Intent Taxonomy  (#87) ─────────────────────────────────────

render_section_header(
    "DeFi Intent Mapper",
    "Describe what you want to do — Claude classifies your intent and recommends the best strategy",
)

_intent_map = {
    "swap":    {"label": "Swap Tokens",       "icon": "🔄", "color": "#6366F1",
                "desc":  "Exchange one token for another at the best available rate."},
    "provide": {"label": "Provide Liquidity",  "icon": "💧", "color": "#06B6D4",
                "desc":  "Add tokens to an AMM pool to earn trading fees. Comes with IL risk."},
    "stake":   {"label": "Stake / Restake",    "icon": "🔒", "color": "#8B5CF6",
                "desc":  "Lock tokens in a protocol to earn staking rewards."},
    "lend":    {"label": "Lend / Deposit",     "icon": "🏦", "color": "#10B981",
                "desc":  "Deposit assets into a lending protocol to earn interest."},
    "borrow":  {"label": "Borrow",             "icon": "💸", "color": "#F59E0B",
                "desc":  "Borrow against your collateral. Use carefully — liquidation risk."},
    "claim":   {"label": "Claim Rewards",      "icon": "🎁", "color": "#EC4899",
                "desc":  "Harvest accumulated reward tokens from a protocol."},
    "bridge":  {"label": "Bridge Assets",      "icon": "🌉", "color": "#14B8A6",
                "desc":  "Move tokens between blockchains via a bridge protocol."},
    "hedge":   {"label": "Hedge / Options",    "icon": "🛡️", "color": "#EF4444",
                "desc":  "Protect against downside using options or delta-neutral strategies."},
}

_intent_input = st.text_input(
    "What do you want to do?",
    placeholder="e.g. 'I want to earn yield on my USDC without IL risk'",
    key="defi_intent_input",
    help="Describe your DeFi goal in plain English. Claude will classify your intent and suggest the matching strategy type.",
)

if _intent_input:
    _input_lower = _intent_input.lower()
    _detected = []

    # Rule-based keyword detection (fast path — no API needed)
    _keyword_map = {
        "swap":    ["swap", "exchange", "convert", "trade", "buy", "sell"],
        "provide": ["liquidity", "lp", "pool", "amm", "provide", "pair"],
        "stake":   ["stake", "restake", "staking", "liquid staking", "lsd", "lrt", "eigenlayer"],
        "lend":    ["lend", "deposit", "earn", "yield", "apy", "interest", "savings"],
        "borrow":  ["borrow", "loan", "leverage", "cdp", "collateral"],
        "claim":   ["claim", "harvest", "collect", "rewards"],
        "bridge":  ["bridge", "cross-chain", "transfer", "move"],
        "hedge":   ["hedge", "options", "protect", "delta neutral", "short"],
    }
    for intent_key, keywords in _keyword_map.items():
        if any(kw in _input_lower for kw in keywords):
            _detected.append(intent_key)

    _used_default = False
    if not _detected:
        _detected = ["lend"]  # default to most common intent
        _used_default = True

    st.markdown("**Detected intent:**")
    _intent_cols = st.columns(min(len(_detected), 4))
    for _idx, _ikey in enumerate(_detected):
        _idef = _intent_map.get(_ikey, {})
        with _intent_cols[_idx % 4]:
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.2);border:1px solid rgba(255,255,255,0.08);"
                f"border-top:2px solid {_idef.get('color','#6B7280')};border-radius:8px;"
                f"padding:10px 12px;text-align:center'>"
                f"<div style='font-size:1.4rem'>{_idef.get('icon','?')}</div>"
                f"<div style='font-size:0.85rem;font-weight:700;color:#F1F5F9;margin-top:4px'>{_idef.get('label','—')}</div>"
                f"<div style='font-size:0.72rem;color:#64748b;margin-top:4px'>{_idef.get('desc','')}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    # Match opportunities from scan data to detected intents
    _latest = load_latest()
    _matched_opps = []
    _strategy_intent_map = {
        "Lending":        "lend",
        "Liquid Staking": "stake",
        "Yield Vault":    "lend",
        "LP":             "provide",
        "Fixed-rate":     "lend",
        "Perps":          "hedge",
    }
    for _p in ("conservative", "medium", "high"):
        for _opp in ((_latest.get("models") or {}).get(_p) or []):
            _strat = _opp.get("strategy", "")
            _opp_intent = _strategy_intent_map.get(_strat, "lend")
            if _opp_intent in _detected:
                _matched_opps.append({
                    "Protocol": _opp.get("protocol", "—"),
                    "Pool":     _opp.get("asset_or_pool", "—"),
                    "Strategy": _strat,
                    "APY":      f"{_opp.get('estimated_apy', 0):.1f}%",
                    "Risk":     _opp.get("il_risk", "—").upper(),
                })

    if _matched_opps:
        st.markdown(f"**Matching opportunities ({len(_matched_opps)}):**")
        st.dataframe(pd.DataFrame(_matched_opps).drop_duplicates(), use_container_width=True, hide_index=True)
    else:
        st.info("No matching opportunities found in current scan. Try running a scan first.")

    # Optional: Claude AI classification for ambiguous intents
    _ai_key = __import__("os").environ.get("ANTHROPIC_API_KEY", "")
    if _ai_key and _used_default:
        with st.spinner("Asking Claude to classify intent…"):
            try:
                import anthropic as _anth
                _cl = _anth.Anthropic(api_key=_ai_key, timeout=8.0)
                _resp = _cl.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=80,
                    messages=[{"role": "user", "content":
                        f"Classify this DeFi intent into ONE of: swap, provide, stake, lend, borrow, claim, bridge, hedge.\n"
                        f"Input: '{_intent_input}'\nReturn only the single word."}],
                )
                _ai_intent = (_resp.content[0].text.strip().lower() if _resp.content else "lend")
                if _ai_intent in _intent_map:
                    st.markdown(f"Claude classified as: **{_intent_map[_ai_intent]['label']}**")
            except Exception:
                pass

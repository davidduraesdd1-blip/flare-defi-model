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
    new_p      = len(digest.get("new_protocols", []))
    news_n     = len(digest.get("news_items", []))
    known_tvl  = digest.get("known_tvl", {})

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
    new_protocols = digest.get("new_protocols", [])
    if new_protocols:
        st.markdown(f"#### New Protocols on Flare ({len(new_protocols)})")
        for proto in new_protocols:
            tvl_str  = f"${proto['tvl_usd']:,.0f}" if proto.get("tvl_usd") else "TVL unknown"
            url_md   = f" · [Visit]({proto['url']})" if proto.get("url") else ""
            desc     = _html.escape(str(proto.get("description", "")))
            st.markdown(
                f"<div class='arb-tag'>"
                f"<span style='font-weight:700; color:#f1f5f9;'>{_html.escape(str(proto['name']))}</span>"
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
                {"Protocol": name, "TVL (USD)": f"${data['tvl_usd']:,.0f}", "Category": data.get("category", "")}
                for name, data in sorted(
                    known_tvl.items(),
                    key=lambda x: x[1].get("tvl_usd", 0) if isinstance(x[1], dict) else 0,
                    reverse=True,
                )
                if isinstance(data, dict)
            ]
            st.dataframe(pd.DataFrame(tvl_rows), use_container_width=True, hide_index=True)

    # News
    news_items = digest.get("news_items", [])
    if news_items:
        st.markdown(
            f"<div style='font-size:0.78rem; font-weight:700; color:#94a3b8; "
            f"text-transform:uppercase; letter-spacing:1.2px; margin:16px 0 10px;'>"
            f"Recent News <span style='color:#334155; font-weight:400;'>({len(news_items)} articles)</span></div>",
            unsafe_allow_html=True,
        )
        for item in news_items[:10]:
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

    errors = digest.get("errors", [])
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
    overall = feedback["overall_health"]
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
    profile_data = feedback["per_profile"] if acc_window == "24h" else feedback.get("per_profile_7d", feedback["per_profile"])

    for p in RISK_PROFILE_NAMES:
        acc   = profile_data.get(p, {})
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
    weights = feedback.get("model_weights", {})
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

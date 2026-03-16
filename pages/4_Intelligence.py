"""
Intelligence — Ecosystem monitor (What's New) and AI model health / accuracy.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd

from ui.common import (
    page_setup, render_sidebar, load_monitor_digest, _ts_fmt,
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

st.markdown("### Ecosystem Monitor")

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
            f"<div class='opp-card' style='border-left:3px solid #3b82f6;'>"
            f"<div style='font-size:0.7rem; color:#475569; letter-spacing:1px; "
            f"text-transform:uppercase; margin-bottom:8px;'>AI Summary</div>"
            f"<div style='color:#94a3b8; font-size:0.9rem; line-height:1.65;'>{ai_text}</div>"
            f"<div style='color:#334155; font-size:0.72rem; margin-top:10px;'>Claude AI · Not financial advice</div>"
            f"</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.82rem;'>"
            "Set ANTHROPIC_API_KEY to enable AI-generated summaries.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # New protocols
    new_protocols = digest.get("new_protocols", [])
    if new_protocols:
        st.markdown(f"#### New Protocols on Flare ({len(new_protocols)})")
        for proto in new_protocols:
            tvl_str  = f"${proto['tvl_usd']:,}" if proto.get("tvl_usd") else "TVL unknown"
            url_md   = f" · [Visit]({proto['url']})" if proto.get("url") else ""
            desc     = proto.get("description", "")
            st.markdown(
                f"<div class='arb-tag'>"
                f"<span style='font-weight:700; color:#f1f5f9;'>{proto['name']}</span>"
                f"<span style='color:#475569;'> · {proto.get('category','?')} · {tvl_str}{url_md}</span>"
                f"{'<div style=color:#64748b;font-size:0.82rem;margin-top:6px>' + desc + '</div>' if desc else ''}"
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
                {"Protocol": name, "TVL (USD)": f"${data['tvl_usd']:,}", "Category": data.get("category", "")}
                for name, data in sorted(known_tvl.items(), key=lambda x: x[1].get("tvl_usd", 0), reverse=True)
            ]
            st.dataframe(tvl_rows, use_container_width=True, hide_index=True)

    # News
    news_items = digest.get("news_items", [])
    if news_items:
        st.markdown(f"#### Recent News ({len(news_items)} articles)")
        for item in news_items[:10]:
            link_md  = f"[{item['title']}]({item['link']})" if item.get("link") else item.get("title", "")
            summary  = item.get("summary", "")
            sum_html = f"<div style='color:#64748b; font-size:0.82rem; margin-top:4px;'>{summary}</div>" if summary else ""
            st.markdown(
                f"<div style='background:#0d1321; border-radius:10px; padding:12px 16px; "
                f"margin-bottom:8px; border:1px solid rgba(255,255,255,0.05);'>"
                f"<div style='color:#94a3b8; font-weight:600;'>{link_md}</div>"
                f"<div style='color:#334155; font-size:0.75rem; margin-top:4px;'>"
                f"{item.get('source','')} · {item.get('published','')}</div>"
                f"{sum_html}"
                f"</div>",
                unsafe_allow_html=True,
            )
        if len(news_items) > 10:
            with st.expander(f"Show all {len(news_items)} articles"):
                for item in news_items[10:]:
                    link_md = f"[{item['title']}]({item['link']})" if item.get("link") else item.get("title", "")
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

st.markdown("### AI Model Health")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
    "How accurately has the model predicted real yields? Updates after each scan.</div>",
    unsafe_allow_html=True,
)

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
    for p in RISK_PROFILE_NAMES:
        acc   = feedback["per_profile"].get(p, {})
        pcfg  = RISK_PROFILES[p]
        pcol  = pcfg["color"]
        grade = acc.get("grade", "N/A")
        score = acc.get("health_score", 50)
        msg   = acc.get("message", "Building history…")
        acc_pct = acc.get("accuracy_pct")
        err_pct = acc.get("avg_error_pct")
        win_rt  = acc.get("win_rate")
        sc      = acc.get("sample_count", 0)

        sc_color = "#10b981" if score >= 70 else ("#f59e0b" if score >= 45 else "#ef4444")

        st.markdown(
            f"<div class='opp-card' style='border-left:3px solid {pcol};'>"
            f"<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
            f"<span style='font-weight:700; color:#f1f5f9;'>{pcfg['label']}</span>"
            f"<div style='display:flex; gap:16px; font-size:0.85rem; color:#475569;'>"
            f"<span>Grade: <span style='color:#f1f5f9; font-weight:700;'>{grade}</span></span>"
            f"<span>Score: <span style='color:{sc_color}; font-weight:700;'>{score}/100</span></span>"
            f"{'<span>Accuracy: <span style=color:#94a3b8>' + str(acc_pct) + '%</span></span>' if acc_pct is not None else ''}"
            f"{'<span>Avg error: <span style=color:#94a3b8>' + str(err_pct) + '%</span></span>' if err_pct is not None else ''}"
            f"{'<span>Win rate: <span style=color:#94a3b8>' + str(win_rt) + '%</span></span>' if win_rt is not None else ''}"
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

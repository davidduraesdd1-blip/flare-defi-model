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
    load_latest, render_fear_greed_trend, render_what_this_means, get_user_level,
)
from scanners.defillama import fetch_governance_alerts, governance_fetch_failed, fetch_all_hacks, fetch_protocol_revenue
from scanners.defi_protocols import fetch_token_unlock_alerts, fetch_tvl_change_alerts
from ai.intent_classifier import classify_defi_intent   # #87


# OPT-42: Cache governance alerts — governance data updates at most hourly
@st.cache_data(ttl=3600)
def _cached_governance_alerts():
    """Cached wrapper for fetch_governance_alerts(). TTL=1 hour."""
    return fetch_governance_alerts()


page_setup("Market Intelligence · Flare DeFi")

ctx        = render_sidebar()
profile    = ctx["profile"]
pro_mode   = ctx.get("pro_mode", False)   # #82 Beginner/Pro mode
demo_mode  = ctx.get("demo_mode", False)  # #67 Demo/Sandbox mode
user_level = ctx.get("user_level", get_user_level())

st.title("🧠 Market Intelligence")

# ── Fear & Greed Trend (Phase 2, item 14) ─────────────────────────────────────
render_section_header("Fear & Greed Index", "Current reading + 7-day + 30-day trend")
render_fear_greed_trend(user_level=user_level)
render_what_this_means(
    "The Fear & Greed Index measures how emotional the crypto market is right now. "
    "0–25 = Extreme Fear (everyone is scared and selling — can be a good time to buy). "
    "25–50 = Fear. 50–75 = Greed (people are excited and buying). "
    "75–100 = Extreme Greed (market is overheated — can be risky to buy). "
    "It's a sentiment signal, not a buy/sell order. Use it alongside other data.",
    title="What is the Fear & Greed Index?",
    intermediate_message="F&G: 0–25 Extreme Fear (capitulation), 75–100 Extreme Greed (overheated). Contrarian signal — not a direct trade trigger.",
)
st.divider()

# ─── Risk & Alert Center ──────────────────────────────────────────────────────

render_section_header("Risk & Alert Center", "Exploit alerts · TVL drops · token unlocks · protocol revenue health")

if not demo_mode:
    _alert_c1, _alert_c2 = st.columns(2)

    # ── Hack / Exploit Alerts ────────────────────────────────────────────────
    with _alert_c1:
        st.markdown("#### ☠️ Recent Exploits & Hacks")
        try:
            @st.cache_data(ttl=86400)
            def _cached_hacks():
                return fetch_all_hacks(limit=50)

            _hacks = _cached_hacks()
            # Show last 10 by date (most recent first)
            _hacks_sorted = sorted(_hacks, key=lambda h: h.get("date", ""), reverse=True)[:10]
            if _hacks_sorted:
                for _h in _hacks_sorted:
                    _hloss = _h.get("funds_lost_usd", 0)
                    _hcolor = "#ef4444" if _hloss >= 10_000_000 else ("#f59e0b" if _hloss >= 1_000_000 else "#6b7280")
                    _hloss_str = (f"${_hloss/1e6:.1f}M" if _hloss >= 1_000_000
                                  else f"${_hloss:,.0f}" if _hloss > 0 else "Undisclosed")
                    _htech = _h.get("technique", _h.get("category", "—"))
                    _hchain = _h.get("chain", "—")
                    st.markdown(
                        f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.06);"
                        f"border-left:3px solid {_hcolor};border-radius:6px;"
                        f"padding:8px 12px;margin-bottom:6px;font-size:0.83rem'>"
                        f"<b style='color:#f1f5f9'>{_html.escape(_h.get('name','Unknown'))}</b>"
                        f"<span style='float:right;color:{_hcolor};font-weight:700'>{_hloss_str}</span><br>"
                        f"<span style='color:#64748b;font-size:0.72rem'>"
                        f"{_h.get('date','')[:10]} · {_html.escape(str(_hchain))} · {_html.escape(str(_htech))}"
                        f"</span></div>",
                        unsafe_allow_html=True,
                    )
                st.caption("Source: DeFiLlama · sorted by date · cached 24h")
            else:
                st.success("No recent exploits in the database.")
        except Exception as _hk_err:
            st.caption(f"Hack data unavailable: {_hk_err}")

    # ── TVL Drop Alerts ──────────────────────────────────────────────────────
    with _alert_c2:
        st.markdown("#### 📉 TVL Drop Alerts")
        try:
            @st.cache_data(ttl=1800)
            def _cached_tvl_alerts():
                return fetch_tvl_change_alerts(threshold_pct=5.0)

            _tvl_alerts = _cached_tvl_alerts()
            if _tvl_alerts:
                for _ta in _tvl_alerts[:10]:
                    _ta_col = "#ef4444" if _ta.get("severity") == "CRITICAL" else "#f59e0b"
                    _ta_chg = _ta.get("change_pct", 0)
                    _ta_now = _ta.get("tvl_now", 0)
                    _ta_fmtb = lambda v: (f"${v/1e9:.2f}B" if v >= 1e9 else f"${v/1e6:.1f}M" if v >= 1e6 else f"${v:,.0f}")
                    st.markdown(
                        f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.06);"
                        f"border-left:3px solid {_ta_col};border-radius:6px;"
                        f"padding:8px 12px;margin-bottom:6px;font-size:0.83rem'>"
                        f"<b style='color:#f1f5f9'>{_html.escape(str(_ta.get('protocol','—')).replace('-',' ').title())}</b>"
                        f"<span style='float:right;color:{_ta_col};font-weight:700'>{_ta_chg:+.1f}%</span><br>"
                        f"<span style='color:#64748b;font-size:0.72rem'>"
                        f"Now: {_ta_fmtb(_ta_now)} · {_ta.get('severity','')}"
                        f"</span></div>",
                        unsafe_allow_html=True,
                    )
                st.caption("Threshold: ≥5% drop vs 24h baseline · cached 30 min")
            else:
                st.success("✓ No significant TVL drops detected right now.")
                st.caption("Monitored: all DeFiLlama-indexed protocols · threshold 5%")
        except Exception as _tvla_err:
            st.caption(f"TVL alert data unavailable: {_tvla_err}")

    st.divider()

    # ── Token Unlock Calendar ─────────────────────────────────────────────────
    _uc1, _uc2 = st.columns([2, 1])
    with _uc1:
        st.markdown("#### 🔓 Token Unlock Calendar (next 30 days)")
    with _uc2:
        _unlock_days = st.selectbox("Window", [7, 14, 30], index=2, key="unlock_window_sel",
                                    label_visibility="collapsed")
    try:
        @st.cache_data(ttl=3600)
        def _cached_unlocks(days: int):
            return fetch_token_unlock_alerts(within_days=days)

        _unlocks = _cached_unlocks(_unlock_days)
        if _unlocks:
            _sev_color = {"CRITICAL": "#ef4444", "WARNING": "#f59e0b", "INFO": "#3b82f6"}
            _unlock_rows = []
            for _ul in _unlocks:
                _ul_c = _sev_color.get(_ul.get("severity", "INFO"), "#6b7280")
                _unlock_rows.append({
                    "Token":      _ul.get("token", "—"),
                    "Date":       _ul.get("date", "—"),
                    "Days":       _ul.get("days_until", "—"),
                    "Unlock %":   f"{_ul.get('amount_pct', 0):.1f}%",
                    "Type":       _ul.get("type", "—"),
                    "Severity":   _ul.get("severity", "—"),
                    "Cliff":      "Yes" if _ul.get("is_cliff") else "No",
                })
            st.dataframe(pd.DataFrame(_unlock_rows), width="stretch", hide_index=True)
            _crit_ul = [u for u in _unlocks if u.get("severity") == "CRITICAL"]
            if _crit_ul:
                st.warning(f"⚠️ {len(_crit_ul)} CRITICAL unlock(s) within {_unlock_days} days — may cause price pressure.")
            st.caption(f"Source: tracked unlock schedule · {_unlock_days}-day window · cached 1h")
        else:
            st.success(f"✓ No significant token unlocks in the next {_unlock_days} days.")
    except Exception as _ul_err:
        st.caption(f"Token unlock data unavailable: {_ul_err}")

    st.divider()

    # ── Protocol Revenue Health ───────────────────────────────────────────────
    render_section_header("Protocol Revenue Health", "DeFiLlama fees API · 24h fees vs 30-day average · trend health")
    try:
        @st.cache_data(ttl=3600)
        def _cached_revenue():
            return fetch_protocol_revenue()

        _rev = _cached_revenue()
        _rev_errors = _rev.get("errors", [])
        _rev_protocols = {k: v for k, v in _rev.items() if k not in ("timestamp", "errors") and isinstance(v, dict)}
        if _rev_protocols:
            _rev_rows = []
            for _slug, _rd in sorted(_rev_protocols.items(), key=lambda x: x[1].get("fees_24h", 0), reverse=True):
                _rh = _rd.get("health", "RED")
                _rh_icon = {"GREEN": "✅", "YELLOW": "⚠️", "RED": "❌"}.get(_rh, "—")
                _rt = _rd.get("trend", 0)
                _f24 = _rd.get("fees_24h", 0)
                _f30 = _rd.get("fees_30d", 0)
                _daily_avg = _f30 / 30 if _f30 > 0 else 0
                _rev_rows.append({
                    "Protocol":    _slug.replace("-", " ").title(),
                    "24h Fees":    f"${_f24:,.0f}",
                    "30d Avg/Day": f"${_daily_avg:,.0f}",
                    "Trend":       f"{_rt:.2f}×",
                    "Health":      f"{_rh_icon} {_rh}",
                })
            st.dataframe(pd.DataFrame(_rev_rows), width="stretch", hide_index=True)
            st.caption(
                "Trend = 24h fees ÷ 30-day daily avg. "
                "✅ >0.9× healthy · ⚠️ 0.5–0.9× slowing · ❌ <0.5× revenue decline · "
                f"Source: DeFiLlama · {_rev.get('timestamp','')[:19]} UTC · cached 1h"
            )
            if user_level == "Beginner":
                st.info(
                    "Protocol revenue shows how much money a DeFi protocol earned in fees today "
                    "compared to its recent average. A protocol earning less than usual may be losing users, "
                    "which can affect yields over time."
                )
        else:
            st.info("Revenue data loading… (DeFiLlama fees API)")
    except Exception as _rev_err:
        st.caption(f"Protocol revenue data unavailable: {_rev_err}")

else:
    st.info("Risk & Alert Center is disabled in Demo Mode.", icon="🎭")

st.divider()

st.caption("Ecosystem monitor, governance alerts, AI model accuracy, and protocol revenue health")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Ecosystem monitor · new protocols · news · AI model accuracy</div>",
    unsafe_allow_html=True,
)


# ─── DeFi Assistant (#87) ─────────────────────────────────────────────────────

render_section_header("DeFi Assistant", "Ask any DeFi question — AI detects your intent and surfaces relevant data")

try:
    if demo_mode:
        st.info("DeFi Assistant is disabled in Demo Mode. Toggle Demo Mode off in the sidebar to use it.")
        _defi_query = ""
    else:
        _defi_query = st.text_input(
            "Ask about DeFi (e.g. 'best place to stake ETH', 'how to LP on Aerodrome', 'compare restaking yields')",
            key="defi_assistant_query",
            placeholder="Type your DeFi question here…",
        )

    if not demo_mode and _defi_query and _defi_query.strip():
        with st.spinner("Classifying intent…"):
            _intent_result = classify_defi_intent(_defi_query.strip())

        _primary    = _intent_result.get("primary", "OTHER")
        _secondary  = _intent_result.get("secondary")
        _conf       = _intent_result.get("confidence", 0.0)
        _action     = _intent_result.get("suggested_action", "")
        _src        = _intent_result.get("source", "keyword_fallback")
        _src_label  = "Claude AI" if _src == "claude_haiku" else "keyword matching"

        _conf_pct   = round(_conf * 100)
        _intent_col = {
            "SWAP":             "#3b82f6",
            "PROVIDE_LIQUIDITY":"#22c55e",
            "STAKE":            "#8b5cf6",
            "BORROW":           "#f59e0b",
            "LEND":             "#14b8a6",
            "CLAIM_REWARDS":    "#84cc16",
            "BRIDGE":           "#ec4899",
            "PORTFOLIO_CHECK":  "#64748b",
            "YIELD_HUNT":       "#f97316",
            "RISK_ASSESSMENT":  "#ef4444",
            "OTHER":            "#475569",
        }.get(_primary, "#475569")

        _sec_str = f" · secondary: **{_secondary}**" if _secondary else ""
        st.markdown(
            f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.07);"
            f"border-left:3px solid {_intent_col};border-radius:8px;padding:12px 16px;margin:8px 0'>"
            f"<div style='margin-bottom:6px'>"
            f"Intent detected: <span style='font-weight:700;color:{_intent_col};font-size:1.05rem'>"
            f"{_primary}</span> ({_conf_pct}% confidence){_sec_str}"
            f"<span style='float:right;font-size:0.68rem;color:#334155'>via {_src_label}</span>"
            f"</div>"
            f"<div style='color:#c4cbdb;font-size:0.87rem'>{_html.escape(_action)}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

        render_what_this_means(
            f"Your question was classified as: '{_primary}'. "
            "This tells the model what kind of DeFi action you're looking for so it can show you the most relevant data. "
            f"The confidence ({_conf_pct}%) shows how certain the model is about this classification. "
            "If the intent is wrong, try rephrasing — e.g. 'best place to stake FLR' instead of just 'staking'.",
            title="What does this intent detection mean?",
            intermediate_message=f"Intent: {_primary} ({_conf_pct}% confidence). Model routing query to relevant data feeds and opportunity scanner.",
        )

        # Context-relevant data based on intent
        if _primary in ("STAKE", "YIELD_HUNT", "LEND"):
            st.markdown(
                "<div style='color:#475569;font-size:0.80rem;margin:10px 0 4px'>"
                "Relevant: top yield opportunities — check the Opportunities tab for live APY data.</div>",
                unsafe_allow_html=True,
            )
            try:
                from scanners.defillama import fetch_yields_pools as _fetch_yp
                _assist_pools = _fetch_yp(min_tvl_usd=10_000_000, max_results=5)
                if _assist_pools:
                    _assist_rows = []
                    for _ap in _assist_pools[:5]:
                        _assist_rows.append({
                            "Protocol": str(_ap.get("project", "—")).replace("-", " ").title(),
                            "Pool":     _ap.get("symbol", "—"),
                            "Chain":    _ap.get("chain", "—"),
                            "APY %":    f"{float(_ap.get('apy') or 0):.2f}%",
                            "TVL":      (f"${float(_ap.get('tvlUsd',0))/1e6:.0f}M"
                                         if float(_ap.get('tvlUsd',0)) >= 1e6
                                         else f"${float(_ap.get('tvlUsd',0)):,.0f}"),
                        })
                    st.dataframe(pd.DataFrame(_assist_rows), width="stretch", hide_index=True)
            except Exception:
                pass

        elif _primary == "PROVIDE_LIQUIDITY":
            st.info("Head to the **Opportunities** tab — use the Multi-Chain Pools and Solana DeFi sections to compare LP yields.")

        elif _primary in ("BORROW",):
            st.info("Compare borrow rates in the Multi-Chain Pools section (Opportunities tab). Aave v3 and Morpho are shown there.")

        elif _primary == "PORTFOLIO_CHECK":
            st.info("Your portfolio is on the **Portfolio** tab (tab 1).")

        elif _primary == "RISK_ASSESSMENT":
            st.info("Protocol risk scores are shown in the Opportunities tab — look for the 'Risk Score' column in the Multi-Chain Pools table.")

        elif _primary == "BRIDGE":
            st.info("Use Li.Fi or Stargate for cross-chain transfers. TVL and bridge flow data is in the Opportunities tab (TVL Change Alerts section).")

except Exception as _assist_exc:
    st.info("DeFi Assistant unavailable. Check logs for details.")

st.divider()


# ─── What's New ───────────────────────────────────────────────────────────────

render_section_header("Ecosystem Monitor", "New protocols · recent news · on-chain activity")

# Demo Mode: skip live fetches, show placeholder (#67)
if demo_mode:
    st.warning(
        "Demo Mode — live API fetches are disabled. Showing sample analysis placeholder.",
        icon="🎭",
    )
    st.markdown(
        "<div style='background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.18);"
        "border-radius:10px;padding:16px 20px;font-size:0.92rem'>"
        "<b>Sample Analysis</b><br><br>"
        "In Demo Mode, the Intelligence page skips all live network calls. "
        "When running with real data, this section shows:<br><br>"
        "• New Flare protocols discovered by the web monitor<br>"
        "• On-chain activity summaries from DeFiLlama<br>"
        "• AI-generated ecosystem digest (requires ANTHROPIC_API_KEY)<br>"
        "• Active governance proposals from Snapshot<br>"
        "• Model accuracy and feedback loop metrics"
        "</div>",
        unsafe_allow_html=True,
    )
    st.stop()

with st.spinner("Analyzing on-chain data..."):
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

    st.divider()

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
            st.dataframe(pd.DataFrame(tvl_rows), width="stretch", hide_index=True)

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

st.divider()


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

    st.divider()
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
        st.divider()
        st.markdown("#### Model Confidence Weights")
        st.markdown(
            "<div style='color:#475569; font-size:0.82rem; margin-bottom:10px;'>"
            "Higher accuracy = higher weight. Adjusted automatically after each scan.</div>",
            unsafe_allow_html=True,
        )
        rows = [{"Profile": p.capitalize(), "Weight": f"{w:.4f}",
                 "Effect": "Boosted" if w > 1.0 else ("Reduced" if w < 0.9 else "Neutral")}
                for p, w in weights.items()]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

st.divider()


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
    _ts = _mf.fetch_macro_timeseries(max(90, _corr_w * 3))

    if _ts and "BTC" in _ts:
        _frames: dict = {}
        for _key in ["BTC", "VIX", "Gold", "SPX", "DXY", "Oil"]:
            _s = _ts.get(_key)
            if _s and isinstance(_s, dict):
                _frames[_key] = pd.Series(_s)
        if len(_frames) >= 2:
            _dft = pd.DataFrame(_frames).sort_index().ffill()
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
            st.plotly_chart(_fig, width="stretch")
        else:
            st.info("Loading macro timeseries… (yfinance required)")
    else:
        st.info("Install yfinance for BTC macro correlations: `pip install yfinance`")

except Exception as _macro_err:
    st.caption(f"Macro data unavailable: {_macro_err}")

# ─── Blood in the Streets · DCA Multiplier (Group 3) ─────────────────────────
st.divider()
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
st.divider()
render_section_header("On-Chain Intelligence", "CoinMetrics Community API · MVRV Z-Score · SOPR · no API key required")

try:
    import macro_feeds as _mf4
    _oc4 = _mf4.fetch_coinmetrics_onchain(days=400)

    if _oc4.get("error") and not _oc4.get("mvrv_z"):
        st.info(f"On-chain data unavailable. {_oc4.get('error')}")
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
            st.plotly_chart(_fig_mz, width="stretch")

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
                st.plotly_chart(_fig5d, width="stretch")

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
                st.plotly_chart(_fig5e, width="stretch")

        _ts5_txt = _oc5.get("timestamp", "")[:19]
        st.caption(f"Source: Deribit · {_ts5_txt} UTC · Cached 15 min")
except Exception as _opt_err:
    st.caption(f"Options data unavailable: {_opt_err}")

st.divider()


# ── Governance Alerts (#74) ────────────────────────────────────────────────────
# Active Snapshot proposals that may impact APY/rates for tracked protocols

render_section_header(
    "Governance Alerts",
    "Active Snapshot votes that may impact yield parameters — vote before the deadline",
)

_gov_demo   = ctx.get("demo_mode", False)
_gov_pro    = pro_mode

with st.spinner("Checking active governance proposals…"):
    if _gov_demo:
        _gov_proposals = [
            {"title": "Adjust USDC borrow rate parameters", "protocol": "aave.eth",
             "space": "aave.eth", "votes": 1842, "ends_at": "2026-04-01", "apy_impact": True,
             "url": "https://snapshot.org/#/aave.eth"},
            {"title": "Enable new fee tier for USDC/USDT", "protocol": "uniswap",
             "space": "uniswap", "votes": 3210, "ends_at": "2026-04-03", "apy_impact": True,
             "url": "https://snapshot.org/#/uniswap"},
            {"title": "Adjust emission schedule for LP rewards", "protocol": "aerodrome.eth",
             "space": "aerodrome.eth", "votes": 503, "ends_at": "2026-03-30", "apy_impact": True,
             "url": "https://snapshot.org/#/aerodrome.eth"},
        ]
    else:
        _gov_proposals = _cached_governance_alerts()

if _gov_proposals:
    _gov_apy_props   = [p for p in _gov_proposals if p.get("apy_impact")]
    _gov_other_props = [p for p in _gov_proposals if not p.get("apy_impact")]
    _gov_sorted      = _gov_apy_props + _gov_other_props

    if _gov_apy_props:
        st.markdown(
            f"<div style='background:rgba(251,191,36,0.06);border:1px solid rgba(251,191,36,0.20);"
            f"border-radius:8px;padding:8px 14px;margin-bottom:12px;font-size:0.82rem;color:#FBBF24'>"
            f"⚡ {len(_gov_apy_props)} active proposal(s) flagged as APY-impacting</div>",
            unsafe_allow_html=True,
        )

    for _gp in _gov_sorted:
        _g_impact = _gp.get("apy_impact")
        _g_border = "#FBBF24" if _g_impact else "#334155"
        _g_badge  = (
            " <span style='background:#1c1200;color:#FBBF24;font-size:0.68rem;"
            "padding:1px 6px;border-radius:4px;border:1px solid #fbbf2444'>⚡ APY Impact</span>"
            if _g_impact else ""
        )
        _g_url    = _html.escape(str(_gp.get("url") or ""))
        _g_link   = (
            f" · <a href='{_g_url}' target='_blank' "
            f"style='color:#a78bfa;font-size:0.72rem;font-weight:600;text-decoration:none;'>Vote ↗</a>"
            if _g_url else ""
        )
        _g_votes  = _gp.get("votes", 0)
        _g_space  = _html.escape(str(_gp.get("space", _gp.get("protocol", "—"))))
        _g_ends   = _html.escape(str(_gp.get("ends_at", _gp.get("end_date", "—"))))
        _g_title  = _html.escape(str(_gp.get("title", "")))

        # Pro mode shows scores_total
        _g_extra = ""
        if _gov_pro:
            _g_scores = _gp.get("scores_total", 0)
            if _g_scores:
                _g_extra = f" · {_g_scores:,.0f} total votes"

        st.markdown(
            f"<div style='background:rgba(0,0,0,0.15);border:1px solid rgba(255,255,255,0.05);"
            f"border-left:3px solid {_g_border};border-radius:6px;"
            f"padding:9px 14px;margin-bottom:7px;font-size:0.86rem'>"
            f"<b>{_g_title}</b>{_g_badge}<br>"
            f"<span style='color:#64748b;font-size:0.74rem'>"
            f"{_g_space} · {_g_votes:,} votes{_g_extra} · ends {_g_ends}"
            f"{_g_link}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    st.caption(
        f"{len(_gov_proposals)} active proposals · {len(_gov_apy_props)} APY-impacting. "
        "Source: Snapshot GraphQL · cached 1 hour."
    )
else:
    # Only show the "all clear" message when the fetch actually succeeded and
    # returned an empty list.  If the Snapshot API was unreachable we show a
    # neutral info message instead of a false positive "no votes" confirmation.
    if _gov_demo or not governance_fetch_failed():
        st.success("✓ No active governance votes affecting APY right now.")
    else:
        st.info("Governance data temporarily unavailable (Snapshot API). Check back later.")

st.divider()


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
        st.dataframe(pd.DataFrame(_rows), width="stretch", hide_index=True)
    st.caption("FTSO oracle prices refresh every 2 min. Divergence >2% may indicate arb opportunity. Source: Flare Data Availability Layer.")

st.divider()


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
        st.dataframe(pd.DataFrame(_matched_opps).drop_duplicates(), width="stretch", hide_index=True)
    else:
        st.info("No matching opportunities found in current scan. Try running a scan first.")

    # Optional: Claude AI classification for ambiguous intents
    from config import FEATURES as _FEATURES
    if _FEATURES.get("anthropic_ai") and _used_default:
        _ai_key = __import__("os").environ.get("ANTHROPIC_API_KEY", "")
        with st.spinner("Asking Claude to classify intent…"):
            try:
                import anthropic as _anth
                _cl = _anth.Anthropic(api_key=_ai_key, timeout=8.0)
                _resp = _cl.messages.create(
                    model="claude-haiku-4-5",
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


# ─── Protocol Revenue Health (#57) ───────────────────────────────────────────

if pro_mode:
    st.divider()
    render_section_header(
        "Protocol Revenue Health",
        "24h vs 30-day average fee revenue — real demand signal for each protocol",
    )

    try:
        from scanners.defillama import fetch_protocol_revenue as _fetch_pr

        with st.spinner("Loading protocol fee revenue data…"):
            if demo_mode:
                _rev_data = {
                    "aave-v3":      {"fees_24h": 820_000, "fees_30d": 21_000_000, "trend": 1.17, "health": "GREEN"},
                    "lido":         {"fees_24h": 1_200_000, "fees_30d": 39_000_000, "trend": 0.92, "health": "GREEN"},
                    "uniswap":      {"fees_24h": 3_500_000, "fees_30d": 130_000_000, "trend": 0.81, "health": "GREEN"},
                    "compound-v3":  {"fees_24h": 95_000, "fees_30d": 3_500_000, "trend": 0.81, "health": "GREEN"},
                    "curve-dex":    {"fees_24h": 210_000, "fees_30d": 8_500_000, "trend": 0.74, "health": "YELLOW"},
                    "pendle":       {"fees_24h": 180_000, "fees_30d": 9_000_000, "trend": 0.60, "health": "YELLOW"},
                    "morpho":       {"fees_24h": 55_000, "fees_30d": 900_000, "trend": 1.83, "health": "GREEN"},
                    "aerodrome-v2": {"fees_24h": 420_000, "fees_30d": 11_000_000, "trend": 1.15, "health": "GREEN"},
                    "timestamp":    "2026-03-27T00:00:00Z",
                    "errors":       [],
                }
            else:
                _rev_data = _fetch_pr()

        _rev_rows = []
        for _slug, _rdata in _rev_data.items():
            if _slug in ("timestamp", "errors") or not isinstance(_rdata, dict):
                continue
            _f24 = _rdata.get("fees_24h", 0)
            _f30 = _rdata.get("fees_30d", 0)
            _trend = _rdata.get("trend", 0)
            _health = _rdata.get("health", "RED")
            _hcolor = "#22c55e" if _health == "GREEN" else ("#f59e0b" if _health == "YELLOW" else "#ef4444")
            _rev_rows.append({
                "Protocol":  _slug.replace("-", " ").title(),
                "24h Fees":  (f"${_f24/1e6:.2f}M" if _f24 >= 1e6 else f"${_f24:,.0f}"),
                "30d Fees":  (f"${_f30/1e6:.1f}M" if _f30 >= 1e6 else f"${_f30:,.0f}"),
                "Trend":     f"{_trend:.2f}x",
                "_health":   _health,
                "_hcolor":   _hcolor,
            })

        if _rev_rows:
            for _rr in _rev_rows:
                _hbadge = (
                    f"<span style='background:rgba(34,197,94,0.12);color:#22c55e;"
                    f"padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:600'>{_rr['_health']}</span>"
                    if _rr["_health"] == "GREEN" else
                    f"<span style='background:rgba(245,158,11,0.12);color:#f59e0b;"
                    f"padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:600'>{_rr['_health']}</span>"
                    if _rr["_health"] == "YELLOW" else
                    f"<span style='background:rgba(239,68,68,0.12);color:#ef4444;"
                    f"padding:2px 8px;border-radius:4px;font-size:0.78rem;font-weight:600'>{_rr['_health']}</span>"
                )
                st.markdown(
                    f"<div style='background:rgba(0,0,0,0.15);border:1px solid rgba(255,255,255,0.05);"
                    f"border-left:3px solid {_rr['_hcolor']};border-radius:6px;"
                    f"padding:8px 14px;margin-bottom:6px;font-size:0.85rem;"
                    f"display:flex;justify-content:space-between;align-items:center'>"
                    f"<div>"
                    f"<b style='color:#f1f5f9'>{_html.escape(_rr['Protocol'])}</b>"
                    f"<span style='color:#64748b;font-size:0.78rem;margin-left:10px'>"
                    f"24h: {_html.escape(_rr['24h Fees'])} · 30d: {_html.escape(_rr['30d Fees'])} · "
                    f"Trend: {_html.escape(_rr['Trend'])}</span>"
                    f"</div>"
                    f"<div>{_hbadge}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            st.caption(
                "Fee revenue = real demand for the protocol's services. "
                "Declining fees can signal reduced usage before TVL drops. "
                "Trend: 24h fees vs 30-day daily average. Source: DeFiLlama · cached 1h."
            )
        else:
            st.info("Protocol revenue data unavailable. Check API connectivity.")

    except Exception as _pr_err:
        st.caption(f"Protocol revenue data unavailable: {_pr_err}")


# ─── RWA Credit Protocol Health (#58) ────────────────────────────────────────

if pro_mode:
    st.divider()
    render_section_header(
        "RWA Credit Protocol Health",
        "Centrifuge · Maple Finance · Clearpool · Goldfinch — TVL trends and health signals",
    )

    try:
        from scanners.defi_protocols import fetch_rwa_credit_health as _fetch_rwa

        with st.spinner("Loading RWA credit protocol data…"):
            if demo_mode:
                _rwa_data = {
                    "centrifuge": {
                        "tvl_usd": 340_000_000, "tvl_7d_change_pct": 2.1,
                        "tvl_30d_change_pct": 8.4, "chains": ["Ethereum", "Centrifuge"],
                        "health": "GROWING",
                    },
                    "maple": {
                        "tvl_usd": 190_000_000, "tvl_7d_change_pct": -1.2,
                        "tvl_30d_change_pct": -3.5, "chains": ["Ethereum", "Solana"],
                        "health": "STABLE",
                    },
                    "clearpool": {
                        "tvl_usd": 46_000_000, "tvl_7d_change_pct": 0.8,
                        "tvl_30d_change_pct": -1.2, "chains": ["Ethereum", "Flare"],
                        "health": "STABLE",
                    },
                    "goldfinch": {
                        "tvl_usd": 82_000_000, "tvl_7d_change_pct": -3.1,
                        "tvl_30d_change_pct": -12.5, "chains": ["Ethereum"],
                        "health": "DECLINING",
                    },
                    "timestamp": "2026-03-27T00:00:00Z",
                }
            else:
                _rwa_data = _fetch_rwa()

        _HEALTH_COLORS = {"GROWING": "#22c55e", "STABLE": "#f59e0b", "DECLINING": "#ef4444"}
        _HEALTH_BADGES = {
            "GROWING":   "background:rgba(34,197,94,0.12);color:#22c55e",
            "STABLE":    "background:rgba(245,158,11,0.12);color:#f59e0b",
            "DECLINING": "background:rgba(239,68,68,0.12);color:#ef4444",
        }

        _rwa_cols = st.columns(2)
        _col_idx  = 0
        for _rwa_name, _rwa_entry in _rwa_data.items():
            if _rwa_name == "timestamp" or not isinstance(_rwa_entry, dict):
                continue
            _tvl     = _rwa_entry.get("tvl_usd", 0)
            _c7d     = _rwa_entry.get("tvl_7d_change_pct", 0)
            _c30d    = _rwa_entry.get("tvl_30d_change_pct", 0)
            _chains  = _rwa_entry.get("chains", [])
            _health  = _rwa_entry.get("health", "STABLE")
            _hcol    = _HEALTH_COLORS.get(_health, "#9ca3af")
            _hbg     = _HEALTH_BADGES.get(_health, "background:rgba(156,163,175,0.12);color:#9ca3af")
            _tvl_str = (f"${_tvl/1e9:.2f}B" if _tvl >= 1e9
                        else f"${_tvl/1e6:.1f}M" if _tvl >= 1e6
                        else f"${_tvl:,.0f}")
            _c7_str    = f"{_c7d:+.1f}%"
            _c30_str   = f"{_c30d:+.1f}%"
            _c7_color  = "#22c55e" if _c7d >= 0 else "#ef4444"
            _c30_color = "#22c55e" if _c30d >= 0 else "#ef4444"
            _chains_str = ", ".join(_chains[:3]) if _chains else "—"

            with _rwa_cols[_col_idx % 2]:
                st.markdown(
                    f"<div style='background:rgba(0,0,0,0.18);border:1px solid rgba(255,255,255,0.07);"
                    f"border-left:3px solid {_hcol};border-radius:8px;padding:14px 16px;margin-bottom:10px'>"
                    f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:8px'>"
                    f"<span style='font-weight:700;font-size:0.95rem;color:#f1f5f9'>"
                    f"{_html.escape(_rwa_name.replace('_', ' ').title())}</span>"
                    f"<span style='font-size:0.78rem;font-weight:600;padding:2px 8px;"
                    f"border-radius:4px;{_hbg}'>{_health}</span>"
                    f"</div>"
                    f"<div style='font-size:1.25rem;font-weight:700;color:#e2e8f0;margin-bottom:6px'>{_tvl_str}</div>"
                    f"<div style='display:flex;gap:16px;font-size:0.80rem;color:#64748b'>"
                    f"<span>7d: <span style='color:{_c7_color}'>{_c7_str}</span></span>"
                    f"<span>30d: <span style='color:{_c30_color}'>{_c30_str}</span></span>"
                    f"<span>Chains: {_html.escape(_chains_str)}</span>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            _col_idx += 1

        st.caption(
            "Health: GROWING = 30d TVL +5% · DECLINING = 30d TVL -10% · STABLE = in between. "
            "Source: DeFiLlama protocol API · Cached 15 min."
        )

    except Exception as _rwa_err:
        st.caption(f"RWA credit data unavailable: {_rwa_err}")

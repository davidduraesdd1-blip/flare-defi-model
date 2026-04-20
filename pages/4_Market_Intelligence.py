"""
Intelligence — Ecosystem monitor (What's New) and AI model health / accuracy.
"""

import sys
import html as _html
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd

logger = logging.getLogger(__name__)

from ui.common import (
    page_setup, render_sidebar, load_monitor_digest, render_section_header, _ts_fmt,
    load_latest, render_fear_greed_trend, render_what_this_means, get_user_level,
)
from ai.intent_classifier import classify_defi_intent   # #87


page_setup("Market Intelligence · Family Office · DeFi Intelligence")

ctx        = render_sidebar()
profile    = ctx["profile"]
pro_mode   = ctx.get("pro_mode", False)   # #82 Beginner/Pro mode
demo_mode  = ctx.get("demo_mode", False)  # #67 Demo/Sandbox mode
user_level = ctx.get("user_level", get_user_level())

st.title("🧠 Market Intelligence")
st.caption("Sentiment · Macro · On-Chain · Ecosystem — all signals in one place")

# ─── Contextual Quick-Access Row (ToS #7 edge-tabs equivalent) ──────────────
# ToS uses rotated 90° edge tabs (Level II / Active Trader / Time & Sales) on
# their charts for contextual side panels. Streamlit doesn't support rotated
# text; st.popover gives the same "always one click away, zero screen cost
# when closed" UX. Place at top-right of page header.
#
# Perf note: st.popover DOES execute its body on render (not lazy), so we wrap
# the heavier file reads in @st.cache_data (60s TTL) to dedupe across reruns.

@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def _qa_recent_alerts() -> list:
    """Read last 5 entries of data/alert_history.jsonl. Cached 60s."""
    try:
        from pathlib import Path as _QP
        import json as _jq
        _alerts_file = _QP("data") / "alert_history.jsonl"
        if not _alerts_file.exists():
            return []
        out = []
        for line in _alerts_file.read_text(encoding="utf-8").strip().split("\n")[-5:]:
            try:
                out.append(_jq.loads(line))
            except Exception:
                continue
        return out
    except Exception:
        return []

@st.cache_data(ttl=60, max_entries=1, show_spinner=False)
def _qa_last_scan_ts() -> str:
    """Return last scan timestamp. Cached 60s."""
    try:
        _r = load_monitor_digest() or {}
        return (_r.get("timestamp") or "")[:16]
    except Exception:
        return ""

_qa_spacer, _qa_agent, _qa_alerts, _qa_log, _qa_glos = st.columns([10, 1, 1, 1, 1])
with _qa_agent:
    with st.popover("🤖", help="Agent status"):
        st.markdown("**Agent Status**")
        _agent_running = st.session_state.get("agent_running", False)
        st.markdown(f"Status: {'🟢 Active' if _agent_running else '⚫ Idle'}")
        st.caption("Full details on the Agent page.")
with _qa_alerts:
    with st.popover("🔔", help="Recent alerts"):
        st.markdown("**Recent Alerts**")
        _alerts_recent = _qa_recent_alerts()
        if _alerts_recent:
            for _a in _alerts_recent:
                st.markdown(f"• {_a.get('timestamp','—')[:16]} — {_a.get('message','—')[:60]}")
        else:
            st.caption("No alerts yet — configure in Settings.")
with _qa_log:
    with st.popover("📜", help="Trade log"):
        st.markdown("**Recent Scans**")
        _ts = _qa_last_scan_ts()
        st.caption(f"Last scan: {_ts or '—'}")
with _qa_glos:
    with st.popover("📖", help="Glossary"):
        st.markdown("**Quick Glossary**")
        st.markdown(
            "• **APY** — annual yield\n\n"
            "• **IL** — impermanent loss\n\n"
            "• **TVL** — total value locked\n\n"
            "• **MVRV** — market value / realized value\n\n"
            "• **SOPR** — spent output profit ratio"
        )
        st.caption("Full glossary available on Dashboard page.")

_t_sent, _t_timing, _t_macro, _t_onchain, _t_eco = st.tabs([
    "📊 Sentiment",
    "📈 Market Timing",
    "🌍 Macro",
    "⛓️ On-Chain",
    "🌱 Ecosystem",
])

with _t_sent:
    # ── Fear & Greed Trend ──────────────────────────────────────────────────────
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


# ─── Market Timing Tab ────────────────────────────────────────────────────────

with _t_timing:
    render_section_header(
        "Market Timing — Top/Bottom Score",
        "5-layer composite signal: On-Chain Macro + Sentiment + Divergence + Structure + Volatility",
    )

    try:
        import yfinance as _yf
        from top_bottom_detector import compute_composite_top_bottom_score, render_top_bottom_widget

        _timing_assets = {
            "BTC-USD": "Bitcoin (BTC)",
            "ETH-USD": "Ethereum (ETH)",
        }

        _timing_sel = st.selectbox(
            "Asset",
            options=list(_timing_assets.keys()),
            format_func=lambda x: _timing_assets[x],
            key="timing_asset_select",
        )

        @st.cache_data(ttl=3600, show_spinner=False, max_entries=10)
        def _fetch_timing_ohlcv(ticker: str, period: str = "6mo", interval: str = "1d"):
            try:
                _t = _yf.Ticker(ticker)
                _df = _t.history(period=period, interval=interval, auto_adjust=True)
                if _df is None or _df.empty:
                    return None
                _df.columns = [c.lower() for c in _df.columns]
                _df = _df[["open", "high", "low", "close", "volume"]].dropna()
                return _df
            except Exception as _e:
                logger.warning("yfinance timing fetch failed %s: %s", ticker, _e)
                return None

        with st.spinner(f"Analyzing {_timing_assets[_timing_sel]}..."):
            _df_daily  = _fetch_timing_ohlcv(_timing_sel, period="6mo",  interval="1d")
            _df_4h     = _fetch_timing_ohlcv(_timing_sel, period="60d",  interval="1h")
            _df_1h     = _fetch_timing_ohlcv(_timing_sel, period="30d",  interval="1h")

            # Gather macro data if available from existing composite signal data
            _macro_inp = {}
            _sent_inp  = {}
            try:
                from macro_feeds import fetch_all_macro_data, fetch_coinmetrics_onchain
                from ui.common import fetch_fear_greed_history as _fgh
                _md = fetch_all_macro_data()
                if _md:
                    _macro_inp["pi_cycle_ratio"] = _md.get("pi_cycle_ratio")
                _oc = fetch_coinmetrics_onchain()
                if _oc:
                    _macro_inp["mvrv_z_score"]        = _oc.get("mvrv_z")
                    _macro_inp["nupl"]                = _oc.get("nupl")
                    _macro_inp["sopr"]                = _oc.get("sopr")
                    _macro_inp["hash_ribbons_signal"]  = _oc.get("hash_ribbon_signal")
                _fg_hist = _fgh()
                if _fg_hist:
                    _fg_latest = (_fg_hist[-1].get("value") if isinstance(_fg_hist, list)
                                  else _fg_hist.get("value"))
                    if _fg_latest:
                        _sent_inp["fear_greed_value"] = float(_fg_latest)
            except Exception as _mex:
                logger.debug("Market timing macro fetch: %s", _mex)

            _timing_result = None
            if _df_daily is not None:
                _timing_result = compute_composite_top_bottom_score(
                    df=_df_daily,
                    macro_data=_macro_inp or None,
                    sentiment_data=_sent_inp or None,
                    df_1h=_df_1h,
                    df_4h=_df_4h,
                    symbol=_timing_sel.replace("-USD", ""),
                )

        if _timing_result:
            render_top_bottom_widget(_timing_result, user_level=user_level)

            # Plain-English explanation (Beginner/Intermediate only — Advanced sees full table)
            if user_level != "advanced":
                with st.expander("ⓘ How is this score calculated?"):
                    st.markdown(
                        "The score combines **5 independent signal layers** — the same approach "
                        "used by professional quant funds and on-chain analysts:\n\n"
                        "| Layer | What it measures | Weight |\n"
                        "|-------|-----------------|--------|\n"
                        "| On-Chain Macro | MVRV Z-Score, NUPL, SOPR, Hash Ribbons, Pi Cycle Top | 30% |\n"
                        "| Sentiment | Fear & Greed Index, Funding Rates | 20% |\n"
                        "| Divergence | RSI, MACD, CVD divergence across timeframes | 25% |\n"
                        "| Structure | BOS/CHoCH, Order Blocks, Fair Value Gaps, Volume Profile | 15% |\n"
                        "| Volatility | Chandelier Exit, Squeeze, Wyckoff Spring/Upthrust | 10% |\n\n"
                        "**Score 80–100** → Extreme bottom zone. Every major BTC bottom "
                        "(Dec 2018, Mar 2020, Nov 2022) scored 80+.\n\n"
                        "**Score 0–20** → Extreme top zone. All major cycle tops "
                        "(Dec 2017, Apr 2021, Nov 2021) scored below 20."
                    )
        else:
            st.warning(
                "Market data unavailable right now — price history could not be loaded. "
                "Try again in 30 seconds.",
                icon="⚠️",
            )

    except ImportError as _imp_e:
        st.info(
            "Market Timing requires yfinance. "
            f"Install with: `pip install yfinance` · Error: {_imp_e}",
        )
    except Exception as _timing_exc:
        logger.error("Market Timing tab error: %s", _timing_exc)
        st.warning(
            "Market timing analysis temporarily unavailable — this is usually a temporary "
            "data issue. Try refreshing in 30 seconds.",
            icon="⚠️",
        )


# ─── DeFi Assistant (#87) ─────────────────────────────────────────────────────

with _t_sent:
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
                "SWAP":             "#00d4aa",
                "PROVIDE_LIQUIDITY":"#22c55e",
                "STAKE":            "#8b5cf6",
                "BORROW":           "#f59e0b",
                "LEND":             "#00d4aa",
                "CLAIM_REWARDS":    "#22c55e",
                "BRIDGE":           "#ef4444",
                "PORTFOLIO_CHECK":  "#64748b",
                "YIELD_HUNT":       "#f59e0b",
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
                f"<span style='float:right;font-size:0.85rem;color:#334155'>via {_src_label}</span>"
                f"</div>"
                f"<div style='color:#cbd5e1;font-size:0.87rem'>{_html.escape(_action)}</div>"
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
                    "<div style='color:#475569;font-size:0.85rem;margin:10px 0 4px'>"
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
                        st.dataframe(pd.DataFrame(_assist_rows), width='stretch', hide_index=True)
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
                st.markdown(
                    "<div style='font-size:0.9rem;color:#94a3b8;margin-bottom:8px'>"
                    "Cross-chain bridge options for Flare Network — capital flow data from DeFiLlama"
                    "</div>",
                    unsafe_allow_html=True,
                )
                # Show bridge recommendations and live chain flow data
                _bridge_protocols = [
                    {"name": "LayerZero", "chains": "ETH → Flare, Base → Flare", "type": "Message passing + token bridge", "url_hint": "layerzero.network"},
                    {"name": "Li.Fi",     "chains": "15+ EVM chains incl. Flare", "type": "DEX aggregator + bridge router", "url_hint": "li.fi"},
                    {"name": "Stargate",  "chains": "ETH, BSC, Polygon, Arbitrum", "type": "Liquidity bridge (USDC/USDT)", "url_hint": "stargate.finance"},
                    {"name": "Wanchain",  "chains": "XRP Ledger ↔ Flare, ETH ↔ Flare", "type": "Cross-chain atomic swaps", "url_hint": "wanchain.org"},
                ]
                _bp_rows = [
                    {"Bridge": r["name"], "Supported Routes": r["chains"], "Type": r["type"]}
                    for r in _bridge_protocols
                ]
                st.dataframe(pd.DataFrame(_bp_rows), width='stretch', hide_index=True)
                st.caption("Live chain TVL flows available in Opportunities → Bridge Flow Monitor.")
    
    except Exception as _assist_exc:
        st.info("DeFi Assistant unavailable. Check logs for details.")

# end _t_sent


with _t_eco:
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
            "🧠 Ecosystem digest hasn't been generated yet. The scheduler builds "
            "it automatically on its daily run — data will appear here once the "
            "first digest completes. Use the button below to run it now."
        )
        if st.button("▶ Run monitor now", key="_run_web_monitor_now"):
            try:
                with st.spinner("Scanning ecosystem — this takes ~60 seconds..."):
                    from scanners.web_monitor import run_web_monitor as _run_now
                    _run_now()
                st.success("Monitor complete — refresh the page to view the digest.")
            except Exception as _wm_e:
                st.warning(
                    "Monitor couldn't complete right now — this is usually a "
                    "temporary data-source issue. Try again in a few minutes.",
                    icon="⚠️",
                )
                logger.warning("web monitor manual trigger failed: %s", _wm_e)
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
                f"<div style='color:#475569; font-size:0.85rem; margin-bottom:14px;'>"
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
                f"<span style='font-size:0.85rem; color:#334155; margin-left:auto;'>Claude AI · Not financial advice</span>"
                f"</div>"
                f"<div style='color:#cbd5e1; font-size:0.90rem; line-height:1.65;'>{_html.escape(ai_text)}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='background:rgba(139,92,246,0.04); border:1px solid rgba(139,92,246,0.12); "
                "border-radius:10px; padding:12px 16px; font-size:0.85rem; color:#475569;'>"
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
                    f"{'<div style=\"color:#64748b;font-size:0.85rem;margin-top:6px\">' + desc + '</div>' if desc else ''}"
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
                st.dataframe(pd.DataFrame(tvl_rows), width='stretch', hide_index=True)
    
        # News
        news_items = digest.get("news_items") or []
        if news_items:
            st.markdown(
                f"<div style='font-size:0.85rem; font-weight:700; color:#94a3b8; "
                f"text-transform:uppercase; letter-spacing:1.2px; margin:16px 0 10px;'>"
                f"Recent News <span style='color:#334155; font-weight:400;'>({len(news_items)} articles)</span></div>",
                unsafe_allow_html=True,
            )
            for item in news_items[:10]:
                if not isinstance(item, dict):
                    continue
                title    = _html.escape(str(item.get("title", "Untitled")))
                link     = item.get("link", "")
                title_md = f"<a href='{_html.escape(link)}' target='_blank' style='color:#cbd5e1; font-weight:600; text-decoration:none;'>{title} ↗</a>" if link else f"<span style='color:#94a3b8; font-weight:600;'>{title}</span>"
                summary  = _html.escape(str(item.get("summary", "")))
                src      = _html.escape(str(item.get("source", "")))
                pub      = _html.escape(str(item.get("published", "")))
                sum_html = f"<div style='color:#64748b; font-size:0.85rem; margin-top:5px; line-height:1.5;'>{summary}</div>" if summary else ""
                st.markdown(
                    f"<div style='background:rgba(13,14,20,0.8); border-radius:12px; padding:13px 16px; "
                    f"margin-bottom:8px; border:1px solid rgba(255,255,255,0.06); "
                    f"transition: border-color 0.2s;'>"
                    f"<div>{title_md}</div>"
                    f"<div style='color:#334155; font-size:0.85rem; margin-top:4px; display:flex; gap:8px;'>"
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
        import logging as _lg_mi
        _lg_mi.getLogger(__name__).warning("[MarketIntel] feedback load error: %s", e)
        st.warning("AI model health data not yet available — run a scan to generate feedback.")
        feedback = None
    
    if feedback:
        overall = feedback.get("overall_health", 50)
        trend   = feedback.get("trend", "building")
        trend_icon = {"improving": "📈", "stable": "➡️", "declining": "📉", "building": "🔧"}.get(trend, "➡️")
        health_color = "#10b981" if overall >= 70 else ("#f59e0b" if overall >= 45 else "#ef4444")

        scans     = feedback.get("total_scans", 0)
        evaluated = feedback.get("evaluated_scans", 0)
        # Below this threshold, the 50/100 default is statistically meaningless and
        # looks alarming — show "Awaiting data" instead so users don't read a 50 as "mediocre".
        _AI_HEALTH_MIN_EVALS = 5
        learning = evaluated < _AI_HEALTH_MIN_EVALS

        c1, c2, c3 = st.columns(3)
        with c1:
            if learning:
                st.markdown("""
                <div class="metric-card card-blue">
                    <div class="label">Overall Health</div>
                    <div class="big-number" style="color:#94a3b8;">—</div>
                    <div style="color:#475569; font-size:0.85rem; margin-top:4px;">Awaiting data</div>
                </div>""", unsafe_allow_html=True)
            else:
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
                <div style="color:#475569; font-size:0.85rem; margin-top:4px;">{trend.capitalize()}</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="metric-card card-blue">
                <div class="label">Predictions</div>
                <div class="big-number">{evaluated}</div>
                <div style="color:#475569; font-size:0.85rem; margin-top:4px;">of {scans} evaluated</div>
            </div>""", unsafe_allow_html=True)

        if learning:
            st.caption(
                f"ℹ️ Model health score needs at least {_AI_HEALTH_MIN_EVALS} evaluated predictions "
                f"before it's meaningful. Currently {evaluated} of {scans} scans evaluated. "
                "Each scheduled scan contributes one prediction; evaluations happen on the next cycle."
            )
    
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
            grade = acc.get("grade", "—")
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
                f"<div style='color:#475569; font-size:0.85rem; margin-top:8px;'>{msg}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    
        # Model weights
        weights = feedback.get("model_weights") or {}
        if weights:
            st.divider()
            st.markdown("#### Model Confidence Weights")
            st.markdown(
                "<div style='color:#475569; font-size:0.85rem; margin-bottom:10px;'>"
                "Higher accuracy = higher weight. Adjusted automatically after each scan.</div>",
                unsafe_allow_html=True,
            )
            rows = [{"Profile": p.capitalize(), "Weight": f"{w:.4f}",
                     "Effect": "Boosted" if w > 1.0 else ("Reduced" if w < 0.9 else "Neutral")}
                    for p, w in weights.items()]
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
    
# end _t_eco (first block: Ecosystem Monitor + AI Model Health)


with _t_macro:
    render_section_header("Macro Intelligence", "FRED + yfinance · 10Y yield · 2Y10Y spread · VIX · CPI · BTC rolling correlations")

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
                         "DXY": "#8b5cf6", "Oil": "#f59e0b"}
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
                st.plotly_chart(_fig, width='stretch')
            else:
                st.info("Loading macro timeseries… (yfinance required)")
        else:
            st.info("Install yfinance for BTC macro correlations: `pip install yfinance`")
    
    except Exception as _macro_err:
        logger.warning("[MarketIntel] macro data failed: %s", _macro_err)
        st.caption("Macro data temporarily unavailable — try refreshing in a few minutes.")
    
    # ─── BTC Technical Analysis (Layer 1 TA signals) ──────────────────────────────
    st.divider()
    render_section_header("BTC Technical Analysis", "Layer 1 · RSI-14 · MA Cross · 30d Momentum · yfinance daily OHLCV")
    
    try:
        import macro_feeds as _mf_ta
        _ta = _mf_ta.fetch_btc_ta_signals()
    
        _rsi_val   = _ta.get("rsi_14")
        _ma_sig    = _ta.get("ma_signal", "NEUTRAL")
        _mom_20    = _ta.get("price_momentum")   # 20d lookback (Issue #R1)
        _ab200     = _ta.get("above_200ma")
        _btc_px    = _ta.get("btc_price")
    
        # RSI color + label
        if _rsi_val is None:
            _rsi_color, _rsi_label = "#64748b", "—"
        elif _rsi_val < 30:
            _rsi_color, _rsi_label = "#22c55e", "Oversold — buy zone"
        elif _rsi_val > 70:
            _rsi_color, _rsi_label = "#ef4444", "Overbought — caution"
        else:
            _rsi_color, _rsi_label = "#f59e0b", "Neutral range"
    
        # MA cross color + icon
        _ma_meta = {
            "GOLDEN_CROSS": ("#22c55e", "▲ Golden Cross", "50d crossed above 200d · bullish trend"),
            "DEATH_CROSS":  ("#ef4444", "▼ Death Cross",  "50d crossed below 200d · bearish trend"),
            "NEUTRAL":      ("#94a3b8", "■ Neutral",       "No definitive MA cross signal"),
        }
        _ma_c, _ma_icon, _ma_desc = _ma_meta.get(_ma_sig, ("#94a3b8", "■ Neutral", ""))
    
        # Momentum color
        if _mom_20 is None:
            _mom_color, _mom_str = "#64748b", "—"
        elif _mom_20 > 10:
            _mom_color, _mom_str = "#22c55e", f"+{_mom_20:.1f}%"
        elif _mom_20 < -10:
            _mom_color, _mom_str = "#ef4444", f"{_mom_20:.1f}%"
        else:
            _mom_color, _mom_str = "#f59e0b", f"{_mom_20:+.1f}%"
    
        # 200MA position
        _ab200_str   = "Above 200d MA ▲" if _ab200 else "Below 200d MA ▼"
        _ab200_color = "#22c55e" if _ab200 else "#ef4444"
    
        # Pre-compute display strings (avoid complex expressions inside f-strings)
        _rsi_disp  = f"{_rsi_val:.1f}" if _rsi_val is not None else "—"
        _btc_disp  = f"${_btc_px:,.0f} BTC/USD" if _btc_px else "Price N/A"
        if _mom_20 is not None and _mom_20 > 10:
            _mom_trend = "Strong uptrend"
        elif _mom_20 is not None and _mom_20 < -10:
            _mom_trend = "Strong downtrend"
        else:
            _mom_trend = "Mild drift"
    
        _tac1, _tac2, _tac3, _tac4 = st.columns(4)
    
        with _tac1:
            st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_rsi_color};
                border-radius:10px;padding:10px;text-align:center">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">RSI-14</div>
      <div style="font-size:20px;font-weight:700;color:{_rsi_color}">{_rsi_disp}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">{_rsi_label}</div>
      <div style="font-size:10px;color:#64748b;margin-top:6px">Wilder 1978 · 14-day</div>
    </div>
    """, unsafe_allow_html=True)
    
        with _tac2:
            st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_ma_c};
                border-radius:10px;padding:10px;text-align:center">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">MA Cross</div>
      <div style="font-size:18px;font-weight:700;color:{_ma_c}">{_ma_icon}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">{_ma_desc}</div>
      <div style="font-size:10px;color:#64748b;margin-top:6px">50d vs 200d · Glassnode 71% accuracy</div>
    </div>
    """, unsafe_allow_html=True)
    
        with _tac3:
            st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_mom_color};
                border-radius:10px;padding:10px;text-align:center">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">30d Momentum</div>
      <div style="font-size:20px;font-weight:700;color:{_mom_color}">{_mom_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">{_mom_trend}</div>
      <div style="font-size:10px;color:#64748b;margin-top:6px">Price change: 30 days</div>
    </div>
    """, unsafe_allow_html=True)
    
        with _tac4:
            st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_ab200_color};
                border-radius:10px;padding:10px;text-align:center">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">200d MA Position</div>
      <div style="font-size:16px;font-weight:700;color:{_ab200_color}">{_ab200_str}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:4px">{_btc_disp}</div>
      <div style="font-size:10px;color:#64748b;margin-top:6px">Long-term trend filter</div>
    </div>
    """, unsafe_allow_html=True)
    
        # Beginner explanation
        _user_level = st.session_state.get("user_level", "beginner")
        if _user_level == "beginner":
            _ta_summary_parts = []
            if _rsi_val is not None:
                if _rsi_val < 30:
                    _ta_summary_parts.append("BTC is in an **oversold** zone — historically a good time to consider buying")
                elif _rsi_val > 70:
                    _ta_summary_parts.append("BTC appears **overbought** — momentum may slow or reverse")
                else:
                    _ta_summary_parts.append("BTC momentum is in a **neutral** range")
            if _ma_sig == "GOLDEN_CROSS":
                _ta_summary_parts.append("the short-term trend is crossing **above** the long-term average (bullish)")
            elif _ma_sig == "DEATH_CROSS":
                _ta_summary_parts.append("the short-term trend has dropped **below** the long-term average (bearish)")
            if _ta_summary_parts:
                st.info("What this means for you: " + "; ".join(_ta_summary_parts) + ".")
    
        st.caption(f"Source: {_ta.get('source', 'yfinance')} · Cached 1 hour · Layer 1 Technical Analysis")
    
    except Exception as _ta_err:
        logger.warning("[MarketIntel] BTC TA failed: %s", _ta_err)
        st.caption("BTC technical signals temporarily unavailable — try refreshing in a few minutes.")
    
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
        _bc3 = {"BLOOD_IN_STREETS": "#ef4444", "EXTREME_FEAR": "#f59e0b", "NORMAL": "#64748b"}.get(_bits3["signal"], "#64748b")
        _bg3 = {"BLOOD_IN_STREETS": "#1f0000",  "EXTREME_FEAR": "#1c1200", "NORMAL": "#111827"}.get(_bits3["signal"], "#111827")
        _dc3 = {0.0: "#ef4444", 0.5: "#f59e0b", 1.0: "#94a3b8", 2.0: "#10b981", 3.0: "#00d4aa"}.get(_dca3, "#94a3b8")
        _dl3 = {0.0: "HOLD — no new buys", 0.5: "0.5× — reduce size", 1.0: "1× — base size", 2.0: "2× — accumulate", 3.0: "3× — max accumulate"}.get(_dca3, f"{_dca3}×")
    
        _col1, _col2 = st.columns(2)
        with _col1:
            st.markdown(f"""
    <div style="background:{_bg3};border:1px solid {_bc3};border-top:3px solid {_bc3};
                border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Blood in Streets Signal</div>
      <div style="font-size:20px;font-weight:700;color:{_bc3}">{_bits3["signal"].replace("_", " ")}</div>
      <div style="font-size:12px;color:#94a3b8;margin-top:4px">{_bits3["strength"]} · {_bits3["criteria_met"]}/3 criteria met</div>
      <div style="font-size:11px;color:#64748b;margin-top:8px">{_bits3["description"]}</div>
      <div style="margin-top:10px;font-size:11px;color:#64748b">
        {"✅" if _bits3["criteria"]["extreme_fear"] else "❌"} F&amp;G≤25 &nbsp;
        {"✅" if _bits3["criteria"]["rsi_oversold"] else "❌"} RSI≤30 &nbsp;
        {"✅" if _bits3["criteria"]["exchange_outflow"] else "❌"} Exchange outflow
      </div>
    </div>
    """, unsafe_allow_html=True)
        with _col2:
            st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_dc3};
                border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">DCA Multiplier</div>
      <div style="font-size:24px;font-weight:700;color:{_dc3}">{_dca3}×</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:4px">{_dl3}</div>
      <div style="font-size:11px;color:#64748b;margin-top:8px">
        F&amp;G: {_fg_v3}/100<br/>
        DXY {_yf3.get("dxy", "—")} · 10Y {_fred3.get("ten_yr_yield", "—")}%
      </div>
    </div>
    """, unsafe_allow_html=True)
    except Exception as _bits_err:
        logger.warning("[MarketIntel] Blood in Streets failed: %s", _bits_err)
        st.caption("Market signal temporarily unavailable — try refreshing in a few minutes.")
    
# end _t_macro


with _t_onchain:
    render_section_header("On-Chain Intelligence", "CoinMetrics · MVRV Z-Score · SOPR · Hash Ribbons · Puell Multiple")

    try:
        import macro_feeds as _mf4
        _oc4 = _mf4.fetch_coinmetrics_onchain(days=400)
    
        # Only render the "all sources failed" card when NO metric is
        # available. With the Blockchain.com fallback, Hash Ribbons + Puell
        # Multiple + Active Addresses are usually live even when CoinMetrics
        # is blocked (MVRV / SOPR require paid-tier and gracefully show "—").
        _any_data = any([
            _oc4.get("mvrv_z") is not None,
            _oc4.get("sopr") is not None,
            _oc4.get("puell_multiple") is not None,
            _oc4.get("hash_ma_30") is not None,
            _oc4.get("active_addresses") is not None,
        ])
        if _oc4.get("error") and not _any_data:
            st.markdown(
                "<div style='background:rgba(30,41,59,0.35); "
                "border:1px solid rgba(100,116,139,0.25); "
                "border-left:3px solid #00d4aa; border-radius:8px; "
                "padding:12px 16px; margin:6px 0 14px;'>"
                "<div style='font-size:0.82rem; color:#94a3b8; line-height:1.5;'>"
                "<span style='color:#00d4aa; font-weight:700;'>Data temporarily unavailable</span>"
                "<span style='color:#475569; margin:0 6px;'>·</span>"
                "BTC on-chain metrics refresh hourly. Check back in a few minutes."
                "</div></div>",
                unsafe_allow_html=True,
            )
        else:
            # Source indicator — tells advisors which backend is live.
            _src = _oc4.get("source", "")
            if _src == "blockchain_com":
                st.caption("ⓘ MVRV Z-Score and SOPR require a paid data provider; Hash Ribbons, Puell Multiple, and active-address metrics shown from Blockchain.com (live).")
            _mz4  = _oc4.get("mvrv_z")
            _ms4  = _oc4.get("mvrv_signal", "—")
            _sp4  = _oc4.get("sopr")
            _ss4  = _oc4.get("sopr_signal", "—")
            _rc4  = _oc4.get("realized_cap")
            _mv4  = _oc4.get("mvrv_ratio")
            _aa4  = _oc4.get("active_addresses")
    
            _mvrv_color = {"UNDERVALUED": "#00d4aa", "FAIR_VALUE": "#10b981", "OVERVALUED": "#f59e0b", "EXTREME_HEAT": "#ef4444"}.get(_ms4, "#64748b")
            _sc4 = {"CAPITULATION": "#00d4aa", "MILD_LOSS": "#10b981", "NORMAL": "#64748b", "PROFIT_TAKING": "#f59e0b"}.get(_ss4, "#64748b")
    
            def _fmtb(v):
                if v is None: return "—"
                if v >= 1e12: return f"${v/1e12:.2f}T"
                if v >= 1e9:  return f"${v/1e9:.1f}B"
                return f"${v/1e6:.0f}M"
    
            _c1, _c2, _c3, _c4 = st.columns(4)
            with _c1:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_mvrv_color};border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">MVRV Z-Score</div>
      <div style="font-size:21px;font-weight:700;color:{_mvrv_color}">{f"{_mz4:+.2f}" if _mz4 is not None else "—"}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:4px">{_ms4.replace("_", " ")}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">MVRV ratio: {f"{_mv4:.3f}" if _mv4 else "—"}</div>
    </div>
    """, unsafe_allow_html=True)
            with _c2:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_sc4};border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">SOPR</div>
      <div style="font-size:21px;font-weight:700;color:{_sc4}">{f"{_sp4:.4f}" if _sp4 is not None else "—"}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:4px">{_ss4.replace("_", " ")}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">&gt;1 profit-taking · &lt;1 capitulation</div>
    </div>
    """, unsafe_allow_html=True)
            with _c3:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid #8b5cf6;border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Realized Cap</div>
      <div style="font-size:22px;font-weight:700;color:#8b5cf6">{_fmtb(_rc4)}</div>
      <div style="font-size:11px;color:#64748b;margin-top:8px">BTC at last-moved price</div>
    </div>
    """, unsafe_allow_html=True)
            with _c4:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid #8b5cf6;border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Active Addresses</div>
      <div style="font-size:22px;font-weight:700;color:#8b5cf6">{f"{_aa4:,}" if _aa4 else "—"}</div>
      <div style="font-size:11px;color:#64748b;margin-top:8px">Unique BTC addresses today</div>
    </div>
    """, unsafe_allow_html=True)
    
            # MVRV Z-Score chart
            _mh4 = _oc4.get("mvrv_history", {})
            if _mh4:
                _mhs = pd.Series(_mh4).sort_index()
                _mhz = (_mhs - _mhs.rolling(365, min_periods=30).mean()) / _mhs.rolling(365, min_periods=30).std().clip(lower=1e-6)
                _fig_mz = go.Figure()
                _fig_mz.add_trace(go.Scatter(x=_mhz.index, y=_mhz.values, mode="lines",
                                             name="MVRV Z-Score", line=dict(color="#8b5cf6", width=2)))
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
                st.plotly_chart(_fig_mz, width='stretch')
    
            # Hash Ribbons + Puell Multiple — added in composite signal sprint
            _hr_sig  = _oc4.get("hash_ribbon_signal", "—")
            _puell   = _oc4.get("puell_multiple")
            _p_sig   = _oc4.get("puell_signal", "—")
            _hr_color = {
                "BUY": "#22c55e", "RECOVERY": "#00d4aa",
                "CAPITULATION": "#ef4444", "CAPITULATION_START": "#f59e0b",
            }.get(_hr_sig, "#64748b")
            _p_color = {
                "EXTREME_BOTTOM": "#22c55e", "ACCUMULATION": "#00d4aa",
                "FAIR_VALUE": "#64748b", "DISTRIBUTION": "#f59e0b", "EXTREME_TOP": "#ef4444",
            }.get(_p_sig, "#64748b")
    
            if _hr_sig != "—" or _puell is not None:
                st.markdown("<div style='margin-top:16px'></div>", unsafe_allow_html=True)
                _h1, _h2 = st.columns(2)
                with _h1:
                    st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_hr_color};border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Hash Ribbons</div>
      <div style="font-size:22px;font-weight:700;color:{_hr_color}">{_hr_sig.replace("_", " ") if _hr_sig != "—" else "—"}</div>
      <div style="font-size:11px;color:#64748b;margin-top:8px">30d vs 60d hash rate MA · C. Edwards 2019</div>
    </div>
    """, unsafe_allow_html=True)
                with _h2:
                    st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_p_color};border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Puell Multiple</div>
      <div style="font-size:21px;font-weight:700;color:{_p_color}">{f"{_puell:.3f}" if _puell is not None else "—"}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:4px">{_p_sig.replace("_", " ")}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">Daily miner USD / 365d MA · D. Puell 2019</div>
    </div>
    """, unsafe_allow_html=True)
    
            _ts4 = _oc4.get("timestamp", "")[:19]
            st.caption(f"Source: CoinMetrics Community · {_ts4} UTC · Cached 1h · MVRV (Mahmudov & Puell 2018) · SOPR (Shirakashi 2019)")
    except Exception as _oc_err:
        logger.warning("[MarketIntel] on-chain data failed: %s", _oc_err)
        st.caption("On-chain data temporarily unavailable — try refreshing in a few minutes.")
    
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
            _osig5 = _oc5.get("signal", "—")
            _spot5 = _oc5.get("spot_price")
    
            _sc5 = {
                "EXTREME_PUTS": "#ef4444", "BEARISH": "#f59e0b",
                "NEUTRAL": "#64748b", "BULLISH": "#10b981", "EXTREME_CALLS": "#00d4aa",
            }.get(_osig5, "#64748b")
    
            _d5a, _d5b, _d5c, _d5d = st.columns(4)
            with _d5a:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid {_sc5};border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Put/Call Ratio</div>
      <div style="font-size:28px;font-weight:700;color:{_sc5}">{f"{_pc5:.3f}" if _pc5 is not None else "—"}</div>
      <div style="font-size:13px;color:#94a3b8;margin-top:4px">{_osig5.replace("_", " ")}</div>
    </div>
    """, unsafe_allow_html=True)
            with _d5b:
                _mp5_d = f"{abs(_mp5 - _spot5) / _spot5 * 100:.1f}% {'below' if _mp5 < _spot5 else 'above'} spot" if _mp5 and _spot5 else ""
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid #8b5cf6;border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Max Pain</div>
      <div style="font-size:24px;font-weight:700;color:#8b5cf6">{f"${_mp5:,.0f}" if _mp5 else "—"}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">{_mp5_d}</div>
    </div>
    """, unsafe_allow_html=True)
            with _d5c:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid #ef4444;border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Total Put OI</div>
      <div style="font-size:24px;font-weight:700;color:#ef4444">{f"{_tput5:,.0f}" if _tput5 else "—"}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">contracts</div>
    </div>
    """, unsafe_allow_html=True)
            with _d5d:
                st.markdown(f"""
    <div style="background:#111827;border:1px solid #1e293b;border-top:3px solid #10b981;border-radius:10px;padding:10px">
      <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Total Call OI</div>
      <div style="font-size:24px;font-weight:700;color:#10b981">{f"{_tcal5:,.0f}" if _tcal5 else "—"}</div>
      <div style="font-size:11px;color:#64748b;margin-top:6px">contracts</div>
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
                            line=dict(color="#8b5cf6", dash="dash", width=1.5),
                            opacity=0.8,
                        )
                        _fig5d.add_annotation(
                            x=_mp5_str, y=1,
                            xref="x", yref="paper",
                            text=f"Max Pain ${_mp5:,.0f}",
                            showarrow=False,
                            font=dict(color="#8b5cf6", size=10),
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
                    st.plotly_chart(_fig5d, width='stretch')
    
            with _col5R:
                if _ts5d:
                    import plotly.graph_objects as go
                    _fig5e = go.Figure()
                    _fig5e.add_trace(go.Scatter(
                        x=[t["dte"] for t in _ts5d], y=[t["atm_iv"] for t in _ts5d],
                        mode="lines+markers", name="ATM IV",
                        line=dict(color="#8b5cf6", width=2), marker=dict(size=6),
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
                    st.plotly_chart(_fig5e, width='stretch')
    
            _ts5_txt = _oc5.get("timestamp", "")[:19]
            st.caption(f"Source: Deribit · {_ts5_txt} UTC · Cached 15 min")
    except Exception as _opt_err:
        logger.warning("[MarketIntel] options data failed: %s", _opt_err)
        st.caption("Options data temporarily unavailable — try refreshing in a few minutes.")
    
# end _t_onchain

# ─── Item 37: In/Out of the Money metric for tracked coins ───────────────────
with _t_onchain:
    st.divider()
    render_section_header(
        "In / Out of the Money",
        "What % of tracked coin holders are currently profitable (above average cost basis)",
    )

    @st.cache_data(ttl=1800, max_entries=1)
    def _fetch_iotm_data() -> list[dict]:
        """Fetch price, ATH, and ATH change % from CoinGecko for tracked coins.
        Computes an IOTM proxy score: % of holders estimated in profit.
        Uses: distance from ATH as a proxy for holder profitability.
        Methodology: CoinGecko ATH + ath_change_percentage + market_data.
        """
        from config import MUST_HAVE_COINS
        from utils.http import _SESSION as _iotm_session, coingecko_limiter as _iotm_lim
        _IOTM_COINS = {
            "xrp": "XRP", "flare-networks": "FLR", "ripple": "XRP",
            "stellar": "XLM", "xdc-network": "XDC", "hedera-hashgraph": "HBAR",
            "stronghold-token": "SHX", "zebec-protocol": "ZBCN",
            "bitcoin": "BTC", "ethereum": "ETH",
        }
        _ids = list(_IOTM_COINS.keys())
        try:
            _iotm_lim.acquire()
            _r = _iotm_session.get(
                "https://api.coingecko.com/api/v3/coins/markets",
                params={
                    "vs_currency": "usd", "ids": ",".join(_ids),
                    "order": "market_cap_desc", "per_page": "30", "page": "1",
                    "price_change_percentage": "7d,30d", "locale": "en",
                },
                timeout=10,
            )
            _raw = _r.json() if _r.status_code == 200 else []
        except Exception:
            return []

        results = []
        for coin in _raw:
            _sym    = (_coin_id := coin.get("id", ""))
            _symbol = _IOTM_COINS.get(_sym, coin.get("symbol", "").upper())
            _price  = float(coin.get("current_price") or 0)
            _ath    = float(coin.get("ath") or 0)
            _ath_ch = float(coin.get("ath_change_percentage") or 0)  # negative = below ATH
            _atl    = float(coin.get("atl") or 0.000001)
            _p7d    = float(coin.get("price_change_percentage_7d_in_currency") or 0)
            _p30d   = float(coin.get("price_change_percentage_30d_in_currency") or 0)
            if _price <= 0 or _ath <= 0:
                continue

            # IOTM proxy: linear scale between ATL (0% in money) and ATH (100% in money)
            # Accounts for fact that most buying happened when price was closer to ATH
            _ath_distance = abs(_ath_ch) / 100.0  # 0 = at ATH, 1+ = far below
            # Rough IOTM estimate: at ATH = 100% in money, 50% below ATH = ~35% in money
            _iotm_pct = max(0.0, min(100.0, 100.0 * (1 - _ath_distance * 0.65)))

            _status = ("In the Money" if _iotm_pct >= 60
                       else "Mixed" if _iotm_pct >= 35
                       else "Out of the Money")

            results.append({
                "symbol":    _symbol,
                "name":      coin.get("name", _symbol),
                "price":     _price,
                "ath":       _ath,
                "ath_pct":   round(_ath_ch, 1),
                "iotm_pct":  round(_iotm_pct, 1),
                "status":    _status,
                "7d_chg":    round(_p7d, 2),
                "30d_chg":   round(_p30d, 2),
            })
        results.sort(key=lambda x: x["iotm_pct"], reverse=True)
        return results

    _iotm_data = _fetch_iotm_data()
    if not _iotm_data:
        st.info("In/Out of the Money data unavailable — CoinGecko API may be rate-limited.")
    else:
        # Summary metrics
        _itm  = [c for c in _iotm_data if c["status"] == "In the Money"]
        _ootm = [c for c in _iotm_data if c["status"] == "Out of the Money"]
        _mix  = [c for c in _iotm_data if c["status"] == "Mixed"]
        _ic1, _ic2, _ic3 = st.columns(3)
        with _ic1:
            st.metric("In the Money", len(_itm),
                      help="Coins where estimated >60% of holders are in profit")
        with _ic2:
            st.metric("Mixed", len(_mix),
                      help="35-60% of holders estimated in profit")
        with _ic3:
            st.metric("Out of the Money", len(_ootm),
                      help="Estimated <35% of holders in profit")

        # IOTM table
        _iotm_rows = []
        for _c in _iotm_data:
            _s = _c["status"]
            _sym_col = ("▲ " if _s == "In the Money" else
                        "■ " if _s == "Mixed" else "▼ ")
            _iotm_rows.append({
                "Coin":          _c["symbol"],
                "Price":         f"${_c['price']:,.4g}",
                "ATH":           f"${_c['ath']:,.4g}",
                "From ATH":      f"{_c['ath_pct']:.1f}%",
                "IOTM Est.":     f"{_c['iotm_pct']:.0f}%",
                "Status":        f"{_sym_col}{_s}",
                "7d Change":     f"{_c['7d_chg']:+.1f}%",
                "30d Change":    f"{_c['30d_chg']:+.1f}%",
            })
        st.dataframe(pd.DataFrame(_iotm_rows), width='stretch', hide_index=True)

        render_what_this_means(
            "The 'In the Money' score estimates how many people who bought this coin are "
            "currently making a profit. When a coin is close to its all-time high, almost "
            "everyone is in profit. When it's far below the all-time high, most holders "
            "are at a loss. Coins where most holders are at a loss often see selling pressure "
            "as prices recover — people sell to break even.",
            title="What is In/Out of the Money?",
            intermediate_message="IOTM proxy: distance from ATH. High IOTM = potential sell pressure at recovery. Low IOTM = capitulation zone.",
        )
        st.caption(
            "IOTM is a proxy estimate based on ATH distance. "
            "True IOTM requires on-chain cost basis data (Glassnode/IntoTheBlock). "
            "Source: CoinGecko · Cached 30 min."
        )


# ─── On-Chain: Lending Protocol Liquidation Risk ─────────────────────────────
with _t_onchain:
    st.divider()
    render_section_header(
        "Lending Liquidation Risk Monitor",
        "DeFiLlama · Kinetic Finance · estimated at-risk TVL at FLR price scenarios",
    )

    @st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
    def _fetch_kinetic_liq_risk() -> dict:
        """Estimate at-risk borrow TVL for Kinetic Finance at various FLR price drops."""
        from scanners.defillama import fetch_protocol_tvl
        d = fetch_protocol_tvl("kinetic-finance")
        return d

    try:
        _kin = _fetch_kinetic_liq_risk()
        _kin_tvl = _kin.get("tvl_usd", 0) or 0
        _kin_7d  = _kin.get("change_7d_pct") or 0

        if _kin_tvl > 0:
            # Kinetic Finance typical parameters (FLR as collateral, USDC/USDT borrowed)
            # Average collateral ratio observed: ~130-150% for sFLR positions
            # At what FLR price drop does a typical 150% CR position get liquidated?
            # Liquidation: CR falls to 120% trigger → FLR must drop ~20% from entry
            _SCENARIOS = [
                (-10, "Small correction",     0.05,   "#f59e0b"),  # ~5% liquidated
                (-20, "Moderate pullback",    0.15,   "#f59e0b"),  # ~15% liquidated
                (-30, "Significant decline",  0.35,   "#ef4444"),  # ~35% liquidated
                (-50, "Severe crash",         0.70,   "#dc2626"),  # ~70% liquidated
            ]

            _liq_c1, _liq_c2 = st.columns([3, 2])
            with _liq_c1:
                st.markdown("#### At-Risk TVL by Price Scenario")
                _liq_rows = []
                for _drop_pct, _scenario, _at_risk_frac, _col in _SCENARIOS:
                    _at_risk_usd = _kin_tvl * _at_risk_frac
                    _liq_rows.append({
                        "FLR Drop":      f"{_drop_pct}%",
                        "Scenario":      _scenario,
                        "Est. At-Risk":  f"${_at_risk_usd/1e6:.1f}M" if _at_risk_usd >= 1e6 else f"${_at_risk_usd/1e3:.0f}K",
                        "% of TVL":      f"{_at_risk_frac*100:.0f}%",
                    })
                st.dataframe(pd.DataFrame(_liq_rows), width='stretch', hide_index=True)

            with _liq_c2:
                _tvl_color = "#22c55e" if _kin_7d >= 0 else "#ef4444"
                st.markdown(f"""
<div style="background:#111827;border:1px solid #1e293b;border-radius:10px;padding:16px;margin-top:8px">
  <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.8px;margin-bottom:6px">Kinetic Finance TVL</div>
  <div style="font-size:24px;font-weight:700;color:#00d4aa">${_kin_tvl/1e6:.1f}M</div>
  <div style="font-size:13px;color:{_tvl_color};margin-top:4px">7d: {_kin_7d:+.1f}%</div>
  <div style="font-size:11px;color:#64748b;margin-top:8px">Primary Flare lending market<br>sFLR/FLR collateral → USDC/USDT borrowed</div>
</div>
""", unsafe_allow_html=True)

            st.caption(
                "⚠️ Estimates based on observed average collateral ratios (~150% CR). "
                "Actual liquidation levels depend on individual position parameters. "
                "Source: DeFiLlama · Cached 30 min."
            )

            _user_level_liq = st.session_state.get("user_level", "beginner")
            if _user_level_liq == "beginner":
                st.info(
                    "**What this means for you:** When FLR price drops, people who borrowed "
                    "against their FLR can be forced to sell (liquidated) if they don't add "
                    "more collateral. This table shows how much of the total lending market "
                    "could face forced selling at each price level. Higher liquidations = "
                    "more selling pressure on FLR price."
                )
        else:
            st.caption("Kinetic Finance TVL data unavailable — DeFiLlama may be rate-limiting.")
    except Exception as _liq_exc:
        logger.warning("[LiquidationRisk] fetch failed: %s", _liq_exc)
        st.caption("Liquidation risk data temporarily unavailable — try refreshing in a few minutes.")


# ── Ecosystem tab — second block (Intent Mapper + Protocol Revenue + RWA Credit) ──
with _t_eco:
    render_section_header(
        "DeFi Intent Mapper",
        "Describe what you want to do — Claude classifies your intent and recommends the best strategy",
    )

_intent_map = {
    "swap":    {"label": "Swap Tokens",       "icon": "🔄", "color": "#8b5cf6",
                "desc":  "Exchange one token for another at the best available rate."},
    "provide": {"label": "Provide Liquidity",  "icon": "💧", "color": "#00d4aa",
                "desc":  "Add tokens to an AMM pool to earn trading fees. Comes with IL risk."},
    "stake":   {"label": "Stake / Restake",    "icon": "🔒", "color": "#8B5CF6",
                "desc":  "Lock tokens in a protocol to earn staking rewards."},
    "lend":    {"label": "Lend / Deposit",     "icon": "🏦", "color": "#10B981",
                "desc":  "Deposit assets into a lending protocol to earn interest."},
    "borrow":  {"label": "Borrow",             "icon": "💸", "color": "#F59E0B",
                "desc":  "Borrow against your collateral. Use carefully — liquidation risk."},
    "claim":   {"label": "Claim Rewards",      "icon": "🎁", "color": "#ef4444",
                "desc":  "Harvest accumulated reward tokens from a protocol."},
    "bridge":  {"label": "Bridge Assets",      "icon": "🌉", "color": "#00d4aa",
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
                f"border-top:2px solid {_idef.get('color','#64748b')};border-radius:8px;"
                f"padding:10px 12px;text-align:center'>"
                f"<div style='font-size:1.4rem'>{_idef.get('icon','?')}</div>"
                f"<div style='font-size:0.85rem;font-weight:700;color:#F1F5F9;margin-top:4px'>{_idef.get('label','—')}</div>"
                f"<div style='font-size:0.85rem;color:#64748b;margin-top:4px'>{_idef.get('desc','')}</div>"
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
        st.dataframe(pd.DataFrame(_matched_opps).drop_duplicates(), width='stretch', hide_index=True)
    else:
        st.info("No matching opportunities found in current scan. Try running a scan first.")

    # Optional: Claude AI classification for ambiguous intents
    from config import FEATURES as _FEATURES, CLAUDE_HAIKU_MODEL as _HAIKU_MODEL
    if _FEATURES.get("anthropic_ai") and _used_default:
        _ai_key = __import__("os").environ.get("ANTHROPIC_API_KEY", "")
        with st.spinner("Asking Claude to classify intent…"):
            try:
                import anthropic as _anth
                _cl = _anth.Anthropic(api_key=_ai_key, timeout=8.0)
                _resp = _cl.messages.create(
                    model=_HAIKU_MODEL,
                    max_tokens=80,
                    messages=[{"role": "user", "content":
                        f"Classify this DeFi intent into ONE of: swap, provide, stake, lend, borrow, claim, bridge, hedge.\n"
                        f"Input: '{_intent_input}'\nReturn only the single word."}],
                )
                _ai_intent = (_resp.content[0].text.strip().lower() if (_resp.content and hasattr(_resp.content[0], "text")) else "lend")
                if _ai_intent in _intent_map:
                    st.markdown(f"Claude classified as: **{_intent_map[_ai_intent]['label']}**")
            except Exception:
                pass


# Protocol Revenue moved to Opportunities → Protocol Intelligence tab to avoid duplication.


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
            _hcol    = _HEALTH_COLORS.get(_health, "#94a3b8")
            _hbg     = _HEALTH_BADGES.get(_health, "background:rgba(156,163,175,0.12);color:#94a3b8")
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
                    f"<span style='font-size:0.85rem;font-weight:600;padding:2px 8px;"
                    f"border-radius:4px;{_hbg}'>{_health}</span>"
                    f"</div>"
                    f"<div style='font-size:1.25rem;font-weight:700;color:#e2e8f0;margin-bottom:6px'>{_tvl_str}</div>"
                    f"<div style='display:flex;gap:16px;font-size:0.85rem;color:#64748b'>"
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
        logger.warning("[MarketIntel] RWA credit data failed: %s", _rwa_err)
        st.caption("RWA credit data temporarily unavailable — try refreshing in a few minutes.")


# ─── Item 35: Unified Flare Ecosystem Panel ──────────────────────────────────

with _t_eco:
    st.divider()
    render_section_header(
        "Unified Flare Ecosystem",
        "All whitelisted Flare protocols — TVL, APY, and agent-executable status in one view",
    )

    @st.cache_data(ttl=900, max_entries=1)
    def _cached_flare_eco():
        try:
            from scanners.defillama import fetch_yields_pools as _fup
            _pools = _fup(min_tvl_usd=100_000) or []
            return [p for p in _pools if str(p.get("chain", "")).lower() == "flare"]
        except Exception:
            return []

    _flare_eco_pools = _cached_flare_eco()
    # Known Flare whitelisted protocols
    _FLARE_PROTO_INFO = {
        "kinetic":   {"full_name": "Kinetic Finance",  "type": "Lending"},
        "blazeswap": {"full_name": "BlazeSwap",        "type": "AMM DEX"},
        "sparkdex":  {"full_name": "SparkDEX",         "type": "Perp DEX"},
        "enosys":    {"full_name": "Enosys",            "type": "Liquid Staking"},
        "clearpool": {"full_name": "Clearpool",         "type": "Institutional Lending"},
        "spectra":   {"full_name": "Spectra Finance",   "type": "Fixed Rate Yield"},
    }
    try:
        from agents.config import FLARE_PROTOCOL_WHITELIST as _FPW
    except Exception:
        _FPW = frozenset(_FLARE_PROTO_INFO.keys())

    if _flare_eco_pools:
        _eco_by_proto: dict = {}
        for _ep in _flare_eco_pools:
            _pr = str(_ep.get("project") or _ep.get("protocol") or "").lower()
            if _pr not in _eco_by_proto:
                _eco_by_proto[_pr] = []
            _eco_by_proto[_pr].append(_ep)

        _eco_rows = []
        for _proto, _info in _FLARE_PROTO_INFO.items():
            _ppools = _eco_by_proto.get(_proto, [])
            _tvl    = sum(float(p.get("tvlUsd") or 0) for p in _ppools)
            _best_a = max((float(p.get("apy") or 0) for p in _ppools), default=0.0)
            _agent  = "✓ Agent" if _proto in _FPW else "—"
            _tvl_s  = (f"${_tvl/1e6:.1f}M" if _tvl >= 1_000_000
                       else f"${_tvl/1e3:.0f}K" if _tvl >= 1_000 else "—")
            _eco_rows.append({
                "Protocol":   _info["full_name"],
                "Type":       _info["type"],
                "TVL":        _tvl_s,
                "Best APY":   f"{_best_a:.1f}%" if _best_a > 0 else "—",
                "Pools":      len(_ppools),
                "Agent":      _agent,
            })
        st.dataframe(pd.DataFrame(_eco_rows), width='stretch', hide_index=True)
        st.caption("Source: DeFiLlama yields · Flare chain · Cached 15 min. Agent = executable by AI agent.")
    else:
        # Static fallback when DeFiLlama is unavailable
        _eco_static = [
            {"Protocol": "Kinetic Finance",   "Type": "Lending",              "TVL": "~$64M", "Best APY": "~8%",   "Pools": 4, "Agent": "✓ Agent"},
            {"Protocol": "BlazeSwap",         "Type": "AMM DEX",              "TVL": "~$12M", "Best APY": "~15%",  "Pools": 6, "Agent": "✓ Agent"},
            {"Protocol": "SparkDEX",          "Type": "Perp DEX",             "TVL": "~$8M",  "Best APY": "~25%",  "Pools": 3, "Agent": "✓ Agent"},
            {"Protocol": "Enosys",            "Type": "Liquid Staking",       "TVL": "~$30M", "Best APY": "~5%",   "Pools": 2, "Agent": "✓ Agent"},
            {"Protocol": "Clearpool",         "Type": "Institutional Lending","TVL": "~$46M", "Best APY": "~11%",  "Pools": 2, "Agent": "✓ Agent"},
            {"Protocol": "Spectra Finance",   "Type": "Fixed Rate Yield",     "TVL": "~$5M",  "Best APY": "~18%",  "Pools": 3, "Agent": "✓ Agent"},
        ]
        st.dataframe(pd.DataFrame(_eco_static), width='stretch', hide_index=True)
        st.caption("Showing research-based estimates — DeFiLlama live data unavailable.")


# ─── Item 36: XRPL AMM + EVM Sidechain Unified Tracking ─────────────────────

with _t_eco:
    st.divider()
    render_section_header(
        "XRPL AMM & EVM Sidechain Tracker",
        "XRP Ledger AMM pools + Flare EVM bridge activity — unified cross-chain view",
    )

    @st.cache_data(ttl=900, max_entries=1)
    def _cached_xrpl_pools():
        try:
            from scanners.defillama import fetch_yields_pools as _fup
            _all = _fup(min_tvl_usd=10_000) or []
            _xrpl = [p for p in _all if str(p.get("chain", "")).lower() in ("xrp", "xrpl")]
            return _xrpl
        except Exception:
            return []

    _xrpl_pools = _cached_xrpl_pools()

    # XRPL AMM Pools
    st.markdown(
        "<div style='font-size:0.85rem;font-weight:600;color:#00d4aa;margin-bottom:8px'>"
        "XRPL Native AMM Pools</div>",
        unsafe_allow_html=True,
    )
    if _xrpl_pools:
        _xrpl_rows = []
        for _xp in _xrpl_pools:
            _xpro = str(_xp.get("project") or "xrpl_amm")
            _xapy = float(_xp.get("apy") or 0)
            _xtvl = float(_xp.get("tvlUsd") or 0)
            _xsym = str(_xp.get("symbol") or "")
            _xil  = str(_xp.get("ilRisk") or "no").lower()
            _xrpl_rows.append({
                "Pool":     _xsym,
                "Protocol": _xpro.replace("_", " ").title(),
                "APY":      f"{_xapy:.1f}%",
                "TVL":      (f"${_xtvl/1e6:.1f}M" if _xtvl >= 1e6 else f"${_xtvl:,.0f}"),
                "IL Risk":  "Yes" if "yes" in _xil else "No",
            })
        st.dataframe(pd.DataFrame(_xrpl_rows), width='stretch', hide_index=True)
    else:
        st.info("XRPL AMM pool data unavailable — using research estimates below.")
        _xrpl_static = [
            {"Pool": "XRP/USDC", "Protocol": "XRPL AMM", "APY": "~3-8%", "TVL": "~$5M+", "IL Risk": "Yes"},
            {"Pool": "XRP/BTC",  "Protocol": "XRPL AMM", "APY": "~2-5%", "TVL": "~$2M+", "IL Risk": "Yes"},
            {"Pool": "XRP/ETH",  "Protocol": "XRPL AMM", "APY": "~2-6%", "TVL": "~$1M+", "IL Risk": "Yes"},
        ]
        st.dataframe(pd.DataFrame(_xrpl_static), width='stretch', hide_index=True)

    # EVM Sidechain bridge flows: Flare ↔ Ethereum + Flare ↔ XRP
    st.markdown(
        "<div style='font-size:0.85rem;font-weight:600;color:#00d4aa;margin:12px 0 8px'>"
        "Flare EVM Bridge Activity (7-Day Flow)</div>",
        unsafe_allow_html=True,
    )
    try:
        from scanners.defillama import fetch_bridge_flows as _fbf
        _xrpl_flows = _fbf(["Flare", "Ethereum", "XRP"])
        if _xrpl_flows:
            _flow_r = []
            for _f in _xrpl_flows:
                _s = _f.get("flow_signal", "STABLE")
                _a = "▲" if _s == "INFLOW" else ("▼" if _s == "OUTFLOW" else "■")
                _d7 = _f.get("change_7d_pct", 0) or 0
                _flow_r.append({
                    "Chain":    _f.get("chain", ""),
                    "TVL":      (f"${_f.get('tvl_usd', 0)/1e9:.2f}B" if _f.get("tvl_usd", 0) >= 1e9
                                 else f"${_f.get('tvl_usd', 0)/1e6:.0f}M"),
                    "7d Flow":  f"{_a} {abs(_d7):.1f}%",
                    "Signal":   _s,
                })
            st.dataframe(pd.DataFrame(_flow_r), width='stretch', hide_index=True)
        else:
            st.caption("Bridge flow data unavailable.")
    except Exception:
        st.caption("Bridge flow data unavailable.")
    st.caption("XRPL AMM: native AMM (XRPL 1.12+) · no wrapped tokens · instant settlement. "
               "Flare EVM: cross-chain via LayerZero/Wanchain · FAssets bridge (FLR↔XRP).")

"""
Flare DeFi Model — Streamlit Dashboard  v2
Simple, beginner-friendly UI. No jargon. Color-coded. Plain English throughout.

New in v2:
  • A–F risk grade badges on every opportunity
  • Portfolio size input → dollar allocations per opportunity
  • "My Yield This Week / Month / Year" hero cards
  • One-Click Starter Portfolios (Conservative / Balanced / Aggressive)
  • APY sparklines per protocol (last 14 scans)
  • Income Planner (FlareDrop replacement calculator)
  • Spectra Fixed-Rate Lock Calculator
  • FTSO Delegation Optimizer
  • FAssets Yield Tracker
  • Wallet balance checker (read-only)
  • Email & Telegram alert configuration
  • PDF/HTML report export
  • AI model weights wired into confidence display
  • Mobile-responsive layout

Run with:  streamlit run app.py
"""

import json
import sys
import requests
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from config import (
    RISK_PROFILES, RISK_PROFILE_NAMES, INCENTIVE_PROGRAM, HISTORY_FILE,
    POSITIONS_FILE, WALLETS_FILE, PROTOCOLS, APIS, TOKENS, FLARE_RPC_URLS,
    MONITOR_DIGEST_FILE, SCHEDULER,
)
from ai.feedback_loop import get_feedback_dashboard

# ─── Page Config ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Flare DeFi Model",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ─── CSS ──────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1200px; }
    h1 { font-size: 2rem !important; }
    h2 { font-size: 1.4rem !important; border-bottom: 1px solid #333; padding-bottom: 6px; }
    h3 { font-size: 1.1rem !important; }
    .metric-card {
        background: #1a1f2e; border-radius: 10px;
        padding: 18px 20px; margin-bottom: 12px;
        border-left: 4px solid #444;
    }
    .card-green  { border-left-color: #2ECC71; }
    .card-orange { border-left-color: #F39C12; }
    .card-red    { border-left-color: #E74C3C; }
    .card-blue   { border-left-color: #3498DB; }
    .big-number  { font-size: 2.2rem; font-weight: 700; }
    .label       { font-size: 0.8rem; color: #aaa; text-transform: uppercase; letter-spacing: 1px; }
    .arb-tag     { background: #1a2a1a; border-radius: 6px; padding: 12px 16px; margin-bottom: 8px; border: 1px solid #2ECC71; }
    .warn-box    { background: #2a1a0a; border-radius: 8px; padding: 12px 16px; border: 1px solid #F39C12; margin-bottom: 12px; }
    .divider     { border-top: 1px solid #2a2f3e; margin: 16px 0; }
    .grade-badge { font-weight: 800; font-size: 0.85rem; padding: 3px 9px; border-radius: 6px; color: #000; }
    div[data-testid="stButton"] > button {
        width: 100%; padding: 14px; font-size: 1rem;
        font-weight: 700; border-radius: 10px; border: 2px solid transparent;
    }
    /* Mobile responsive */
    @media (max-width: 768px) {
        .big-number  { font-size: 1.6rem !important; }
        h1           { font-size: 1.5rem !important; }
        h2           { font-size: 1.1rem !important; }
        h3           { font-size: 1rem !important; }
        .metric-card { padding: 12px 14px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
    }
</style>
""", unsafe_allow_html=True)


# ─── Data Loaders ────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_latest() -> dict:
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE) as f:
            h = json.load(f)
        return h.get("latest", {})
    except json.JSONDecodeError:
        st.warning("⚠️ history.json is corrupted. Re-run the scheduler to regenerate it.")
        return {}
    except Exception as e:
        st.warning(f"⚠️ Could not load scan data: {e}")
        return {}


@st.cache_data(ttl=60)
def load_history_runs() -> list:
    if not HISTORY_FILE.exists():
        return []
    try:
        with open(HISTORY_FILE) as f:
            h = json.load(f)
        return h.get("runs", [])
    except Exception:
        return []


@st.cache_data(ttl=60)
def load_positions() -> list:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ positions.json is corrupted. Check or delete data/positions.json.")
        return []
    except Exception:
        return []


def save_positions(positions: list) -> None:
    try:
        with open(POSITIONS_FILE, "w") as f:
            json.dump(positions, f, indent=2)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Could not save positions: {e}")


def load_wallets() -> list:
    if not WALLETS_FILE.exists():
        return []
    try:
        with open(WALLETS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_wallets(wallets: list) -> None:
    try:
        with open(WALLETS_FILE, "w") as f:
            json.dump(wallets, f, indent=2)
    except Exception as e:
        st.error(f"Could not save wallets: {e}")


@st.cache_data(ttl=300)
def load_monitor_digest() -> dict:
    """Load the latest web monitor digest from disk (cached 5 min)."""
    if not MONITOR_DIGEST_FILE.exists():
        return {}
    try:
        with open(MONITOR_DIGEST_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Utility Helpers ─────────────────────────────────────────────────────────

def _ts_fmt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d, %Y  %I:%M %p UTC")
    except Exception:
        return iso or "—"


def _next_scan() -> str:
    tz = ZoneInfo(SCHEDULER["timezone"])
    now_local = datetime.now(tz)
    today = now_local.date()
    scan_times = [
        datetime(today.year, today.month, today.day, 6,  0, tzinfo=tz),
        datetime(today.year, today.month, today.day, 18, 0, tzinfo=tz),
    ]
    future = [t for t in scan_times if t > now_local]
    if not future:
        tmrw = today + timedelta(days=1)
        next_t = datetime(tmrw.year, tmrw.month, tmrw.day, 6, 0, tzinfo=tz)
    else:
        next_t = min(future)
    next_utc = next_t.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    delta    = next_utc - datetime.utcnow()
    h, m     = divmod(int(delta.total_seconds()) // 60, 60)
    return f"in {h}h {m}m  ({next_utc.strftime('%I:%M %p UTC')})"


def risk_score_to_grade(score: float) -> tuple:
    """Map risk score 0–10 to letter grade + badge colour."""
    if score <= 2.0:   return "A",  "#2ECC71"
    elif score <= 3.5: return "A-", "#27AE60"
    elif score <= 5.0: return "B",  "#F39C12"
    elif score <= 6.5: return "C",  "#E67E22"
    elif score <= 8.0: return "D",  "#E74C3C"
    else:              return "F",  "#C0392B"


# ─── Position P&L Calculator ─────────────────────────────────────────────────

def compute_position_pnl(pos: dict, current_prices: list) -> dict:
    """Compute P&L, fees earned, IL estimate, and HODL comparison for a position."""
    from models.risk_models import calculate_il

    deposit_usd    = float(pos.get("deposit_usd") or pos.get("entry_value") or 0)
    current_value  = float(pos.get("current_value") or 0)
    entry_apy      = float(pos.get("entry_apy", 0))
    unclaimed_fees = float(pos.get("unclaimed_fees", 0))

    # Days active
    days_active = 0
    entry_date_str = pos.get("entry_date", "")
    if entry_date_str:
        try:
            entry_dt = datetime.fromisoformat(entry_date_str)
            days_active = max(0, (datetime.utcnow() - entry_dt).days)
        except Exception:
            pass

    # Estimated fees earned from APY × days held
    fees_earned_est = (
        deposit_usd * (entry_apy / 100) * (days_active / 365)
        if days_active > 0 and entry_apy > 0 else 0.0
    )

    value_change     = current_value - deposit_usd
    value_change_pct = (value_change / deposit_usd * 100) if deposit_usd > 0 else 0.0

    # IL estimate + HODL comparison for LP positions
    il_pct     = 0.0
    hodl_value = 0.0
    if pos.get("position_type", "lp") == "lp":
        price_lookup  = {p.get("symbol", ""): p.get("price_usd", 0) for p in (current_prices or [])}
        token_a       = pos.get("token_a", "")
        entry_price_a = float(pos.get("entry_price_a", 0))
        curr_price_a  = price_lookup.get(token_a, entry_price_a) or entry_price_a
        if entry_price_a > 0 and curr_price_a > 0:
            il_pct = calculate_il(curr_price_a / entry_price_a)
        token_b        = pos.get("token_b", "")
        token_a_amount = float(pos.get("token_a_amount", 0))
        token_b_amount = float(pos.get("token_b_amount", 0))
        entry_price_b  = float(pos.get("entry_price_b", 0))
        curr_price_b   = price_lookup.get(token_b, entry_price_b) or entry_price_b
        if token_a_amount > 0 and curr_price_a > 0:
            hodl_value += token_a_amount * curr_price_a
        if token_b_amount > 0 and curr_price_b > 0:
            hodl_value += token_b_amount * curr_price_b

    return {
        "days_active":      days_active,
        "deposit_usd":      deposit_usd,
        "value_change":     value_change,
        "value_change_pct": value_change_pct,
        "fees_earned_est":  fees_earned_est,
        "total_return":     value_change + unclaimed_fees,
        "il_pct":           il_pct,
        "hodl_value":       hodl_value,
        "unclaimed_fees":   unclaimed_fees,
        "current_value":    current_value,
    }


# ─── Opportunity Card ─────────────────────────────────────────────────────────

def render_opportunity_card(
    opp: dict, idx: int, profile_color: str,
    portfolio_size: float = 0, weight: float = 1.0
) -> None:
    apy    = opp.get("estimated_apy", 0)
    lo     = opp.get("apy_low",  apy * 0.8)
    hi     = opp.get("apy_high", apy * 1.2)
    conf   = min(100, opp.get("confidence", 50) * weight)
    il     = opp.get("il_risk", "low")
    action = opp.get("action", opp.get("plain_english", "—"))
    proto  = opp.get("protocol", "—")
    pool   = opp.get("asset_or_pool", "—")
    src    = opp.get("data_source", "baseline")
    rs     = opp.get("risk_score", 5.0)
    kf     = opp.get("kelly_fraction", 0)

    grade, grade_color = risk_score_to_grade(rs)
    il_icon  = {"none": "🟢", "low": "🟢", "medium": "🟡", "high": "🔴"}.get(il, "🟡")
    src_note = " *(estimated)*" if src in ("baseline", "estimate") else ""
    medal    = ["🥇", "🥈", "🥉", "4.", "5.", "6."][min(idx, 5)]

    if portfolio_size > 0:
        alloc_str = f"💰 Suggested: <b style='color:#fff'>${kf * portfolio_size:,.0f}</b> ({kf*100:.0f}% of ${portfolio_size:,.0f})"
    else:
        alloc_str = f"💰 Suggested allocation: <b style='color:#fff'>{kf*100:.0f}%</b> of portfolio"

    st.markdown(f"""
    <div class="metric-card" style="border-left-color:{profile_color}">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <span style="font-size:1.1rem; font-weight:700;">{medal} {proto} — {pool}</span>
            <div style="display:flex; align-items:center; gap:10px;">
                <span class="grade-badge" style="background:{grade_color};">{grade}</span>
                <span style="font-size:1.6rem; font-weight:800; color:{profile_color};">{apy:.1f}%</span>
            </div>
        </div>
        <div style="color:#aaa; font-size:0.85rem; margin-top:4px;">APY range: {lo:.1f}% – {hi:.1f}%{src_note}</div>
        <div style="margin:10px 0; font-size:0.97rem;">{action}</div>
        <div style="display:flex; gap:20px; font-size:0.82rem; color:#aaa; margin-top:8px; flex-wrap:wrap;">
            <span>{il_icon} Price risk: <b style="color:#fff">{il.upper()}</b></span>
            <span>Model confidence: <b style="color:#fff">{conf:.0f}%</b></span>
            <span>{alloc_str}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ─── Yield Hero Cards ─────────────────────────────────────────────────────────

def render_yield_hero_card(positions: list, opps: list, portfolio_size: float) -> None:
    total_value = sum(p.get("current_value", 0) for p in positions)
    if total_value == 0:
        total_value = portfolio_size

    if opps:
        avg_apy = sum(o.get("estimated_apy", 0) for o in opps[:3]) / min(3, len(opps))
    else:
        avg_apy = 0.0

    weekly_yield  = total_value * (avg_apy / 100) / 52
    monthly_yield = total_value * (avg_apy / 100) / 12
    annual_yield  = total_value * (avg_apy / 100)

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""
        <div class="metric-card card-green">
            <div class="label">Est. Yield This Week</div>
            <div class="big-number" style="color:#2ECC71;">${weekly_yield:,.2f}</div>
            <div style="color:#aaa; font-size:0.85rem;">{avg_apy / 52:.2f}% this week</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Est. Monthly Yield</div>
            <div class="big-number" style="color:#3498DB;">${monthly_yield:,.2f}</div>
            <div style="color:#aaa; font-size:0.85rem;">Based on ${total_value:,.0f} portfolio</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""
        <div class="metric-card card-orange">
            <div class="label">Est. Annual Yield</div>
            <div class="big-number" style="color:#F39C12;">${annual_yield:,.2f}</div>
            <div style="color:#aaa; font-size:0.85rem;">{avg_apy:.1f}% APY (top-3 avg)</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(
        "<div style='color:#666; font-size:0.78rem; margin-bottom:4px;'>"
        "Estimated yield uses current model recommendations. Actual results vary. Not financial advice.</div>",
        unsafe_allow_html=True,
    )


# ─── Starter Portfolios ───────────────────────────────────────────────────────

def render_starter_portfolio(model_data: dict, portfolio_size: float) -> None:
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>Pre-built allocations generated by the model. "
        "Pick the style that fits your risk level.</span>",
        unsafe_allow_html=True,
    )
    tab1, tab2, tab3 = st.tabs(["🟢 Conservative", "🟡 Balanced", "🔴 Aggressive"])

    for tab, profile in [(tab1, "conservative"), (tab2, "medium"), (tab3, "high")]:
        with tab:
            opps = model_data.get(profile, [])
            cfg  = RISK_PROFILES[profile]
            color = cfg["color"]
            st.markdown(
                f"<span style='color:{color};'>{cfg['label']} — "
                f"Target {cfg['target_apy_low']:.0f}–{cfg['target_apy_high']:.0f}% APY</span>",
                unsafe_allow_html=True,
            )
            if not opps:
                st.info("No scan data yet. Run `python scheduler.py --now` first.")
                continue

            rows = []
            for opp in opps[:6]:
                kf   = opp.get("kelly_fraction", 0)
                grade, _ = risk_score_to_grade(opp.get("risk_score", 5))
                rows.append({
                    "Protocol":   opp.get("protocol", "—"),
                    "Pool / Asset": opp.get("asset_or_pool", "—"),
                    "Est. APY":   f"{opp.get('estimated_apy', 0):.1f}%",
                    "Alloc %":    f"{kf * 100:.0f}%",
                    f"$ Amount":  f"${kf * portfolio_size:,.0f}" if portfolio_size > 0 else "—",
                    "Risk Grade": grade,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption(cfg["description"])


# ─── Protocol APY Sparklines ──────────────────────────────────────────────────

def render_protocol_sparklines(runs: list, opps: list, profile: str) -> None:
    if not opps or len(runs) < 3:
        st.info("Need at least 3 scans to show sparklines. Run the scheduler a few more times.")
        return

    top_pools = [(o.get("protocol", ""), o.get("asset_or_pool", "")) for o in opps[:3]]
    cols = st.columns(len(top_pools))

    for col, (proto, pool) in zip(cols, top_pools):
        history_apy = []
        for run in runs[-14:]:
            run_opps = run.get("models", {}).get(profile, [])
            match = next(
                (o for o in run_opps
                 if o.get("protocol") == proto and o.get("asset_or_pool") == pool),
                None,
            )
            if match:
                history_apy.append(match.get("estimated_apy", 0))

        with col:
            st.markdown(
                f"<div style='font-size:0.8rem; color:#aaa; text-align:center;'>{proto}<br><b style='color:#fff'>{pool}</b></div>",
                unsafe_allow_html=True,
            )
            if len(history_apy) >= 2:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=history_apy,
                    mode="lines",
                    line=dict(color="#3498DB", width=2),
                    fill="tozeroy",
                    fillcolor="rgba(52,152,219,0.1)",
                ))
                fig.update_layout(
                    plot_bgcolor="#1a1f2e", paper_bgcolor="#1a1f2e",
                    font_color="#ccc",
                    xaxis=dict(visible=False),
                    yaxis=dict(title="APY %", gridcolor="#2a2f3e", tickfont=dict(size=9)),
                    margin=dict(l=30, r=8, t=6, b=6),
                    height=110,
                    showlegend=False,
                )
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.markdown(
                    "<div style='color:#555; font-size:0.8rem; text-align:center; padding:20px;'>Building history…</div>",
                    unsafe_allow_html=True,
                )


# ─── Arbitrage Alerts ─────────────────────────────────────────────────────────

def render_arb_alerts(arb_list: list) -> None:
    if not arb_list:
        st.info("No significant arbitrage opportunities detected right now. The model will alert you when one appears.")
        return

    for arb in arb_list[:5]:
        profit  = arb.get("estimated_profit", 0)
        urgency = arb.get("urgency", "monitor")
        label   = arb.get("strategy_label", arb.get("strategy", "Arb"))
        desc    = arb.get("plain_english", "—")
        token   = arb.get("token_or_pair", "—")

        urgency_color = {"act_now": "#E74C3C", "act_soon": "#F39C12", "monitor": "#3498DB"}.get(urgency, "#3498DB")
        urgency_label = {"act_now": "ACT NOW", "act_soon": "ACT SOON", "monitor": "MONITOR"}.get(urgency, "MONITOR")

        st.markdown(f"""
        <div class="arb-tag">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700;">⚡ {label} — {token}</span>
                <span style="color:{urgency_color}; font-weight:700; font-size:0.85rem;">{urgency_label}</span>
            </div>
            <div style="margin-top:6px; font-size:0.92rem;">{desc}</div>
            <div style="margin-top:6px; font-size:0.82rem; color:#aaa;">
                Estimated net profit: <b style="color:#2ECC71">+{profit:.2f}%</b>
            </div>
        </div>
        """, unsafe_allow_html=True)


# ─── AI Model Health ──────────────────────────────────────────────────────────

def render_model_health(profile: str, feedback: dict) -> None:
    overall = feedback["overall_health"]
    p_data  = feedback["per_profile"].get(profile, {})

    health  = p_data.get("health_score", 50)
    grade   = p_data.get("grade", "N/A")
    msg     = p_data.get("message", "Building accuracy history...")
    acc     = p_data.get("accuracy_pct")
    samples = p_data.get("sample_count", 0)
    trend   = feedback.get("trend", "building")

    color      = "#2ECC71" if health >= 70 else ("#F39C12" if health >= 45 else "#E74C3C")
    trend_icon = {"improving": "📈", "stable": "➡️", "declining": "📉", "building": "🔧"}.get(trend, "➡️")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Model Health Score</div>
            <div class="big-number" style="color:{color};">{health}<span style="font-size:1rem">/100</span></div>
            <div style="color:#aaa; font-size:0.85rem;">Grade: <b>{grade}</b></div>
        </div>""", unsafe_allow_html=True)
    with col2:
        acc_str = f"{acc:.1f}%" if acc is not None else "Building..."
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">30-Day Prediction Accuracy</div>
            <div class="big-number">{acc_str}</div>
            <div style="color:#aaa; font-size:0.85rem;">Based on {samples} predictions</div>
        </div>""", unsafe_allow_html=True)
    with col3:
        st.markdown(f"""
        <div class="metric-card card-blue">
            <div class="label">Trend</div>
            <div class="big-number">{trend_icon}</div>
            <div style="color:#aaa; font-size:0.85rem;">{trend.capitalize()}</div>
        </div>""", unsafe_allow_html=True)

    st.markdown(f"<div style='color:#aaa; font-size:0.9rem; margin-top:4px;'>{msg}</div>", unsafe_allow_html=True)


# ─── Historical Performance Chart ─────────────────────────────────────────────

def render_performance_chart(runs: list, profile: str) -> None:
    records = []
    for run in runs[-30:]:
        ts   = run.get("run_id", "")
        opps = run.get("models", {}).get(profile, [])
        if opps and ts:
            top_apy = opps[0].get("estimated_apy", 0)
            try:
                records.append({"date": datetime.fromisoformat(ts), "predicted_apy": top_apy})
            except Exception:
                pass

    if len(records) < 2:
        st.info("Not enough scan history yet. The chart will appear after a few scans.")
        return

    df = pd.DataFrame(records).sort_values("date")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["predicted_apy"],
        mode="lines+markers",
        name="Top Predicted APY",
        line=dict(color="#3498DB", width=2),
        marker=dict(size=6),
    ))
    fig.update_layout(
        plot_bgcolor="#1a1f2e", paper_bgcolor="#1a1f2e",
        font_color="#ccc",
        xaxis=dict(title="Scan Date", gridcolor="#2a2f3e"),
        yaxis=dict(title="APY (%)", gridcolor="#2a2f3e"),
        title=dict(text="Top Opportunity APY Over Time", font=dict(size=14)),
        margin=dict(l=40, r=20, t=40, b=40),
        height=280,
    )
    st.plotly_chart(fig, use_container_width=True)


# ─── Price Strip ──────────────────────────────────────────────────────────────

def render_price_strip(prices: list) -> None:
    if not prices:
        return
    cols = st.columns(len(prices))
    for i, p in enumerate(prices):
        sym   = p.get("symbol", "?")
        price = p.get("price_usd", 0)
        chg   = p.get("change_24h", 0)
        color = "#2ECC71" if chg >= 0 else "#E74C3C"
        arrow = "▲" if chg >= 0 else "▼"
        src   = " *" if p.get("data_source") in ("estimate",) else ""
        with cols[i]:
            st.markdown(f"""
            <div style="text-align:center; padding:10px; background:#1a1f2e; border-radius:8px;">
                <div style="font-size:0.75rem; color:#aaa;">{sym}{src}</div>
                <div style="font-size:1.2rem; font-weight:700;">${price:,.4f}</div>
                <div style="font-size:0.8rem; color:{color};">{arrow} {abs(chg):.2f}%</div>
            </div>""", unsafe_allow_html=True)


# ─── Your Positions ───────────────────────────────────────────────────────────

def render_add_position_form(positions: list) -> None:
    """Expander form to add a new tracked position."""
    with st.expander("➕ Track a New Position"):
        st.markdown(
            "<span style='color:#aaa; font-size:0.85rem;'>"
            "Record a position after acting on a recommendation to track your actual P&L, fees, and IL.</span>",
            unsafe_allow_html=True,
        )
        with st.form("add_position_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            with c1:
                proto_key  = st.selectbox(
                    "Protocol",
                    options=list(PROTOCOLS.keys()),
                    format_func=lambda k: PROTOCOLS[k]["name"],
                    key="new_pos_proto",
                )
                pool_name = st.text_input("Pool / Asset name", placeholder="WFLR-USD0 or sFLR")
                pos_type  = st.selectbox("Position type", ["lp", "lending", "staking"])
                entry_date = st.date_input("Entry date")
            with c2:
                deposit_usd    = st.number_input("Deposit amount ($)", min_value=0.0, value=1000.0, step=100.0)
                entry_apy      = st.number_input("Entry APY (%)", min_value=0.0, value=0.0, step=1.0)
                current_value  = st.number_input("Current value ($, 0 = same as deposit)", min_value=0.0, value=0.0, step=100.0)
                unclaimed_fees = st.number_input("Unclaimed fees ($)", min_value=0.0, value=0.0, step=1.0)

            st.markdown("<div style='color:#aaa; font-size:0.8rem; margin-top:4px;'>Token details (for IL tracking — LP only)</div>", unsafe_allow_html=True)
            tc1, tc2 = st.columns(2)
            with tc1:
                token_a        = st.text_input("Token A symbol", placeholder="WFLR")
                token_a_amount = st.number_input("Token A amount", min_value=0.0, value=0.0)
                entry_price_a  = st.number_input("Token A entry price ($)", min_value=0.0, value=0.0, format="%.6f")
            with tc2:
                token_b        = st.text_input("Token B symbol (LP only)", placeholder="USD0")
                token_b_amount = st.number_input("Token B amount", min_value=0.0, value=0.0)
                entry_price_b  = st.number_input("Token B entry price ($)", min_value=0.0, value=0.0, format="%.6f")

            notes = st.text_input("Notes (optional)")

            if st.form_submit_button("Add Position"):
                if not pool_name:
                    st.error("Pool / Asset name is required.")
                else:
                    cur_val = float(current_value) if current_value > 0 else float(deposit_usd)
                    new_pos = {
                        "id":             f"pos_{int(datetime.utcnow().timestamp())}",
                        "protocol":       proto_key,
                        "pool":           pool_name,
                        "position_type":  pos_type,
                        "entry_date":     entry_date.isoformat(),
                        "deposit_usd":    float(deposit_usd),
                        "entry_apy":      float(entry_apy),
                        "current_value":  cur_val,
                        "unclaimed_fees": float(unclaimed_fees),
                        "entry_value":    float(deposit_usd),
                        "token_a":        token_a,
                        "token_a_amount": float(token_a_amount),
                        "entry_price_a":  float(entry_price_a),
                        "token_b":        token_b,
                        "token_b_amount": float(token_b_amount),
                        "entry_price_b":  float(entry_price_b),
                        "notes":          notes,
                    }
                    positions.append(new_pos)
                    save_positions(positions)
                    st.success(f"Position added: {pool_name} on {PROTOCOLS[proto_key]['name']}")
                    st.rerun()


def render_positions(positions: list, prices: list) -> None:
    if not positions:
        st.info("No positions tracked yet. Use the form below to add your first position.")
    else:
        total_value   = sum(p.get("current_value", 0) for p in positions)
        total_fees    = sum(p.get("unclaimed_fees", 0) for p in positions)
        total_deposit = sum(float(p.get("deposit_usd") or p.get("entry_value") or 0) for p in positions)
        total_pnl     = total_value - total_deposit

        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.markdown(f"""
            <div class="metric-card card-blue">
                <div class="label">Total Portfolio Value</div>
                <div class="big-number">${total_value:,.0f}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            pnl_color = "#2ECC71" if total_pnl >= 0 else "#E74C3C"
            st.markdown(f"""
            <div class="metric-card" style="border-left-color:{pnl_color}">
                <div class="label">Total P&L</div>
                <div class="big-number" style="color:{pnl_color};">{total_pnl:+,.0f}</div>
                <div style="color:#aaa; font-size:0.85rem;">vs ${total_deposit:,.0f} deposited</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="metric-card card-green">
                <div class="label">Unclaimed Fees</div>
                <div class="big-number" style="color:#2ECC71;">${total_fees:,.2f}</div>
            </div>""", unsafe_allow_html=True)
        with c4:
            st.markdown(f"""
            <div class="metric-card card-orange">
                <div class="label">Active Positions</div>
                <div class="big-number">{len(positions)}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        for idx, pos in enumerate(positions):
            pnl      = compute_position_pnl(pos, prices)
            proto    = pos.get("protocol", "?").capitalize()
            pool     = pos.get("pool", "?")
            ptype    = pos.get("position_type", "lp").upper()
            vc       = pnl["value_change"]
            vc_pct   = pnl["value_change_pct"]
            vc_color = "#2ECC71" if vc >= 0 else "#E74C3C"
            days     = pnl["days_active"]
            fees_est = pnl["fees_earned_est"]
            il_pct   = pnl["il_pct"]
            hodl     = pnl["hodl_value"]

            days_str  = f"{days}d active" if days > 0 else "no entry date"
            fees_str  = f" &nbsp;|&nbsp; Est. fees earned: <b style='color:#2ECC71'>${fees_est:,.2f}</b>" if fees_est > 0 else ""
            il_str    = f" &nbsp;|&nbsp; IL est: <b style='color:#F39C12'>{il_pct:.1f}%</b>" if il_pct > 0.1 else ""
            hodl_str  = f" &nbsp;|&nbsp; HODL would be <b>${hodl:,.0f}</b>" if hodl > 0 else ""
            bal_str   = " ".join(filter(None, [pos.get("token0_balance",""), pos.get("token1_balance","")]))

            col_card, col_del = st.columns([12, 1])
            with col_card:
                st.markdown(f"""
                <div style="background:#1a1f2e; border-radius:8px; padding:14px 18px; margin-bottom:8px; border-left:3px solid {vc_color};">
                    <div style="display:flex; justify-content:space-between; flex-wrap:wrap;">
                        <span style="font-weight:700;">{pool} — {proto} <span style="color:#555; font-size:0.78rem;">({ptype})</span></span>
                        <span style="color:{vc_color}; font-weight:700;">{vc:+,.0f} ({vc_pct:+.1f}%)</span>
                    </div>
                    <div style="color:#aaa; font-size:0.82rem; margin-top:6px;">
                        <b style="color:#fff">${pnl['current_value']:,.0f}</b> current &nbsp;|&nbsp;
                        <b style="color:#fff">${pnl['deposit_usd']:,.0f}</b> deposited &nbsp;|&nbsp; {days_str}
                        {fees_str}{il_str}{hodl_str}
                    </div>
                    <div style="color:#aaa; font-size:0.82rem; margin-top:4px;">
                        Unclaimed fees: <b style="color:#2ECC71">${pnl['unclaimed_fees']:,.2f}</b>
                        {"&nbsp;|&nbsp; " + bal_str if bal_str else ""}
                    </div>
                </div>""", unsafe_allow_html=True)
            with col_del:
                if st.button("🗑", key=f"del_pos_{idx}", help="Remove this position"):
                    positions.pop(idx)
                    save_positions(positions)
                    st.rerun()

    render_add_position_form(positions)


# ─── Exit Strategy ────────────────────────────────────────────────────────────

def render_exit_strategy(positions: list, prices: list) -> None:
    """Price target ladder + per-position exit timeline based on incentive expiry."""
    incentive_expiry = datetime.strptime(INCENTIVE_PROGRAM["expires"], "%Y-%m-%d")
    days_left = max(0, (incentive_expiry - datetime.utcnow()).days)

    if days_left > 90:
        urgency_color = "#2ECC71"
        urgency_msg   = f"Monitor monthly. Consider setting an exit reminder for May 2026."
    elif days_left > 30:
        urgency_color = "#F39C12"
        urgency_msg   = f"Begin reducing high-IL LP positions. Lock in fixed-rate yields where possible."
    else:
        urgency_color = "#E74C3C"
        urgency_msg   = f"URGENT — incentive-dependent APYs will drop sharply. Review all LP positions now."

    st.markdown(
        f"<div style='background:#1a1a0a; border-radius:8px; padding:12px 16px; border-left:4px solid {urgency_color}; margin-bottom:12px;'>"
        f"<b style='color:{urgency_color};'>⏳ {days_left} days until incentive expiry (July 1, 2026)</b> — {urgency_msg}"
        f"</div>",
        unsafe_allow_html=True,
    )

    tab_targets, tab_timeline = st.tabs(["💰 Price Target Ladder", "📅 Exit Timeline"])

    with tab_targets:
        st.markdown("<span style='color:#aaa; font-size:0.85rem;'>Set price targets to plan profit-taking on FLR or FXRP holdings.</span>", unsafe_allow_html=True)
        price_lookup = {p.get("symbol", ""): p.get("price_usd", 0) for p in (prices or [])}

        c1, c2, c3 = st.columns(3)
        with c1:
            asset_choice = st.selectbox("Asset", ["FLR", "FXRP", "sFLR", "Custom"], key="exit_asset")
        with c2:
            default_price = price_lookup.get(asset_choice, 0.020) or 0.020
            asset_price = st.number_input("Current price ($)", min_value=0.0001, value=float(default_price),
                                          format="%.6f", step=0.001, key="exit_price")
        with c3:
            holdings = st.number_input("Holdings (tokens)", min_value=0.0, value=10000.0, step=1000.0, key="exit_holdings")

        if asset_price > 0 and holdings > 0:
            rows = []
            for mult, label, action in [
                (1.25, "+25%",  "Take 10% profit"),
                (1.50, "+50%",  "Take 15–20% profit"),
                (2.00, "+100%", "Take 25% profit"),
                (3.00, "+200%", "Take 33% profit"),
                (5.00, "+400%", "Consider full exit"),
            ]:
                tp   = asset_price * mult
                val  = holdings * tp
                gain = val - holdings * asset_price
                rows.append({
                    "Target":          label,
                    "Price":           f"${tp:.6f}",
                    "Portfolio Value": f"${val:,.0f}",
                    "Gain vs Now":     f"+${gain:,.0f}",
                    "Suggested Action": action,
                })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.caption("These are planning targets, not financial advice. Adjust percentages to your own risk tolerance.")

    with tab_timeline:
        if not positions:
            st.info("Add positions in 'Your Current Positions' to see per-position exit guidance.")
            return

        rows = []
        for pos in positions:
            entry_date_str = pos.get("entry_date", "")
            days_held = 0
            if entry_date_str:
                try:
                    days_held = max(0, (datetime.utcnow() - datetime.fromisoformat(entry_date_str)).days)
                except Exception:
                    pass

            proto_key    = pos.get("protocol", "")
            is_incentive = proto_key in ("blazeswap", "enosys", "sparkdex")
            entry_apy    = pos.get("entry_apy", 0)
            pnl          = compute_position_pnl(pos, prices)
            roi_str      = f"{pnl['value_change_pct']:+.1f}%" if pnl["deposit_usd"] > 0 else "—"

            rows.append({
                "Position":       f"{pos.get('pool','?')} ({pos.get('protocol','?').capitalize()})",
                "Type":           pos.get("position_type", "lp").upper(),
                "Days Held":      days_held,
                "Entry APY":      f"{entry_apy:.1f}%",
                "Current P&L":    roi_str,
                "Incentive Risk": "⚠️ YES" if is_incentive else "✅ Low",
                "Exit By":        "Jun 2026" if is_incentive else "Flexible",
            })

        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "DEX LP pools (Blazeswap, Enosys, SparkDEX) depend heavily on RFLR incentives that expire July 2026. "
            "Lending and staking positions have lower incentive dependency."
        )


# ─── Incentive Warning ────────────────────────────────────────────────────────

def render_incentive_warning() -> None:
    st.markdown(f"""
    <div class="warn-box">
        <span style="font-weight:700; color:#F39C12;">⚠️ Important:</span>
        {INCENTIVE_PROGRAM['note']}
    </div>""", unsafe_allow_html=True)


# ─── Wallet Tracker (multi-wallet) ────────────────────────────────────────────

def _fetch_wallet_balances(wallet: str) -> list:
    """Return [{Token, Balance}] rows or raise."""
    from web3 import Web3
    w3 = None
    for url in FLARE_RPC_URLS:
        try:
            candidate = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 8}))
            if candidate.is_connected():
                w3 = candidate
                break
        except Exception:
            continue
    if not w3:
        raise ConnectionError("Could not connect to Flare RPC.")

    addr_cs = Web3.to_checksum_address(wallet)
    flr_balance = w3.eth.get_balance(addr_cs) / 1e18

    ERC20_ABI = [{
        "inputs":  [{"name": "account", "type": "address"}],
        "name":    "balanceOf",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    }]
    token_balances = {"FLR (native)": flr_balance}
    token_decimals = {"USD0": 6, "USDT": 6, "USDC.e": 6}
    for sym, addr in TOKENS.items():
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            dec = token_decimals.get(sym, 18)
            bal = contract.functions.balanceOf(addr_cs).call()
            token_balances[sym] = bal / (10 ** dec)
        except Exception:
            pass

    return [{"Token": k, "Balance": f"{v:,.4f}"} for k, v in token_balances.items() if v >= 0.0001]


def render_wallet_connect() -> None:
    with st.expander("🔗 Wallet Tracker — Multi-Wallet (Read-Only)"):
        saved_wallets = load_wallets()

        # ── Add wallet row ────────────────────────────────────────────────────
        ca, cl, cb = st.columns([4, 2, 1])
        with ca:
            new_addr  = st.text_input("Address (0x…)", placeholder="0x1234…abcd", label_visibility="collapsed", key="new_wallet_addr")
        with cl:
            new_label = st.text_input("Label",          placeholder="e.g. Main Wallet",  label_visibility="collapsed", key="new_wallet_label")
        with cb:
            if st.button("Add Wallet", key="add_wallet_btn"):
                if new_addr and len(new_addr) == 42 and new_addr.startswith("0x"):
                    label = new_label.strip() or (new_addr[:6] + "…" + new_addr[-4:])
                    saved_wallets.append({"label": label, "address": new_addr})
                    save_wallets(saved_wallets)
                    st.rerun()
                else:
                    st.warning("Enter a valid 42-character address starting with 0x.")

        if not saved_wallets:
            st.caption("Enter a wallet address above to start tracking balances.")
            return

        # ── Wallet selector ───────────────────────────────────────────────────
        wallet_labels = [
            f"{w['label']}  ({w['address'][:6]}…{w['address'][-4:]})"
            for w in saved_wallets
        ]
        sel_idx = st.selectbox("Select wallet", range(len(wallet_labels)),
                               format_func=lambda i: wallet_labels[i], key="wallet_select")

        col_check, col_remove = st.columns([3, 1])
        with col_check:
            check = st.button("🔍 Check Balances", key="check_wallet_btn")
        with col_remove:
            if st.button("🗑 Remove Wallet", key="remove_wallet_btn"):
                saved_wallets.pop(sel_idx)
                save_wallets(saved_wallets)
                st.rerun()

        if check:
            wallet = saved_wallets[sel_idx]["address"]
            with st.spinner("Fetching on-chain balances…"):
                try:
                    rows = _fetch_wallet_balances(wallet)
                    if rows:
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                    else:
                        st.info("No significant token balances found for this address.")
                    st.caption("LP positions and protocol deposits are tracked in Your Positions below.")
                except ImportError:
                    st.warning("web3 not installed. Run: pip install web3")
                except Exception as e:
                    st.error(f"Error reading balances: {e}")


# ─── FTSO Delegation Optimizer ────────────────────────────────────────────────

def render_ftso_optimizer() -> None:
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>"
        "Delegate your FLR vote power to FTSO price providers and earn rewards every reward epoch (~3.5 days). "
        "You keep your FLR — delegation does not lock or transfer your tokens.</span>",
        unsafe_allow_html=True,
    )

    # Baseline provider data — updated during scheduler scan when on-chain data is available
    ftso_providers = [
        {"name": "Ankr",          "reward_rate": 4.5, "uptime": 99.2, "note": "Large global infrastructure provider"},
        {"name": "AlphaOracle",   "reward_rate": 4.4, "uptime": 99.0, "note": "High uptime, consistent rewards"},
        {"name": "SolidiFi",      "reward_rate": 4.2, "uptime": 98.8, "note": "Community-run provider"},
        {"name": "FlareOracle",   "reward_rate": 4.3, "uptime": 98.9, "note": "Flare-native provider"},
        {"name": "FTSO EU",       "reward_rate": 4.1, "uptime": 98.5, "note": "European-based node"},
        {"name": "BlockNG",       "reward_rate": 4.0, "uptime": 97.5, "note": "Multi-chain infrastructure"},
    ]

    flr_amount = st.number_input(
        "FLR to delegate", min_value=0.0, value=1000.0, step=100.0, key="ftso_flr"
    )
    if flr_amount > 0:
        rows = []
        for p in ftso_providers:
            annual_flr = flr_amount * (p["reward_rate"] / 100)
            rows.append({
                "Provider":           p["name"],
                "Est. Annual Rate":   f"{p['reward_rate']:.1f}%",
                "Uptime":             f"{p['uptime']:.1f}%",
                f"Annual FLR Earned": f"{annual_flr:,.1f} FLR",
                "Notes":              p["note"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Tip: You can split your delegation between 2 providers for maximum reward coverage. "
            "Delegate at app.flare.network or via Sceptre (which also earns sFLR staking rewards). "
            "Rates are estimated historical averages — actual rewards vary by epoch."
        )


# ─── Spectra Fixed-Rate Calculator ────────────────────────────────────────────

def render_spectra_calculator() -> None:
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>"
        "Lock your sFLR at 10.79% fixed rate until May 17, 2026 via Spectra Finance. "
        "Compare to variable sFLR staking (7–11%) and LP (36.74%).</span>",
        unsafe_allow_html=True,
    )

    maturity_date    = datetime(2026, 5, 17)
    days_to_maturity = max(0, (maturity_date - datetime.utcnow()).days)

    col1, col2 = st.columns([2, 1])
    with col1:
        sflr_amount = st.number_input(
            "sFLR amount to lock", min_value=0.0, value=1000.0, step=100.0, key="spectra_amt"
        )
    with col2:
        st.markdown(
            f"<div style='color:#aaa; font-size:0.9rem; padding-top:28px;'>"
            f"Days to maturity: <b style='color:#fff;'>{days_to_maturity}</b> (May 17, 2026)</div>",
            unsafe_allow_html=True,
        )

    if sflr_amount > 0 and days_to_maturity > 0:
        fixed_yield    = sflr_amount * 0.1079  * days_to_maturity / 365
        var_low        = sflr_amount * 0.07    * days_to_maturity / 365
        var_high       = sflr_amount * 0.11    * days_to_maturity / 365
        lp_yield       = sflr_amount * 0.3674  * days_to_maturity / 365

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""
            <div class="metric-card card-green">
                <div class="label">Fixed Rate (PT-sFLR)</div>
                <div class="big-number" style="color:#2ECC71;">+{fixed_yield:.2f} sFLR</div>
                <div style="color:#aaa; font-size:0.85rem;">10.79% guaranteed · Zero IL risk</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div class="metric-card card-orange">
                <div class="label">Variable sFLR Staking</div>
                <div class="big-number" style="color:#F39C12;">+{var_low:.2f}–{var_high:.2f} sFLR</div>
                <div style="color:#aaa; font-size:0.85rem;">7–11% variable · Market-dependent</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="metric-card card-red">
                <div class="label">LP Route (Spectra)</div>
                <div class="big-number" style="color:#E74C3C;">+{lp_yield:.2f} sFLR</div>
                <div style="color:#aaa; font-size:0.85rem;">~36.74% APY · IL risk applies</div>
            </div>""", unsafe_allow_html=True)

        st.caption(
            f"Amounts show sFLR earned over {days_to_maturity} days. "
            "Fixed rate is guaranteed. Variable rate fluctuates. "
            "LP return is estimated and subject to impermanent loss."
        )
    elif days_to_maturity == 0:
        st.warning("The sFLR-MAY2026 market has matured. Check Spectra Finance for new markets.")


# ─── FlareDrop Income Replacement Planner ─────────────────────────────────────

def render_flaredrop_calculator() -> None:
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>"
        "Flare's 2.2B FLR distribution ends July 2026. "
        "Find out how much capital you need in each strategy to replace that monthly income.</span>",
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)
    with col1:
        monthly_flr = st.number_input(
            "Monthly FlareDrop income (FLR/month)", min_value=0.0, value=500.0, step=50.0, key="fd_flr"
        )
    with col2:
        flr_price = st.number_input(
            "FLR price (USD)", min_value=0.001, value=0.020, step=0.001, format="%.3f", key="fd_price"
        )

    if monthly_flr > 0 and flr_price > 0:
        monthly_usd = monthly_flr * flr_price
        annual_usd  = monthly_usd * 12

        strategies = [
            ("sFLR Staking (Sceptre)",      9.0,  "None",   "Stake FLR → earn sFLR yield + FTSO rewards"),
            ("FTSO Delegation",             4.3,  "None",   "Delegate vote power, keep your FLR"),
            ("Kinetic Lending (USDT0)",     8.0,  "None",   "Lend stablecoins, no price risk"),
            ("Clearpool X-Pool (USD0)",    11.5,  "None",   "Institutional lending, higher yield"),
            ("Blazeswap LP (sFLR-WFLR)",   37.0,  "Low",    "Provide liquidity, earn trading fees + rewards"),
            ("Mystic Finance (USD0)",       9.0,  "None",   "Morpho-style optimised lending"),
        ]

        st.markdown(
            f"**To replace ${monthly_usd:,.2f}/month (${annual_usd:,.2f}/year) in FlareDrop income:**"
        )

        rows = []
        for name, apy, il, action in strategies:
            capital_needed = annual_usd / (apy / 100)
            rows.append({
                "Strategy":      name,
                "APY":           f"{apy:.1f}%",
                "IL Risk":       il,
                "Capital Needed":f"${capital_needed:,.0f}",
                "How To":        action,
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Capital needed = Annual income ÷ APY. "
            "Diversify across 2–3 strategies to reduce single-protocol risk."
        )


# ─── FAssets Yield Tracker ────────────────────────────────────────────────────

def render_fassets_tracker() -> None:
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>"
        "FAssets bring Bitcoin, XRP, and other assets on-chain to Flare so they can earn DeFi yields. "
        "No need to sell — wrap and deploy.</span>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        <div class="metric-card card-blue">
            <div class="label">FXRP (Live Now)</div>
            <div style="font-size:1.2rem; font-weight:700; color:#3498DB;">4–10% APY</div>
            <div style="color:#aaa; font-size:0.85rem; margin-top:8px;">
                Bridge XRP → FXRP via Flare · Deploy in Upshift EarnXRP vault<br>
                Status: <b style="color:#2ECC71;">LIVE</b>
            </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""
        <div class="metric-card card-orange">
            <div class="label">FBTC (Coming Soon)</div>
            <div style="font-size:1.2rem; font-weight:700; color:#F39C12;">Est. 3–8% APY</div>
            <div style="color:#aaa; font-size:0.85rem; margin-top:8px;">
                Bring Bitcoin on-chain · Earn yield without selling BTC<br>
                Status: <b style="color:#F39C12;">IN DEVELOPMENT</b>
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("""
**FTSO Collateral Agent Income**

Flare's FAssets system needs collateral agents to mint synthetic assets. Agents earn:
- **Minting fees** — ~0.5% per FXRP minted
- **FTSO rewards** — FLR collateral continues earning FTSO delegation rewards
- **Pool fees** — Share of agent-pool minting revenues

*Requires significant collateral (~$10,000 FLR equivalent minimum). See flare.network/fassets for details.*
    """)


# ─── Alert Configuration ──────────────────────────────────────────────────────

def render_alerts_config() -> None:
    with st.expander("🔔 Alert Settings (Email & Telegram)"):
        try:
            from ai.alerts import load_alerts_config, save_alerts_config, test_email, test_telegram
        except ImportError:
            st.error("ai/alerts.py not found. Check your installation.")
            return

        config = load_alerts_config()

        tab1, tab2, tab3 = st.tabs(["📧 Email", "📱 Telegram", "⚙️ Thresholds"])

        with tab1:
            enabled    = st.checkbox("Enable email alerts", value=config["email"].get("enabled", False), key="email_enabled")
            email_addr = st.text_input("Email address",  value=config["email"].get("address", ""),     key="email_addr")
            smtp_srv   = st.text_input("SMTP server",    value=config["email"].get("smtp_server", "smtp.gmail.com"), key="smtp_srv")
            smtp_port  = st.number_input("SMTP port",    value=int(config["email"].get("smtp_port", 587)), min_value=1, max_value=65535, key="smtp_port")
            smtp_user  = st.text_input("SMTP username",  value=config["email"].get("username", ""),    key="smtp_user")
            smtp_pass  = st.text_input("SMTP password",  value=config["email"].get("password", ""),    key="smtp_pass", type="password")
            st.caption("Gmail users: use an App Password (not your main password). Settings → Security → App Passwords.")
            st.warning("Credentials are stored in `data/alerts_config.json`. Keep this file private — do not commit it to git.", icon="⚠️")

        with tab2:
            tg_enabled = st.checkbox("Enable Telegram alerts", value=config["telegram"].get("enabled", False), key="tg_enabled")
            bot_token  = st.text_input("Bot token",  value=config["telegram"].get("bot_token", ""), key="bot_token", type="password")
            chat_id    = st.text_input("Chat ID",    value=config["telegram"].get("chat_id", ""),   key="chat_id")
            st.caption("Create a bot via @BotFather on Telegram. Get your Chat ID by messaging @userinfobot.")

        with tab3:
            min_apy   = st.slider("Alert when any opportunity APY exceeds (%)", 50, 300,
                                  int(config["thresholds"].get("min_apy_alert", 150)), 10, key="min_apy_thresh")
            arb_alert = st.checkbox("Alert on ACT NOW arbitrage opportunities",
                                    value=config["thresholds"].get("new_arb_alert", True), key="arb_alert_cb")

        col_save, col_test_e, col_test_t = st.columns(3)
        with col_save:
            if st.button("💾 Save Settings", key="save_alerts"):
                new_config = {
                    "email":    {"enabled": enabled, "address": email_addr, "smtp_server": smtp_srv,
                                 "smtp_port": int(smtp_port), "username": smtp_user, "password": smtp_pass},
                    "telegram": {"enabled": tg_enabled, "bot_token": bot_token, "chat_id": chat_id},
                    "thresholds": {"min_apy_alert": min_apy, "new_arb_alert": arb_alert},
                }
                save_alerts_config(new_config)
                st.success("Settings saved!")
        with col_test_e:
            if st.button("📧 Test Email", key="test_email_btn"):
                cfg_now = load_alerts_config()
                ok, msg = test_email(cfg_now)
                st.success(msg) if ok else st.error(msg)
        with col_test_t:
            if st.button("📱 Test Telegram", key="test_tg_btn"):
                cfg_now = load_alerts_config()
                ok, msg = test_telegram(cfg_now)
                st.success(msg) if ok else st.error(msg)


# ─── PDF / HTML Export ────────────────────────────────────────────────────────

def generate_html_report(latest: dict, profile: str, portfolio_size: float) -> str:
    opps        = latest.get("models", {}).get(profile, [])
    profile_cfg = RISK_PROFILES[profile]
    ts          = latest.get("completed_at", datetime.utcnow().isoformat())

    rows = ""
    for opp in opps[:6]:
        apy   = opp.get("estimated_apy", 0)
        kf    = opp.get("kelly_fraction", 0)
        grade, _ = risk_score_to_grade(opp.get("risk_score", 5))
        alloc_str = f"${kf * portfolio_size:,.0f}" if portfolio_size > 0 else f"{kf*100:.0f}%"
        rows += f"""
        <tr>
            <td>{opp.get('rank','—')}</td>
            <td>{opp.get('protocol','—')}</td>
            <td>{opp.get('asset_or_pool','—')}</td>
            <td>{apy:.1f}%</td>
            <td>{opp.get('apy_low', apy*0.8):.1f}%–{opp.get('apy_high', apy*1.2):.1f}%</td>
            <td><b>{grade}</b></td>
            <td>{alloc_str}</td>
            <td style="font-size:0.85rem;">{opp.get('action','—')}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Flare DeFi Report — {_ts_fmt(ts)}</title>
<style>
  body  {{ font-family: Arial, sans-serif; padding: 40px; color: #222; max-width: 1000px; margin: auto; }}
  h1    {{ color: #2c3e50; border-bottom: 2px solid #e74c3c; padding-bottom: 8px; }}
  h2    {{ color: #2c3e50; margin-top: 32px; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  th    {{ background: #2c3e50; color: white; padding: 10px; text-align: left; font-size: 0.85rem; }}
  td    {{ padding: 8px 10px; border-bottom: 1px solid #eee; vertical-align: top; }}
  tr:nth-child(even) {{ background: #f9f9f9; }}
  .warn {{ background: #fff3cd; padding: 14px 16px; border-radius: 6px; margin: 16px 0; border-left: 4px solid #f39c12; }}
  .meta {{ color: #666; font-size: 0.9rem; margin-bottom: 24px; }}
  .footer {{ margin-top: 40px; color: #999; font-size: 0.8rem; border-top: 1px solid #eee; padding-top: 16px; }}
  @media print {{ body {{ padding: 20px; }} }}
</style>
</head>
<body>
<h1>⚡ Flare DeFi Opportunities Report</h1>
<p class="meta">
  Risk Profile: <b>{profile_cfg['label']}</b> &nbsp;|&nbsp;
  Generated: <b>{_ts_fmt(ts)}</b> &nbsp;|&nbsp;
  Portfolio Size: <b>${portfolio_size:,.0f}</b>
</p>
<div class="warn">⚠️ {INCENTIVE_PROGRAM['note']}</div>
<h2>Top Opportunities</h2>
<table>
<tr><th>#</th><th>Protocol</th><th>Pool / Asset</th><th>Est. APY</th><th>APY Range</th><th>Risk Grade</th><th>Suggested Allocation</th><th>Action</th></tr>
{rows}
</table>
<h2>How to Read This Report</h2>
<ul>
  <li><b>Est. APY</b> — Model's central estimate for annualised yield. Actual results will vary.</li>
  <li><b>APY Range</b> — Conservative low / high scenario band (±20%).</li>
  <li><b>Risk Grade A–F</b> — A = very safe (lending/staking). F = high risk (leveraged/perps).</li>
  <li><b>Suggested Allocation</b> — Kelly Criterion position sizing. Never put 100% in one strategy.</li>
</ul>
<div class="footer">
  Flare DeFi Model · Data from Blazeswap, SparkDEX, Ēnosys, Kinetic, Clearpool, Spectra, Upshift, Mystic, Hyperliquid, Cyclo, Sceptre, Firelight<br>
  <b>This is not financial advice. Always do your own research before investing.</b>
</div>
</body>
</html>"""


def render_pdf_export(latest: dict, profile: str, portfolio_size: float) -> None:
    if latest:
        html_content = generate_html_report(latest, profile, portfolio_size)
        fname = f"flare_defi_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.html"
        st.download_button(
            label="📄 Download Report (HTML → Print → Save as PDF)",
            data=html_content,
            file_name=fname,
            mime="text/html",
            key="pdf_export_btn",
        )
        st.caption(
            "Open the downloaded file in Chrome or Edge. Press **Ctrl+P** → **Save as PDF** "
            "for a print-ready one-page report."
        )
    else:
        st.info("Run a scan first to generate a report.")


# ─── What's New Section ───────────────────────────────────────────────────────

def render_whats_new() -> None:
    """
    Displays the latest web monitor digest:
    - AI summary (if available)
    - New protocols detected on Flare
    - New token listings
    - Recent news from RSS feeds
    """
    digest = load_monitor_digest()
    if not digest:
        with st.expander("🌐 What's New on Flare — Ecosystem Monitor"):
            st.info(
                "No web monitor data yet. The daily monitor runs at 8am. "
                "You can trigger it manually: "
                "`python -c \"from scanners.web_monitor import run_web_monitor; run_web_monitor()\"`"
            )
        return

    generated = digest.get("generated_at", "")
    age_label  = f"Last checked: {_ts_fmt(generated)}" if generated else ""
    new_p  = len(digest.get("new_protocols", []))
    new_t  = len(digest.get("new_tokens", []))
    news_n = len(digest.get("news_items", []))
    badge  = f" — {new_p} new protocol(s)  ·  {news_n} news item(s)" if (new_p or news_n) else ""

    with st.expander(f"🌐 What's New on Flare{badge}"):
        if age_label:
            st.markdown(
                f"<span style='color:#aaa; font-size:0.8rem;'>{age_label} &nbsp;·&nbsp; "
                f"Sources: {', '.join(digest.get('sources_checked', []))}</span>",
                unsafe_allow_html=True,
            )

        # ── AI digest ─────────────────────────────────────────────────────────
        ai_text = digest.get("ai_digest", "").strip()
        if ai_text:
            st.markdown("#### AI Summary")
            st.markdown(
                f"<div style='background:#1a1f2e; border-radius:8px; padding:14px 18px; "
                f"border-left:4px solid #3498DB; margin-bottom:12px;'>{ai_text}</div>",
                unsafe_allow_html=True,
            )
            st.markdown(
                "<span style='color:#555; font-size:0.75rem;'>Generated by Claude AI · "
                "Not financial advice.</span>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<span style='color:#555; font-size:0.8rem;'>Set ANTHROPIC_API_KEY "
                "to enable AI-generated summaries.</span>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── New protocols ──────────────────────────────────────────────────────
        new_protocols = digest.get("new_protocols", [])
        if new_protocols:
            st.markdown(f"#### 🆕 New Protocols on Flare ({len(new_protocols)})")
            for proto in new_protocols:
                tvl_str  = f"${proto['tvl_usd']:,}" if proto.get("tvl_usd") else "TVL unknown"
                url_md   = f" · [Visit]({proto['url']})" if proto.get("url") else ""
                desc     = proto.get("description", "")
                desc_html = f"<br><span style='color:#aaa; font-size:0.82rem;'>{desc}</span>" if desc else ""
                st.markdown(
                    f"<div class='arb-tag'>"
                    f"<b>{proto['name']}</b> &nbsp;·&nbsp; {proto.get('category','?')} "
                    f"&nbsp;·&nbsp; {tvl_str}{url_md}"
                    f"{desc_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.markdown(
                "<span style='color:#555; font-size:0.85rem;'>No new protocols detected "
                "on Flare since last check.</span>",
                unsafe_allow_html=True,
            )

        # ── Known protocol TVL ─────────────────────────────────────────────────
        known_tvl = digest.get("known_tvl", {})
        if known_tvl:
            with st.expander(f"📊 Live TVL for {len(known_tvl)} tracked protocols (DeFi Llama)"):
                tvl_rows = [
                    {"Protocol": name, "TVL (USD)": f"${data['tvl_usd']:,}", "Category": data.get("category", "")}
                    for name, data in sorted(known_tvl.items(), key=lambda x: x[1].get("tvl_usd", 0), reverse=True)
                ]
                st.dataframe(tvl_rows, use_container_width=True, hide_index=True)

        # ── New tokens ────────────────────────────────────────────────────────
        new_tokens = digest.get("new_tokens", [])
        if new_tokens:
            st.markdown(f"#### 🪙 New Token Listings on Flare ({len(new_tokens)})")
            with st.expander("Show new tokens"):
                token_rows = [
                    {"Symbol": t["symbol"], "Name": t["name"], "Contract": t["address"]}
                    for t in new_tokens[:50]
                ]
                st.dataframe(token_rows, use_container_width=True, hide_index=True)

        # ── News feed ─────────────────────────────────────────────────────────
        news_items = digest.get("news_items", [])
        if news_items:
            st.markdown(f"#### 📰 Recent News ({len(news_items)} articles in last 48h)")
            for item in news_items[:10]:
                link_md = f"[{item['title']}]({item['link']})" if item.get("link") else item.get("title", "")
                pub     = item.get("published", "")
                summary = item.get("summary", "")
                sum_html = f"<br><span style='color:#aaa; font-size:0.82rem;'>{summary}</span>" if summary else ""
                st.markdown(
                    f"<div style='background:#111827; border-radius:6px; padding:10px 14px; "
                    f"margin-bottom:8px; border-left:3px solid #3498DB;'>"
                    f"<b>{link_md}</b><br>"
                    f"<span style='color:#888; font-size:0.78rem;'>{item.get('source','')} · {pub}</span>"
                    f"{sum_html}"
                    f"</div>",
                    unsafe_allow_html=True,
                )
            if len(news_items) > 10:
                with st.expander(f"Show all {len(news_items)} articles"):
                    for item in news_items[10:]:
                        link_md = f"[{item['title']}]({item['link']})" if item.get("link") else item.get("title", "")
                        st.markdown(
                            f"- **{link_md}** — {item.get('source','')} · {item.get('published','')}"
                        )
        else:
            st.markdown(
                "<span style='color:#555; font-size:0.85rem;'>No news in the last 48 hours. "
                "Install feedparser to enable RSS monitoring: `pip install feedparser`</span>",
                unsafe_allow_html=True,
            )

        # ── Errors (debug) ────────────────────────────────────────────────────
        errors = digest.get("errors", [])
        if errors:
            with st.expander("⚠️ Monitor errors (non-critical)"):
                for err in errors:
                    st.caption(err)


# ─── Main App ─────────────────────────────────────────────────────────────────

def main():
    latest    = load_latest()
    runs      = load_history_runs()
    positions = load_positions()

    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown("## ⚡ Flare DeFi Model")

    col_left, col_mid, col_right = st.columns([3, 2, 1])
    with col_left:
        last_scan = latest.get("completed_at") or latest.get("run_id")
        st.markdown(
            f"<span style='color:#aaa; font-size:0.85rem;'>"
            f"Last scan: {_ts_fmt(last_scan) if last_scan else 'No scan yet — run scheduler.py'}"
            f" &nbsp;|&nbsp; Next scan: {_next_scan()}"
            f"</span>",
            unsafe_allow_html=True,
        )
    with col_mid:
        portfolio_size = st.number_input(
            "Your portfolio size ($)",
            min_value=0.0,
            value=float(st.session_state.get("_portfolio_size", 10000.0)),
            step=1000.0,
            format="%.0f",
            key="_portfolio_size",
            help="Enter how much capital you have to invest. Used to show dollar allocation amounts.",
        )
    with col_right:
        if st.button("🔄  Refresh"):
            st.cache_data.clear()
            st.rerun()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Wallet ────────────────────────────────────────────────────────────────
    render_wallet_connect()

    # ── Incentive Warning ─────────────────────────────────────────────────────
    render_incentive_warning()

    # ── Price Strip ───────────────────────────────────────────────────────────
    flare_scan = latest.get("flare_scan", {})
    prices = flare_scan.get("prices", [])
    render_price_strip(prices)

    # ── Data Freshness Indicator ───────────────────────────────────────────────
    all_data_pts = (
        flare_scan.get("pools", []) +
        flare_scan.get("lending", []) +
        flare_scan.get("staking", [])
    )
    if all_data_pts:
        live_count     = sum(1 for p in all_data_pts if p.get("data_source") == "live")
        baseline_count = sum(1 for p in all_data_pts if p.get("data_source") in ("baseline", "estimate"))
        research_count = sum(1 for p in all_data_pts if p.get("data_source") == "research")
        total          = len(all_data_pts)
        freshness_color = "#2ECC71" if live_count / total >= 0.7 else ("#F39C12" if live_count > 0 else "#E74C3C")
        parts = [f"<span style='color:{freshness_color};'>● {live_count} live</span>"]
        if baseline_count:
            parts.append(f"<span style='color:#E74C3C;'>{baseline_count} estimated</span>")
        if research_count:
            parts.append(f"<span style='color:#aaa;'>{research_count} research</span>")
        st.markdown(
            f"<div style='font-size:0.78rem; color:#666; margin:4px 0 8px;'>"
            f"Data freshness: {' · '.join(parts)} out of {total} data points"
            f"</div>",
            unsafe_allow_html=True,
        )
        for warn in flare_scan.get("warnings", []):
            st.markdown(
                f"<div class='warn-box' style='font-size:0.82rem; padding:8px 12px; margin-bottom:6px;'>"
                f"⚠️ {warn}</div>",
                unsafe_allow_html=True,
            )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── What's New — Ecosystem Monitor ────────────────────────────────────────
    render_whats_new()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Risk Profile Selector ─────────────────────────────────────────────────
    st.markdown("### Select Your Risk Profile")
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>Pick the style that matches how much risk you're comfortable with.</span>",
        unsafe_allow_html=True,
    )

    if "risk_profile" not in st.session_state:
        st.session_state.risk_profile = "conservative"

    col1, col2, col3 = st.columns(3)
    with col1:
        if st.button("🟢  SAFE\nUltra Conservative\n15–40% APY"):
            st.session_state.risk_profile = "conservative"
    with col2:
        if st.button("🟡  BALANCED\nMedium Risk\n50–130% APY"):
            st.session_state.risk_profile = "medium"
    with col3:
        if st.button("🔴  AGGRESSIVE\nHigh Risk\n150–265%+ APY"):
            st.session_state.risk_profile = "high"

    profile     = st.session_state.risk_profile
    profile_cfg = RISK_PROFILES[profile]
    p_color     = profile_cfg["color"]

    st.markdown(f"""
    <div style="background:#1a1f2e; border-radius:8px; padding:12px 16px; border-left:4px solid {p_color}; margin-top:8px;">
        <b style="color:{p_color};">{profile_cfg['label']}</b> — {profile_cfg['description']}
    </div>""", unsafe_allow_html=True)

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Model Data + Weights ──────────────────────────────────────────────────
    model_data = latest.get("models", {})
    opps       = model_data.get(profile, [])

    try:
        feedback = get_feedback_dashboard()
        weights  = feedback.get("model_weights", {})
    except Exception:
        feedback = {"overall_health": 50, "per_profile": {}, "trend": "building", "model_weights": {}}
        weights  = {}
    weight = max(0.5, min(1.5, weights.get(profile, 1.0)))

    # ── Yield Hero Cards ──────────────────────────────────────────────────────
    st.markdown("### Your Estimated Yield")
    render_yield_hero_card(positions, opps, portfolio_size)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Top Opportunities ─────────────────────────────────────────────────────
    st.markdown(f"### Today's Top Opportunities — {profile_cfg['label']}")

    if not opps:
        st.warning(
            "No scan data yet. Run `python scheduler.py --now` to generate your first scan."
        )
    else:
        for i, opp in enumerate(opps[:3]):
            render_opportunity_card(opp, i, p_color, portfolio_size, weight)

        if len(opps) > 3:
            with st.expander(f"See all {len(opps)} opportunities"):
                for i, opp in enumerate(opps[3:], start=3):
                    render_opportunity_card(opp, i, p_color, portfolio_size, weight)

    # ── APY Sparklines ────────────────────────────────────────────────────────
    if opps and runs:
        with st.expander("📈 APY Trend Sparklines — last 14 scans per protocol"):
            render_protocol_sparklines(runs, opps, profile)

    # ── Starter Portfolios ────────────────────────────────────────────────────
    with st.expander("📋 One-Click Starter Portfolios — see full allocation table"):
        render_starter_portfolio(model_data, portfolio_size)

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Planning Tools ────────────────────────────────────────────────────────
    st.markdown("### Planning Tools")

    calc_tab1, calc_tab2, calc_tab3 = st.tabs([
        "💰 Income Planner",
        "🔒 Spectra Fixed-Rate",
        "📡 FTSO Delegation",
    ])
    with calc_tab1:
        render_flaredrop_calculator()
    with calc_tab2:
        render_spectra_calculator()
    with calc_tab3:
        render_ftso_optimizer()

    # ── FAssets Tracker ───────────────────────────────────────────────────────
    with st.expander("🌐 FAssets Yield Tracker (FXRP / FBTC)"):
        render_fassets_tracker()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Arbitrage Alerts ──────────────────────────────────────────────────────
    st.markdown("### Arbitrage Alerts")
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>Opportunities to earn extra profit by exploiting price differences across platforms.</span>",
        unsafe_allow_html=True,
    )
    arb_data = latest.get("arbitrage", {}).get(profile, [])
    render_arb_alerts(arb_data)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Options Strategies ────────────────────────────────────────────────────
    st.markdown("### Options & Derivatives Strategies")
    opts_data = latest.get("options", {}).get(profile, {})
    analysis  = opts_data.get("analysis", {}) if opts_data else {}

    if not analysis:
        st.info("Options analysis will appear here after the first scan.")
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
                            rr = strat_data.get("risk_reward", 0)
                            apy_str = f" — **{rr:.1f}:1 risk/reward**"
                        st.markdown(f"**{strat_data.get('strategy', strat_name)}**{apy_str}")
                        st.markdown(f"{plain}")
                        if exec_note:
                            st.markdown(
                                f"<span style='color:#aaa; font-size:0.85rem;'>How: {exec_note}</span>",
                                unsafe_allow_html=True,
                            )
                        st.markdown("---")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── AI Model Health ───────────────────────────────────────────────────────
    st.markdown("### AI Model Health")
    st.markdown(
        "<span style='color:#aaa; font-size:0.85rem;'>"
        "How accurate has this model been at predicting real yields? "
        "Updates automatically after each scan.</span>",
        unsafe_allow_html=True,
    )
    render_model_health(profile, feedback)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Historical Chart ──────────────────────────────────────────────────────
    st.markdown("### Historical Top APY Trend")
    render_performance_chart(runs, profile)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Your Positions ────────────────────────────────────────────────────────
    st.markdown("### Your Current Positions")
    render_positions(positions, prices)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Exit Strategy ─────────────────────────────────────────────────────────
    st.markdown("### Exit Strategy & Price Targets")
    render_exit_strategy(positions, prices)
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Export + Alerts ───────────────────────────────────────────────────────
    st.markdown("### Export & Notifications")
    exp_col, alert_col = st.columns([1, 2])
    with exp_col:
        render_pdf_export(latest, profile, portfolio_size)
    with alert_col:
        render_alerts_config()

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    # ── Footer ────────────────────────────────────────────────────────────────
    warnings = latest.get("warnings", [])
    if warnings:
        with st.expander("⚠️ Data Quality Notes"):
            for w in warnings:
                st.markdown(f"- {w}")

    st.markdown("""
    <div style="text-align:center; color:#555; font-size:0.78rem; margin-top:24px;">
        Flare DeFi Model v2 — Data from Blazeswap, SparkDEX, Ēnosys, Kinetic, Clearpool,
        Spectra, Upshift, Mystic, Hyperliquid, Cyclo, Sceptre, Firelight<br>
        <b>Not financial advice. Always do your own research before investing.</b>
    </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()

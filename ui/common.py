"""
ui/common.py — Shared CSS, data loaders, helpers, and render components.
Imported by every page in the Flare DeFi Model multi-page app.
"""

import sys
import json
import html as _html
import time
import subprocess
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import (
    RISK_PROFILES, RISK_PROFILE_NAMES, INCENTIVE_PROGRAM, HISTORY_FILE,
    POSITIONS_FILE, WALLETS_FILE, PROTOCOLS, TOKENS, FLARE_RPC_URLS,
    MONITOR_DIGEST_FILE, SCHEDULER,
)
from utils.file_io import atomic_json_write


# ─── Live Price Loader (bypasses stale scan data) ─────────────────────────────

@st.cache_data(ttl=120)
def load_live_prices() -> list:
    """
    Fetch current token prices directly from CoinGecko.
    Cached for 2 minutes so the price strip stays fresh without hammering the API.
    Falls back to scan-cached prices if CoinGecko is unavailable.
    """
    try:
        from scanners.flare_scanner import fetch_prices
        results = fetch_prices()
        if results:
            return [
                {"symbol": p.symbol, "price_usd": p.price_usd,
                 "change_24h": p.change_24h, "data_source": p.data_source}
                for p in results
            ]
    except Exception:
        pass
    return []


# ─── Page Bootstrap ───────────────────────────────────────────────────────────

def page_setup(title: str = "Flare DeFi Model") -> None:
    """Must be the first call in every page."""
    st.set_page_config(
        page_title=title,
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _inject_css()


def _inject_css() -> None:
    st.markdown("""
<style>
    /* ── Base ─────────────────────────────────────────────────────────── */
    .main { background-color: #060b16; }
    .block-container {
        padding-top: 1.8rem; padding-bottom: 3rem;
        max-width: 1200px;
    }
    h1 {
        font-size: 1.75rem !important; font-weight: 800 !important;
        letter-spacing: -0.5px; color: #f8fafc !important;
    }
    h2 {
        font-size: 1.2rem !important; font-weight: 600 !important;
        color: #cbd5e1 !important; letter-spacing: -0.2px;
    }
    h3 {
        font-size: 1.0rem !important; font-weight: 600 !important;
        color: #94a3b8 !important; text-transform: uppercase;
        letter-spacing: 0.8px;
    }

    /* ── Glassmorphism Metric Cards ───────────────────────────────────── */
    .metric-card {
        background: linear-gradient(145deg,
            rgba(17,24,39,0.85) 0%,
            rgba(10,15,28,0.90) 100%);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border-radius: 16px;
        padding: 22px 26px;
        margin-bottom: 14px;
        border: 1px solid rgba(255,255,255,0.07);
        border-left: 3px solid #1e3a5f;
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .metric-card:hover {
        border-color: rgba(255,255,255,0.12);
        box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    }
    .card-green  { border-left-color: #10b981; }
    .card-blue   { border-left-color: #3b82f6; }
    .card-orange { border-left-color: #f59e0b; }
    .card-red    { border-left-color: #ef4444; }

    .big-number {
        font-size: 2.1rem; font-weight: 800;
        letter-spacing: -0.8px; line-height: 1.1;
        color: #f1f5f9;
    }
    .label {
        font-size: 0.68rem; color: #475569;
        text-transform: uppercase; letter-spacing: 1.5px;
        margin-bottom: 8px;
    }

    /* ── Opportunity Cards ────────────────────────────────────────────── */
    .opp-card {
        background: linear-gradient(145deg,
            rgba(15,20,35,0.92) 0%,
            rgba(10,14,26,0.95) 100%);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border-radius: 16px;
        padding: 20px 24px;
        margin-bottom: 12px;
        border: 1px solid rgba(255,255,255,0.06);
        transition: border-color 0.2s, transform 0.15s, box-shadow 0.2s;
    }
    .opp-card:hover {
        border-color: rgba(255,255,255,0.11);
        transform: translateY(-1px);
        box-shadow: 0 6px 28px rgba(0,0,0,0.45);
    }

    /* ── Arbitrage Tag ────────────────────────────────────────────────── */
    .arb-tag {
        background: linear-gradient(145deg,
            rgba(16,185,129,0.05) 0%,
            rgba(5,150,105,0.03) 100%);
        border-radius: 14px;
        padding: 16px 20px;
        margin-bottom: 10px;
        border: 1px solid rgba(16,185,129,0.15);
        transition: border-color 0.2s;
    }
    .arb-tag:hover { border-color: rgba(16,185,129,0.28); }

    /* ── Warning Box ──────────────────────────────────────────────────── */
    .warn-box {
        background: linear-gradient(145deg,
            rgba(245,158,11,0.06) 0%,
            rgba(217,119,6,0.04) 100%);
        border-radius: 14px;
        padding: 14px 18px;
        border: 1px solid rgba(245,158,11,0.18);
        margin-bottom: 14px;
    }

    /* ── Grade Badge ──────────────────────────────────────────────────── */
    .grade-badge {
        font-weight: 800; font-size: 0.75rem;
        padding: 3px 10px; border-radius: 7px; color: #000;
        letter-spacing: 0.5px;
    }

    /* ── Live / Estimated Badges ──────────────────────────────────────── */
    .badge-live {
        font-size: 0.62rem; font-weight: 700; color: #10b981;
        background: rgba(16,185,129,0.10);
        border: 1px solid rgba(16,185,129,0.22);
        border-radius: 5px; padding: 1px 6px;
        letter-spacing: 0.6px; text-transform: uppercase;
        vertical-align: middle; margin-left: 4px;
    }
    .badge-est {
        font-size: 0.62rem; font-weight: 700; color: #f59e0b;
        background: rgba(245,158,11,0.10);
        border: 1px solid rgba(245,158,11,0.22);
        border-radius: 5px; padding: 1px 6px;
        letter-spacing: 0.6px; text-transform: uppercase;
        vertical-align: middle; margin-left: 4px;
    }

    /* ── Dividers ─────────────────────────────────────────────────────── */
    .divider {
        border: none;
        border-top: 1px solid rgba(255,255,255,0.05);
        margin: 24px 0;
    }

    /* ── Sidebar ──────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0a0f1e 0%, #060b16 100%);
        border-right: 1px solid rgba(255,255,255,0.05);
    }
    [data-testid="stSidebar"] .block-container { padding-top: 1.2rem; }

    /* ── Buttons ──────────────────────────────────────────────────────── */
    div[data-testid="stButton"] > button {
        border-radius: 10px;
        border: 1px solid rgba(255,255,255,0.10);
        font-weight: 700;
        font-size: 0.82rem;
        letter-spacing: 0.3px;
        transition: background 0.15s, border-color 0.15s, box-shadow 0.15s;
    }
    div[data-testid="stButton"] > button:hover {
        border-color: rgba(245,158,11,0.35);
        box-shadow: 0 0 12px rgba(245,158,11,0.12);
    }

    /* ── Dataframes ───────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }

    /* ── Price Chip ───────────────────────────────────────────────────── */
    .price-chip {
        text-align: center;
        padding: 14px 12px;
        background: linear-gradient(145deg,
            rgba(17,24,39,0.90) 0%,
            rgba(10,15,28,0.92) 100%);
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        border-radius: 14px;
        border: 1px solid rgba(255,255,255,0.07);
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .price-chip:hover {
        border-color: rgba(255,255,255,0.13);
        box-shadow: 0 4px 16px rgba(0,0,0,0.35);
    }

    /* ── Section Label ────────────────────────────────────────────────── */
    .section-label {
        font-size: 0.65rem; color: #334155;
        text-transform: uppercase; letter-spacing: 1.6px;
        margin-bottom: 10px; margin-top: 6px;
    }

    /* ── Tabs ─────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tab"] {
        font-size: 0.82rem; font-weight: 600;
        color: #475569;
    }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
        color: #f59e0b;
    }

    /* ── Inputs ───────────────────────────────────────────────────────── */
    [data-testid="stNumberInput"] input,
    [data-testid="stTextInput"] input {
        background: rgba(17,24,39,0.8) !important;
        border: 1px solid rgba(255,255,255,0.09) !important;
        border-radius: 9px !important;
    }

    /* ── Expanders ────────────────────────────────────────────────────── */
    [data-testid="stExpander"] {
        border: 1px solid rgba(255,255,255,0.06) !important;
        border-radius: 12px !important;
        background: rgba(10,14,26,0.6) !important;
    }

    /* ── Mobile ───────────────────────────────────────────────────────── */
    @media (max-width: 768px) {
        .big-number { font-size: 1.55rem !important; }
        h1 { font-size: 1.4rem !important; }
        .metric-card, .opp-card { padding: 14px 16px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
        .price-chip { padding: 10px 8px; }
    }
</style>
""", unsafe_allow_html=True)


# ─── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar() -> dict:
    """
    Persistent sidebar: scan status, portfolio size, risk profile, refresh.
    Returns dict with keys: profile, profile_cfg, color, weight, feedback, portfolio_size.
    """
    # ─── 5-minute auto-refresh to keep data live ─────────────────────────────
    _now = time.time()
    if "last_auto_refresh" not in st.session_state:
        st.session_state.last_auto_refresh = _now
    elif _now - st.session_state.last_auto_refresh > 300:
        st.session_state.last_auto_refresh = _now
        st.cache_data.clear()
        st.rerun()

    with st.sidebar:
        st.markdown(
            "<div style='font-size:1.2rem; font-weight:700; color:#f8fafc; "
            "letter-spacing:-0.3px; margin-bottom:4px;'>⚡ Flare DeFi</div>",
            unsafe_allow_html=True,
        )

        latest    = load_latest()
        last_scan = latest.get("completed_at") or latest.get("run_id")
        st.markdown(
            f"<div style='font-size:0.75rem; color:#475569; line-height:1.6;'>"
            f"Last scan: <span style='color:#64748b'>{_ts_fmt(last_scan) if last_scan else 'None yet'}</span><br>"
            f"Next: <span style='color:#64748b'>{_next_scan()}</span></div>",
            unsafe_allow_html=True,
        )

        col_r, col_s = st.columns(2)
        with col_r:
            if st.button("↺ Reload", key="sidebar_refresh", use_container_width=True,
                         help="Reload the latest saved scan data from disk"):
                st.cache_data.clear()
                st.rerun()
        with col_s:
            if st.button("▶ Scan", key="sidebar_scan_now", use_container_width=True,
                         help="Run a fresh scan now (~30 seconds). Auto-reloads when done."):
                try:
                    scheduler_path = str(Path(__file__).parent.parent / "scheduler.py")
                    subprocess.Popen(
                        [sys.executable, scheduler_path, "--now"],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                    )
                    st.session_state._scanning = True
                    st.session_state._scan_deadline = time.time() + 120
                    st.session_state._scan_baseline = (
                        latest.get("completed_at") or latest.get("run_id") or ""
                    )
                except Exception as _e:
                    st.error(f"Could not start scan: {_e}")

        # ─── Scan completion polling ───────────────────────────────────────────
        if st.session_state.get("_scanning"):
            try:
                with open(HISTORY_FILE) as _f:
                    _hist_ts = json.load(_f).get("latest", {}).get("completed_at") or ""
            except Exception:
                _hist_ts = ""
            if _hist_ts and _hist_ts != st.session_state.get("_scan_baseline", ""):
                st.session_state._scanning = False
                st.cache_data.clear()
                st.rerun()
            elif time.time() < st.session_state.get("_scan_deadline", 0):
                st.caption("⏳ Scanning… auto-reloading when done.")
                time.sleep(4)
                st.rerun()
            else:
                st.session_state._scanning = False
                st.caption("Scan timed out — click ↺ Reload.")

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # Portfolio size
        st.markdown("<div class='section-label'>Portfolio Size</div>", unsafe_allow_html=True)
        portfolio_size = st.number_input(
            "Portfolio Size",
            min_value=0.0,
            value=float(st.session_state.get("_portfolio_size", 10000.0)),
            step=1000.0,
            format="%.0f",
            key="_portfolio_size",
            label_visibility="collapsed",
        )

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # Risk profile
        st.markdown("<div class='section-label'>Risk Profile</div>", unsafe_allow_html=True)
        profile = st.radio(
            "Risk Profile",
            options=list(RISK_PROFILE_NAMES),
            format_func=lambda p: {
                "conservative": "🟢  Conservative",
                "medium":       "🟡  Balanced",
                "high":         "🔴  Aggressive",
            }[p],
            key="risk_profile",
            label_visibility="collapsed",
        )
        profile_cfg = RISK_PROFILES[profile]
        color       = profile_cfg["color"]
        st.markdown(
            f"<div style='font-size:0.78rem; color:#475569; margin-top:4px;'>"
            f"{profile_cfg['target_apy_low']:.0f}–{profile_cfg['target_apy_high']:.0f}% target APY</div>",
            unsafe_allow_html=True,
        )

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.68rem; color:#334155; line-height:1.5;'>"
            "Not financial advice.<br>Always do your own research.</div>",
            unsafe_allow_html=True,
        )

    # Model weights (outside sidebar context)
    try:
        from ai.feedback_loop import get_feedback_dashboard
        feedback = get_feedback_dashboard()
        weights  = feedback.get("model_weights", {})
    except Exception:
        feedback = {"overall_health": 50, "per_profile": {}, "trend": "building", "model_weights": {}}
        weights  = {}

    weight = max(0.5, min(1.5, weights.get(profile, 1.0)))

    return {
        "profile":        profile,
        "profile_cfg":    profile_cfg,
        "color":          color,
        "weight":         weight,
        "feedback":       feedback,
        "portfolio_size": portfolio_size,
    }


# ─── Data Loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load_history_file() -> dict:
    """Single cached read of history.json — shared by load_latest() and load_history_runs()."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ history.json is corrupted — re-run the scheduler.")
        return {}
    except Exception as e:
        st.warning(f"⚠️ Could not load scan data: {e}")
        return {}


def load_latest() -> dict:
    return _load_history_file().get("latest", {})


def load_history_runs() -> list:
    return _load_history_file().get("runs", [])


@st.cache_data(ttl=60)
def load_positions() -> list:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ positions.json is corrupted.")
        return []
    except Exception:
        return []


def save_positions(positions: list) -> None:
    if not atomic_json_write(POSITIONS_FILE, positions):
        st.error("Could not save positions — check logs.")
    st.cache_data.clear()


@st.cache_data(ttl=300)
def load_wallets() -> list:
    if not WALLETS_FILE.exists():
        return []
    try:
        with open(WALLETS_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def save_wallets(wallets: list) -> None:
    if not atomic_json_write(WALLETS_FILE, wallets):
        st.error("Could not save wallets — check logs.")
    st.cache_data.clear()


@st.cache_data(ttl=300)
def load_monitor_digest() -> dict:
    if not MONITOR_DIGEST_FILE.exists():
        return {}
    try:
        with open(MONITOR_DIGEST_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Utility Helpers ──────────────────────────────────────────────────────────

_URGENCY_COLOR = {"act_now": "#ef4444", "act_soon": "#f59e0b", "monitor": "#3b82f6"}
_URGENCY_LABEL = {"act_now": "ACT NOW", "act_soon": "ACT SOON", "monitor": "MONITOR"}


def render_urgency_badge(urgency: str) -> str:
    """Return an HTML badge string for arbitrage urgency levels."""
    color = _URGENCY_COLOR.get(urgency, "#3b82f6")
    label = _URGENCY_LABEL.get(urgency, urgency.upper())
    return (
        f"<span style=\"color:{color}; font-weight:700; font-size:0.78rem; "
        f"background:rgba(255,255,255,0.04); padding:3px 10px; border-radius:6px;\">"
        f"{label}</span>"
    )


def _ts_fmt(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%b %d  %I:%M %p UTC")
    except Exception:
        return iso or "—"


def _next_scan() -> str:
    tz        = ZoneInfo(SCHEDULER["timezone"])
    now_local = datetime.now(tz)
    today     = now_local.date()
    scan_times = []
    for t in SCHEDULER["run_times"]:
        h, m = map(int, t.split(":"))
        scan_times.append(datetime(today.year, today.month, today.day, h, m, tzinfo=tz))
    future = [t for t in scan_times if t > now_local]
    if not future:
        tmrw   = today + timedelta(days=1)
        h0, m0 = map(int, SCHEDULER["run_times"][0].split(":"))
        next_t = datetime(tmrw.year, tmrw.month, tmrw.day, h0, m0, tzinfo=tz)
    else:
        next_t = min(future)
    next_utc = next_t.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    delta       = next_utc - datetime.utcnow()
    total_mins  = max(0, int(delta.total_seconds())) // 60
    h, m        = divmod(total_mins, 60)
    return f"{h}h {m}m"


def risk_score_to_grade(score: float) -> tuple:
    if score <= 2.0:   return "A",  "#10b981"
    elif score <= 3.5: return "A-", "#34d399"
    elif score <= 5.0: return "B",  "#f59e0b"
    elif score <= 6.5: return "C",  "#f97316"
    elif score <= 8.0: return "D",  "#ef4444"
    else:              return "F",  "#dc2626"


def compute_position_pnl(pos: dict, current_prices: list) -> dict:
    from models.risk_models import calculate_il

    deposit_usd    = float(pos.get("deposit_usd") or pos.get("entry_value") or 0)
    current_value  = float(pos.get("current_value") or 0)
    entry_apy      = float(pos.get("entry_apy") or 0)
    unclaimed_fees = float(pos.get("unclaimed_fees", 0))

    days_active    = 0
    entry_date_str = pos.get("entry_date", "")
    if entry_date_str:
        try:
            days_active = max(0, (datetime.utcnow() - datetime.fromisoformat(entry_date_str)).days)
        except Exception:
            pass

    fees_earned_est = (
        deposit_usd * (entry_apy / 100) * (days_active / 365)
        if days_active > 0 and entry_apy > 0 else 0.0
    )

    value_change     = current_value - deposit_usd
    value_change_pct = (value_change / deposit_usd * 100) if deposit_usd > 0 else 0.0

    il_pct = 0.0
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


# ─── Shared Render Components ─────────────────────────────────────────────────

def render_price_strip(prices: list) -> None:
    if not prices:
        return
    cols = st.columns(len(prices))
    for i, p in enumerate(prices):
        sym   = _html.escape(str(p.get("symbol", "?")))
        price = p.get("price_usd", 0)
        chg   = p.get("change_24h", 0)
        color = "#10b981" if chg >= 0 else "#ef4444"
        arrow = "▲" if chg >= 0 else "▼"
        src   = "*" if p.get("data_source") in ("estimate",) else ""
        with cols[i]:
            st.markdown(f"""
            <div class="price-chip">
                <div style="font-size:0.7rem; color:#64748b; margin-bottom:4px;">{sym}{src}</div>
                <div style="font-size:1.1rem; font-weight:700; letter-spacing:-0.3px;">${price:,.4f}</div>
                <div style="font-size:0.75rem; color:{color}; margin-top:2px;">{arrow} {abs(chg):.2f}%</div>
            </div>""", unsafe_allow_html=True)


def render_incentive_warning() -> None:
    from config import INCENTIVE_PROGRAM
    st.markdown(f"""
    <div class="warn-box">
        <span style="font-weight:600; color:#f59e0b; font-size:0.9rem;">⚠️ Incentive Notice</span>
        <div style="color:#94a3b8; font-size:0.85rem; margin-top:6px;">{INCENTIVE_PROGRAM['note']}</div>
    </div>""", unsafe_allow_html=True)


def render_yield_hero_cards(positions: list, opps: list, portfolio_size: float) -> None:
    total_value = sum(p.get("current_value", 0) for p in positions) or portfolio_size
    avg_apy     = (sum(o.get("estimated_apy", 0) for o in opps[:3]) / min(3, len(opps))) if opps else 0.0

    weekly_yield  = total_value * (avg_apy / 100) / 52
    monthly_yield = total_value * (avg_apy / 100) / 12
    annual_yield  = total_value * (avg_apy / 100)

    c1, c2, c3 = st.columns(3)
    for col, label, value, sub, cls in [
        (c1, "Est. This Week",   f"${weekly_yield:,.2f}",  f"{avg_apy/52:.3f}% weekly",    "card-green"),
        (c2, "Est. This Month",  f"${monthly_yield:,.2f}", f"on ${total_value:,.0f}",       "card-blue"),
        (c3, "Est. This Year",   f"${annual_yield:,.2f}",  f"{avg_apy:.1f}% APY (top-3)",  "card-orange"),
    ]:
        with col:
            st.markdown(f"""
            <div class="metric-card {cls}">
                <div class="label">{label}</div>
                <div class="big-number">{value}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">{sub}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown(
        "<div style='color:#334155; font-size:0.75rem;'>"
        "Estimated using top-3 opportunities. Actual results vary. Not financial advice.</div>",
        unsafe_allow_html=True,
    )


def render_opportunity_card(
    opp: dict, idx: int, color: str,
    portfolio_size: float = 0, weight: float = 1.0,
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
    il_color = {"none": "#10b981", "low": "#10b981", "medium": "#f59e0b", "high": "#ef4444"}.get(il, "#f59e0b")
    est_tag  = " <span class='badge-est'>EST</span>" if src in ("baseline", "estimate") else " <span class='badge-live'>LIVE</span>"
    medal    = ["🥇", "🥈", "🥉", "4", "5", "6"][min(idx, 5)]
    proto    = _html.escape(str(proto))
    pool     = _html.escape(str(pool))
    action   = _html.escape(str(action))

    alloc_str = (
        f"${kf * portfolio_size:,.0f} <span style='color:#475569'>({kf*100:.0f}%)</span>"
        if portfolio_size > 0
        else f"{kf*100:.0f}% of portfolio"
    )

    st.markdown(f"""
    <div class="opp-card" style="border-left: 3px solid {color};">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
            <div>
                <span style="font-size:0.8rem; color:#475569; margin-right:6px;">{medal}</span>
                <span style="font-size:1.05rem; font-weight:700; color:#f1f5f9;">{proto}</span>
                <span style="color:#475569; margin:0 6px;">·</span>
                <span style="font-size:0.95rem; color:#94a3b8;">{pool}</span>
            </div>
            <div style="display:flex; align-items:center; gap:10px;">
                <span class="grade-badge" style="background:{grade_color};">{grade}</span>
                <span style="font-size:1.7rem; font-weight:800; color:{color}; letter-spacing:-1px;">{apy:.1f}%{est_tag}</span>
            </div>
        </div>
        <div style="color:#475569; font-size:0.8rem; margin-top:8px;">
            Range: <span style="color:#64748b">{lo:.1f}% – {hi:.1f}%</span>
        </div>
        <div style="color:#94a3b8; font-size:0.92rem; margin-top:10px; line-height:1.5;">{action}</div>
        <div style="display:flex; gap:24px; font-size:0.8rem; color:#475569; margin-top:12px; flex-wrap:wrap;">
            <span>Price risk: <span style="color:{il_color}; font-weight:600;">{il.upper()}</span></span>
            <span>Confidence: <span style="color:#64748b; font-weight:600;">{conf:.0f}%</span></span>
            <span>Suggested: <span style="color:#94a3b8; font-weight:600;">{alloc_str}</span></span>
        </div>
    </div>
    """, unsafe_allow_html=True)

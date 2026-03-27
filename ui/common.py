"""
ui/common.py — Shared CSS, data loaders, helpers, and render components.
Imported by every page in the Flare DeFi Model multi-page app.
"""

import sys
import json
import html as _html
import logging
import os
import time
import subprocess
import streamlit as st
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Security Audit Logger (#15) ────────────────────────────────────────────
# Dedicated logger for security-relevant events; does NOT propagate to root.
_audit_handler = logging.FileHandler(
    os.path.join(Path(__file__).parent.parent, "defi_audit.log"),
    encoding="utf-8",
)
_audit_handler.setFormatter(logging.Formatter(
    "%(asctime)s [AUDIT] %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
))
_audit_log = logging.getLogger("defi.audit")
_audit_log.addHandler(_audit_handler)
_audit_log.setLevel(logging.INFO)
_audit_log.propagate = False


def audit(event: str, **ctx) -> None:
    """Log a security-relevant user action to the audit trail."""
    extra = " ".join(f"{k}={v!r}" for k, v in ctx.items())
    _audit_log.info("%s %s", event, extra)

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
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug(f"load_live_prices: {e}")
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


@st.cache_data(ttl=86400)
def _build_css(theme: str) -> str:
    """Return the full CSS string for the given theme. Cached for 24 hours (upgrade #32)."""
    if theme == "light":
        return _CSS_LIGHT
    return _CSS_DARK


def _inject_css() -> None:
    # ── Detect theme FIRST before injecting any CSS ───────────────────────────
    # This ensures we inject ONLY one theme's CSS, preventing dark/light conflicts.
    _native_light = False
    try:
        _native_light = st.context.theme.base == "light"
    except Exception:
        pass
    _is_light = _native_light or st.session_state.get("_theme") == "light"

    if _is_light:
        # ── LIGHT MODE: complete standalone CSS (no dark CSS injected at all) ──
        st.markdown(_build_css("light"), unsafe_allow_html=True)
    else:
        # ── DARK MODE: complete standalone CSS ────────────────────────────────
        st.markdown(_build_css("dark"), unsafe_allow_html=True)


# ── CSS constant strings (extracted for module-level caching, upgrade #32) ────
_CSS_LIGHT = """
<style>
    /* ── Chrome Reset ─────────────────────────────────────────────────── */
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

    /* ── Base / App Shell ─────────────────────────────────────────────── */
    .stApp, .main { background: #f1f5f9 !important; color: #1e293b !important; }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }

    /* ── Typography ───────────────────────────────────────────────────── */
    h1 { font-size: 1.75rem !important; font-weight: 800 !important; letter-spacing: -0.5px; color: #0f172a !important; }
    h2 { font-size: 1.2rem !important; font-weight: 600 !important; color: #1e293b !important; letter-spacing: -0.2px; }
    h3 { font-size: 1.0rem !important; font-weight: 600 !important; color: #475569 !important; text-transform: uppercase; letter-spacing: 0.8px; }

    /* ── Custom Scrollbar ─────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #dde3ee; }
    ::-webkit-scrollbar-thumb { background: #b8c4d6; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

    /* ── Metric Cards ─────────────────────────────────────────────────── */
    .metric-card {
        background: rgba(255,255,255,0.97);
        border-radius: 16px; padding: 22px 26px; margin-bottom: 14px;
        border: 1px solid rgba(0,0,0,0.08); border-left: 3px solid #1e3a5f;
        box-shadow: 0 2px 12px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.8);
        transition: border-color 0.22s ease, box-shadow 0.22s ease, transform 0.22s ease;
    }
    .metric-card:hover { box-shadow: 0 8px 28px rgba(0,0,0,0.10); transform: translateY(-2px); }
    .card-green  { border-left-color: #22c55e; }
    .card-blue   { border-left-color: #3b82f6; }
    .card-orange { border-left-color: #f59e0b; }
    .card-red    { border-left-color: #ef4444; }
    .card-violet { border-left-color: #8b5cf6; }
    .big-number { font-size: 2.1rem; font-weight: 800; letter-spacing: -0.8px; line-height: 1.1; color: #0f172a; font-variant-numeric: tabular-nums; }
    .label { font-size: 0.68rem; color: #475569; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px; }

    /* ── Opportunity Cards ────────────────────────────────────────────── */
    .opp-card {
        background: rgba(255,255,255,0.95);
        border-radius: 16px; padding: 20px 24px; margin-bottom: 12px;
        border: 1px solid rgba(0,0,0,0.07);
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
        transition: box-shadow 0.22s ease, transform 0.22s ease;
    }
    .opp-card:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,0,0,0.10); }

    /* ── Card base text ───────────────────────────────────────────────── */
    .metric-card, .opp-card, .arb-tag, .warn-box, .price-chip { color: #1e293b; }

    /* ── Arbitrage Tag ────────────────────────────────────────────────── */
    .arb-tag {
        background: rgba(16,185,129,0.07); border-radius: 14px;
        padding: 16px 20px; margin-bottom: 10px;
        border: 1px solid rgba(16,185,129,0.22);
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .arb-tag:hover { border-color: rgba(16,185,129,0.35); box-shadow: 0 4px 20px rgba(16,185,129,0.07); }

    /* ── Warning Box ──────────────────────────────────────────────────── */
    .warn-box { background: rgba(245,158,11,0.08); border-radius: 12px; padding: 14px 18px; border: 1px solid rgba(245,158,11,0.20); margin-bottom: 14px; }

    /* ── Grade Badge ──────────────────────────────────────────────────── */
    .grade-badge { font-weight: 800; font-size: 0.75rem; padding: 3px 10px; border-radius: 7px; color: #000; letter-spacing: 0.5px; }

    /* ── Badges ───────────────────────────────────────────────────────── */
    .badge-live { font-size: 0.62rem; font-weight: 700; color: #166534; background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.28); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }
    .badge-est  { font-size: 0.62rem; font-weight: 700; color: #92400e; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.28); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }
    .badge-new  { font-size: 0.60rem; font-weight: 700; color: #5b21b6; background: rgba(139,92,246,0.14); border: 1px solid rgba(139,92,246,0.30); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }

    /* ── Pulsing Live Dot ─────────────────────────────────────────────── */
    @keyframes pulse-dot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.55); } 70% { box-shadow: 0 0 0 6px rgba(34,197,94,0); } 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); } }
    .live-dot  { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: pulse-dot 2.2s infinite; vertical-align: middle; margin-right: 5px; }
    .stale-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; vertical-align: middle; margin-right: 5px; }

    /* ── Skeleton ─────────────────────────────────────────────────────── */
    @keyframes skeleton-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
    .skeleton { background: linear-gradient(90deg, rgba(0,0,0,0.04) 25%, rgba(0,0,0,0.08) 50%, rgba(0,0,0,0.04) 75%); background-size: 200% 100%; animation: skeleton-shimmer 1.6s ease-in-out infinite; border-radius: 8px; min-height: 20px; }

    /* ── APY Glows ────────────────────────────────────────────────────── */
    .apy-glow         { text-shadow: 0 0 18px rgba(34,197,94,0.45); }
    .apy-glow-high    { text-shadow: 0 0 24px rgba(245,158,11,0.55); }
    .apy-glow-extreme { text-shadow: 0 0 28px rgba(139,92,246,0.60); }

    /* ── Dividers ─────────────────────────────────────────────────────── */
    .divider { border: none; border-top: 1px solid rgba(0,0,0,0.09); margin: 28px 0; }

    /* ── Section Header ───────────────────────────────────────────────── */
    .section-header { font-size: 0.78rem; font-weight: 700; color: #1e293b; text-transform: uppercase; letter-spacing: 1.4px; padding-bottom: 8px; border-bottom: 1px solid rgba(109,40,217,0.3); margin-bottom: 16px; margin-top: 8px; }

    /* ── Section Label ────────────────────────────────────────────────── */
    .section-label { font-size: 0.65rem; color: #475569; text-transform: uppercase; letter-spacing: 1.6px; margin-bottom: 10px; margin-top: 6px; }

    /* ── Sidebar ──────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] { background: #e8edf5 !important; border-right: 1px solid rgba(0,0,0,0.08); }
    [data-testid="stSidebar"] .block-container { padding-top: 0.6rem !important; padding-bottom: 0.4rem !important; }
    [data-testid="stSidebar"] .divider { margin: 6px 0 !important; }
    [data-testid="stSidebar"] .section-label { margin-bottom: 3px !important; margin-top: 2px !important; }

    /* ── Buttons ──────────────────────────────────────────────────────── */
    div[data-testid="stButton"] > button,
    button[kind="secondary"], button[kind="primary"] {
        border-radius: 10px !important;
        border: 1px solid rgba(0,0,0,0.12) !important;
        font-weight: 700 !important; font-size: 0.82rem !important; letter-spacing: 0.3px !important;
        background: #ffffff !important;
        color: #1e293b !important;
        transition: background 0.15s, border-color 0.15s, box-shadow 0.15s, transform 0.1s !important;
    }
    div[data-testid="stButton"] > button:hover,
    button[kind="secondary"]:hover { border-color: rgba(109,40,217,0.4) !important; box-shadow: 0 0 12px rgba(109,40,217,0.15) !important; transform: translateY(-1px) !important; color: #0f172a !important; background: #f8f5ff !important; }
    div[data-testid="stButton"] > button:active { transform: translateY(0) !important; }

    /* ── Number input +/- step buttons ───────────────────────────────── */
    [data-testid="stNumberInput"] button { background: #f1f5f9 !important; color: #1e293b !important; border-color: rgba(0,0,0,0.10) !important; }

    /* ── Dataframes ───────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    [data-testid="stDataFrame"] table { background: rgba(255,255,255,0.97) !important; }
    [data-testid="stDataFrame"] thead tr th { background: rgba(241,245,249,0.98) !important; color: #64748b !important; font-size: 0.72rem !important; letter-spacing: 0.9px !important; text-transform: uppercase !important; border-bottom: 1px solid rgba(0,0,0,0.06) !important; }
    [data-testid="stDataFrame"] tbody tr:hover td { background: rgba(109,40,217,0.04) !important; }

    /* ── Price Chip ───────────────────────────────────────────────────── */
    .price-chip { text-align: center; padding: 14px 12px; background: rgba(255,255,255,0.97); border-radius: 14px; border: 1px solid rgba(0,0,0,0.08); box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s; }
    .price-chip:hover { box-shadow: 0 6px 20px rgba(0,0,0,0.10); transform: translateY(-1px); }

    /* ── Tabs ─────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tab"] { font-size: 0.82rem; font-weight: 600; color: #64748b; transition: color 0.15s; }
    [data-testid="stTabs"] [role="tab"]:hover { color: #475569; }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #6d28d9 !important; }
    [data-testid="stTabs"] [role="tabpanel"] { padding-top: 16px; }

    /* ── Inputs ───────────────────────────────────────────────────────── */
    [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input {
        background: rgba(255,255,255,0.97) !important; border: 1px solid rgba(0,0,0,0.10) !important;
        border-radius: 9px !important; color: #1e293b !important; transition: border-color 0.15s, box-shadow 0.15s;
    }
    [data-testid="stNumberInput"] input:focus, [data-testid="stTextInput"] input:focus {
        border-color: rgba(109,40,217,0.45) !important; box-shadow: 0 0 0 3px rgba(109,40,217,0.10) !important;
    }

    /* ── Expanders ────────────────────────────────────────────────────── */
    [data-testid="stExpander"] { border: 1px solid rgba(0,0,0,0.08) !important; border-radius: 12px !important; background: rgba(248,250,252,0.95) !important; transition: border-color 0.2s; }
    [data-testid="stExpander"]:hover { border-color: rgba(0,0,0,0.12) !important; }

    /* ── Radio ────────────────────────────────────────────────────────── */
    [data-testid="stRadio"] label span { color: #475569 !important; }
    [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p { color: #475569 !important; }

    /* ── Alert Boxes ──────────────────────────────────────────────────── */
    [data-testid="stAlert"] { background: rgba(255,255,255,0.92) !important; border-color: rgba(0,0,0,0.08) !important; border-radius: 12px !important; border-left-width: 3px !important; }
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] li { color: #1e293b !important; }

    /* ── Markdown text ────────────────────────────────────────────────── */
    [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { color: #1e293b; }

    /* ── Misc ─────────────────────────────────────────────────────────── */
    .mono-number { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
    .rank-1 { color: #b45309 !important; }
    .rank-2 { color: #475569 !important; }
    .rank-3 { color: #92400e !important; }

    /* ── Mobile ───────────────────────────────────────────────────────── */
    @media (max-width: 768px) {
        .big-number { font-size: 1.55rem !important; } h1 { font-size: 1.4rem !important; }
        .metric-card, .opp-card { padding: 14px 16px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
        .price-chip { padding: 10px 8px; }
        [data-testid="stTabs"] [role="tab"] { font-size: 0.75rem; }
    }

    /* ── Flip dark inline colors → accessible dark equivalents ───────── */
    :is(div,span,p,a)[style*="color:#f1f5f9"]  { color: #0f172a !important; }
    :is(div,span,p,a)[style*="color:#e2e8f0"]  { color: #1e293b !important; }
    :is(div,span,p,a)[style*="color:#c4cbdb"]  { color: #334155 !important; }
    :is(div,span,p,a)[style*="color:#cbd5e1"]  { color: #334155 !important; }
    :is(div,span,p,a)[style*="color:#94a3b8"]  { color: #475569 !important; }
    :is(div,span,p,a)[style*="color:#64748b"]  { color: #475569 !important; }
    :is(div,span,p,a)[style*="color:#475569"]  { color: #475569 !important; }
    :is(div,span,p,a)[style*="color:#334155"]  { color: #475569 !important; }
    :is(div,span,p,a)[style*="color:#1e293b"]  { color: #1e293b !important; }
    :is(div,span,p,a)[style*="color:#0f172a"]  { color: #0f172a !important; }
    /* Neon/vibrant → accessible on white */
    :is(div,span,p,a)[style*="color:#f59e0b"]  { color: #92400e !important; }
    :is(div,span,p,a)[style*="color:#a78bfa"]  { color: #5b21b6 !important; }
    :is(div,span,p,a)[style*="color:#22c55e"]  { color: #166534 !important; }
    :is(div,span,p,a)[style*="color:#10b981"]  { color: #065f46 !important; }
    :is(div,span,p,a)[style*="color:#3b82f6"]  { color: #1d4ed8 !important; }
    :is(div,span,p,a)[style*="color:#8b5cf6"]  { color: #5b21b6 !important; }

    /* ── Flip dark card backgrounds → white ──────────────────────────── */
    div[style*="background:rgba(13,14,20"], div[style*="background:rgba(19,20,28"] {
        background: rgba(255,255,255,0.92) !important; border-color: rgba(0,0,0,0.07) !important;
    }
    div[style*="border:1px solid rgba(255,255,255"] { border-color: rgba(0,0,0,0.08) !important; }
    div[style*="background:rgba(255,255,255,0.05"], div[style*="background:rgba(255,255,255,0.07"],
    div[style*="background:rgba(255,255,255,0.04"] { background: rgba(0,0,0,0.07) !important; }
    span[style*="background:rgba(255,255,255,0.04"] { background: rgba(0,0,0,0.05) !important; }

    /* ── Override Streamlit CSS variables ────────────────────────────── */
    /* These variables drive ALL widget backgrounds (expanders, selects,  */
    /* code blocks, etc.). Without these, dark-theme vars bleed through.  */
    :root {
        --background-color: #f1f5f9 !important;
        --secondary-background-color: #e8edf5 !important;
        --text-color: #1e293b !important;
        color-scheme: light;
    }

    /* ── Select / Dropdown widgets (data-baseweb) ─────────────────────── */
    [data-baseweb="select"] [data-baseweb="input-container"],
    [data-baseweb="select"] [data-baseweb="value-container"],
    [data-baseweb="input"],
    [data-baseweb="textarea"],
    [data-baseweb="select"] { background-color: #ffffff !important; color: #1e293b !important; }
    [data-baseweb="list"],
    [data-baseweb="popover"] { background-color: #ffffff !important; color: #1e293b !important; border: 1px solid rgba(0,0,0,0.10) !important; }
    [data-baseweb="list"] li,
    [data-baseweb="menu-item"] { color: #1e293b !important; }
    [data-baseweb="list"] li:hover,
    [data-baseweb="menu-item"]:hover { background-color: rgba(109,40,217,0.07) !important; }

    /* ── Select label + option text ───────────────────────────────────── */
    [data-testid="stSelectbox"] label,
    [data-testid="stSelectbox"] span,
    [data-testid="stMultiSelect"] label,
    [data-testid="stMultiSelect"] span { color: #1e293b !important; }

    /* ── Slider ───────────────────────────────────────────────────────── */
    [data-testid="stSlider"] label,
    [data-testid="stSlider"] p { color: #1e293b !important; }

    /* ── Caption / code blocks ────────────────────────────────────────── */
    [data-testid="stCaptionContainer"] p,
    .stCaption, caption { color: #64748b !important; }
    [data-testid="stCodeBlock"] pre,
    [data-testid="stCodeBlock"] code { background-color: #f8fafc !important; color: #1e293b !important; border: 1px solid rgba(0,0,0,0.07) !important; }

    /* ── Column + vertical block wrappers ─────────────────────────────── */
    /* These sometimes get a dark background from Streamlit's CSS variable */
    [data-testid="stColumn"],
    [data-testid="stVerticalBlock"],
    [data-testid="stHorizontalBlock"] { background: transparent !important; }

    /* ── Sidebar text / labels ────────────────────────────────────────── */
    [data-testid="stSidebar"] * { color: #1e293b; }
    [data-testid="stSidebar"] label,
    [data-testid="stSidebar"] p,
    [data-testid="stSidebar"] span { color: #1e293b !important; }
    [data-testid="stSidebar"] [data-baseweb="select"] [data-baseweb="input-container"],
    [data-testid="stSidebar"] [data-baseweb="select"] { background-color: #dde4ee !important; }

    /* ── Info / success / warning / error banners ─────────────────────── */
    [data-testid="stInfo"],
    [data-testid="stSuccess"],
    [data-testid="stWarning"],
    [data-testid="stError"] { color: #1e293b !important; }

    /* ── st.metric widget ─────────────────────────────────────────────── */
    [data-testid="stMetric"] label { color: #475569 !important; }
    [data-testid="stMetric"] [data-testid="stMetricValue"] { color: #0f172a !important; }
    [data-testid="stMetric"] [data-testid="stMetricDelta"] { color: #475569 !important; }

    /* ── Checkbox / toggle ────────────────────────────────────────────── */
    [data-testid="stCheckbox"] label span,
    [data-testid="stToggle"] label span { color: #1e293b !important; }
</style>
"""

_CSS_DARK = """
<style>
    /* ── Chrome Reset ─────────────────────────────────────────────────── */
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

    /* ── Base / App Shell ─────────────────────────────────────────────── */
    .stApp, .main { background: #0d0e14 !important; color: #e2e8f0 !important; }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }

    /* ── Typography ───────────────────────────────────────────────────── */
    h1 { font-size: 1.75rem !important; font-weight: 800 !important; letter-spacing: -0.5px; color: #e2e8f0 !important; }
    h2 { font-size: 1.2rem !important; font-weight: 600 !important; color: #cbd5e1 !important; letter-spacing: -0.2px; }
    h3 { font-size: 1.0rem !important; font-weight: 600 !important; color: #94a3b8 !important; text-transform: uppercase; letter-spacing: 0.8px; }

    /* ── Custom Scrollbar ─────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #13141c; }
    ::-webkit-scrollbar-thumb { background: #2d2e45; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #3d3e5a; }

    /* ── Glassmorphism Metric Cards ───────────────────────────────────── */
    .metric-card {
        background: rgba(19,20,28,0.95);
        backdrop-filter: blur(16px) saturate(180%);
        -webkit-backdrop-filter: blur(16px) saturate(180%);
        border-radius: 16px; padding: 22px 26px; margin-bottom: 14px;
        border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #1e3a5f;
        box-shadow: 0 4px 24px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05);
        transition: border-color 0.22s cubic-bezier(0.4,0,0.2,1),
                    box-shadow 0.22s cubic-bezier(0.4,0,0.2,1),
                    transform 0.22s cubic-bezier(0.4,0,0.2,1);
    }
    .metric-card:hover { border-color: rgba(255,255,255,0.14); box-shadow: 0 10px 36px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.08); transform: translateY(-2px); }
    .card-green  { border-left-color: #22c55e; }
    .card-blue   { border-left-color: #3b82f6; }
    .card-orange { border-left-color: #f59e0b; }
    .card-red    { border-left-color: #ef4444; }
    .card-violet { border-left-color: #8b5cf6; }
    .big-number { font-size: 2.1rem; font-weight: 800; letter-spacing: -0.8px; line-height: 1.1; color: #f1f5f9; font-variant-numeric: tabular-nums; }
    .label { font-size: 0.68rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 8px; }

    /* ── Opportunity Cards ────────────────────────────────────────────── */
    .opp-card {
        background: rgba(19,20,28,0.92);
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        border-radius: 16px; padding: 20px 24px; margin-bottom: 12px;
        border: 1px solid rgba(255,255,255,0.07);
        box-shadow: 0 2px 16px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04);
        transition: border-color 0.22s cubic-bezier(0.4,0,0.2,1), transform 0.22s cubic-bezier(0.4,0,0.2,1), box-shadow 0.22s cubic-bezier(0.4,0,0.2,1);
    }
    .opp-card:hover { border-color: rgba(255,255,255,0.13); transform: translateY(-2px); box-shadow: 0 10px 36px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.07); }

    /* ── Arbitrage Tag ────────────────────────────────────────────────── */
    .arb-tag { background: rgba(16,185,129,0.04); border-radius: 14px; padding: 16px 20px; margin-bottom: 10px; border: 1px solid rgba(16,185,129,0.13); transition: border-color 0.2s, box-shadow 0.2s; }
    .arb-tag:hover { border-color: rgba(16,185,129,0.26); box-shadow: 0 4px 20px rgba(16,185,129,0.07); }

    /* ── Warning Box ──────────────────────────────────────────────────── */
    .warn-box { background: rgba(245,158,11,0.05); border-radius: 12px; padding: 14px 18px; border: 1px solid rgba(245,158,11,0.15); margin-bottom: 14px; box-shadow: 0 2px 12px rgba(245,158,11,0.05); }

    /* ── Grade Badge ──────────────────────────────────────────────────── */
    .grade-badge { font-weight: 800; font-size: 0.75rem; padding: 3px 10px; border-radius: 7px; color: #000; letter-spacing: 0.5px; }

    /* ── Badges ───────────────────────────────────────────────────────── */
    .badge-live { font-size: 0.62rem; font-weight: 700; color: #22c55e; background: rgba(34,197,94,0.12); border: 1px solid rgba(34,197,94,0.28); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }
    .badge-est  { font-size: 0.62rem; font-weight: 700; color: #f59e0b; background: rgba(245,158,11,0.12); border: 1px solid rgba(245,158,11,0.28); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }
    .badge-new  { font-size: 0.60rem; font-weight: 700; color: #a78bfa; background: rgba(139,92,246,0.14); border: 1px solid rgba(139,92,246,0.30); border-radius: 5px; padding: 1px 7px; letter-spacing: 0.6px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }

    /* ── Pulsing Live Dot ─────────────────────────────────────────────── */
    @keyframes pulse-dot { 0% { box-shadow: 0 0 0 0 rgba(34,197,94,0.55); } 70% { box-shadow: 0 0 0 6px rgba(34,197,94,0); } 100% { box-shadow: 0 0 0 0 rgba(34,197,94,0); } }
    .live-dot  { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #22c55e; animation: pulse-dot 2.2s infinite; vertical-align: middle; margin-right: 5px; }
    .stale-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #f59e0b; vertical-align: middle; margin-right: 5px; }

    /* ── Skeleton ─────────────────────────────────────────────────────── */
    @keyframes skeleton-shimmer { 0% { background-position: 200% 0; } 100% { background-position: -200% 0; } }
    .skeleton { background: linear-gradient(90deg, rgba(255,255,255,0.04) 25%, rgba(255,255,255,0.09) 50%, rgba(255,255,255,0.04) 75%); background-size: 200% 100%; animation: skeleton-shimmer 1.6s ease-in-out infinite; border-radius: 8px; min-height: 20px; }

    /* ── APY Glows ────────────────────────────────────────────────────── */
    .apy-glow         { text-shadow: 0 0 18px rgba(34,197,94,0.45); }
    .apy-glow-high    { text-shadow: 0 0 24px rgba(245,158,11,0.55); }
    .apy-glow-extreme { text-shadow: 0 0 28px rgba(139,92,246,0.60); }

    /* ── Dividers ─────────────────────────────────────────────────────── */
    .divider { border: none; border-top: 1px solid rgba(255,255,255,0.05); margin: 28px 0; }

    /* ── Section Header ───────────────────────────────────────────────── */
    .section-header { font-size: 0.78rem; font-weight: 700; color: #e2e8f0; text-transform: uppercase; letter-spacing: 1.4px; padding-bottom: 8px; border-bottom: 1px solid rgba(139,92,246,0.35); margin-bottom: 16px; margin-top: 8px; }

    /* ── Section Label ────────────────────────────────────────────────── */
    .section-label { font-size: 0.65rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 1.6px; margin-bottom: 10px; margin-top: 6px; }

    /* ── Sidebar ──────────────────────────────────────────────────────── */
    [data-testid="stSidebar"] { background: #0f1019 !important; border-right: 1px solid rgba(255,255,255,0.06); }
    [data-testid="stSidebar"] .block-container { padding-top: 0.6rem !important; padding-bottom: 0.4rem !important; }
    [data-testid="stSidebar"] .divider { margin: 6px 0 !important; }
    [data-testid="stSidebar"] .section-label { margin-bottom: 3px !important; margin-top: 2px !important; }

    /* ── Buttons ──────────────────────────────────────────────────────── */
    div[data-testid="stButton"] > button {
        border-radius: 10px; border: 1px solid rgba(255,255,255,0.10);
        font-weight: 700; font-size: 0.82rem; letter-spacing: 0.3px;
        background: rgba(19,20,28,0.85); color: #cbd5e1;
        transition: background 0.15s, border-color 0.15s, box-shadow 0.15s, transform 0.1s;
    }
    div[data-testid="stButton"] > button:hover { border-color: rgba(139,92,246,0.45); box-shadow: 0 0 16px rgba(139,92,246,0.18); transform: translateY(-1px); color: #f1f5f9; }
    div[data-testid="stButton"] > button:active { transform: translateY(0); }

    /* ── Dataframes ───────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    [data-testid="stDataFrame"] table { background: rgba(13,14,20,0.96) !important; }
    [data-testid="stDataFrame"] thead tr th { background: rgba(19,20,28,0.98) !important; color: #94a3b8 !important; font-size: 0.72rem !important; letter-spacing: 0.9px !important; text-transform: uppercase !important; border-bottom: 1px solid rgba(255,255,255,0.06) !important; }
    [data-testid="stDataFrame"] tbody tr:hover td { background: rgba(139,92,246,0.06) !important; }

    /* ── Price Chip ───────────────────────────────────────────────────── */
    .price-chip { text-align: center; padding: 14px 12px; background: rgba(19,20,28,0.94); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-radius: 14px; border: 1px solid rgba(255,255,255,0.07); box-shadow: 0 2px 12px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04); transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s; }
    .price-chip:hover { border-color: rgba(255,255,255,0.13); box-shadow: 0 6px 24px rgba(0,0,0,0.45); transform: translateY(-1px); }

    /* ── Tabs ─────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tab"] { font-size: 0.82rem; font-weight: 600; color: #64748b; transition: color 0.15s; }
    [data-testid="stTabs"] [role="tab"]:hover { color: #94a3b8; }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #a78bfa; }
    [data-testid="stTabs"] [role="tabpanel"] { padding-top: 16px; }

    /* ── Inputs ───────────────────────────────────────────────────────── */
    [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input {
        background: rgba(19,20,28,0.88) !important; border: 1px solid rgba(255,255,255,0.09) !important;
        border-radius: 9px !important; color: #e2e8f0 !important;
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    [data-testid="stNumberInput"] input:focus, [data-testid="stTextInput"] input:focus {
        border-color: rgba(139,92,246,0.45) !important; box-shadow: 0 0 0 3px rgba(139,92,246,0.12) !important;
    }

    /* ── Expanders ────────────────────────────────────────────────────── */
    [data-testid="stExpander"] { border: 1px solid rgba(255,255,255,0.07) !important; border-radius: 12px !important; background: rgba(13,14,20,0.72) !important; transition: border-color 0.2s; }
    [data-testid="stExpander"]:hover { border-color: rgba(255,255,255,0.11) !important; }

    /* ── Radio ────────────────────────────────────────────────────────── */
    [data-testid="stRadio"] label span { color: #94a3b8 !important; }
    [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p { color: #94a3b8 !important; }

    /* ── Alert Boxes ──────────────────────────────────────────────────── */
    [data-testid="stAlert"] { background: rgba(19,20,28,0.92) !important; border-radius: 12px !important; border-left-width: 3px !important; }
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] li { color: #94a3b8 !important; }

    /* ── Misc ─────────────────────────────────────────────────────────── */
    .mono-number { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
    .rank-1 { color: #fbbf24 !important; }
    .rank-2 { color: #94a3b8 !important; }
    .rank-3 { color: #b45309 !important; }

    /* ── Mobile ───────────────────────────────────────────────────────── */
    @media (max-width: 768px) {
        .big-number { font-size: 1.55rem !important; } h1 { font-size: 1.4rem !important; }
        .metric-card, .opp-card { padding: 14px 16px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
        .price-chip { padding: 10px 8px; }
        [data-testid="stTabs"] [role="tab"] { font-size: 0.75rem; }
    }

    /* ── Dark-mode legibility lift ────────────────────────────────────── */
    :is(div,span,p,a)[style*="color:#334155"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#475569"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#64748b"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#1e293b"] { color: #64748b !important; }
    :is(div,span,p,a)[style*="color:#0f172a"] { color: #64748b !important; }
</style>
"""


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
        _native_light = False
        try:
            _native_light = st.context.theme.base == "light"
        except Exception:
            pass
        _is_light = _native_light or st.session_state.get("_theme") == "light"
        _logo_col, _theme_col = st.columns([3, 1])
        with _logo_col:
            st.markdown(
                "<div style='font-size:1.25rem; font-weight:800; "
                "background: linear-gradient(90deg, #a78bfa, #60a5fa); "
                "-webkit-background-clip: text; -webkit-text-fill-color: transparent; "
                "background-clip: text; letter-spacing:-0.3px; margin-bottom:0px;'>⚡ Flare DeFi</div>"
                "<div style='font-size:0.68rem; letter-spacing:1.2px; "
                "text-transform:uppercase; margin-bottom:4px;'>Analytics Dashboard</div>",
                unsafe_allow_html=True,
            )
        with _theme_col:
            st.markdown("<div style='padding-top:6px;'></div>", unsafe_allow_html=True)
            if st.button("☀" if _is_light else "🌙", key="_theme_toggle",
                         help="Switch to light mode" if not _is_light else "Switch to dark mode",
                         use_container_width=True):
                st.session_state["_theme"] = "dark" if _is_light else "light"
                st.rerun()

        latest    = load_latest()
        last_scan = latest.get("completed_at") or latest.get("run_id")

        # ── Auto-scan on first open (no data file exists yet) ─────────────────
        if not last_scan and not st.session_state.get("_auto_scan_triggered"):
            st.session_state["_auto_scan_triggered"] = True
            if not st.session_state.get("_scanning"):
                try:
                    _sched_path = str(Path(__file__).parent.parent / "scheduler.py")
                    subprocess.Popen(
                        [sys.executable, _sched_path, "--now"],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                    )
                    st.session_state._scanning = True
                    st.session_state._scan_deadline = time.time() + 120
                    st.session_state._scan_baseline = ""
                except Exception:
                    pass  # user can click ▶ Scan manually

        # Determine data freshness
        is_fresh = False
        if last_scan:
            try:
                scan_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                if scan_dt.tzinfo is None:
                    scan_dt = scan_dt.replace(tzinfo=timezone.utc)
                is_fresh = (datetime.now(timezone.utc) - scan_dt).total_seconds() < 3600
            except Exception:
                pass
        dot_html = "<span class='live-dot'></span>" if is_fresh else "<span class='stale-dot'></span>"
        st.markdown(
            f"<div style='font-size:0.73rem; color:#475569; line-height:1.5; margin-bottom:4px;'>"
            f"{dot_html}"
            f"<span style='color:#94a3b8'>{_ts_fmt(last_scan) if last_scan else 'No scan yet'}</span>"
            f" · Next <span style='color:#64748b'>{_next_scan()}</span></div>",
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
                with open(HISTORY_FILE, encoding="utf-8") as _f:
                    _hist_ts = (json.load(_f).get("latest") or {}).get("completed_at") or ""
            except Exception:
                _hist_ts = ""
            if _hist_ts and _hist_ts != st.session_state.get("_scan_baseline", ""):
                st.session_state._scanning = False
                st.cache_data.clear()
                st.rerun()
            elif time.time() < st.session_state.get("_scan_deadline", 0):
                st.caption("⏳ Scanning… auto-reloading when done.")
                time.sleep(0.5)   # short poll — avoid blocking the server thread for 2s on every rerender
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
        _PROFILE_EMOJI = {"conservative": "🟢", "medium": "🟡", "high": "🔴"}
        _PROFILE_DISPLAY = {"conservative": "Conservative", "medium": "Balanced", "high": "Aggressive"}
        profile = st.radio(
            "Risk Profile",
            options=list(RISK_PROFILE_NAMES),
            format_func=lambda p: (
                f"{_PROFILE_EMOJI[p]}  {_PROFILE_DISPLAY[p]}  "
                f"({RISK_PROFILES[p]['target_apy_low']:.0f}–{RISK_PROFILES[p]['target_apy_high']:.0f}%)"
            ),
            key="risk_profile",
            label_visibility="collapsed",
        )
        profile_cfg = RISK_PROFILES[profile]
        color       = profile_cfg["color"]

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # Pro Mode toggle (#82) — stored in session_state so all pages read it consistently
        _pro_val = st.toggle(
            "Pro Mode",
            value=st.session_state.get("defi_pro_mode", False),
            key="defi_pro_mode_sidebar",
            help=(
                "Pro: shows Sharpe ratio, Kelly fraction, HMM state, real yield ratio, "
                "technical indicators, and all advanced columns. "
                "Beginner: simplified card view with plain-English labels and tooltips."
            ),
        )
        # Sync to the canonical session state key used by all pages
        st.session_state["defi_pro_mode"] = _pro_val

        # Demo / Sandbox mode toggle (#67)
        _demo_val = st.toggle(
            "Demo / Sandbox",
            value=st.session_state.get("defi_demo_mode", False),
            key="defi_demo_toggle",
            help="Demo mode: shows synthetic placeholder data only — no real API calls. Safe for screenshots and onboarding.",
        )
        st.session_state["defi_demo_mode"] = _demo_val
        if _demo_val:
            st.markdown(
                "<div style='font-size:11px;color:#F59E0B;margin-top:-4px;margin-bottom:4px;'>"
                "⚠️ DEMO MODE — synthetic data only</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.67rem; color:#334155; line-height:1.4; padding:4px 0;'>"
            "⚠ Not financial advice · DYOR before investing.</div>",
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
        "demo_mode":      st.session_state.get("defi_demo_mode", False),
        "pro_mode":       st.session_state.get("defi_pro_mode", False),
    }


# ─── Data Loaders ─────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def _load_history_file() -> dict:
    """Single cached read of history.json — shared by load_latest() and load_history_runs()."""
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ history.json is corrupted — re-run the scheduler.")
        return {}
    except Exception as e:
        st.warning(f"⚠️ Could not load scan data: {e}")
        return {}


def load_latest() -> dict:
    return _load_history_file().get("latest") or {}


def load_history_runs() -> list:
    return _load_history_file().get("runs") or []


@st.cache_data(ttl=60)
def load_positions() -> list:
    if not POSITIONS_FILE.exists():
        return []
    try:
        with open(POSITIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ positions.json is corrupted.")
        return []
    except Exception:
        return []


def save_positions(positions: list) -> None:
    if not atomic_json_write(POSITIONS_FILE, positions):
        st.error("Could not save positions — check logs.")
    load_positions.clear()


@st.cache_data(ttl=300)
def load_wallets() -> list:
    if not WALLETS_FILE.exists():
        return []
    try:
        with open(WALLETS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_wallets(wallets: list) -> None:
    if not atomic_json_write(WALLETS_FILE, wallets):
        st.error("Could not save wallets — check logs.")
    load_wallets.clear()


@st.cache_data(ttl=300)
def load_monitor_digest() -> dict:
    if not MONITOR_DIGEST_FILE.exists():
        return {}
    try:
        with open(MONITOR_DIGEST_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ─── Memory Optimization: Shared Model Cache (#69) ───────────────────────────
# st.cache_resource creates a SINGLE shared copy per process rather than
# per-session copies. For a 20MB history.json this saves N×20MB of RAM
# where N = concurrent user sessions.

@st.cache_resource
def _get_history_cache() -> dict:
    """Process-level singleton for the history.json data object."""
    return {"data": None, "loaded_at": 0.0}


def load_latest_cached(ttl: float = 60.0) -> dict:
    """
    Memory-optimized version of load_latest() using a shared process-level cache.
    Avoids duplicating the large history dict across all Streamlit sessions.
    Falls back to load_latest() if cache object is unavailable.
    """
    try:
        _cache_obj = _get_history_cache()
        now = time.time()
        if _cache_obj["data"] is None or (now - _cache_obj["loaded_at"]) > ttl:
            _cache_obj["data"]      = load_latest()
            _cache_obj["loaded_at"] = now
        return _cache_obj["data"] or {}
    except Exception:
        return load_latest()


# ─── Utility Helpers ──────────────────────────────────────────────────────────

_URGENCY_COLOR = {"act_now": "#ef4444", "act_soon": "#f59e0b", "monitor": "#3b82f6"}
_URGENCY_LABEL = {"act_now": "ACT NOW", "act_soon": "ACT SOON", "monitor": "MONITOR"}


def render_urgency_badge(urgency: str) -> str:
    """Return an HTML badge string for arbitrage urgency levels."""
    color = _URGENCY_COLOR.get(urgency, "#3b82f6")
    label = _URGENCY_LABEL.get(urgency, _html.escape((urgency or "").upper()))
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
        try:
            parts = t.split(":")
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            continue
        scan_times.append(datetime(today.year, today.month, today.day, h, m, tzinfo=tz))
    future = [t for t in scan_times if t > now_local]
    if not future:
        if not SCHEDULER.get("run_times"):
            return "unknown"
        tmrw   = today + timedelta(days=1)
        try:
            _p0 = SCHEDULER["run_times"][0].split(":")
            h0, m0 = int(_p0[0]), int(_p0[1]) if len(_p0) > 1 else 0
        except (ValueError, IndexError):
            return "unknown"
        next_t = datetime(tmrw.year, tmrw.month, tmrw.day, h0, m0, tzinfo=tz)
    else:
        next_t = min(future)
    next_utc = next_t.astimezone(timezone.utc)
    delta       = next_utc - datetime.now(timezone.utc)
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
            _entry_dt = datetime.fromisoformat(entry_date_str)
            if _entry_dt.tzinfo is None:
                _entry_dt = _entry_dt.replace(tzinfo=timezone.utc)
            days_active = max(0, (datetime.now(timezone.utc) - _entry_dt).days)
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
        price_lookup  = {
            (p.get("symbol", "") if isinstance(p, dict) else getattr(p, "symbol", "")): (p.get("price_usd", 0) if isinstance(p, dict) else getattr(p, "price_usd", 0))
            for p in (current_prices or [])
        }
        token_a       = pos.get("token_a", "")
        entry_price_a = float(pos.get("entry_price_a", 0))
        _lkp_a        = price_lookup.get(token_a)
        curr_price_a  = _lkp_a if _lkp_a is not None else entry_price_a
        if entry_price_a > 0 and curr_price_a > 0:
            il_pct = calculate_il(curr_price_a / entry_price_a)
        token_b        = pos.get("token_b", "")
        token_a_amount = float(pos.get("token_a_amount", 0))
        token_b_amount = float(pos.get("token_b_amount", 0))
        entry_price_b  = float(pos.get("entry_price_b", 0))
        _lkp_b        = price_lookup.get(token_b)
        curr_price_b  = _lkp_b if _lkp_b is not None else entry_price_b
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


# ─── FTSO IL Calculator (Feature 4) ──────────────────────────────────────────

def render_ftso_il_calculator(prices: list = None) -> None:
    """
    Feature 4: Interactive IL calculator using FTSO/live prices.
    Renders a Streamlit widget showing IL for any token pair at any price scenario.
    Can be called from any page.
    """
    from models.risk_models import calculate_il
    from config import FALLBACK_PRICES

    # Build price lookup from live prices
    price_lkp = {}
    if prices:
        price_lkp = {p.get("symbol", ""): p.get("price_usd", 0) for p in prices if isinstance(p, dict)}

    known_tokens = ["FLR", "WFLR", "sFLR", "FXRP", "XRP", "wETH", "HLN", "USD0", "USDT0", "USDC.e"]

    c1, c2 = st.columns(2)
    with c1:
        token_a_sym = st.selectbox("Token A", known_tokens, key="il_token_a")
        _default_a  = price_lkp.get(token_a_sym) or FALLBACK_PRICES.get(token_a_sym, 0.01)
        entry_a = st.number_input("Token A entry price ($)", value=float(_default_a),
                                  min_value=0.0001, format="%.6f", key="il_entry_a",
                                  help="Uses FTSO live price when available")
        current_a = st.number_input("Token A current price ($)", value=float(price_lkp.get(token_a_sym) or _default_a),
                                    min_value=0.0001, format="%.6f", key="il_curr_a")
    with c2:
        token_b_sym = st.selectbox("Token B", ["USD0", "USDT0", "USDC.e", "WFLR", "sFLR", "FXRP", "wETH", "HLN"], key="il_token_b")
        _default_b  = price_lkp.get(token_b_sym) or FALLBACK_PRICES.get(token_b_sym, 1.00)
        entry_b = st.number_input("Token B entry price ($)", value=float(_default_b),
                                  min_value=0.0001, format="%.6f", key="il_entry_b")
        current_b = st.number_input("Token B current price ($)", value=float(price_lkp.get(token_b_sym) or _default_b),
                                    min_value=0.0001, format="%.6f", key="il_curr_b")

    deposit = st.number_input("Deposit value ($)", min_value=1.0, value=1000.0, step=100.0, key="il_deposit")

    # Compute IL using the FTSO-validated price ratio
    if entry_a > 0 and entry_b > 0 and current_a > 0 and current_b > 0:
        # Price ratio = (current_a/current_b) / (entry_a/entry_b)
        entry_ratio   = entry_a / entry_b
        current_ratio = current_a / current_b
        price_ratio   = current_ratio / entry_ratio if entry_ratio > 0 else 1.0
        il_pct        = calculate_il(price_ratio)
        il_usd        = deposit * il_pct / 100
        hodl_val      = deposit * 0.5 * (current_a / entry_a) + deposit * 0.5 * (current_b / entry_b)
        lp_val        = deposit - il_usd
        il_color      = "#10b981" if il_pct < 1 else ("#f59e0b" if il_pct < 5 else "#ef4444")

        is_ftso = bool(price_lkp.get(token_a_sym) or price_lkp.get(token_b_sym))
        src_label = "FTSO/Live" if is_ftso else "Manual"

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""<div class="metric-card card-red">
            <div class="label">Impermanent Loss</div>
            <div class="big-number" style="color:{il_color};">{il_pct:.2f}%</div>
            <div style="color:#475569; font-size:0.8rem; margin-top:4px;">≈ ${il_usd:,.2f} on ${deposit:,.0f}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""<div class="metric-card card-blue">
            <div class="label">LP Value Now</div>
            <div class="big-number">${lp_val:,.0f}</div>
            <div style="color:#475569; font-size:0.8rem; margin-top:4px;">After IL vs ${deposit:,.0f} in</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            hodl_diff = hodl_val - lp_val
            h_color   = "#10b981" if hodl_diff <= 0 else "#ef4444"
            st.markdown(f"""<div class="metric-card card-orange">
            <div class="label">HODL vs LP</div>
            <div class="big-number" style="color:{h_color};">${hodl_diff:+,.0f}</div>
            <div style="color:#475569; font-size:0.8rem; margin-top:4px;">HODL would be ${hodl_val:,.0f}</div>
            </div>""", unsafe_allow_html=True)

        st.markdown(
            f"<div style='font-size:0.75rem; color:#475569; margin-top:6px;'>"
            f"Price source: <span style='color:#a78bfa;'>{src_label}</span> · "
            f"Price ratio: {price_ratio:.4f}x · "
            f"IL formula: 2√r/(1+r) − 1</div>",
            unsafe_allow_html=True,
        )


# ─── Shared Render Components ─────────────────────────────────────────────────

def render_price_strip(prices: list) -> None:
    if not prices:
        return
    cols = st.columns(len(prices))
    for i, p in enumerate(prices):
        sym   = _html.escape(str(p.get("symbol", "?")))
        price = float(p.get("price_usd") or 0)
        chg   = float(p.get("change_24h") or 0)
        color = "#22c55e" if chg >= 0 else "#ef4444"
        arrow = "▲" if chg >= 0 else "▼"
        is_live = p.get("data_source") not in ("estimate", "baseline")
        dot_html = "<span class='live-dot'></span>" if is_live else "<span class='stale-dot'></span>"
        # Format price: use fewer decimals for higher-value tokens
        price_str = f"${price:,.2f}" if price >= 1 else f"${price:,.4f}"
        with cols[i]:
            st.markdown(f"""
            <div class="price-chip">
                <div style="font-size:0.68rem; color:#64748b; margin-bottom:5px; display:flex; align-items:center; justify-content:center; gap:4px;">
                    {dot_html}<span style="letter-spacing:0.6px; text-transform:uppercase;">{sym}</span>
                </div>
                <div style="font-size:1.12rem; font-weight:700; letter-spacing:-0.3px; font-variant-numeric:tabular-nums; color:#f1f5f9;">{price_str}</div>
                <div style="font-size:0.75rem; color:{color}; margin-top:3px; font-weight:600;">{arrow} {abs(chg):.2f}%</div>
            </div>""", unsafe_allow_html=True)


def render_section_header(title: str, subtitle: str = "") -> None:
    """Renders a section title with violet gradient underline and optional subtitle."""
    sub_html = (
        f"<div style='color:#475569; font-size:0.84rem; margin-top:4px; margin-bottom:16px;'>"
        f"{_html.escape(subtitle)}</div>"
        if subtitle else ""
    )
    st.markdown(
        f"<div class='section-header'>{_html.escape(title)}</div>{sub_html}",
        unsafe_allow_html=True,
    )


def render_incentive_warning() -> None:
    from config import INCENTIVE_PROGRAM
    st.markdown(f"""
    <div class="warn-box" style="display:flex; align-items:flex-start; gap:12px;">
        <span style="font-size:1.1rem; flex-shrink:0; margin-top:1px;">⚠️</span>
        <div>
            <div style="font-weight:700; color:#f59e0b; font-size:0.87rem; margin-bottom:4px;">Incentive Program Notice</div>
            <div style="color:#94a3b8; font-size:0.83rem; line-height:1.55;">{INCENTIVE_PROGRAM['note']}</div>
        </div>
    </div>""", unsafe_allow_html=True)


def render_yield_hero_cards(positions: list, opps: list, portfolio_size: float) -> None:
    total_value = sum(p.get("current_value", 0) for p in positions) or portfolio_size
    avg_apy     = (sum(o.get("estimated_apy", 0) for o in opps[:3]) / min(3, len(opps))) if opps else 0.0

    weekly_yield  = total_value * (avg_apy / 100) / 52
    monthly_yield = total_value * (avg_apy / 100) / 12
    annual_yield  = total_value * (avg_apy / 100)

    c1, c2, c3 = st.columns(3)
    for col, label, value, sub, cls, accent, uid in [
        (c1, "Est. This Week",  f"${weekly_yield:,.2f}",  f"{avg_apy/52:.3f}% weekly",   "card-green",  "#22c55e", "yield-hero-week"),
        (c2, "Est. This Month", f"${monthly_yield:,.2f}", f"on ${total_value:,.0f}",      "card-blue",   "#3b82f6", "yield-hero-month"),
        (c3, "Est. This Year",  f"${annual_yield:,.2f}",  f"{avg_apy:.1f}% APY (top-3)", "card-orange", "#f59e0b", "yield-hero-year"),
    ]:
        with col:
            st.markdown(f"""
            <div id="{uid}" class="metric-card {cls}">
                <div class="label">{label}</div>
                <div class="big-number" style="color:{accent};">{value}</div>
                <div style="color:#64748b; font-size:0.82rem; margin-top:6px;">{sub}</div>
            </div>""", unsafe_allow_html=True)

    # Count-up animation — targets specific hero-card IDs so it never bleeds into other metric cards
    import streamlit.components.v1 as _components
    _components.html(f"""
    <script>
    (function() {{
        function animateCountUp(id, target, prefix, decimals, duration) {{
            var el = window.parent.document.getElementById(id);
            if (!el) return;
            var numEl = el.querySelector('.big-number');
            if (!numEl) return;
            var start = 0, startTime = null;
            function step(ts) {{
                if (!startTime) startTime = ts;
                var progress = Math.min((ts - startTime) / duration, 1);
                var eased = 1 - Math.pow(1 - progress, 3);
                var val = start + (target - start) * eased;
                numEl.textContent = prefix + val.toLocaleString('en-US', {{
                    minimumFractionDigits: decimals,
                    maximumFractionDigits: decimals
                }});
                if (progress < 1) requestAnimationFrame(step);
            }}
            requestAnimationFrame(step);
        }}
        setTimeout(function() {{
            animateCountUp('yield-hero-week',  {weekly_yield:.2f},  '$', 2, 900);
            animateCountUp('yield-hero-month', {monthly_yield:.2f}, '$', 2, 900);
            animateCountUp('yield-hero-year',  {annual_yield:.2f},  '$', 2, 900);
        }}, 200);
    }})();
    </script>
    """, height=0)

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
    il     = opp.get("il_risk") or "low"
    action = opp.get("action", opp.get("plain_english", "—"))
    proto  = opp.get("protocol", "—")
    pool   = opp.get("asset_or_pool", "—")
    src    = opp.get("data_source", "baseline")
    rs     = opp.get("risk_score", 5.0)
    kf     = opp.get("kelly_fraction", 0)
    tvl    = opp.get("tvl_usd", 0)

    grade, grade_color = risk_score_to_grade(rs)
    il_color = {"none": "#22c55e", "low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}.get(il, "#f59e0b")
    il_icon  = {"none": "✓", "low": "✓", "medium": "~", "high": "!"}.get(il, "~")

    est_tag  = " <span class='badge-est'>EST</span>" if src in ("baseline", "estimate") else " <span class='badge-live'>LIVE</span>"
    medals   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
    medal    = medals[min(idx, 5)]
    proto    = _html.escape(str(proto))
    pool     = _html.escape(str(pool))
    action   = _html.escape(str(action))

    # APY glow class based on magnitude
    if apy >= 50:
        glow_cls = "apy-glow-extreme"
    elif apy >= 20:
        glow_cls = "apy-glow-high"
    elif apy >= 8:
        glow_cls = "apy-glow"
    else:
        glow_cls = ""

    alloc_str = (
        f"${kf * portfolio_size:,.0f} <span style='color:#475569'>({kf*100:.0f}%)</span>"
        if portfolio_size > 0
        else f"{kf*100:.0f}% of portfolio"
    )

    # TVL display + velocity arrow (Upgrade #1)
    tvl_trend    = opp.get("tvl_trend", "")
    tvl_velocity = opp.get("tvl_velocity", 0.0)
    _trend_arrow = {"up": "↑", "down": "↓", "stable": "→"}.get(tvl_trend, "")
    _trend_color = {"up": "#22c55e", "down": "#ef4444", "stable": "#64748b"}.get(tvl_trend, "#64748b")
    _trend_html  = (
        f"<span style='color:{_trend_color}; font-weight:700; margin-left:3px;' "
        f"title='7-day TVL change: {tvl_velocity:+.1f}%'>{_trend_arrow}</span>"
        if _trend_arrow else ""
    )
    tvl_html = (
        f"<span>TVL: <span style='color:#64748b; font-weight:600;'>"
        f"${tvl/1e6:.1f}M</span>{_trend_html}</span>"
        if tvl >= 1_000_000
        else (f"<span>TVL: <span style='color:#64748b; font-weight:600;'>"
              f"${tvl:,.0f}</span>{_trend_html}</span>" if tvl > 0 else "")
    )

    # APY decomposition (Upgrade #2) + expiry badge + sustainability (Feature 1)
    fee_apy    = opp.get("fee_apy", 0.0)
    reward_apy = opp.get("reward_apy", 0.0)
    apy_trend_flag = opp.get("apy_trend", "")      # Feature 14: "rising"/"falling"/"stable"/""
    apy_trend_pct  = opp.get("apy_trend_pct", 0.0)
    _apy_decomp_html = ""
    if reward_apy > 0 or fee_apy > 0:
        # Sustainability score (how much of APY is durable fee income)
        _sustain_pct  = round(fee_apy / apy * 100) if apy > 0 else (100 if fee_apy > 0 else 0)
        _sustain_color = "#10b981" if _sustain_pct >= 60 else ("#f59e0b" if _sustain_pct >= 30 else "#ef4444")

        # Incentive expiry badge
        _expiry_html = ""
        if reward_apy > 0:
            try:
                _expiry_dt   = datetime.strptime(INCENTIVE_PROGRAM.get("expires", "2026-07-01"), "%Y-%m-%d").replace(tzinfo=timezone.utc)
                _days_left   = max(0, (_expiry_dt - datetime.now(timezone.utc)).days)
                _exp_color   = "#10b981" if _days_left > 90 else ("#f59e0b" if _days_left > 30 else "#ef4444")
                _expiry_html = (
                    f"<span style='color:{_exp_color}; font-size:0.70rem; font-weight:600; "
                    f"background:rgba(255,255,255,0.04); padding:1px 6px; border-radius:4px; "
                    f"border:1px solid {_exp_color}44;' title='Incentive program expires Jul 2026'>"
                    f"⏳ {_days_left}d left</span>"
                )
            except Exception:
                _expiry_html = ""

        # APY trend flag (Feature 14)
        _trend_flag_html = ""
        if apy_trend_flag in ("rising", "falling") and abs(apy_trend_pct) >= 5:
            _tf_color = "#22c55e" if apy_trend_flag == "rising" else "#ef4444"
            _tf_icon  = "▲" if apy_trend_flag == "rising" else "▼"
            _trend_flag_html = (
                f"<span style='color:{_tf_color}; font-size:0.70rem; font-weight:600;' "
                f"title='APY trending {apy_trend_flag} over last 7 scans'>"
                f"{_tf_icon} {abs(apy_trend_pct):.0f}% {apy_trend_flag}</span>"
            )

        _apy_decomp_html = (
            f"<div style='display:flex; gap:8px; font-size:0.72rem; margin-top:6px; flex-wrap:wrap; align-items:center;'>"
            + (f"<span style='color:#64748b;'>Base fees: <span style='color:#94a3b8; font-weight:600;'>{fee_apy:.1f}%</span></span>"
               f"<span style='color:#334155;'>·</span>" if fee_apy > 0 else "")
            + (f"<span style='color:#64748b;'>Rewards: <span style='color:#a78bfa; font-weight:600;'>{reward_apy:.1f}%</span></span>"
               f"<span style='color:#334155;'>·</span>" if reward_apy > 0 else "")
            + f"<span style='color:#64748b;'>Sustainable: <span style='color:{_sustain_color}; font-weight:600;'>{_sustain_pct}%</span></span>"
            + (f"<span style='color:#334155;'>·</span>{_expiry_html}" if _expiry_html else "")
            + (f"<span style='color:#334155;'>·</span>{_trend_flag_html}" if _trend_flag_html else "")
            + f"</div>"
        )

    # Real Yield Ratio (#73) — fee revenue vs token incentive indicator
    _ry_html = ""
    if fee_apy > 0 or reward_apy > 0:
        _ry_total = fee_apy + reward_apy
        _ry_ratio = fee_apy / _ry_total if _ry_total > 0 else 0.0
        _ry_pct   = round(_ry_ratio * 100)
        if _ry_ratio >= 0.7:
            _ry_label, _ry_color = "Sustainable", "#34D399"
        elif _ry_ratio >= 0.35:
            _ry_label, _ry_color = "Partial", "#FBBF24"
        else:
            _ry_label, _ry_color = "Incentive-Driven", "#EF4444"
        _ry_html = (
            f"<span style='font-size:0.70rem;color:{_ry_color};font-weight:600;"
            f"background:rgba(0,0,0,0.2);padding:1px 6px;border-radius:4px;"
            f"border:1px solid {_ry_color}44;' title='Real Yield: {_ry_pct}% of APY is from protocol fees (not emissions)'>"
            f"⚡ Real Yield {_ry_pct}% · {_ry_label}</span>"
        )

    # Confidence bar visual (0–100)
    conf_bar_pct = f"{conf:.0f}%"
    conf_color   = "#22c55e" if conf >= 70 else ("#f59e0b" if conf >= 45 else "#ef4444")

    st.markdown(f"""<div class="opp-card" style="border-left:3px solid {color};">
<div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
<div style="flex:1;min-width:0;"><span style="font-size:0.82rem;color:#475569;margin-right:8px;">{medal}</span><span style="font-size:1.05rem;font-weight:700;color:#f1f5f9;">{proto}</span><span style="color:#334155;margin:0 6px;">·</span><span style="font-size:0.95rem;color:#94a3b8;">{pool}</span></div>
<div style="display:flex;align-items:center;gap:10px;flex-shrink:0;"><span class="grade-badge" style="background:{grade_color};color:#000;">{grade}</span><span class="{glow_cls}" style="font-size:1.8rem;font-weight:800;color:{color};letter-spacing:-1px;font-variant-numeric:tabular-nums;">{apy:.1f}%{est_tag}</span></div>
</div>
<div style="margin-top:10px;margin-bottom:2px;">
<div style="display:flex;justify-content:space-between;font-size:0.72rem;color:#475569;margin-bottom:3px;"><span>Low {lo:.1f}%</span><span style="color:#64748b;">APY Range</span><span>High {hi:.1f}%</span></div>
<div style="background:rgba(255,255,255,0.05);border-radius:4px;height:4px;position:relative;"><div style="position:absolute;left:0;top:0;height:4px;width:100%;border-radius:4px;background:linear-gradient(90deg,rgba(59,130,246,0.3),{color},rgba(245,158,11,0.4));"></div></div>
</div>
{_apy_decomp_html}
<div style="color:#94a3b8;font-size:0.91rem;margin-top:10px;line-height:1.55;">{action}</div>
<div style="display:flex;gap:20px;font-size:0.78rem;color:#475569;margin-top:12px;flex-wrap:wrap;align-items:center;">
<span><span style="color:{il_color};font-weight:700;">{il_icon}</span><span style="margin-left:3px;">Price risk: <span style="color:{il_color};font-weight:600;">{il.upper()}</span></span></span>
<span style="display:flex;align-items:center;gap:5px;">Confidence:<span style="display:inline-block;width:48px;height:5px;background:rgba(255,255,255,0.07);border-radius:3px;vertical-align:middle;margin:0 2px;overflow:hidden;"><span style="display:block;width:{conf_bar_pct};height:100%;background:{conf_color};border-radius:3px;"></span></span><span style="color:{conf_color};font-weight:600;">{conf:.0f}%</span></span>
<span>Suggested: <span style="color:#94a3b8;font-weight:600;">{alloc_str}</span></span>
{tvl_html}
{_ry_html}
</div>
</div>""", unsafe_allow_html=True)

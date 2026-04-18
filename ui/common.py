"""
ui/common.py — Shared CSS, data loaders, helpers, and render components.
Imported by every page in the Flare DeFi Model multi-page app.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import json
import html as _html
import logging
import os
import time
import subprocess
import streamlit as st
from datetime import datetime, timedelta, timezone
try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone as _tz
    class ZoneInfo:
        def __init__(self, key): self._key = key
        @staticmethod
        def utc(): return _tz.utc

logger = logging.getLogger(__name__)

# OPT-41: module-level TTL cache for get_feedback_dashboard() to avoid re-reading
# history.json + recomputing accuracy metrics on every sidebar render (~300ms saved).
_FEEDBACK_CACHE: dict = {"data": None, "expires": 0.0}


def get_user_level() -> str:
    """Return current user level: 'beginner', 'intermediate', or 'advanced'.

    Defaults to 'beginner' on first run (safe for new users).
    Call this from any page to get the level-aware display mode.
    """
    return st.session_state.get("user_level", "beginner")


# ─── Regional Color Preference (ToS #10) ─────────────────────────────────────
# Western convention: up = green, down = red.
# Some Asian markets (especially China, Japan, Korea): up = red, down = green.
# User toggle in Settings flips this globally. Defaults to Western.

_UP_GREEN = "#22c55e"
_DN_RED   = "#ef4444"

def color_up() -> str:
    """Return the color for positive/up-moves based on user preference."""
    return _DN_RED if st.session_state.get("up_is_red", False) else _UP_GREEN

def color_down() -> str:
    """Return the color for negative/down-moves based on user preference."""
    return _UP_GREEN if st.session_state.get("up_is_red", False) else _DN_RED

def color_for_delta(delta: float) -> str:
    """Convenience: pick up/down color based on the sign of a delta value."""
    if delta is None:
        return "#64748b"  # neutral grey
    try:
        d = float(delta)
    except (TypeError, ValueError):
        return "#64748b"
    if d > 0:  return color_up()
    if d < 0:  return color_down()
    return "#64748b"


# ─── Hero Number Pattern (ToS #6) ────────────────────────────────────────────
# ToS pages anchor on ONE big number per page — the thing the user came for.
# Call this right after page_setup() to establish the page's primary answer.

def render_hero_number(
    label: str,
    value: str,
    delta: str = None,
    delta_color: str = None,
    secondary_label: str = None,
    secondary_value: str = None,
) -> None:
    """Render a ToS-style hero number block at the top of a page.
    Renders "—" em-dash when value/label is None or empty.
    """
    # Guard: render em-dash placeholder instead of literal "None" string
    _label_str = str(label) if label not in (None, "") else "—"
    _value_str = str(value) if value not in (None, "") else "—"
    _dc = delta_color or "#64748b"
    if delta and delta_color is None:
        _first = next((c for c in str(delta) if c in "+-"), None)
        if _first == "+":   _dc = color_up()
        elif _first == "-": _dc = color_down()
    _delta_html = (
        f"<span style='color:{_dc}; font-size:1.0rem; font-weight:700; "
        f"margin-left:14px; font-variant-numeric:tabular-nums;'>{_html.escape(str(delta))}</span>"
        if delta not in (None, "") else ""
    )
    _sec_html = ""
    if secondary_label and secondary_value:
        _sec_html = (
            f"<div style='margin-top:6px; color:#64748b; font-size:0.82rem;'>"
            f"{_html.escape(str(secondary_label))}"
            f"<span style='color:#e2e8f0; margin-left:6px; font-weight:600;'>"
            f"{_html.escape(str(secondary_value))}</span></div>"
        )
    st.markdown(
        f"<div style='padding:8px 0 16px 0;'>"
        f"<div style='color:#64748b; font-size:0.72rem; font-weight:700; "
        f"letter-spacing:1.5px; text-transform:uppercase; margin-bottom:4px;'>"
        f"{_html.escape(_label_str)}</div>"
        f"<div style='display:flex; align-items:baseline;'>"
        f"<div style='font-size:clamp(2.0rem, 3.2vw, 2.75rem); font-weight:800; "
        f"letter-spacing:-1px; line-height:1.0; color:#f1f5f9; "
        f"font-variant-numeric:tabular-nums; font-family: \"JetBrains Mono\", monospace;'>"
        f"{_html.escape(_value_str)}</div>{_delta_html}</div>{_sec_html}</div>",
        unsafe_allow_html=True,
    )


# ─── Security Audit Logger (#15) ────────────────────────────────────────────
# Dedicated logger for security-relevant events; does NOT propagate to root.
# Write to /tmp on Linux (Streamlit Cloud mounts /mount/src via NFS — file
# creation on NFS can hang indefinitely, freezing the app before Streamlit starts).
_audit_log = logging.getLogger("defi.audit")
_audit_log.setLevel(logging.INFO)
_audit_log.propagate = False
try:
    _audit_log_path = (
        os.path.join("/tmp", "defi_audit.log")
        if sys.platform != "win32"
        else os.path.join(Path(__file__).parent.parent, "defi_audit.log")
    )
    _audit_handler = logging.FileHandler(_audit_log_path, encoding="utf-8")
    _audit_handler.setFormatter(logging.Formatter(
        "%(asctime)s [AUDIT] %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ"
    ))
    _audit_log.addHandler(_audit_handler)
except Exception:
    # Fall back to stderr if file creation fails — never block startup
    _audit_log.addHandler(logging.StreamHandler())


def audit(event: str, **ctx) -> None:
    """Log a security-relevant user action to the audit trail."""
    extra = " ".join(f"{k}={v!r}" for k, v in ctx.items())
    _audit_log.info("%s %s", event, extra)

from config import (
    RISK_PROFILES, RISK_PROFILE_NAMES, INCENTIVE_PROGRAM, HISTORY_FILE,
    POSITIONS_FILE, WALLETS_FILE, PROTOCOLS, TOKENS, FLARE_RPC_URLS,
    MONITOR_DIGEST_FILE, SCHEDULER, BRAND_NAME, BRAND_LOGO_PATH,
    PROTOCOL_AUDITS, risk_letter_grade, EMBED_MODE, GIPS_MODE,
    ANTHROPIC_ENABLED, ANTHROPIC_API_KEY, refresh_fallback_prices,
)
from utils.file_io import atomic_json_write
from utils.http import _SESSION as _http_session, coingecko_limiter


# ─── API Status Helper (#17) ──────────────────────────────────────────────────

@st.cache_data(ttl=300, max_entries=1)  # F3: memory guard — no args
def _get_api_status() -> dict:
    """Return API connectivity status dict. Cached 5 minutes to avoid startup spam."""
    try:
        from macro_feeds import validate_api_connections
        return validate_api_connections()
    except Exception:
        return {}


# ─── Live Price Loader (bypasses stale scan data) ─────────────────────────────

@st.cache_data(ttl=900, max_entries=1, show_spinner=False)
def _gen_opportunities_pdf_cached(completed_at: str) -> bytes:
    """Generate DeFi opportunities PDF once per scan, keyed by completed_at timestamp.
    Avoids re-rendering the PDF (CPU-intensive) on every sidebar rerun.
    Returns empty bytes if model_data is unavailable or pdf_export fails.
    """
    try:
        import pdf_export as _pdf_exp
        _scan = load_latest()
        _mdata = (_scan.get("models") or {}) if _scan else {}
        if not _mdata:
            return b""
        return _pdf_exp.generate_opportunities_pdf(_mdata)
    except Exception:
        return b""


@st.cache_data(ttl=120, max_entries=1)  # F3: memory guard
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

def _is_embed_mode() -> bool:
    """
    Return True when running in embed/iframe mode.
    Activated via:
      - DEFI_EMBED_MODE=1 environment variable (set at server launch), OR
      - ?embed=1 query parameter in the URL (allows per-session embedding)
    """
    if EMBED_MODE:
        return True
    try:
        params = st.query_params
        return str(params.get("embed", "0")) in ("1", "true", "True")
    except Exception:
        return False


def page_setup(title: str = "Flare DeFi Model") -> None:
    """Must be the first call in every page."""
    _embed = _is_embed_mode()
    st.set_page_config(
        page_title=title,
        page_icon="⚡",
        layout="wide",
        initial_sidebar_state="collapsed" if _embed else "expanded",
    )
    _inject_css()
    # Rename Streamlit's auto-generated "app" root nav item to "Dashboard".
    # Streamlit derives the label from app.py filename. Newer Streamlit versions
    # wrap the label in different DOM variants (<span>, <div>, data-testid
    # nodes, or raw text), so we target all of them aggressively. Also inject
    # JS as a fallback for any variant the CSS misses.
    st.markdown("""
<style>
/* Hide any rendering of the "app" label in the first nav item */
section[data-testid="stSidebarNav"] ul li:first-child a,
section[data-testid="stSidebarNav"] ul li:first-child a span,
section[data-testid="stSidebarNav"] ul li:first-child a div,
section[data-testid="stSidebarNav"] ul li:first-child a p,
[data-testid="stSidebarNav"] li:first-child a,
[data-testid="stSidebarNavLink"]:first-of-type {
    font-size: 0 !important;
    color: transparent !important;
    position: relative !important;
}
section[data-testid="stSidebarNav"] ul li:first-child a *,
[data-testid="stSidebarNav"] li:first-child a *,
[data-testid="stSidebarNavLink"]:first-of-type * {
    font-size: 0 !important;
    visibility: hidden !important;
    color: transparent !important;
}
section[data-testid="stSidebarNav"] ul li:first-child a::before,
[data-testid="stSidebarNav"] li:first-child a::before,
[data-testid="stSidebarNavLink"]:first-of-type::before {
    content: "Dashboard" !important;
    font-size: 0.9rem !important;
    visibility: visible !important;
    color: #f1f5f9 !important;
    font-weight: 500 !important;
    display: inline-block !important;
    position: absolute !important;
    left: 1rem !important;
    top: 50% !important;
    transform: translateY(-50%) !important;
}
</style>
<script>
/* JS fallback — rewrite text content directly for DOM variants CSS can't reach. */
(function renameAppLabel() {
    try {
        const nav = window.parent.document.querySelector('[data-testid="stSidebarNav"]');
        if (!nav) return;
        const firstLink = nav.querySelector('li:first-child a');
        if (!firstLink) return;
        const textNodes = firstLink.querySelectorAll('span, div, p');
        textNodes.forEach(n => {
            if (n.textContent && n.textContent.trim().toLowerCase() === 'app') {
                n.textContent = 'Dashboard';
            }
        });
    } catch (e) { /* cross-origin or DOM not ready — ignore */ }
})();
</script>""", unsafe_allow_html=True)
    if _embed:
        # Embed mode: hide sidebar, navigation arrows, top toolbar, and Streamlit chrome.
        # This gives a clean iframe-embeddable surface for advisor platforms.
        st.markdown("""
<style>
[data-testid="stSidebar"],
[data-testid="collapsedControl"],
[data-testid="stSidebarNavLink"],
section[data-testid="stSidebarNav"],
header[data-testid="stHeader"],
#MainMenu, footer { display: none !important; visibility: hidden !important; }
.block-container { padding-top: 0.5rem !important; }
</style>""", unsafe_allow_html=True)
    if GIPS_MODE:
        st.markdown("""
<div style='background:#1e293b;border:1px solid #334155;border-left:3px solid #00d4aa;
border-radius:6px;padding:8px 16px;margin-bottom:12px;font-size:0.85rem;color:#94a3b8;'>
<strong style='color:#00d4aa;'>GIPS Disclosure</strong> — Performance figures shown are
time-weighted suggested allocations (TWR). Past DeFi yield rates are not a guarantee of
future performance. All APY data is sourced from live protocol feeds and is provided for
informational purposes only. Not investment advice. Consult a licensed advisor.
</div>""", unsafe_allow_html=True)


@st.cache_data(ttl=86400, max_entries=2)
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
    _theme_key = "light" if _is_light else "dark"

    # PERF: CSS strings are 16-23 KB. Guard skips re-injection on reruns of the
    # SAME page with the SAME theme. The guard is keyed by (page_path, theme) so
    # navigating to a new page always re-injects (Streamlit clears the DOM on
    # each page navigation — the previous page's CSS is gone).
    _page_key = None
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx as _get_ctx
        _ctx = _get_ctx()
        if _ctx is not None:
            _page_key = getattr(_ctx, "main_script_path", None)
    except Exception:
        pass

    # Toggle state is part of the guard key: when the user flips Focus Mode or
    # Compact Sidebar, the CSS MUST re-inject on the next rerun so the new
    # overlay takes effect. Without this, toggles appeared to silently fail.
    _ul_default = st.session_state.get("user_level", "beginner") == "advanced"
    _toggle_state = (
        bool(st.session_state.get("focus_mode", False)),
        bool(st.session_state.get("compact_sidebar", _ul_default)),
    )
    if (
        _page_key is not None  # only guard when we can identify the page
        and st.session_state.get("_defi_css_injected")
        and st.session_state.get("_defi_css_theme_last") == _theme_key
        and st.session_state.get("_defi_css_page_last") == _page_key
        and st.session_state.get("_defi_css_toggles_last") == _toggle_state
    ):
        return

    if _is_light:
        # ── LIGHT MODE: complete standalone CSS (no dark CSS injected at all) ──
        st.markdown(_build_css("light"), unsafe_allow_html=True)
    else:
        # ── DARK MODE: complete standalone CSS ────────────────────────────────
        st.markdown(_build_css("dark"), unsafe_allow_html=True)

    # ── Focus Mode (ToS #4) — hide educational scaffolds, maximize data ───
    if st.session_state.get("focus_mode", False):
        st.markdown("""
<style>
    /* Hide 'what does this mean for me' Beginner helpers when in Focus Mode */
    .beginner-help, .what-this-means-box { display: none !important; }
    /* Hide Beginner welcome banner in Focus Mode */
    [data-testid="stAlert"].beginner-banner { display: none !important; }
    /* Tighten block-container padding for more data density */
    .block-container { padding-top: 0.8rem !important; padding-bottom: 1.5rem !important; }
    /* Shrink section header margins */
    .section-header { margin-top: 4px !important; margin-bottom: 10px !important; }
</style>""", unsafe_allow_html=True)

    # ── Compact Sidebar overlay (ToS #1) ───────────────────────────────────
    # Per Q2 tiered default: Beginner = off (labels on), Intermediate = off,
    # Advanced = default on. Explicit user preference overrides.
    _compact = st.session_state.get("compact_sidebar", _ul_default)
    if _compact:
        st.markdown("""
<style>
    /* Icon-only sidebar nav — compact mode (ToS #1) */
    [data-testid="stSidebarNav"] a span:not(:first-child) { display: none !important; }
    [data-testid="stSidebarNav"] a { padding: 8px 10px !important; justify-content: center; }
    [data-testid="stSidebar"] { min-width: 90px !important; max-width: 110px !important; }
    [data-testid="stSidebar"] button, [data-testid="stSidebar"] .stTextInput,
    [data-testid="stSidebar"] label { font-size: 0.72rem !important; }
    [data-testid="stSidebarNav"] a:hover::after {
        content: attr(aria-label);
        position: absolute; left: 100%; margin-left: 8px;
        background: #1e293b; color: #f1f5f9;
        padding: 4px 10px; border-radius: 6px;
        font-size: 0.8rem; white-space: nowrap;
        box-shadow: 0 2px 8px rgba(0,0,0,0.4);
        pointer-events: none;
    }
</style>""", unsafe_allow_html=True)

    st.session_state["_defi_css_injected"] = True
    st.session_state["_defi_css_theme_last"] = _theme_key
    st.session_state["_defi_css_page_last"] = _page_key
    st.session_state["_defi_css_toggles_last"] = _toggle_state


# ── CSS constant strings (extracted for module-level caching, upgrade #32) ────
_CSS_LIGHT = """
<style>
    /* ── Google Fonts — Inter (UI) + JetBrains Mono (data) ───────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap');

    /* ── Chrome Reset ─────────────────────────────────────────────────── */
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

    /* ── Base / App Shell ─────────────────────────────────────────────── */
    .stApp, .main { background: #f1f5f9 !important; color: #1e293b !important; font-family: 'Inter', system-ui, sans-serif !important; }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }

    /* ── Typography ───────────────────────────────────────────────────── */
    h1 { font-size: clamp(1.3rem, 1.4vw, 1.75rem) !important; font-weight: 800 !important; letter-spacing: -0.5px; color: #0f172a !important; }
    h2 { font-size: clamp(1.0rem, 1.1vw, 1.2rem) !important; font-weight: 600 !important; color: #1e293b !important; letter-spacing: -0.2px; }
    h3 { font-size: clamp(0.85rem, 0.9vw, 1.0rem) !important; font-weight: 600 !important; color: #475569 !important; text-transform: uppercase; letter-spacing: 0.8px; }

    /* ── Custom Scrollbar ─────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #dde3ee; }
    ::-webkit-scrollbar-thumb { background: #b8c4d6; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #94a3b8; }

    /* ── Metric Cards ─────────────────────────────────────────────────── */
    .metric-card {
        background: rgba(255,255,255,0.97);
        border-radius: 10px; padding: 14px 18px; margin-bottom: 10px;
        border: 1px solid rgba(0,0,0,0.08); border-left: 3px solid #1e3a5f;
        box-shadow: 0 2px 12px rgba(0,0,0,0.06), inset 0 1px 0 rgba(255,255,255,0.8);
        transition: border-color 0.22s ease, box-shadow 0.22s ease, transform 0.22s ease;
    }
    .metric-card:hover { box-shadow: 0 8px 28px rgba(0,0,0,0.10); transform: translateY(-2px); }
    .card-green  { border-left-color: #22c55e; }
    .card-blue   { border-left-color: #00d4aa; }
    .card-orange { border-left-color: #f59e0b; }
    .card-red    { border-left-color: #ef4444; }
    .card-violet { border-left-color: #8b5cf6; }
    .big-number { font-size: 1.45rem; font-weight: 800; letter-spacing: -0.5px; line-height: 1.1; color: #0f172a; font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono', monospace; }
    .label { font-size: clamp(0.58rem, 0.65vw, 0.65rem); color: #475569; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 4px; }

    /* ── Opportunity Cards ────────────────────────────────────────────── */
    .opp-card {
        background: rgba(255,255,255,0.95);
        border-radius: 10px; padding: 10px 14px; margin-bottom: 7px;
        border: 1px solid rgba(0,0,0,0.07);
        box-shadow: 0 2px 10px rgba(0,0,0,0.06);
        transition: box-shadow 0.22s ease, transform 0.22s ease;
    }
    .opp-card:hover { transform: translateY(-2px); box-shadow: 0 8px 28px rgba(0,0,0,0.10); }

    /* ── Card base text ───────────────────────────────────────────────── */
    .metric-card, .opp-card, .arb-tag, .warn-box, .price-chip { color: #1e293b; }

    /* ── Arbitrage Tag ────────────────────────────────────────────────── */
    .arb-tag {
        background: rgba(16,185,129,0.07); border-radius: 10px;
        padding: 10px 14px; margin-bottom: 7px;
        border: 1px solid rgba(16,185,129,0.22);
        transition: border-color 0.2s, box-shadow 0.2s;
    }
    .arb-tag:hover { border-color: rgba(16,185,129,0.35); box-shadow: 0 4px 20px rgba(16,185,129,0.07); }

    /* ── Warning Box ──────────────────────────────────────────────────── */
    .warn-box { background: rgba(245,158,11,0.08); border-radius: 10px; padding: 9px 13px; border: 1px solid rgba(245,158,11,0.20); margin-bottom: 9px; }

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
    .section-header { font-size: 0.85rem; font-weight: 700; color: #1e293b; text-transform: uppercase; letter-spacing: 1.4px; padding-bottom: 8px; border-bottom: 1px solid rgba(0,212,170,0.3); margin-bottom: 16px; margin-top: 8px; }

    /* ── Expander label — same size as section-header ─────────────────── */
    [data-testid="stExpander"] summary p { font-size: 0.85rem !important; }

    /* ── Global base font: 0.85rem for all interactive + body elements ── */
    [data-testid="stMarkdownContainer"] div,
    [data-testid="stMarkdownContainer"] span { font-size: 0.85rem; }
    [data-testid="stMain"] label, [data-testid="stMain"] label p, [data-testid="stMain"] label span { font-size: 0.85rem !important; }
    [data-testid="stMain"] input, [data-testid="stMain"] textarea { font-size: 0.85rem !important; }
    [data-testid="stMain"] [data-baseweb="select"] span, [data-testid="stMain"] [data-baseweb="select"] div, [data-testid="stMain"] [data-baseweb="select"] input { font-size: 0.85rem !important; }
    [data-testid="stMain"] [role="listbox"] li, [data-testid="stMain"] [role="option"], [data-testid="stMain"] [role="option"] * { font-size: 0.85rem !important; }
    [data-testid="stMain"] button p, [data-testid="stMain"] button span, [data-testid="stFormSubmitButton"] button p { font-size: 0.85rem !important; }
    [data-testid="stMain"] [data-testid="stTab"] p, [data-testid="stMain"] [data-testid="stTab"] span { font-size: 0.85rem !important; }
    [data-testid="stMain"] p { font-size: 0.85rem !important; }
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] label p, [data-testid="stSidebar"] label span { font-size: 0.85rem !important; }
    [data-testid="stSidebar"] p { font-size: 0.85rem !important; }
    [data-testid="stCaptionContainer"] p, [data-testid="stMain"] small { font-size: 0.75rem !important; }

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
        font-weight: 700 !important; font-size:0.85rem !important; letter-spacing: 0.3px !important;
        background: #ffffff !important;
        color: #1e293b !important;
        transition: background 0.15s, border-color 0.15s, box-shadow 0.15s, transform 0.1s !important;
    }
    div[data-testid="stButton"] > button:hover,
    button[kind="secondary"]:hover { border-color: rgba(0,212,170,0.4) !important; box-shadow: 0 0 12px rgba(0,212,170,0.15) !important; transform: translateY(-1px) !important; color: #0f172a !important; background: #f0fefb !important; }
    div[data-testid="stButton"] > button:active { transform: translateY(0) !important; }

    /* ── Number input +/- step buttons ───────────────────────────────── */
    [data-testid="stNumberInput"] button { background: #f1f5f9 !important; color: #1e293b !important; border-color: rgba(0,0,0,0.10) !important; }

    /* ── Dataframes ───────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    [data-testid="stDataFrame"] table { background: rgba(255,255,255,0.97) !important; }
    [data-testid="stDataFrame"] thead tr th { background: rgba(241,245,249,0.98) !important; color: #64748b !important; font-size:0.85rem !important; letter-spacing: 0.9px !important; text-transform: uppercase !important; border-bottom: 1px solid rgba(0,0,0,0.06) !important; }
    [data-testid="stDataFrame"] tbody tr:hover td { background: rgba(0,212,170,0.04) !important; }

    /* ── Price Chip ───────────────────────────────────────────────────── */
    .price-chip { text-align: center; padding: 9px 8px; background: rgba(255,255,255,0.97); border-radius: 10px; border: 1px solid rgba(0,0,0,0.08); box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s; }
    .price-chip:hover { box-shadow: 0 6px 20px rgba(0,0,0,0.10); transform: translateY(-1px); }

    /* ── Tabs ─────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tab"] { font-size:0.85rem; font-weight: 600; color: #64748b; transition: color 0.15s; }
    [data-testid="stTabs"] [role="tab"]:hover { color: #475569; }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #00d4aa !important; }
    [data-testid="stTabs"] [role="tabpanel"] { padding-top: 16px; }

    /* ── Inputs ───────────────────────────────────────────────────────── */
    [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input {
        background: rgba(255,255,255,0.97) !important; border: 1px solid rgba(0,0,0,0.10) !important;
        border-radius: 9px !important; color: #1e293b !important; transition: border-color 0.15s, box-shadow 0.15s;
    }
    [data-testid="stNumberInput"] input:focus, [data-testid="stTextInput"] input:focus {
        border-color: rgba(0,212,170,0.45) !important; box-shadow: 0 0 0 3px rgba(0,212,170,0.10) !important;
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
        .big-number { font-size: 1.1rem !important; } h1 { font-size: 1.4rem !important; }
        .metric-card, .opp-card { padding: 10px 12px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
        .price-chip { padding: 10px 8px; }
        [data-testid="stTabs"] [role="tab"] { font-size: 0.75rem; }
        /* 44px minimum tap targets for mobile accessibility */
        div[data-testid="stButton"] > button { min-height: 44px !important; }
        [data-testid="stRadio"] label { min-height: 44px !important; padding: 10px 0 !important; }
        [data-testid="stCheckbox"] label { min-height: 44px !important; padding: 10px 0 !important; }
        [data-testid="stToggle"] label { min-height: 44px !important; }
        [data-baseweb="select"] { min-height: 44px !important; }
        /* Stack columns on mobile */
        [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
        [data-testid="stColumn"] { min-width: 100% !important; }
    }

    /* ── Flip dark inline colors → accessible dark equivalents ───────── */
    :is(div,span,p,a)[style*="color:#f1f5f9"]  { color: #0f172a !important; }
    :is(div,span,p,a)[style*="color:#e2e8f0"]  { color: #1e293b !important; }
    :is(div,span,p,a)[style*="color:#cbd5e1"]  { color: #334155 !important; }
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
    :is(div,span,p,a)[style*="color:#00d4aa"]  { color: #1d4ed8 !important; }
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
    [data-baseweb="menu-item"]:hover { background-color: rgba(0,212,170,0.07) !important; }

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
    /* ── Google Fonts — Inter (UI) + JetBrains Mono (data) ───────────── */
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;700&display=swap');

    /* ── Chrome Reset ─────────────────────────────────────────────────── */
    #MainMenu, footer, [data-testid="stToolbar"] { visibility: hidden; }

    /* ── Base / App Shell ─────────────────────────────────────────────── */
    .stApp, .main { background: #0d0e14 !important; color: #e2e8f0 !important; font-family: 'Inter', system-ui, sans-serif !important; }
    .block-container { padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1200px; }

    /* ── Typography ───────────────────────────────────────────────────── */
    h1 { font-size: clamp(1.3rem, 1.4vw, 1.75rem) !important; font-weight: 800 !important; letter-spacing: -0.5px; color: #e2e8f0 !important; }
    h2 { font-size: clamp(1.0rem, 1.1vw, 1.2rem) !important; font-weight: 600 !important; color: #cbd5e1 !important; letter-spacing: -0.2px; }
    h3 { font-size: clamp(0.85rem, 0.9vw, 1.0rem) !important; font-weight: 600 !important; color: #94a3b8 !important; text-transform: uppercase; letter-spacing: 0.8px; }

    /* ── Custom Scrollbar ─────────────────────────────────────────────── */
    ::-webkit-scrollbar { width: 5px; height: 5px; }
    ::-webkit-scrollbar-track { background: #13141c; }
    ::-webkit-scrollbar-thumb { background: #2d2e45; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #3d3e5a; }

    /* ── Glassmorphism Metric Cards ───────────────────────────────────── */
    .metric-card {
        background: rgba(17,24,39,0.95);
        backdrop-filter: blur(16px) saturate(180%);
        -webkit-backdrop-filter: blur(16px) saturate(180%);
        border-radius: 10px; padding: 14px 18px; margin-bottom: 10px;
        border: 1px solid rgba(255,255,255,0.08); border-left: 3px solid #1e3a5f;
        box-shadow: 0 4px 24px rgba(0,0,0,0.4), inset 0 1px 0 rgba(255,255,255,0.05);
        transition: border-color 0.22s cubic-bezier(0.4,0,0.2,1),
                    box-shadow 0.22s cubic-bezier(0.4,0,0.2,1),
                    transform 0.22s cubic-bezier(0.4,0,0.2,1);
    }
    .metric-card:hover { border-color: rgba(255,255,255,0.14); box-shadow: 0 10px 36px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.08); transform: translateY(-2px); }
    .card-green  { border-left-color: #22c55e; }
    .card-blue   { border-left-color: #00d4aa; }
    .card-orange { border-left-color: #f59e0b; }
    .card-red    { border-left-color: #ef4444; }
    .card-violet { border-left-color: #8b5cf6; }
    .big-number { font-size: 1.2rem; font-weight: 800; letter-spacing: -0.5px; line-height: 1.1; color: #f1f5f9; font-variant-numeric: tabular-nums; font-family: 'JetBrains Mono', monospace; }
    .label { font-size: clamp(0.58rem, 0.65vw, 0.65rem); color: #94a3b8; text-transform: uppercase; letter-spacing: 1.5px; margin-bottom: 4px; }

    /* ── Opportunity Cards ────────────────────────────────────────────── */
    .opp-card {
        background: rgba(17,24,39,0.92);
        backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
        border-radius: 10px; padding: 10px 14px; margin-bottom: 7px;
        border: 1px solid rgba(255,255,255,0.07);
        box-shadow: 0 2px 16px rgba(0,0,0,0.35), inset 0 1px 0 rgba(255,255,255,0.04);
        transition: border-color 0.22s cubic-bezier(0.4,0,0.2,1), transform 0.22s cubic-bezier(0.4,0,0.2,1), box-shadow 0.22s cubic-bezier(0.4,0,0.2,1);
    }
    .opp-card:hover { border-color: rgba(255,255,255,0.13); transform: translateY(-2px); box-shadow: 0 10px 36px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.07); }

    /* ── Arbitrage Tag ────────────────────────────────────────────────── */
    .arb-tag { background: rgba(16,185,129,0.04); border-radius: 10px; padding: 10px 14px; margin-bottom: 7px; border: 1px solid rgba(16,185,129,0.13); transition: border-color 0.2s, box-shadow 0.2s; }
    .arb-tag:hover { border-color: rgba(16,185,129,0.26); box-shadow: 0 4px 20px rgba(16,185,129,0.07); }

    /* ── Warning Box ──────────────────────────────────────────────────── */
    .warn-box { background: rgba(245,158,11,0.05); border-radius: 10px; padding: 9px 13px; border: 1px solid rgba(245,158,11,0.15); margin-bottom: 9px; box-shadow: 0 2px 12px rgba(245,158,11,0.05); }

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
    .section-header { font-size: 0.85rem; font-weight: 700; color: #e2e8f0; text-transform: uppercase; letter-spacing: 1.4px; padding-bottom: 8px; border-bottom: 1px solid rgba(0,212,170,0.35); margin-bottom: 16px; margin-top: 8px; }

    /* ── Expander label — same size as section-header ─────────────────── */
    [data-testid="stExpander"] summary p { font-size: 0.85rem !important; }

    /* ── Global base font: 0.85rem for all interactive + body elements ── */
    [data-testid="stMarkdownContainer"] div,
    [data-testid="stMarkdownContainer"] span { font-size: 0.85rem; }
    [data-testid="stMain"] label, [data-testid="stMain"] label p, [data-testid="stMain"] label span { font-size: 0.85rem !important; }
    [data-testid="stMain"] input, [data-testid="stMain"] textarea { font-size: 0.85rem !important; }
    [data-testid="stMain"] [data-baseweb="select"] span, [data-testid="stMain"] [data-baseweb="select"] div, [data-testid="stMain"] [data-baseweb="select"] input { font-size: 0.85rem !important; }
    [data-testid="stMain"] [role="listbox"] li, [data-testid="stMain"] [role="option"], [data-testid="stMain"] [role="option"] * { font-size: 0.85rem !important; }
    [data-testid="stMain"] button p, [data-testid="stMain"] button span, [data-testid="stFormSubmitButton"] button p { font-size: 0.85rem !important; }
    [data-testid="stMain"] [data-testid="stTab"] p, [data-testid="stMain"] [data-testid="stTab"] span { font-size: 0.85rem !important; }
    [data-testid="stMain"] p { font-size: 0.85rem !important; }
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] label p, [data-testid="stSidebar"] label span { font-size: 0.85rem !important; }
    [data-testid="stSidebar"] p { font-size: 0.85rem !important; }
    [data-testid="stCaptionContainer"] p, [data-testid="stMain"] small { font-size: 0.75rem !important; }

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
        font-weight: 700; font-size:0.85rem; letter-spacing: 0.3px;
        background: rgba(17,24,39,0.85); color: #cbd5e1;
        transition: background 0.15s, border-color 0.15s, box-shadow 0.15s, transform 0.1s;
    }
    div[data-testid="stButton"] > button:hover { border-color: rgba(0,212,170,0.45); box-shadow: 0 0 16px rgba(0,212,170,0.18); transform: translateY(-1px); color: #f1f5f9; }
    div[data-testid="stButton"] > button:active { transform: translateY(0); }

    /* ── Dataframes ───────────────────────────────────────────────────── */
    [data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
    [data-testid="stDataFrame"] table { background: rgba(13,14,20,0.96) !important; }
    [data-testid="stDataFrame"] thead tr th { background: rgba(17,24,39,0.98) !important; color: #94a3b8 !important; font-size:0.85rem !important; letter-spacing: 0.9px !important; text-transform: uppercase !important; border-bottom: 1px solid rgba(255,255,255,0.06) !important; }
    [data-testid="stDataFrame"] tbody tr:hover td { background: rgba(0,212,170,0.06) !important; }

    /* ── Price Chip ───────────────────────────────────────────────────── */
    .price-chip { text-align: center; padding: 9px 8px; background: rgba(17,24,39,0.94); backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px); border-radius: 10px; border: 1px solid rgba(255,255,255,0.07); box-shadow: 0 2px 12px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.04); transition: border-color 0.2s, box-shadow 0.2s, transform 0.15s; }
    .price-chip:hover { border-color: rgba(255,255,255,0.13); box-shadow: 0 6px 24px rgba(0,0,0,0.45); transform: translateY(-1px); }

    /* ── Tabs ─────────────────────────────────────────────────────────── */
    [data-testid="stTabs"] [role="tab"] { font-size:0.85rem; font-weight: 600; color: #64748b; transition: color 0.15s; }
    [data-testid="stTabs"] [role="tab"]:hover { color: #94a3b8; }
    [data-testid="stTabs"] [role="tab"][aria-selected="true"] { color: #00d4aa; }
    [data-testid="stTabs"] [role="tabpanel"] { padding-top: 16px; }

    /* ── Inputs ───────────────────────────────────────────────────────── */
    [data-testid="stNumberInput"] input, [data-testid="stTextInput"] input {
        background: rgba(17,24,39,0.88) !important; border: 1px solid rgba(255,255,255,0.09) !important;
        border-radius: 9px !important; color: #e2e8f0 !important;
        transition: border-color 0.15s, box-shadow 0.15s;
    }
    [data-testid="stNumberInput"] input:focus, [data-testid="stTextInput"] input:focus {
        border-color: rgba(0,212,170,0.45) !important; box-shadow: 0 0 0 3px rgba(0,212,170,0.12) !important;
    }

    /* ── Expanders ────────────────────────────────────────────────────── */
    [data-testid="stExpander"] { border: 1px solid rgba(255,255,255,0.07) !important; border-radius: 12px !important; background: rgba(13,14,20,0.72) !important; transition: border-color 0.2s; }
    [data-testid="stExpander"]:hover { border-color: rgba(255,255,255,0.11) !important; }

    /* ── Radio ────────────────────────────────────────────────────────── */
    [data-testid="stRadio"] label span { color: #94a3b8 !important; }
    [data-testid="stRadio"] [data-testid="stMarkdownContainer"] p { color: #94a3b8 !important; }

    /* ── Alert Boxes ──────────────────────────────────────────────────── */
    [data-testid="stAlert"] { background: rgba(17,24,39,0.92) !important; border-radius: 12px !important; border-left-width: 3px !important; }
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] p,
    [data-testid="stAlert"] [data-testid="stMarkdownContainer"] li { color: #94a3b8 !important; }

    /* ── Misc ─────────────────────────────────────────────────────────── */
    .mono-number { font-variant-numeric: tabular-nums; font-feature-settings: "tnum"; }
    .rank-1 { color: #f59e0b !important; }
    .rank-2 { color: #94a3b8 !important; }
    .rank-3 { color: #b45309 !important; }

    /* ── Mobile ───────────────────────────────────────────────────────── */
    @media (max-width: 768px) {
        .big-number { font-size: 1.1rem !important; } h1 { font-size: 1.4rem !important; }
        .metric-card, .opp-card { padding: 10px 12px; }
        .block-container { padding-left: 0.5rem; padding-right: 0.5rem; }
        .price-chip { padding: 10px 8px; }
        [data-testid="stTabs"] [role="tab"] { font-size: 0.75rem; }
        /* 44px minimum tap targets for mobile accessibility */
        div[data-testid="stButton"] > button { min-height: 44px !important; }
        [data-testid="stRadio"] label { min-height: 44px !important; padding: 10px 0 !important; }
        [data-testid="stCheckbox"] label { min-height: 44px !important; padding: 10px 0 !important; }
        [data-testid="stToggle"] label { min-height: 44px !important; }
        [data-baseweb="select"] { min-height: 44px !important; }
        /* Stack columns on mobile */
        [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; }
        [data-testid="stColumn"] { min-width: 100% !important; }
    }

    /* ── Dark-mode legibility lift ────────────────────────────────────── */
    :is(div,span,p,a)[style*="color:#334155"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#475569"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#64748b"] { color: #94a3b8 !important; }
    :is(div,span,p,a)[style*="color:#1e293b"] { color: #64748b !important; }
    :is(div,span,p,a)[style*="color:#0f172a"] { color: #64748b !important; }
</style>
"""


# ─── Scan poll fragment (OPT-43) ─────────────────────────────────────────────
# Defined at module level so Streamlit can track it across renders.
# Called from render_sidebar() when _scanning=True.
# run_every=0.5 auto-reruns only this fragment (not the full page) on each tick,
# avoiding the old time.sleep(0.5) + full st.rerun() pattern.

@st.fragment(run_every=2)
def _scan_progress_fragment() -> None:
    """Poll for scan completion every 2 s using a Streamlit fragment.

    This fragment is ALWAYS rendered in the sidebar (not gated on _scanning) so
    its DOM element always exists and Streamlit never logs "fragment does not exist"
    warnings after a full app rerun.  The guard below makes it a cheap no-op when
    no scan is running.

    When the scan finishes: clears the history cache and triggers a full rerun
    so all pages see fresh data.  When the deadline expires, marks scan as done.
    """
    if not st.session_state.get("_scanning"):
        return  # no-op — fragment DOM element stays alive, no polling work done
    try:
        with open(HISTORY_FILE, encoding="utf-8") as _ff:
            _hist_ts = (json.load(_ff).get("latest") or {}).get("completed_at") or ""
    except Exception:
        _hist_ts = ""
    if _hist_ts and _hist_ts != st.session_state.get("_scan_baseline", ""):
        st.session_state._scanning = False
        # Clear ALL scan-derived caches so every calculation refreshes from the new data:
        # 1. history file cache (opportunity APYs, model results, arbitrage)
        # 2. process-level history singleton
        # 3. AI feedback cache (model weights update after every scan)
        try:
            _load_history_file.clear()
            _get_history_cache()["data"] = None
            _FEEDBACK_CACHE["data"] = None
        except Exception:
            pass
        st.rerun()
    elif time.time() < st.session_state.get("_scan_deadline", 0):
        st.caption("⏳ Scanning… auto-reloading when done.")
    else:
        st.session_state._scanning = False
        st.caption("Scan timed out — click ↺ Reload.")


# ─── Sidebar ──────────────────────────────────────────────────────────────────

def render_sidebar() -> dict:
    """
    Persistent sidebar: scan status, portfolio size, risk profile, refresh.
    Returns dict with keys: profile, profile_cfg, color, weight, feedback, portfolio_size.
    """
    # ─── Refresh FALLBACK_PRICES once per session from CoinGecko ─────────────
    # Fires in a daemon thread so it never blocks the initial render.
    # The in-memory FALLBACK_PRICES dict is updated in-place by the thread,
    # so downstream callers see live prices on the next interaction.
    if not st.session_state.get("_fallback_prices_refreshed"):
        st.session_state["_fallback_prices_refreshed"] = True
        import threading as _threading
        _threading.Thread(target=refresh_fallback_prices, daemon=True).start()

    # ─── 5-minute auto-refresh to keep data live ─────────────────────────────
    _now = time.time()
    if "last_auto_refresh" not in st.session_state:
        st.session_state.last_auto_refresh = _now
    elif _now - st.session_state.last_auto_refresh > 300:
        st.session_state.last_auto_refresh = _now
        # OPT-44: targeted cache clear — only invalidate data that ages quickly.
        # Do NOT clear _build_css (24h TTL), _get_api_status (5-min own TTL),
        # or load_wallets/load_positions (user data that doesn't change on auto-refresh).
        _load_history_file.clear()
        _get_history_cache()["data"] = None
        _FEEDBACK_CACHE["data"] = None   # model weights updated by background scans
        load_live_prices.clear()
        st.rerun()

    with st.sidebar:
        _native_light = False
        try:
            _native_light = st.context.theme.base == "light"
        except Exception:
            pass
        _is_light = _native_light or st.session_state.get("_theme") == "light"
        # ── Brand row (full-width) — 2-line layout, no wrapping per line ──────
        # Line 1: "⚡ Family Office"   Line 2: "DeFi Intelligence" (subtitle)
        if BRAND_LOGO_PATH and Path(BRAND_LOGO_PATH).exists():
            st.image(BRAND_LOGO_PATH, width=140)
        else:
            # If BRAND_NAME contains a middle-dot or em-dash separator, split it
            # into a 2-line layout (before / after) so the full name always fits
            # without ellipsis truncation on the narrow sidebar.
            if BRAND_NAME and (" · " in BRAND_NAME or " — " in BRAND_NAME):
                _sep = " · " if " · " in BRAND_NAME else " — "
                _head_raw, _sub_raw = BRAND_NAME.split(_sep, 1)
                _header_text = _html.escape(_head_raw.strip())
                _subtitle    = _html.escape(_sub_raw.strip())
            else:
                _header_text = _html.escape(str(BRAND_NAME)) if BRAND_NAME else "⚡ Family Office"
                _subtitle    = "" if BRAND_NAME else "DeFi Intelligence"
            _sub_color = "#64748b" if not _is_light else "#475569"
            st.markdown(
                f"<div style='font-size:0.95rem; font-weight:800; line-height:1.2; "
                f"background: linear-gradient(90deg, #00d4aa, #00d4aa); "
                f"-webkit-background-clip: text; -webkit-text-fill-color: transparent; "
                f"background-clip: text; letter-spacing:-0.2px; margin:2px 0 0; "
                f"white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
                f"{_header_text}</div>"
                + (f"<div style='font-size:0.75rem; color:{_sub_color}; "
                   f"font-weight:600; letter-spacing:0.4px; margin:1px 0 6px; "
                   f"white-space:nowrap; overflow:hidden; text-overflow:ellipsis;'>"
                   f"{_subtitle}</div>" if _subtitle else ""),
                unsafe_allow_html=True,
            )
        # ── Theme toggle — dedicated row below brand, full-width with label ────
        _tt_label = "☀ Light mode" if not _is_light else "🌙 Dark mode"
        if st.button(_tt_label, key="_theme_toggle",
                     help="Switch theme" + (" (currently light)" if _is_light else " (currently dark)"),
                     width='stretch'):
            st.session_state["_theme"] = "dark" if _is_light else "light"
            st.rerun()

        latest    = load_latest()
        last_scan = latest.get("completed_at") or latest.get("run_id")

        # Determine data freshness (used both for auto-scan trigger and dot indicator)
        is_fresh = False
        _scan_age_secs = None
        if last_scan:
            try:
                scan_dt = datetime.fromisoformat(last_scan.replace("Z", "+00:00"))
                if scan_dt.tzinfo is None:
                    scan_dt = scan_dt.replace(tzinfo=timezone.utc)
                _scan_age_secs = (datetime.now(timezone.utc) - scan_dt).total_seconds()
                is_fresh = _scan_age_secs < 3600
            except Exception:
                pass

        # ── Auto-scan: no data OR data is stale (> 4 hours old) ──────────────
        # Streamlit Cloud's free tier puts apps to sleep, so the background
        # scheduler stops running. This ensures data is refreshed on every
        # page open when the cached data is more than 4 hours old.
        _data_stale = (not last_scan) or (_scan_age_secs is not None and _scan_age_secs > 14400)
        if _data_stale and not st.session_state.get("_auto_scan_triggered"):
            st.session_state["_auto_scan_triggered"] = True
            if not st.session_state.get("_scanning"):
                try:
                    _sched_path = str(Path(__file__).parent.parent / "scheduler.py")
                    subprocess.Popen(
                        [sys.executable, _sched_path, "--now"],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                    )
                    st.session_state._scanning = True
                    st.session_state._scan_deadline = time.time() + 300
                    st.session_state._scan_baseline = ""
                except Exception:
                    pass  # user can click ▶ Scan manually
        dot_html = "<span class='live-dot'></span>" if is_fresh else "<span class='stale-dot'></span>"
        st.markdown(
            f"<div style='font-size:0.85rem; color:#475569; line-height:1.5; margin-bottom:4px;'>"
            f"{dot_html}"
            f"<span style='color:#94a3b8'>{_ts_fmt(last_scan) if last_scan else 'No scan yet'}</span>"
            f" · Next <span style='color:#64748b'>{_next_scan()}</span></div>",
            unsafe_allow_html=True,
        )

        # ── Incentive Countdown Widget (Agent Priority 8) ────────────────────
        try:
            _exp_dt    = datetime.strptime(INCENTIVE_PROGRAM.get("expires", "2026-07-01"), "%Y-%m-%d")
            _cnt_days  = max(0, (_exp_dt - datetime.now()).days)
            if _cnt_days <= 90:
                _cnt_color = "#ef4444" if _cnt_days <= 30 else "#f59e0b"
                _cnt_bg    = f"{_cnt_color}1f"   # ~12% opacity bg (higher contrast than 0.15 black)
                _cnt_icon  = "🔴" if _cnt_days == 0 else ("⚠" if _cnt_days <= 30 else "⏳")
                _cnt_msg   = "Rewards ended" if _cnt_days == 0 else f"Rewards expire in {_cnt_days}d"
                _exp_str = INCENTIVE_PROGRAM.get("expires", "2026-07-01")
                st.markdown(
                    f"<div style='background:{_cnt_bg}; border:1px solid {_cnt_color}aa; "
                    f"border-left:4px solid {_cnt_color}; border-radius:6px; "
                    f"padding:6px 10px; margin:6px 0; font-size:0.82rem; color:{_cnt_color}; "
                    f"font-weight:700; line-height:1.35;' "
                    f"title='RFLR/SPRK incentive program expires {_exp_str}. Base fee yield continues after this date.'>"
                    f"{_cnt_icon} {_cnt_msg}<br>"
                    f"<span style='font-weight:500; font-size:0.75rem; opacity:0.85;'>Only base yield after</span></div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

        # ── Persistent Agent Status Badge (Item 17) ───────────────────────────
        try:
            from agents.agent_runner import get_state as _get_agent_state
            _ag_state = _get_agent_state()
            _ag_running = _ag_state.get("running", False)
            _ag_estop   = _ag_state.get("emergency_stop_active", False)
            _ag_mode    = _ag_state.get("mode", "PAPER")
            if _ag_estop:
                _ag_badge_color, _ag_badge_text = "#ef4444", "⛔ EMERGENCY STOP"
            elif _ag_running:
                _ag_badge_color, _ag_badge_text = "#22c55e", f"● Agent LIVE ({_ag_mode})"
            else:
                _ag_badge_color, _ag_badge_text = "#64748b", f"○ Agent PAUSED ({_ag_mode})"
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.2);border:1px solid {_ag_badge_color}44;"
                f"border-left:3px solid {_ag_badge_color};border-radius:6px;"
                f"padding:5px 10px;margin:6px 0;font-size:0.85rem;"
                f"color:{_ag_badge_color};font-weight:600;'>{_ag_badge_text}</div>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass

        # Force sidebar buttons to keep labels on one line (Streamlit otherwise
        # character-wraps when 3 buttons share a narrow sidebar — "Reload" becomes
        # "R-e-l-o-a-d" vertical).  Also reduce padding for better fit.
        st.markdown("""
<style>
section[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]
button[kind="secondary"] div[data-testid="stMarkdownContainer"] p {
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: clip !important;
    font-size: 0.82rem !important;
}
section[data-testid="stSidebar"] div[data-testid="stHorizontalBlock"]
button[kind="secondary"] {
    padding: 6px 4px !important;
    min-width: 0 !important;
}
</style>""", unsafe_allow_html=True)
        col_r, col_s, col_all = st.columns(3)
        with col_r:
            if st.button("↺", key="sidebar_refresh",
                         width='stretch',
                         help="Reload — load the latest saved scan data from disk"):
                # OPT-44: targeted clear — reload scan data and live prices only
                _load_history_file.clear()
                _get_history_cache()["data"] = None   # coherence: clear shared resource cache too
                load_live_prices.clear()
                st.rerun()
        with col_all:
            if st.button("⟳", key="sidebar_refresh_all",
                         width='stretch',
                         help="Refresh All — clears every cache and fetches fresh data from all sources"):
                # Nuclear clear: invalidate EVERY st.cache_data in this module
                try:
                    st.cache_data.clear()
                except Exception:
                    # Fallback: clear individual known caches
                    _load_history_file.clear()
                    load_live_prices.clear()
                    _get_api_status.clear()
                # Always clear module-level macro caches (not @st.cache_data decorated)
                try:
                    from macro_feeds import clear_macro_caches
                    clear_macro_caches()
                except Exception:
                    pass
                try:
                    from cycle_indicators import clear_cycle_caches
                    clear_cycle_caches()
                except Exception:
                    pass
                _get_history_cache()["data"] = None
                _FEEDBACK_CACHE["data"] = None
                st.success("All caches cleared — fetching fresh data…")
                st.rerun()
        with col_s:
            if st.button("▶", key="sidebar_scan_now",
                         width='stretch',
                         help="Scan — run a fresh scan now (~30 seconds). Auto-reloads when done."):
                try:
                    scheduler_path = str(Path(__file__).parent.parent / "scheduler.py")
                    subprocess.Popen(
                        [sys.executable, scheduler_path, "--now"],
                        creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                    )
                    st.session_state._scanning = True
                    st.session_state._scan_deadline = time.time() + 300
                    st.session_state._scan_baseline = (
                        latest.get("completed_at") or latest.get("run_id") or ""
                    )
                except Exception as _e:
                    # Streamlit Cloud restricts subprocess spawning.
                    # The auto-scheduler runs on startup — manual scan not available on Cloud.
                    if "cloud" in str(_e).lower() or sys.platform != "win32":
                        st.info("Manual scan not available on Streamlit Cloud. Data refreshes automatically every 4 hours.")
                    else:
                        logger.warning("[common] scan launch error: %s", _e)
                        st.error("Could not start scan — please try again in a moment.")

        # ─── Scan completion polling (OPT-43) ─────────────────────────────────
        # Always render the fragment so its DOM element persists across full reruns.
        # The fragment's internal guard makes it a no-op when _scanning=False,
        # preventing "fragment does not exist" log spam after scan completes.
        _scan_progress_fragment()

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

        # ── User Level selector — 3-tier experience system (Phase 1) ──────────────
        # Beginner = default on first run. Persists across all pages via session_state.
        # pro_mode kept for backward compat: True when user is Advanced.
        st.markdown("<div class='section-label'>Experience Level</div>", unsafe_allow_html=True)
        _LEVEL_OPTIONS = ["beginner", "intermediate", "advanced"]
        _LEVEL_LABELS  = {
            "beginner":     "🟢 Beginner",
            "intermediate": "🟡 Intermediate",
            "advanced":     "🔴 Advanced",
        }
        _cur_level = st.session_state.get("user_level", "beginner")
        _level_val = st.radio(
            "User Level",
            options=_LEVEL_OPTIONS,
            format_func=lambda lv: _LEVEL_LABELS[lv],
            index=_LEVEL_OPTIONS.index(_cur_level) if _cur_level in _LEVEL_OPTIONS else 0,
            key="defi_user_level_radio",
            label_visibility="collapsed",
            help=(
                "Beginner: plain-English view, tooltips always visible, simplified signals. "
                "Intermediate: key numbers + condensed explanations. "
                "Advanced: full technical detail, all raw numbers."
            ),
        )
        st.session_state["user_level"]    = _level_val
        # Backward compatibility — all existing pro_mode checks continue to work
        st.session_state["defi_pro_mode"] = (_level_val == "advanced")

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

        # ── PDF Export ──────────────────────────────────────────────────────────
        try:
            _scan_data  = load_latest()
            _completed_at = (_scan_data.get("completed_at") or "") if _scan_data else ""
            if _completed_at:
                _pdf_bytes = _gen_opportunities_pdf_cached(_completed_at)
                if _pdf_bytes:
                    from datetime import datetime as _dt_pdf, timezone as _tz_pdf
                    _pdf_ts = _dt_pdf.now(_tz_pdf.utc).strftime("%Y%m%d_%H%M")
                    st.download_button(
                        label="📄 Download Report (PDF)",
                        data=_pdf_bytes,
                        file_name=f"defi_opportunities_{_pdf_ts}.pdf",
                        mime="application/pdf",
                        key="sidebar_pdf_export",
                        width='stretch',
                        help="Download a PDF report of all current DeFi opportunities across all risk profiles.",
                    )
        except Exception:
            pass  # PDF export never crashes the sidebar

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Glossary popover (Phase 1) ─────────────────────────────────────────
        try:
            from ui.glossary import glossary_popover as _glossary_pop
            _glossary_pop(st.session_state.get("user_level", "beginner"))
        except ImportError:
            pass

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Agent Control Panel (mini) ─────────────────────────────────────────
        try:
            from agents.agent_runner import get_state as _agent_get_state, set_running as _agent_set_running, set_emergency_stop as _agent_set_estop
            from agents.config import EMERGENCY_STOP_KEY as _ESTOP_KEY, PAPER_TRADING_GATE_DAYS as _GATE_DAYS
            from agents.audit_log import AuditLog as _AuditLog
            _agent_state     = _agent_get_state()
            _agent_running   = _agent_state.get("running", False)
            _agent_estop_on  = _agent_state.get(_ESTOP_KEY, False)
            _agent_mode      = _agent_state.get("mode", "PAPER")
            _paper_days    = _AuditLog().get_paper_trade_days()
            _last_dec      = _agent_state.get("last_decision", {})

            if _agent_estop_on:
                _a_color = "#ef4444"; _a_icon = "🔴"; _a_label = "STOPPED"
            elif _agent_running:
                _a_color = "#22c55e"; _a_icon = "🟢"; _a_label = "RUNNING"
            else:
                _a_color = "#f59e0b"; _a_icon = "⏸️"; _a_label = "PAUSED"

            st.markdown(
                f"<div style='font-size:0.60rem;text-transform:uppercase;letter-spacing:0.8px;"
                f"color:#475569;margin-bottom:4px;'>Agent</div>"
                f"<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;'>"
                f"<span style='color:{_a_color};font-size:0.85rem;font-weight:700;'>"
                f"{_a_icon} {_a_label} · {_agent_mode}</span>"
                f"<span style='color:#64748b;font-size:0.65rem;'>"
                f"Paper: {_paper_days}/{_GATE_DAYS}d</span></div>",
                unsafe_allow_html=True,
            )
            if _last_dec.get("action"):
                _action_color = "#22c55e" if _last_dec.get("approved") else "#f59e0b"
                st.markdown(
                    f"<div style='font-size:0.65rem;color:{_action_color};margin-bottom:6px;'>"
                    f"Last: {_html.escape(str(_last_dec.get('action','—')))} → "
                    f"{_html.escape(str(_last_dec.get('protocol','—')))}</div>",
                    unsafe_allow_html=True,
                )
            _sa_col, _sb_col = st.columns(2)
            with _sa_col:
                _btn_label = "⏸ Pause" if _agent_running else "▶ Start"
                if st.button(_btn_label, key="sidebar_agent_toggle",
                         width='stretch'):
                    _agent_set_running(not _agent_running)
                    st.rerun()
            with _sb_col:
                if st.button("🛑 E-Stop", key="sidebar_agent_estop",
                         width='stretch'):
                    _agent_set_estop(True, "Sidebar emergency stop")
                    st.rerun()
            st.page_link("pages/5_Agent.py", label="→ Agent Control Panel", icon="🤖")
        except Exception:
            pass  # never crash sidebar on agent import failure

        # ── AI / API Health Banner ─────────────────────────────────────────────
        # Shows prominently in sidebar on every page load so issues are never hidden.
        try:
            _ai_key_present = bool(ANTHROPIC_API_KEY)
            if ANTHROPIC_ENABLED and _ai_key_present:
                _ai_banner_color, _ai_banner_icon, _ai_banner_text = (
                    "#22c55e", "✅", "Claude AI active"
                )
            elif ANTHROPIC_ENABLED and not _ai_key_present:
                _ai_banner_color, _ai_banner_icon, _ai_banner_text = (
                    "#ef4444", "🔴", "Claude AI: key missing"
                )
            else:
                _ai_banner_color, _ai_banner_icon, _ai_banner_text = (
                    "#64748b", "⚫", "Claude AI disabled"
                )
            st.markdown(
                f"<div style='background:rgba(0,0,0,0.15);border:1px solid {_ai_banner_color}44;"
                f"border-left:3px solid {_ai_banner_color};border-radius:6px;"
                f"padding:4px 10px;margin:4px 0;font-size:0.85rem;"
                f"color:{_ai_banner_color};font-weight:600;'>"
                f"{_ai_banner_icon} {_ai_banner_text}</div>",
                unsafe_allow_html=True,
            )
        except Exception:
            pass

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── API Status Dots (#17) ──────────────────────────────────────────────
        try:
            _api_status = _get_api_status()
            _dot_parts = []
            for _svc, _status in _api_status.items():
                if _svc.startswith("_"):
                    continue
                if _status in ("ok", "configured"):
                    _dot = "<span style='color:#22c55e;font-size:0.65rem;'>●</span>"
                elif _status in ("no key", "community (may be blocked)"):
                    _dot = "<span style='color:#64748b;font-size:0.65rem;'>●</span>"
                else:
                    _dot = "<span style='color:#ef4444;font-size:0.65rem;'>●</span>"
                _dot_parts.append(
                    f"<span title='{_svc}: {_status}'>{_dot}"
                    f"<span style='font-size:0.60rem;color:#475569;margin-left:2px;'>{_svc}</span></span>"
                )
            if _dot_parts:
                st.markdown(
                    "<div style='font-size:0.65rem; color:#334155; line-height:1.6;'>"
                    "<div style='font-size:0.60rem; text-transform:uppercase; "
                    "letter-spacing:0.8px; color:#475569; margin-bottom:3px;'>API Status</div>"
                    "<div style='display:flex; flex-wrap:wrap; gap:6px;'>"
                    + " ".join(_dot_parts) +
                    "</div></div>",
                    unsafe_allow_html=True,
                )
        except Exception:
            pass

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Per-User API Keys (#18) ────────────────────────────────────────────
        with st.expander("🔑 API Keys (Session Only)", expanded=False):
            st.caption("Keys stored in session only — cleared on page refresh.")
            _user_cg = st.text_input("CoinGecko Pro Key", type="password", key="defi_user_cg_key")
            _user_cm = st.text_input("CoinMetrics Key", type="password", key="defi_user_cm_key")
            if st.button("Apply Keys", key="defi_btn_apply_keys"):
                if _user_cg:
                    st.session_state["defi_runtime_coingecko_key"] = _user_cg
                if _user_cm:
                    st.session_state["defi_runtime_coinmetrics_key"] = _user_cm
                st.success("Keys applied for this session")

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Wallet Import (Beta) (#110) ────────────────────────────────────────
        with st.expander("🔗 Wallet Import (Beta)", expanded=False):
            _wallet = st.text_input(
                "EVM Wallet Address",
                placeholder="0x...",
                key="defi_wallet_address",
                help="Read-only. We never request signing or private keys.",
            )
            if _wallet and len(_wallet) == 42 and _wallet.startswith("0x"):
                st.caption("✓ Valid address detected")
                st.session_state["defi_wallet_address_valid"] = _wallet
            elif _wallet:
                st.warning("Invalid address format")
                st.session_state["defi_wallet_address_valid"] = None
            else:
                # User cleared the input — reset so Zerion section stops showing stale data
                st.session_state["defi_wallet_address_valid"] = None

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
        st.markdown(
            "<div style='font-size:0.75rem; color:#334155; line-height:1.4; padding:4px 0;'>"
            "⚠ Not financial advice · DYOR before investing.</div>",
            unsafe_allow_html=True,
        )

    # Model weights (outside sidebar context)
    # OPT-41: use module-level TTL cache to avoid re-reading history.json every render
    try:
        from ai.feedback_loop import get_feedback_dashboard
        _now_ts = time.time()
        if _FEEDBACK_CACHE["data"] is not None and _now_ts < _FEEDBACK_CACHE["expires"]:
            feedback = _FEEDBACK_CACHE["data"]
        else:
            feedback = get_feedback_dashboard()
            _FEEDBACK_CACHE["data"]    = feedback
            _FEEDBACK_CACHE["expires"] = _now_ts + 300  # 5-minute TTL
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
        "user_level":     st.session_state.get("user_level", "beginner"),
    }


# ─── Data Loaders ─────────────────────────────────────────────────────────────

def _history_mtime() -> float:
    """Return history.json modification time (seconds since epoch), or 0.0 if absent.

    Used as a cache-key dependency so _load_history_file auto-invalidates the
    moment the scheduler writes a new scan — regardless of whether the scan was
    triggered via the UI button or the background auto-scheduler.
    """
    try:
        return HISTORY_FILE.stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(ttl=60, max_entries=5)  # F3: memory guard — keyed by mtime, small unique set
def _load_history_file(_mtime: float = 0.0) -> dict:
    """Single cached read of history.json — shared by load_latest() and load_history_runs().

    _mtime is the file's modification time and acts as the primary cache-busting
    key: any new scan that writes to history.json produces a new mtime, which
    Streamlit treats as a cache miss, forcing an immediate fresh read.
    The ttl=60 is a safety net for edge cases (file unchanged but data stale).
    """
    if not HISTORY_FILE.exists():
        return {}
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        st.warning("⚠️ history.json is corrupted — re-run the scheduler.")
        return {}
    except Exception as e:
        logger.warning("[History] load error: %s", e)
        st.warning("⚠️ Scan data temporarily unavailable — try running the scanner or refreshing.")
        return {}


def load_latest() -> dict:
    return _load_history_file(_history_mtime()).get("latest") or {}


def load_history_runs() -> list:
    return _load_history_file(_history_mtime()).get("runs") or []


@st.cache_data(ttl=60, max_entries=1)  # F3: memory guard
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


@st.cache_data(ttl=300, max_entries=1)  # F3: memory guard
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


@st.cache_data(ttl=300, max_entries=1)  # F3: memory guard
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

_URGENCY_COLOR = {"act_now": "#ef4444", "act_soon": "#f59e0b", "monitor": "#00d4aa"}
_URGENCY_LABEL = {"act_now": "ACT NOW", "act_soon": "ACT SOON", "monitor": "MONITOR"}


def render_urgency_badge(urgency: str) -> str:
    """Return an HTML badge string for arbitrage urgency levels."""
    color = _URGENCY_COLOR.get(urgency, "#00d4aa")
    label = _URGENCY_LABEL.get(urgency, _html.escape((urgency or "").upper()))
    return (
        f"<span style=\"color:{color}; font-weight:700; font-size:0.85rem; "
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
    """Map 0-10 risk score to A-F letter grade + hex color.
    Aligned with Exponential.fi industry standard: A = safest, F = riskiest.
    """
    from config import risk_letter_grade as _rlg
    return _rlg(float(score))


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
        _default_a  = float(price_lkp.get(token_a_sym) or FALLBACK_PRICES.get(token_a_sym, 0.01))
        # Key includes token symbol — changing pair creates a fresh widget with updated price
        entry_a = st.number_input("Token A entry price ($)", value=_default_a,
                                  min_value=0.0001, format="%.6f",
                                  key=f"il_entry_a_{token_a_sym}",
                                  help="Uses FTSO live price when available")
        current_a = st.number_input("Token A current price ($)",
                                    value=float(price_lkp.get(token_a_sym) or _default_a),
                                    min_value=0.0001, format="%.6f",
                                    key=f"il_curr_a_{token_a_sym}")
    with c2:
        token_b_sym = st.selectbox("Token B", ["USD0", "USDT0", "USDC.e", "WFLR", "sFLR", "FXRP", "wETH", "HLN"], key="il_token_b")
        _default_b  = float(price_lkp.get(token_b_sym) or FALLBACK_PRICES.get(token_b_sym, 1.00))
        entry_b = st.number_input("Token B entry price ($)", value=_default_b,
                                  min_value=0.0001, format="%.6f",
                                  key=f"il_entry_b_{token_b_sym}")
        current_b = st.number_input("Token B current price ($)",
                                    value=float(price_lkp.get(token_b_sym) or _default_b),
                                    min_value=0.0001, format="%.6f",
                                    key=f"il_curr_b_{token_b_sym}")

    deposit = st.number_input("Deposit value ($)", min_value=1.0, value=1000.0, step=100.0, key="il_deposit")

    # Compute IL using the FTSO-validated price ratio
    if entry_a > 0 and entry_b > 0 and current_a > 0 and current_b > 0:
        # Price ratio = (current_a/current_b) / (entry_a/entry_b)
        entry_ratio   = entry_a / entry_b
        current_ratio = current_a / current_b
        price_ratio   = current_ratio / entry_ratio if entry_ratio > 0 else 1.0
        il_pct        = calculate_il(price_ratio)
        hodl_val      = deposit * 0.5 * (current_a / entry_a) + deposit * 0.5 * (current_b / entry_b)
        # IL loss is relative to current hodl value, not original deposit
        il_usd        = hodl_val * il_pct / 100
        lp_val        = hodl_val - il_usd
        il_color      = "#10b981" if il_pct < 1 else ("#f59e0b" if il_pct < 5 else "#ef4444")

        is_ftso = bool(price_lkp.get(token_a_sym) or price_lkp.get(token_b_sym))
        src_label = "FTSO/Live" if is_ftso else "Manual"

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""<div class="metric-card card-red">
            <div class="label">Impermanent Loss</div>
            <div class="big-number" style="color:{il_color};">{il_pct:.2f}%</div>
            <div style="color:#475569; font-size:0.85rem; margin-top:4px;">≈ ${il_usd:,.2f} on ${deposit:,.0f}</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""<div class="metric-card card-blue">
            <div class="label">LP Value Now</div>
            <div class="big-number">${lp_val:,.0f}</div>
            <div style="color:#475569; font-size:0.85rem; margin-top:4px;">After IL vs ${deposit:,.0f} in</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            hodl_diff = hodl_val - lp_val
            h_color   = "#10b981" if hodl_diff <= 0 else "#ef4444"
            st.markdown(f"""<div class="metric-card card-orange">
            <div class="label">HODL vs LP</div>
            <div class="big-number" style="color:{h_color};">${hodl_diff:+,.0f}</div>
            <div style="color:#475569; font-size:0.85rem; margin-top:4px;">HODL would be ${hodl_val:,.0f}</div>
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
                <div style="font-size:0.85rem; color:#64748b; margin-bottom:5px; display:flex; align-items:center; justify-content:center; gap:4px;">
                    {dot_html}<span style="letter-spacing:0.6px; text-transform:uppercase;">{sym}</span>
                </div>
                <div style="font-size:1.12rem; font-weight:700; letter-spacing:-0.3px; font-variant-numeric:tabular-nums; color:#f1f5f9;">{price_str}</div>
                <div style="font-size:0.75rem; color:{color}; margin-top:3px; font-weight:600;">{arrow} {abs(chg):.2f}%</div>
            </div>""", unsafe_allow_html=True)


def render_section_header(title: str, subtitle: str = "") -> None:
    """Renders a section title with violet gradient underline and optional subtitle."""
    sub_html = (
        f"<div style='color:#475569; font-size:0.85rem; margin-top:4px; margin-bottom:16px;'>"
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
            <div style="color:#94a3b8; font-size:0.85rem; line-height:1.55;">{INCENTIVE_PROGRAM['note']}</div>
        </div>
    </div>""", unsafe_allow_html=True)


def render_yield_hero_cards(positions: list, opps: list, portfolio_size: float) -> None:
    total_value = sum(p.get("current_value", 0) for p in positions) or portfolio_size
    if opps:
        top3 = opps[:3]
        # Net APY: subtract expected IL so LP positions don't overstate projected returns.
        # For lending/staking (il_estimate_pct=0) this is identical to gross APY.
        net_apys = [max(0.0, o.get("estimated_apy", 0) - o.get("il_estimate_pct", 0)) for o in top3]
        avg_apy  = sum(net_apys) / len(net_apys)
    else:
        avg_apy = 0.0

    # Compound interest: derive weekly/monthly equivalent rates from annual APY.
    # APY is a compounded annual figure; simple division (APY/52) overstates by ~6-7%.
    weekly_rate   = (1 + avg_apy / 100) ** (1 / 52) - 1
    monthly_rate  = (1 + avg_apy / 100) ** (1 / 12) - 1
    weekly_yield  = total_value * weekly_rate
    monthly_yield = total_value * monthly_rate
    annual_yield  = total_value * (avg_apy / 100)

    c1, c2, c3 = st.columns(3)
    for col, label, value, sub, cls, accent, uid in [
        (c1, "Est. This Week",  f"${weekly_yield:,.2f}",  f"{weekly_rate*100:.3f}% weekly", "card-green",  "#22c55e", "yield-hero-week"),
        (c2, "Est. This Month", f"${monthly_yield:,.2f}", f"on ${total_value:,.0f}",         "card-blue",   "#00d4aa", "yield-hero-month"),
        (c3, "Est. This Year",  f"${annual_yield:,.2f}",  f"{avg_apy:.1f}% net APY (top-3)","card-orange", "#f59e0b", "yield-hero-year"),
    ]:
        with col:
            st.markdown(f"""
            <div id="{uid}" class="metric-card {cls}">
                <div class="label">{label}</div>
                <div class="big-number" style="color:{accent};">{value}</div>
                <div style="color:#64748b; font-size:0.75rem; margin-top:4px;">{sub}</div>
            </div>""", unsafe_allow_html=True)

    # Count-up animation — targets specific hero-card IDs so it never bleeds into other metric cards
    st.html(f"""
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
    """)

    st.markdown(
        "<div style='color:#334155; font-size:0.75rem;'>"
        "Estimated using top-3 opportunities. Actual results vary. Not financial advice.</div>",
        unsafe_allow_html=True,
    )


def render_opportunity_card(
    opp: dict, idx: int, color: str,
    portfolio_size: float = 0, weight: float = 1.0,
) -> None:
    _card_user_level = get_user_level()
    _raw_apy = opp.get("estimated_apy", 0)
    try:
        apy = float(_raw_apy or 0)
    except (TypeError, ValueError):
        apy = 0.0
    import math as _math
    if not _math.isfinite(apy):
        apy = 0.0
    lo     = opp.get("apy_low",  apy * 0.8)
    hi     = opp.get("apy_high", apy * 1.2)
    _w     = float(weight) if isinstance(weight, (int, float)) else 1.0
    conf   = min(100, opp.get("confidence", 50) * _w)
    il     = opp.get("il_risk") or "low"
    action = opp.get("action", opp.get("plain_english", "—"))
    proto  = opp.get("protocol", "—")
    pool   = opp.get("asset_or_pool", "—")
    src    = opp.get("data_source", "baseline")
    rs     = min(10.0, max(0.0, float(opp.get("risk_score") or 5.0)))
    kf     = opp.get("kelly_fraction", 0)
    tvl    = opp.get("tvl_usd", 0)

    grade, grade_color = risk_score_to_grade(rs)
    il_color = {"none": "#22c55e", "low": "#22c55e", "medium": "#f59e0b", "high": "#ef4444"}.get(il, "#f59e0b")
    il_icon  = {"none": "✓", "low": "✓", "medium": "~", "high": "!"}.get(il, "~")

    # IL estimate % (inline percentage from model, not just category)
    try:
        il_est_pct = float(opp.get("il_estimate_pct") or 0.0)
    except (TypeError, ValueError):
        il_est_pct = 0.0
    il_est_html  = (
        f" <span style='color:{il_color}; font-size:0.85rem;' "
        f"title='Estimated impermanent loss over 1 year based on pair volatility'>"
        f"~{il_est_pct:.1f}% IL</span>"
        if il_est_pct > 0 else ""
    )

    # Audit badge: look up protocol in PROTOCOL_AUDITS
    proto_key    = (str(opp.get("protocol", "")).lower().split() or [""])[0]
    _audit_data  = PROTOCOL_AUDITS.get(proto_key, {})
    _auditors    = _audit_data.get("auditors", [])
    _audit_year  = _audit_data.get("year", "")
    _audit_note  = _audit_data.get("note", "")
    _audit_html  = (
        f"<span style='font-size:0.85rem; color:#22c55e; font-weight:600; "
        f"background:rgba(52,211,153,0.08); padding:1px 6px; border-radius:4px; "
        f"border:1px solid rgba(52,211,153,0.25);' "
        f"title='{_html.escape(_audit_note)}'>"
        f"🛡 {' + '.join(_auditors[:2])} ({_audit_year})</span>"
        if _auditors else ""
    )

    # Protocol URL deep link
    _proto_url  = (PROTOCOLS.get(proto_key) or {}).get("url", "")
    _url_html   = (
        f"<a href='{_proto_url}' target='_blank' rel='noopener noreferrer' "
        f"style='font-size:0.85rem; color:#00d4aa; font-weight:600; "
        f"text-decoration:none; padding:1px 8px; border-radius:4px; "
        f"border:1px solid rgba(0,212,170,0.3); background:rgba(0,212,170,0.06);' "
        f"title='Open {_html.escape(str(proto))} in new tab'>"
        f"Open ↗</a>"
        if _proto_url else ""
    )

    # Preview badge: protocol has live=False in config — not yet tradeable
    _proto_is_live = (PROTOCOLS.get(proto_key) or {}).get("live", True)
    _preview_badge_html = (
        "<span style='font-size:0.85rem;font-weight:700;color:#f59e0b;"
        "background:rgba(245,158,11,0.12);padding:1px 7px;border-radius:4px;"
        "border:1px solid rgba(245,158,11,0.35);' "
        "title='This protocol is in preview mode — no public API available yet. "
        "Data shown is estimated. Monitor only; do not trade.'>⚠ PREVIEW</span>"
        if not _proto_is_live else ""
    )

    est_tag  = " <span class='badge-est'>EST</span>" if src in ("baseline", "estimate") else " <span class='badge-live'>LIVE</span>"
    medals   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣"]
    medal    = medals[min(idx, 5)]
    proto    = _html.escape(str(proto))
    pool     = _html.escape(str(pool))
    action   = _html.escape(str(action))

    # ── BUY / HOLD / AVOID badge (CLAUDE.md §8: shape + color always combined) ──
    # Derived from model confidence (already weighted by risk profile multiplier).
    # ≥70% = strong opportunity → BUY; 45-70% = hold existing; <45% = avoid.
    if conf >= 70:
        _action_shape, _action_label, _action_color = "▲", "BUY", "#22c55e"
    elif conf >= 45:
        _action_shape, _action_label, _action_color = "■", "HOLD", "#94a3b8"
    else:
        _action_shape, _action_label, _action_color = "▼", "AVOID", "#ef4444"
    _action_badge_html = (
        f"<span style='font-size:0.75rem;font-weight:800;color:{_action_color};"
        f"background:rgba(0,0,0,0.25);padding:2px 8px;border-radius:5px;"
        f"border:1px solid {_action_color}55;letter-spacing:0.04em;white-space:nowrap;' "
        f"title='Model signal: {_action_label} — based on yield quality, risk, and composite market environment'>"
        f"{_action_shape} {_action_label}</span>"
    )

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
                    f"<span style='color:{_exp_color}; font-size:0.85rem; font-weight:600; "
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
                f"<span style='color:{_tf_color}; font-size:0.85rem; font-weight:600;' "
                f"title='APY trending {apy_trend_flag} over last 7 scans'>"
                f"{_tf_icon} {abs(apy_trend_pct):.0f}% {apy_trend_flag}</span>"
            )

        _apy_decomp_html = (
            f"<div style='display:flex; gap:6px; font-size:0.85rem; margin-top:3px; flex-wrap:wrap; align-items:center;'>"
            + (f"<span style='color:#64748b;'>Base fees: <span style='color:#94a3b8; font-weight:600;'>{fee_apy:.1f}%</span></span>"
               f"<span style='color:#334155;'>·</span>" if fee_apy > 0 else "")
            + (f"<span style='color:#64748b;'>Rewards: <span style='color:#a78bfa; font-weight:600;'>{reward_apy:.1f}%</span></span>"
               f"<span style='color:#334155;'>·</span>" if reward_apy > 0 else "")
            + f"<span style='color:#64748b;'>Sustainable: <span style='color:{_sustain_color}; font-weight:600;'>{_sustain_pct}%</span></span>"
            + (f"<span style='color:#334155;'>·</span>{_expiry_html}" if _expiry_html else "")
            + (f"<span style='color:#334155;'>·</span>{_trend_flag_html}" if _trend_flag_html else "")
            + f"</div>"
        )

    # ── Treasury spread: DeFi yield premium vs. tokenized T-bill baseline ───────
    # Baseline: avg of BUIDL/BENJI/OUSG/USDY/TBILL (~4.25% Apr 2026). Updated monthly.
    _TREASURY_BASELINE_PCT = 4.25
    _spread = round(apy - _TREASURY_BASELINE_PCT, 2)
    _spread_color = "#22c55e" if _spread >= 2.0 else ("#f59e0b" if _spread >= 0 else "#ef4444")
    _spread_html = (
        f"<span style='font-size:0.85rem;color:{_spread_color};font-weight:600;"
        f"background:{_spread_color}14;padding:1px 6px;border-radius:4px;"
        f"border:1px solid {_spread_color}33;white-space:nowrap;' "
        f"title='vs. tokenized T-bill baseline (BUIDL/BENJI avg ~{_TREASURY_BASELINE_PCT}% · Apr 2026)'>"
        f"{'▲' if _spread >= 0 else '▼'} {_spread:+.2f}% vs. T-bill baseline</span>"
    )

    # Real Yield Ratio (#73) — fee revenue vs token incentive indicator
    _ry_html = ""
    if fee_apy > 0 or reward_apy > 0:
        _ry_total = fee_apy + reward_apy
        _ry_ratio = fee_apy / _ry_total if _ry_total > 0 else 0.0
        _ry_pct   = round(_ry_ratio * 100)
        if _ry_ratio >= 0.7:
            _ry_label, _ry_color = "Sustainable", "#22c55e"
        elif _ry_ratio >= 0.35:
            _ry_label, _ry_color = "Partial", "#f59e0b"
        else:
            _ry_label, _ry_color = "Incentive-Driven", "#EF4444"
        _ry_html = (
            f"<span style='font-size:0.85rem;color:{_ry_color};font-weight:600;"
            f"background:rgba(0,0,0,0.2);padding:1px 6px;border-radius:4px;"
            f"border:1px solid {_ry_color}44;' title='Real Yield: {_ry_pct}% of APY is from protocol fees (not emissions)'>"
            f"⚡ Real Yield {_ry_pct}% · {_ry_label}</span>"
        )

    # Confidence bar visual (0–100)
    conf_bar_pct = f"{conf:.0f}%"
    conf_color   = "#22c55e" if conf >= 70 else ("#f59e0b" if conf >= 45 else "#ef4444")

    # Safety score (inverse of risk) mapped to 0-100 for visual bar
    safety_pct    = max(0, min(100, int(round((10.0 - rs) * 10))))
    safety_color  = "#22c55e" if safety_pct >= 66 else ("#f59e0b" if safety_pct >= 33 else "#ef4444")
    safety_label  = "Good" if safety_pct >= 66 else ("Moderate" if safety_pct >= 33 else "Low")
    conf_label    = "Strong" if conf >= 70 else ("Moderate" if conf >= 45 else "Low")

    # Beginner: hide advanced chips behind the card (reduce from 13+ elements to ~7)
    _is_beg = _card_user_level == "beginner"
    _ry_html_render     = "" if _is_beg else _ry_html
    _spread_html_render = "" if _is_beg else _spread_html
    _audit_html_render  = "" if _is_beg else _audit_html

    # Integrated confidence + safety bars — rendered INSIDE the card so they
    # visually belong to it rather than floating below.
    _integrated_bars_html = (
        f"<div style='display:flex;gap:12px;margin-top:8px;"
        f"padding-top:8px;border-top:1px solid rgba(255,255,255,0.06);'>"
        f"<div style='flex:1;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline;"
        f"font-size:0.72rem;margin-bottom:3px;'>"
        f"<span style='color:#64748b;text-transform:uppercase;letter-spacing:0.05em;'>Model Confidence</span>"
        f"<span style='color:{conf_color};font-weight:700;'>{conf_label} · {conf:.0f}%</span>"
        f"</div>"
        f"<div style='height:5px;background:rgba(255,255,255,0.07);border-radius:3px;overflow:hidden;'>"
        f"<div style='width:{conf_bar_pct};height:100%;background:{conf_color};border-radius:3px;'></div>"
        f"</div></div>"
        f"<div style='flex:1;'>"
        f"<div style='display:flex;justify-content:space-between;align-items:baseline;"
        f"font-size:0.72rem;margin-bottom:3px;'>"
        f"<span style='color:#64748b;text-transform:uppercase;letter-spacing:0.05em;'>Safety Score</span>"
        f"<span style='color:{safety_color};font-weight:700;'>{safety_label} · {safety_pct}%</span>"
        f"</div>"
        f"<div style='height:5px;background:rgba(255,255,255,0.07);border-radius:3px;overflow:hidden;'>"
        f"<div style='width:{safety_pct}%;height:100%;background:{safety_color};border-radius:3px;'></div>"
        f"</div></div>"
        f"</div>"
    )

    # Grade chip — upgraded to prominent filled chip (was: small letter)
    _grade_chip_html = (
        f"<span class='grade-badge' style='background:{grade_color};color:#fff;"
        f"font-weight:800;letter-spacing:0.8px;padding:3px 10px;border-radius:5px;"
        f"font-size:0.8rem;text-shadow:0 1px 2px rgba(0,0,0,0.3);' "
        f"title='Safety Grade — A=safest, F=riskiest'>GRADE {grade}</span>"
    )

    st.markdown(f"""<div class="opp-card" style="border-left:3px solid {color};">
<div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:6px;">
<div style="flex:1;min-width:0;display:flex;align-items:center;gap:5px;flex-wrap:wrap;"><span style="font-size:0.85rem;color:#475569;">{medal}</span><span style="font-size:0.95rem;font-weight:700;color:#f1f5f9;">{proto}</span><span style="color:#334155;margin:0 4px;">·</span><span style="font-size:0.85rem;color:#94a3b8;">{pool}</span>{(' ' + _preview_badge_html) if _preview_badge_html else ''}</div>
<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">{_action_badge_html}{_grade_chip_html}<span class="{glow_cls}" style="font-size:1.05rem;font-weight:800;color:{color};letter-spacing:-0.5px;font-variant-numeric:tabular-nums;">{apy:.1f}%{est_tag}</span></div>
</div>
{_apy_decomp_html}
<div style="color:#94a3b8;font-size:0.85rem;margin-top:4px;line-height:1.35;">{action}</div>
<div style="display:flex;gap:10px;font-size:0.85rem;color:#475569;margin-top:5px;flex-wrap:wrap;align-items:center;">
<span><span style="color:{il_color};font-weight:700;">{il_icon}</span><span style="margin-left:2px;">Risk: <span style="color:{il_color};font-weight:600;">{il.upper()}</span>{il_est_html}</span></span>
<span>Alloc: <span style="color:#94a3b8;font-weight:600;">{alloc_str}</span></span>
{tvl_html}
{_ry_html_render}
{_spread_html_render}
</div>
{f'<div style="display:flex;gap:6px;margin-top:3px;flex-wrap:wrap;align-items:center;">{_audit_html_render}{_url_html}</div>' if (_audit_html_render or _url_html) else ""}
{_integrated_bars_html}
</div>""", unsafe_allow_html=True)


# ─── Phase 2 — New helpers ────────────────────────────────────────────────────

# ── Coin Universe (Phase 2, item 16) ──────────────────────────────────────────

MUST_HAVE_COINS: list[str] = ["XRP", "XLM", "XDC", "CC", "HBAR", "SHX", "ZBCN"]
_STABLECOINS: frozenset[str] = frozenset({
    "usdt","usdc","dai","busd","tusd","fdusd","usdd","frax","lusd","usdp",
    "gusd","susd","nusd","crvusd","pyusd","eurc","eur",
})


@st.cache_data(ttl=3600, max_entries=1)
def fetch_coin_universe() -> list[dict]:
    """Fetch top-30 non-stablecoin coins + 7 must-haves from CoinGecko.

    Returns list of dicts with keys: id, symbol, name, rank.
    Must-haves always included even if below top-30.
    Cached 1 hour.
    """
    try:
        coingecko_limiter.acquire()
        _resp = _http_session.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={
                "vs_currency": "usd", "order": "market_cap_desc",
                "per_page": "60", "page": "1",
                "sparkline": "false", "locale": "en",
            },
            timeout=10,
        )
        _data = _resp.json() if _resp.status_code == 200 else []

        seen: set[str] = set()
        top30: list[dict] = []
        for _coin in _data:
            _sym = (_coin.get("symbol") or "").lower()
            if _sym in _STABLECOINS or _sym in seen or len(top30) >= 30:
                continue
            seen.add(_sym)
            top30.append({
                "id":     _coin.get("id", ""),
                "symbol": _coin.get("symbol", "").upper(),
                "name":   _coin.get("name", ""),
                "rank":   _coin.get("market_cap_rank", 999),
            })

        # Inject must-haves that didn't make the top-30
        top30_symbols = {c["symbol"] for c in top30}
        for _s in MUST_HAVE_COINS:
            if _s not in top30_symbols:
                top30.append({"id": _s.lower(), "symbol": _s, "name": _s, "rank": 999})

        return top30
    except Exception:
        return [{"id": s.lower(), "symbol": s, "name": s, "rank": 999}
                for s in MUST_HAVE_COINS]


# ── Fear & Greed Trend (Phase 2, item 14) ─────────────────────────────────────

@st.cache_data(ttl=3600, max_entries=3)
def fetch_fear_greed_history(days: int = 30) -> list[dict]:
    """Fetch Fear & Greed Index history from alternative.me API.

    Returns list of dicts (most recent first) with 'value' and 'timestamp'.
    Cached 1 hour.
    """
    try:
        _resp = _http_session.get(
            f"https://api.alternative.me/fng/?limit={days}&format=json",
            timeout=8,
        )
        return _resp.json().get("data", []) if _resp.status_code == 200 else []
    except Exception:
        return []


def render_fear_greed_trend(user_level: str = "beginner") -> None:
    """Render Fear & Greed current value + 7-day avg + 30-day avg (Phase 2, item 14)."""
    _history = fetch_fear_greed_history(30)
    _values  = []
    for _item in _history:
        try:
            _values.append(int(_item["value"]))
        except Exception:
            pass

    if not _values:
        st.caption("Fear & Greed data unavailable right now.")
        return

    _cur   = _values[0]
    _avg7  = sum(_values[:7])  / min(7, len(_values))
    _avg30 = sum(_values[:30]) / min(30, len(_values))

    def _fg(v: float) -> tuple[str, str]:
        if v <= 25:  return "Extreme Fear",  "#ef4444"
        if v <= 45:  return "Fear",           "#f59e0b"
        if v <= 55:  return "Neutral",        "#64748b"
        if v <= 75:  return "Greed",          "#f59e0b"
        return "Extreme Greed", "#22c55e"

    _cl, _cc = _fg(_cur)
    _7l, _7c = _fg(_avg7)
    _30l, _30c = _fg(_avg30)

    _c1, _c2, _c3 = st.columns(3)
    for _col, _val, _lbl, _col_hex, _period in [
        (_c1, _cur,   _cl,  _cc,  "Now"),
        (_c2, _avg7,  _7l,  _7c,  "7-Day Avg"),
        (_c3, _avg30, _30l, _30c, "30-Day Avg"),
    ]:
        with _col:
            st.markdown(
                f"<div style='text-align:center;padding:12px;background:rgba(17,24,39,0.7);"
                f"border-radius:8px;border:1px solid rgba(255,255,255,0.07);'>"
                f"<div style='font-size:0.62rem;color:#64748b;text-transform:uppercase;"
                f"letter-spacing:0.8px;margin-bottom:4px'>{_period}</div>"
                f"<div style='font-size:1.9rem;font-weight:800;color:{_col_hex};"
                f"font-family:JetBrains Mono,monospace'>{_val:.0f}</div>"
                f"<div style='font-size:0.85rem;color:{_col_hex};margin-top:2px'>{_lbl}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )

    if user_level == "beginner":
        st.caption(
            f"💡 **What this means:** Fear & Greed measures market emotion — 0 = extreme panic "
            f"(markets selling off), 100 = extreme euphoria (bubble risk). "
            f"Current reading: **{_cur}** ({_cl}). "
            f"Contrarian tip: extreme fear is historically a buying opportunity; "
            f"extreme greed signals caution."
        )


# ── Welcome Banner (Phase 2, item 6) ──────────────────────────────────────────

def render_welcome_banner() -> None:
    """Show a one-time welcome message for Beginner users.

    Appears once per session, dismissible with a button.
    No-op for Intermediate/Advanced users.
    """
    if get_user_level() != "beginner":
        return
    if st.session_state.get("_defi_welcome_dismissed"):
        return

    _wc1, _wc2 = st.columns([30, 1])
    with _wc1:
        st.markdown(
            "<div style='background:rgba(30,58,138,0.22);border:1px solid rgba(59,130,246,0.3);"
            "border-left:3px solid #00d4aa;border-radius:6px;padding:5px 12px;margin-bottom:8px;"
            "line-height:1.35; font-size:12px;'>"
            "<b style='color:#93c5fd;'>👋 Welcome to Flare DeFi Analytics.</b> "
            "<span style='color:#94a3b8;'>Start at "
            "<b style='color:#cbd5e1;'>Opportunities</b> · "
            "Change experience level in the sidebar any time.</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    with _wc2:
        if st.button("✕", key="_defi_dismiss_welcome", help="Dismiss"):
            st.session_state["_defi_welcome_dismissed"] = True
            st.rerun()


# ── Beginner UX — "What does this mean?" Panel (Phase 2, item 8) ──────────────

def render_what_this_means(
    message: str,
    title: str = "What does this mean for me?",
    intermediate_message: str = "",
) -> None:
    """Render a level-aware explanation after signals/scores.

    Beginner:     full info panel with title and plain-English explanation.
    Intermediate: condensed one-liner caption (pass via intermediate_message).
    Advanced:     no-op — maximum data density, no hand-holding.
    """
    level = get_user_level()
    if level == "beginner":
        st.markdown(
            f"<div style='background:rgba(30,58,138,0.20);border:1px solid rgba(59,130,246,0.30);"
            f"border-radius:5px;padding:6px 12px;margin:4px 0;line-height:1.4;'>"
            f"<span style='font-size:13px;font-weight:700;color:#93c5fd;'>💡 {title}</span>"
            f"<span style='font-size:13px;color:#94a3b8;'> — {message}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    elif level == "intermediate" and intermediate_message:
        st.caption(f"ℹ️ {intermediate_message}")


# ── Color-Coded Gauge (Phase 2, item 9) ───────────────────────────────────────

def render_gauge(
    value: float,
    label: str,
    min_v: float = 0.0,
    max_v: float = 100.0,
    low_threshold: float = 0.33,
    high_threshold: float = 0.66,
    user_level: str = "beginner",
    unit: str = "",
) -> None:
    """Render a color-coded progress bar gauge.

    Beginner: shows plain-English label (Low / Moderate / Good) + colored bar.
    Intermediate/Advanced: shows numeric value + colored bar.
    """
    _pct = max(0.0, min(1.0, (value - min_v) / max(max_v - min_v, 0.001)))

    if _pct >= high_threshold:
        _color, _plain = "#22c55e", "Good"
    elif _pct >= low_threshold:
        _color, _plain = "#f59e0b", "Moderate"
    else:
        _color, _plain = "#ef4444", "Low"

    _display = _plain if user_level == "beginner" else f"{value:.1f}{unit}"
    _bar_w   = f"{_pct * 100:.1f}%"

    st.markdown(
        f"<div style='margin-bottom:8px;'>"
        f"<div style='display:flex;justify-content:space-between;"
        f"font-size:0.85rem;color:#64748b;margin-bottom:4px;'>"
        f"<span>{label}</span>"
        f"<span style='color:{_color};font-weight:700;'>{_display}</span>"
        f"</div>"
        f"<div style='background:rgba(255,255,255,0.07);border-radius:4px;height:6px;'>"
        f"<div style='width:{_bar_w};height:6px;background:{_color};"
        f"border-radius:4px;transition:width 0.3s ease;'></div>"
        f"</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ── Shared Composite Signal (4-layer, cached 1 h) ─────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False, max_entries=1)
def get_composite_signal_cached() -> dict:
    """Compute the 4-layer composite market environment signal. Cached 1 hour.

    Uses ThreadPoolExecutor for parallel macro/on-chain/TA fetches (~10s vs ~30s
    sequential). Includes Fear & Greed current value and 30-day average.
    Shared across Dashboard (app.py) and Opportunities page — one cache entry,
    one data fetch cycle regardless of which page the user visits first.
    """
    try:
        from models.composite_signal import compute_composite_signal as _ccs
        from macro_feeds import (
            fetch_all_macro_data as _fmac,
            fetch_coinmetrics_onchain as _foc,
            fetch_btc_ta_signals as _fta,
        )
    except ImportError:
        return {}

    try:
        import threading as _thr
        from concurrent.futures import ThreadPoolExecutor

        try:
            from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
            _ctx = get_script_run_ctx()
        except Exception:
            _ctx = None

        def _run(fn, *args):
            if _ctx is not None:
                try:
                    add_script_run_ctx(_thr.current_thread(), _ctx)
                except Exception:
                    pass
            return fn(*args)

        with ThreadPoolExecutor(max_workers=3) as _ex:
            _f1 = _ex.submit(_run, _fmac)
            _f2 = _ex.submit(_run, _foc, 90)
            _f3 = _ex.submit(_run, _fta)
            _macro   = _f1.result(timeout=20)
            _onchain = _f2.result(timeout=20)
            _ta      = _f3.result(timeout=20)

        _fg_val, _fg_30d_avg = None, None
        try:
            _hist = fetch_fear_greed_history(30)
            if _hist:
                _fg_val    = int(_hist[0]["value"])
                _vals_30   = [int(h["value"]) for h in _hist if "value" in h]
                _fg_30d_avg = round(sum(_vals_30) / len(_vals_30), 1) if _vals_30 else None
        except Exception:
            pass

        _csig = _ccs(
            macro_data=_macro,
            onchain_data=_onchain,
            ta_data=_ta,
            fg_value=_fg_val,
            fg_30d_avg=_fg_30d_avg,
        )

        # ── CoinsKid cycle indicators (additive; optional signals) ────────────
        try:
            from cycle_indicators import (
                fetch_google_trends_signal as _ftrends,
                fetch_stablecoin_supply_delta as _fstable,
                cycle_score_100 as _cscore,
            )
            _trends = _ftrends("bitcoin")
            _stable = _fstable()
            _extras = {
                "trends":       (_trends or {}).get("score"),
                "stable_delta": (_stable or {}).get("score"),
            }
            _cycle = _cscore(_csig.get("score"), extras=_extras)
            _csig["cycle_100"]       = _cycle
            _csig["trends_signal"]   = _trends
            _csig["stable_signal"]   = _stable
        except Exception as _ci_err:
            import logging as _cilg
            _cilg.getLogger(__name__).debug("[CycleIndicators] wire failed: %s", _ci_err)

        return _csig
    except Exception:
        return {}


# ── Cycle Position Gauge — INDEPENDENT of composite signal ────────────────────
# Renders even if macro feeds are still warming up. Uses whatever inputs are
# available: composite score (if fetched), Google Trends, stablecoin delta.
# Always returns a valid dict with cycle_100 populated — never empty.

@st.cache_data(ttl=1800, show_spinner=False, max_entries=1)
def get_cycle_position_cached() -> dict:
    """
    Fetch the Cycle Position score independently of the full composite signal.

    Returns a dict with 'cycle_100' populated even during composite warm-up.
    TTL: 30 min (shorter than composite because cycle inputs are lighter).
    """
    try:
        from cycle_indicators import (
            fetch_google_trends_signal as _ftrends,
            fetch_stablecoin_supply_delta as _fstable,
            cycle_score_100 as _cscore,
        )
    except ImportError:
        return {"cycle_100": {"score": 50, "zone": "NEUTRAL", "zone_label": "Neutral",
                              "color": "#64748b", "inputs_used": 0}}

    # Opportunistically reuse composite score if already cached
    _composite_score = None
    try:
        _csig = get_composite_signal_cached()
        if _csig and _csig.get("score") is not None:
            _composite_score = _csig.get("score")
    except Exception:
        pass

    _trends = _stable = None
    try:
        _trends = _ftrends("bitcoin")
    except Exception:
        pass
    try:
        _stable = _fstable()
    except Exception:
        pass

    _extras = {
        "trends":       (_trends or {}).get("score"),
        "stable_delta": (_stable or {}).get("score"),
    }
    _cycle = _cscore(_composite_score, extras=_extras)
    return {
        "cycle_100":     _cycle,
        "trends_signal": _trends,
        "stable_signal": _stable,
    }


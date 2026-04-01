"""
Flare DeFi Model — Dashboard (Home)
Multi-page app entry point. Shows prices, top opportunities, and arb alerts.
Run with:  streamlit run app.py
"""

import os
import sys
import html as _html
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Sentry error monitoring (free tier — only loads when DSN is set) ──────────

def _scrub_sentry_event(event, hint):
    """Remove API keys and PII from Sentry events before sending."""
    if "request" in event:
        event["request"].pop("cookies", None)
        event["request"].pop("headers", None)
    for key in list(event.get("extra", {}).keys()):
        if any(x in key.upper() for x in ["KEY", "SECRET", "TOKEN", "PASSWORD", "DSN"]):
            event["extra"][key] = "[REDACTED]"
    return event


try:
    import sentry_sdk
    _SENTRY_DSN = os.environ.get("DEFI_SENTRY_DSN", "")
    if _SENTRY_DSN:
        sentry_sdk.init(
            dsn=_SENTRY_DSN,
            traces_sample_rate=0.05,
            profiles_sample_rate=0.0,
            before_send=_scrub_sentry_event,
            # Ignore benign Streamlit-internal media-file expiry errors.
            # These fire when a browser loads a stale Plotly chart URL after
            # Streamlit's in-memory media store has already evicted the file.
            # They are always "handled=yes" and never affect the user experience.
            ignore_errors=["MediaFileStorageError"],
        )
except ImportError:
    pass

from ui.common import (
    page_setup, render_sidebar, load_latest, load_positions,
    render_price_strip, render_incentive_warning,
    render_yield_hero_cards, render_opportunity_card,
    render_urgency_badge, render_section_header, _ts_fmt, load_live_prices,
)
import streamlit as st

# ── Feature Flags (#21) ───────────────────────────────────────────────────────
try:
    from web3 import Web3  # noqa: F401
    from config import FEATURES
    FEATURES["web3"] = True
except ImportError:
    pass


# ─── DB Integrity Check at Startup (#14) ──────────────────────────────────────
@st.cache_resource
def _startup_db_check() -> bool:
    """Run DB integrity check once per process startup."""
    try:
        from database import check_db_integrity
        return check_db_integrity()
    except Exception:
        return True   # don't block startup on import error


page_setup("Dashboard · Flare DeFi")

_db_ok = _startup_db_check()
if not _db_ok:
    st.warning(
        "Database integrity check failed — the SQLite file may be corrupted. "
        "Delete data/defi_model.db and restart to rebuild.",
        icon="⚠️",
    )

# ── Global DeFi CSS (#59 UI/UX Refresh) ──────────────────────────────────────
DEFI_CSS = """
<style>
.defi-card { background: #1a1a2e; border: 1px solid #16213e; border-radius: 10px; padding: 14px; margin-bottom: 10px; }
.yield-high { color: #00e676; font-weight: bold; }
.yield-medium { color: #ffab40; font-weight: bold; }
.yield-low { color: #78909c; }
.risk-low { background: #1b5e20; color: #a5d6a7; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
.risk-medium { background: #e65100; color: #ffe0b2; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
.risk-high { background: #b71c1c; color: #ffcdd2; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }
.protocol-badge { background: #0d47a1; color: #bbdefb; padding: 2px 8px; border-radius: 12px; font-size: 0.8em; }
</style>
"""
st.markdown(DEFI_CSS, unsafe_allow_html=True)

try:
    ctx = render_sidebar()
except Exception as _e:
    import logging as _logging; _logging.getLogger(__name__).warning("Sidebar error: %s", _e)
    ctx = {}

profile       = ctx.get("profile", "conservative")
profile_cfg   = ctx.get("profile_cfg", {})
color         = ctx.get("color", "#00D4FF")
weight        = ctx.get("weight", {})
portfolio_size = ctx.get("portfolio_size", 10000)
pro_mode      = ctx.get("pro_mode", False)   # #82 Beginner/Pro mode
demo_mode     = ctx.get("demo_mode", False)   # #67 Demo/Sandbox mode

# ── Demo Mode Banner (#67) ────────────────────────────────────────────────────
if demo_mode:
    try:
        from data.demo_data import DEMO_PORTFOLIO, DEMO_OPPORTUNITIES, DEMO_MACRO  # noqa: F401
    except Exception:
        pass
    st.warning(
        "Demo Mode — Showing sample data. No API keys required. "
        "Toggle in sidebar to disable.",
        icon="🎭",
    )

latest    = load_latest()
positions = load_positions()

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown(
    "<h1 style='margin-bottom:4px;'>Dashboard</h1>"
    "<div style='color:#475569; font-size:0.87rem; margin-bottom:20px; "
    "display:flex; align-items:center; gap:12px; flex-wrap:wrap;'>"
    "<span>Live prices</span>"
    "<span style='color:#1e293b;'>·</span>"
    "<span>Top opportunities</span>"
    "<span style='color:#1e293b;'>·</span>"
    "<span>Arbitrage alerts</span>"
    "</div>",
    unsafe_allow_html=True,
)

# ── Incentive Warning ─────────────────────────────────────────────────────────
render_incentive_warning()

# ── Price Strip (live — refreshed every 2 min, not from stale scan data) ──────
flare_scan = latest.get("flare_scan", {})
prices     = load_live_prices() or flare_scan.get("prices", [])
render_price_strip(prices)

# ── Data Freshness ────────────────────────────────────────────────────────────
all_pts = (flare_scan.get("pools") or []) + (flare_scan.get("lending") or []) + (flare_scan.get("staking") or [])
if all_pts:
    total     = len(all_pts)
    live      = sum(1 for p in all_pts if p.get("data_source") == "live")
    estimated = sum(1 for p in all_pts if p.get("data_source") in ("baseline", "estimate"))
    live_pct  = live / total if total else 0
    pill_bg   = "rgba(34,197,94,0.10)"  if live_pct >= 0.7 else ("rgba(245,158,11,0.10)" if live > 0 else "rgba(239,68,68,0.10)")
    pill_border = "rgba(34,197,94,0.25)" if live_pct >= 0.7 else ("rgba(245,158,11,0.25)" if live > 0 else "rgba(239,68,68,0.25)")
    pill_color  = "#22c55e"  if live_pct >= 0.7 else ("#f59e0b" if live > 0 else "#ef4444")
    dot_cls     = "live-dot" if live_pct >= 0.7 else "stale-dot"
    fresh_label = f"{live}/{total} live" + (f" · {estimated} estimated" if estimated else "")
    st.markdown(
        f"<div style='display:inline-flex; align-items:center; gap:6px; "
        f"background:{pill_bg}; border:1px solid {pill_border}; "
        f"border-radius:20px; padding:3px 12px; margin:6px 0 16px; font-size:0.74rem;'>"
        f"<span class='{dot_cls}'></span>"
        f"<span style='color:{pill_color}; font-weight:600;'>{fresh_label}</span>"
        f"<span style='color:#334155;'>data points</span>"
        f"</div>",
        unsafe_allow_html=True,
    )
    for warn in flare_scan.get("warnings", []):
        st.markdown(
            f"<div class='warn-box' style='font-size:0.82rem; padding:10px 14px;'>⚠️ {_html.escape(str(warn))}</div>",
            unsafe_allow_html=True,
        )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Yield Hero Cards ──────────────────────────────────────────────────────────
model_data = latest.get("models") or {}
opps       = model_data.get(profile, [])

render_section_header("Estimated Yield", "Projected returns based on your top-3 ranked opportunities")
render_yield_hero_cards(positions, opps, portfolio_size)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Top Opportunities ─────────────────────────────────────────────────────────
_prof_label   = profile_cfg.get("label", profile.capitalize())
_prof_apy_low = profile_cfg.get("target_apy_low", 0)
_prof_apy_hi  = profile_cfg.get("target_apy_high", 0)
try:
    _prof_apy_low = float(_prof_apy_low)
    _prof_apy_hi  = float(_prof_apy_hi)
except (TypeError, ValueError):
    _prof_apy_low = _prof_apy_hi = 0.0
render_section_header("Top Opportunities", f"{_prof_label} · {_prof_apy_low:.0f}–{_prof_apy_hi:.0f}% target APY")

if not pro_mode:
    st.markdown(
        "<div style='background:rgba(139,92,246,0.06);border:1px solid rgba(139,92,246,0.14);"
        "border-radius:8px;padding:7px 14px;font-size:0.78rem;color:#a78bfa;margin-bottom:10px'>"
        "Beginner mode: showing simplified view. Enable Pro Mode in the sidebar to see Sharpe ratio, "
        "Kelly fraction, HMM state, and full technical detail.</div>",
        unsafe_allow_html=True,
    )

if not opps:
    if not latest.get("completed_at") and not latest.get("run_id"):
        st.info("First scan is starting automatically — please wait ~30 seconds, then click Reload in the sidebar.")
    else:
        st.info("No opportunities found for this risk profile in the latest scan.")
else:
    for i, opp in enumerate(opps[:3]):
        render_opportunity_card(opp, i, color, portfolio_size, weight)

    if len(opps) > 3:
        with st.expander(f"Show all {len(opps)} opportunities"):
            for i, opp in enumerate(opps[3:], start=3):
                render_opportunity_card(opp, i, color, portfolio_size, weight)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Arbitrage Alerts ──────────────────────────────────────────────────────────
render_section_header("Arbitrage Alerts", "Real-time profit opportunities from price differences across platforms")

arb_data = (latest.get("arbitrage") or {}).get(profile, [])
if not arb_data:
    st.markdown(
        "<div style='color:#334155; font-size:0.88rem; padding:16px 0;'>"
        "No significant arbitrage detected right now.</div>",
        unsafe_allow_html=True,
    )
else:
    for arb in arb_data[:5]:
        try:
            profit = float(arb.get("estimated_profit") or 0)
        except (TypeError, ValueError):
            profit = 0.0
        urgency       = arb.get("urgency", "monitor")
        label         = _html.escape(str(arb.get("strategy_label", arb.get("strategy", "Arb"))))
        desc          = _html.escape(str(arb.get("plain_english", "—")))
        token         = _html.escape(str(arb.get("token_or_pair", "—")))
        st.markdown(f"""
        <div class="arb-tag">
            <div style="display:flex; justify-content:space-between; align-items:center;">
                <span style="font-weight:700; color:#f1f5f9;">⚡ {label} · {token}</span>
                {render_urgency_badge(urgency)}
            </div>
            <div style="color:#94a3b8; font-size:0.88rem; margin-top:8px;">{desc}</div>
            <div style="color:#475569; font-size:0.8rem; margin-top:8px;">
                Estimated profit: <span style="color:#10b981; font-weight:700;">+{profit:.2f}%</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

# ── FXRP Ecosystem Metrics ────────────────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
render_section_header("FXRP Ecosystem", "FAssets live metrics · March 2026")
_fxrp_cols = st.columns(4)

# Wire live values where available; fall back to known estimates.
try:
    _fxrp_circ_raw = float(
        latest.get("fasset", {}).get("assets", {}).get("FXRP", {}).get("circulating") or 0
    )
except (TypeError, ValueError):
    _fxrp_circ_raw = 0.0
if _fxrp_circ_raw >= 1_000_000:
    _fxrp_minted_str = f"{_fxrp_circ_raw / 1_000_000:.1f}M+"
elif _fxrp_circ_raw >= 1_000:
    _fxrp_minted_str = f"{_fxrp_circ_raw / 1_000:.0f}K+"
elif _fxrp_circ_raw > 0:
    _fxrp_minted_str = f"{int(_fxrp_circ_raw):,}+"
else:
    _fxrp_minted_str = "~12.5M"   # baseline estimate when scan not yet run

try:
    from config import PROTOCOLS as _PROTOS
    _live_protocol_count = len([p for p in _PROTOS.values() if p.get("live", True)])
    _active_protocols_str = f"{_live_protocol_count}+"
except Exception:
    _active_protocols_str = "13+"

_fxrp_stats = [
    ("FXRP Minted",        _fxrp_minted_str,     "Total FXRP in circulation (live from scan)"),
    ("In Active DeFi",     "~89%",                "Share of FXRP deployed in DeFi protocols"),
    ("FAssets Incentives", "2.2B FLR",            "Total rFLR distributing over 12 months"),
    ("Active Protocols",   _active_protocols_str, "Flare DeFi protocols tracked by this model"),
]
for col, (label, value, tip) in zip(_fxrp_cols, _fxrp_stats):
    with col:
        st.markdown(
            f"<div style='background:rgba(30,41,59,0.7); border:1px solid rgba(99,102,241,0.25); "
            f"border-radius:10px; padding:14px 16px; text-align:center;' title='{tip}'>"
            f"<div style='font-size:0.68rem; font-weight:600; color:#64748b; text-transform:uppercase; "
            f"letter-spacing:0.06em; margin-bottom:4px;'>{label}</div>"
            f"<div style='font-size:1.3rem; font-weight:700; color:#e2e8f0;'>{value}</div>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ── Footer ────────────────────────────────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

warnings = latest.get("warnings", [])
if warnings:
    with st.expander("⚠️ Data Quality Notes"):
        for w in warnings:
            st.markdown(f"- {w}")

st.markdown(
    "<div style='color:#1e293b; font-size:0.70rem; text-align:center; padding-top:4px; line-height:1.7;'>"
    "Flare DeFi Model · Blazeswap · SparkDEX · Ēnosys · Kinetic · Clearpool · Spectra · Upshift · Mystic · Hyperliquid · Flamix · Firelight · Cyclo · Sceptre · Kinza · OrbitalX<br>"
    "<span style='color:#64748b;'>Not financial advice · Always DYOR</span>"
    "</div>",
    unsafe_allow_html=True,
)

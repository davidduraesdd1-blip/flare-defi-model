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
    """Remove API keys, PII, and expected-noise events from Sentry before sending."""
    _msg = str(event.get("message", "") or "")
    _exc_values = (event.get("exception") or {}).get("values") or []
    _exc_msgs = " ".join(str((v.get("value") or "")) for v in _exc_values)
    _combined = (_msg + " " + _exc_msgs).lower()

    # Anthropic credit exhaustion — billing issue, already handled in code
    if "credit balance" in _combined and ("anthropic" in _combined or "400" in _combined):
        return None

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
    render_welcome_banner, signal_badge_html, render_what_this_means,
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
    except ImportError:
        return True   # database module missing — new install, no DB to check yet
    except Exception:
        return False  # DB module exists but check raised — treat as integrity failure


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
user_level    = ctx.get("user_level", "beginner")

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

# ── Welcome Banner (Phase 2, item 6) — beginner only, once per session ────────
render_welcome_banner()

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

# ── Market Environment Banner (4-layer composite signal) ─────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def _dash_composite_signal() -> dict:
    try:
        from models.composite_signal import compute_composite_signal as _ccs
        from macro_feeds import fetch_all_macro_data as _fmac, fetch_coinmetrics_onchain as _foc, fetch_btc_ta_signals as _fta
        return _ccs(macro_data=_fmac(), onchain_data=_foc(days=400), ta_data=_fta())
    except Exception:
        return {}

try:
    _d_csig = _dash_composite_signal()
    if _d_csig and _d_csig.get("score", 0) != 0.0:
        _d_score  = _d_csig.get("score", 0.0)
        _d_signal = _d_csig.get("signal", "NEUTRAL").replace("_", " ")
        if _d_score >= 0.3:   _d_col, _d_bg = "#22c55e", "rgba(34,197,94,0.07)"
        elif _d_score >= 0.1: _d_col, _d_bg = "#00d4aa", "rgba(0,212,170,0.07)"
        elif _d_score >= -0.1: _d_col, _d_bg = "#f59e0b", "rgba(245,158,11,0.07)"
        elif _d_score >= -0.3: _d_col, _d_bg = "#f97316", "rgba(249,115,22,0.07)"
        else:                  _d_col, _d_bg = "#ef4444", "rgba(239,68,68,0.07)"
        _d_txt   = _d_csig.get("beginner_summary", _d_signal)
        _d_shape = "▲" if _d_score >= 0.10 else ("▼" if _d_score <= -0.10 else "■")
        _d_layers = _d_csig.get("layers", {})
        _d_weights = _d_csig.get("weights_applied", {"technical": 0.20, "macro": 0.20, "sentiment": 0.25, "onchain": 0.35})
        def _dfmt(v): return f"+{v:.2f}" if v >= 0 else f"{v:.2f}"
        _d_ta = _d_layers.get("technical", {}).get("score", 0)
        _d_ma = _d_layers.get("macro", {}).get("score", 0)
        _d_se = _d_layers.get("sentiment", {}).get("score", 0)
        _d_oc = _d_layers.get("onchain", {}).get("score", 0)
        _d_xai = [("Technical", _d_ta, _d_weights.get("technical", 0.20)),
                  ("Macro",     _d_ma, _d_weights.get("macro",     0.20)),
                  ("Sentiment", _d_se, _d_weights.get("sentiment", 0.25)),
                  ("On-Chain",  _d_oc, _d_weights.get("onchain",   0.35))]
        _d_dir  = 1 if _d_score > 0 else (-1 if _d_score < 0 else 0)
        _d_agree = sum(1 for _, s, _ in _d_xai if (s > 0.05) == (_d_dir > 0) and _d_dir != 0)
        _d_conf  = {4: "HIGH", 3: "HIGH", 2: "MEDIUM", 1: "LOW", 0: "LOW"}.get(_d_agree, "MEDIUM")
        _d_conf_c = {"HIGH": "#22c55e", "MEDIUM": "#f59e0b", "LOW": "#ef4444"}[_d_conf]

        if user_level != "beginner":
            st.html(
                f"<div style='background:{_d_bg};border:1px solid {_d_col}33;"
                f"border-left:4px solid {_d_col};border-radius:8px;padding:10px 18px;"
                f"margin-bottom:4px;display:flex;align-items:center;gap:24px;flex-wrap:wrap;'>"
                f"<div><span style='color:#64748b;font-size:0.72rem;text-transform:uppercase;letter-spacing:0.06em;'>Market Environment</span>"
                f"<div style='color:{_d_col};font-weight:800;font-size:1.05rem;'>{_d_shape} {_d_signal}</div>"
                f"<div style='color:#64748b;font-size:0.76rem;'>Score {_dfmt(_d_score)} &nbsp;·&nbsp; "
                f"<span style='color:{_d_conf_c};font-weight:600;'>{_d_conf} CONFIDENCE</span></div></div>"
                f"<div style='color:#475569;font-size:0.78rem;border-left:1px solid #1e293b;padding-left:20px;'>"
                f"<div>Technical <span style='color:{'#22c55e' if _d_ta>=0 else '#ef4444'};font-weight:600;'>{_dfmt(_d_ta)}</span></div>"
                f"<div>Macro <span style='color:{'#22c55e' if _d_ma>=0 else '#ef4444'};font-weight:600;'>{_dfmt(_d_ma)}</span></div>"
                f"<div>Sentiment <span style='color:{'#22c55e' if _d_se>=0 else '#ef4444'};font-weight:600;'>{_dfmt(_d_se)}</span></div>"
                f"<div>On-Chain <span style='color:{'#22c55e' if _d_oc>=0 else '#ef4444'};font-weight:600;'>{_dfmt(_d_oc)}</span></div>"
                f"</div></div>"
            )
        else:
            st.html(
                f"<div style='background:{_d_bg};border:1px solid {_d_col}33;"
                f"border-left:4px solid {_d_col};border-radius:8px;padding:12px 18px;"
                f"margin-bottom:4px;'>"
                f"<span style='color:{_d_col};font-weight:700;font-size:0.9rem;'>{_d_shape} Market Conditions</span>"
                f"<span style='color:#94a3b8;font-size:0.84rem;margin-left:12px;'>{_d_txt}</span>"
                f"<span style='margin-left:16px;background:{_d_conf_c}22;color:{_d_conf_c};"
                f"font-size:0.72rem;font-weight:700;padding:2px 8px;border-radius:10px;"
                f"border:1px solid {_d_conf_c}44;'>{_d_conf} CONFIDENCE</span>"
                f"</div>"
            )

        # XAI breakdown expander
        with st.expander("🔍 Why this signal? — Signal driver breakdown", expanded=False):
            _xai_rows = ""
            for _xn, _xs, _xw in _d_xai:
                _xwc = _xs * _xw
                _xbar_w = min(abs(_xwc) * 250, 100)
                _xbar_c = "#22c55e" if _xwc >= 0 else "#ef4444"
                _xai_rows += (
                    f"<div style='display:flex;align-items:center;gap:10px;margin:5px 0;'>"
                    f"<div style='width:90px;font-size:0.78rem;color:#cbd5e1;'>{_xn}</div>"
                    f"<div style='width:40px;font-size:0.7rem;color:#64748b;text-align:right;'>{_xw*100:.0f}%</div>"
                    f"<div style='flex:1;background:#1e293b;border-radius:3px;height:14px;overflow:hidden;'>"
                    f"<div style='width:{_xbar_w:.0f}%;background:{_xbar_c};height:100%;border-radius:3px;'></div></div>"
                    f"<div style='width:55px;font-size:0.78rem;font-weight:600;color:{_xbar_c};text-align:right;'>{_xwc*100:+.1f}%</div>"
                    f"</div>"
                )
            _xai_note = ("Each bar shows how much that factor pushed the signal bullish (+) or bearish (−)."
                         if user_level == "beginner"
                         else f"Weighted contributions · regime: {_d_csig.get('regime', 'N/A')} · weights are regime-adjusted.")
            st.html(f"<div style='padding:4px 0 8px;'>"
                    f"<div style='font-size:0.72rem;color:#64748b;margin-bottom:8px;'>{_xai_note}</div>"
                    f"{_xai_rows}</div>")
except Exception as _d_cs_err:
    import logging as _dlg; _dlg.getLogger(__name__).debug("[Dashboard] composite signal error: %s", _d_cs_err)

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
render_what_this_means(
    "These numbers show how much you could earn from the top 3 opportunities if you invested your "
    "full portfolio. Weekly = what you'd earn in one week. Monthly = one month. Yearly = one full year. "
    "These are estimates — actual returns depend on fees, price changes, and how long you stay invested.",
    title="What are these yield estimates?",
    intermediate_message="Est. returns from your top-3 opportunities assuming full allocation — actual results vary.",
)

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

render_what_this_means(
    "Each card is a place to put your money to earn interest or trading fees. "
    "The big % number is the APY — how much you'd earn per year if nothing changes. "
    "The Grade (A–F) tells you how safe it is: A = safest, F = riskiest. "
    "The 'Confidence' bar shows how sure the model is about this opportunity. "
    "Start with Grade A or B to keep risk low.",
    title="How do I read these opportunity cards?",
    intermediate_message="APY = annualised return. Grade A–F = safety (A safest). Confidence = model conviction. Gauges show numeric values.",
)

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
    render_what_this_means(
        "Arbitrage means buying something cheap in one place and selling it for more somewhere else — "
        "all in a single transaction. The 'Estimated profit' shows the % gain per trade. "
        "ACT NOW = opportunity closes fast. MONITOR = watch it, may be ready soon. "
        "These opportunities are usually short-lived — the model flags them as soon as they appear.",
        title="What is arbitrage?",
        intermediate_message="Cross-protocol arb: buy low on one DEX, sell high on another in one tx. ACT NOW = closes fast.",
    )

# ── FXRP Ecosystem Metrics ────────────────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
render_section_header("FXRP Ecosystem", "FAssets live metrics · April 2026")
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

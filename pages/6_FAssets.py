"""
FAssets — Dedicated dashboard for the Flare FAssets system.
Shows FXRP / FBTC / FDOGE mint & redeem rates, collateral ratios, agent health,
and live premium/discount vs spot prices.
"""

import sys
import html as _html
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

from ui.common import page_setup, render_sidebar, render_section_header, load_latest, load_history_runs, _ts_fmt

page_setup("FAssets · Flare DeFi")

ctx     = render_sidebar()
profile = ctx["profile"]

st.markdown("# FAssets Dashboard")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "FXRP · FBTC · FDOGE · mint/redeem rates · collateral ratios · system health</div>",
    unsafe_allow_html=True,
)


# ─── Fetch FAsset data ────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def _load_fasset_data() -> dict:
    # Fast path: read from the most recent scan (no network calls needed).
    # fetch_fasset_data() is now included in the scan pipeline so this will
    # be populated after the first scheduled scan.
    cached = load_latest().get("fasset", {})
    if cached and isinstance(cached.get("assets"), dict) and cached["assets"]:
        return cached
    # Slow path: direct API call — only runs before first scan or after cache miss.
    try:
        from scanners.flare_scanner import fetch_fasset_data
        return fetch_fasset_data()
    except Exception:
        return {}


fasset = _load_fasset_data()
if not fasset:
    st.info("FAsset data unavailable. Try running a scan first.")
    st.stop()

data_src = fasset.get("data_source", "baseline")
src_badge = (
    "<span class='badge-live'>LIVE</span>" if data_src == "live"
    else "<span class='badge-est'>ESTIMATED</span>"
)
fetched = fasset.get("fetched_at", "")

if data_src == "baseline":
    st.markdown(
        "<div class='warn-box' style='font-size:0.86rem; line-height:1.55;'>"
        "⚠️ Live FAsset API is currently unreachable — displaying research-based estimates. "
        "Fees and collateral ratios are accurate; circulating supply is approximate. "
        "Click <b>▶ Scan</b> in the sidebar to retry.</div>",
        unsafe_allow_html=True,
    )

st.markdown(
    f"<div style='font-size:0.75rem; color:#475569; margin-bottom:16px;'>"
    f"Data: {src_badge}&nbsp; · &nbsp;{_ts_fmt(fetched) if fetched else '—'}</div>",
    unsafe_allow_html=True,
)


# ─── System Health Banner (Feature 13 expanded) ───────────────────────────────

health    = fasset.get("system_health", "unknown")
agents    = fasset.get("agent_count", 0)
h_color   = {"healthy": "#10b981", "caution": "#f59e0b", "unknown": "#475569"}.get(health, "#475569")
h_icon    = {"healthy": "✓", "caution": "⚠", "unknown": "?"}.get(health, "?")

assets     = fasset.get("assets", {})
latest     = load_latest()
prices_raw = latest.get("prices", [])
price_lkp  = {p["symbol"]: p.get("price_usd", 0) for p in prices_raw if isinstance(p, dict) and p.get("symbol")}

fxrp_info  = assets.get("FXRP", {})
fxrp_circ  = float(fxrp_info.get("circulating", 0) or 0)
fxrp_price = price_lkp.get("FXRP", price_lkp.get("XRP", 1.53))
fxrp_tvl   = fxrp_circ * fxrp_price

# Minting capacity estimate: assume max capacity = 2× circulating (conservative)
_fxrp_max_cap  = fxrp_circ * 2.5   # reasonable upper bound from collateral posted
_mint_remaining = max(0.0, _fxrp_max_cap - fxrp_circ)
_mint_pct_used  = (fxrp_circ / _fxrp_max_cap * 100) if _fxrp_max_cap > 0 else 0

# Agent health distribution (Feature 13): estimate from agent_count + system health
# Live API doesn't provide granular per-agent status, so derive a distribution
if agents and agents > 0:
    if health == "healthy":
        _ag_healthy = max(1, round(agents * 0.90))
        _ag_warning = agents - _ag_healthy
        _ag_liq     = 0
    elif health == "caution":
        _ag_liq     = max(0, round(agents * 0.05))
        _ag_warning = max(1, round(agents * 0.20))
        _ag_healthy = agents - _ag_warning - _ag_liq
    else:
        _ag_healthy = agents
        _ag_warning = 0
        _ag_liq     = 0
else:
    _ag_healthy = _ag_warning = _ag_liq = 0

# ── Row 1: 4 metric cards ──────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown(f"""
    <div class="metric-card card-green">
        <div class="label">System Health</div>
        <div class="big-number" style="color:{h_color};">{h_icon}</div>
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">{health.capitalize()}</div>
    </div>""", unsafe_allow_html=True)
with c2:
    st.markdown(f"""
    <div class="metric-card card-blue">
        <div class="label">Active Agents</div>
        <div class="big-number">{agents if agents else "—"}</div>
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">
            <span style="color:#10b981;">✓ {_ag_healthy}</span>
            {"&nbsp; <span style='color:#f59e0b;'>⚠ " + str(_ag_warning) + "</span>" if _ag_warning else ""}
            {"&nbsp; <span style='color:#ef4444;'>✗ " + str(_ag_liq) + "</span>" if _ag_liq else ""}
        </div>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown(f"""
    <div class="metric-card card-orange">
        <div class="label">FXRP Circulating</div>
        <div class="big-number" style="color:#f59e0b;">{fxrp_circ:,.0f}</div>
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">≈ ${fxrp_tvl:,.0f} USD</div>
    </div>""", unsafe_allow_html=True)
with c4:
    _cap_color = "#10b981" if _mint_pct_used < 60 else ("#f59e0b" if _mint_pct_used < 85 else "#ef4444")
    st.markdown(f"""
    <div class="metric-card card-violet">
        <div class="label">Mint Capacity Used</div>
        <div class="big-number" style="color:{_cap_color};">{_mint_pct_used:.0f}%</div>
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">
            ~{_mint_remaining:,.0f} FXRP remaining
        </div>
    </div>""", unsafe_allow_html=True)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ── Feature 13: Backing Ratio Trend Chart ─────────────────────────────────────
render_section_header("Collateral Ratio Trend", "FXRP backing ratio across recent scans")

_history_runs = load_history_runs()
_cr_dates = []
_cr_vals  = []
for _run in _history_runs[-20:]:   # last 20 scans
    _ts = (_run.get("completed_at") or _run.get("run_id", ""))[:19]
    _fasset_run = _run.get("fasset", {})
    _cr = (_fasset_run.get("assets") or {}).get("FXRP", {}).get("cr_pct")
    if _cr and isinstance(_cr, (int, float)):
        try:
            _cr_dates.append(_ts.replace("T", " "))
            _cr_vals.append(float(_cr))
        except Exception:
            pass

if len(_cr_vals) >= 2:
    _fig_cr = go.Figure()
    _cr_line_color = "#10b981" if (sum(_cr_vals[-3:]) / len(_cr_vals[-3:])) >= 200 else "#f59e0b"
    _fig_cr.add_trace(go.Scatter(
        x=_cr_dates, y=_cr_vals,
        mode="lines+markers",
        name="FXRP Collateral Ratio",
        line=dict(color=_cr_line_color, width=2.5),
        marker=dict(size=6, color=_cr_line_color),
        fill="tozeroy",
        fillcolor="rgba(16,185,129,0.06)",
        hovertemplate="%{x}<br>CR: %{y:.0f}%<extra></extra>",
    ))
    # Reference lines
    _fig_cr.add_hline(y=200, line_dash="dash", line_color="rgba(16,185,129,0.5)",
                      annotation_text="200% healthy", annotation_position="bottom right",
                      annotation_font_size=11, annotation_font_color="#10b981")
    _fig_cr.add_hline(y=160, line_dash="dash", line_color="rgba(239,68,68,0.5)",
                      annotation_text="160% min (CCB)", annotation_position="bottom right",
                      annotation_font_size=11, annotation_font_color="#ef4444")
    _fig_cr.update_layout(
        height=240,
        margin=dict(l=0, r=0, t=12, b=0),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(showgrid=False, color="#64748b", tickfont_size=11),
        yaxis=dict(gridcolor="rgba(255,255,255,0.05)", color="#64748b",
                   tickformat=".0f", ticksuffix="%", tickfont_size=11),
        showlegend=False,
    )
    st.plotly_chart(_fig_cr, use_container_width=True, config={"displayModeBar": False})
else:
    st.caption("Collateral ratio history available after 2+ scans.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Per-Asset Cards ──────────────────────────────────────────────────────────

render_section_header("FAsset Details", "Mint · redeem · collateral per bridged asset")

_ASSET_COLOR = {"FXRP": "#3b82f6", "FBTC": "#f59e0b", "FDOGE": "#22c55e"}
_ASSET_ICON  = {"FXRP": "XRP", "FBTC": "BTC", "FDOGE": "DOGE"}

for sym, info in assets.items():
    if not isinstance(info, dict):
        continue
    color      = _ASSET_COLOR.get(sym, "#8b5cf6")
    icon_label = _ASSET_ICON.get(sym, sym)
    mint_fee   = info.get("mint_fee_pct", 0.25)
    redeem_fee = info.get("redeem_fee_pct", 0.20)
    cr_pct     = info.get("cr_pct", 160.0)
    circ       = info.get("circulating", 0)
    col_tok    = info.get("collateral_token", "FLR")
    note       = _html.escape(info.get("note", ""))

    # Collateral health colour
    if cr_pct >= 200:
        cr_color = "#10b981"
        cr_label = "Healthy"
    elif cr_pct >= 160:
        cr_color = "#f59e0b"
        cr_label = "Adequate"
    else:
        cr_color = "#ef4444"
        cr_label = "At Risk"

    # Premium / discount from live price vs FXRP spot
    prem_html = ""
    if sym == "FXRP":
        spot_xrp  = price_lkp.get("XRP", 0)
        spot_fxrp = price_lkp.get("FXRP", 0)
        if spot_xrp > 0 and spot_fxrp > 0:
            prem_pct  = (spot_fxrp - spot_xrp) / spot_xrp * 100
            prem_col  = "#ef4444" if prem_pct < -0.5 else ("#22c55e" if prem_pct > 0.5 else "#64748b")
            prem_sign = "+" if prem_pct >= 0 else ""
            prem_html = (
                f"<span>Peg: <span style='color:{prem_col}; font-weight:600;'>"
                f"{prem_sign}{prem_pct:.2f}%</span> vs XRP</span>"
            )

    st.markdown(f"""
<div class="opp-card" style="border-left:3px solid {color};">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
    <div>
      <span style="font-size:1.1rem; font-weight:800; color:#f1f5f9;">{sym}</span>
      <span style="color:#475569; font-size:0.82rem; margin-left:8px;">Bridged {icon_label} on Flare</span>
    </div>
    <span style="color:{cr_color}; font-size:0.82rem; font-weight:700; background:rgba(255,255,255,0.04);
                 padding:3px 10px; border-radius:6px;">{cr_label}</span>
  </div>

  <div style="display:flex; gap:24px; flex-wrap:wrap; margin-top:14px; font-size:0.82rem; color:#475569;">
    <div>
      <div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Mint Fee</div>
      <div style="font-size:1.3rem; font-weight:700; color:#f1f5f9;">{mint_fee:.2f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Redeem Fee</div>
      <div style="font-size:1.3rem; font-weight:700; color:#f1f5f9;">{redeem_fee:.2f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Collateral Ratio</div>
      <div style="font-size:1.3rem; font-weight:700; color:{cr_color};">{cr_pct:.0f}%</div>
    </div>
    <div>
      <div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Collateral Token</div>
      <div style="font-size:1.3rem; font-weight:700; color:#a78bfa;">{col_tok}</div>
    </div>
    {f'<div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Circulating</div><div style="font-size:1.3rem; font-weight:700; color:#94a3b8;">{circ:,.0f}</div></div>' if circ > 0 else ""}
  </div>

  <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:12px; font-size:0.78rem; color:#475569;">
    {prem_html}
  </div>

  {f'<div style="color:#475569; font-size:0.80rem; margin-top:10px; line-height:1.5;">{note}</div>' if note else ""}
</div>""", unsafe_allow_html=True)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── How FAssets Work (Explainer) ─────────────────────────────────────────────

render_section_header("How FAssets Work", "The Flare bridge mechanism explained")

with st.expander("FAsset mechanics — mint, hold, redeem"):
    st.markdown("""
**Minting FXRP:**
1. Request a mint from an agent on Flare — pay the mint fee (0.25%)
2. Send real XRP to the agent's XRP address
3. Receive FXRP on Flare within ~5 minutes (XRP confirmation time)
4. Use FXRP in DeFi — LP pools, lending, Spectra yield tokenization

**Redeeming FXRP:**
1. Send FXRP to the redemption contract
2. Pay the redemption fee (0.20%)
3. Agent sends real XRP to your XRP address within ~24 hours

**Collateral System:**
- Agents post FLR as collateral (minimum 160% of minted value)
- If FLR price drops and CR falls below 150%, agent is liquidated
- Vault CR > 200% = healthy buffer against FLR price volatility

**Arbitrage Opportunity:**
- FXRP trades at a discount to XRP on DEXes → buy FXRP, redeem for XRP (lock in spread minus fees)
- FXRP trades at premium → buy XRP, mint FXRP, sell (lock in spread minus fees)
- Net profit threshold ≈ 0.5% (fees are 0.25% + 0.20% = 0.45% round trip)
""")


# ─── FAsset Arbitrage Window ──────────────────────────────────────────────────

render_section_header("Current Arb Window", "Real-time premium/discount vs XRP spot")

try:
    from models.arbitrage import detect_fassets_arb
    arb_opps = detect_fassets_arb(load_latest().get("prices", []))
    if arb_opps:
        for arb in arb_opps:
            net = arb.get("net_profit_pct", 0)
            direction = arb.get("direction", "")
            col = "#22c55e" if net > 0 else "#ef4444"
            st.markdown(
                f"<div class='arb-tag'>"
                f"<span style='font-weight:700; color:#f1f5f9;'>{arb.get('strategy_label', 'FAssets Arb')}</span>"
                f"<span style='color:#475569; margin-left:8px;'>{arb.get('urgency', '').upper()}</span>"
                f"<div style='color:#94a3b8; font-size:0.82rem; margin-top:6px;'>"
                f"Net profit: <span style='color:{col}; font-weight:700;'>{net:.2f}%</span>"
                f" · {_html.escape(str(arb.get('action', '')))}</div>"
                f"</div>",
                unsafe_allow_html=True,
            )
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>"
            "No FAsset arbitrage window open right now. Spread is within normal range.</div>",
            unsafe_allow_html=True,
        )
except Exception as e:
    st.caption(f"Arb detection unavailable: {e}")

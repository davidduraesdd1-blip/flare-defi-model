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

from ui.common import page_setup, render_sidebar, render_section_header, load_latest, _ts_fmt

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

@st.cache_data(ttl=120)
def _load_fasset_data() -> dict:
    try:
        from scanners.flare_scanner import fetch_fasset_data
        return fetch_fasset_data()
    except Exception as e:
        st.warning(f"Could not load FAsset data: {e}")
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


# ─── System Health Banner ─────────────────────────────────────────────────────

health    = fasset.get("system_health", "unknown")
agents    = fasset.get("agent_count", 0)
h_color   = {"healthy": "#10b981", "caution": "#f59e0b", "unknown": "#475569"}.get(health, "#475569")
h_icon    = {"healthy": "✓", "caution": "⚠", "unknown": "?"}.get(health, "?")

assets     = fasset.get("assets", {})
latest     = load_latest()
prices_raw = latest.get("prices", [])
price_lkp  = {p.get("symbol", ""): p.get("price_usd", 0) for p in prices_raw}

fxrp_circ  = assets.get("FXRP", {}).get("circulating", 0)
fxrp_price = price_lkp.get("FXRP", price_lkp.get("XRP", 1.53))
fxrp_tvl   = fxrp_circ * fxrp_price

c1, c2, c3 = st.columns(3)
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
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">minting agents</div>
    </div>""", unsafe_allow_html=True)
with c3:
    st.markdown(f"""
    <div class="metric-card card-orange">
        <div class="label">FXRP Circulating</div>
        <div class="big-number" style="color:#f59e0b;">{fxrp_circ:,.0f}</div>
        <div style="color:#475569; font-size:0.82rem; margin-top:4px;">
            ≈ ${fxrp_tvl:,.0f} USD
        </div>
    </div>""", unsafe_allow_html=True)

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

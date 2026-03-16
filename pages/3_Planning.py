"""
Planning — Income planner, Spectra fixed-rate, FTSO delegation, FAssets tracker.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime

from ui.common import page_setup, render_sidebar
from config import FALLBACK_PRICES

page_setup("Planning · Flare DeFi")

render_sidebar()

st.markdown("# Planning Tools")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Income planner · fixed-rate lock · FTSO delegation · FAssets</div>",
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4 = st.tabs([
    "💰  Income Planner",
    "🔒  Spectra Fixed-Rate",
    "📡  FTSO Delegation",
    "🌐  FAssets",
])


# ─── Tab 1: FlareDrop Income Planner ─────────────────────────────────────────

with tab1:
    st.markdown("### FlareDrop Income Replacement")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Flare's 2.2B FLR distribution ends July 2026. Find out how much capital "
        "you need in each strategy to replace that income.</div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        monthly_flr = st.number_input("Monthly FlareDrop income (FLR/month)",
                                      min_value=0.0, value=500.0, step=50.0, key="fd_flr")
    with c2:
        flr_price = st.number_input("FLR price ($)", min_value=0.001, value=float(FALLBACK_PRICES["FLR"]),
                                    step=0.001, format="%.4f", key="fd_price")

    if monthly_flr > 0 and flr_price > 0:
        monthly_usd = monthly_flr * flr_price
        annual_usd  = monthly_usd * 12

        c1, c2, c3 = st.columns(3)
        for col, label, val, cls in [
            (c1, "Monthly FLR Income", f"{monthly_flr:,.0f} FLR", "card-orange"),
            (c2, "Monthly in USD",     f"${monthly_usd:,.2f}",    "card-blue"),
            (c3, "Annual in USD",      f"${annual_usd:,.2f}",     "card-green"),
        ]:
            with col:
                st.markdown(f"""
                <div class="metric-card {cls}">
                    <div class="label">{label}</div>
                    <div class="big-number">{val}</div>
                </div>""", unsafe_allow_html=True)

        strategies = [
            ("sFLR Staking (Sceptre)",       9.0,  "None",   "Stake FLR → earn sFLR + FTSO rewards"),
            ("FTSO Delegation",              4.3,  "None",   "Delegate vote power, keep your FLR"),
            ("Kinetic Lending (USDT0)",      8.0,  "None",   "Lend stablecoins, no price risk"),
            ("Clearpool X-Pool (USD0)",     11.5,  "None",   "Institutional lending, higher yield"),
            ("Blazeswap LP (sFLR-WFLR)",    37.0,  "Low",    "Provide liquidity, earn fees + rewards"),
            ("Mystic Finance (USD0)",        9.0,  "None",   "Morpho-style optimised lending"),
        ]

        rows = []
        for name, apy, il, action in strategies:
            rows.append({
                "Strategy":       name,
                "APY":            f"{apy:.1f}%",
                "IL Risk":        il,
                "Capital Needed": f"${annual_usd / (apy / 100):,.0f}",
                "How To":         action,
            })
        st.markdown(
            f"<div style='color:#94a3b8; font-size:0.88rem; margin:16px 0 10px;'>"
            f"Capital needed to replace <b>${monthly_usd:,.2f}/month</b>:</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("Capital = Annual income ÷ APY. Diversify across 2–3 strategies.")


# ─── Tab 2: Spectra Fixed-Rate ────────────────────────────────────────────────

with tab2:
    st.markdown("### Spectra Fixed-Rate Lock")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Lock sFLR at 10.79% fixed until May 17, 2026 via Spectra Finance. "
        "Compare against variable staking (7–11%) and LP (~36.74%).</div>",
        unsafe_allow_html=True,
    )

    maturity_date    = datetime(2026, 5, 17)
    days_to_maturity = max(0, (maturity_date - datetime.utcnow()).days)

    c1, c2 = st.columns([2, 1])
    with c1:
        sflr_amount = st.number_input("sFLR to lock", min_value=0.0, value=1000.0, step=100.0, key="spectra_amt")
    with c2:
        st.markdown(
            f"<div style='color:#64748b; font-size:0.88rem; padding-top:28px;'>"
            f"Days to maturity: <span style='color:#f1f5f9; font-weight:600;'>{days_to_maturity}</span></div>",
            unsafe_allow_html=True,
        )

    if sflr_amount > 0 and days_to_maturity > 0:
        fixed_yield = sflr_amount * 0.1079 * days_to_maturity / 365
        var_low     = sflr_amount * 0.07   * days_to_maturity / 365
        var_high    = sflr_amount * 0.11   * days_to_maturity / 365
        lp_yield    = sflr_amount * 0.3674 * days_to_maturity / 365

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""
            <div class="metric-card card-green">
                <div class="label">Fixed Rate (PT-sFLR)</div>
                <div class="big-number" style="color:#10b981;">+{fixed_yield:.2f} sFLR</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">10.79% · Zero IL risk</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div class="metric-card card-orange">
                <div class="label">Variable Staking</div>
                <div class="big-number" style="color:#f59e0b;">+{var_low:.2f}–{var_high:.2f}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">7–11% variable</div>
            </div>""", unsafe_allow_html=True)
        with c3:
            st.markdown(f"""
            <div class="metric-card card-red">
                <div class="label">LP Route (Spectra)</div>
                <div class="big-number" style="color:#ef4444;">+{lp_yield:.2f}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">~36.74% · IL risk</div>
            </div>""", unsafe_allow_html=True)

        st.caption(
            f"Amounts over {days_to_maturity} days. Fixed is guaranteed. "
            "Variable fluctuates. LP subject to impermanent loss."
        )
    elif days_to_maturity == 0:
        st.warning("The sFLR-MAY2026 market has matured. Check Spectra Finance for new markets.")


# ─── Tab 3: FTSO Delegation ───────────────────────────────────────────────────

with tab3:
    st.markdown("### FTSO Delegation Optimizer")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Delegate FLR vote power to earn FTSO rewards every ~3.5 days. "
        "You keep your FLR — delegation does not lock or transfer tokens.</div>",
        unsafe_allow_html=True,
    )

    ftso_providers = [
        {"name": "Ankr",        "reward_rate": 4.5, "uptime": 99.2, "note": "Large global infrastructure"},
        {"name": "AlphaOracle", "reward_rate": 4.4, "uptime": 99.0, "note": "High uptime, consistent rewards"},
        {"name": "SolidiFi",    "reward_rate": 4.2, "uptime": 98.8, "note": "Community-run provider"},
        {"name": "FlareOracle", "reward_rate": 4.3, "uptime": 98.9, "note": "Flare-native provider"},
        {"name": "FTSO EU",     "reward_rate": 4.1, "uptime": 98.5, "note": "European-based node"},
        {"name": "BlockNG",     "reward_rate": 4.0, "uptime": 97.5, "note": "Multi-chain infrastructure"},
    ]

    flr_amount = st.number_input("FLR to delegate", min_value=0.0, value=1000.0, step=100.0, key="ftso_flr")
    if flr_amount > 0:
        rows = [{
            "Provider":          p["name"],
            "Est. Annual Rate":  f"{p['reward_rate']:.1f}%",
            "Uptime":            f"{p['uptime']:.1f}%",
            "Annual FLR Earned": f"{flr_amount * (p['reward_rate']/100):,.1f} FLR",
            "Notes":             p["note"],
        } for p in ftso_providers]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption(
            "Split between 2 providers for coverage. "
            "Delegate at app.flare.network or via Sceptre (earns sFLR rewards too). "
            "Rates are estimated historical averages."
        )


# ─── Tab 4: FAssets ───────────────────────────────────────────────────────────

with tab4:
    st.markdown("### FAssets Yield Tracker")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "FAssets bring BTC, XRP, and other assets on-chain to earn DeFi yields without selling.</div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("""
        <div class="metric-card card-blue">
            <div class="label">FXRP — Live Now</div>
            <div style="font-size:1.3rem; font-weight:700; color:#3b82f6;">4–10% APY</div>
            <div style="color:#475569; font-size:0.83rem; margin-top:8px;">
                Bridge XRP → FXRP via Flare · Deploy in Upshift EarnXRP vault<br>
                Status: <span style="color:#10b981; font-weight:600;">LIVE</span>
            </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown("""
        <div class="metric-card card-orange">
            <div class="label">FBTC — Coming Soon</div>
            <div style="font-size:1.3rem; font-weight:700; color:#f59e0b;">Est. 3–8% APY</div>
            <div style="color:#475569; font-size:0.83rem; margin-top:8px;">
                Bring Bitcoin on-chain · Earn yield without selling BTC<br>
                Status: <span style="color:#f59e0b; font-weight:600;">IN DEVELOPMENT</span>
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("""
**FTSO Collateral Agent Income**

Flare's FAssets system needs collateral agents to mint synthetic assets. Agents earn:
- **Minting fees** — ~0.5% per FXRP minted
- **FTSO rewards** — FLR collateral continues earning FTSO delegation rewards
- **Pool fees** — Share of agent-pool minting revenues

*Requires significant collateral (~$10,000 FLR minimum). See flare.network/fassets for details.*
    """)

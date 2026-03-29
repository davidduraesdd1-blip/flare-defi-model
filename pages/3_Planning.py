"""
Planning — Income planner, Spectra fixed-rate, FTSO delegation, FAssets tracker.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import datetime, timezone

from ui.common import page_setup, render_sidebar, render_section_header
from config import FALLBACK_PRICES
from models.risk_models import (
    calc_il_vs_hodl,
    calc_concentrated_lp_efficiency,
    compute_il_vs_hodl,
    compute_concentrated_lp_metrics,   # #83
)

page_setup("Planning · Flare DeFi")

_ctx      = render_sidebar()
_pro_mode = _ctx.get("pro_mode", False)   # #82 Beginner/Pro mode


# ─── Input Validation Helpers (#13) ──────────────────────────────────────────

def _validate_positive(value: float, name: str) -> tuple[bool, str]:
    """Return (True, '') if value > 0, else (False, error message)."""
    if value <= 0:
        return False, f"{name} must be positive"
    return True, ""


def _validate_range(value: float, min_v: float, max_v: float, name: str) -> tuple[bool, str]:
    """Return (True, '') if min_v <= value <= max_v, else (False, error message)."""
    if not (min_v <= value <= max_v):
        return False, f"{name} must be between {min_v} and {max_v}"
    return True, ""

st.title("📐 Planning Tools")
st.caption("Model income scenarios, lock fixed rates with Spectra, delegate to FTSO, and plan FAssets allocations")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Income planner · fixed-rate lock · FTSO delegation · FAssets</div>",
    unsafe_allow_html=True,
)

tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
    "💰  Income Planner",
    "🔒  Spectra Fixed-Rate",
    "📡  FTSO Delegation",
    "🌐  FAssets",
    "🎯  Strategy Planner",
    "📈  Compound Calculator",
])


# ─── Tab 1: FlareDrop Income Planner ─────────────────────────────────────────

with tab1:
    render_section_header("FlareDrop Income Replacement", "How much capital you need to replace lost FlareDrop income")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Flare's 2.2B FLR distribution <b>ended January 30, 2026</b>. "
        "Find out how much capital you need in each DeFi strategy to replace that lost income.</div>",
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
            ("sFLR Staking (Sceptre)",       4.5,  "None",   "Stake FLR → earn sFLR + FTSO rewards (reduced post-FlareDrop)"),
            ("FTSO Delegation",              4.3,  "None",   "Delegate vote power, keep your FLR"),
            ("Kinetic Lending (USDT0)",      8.0,  "None",   "Lend stablecoins, no price risk"),
            ("Clearpool X-Pool (USD0)",     11.5,  "None",   "Institutional lending, higher yield"),
            ("Clearpool USDX T-Pool",        9.1,  "None",   "T-bill backed, ~$38M TVL, new March 2026"),
            ("earnXRP Vault (Upshift)",      7.0,  "None",   "Deposit FXRP → earn 4–10% via conc. liquidity"),
            ("Blazeswap LP (sFLR-WFLR)",    37.0,  "Low",    "Provide liquidity, earn fees + RFLR rewards"),
            ("Mystic Finance (USD0)",        9.0,  "None",   "Morpho-style optimised lending"),
        ]

        rows = []
        for name, apy, il, action in strategies:
            rows.append({
                "Strategy":       name,
                "APY":            f"{apy:.1f}%",
                "IL Risk":        il,
                "Capital Needed": f"${annual_usd / (apy / 100):,.0f}" if apy > 0 else "N/A",
                "How To":         action,
            })
        st.markdown(
            f"<div style='color:#94a3b8; font-size:0.88rem; margin:16px 0 10px;'>"
            f"Capital needed to replace <b>${monthly_usd:,.2f}/month</b>:</div>",
            unsafe_allow_html=True,
        )
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption("Capital = Annual income ÷ APY. Diversify across 2–3 strategies.")


# ─── Tab 2: Spectra Fixed-Rate ────────────────────────────────────────────────

with tab2:
    render_section_header("Spectra Fixed-Rate Lock", "Lock sFLR at fixed APY vs variable staking vs LP")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Lock sFLR at ~18.6% fixed until May 17, 2026 via Spectra Finance. "
        "Compare against variable staking (4–5%) and LP (~36.74%).</div>",
        unsafe_allow_html=True,
    )

    maturity_date    = datetime(2026, 5, 17, tzinfo=timezone.utc)
    days_to_maturity = max(0, (maturity_date - datetime.now(timezone.utc)).days)

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
        fixed_yield = sflr_amount * 0.186  * days_to_maturity / 365
        var_low     = sflr_amount * 0.04   * days_to_maturity / 365
        var_high    = sflr_amount * 0.05   * days_to_maturity / 365
        lp_yield    = sflr_amount * 0.3674 * days_to_maturity / 365

        c1, c2, c3 = st.columns(3)
        with c1:
            st.markdown(f"""
            <div class="metric-card card-green">
                <div class="label">Fixed Rate (PT-sFLR)</div>
                <div class="big-number" style="color:#10b981;">+{fixed_yield:.2f} sFLR</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">~18.6% · Zero IL risk</div>
            </div>""", unsafe_allow_html=True)
        with c2:
            st.markdown(f"""
            <div class="metric-card card-orange">
                <div class="label">Variable Staking</div>
                <div class="big-number" style="color:#f59e0b;">+{var_low:.2f}–{var_high:.2f}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:6px;">4–5% variable (post-FlareDrop)</div>
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
    render_section_header("FTSO Delegation Optimizer", "Earn FTSO rewards without locking or transferring FLR")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Delegate FLR vote power to earn FTSO rewards every ~3.5 days. "
        "You keep your FLR — delegation does not lock or transfer tokens.</div>",
        unsafe_allow_html=True,
    )

    # Feature 7: Risk-adjusted FTSO delegation optimizer
    # Score = reward_rate × (uptime/100)² × vote_power_factor
    # Vote power cap: providers >2.5% vote power get rewards cut off
    ftso_providers = [
        {"name": "Ankr",        "reward_rate": 4.5, "uptime": 99.2, "vote_power_pct": 8.2,  "note": "Large global infra — ABOVE 2.5% vote power cap ⚠"},
        {"name": "AlphaOracle", "reward_rate": 4.4, "uptime": 99.0, "vote_power_pct": 1.8,  "note": "High uptime, consistent rewards"},
        {"name": "SolidiFi",    "reward_rate": 4.2, "uptime": 98.8, "vote_power_pct": 2.1,  "note": "Community-run, near cap — monitor"},
        {"name": "FlareOracle", "reward_rate": 4.3, "uptime": 98.9, "vote_power_pct": 1.4,  "note": "Flare-native, well under cap"},
        {"name": "FTSO EU",     "reward_rate": 4.1, "uptime": 98.5, "vote_power_pct": 0.9,  "note": "European-based, decentralised"},
        {"name": "BlockNG",     "reward_rate": 4.0, "uptime": 97.5, "vote_power_pct": 0.7,  "note": "Multi-chain infrastructure"},
        {"name": "DelegateXRP", "reward_rate": 4.3, "uptime": 98.7, "vote_power_pct": 1.2,  "note": "XRP community focused"},
        {"name": "OracleDeFi",  "reward_rate": 4.2, "uptime": 98.6, "vote_power_pct": 0.6,  "note": "DeFi-native, low vote power"},
    ]

    _VOTE_CAP = 2.5   # providers above this % have reward eligibility risk

    # Compute risk-adjusted scores
    for p in ftso_providers:
        vp    = p["vote_power_pct"]
        cap_penalty = 0.0 if vp <= _VOTE_CAP else min(1.0, (vp - _VOTE_CAP) / _VOTE_CAP)
        p["risk_adj_rate"]  = round(p["reward_rate"] * (p["uptime"] / 100) ** 2 * (1 - cap_penalty * 0.5), 3)
        p["cap_warning"]    = vp > _VOTE_CAP
        p["cap_risk"]       = "⚠ ABOVE CAP" if vp > _VOTE_CAP else ("⚡ Near cap" if vp > _VOTE_CAP * 0.8 else "✓ OK")

    # Sort by risk-adjusted rate
    ftso_providers.sort(key=lambda x: x["risk_adj_rate"], reverse=True)

    flr_amount = st.number_input("FLR to delegate", min_value=0.0, value=1000.0, step=100.0, key="ftso_flr")

    # Vote power cap warning banner
    over_cap = [p for p in ftso_providers if p["cap_warning"]]
    if over_cap:
        st.markdown(
            f"<div class='warn-box'>"
            f"<span style='font-weight:700; color:#f59e0b;'>⚠ Vote Power Cap Warning</span>"
            f"<div style='color:#94a3b8; font-size:0.83rem; margin-top:4px;'>"
            f"{len(over_cap)} provider(s) exceed the 2.5% vote power cap. "
            f"Flare reduces reward eligibility for over-cap providers — avoid delegating to these: "
            f"<b>{', '.join(p['name'] for p in over_cap)}</b></div></div>",
            unsafe_allow_html=True,
        )

    if flr_amount > 0:
        rows = []
        for p in ftso_providers:
            annual_flr = flr_amount * (p["reward_rate"] / 100)
            risk_flr   = flr_amount * (p["risk_adj_rate"] / 100)
            rows.append({
                "Provider":           p["name"],
                "Raw APY":            f"{p['reward_rate']:.1f}%",
                "Uptime":             f"{p['uptime']:.1f}%",
                "Vote Power":         f"{p['vote_power_pct']:.1f}%",
                "Cap Status":         p["cap_risk"],
                "Risk-Adj APY":       f"{p['risk_adj_rate']:.2f}%",
                "Annual FLR (raw)":   f"{annual_flr:,.1f}",
                "Annual FLR (adj)":   f"{risk_flr:,.1f}",
                "Notes":              p["note"],
            })
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

        # Top recommendation
        top2 = [p for p in ftso_providers if not p["cap_warning"]][:2]
        if len(top2) >= 2:
            st.markdown(
                f"<div style='background:rgba(139,92,246,0.06); border:1px solid rgba(139,92,246,0.14); "
                f"border-radius:10px; padding:12px 16px; font-size:0.84rem; color:#94a3b8; margin-top:10px;'>"
                f"🤖 <span style='color:#a78bfa; font-weight:600;'>Recommendation:</span> "
                f"Split {flr_amount:,.0f} FLR between "
                f"<b style='color:#f1f5f9;'>{top2[0]['name']}</b> ({flr_amount*0.6:,.0f} FLR, "
                f"risk-adj {top2[0]['risk_adj_rate']:.2f}%) and "
                f"<b style='color:#f1f5f9;'>{top2[1]['name']}</b> ({flr_amount*0.4:,.0f} FLR, "
                f"risk-adj {top2[1]['risk_adj_rate']:.2f}%) for coverage.</div>",
                unsafe_allow_html=True,
            )

        st.caption(
            "Risk-Adj APY = Raw APY × Uptime² × vote-power-cap factor. "
            "Split between 2 providers for coverage. "
            "Delegate at app.flare.network or via Sceptre. "
            "Vote power cap: providers >2.5% lose reward eligibility."
        )


# ─── Tab 4: FAssets ───────────────────────────────────────────────────────────

with tab4:
    render_section_header("FAssets Yield Tracker", "BTC · XRP · DOGE on-chain without selling")
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

    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
    st.markdown("""
    <div class="metric-card" style="border-left:3px solid #a78bfa;">
        <div class="label">FDOGE — Beta</div>
        <div style="font-size:1.3rem; font-weight:700; color:#a78bfa;">Est. 2–5% APY</div>
        <div style="color:#475569; font-size:0.83rem; margin-top:8px;">
            Bring Dogecoin on-chain to Flare · Earn DeFi yield on DOGE without selling<br>
            Status: <span style="color:#a78bfa; font-weight:600;">BETA — Limited minting</span>
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


# ─── Tab 5: Intent-Based Strategy Planner (Feature 15) ───────────────────────

with tab5:
    render_section_header("Strategy Planner", "Tell us your goal — get a personalised DeFi plan")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:20px;'>"
        "Answer a few questions and the planner builds a custom strategy using only Flare-native protocols.</div>",
        unsafe_allow_html=True,
    )

    c1, c2 = st.columns(2)
    with c1:
        intent      = st.selectbox("My primary goal is…", [
            "Maximise yield (I accept higher risk)",
            "Stable passive income (low risk)",
            "Replace FlareDrop income",
            "Earn on my XRP without selling",
            "Earn on my BTC without selling",
            "Preserve capital + beat inflation",
            "Learn DeFi with small amount",
        ], key="intent_goal")
        capital     = st.number_input("Capital to deploy ($)", min_value=0.0, value=5000.0, step=500.0, key="intent_capital")
    with c2:
        risk_tol    = st.selectbox("Risk tolerance", ["Low — protect capital", "Medium — balanced", "High — max yield"], key="intent_risk")
        time_horiz  = st.selectbox("Time horizon", ["<3 months", "3–6 months", "6–12 months", "1–2 years", "2+ years"], key="intent_time")
        il_comfort  = st.checkbox("I'm comfortable with impermanent loss risk", value=False, key="intent_il")
        exp_level   = st.selectbox("DeFi experience", ["Beginner", "Intermediate", "Advanced"], key="intent_exp")

    if st.button("Build My Strategy", key="build_strategy_btn", width="stretch", type="primary"):
        # Strategy engine: maps intent + parameters to Flare-native strategies
        _plans = []
        _warnings = []
        _is_low_risk  = "Low" in risk_tol
        _is_high_risk = "High" in risk_tol
        _long_horizon = time_horiz in ("6–12 months", "1–2 years", "2+ years")

        days_to_jul26 = max(0, (datetime(2026, 7, 1, tzinfo=timezone.utc) - datetime.now(timezone.utc)).days)
        _incentive_ok = days_to_jul26 > 60

        if "XRP" in intent:
            _plans = [
                {"protocol": "Upshift EarnXRP", "strategy": "FXRP Vault", "alloc_pct": 60, "apy_est": 7.0, "risk": "Low",
                 "action": "Bridge XRP → FXRP via Flare. Deposit in Upshift EarnXRP vault for 4–10% APY with auto-compounding."},
                {"protocol": "Enosys DEX",      "strategy": "FXRP-USD0 LP", "alloc_pct": 30, "apy_est": 45.0, "risk": "Medium",
                 "action": "Provide FXRP-USD0 liquidity on Enosys for high APY. Accept ~5–15% IL risk."},
                {"protocol": "Kinetic Finance",  "strategy": "FXRP Lending", "alloc_pct": 10, "apy_est": 5.0, "risk": "Low",
                 "action": "Deposit FXRP on Kinetic for lending yield with no IL."},
            ]
        elif "BTC" in intent:
            _plans = [
                {"protocol": "Flare FAssets",   "strategy": "FBTC (Coming Soon)", "alloc_pct": 100, "apy_est": 5.0, "risk": "Medium",
                 "action": "Wait for FBTC launch (in development). Bridge BTC → FBTC → deploy in LP or lending. Monitor flare.network for launch date."},
            ]
            _warnings.append("FBTC is not yet live. Use sFLR staking or Kinetic lending in the meantime.")
        elif "Replace FlareDrop" in intent:
            _plans = [
                {"protocol": "Kinetic Finance",  "strategy": "Stablecoin Lending", "alloc_pct": 40, "apy_est": 8.5, "risk": "Low",
                 "action": "Deposit USDT0 or USD0 on Kinetic for 8–12% APY with zero IL. Most stable income."},
                {"protocol": "Clearpool",        "strategy": "X-Pool USD0",        "alloc_pct": 30, "apy_est": 11.5, "risk": "Low",
                 "action": "Institutional lending pool. 11–14% APY on USD0. Lower TVL than Kinetic — moderate smart-contract risk."},
                {"protocol": "Sceptre",          "strategy": "sFLR Staking",       "alloc_pct": 20, "apy_est": 4.5, "risk": "Low",
                 "action": "Stake FLR for sFLR to earn 4–5% APY + FTSO rewards. Capital grows with FLR price."},
                {"protocol": "FTSO Delegation",  "strategy": "Vote Power",          "alloc_pct": 10, "apy_est": 4.3, "risk": "None",
                 "action": "Delegate remaining FLR vote power for 4.3% APY. Keep your FLR liquid."},
            ]
        elif "capital" in intent.lower() or "inflation" in intent.lower():
            _plans = [
                {"protocol": "Kinetic Finance",  "strategy": "USDT0 Lending",      "alloc_pct": 50, "apy_est": 8.5, "risk": "Low",
                 "action": "Stable dollar-denominated yield. 8–12% beats inflation significantly."},
                {"protocol": "Clearpool",        "strategy": "USDX T-Pool",        "alloc_pct": 30, "apy_est": 9.1, "risk": "Low",
                 "action": "T-bill backed pool. ~$38M TVL. 9–10% on stablecoins."},
                {"protocol": "Sceptre",          "strategy": "sFLR Staking",       "alloc_pct": 20, "apy_est": 4.5, "risk": "Low",
                 "action": "FLR upside + 4.5% staking yield as hedge against inflation."},
            ]
        elif "Learn" in intent or "small" in intent.lower():
            _plans = [
                {"protocol": "Sceptre",          "strategy": "sFLR Staking",       "alloc_pct": 50, "apy_est": 4.5, "risk": "None",
                 "action": "Safest way to earn: stake FLR → get sFLR. Learn how LSTs work with zero IL."},
                {"protocol": "Kinetic Finance",  "strategy": "USDT0 Lending",      "alloc_pct": 30, "apy_est": 8.0, "risk": "Low",
                 "action": "Deposit a stablecoin and earn interest. No price risk."},
                {"protocol": "Blazeswap",        "strategy": "sFLR-WFLR LP",       "alloc_pct": 20, "apy_est": 37.0, "risk": "Low",
                 "action": "Both tokens track FLR price — minimal IL. Good intro to liquidity providing."},
            ]
        elif _is_high_risk or "Maximise" in intent:
            _plans = [
                {"protocol": "Blazeswap",        "strategy": "WFLR-USD0 LP",       "alloc_pct": 40, "apy_est": 133.0, "risk": "High",
                 "action": "Highest available APY on Flare. Full USD0 paired with WFLR — significant IL risk if FLR moves."},
                {"protocol": "Enosys DEX",       "strategy": "FXRP-WFLR LP",       "alloc_pct": 30, "apy_est": 78.0,  "risk": "High",
                 "action": "Two volatile assets — compounding IL risk but high reward."},
                {"protocol": "Hyperliquid",      "strategy": "HLP Vault",          "alloc_pct": 20, "apy_est": 15.0,  "risk": "Medium",
                 "action": "Cross-chain perps liquidity. Market-making yield. Lower IL than DEX LP."},
                {"protocol": "Kinetic Finance",  "strategy": "Stablecoin Lending", "alloc_pct": 10, "apy_est": 8.5,   "risk": "Low",
                 "action": "Stable income anchor for the portfolio."},
            ]
            if _incentive_ok:
                _warnings.append(f"⚠ rFLR incentives expire in {days_to_jul26} days — re-evaluate LP positions by May 2026.")
        else:
            # Medium / stable income default
            _plans = [
                {"protocol": "Kinetic Finance",  "strategy": "USDT0 Lending",      "alloc_pct": 35, "apy_est": 8.5,  "risk": "Low",
                 "action": "Core stable income. 8–12% APY, no price risk."},
                {"protocol": "Sceptre",          "strategy": "sFLR Staking",       "alloc_pct": 25, "apy_est": 4.5,  "risk": "Low",
                 "action": "FLR upside + staking yield. Low risk, liquid."},
                {"protocol": "Blazeswap",        "strategy": "sFLR-WFLR LP",       "alloc_pct": 25, "apy_est": 37.0, "risk": "Low",
                 "action": "Correlated pair LP — low IL, elevated APY."},
                {"protocol": "Clearpool",        "strategy": "X-Pool USD0",        "alloc_pct": 15, "apy_est": 11.5, "risk": "Low",
                 "action": "Institutional yield. Higher than Kinetic, slightly more complex."},
            ]

        # Adjust for IL comfort
        if not il_comfort:
            _had_high = any(p["risk"] == "High" for p in _plans)
            _plans = [p for p in _plans if p["risk"] in ("None", "Low", "Medium")]
            if _had_high:
                _warnings.append("High-IL strategies removed — toggle 'comfortable with IL' to unlock them.")

        # Render the plan
        if _plans:
            total_alloc = sum(p["alloc_pct"] for p in _plans)
            st.markdown("### Your Personalised Strategy")
            for plan in _plans:
                alloc_usd = capital * plan["alloc_pct"] / 100
                risk_color = {"None": "#10b981", "Low": "#22c55e", "Medium": "#f59e0b", "High": "#ef4444"}.get(plan["risk"], "#64748b")
                st.markdown(
                    f"<div class='opp-card' style='border-left:3px solid {risk_color};'>"
                    f"<div style='display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;'>"
                    f"<div><span style='font-weight:700; color:#f1f5f9;'>{plan['protocol']}</span>"
                    f"<span style='color:#475569; margin:0 6px;'>·</span>"
                    f"<span style='color:#94a3b8; font-size:0.9rem;'>{plan['strategy']}</span></div>"
                    f"<div style='display:flex; gap:12px; font-size:0.82rem;'>"
                    f"<span style='color:#a78bfa; font-weight:700;'>{plan['apy_est']:.0f}% est. APY</span>"
                    f"<span style='color:{risk_color}; font-weight:600;'>{plan['risk']} Risk</span>"
                    f"<span style='color:#f1f5f9; font-weight:700;'>{plan['alloc_pct']}% = ${alloc_usd:,.0f}</span>"
                    f"</div></div>"
                    f"<div style='color:#94a3b8; font-size:0.88rem; margin-top:8px;'>{plan['action']}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            # Summary metrics
            blended_apy = sum(p["apy_est"] * p["alloc_pct"] / total_alloc for p in _plans) if total_alloc > 0 else 0.0
            annual_usd  = capital * blended_apy / 100
            st.markdown(
                f"<div style='background:rgba(139,92,246,0.06); border:1px solid rgba(139,92,246,0.14); "
                f"border-radius:10px; padding:14px 18px; margin-top:14px; font-size:0.88rem;'>"
                f"<div style='color:#a78bfa; font-weight:700; margin-bottom:6px;'>Strategy Summary</div>"
                f"<div style='display:flex; gap:24px; flex-wrap:wrap; color:#94a3b8;'>"
                f"<span>Capital: <b style='color:#f1f5f9;'>${capital:,.0f}</b></span>"
                f"<span>Blended APY: <b style='color:#22c55e;'>{blended_apy:.1f}%</b></span>"
                f"<span>Est. Annual Yield: <b style='color:#22c55e;'>${annual_usd:,.0f}</b></span>"
                f"<span>Est. Monthly: <b style='color:#22c55e;'>${annual_usd/12:,.0f}</b></span>"
                f"</div></div>",
                unsafe_allow_html=True,
            )

        if _warnings:
            for w in _warnings:
                st.warning(w)

        st.caption("Allocations are suggestions only. Always diversify and do your own research. Not financial advice.")


# ─── Tab 6: Compound Returns Calculator (Phase 10) ───────────────────────────

with tab6:
    import plotly.graph_objects as go
    import math

    render_section_header(
        "Compound Returns Calculator",
        "Project how DeFi yields grow over time with daily compounding + optional monthly top-ups",
    )

    _cc1, _cc2, _cc3 = st.columns(3)
    with _cc1:
        _cc_principal = st.number_input(
            "Initial Capital ($)", min_value=100.0, max_value=10_000_000.0,
            value=10_000.0, step=1_000.0, format="%.0f", key="cc_principal",
        )
    with _cc2:
        _cc_apy = st.number_input(
            "Target APY (%)", min_value=0.1, max_value=500.0,
            value=12.0, step=0.5, format="%.1f", key="cc_apy",
        )
    with _cc3:
        _cc_months = st.slider("Projection (months)", min_value=1, max_value=60, value=24, key="cc_months")

    _cc4, _cc5 = st.columns(2)
    with _cc4:
        _cc_topup = st.number_input(
            "Monthly Top-up ($)", min_value=0.0, max_value=100_000.0,
            value=0.0, step=100.0, format="%.0f", key="cc_topup",
        )
    with _cc5:
        _cc_compound = st.selectbox(
            "Compounding Frequency",
            ["Daily", "Weekly", "Monthly"],
            index=0,
            key="cc_compound",
        )

    _freq_map = {"Daily": 365, "Weekly": 52, "Monthly": 12}
    _n = _freq_map[_cc_compound]
    _r = _cc_apy / 100.0

    # Compute month-by-month balance
    _balance = _cc_principal
    _months_list   = [0]
    _balance_list  = [_balance]
    _interest_list = [0.0]
    _topup_total   = 0.0

    for _m in range(1, _cc_months + 1):
        _periods_this_month = _n / 12
        # Compound for this month's periods
        _balance = _balance * (1 + _r / _n) ** _periods_this_month
        # Add monthly top-up at end of month
        _balance    += _cc_topup
        _topup_total += _cc_topup
        _months_list.append(_m)
        _balance_list.append(round(_balance, 2))
        _interest_list.append(round(_balance - _cc_principal - _topup_total, 2))

    _final_balance  = _balance_list[-1]
    _total_interest = _interest_list[-1]
    _total_invested = _cc_principal + _topup_total
    _roi_pct = (_final_balance - _total_invested) / _total_invested * 100 if _total_invested > 0 else 0

    # KPI strip
    _kc1, _kc2, _kc3, _kc4 = st.columns(4)
    _kc1.metric("Final Balance",   f"${_final_balance:,.0f}")
    _kc2.metric("Interest Earned", f"${_total_interest:,.0f}")
    _kc3.metric("Total Invested",  f"${_total_invested:,.0f}")
    _kc4.metric("ROI",             f"{_roi_pct:.1f}%")

    # Chart
    _fig_cc = go.Figure()
    _fig_cc.add_trace(go.Scatter(
        x=_months_list, y=_balance_list,
        mode="lines", name="Balance",
        line=dict(color="#22c55e", width=2.5),
        fill="tozeroy", fillcolor="rgba(34,197,94,0.07)",
    ))
    _principal_line = [_cc_principal + _cc_topup * _m for _m in _months_list]
    _fig_cc.add_trace(go.Scatter(
        x=_months_list, y=_principal_line,
        mode="lines", name="Capital Invested",
        line=dict(color="#6366f1", width=1.5, dash="dash"),
    ))
    _fig_cc.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(
            title="Month", gridcolor="rgba(148,163,184,0.1)",
            tickfont=dict(color="#64748b", size=10),
        ),
        yaxis=dict(
            title="Value (USD)", gridcolor="rgba(148,163,184,0.1)",
            tickprefix="$", tickfont=dict(color="#64748b", size=10),
        ),
        legend=dict(font=dict(color="#94a3b8"), bgcolor="rgba(0,0,0,0)"),
        margin=dict(l=60, r=20, t=10, b=40),
        height=300,
    )
    st.plotly_chart(_fig_cc, width="stretch", config={"displayModeBar": False})

    # Compare multiple APY scenarios
    with st.expander("Compare APY scenarios"):
        _scenarios = [_cc_apy * 0.5, _cc_apy, _cc_apy * 1.5, _cc_apy * 2.0]
        _scen_rows = []
        for _s_apy in _scenarios:
            _s_r = _s_apy / 100.0
            _s_bal = _cc_principal
            for _m in range(_cc_months):
                _s_bal = _s_bal * (1 + _s_r / _n) ** (_n / 12) + _cc_topup
            _s_interest = _s_bal - _total_invested
            _scen_rows.append({
                "APY":            f"{_s_apy:.1f}%",
                "Final Balance":  f"${_s_bal:,.0f}",
                "Interest":       f"${_s_interest:,.0f}",
                "ROI":            f"{(_s_bal - _total_invested) / _total_invested * 100:.1f}%" if _total_invested > 0 else "—",
            })
        st.dataframe(pd.DataFrame(_scen_rows), width="stretch", hide_index=True)
    st.caption(
        f"Assumes {_cc_compound.lower()} compounding · No fees deducted · "
        "Real DeFi yields fluctuate — use as a directional guide only. Not financial advice."
    )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── IL vs HODL Calculator  (#75) ────────────────────────────────────────────

render_section_header(
    "IL vs HODL Calculator",
    "Compare LP value vs HODL · find out if pool APY covers impermanent loss",
)

_il_c1, _il_c2 = st.columns(2)
with _il_c1:
    _il_price_chg = st.number_input(
        "Token price change % (e.g. 50 = token doubled, -30 = dropped 30%)",
        min_value=-99.0, max_value=10_000.0,
        value=20.0, step=5.0, format="%.1f", key="il_price_chg",
        help="% change in the price of one token relative to the other since entering the pool",
    )
    _il_invest = st.number_input(
        "Initial LP value ($)", min_value=1.0,
        value=1000.0, step=100.0, format="%.0f", key="il_invest2",
    )
with _il_c2:
    _il_pool_apy = st.number_input(
        "Pool APY % (fee + reward yield)",
        min_value=0.0, max_value=10_000.0,
        value=30.0, step=1.0, format="%.1f", key="il_pool_apy",
        help="Current pool APY — used to calculate if fees will cover IL over 1 year",
    )
    _il_hold_yrs = st.number_input(
        "Holding period (years)", min_value=0.01, max_value=10.0,
        value=1.0, step=0.25, format="%.2f", key="il_hold_yrs",
    )

# ── Input validation (#13) ───────────────────────────────────────────────────
_il_valid = True
_ok, _msg = _validate_positive(_il_invest, "Initial LP value")
if not _ok:
    st.error(_msg)
    _il_valid = False
# Price change must be > -100% (ratio > -1.0) to avoid division-by-zero in IL formula
if _il_price_chg / 100.0 <= -1.0:
    st.error("Price change cannot be -100% or less (pool would be fully drained)")
    _il_valid = False
_ok3, _msg3 = _validate_range(_il_pool_apy, 0.0, 10_000.0, "Pool APY")
if not _ok3:
    st.error(_msg3)
    _il_valid = False

if not _il_valid:
    st.caption("Fix the input errors above to see the IL calculation.")
else:
    # Use compute_il_vs_hodl for the new formula-based approach (#75)
    _price_ratio_change = _il_price_chg / 100.0
    _fees_earned_usd    = _il_invest * (_il_pool_apy / 100.0) * _il_hold_yrs

    _il_res2 = compute_il_vs_hodl(
        price_ratio_change=_price_ratio_change,
        initial_value=_il_invest,
        fees_earned=_fees_earned_usd,
        holding_period_years=_il_hold_yrs,
    )

    if "error" not in _il_res2:
        _il_m1, _il_m2, _il_m3, _il_m4 = st.columns(4)
        _il_m1.metric("LP Position Value",    f"${_il_res2['lp_value']:,.2f}")
        _il_m2.metric("HODL Value",           f"${_il_res2['hodl_value']:,.2f}")
        _il_m3.metric("Impermanent Loss",     f"${_il_res2['il_usd']:,.2f}",
                      delta=f"{_il_res2['il_pct']:.2f}%",
                      delta_color="inverse")
        _il_m4.metric("Net vs HODL (w/ fees)", f"${_il_res2['net_vs_hodl_usd']:,.2f}",
                      delta=f"{_il_res2['net_vs_hodl_usd']:+.2f}$",
                      delta_color="normal")

        # APY coverage check
        _bkeven = _il_res2["breakeven_fee_apy"]
        _il_abs_pct = abs(_il_res2["il_pct"])
        if _il_res2["fees_cover_il"]:
            st.success(
                f"Pool APY ({_il_pool_apy:.1f}%) covers IL ({_il_abs_pct:.2f}%). "
                f"LP beats HODL by ${_il_res2['net_vs_hodl_usd']:+.2f} after {_il_hold_yrs:.2f}y."
            )
        else:
            _gap = _bkeven - _il_pool_apy
            st.warning(
                f"Pool APY ({_il_pool_apy:.1f}%) does NOT cover IL ({_il_abs_pct:.2f}%). "
                f"You need at least {_bkeven:.1f}% APY to break even (gap: {_gap:.1f}%)."
            )

        # Also show the traditional calc using price ratios
        with st.expander("Advanced: entry/current price ratio method"):
            _il2c1, _il2c2 = st.columns(2)
            with _il2c1:
                _il_entry  = st.number_input("Entry price ratio (token1/token0)", min_value=0.0001,
                                              value=1.0, step=0.01, format="%.4f", key="il_entry")
                _il_current = st.number_input("Current price ratio", min_value=0.0001,
                                               value=1.2, step=0.01, format="%.4f", key="il_current")
            with _il2c2:
                _il_invest_adv = st.number_input("Initial LP value ($) [advanced]", min_value=1.0,
                                                   value=1000.0, step=100.0, format="%.0f", key="il_invest_adv")
                _il_fees_adv   = st.number_input("Fees earned ($) [advanced]", min_value=0.0,
                                                   value=0.0, step=10.0, format="%.2f", key="il_fees_adv")
            _il_res_adv = calc_il_vs_hodl(
                entry_price_ratio=_il_entry,
                current_price_ratio=_il_current,
                initial_usd=_il_invest_adv,
                fee_income_usd=_il_fees_adv,
            )
            if "error" not in _il_res_adv:
                _adv_cols = st.columns(4)
                _adv_cols[0].metric("LP Value", f"${_il_res_adv['lp_value_usd']:,.2f}")
                _adv_cols[1].metric("HODL Value", f"${_il_res_adv['hodl_value_usd']:,.2f}")
                _adv_cols[2].metric("IL $", f"${_il_res_adv['il_usd']:,.2f}")
                _adv_cols[3].metric("Net vs HODL", f"${_il_res_adv['net_vs_hodl_usd']:,.2f}")
                if _il_res_adv["fees_offset_il"]:
                    st.success(f"Fees (${_il_fees_adv:.2f}) offset IL.")
                else:
                    st.warning(f"Need ${abs(_il_res_adv['il_usd']) - _il_fees_adv:.2f} more in fees.")
                st.caption(f"Price ratio changed {_il_res_adv['price_ratio_change']:+.1f}% since entry.")
    else:
        st.error(_il_res2.get("error", "Calculation error"))

    # Guard: only access _il_res2 when it was actually computed (i.e. _il_valid was True)
    if _il_valid and "error" not in _il_res2:
        st.caption(
            f"IL formula: 2×√k/(1+k) − 1 where k = 1 + price_change. "
            f"Breakeven APY = {_il_res2.get('breakeven_fee_apy', 0):.1f}% (needed to offset IL over {_il_hold_yrs:.1f}y). "
            "Not financial advice."
        )
    else:
        st.caption("IL formula: 2×√k/(1+k) − 1 where k = 1 + price_change. Not financial advice.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Concentrated LP Range Efficiency  (#83) ─────────────────────────────────

render_section_header(
    "Concentrated LP Range Efficiency",
    "Estimate Uniswap V3 capital efficiency and probability of staying in range",
)

_clp_c1, _clp_c2 = st.columns(2)
with _clp_c1:
    _clp_price = st.number_input("Current token price ($)", min_value=0.0001,
                                  value=1.0, step=0.01, format="%.4f", key="clp_price")
    _clp_lower = st.number_input("Range lower bound ($)", min_value=0.0001,
                                  value=0.8, step=0.01, format="%.4f", key="clp_lower")
with _clp_c2:
    _clp_upper = st.number_input("Range upper bound ($)", min_value=0.0001,
                                  value=1.2, step=0.01, format="%.4f", key="clp_upper")
    _clp_vol   = st.number_input("Daily volatility % (estimate)", min_value=0.01,
                                  value=3.0, step=0.5, format="%.1f", key="clp_vol")

# ── Input validation (#13): lower bound must be < upper bound ─────────────────
_clp_valid = True
if _clp_lower >= _clp_upper:
    st.error(
        f"Range lower bound (${_clp_lower:.4f}) must be less than "
        f"upper bound (${_clp_upper:.4f})"
    )
    _clp_valid = False
_ok_p, _msg_p = _validate_positive(_clp_price, "Current token price")
if not _ok_p:
    st.error(_msg_p)
    _clp_valid = False

_clp_res = {} if not _clp_valid else calc_concentrated_lp_efficiency(
    price=_clp_price, lower_tick_price=_clp_lower,
    upper_tick_price=_clp_upper, volatility_pct_daily=_clp_vol,
)

if _clp_valid and "error" not in _clp_res and _clp_res:
    _clp_cols = st.columns(4)
    _clp_cols[0].metric("In-Range Probability (7d)", f"{_clp_res['in_range_pct']:.0f}%")
    _clp_cols[1].metric("Capital Efficiency", f"{_clp_res['capital_efficiency_x']:.1f}×")
    _clp_cols[2].metric("Range Width", f"{_clp_res['range_width_pct']:.1f}%")
    _clp_cols[3].metric("Est. Days In Range", f"{_clp_res['est_days_in_range']:.0f}d")
    if _clp_res.get("in_range"):
        st.info(f"ℹ️ {_clp_res['label']}. "
                f"At {_clp_vol}% daily vol, expect to rebalance approx every {_clp_res['est_days_in_range']:.0f} days.")
    else:
        st.error("⚠️ Current price is outside your specified range — position earns no fees.")
elif _clp_valid and "error" in _clp_res:
    st.warning(_clp_res["error"])


st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ── Concentrated LP Optimizer (#83) ──────────────────────────────────────────

render_section_header(
    "Concentrated LP Optimizer",
    "Uniswap v3 / SparkDEX V3 — capital efficiency, IL at boundaries, fee income estimate",
)

_OPT_POOLS = {
    "ETH/USDC 0.05%":  18.0,
    "ETH/USDC 0.3%":   35.0,
    "ETH/USDT 0.05%":  16.0,
    "BTC/USDC 0.3%":   28.0,
    "ARB/USDC 0.3%":   55.0,
    "SOL/USDC 0.3%":   72.0,
    "Custom (enter manually)": 0.0,
}

_opt_c1, _opt_c2 = st.columns([2, 1])
with _opt_c1:
    _opt_price = st.number_input(
        "Current token price ($)", min_value=0.0001, value=2500.0,
        step=10.0, format="%.2f", key="opt83_price",
    )
    _opt_pool_sel = st.selectbox(
        "Select pool (sets fee APY preset)", list(_OPT_POOLS.keys()), key="opt83_pool",
    )
    _opt_fee_default = _OPT_POOLS[_opt_pool_sel]
    _opt_fee = st.number_input(
        "Pool fee APY (%)", min_value=0.0, value=float(_opt_fee_default) if _opt_fee_default > 0 else 20.0,
        step=1.0, format="%.1f", key="opt83_fee",
    )

with _opt_c2:
    _opt_days = st.number_input(
        "Holding period (days)", min_value=1, max_value=365, value=30, step=1, key="opt83_days",
    )

# ── Range preset buttons ───────────────────────────────────────────────────
st.markdown(
    "<div style='color:#64748b;font-size:0.80rem;margin:8px 0 4px'>Quick range presets</div>",
    unsafe_allow_html=True,
)
_preset_cols = st.columns(5)
_preset_pcts = [1, 2, 5, 10, 20]
_opt_lower_default = round(_opt_price * 0.90, 4)
_opt_upper_default = round(_opt_price * 1.10, 4)

for _pi, _pct in enumerate(_preset_pcts):
    if _preset_cols[_pi].button(f"±{_pct}%", key=f"opt83_preset_{_pct}"):
        st.session_state["opt83_lower_val"] = round(_opt_price * (1 - _pct / 100), 4)
        st.session_state["opt83_upper_val"] = round(_opt_price * (1 + _pct / 100), 4)

_opt_lower = st.number_input(
    "Range lower bound ($)", min_value=0.0001,
    value=float(st.session_state.get("opt83_lower_val", _opt_lower_default)),
    step=_opt_price * 0.01, format="%.4f", key="opt83_lower",
)
_opt_upper = st.number_input(
    "Range upper bound ($)", min_value=0.0001,
    value=float(st.session_state.get("opt83_upper_val", _opt_upper_default)),
    step=_opt_price * 0.01, format="%.4f", key="opt83_upper",
)

# ── Input validation (#13) ────────────────────────────────────────────────────
_opt_input_ok = True
if _opt_lower >= _opt_upper:
    st.error(
        f"Range lower bound (${_opt_lower:.4f}) must be less than "
        f"upper bound (${_opt_upper:.4f})"
    )
    _opt_input_ok = False
elif not (0 <= _opt_fee <= 1000):
    st.error("Pool fee APY must be between 0 and 1000%")
    _opt_input_ok = False

if _opt_input_ok:
    try:
        _opt_res = compute_concentrated_lp_metrics(
            current_price=_opt_price,
            lower_tick_price=_opt_lower,
            upper_tick_price=_opt_upper,
            fee_apy=_opt_fee,
            holding_period_days=int(_opt_days),
        )

        if "error" in _opt_res:
            st.warning(f"Input error: {_opt_res['error']}")
        else:
            _m1, _m2, _m3, _m4 = st.columns(4)
            _m1.metric("Capital Efficiency", f"{_opt_res['capital_efficiency']:.1f}×")
            _m2.metric("Range Width", f"{_opt_res['range_width_pct']:.1f}%")
            _m3.metric("In-Range Probability", f"{_opt_res['in_range_probability_pct']:.0f}%")
            _m4.metric(f"Fee Income ({int(_opt_days)}d)", f"{_opt_res['fee_income_pct']:.2f}%")

            _m5, _m6, _m7 = st.columns(3)
            _il_up_pct = _opt_res["il_if_hits_upper"] * 100
            _il_lo_pct = _opt_res["il_if_hits_lower"] * 100
            _m5.metric("IL if hits upper",   f"{_il_up_pct:.2f}%", delta_color="inverse")
            _m6.metric("IL if hits lower",   f"{_il_lo_pct:.2f}%", delta_color="inverse")
            _m7.metric("Est. Net Return",    f"{_opt_res['estimated_net_return_pct']:.2f}%")

            st.markdown(
                f"<div style='background:rgba(99,102,241,0.08);border:1px solid rgba(99,102,241,0.18);"
                f"border-radius:8px;padding:10px 14px;font-size:0.85rem;color:#c4cbdb;margin-top:8px'>"
                f"💡 <b>Recommendation:</b> {_opt_res['recommendation']}</div>",
                unsafe_allow_html=True,
            )

            # Visual range bar
            if _opt_lower < _opt_upper:
                _range_span   = _opt_upper - _opt_lower
                _cp_in_range  = _opt_lower <= _opt_price <= _opt_upper
                _cp_pct       = min(100, max(0, (_opt_price - _opt_lower) / _range_span * 100)) if _range_span > 0 else 50
                _bar_color    = "#22c55e" if _cp_in_range else "#ef4444"
                _status_text  = "In range — fees accruing" if _cp_in_range else "OUT OF RANGE — no fees"
                st.markdown(
                    f"<div style='margin:12px 0 4px;font-size:0.75rem;color:#64748b'>Price position within range</div>",
                    unsafe_allow_html=True,
                )
                st.progress(int(_cp_pct) / 100)
                st.markdown(
                    f"<div style='display:flex;justify-content:space-between;font-size:0.71rem;color:#475569;margin-top:-6px'>"
                    f"<span>${_opt_lower:.4f}</span>"
                    f"<span style='color:{_bar_color};font-weight:600'>${_opt_price:.4f} — {_status_text}</span>"
                    f"<span>${_opt_upper:.4f}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

            with st.expander("How concentrated LP works"):
                st.markdown("""
**Capital efficiency multiplier**: how much more capital-efficient this range is vs full-range V2.
- A 5× multiplier means the same fees on 1/5th the capital.
- Narrower range = higher efficiency but more frequent rebalancing when price exits.

**IL at boundaries**: if price hits the upper or lower boundary, this is your impermanent loss %
compared to just holding. Negative means a loss relative to holding.

**In-range probability**: rough estimate of the chance price stays inside your range over the
holding period. Based on 2% assumed daily price volatility.

**Fee income estimate** = (fee_apy / 365) × days × capital_efficiency_multiplier

**Net return estimate** = fee_income + IL_midpoint × (1 - in_range_probability)
                """)
    except Exception as _opt_exc:
        st.warning(f"Concentrated LP calculator error: {_opt_exc}")

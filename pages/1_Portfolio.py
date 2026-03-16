"""
Portfolio — Wallet balances, tracked positions, P&L, exit strategy, historical chart.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta

from ui.common import (
    page_setup, render_sidebar, load_latest, load_history_runs,
    load_positions, save_positions, load_wallets, save_wallets,
    compute_position_pnl, render_opportunity_card, _ts_fmt,
)
from config import PROTOCOLS, TOKENS, FLARE_RPC_URLS, INCENTIVE_PROGRAM, RISK_PROFILES

page_setup("Portfolio · Flare DeFi")

ctx            = render_sidebar()
portfolio_size = ctx["portfolio_size"]

latest    = load_latest()
runs      = load_history_runs()
positions = load_positions()
flare_scan = latest.get("flare_scan", {})
prices     = flare_scan.get("prices", [])

st.markdown("# Portfolio")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Wallet balances · tracked positions · P&L · exit planning</div>",
    unsafe_allow_html=True,
)


# ─── Wallet Tracker ───────────────────────────────────────────────────────────

def _fetch_wallet_balances(wallet: str) -> list:
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

    addr_cs     = Web3.to_checksum_address(wallet)
    flr_balance = w3.eth.get_balance(addr_cs) / 1e18
    ERC20_ABI   = [{
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf", "outputs": [{"type": "uint256"}],
        "stateMutability": "view", "type": "function",
    }]
    token_balances  = {"FLR (native)": flr_balance}
    token_decimals  = {"USD0": 6, "USDT": 6, "USDC.e": 6}
    for sym, addr in TOKENS.items():
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            dec = token_decimals.get(sym, 18)
            bal = contract.functions.balanceOf(addr_cs).call()
            token_balances[sym] = bal / (10 ** dec)
        except Exception:
            pass
    return [{"Token": k, "Balance": f"{v:,.4f}"} for k, v in token_balances.items() if v >= 0.0001]


st.markdown("### Wallet Tracker")
with st.expander("Connect a wallet (read-only)"):
    saved_wallets = load_wallets()
    ca, cl, cb = st.columns([4, 2, 1])
    with ca:
        new_addr  = st.text_input("Address", placeholder="0x1234…abcd", label_visibility="collapsed", key="new_wallet_addr")
    with cl:
        new_label = st.text_input("Label",   placeholder="Main Wallet",  label_visibility="collapsed", key="new_wallet_label")
    with cb:
        if st.button("Add", key="add_wallet_btn", use_container_width=True):
            if new_addr and len(new_addr) == 42 and new_addr.startswith("0x"):
                label = new_label.strip() or f"{new_addr[:6]}…{new_addr[-4:]}"
                saved_wallets.append({"label": label, "address": new_addr})
                save_wallets(saved_wallets)
                st.rerun()
            else:
                st.warning("Enter a valid 42-character 0x address.")

    if saved_wallets:
        wallet_labels = [f"{w['label']}  ({w['address'][:6]}…{w['address'][-4:]})" for w in saved_wallets]
        sel_idx = st.selectbox("Wallet", range(len(wallet_labels)), format_func=lambda i: wallet_labels[i], key="wallet_select")
        col_check, col_remove = st.columns([3, 1])
        with col_check:
            if st.button("Check Balances", key="check_wallet_btn", use_container_width=True):
                with st.spinner("Fetching on-chain balances…"):
                    try:
                        rows = _fetch_wallet_balances(saved_wallets[sel_idx]["address"])
                        st.dataframe(pd.DataFrame(rows) if rows else pd.DataFrame(), use_container_width=True, hide_index=True)
                        if not rows:
                            st.info("No significant balances found.")
                    except ImportError:
                        st.warning("Install web3: `pip install web3`")
                    except Exception as e:
                        st.error(f"Error: {e}")
        with col_remove:
            if st.button("Remove", key="remove_wallet_btn", use_container_width=True):
                saved_wallets.pop(sel_idx)
                save_wallets(saved_wallets)
                st.rerun()
    else:
        st.caption("Add a wallet address above to start tracking.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Positions Overview ───────────────────────────────────────────────────────

st.markdown("### Your Positions")

if positions:
    total_value   = sum(p.get("current_value", 0) for p in positions)
    total_fees    = sum(p.get("unclaimed_fees", 0) for p in positions)
    total_deposit = sum(float(p.get("deposit_usd") or p.get("entry_value") or 0) for p in positions)
    total_pnl     = total_value - total_deposit
    pnl_color     = "#10b981" if total_pnl >= 0 else "#ef4444"

    c1, c2, c3, c4 = st.columns(4)
    for col, label, val, sub, cls in [
        (c1, "Portfolio Value",   f"${total_value:,.0f}",             "",                       "card-blue"),
        (c2, "Total P&L",         f"{total_pnl:+,.0f}",               f"vs ${total_deposit:,.0f} in", "card-green" if total_pnl >= 0 else "card-red"),
        (c3, "Unclaimed Fees",    f"${total_fees:,.2f}",              "",                       "card-green"),
        (c4, "Open Positions",    str(len(positions)),                 "",                       "card-blue"),
    ]:
        with col:
            st.markdown(f"""
            <div class="metric-card {cls}">
                <div class="label">{label}</div>
                <div class="big-number">{val}</div>
                <div style="color:#475569; font-size:0.8rem; margin-top:4px;">{sub}</div>
            </div>""", unsafe_allow_html=True)

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    for idx, pos in enumerate(positions):
        pnl      = compute_position_pnl(pos, prices)
        proto    = pos.get("protocol", "?").capitalize()
        pool     = pos.get("pool", "?")
        ptype    = pos.get("position_type", "lp").upper()
        vc       = pnl["value_change"]
        vc_pct   = pnl["value_change_pct"]
        vc_color = "#10b981" if vc >= 0 else "#ef4444"
        days     = pnl["days_active"]
        fees_est = pnl["fees_earned_est"]
        il_pct   = pnl["il_pct"]
        hodl     = pnl["hodl_value"]

        days_str  = f"{days}d" if days > 0 else "—"
        fees_html = f" · Est. fees earned: <span style='color:#10b981'>${fees_est:,.2f}</span>" if fees_est > 0 else ""
        il_html   = f" · IL est: <span style='color:#f59e0b'>{il_pct:.1f}%</span>" if il_pct > 0.1 else ""
        hodl_html = f" · HODL: <span style='color:#64748b'>${hodl:,.0f}</span>" if hodl > 0 else ""
        bal_str   = " ".join(filter(None, [pos.get("token0_balance",""), pos.get("token1_balance","")]))

        col_card, col_del = st.columns([14, 1])
        with col_card:
            st.markdown(f"""
            <div class="opp-card" style="border-left:3px solid {vc_color};">
                <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                    <div>
                        <span style="font-weight:700; color:#f1f5f9;">{pool}</span>
                        <span style="color:#475569; margin:0 6px;">·</span>
                        <span style="color:#64748b; font-size:0.88rem;">{proto}</span>
                        <span style="color:#334155; font-size:0.75rem; margin-left:6px;">({ptype})</span>
                    </div>
                    <span style="color:{vc_color}; font-weight:700;">{vc:+,.0f} ({vc_pct:+.1f}%)</span>
                </div>
                <div style="color:#475569; font-size:0.82rem; margin-top:10px;">
                    <span style="color:#94a3b8">${pnl['current_value']:,.0f}</span> current ·
                    <span style="color:#64748b">${pnl['deposit_usd']:,.0f}</span> deposited ·
                    {days_str} active{fees_html}{il_html}{hodl_html}
                </div>
                <div style="color:#334155; font-size:0.78rem; margin-top:6px;">
                    Unclaimed fees: <span style="color:#10b981">${pnl['unclaimed_fees']:,.2f}</span>
                    {"  ·  " + bal_str if bal_str else ""}
                </div>
            </div>""", unsafe_allow_html=True)
        with col_del:
            if st.button("✕", key=f"del_pos_{idx}", help="Remove position"):
                positions.pop(idx)
                save_positions(positions)
                st.rerun()

else:
    st.markdown(
        "<div style='color:#334155; font-size:0.9rem; padding:20px 0;'>"
        "No positions tracked yet. Add your first position below.</div>",
        unsafe_allow_html=True,
    )

# ── Add Position ──────────────────────────────────────────────────────────────
with st.expander("➕ Track a New Position"):
    with st.form("add_position_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            proto_key  = st.selectbox("Protocol", options=list(PROTOCOLS.keys()),
                                      format_func=lambda k: PROTOCOLS[k]["name"])
            pool_name  = st.text_input("Pool / Asset", placeholder="WFLR-USD0 or sFLR")
            pos_type   = st.selectbox("Type", ["lp", "lending", "staking"])
            entry_date = st.date_input("Entry date")
        with c2:
            deposit_usd    = st.number_input("Deposit ($)", min_value=0.0, value=1000.0, step=100.0)
            entry_apy      = st.number_input("Entry APY (%)", min_value=0.0, value=0.0, step=1.0)
            current_value  = st.number_input("Current value ($, 0 = same)", min_value=0.0, value=0.0, step=100.0)
            unclaimed_fees = st.number_input("Unclaimed fees ($)", min_value=0.0, value=0.0, step=1.0)

        st.markdown("<div style='color:#475569; font-size:0.8rem; margin-top:8px;'>Token details (LP / IL tracking)</div>", unsafe_allow_html=True)
        tc1, tc2 = st.columns(2)
        with tc1:
            token_a        = st.text_input("Token A", placeholder="WFLR")
            token_a_amount = st.number_input("Token A amount", min_value=0.0, value=0.0)
            entry_price_a  = st.number_input("Token A entry price ($)", min_value=0.0, value=0.0, format="%.6f")
        with tc2:
            token_b        = st.text_input("Token B", placeholder="USD0")
            token_b_amount = st.number_input("Token B amount", min_value=0.0, value=0.0)
            entry_price_b  = st.number_input("Token B entry price ($)", min_value=0.0, value=0.0, format="%.6f")

        notes = st.text_input("Notes (optional)")

        if st.form_submit_button("Add Position", use_container_width=True):
            if not pool_name:
                st.error("Pool / Asset name is required.")
            else:
                positions.append({
                    "id":             f"pos_{int(datetime.utcnow().timestamp())}",
                    "protocol":       proto_key,
                    "pool":           pool_name,
                    "position_type":  pos_type,
                    "entry_date":     entry_date.isoformat(),
                    "deposit_usd":    float(deposit_usd),
                    "entry_apy":      float(entry_apy),
                    "current_value":  float(current_value) if current_value > 0 else float(deposit_usd),
                    "unclaimed_fees": float(unclaimed_fees),
                    "entry_value":    float(deposit_usd),
                    "token_a":        token_a,
                    "token_a_amount": float(token_a_amount),
                    "entry_price_a":  float(entry_price_a),
                    "token_b":        token_b,
                    "token_b_amount": float(token_b_amount),
                    "entry_price_b":  float(entry_price_b),
                    "notes":          notes,
                })
                save_positions(positions)
                st.success(f"Added: {pool_name}")
                st.rerun()

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Exit Strategy ────────────────────────────────────────────────────────────

st.markdown("### Exit Strategy")

incentive_expiry = datetime.strptime(INCENTIVE_PROGRAM["expires"], "%Y-%m-%d")
days_left        = max(0, (incentive_expiry - datetime.utcnow()).days)
exp_color        = "#10b981" if days_left > 90 else ("#f59e0b" if days_left > 30 else "#ef4444")
exp_msg          = (
    "Monitor monthly. Consider setting a reminder for May 2026."
    if days_left > 90 else
    "Begin reducing high-IL LP positions." if days_left > 30 else
    "URGENT — incentive-dependent APYs will drop sharply soon."
)

st.markdown(
    f"<div class='warn-box' style='border-color:{exp_color}33;'>"
    f"<span style='color:{exp_color}; font-weight:600;'>⏳ {days_left} days until incentive expiry</span>"
    f"<div style='color:#64748b; font-size:0.85rem; margin-top:4px;'>{exp_msg}</div></div>",
    unsafe_allow_html=True,
)

tab_targets, tab_timeline = st.tabs(["Price Targets", "Exit Timeline"])

with tab_targets:
    price_lookup = {p.get("symbol", ""): p.get("price_usd", 0) for p in (prices or [])}
    c1, c2, c3 = st.columns(3)
    with c1:
        asset_choice = st.selectbox("Asset", ["FLR", "FXRP", "sFLR", "Custom"], key="exit_asset")
    with c2:
        default_price = price_lookup.get(asset_choice, 0.020) or 0.020
        asset_price = st.number_input("Current price ($)", min_value=0.0001,
                                      value=float(default_price), format="%.6f", step=0.001, key="exit_price")
    with c3:
        holdings = st.number_input("Holdings (tokens)", min_value=0.0, value=10000.0, step=1000.0, key="exit_holdings")

    if asset_price > 0 and holdings > 0:
        rows = []
        for mult, label, action in [
            (1.25, "+25%", "Take 10% profit"),
            (1.50, "+50%", "Take 15–20% profit"),
            (2.00, "+100%", "Take 25% profit"),
            (3.00, "+200%", "Take 33% profit"),
            (5.00, "+400%", "Consider full exit"),
        ]:
            tp   = asset_price * mult
            val  = holdings * tp
            gain = val - holdings * asset_price
            rows.append({"Target": label, "Price": f"${tp:.6f}",
                         "Value": f"${val:,.0f}", "Gain": f"+${gain:,.0f}", "Action": action})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("Planning targets only — not financial advice.")

with tab_timeline:
    if not positions:
        st.info("Add positions above to see per-position exit guidance.")
    else:
        rows = []
        for pos in positions:
            days_held = 0
            if pos.get("entry_date"):
                try:
                    days_held = max(0, (datetime.utcnow() - datetime.fromisoformat(pos["entry_date"])).days)
                except Exception:
                    pass
            proto_key    = pos.get("protocol", "")
            is_incentive = proto_key in ("blazeswap", "enosys", "sparkdex")
            pnl          = compute_position_pnl(pos, prices)
            rows.append({
                "Position":     f"{pos.get('pool','?')} ({pos.get('protocol','?').capitalize()})",
                "Type":         pos.get("position_type", "lp").upper(),
                "Days Held":    days_held,
                "Entry APY":    f"{pos.get('entry_apy',0):.1f}%",
                "P&L":          f"{pnl['value_change_pct']:+.1f}%" if pnl["deposit_usd"] > 0 else "—",
                "Incentive":    "⚠️ YES" if is_incentive else "✅ Low",
                "Exit By":      "Jun 2026" if is_incentive else "Flexible",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("DEX LP pools depend on RFLR incentives expiring July 2026. Lending/staking have lower dependency.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Historical APY Chart ─────────────────────────────────────────────────────

st.markdown("### Historical APY Trend")

profile = ctx["profile"]
records = []
for run in runs[-30:]:
    ts   = run.get("run_id", "")
    opps = run.get("models", {}).get(profile, [])
    if opps and ts:
        try:
            records.append({"date": datetime.fromisoformat(ts), "apy": opps[0].get("estimated_apy", 0)})
        except Exception:
            pass

if len(records) >= 2:
    import pandas as pd_local
    df  = pd_local.DataFrame(records).sort_values("date")
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["date"], y=df["apy"],
        mode="lines+markers",
        line=dict(color="#3b82f6", width=2),
        marker=dict(size=5, color="#3b82f6"),
        fill="tozeroy",
        fillcolor="rgba(59,130,246,0.06)",
    ))
    fig.update_layout(
        plot_bgcolor="#0d1321", paper_bgcolor="#0d1321",
        font_color="#94a3b8",
        xaxis=dict(gridcolor="#1e293b", color="#475569"),
        yaxis=dict(title="APY %", gridcolor="#1e293b", color="#475569"),
        margin=dict(l=40, r=20, t=20, b=40),
        height=260,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Need at least 2 scans to show the chart.")

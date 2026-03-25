"""
Portfolio — Wallet balances, tracked positions, P&L, exit strategy, historical chart.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import csv
import io
import logging
import html as _html
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

from ui.common import (
    page_setup, render_sidebar, load_latest, load_history_runs,
    load_positions, save_positions, load_wallets, save_wallets,
    compute_position_pnl, render_opportunity_card, render_section_header,
    _ts_fmt, load_live_prices, risk_score_to_grade, render_ftso_il_calculator,
)
from config import PROTOCOLS, TOKENS, INCENTIVE_PROGRAM, RISK_PROFILES, FALLBACK_PRICES

page_setup("Portfolio · Flare DeFi")

ctx            = render_sidebar()
portfolio_size = ctx["portfolio_size"]

latest    = load_latest()
runs      = load_history_runs()
positions = load_positions()
flare_scan = latest.get("flare_scan") or {}
prices     = load_live_prices() or flare_scan.get("prices") or []

st.markdown("# Portfolio")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Wallet balances · tracked positions · P&L · exit planning</div>",
    unsafe_allow_html=True,
)


# ─── Export Helpers ───────────────────────────────────────────────────────────

def _build_csv_export(positions: list, pnl_results: list) -> bytes:
    """Build a UTF-8 CSV of all positions with P&L data."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Protocol", "Pool", "Type", "Entry Date",
        "Deposit ($)", "Current Value ($)", "P&L ($)", "P&L (%)",
        "Days Active", "Est. Fees Earned ($)", "IL (%)",
        "Unclaimed Fees ($)", "Entry APY (%)", "Notes",
    ])
    for pos, pnl in zip(positions, pnl_results):
        writer.writerow([
            pos.get("protocol", "").capitalize(),
            pos.get("pool", ""),
            pos.get("position_type", ""),
            pos.get("entry_date", ""),
            f"{pnl['deposit_usd']:.2f}",
            f"{pnl['current_value']:.2f}",
            f"{pnl['value_change']:.2f}",
            f"{pnl['value_change_pct']:.2f}",
            pnl["days_active"],
            f"{pnl['fees_earned_est']:.2f}",
            f"{pnl['il_pct']:.2f}",
            f"{pnl['unclaimed_fees']:.2f}",
            f"{pos.get('entry_apy') or 0:.1f}",
            pos.get("notes", ""),
        ])
    return buf.getvalue().encode("utf-8")


def _build_pdf_export(positions: list, pnl_results: list) -> bytes:
    """Build a PDF portfolio report using fpdf2."""
    try:
        from fpdf import FPDF
    except ImportError:
        return b""

    total_value   = sum(p["current_value"] for p in pnl_results)
    total_deposit = sum(p["deposit_usd"]   for p in pnl_results)
    total_pnl     = total_value - total_deposit
    total_fees    = sum(p["unclaimed_fees"] for p in pnl_results)
    report_date   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Flare DeFi Model - Portfolio Report", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated: {report_date}", ln=True)
    pdf.ln(4)

    # Summary row
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Portfolio Summary", ln=True)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(60, 6, f"Total Value:   ${total_value:,.2f}")
    pdf.cell(60, 6, f"Total P&L:   ${total_pnl:+,.2f}")
    pdf.cell(0,  6, f"Unclaimed Fees:   ${total_fees:,.2f}", ln=True)
    pdf.cell(60, 6, f"Positions:   {len(positions)}")
    pdf.cell(0,  6, f"Deposited:   ${total_deposit:,.2f}", ln=True)
    pdf.ln(6)

    # Table header
    cols   = ["Protocol", "Pool", "Deposit", "Value", "P&L", "P&L%", "Days", "Fees Est.", "IL%"]
    widths = [28, 42, 22, 22, 22, 14, 12, 22, 12]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(30, 41, 59)
    pdf.set_text_color(255, 255, 255)
    for col, w in zip(cols, widths):
        pdf.cell(w, 7, col, border=1, fill=True)
    pdf.ln()

    # Table rows
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(0, 0, 0)
    for i, (pos, pnl) in enumerate(zip(positions, pnl_results)):
        fill = i % 2 == 0
        pdf.set_fill_color(241, 245, 249) if fill else pdf.set_fill_color(255, 255, 255)
        row = [
            pos.get("protocol", "").capitalize()[:12],
            pos.get("pool", "")[:20],
            f"${pnl['deposit_usd']:,.0f}",
            f"${pnl['current_value']:,.0f}",
            f"${pnl['value_change']:+,.0f}",
            f"{pnl['value_change_pct']:+.1f}%",
            str(pnl["days_active"]),
            f"${pnl['fees_earned_est']:,.2f}",
            f"{pnl['il_pct']:.1f}%",
        ]
        for val, w in zip(row, widths):
            pdf.cell(w, 6, val, border=1, fill=fill)
        pdf.ln()

    pdf.ln(8)
    pdf.set_font("Helvetica", "I", 7)
    pdf.cell(0, 5, "Not financial advice. DeFi positions carry risk including impermanent loss and smart contract risk.", ln=True)

    return bytes(pdf.output())


# ─── Wallet Tracker ───────────────────────────────────────────────────────────

def _detect_onchain_positions(wallet: str) -> list:
    """
    Detect lending/staking positions from on-chain token balances.
    Checks: Kinetic kTokens (lending), sFLR (Sceptre staking), stXRP (Firelight staking).
    Returns a list of pre-filled position dicts the user can confirm and add.
    """
    from web3 import Web3
    from scanners.flare_scanner import _get_web3
    w3 = _get_web3()
    if not w3:
        raise ConnectionError("Could not connect to Flare RPC.")

    addr_cs   = Web3.to_checksum_address(wallet)
    ERC20_ABI = [{
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf", "outputs": [{"type": "uint256"}],
        "stateMutability": "view", "type": "function",
    }]

    suggestions = []
    today_str   = datetime.now(timezone.utc).date().isoformat()

    # ── Kinetic kToken balances (lending positions) ────────────────────────────
    k_tokens = PROTOCOLS.get("kinetic", {}).get("kTokens", {})
    for asset, cfg in k_tokens.items():
        addr = cfg.get("address")
        if not addr:
            continue
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            bal = contract.functions.balanceOf(addr_cs).call()
            if bal > 0:
                bal_human = bal / 1e8   # kTokens use 8 decimals (Compound standard)
                suggestions.append({
                    "protocol":       "kinetic",
                    "pool":           asset,
                    "position_type":  "lending",
                    "entry_date":     today_str,
                    "deposit_usd":    0.0,
                    "entry_apy":      cfg.get("baseline_supply", 0.0),
                    "current_value":  0.0,
                    "unclaimed_fees": 0.0,
                    "entry_value":    0.0,
                    "token_a":        asset,
                    "token_a_amount": round(bal_human, 6),
                    "entry_price_a":  0.0,
                    "token_b":        "",
                    "token_b_amount": 0.0,
                    "entry_price_b":  0.0,
                    "notes":          f"Auto-detected: {bal_human:.6f} k{asset} on Kinetic",
                })
        except Exception as _e:
            logger.debug(f"kToken balance check failed for {asset}: {_e}")

    # ── sFLR balance (Sceptre staking) ─────────────────────────────────────────
    sflr_addr = TOKENS.get("sFLR")
    if sflr_addr:
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(sflr_addr), abi=ERC20_ABI)
            bal = contract.functions.balanceOf(addr_cs).call() / 1e18
            if bal >= 0.001:
                suggestions.append({
                    "protocol":       "sceptre",
                    "pool":           "sFLR",
                    "position_type":  "staking",
                    "entry_date":     today_str,
                    "deposit_usd":    0.0,
                    "entry_apy":      4.5,
                    "current_value":  0.0,
                    "unclaimed_fees": 0.0,
                    "entry_value":    0.0,
                    "token_a":        "sFLR",
                    "token_a_amount": round(bal, 4),
                    "entry_price_a":  0.0,
                    "token_b":        "",
                    "token_b_amount": 0.0,
                    "entry_price_b":  0.0,
                    "notes":          f"Auto-detected: {bal:.4f} sFLR staked on Sceptre",
                })
        except Exception as _e:
            logger.debug(f"sFLR balance check failed: {_e}")

    # ── stXRP balance (Firelight staking) ──────────────────────────────────────
    stxrp_addr = TOKENS.get("stXRP")
    if stxrp_addr:
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(stxrp_addr), abi=ERC20_ABI)
            bal = contract.functions.balanceOf(addr_cs).call() / 1e18
            if bal >= 0.001:
                suggestions.append({
                    "protocol":       "firelight",
                    "pool":           "stXRP",
                    "position_type":  "staking",
                    "entry_date":     today_str,
                    "deposit_usd":    0.0,
                    "entry_apy":      5.0,
                    "current_value":  0.0,
                    "unclaimed_fees": 0.0,
                    "entry_value":    0.0,
                    "token_a":        "stXRP",
                    "token_a_amount": round(bal, 4),
                    "entry_price_a":  0.0,
                    "token_b":        "",
                    "token_b_amount": 0.0,
                    "entry_price_b":  0.0,
                    "notes":          f"Auto-detected: {bal:.4f} stXRP on Firelight",
                })
        except Exception as _e:
            logger.debug(f"stXRP balance check failed: {_e}")

    return suggestions


def _fetch_wallet_balances(wallet: str) -> list:
    from web3 import Web3
    from scanners.flare_scanner import _get_web3
    w3 = _get_web3()
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
    token_decimals  = {"USD0": 6, "USDT": 6, "USDC.e": 6, "USDT0": 6, "FXRP": 6, "FDOGE": 8, "FBTC": 8}
    for sym, addr in TOKENS.items():
        if not addr:
            continue
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
            dec = token_decimals.get(sym, 18)
            bal = contract.functions.balanceOf(addr_cs).call()
            token_balances[sym] = bal / (10 ** dec)
        except Exception as e:
            logger.debug(f"Failed to fetch {sym} balance for {addr_cs}: {e}")
    return [{"Token": k, "Balance": f"{v:,.4f}"} for k, v in token_balances.items() if v >= 0.0001]


render_section_header("Wallet Tracker", "Read-only on-chain balance lookup")
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
                try:
                    from web3 import Web3
                    checksum_addr = Web3.to_checksum_address(new_addr)
                    label = new_label.strip() or f"{checksum_addr[:6]}…{checksum_addr[-4:]}"
                    saved_wallets.append({"label": label, "address": checksum_addr})
                    save_wallets(saved_wallets)
                    st.rerun()
                except Exception:
                    st.warning("Invalid address — failed checksum validation.")
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

            if st.button("🔍 Detect Positions", key="detect_pos_btn", use_container_width=True,
                         help="Auto-detect Kinetic lending, sFLR staking, and stXRP staking from wallet"):
                with st.spinner("Scanning on-chain positions…"):
                    try:
                        suggestions = _detect_onchain_positions(saved_wallets[sel_idx]["address"])
                        if suggestions:
                            st.session_state["_pos_suggestions"] = suggestions
                            st.success(f"Found {len(suggestions)} position(s). Review below ↓")
                        else:
                            st.info("No Kinetic / sFLR / stXRP positions detected for this wallet.")
                    except ImportError:
                        st.warning("Install web3: `pip install web3`")
                    except Exception as e:
                        st.error(f"Detection error: {e}")

        # ── Detected position suggestions ─────────────────────────────────────
        if st.session_state.get("_pos_suggestions"):
            st.markdown("**Detected positions — confirm to add:**")
            for i, sug in enumerate(st.session_state["_pos_suggestions"]):
                ca2, cb2 = st.columns([5, 1])
                with ca2:
                    st.markdown(
                        f"<div style='font-size:0.85rem; color:#94a3b8; padding:4px 0;'>"
                        f"<b>{sug['pool']}</b> · {sug['protocol'].capitalize()} · "
                        f"{sug['position_type']} · {sug.get('token_a_amount', 0):,.4f} {sug.get('token_a', '')}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
                with cb2:
                    if st.button("Add", key=f"add_sug_{i}", use_container_width=True):
                        new_pos = dict(sug)
                        new_pos["id"] = f"pos_{int(datetime.now(timezone.utc).timestamp())}_{i}"
                        positions.append(new_pos)
                        save_positions(positions)
                        st.session_state["_pos_suggestions"].pop(i)
                        st.rerun()
            if st.button("Clear suggestions", key="clear_sug_btn"):
                st.session_state["_pos_suggestions"] = []
                st.rerun()
        with col_remove:
            if st.button("Remove", key="remove_wallet_btn", use_container_width=True):
                if sel_idx < len(saved_wallets):
                    saved_wallets.pop(sel_idx)
                    save_wallets(saved_wallets)
                st.rerun()
    else:
        st.caption("Add a wallet address above to start tracking.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Positions Overview ───────────────────────────────────────────────────────

pnl_results: list = []  # populated inside the positions block; defined here to avoid UnboundLocalError

render_section_header("Your Positions", "P&L · fees earned · impermanent loss estimate")

if positions:
    total_value   = sum(p.get("current_value", 0) for p in positions)
    total_fees    = sum(p.get("unclaimed_fees", 0) for p in positions)
    total_deposit = sum(float(p.get("deposit_usd") or p.get("entry_value") or 0) for p in positions)
    total_pnl     = total_value - total_deposit
    pnl_color     = "#10b981" if total_pnl >= 0 else "#ef4444"

    c1, c2, c3, c4 = st.columns(4)
    for col, label, val, sub, cls in [
        (c1, "Portfolio Value",   f"${total_value:,.0f}",             "",                       "card-blue"),
        (c2, "Total P&L",         f"${total_pnl:+,.0f}",              f"vs ${total_deposit:,.0f} in", "card-green" if total_pnl >= 0 else "card-red"),
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

    # Compute P&L once for all positions — reused in cards and exit timeline below
    pnl_results = [compute_position_pnl(pos, prices) for pos in positions]

    # ── Export ────────────────────────────────────────────────────────────────
    _exp_csv, _exp_pdf = st.columns([1, 1])
    with _exp_csv:
        _csv_bytes = _build_csv_export(positions, pnl_results)
        st.download_button(
            "⬇ Export CSV",
            data=_csv_bytes,
            file_name=f"flare_portfolio_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with _exp_pdf:
        _pdf_bytes = _build_pdf_export(positions, pnl_results)
        if _pdf_bytes:
            st.download_button(
                "⬇ Export PDF",
                data=_pdf_bytes,
                file_name=f"flare_portfolio_{datetime.now(timezone.utc).strftime('%Y%m%d')}.pdf",
                mime="application/pdf",
                use_container_width=True,
            )
        else:
            st.caption("PDF: `pip install fpdf2`")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

    for idx, pos in enumerate(positions):
        pnl      = pnl_results[idx]
        proto    = _html.escape(pos.get("protocol", "?").capitalize())
        pool     = _html.escape(pos.get("pool", "?"))
        ptype    = _html.escape(pos.get("position_type", "lp").upper())
        vc       = pnl["value_change"]
        vc_pct   = pnl["value_change_pct"]
        vc_color = "#10b981" if vc >= 0 else "#ef4444"
        days     = pnl["days_active"]
        fees_est = pnl["fees_earned_est"]
        il_pct   = pnl["il_pct"]
        hodl     = pnl["hodl_value"]

        # Feature 8: Risk grade per position
        _ptype_lower = pos.get("position_type", "lp")
        _il_risk = "high" if _ptype_lower == "lp" else "none"
        _rs = 7.0 if _ptype_lower == "lp" else (2.0 if _ptype_lower == "lending" else 1.0)
        _grade, _grade_color = risk_score_to_grade(_rs)
        _grade_html = (
            f"<span style='background:{_grade_color}; color:#000; font-size:0.65rem; "
            f"font-weight:800; padding:1px 7px; border-radius:4px; margin-left:6px;'>{_grade}</span>"
        )

        days_str  = f"{days}d" if days > 0 else "—"
        fees_html = f" · Est. fees earned: <span style='color:#10b981'>${fees_est:,.2f}</span>" if fees_est > 0 else ""
        il_html   = f" · IL est: <span style='color:#f59e0b'>{il_pct:.1f}%</span>" if il_pct > 0.1 else ""
        hodl_html = f" · HODL: <span style='color:#64748b'>${hodl:,.0f}</span>" if hodl > 0 else ""
        # Build balance string — support both new format (token_a/token_b) and legacy (token0_balance/token1_balance)
        bal_parts = []
        if pos.get("token_a") and pos.get("token_a_amount", 0) > 0:
            bal_parts.append(f"{pos['token_a_amount']:,.4f} {pos['token_a']}")
        elif pos.get("token0_balance"):
            bal_parts.append(pos["token0_balance"])
        if pos.get("token_b") and pos.get("token_b_amount", 0) > 0:
            bal_parts.append(f"{pos['token_b_amount']:,.4f} {pos['token_b']}")
        elif pos.get("token1_balance"):
            bal_parts.append(pos["token1_balance"])
        bal_str = _html.escape(" · ".join(bal_parts))

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
                        {_grade_html}
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
                if idx < len(positions):
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
            elif float(deposit_usd) <= 0:
                st.error("Deposit amount must be greater than $0.")
            else:
                positions.append({
                    "id":             f"pos_{int(datetime.now(timezone.utc).timestamp())}",
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

render_section_header("Exit Strategy", "Incentive expiry countdown · price targets · exit timeline")

try:
    incentive_expiry = datetime.strptime(INCENTIVE_PROGRAM["expires"].strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
except (ValueError, KeyError):
    incentive_expiry = datetime(2026, 7, 1, tzinfo=timezone.utc)
days_left        = max(0, (incentive_expiry - datetime.now(timezone.utc)).days)
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
    _EXIT_FALLBACKS = {
        "FLR":  FALLBACK_PRICES["FLR"],
        "FXRP": FALLBACK_PRICES["FXRP"],
        "sFLR": FALLBACK_PRICES["FLR"],   # sFLR ≈ FLR
    }
    c1, c2, c3 = st.columns(3)
    with c1:
        asset_choice = st.selectbox("Asset", ["FLR", "FXRP", "sFLR", "Custom"], key="exit_asset")
    with c2:
        default_price = price_lookup.get(asset_choice) or _EXIT_FALLBACKS.get(asset_choice, FALLBACK_PRICES["FLR"])
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
        for i, pos in enumerate(positions):
            days_held = 0
            if pos.get("entry_date"):
                try:
                    _held_dt = datetime.fromisoformat(pos["entry_date"])
                    if _held_dt.tzinfo is None:
                        _held_dt = _held_dt.replace(tzinfo=timezone.utc)
                    days_held = max(0, (datetime.now(timezone.utc) - _held_dt).days)
                except Exception:
                    pass
            proto_key    = pos.get("protocol", "")
            is_incentive = proto_key in ("blazeswap", "enosys", "sparkdex")
            pnl          = pnl_results[i]
            rows.append({
                "Position":     f"{pos.get('pool','?')} ({pos.get('protocol','?').capitalize()})",
                "Type":         pos.get("position_type", "lp").upper(),
                "Days Held":    days_held,
                "Entry APY":    f"{(pos.get('entry_apy') or 0):.1f}%",
                "P&L":          f"{pnl['value_change_pct']:+.1f}%" if pnl["deposit_usd"] > 0 else "—",
                "Incentive":    "⚠️ YES" if is_incentive else "✅ Low",
                "Exit By":      "Jun 2026" if is_incentive else "Flexible",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.caption("DEX LP pools depend on RFLR incentives expiring ~July 2026. FlareDrop ended Jan 30 2026 — sFLR staking yields reduced. Lending positions have low incentive dependency.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── PnL vs HODL Comparison (Feature 5) ──────────────────────────────────────

if positions:
    render_section_header("PnL vs HODL", "Quantified LP vs holding cost — are your fees beating impermanent loss?")

    hodl_rows = []
    total_lp_val   = 0
    total_hodl_val = 0
    total_dep      = 0
    for i, pos in enumerate(positions):
        pnl = pnl_results[i]
        lp_val   = pnl["current_value"]
        hodl_val = pnl["hodl_value"]
        dep      = pnl["deposit_usd"]
        fees_est = pnl["fees_earned_est"]
        if hodl_val > 0 and lp_val > 0:
            total_lp_val   += lp_val
            total_hodl_val += hodl_val
            total_dep      += dep
            diff     = (lp_val + fees_est) - hodl_val
            diff_pct = diff / hodl_val * 100 if hodl_val > 0 else 0
            hodl_rows.append({
                "Position":      f"{pos.get('pool','?')} ({pos.get('protocol','?').capitalize()})",
                "Deposit":       f"${dep:,.0f}",
                "LP Value":      f"${lp_val:,.0f}",
                "Est. Fees":     f"${fees_est:,.2f}",
                "HODL Value":    f"${hodl_val:,.0f}",
                "LP+Fees vs HODL": f"{diff:+,.0f} ({diff_pct:+.1f}%)",
            })

    if hodl_rows:
        st.dataframe(pd.DataFrame(hodl_rows), use_container_width=True, hide_index=True)
        if total_hodl_val > 0:
            net_diff = (total_lp_val + sum(pnl_results[i]["fees_earned_est"] for i in range(len(positions)))) - total_hodl_val
            net_color = "#10b981" if net_diff >= 0 else "#ef4444"
            verdict   = "LP + fees is OUTPERFORMING HODL ✓" if net_diff >= 0 else "HODL would have been better — consider IL impact ⚠"
            st.markdown(
                f"<div style='font-size:0.84rem; color:{net_color}; margin-top:8px; font-weight:600;'>"
                f"Overall: {verdict} (net {net_diff:+,.0f})</div>",
                unsafe_allow_html=True,
            )
        st.caption("HODL value = token amounts × current prices without providing liquidity. Fees are estimated from entry APY × days held.")
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>Add LP positions with token amounts to see HODL comparison.</div>",
            unsafe_allow_html=True,
        )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Claimable Rewards Aggregator (Feature 3) ─────────────────────────────────

if positions:
    render_section_header("Claimable Rewards", "All unclaimed fees + FTSO rewards across your positions")

    total_fees    = sum(float(p.get("unclaimed_fees", 0)) for p in positions)
    total_rewards = sum(float(p.get("rewards", 0)) for p in positions if isinstance(p.get("rewards"), (int, float)))

    # FTSO reward estimate: ~4.3% APY on FLR held in LP positions
    _FTSO_RATE    = 0.043
    flr_in_lp     = 0.0
    for p in positions:
        if p.get("position_type") == "lp":
            tok_a = (p.get("token_a") or "").upper()
            tok_b = (p.get("token_b") or "").upper()
            price_lkp = {pr.get("symbol", ""): pr.get("price_usd", 0) for pr in (prices or [])}
            flr_price = price_lkp.get("FLR") or FALLBACK_PRICES.get("FLR", 0.0088)
            if "FLR" in (tok_a, tok_b) or "WFLR" in (tok_a, tok_b):
                dep = float(p.get("deposit_usd", 0)) * 0.5   # ~50% in FLR side
                flr_in_lp += dep / flr_price if flr_price > 0 else 0

    days_to_expiry = max(0, (datetime(2026, 7, 1, tzinfo=timezone.utc) - datetime.now(timezone.utc)).days)
    ftso_est_usd   = flr_in_lp * FALLBACK_PRICES.get("FLR", 0.0088) * _FTSO_RATE * (30 / 365)  # 30-day estimate

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(f"""<div class="metric-card card-green">
        <div class="label">Unclaimed LP Fees</div>
        <div class="big-number" style="color:#10b981;">${total_fees:,.2f}</div>
        <div style="color:#475569; font-size:0.8rem; margin-top:4px;">Across {len(positions)} position(s)</div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="metric-card card-blue">
        <div class="label">FTSO Delegation Est.</div>
        <div class="big-number">${ftso_est_usd:,.2f}</div>
        <div style="color:#475569; font-size:0.8rem; margin-top:4px;">~30-day estimate @ 4.3% APY</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="metric-card card-orange">
        <div class="label">Incentives Expire In</div>
        <div class="big-number" style="color:#f59e0b;">{days_to_expiry}d</div>
        <div style="color:#475569; font-size:0.8rem; margin-top:4px;">rFLR program ends Jul 2026</div>
        </div>""", unsafe_allow_html=True)

    st.caption("Unclaimed fees pulled from tracked positions. FTSO estimate based on FLR in LP positions at 4.3% APY. Claim via app.flare.network.")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── rFLR Incentive Tracker (Feature 6) ──────────────────────────────────────

if positions:
    _incentive_positions = [p for p in positions if p.get("protocol", "") in ("blazeswap", "enosys", "sparkdex")]
    if _incentive_positions:
        render_section_header("rFLR Incentive Tracker", "Estimated rFLR earned + projected earnings to July 2026")

        _RFLR_PER_USD_DAILY  = 0.0012   # rough: ~43% reward APY on $1 → ~$0.00118/day in rFLR value
        _FLR_PRICE           = (next((pr.get("price_usd", 0) for pr in (prices or []) if pr.get("symbol") == "FLR"), 0)
                                or FALLBACK_PRICES.get("FLR", 0.0088))
        _days_to_jul2026     = max(0, (datetime(2026, 7, 1, tzinfo=timezone.utc) - datetime.now(timezone.utc)).days)

        rflr_rows = []
        for p in _incentive_positions:
            dep       = float(p.get("deposit_usd", 0))
            entry_apy = float(p.get("entry_apy", 0))
            _reward_rate = max(0, (entry_apy - 5) / 100)   # rough: subtract ~5% base fee yield
            days_held = 0
            if p.get("entry_date"):
                try:
                    _p_entry_dt = datetime.fromisoformat(p["entry_date"])
                    if _p_entry_dt.tzinfo is None:
                        _p_entry_dt = _p_entry_dt.replace(tzinfo=timezone.utc)
                    days_held = max(0, (datetime.now(timezone.utc) - _p_entry_dt).days)
                except Exception:
                    pass
            earned_usd   = dep * _reward_rate * days_held / 365 if days_held > 0 else 0
            earned_rflr  = earned_usd / _FLR_PRICE if _FLR_PRICE > 0 else 0
            proj_usd     = dep * _reward_rate * _days_to_jul2026 / 365
            proj_rflr    = proj_usd / _FLR_PRICE if _FLR_PRICE > 0 else 0
            rflr_rows.append({
                "Position":           f"{p.get('pool','?')} ({p.get('protocol','?').capitalize()})",
                "Deposit":            f"${dep:,.0f}",
                "Days Held":          days_held,
                "Est. rFLR Earned":   f"{earned_rflr:,.0f} FLR (≈${earned_usd:,.2f})",
                f"Proj. to Jul 2026": f"{proj_rflr:,.0f} FLR (≈${proj_usd:,.2f})",
            })

        st.dataframe(pd.DataFrame(rflr_rows), use_container_width=True, hide_index=True)
        st.caption(
            f"rFLR rewards estimated from entry APY minus ~5% base fees. FLR price: ${_FLR_PRICE:.4f}. "
            f"Incentive program ends July 1 2026 ({_days_to_jul2026} days). Claim via blazeswap.finance or enosys.finance."
        )
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── FTSO IL Calculator (Feature 4) ──────────────────────────────────────────

render_section_header("IL Calculator", "Real-time impermanent loss with FTSO price data")
render_ftso_il_calculator(prices)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Net Worth Over Time Chart (Feature 2) ────────────────────────────────────

render_section_header("Net Worth Projection", "Portfolio value over time — based on your positions + top opportunity APY")

if positions:
    total_dep_nw = sum(float(p.get("deposit_usd", 0)) for p in positions)
    avg_apy      = 0.0
    _latest_opps = (latest.get("models") or {}).get(ctx.get("profile", "medium")) or []
    if _latest_opps:
        avg_apy = sum(o.get("estimated_apy", 0) for o in _latest_opps[:3]) / min(3, len(_latest_opps))

    if total_dep_nw > 0 and avg_apy > 0:
        months     = list(range(0, 25))
        lp_curve   = [total_dep_nw * ((1 + avg_apy / 100 / 12) ** m) for m in months]
        hodl_curve = [total_dep_nw] * len(months)   # HODL = flat (no yield)

        # Project post-incentive (after Jul 2026): drop to ~5% base fees only
        _months_to_expiry = min(24, max(0, _days_to_jul2026 // 30))
        base_fee_apy = max(5.0, avg_apy * 0.25)  # ~25% of current APY remains as base fees
        post_expiry  = [lp_curve[_months_to_expiry] * ((1 + base_fee_apy / 100 / 12) ** (m - _months_to_expiry))
                        for m in range(_months_to_expiry, len(months))]
        lp_curve_adj = lp_curve[:_months_to_expiry] + post_expiry

        now_dt = datetime.now(timezone.utc)
        dates  = [(now_dt + timedelta(days=30 * m)).strftime("%b %Y") for m in months]

        fig_nw = go.Figure()
        fig_nw.add_trace(go.Scatter(
            x=dates, y=lp_curve,
            mode="lines", name=f"LP @ {avg_apy:.0f}% APY (current)",
            line=dict(color="#3b82f6", width=2, dash="dash"),
            opacity=0.5,
        ))
        fig_nw.add_trace(go.Scatter(
            x=dates, y=lp_curve_adj,
            mode="lines", name="LP (post-incentive adjusted)",
            line=dict(color="#a78bfa", width=2),
            fill="tozeroy", fillcolor="rgba(167,139,250,0.06)",
        ))
        fig_nw.add_trace(go.Scatter(
            x=dates, y=hodl_curve,
            mode="lines", name="HODL (no yield)",
            line=dict(color="#475569", width=1, dash="dot"),
        ))
        # Mark incentive expiry
        if 0 < _months_to_expiry < len(dates):
            fig_nw.add_vline(
                x=dates[_months_to_expiry], line_dash="dot",
                line_color="#f59e0b", opacity=0.6,
                annotation_text="Incentive expiry",
                annotation_font_color="#f59e0b",
            )
        fig_nw.update_layout(
            plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
            font_color="#475569",
            xaxis=dict(gridcolor="rgba(148,163,184,0.15)", color="#475569"),
            yaxis=dict(title="Portfolio Value ($)", gridcolor="rgba(148,163,184,0.15)", color="#475569",
                       tickprefix="$", tickformat=",.0f"),
            legend=dict(font=dict(size=10, color="#64748b"), bgcolor="rgba(0,0,0,0)"),
            margin=dict(l=60, r=20, t=20, b=40),
            height=290,
        )
        st.plotly_chart(fig_nw, use_container_width=True)
        st.caption(
            f"Starting from ${total_dep_nw:,.0f}. LP curve uses top-3 avg APY ({avg_apy:.0f}%). "
            f"Post-incentive drops to ~{base_fee_apy:.0f}% (base fees only). Not financial advice."
        )
    else:
        st.info("Add positions and run a scan to see the net worth projection.")
else:
    st.info("Add positions to see the net worth projection chart.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Historical APY Chart ─────────────────────────────────────────────────────

render_section_header("Historical APY Trend", "Top opportunity APY — last 30 scans")

profile = ctx["profile"]
records = []
for run in runs[-30:]:
    ts   = run.get("run_id", "")
    opps = (run.get("models") or {}).get(profile) or []
    if opps and ts:
        try:
            records.append({"date": datetime.fromisoformat(ts), "apy": opps[0].get("estimated_apy", 0)})
        except Exception:
            pass

if len(records) >= 2:
    df  = pd.DataFrame(records).sort_values("date")
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
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_color="#475569",
        xaxis=dict(gridcolor="rgba(148,163,184,0.15)", color="#475569"),
        yaxis=dict(title="APY %", gridcolor="rgba(148,163,184,0.15)", color="#475569"),
        margin=dict(l=40, r=20, t=20, b=40),
        height=260,
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("Need at least 2 scans to show the chart.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Portfolio Correlation Matrix (Upgrade #5) ────────────────────────────────

render_section_header("Correlation Matrix", "How correlated are your positions? Warns on concentration risk.")

# Static pairwise token correlation matrix (based on known Flare ecosystem behaviour, Mar 2026).
# FLR/sFLR highly correlated; stables uncorrelated; cross-chain crypto partially correlated.
_TOKEN_CORR: dict = {
    ("FLR",   "FLR"):   1.00,
    ("FLR",   "sFLR"):  0.99,
    ("FLR",   "WFLR"):  1.00,
    ("FLR",   "FXRP"):  0.35,
    ("FLR",   "XRP"):   0.35,
    ("FLR",   "stXRP"): 0.35,
    ("FLR",   "USD0"):  0.05,
    ("FLR",   "USDT0"): 0.05,
    ("FLR",   "USDC.e"):0.05,
    ("FLR",   "wETH"):  0.55,
    ("FLR",   "HLN"):   0.60,
    ("sFLR",  "WFLR"):  0.99,
    ("sFLR",  "FXRP"):  0.35,
    ("sFLR",  "USD0"):  0.05,
    ("FXRP",  "XRP"):   0.99,
    ("FXRP",  "stXRP"): 0.98,
    ("FXRP",  "USD0"):  0.05,
    ("FXRP",  "USDT0"): 0.05,
    ("FXRP",  "wETH"):  0.45,
    ("FXRP",  "HLN"):   0.40,
    ("XRP",   "stXRP"): 0.98,
    ("wETH",  "USD0"):  0.05,
    ("wETH",  "HLN"):   0.50,
    ("USD0",  "USDT0"): 0.99,
    ("USD0",  "USDC.e"):0.99,
    ("USDT0", "USDC.e"):0.99,
}


def _get_corr(a: str, b: str) -> float:
    a, b = a.upper(), b.upper()
    if a == b:
        return 1.0
    return _TOKEN_CORR.get((a, b), _TOKEN_CORR.get((b, a), 0.30))  # default: weak positive


def _position_tokens(pos: dict) -> list:
    """Extract the token symbols a position is exposed to."""
    tokens = []
    tok_a = (pos.get("token_a") or "").strip().upper()
    tok_b = (pos.get("token_b") or "").strip().upper()
    if tok_a:
        tokens.append(tok_a)
    if tok_b and tok_b != tok_a:
        tokens.append(tok_b)
    if not tokens:
        # Guess from pool name
        pool = (pos.get("pool") or "").replace("-", "/").replace("_", "/")
        for part in pool.split("/"):
            t = part.strip().upper()
            if t and t not in tokens:
                tokens.append(t)
    return tokens[:2]  # at most 2 tokens per LP


if not positions or len(positions) < 2:
    st.markdown(
        "<div style='color:#334155; font-size:0.85rem;'>"
        "Add at least 2 positions to see the correlation matrix.</div>",
        unsafe_allow_html=True,
    )
else:
    # Build position labels and compute pairwise correlation
    pos_labels = [
        f"{pos.get('pool', '?')} ({pos.get('protocol', '?').capitalize()})"
        for pos in positions
    ]
    n = len(positions)
    corr_matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                corr_matrix[i][j] = 1.0
                continue
            toks_i = _position_tokens(positions[i])
            toks_j = _position_tokens(positions[j])
            if not toks_i or not toks_j:
                corr_matrix[i][j] = 0.30   # unknown
                continue
            # Average pairwise correlation across all token combinations
            pairs = [(a, b) for a in toks_i for b in toks_j]
            corr_matrix[i][j] = sum(_get_corr(a, b) for a, b in pairs) / len(pairs)

    # Plotly heatmap (go already imported at top of file)
    fig_corr = go.Figure(data=go.Heatmap(
        z=corr_matrix,
        x=pos_labels,
        y=pos_labels,
        colorscale=[
            [0.0,  "rgba(16,185,129,0.15)"],
            [0.3,  "rgba(59,130,246,0.25)"],
            [0.7,  "rgba(245,158,11,0.40)"],
            [1.0,  "rgba(239,68,68,0.65)"],
        ],
        zmin=0, zmax=1,
        text=[[f"{v:.2f}" for v in row] for row in corr_matrix],
        texttemplate="%{text}",
        textfont={"size": 11, "color": "#1e293b"},
        hovertemplate="<b>%{y}</b> vs <b>%{x}</b><br>Correlation: %{z:.2f}<extra></extra>",
    ))
    fig_corr.update_layout(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font_color="#475569",
        xaxis=dict(tickangle=-30, tickfont=dict(size=10, color="#475569")),
        yaxis=dict(tickfont=dict(size=10, color="#475569")),
        margin=dict(l=20, r=20, t=20, b=80),
        height=max(240, 80 + 60 * n),
    )
    st.plotly_chart(fig_corr, use_container_width=True)

    # Concentration risk warning
    high_corr_pairs = [
        (pos_labels[i], pos_labels[j], corr_matrix[i][j])
        for i in range(n) for j in range(i + 1, n)
        if corr_matrix[i][j] >= 0.80
    ]
    if high_corr_pairs:
        warn_lines = "".join(
            f"<li>{_html.escape(a)} ↔ {_html.escape(b)} "
            f"(<span style='color:#ef4444; font-weight:600;'>{c:.0%}</span>)</li>"
            for a, b, c in high_corr_pairs
        )
        st.markdown(
            f"<div class='warn-box'>"
            f"<div style='font-weight:700; color:#f59e0b; margin-bottom:6px;'>⚠ Concentration Risk</div>"
            f"<div style='color:#94a3b8; font-size:0.83rem; line-height:1.55;'>"
            f"These positions move together — a single market event could hit all of them:<ul style='margin:6px 0 0 0;'>"
            f"{warn_lines}</ul>"
            f"<div style='margin-top:8px; color:#64748b;'>Consider diversifying into uncorrelated assets (stablecoins, wETH) or reducing position sizes.</div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            "<div style='color:#10b981; font-size:0.85rem; padding:4px 0;'>"
            "✓ Portfolio is well-diversified — no highly correlated position pairs detected.</div>",
            unsafe_allow_html=True,
        )
    st.caption("Correlations are estimates based on Flare ecosystem token relationships. Actual correlations vary with market conditions.")

# ── AgentKit Wallet ───────────────────────────────────────────────────────────
st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
render_section_header("AgentKit Wallet", "Coinbase AgentKit EVM wallet — check on-chain balances")

try:
    from agentkit_wallet import get_wallet_status, get_setup_instructions
    ws = get_wallet_status()

    if ws["available"]:
        # ── Connected state ───────────────────────────────────────────────────
        addr = ws["address"] or "—"
        net  = ws["network"] or "—"
        st.markdown(
            f"<div style='display:flex; align-items:center; gap:8px; margin-bottom:12px;'>"
            f"<span style='display:inline-block; width:8px; height:8px; border-radius:50%; "
            f"background:#10b981;'></span>"
            f"<span style='color:#10b981; font-weight:600; font-size:0.85rem;'>Connected</span>"
            f"<span style='color:#64748b; font-size:0.82rem;'>·</span>"
            f"<span style='color:#475569; font-size:0.82rem; font-family:monospace;'>{addr}</span>"
            f"<span style='color:#64748b; font-size:0.82rem;'>·</span>"
            f"<span style='color:#64748b; font-size:0.82rem;'>{net}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

        balances = ws.get("balances") or {}
        if balances:
            bal_cols = st.columns(min(len(balances), 4))
            for i, (token, amount) in enumerate(balances.items()):
                with bal_cols[i % len(bal_cols)]:
                    st.markdown(
                        f"<div style='background:rgba(16,185,129,0.08); border:1px solid rgba(16,185,129,0.20); "
                        f"border-radius:10px; padding:12px 16px;'>"
                        f"<div style='font-size:0.72rem; font-weight:600; color:#64748b; "
                        f"text-transform:uppercase; letter-spacing:0.06em;'>{_html.escape(str(token))}</div>"
                        f"<div style='font-size:1.4rem; font-weight:700; color:#e2e8f0; margin-top:4px;'>"
                        f"{_html.escape(str(amount))}</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
        else:
            st.markdown(
                "<div style='color:#64748b; font-size:0.85rem;'>No token balances found — wallet may be empty.</div>",
                unsafe_allow_html=True,
            )

        if st.button("↺ Refresh Wallet", key="agentkit_refresh"):
            st.cache_data.clear()
            st.rerun()

    else:
        # ── Not configured / error state ─────────────────────────────────────
        err = ws.get("error", "")
        if err:
            st.markdown(
                f"<div class='warn-box' style='font-size:0.83rem;'>⚠️ {_html.escape(str(err))}</div>",
                unsafe_allow_html=True,
            )
        with st.expander("Setup Instructions", expanded=not ws["available"]):
            st.markdown(get_setup_instructions())

except Exception as _aw_err:
    st.markdown(
        f"<div class='warn-box' style='font-size:0.83rem;'>⚠️ AgentKit wallet unavailable: "
        f"{_html.escape(str(_aw_err))}</div>",
        unsafe_allow_html=True,
    )

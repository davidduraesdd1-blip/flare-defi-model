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
    render_what_this_means, get_user_level,
)


def _sanitize_address(addr: str) -> str:
    """Sanitize a user-supplied Ethereum address (#13).

    Strips all non-hex characters, then re-attaches the 0x prefix so that
    an 'x' mid-string (e.g. '0xABCxDEF...') cannot survive and produce an
    address that passes the length check but contains an invalid character.
    """
    import re
    stripped = addr.strip()
    # Detect and remove 0x prefix before hex-only filtering
    has_prefix = stripped.lower().startswith("0x")
    hex_part = stripped[2:] if has_prefix else stripped
    # Keep only valid hex characters (no 'x' allowed here)
    hex_cleaned = re.sub(r"[^0-9a-fA-F]", "", hex_part)
    # Re-attach prefix and truncate to 42 chars (0x + 40 hex = 42)
    return ("0x" + hex_cleaned)[:42]


@st.cache_data(ttl=14400, show_spinner=False)
def _fetch_live_token_corr(tokens_key: str) -> dict:
    """Fetch 90-day Pearson correlations for known tokens via yfinance (4-hour cache).

    tokens_key — sorted comma-joined uppercase symbol list (hashable cache key).
    Returns {(SYM_A, SYM_B): float} for all pairs found, or {} on failure.
    Stablecoins are excluded from the live fetch (correlations are hardcoded to ~0.99/0.04).
    Falls back to hardcoded table per _get_corr_l() — callers never see an exception.
    """
    _TICKER_MAP = {
        "BTC": "BTC-USD", "WBTC": "BTC-USD", "CBBTC": "BTC-USD",
        "ETH": "ETH-USD", "WETH": "ETH-USD", "STETH": "ETH-USD", "CBETH": "ETH-USD",
        "XRP": "XRP-USD", "FXRP": "XRP-USD", "STXRP": "XRP-USD",
        "SOL": "SOL-USD", "BNB": "BNB-USD", "AVAX": "AVAX-USD",
        "ADA": "ADA-USD", "DOT": "DOT-USD", "LINK": "LINK-USD",
        "ATOM": "ATOM-USD", "LTC": "LTC-USD", "XLM": "XLM-USD",
        "HBAR": "HBAR-USD", "MATIC": "MATIC-USD", "POL": "MATIC-USD",
        "FLR": "FLR-USD", "XDC": "XDC-USD", "HLN": "HLN-USD",
        "DOGE": "DOGE-USD", "SHIB": "SHIB-USD", "UNI": "UNI-USD",
        "AAVE": "AAVE-USD", "CRV": "CRV-USD", "MKR": "MKR-USD",
    }
    _STABLES_LIVE = frozenset({
        "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "FRAX", "LUSD",
        "USD0", "USDT0", "USDC.E", "GUSD", "PYUSD", "CRVUSD", "USDP",
    })
    try:
        import yfinance as _yf
        tokens_list = [
            t.strip() for t in tokens_key.split(",")
            if t.strip() and t.strip().upper() not in _STABLES_LIVE
            and t.strip().upper() in _TICKER_MAP
        ]
        if len(tokens_list) < 2:
            return {}

        # Deduplicate tickers: FXRP + XRP both map to XRP-USD → treat as one
        tick_to_syms: dict = {}
        sym_to_tick: dict = {}
        for sym in tokens_list:
            tick = _TICKER_MAP[sym.upper()]
            sym_to_tick[sym.upper()] = tick
            tick_to_syms.setdefault(tick, []).append(sym.upper())

        unique_tickers = list(tick_to_syms.keys())
        if len(unique_tickers) < 2:
            return {}

        raw = _yf.download(unique_tickers, period="120d", auto_adjust=True, progress=False)

        # Extract Close prices — handle both MultiIndex (multi-ticker) and flat (single-ticker)
        if hasattr(raw.columns, "levels"):
            lvl0 = list(raw.columns.get_level_values(0))
            lvl1 = list(raw.columns.get_level_values(1))
            if "Close" in lvl0:
                close_df = raw["Close"]
            elif "Close" in lvl1:
                close_df = raw.xs("Close", level=1, axis=1)
            else:
                return {}
        elif "Close" in raw.columns:
            close_df = raw[["Close"]].rename(columns={"Close": unique_tickers[0]})
        else:
            return {}

        close_df = close_df.dropna(axis=1, thresh=60)
        if close_df.shape[1] < 2:
            return {}

        corr_df = close_df.pct_change().dropna().corr()

        result: dict = {}
        for tick_a, syms_a in tick_to_syms.items():
            if tick_a not in corr_df.columns:
                continue
            for tick_b, syms_b in tick_to_syms.items():
                if tick_a == tick_b:
                    continue
                if tick_b not in corr_df.columns:
                    continue
                val = float(corr_df.loc[tick_a, tick_b])
                if not (-1.0 <= val <= 1.0):
                    continue
                # Map the correlation to all alias pairs for each ticker
                for sa in syms_a:
                    for sb in syms_b:
                        result[(sa, sb)] = val
                        result[(sb, sa)] = val

        # Aliases that share a ticker move identically (FXRP ↔ XRP = 0.99)
        for _syms in tick_to_syms.values():
            for i in range(len(_syms)):
                for j in range(i + 1, len(_syms)):
                    result[(_syms[i], _syms[j])] = 0.99
                    result[(_syms[j], _syms[i])] = 0.99

        return result
    except Exception:
        return {}


from config import PROTOCOLS, TOKENS, INCENTIVE_PROGRAM, RISK_PROFILES, FALLBACK_PRICES

page_setup("Portfolio · Family Office · DeFi Intelligence")

# ── Portfolio page: unified 0.85rem across all form elements ──────────────────
st.markdown("""
<style>
/* Labels on every input / select / date */
[data-testid="stMain"] label,
[data-testid="stMain"] label p,
[data-testid="stMain"] label span { font-size: 0.85rem !important; }

/* Text inputs and number inputs */
[data-testid="stMain"] input[type="text"],
[data-testid="stMain"] input[type="number"],
[data-testid="stMain"] input[type="date"],
[data-testid="stMain"] textarea { font-size: 0.85rem !important; }

/* Selectbox / dropdown — displayed value */
[data-testid="stMain"] [data-baseweb="select"] span,
[data-testid="stMain"] [data-baseweb="select"] div,
[data-testid="stMain"] [data-baseweb="select"] input { font-size: 0.85rem !important; }

/* Dropdown option list items */
[data-testid="stMain"] [role="listbox"] li,
[data-testid="stMain"] [role="option"],
[data-testid="stMain"] [role="option"] * { font-size: 0.85rem !important; }

/* Buttons (Add Position, form submit) */
[data-testid="stMain"] button p,
[data-testid="stMain"] button span,
[data-testid="stFormSubmitButton"] button p { font-size: 0.85rem !important; }

/* Tabs (Price Targets / Exit Timeline) */
[data-testid="stMain"] [data-testid="stTab"] p,
[data-testid="stMain"] [data-testid="stTab"] span { font-size: 0.85rem !important; }

/* Table / dataframe cells and headers */
[data-testid="stMain"] [data-testid="stDataFrameResizable"] th,
[data-testid="stMain"] [data-testid="stDataFrameResizable"] td,
[data-testid="stMain"] [data-testid="stDataFrameResizable"] * { font-size: 0.85rem !important; }

/* General markdown text, subtitles, help text */
[data-testid="stMain"] p { font-size: 0.85rem !important; }
[data-testid="stMain"] small,
[data-testid="stMain"] [data-testid="stCaptionContainer"] p { font-size: 0.75rem !important; }
</style>
""", unsafe_allow_html=True)

ctx            = render_sidebar()
portfolio_size = ctx["portfolio_size"]
pro_mode       = ctx.get("pro_mode", False)   # #82 Beginner/Pro mode
demo_mode      = ctx.get("demo_mode", False)  # #67 Demo/Sandbox mode

latest    = load_latest()
runs      = load_history_runs()
positions = load_positions()
flare_scan = latest.get("flare_scan") or {}
prices     = load_live_prices() or flare_scan.get("prices") or []

st.title("💼 Portfolio")
st.caption("Track your DeFi positions, wallet balances, P&L, and exit strategies across all Flare protocols")

# ──────────────────────────────────────────────────────────────────────────────
# HERO CARD — total portfolio value, 24h change, allocation breakdown
# ──────────────────────────────────────────────────────────────────────────────
def _build_hero_metrics(positions: list, runs: list, latest: dict) -> dict:
    """Compute hero card numbers from positions + history."""
    total_value   = sum(float(p.get("current_value") or 0) for p in positions)
    total_deposit = sum(float(p.get("deposit_usd") or p.get("entry_value") or 0) for p in positions)
    total_pnl     = total_value - total_deposit
    total_fees    = sum(float(p.get("unclaimed_fees") or 0) for p in positions)

    # Approximate 24h change from history (last two scan runs)
    change_24h = 0.0
    if len(runs) >= 2:
        try:
            vals = [
                sum(float(p.get("current_value") or 0) for p in (r.get("positions") or []))
                for r in runs[-2:]
            ]
            if len(vals) == 2 and vals[0] > 0:
                change_24h = ((vals[1] - vals[0]) / vals[0]) * 100
        except Exception:
            pass

    # Allocation by protocol
    alloc: dict = {}
    for p in positions:
        proto = str(p.get("protocol") or "Unknown").capitalize()
        alloc[proto] = alloc.get(proto, 0) + float(p.get("current_value") or 0)

    return {
        "total_value":   total_value,
        "total_pnl":     total_pnl,
        "total_fees":    total_fees,
        "change_24h":    change_24h,
        "allocation":    alloc,
        "position_count": len(positions),
    }


_hero = _build_hero_metrics(positions, runs, latest)

if _hero["total_value"] > 0 or positions:
    _v  = _hero["total_value"]
    _pnl = _hero["total_pnl"]
    _fees = _hero["total_fees"]
    _chg  = _hero["change_24h"]
    _chg_color = "#22c55e" if _chg >= 0 else "#ef4444"
    _pnl_color = "#22c55e" if _pnl >= 0 else "#ef4444"

    # Hero stat row
    _c1, _c2, _c3, _c4 = st.columns(4)
    _c1.metric("Total Portfolio", f"${_v:,.0f}",
               delta=f"{_chg:+.2f}% 24h" if abs(_chg) > 0.001 else None,
               delta_color="normal")
    _c2.metric("Unrealized P&L", f"${_pnl:+,.0f}")
    _c3.metric("Unclaimed Fees", f"${_fees:,.2f}")
    _c4.metric("Positions", str(_hero["position_count"]))

    # Allocation pie + net worth sparkline side by side
    if _hero["allocation"]:
        _col_pie, _col_spark = st.columns([1, 2])

        with _col_pie:
            _alloc_labels = list(_hero["allocation"].keys())
            _alloc_vals   = list(_hero["allocation"].values())
            _fig_pie = go.Figure(go.Pie(
                labels=_alloc_labels, values=_alloc_vals,
                hole=0.55,
                marker_colors=["#00d4aa", "#3b82f6", "#f59e0b", "#8b5cf6", "#ef4444",
                                "#22c55e", "#f97316", "#06b6d4"],
                textfont_size=11,
            ))
            _fig_pie.update_layout(
                showlegend=True, legend_font_size=11,
                paper_bgcolor="rgba(0,0,0,0)", margin=dict(l=0, r=0, t=20, b=0),
                height=180,
                annotations=[dict(
                    text=f"${_v:,.0f}", x=0.5, y=0.5, showarrow=False,
                    font=dict(size=14, color="#f1f5f9"), xref="paper", yref="paper"
                )],
            )
            st.plotly_chart(_fig_pie, width='stretch', config={"displayModeBar": False})

        with _col_spark:
            # Net worth sparkline from history runs
            _nw_dates  = []
            _nw_values = []
            for _r in (runs or []):
                try:
                    _ts = _r.get("timestamp") or _r.get("_ts") or ""
                    _rv = sum(float(p.get("current_value") or 0) for p in (_r.get("positions") or []))
                    if _ts and _rv > 0:
                        _nw_dates.append(str(_ts)[:10])
                        _nw_values.append(_rv)
                except Exception:
                    pass
            # Fall back to current point only if no history
            if not _nw_values:
                _nw_dates  = [datetime.now(timezone.utc).strftime("%Y-%m-%d")]
                _nw_values = [_v]

            _fig_nw = go.Figure()
            _fig_nw.add_trace(go.Scatter(
                x=_nw_dates, y=_nw_values,
                mode="lines", fill="tozeroy",
                line=dict(color="#00d4aa", width=2),
                fillcolor="rgba(0,212,170,0.08)",
                name="Net Worth",
            ))
            _fig_nw.update_layout(
                xaxis=dict(showgrid=False, tickfont_size=10, showticklabels=len(_nw_dates) > 1),
                yaxis=dict(showgrid=True, gridcolor="rgba(255,255,255,0.04)",
                           tickprefix="$", tickfont_size=10),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                font_color="#94a3b8", margin=dict(l=8, r=8, t=20, b=8),
                height=180, title_text="Net Worth History", title_font_size=12,
                showlegend=False,
            )
            st.plotly_chart(_fig_nw, width='stretch', config={"displayModeBar": False})

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Main Portfolio Tabs ─────────────────────────────────────────────────────
_tab_pos, _tab_wallet, _tab_rewards, _tab_fassets = st.tabs([
    "📊 Positions", "👛 Wallet", "🎁 Rewards & Incentives", "🔗 FAssets",
])

with _tab_pos:
    # ── Demo Mode Holdings (#67) ──────────────────────────────────────────────────
    if demo_mode:
        st.warning(
            "Demo Mode — Showing sample portfolio data. No API keys required.",
            icon="🎭",
        )
        try:
            from data.demo_data import DEMO_PORTFOLIO
            _demo_holdings = DEMO_PORTFOLIO.get("holdings", [])
            _demo_total    = DEMO_PORTFOLIO.get("total_value_usd", 0)
            if _demo_holdings:
                st.markdown("**Sample Holdings**")
                _demo_rows = [
                    {
                        "Protocol":    h["protocol"],
                        "Asset":       h["asset"],
                        "Value (USD)": f"${h['amount_usd']:,.0f}",
                        "APY":         f"{h['apy']*100:.1f}%",
                        "Est. Annual": f"${h['amount_usd']*h['apy']:,.0f}",
                    }
                    for h in _demo_holdings
                ]
                st.dataframe(pd.DataFrame(_demo_rows), width='stretch', hide_index=True)
                st.metric("Total Portfolio Value", f"${_demo_total:,.0f}")
        except Exception as _e:
            logger.warning("[Portfolio] demo data error: %s", _e)
            st.info("Demo data temporarily unavailable — refresh to try again.")



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
            str(pos.get("protocol") or "").capitalize(),
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

    from fpdf.enums import XPos, YPos  # fpdf2 v2.5.2+ new_x/new_y API

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Flare DeFi Model - Portfolio Report", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Generated: {report_date}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    # Summary row
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Portfolio Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(60, 6, f"Total Value:   ${total_value:,.2f}")
    pdf.cell(60, 6, f"Total P&L:   ${total_pnl:+,.2f}")
    pdf.cell(0,  6, f"Unclaimed Fees:   ${total_fees:,.2f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(60, 6, f"Positions:   {len(positions)}")
    pdf.cell(0,  6, f"Deposited:   ${total_deposit:,.2f}", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
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
            str(pos.get("protocol") or "").capitalize()[:12],
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
    pdf.cell(0, 5, "Not financial advice. DeFi positions carry risk including impermanent loss and smart contract risk.", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())


def _build_portfolio_csv(holdings: list) -> bytes:
    """Build a UTF-8 CSV from a list of holding dicts (protocol/asset/amount_usd/apy)."""
    import pandas as pd
    if not holdings:
        return b"protocol,asset,amount_usd,apy\n"
    df = pd.DataFrame(holdings)
    return df.to_csv(index=False).encode("utf-8")


def _build_portfolio_report(holdings: list, total_value: float) -> bytes:
    """Build a plain-text portfolio summary report."""
    lines = [
        "DeFi Portfolio Report",
        f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"Total Value: ${total_value:,.0f}",
        "",
        "Holdings:",
    ]
    for h in holdings:
        lines.append(
            f"  {h.get('protocol') or ''} / {h.get('asset') or ''}: "
            f"${(h.get('amount_usd') or 0):,.0f} @ {(h.get('apy') or 0) * 100:.1f}% APY"
        )
    return "\n".join(lines).encode("utf-8")


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



with _tab_wallet:
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
                _clean_addr = _sanitize_address(new_addr.strip()) if new_addr else ""
                if _clean_addr and len(_clean_addr) == 42 and _clean_addr.startswith("0x"):
                    try:
                        from web3 import Web3
                        checksum_addr = Web3.to_checksum_address(_clean_addr)
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
                            logger.warning("[Portfolio] wallet balance fetch failed: %s", e)
                            st.error("Unable to fetch wallet balances — please check your address and try again.")

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
                            logger.warning("[Portfolio] position detection failed: %s", e)
                            st.error("Position detection failed — check your wallet address and try again.")

            # ── Detected position suggestions ─────────────────────────────────────
            if st.session_state.get("_pos_suggestions"):
                st.markdown("**Detected positions — confirm to add:**")
                for i, sug in enumerate(st.session_state["_pos_suggestions"]):
                    ca2, cb2 = st.columns([5, 1])
                    with ca2:
                        import html as _html_p
                        _sug_pool     = _html_p.escape(str(sug.get("pool", "")))
                        _sug_protocol = _html_p.escape(str(sug.get("protocol") or "").capitalize())
                        _sug_ptype    = _html_p.escape(str(sug.get("position_type", "")))
                        _sug_token_a  = _html_p.escape(str(sug.get("token_a", "")))
                        st.markdown(
                            f"<div style='font-size:0.85rem; color:#94a3b8; padding:4px 0;'>"
                            f"<b>{_sug_pool}</b> · {_sug_protocol} · "
                            f"{_sug_ptype} · {sug.get('token_a_amount', 0):,.4f} {_sug_token_a}"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                    with cb2:
                        if st.button("Add", key=f"add_sug_{i}", use_container_width=True):
                            new_pos = dict(sug)
                            new_pos["id"] = f"pos_{int(datetime.now(timezone.utc).timestamp())}_{i}"
                            positions.append(new_pos)
                            save_positions(positions)
                            # Build a new list excluding this index — never pop during iteration
                            st.session_state["_pos_suggestions"] = [
                                s for j, s in enumerate(st.session_state["_pos_suggestions"]) if j != i
                            ]
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


    # ─── Zerion Wallet Positions (#111) ───────────────────────────────────────────

    render_section_header("Wallet Positions", "Live DeFi portfolio via Zerion — chain breakdown & top positions")

    @st.cache_data(ttl=120)
    def _fetch_zerion_cached(addr: str) -> dict:
        from scanners.wallet import fetch_zerion_portfolio
        return fetch_zerion_portfolio(addr)


    try:
        _zerion_addr = st.session_state.get("defi_wallet_address_valid")
        if _zerion_addr:
            with st.spinner("Fetching wallet portfolio from Zerion…"):
                try:
                    _zp = _fetch_zerion_cached(_zerion_addr)
                    _zp_err = _zp.get("error")
                    if _zp_err:
                        logger.warning("[Portfolio] Zerion API error: %s", _zp_err)
                        st.warning("Zerion data temporarily unavailable — try refreshing in a few minutes.")
                    else:
                        # Summary metric
                        _z_total = _zp.get("total_value_usd", 0.0)
                        st.metric("Total Wallet Value (USD)", f"${_z_total:,.2f}")

                        # Chain breakdown chart
                        _chain_bd = _zp.get("chain_breakdown") or {}
                        if _chain_bd:
                            import plotly.express as px
                            _chain_df = pd.DataFrame([
                                {"Chain": k, "Value (USD)": v}
                                for k, v in _chain_bd.items() if v > 0
                            ])
                            if not _chain_df.empty:
                                _fig_chain = px.pie(
                                    _chain_df, names="Chain", values="Value (USD)",
                                    title="Chain Breakdown",
                                    color_discrete_sequence=px.colors.sequential.Plasma_r,
                                )
                                _fig_chain.update_layout(
                                    paper_bgcolor="rgba(0,0,0,0)",
                                    plot_bgcolor="rgba(0,0,0,0)",
                                    font_color="#94a3b8",
                                    margin=dict(l=20, r=20, t=40, b=20),
                                    height=280,
                                    showlegend=True,
                                )
                                st.plotly_chart(_fig_chain, width='stretch', config={"displayModeBar": False})

                        # Top 10 positions table
                        _z_positions = (_zp.get("positions") or [])[:10]
                        if _z_positions:
                            st.markdown("**Top 10 Positions**")
                            _z_rows = []
                            for _zpos in _z_positions:
                                _chg = _zpos.get("change_1d_pct", 0)
                                _chg_str = f"{_chg:+.2f}%" if _chg else "—"
                                _z_rows.append({
                                    "Asset":     _zpos.get("name", "—"),
                                    "Chain":     _zpos.get("chain", "—"),
                                    "Value USD": f"${_zpos.get('value_usd', 0):,.2f}",
                                    "Quantity":  f"{_zpos.get('quantity', 0):,.4f}",
                                    "Price":     f"${_zpos.get('price', 0):,.4f}" if _zpos.get("price") else "—",
                                    "1d Change": _chg_str,
                                })
                            st.dataframe(pd.DataFrame(_z_rows), width='stretch', hide_index=True)

                        # DeFi protocols
                        _protos = _zp.get("defi_protocols") or []
                        if _protos:
                            st.caption(f"DeFi protocols detected: {', '.join(_protos)}")

                        st.caption(f"Data from Zerion · Read-only · Refreshes every 5 min · {_zp.get('timestamp', '')}")
                except ImportError:
                    st.info("Zerion module unavailable.")
                except Exception as _ze:
                    logger.warning("[Portfolio] Zerion portfolio fetch failed: %s", _ze)
                    st.error("Zerion portfolio data unavailable — try again in a moment.")
        else:
            st.info("Connect wallet to see your DeFi positions. Enter your EVM address in the sidebar Wallet Import section.")
    except Exception as _outer_e:
        logger.warning("[Portfolio] wallet positions section failed: %s", _outer_e)
        st.warning("Wallet positions temporarily unavailable — try refreshing.")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)



with _tab_pos:
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
            proto    = _html.escape(str(pos.get("protocol") or "?").capitalize())
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
                        Unclaimed fees: <span style="color:#10b981">${pnl['unclaimed_fees']:,.2f}</span>{"  ·  " + bal_str if bal_str else ""}
                    </div>
                </div>""", unsafe_allow_html=True)
            with col_del:
                if st.button("✕", key=f"del_pos_{idx}", help="Remove position"):
                    save_positions([p for i, p in enumerate(positions) if i != idx])
                    st.rerun()

    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.9rem; padding:20px 0;'>"
            "No positions tracked yet. Add your first position below.</div>",
            unsafe_allow_html=True,
        )

    # ── Add Position ──────────────────────────────────────────────────────────────
    with st.expander("➕ Track a New Position"):
        with st.form("add_position_form", clear_on_submit=True, enter_to_submit=False):
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
                                          value=float(default_price), format="%.6f", step=0.001,
                                          key=f"exit_price_{asset_choice}")
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
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
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
                    "Position":     f"{pos.get('pool','?')} ({str(pos.get('protocol') or '?').capitalize()})",
                    "Type":         pos.get("position_type", "lp").upper(),
                    "Days Held":    days_held,
                    "Entry APY":    f"{(pos.get('entry_apy') or 0):.1f}%",
                    "P&L":          f"{pnl['value_change_pct']:+.1f}%" if pnl["deposit_usd"] > 0 else "—",
                    "Incentive":    "⚠️ YES" if is_incentive else "✅ Low",
                    "Exit By":      "Jun 2026" if is_incentive else "Flexible",
                })
            st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            st.caption("DEX LP pools depend on RFLR incentives expiring ~July 2026. FlareDrop ended Jan 30 2026 — sFLR staking yields reduced. Lending positions have low incentive dependency.")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


    # ─── PnL vs HODL Comparison (Feature 5) ──────────────────────────────────────

    if positions:
        render_section_header("Triple P&L Benchmarks", "How your DeFi strategy compares to holding USD, ETH, or your own tokens")

        # ── Fetch ETH price benchmark ──────────────────────────────────────────
        @st.cache_data(ttl=900)
        def _get_eth_benchmark_prices(entry_date_str: str) -> dict:
            """Get ETH-USD price at entry date and today using yfinance."""
            try:
                import yfinance as yf
                from datetime import timedelta
                _entry = datetime.strptime(entry_date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                _end   = datetime.now(timezone.utc)
                _start = _entry - timedelta(days=2)   # 2-day buffer for non-trading days
                _df    = yf.download(
                    "ETH-USD",
                    start=_start.strftime("%Y-%m-%d"),
                    end=_end.strftime("%Y-%m-%d"),
                    progress=False,
                    auto_adjust=True,
                )
                if _df.empty:
                    return {}
                _close = _df["Close"].dropna()
                if len(_close) < 2:
                    return {}
                return {
                    "entry_price": float(_close.iloc[0]),
                    "current_price": float(_close.iloc[-1]),
                }
            except Exception:
                return {}

        hodl_rows = []
        total_lp_val   = 0
        total_hodl_val = 0
        total_dep      = 0
        total_fees_lp  = 0
        for i, pos in enumerate(positions):
            pnl      = pnl_results[i]
            lp_val   = pnl["current_value"]
            hodl_val = pnl["hodl_value"]
            dep      = pnl["deposit_usd"]
            fees_est = pnl["fees_earned_est"]
            if hodl_val > 0 and lp_val > 0:
                total_lp_val   += lp_val
                total_hodl_val += hodl_val
                total_dep      += dep
                total_fees_lp  += fees_est
                diff     = (lp_val + fees_est) - hodl_val
                diff_pct = diff / hodl_val * 100 if hodl_val > 0 else 0

                # USD benchmark: original deposit unchanged (0% return)
                usd_val  = dep
                usd_diff = (lp_val + fees_est) - usd_val

                # ETH benchmark: how much ETH could we have bought at entry?
                _entry_date = pos.get("entry_date", "")
                eth_val = 0.0
                if _entry_date:
                    _eth_px = _get_eth_benchmark_prices(_entry_date)
                    if _eth_px:
                        _eth_entry = _eth_px.get("entry_price", 0)
                        _eth_now   = _eth_px.get("current_price", 0)
                        if _eth_entry > 0:
                            eth_val  = dep * (_eth_now / _eth_entry)
                eth_diff = (lp_val + fees_est) - eth_val if eth_val > 0 else 0.0

                row = {
                    "Position":        f"{pos.get('pool','?')} ({str(pos.get('protocol') or '?').capitalize()})",
                    "Deposit":         f"${dep:,.0f}",
                    "LP+Fees":         f"${lp_val + fees_est:,.0f}",
                    "vs USD (0%)":     f"{usd_diff:+,.0f}",
                    "vs HODL tokens":  f"{diff:+,.0f} ({diff_pct:+.1f}%)",
                }
                if eth_val > 0:
                    row["vs ETH"] = f"{eth_diff:+,.0f}"
                hodl_rows.append(row)

        if hodl_rows:
            st.dataframe(pd.DataFrame(hodl_rows), width='stretch', hide_index=True)
            if total_hodl_val > 0:
                net_diff  = (total_lp_val + total_fees_lp) - total_hodl_val
                net_color = "#10b981" if net_diff >= 0 else "#ef4444"
                verdict   = "LP + fees BEATS token HODL ✓" if net_diff >= 0 else "Token HODL would have been better ⚠"
                usd_net   = (total_lp_val + total_fees_lp) - total_dep
                usd_col   = "#10b981" if usd_net >= 0 else "#ef4444"
                _bench_c1, _bench_c2 = st.columns(2)
                with _bench_c1:
                    st.markdown(
                        f"<div style='font-size:0.84rem; color:{net_color}; font-weight:600; margin-top:6px;'>"
                        f"vs HODL: {verdict} (net {net_diff:+,.0f})</div>",
                        unsafe_allow_html=True,
                    )
                with _bench_c2:
                    st.markdown(
                        f"<div style='font-size:0.84rem; color:{usd_col}; font-weight:600; margin-top:6px;'>"
                        f"vs USD baseline: {usd_net:+,.0f} total profit</div>",
                        unsafe_allow_html=True,
                    )
            st.caption(
                "LP+Fees = current position value + estimated accrued fees. "
                "USD baseline = original deposit (0% return, no risk). "
                "HODL = holding the same tokens without providing liquidity. "
                "ETH = buying ETH at entry date price instead. "
                "ETH prices from Yahoo Finance — requires internet."
            )
        else:
            st.markdown(
                "<div style='color:#334155; font-size:0.85rem;'>Add LP positions with token amounts to see benchmarks.</div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)



with _tab_rewards:
    # ─── Rewards & Incentives ─────────────────────────────────────────────────────

    if positions:
        render_section_header("Rewards & Incentives", "Unclaimed fees · FTSO rewards · rFLR incentive tracker")

        # ── Claimable Rewards ──────────────────────────────────────────────────────
        total_fees    = sum(float(p.get("unclaimed_fees", 0)) for p in positions)
        total_rewards = sum(float(p.get("rewards", 0)) for p in positions if isinstance(p.get("rewards"), (int, float)))

        _FTSO_RATE    = 0.043
        flr_in_lp     = 0.0
        for p in positions:
            if p.get("position_type") == "lp":
                tok_a = (p.get("token_a") or "").upper()
                tok_b = (p.get("token_b") or "").upper()
                price_lkp = {pr.get("symbol", ""): pr.get("price_usd", 0) for pr in (prices or [])}
                flr_price = price_lkp.get("FLR") or FALLBACK_PRICES.get("FLR", 0.0088)
                if "FLR" in (tok_a, tok_b) or "WFLR" in (tok_a, tok_b):
                    dep = float(p.get("deposit_usd", 0)) * 0.5
                    flr_in_lp += dep / flr_price if flr_price > 0 else 0

        _days_to_jul2026 = max(0, (datetime(2026, 7, 1, tzinfo=timezone.utc) - datetime.now(timezone.utc)).days)
        days_to_expiry   = _days_to_jul2026
        ftso_est_usd     = flr_in_lp * FALLBACK_PRICES.get("FLR", 0.0088) * _FTSO_RATE * (30 / 365)

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

        # ── rFLR Incentive Tracker ─────────────────────────────────────────────────
        _incentive_positions = [p for p in positions if p.get("protocol", "") in ("blazeswap", "enosys", "sparkdex")]
        if _incentive_positions:
            st.markdown("##### rFLR Incentive Tracker")
            _FLR_PRICE = (next((pr.get("price_usd", 0) for pr in (prices or []) if pr.get("symbol") == "FLR"), 0)
                          or FALLBACK_PRICES.get("FLR", 0.0088))
            rflr_rows = []
            for p in _incentive_positions:
                dep          = float(p.get("deposit_usd", 0))
                entry_apy    = float(p.get("entry_apy", 0))
                _reward_rate = max(0, (entry_apy - 5) / 100)
                days_held    = 0
                if p.get("entry_date"):
                    try:
                        _p_entry_dt = datetime.fromisoformat(p["entry_date"])
                        if _p_entry_dt.tzinfo is None:
                            _p_entry_dt = _p_entry_dt.replace(tzinfo=timezone.utc)
                        days_held = max(0, (datetime.now(timezone.utc) - _p_entry_dt).days)
                    except Exception:
                        pass
                earned_usd  = dep * _reward_rate * days_held / 365 if days_held > 0 else 0
                earned_rflr = earned_usd / _FLR_PRICE if _FLR_PRICE > 0 else 0
                proj_usd    = dep * _reward_rate * _days_to_jul2026 / 365
                proj_rflr   = proj_usd / _FLR_PRICE if _FLR_PRICE > 0 else 0
                rflr_rows.append({
                    "Position":           f"{p.get('pool','?')} ({str(p.get('protocol') or '?').capitalize()})",
                    "Deposit":            f"${dep:,.0f}",
                    "Days Held":          days_held,
                    "Est. rFLR Earned":   f"{earned_rflr:,.0f} FLR (≈${earned_usd:,.2f})",
                    f"Proj. to Jul 2026": f"{proj_rflr:,.0f} FLR (≈${proj_usd:,.2f})",
                })
            st.dataframe(pd.DataFrame(rflr_rows), width='stretch', hide_index=True)
            st.caption(
                f"rFLR rewards estimated from entry APY minus ~5% base fees. FLR price: ${_FLR_PRICE:.4f}. "
                f"Incentive program ends July 1 2026 ({_days_to_jul2026} days). Claim via blazeswap.finance or enosys.finance."
            )
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)



with _tab_pos:
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
            # Mark incentive expiry — use add_shape instead of add_vline to avoid
            # Plotly's _mean() TypeError when x-axis contains string categorical labels.
            if 0 < _months_to_expiry < len(dates):
                _expiry_x = dates[_months_to_expiry]
                fig_nw.add_shape(
                    type="line",
                    x0=_expiry_x, x1=_expiry_x,
                    y0=0, y1=1,
                    xref="x", yref="paper",
                    line=dict(color="#f59e0b", dash="dot", width=1.5),
                    opacity=0.6,
                )
                fig_nw.add_annotation(
                    x=_expiry_x, y=1,
                    xref="x", yref="paper",
                    text="Incentive expiry",
                    showarrow=False,
                    font=dict(color="#f59e0b", size=10),
                    xanchor="left",
                    yanchor="top",
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
            st.plotly_chart(fig_nw, width='stretch')
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
        st.plotly_chart(fig, width='stretch')
    else:
        st.info("Need at least 2 scans to show the chart.")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


    # ─── Portfolio Correlation Matrix (Item 13 — improved) ───────────────────────

    render_section_header(
        "Correlation Matrix",
        "Live inter-position correlations · Protocol concentration risk · Bear market stress test",
    )

    # ── Fallback correlation table (historical estimates where live data unavailable) ─
    # Sources: 90-day rolling Pearson on daily closes; Flare ecosystem analysis; CoinGecko.
    _TOKEN_CORR: dict = {
        # ── Flare ecosystem (high internal correlation) ──────────────────────
        ("FLR",   "sFLR"):   0.99,
        ("FLR",   "WFLR"):   1.00,
        ("FLR",   "FXRP"):   0.35,
        ("FLR",   "XRP"):    0.35,
        ("FLR",   "stXRP"):  0.35,
        ("FLR",   "wETH"):   0.55,
        ("FLR",   "HLN"):    0.60,
        ("FLR",   "wBTC"):   0.50,
        ("FLR",   "BTC"):    0.50,
        ("FLR",   "ETH"):    0.52,
        ("FLR",   "SOL"):    0.42,
        ("FLR",   "XLM"):    0.30,
        ("FLR",   "XDC"):    0.28,
        ("FLR",   "HBAR"):   0.32,
        ("FLR",   "SHX"):    0.22,
        ("FLR",   "ZBCN"):   0.18,
        ("FLR",   "CC"):     0.20,
        ("sFLR",  "WFLR"):   0.99,
        ("sFLR",  "FXRP"):   0.35,
        ("sFLR",  "XRP"):    0.35,
        ("sFLR",  "wETH"):   0.53,
        ("sFLR",  "wBTC"):   0.48,
        # ── XRP family ───────────────────────────────────────────────────────
        ("FXRP",  "XRP"):    0.99,
        ("FXRP",  "stXRP"):  0.98,
        ("FXRP",  "wETH"):   0.45,
        ("FXRP",  "HLN"):    0.40,
        ("FXRP",  "BTC"):    0.52,
        ("FXRP",  "ETH"):    0.47,
        ("FXRP",  "XLM"):    0.55,
        ("FXRP",  "XDC"):    0.48,
        ("FXRP",  "HBAR"):   0.42,
        ("FXRP",  "SHX"):    0.30,
        ("FXRP",  "ZBCN"):   0.22,
        ("FXRP",  "CC"):     0.25,
        ("XRP",   "stXRP"):  0.98,
        ("XRP",   "XLM"):    0.58,
        ("XRP",   "XDC"):    0.50,
        ("XRP",   "HBAR"):   0.45,
        ("XRP",   "SHX"):    0.32,
        ("XRP",   "ZBCN"):   0.24,
        ("XRP",   "CC"):     0.25,
        ("XRP",   "BTC"):    0.55,
        ("XRP",   "ETH"):    0.50,
        ("XRP",   "SOL"):    0.42,
        ("XRP",   "BNB"):    0.48,
        # ── Must-have coin cross-correlations ────────────────────────────────
        ("XLM",   "XDC"):    0.52,
        ("XLM",   "HBAR"):   0.48,
        ("XLM",   "SHX"):    0.38,
        ("XLM",   "ZBCN"):   0.25,
        ("XLM",   "CC"):     0.22,
        ("XLM",   "BTC"):    0.50,
        ("XLM",   "ETH"):    0.45,
        ("XLM",   "SOL"):    0.38,
        ("XDC",   "HBAR"):   0.45,
        ("XDC",   "SHX"):    0.32,
        ("XDC",   "ZBCN"):   0.22,
        ("XDC",   "CC"):     0.20,
        ("XDC",   "BTC"):    0.42,
        ("XDC",   "ETH"):    0.38,
        ("HBAR",  "SHX"):    0.30,
        ("HBAR",  "ZBCN"):   0.20,
        ("HBAR",  "CC"):     0.22,
        ("HBAR",  "BTC"):    0.48,
        ("HBAR",  "ETH"):    0.44,
        ("HBAR",  "SOL"):    0.40,
        ("SHX",   "ZBCN"):   0.25,
        ("SHX",   "CC"):     0.18,
        ("SHX",   "BTC"):    0.28,
        ("SHX",   "ETH"):    0.24,
        ("ZBCN",  "CC"):     0.15,
        ("ZBCN",  "BTC"):    0.20,
        ("ZBCN",  "ETH"):    0.18,
        ("CC",    "BTC"):    0.22,
        ("CC",    "ETH"):    0.20,
        # ── Major crypto cross-correlations ─────────────────────────────────
        ("BTC",   "ETH"):    0.82,
        ("BTC",   "SOL"):    0.72,
        ("BTC",   "BNB"):    0.68,
        ("BTC",   "AVAX"):   0.65,
        ("BTC",   "MATIC"):  0.62,
        ("BTC",   "ADA"):    0.64,
        ("BTC",   "DOT"):    0.62,
        ("BTC",   "LINK"):   0.60,
        ("BTC",   "ATOM"):   0.58,
        ("BTC",   "LTC"):    0.68,
        ("BTC",   "wBTC"):   0.99,
        ("BTC",   "cbBTC"):  0.99,
        ("ETH",   "SOL"):    0.76,
        ("ETH",   "BNB"):    0.70,
        ("ETH",   "AVAX"):   0.68,
        ("ETH",   "MATIC"):  0.65,
        ("ETH",   "ADA"):    0.62,
        ("ETH",   "DOT"):    0.64,
        ("ETH",   "LINK"):   0.65,
        ("ETH",   "ATOM"):   0.60,
        ("ETH",   "wETH"):   0.99,
        ("ETH",   "stETH"):  0.99,
        ("ETH",   "cbETH"):  0.98,
        ("SOL",   "BNB"):    0.68,
        ("SOL",   "AVAX"):   0.70,
        ("SOL",   "MATIC"):  0.64,
        ("wETH",  "wBTC"):   0.80,
        ("wETH",  "HLN"):    0.50,
        # ── Stablecoins — uncorrelated with everything ───────────────────────
        ("USD0",  "USDT0"):  0.99,
        ("USD0",  "USDC.e"): 0.99,
        ("USD0",  "USDT"):   0.99,
        ("USD0",  "USDC"):   0.99,
        ("USD0",  "DAI"):    0.99,
        ("USDT0", "USDC.e"): 0.99,
        ("USDT0", "USDT"):   0.99,
        ("USDT0", "USDC"):   0.99,
        ("USDT",  "USDC"):   0.99,
        ("USDT",  "DAI"):    0.99,
        ("USDC",  "DAI"):    0.99,
    }


    _STABLES = frozenset({"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "FRAX", "LUSD",
                          "USD0", "USDT0", "USDC.E", "GUSD", "PYUSD", "USDP", "CRVUSD"})

    def _get_corr_l(a: str, b: str, _live: dict) -> float:
        """Correlation between tokens a and b. Live data first, then fallback table."""
        a, b = a.upper(), b.upper()
        if a == b:
            return 1.0
        if a in _STABLES and b in _STABLES:
            return 0.99
        if a in _STABLES or b in _STABLES:
            return 0.04
        v = _live.get((a, b)) if _live else None
        if v is not None:
            return max(-1.0, min(1.0, float(v)))
        return _TOKEN_CORR.get((a, b), _TOKEN_CORR.get((b, a), 0.30))

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
            pool = (pos.get("pool") or "").replace("-", "/").replace("_", "/")
            for part in pool.split("/"):
                t = part.strip().upper()
                if t and t not in tokens:
                    tokens.append(t)
        return tokens[:2]

    if not positions or len(positions) < 2:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>"
            "Add at least 2 positions to see the correlation matrix.</div>",
            unsafe_allow_html=True,
        )
    else:
        # ── Fetch live correlations (yfinance, 4h cache) ──────────────────────
        _all_toks: set = set()
        for _p in positions:
            _all_toks.update(_position_tokens(_p))
        _tok_key = ",".join(sorted(_all_toks))
        _live_corr = _fetch_live_token_corr(_tok_key)
        _has_live  = bool(_live_corr)

        # ── Build position labels and pairwise correlation matrix ─────────────
        pos_labels = [
            f"{pos.get('pool', '?')} ({str(pos.get('protocol') or '?').capitalize()})"
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
                    corr_matrix[i][j] = 0.30
                    continue
                pairs = [(a, b) for a in toks_i for b in toks_j]
                corr_matrix[i][j] = sum(_get_corr_l(a, b, _live_corr) for a, b in pairs) / len(pairs)

        # ── Size-weighted diversification score ───────────────────────────────
        # Weight each pair's correlation by deposit-USD so a $100 stablecoin doesn't
        # dominate a $10,000 LP. Falls back to equal weight when deposit_usd is missing.
        _total_dep = sum(float(p.get("deposit_usd") or 0) for p in positions) or float(n)
        _pos_weights = [
            max(1.0 / n, float(p.get("deposit_usd") or _total_dep / n) / _total_dep)
            for p in positions
        ]
        _w_corr_sum = 0.0
        _w_total    = 0.0
        for _wi in range(n):
            for _wj in range(n):
                if _wi != _wj:
                    _w = _pos_weights[_wi] * _pos_weights[_wj]
                    _w_corr_sum += corr_matrix[_wi][_wj] * _w
                    _w_total    += _w
        _avg_corr  = _w_corr_sum / _w_total if _w_total > 0 else 0.0
        _div_score = round((1.0 - _avg_corr) * 100, 1)

        if _div_score >= 70:
            _div_color = "#22c55e"; _div_label = "Well-Diversified"
        elif _div_score >= 45:
            _div_color = "#f59e0b"; _div_label = "Moderately Diversified"
        else:
            _div_color = "#ef4444"; _div_label = "Concentrated"

        _corr_lv = get_user_level()

        # ── Score row: metric + data-source badge ─────────────────────────────
        _src_badge = (
            "<span style='background:rgba(34,197,94,0.10);border:1px solid rgba(34,197,94,0.30);"
            "border-radius:4px;padding:1px 7px;font-size:0.68rem;color:#22c55e;font-weight:600;'>"
            "● Live</span>"
            if _has_live else
            "<span style='background:rgba(100,116,139,0.10);border:1px solid rgba(100,116,139,0.30);"
            "border-radius:4px;padding:1px 7px;font-size:0.68rem;color:#64748b;font-weight:600;'>"
            "○ Estimated</span>"
        )
        _sc1, _sc2, _ = st.columns([2, 2, 4])
        with _sc1:
            st.metric(
                "Diversification Score",
                f"{_div_score}/100" if _corr_lv != "beginner" else _div_label,
                help=(
                    "Size-weighted: large positions count more than small ones. "
                    "100 = completely uncorrelated (maximum diversification). "
                    "0 = all positions move identically (no diversification). "
                    f"Weighted avg cross-position correlation: {_avg_corr:.2f}"
                ),
            )
        with _sc2:
            st.markdown(f"<div style='margin-top:28px;'>{_src_badge}</div>", unsafe_allow_html=True)

        if _corr_lv == "beginner":
            from ui.common import render_gauge as _rg
            _rg(_div_score, "Diversification", min_v=0, max_v=100,
                low_threshold=0.45, high_threshold=0.70,
                user_level="beginner", unit="/100")

        # ── Heatmap ───────────────────────────────────────────────────────────
        def _corr_label(v: float) -> str:
            if _corr_lv == "beginner":
                if v >= 0.80: return "High"
                if v >= 0.50: return "Med"
                return "Low"
            return f"{v:.2f}"

        fig_corr = go.Figure(data=go.Heatmap(
            z=corr_matrix,
            x=pos_labels,
            y=pos_labels,
            colorscale=[
                [0.0, "rgba(16,185,129,0.15)"],
                [0.3, "rgba(59,130,246,0.25)"],
                [0.7, "rgba(245,158,11,0.40)"],
                [1.0, "rgba(239,68,68,0.65)"],
            ],
            zmin=0, zmax=1,
            text=[[_corr_label(v) for v in row] for row in corr_matrix],
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
        st.plotly_chart(fig_corr, width='stretch')

        # ── Tiered risk warnings ──────────────────────────────────────────────
        high_corr_pairs = [
            (pos_labels[i], pos_labels[j], corr_matrix[i][j])
            for i in range(n) for j in range(i + 1, n)
            if corr_matrix[i][j] >= 0.80
        ]
        caution_pairs = [
            (pos_labels[i], pos_labels[j], corr_matrix[i][j])
            for i in range(n) for j in range(i + 1, n)
            if 0.65 <= corr_matrix[i][j] < 0.80
        ]

        if high_corr_pairs:
            warn_lines = "".join(
                f"<li>{_html.escape(a)} ↔ {_html.escape(b)} "
                f"(<span style='color:#ef4444; font-weight:600;'>{c:.0%}</span>)</li>"
                for a, b, c in high_corr_pairs
            )
            st.markdown(
                f"<div class='warn-box'>"
                f"<div style='font-weight:700; color:#ef4444; margin-bottom:6px;'>🔴 High Concentration Risk</div>"
                f"<div style='color:#94a3b8; font-size:0.83rem; line-height:1.55;'>"
                f"These positions move almost identically — a single market crash could hit all simultaneously:"
                f"<ul style='margin:6px 0 0 0;'>{warn_lines}</ul>"
                f"<div style='margin-top:8px; color:#64748b;'>Action: reduce one position or add an uncorrelated asset (stablecoin or non-correlated chain).</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        elif caution_pairs:
            caution_lines = "".join(
                f"<li>{_html.escape(a)} ↔ {_html.escape(b)} "
                f"(<span style='color:#f59e0b; font-weight:600;'>{c:.0%}</span>)</li>"
                for a, b, c in caution_pairs
            )
            st.markdown(
                f"<div class='warn-box'>"
                f"<div style='font-weight:700; color:#f59e0b; margin-bottom:6px;'>🟡 Moderate Concentration — Watch These Pairs</div>"
                f"<div style='color:#94a3b8; font-size:0.83rem; line-height:1.55;'>"
                f"These positions are moderately correlated — they tend to move in the same direction:"
                f"<ul style='margin:6px 0 0 0;'>{caution_lines}</ul>"
                f"<div style='margin-top:8px; color:#64748b;'>Not urgent, but worth monitoring during broad market downturns.</div>"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.markdown(
                "<div style='color:#10b981; font-size:0.85rem; padding:4px 0;'>"
                "✓ Portfolio is well-diversified — no highly correlated position pairs detected."
                "</div>",
                unsafe_allow_html=True,
            )

        # ── Protocol Concentration Risk ───────────────────────────────────────
        _proto_dep: dict = {}
        for _pp in positions:
            _pr = str(_pp.get("protocol") or "Unknown").capitalize()
            _proto_dep[_pr] = _proto_dep.get(_pr, 0.0) + float(_pp.get("deposit_usd") or 0)
        _total_dep_proto = sum(_proto_dep.values()) or 1.0
        _proto_rows = sorted(_proto_dep.items(), key=lambda x: x[1], reverse=True)
        _max_proto_name, _max_proto_dep = _proto_rows[0] if _proto_rows else ("", 0)
        _max_proto_pct = _max_proto_dep / _total_dep_proto * 100

        if len(_proto_dep) >= 1:
            with st.expander("🏛 Protocol Concentration Risk", expanded=(_max_proto_pct > 50)):
                st.caption(
                    "Token correlation ≠ smart contract risk. Even uncorrelated tokens in the same "
                    "protocol share the same exploit surface — a single hack could drain all of them."
                )
                for _pr_name, _pr_dep in _proto_rows:
                    _pr_pct = _pr_dep / _total_dep_proto * 100
                    _pr_col = "#ef4444" if _pr_pct > 50 else ("#f59e0b" if _pr_pct > 30 else "#22c55e")
                    st.markdown(
                        f"<div style='display:flex;justify-content:space-between;align-items:center;"
                        f"padding:5px 0;border-bottom:1px solid rgba(148,163,184,0.07);font-size:0.82rem;'>"
                        f"<span style='color:#e2e8f0;font-weight:600;'>{_html.escape(_pr_name)}</span>"
                        f"<div style='display:flex;align-items:center;gap:10px;'>"
                        f"<div style='background:rgba(255,255,255,0.05);border-radius:3px;width:80px;height:5px;'>"
                        f"<div style='width:{min(100, _pr_pct):.0f}%;height:5px;background:{_pr_col};"
                        f"border-radius:3px;'></div></div>"
                        f"<span style='color:{_pr_col};font-weight:700;min-width:44px;text-align:right;'>"
                        f"{_pr_pct:.0f}%</span>"
                        f"<span style='color:#64748b;'>${_pr_dep:,.0f}</span>"
                        f"</div></div>",
                        unsafe_allow_html=True,
                    )
                if _max_proto_pct > 50:
                    st.warning(
                        f"⚠️ **{_max_proto_pct:.0f}%** of your portfolio is in **{_max_proto_name}**. "
                        f"A single smart contract exploit here could impact "
                        f"**${_max_proto_dep:,.0f}** of your deposits."
                    )

        # ── Bear Market Stress Test — BTC −40% ────────────────────────────────
        if _total_dep > 10:
            _btc_corr_per_pos = []
            for _bp in positions:
                _bt = _position_tokens(_bp)
                if not _bt:
                    _btc_corr_per_pos.append(0.30)
                    continue
                _btc_corr_per_pos.append(
                    sum(_get_corr_l(t, "BTC", _live_corr) for t in _bt) / len(_bt)
                )
            _drop_factor = 0.40
            _est_loss = sum(
                max(0.0, _btc_corr_per_pos[i]) * _drop_factor * float(positions[i].get("deposit_usd") or 0)
                for i in range(n)
            )
            _port_val = sum(float(p.get("current_value") or p.get("deposit_usd") or 0) for p in positions)
            _stress_val = max(0.0, _port_val - _est_loss)
            _loss_pct   = _est_loss / _port_val * 100 if _port_val > 0 else 0.0
            _stress_col = "#ef4444" if _loss_pct > 25 else ("#f59e0b" if _loss_pct > 10 else "#22c55e")
            _stress_lbl = (
                "High impact — consider rebalancing" if _loss_pct > 25 else
                "Moderate impact" if _loss_pct > 10 else
                "Low impact — well hedged"
            )
            with st.expander("🧪 Bear Market Stress Test — BTC −40%", expanded=False):
                st.caption(
                    "Estimates portfolio loss if Bitcoin drops 40% (2022-bear-market scale). "
                    "Formula: Σ (BTC_correlation × 40% × deposit_USD). Linear approximation — not a guarantee."
                )
                _bt1, _bt2, _bt3 = st.columns(3)
                with _bt1:
                    st.metric("Estimated Loss", f"−${_est_loss:,.0f}",
                              delta=f"−{_loss_pct:.1f}%", delta_color="inverse")
                with _bt2:
                    st.metric("Portfolio After", f"${_stress_val:,.0f}")
                with _bt3:
                    st.metric("Stress Level", _stress_lbl)
                st.markdown(
                    f"<div style='font-size:0.72rem;color:{_stress_col};"
                    f"margin-top:4px;font-weight:600;'>"
                    f"{'▼ ' if _loss_pct > 10 else '■ '}"
                    f"Estimated {_loss_pct:.1f}% portfolio loss in a BTC −40% scenario</div>",
                    unsafe_allow_html=True,
                )

        # ── Actionable Rebalancing Recommendation ─────────────────────────────
        if _div_score < 70 and _total_dep > 0:
            _add_usd   = round(_total_dep * 0.20)
            _target    = min(100, _div_score + 18)
            st.markdown(
                f"<div style='background:rgba(139,92,246,0.05);border:1px solid rgba(139,92,246,0.15);"
                f"border-radius:10px;padding:12px 16px;font-size:0.83rem;color:#94a3b8;margin-top:10px;'>"
                f"💡 <span style='color:#a78bfa;font-weight:700;'>Rebalancing Tip</span> — "
                f"Adding ~${_add_usd:,.0f} to a stablecoin (USDC, USD0) or a low-correlation asset "
                f"(XDC, HBAR, SHX) could raise your diversification score from "
                f"<span style='color:{_div_color};font-weight:700;'>{_div_score:.0f}</span> toward "
                f"<span style='color:#22c55e;font-weight:700;'>{_target:.0f}+</span>."
                f"</div>",
                unsafe_allow_html=True,
            )

        # ── Level-aware explanation ───────────────────────────────────────────
        render_what_this_means(
            message=(
                f"Your diversification score is **{_div_score}/100** ({_div_label}). "
                + (
                    "A score above 70 is great — it means your positions don't all crash at the same time. "
                    "If one goes down, the others are less likely to follow. "
                    "Try to include a mix of: crypto (XRP, ETH), ecosystem tokens (FLR), and stablecoins."
                    if _div_score >= 70 else
                    "A score between 45 and 70 means your positions are somewhat similar. "
                    "Adding a stablecoin position or an uncorrelated token can improve this."
                    if _div_score >= 45 else
                    "A score below 45 means your positions are very similar and tend to move together. "
                    "This is called concentration risk — if the market drops, all your positions could drop at once. "
                    "Consider adding a stablecoin (like USDT or USDC) to reduce this risk."
                )
            ),
            intermediate_message=(
                f"Diversification score: {_div_score}/100 (size-weighted) · avg cross-corr {_avg_corr:.2f} · "
                + ("well diversified" if _div_score >= 70 else "moderate concentration" if _div_score >= 45 else "high concentration risk")
                + (" · Live data" if _has_live else " · estimated")
            ),
        )
        _data_note = (
            "Live 90-day Pearson correlations from yfinance (4h cache) where available"
            if _has_live else
            "Estimated correlations (yfinance unavailable — using historical Flare/crypto estimates)"
        )
        st.caption(f"Data: {_data_note}. Actual correlations vary with market conditions.")

    # ─── Portfolio Rebalancing Advisor (Phase 8) ──────────────────────────────────

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    render_section_header("Rebalancing Advisor", "Compare current allocation vs model recommendation — find drift")

    if positions and total_value > 0:
        # ── Step 1: Current allocation by protocol type ──────────────────────────
        _type_map = {k: v.get("type", "Other") for k, v in PROTOCOLS.items()}
        _current_alloc: dict = {}
        for pnl_idx, pos in enumerate(positions):
            proto_key  = pos.get("protocol", "")
            proto_type = _type_map.get(proto_key, "Other")
            # Simplify types for comparison
            if "DEX" in proto_type or "Perp" in proto_type:
                bucket = "DEX / LP"
            elif "Lending" in proto_type or "CDP" in proto_type:
                bucket = "Lending"
            elif "Staking" in proto_type or "Liquid" in proto_type:
                bucket = "Staking"
            elif "Yield" in proto_type:
                bucket = "Yield Vault"
            else:
                bucket = "Other"
            _current_alloc[bucket] = _current_alloc.get(bucket, 0) + pnl_results[pnl_idx]["current_value"]

        _current_pct = {k: round(v / total_value * 100, 1) for k, v in _current_alloc.items()}

        # ── Step 2: Target allocation from model recommendations ─────────────────
        _prof_key    = ctx.get("profile", "medium")
        _model_opps  = (latest.get("models") or {}).get(_prof_key) or []
        _target_alloc: dict = {}
        _total_kf = sum(o.get("kelly_fraction", 0) for o in _model_opps) or 1.0
        for opp in _model_opps:
            proto_key  = ""
            proto_name = opp.get("protocol", "")
            # Reverse-lookup protocol key from name
            for k, v in PROTOCOLS.items():
                if v.get("name") == proto_name:
                    proto_key = k
                    break
            proto_type = _type_map.get(proto_key, "Other")
            if "DEX" in proto_type or "Perp" in proto_type:
                bucket = "DEX / LP"
            elif "Lending" in proto_type or "CDP" in proto_type:
                bucket = "Lending"
            elif "Staking" in proto_type or "Liquid" in proto_type:
                bucket = "Staking"
            elif "Yield" in proto_type:
                bucket = "Yield Vault"
            else:
                bucket = "Other"
            kf = opp.get("kelly_fraction", 0) / _total_kf * 100
            _target_alloc[bucket] = _target_alloc.get(bucket, 0) + kf

        _target_pct = {k: round(v, 1) for k, v in _target_alloc.items()}

        if _target_pct:
            # ── Step 3: Show drift table ─────────────────────────────────────────
            all_buckets = sorted(set(list(_current_pct.keys()) + list(_target_pct.keys())))
            rebal_rows  = []
            actions     = []
            for bucket in all_buckets:
                cur = _current_pct.get(bucket, 0.0)
                tgt = _target_pct.get(bucket, 0.0)
                drift = cur - tgt
                if drift > 8:
                    action = "Reduce"
                    arrow  = "↓ Overweight"
                    dollar_adj = -(drift / 100) * total_value
                elif drift < -8:
                    action = "Increase"
                    arrow  = "↑ Underweight"
                    dollar_adj = abs(drift / 100) * total_value
                else:
                    action = "Hold"
                    arrow  = "✓ On target"
                    dollar_adj = 0.0
                if action != "Hold":
                    actions.append({"bucket": bucket, "action": action, "drift": drift, "dollar_adj": dollar_adj})
                rebal_rows.append({
                    "Strategy Type": bucket,
                    "Current %":     f"{cur:.1f}%",
                    "Model Target %": f"{tgt:.1f}%",
                    "Drift":         f"{drift:+.1f}%",
                    "Signal":        arrow,
                    "$ Adjustment":  f"${abs(dollar_adj):,.0f}" if dollar_adj != 0 else "—",
                })

            st.dataframe(pd.DataFrame(rebal_rows), width='stretch', hide_index=True)

            # ── Step 4: Actionable suggestions ───────────────────────────────────
            if actions:
                st.markdown(
                    "<div style='font-weight:600; color:#a78bfa; font-size:0.88rem; margin:12px 0 8px;'>"
                    "Rebalancing Actions</div>",
                    unsafe_allow_html=True,
                )
                for act in sorted(actions, key=lambda x: abs(x["drift"]), reverse=True):
                    _act_color = "#ef4444" if act["action"] == "Reduce" else "#10b981"
                    _act_icon  = "▼" if act["action"] == "Reduce" else "▲"
                    _msg = (
                        f"Withdraw ${abs(act['dollar_adj']):,.0f} from {act['bucket']} positions "
                        f"(currently {_current_pct.get(act['bucket'], 0):.0f}% vs {_target_pct.get(act['bucket'], 0):.0f}% target)"
                        if act["action"] == "Reduce"
                        else f"Add ${abs(act['dollar_adj']):,.0f} to {act['bucket']} opportunities "
                        f"({_current_pct.get(act['bucket'], 0):.0f}% current vs {_target_pct.get(act['bucket'], 0):.0f}% target)"
                    )
                    st.markdown(
                        f"<div style='background:rgba(15,23,42,0.5); border:1px solid rgba(148,163,184,0.1); "
                        f"border-left:3px solid {_act_color}; border-radius:8px; padding:10px 14px; "
                        f"margin-bottom:6px; font-size:0.87rem; color:#94a3b8;'>"
                        f"<span style='color:{_act_color}; font-weight:700;'>{_act_icon} {act['action']} {act['bucket']}</span>"
                        f"<span style='color:#475569; margin:0 6px;'>·</span>{_msg}</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<div style='color:#10b981; font-size:0.85rem; padding:6px 0;'>"
                    "✓ Portfolio is well-aligned with model recommendations — no rebalancing needed.</div>",
                    unsafe_allow_html=True,
                )
            st.caption(f"Target based on Kelly-sized {RISK_PROFILES[_prof_key]['label']} model picks. Drift >8% triggers an action. Not financial advice.")
        else:
            st.info("Run a scan first to generate model recommendations for comparison.")
    else:
        st.markdown(
            "<div style='color:#334155; font-size:0.85rem;'>"
            "Add tracked positions to see rebalancing suggestions.</div>",
            unsafe_allow_html=True,
        )


with _tab_wallet:
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



with _tab_pos:
    # ─── D5 — Daily Income Tracker ───────────────────────────────────────────────
    # Shows estimated daily / weekly / monthly income for every tracked position

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    render_section_header(
        "Daily Income Tracker",
        "How much income your DeFi positions generate every day, week, and month",
    )

    if positions:
        from ui.common import render_what_this_means as _d5_rwm
        _d5_rwm(
            "This shows how much money your DeFi positions generate each day based on their "
            "current APY and deposited value. It's your 'DeFi salary'. "
            "Daily income = (Deposit × APY%) ÷ 365. "
            "Actual income varies with APY — this is an estimate.",
            title="What is daily income?",
        )

        _d5_rows  = []
        _d5_total_daily  = 0.0
        _d5_total_weekly = 0.0
        _d5_total_monthly = 0.0
        for i, pos in enumerate(positions):
            _pnl    = pnl_results[i]
            _dep    = float(pos.get("deposit_usd") or 0)
            _cur    = float(_pnl.get("current_value") or _dep)
            _apy    = float(pos.get("entry_apy") or 0)    # stored as % (e.g. 12.5)
            # Estimate using current value for accuracy
            _base   = _cur if _cur > 0 else _dep
            _daily  = _base * (_apy / 100) / 365
            _weekly = _daily * 7
            _monthly = _daily * 30
            _d5_total_daily  += _daily
            _d5_total_weekly += _weekly
            _d5_total_monthly += _monthly
            _d5_rows.append({
                "Position":   f"{pos.get('pool','?')} ({str(pos.get('protocol') or '?').capitalize()})",
                "Type":       pos.get("position_type", "lp").upper(),
                "APY %":      f"{_apy:.1f}%",
                "Value":      f"${_cur:,.0f}",
                "Daily ($)":  f"${_daily:.2f}",
                "Weekly ($)": f"${_weekly:.2f}",
                "Monthly ($)":f"${_monthly:,.2f}",
            })

        if _d5_rows:
            st.dataframe(pd.DataFrame(_d5_rows), width='stretch', hide_index=True)

            # Summary metrics
            _dc1, _dc2, _dc3, _dc4 = st.columns(4)
            _annual = _d5_total_daily * 365
            with _dc1:
                st.metric("Daily Income",   f"${_d5_total_daily:.2f}",
                          help="Estimated income generated per day across all positions")
            with _dc2:
                st.metric("Weekly Income",  f"${_d5_total_weekly:.2f}")
            with _dc3:
                st.metric("Monthly Income", f"${_d5_total_monthly:,.2f}")
            with _dc4:
                st.metric("Annual Run Rate", f"${_annual:,.0f}")

            # Bar chart — income per position
            if len(_d5_rows) > 1:
                _d5_fig = go.Figure()
                _d5_fig.add_trace(go.Bar(
                    x=[r["Position"] for r in _d5_rows],
                    y=[float(r["Monthly ($)"].replace("$", "").replace(",", "")) for r in _d5_rows],
                    marker_color="#00d4aa",
                    text=[r["Monthly ($)"] for r in _d5_rows],
                    textposition="outside",
                    hovertemplate="<b>%{x}</b><br>Monthly: $%{y:,.2f}<extra></extra>",
                ))
                _d5_fig.update_layout(
                    paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                    font_color="#475569",
                    xaxis=dict(tickangle=-30, gridcolor="rgba(148,163,184,0.1)"),
                    yaxis=dict(title="Monthly Income ($)", gridcolor="rgba(148,163,184,0.1)",
                               tickprefix="$"),
                    height=240, margin=dict(l=40, r=20, t=20, b=80),
                    showlegend=False,
                )
                st.plotly_chart(_d5_fig, width='stretch', config={"displayModeBar": False})

            st.caption(
                "Income estimated from entry APY × current position value. "
                "Actual earnings depend on live APY changes and compounding. "
                "Not financial advice."
            )
    else:
        st.info("Add positions to see daily income estimates.")


    # ─── Portfolio Summary Export (Batch 9) ───────────────────────────────────────

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    render_section_header("Portfolio Export", "Download a CSV or text summary of your current holdings")

    _export_holdings = []
    if positions:
        for _pos in positions:
            _pos_apy = float(_pos.get("entry_apy") or 0) / 100.0
            _export_holdings.append({
                "protocol":   _pos.get("protocol", ""),
                "asset":      _pos.get("pool", _pos.get("token_a", "")),
                "amount_usd": float(_pos.get("current_value") or _pos.get("deposit_usd") or 0),
                "apy":        _pos_apy,
                "entry_date": _pos.get("entry_date", ""),
                "notes":      _pos.get("notes", ""),
            })

    _exp_total = sum(h["amount_usd"] for h in _export_holdings)

    _col_csv, _col_txt = st.columns(2)
    with _col_csv:
        st.download_button(
            "📥 Export Portfolio CSV",
            data=_build_portfolio_csv(_export_holdings),
            file_name=f"portfolio_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv",
            mime="text/csv",
            use_container_width=True,
            key="batch9_csv_export",
        )
    with _col_txt:
        st.download_button(
            "📄 Export Portfolio Report",
            data=_build_portfolio_report(_export_holdings, _exp_total),
            file_name=f"portfolio_report_{datetime.now(timezone.utc).strftime('%Y%m%d')}.txt",
            mime="text/plain",
            use_container_width=True,
            key="batch9_txt_export",
        )
    if not _export_holdings:
        st.caption("Add tracked positions to enable portfolio export.")

# ─── FAssets Data Loader (used by FAssets tab) ───────────────────────────────

@st.cache_data(ttl=60)
def _load_fasset_data() -> dict:
    """Load FAsset data from most recent scan or direct API."""
    cached = load_latest().get("fasset", {})
    if cached and isinstance(cached.get("assets"), dict) and cached["assets"]:
        return cached
    try:
        from scanners.flare_scanner import fetch_fasset_data
        return fetch_fasset_data()
    except Exception:
        return {}


with _tab_fassets:
    _fa = _load_fasset_data()
    if not _fa:
        st.info(
            "FAsset data unavailable. Run a scan first — click ▶ Scan in the sidebar.",
            icon="ℹ️",
        )
    else:
        _fa_src   = _fa.get("data_source", "baseline")
        _fa_badge = (
            "<span class='badge-live'>LIVE</span>" if _fa_src == "live"
            else "<span class='badge-est'>ESTIMATED</span>"
        )
        _fa_ts = _fa.get("fetched_at", "")

        if _fa_src == "baseline":
            st.markdown(
                "<div class='warn-box' style='font-size:0.86rem; line-height:1.55;'>"
                "⚠️ Live FAsset API unreachable — showing research-based estimates. "
                "Fees and collateral ratios are accurate; circulating supply is approximate. "
                "Click <b>▶ Scan</b> in the sidebar to retry.</div>",
                unsafe_allow_html=True,
            )

        st.markdown(
            f"<div style='font-size:0.75rem; color:#475569; margin-bottom:16px;'>"
            f"Data: {_fa_badge}&nbsp;·&nbsp;{_ts_fmt(_fa_ts) if _fa_ts else '—'}</div>",
            unsafe_allow_html=True,
        )

        # ── System Health Banner ───────────────────────────────────────────────
        _fa_health  = str(_fa.get("system_health") or "unknown")
        _fa_agents  = _fa.get("agent_count", 0)
        _h_color    = {"healthy": "#10b981", "caution": "#f59e0b", "unknown": "#475569"}.get(_fa_health, "#475569")
        _h_icon     = {"healthy": "✓", "caution": "⚠", "unknown": "?"}.get(_fa_health, "?")
        _fa_assets  = _fa.get("assets", {})
        _fa_prices  = load_latest().get("flare_scan", {}).get("prices", [])
        _fa_lkp     = {p["symbol"]: p.get("price_usd", 0) for p in _fa_prices if isinstance(p, dict) and p.get("symbol")}

        _fxrp_info  = _fa_assets.get("FXRP", {})
        _fxrp_circ  = float(_fxrp_info.get("circulating", 0) or 0)
        _fxrp_price = _fa_lkp.get("FXRP", _fa_lkp.get("XRP", 1.53))
        _fxrp_tvl   = _fxrp_circ * _fxrp_price
        _fxrp_max   = _fxrp_circ * 2.5
        _mint_rem   = max(0.0, _fxrp_max - _fxrp_circ)
        _mint_pct   = (_fxrp_circ / _fxrp_max * 100) if _fxrp_max > 0 else 0.0

        if _fa_agents and _fa_agents > 0:
            if _fa_health == "healthy":
                _ag_ok   = max(1, round(_fa_agents * 0.90))
                _ag_warn = _fa_agents - _ag_ok
                _ag_liq  = 0
            elif _fa_health == "caution":
                _ag_liq  = max(0, round(_fa_agents * 0.05))
                _ag_warn = max(1, round(_fa_agents * 0.20))
                _ag_ok   = _fa_agents - _ag_warn - _ag_liq
            else:
                _ag_ok = _fa_agents; _ag_warn = 0; _ag_liq = 0
        else:
            _ag_ok = _ag_warn = _ag_liq = 0

        _fc1, _fc2, _fc3, _fc4 = st.columns(4)
        with _fc1:
            st.markdown(f"""
            <div class="metric-card card-green">
                <div class="label">System Health</div>
                <div class="big-number" style="color:{_h_color};">{_h_icon}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:4px;">{_fa_health.capitalize()}</div>
            </div>""", unsafe_allow_html=True)
        with _fc2:
            _fw_html = f"&nbsp;<span style='color:#f59e0b;'>⚠ {_ag_warn}</span>" if _ag_warn else ""
            _fl_html = f"&nbsp;<span style='color:#ef4444;'>✗ {_ag_liq}</span>" if _ag_liq else ""
            # Agent health distribution is estimated from total count + system health status.
            # Live API does not expose per-agent status — numbers are approximate.
            _ag_est_note = " (est.)" if _fa_agents else ""
            st.markdown(
                f"<div class='metric-card card-blue'>"
                f"<div class='label'>Active Agents{_ag_est_note}</div>"
                f"<div class='big-number'>{_fa_agents if _fa_agents else '—'}</div>"
                f"<div style='color:#475569; font-size:0.82rem; margin-top:4px;'>"
                f"<span style='color:#10b981;'>✓ {_ag_ok}</span>{_fw_html}{_fl_html}"
                f"</div></div>",
                unsafe_allow_html=True,
            )
        with _fc3:
            st.markdown(f"""
            <div class="metric-card card-orange">
                <div class="label">FXRP Circulating</div>
                <div class="big-number" style="color:#f59e0b;">{_fxrp_circ:,.0f}</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:4px;">≈ ${_fxrp_tvl:,.0f} USD</div>
            </div>""", unsafe_allow_html=True)
        with _fc4:
            _cap_color = "#10b981" if _mint_pct < 60 else ("#f59e0b" if _mint_pct < 85 else "#ef4444")
            st.markdown(f"""
            <div class="metric-card card-violet">
                <div class="label">Mint Capacity Used</div>
                <div class="big-number" style="color:{_cap_color};">{_mint_pct:.0f}%</div>
                <div style="color:#475569; font-size:0.82rem; margin-top:4px;">
                    ~{_mint_rem:,.0f} FXRP remaining
                </div>
            </div>""", unsafe_allow_html=True)

        render_what_this_means(
            "FAssets are tokens on the Flare blockchain backed 1:1 by real assets from other chains. "
            "FXRP is backed by real XRP. System Health shows if the backing system is running safely. "
            "Active Agents hold FLR collateral to guarantee FXRP is backed. "
            "Mint Capacity shows how much more FXRP can be created.",
            title="What are FAssets?",
            intermediate_message="FAssets = tokenised cross-chain assets (FXRP ≈ XRP on Flare). Agents hold FLR collateral. Mint capacity = remaining headroom.",
        )
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Collateral Ratio Trend ─────────────────────────────────────────────
        render_section_header("Collateral Ratio Trend", "FXRP backing ratio across recent scans")
        _fa_runs  = load_history_runs()
        _cr_dates = []
        _cr_vals  = []
        for _fa_run in _fa_runs[-20:]:
            _fa_ts2 = (_fa_run.get("completed_at") or _fa_run.get("run_id", ""))[:19]
            _fa_r   = _fa_run.get("fasset", {})
            _cr     = (_fa_r.get("assets") or {}).get("FXRP", {}).get("cr_pct")
            if _cr and isinstance(_cr, (int, float)):
                try:
                    _cr_dates.append(_fa_ts2.replace("T", " "))
                    _cr_vals.append(float(_cr))
                except Exception:
                    pass
        if len(_cr_vals) >= 2:
            _cr_color = "#10b981" if (sum(_cr_vals[-3:]) / len(_cr_vals[-3:])) >= 200 else "#f59e0b"
            _fig_cr = go.Figure()
            _fig_cr.add_trace(go.Scatter(
                x=_cr_dates, y=_cr_vals,
                mode="lines+markers",
                name="FXRP Collateral Ratio",
                line=dict(color=_cr_color, width=2.5),
                marker=dict(size=6, color=_cr_color),
                fill="tozeroy", fillcolor="rgba(16,185,129,0.06)",
                hovertemplate="%{x}<br>CR: %{y:.0f}%<extra></extra>",
            ))
            _fig_cr.add_hline(y=200, line_dash="dash", line_color="rgba(16,185,129,0.5)",
                              annotation_text="200% healthy", annotation_position="bottom right",
                              annotation_font_size=11, annotation_font_color="#10b981")
            _fig_cr.add_hline(y=160, line_dash="dash", line_color="rgba(239,68,68,0.5)",
                              annotation_text="160% min (CCB)", annotation_position="bottom right",
                              annotation_font_size=11, annotation_font_color="#ef4444")
            _fig_cr.update_layout(
                height=240, margin=dict(l=0, r=0, t=12, b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                xaxis=dict(showgrid=False, color="#64748b", tickfont_size=11),
                yaxis=dict(gridcolor="rgba(255,255,255,0.05)", color="#64748b",
                           tickformat=".0f", ticksuffix="%", tickfont_size=11),
                showlegend=False,
            )
            st.plotly_chart(_fig_cr, width='stretch', config={"displayModeBar": False})
        else:
            st.caption("Collateral ratio history available after 2+ scans.")

        render_what_this_means(
            "The Collateral Ratio shows how well-backed FXRP is. 200% means for every $1 FXRP, $2 of FLR is held as collateral. "
            "Below 160%, the system triggers emergency measures to protect FXRP holders.",
            title="What does the Collateral Ratio mean?",
            intermediate_message="CR <160% triggers CCB liquidations. CR ≥200% = well-collateralised.",
        )
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── Per-Asset Cards ────────────────────────────────────────────────────
        render_section_header("FAsset Details", "Mint · redeem · collateral per bridged asset")
        _FA_COLOR = {"FXRP": "#3b82f6", "FBTC": "#f59e0b", "FDOGE": "#22c55e"}
        _FA_ICON  = {"FXRP": "XRP", "FBTC": "BTC", "FDOGE": "DOGE"}
        for _fa_sym, _fa_info in _fa_assets.items():
            if not isinstance(_fa_info, dict):
                continue
            _fa_col      = _FA_COLOR.get(_fa_sym, "#8b5cf6")
            _fa_icon     = _FA_ICON.get(_fa_sym, _fa_sym)
            _fa_mf       = _fa_info.get("mint_fee_pct", 0.25)
            _fa_rf       = _fa_info.get("redeem_fee_pct", 0.20)
            _fa_cr       = _fa_info.get("cr_pct", 160.0)
            _fa_ci       = _fa_info.get("circulating", 0)
            _fa_ct       = _fa_info.get("collateral_token", "FLR")
            _fa_note_str = _html.escape(_fa_info.get("note", ""))
            _fa_cr_col   = "#10b981" if _fa_cr >= 200 else ("#f59e0b" if _fa_cr >= 160 else "#ef4444")
            _fa_cr_lbl   = "Healthy" if _fa_cr >= 200 else ("Adequate" if _fa_cr >= 160 else "At Risk")
            _prem_html   = ""
            if _fa_sym == "FXRP":
                _sp_xrp  = _fa_lkp.get("XRP", 0)
                _sp_fxrp = _fa_lkp.get("FXRP", 0)
                if _sp_xrp > 0 and _sp_fxrp > 0:
                    _pp  = (_sp_fxrp - _sp_xrp) / _sp_xrp * 100
                    _pc  = "#ef4444" if _pp < -0.5 else ("#22c55e" if _pp > 0.5 else "#64748b")
                    _ps  = "+" if _pp >= 0 else ""
                    _prem_html = (
                        f"<span>Peg: <span style='color:{_pc}; font-weight:600;'>"
                        f"{_ps}{_pp:.2f}%</span> vs XRP</span>"
                    )
            _circ_html = (
                f'<div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; '
                f'letter-spacing:1.2px; margin-bottom:4px;">Circulating</div>'
                f'<div style="font-size:1.3rem; font-weight:700; color:#94a3b8;">{_fa_ci:,.0f}</div></div>'
            ) if _fa_ci > 0 else ""
            st.markdown(f"""
<div class="opp-card" style="border-left:3px solid {_fa_col};">
  <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:8px;">
    <div>
      <span style="font-size:1.1rem; font-weight:800; color:#f1f5f9;">{_fa_sym}</span>
      <span style="color:#475569; font-size:0.82rem; margin-left:8px;">Bridged {_fa_icon} on Flare</span>
    </div>
    <span style="color:{_fa_cr_col}; font-size:0.82rem; font-weight:700; background:rgba(255,255,255,0.04);
                 padding:3px 10px; border-radius:6px;">{_fa_cr_lbl}</span>
  </div>
  <div style="display:flex; gap:24px; flex-wrap:wrap; margin-top:14px; font-size:0.82rem; color:#475569;">
    <div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Mint Fee</div>
         <div style="font-size:1.3rem; font-weight:700; color:#f1f5f9;">{_fa_mf:.2f}%</div></div>
    <div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Redeem Fee</div>
         <div style="font-size:1.3rem; font-weight:700; color:#f1f5f9;">{_fa_rf:.2f}%</div></div>
    <div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Collateral Ratio</div>
         <div style="font-size:1.3rem; font-weight:700; color:{_fa_cr_col};">{_fa_cr:.0f}%</div></div>
    <div><div style="font-size:0.65rem; color:#334155; text-transform:uppercase; letter-spacing:1.2px; margin-bottom:4px;">Collateral Token</div>
         <div style="font-size:1.3rem; font-weight:700; color:#a78bfa;">{_fa_ct}</div></div>
    {_circ_html}
  </div>
  <div style="display:flex; gap:16px; flex-wrap:wrap; margin-top:12px; font-size:0.78rem; color:#475569;">{_prem_html}</div>
  {f'<div style="color:#475569; font-size:0.80rem; margin-top:10px; line-height:1.5;">{_fa_note_str}</div>' if _fa_note_str else ""}
</div>""", unsafe_allow_html=True)
        st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

        # ── How FAssets Work ───────────────────────────────────────────────────
        render_section_header("How FAssets Work", "The Flare bridge mechanism explained")
        with st.expander("FAsset mechanics — mint, hold, redeem"):
            st.markdown("""
**Minting FXRP:**
1. Request a mint from an agent on Flare — pay the mint fee (0.25%)
2. Send real XRP to the agent's XRP address
3. Receive FXRP on Flare within ~5 minutes (XRP confirmation time)
4. Use FXRP in DeFi — LP pools, lending, Spectra yield tokenization

**Redeeming FXRP:**
1. Send FXRP to the redemption contract — pay redemption fee (0.20%)
2. Agent sends real XRP to your XRP address within ~24 hours

**Collateral System:**
- Agents post FLR as collateral (minimum 160% of minted value)
- If FLR price drops and CR falls below 150%, agent is liquidated
- Vault CR > 200% = healthy buffer against FLR price volatility

**Arbitrage Opportunity:**
- FXRP at discount to XRP → buy FXRP, redeem for XRP (lock in spread minus fees)
- FXRP at premium → buy XRP, mint FXRP, sell on DEX
- Net profit threshold ≈ 0.5% (round-trip fees = 0.45%)
""")

        # ── FAsset Arbitrage Window ────────────────────────────────────────────
        render_section_header("Current Arb Window", "Real-time premium/discount vs XRP spot")
        try:
            from models.arbitrage import detect_fassets_arb
            from dataclasses import asdict as _fa_asdict
            _fa_scan  = load_latest().get("flare_scan") or {}
            _fa_arbs  = detect_fassets_arb(_fa_scan.get("prices", []))
            _fa_arbs2 = [_fa_asdict(a) if not isinstance(a, dict) else a for a in _fa_arbs]
            if _fa_arbs2:
                for _fa_arb in _fa_arbs2:
                    _fa_net = _fa_arb.get("estimated_profit", 0)
                    _fa_nc  = "#22c55e" if _fa_net > 0 else "#ef4444"
                    st.markdown(
                        f"<div class='arb-tag'>"
                        f"<span style='font-weight:700; color:#f1f5f9;'>{_fa_arb.get('strategy_label', 'FAssets Arb')}</span>"
                        f"<span style='color:#475569; margin-left:8px;'>{_fa_arb.get('urgency', '').upper()}</span>"
                        f"<div style='color:#94a3b8; font-size:0.82rem; margin-top:6px;'>"
                        f"Net profit: <span style='color:{_fa_nc}; font-weight:700;'>{_fa_net:.2f}%</span>"
                        f" · {_html.escape(str(_fa_arb.get('plain_english', '')))}</div></div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    "<div style='color:#334155; font-size:0.85rem;'>"
                    "No FAsset arbitrage window open right now. Spread is within normal range.</div>",
                    unsafe_allow_html=True,
                )
        except Exception as _fa_e:
            logger.warning("[Portfolio] FAsset arb detection failed: %s", _fa_e)
            st.caption("Arb detection temporarily unavailable — try refreshing.")


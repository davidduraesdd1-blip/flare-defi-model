"""
pages/5_Agent.py — Autonomous Trading Agent Control Panel.

Shows: agent status, phase gate progress, paper P&L, trade log,
open positions, emergency stop, and wallet setup wizard.
"""

import sys
from pathlib import Path
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.common import (
    page_setup, render_sidebar, render_section_header, get_user_level,
    render_what_this_means,
)
from agents.agent_runner import AgentRunner
from agents.config import (
    OPERATING_MODE, PAPER_TRADING_GATE_DAYS, PHASE2_WALLET_CAP_USD,
    PAPER_STARTING_BALANCE_USD, EMERGENCY_STOP_KEY,
)
from agents.wallet_manager import WalletManager

page_setup("Agent · Flare DeFi")
ctx = render_sidebar()
_user_level = ctx.get("user_level", get_user_level())

_runner = AgentRunner()
_wallets = WalletManager()

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.agent-status-card {
    background: rgba(0,212,170,0.07);
    border: 1px solid rgba(0,212,170,0.25);
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 12px;
}
.agent-stopped { border-color: rgba(239,68,68,0.4); background: rgba(239,68,68,0.07); }
.agent-running { border-color: rgba(34,197,94,0.4); background: rgba(34,197,94,0.07); }
.phase-gate-locked { border-color: rgba(245,158,11,0.4); background: rgba(245,158,11,0.07); }
.limit-row { display:flex; justify-content:space-between; padding:4px 0;
             border-bottom:1px solid rgba(255,255,255,0.04); font-size:0.82rem; }
.estop-btn { background:#ef4444 !important; color:#fff !important; font-weight:800;
             border-radius:8px !important; width:100% !important; }
</style>
""", unsafe_allow_html=True)

# ─── Page header ──────────────────────────────────────────────────────────────
render_section_header(
    "Autonomous Trading Agent",
    "AI-powered yield optimizer · Paper → Live progression · All limits enforced",
)
render_what_this_means(
    "This agent automatically analyzes DeFi opportunities and decides whether to enter, "
    "exit, or hold positions — all within strict risk limits you cannot override. "
    "Paper mode is safe: zero real money, full simulation. "
    "Live mode requires 14 days of paper trading AND your manual unlock.",
    title="What is the Trading Agent?",
    intermediate_message=(
        f"Claude claude-sonnet-4-6 decision loop · 5-min cycle · "
        f"2% max trade · 2% daily loss cap · 10% drawdown stop · "
        f"{PAPER_TRADING_GATE_DAYS}-day paper gate before live"
    ),
)

# ─── Load state ───────────────────────────────────────────────────────────────
state        = _runner.get_state()
mode         = state.get("mode", OPERATING_MODE)
running      = state.get("running", False)
e_stop       = state.get(EMERGENCY_STOP_KEY, False)
paper_days   = _runner.get_paper_days()
live_unlocked = state.get("live_manually_unlocked", False)
last_ts      = state.get("last_decision_ts", "—")
last_dec     = state.get("last_decision", {})
paper_stats  = _runner.get_paper_stats()
open_pos     = _runner.get_open_positions()

# ─── Status card ──────────────────────────────────────────────────────────────
if e_stop:
    status_class = "agent-stopped"
    status_icon  = "🔴"
    status_text  = "EMERGENCY STOP ACTIVE"
elif running:
    status_class = "agent-running"
    status_icon  = "🟢"
    status_text  = f"RUNNING · {mode}"
else:
    status_class = "agent-stopped"
    status_icon  = "⏸️"
    status_text  = f"PAUSED · {mode}"

st.markdown(f"""
<div class="agent-status-card {status_class}">
    <div style="font-size:1.1rem;font-weight:800;color:#f1f5f9;">
        {status_icon} {status_text}
    </div>
    <div style="color:#94a3b8;font-size:0.82rem;margin-top:6px;">
        Last decision: {last_ts}
        {"&nbsp;·&nbsp;" + last_dec.get("action","—") + " → " + last_dec.get("protocol","—")
         if last_dec.get("action") else ""}
    </div>
</div>
""", unsafe_allow_html=True)

# Last decision reasoning
if last_dec.get("reasoning"):
    verdict_color = "#22c55e" if last_dec.get("approved") else "#f59e0b"
    verdict_icon  = "▲" if last_dec.get("approved") else "■"
    st.markdown(
        f"<div style='font-size:0.82rem;color:{verdict_color};margin-bottom:12px;'>"
        f"{verdict_icon} {last_dec.get('reason','')}</div>"
        f"<div style='font-size:0.80rem;color:#64748b;margin-bottom:16px;'>"
        f"Reasoning: {last_dec.get('reasoning','')}</div>",
        unsafe_allow_html=True,
    )

# ─── Control buttons ──────────────────────────────────────────────────────────
col_start, col_stop, col_cycle, col_estop = st.columns([2, 2, 2, 2])

with col_start:
    if not running and not e_stop:
        if st.button("▶ Start Agent", use_container_width=True, type="primary"):
            _runner.start()
            st.success("Agent started.")
            st.rerun()
    elif running:
        if st.button("⏸ Pause Agent", use_container_width=True):
            _runner.stop()
            st.info("Agent paused.")
            st.rerun()

with col_stop:
    if e_stop:
        if st.button("🔄 Reset Emergency Stop", use_container_width=True):
            _runner.reset_emergency_stop()
            st.success("Emergency stop cleared.")
            st.rerun()

with col_cycle:
    if st.button("⚡ Run One Cycle Now", use_container_width=True):
        _runner.run_cycle_now()
        st.success("Cycle complete.")
        st.rerun()

with col_estop:
    if st.button("🛑 EMERGENCY STOP", use_container_width=True, type="secondary"):
        _runner.emergency_stop("User triggered emergency stop from UI")
        st.error("Emergency stop activated. All activity halted.")
        st.rerun()

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Performance summary ───────────────────────────────────────────────────────
render_section_header("Paper Performance", "Simulation results — no real money")

paper_balance = PAPER_STARTING_BALANCE_USD
if paper_stats["total_trades"] > 0:
    paper_balance += paper_stats["total_pnl"]

m1, m2, m3, m4 = st.columns(4)
with m1:
    st.metric("Paper Balance",
              f"${paper_balance:,.2f}",
              delta=f"${paper_stats['total_pnl']:+,.2f}")
with m2:
    st.metric("Total Trades", paper_stats["total_trades"])
with m3:
    st.metric("Win Rate",
              f"{paper_stats['win_rate']:.1f}%" if paper_stats["total_trades"] > 0 else "—")
with m4:
    st.metric("Avg Trade P&L",
              f"{paper_stats['avg_pnl_pct']:+.3f}%" if paper_stats["total_trades"] > 0 else "—")

render_what_this_means(
    "Paper trading is a simulation using real market prices but virtual money. "
    "The agent must run in paper mode for 14 days before live trading can be unlocked. "
    "Watch the win rate and average P&L — these tell you if the model is working.",
    title="What is paper trading?",
    intermediate_message=f"Paper P&L simulated from real prices + slippage model. Gate: {paper_days}/{PAPER_TRADING_GATE_DAYS} days.",
)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Phase Gate ───────────────────────────────────────────────────────────────
render_section_header("Phase Gate", "14-day paper requirement before live unlock")

gate_pct = min(100, paper_days / PAPER_TRADING_GATE_DAYS * 100)
gate_color = "#22c55e" if gate_pct >= 100 else "#f59e0b"

st.markdown(
    f"<div class='phase-gate-locked' style='border-radius:10px;padding:14px 18px;margin-bottom:12px;'>"
    f"<div style='display:flex;justify-content:space-between;margin-bottom:8px;'>"
    f"<span style='color:#e2e8f0;font-weight:700'>Paper Days Completed</span>"
    f"<span style='color:{gate_color};font-weight:800'>{paper_days} / {PAPER_TRADING_GATE_DAYS}</span>"
    f"</div>"
    f"<div style='background:rgba(255,255,255,0.07);border-radius:4px;height:8px;'>"
    f"<div style='width:{gate_pct:.0f}%;height:8px;background:{gate_color};"
    f"border-radius:4px;transition:width 0.4s;'></div></div>"
    f"</div>",
    unsafe_allow_html=True,
)

if gate_pct >= 100 and not live_unlocked:
    st.markdown("**Phase gate satisfied.** You can now unlock live trading.")
    confirm = st.text_input(
        "Type UNLOCK LIVE to confirm:",
        placeholder="UNLOCK LIVE",
        key="live_unlock_confirm",
    )
    if st.button("🔓 Unlock Live Mode", type="primary"):
        if confirm.strip() == "UNLOCK LIVE":
            try:
                _runner.unlock_live()
                st.success("Live mode unlocked. Set AGENT_MODE=LIVE_PHASE2 env var to activate.")
                st.rerun()
            except Exception as e:
                st.error(str(e))
        else:
            st.error("Type exactly: UNLOCK LIVE")
elif live_unlocked:
    st.success(f"✓ Live mode unlocked. Current mode: **{mode}**. "
               f"Phase 2 wallet cap: ${PHASE2_WALLET_CAP_USD:,.0f}")
else:
    remaining = PAPER_TRADING_GATE_DAYS - paper_days
    st.info(f"ℹ️ {remaining} more paper trading days needed before live unlock is available.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Active Risk Limits ───────────────────────────────────────────────────────
render_section_header("Active Risk Limits", "Hardcoded safeguards — these cannot be changed by the AI")

paper_balance_for_limits = max(paper_balance, 100)
limits = _runner.get_risk_limits(paper_balance_for_limits)

limit_rows = [
    ("Max trade size",         f"${limits['max_trade_usd']:,.2f}  (2% of wallet)"),
    ("Max daily loss",         f"${limits['max_daily_loss_usd']:,.2f}  (2% of wallet)"),
    ("Max drawdown",           f"{limits['max_drawdown_pct']:.0f}%  → full stop"),
    ("Max open positions",     str(limits["max_open_positions"])),
    ("Loss cooldown",          f"{limits['cooldown_minutes']} minutes"),
    ("Min AI confidence",      f"{limits['min_confidence']*100:.0f}%"),
    ("Min net profit",         f"{limits['min_profit_pct']:.1f}%  after gas"),
    ("Max slippage",           f"{limits['max_slippage_pct']:.1f}%"),
    ("Whitelisted protocols",  ", ".join(limits["protocol_whitelist"])),
    ("Phase 2 wallet cap",     f"${limits['phase2_cap_usd']:,.0f}"),
]

limit_html = "".join(
    f"<div class='limit-row'>"
    f"<span style='color:#64748b'>{k}</span>"
    f"<span style='color:#e2e8f0;font-weight:600'>{v}</span>"
    f"</div>"
    for k, v in limit_rows
)
st.markdown(
    f"<div style='background:rgba(0,0,0,0.2);border-radius:8px;padding:12px 16px;'>"
    f"{limit_html}</div>",
    unsafe_allow_html=True,
)

render_what_this_means(
    "These limits are hardcoded in the program. The AI cannot see or change them. "
    "Every decision the AI makes is checked against these limits before anything happens. "
    "If any limit is violated, the trade is rejected — no exceptions.",
    title="How are the risk limits enforced?",
    intermediate_message="Risk limits enforced by independent RiskGuard layer — AI decision engine never accesses config.py.",
)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Open Positions ───────────────────────────────────────────────────────────
render_section_header("Open Positions", "Current paper/live positions")

if open_pos:
    pos_rows = []
    for p in open_pos:
        pos_rows.append({
            "Chain":    p.get("chain", "—").upper(),
            "Protocol": p.get("protocol", "—").title(),
            "Pool":     p.get("pool", "—"),
            "Size":     f"${p.get('size_usd', 0):,.2f}",
            "Est. APY": f"{p.get('expected_apy', 0)*100:.1f}%",
            "Unreal. P&L": f"${p.get('unrealized_pnl', 0):+,.4f}",
            "Opened":   str(p.get("entry_timestamp", ""))[:16],
        })
    st.dataframe(pd.DataFrame(pos_rows), width="stretch", hide_index=True)
else:
    st.markdown(
        "<div style='color:#475569;font-size:0.88rem;padding:12px 0;'>"
        "No open positions. Agent will enter positions when approved opportunities appear.</div>",
        unsafe_allow_html=True,
    )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Trade Log ────────────────────────────────────────────────────────────────
render_section_header("Trade Log", "Full record of all paper and live trades")

trades = _runner.get_trades(status="all")
if trades:
    trade_rows = []
    for t in trades:
        trade_rows.append({
            "Time":      str(t.get("timestamp", ""))[:16],
            "Mode":      t.get("mode", "—"),
            "Chain":     t.get("chain", "—").upper(),
            "Protocol":  t.get("protocol", "—").title(),
            "Pool":      t.get("pool", "—"),
            "Action":    t.get("action", "—"),
            "Size":      f"${t.get('size_usd', 0):,.2f}",
            "APY":       f"{t.get('expected_apy', 0)*100:.1f}%",
            "Slippage":  f"{t.get('slippage_pct', 0)*100:.3f}%",
            "Gas":       f"${t.get('gas_usd', 0):.5f}",
            "Status":    t.get("status", "—"),
            "P&L":       f"${t.get('realized_pnl', 0):+,.4f}" if t.get("status") == "closed" else "open",
        })
    df = pd.DataFrame(trade_rows)
    st.dataframe(df, width="stretch", hide_index=True)

    csv = df.to_csv(index=False)
    st.download_button("⬇ Export Trade Log CSV", data=csv,
                       file_name="agent_trade_log.csv", mime="text/csv")
else:
    st.markdown(
        "<div style='color:#475569;font-size:0.88rem;padding:12px 0;'>"
        "No trades yet. Start the agent and run a cycle to begin paper trading.</div>",
        unsafe_allow_html=True,
    )

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Audit Log ────────────────────────────────────────────────────────────────
render_section_header("Audit Log", "Every decision, approval, rejection, and error")

with st.expander("Show Full Audit Log (last 50 events)", expanded=False):
    audit_rows = _runner.get_recent_audit(limit=50)
    if audit_rows:
        audit_df_rows = []
        for a in audit_rows:
            audit_df_rows.append({
                "Time":     str(a.get("timestamp", ""))[:16],
                "Event":    a.get("event_type", "—"),
                "Chain":    a.get("chain", "—"),
                "Protocol": a.get("protocol", "—"),
                "Action":   a.get("action", "—"),
                "Approved": "✓" if a.get("approved") else "✗",
                "Reason":   str(a.get("reason", ""))[:80],
            })
        st.dataframe(pd.DataFrame(audit_df_rows), width="stretch", hide_index=True)
    else:
        st.caption("No audit events yet.")

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

# ─── Wallet Setup ─────────────────────────────────────────────────────────────
render_section_header("Wallet Setup", "Generate encrypted agent wallets for Phase 2")

wallet_status = _wallets.wallets_exist()
flare_addr    = _wallets.get_flare_address()
xrpl_addr     = _wallets.get_xrpl_address()

if wallet_status["flare"] and wallet_status["xrpl"]:
    st.success("✓ Both wallets generated and encrypted.")
    if flare_addr:
        st.markdown(f"**Flare address:** `{flare_addr}`")
        st.caption("Fund this address with FLR before enabling Phase 2.")
    if xrpl_addr:
        st.markdown(f"**XRPL address:** `{xrpl_addr}`")
        st.caption("Fund this address with XRP (min 10 XRP reserve) before enabling Phase 2.")
else:
    render_what_this_means(
        "Before live trading, you need two dedicated wallets — one for Flare, one for XRPL. "
        "These are separate from your personal wallets. Set a strong password and never share it. "
        "The private keys are stored encrypted on this device — never uploaded anywhere.",
        title="About agent wallets",
        intermediate_message="Dedicated bot wallets, isolated from personal holdings. AES-256-GCM encrypted, PBKDF2-SHA256 key derivation.",
    )

    with st.form("wallet_setup_form"):
        st.markdown("**Generate agent wallets** — both Flare (EVM) and XRPL")
        st.warning("⚠️ Set a strong password. You will need this password every time the agent signs a live transaction.")
        pwd1 = st.text_input("Password", type="password", key="wallet_pwd1")
        pwd2 = st.text_input("Confirm password", type="password", key="wallet_pwd2")
        submitted = st.form_submit_button("Generate Wallets", type="primary")

    if submitted:
        if not pwd1 or len(pwd1) < 12:
            st.error("Password must be at least 12 characters.")
        elif pwd1 != pwd2:
            st.error("Passwords do not match.")
        else:
            try:
                result = _wallets.setup_wallets(pwd1)
                st.success("✓ Wallets generated and encrypted successfully!")
                st.markdown(f"**Flare address:** `{result.get('flare', 'error')}`")
                st.markdown(f"**XRPL address:** `{result.get('xrpl', 'error')}`")
                st.info(
                    "Fund each wallet with the Phase 2 amount ($1,000 equivalent) "
                    "only after completing 14 days of paper trading."
                )
                st.rerun()
            except Exception as e:
                st.error(f"Wallet generation failed: {e}")

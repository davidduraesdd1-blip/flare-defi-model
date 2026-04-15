"""
pages/5_Agent.py — Autonomous Trading Agent Control Panel.

Shows: agent status, phase gate progress, paper P&L, trade log,
open positions, emergency stop, and wallet setup wizard.
"""

import sys
import logging
from pathlib import Path
import pandas as pd
import streamlit as st

logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent.parent))

from ui.common import (
    page_setup, render_sidebar, render_section_header, get_user_level,
    render_what_this_means,
)
from agents.agent_runner import AgentRunner
from agents.config import (
    OPERATING_MODE, PAPER_TRADING_GATE_DAYS, PHASE2_WALLET_CAP_USD,
    PAPER_STARTING_BALANCE_USD, EMERGENCY_STOP_KEY,
    MAX_TRADE_SIZE_PCT, MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT,
    MIN_CONFIDENCE, MAX_OPEN_POSITIONS, COOLDOWN_AFTER_LOSS_SECONDS,
    MIN_TRADE_SIZE_USD, MAX_REASONABLE_APY,
    load_overrides, save_overrides,
)
page_setup("Agent · Family Office · DeFi Intelligence")
ctx = render_sidebar()
_user_level = ctx.get("user_level", get_user_level())

_runner = AgentRunner()

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.agent-status-card {
    background: rgba(0,212,170,0.07);
    border: 1px solid rgba(0,212,170,0.25);
    border-radius: 10px;
    padding: 10px 14px;
    margin-bottom: 8px;
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
        f"claude-sonnet-4-6 decision loop · 5-min cycle · "
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

import html as _html_mod
_dec_action   = _html_mod.escape(str(last_dec.get("action", "—")))
_dec_protocol = _html_mod.escape(str(last_dec.get("protocol", "—")))
_dec_suffix = (
    f"&nbsp;·&nbsp;{_dec_action} → {_dec_protocol}"
    if last_dec.get("action") else ""
)
st.html(
    f"<div class='agent-status-card {status_class}' style='background:rgba(0,212,170,0.07);"
    f"border:1px solid rgba(0,212,170,0.25);border-radius:10px;padding:10px 14px;margin-bottom:8px;'>"
    f"<div style='font-size:1.1rem;font-weight:800;color:#f1f5f9;'>{status_icon} {status_text}</div>"
    f"<div style='color:#94a3b8;font-size:0.82rem;margin-top:6px;'>"
    f"Last decision: {_html_mod.escape(str(last_ts or '—'))}{_dec_suffix}</div></div>"
)

# Last decision reasoning
if last_dec.get("reasoning"):
    verdict_color = "#22c55e" if last_dec.get("approved") else "#f59e0b"
    verdict_icon  = "▲" if last_dec.get("approved") else "■"
    _dec_reason   = _html_mod.escape(str(last_dec.get("reason", "")))
    _dec_reasoning = _html_mod.escape(str(last_dec.get("reasoning", "")))
    st.html(
        f"<div style='font-size:0.82rem;color:{verdict_color};margin-bottom:12px;'>"
        f"{verdict_icon} {_dec_reason}</div>"
        f"<div style='font-size:0.80rem;color:#64748b;margin-bottom:16px;'>"
        f"Reasoning: {_dec_reasoning}</div>"
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

st.divider()

# ─── Agent Configuration ──────────────────────────────────────────────────────
render_section_header("Agent Configuration", "Adjust risk limits and behaviour — changes apply on next decision cycle")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:8px;'>"
    "Changes take effect on the <b>next decision cycle</b> (within 5 minutes). "
    "The decision loop interval requires an app restart to change.</div>",
    unsafe_allow_html=True,
)

try:
    _overrides = load_overrides()

    st.markdown("#### Position Sizing & Trade Quality")
    _ag_c1, _ag_c2, _ag_c3 = st.columns(3)
    with _ag_c1:
        _max_trade = st.slider(
            "Max trade size (% of wallet)",
            min_value=1, max_value=10,
            value=int(round(_overrides.get("MAX_TRADE_SIZE_PCT", MAX_TRADE_SIZE_PCT) * 100)),
            step=1,
            key="ag_max_trade_pct",
            help=f"Default: {int(MAX_TRADE_SIZE_PCT*100)}%. Max size per single trade as % of wallet balance.",
        )
    with _ag_c2:
        _min_trade = st.number_input(
            "Min trade size ($)",
            min_value=1.0, max_value=500.0,
            value=float(_overrides.get("MIN_TRADE_SIZE_USD", MIN_TRADE_SIZE_USD)),
            step=1.0,
            key="ag_min_trade_usd",
            help=f"Default: ${MIN_TRADE_SIZE_USD:.0f}. Trades smaller than this are skipped (gas cost > profit).",
        )
    with _ag_c3:
        _min_conf = st.slider(
            "Min confidence threshold",
            min_value=50, max_value=90,
            value=int(round(_overrides.get("MIN_CONFIDENCE", MIN_CONFIDENCE) * 100)),
            step=5,
            key="ag_min_confidence",
            help=f"Default: {int(MIN_CONFIDENCE*100)}%. Claude must be this confident or the trade is skipped.",
        )

    st.markdown("#### Loss Limits & Risk Controls")
    _ag_d1, _ag_d2, _ag_d3 = st.columns(3)
    with _ag_d1:
        _max_daily = st.slider(
            "Daily loss limit (% of wallet)",
            min_value=1, max_value=10,
            value=int(round(_overrides.get("MAX_DAILY_LOSS_PCT", MAX_DAILY_LOSS_PCT) * 100)),
            step=1,
            key="ag_max_daily_loss",
            help=f"Default: {int(MAX_DAILY_LOSS_PCT*100)}%. Agent pauses for the rest of the day if this is hit.",
        )
    with _ag_d2:
        _max_drawdown = st.slider(
            "Max drawdown from peak (%)",
            min_value=5, max_value=30,
            value=int(round(_overrides.get("MAX_DRAWDOWN_PCT", MAX_DRAWDOWN_PCT) * 100)),
            step=5,
            key="ag_max_drawdown",
            help=f"Default: {int(MAX_DRAWDOWN_PCT*100)}%. Full stop if portfolio drops this far from peak (requires manual restart).",
        )
    with _ag_d3:
        _cooldown = st.slider(
            "Cooldown after loss (minutes)",
            min_value=15, max_value=240,
            value=int(_overrides.get("COOLDOWN_AFTER_LOSS_SECONDS", COOLDOWN_AFTER_LOSS_SECONDS) // 60),
            step=15,
            key="ag_cooldown_min",
            help=f"Default: {COOLDOWN_AFTER_LOSS_SECONDS // 60} min. How long the agent pauses after any losing trade.",
        )

    st.markdown("#### Positions & APY Limits")
    _ag_e1, _ag_e2 = st.columns(2)
    with _ag_e1:
        _max_pos = st.slider(
            "Max simultaneous positions",
            min_value=1, max_value=5,
            value=int(_overrides.get("MAX_OPEN_POSITIONS", MAX_OPEN_POSITIONS)),
            step=1,
            key="ag_max_positions",
            help=f"Default: {MAX_OPEN_POSITIONS}. Max number of open trades at once.",
        )
    with _ag_e2:
        _max_apy = st.slider(
            "Max believable APY (%)",
            min_value=50, max_value=500,
            value=int(round(_overrides.get("MAX_REASONABLE_APY", MAX_REASONABLE_APY) * 100)),
            step=50,
            key="ag_max_apy",
            help=f"Default: {int(MAX_REASONABLE_APY*100)}%. APY signals above this are rejected as likely data errors.",
        )

    st.markdown("#### Paper Trading Settings")
    _ag_f1, _ag_f2, _ag_f3 = st.columns(3)
    with _ag_f1:
        # Logarithmic slider $1 → $1B, with manual entry for exact amounts
        _pb_milestones = [
            1, 5, 10, 25, 50, 100, 250, 500,
            1_000, 2_500, 5_000, 10_000, 25_000, 50_000, 100_000,
            250_000, 500_000, 750_000,
            1_000_000, 2_500_000, 5_000_000, 10_000_000, 25_000_000,
            50_000_000, 100_000_000, 250_000_000, 500_000_000, 1_000_000_000,
        ]
        def _fmt_pb(v: float) -> str:
            v = int(v)
            if v >= 1_000_000_000: return "$1B"
            if v >= 1_000_000: return f"${v//1_000_000}M"
            if v >= 1_000: return f"${v//1_000}K"
            return f"${v}"
        _cur_pb = float(_overrides.get("PAPER_STARTING_BALANCE_USD", PAPER_STARTING_BALANCE_USD))
        _pb_idx = min(range(len(_pb_milestones)), key=lambda i: abs(_pb_milestones[i] - _cur_pb))
        _pb_slider_idx = st.slider(
            "Paper trading balance",
            min_value=0, max_value=len(_pb_milestones) - 1,
            value=_pb_idx,
            format="%d",
            key="ag_paper_balance_slider",
            help="Slide to set starting balance ($1 – $1B). Use entry below for exact amount.",
        )
        _pb_from_slider = _pb_milestones[_pb_slider_idx]
        st.markdown(
            f"<div style='font-size:0.8rem;color:#00d4aa;font-weight:700;margin-top:-8px;margin-bottom:2px;'>"
            f"{_fmt_pb(_pb_from_slider)}</div>",
            unsafe_allow_html=True,
        )
        _paper_bal = st.number_input(
            "Exact balance ($)",
            min_value=1.0, max_value=1_000_000_000.0,
            value=float(_pb_from_slider),
            step=max(1.0, float(_pb_from_slider) / 10),
            format="%.0f",
            key="ag_paper_balance",
            label_visibility="collapsed",
            help=f"Default: ${PAPER_STARTING_BALANCE_USD:,.0f}. Virtual wallet for paper trading mode.",
        )
    with _ag_f2:
        _gate_days = st.slider(
            "Paper trading gate (days)",
            min_value=3, max_value=30,
            value=int(_overrides.get("PAPER_TRADING_GATE_DAYS", PAPER_TRADING_GATE_DAYS)),
            step=1,
            key="ag_gate_days",
            help=f"Default: {PAPER_TRADING_GATE_DAYS} days. Minimum paper trading days before live mode can be unlocked.",
        )
    with _ag_f3:
        _phase2_cap = st.number_input(
            "Live Phase 2 wallet cap ($)",
            min_value=100.0, max_value=10_000.0,
            value=float(_overrides.get("PHASE2_WALLET_CAP_USD", PHASE2_WALLET_CAP_USD)),
            step=100.0,
            key="ag_phase2_cap",
            help=f"Default: ${PHASE2_WALLET_CAP_USD:,.0f}. Hard cap on wallet size in Live Phase 2.",
        )

    st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
    _btn_c1, _btn_c2, _btn_c3 = st.columns([2, 2, 4])
    with _btn_c1:
        if st.button("💾 Save Agent Config", key="save_agent_cfg", use_container_width=True, type="primary"):
            _new_overrides = {
                "MAX_TRADE_SIZE_PCT":          _max_trade / 100.0,
                "MAX_DAILY_LOSS_PCT":          _max_daily / 100.0,
                "MAX_DRAWDOWN_PCT":            _max_drawdown / 100.0,
                "MIN_CONFIDENCE":              _min_conf / 100.0,
                "MAX_OPEN_POSITIONS":          int(_max_pos),
                "COOLDOWN_AFTER_LOSS_SECONDS": int(_cooldown * 60),
                "PAPER_STARTING_BALANCE_USD":  float(_paper_bal),
                "PHASE2_WALLET_CAP_USD":       float(_phase2_cap),
                "MIN_TRADE_SIZE_USD":          float(_min_trade),
                "MAX_REASONABLE_APY":          _max_apy / 100.0,
                "PAPER_TRADING_GATE_DAYS":     int(_gate_days),
            }
            save_overrides(_new_overrides)
            st.success("Agent config saved. Changes take effect on the next decision cycle.")
    with _btn_c2:
        if st.button("↺ Reset to Defaults", key="reset_agent_cfg", use_container_width=True):
            save_overrides({})
            st.success("Agent config reset to code defaults.")
            st.rerun()

    # ── Active limits panel — shows effective values (overrides applied) ─────────
    # Compute effective values: override wins, else fall back to code default
    def _eff(key, default): return _overrides.get(key, default)
    def _tag(key): return "custom" if key in _overrides else "default"

    st.markdown("#### Active Limits (what the agent will use on the next cycle)")
    _lc1, _lc2, _lc3, _lc4 = st.columns(4)
    _lc1.metric("Max trade size",
                f"{int(round(_eff('MAX_TRADE_SIZE_PCT', MAX_TRADE_SIZE_PCT) * 100))}% of wallet",
                delta=_tag("MAX_TRADE_SIZE_PCT"), delta_color="off")
    _lc2.metric("Daily loss limit",
                f"{int(round(_eff('MAX_DAILY_LOSS_PCT', MAX_DAILY_LOSS_PCT) * 100))}% of wallet",
                delta=_tag("MAX_DAILY_LOSS_PCT"), delta_color="off")
    _lc3.metric("Min confidence",
                f"{int(round(_eff('MIN_CONFIDENCE', MIN_CONFIDENCE) * 100))}%",
                delta=_tag("MIN_CONFIDENCE"), delta_color="off")
    _lc4.metric("Max positions",
                str(int(_eff("MAX_OPEN_POSITIONS", MAX_OPEN_POSITIONS))),
                delta=_tag("MAX_OPEN_POSITIONS"), delta_color="off")
    _lc5, _lc6, _lc7, _lc8 = st.columns(4)
    _lc5.metric("Max drawdown",
                f"{int(round(_eff('MAX_DRAWDOWN_PCT', MAX_DRAWDOWN_PCT) * 100))}%",
                delta=_tag("MAX_DRAWDOWN_PCT"), delta_color="off")
    _lc6.metric("Cooldown after loss",
                f"{int(_eff('COOLDOWN_AFTER_LOSS_SECONDS', COOLDOWN_AFTER_LOSS_SECONDS) // 60)} min",
                delta=_tag("COOLDOWN_AFTER_LOSS_SECONDS"), delta_color="off")
    _lc7.metric("Min trade size",
                f"${_eff('MIN_TRADE_SIZE_USD', MIN_TRADE_SIZE_USD):.0f}",
                delta=_tag("MIN_TRADE_SIZE_USD"), delta_color="off")
    _lc8.metric("Max believable APY",
                f"{int(round(_eff('MAX_REASONABLE_APY', MAX_REASONABLE_APY) * 100))}%",
                delta=_tag("MAX_REASONABLE_APY"), delta_color="off")
    st.caption(
        "Values labelled 'custom' are your saved overrides. 'default' = code defaults. "
        "Hit 'Run One Cycle Now' to run a cycle — the Audit Log entry below will confirm "
        "these exact limits were active (stored in config_at_cycle for each decision)."
    )

except Exception as _ag_cfg_err:
    logger.warning("[Agent] config section failed: %s", _ag_cfg_err)
    st.warning("Agent configuration temporarily unavailable — try refreshing.")

st.divider()

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

st.divider()

# ─── Phase Gate ───────────────────────────────────────────────────────────────
render_section_header("Phase Gate", "14-day paper requirement before live unlock")

gate_pct = min(100, paper_days / PAPER_TRADING_GATE_DAYS * 100)
gate_color = "#22c55e" if gate_pct >= 100 else "#f59e0b"

st.markdown(
    f"<div class='phase-gate-locked' style='border-radius:10px;padding:9px 13px;margin-bottom:8px;'>"
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
                logger.warning("[Agent] unlock_live failed: %s", e)
                st.error("Failed to unlock live mode — please check your configuration and try again.")
        else:
            st.error("Type exactly: UNLOCK LIVE")
elif live_unlocked:
    st.success(f"✓ Live mode unlocked. Current mode: **{mode}**. "
               f"Phase 2 wallet cap: ${PHASE2_WALLET_CAP_USD:,.0f}")
else:
    remaining = PAPER_TRADING_GATE_DAYS - paper_days
    st.info(f"ℹ️ {remaining} more paper trading days needed before live unlock is available.")

st.divider()

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

st.divider()

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
    st.dataframe(pd.DataFrame(pos_rows), width='stretch', hide_index=True)
else:
    st.markdown(
        "<div style='color:#475569;font-size:0.88rem;padding:12px 0;'>"
        "No open positions. Agent will enter positions when approved opportunities appear.</div>",
        unsafe_allow_html=True,
    )

st.divider()

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
    st.dataframe(df, width='stretch', hide_index=True)

    csv = df.to_csv(index=False)
    st.download_button("⬇ Export Trade Log CSV", data=csv,
                       file_name="agent_trade_log.csv", mime="text/csv")
else:
    st.markdown(
        "<div style='color:#475569;font-size:0.88rem;padding:12px 0;'>"
        "No trades yet. Start the agent and run a cycle to begin paper trading.</div>",
        unsafe_allow_html=True,
    )

st.divider()

# ─── Audit Log ────────────────────────────────────────────────────────────────
render_section_header("Audit Log", "Every decision, approval, rejection, and error")

st.caption(
    "Every decision cycle is logged below — including what config limits were active. "
    "Data is fetched from live APIs each cycle; the decision row confirms it ran. "
    "Expand the 'extra' column in the raw DB to see the full config_at_cycle snapshot."
)
with st.expander("Audit Log (last 200 events)", expanded=True):
    audit_rows = _runner.get_recent_audit(limit=200)
    if audit_rows:
        audit_df_rows = []
        for a in audit_rows:
            # Parse composite score from extra JSON if present
            _extra_str = a.get("extra", "{}")
            try:
                import json as _json
                _extra = _json.loads(_extra_str) if isinstance(_extra_str, str) else (_extra_str or {})
            except Exception:
                _extra = {}
            _composite = _extra.get("composite_score") or _extra.get("config_at_cycle", {})
            _cscore_str = f"{float(_extra.get('composite_score', 0)):+.3f}" if _extra.get("composite_score") is not None else "—"
            audit_df_rows.append({
                "Time":       str(a.get("timestamp", ""))[:16],
                "Event":      a.get("event_type", "—"),
                "Chain":      a.get("chain", "—"),
                "Protocol":   a.get("protocol", "—"),
                "Action":     a.get("action", "—"),
                "Size $":     f"${a.get('size_usd', 0):,.0f}" if a.get("size_usd") else "—",
                "Approved":   "✓" if a.get("approved") else "✗",
                "Signal":     _cscore_str,
                "Reason":     str(a.get("reason", ""))[:100],
            })

        _audit_df = pd.DataFrame(audit_df_rows)
        st.dataframe(_audit_df, width='stretch', hide_index=True)

        # ── Download buttons (Item 42) ──────────────────────────────────────
        _al_c1, _al_c2 = st.columns(2)
        with _al_c1:
            _audit_csv = _audit_df.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇ Export Audit Log CSV",
                data=_audit_csv,
                file_name="agent_audit_log.csv",
                mime="text/csv",
                key="dl_audit_csv",
            )
        with _al_c2:
            try:
                from fpdf import FPDF
                from fpdf.enums import XPos, YPos
                _report_dt = pd.Timestamp.now("UTC").strftime("%Y-%m-%d %H:%M UTC")
                _pdf = FPDF()
                _pdf.set_auto_page_break(auto=True, margin=10)
                _pdf.add_page()
                _pdf.set_font("Helvetica", "B", 14)
                _pdf.cell(0, 10, "Agent Audit Log", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                _pdf.set_font("Helvetica", "", 8)
                _pdf.cell(0, 6, f"Generated: {_report_dt} · {len(audit_rows)} events",
                           new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                _pdf.ln(3)
                _cols    = ["Time", "Event", "Protocol", "Action", "Approved", "Reason"]
                _widths  = [30, 35, 25, 25, 18, 57]
                _pdf.set_font("Helvetica", "B", 7)
                _pdf.set_fill_color(17, 24, 39)
                _pdf.set_text_color(255, 255, 255)
                for _c, _w in zip(_cols, _widths):
                    _pdf.cell(_w, 6, _c, border=1, fill=True)
                _pdf.ln()
                _pdf.set_text_color(0, 0, 0)
                _pdf.set_font("Helvetica", "", 6)
                def _ps(s: str) -> str:
                    """Sanitize text for FPDF Helvetica (latin-1 only)."""
                    return (str(s).replace("\u2014", "-").replace("\u2013", "-")
                             .replace("\u2018", "'").replace("\u2019", "'")
                             .replace("\u201c", '"').replace("\u201d", '"')
                             .encode("latin-1", errors="replace").decode("latin-1"))
                for _i, _a in enumerate(audit_rows):
                    _fill = _i % 2 == 0
                    _pdf.set_fill_color(241, 245, 249) if _fill else _pdf.set_fill_color(255, 255, 255)
                    _row_vals = [
                        _ps(str(_a.get("timestamp", ""))[:16]),
                        _ps(str(_a.get("event_type", ""))[:16]),
                        _ps(str(_a.get("protocol", "-"))[:12]),
                        _ps(str(_a.get("action", "-"))[:12]),
                        "YES" if _a.get("approved") else "NO",
                        _ps(str(_a.get("reason", ""))[:40]),
                    ]
                    for _v, _w in zip(_row_vals, _widths):
                        _pdf.cell(_w, 5, _v, border=1, fill=_fill)
                    _pdf.ln()
                _pdf_bytes = bytes(_pdf.output())
                st.download_button(
                    "⬇ Export Audit Log PDF",
                    data=_pdf_bytes,
                    file_name="agent_audit_log.pdf",
                    mime="application/pdf",
                    key="dl_audit_pdf",
                )
            except ImportError:
                st.caption("PDF export unavailable (install fpdf2)")
    else:
        st.caption("No audit events yet — hit 'Run One Cycle Now' to generate the first entry.")


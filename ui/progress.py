"""
ui/progress.py — Progress renderers for the DeFi Model.

- render_sidebar_progress():   compact text + linear bar, fits in 240px sidebar
- render_main_progress():      full rich treatment — SVG ring, step detail,
                                partial results, rotating DeFi fun facts

Both read state from utils.scan_progress. Theme-aware: detects light vs dark
via st.session_state['_theme'] + falls back to st.context when available.
"""
from __future__ import annotations

import time
from html import escape

import streamlit as st

from utils import scan_progress as _progress

# Fun facts rotate every 4s. Curated for DeFi audience.
DEFI_FUN_FACTS = [
    "DeFi's first $1B TVL was hit by MakerDAO in Feb 2020 — Uniswap joined the club four months later.",
    "The word 'yield farming' was coined in June 2020 when Compound launched COMP token rewards.",
    "Flash loans borrow millions of dollars with no collateral — the catch: you must repay within a single Ethereum transaction.",
    "At its 2021 peak, Anchor Protocol on Terra paid 20% APY on UST — the collapse wiped out $40B in days.",
    "The 2023 Euler Finance hack drained $197M, then the hacker voluntarily returned 100% of the funds.",
    "Curve Finance holds the record for highest stablecoin DEX volume — over $100B traded in a single year.",
    "Uniswap V3 introduced concentrated liquidity in 2021 — LPs can now achieve up to 4000× capital efficiency.",
    "The first RWA tokenization on-chain: MakerDAO accepted real-world assets as collateral in 2022.",
    "Aave's 'flash liquidation' is why 70%+ of DeFi liquidations are fully automated within 2 Ethereum blocks.",
    "Pendle lets you split a yield-bearing token into principal + yield — trading future yield like a bond strip.",
    "The Flare Network's FTSO has 100+ independent price oracles — no single provider can manipulate feeds.",
    "DeFi protocols collectively process more than $3T in annual volume — more than Visa on most days.",
    "Lido controls ~28% of all staked ETH — the largest liquid-staking derivative in DeFi.",
    "The 'impermanent loss' formula was first published in a 2018 Uniswap whitepaper footnote.",
    "Arbitrage bots on DEXs capture $600M+ in MEV every year — most of it flows to Flashbots builders.",
]


def _is_light_mode() -> bool:
    try:
        if st.context.theme.base == "light":
            return True
    except Exception:
        pass
    return st.session_state.get("_theme") == "light"


def _fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds > 60:
        return f"~{int(seconds // 60)}m {int(seconds % 60)}s left"
    if seconds > 3:
        return f"~{int(seconds)}s left"
    return "almost done…"


def render_sidebar_progress() -> None:
    """
    Compact sidebar indicator — always safe to call. Renders nothing when
    no scan is running. Fits in a ~240px sidebar without wrapping.
    """
    state = _progress.read()
    if not state or not state.get("running"):
        return
    step_n = int(state.get("step", 0))
    total  = int(state.get("total_steps", _progress.TOTAL_STEPS))
    name   = str(state.get("step_name", "Working…"))
    eta    = _fmt_eta(_progress.eta_seconds(state))
    pct    = 0 if total <= 0 else min(100, int(round(100.0 * step_n / total)))
    teal   = "#00d4aa"
    track  = "rgba(148,163,184,0.2)" if _is_light_mode() else "rgba(100,116,139,0.25)"
    text   = "#475569" if _is_light_mode() else "#94a3b8"

    st.markdown(
        f"""
<div style="margin:8px 0 12px 0; padding:10px 12px; border-radius:8px;
            border:1px solid rgba(0,212,170,0.25);
            background:rgba(0,212,170,0.06);">
  <div style="display:flex;align-items:center;justify-content:space-between;
              gap:8px;font-size:0.72rem;color:{text};font-weight:600;
              letter-spacing:0.3px;text-transform:uppercase;margin-bottom:6px;">
    <span>⏳ SCANNING</span><span>{step_n}/{total}</span>
  </div>
  <div style="font-size:0.78rem;color:{teal};font-weight:700;line-height:1.25;
              white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
    {escape(name)}
  </div>
  <div style="margin-top:6px;height:6px;background:{track};border-radius:3px;
              overflow:hidden;">
    <div style="height:100%;width:{pct}%;background:{teal};
                transition:width 0.4s ease-out;"></div>
  </div>
  <div style="margin-top:4px;font-size:0.68rem;color:{text};
              display:flex;justify-content:space-between;">
    <span>{pct}%</span><span>{escape(eta)}</span>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )


def _svg_ring(pct: int, size: int = 140) -> str:
    """Circular SVG progress ring — theme-safe (pure colour, no opacity tricks)."""
    r = size / 2 - 10
    cx = cy = size / 2
    circ = 2 * 3.14159265 * r
    fill_len = circ * pct / 100.0
    gap_len  = circ - fill_len
    track  = "rgba(148,163,184,0.22)" if _is_light_mode() else "rgba(100,116,139,0.3)"
    fill   = "#00d4aa"
    text   = "#0f172a" if _is_light_mode() else "#f1f5f9"
    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="{track}" stroke-width="10"/>'
        f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="none" '
        f'stroke="{fill}" stroke-width="10" stroke-linecap="round" '
        f'stroke-dasharray="{fill_len:.2f} {gap_len:.2f}" '
        f'transform="rotate(-90 {cx} {cy})"/>'
        f'<text x="{cx}" y="{cy}" text-anchor="middle" '
        f'dominant-baseline="central" font-family="JetBrains Mono, monospace" '
        f'font-size="26" font-weight="800" fill="{text}">{pct}%</text>'
        f'</svg>'
    )


def render_main_progress() -> bool:
    """
    Full rich progress screen for the main dashboard. Returns True when a
    scan is running (caller should skip its normal "empty-state" placeholder
    in that case) and False otherwise.
    """
    state = _progress.read()
    if not state or not state.get("running"):
        return False

    step_n = int(state.get("step", 0))
    total  = int(state.get("total_steps", _progress.TOTAL_STEPS))
    name   = str(state.get("step_name", "Working…"))
    detail = str(state.get("detail", ""))
    eta    = _fmt_eta(_progress.eta_seconds(state))
    partial = state.get("partial") or []
    pct    = 0 if total <= 0 else min(100, int(round(100.0 * step_n / total)))
    text     = "#0f172a" if _is_light_mode() else "#f1f5f9"
    subtext  = "#475569" if _is_light_mode() else "#94a3b8"
    cardbg   = "#ffffff" if _is_light_mode() else "#111827"
    border   = "#e2e8f0" if _is_light_mode() else "rgba(0,212,170,0.2)"

    fact = DEFI_FUN_FACTS[int(time.time() / 4) % len(DEFI_FUN_FACTS)]

    # ── Top block: ring + step detail
    st.markdown(
        f"""
<div style="background:{cardbg};border:1px solid {border};border-left:3px solid #00d4aa;
            border-radius:12px;padding:24px;margin:8px 0 18px 0;
            display:flex;align-items:center;gap:24px;flex-wrap:wrap;">
  <div style="flex:0 0 auto;">{_svg_ring(pct)}</div>
  <div style="flex:1 1 240px;min-width:240px;">
    <div style="font-size:0.72rem;color:{subtext};font-weight:700;
                letter-spacing:0.8px;text-transform:uppercase;margin-bottom:6px;">
      Scan in progress · Step {step_n} of {total}
    </div>
    <div style="font-size:1.3rem;color:{text};font-weight:800;line-height:1.2;
                margin-bottom:6px;">{escape(name)}</div>
    <div style="font-size:0.9rem;color:#00d4aa;font-weight:600;">
      {escape(detail) if detail else '&nbsp;'}
    </div>
    <div style="font-size:0.8rem;color:{subtext};margin-top:8px;">
      {escape(eta) if eta else 'measuring pace…'}
    </div>
  </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # ── Partial results (only after step 5 produces previews)
    if partial:
        rows_html = ""
        for r in partial[:5]:
            lbl  = escape(str(r.get("label", "—")))
            prof = escape(str(r.get("profile", "")).title())
            apy  = float(r.get("apy", 0) or 0)
            rows_html += (
                f'<div style="display:flex;justify-content:space-between;'
                f'gap:12px;padding:6px 0;border-bottom:1px dashed {border};">'
                f'<span style="color:{text};font-weight:700">{lbl}</span>'
                f'<span style="color:{subtext};font-size:0.8rem">{prof}</span>'
                f'<span style="color:#00d4aa;font-weight:700;'
                f'font-family:JetBrains Mono, monospace">{apy:.1f}% APY</span>'
                f'</div>'
            )
        st.markdown(
            f"""
<div style="background:{cardbg};border:1px solid {border};border-radius:12px;
            padding:16px 20px;margin-bottom:14px;">
  <div style="font-size:0.72rem;color:{subtext};font-weight:700;
              letter-spacing:0.8px;text-transform:uppercase;margin-bottom:8px;">
    ⚡ Top opportunities found so far
  </div>
  {rows_html}
</div>
""",
            unsafe_allow_html=True,
        )

    # ── Rotating fun fact
    st.markdown(
        f"""
<div style="font-size:0.82rem;color:{subtext};padding:10px 14px;
            border-left:2px solid rgba(0,212,170,0.5);margin-bottom:8px;
            line-height:1.5;font-style:italic;">
  💡 {escape(fact)}
</div>
""",
        unsafe_allow_html=True,
    )
    return True


@st.fragment(run_every=2)
def main_progress_fragment() -> None:
    """Fragment-wrapped main-dashboard progress. Call once per page."""
    try:
        render_main_progress()
    except Exception:
        pass

"""
ui/sidebar.py — Shared sibling-family left rail, top bar, and page header.

Mirrors the static mockups in shared-docs/design-mockups/sibling-family-crypto-signal*.html.
Streamlit's native sidebar is wrapped with our own CSS so it reads as the mockup's
left rail. The top bar, page header, and macro strip render as the first elements
of the main column via st.markdown.

Every page should:

    from ui import inject_theme, inject_streamlit_overrides, render_sidebar, render_top_bar, page_header

    inject_theme("crypto-signal-app", theme=st.session_state.get("theme", "dark"))
    inject_streamlit_overrides()
    render_sidebar(active="home", user_level=...)
    render_top_bar(breadcrumb=("Markets", "Home"), user_level=...)
    page_header(title="Market home", subtitle="...", data_sources=[...])
"""
from __future__ import annotations

from typing import Iterable, Literal, Sequence

try:
    import streamlit as st
except ImportError:  # pragma: no cover — module is streamlit-specific
    st = None  # type: ignore

from .design_system import ACCENTS, family_of


# ── Navigation model ──────────────────────────────────────────────────

NavItem = tuple[str, str, str]  # (key, label, icon)

# Full nav as shown on the mockups. The key maps to the internal page key the
# running Streamlit app already uses (Dashboard, Config Editor, etc.) via
# PAGE_KEY_TO_APP below — preserves existing logic, only relabels.
DEFAULT_NAV: dict[str, list[NavItem]] = {
    "Markets": [
        ("home",     "Home",       "◉"),
        ("signals",  "Signals",    "▲"),
        ("regimes",  "Regimes",    "◈"),
    ],
    "Research": [
        ("backtester", "Backtester", "∿"),
        ("onchain",    "On-chain",   "⬡"),
    ],
    "Account": [
        ("alerts",   "Alerts",     "◐"),
        ("settings", "Settings",   "⚙"),
    ],
}

# Maps the mockup-friendly key → existing app.py page key. Keeps all the
# existing page_* functions intact — only the presentation changes.
PAGE_KEY_TO_APP: dict[str, str] = {
    "home":       "Dashboard",
    "signals":    "Dashboard",        # Signals tab inside Dashboard
    "regimes":    "Dashboard",        # Regime section inside Dashboard
    "backtester": "Backtest Viewer",
    "onchain":    "Dashboard",        # On-chain subsection inside Dashboard
    "alerts":     "Config Editor",    # Alerts tab in Settings
    "settings":   "Config Editor",
}


# ── Sidebar brand block (standalone — usable without full nav swap) ──

def render_sidebar_brand(
    *,
    app: str = "crypto-signal-app",
    brand_name: str = "Signal",
    brand_tld: str = ".app",
    brand_glyph: str = "◈",
    version: str = "",
) -> None:
    """Render just the mockup brand block at the top of the sidebar.
    Use this when the caller wants to keep its existing nav but still get
    the new branded rail. Each sibling app passes its own name/tld/glyph."""
    if st is None:
        return
    accent = ACCENTS.get(app, ACCENTS["crypto-signal-app"])  # type: ignore[index]
    st.sidebar.markdown(
        f'<div class="ds-rail-brand">'
        f'<div class="ds-brand-dot" style="background:{accent["accent"]};color:{accent["accent_ink"]};">{brand_glyph}</div>'
        f'<div class="ds-brand-wm">{brand_name}<span style="color:var(--text-muted);">{brand_tld}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )
    if version:
        st.sidebar.markdown(
            f'<div style="font-size:10px;color:var(--text-muted);letter-spacing:0.08em;'
            f'text-transform:uppercase;padding:0 10px 12px;">{version}</div>',
            unsafe_allow_html=True,
        )


# ── Sidebar renderer (full — brand + grouped nav + session state) ──────

def render_sidebar(
    *,
    app: str = "crypto-signal-app",
    active: str = "home",
    brand_name: str = "Signal",
    brand_tld: str = ".app",
    brand_glyph: str = "◈",
    user_level: Literal["beginner", "intermediate", "advanced"] = "beginner",
) -> str:
    """
    Render the brand header + grouped nav inside st.sidebar.

    Returns the active nav key, normalised via st.session_state['nav_key'] so
    downstream code can read it without re-computing.
    """
    if st is None:
        return active

    accent = ACCENTS[app]  # type: ignore[index]

    # The brand card — matches the mockup "◈ Signal.app" wordmark
    st.sidebar.markdown(
        f'<div class="ds-rail-brand">'
        f'<div class="ds-brand-dot" style="background:{accent["accent"]};color:{accent["accent_ink"]};">{brand_glyph}</div>'
        f'<div class="ds-brand-wm">{brand_name}<span style="color:var(--text-muted);">{brand_tld}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # Grouped nav rendered as clickable buttons — uses a radio under the hood
    # for selection state so Streamlit handles reruns cleanly, but visually
    # styled to look like the mockup.
    flat: list[tuple[str, str, str, str]] = []  # (group, key, label, icon)
    for group, items in DEFAULT_NAV.items():
        for k, lbl, ic in items:
            flat.append((group, k, lbl, ic))

    keys = [f[1] for f in flat]
    if active not in keys:
        active = keys[0]

    # Session default
    if "nav_key" not in st.session_state:
        st.session_state["nav_key"] = active

    # Render each group header + items. Use st.button for nav items so
    # Streamlit reruns on click; visual look comes from overrides.py.
    for group, items in DEFAULT_NAV.items():
        st.sidebar.markdown(
            f'<div class="ds-nav-group">{group}</div>',
            unsafe_allow_html=True,
        )
        for k, lbl, ic in items:
            is_active = (st.session_state.get("nav_key") == k)
            btn_class = "ds-nav-item active" if is_active else "ds-nav-item"
            # Streamlit doesn't let us style a button by class directly; we tag
            # the container via a wrapper markdown + button — the overrides
            # target the first-child button inside `[data-testid="stSidebar"]
            # div:has(> .ds-nav-marker.<key>)`.
            st.sidebar.markdown(
                f'<div class="ds-nav-marker {btn_class}" data-nav-key="{k}">'
                f'<span class="ds-nav-dot"></span>'
                f'<span class="ds-nav-icon">{ic}</span>'
                f'<span class="ds-nav-lbl">{lbl}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if st.sidebar.button(
                lbl,
                key=f"ds_nav_{k}",
                use_container_width=True,
                type=("primary" if is_active else "secondary"),
            ):
                st.session_state["nav_key"] = k
                st.session_state["_nav_target"] = PAGE_KEY_TO_APP.get(k, "Dashboard")
                st.rerun()

    return st.session_state.get("nav_key", active)


# ── Top bar ───────────────────────────────────────────────────────────

def render_top_bar(
    *,
    breadcrumb: Sequence[str] = ("Markets", "Home"),
    user_level: Literal["beginner", "intermediate", "advanced"] = "beginner",
    show_level: bool = True,
    show_refresh: bool = True,
    show_theme: bool = True,
) -> None:
    """
    Render the top bar: breadcrumb + level pills + refresh + theme. Renders
    into the main column (must be called BEFORE any other page markdown).
    """
    if st is None:
        return

    *rest, last = list(breadcrumb) or ["", ""]
    crumb_html = " / ".join(rest) + (" / " if rest else "") + f"<b>{last}</b>"

    level_html = ""
    if show_level:
        lvls = [("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")]
        buttons = "".join(
            f'<button class="{"on" if user_level == k else ""}" data-level="{k}">{lbl}</button>'
            for k, lbl in lvls
        )
        level_html = f'<div class="ds-level-group">{buttons}</div>'

    refresh_html = '<button class="ds-chip-btn" data-action="refresh">↻ Refresh</button>' if show_refresh else ""
    theme_html   = '<button class="ds-chip-btn" data-action="theme">☾ Theme</button>' if show_theme else ""

    st.markdown(
        f'<div class="ds-topbar">'
        f'<div class="ds-crumbs">{crumb_html}</div>'
        f'<div class="ds-topbar-spacer"></div>'
        f'{level_html}{refresh_html}{theme_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Page header ───────────────────────────────────────────────────────

def page_header(
    title: str,
    subtitle: str = "",
    *,
    data_sources: Iterable[tuple[str, str]] | None = None,
) -> None:
    """
    Render the page-hd block seen on every mockup: title + subtitle on the
    left, data-source pills on the right.

    data_sources: iterable of (label, status). status ∈ {live, cached, down}.
    """
    if st is None:
        return

    if data_sources:
        pills = []
        for label, status in data_sources:
            cls = "ds-pill"
            if status == "cached":
                cls += " warn"
            elif status == "down":
                cls += " down"
            pills.append(f'<span class="{cls}"><span class="tick"></span> {label} · {status}</span>')
        pills_html = f'<div class="ds-row">{"".join(pills)}</div>'
    else:
        pills_html = ""

    sub_html = f'<div class="ds-page-sub">{subtitle}</div>' if subtitle else ""

    st.markdown(
        f'<div class="ds-page-hd">'
        f'<div>'
        f'<h1 class="ds-page-title">{title}</h1>'
        f'{sub_html}'
        f'</div>'
        f'{pills_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Macro strip ───────────────────────────────────────────────────────

def macro_strip(items: Sequence[tuple[str, str, str]]) -> None:
    """
    Render the 5-col macro strip from the Home mockup.
    Each item: (label, value, sub). sub may contain a leading "+" / "−".
    """
    if st is None:
        return

    cells = []
    for label, value, sub in items:
        cells.append(
            f'<div><div class="lbl">{label}</div>'
            f'<div class="val">{value}</div>'
            f'<div class="sub">{sub}</div></div>'
        )
    st.markdown(
        f'<div class="ds-card ds-strip">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


# ── Hero signal cards ─────────────────────────────────────────────────

def hero_signal_card_html(
    ticker: str,
    price: float | None,
    change_pct: float | None,
    signal: Literal["BUY", "HOLD", "SELL", None] = None,
    regime_label: str = "",
    regime_confidence: float | None = None,
) -> str:
    """Return HTML for a single hero signal card (matches Home mockup)."""
    # Format price
    if price is None:
        price_str = "—"
    elif price >= 1000:
        price_str = f"{price:,.0f}"
    elif price >= 10:
        price_str = f"{price:,.2f}"
    else:
        price_str = f"{price:,.4f}"

    # Format change
    if change_pct is None:
        change_cls = ""
        change_str = "—"
    elif change_pct > 0:
        change_cls = "up"
        change_str = f"+ {change_pct:.2f}% · 24h"
    elif change_pct < 0:
        change_cls = "down"
        change_str = f"− {abs(change_pct):.2f}% · 24h"
    else:
        change_cls = ""
        change_str = "0.00% · 24h"

    # Signal badge (shape + color — matches mockup + CLAUDE.md §8 color-blind rule)
    badge_html = ""
    if signal in ("BUY", "HOLD", "SELL"):
        shape, css_class, label = {
            "BUY":  ("▲", "ds-sb-buy",  "Buy"),
            "HOLD": ("■", "ds-sb-hold", "Hold"),
            "SELL": ("▼", "ds-sb-sell", "Sell"),
        }[signal]
        badge_html = f'<span class="ds-signal-badge {css_class}">{shape} {label}</span>'

    # Regime line
    regime_html = ""
    if regime_label:
        conf_txt = f" · {int(regime_confidence)}% conf" if regime_confidence is not None else ""
        regime_html = (
            f'<div class="ds-regime"><span class="dot"></span> '
            f'Regime: {regime_label}{conf_txt}</div>'
        )

    # Single-line to avoid Streamlit markdown's 4-space = code-block rule.
    return (
        f'<div class="ds-card ds-signal-hero">'
        f'<div class="ds-signal-lhs">'
        f'<div class="ds-signal-ticker">{ticker}</div>'
        f'<div class="ds-signal-big">{price_str}</div>'
        f'<div class="ds-signal-change {change_cls}">{change_str}</div>'
        f'</div>'
        f'<div class="ds-signal-rhs">'
        f'{badge_html}{regime_html}'
        f'</div>'
        f'</div>'
    )


def hero_signal_cards_row(cards: Sequence[dict]) -> None:
    """
    Render a 3-col row of hero signal cards.
    Each card dict keys: ticker, price, change_pct, signal, regime_label, regime_confidence.
    """
    if st is None:
        return
    html = "".join(
        hero_signal_card_html(
            ticker=c.get("ticker", "—"),
            price=c.get("price"),
            change_pct=c.get("change_pct"),
            signal=c.get("signal"),
            regime_label=c.get("regime_label", ""),
            regime_confidence=c.get("regime_confidence"),
        )
        for c in cards
    )
    st.markdown(
        f'<div class="ds-hero-grid">{html}</div>',
        unsafe_allow_html=True,
    )


# ── Watchlist ─────────────────────────────────────────────────────────

def watchlist_card(
    title: str,
    subtitle: str,
    rows: Sequence[dict],
) -> None:
    """Render the 2-col watchlist card from the Home mockup.
    Each row dict: ticker, price, change_pct, spark_points (list of (x, y) tuples).
    """
    if st is None:
        return
    row_html = []
    for r in rows:
        ticker = r.get("ticker", "—")
        price = r.get("price")
        change = r.get("change_pct")
        if price is None:
            price_str = "—"
        elif price >= 1000:
            price_str = f"${price:,.0f}"
        elif price >= 10:
            price_str = f"${price:,.2f}"
        else:
            price_str = f"${price:,.4f}"
        if change is None:
            change_cls, change_str = "", "—"
        elif change > 0:
            change_cls, change_str = "up", f"+{change:.2f}%"
        elif change < 0:
            change_cls, change_str = "down", f"−{abs(change):.2f}%"
        else:
            change_cls, change_str = "", "0.00%"
        spark_points = r.get("spark_points") or []
        if spark_points:
            stroke = "#22c55e" if (change is not None and change >= 0) else "#ef4444"
            pts = " ".join(f"{x},{y}" for x, y in spark_points)
            spark = (
                f'<svg class="ds-spark" viewBox="0 0 80 22" preserveAspectRatio="none">'
                f'<polyline fill="none" stroke="{stroke}" stroke-width="1.5" points="{pts}"/>'
                f"</svg>"
            )
        else:
            spark = '<svg class="ds-spark" viewBox="0 0 80 22"></svg>'
        row_html.append(
            f'<div class="ds-wl-row">'
            f'<div class="t">{ticker}</div>'
            f'<div class="p">{price_str}</div>'
            f'<div class="d {change_cls}">{change_str}</div>'
            f"{spark}"
            f"</div>"
        )
    st.markdown(
        f'<div class="ds-card">'
        f'<div class="ds-card-hd">'
        f'<div class="ds-card-title">{title}</div>'
        f'<div class="ds-card-sub">{subtitle}</div>'
        f'</div>'
        f'<div class="ds-watchlist">{"".join(row_html)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Backtest preview card (4-KPI grid) ────────────────────────────────

def backtest_preview_card(
    title: str,
    subtitle: str,
    kpis: Sequence[tuple[str, str, str, str]],
) -> None:
    """Render the 2×2 KPI grid shown next to the Watchlist on Home.
    Each kpi: (label, value, delta_text, delta_direction ∈ {up, down, ""}).
    """
    if st is None:
        return
    cells = []
    for label, value, delta_text, direction in kpis:
        dc = f" {direction}" if direction in ("up", "down") else ""
        val_color = ""
        if direction == "up":
            val_color = ' style="color: var(--success);"'
        elif direction == "down":
            val_color = ' style="color: var(--danger);"'
        cells.append(
            f'<div class="ds-kpi">'
            f'<div class="ds-kpi-label">{label}</div>'
            f'<div class="ds-kpi-value"{val_color}>{value}</div>'
            f'<div class="ds-kpi-delta{dc}">{delta_text}</div>'
            f"</div>"
        )
    st.markdown(
        f'<div class="ds-card">'
        f'<div class="ds-card-hd">'
        f'<div class="ds-card-title">{title}</div>'
        f'<div class="ds-card-sub">{subtitle}</div>'
        f'</div>'
        f'<div class="ds-kpi-grid">{"".join(cells)}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


# ── Regime card ───────────────────────────────────────────────────────

REGIME_VARIANT = {
    "bull":         "bull",
    "bullish":      "bull",
    "bear":         "bear",
    "bearish":      "bear",
    "transition":   "trans",
    "trans":        "trans",
    "accumulation": "accum",
    "accum":        "accum",
    "distribution": "dist",
    "dist":         "dist",
}


def regime_card_html(
    ticker: str,
    state: str,
    confidence: float | None = None,
    since: str = "",
) -> str:
    """Return HTML for a single regime card. state maps to bull/bear/trans/accum/dist."""
    variant = REGIME_VARIANT.get(str(state).strip().lower(), "trans")
    conf = f"confidence {int(confidence)}%" if confidence is not None else ""
    since_html = f'<div class="since">{since}</div>' if since else ""
    return (
        f'<div class="ds-card ds-rgm {variant}">'
        f'<div class="t">{ticker}</div>'
        f'<div class="state">{state}</div>'
        f'<div class="conf">{conf}</div>'
        f'{since_html}'
        f'</div>'
    )


def regime_cards_grid(cards: Sequence[dict], cols: int = 4) -> None:
    """Render a grid of regime cards.
    Each card dict: ticker, state, confidence, since.
    """
    if st is None:
        return
    html = "".join(
        regime_card_html(
            ticker=c.get("ticker", "—"),
            state=c.get("state", "Transition"),
            confidence=c.get("confidence"),
            since=c.get("since", ""),
        )
        for c in cards
    )
    st.markdown(
        f'<div class="ds-grid ds-cols-{cols}">{html}</div>',
        unsafe_allow_html=True,
    )

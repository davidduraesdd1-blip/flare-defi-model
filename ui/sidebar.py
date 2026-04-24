"""
ui/sidebar.py — Shared sibling-family left rail, top bar, and page header
for rwa-infinity-model.

Mirrors the static mockups in
shared-docs/design-mockups/sibling-family-rwa-infinity-*.html.

Every page should:

    from ui import inject_theme, inject_streamlit_overrides, render_top_bar, page_header

    inject_theme("rwa-infinity-model", theme=st.session_state.get("theme", "dark"))
    inject_streamlit_overrides()
    # existing sidebar stays; the new brand block is inserted separately in app.py
    render_top_bar(breadcrumb=("Markets", "Dashboard"), user_level=...)
    page_header(title="...", subtitle="...", data_sources=[...])
"""
from __future__ import annotations

from typing import Iterable, Literal, Sequence

try:
    import streamlit as st
except ImportError:
    st = None  # type: ignore

from .design_system import ACCENTS


NavItem = tuple[str, str, str]

DEFAULT_NAV: dict[str, list[NavItem]] = {
    "Markets": [
        ("dashboard", "Dashboard",  "◉"),
        ("holdings",  "Holdings",   "∎"),
        ("macro",     "Macro feeds", "⬡"),
    ],
    "Research": [
        ("montecarlo", "Monte Carlo", "∿"),
        ("yields",     "Yields",     "▲"),
    ],
    "Account": [
        ("alerts",   "Alerts",   "◐"),
        ("settings", "Settings", "⚙"),
    ],
}


def render_sidebar_brand(
    *,
    app: str = "rwa-infinity-model",
    brand_name: str = "Infinity",
    brand_tld: str = ".rwa",
    brand_glyph: str = "♾",
    version: str = "",
) -> None:
    """Render the sibling-family brand block at the top of the Streamlit sidebar."""
    if st is None:
        return
    accent = ACCENTS[app]  # type: ignore[index]
    sub = f'<div style="font-size:10px;color:var(--text-muted);letter-spacing:0.08em;text-transform:uppercase;padding:0 10px 12px;">{version}</div>' if version else ""
    st.sidebar.markdown(
        f"""
        <div class="ds-rail-brand">
          <div class="ds-brand-dot" style="background:{accent['accent']};color:{accent['accent_ink']};">
            {brand_glyph}
          </div>
          <div class="ds-brand-wm">
            {brand_name}<span style="color:var(--text-muted);">{brand_tld}</span>
          </div>
        </div>
        {sub}
        """,
        unsafe_allow_html=True,
    )


def render_top_bar(
    *,
    breadcrumb: Sequence[str] = ("Markets", "Dashboard"),
    user_level: Literal["beginner", "intermediate", "advanced"] = "beginner",
    show_level: bool = True,
    show_refresh: bool = True,
    show_theme: bool = True,
) -> None:
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
        f"""
        <div class="ds-topbar">
          <div class="ds-crumbs">{crumb_html}</div>
          <div class="ds-topbar-spacer"></div>
          {level_html}
          {refresh_html}
          {theme_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_header(
    title: str,
    subtitle: str = "",
    *,
    data_sources: Iterable[tuple[str, str]] | None = None,
) -> None:
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
        f"""
        <div class="ds-page-hd">
          <div>
            <h1 class="ds-page-title">{title}</h1>
            {sub_html}
          </div>
          {pills_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def macro_strip(items: Sequence[tuple[str, str, str]]) -> None:
    if st is None:
        return
    cells = [
        f'<div><div class="lbl">{l}</div><div class="val">{v}</div><div class="sub">{s}</div></div>'
        for l, v, s in items
    ]
    st.markdown(
        f'<div class="ds-card ds-strip">{"".join(cells)}</div>',
        unsafe_allow_html=True,
    )


# Kept for symmetry with crypto-signal-app — rwa app keeps its existing tab
# navigation, we only inject the brand block via render_sidebar_brand.
def render_sidebar(**kwargs):
    render_sidebar_brand(**kwargs)

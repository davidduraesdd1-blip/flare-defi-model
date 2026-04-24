"""
ui/overrides.py — Streamlit widget CSS overrides that shadow default Streamlit
styling so existing pages inherit the mockup's look without structural changes.

All selectors target Streamlit's stable data-testid hooks. Call
inject_streamlit_overrides() once per page, after inject_theme().
"""
from __future__ import annotations


def inject_streamlit_overrides() -> None:
    try:
        import streamlit as st
    except ImportError:  # pragma: no cover
        return

    css = """
    /* ─── Sibling-family design-system shell overrides ─── */

    /* Main content column */
    section.main > div.block-container {
      padding-top: 16px;
      padding-bottom: 80px;
      max-width: none;
    }

    /* Sidebar canvas */
    [data-testid="stSidebar"] {
      background: var(--bg-1) !important;
      border-right: 1px solid var(--border) !important;
      min-width: var(--rail-w) !important;
      max-width: calc(var(--rail-w) + 24px) !important;
    }
    [data-testid="stSidebar"] > div:first-child {
      padding: 16px 12px !important;
      background: var(--bg-1) !important;
    }

    /* Brand block */
    .ds-rail-brand {
      display: flex; align-items: center; gap: 10px;
      padding: 6px 10px 20px;
      font-weight: 600; font-size: 15px; letter-spacing: -0.01em;
      color: var(--text-primary);
    }
    .ds-brand-dot {
      width: 22px; height: 22px; border-radius: 6px;
      display: grid; place-items: center;
      font-weight: 700; font-size: 12px;
    }
    .ds-brand-wm { color: var(--text-primary); }

    /* Nav group header */
    .ds-nav-group {
      margin: 14px 0 4px; padding: 0 10px;
      color: var(--text-muted); font-size: 11px; font-weight: 500;
      letter-spacing: 0.08em; text-transform: uppercase;
    }

    /* Nav item (we render markdown + button, markdown holds the visual, button
       catches clicks; we hide the button's default chrome and overlay it on
       top of the marker) */
    .ds-nav-marker {
      display: flex; align-items: center; gap: 10px;
      padding: 8px 10px; border-radius: 8px;
      color: var(--text-secondary); font-size: 13.5px; font-weight: 500;
      cursor: pointer; user-select: none;
      transition: background 120ms, color 120ms;
      margin-bottom: -40px;  /* pull the following stButton up over the marker */
      position: relative; z-index: 1;
    }
    .ds-nav-marker:hover { background: var(--bg-2); color: var(--text-primary); }
    .ds-nav-marker.active {
      background: var(--accent-soft);
      color: var(--text-primary);
    }
    .ds-nav-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--accent); opacity: 0; }
    .ds-nav-marker.active .ds-nav-dot { opacity: 1; }
    .ds-nav-icon { opacity: 0.8; width: 16px; display: inline-block; text-align: center; }

    /* Hide the underlying sidebar buttons but keep them clickable over the
       marker; they inherit the marker's visual footprint */
    [data-testid="stSidebar"] [data-testid="stButton"] > button {
      opacity: 0;
      height: 34px;
      margin-top: 0;
      position: relative; z-index: 2;
      background: transparent !important;
      border: none !important;
      box-shadow: none !important;
    }
    [data-testid="stSidebar"] [data-testid="stButton"] {
      margin-top: 0 !important;
    }

    /* Top bar */
    .ds-topbar {
      background: var(--bg-0);
      border-bottom: 1px solid var(--border);
      display: flex; align-items: center; gap: 12px;
      padding: 10px 4px 14px 4px;
      margin: -8px 0 16px 0;
    }
    .ds-crumbs { color: var(--text-muted); font-size: 13px; }
    .ds-crumbs b { color: var(--text-primary); font-weight: 500; }
    .ds-topbar-spacer { flex: 1; }
    .ds-level-group {
      display: inline-flex; align-items: center; gap: 0;
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: 8px; padding: 2px;
    }
    .ds-level-group button {
      all: unset; cursor: pointer;
      padding: 4px 10px; border-radius: 6px; font-size: 12.5px;
      color: var(--text-muted); font-weight: 500;
      font-family: var(--font-ui);
    }
    .ds-level-group button.on {
      background: var(--accent-soft); color: var(--text-primary);
    }
    .ds-chip-btn {
      all: unset; cursor: pointer;
      display: inline-flex; align-items: center; gap: 6px;
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: 8px; padding: 6px 10px; font-size: 13px;
      color: var(--text-secondary); font-family: var(--font-ui);
    }
    .ds-chip-btn:hover { border-color: var(--border-strong); color: var(--text-primary); }

    /* Page header */
    .ds-page-hd {
      display: flex; justify-content: space-between; align-items: flex-end;
      gap: 16px; margin: 0 0 20px 0; flex-wrap: wrap;
    }
    .ds-page-title { margin: 0; font-size: 22px; font-weight: 600;
      letter-spacing: -0.01em; color: var(--text-primary); }
    .ds-page-sub { color: var(--text-muted); font-size: 13.5px; margin-top: 4px; }

    /* Data-source pills */
    .ds-row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
    .ds-pill {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 11.5px; padding: 3px 8px; border-radius: 999px;
      background: var(--bg-2); color: var(--text-secondary);
      border: 1px solid var(--border);
    }
    .ds-pill .tick { width: 6px; height: 6px; border-radius: 50%; background: var(--success); }
    .ds-pill.warn .tick { background: var(--warning); }
    .ds-pill.down .tick { background: var(--danger); }

    /* Card primitive + variants */
    .ds-card {
      background: var(--bg-1);
      border: 1px solid var(--border);
      border-radius: var(--card-radius);
      padding: var(--card-pad);
    }
    .ds-strip {
      display: grid; grid-template-columns: repeat(5, 1fr); gap: 0; padding: 0;
    }
    .ds-strip > div { padding: 12px 14px; border-right: 1px solid var(--border); }
    .ds-strip > div:last-child { border-right: none; }
    .ds-strip .lbl { font-size: 10.5px; color: var(--text-muted);
      text-transform: uppercase; letter-spacing: 0.05em; }
    .ds-strip .val { font-size: 17px; font-family: var(--font-mono);
      font-weight: 600; margin-top: 2px; color: var(--text-primary); }
    .ds-strip .sub { font-size: 11.5px; color: var(--text-muted);
      margin-top: 2px; font-family: var(--font-mono); }

    /* Card headers (shared) */
    .ds-card-hd {
      display: flex; justify-content: space-between; align-items: baseline;
      margin-bottom: 10px;
    }
    .ds-card-title { font-size: 12px; color: var(--text-muted); font-weight: 500;
      letter-spacing: 0.04em; text-transform: uppercase; }
    .ds-card-sub { font-size: 11.5px; color: var(--text-muted); }

    /* Restyle Streamlit native widgets so in-page content inherits the look */
    .stMarkdown, .stMarkdown p, .stMarkdown li { color: var(--text-primary); }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMetric"] {
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: var(--card-radius); padding: 14px var(--card-pad);
    }
    [data-testid="stMetricLabel"] {
      color: var(--text-muted) !important;
      font-size: 11px !important; text-transform: uppercase;
      letter-spacing: 0.06em; font-weight: 500;
    }
    [data-testid="stMetricValue"] {
      font-family: var(--font-mono);
      font-size: 22px !important; font-weight: 600 !important;
      color: var(--text-primary) !important;
      line-height: 1.1;
    }
    [data-testid="stMetricDelta"] {
      font-family: var(--font-mono); font-size: 12px !important;
    }

    /* Primary buttons — outside the sidebar */
    section.main [data-testid="stButton"] > button {
      background: var(--bg-1); color: var(--text-primary);
      border: 1px solid var(--border); border-radius: 8px;
      font-weight: 500; padding: 6px 14px;
      transition: background 120ms, border-color 120ms;
    }
    section.main [data-testid="stButton"] > button:hover {
      border-color: var(--border-strong); background: var(--bg-2);
    }
    section.main [data-testid="stButton"] > button[kind="primary"] {
      background: var(--accent); color: var(--accent-ink);
      border-color: var(--accent);
    }

    /* Inputs */
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stSelectbox"] [data-baseweb="select"] > div,
    [data-testid="stMultiSelect"] [data-baseweb="select"] > div {
      background: var(--bg-1) !important;
      color: var(--text-primary) !important;
      border-color: var(--border) !important;
    }

    /* Expanders */
    [data-testid="stExpander"] {
      background: var(--bg-1); border: 1px solid var(--border);
      border-radius: var(--card-radius);
    }
    [data-testid="stExpander"] summary { color: var(--text-primary); }

    /* Tabs */
    [data-testid="stTabs"] [data-baseweb="tab-list"] {
      gap: 4px; border-bottom: 1px solid var(--border);
    }
    [data-testid="stTabs"] button[role="tab"] {
      background: transparent; color: var(--text-muted);
      border-radius: 6px 6px 0 0; padding: 8px 14px;
      font-weight: 500;
    }
    [data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
      color: var(--text-primary);
      border-bottom: 2px solid var(--accent);
    }

    /* Dataframes */
    [data-testid="stDataFrame"] {
      border: 1px solid var(--border); border-radius: var(--card-radius);
      overflow: hidden;
    }

    /* Radios (sidebar nav alternative) — we hide native radio visuals inside sidebar
       but keep them functional for fallback callers */
    [data-testid="stSidebar"] [data-testid="stRadio"] > label { display: none; }

    /* Mobile */
    @media (max-width: 768px) {
      [data-testid="stSidebar"] { min-width: 100% !important; max-width: 100% !important; }
      section.main > div.block-container { padding-top: 12px; padding-bottom: 48px; }
      .ds-strip { grid-template-columns: repeat(2, 1fr) !important; }
      .ds-strip > div { border-right: none; border-bottom: 1px solid var(--border); }
      .ds-strip > div:last-child { border-bottom: none; }
      .ds-page-hd { flex-direction: column; align-items: flex-start; }
      .ds-level-group { display: none; }
    }
    """

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)

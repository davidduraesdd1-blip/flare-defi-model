"""
Settings — Alert configuration (email & Telegram) and PDF/HTML report export.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import html as _html
import streamlit as st
from datetime import datetime

from ui.common import page_setup, render_sidebar, load_latest
from config import RISK_PROFILES, INCENTIVE_PROGRAM

page_setup("Settings · Flare DeFi")

render_sidebar()

st.markdown("# Settings")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Alert notifications · report export</div>",
    unsafe_allow_html=True,
)


# ─── Alert Configuration ──────────────────────────────────────────────────────

st.markdown("### Alert Notifications")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
    "Get notified by email or Telegram when high-APY opportunities appear.</div>",
    unsafe_allow_html=True,
)

try:
    from ai.alerts import load_alerts_config, save_alerts_config, test_email, test_telegram
except ImportError:
    st.error("ai/alerts.py not found. Check your installation.")
    st.stop()

config = load_alerts_config()

tab_email, tab_tg, tab_thresh = st.tabs(["📧  Email", "📱  Telegram", "⚙️  Thresholds"])

with tab_email:
    st.markdown("<div style='color:#475569; font-size:0.82rem; margin-bottom:12px;'>Gmail users: use an App Password (Settings → Security → App Passwords).</div>", unsafe_allow_html=True)
    enabled    = st.toggle("Enable email alerts", value=config["email"].get("enabled", False), key="email_enabled")
    email_addr = st.text_input("Email address",  value=config["email"].get("address", ""),          key="email_addr")
    c1, c2     = st.columns(2)
    with c1:
        smtp_srv  = st.text_input("SMTP server", value=config["email"].get("smtp_server", "smtp.gmail.com"), key="smtp_srv")
        smtp_user = st.text_input("SMTP username", value=config["email"].get("username", ""),        key="smtp_user")
    with c2:
        smtp_port = st.number_input("SMTP port", value=int(config["email"].get("smtp_port", 587)),
                                    min_value=1, max_value=65535, key="smtp_port")
        smtp_pass = st.text_input("SMTP password", value=config["email"].get("password", ""),
                                   key="smtp_pass", type="password")
    st.markdown(
        "<div class='warn-box' style='font-size:0.82rem;'>"
        "Credentials stored in <code>data/alerts_config.json</code> — never commit this file to git.</div>",
        unsafe_allow_html=True,
    )

with tab_tg:
    st.markdown("<div style='color:#475569; font-size:0.82rem; margin-bottom:12px;'>Create a bot via @BotFather · Get your Chat ID via @userinfobot.</div>", unsafe_allow_html=True)
    tg_enabled = st.toggle("Enable Telegram alerts", value=config["telegram"].get("enabled", False), key="tg_enabled")
    bot_token  = st.text_input("Bot token", value=config["telegram"].get("bot_token", ""),
                                key="bot_token", type="password")
    chat_id    = st.text_input("Chat ID",   value=config["telegram"].get("chat_id", ""), key="chat_id")

with tab_thresh:
    st.markdown("<div style='color:#475569; font-size:0.82rem; margin-bottom:12px;'>Alerts are only sent when these thresholds are crossed.</div>", unsafe_allow_html=True)
    min_apy   = st.slider("Alert when any APY exceeds (%)", 50, 300,
                          int(config["thresholds"].get("min_apy_alert", 150)), 10, key="min_apy_thresh")
    arb_alert = st.toggle("Alert on ACT NOW arbitrage opportunities",
                           value=config["thresholds"].get("new_arb_alert", True), key="arb_alert_cb")

st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
col_save, col_test_e, col_test_t = st.columns(3)

with col_save:
    if st.button("Save Settings", key="save_alerts", use_container_width=True):
        new_config = {
            "email":    {"enabled": enabled, "address": email_addr, "smtp_server": smtp_srv,
                         "smtp_port": int(smtp_port), "username": smtp_user, "password": smtp_pass},
            "telegram": {"enabled": tg_enabled, "bot_token": bot_token, "chat_id": chat_id},
            "thresholds": {"min_apy_alert": min_apy, "new_arb_alert": arb_alert},
        }
        save_alerts_config(new_config)
        st.success("Settings saved.")

with col_test_e:
    if st.button("Send Test Email", key="test_email_btn", use_container_width=True):
        # Use current form values — not stale disk config — so unsaved changes are tested
        _test_cfg = {"email": {"enabled": enabled, "address": email_addr,
                               "smtp_server": smtp_srv, "smtp_port": int(smtp_port),
                               "username": smtp_user, "password": smtp_pass}}
        ok, msg = test_email(_test_cfg)
        st.success(msg) if ok else st.error(msg)

with col_test_t:
    if st.button("Send Test Telegram", key="test_tg_btn", use_container_width=True):
        # Use current form values — not stale disk config — so unsaved changes are tested
        _test_cfg = {"telegram": {"enabled": tg_enabled, "bot_token": bot_token, "chat_id": chat_id}}
        ok, msg = test_telegram(_test_cfg)
        st.success(msg) if ok else st.error(msg)

st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


# ─── Report Export ────────────────────────────────────────────────────────────

st.markdown("### Export Report")
st.markdown(
    "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
    "Download a printable HTML report. Open in Chrome/Edge → Ctrl+P → Save as PDF.</div>",
    unsafe_allow_html=True,
)

latest = load_latest()

if not latest:
    st.info("Run a scan first to generate a report.")
else:
    from ui.common import risk_score_to_grade, _ts_fmt

    col_profile, col_size = st.columns(2)
    with col_profile:
        report_profile = st.selectbox("Risk profile for report",
                                      ["conservative", "medium", "high"],
                                      format_func=lambda p: RISK_PROFILES[p]["label"],
                                      key="report_profile")
    with col_size:
        report_portfolio = st.number_input("Portfolio size for report ($)",
                                           min_value=0.0, value=10000.0, step=1000.0,
                                           key="report_portfolio")

    opps        = latest.get("models", {}).get(report_profile, [])
    profile_cfg = RISK_PROFILES[report_profile]
    ts          = latest.get("completed_at", datetime.utcnow().isoformat())

    rows_html = ""
    for opp in opps[:6]:
        apy       = opp.get("estimated_apy", 0)
        kf        = opp.get("kelly_fraction", 0)
        grade, _  = risk_score_to_grade(opp.get("risk_score", 5))
        alloc_str = f"${kf * report_portfolio:,.0f}" if report_portfolio > 0 else f"{kf*100:.0f}%"
        proto     = _html.escape(str(opp.get("protocol", "—")))
        pool      = _html.escape(str(opp.get("asset_or_pool", "—")))
        action    = _html.escape(str(opp.get("action", "—")))
        rank      = _html.escape(str(opp.get("rank", "—")))
        rows_html += f"""
        <tr>
            <td>{rank}</td>
            <td>{proto}</td>
            <td>{pool}</td>
            <td>{apy:.1f}%</td>
            <td>{opp.get('apy_low', apy*0.8):.1f}%–{opp.get('apy_high', apy*1.2):.1f}%</td>
            <td><b>{grade}</b></td>
            <td>{alloc_str}</td>
            <td style="font-size:0.85rem;">{action}</td>
        </tr>"""

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Flare DeFi Report — {_ts_fmt(ts)}</title>
<style>
  body  {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
           padding: 40px; color: #1e293b; max-width: 1000px; margin: auto; }}
  h1    {{ color: #0f172a; border-bottom: 2px solid #3b82f6; padding-bottom: 10px; font-size: 1.6rem; }}
  h2    {{ color: #0f172a; margin-top: 32px; font-size: 1.1rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
  th    {{ background: #1e293b; color: #f8fafc; padding: 10px 12px; text-align: left; font-size: 0.82rem; }}
  td    {{ padding: 9px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; font-size: 0.88rem; }}
  tr:nth-child(even) {{ background: #f8fafc; }}
  .warn {{ background: #fefce8; padding: 14px 16px; border-radius: 8px; margin: 16px 0;
           border-left: 4px solid #f59e0b; font-size: 0.9rem; }}
  .meta {{ color: #64748b; font-size: 0.88rem; margin-bottom: 24px; }}
  .footer {{ margin-top: 40px; color: #94a3b8; font-size: 0.78rem;
             border-top: 1px solid #e2e8f0; padding-top: 16px; }}
  @media print {{ body {{ padding: 20px; }} }}
</style>
</head>
<body>
<h1>⚡ Flare DeFi Opportunities Report</h1>
<p class="meta">
  Profile: <b>{profile_cfg['label']}</b> &nbsp;·&nbsp;
  Generated: <b>{_ts_fmt(ts)}</b> &nbsp;·&nbsp;
  Portfolio: <b>${report_portfolio:,.0f}</b>
</p>
<div class="warn">⚠️ {INCENTIVE_PROGRAM['note']}</div>
<h2>Top Opportunities</h2>
<table>
<tr><th>#</th><th>Protocol</th><th>Pool / Asset</th><th>Est. APY</th>
    <th>APY Range</th><th>Grade</th><th>Allocation</th><th>Action</th></tr>
{rows_html}
</table>
<h2>How to Read This Report</h2>
<ul>
  <li><b>Est. APY</b> — Model's central estimate. Actual results will vary.</li>
  <li><b>APY Range</b> — Conservative ±20% scenario band.</li>
  <li><b>Grade A–F</b> — A = very safe (lending/staking). F = high risk (leveraged/perps).</li>
  <li><b>Allocation</b> — Kelly Criterion position sizing. Never concentrate 100% in one strategy.</li>
</ul>
<div class="footer">
  Flare DeFi Model · Data from Blazeswap, SparkDEX, Ēnosys, Kinetic, Clearpool,
  Spectra, Upshift, Mystic, Hyperliquid, Cyclo, Sceptre, Firelight<br>
  <b>Not financial advice. Always do your own research before investing.</b>
</div>
</body>
</html>"""

    fname = f"flare_defi_report_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.html"
    st.download_button(
        label="Download Report (HTML → Print → Save as PDF)",
        data=html_content,
        file_name=fname,
        mime="text/html",
        key="pdf_export_btn",
        use_container_width=True,
    )

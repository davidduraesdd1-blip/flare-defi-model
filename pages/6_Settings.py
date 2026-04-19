"""
Settings — Alert configuration (email + generic webhook) and PDF/HTML report export.
"""

import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import csv
import html as _html
import io
import streamlit as st
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from ui.common import page_setup, render_sidebar, load_latest, load_history_runs, render_section_header, render_what_this_means
from config import RISK_PROFILES, INCENTIVE_PROGRAM

try:
    from agents.wallet_manager import WalletManager as _WalletManager
    _wallets = _WalletManager()
    _WALLETS_OK = True
except Exception:
    _wallets = None
    _WALLETS_OK = False

page_setup("Settings · Family Office · DeFi Intelligence")

render_sidebar()

st.markdown("# Control Center")
st.markdown(
    "<div style='color:#475569; font-size:0.88rem; margin-bottom:24px;'>"
    "Alert notifications · API key management · cache controls · report export · wallet setup</div>",
    unsafe_allow_html=True,
)

_ctrl_tab_alerts, _ctrl_tab_api, _ctrl_tab_cache, _ctrl_tab_export, _ctrl_tab_wallet = st.tabs([
    "🔔 Alerts", "🔑 API Keys", "🗄️ Cache", "📥 Export", "💼 Wallet Setup",
])

with _ctrl_tab_alerts:
    st.markdown("### Alert Notifications")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Get notified by email or generic HTTPS webhook when high-APY opportunities appear.</div>",
        unsafe_allow_html=True,
    )


with _ctrl_tab_alerts:
    try:
        from ai.alerts import (
            load_alerts_config, save_alerts_config,
            test_email, test_webhook,
        )
    except ImportError:
        st.error("Alert settings are currently unavailable — please contact support.")
        st.stop()

    config = load_alerts_config()

    tab_email, tab_webhook, tab_thresh = st.tabs([
        "📧  Email", "🔗  Webhook", "⚙️  Thresholds",
    ])

    with tab_email:
        st.markdown("<div style='color:#475569; font-size:0.85rem; margin-bottom:12px;'>Gmail users: use an App Password (Settings → Security → App Passwords).</div>", unsafe_allow_html=True)
        enabled    = st.toggle("Enable email alerts", value=config["email"].get("enabled", False), key="email_enabled")
        email_addr = st.text_input("Email address",  value=config["email"].get("address", ""),          key="email_addr")
        c1, c2     = st.columns(2)
        with c1:
            smtp_srv  = st.text_input("SMTP server", value=config["email"].get("smtp_server", "smtp.gmail.com"), key="smtp_srv")
            smtp_user = st.text_input("SMTP username", value=config["email"].get("username", ""),        key="smtp_user")
        with c2:
            try:
                _port_val = max(1, min(65535, int(config["email"].get("smtp_port", 587))))
            except (ValueError, TypeError):
                _port_val = 587
            smtp_port = st.number_input("SMTP port", value=_port_val,
                                        min_value=1, max_value=65535, key="smtp_port")
            # Audit R2f: never pre-fill a password into a text_input — even
            # type="password" ships the live value in the rendered DOM on
            # every rerun. Leave blank; on save, empty input means "keep
            # the stored value" (see save handler below).
            _smtp_pass_has_value = bool(config["email"].get("password"))
            smtp_pass = st.text_input(
                "SMTP password",
                value="",
                key="smtp_pass",
                type="password",
                placeholder="●●●● (saved)" if _smtp_pass_has_value else "",
                help="Leave blank to keep the currently saved password.",
            )
        st.markdown(
            "<div class='warn-box' style='font-size:0.85rem;'>"
            "Credentials stored in <code>data/alerts_config.json</code> — never commit this file to git.</div>",
            unsafe_allow_html=True,
        )

    with tab_webhook:
        st.markdown("<div style='color:#475569; font-size:0.85rem; margin-bottom:12px;'>Send JSON payloads to any HTTPS endpoint (Zapier, Make, n8n, Slack, custom API). Optionally sign with HMAC-SHA256.</div>", unsafe_allow_html=True)
        webhook_enabled = st.toggle("Enable webhook alerts", value=config.get("webhook", {}).get("enabled", False), key="webhook_enabled")
        webhook_url     = st.text_input("Webhook URL (HTTPS)", value=config.get("webhook", {}).get("url", ""),
                                         key="webhook_url", placeholder="https://hooks.zapier.com/…")
        # Audit R2f: don't re-emit the secret in the rendered DOM every
        # rerun. Blank field on load; empty submit keeps the stored value.
        _webhook_secret_has_value = bool(config.get("webhook", {}).get("secret"))
        webhook_secret  = st.text_input(
            "Signing secret (optional)",
            value="",
            key="webhook_secret",
            type="password",
            placeholder="●●●● (saved)" if _webhook_secret_has_value else "",
            help="Leave blank to keep the stored secret. If set, adds X-Flare-Signature HMAC-SHA256 header to each request.",
        )
        st.markdown(
            "<div class='warn-box' style='font-size:0.85rem;'>"
            "Secret stored in <code>data/alerts_config.json</code> — never commit this file.</div>",
            unsafe_allow_html=True,
        )
    with tab_thresh:
        st.markdown(
            "<div style='color:#475569; font-size:0.85rem; margin-bottom:12px;'>"
            "Alerts are only sent when these thresholds are crossed. "
            "Smart tuning auto-adjusts the APY threshold after each scan based on prediction accuracy.</div>",
            unsafe_allow_html=True,
        )
        try:
            _raw_apy_thresh = float(config["thresholds"].get("min_apy_alert", 150))
        except (TypeError, ValueError):
            _raw_apy_thresh = 150.0
        _apy_slider_val = int(round(_raw_apy_thresh / 10) * 10)   # round to nearest step=10, no truncation
        min_apy   = st.slider("Alert when any APY exceeds (%)", 50, 300,
                              max(50, min(300, _apy_slider_val)), 10, key="min_apy_thresh")
        arb_alert = st.toggle("Alert on ACT NOW arbitrage opportunities",
                               value=config["thresholds"].get("new_arb_alert", True), key="arb_alert_cb")

        # Upgrade #6: Smart Alert Tuning status display
        try:
            from ai.alerts import get_calibration_report, calibrate_alert_thresholds
            report = get_calibration_report()
            cal_at  = report.get("calibrated_at")
            samples = report.get("calibration_samples")
            p75     = report.get("raw_p75_apy")
            cal_html = ""
            if cal_at and samples:
                from ui.common import _ts_fmt
                _p75_str = f"p75 APY = {p75:.1f}%" if p75 is not None else "p75 APY = —"
                cal_html = (
                    f"<span style='color:#22c55e; font-weight:600;'>Active</span> · "
                    f"Last calibrated: {_ts_fmt(cal_at)} · "
                    f"{samples} samples · {_p75_str}"
                )
            else:
                cal_html = "<span style='color:#475569;'>Waiting for prediction history (need 6+ evaluated predictions)</span>"
            st.markdown(
                f"<div style='background:rgba(139,92,246,0.04); border:1px solid rgba(139,92,246,0.14); "
                f"border-radius:10px; padding:10px 14px; margin-top:10px; font-size:0.85rem; color:#94a3b8;'>"
                f"🤖 <span style='font-weight:600; color:#a78bfa;'>Smart Alert Tuning</span> — "
                f"{cal_html}</div>",
                unsafe_allow_html=True,
            )
            if st.button("Calibrate Now", key="calibrate_now_btn",
                         help="Run smart threshold calibration immediately using current prediction history"):
                result = calibrate_alert_thresholds()
                if result.get("calibrated"):
                    st.success(
                        f"Calibrated: {result['old_threshold']:.1f}% → {result['new_threshold']:.1f}% "
                        f"({result['direction']}, {result['samples']} samples)"
                    )
                else:
                    st.info(result.get("reason", "Not enough data yet."))
        except Exception as _cex:
            st.caption(f"Smart tuning status unavailable: {_cex}")

    st.markdown("<div style='margin-top:16px;'></div>", unsafe_allow_html=True)
    col_save, col_test_e, col_test_w = st.columns(3)

    with col_save:
        if st.button("Save Settings", key="save_alerts",
                         width='stretch'):
            # Audit R2f: preserve stored secrets when their input is blank.
            # Inputs are seeded empty to avoid DOM re-emission, so an empty
            # submit means "don't change" — NOT "clear the stored value".
            _new_pw     = smtp_pass if smtp_pass else config["email"].get("password", "")
            _new_secret = webhook_secret if webhook_secret else config.get("webhook", {}).get("secret", "")
            new_config = {
                "email":    {"enabled": enabled, "address": email_addr, "smtp_server": smtp_srv,
                             "smtp_port": int(smtp_port), "username": smtp_user, "password": _new_pw},
                "webhook":  {"enabled": webhook_enabled, "url": webhook_url, "secret": _new_secret},
                "thresholds": {"min_apy_alert": min_apy, "new_arb_alert": arb_alert},
            }
            save_alerts_config(new_config)
            st.success("Settings saved.")

    with col_test_e:
        if st.button("Send Test Email", key="test_email_btn",
                         width='stretch'):
            # Same blank-preserve rule for the test path.
            _test_pw = smtp_pass if smtp_pass else config["email"].get("password", "")
            _test_cfg = {"email": {"enabled": enabled, "address": email_addr,
                                   "smtp_server": smtp_srv, "smtp_port": int(smtp_port),
                                   "username": smtp_user, "password": _test_pw}}
            ok, msg = test_email(_test_cfg)
            st.success(msg) if ok else st.error(msg)

    with col_test_w:
        if st.button("Test Webhook", key="test_webhook_btn",
                         width='stretch'):
            # Audit R2f: blank-preserve for the signing secret.
            _test_secret = webhook_secret if webhook_secret else config.get("webhook", {}).get("secret", "")
            _test_cfg = {"webhook": {"enabled": webhook_enabled, "url": webhook_url, "secret": _test_secret}}
            ok, msg = test_webhook(_test_cfg)
            st.success(msg) if ok else st.error(msg)


with _ctrl_tab_export:
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

        opps        = (latest.get("models") or {}).get(report_profile) or []
        profile_cfg = RISK_PROFILES[report_profile]
        ts          = latest.get("completed_at", datetime.now(timezone.utc).isoformat())

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
      h1    {{ color: #0f172a; border-bottom: 2px solid #00d4aa; padding-bottom: 10px; font-size: 1.6rem; }}
      h2    {{ color: #0f172a; margin-top: 32px; font-size: 1.1rem; }}
      table {{ border-collapse: collapse; width: 100%; margin-top: 12px; }}
      th    {{ background: #1e293b; color: #f8fafc; padding: 10px 12px; text-align: left; font-size:0.85rem; }}
      td    {{ padding: 9px 12px; border-bottom: 1px solid #f1f5f9; vertical-align: top; font-size: 0.88rem; }}
      tr:nth-child(even) {{ background: #f8fafc; }}
      .warn {{ background: #fefce8; padding: 14px 16px; border-radius: 8px; margin: 16px 0;
               border-left: 4px solid #f59e0b; font-size: 0.9rem; }}
      .meta {{ color: #64748b; font-size: 0.88rem; margin-bottom: 24px; }}
      .footer {{ margin-top: 40px; color: #94a3b8; font-size:0.85rem;
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

        fname = f"flare_defi_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.html"
        st.download_button(
            label="Download Report (HTML → Print → Save as PDF)",
            data=html_content,
            file_name=fname,
            mime="text/html",
            key="pdf_export_btn",
            width='stretch',
        )

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)



with _ctrl_tab_export:
    # ─── Tax CSV Export (Feature 11) ──────────────────────────────────────────────

    st.markdown("### Tax Export")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Download a CSV of all scanned opportunities for tax records or external analysis. "
        "Includes estimated yield per position based on your portfolio size.</div>",
        unsafe_allow_html=True,
    )

    _tax_runs = load_history_runs()
    if not _tax_runs:
        st.info("No scan history yet. Run a scan first to generate a tax export.")
    else:
        _tax_col1, _tax_col2 = st.columns(2)
        with _tax_col1:
            _tax_portfolio = st.number_input(
                "Portfolio size for yield estimate ($)",
                min_value=0.0, value=10000.0, step=1000.0, key="tax_portfolio_size",
            )
        with _tax_col2:
            _tax_profiles = st.multiselect(
                "Include profiles",
                ["conservative", "medium", "high"],
                default=["conservative", "medium", "high"],
                format_func=lambda p: RISK_PROFILES[p]["label"],
                key="tax_profiles",
            )

        _tax_rows = []
        for _run in _tax_runs:
            _date_str = (_run.get("completed_at") or _run.get("run_id", ""))[:10]
            for _prof in (_tax_profiles or ["conservative", "medium", "high"]):
                for _opp in (_run.get("models") or {}).get(_prof, [])[:5]:
                    _apy = float(_opp.get("estimated_apy") or 0)
                    _ann_yield = round(_tax_portfolio * _apy / 100, 2) if _tax_portfolio > 0 else 0.0
                    _day_yield = round(_ann_yield / 365, 4)
                    _tax_rows.append({
                        "Date":                 _date_str,
                        "Risk Profile":         _prof.capitalize(),
                        "Protocol":             _opp.get("protocol", "—"),
                        "Pool / Asset":         _opp.get("asset_or_pool", "—"),
                        "Est. APY (%)":         round(_apy, 2),
                        "Est. Annual Yield ($)": _ann_yield,
                        "Est. Daily Yield ($)":  _day_yield,
                        "Confidence (%)":       round(float(_opp.get("confidence") or 0), 1),
                        "Action":               _opp.get("action", "—"),
                    })

        if _tax_rows:
            _buf = io.StringIO()
            _writer = csv.DictWriter(_buf, fieldnames=list(_tax_rows[0].keys()))
            _writer.writeheader()
            _writer.writerows(_tax_rows)
            _csv_fname = f"flare_defi_tax_{datetime.now(timezone.utc).strftime('%Y%m%d')}.csv"
            st.download_button(
                label=f"Download Tax CSV ({len(_tax_rows)} rows, {len(_tax_runs)} scans)",
                data=_buf.getvalue().encode("utf-8"),
                file_name=_csv_fname,
                mime="text/csv",
                key="tax_csv_btn",
                width='stretch',
            )
            st.caption(f"Top 5 opportunities per profile per scan · {len(_tax_runs)} scan(s) in history")


    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)

with _ctrl_tab_export:
    # ─── Investment Committee PDF Report (Item 38) ───────────────────────────────
    st.markdown("### Investment Committee Report")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Generate a professional PDF report for your family office investment committee. "
        "Includes portfolio positions, market context, top opportunities, treasury health, "
        "and AI agent performance summary.</div>",
        unsafe_allow_html=True,
    )
    if st.button("Generate Investment Committee PDF", key="ic_pdf_btn", width='stretch'):
      with st.spinner("Generating Investment Committee PDF..."):
        try:
            from pdf_export import generate_investment_committee_pdf
            from agents.agent_runner import AgentRunner as _AR
            from agents.data_feed import get_agent_context
            from scanners.defillama import fetch_protocol_treasuries as _fpt
            from config import BRAND_NAME

            _ar = _AR()
            _ag_state  = _ar.get_state()
            _positions = _ar.get_open_positions()
            _ag_stats  = _ar.get_paper_stats()
            _ag_stats["mode"] = _ag_state.get("mode", "PAPER")

            # Minimal market context
            try:
                _mctx = get_agent_context(
                    wallet_balance_usd=_ag_state.get("wallet_usd", 10000.0),
                    daily_pnl_usd=0.0,
                    open_positions=_positions,
                    operating_mode=_ag_state.get("mode", "PAPER"),
                )
            except Exception:
                _mctx = {}

            _opps  = _mctx.get("top_opportunities", [])
            _treas = _fpt()

            _pdf_bytes = generate_investment_committee_pdf(
                portfolio_positions = _positions,
                top_opportunities   = _opps,
                market_context      = _mctx.get("market_context", {}),
                agent_stats         = _ag_stats,
                treasury_data       = _treas,
                family_office_name  = str(BRAND_NAME) if BRAND_NAME else "Family Office",
            )
            _ic_fname = f"investment_committee_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
            st.download_button(
                label="Download Investment Committee PDF",
                data=_pdf_bytes,
                file_name=_ic_fname,
                mime="application/pdf",
                key="ic_pdf_download",
                width='stretch',
            )
            st.success("Report generated successfully.")
        except ImportError as _ie:
            logger.warning("[Settings] PDF dependency missing: %s", _ie)
            st.error("PDF export unavailable — run `pip install reportlab` to enable this feature.")
        except Exception as _ic_err:
            logger.warning("[Settings] report generation failed: %s", _ic_err)
            st.error("Report generation failed — please try again. If this persists, check your data connection.")

    # ─── RIA Advisor PDF Report ──────────────────────────────────────────────────
    st.markdown("### RIA Advisor Report")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:12px;'>"
        "Generate a compliance-ready PDF for RIA/advisor client meetings. "
        "Includes GIPS-compatible language, 'suggested allocation' framing, risk grades, "
        "market environment summary, and a full regulatory disclaimer.</div>",
        unsafe_allow_html=True,
    )
    _ria_advisor = st.text_input("Advisor Name (optional)", key="ria_advisor_name", placeholder="Jane Smith, CFP")
    _ria_client  = st.text_input("Client Name (optional)",  key="ria_client_name",  placeholder="Smith Family Account")
    if st.button("Generate RIA Advisor PDF", key="ria_pdf_btn", width='stretch'):
      with st.spinner("Generating RIA Advisor PDF..."):
        try:
            from pdf_export import generate_ria_advisor_pdf
            from models.composite_signal import compute_composite_signal
            from macro_feeds import fetch_all_macro_data, fetch_coinmetrics_onchain, fetch_btc_ta_signals
            from ui.common import fetch_fear_greed_history as _fgh, load_latest as _load_lat
            from config import BRAND_NAME as _BN

            # load_latest() returns the full result dict; models are nested under "models" key.
            # e.g. latest = {run_id:..., models:{conservative:[...], medium:[...], high:[...]}, ...}
            _latest_ria  = _load_lat()
            _results_ria = (_latest_ria.get("models") or {})

            if not _results_ria:
                st.warning(
                    "No scan data found. Run a full scan from the Dashboard or Opportunities page first, "
                    "then return here to generate the PDF.",
                    icon="⚠️",
                )
                st.stop()

            # Try to get composite signal for market context section
            _csig_ria = {}
            try:
                _md  = fetch_all_macro_data()
                _ocd = fetch_coinmetrics_onchain(days=400)
                _tad = fetch_btc_ta_signals()
                _fgl = _fgh(30)
                _fgv, _fg30 = None, None
                if _fgl:
                    _fgv  = int(_fgl[0]["value"])
                    _fgv30 = [int(h["value"]) for h in _fgl if "value" in h]
                    _fg30  = round(sum(_fgv30) / len(_fgv30), 1) if _fgv30 else None
                _csig_ria = compute_composite_signal(_md, _ocd, _fgv, ta_data=_tad, fg_30d_avg=_fg30)
            except Exception:
                pass

            _ria_bytes = generate_ria_advisor_pdf(
                model_results    = _results_ria,
                composite_signal = _csig_ria,
                advisor_name     = _ria_advisor,
                client_name      = _ria_client,
                brand_name       = _BN or "DeFi Intelligence Platform",
            )
            _ria_fname = f"ria_advisor_report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.pdf"
            st.download_button(
                label="Download RIA Advisor PDF",
                data=_ria_bytes,
                file_name=_ria_fname,
                mime="application/pdf",
                key="ria_pdf_download",
                width='stretch',
            )
            st.success("RIA report generated.")
        except ImportError as _ie:
            st.error(f"PDF generation unavailable — install fpdf2: pip install fpdf2")
        except Exception as _ria_err:
            logger.warning("[Settings] RIA report generation failed: %s", _ria_err)
            st.error("RIA report generation failed — please try again. If this persists, check your data connection.")

    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)


with _ctrl_tab_wallet:
    # ─── Wallet Setup ─────────────────────────────────────────────────────────────
    render_section_header("Wallet Setup", "Generate encrypted agent wallets for Phase 2")

    if not _WALLETS_OK or _wallets is None:
        st.warning("Wallet manager unavailable — ensure agents package dependencies are installed.")
    else:
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
                        logger.error("[Settings] wallet generation failed: %s", e)
                        st.error("Wallet generation failed — please try again. If this persists, check your Python environment.")


with _ctrl_tab_api:
    st.markdown("### API Key Management")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Set API keys below. Keys entered here apply for the lifetime of this running process "
        "(cleared on restart) and propagate to all downstream code paths. To persist permanently "
        "across restarts, set them as environment variables or in a <code>.env</code> file.</div>",
        unsafe_allow_html=True,
    )
    _api_keys = [
        ("ANTHROPIC_API_KEY",    "Anthropic (Claude Decision Engine)",  "Required for autonomous agent decisions"),
        ("COINMETRICS_API_KEY",  "CoinMetrics (On-Chain Data)",         "MVRV, SOPR, Hash Rate — optional, uses community API if blank"),
        ("FRED_API_KEY",         "FRED (Macro Data)",                    "DXY, VIX, CPI, yield curve — optional, falls back to cached values"),
        ("ZERION_API_KEY",       "Zerion (Multi-Chain Wallet Data)",    "Required for Zerion wallet positions in Portfolio tab"),
        ("CG_PRO_API_KEY",       "CoinGecko Pro",                       "Optional — removes rate limits on price feeds"),
    ]
    import os
    for _key, _label, _desc in _api_keys:
        _cur = st.session_state.get(f"api_{_key}", "") or os.environ.get(_key, "")
        _masked = (_cur[:6] + "…" + _cur[-4:]) if len(_cur) > 12 else ("●" * len(_cur) if _cur else "")
        _c1, _c2 = st.columns([3, 1])
        with _c1:
            _new_val = st.text_input(
                f"{_label}",
                value=_cur,
                type="password",
                help=_desc,
                key=f"api_input_{_key}",
            )
        with _c2:
            _set_lbl = "Set" if _cur else "Not set"
            _set_col = "#10b981" if _cur else "#ef4444"
            st.markdown(f"<div style='font-size:0.75rem; color:{_set_col}; margin-top:28px;'>{_set_lbl}</div>", unsafe_allow_html=True)
        if _new_val and _new_val != _cur:
            st.session_state[f"api_{_key}"] = _new_val
            os.environ[_key] = _new_val
    st.markdown(
        "<div class='warn-box' style='font-size:0.85rem;'>"
        "API keys entered here are stored in session memory only — they are cleared when you restart the app. "
        "For permanent storage, set them as system environment variables or in a <code>.env</code> file.</div>",
        unsafe_allow_html=True,
    )

with _ctrl_tab_cache:
    st.markdown("### Cache Controls")
    st.markdown(
        "<div style='color:#475569; font-size:0.85rem; margin-bottom:16px;'>"
        "Manage data caches. Clearing forces a fresh fetch on the next scan or page load.</div>",
        unsafe_allow_html=True,
    )
    _cc1, _cc2 = st.columns(2)
    with _cc1:
        if st.button("🗑️ Clear All Streamlit Cache", key="clear_all_cache",
                         width='stretch',
                     help="Clears all @st.cache_data caches across all pages"):
            st.cache_data.clear()
            st.success("All Streamlit caches cleared — data will refresh on next load.")
        if st.button("🔄 Force Macro Data Refresh", key="clear_macro_cache",
                         width='stretch',
                     help="Forces fresh fetch of DXY, VIX, CPI, yield curve data"):
            try:
                from macro_feeds import clear_macro_caches
                clear_macro_caches()
            except Exception:
                pass
            st.cache_data.clear()
            st.success("Macro data cache cleared.")
    with _cc2:
        if st.button("🔄 Force Scan Data Refresh", key="clear_scan_cache",
                         width='stretch',
                     help="Forces the next scan to re-fetch all DeFiLlama and scanner data"):
            st.cache_data.clear()
            st.success("Scan cache cleared — next scan will fetch fresh data.")
        if st.button("🔄 Force Price Data Refresh", key="clear_price_cache",
                         width='stretch',
                     help="Forces fresh CoinGecko price fetch"):
            st.cache_data.clear()
            st.success("Price cache cleared.")
    st.markdown("<div class='divider'></div>", unsafe_allow_html=True)
    st.markdown("**Cache Status**")
    _cache_info = [
        ("DeFiLlama Yields",   "60 min TTL",  "Auto-refreshes every scan cycle"),
        ("CoinGecko Prices",   "5 min TTL",   "Rate-limited to 0.4 req/s"),
        ("FRED Macro Data",    "2 hour TTL",  "DXY, VIX, CPI, 2Y10Y"),
        ("CoinMetrics On-Chain","1 hour TTL", "MVRV, SOPR, Hash Rate, Puell"),
        ("Fear & Greed",       "1 hour TTL",  "30-day history from Alternative.me"),
        ("Composite Signal",   "1 hour TTL",  "3-layer model: Macro + Sentiment + On-Chain"),
    ]
    for _cn, _ttl, _desc in _cache_info:
        st.markdown(
            f"<div style='display:flex; justify-content:space-between; "
            f"border-bottom:1px solid rgba(148,163,184,0.1); padding:6px 0; font-size:0.85rem;'>"
            f"<span style='color:#e2e8f0;'>{_cn}</span>"
            f"<span style='color:#00d4aa;'>{_ttl}</span>"
            f"<span style='color:#475569;'>{_desc}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )


# ─── Display Preferences (ToS #10) ────────────────────────────────────────────

st.markdown("<div class='divider' style='margin-top:32px;'></div>", unsafe_allow_html=True)

with st.expander("🎨 Display Preferences", expanded=False):
    # ─── Compact Sidebar (ToS #1) — tiered default per Q2 ─────────────────
    _ul_current = st.session_state.get("user_level", "beginner")
    _compact_default = _ul_current == "advanced"
    if _ul_current != "beginner":
        _compact_new = st.toggle(
            "Compact sidebar (icons only)",
            value=st.session_state.get("compact_sidebar", _compact_default),
            key="compact_sidebar_toggle",
            help="Hide sidebar labels, show icons only. Hover for tooltips. Advanced-tier default ON.",
        )
        if _compact_new != st.session_state.get("compact_sidebar", _compact_default):
            st.session_state["compact_sidebar"] = _compact_new
            st.rerun()

        # ─── Focus Mode (ToS #4) — hide educational scaffolds ─────────────
        _focus_new = st.toggle(
            "Focus mode (hide helper content)",
            value=st.session_state.get("focus_mode", False),
            key="focus_mode_toggle",
            help="Hide 'What does this mean for me?' panels and tighten spacing. Maximum data density for advisors.",
        )
        if _focus_new != st.session_state.get("focus_mode", False):
            st.session_state["focus_mode"] = _focus_new
            st.rerun()

        st.markdown("<div style='height:12px;'></div>", unsafe_allow_html=True)

    st.markdown(
        "<div style='color:#94a3b8; font-size:0.88rem; margin-bottom:12px;'>"
        "Regional color convention for gains and losses.</div>",
        unsafe_allow_html=True,
    )
    _up_is_red_current = st.session_state.get("up_is_red", False)
    _region_c1, _region_c2, _region_c3 = st.columns([3, 1, 3])
    with _region_c1:
        st.markdown(
            "<div style='text-align:center; padding:8px; border-radius:8px; "
            + ("background:rgba(148,163,184,0.08);" if _up_is_red_current else "background:rgba(0,212,170,0.08); border:1px solid rgba(0,212,170,0.3);")
            + "'>"
            "<div style='color:#22c55e; font-weight:700; font-size:0.95rem;'>+1.00 ▲</div>"
            "<div style='color:#ef4444; font-weight:700; font-size:0.95rem;'>-1.00 ▼</div>"
            "<div style='color:#64748b; font-size:0.75rem; margin-top:4px;'>Western (US, EU)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    with _region_c2:
        _new_up_is_red = st.toggle(
            "⇄",
            value=_up_is_red_current,
            key="up_is_red_toggle",
            help="Flip color convention for markets where up = red (China, Japan, Korea).",
            label_visibility="collapsed",
        )
    with _region_c3:
        st.markdown(
            "<div style='text-align:center; padding:8px; border-radius:8px; "
            + ("background:rgba(0,212,170,0.08); border:1px solid rgba(0,212,170,0.3);" if _up_is_red_current else "background:rgba(148,163,184,0.08);")
            + "'>"
            "<div style='color:#ef4444; font-weight:700; font-size:0.95rem;'>+1.00 ▲</div>"
            "<div style='color:#22c55e; font-weight:700; font-size:0.95rem;'>-1.00 ▼</div>"
            "<div style='color:#64748b; font-size:0.75rem; margin-top:4px;'>Asian (CN, JP, KR)</div>"
            "</div>",
            unsafe_allow_html=True,
        )
    if _new_up_is_red != _up_is_red_current:
        st.session_state["up_is_red"] = _new_up_is_red
        st.rerun()


# ─── Share Feedback (ToS #9) ──────────────────────────────────────────────────

st.markdown("<div class='divider' style='margin-top:32px;'></div>", unsafe_allow_html=True)

with st.expander("💬 Share Feedback", expanded=False):
    st.markdown(
        "<div style='color:#94a3b8; font-size:0.88rem; margin-bottom:4px;'>"
        "Your feedback helps us improve the platform.</div>"
        "<div style='color:#64748b; font-size:0.82rem; margin-bottom:16px;'>"
        "Need assistance with an issue? "
        "<a href='https://github.com/davidduraesdd1-blip/flare-defi-model/issues' "
        "target='_blank' style='color:#00d4aa; text-decoration:none;'>"
        "Contact support →</a></div>",
        unsafe_allow_html=True,
    )

    _fb_text = st.text_area(
        "Tell us about your experience",
        key="feedback_text",
        placeholder="What's working well? What could be better?",
        height=120,
        label_visibility="collapsed",
    )

    st.markdown(
        "<div style='color:#94a3b8; font-size:0.85rem; margin:12px 0 8px;'>"
        "Overall, how satisfied are you with your experience?</div>",
        unsafe_allow_html=True,
    )
    # Sentiment selection uses on_click callbacks so it's written to session_state
    # BEFORE the rerun completes, avoiding the Streamlit 1-rerun-lag pattern.
    def _set_sentiment(val: str) -> None:
        st.session_state["fb_sentiment"] = val

    _fb_c1, _fb_c2, _fb_c3, _fb_spacer = st.columns([1, 1, 1, 5])
    _current_sent = st.session_state.get("fb_sentiment")
    with _fb_c1:
        st.button(
            "😊", key="fb_happy", help="Satisfied",
            on_click=_set_sentiment, args=("positive",),
            type=("primary" if _current_sent == "positive" else "secondary"),
        )
    with _fb_c2:
        st.button(
            "😐", key="fb_neutral", help="Neutral",
            on_click=_set_sentiment, args=("neutral",),
            type=("primary" if _current_sent == "neutral" else "secondary"),
        )
    with _fb_c3:
        st.button(
            "☹", key="fb_sad", help="Dissatisfied",
            on_click=_set_sentiment, args=("negative",),
            type=("primary" if _current_sent == "negative" else "secondary"),
        )

    if st.button("Send feedback", key="fb_send", width='stretch',
                 disabled=not _fb_text.strip()):
        try:
            from pathlib import Path as _Path
            import json as _json
            # Absolute path resolved relative to this file's project root, so
            # writes go to the same place regardless of cwd or how Streamlit
            # invokes the page.
            _project_root = _Path(__file__).resolve().parent.parent
            _fb_path = _project_root / "data" / "user_feedback.jsonl"
            _fb_path.parent.mkdir(parents=True, exist_ok=True)
            _fb_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "app": "defi",
                "user_level": st.session_state.get("user_level", "beginner"),
                "sentiment": st.session_state.get("fb_sentiment"),
                "text": _fb_text.strip(),
            }
            with _fb_path.open("a", encoding="utf-8") as _fp:
                _fp.write(_json.dumps(_fb_entry) + "\n")
            st.success("Thanks — feedback received.")
            st.session_state["feedback_text"] = ""
            st.session_state.pop("fb_sentiment", None)
        except Exception as _fb_err:
            st.error(f"Couldn't save feedback right now. Try again in a moment. ({_fb_err})")

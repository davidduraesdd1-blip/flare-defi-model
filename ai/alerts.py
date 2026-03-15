"""
AI Alerts — Email and Telegram notifications for APY threshold events.
Called by the scheduler after each scan completes.
Configure settings via the Streamlit dashboard → Alert Settings.
"""

import json
import os
import re
import smtplib
import ssl
import tempfile
import logging
import requests
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

logger = logging.getLogger(__name__)

from config import RISK_PROFILE_NAMES

ALERTS_CONFIG_FILE = Path(__file__).parent.parent / "data" / "alerts_config.json"
# ⚠️  SECURITY: alerts_config.json contains SMTP credentials in plaintext.
#     Add  data/alerts_config.json  to your .gitignore — NEVER commit this file.


# ─── Input Validation ─────────────────────────────────────────────────────────

def _is_valid_email(addr: str) -> bool:
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", addr.strip()))


def _is_valid_telegram_token(token: str) -> bool:
    # Format: <digits>:<35+ alphanumeric/underscore/hyphen chars>
    return bool(re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", token.strip()))


# ─── Config I/O ───────────────────────────────────────────────────────────────

def load_alerts_config() -> dict:
    if ALERTS_CONFIG_FILE.exists():
        try:
            with open(ALERTS_CONFIG_FILE) as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load alerts config: {e}")
    return {
        "email": {
            "enabled":     False,
            "address":     "",
            "smtp_server": "smtp.gmail.com",
            "smtp_port":   587,
            "username":    "",
            "password":    "",
        },
        "telegram": {
            "enabled":   False,
            "bot_token": "",
            "chat_id":   "",
        },
        "thresholds": {
            "min_apy_alert":  150.0,
            "new_arb_alert":  True,
        },
    }


def save_alerts_config(config: dict) -> None:
    """Atomic write: temp file then rename, same pattern as save_history()."""
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=ALERTS_CONFIG_FILE.parent, suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(config, f, indent=2)
        os.replace(tmp_path, ALERTS_CONFIG_FILE)
        logger.info("Alerts config saved.")
    except Exception as e:
        logger.error(f"Could not save alerts config: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# ─── Delivery ─────────────────────────────────────────────────────────────────

def send_email_alert(subject: str, body: str, config: dict) -> bool:
    """Send an email alert. Returns True on success."""
    cfg = config.get("email", {})
    if not cfg.get("enabled") or not cfg.get("address"):
        return False
    if not _is_valid_email(cfg["address"]):
        logger.warning(f"Email alert skipped — invalid address: {cfg['address']!r}")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"]    = cfg.get("username") or cfg["address"]
        msg["To"]      = cfg["address"]
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        tls_context = ssl.create_default_context()
        with smtplib.SMTP(cfg.get("smtp_server", "smtp.gmail.com"),
                          int(cfg.get("smtp_port", 587))) as server:
            server.ehlo()
            server.starttls(context=tls_context)
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.sendmail(msg["From"], msg["To"], msg.as_string())
        logger.info(f"Email alert sent: {subject}")
        return True
    except Exception as e:
        logger.warning(f"Email alert failed: {e}")
        return False


def send_telegram_alert(message: str, config: dict) -> bool:
    """Send a Telegram message via bot. Returns True on success."""
    cfg = config.get("telegram", {})
    if not cfg.get("enabled") or not cfg.get("bot_token") or not cfg.get("chat_id"):
        return False
    if not _is_valid_telegram_token(cfg["bot_token"]):
        logger.warning("Telegram alert skipped — bot_token format is invalid (expected digits:35+chars)")
        return False
    try:
        url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": cfg["chat_id"], "text": message, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code == 200:
            logger.info("Telegram alert sent.")
            return True
        logger.warning(f"Telegram alert failed: {r.status_code} — {r.text[:100]}")
        return False
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
        return False


def test_email(config: dict) -> tuple:
    """Send a test email. Returns (success: bool, message: str)."""
    ok = send_email_alert(
        "⚡ Flare DeFi Model — Test Alert",
        f"This is a test alert sent at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.\n\nIf you received this, email alerts are configured correctly.",
        config,
    )
    return (ok, "Test email sent successfully!" if ok else "Email failed — check SMTP settings and logs.")


def test_telegram(config: dict) -> tuple:
    """Send a test Telegram message. Returns (success: bool, message: str)."""
    ok = send_telegram_alert(
        f"⚡ <b>Flare DeFi Model — Test Alert</b>\n"
        f"Sent at {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}.\n"
        f"Telegram alerts are working correctly.",
        config,
    )
    return (ok, "Test message sent!" if ok else "Telegram failed — check bot token and chat ID.")


# ─── Threshold Checker ────────────────────────────────────────────────────────

def check_and_send_alerts(model_results: dict, arb_results: dict = None) -> None:
    """
    Called by the scheduler after each scan.
    Checks results against user thresholds and sends email/Telegram alerts.
    """
    config     = load_alerts_config()
    thresholds = config.get("thresholds", {})
    min_apy    = float(thresholds.get("min_apy_alert", 150.0))
    arb_alert  = thresholds.get("new_arb_alert", True)

    lines = [
        f"⚡ Flare DeFi Scan — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    triggered = False

    for profile in RISK_PROFILE_NAMES:
        opps = model_results.get(profile, [])
        if opps:
            top = opps[0]
            apy = top.get("estimated_apy", 0)
            if apy >= min_apy:
                triggered = True
                lines.append(
                    f"🔥 [{profile.upper()}] {top.get('protocol')} — "
                    f"{top.get('asset_or_pool')}: {apy:.1f}% APY "
                    f"(Confidence: {top.get('confidence', 0):.0f}%)"
                )

    if arb_alert and arb_results:
        for profile, arbs in arb_results.items():
            for arb in (arbs or [])[:1]:
                if arb.get("urgency") == "act_now":
                    triggered = True
                    lines.append(
                        f"⚡ ARB [{profile.upper()}] {arb.get('strategy_label', 'Opportunity')}: "
                        f"+{arb.get('estimated_profit', 0):.2f}% — ACT NOW"
                    )

    if not triggered:
        logger.debug("No alert thresholds met — skipping notifications.")
        return

    lines.append("\nOpen your Flare DeFi dashboard for full details.")
    message = "\n".join(lines)
    subject = "⚡ Flare DeFi Alert — New Opportunity Detected"

    send_email_alert(subject, message, config)
    send_telegram_alert(message, config)

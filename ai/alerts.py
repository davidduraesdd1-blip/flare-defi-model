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
import logging
import requests
from pathlib import Path
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone

from utils.http import _SESSION

logger = logging.getLogger(__name__)

from config import RISK_PROFILE_NAMES
from utils.file_io import atomic_json_write

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
            with open(ALERTS_CONFIG_FILE, encoding="utf-8") as f:
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
        # Feature 9: Discord and generic webhook support
        "discord": {
            "enabled":     False,
            "webhook_url": "",
        },
        "webhook": {
            "enabled":     False,
            "url":         "",
            "secret":      "",   # optional HMAC-SHA256 signing secret
        },
        "thresholds": {
            "min_apy_alert":  150.0,
            "new_arb_alert":  True,
        },
    }


def save_alerts_config(config: dict) -> None:
    """Atomic write: temp file then rename, same pattern as save_history()."""
    if atomic_json_write(ALERTS_CONFIG_FILE, config):
        logger.info("Alerts config saved.")


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
                          int(cfg.get("smtp_port", 587)),
                          timeout=30) as server:
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
        if r.ok:
            logger.info("Telegram alert sent.")
            return True
        logger.warning(f"Telegram alert failed: {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Telegram alert failed: {e}")
        return False


def send_discord_alert(message: str, config: dict) -> bool:
    """
    Feature 9: Send an alert to Discord via incoming webhook.
    Returns True on success.
    """
    cfg = config.get("discord", {})
    if not cfg.get("enabled") or not cfg.get("webhook_url"):
        return False
    url = cfg["webhook_url"].strip()
    if not url.startswith("https://discord.com/api/webhooks/"):
        logger.warning("Discord alert skipped — webhook URL format invalid.")
        return False
    try:
        # Discord embeds markdown-ish formatting; wrap in a code block for readability
        payload = {"content": f"```\n{message[:1990]}\n```"}
        r = requests.post(url, json=payload, timeout=10)
        if r.status_code in (200, 204):
            logger.info("Discord alert sent.")
            return True
        logger.warning(f"Discord alert failed: {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Discord alert failed: {e}")
        return False


def send_webhook_alert(subject: str, message: str, config: dict) -> bool:
    """
    Feature 9: Send a generic JSON webhook (e.g. Zapier, Make, n8n, Slack incoming webhook).
    Optionally signs the payload with HMAC-SHA256 if a secret is set.
    Returns True on success.
    """
    cfg = config.get("webhook", {})
    if not cfg.get("enabled") or not cfg.get("url"):
        return False
    url = cfg["url"].strip()
    if not url.startswith("https://"):
        logger.warning("Webhook alert skipped — URL must use HTTPS.")
        return False
    try:
        payload = {
            "source":    "flare_defi_model",
            "subject":   subject,
            "message":   message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        headers = {"Content-Type": "application/json"}
        secret = cfg.get("secret", "").strip()
        if secret:
            import hmac
            import hashlib
            body = json.dumps(payload, separators=(",", ":")).encode()
            sig  = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            headers["X-Flare-Signature"] = sig
        else:
            body = json.dumps(payload, separators=(",", ":")).encode()
        r = requests.post(url, data=body, headers=headers, timeout=10)
        if r.ok:
            logger.info("Webhook alert sent.")
            return True
        logger.warning(f"Webhook alert failed: {r.status_code} — {r.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"Webhook alert failed: {e}")
        return False


def send_url_webhook_alert(message: str, webhook_url: str = None) -> bool:
    """
    Send an alert to a Discord or Telegram webhook URL directly (#18 / Batch 9).
    ``webhook_url`` falls back to the DEFI_WEBHOOK_URL env var.
    Returns True on success.
    """
    if not webhook_url:
        webhook_url = os.environ.get("DEFI_WEBHOOK_URL", "")
    if not webhook_url:
        return False
    if not webhook_url.startswith("https://"):
        logger.warning("send_url_webhook_alert: URL must use HTTPS — skipping")
        return False

    try:
        from utils.http import is_safe_url
        if not is_safe_url(webhook_url):
            logger.warning("send_url_webhook_alert: URL not in SSRF allowlist — skipping")
            return False

        # Detect Discord vs Telegram by URL pattern
        if "discord.com/api/webhooks" in webhook_url:
            payload = {"content": message, "username": "DeFi Model"}
        elif "api.telegram.org" in webhook_url:
            chat_id = os.environ.get("DEFI_TELEGRAM_CHAT_ID", "")
            payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
        else:
            payload = {"text": message}

        r = _SESSION.post(webhook_url, json=payload, timeout=5)
        return r.status_code in (200, 204)
    except Exception as e:
        logger.warning("URL webhook alert failed: %s", e)
        return False


def test_discord(config: dict) -> tuple:
    """Send a test Discord message. Returns (success: bool, message: str)."""
    ok = send_discord_alert(
        f"⚡ Flare DeFi Model — Test Alert\n"
        f"Sent at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n"
        f"Discord alerts are working correctly.",
        config,
    )
    return (ok, "Test Discord message sent!" if ok else "Discord failed — check webhook URL.")


def test_webhook(config: dict) -> tuple:
    """Send a test webhook. Returns (success: bool, message: str)."""
    ok = send_webhook_alert(
        "⚡ Flare DeFi Model — Test Alert",
        f"Test sent at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Webhook is working.",
        config,
    )
    return (ok, "Test webhook sent!" if ok else "Webhook failed — check URL and logs.")


def test_email(config: dict) -> tuple:
    """Send a test email. Returns (success: bool, message: str)."""
    ok = send_email_alert(
        "⚡ Flare DeFi Model — Test Alert",
        f"This is a test alert sent at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n\nIf you received this, email alerts are configured correctly.",
        config,
    )
    return (ok, "Test email sent successfully!" if ok else "Email failed — check SMTP settings and logs.")


def test_telegram(config: dict) -> tuple:
    """Send a test Telegram message. Returns (success: bool, message: str)."""
    ok = send_telegram_alert(
        f"⚡ <b>Flare DeFi Model — Test Alert</b>\n"
        f"Sent at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}.\n"
        f"Telegram alerts are working correctly.",
        config,
    )
    return (ok, "Test message sent!" if ok else "Telegram failed — check bot token and chat ID.")


# ─── CL Out-of-Range Alert Checker (Feature 12) ───────────────────────────────

def check_cl_range_alerts(prices: list) -> list:
    """
    Feature 12: Returns a list of alert strings for any LP positions whose
    current FTSO price has moved outside the user-defined CL (concentrated
    liquidity) range.  Positions must have cl_range_low and cl_range_high set.
    prices: list of dicts with keys {symbol, price_usd}.
    """
    import json
    from config import POSITIONS_FILE

    price_lkp = {
        p.get("symbol", ""): float(p.get("price_usd", 0))
        for p in prices if isinstance(p, dict) and p.get("symbol")
    }
    if not price_lkp:
        return []

    try:
        with open(POSITIONS_FILE, encoding="utf-8") as _f:
            positions = json.load(_f)
    except Exception:
        return []

    alerts = []
    for pos in positions:
        if pos.get("position_type", "lp") != "lp":
            continue
        try:
            cl_low  = float(pos["cl_range_low"])
            cl_high = float(pos["cl_range_high"])
        except (KeyError, TypeError, ValueError):
            continue
        token_a = pos.get("token_a", "")
        current_price = price_lkp.get(token_a, 0.0)
        if current_price <= 0:
            continue
        label = pos.get("name") or pos.get("asset_or_pool") or token_a
        if current_price < cl_low:
            alerts.append(
                f"CL OUT OF RANGE (BELOW): {label} — "
                f"{token_a} ${current_price:.4f} < range low ${cl_low:.4f} "
                f"(fees paused, position is 100% {token_a})"
            )
        elif current_price > cl_high:
            alerts.append(
                f"CL OUT OF RANGE (ABOVE): {label} — "
                f"{token_a} ${current_price:.4f} > range high ${cl_high:.4f} "
                f"(fees paused, position fully converted)"
            )
    return alerts


# ─── Threshold Checker ────────────────────────────────────────────────────────

def check_and_send_alerts(model_results: dict, arb_results: dict = None) -> None:
    """
    Called by the scheduler after each scan.
    Checks results against user thresholds and sends email/Telegram alerts.
    Also checks for TVL change alerts (#79).
    """
    config     = load_alerts_config()
    thresholds = config.get("thresholds", {})
    try:
        min_apy = float(thresholds.get("min_apy_alert", 150.0))
    except (TypeError, ValueError):
        logger.warning("Invalid min_apy_alert in config — using default 150.0")
        min_apy = 150.0
    arb_alert  = thresholds.get("new_arb_alert", True)

    lines = [
        f"⚡ Flare DeFi Scan — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]
    triggered = False

    for profile in RISK_PROFILE_NAMES:
        opps = model_results.get(profile, [])
        if opps:
            top = opps[0]
            apy = top.get("estimated_apy") or 0
            if apy >= min_apy:
                triggered = True
                lines.append(
                    f"🔥 [{profile.upper()}] {top.get('protocol')} — "
                    f"{top.get('asset_or_pool')}: {apy:.1f}% APY "
                    f"(Confidence: {top.get('confidence', 0):.0f}%)"
                )

    if arb_alert and arb_results:
        for profile, arbs in arb_results.items():
            if not isinstance(arbs, list):
                continue
            for arb in arbs[:1]:
                if arb.get("urgency") == "act_now":
                    triggered = True
                    lines.append(
                        f"⚡ ARB [{profile.upper()}] {arb.get('strategy_label', 'Opportunity')}: "
                        f"+{arb.get('estimated_profit', 0):.2f}% — ACT NOW"
                    )

    # #79 — TVL Change Alerts: check for significant drops and include in notifications
    try:
        from scanners.defi_protocols import fetch_tvl_change_alerts
        tvl_alerts = fetch_tvl_change_alerts(threshold_pct=5.0)
        for alert in tvl_alerts:
            triggered = True
            sev   = alert.get("severity", "WARNING")
            proto = alert.get("protocol", "unknown")
            chg   = alert.get("change_pct", 0.0)
            tvl_m = round(alert.get("tvl_now", 0) / 1e6, 1)
            lines.append(
                f"{'🚨' if sev == 'CRITICAL' else '⚠️'} TVL {sev}: {proto} "
                f"dropped {chg:+.1f}% (now ${tvl_m}M)"
            )
    except Exception as e:
        logger.debug("[Alerts] TVL change check failed: %s", e)

    if not triggered:
        logger.debug("No alert thresholds met — skipping notifications.")
        return

    lines.append("\nOpen your Flare DeFi dashboard for full details.")
    message = "\n".join(lines)
    subject = "⚡ Flare DeFi Alert — New Opportunity Detected"

    send_email_alert(subject, message, config)
    send_telegram_alert(message, config)
    send_discord_alert(message, config)       # Feature 9
    send_webhook_alert(subject, message, config)  # Feature 9


# ─── Smart Alert Tuning (Upgrade #6) ─────────────────────────────────────────

_MIN_APY_FLOOR   = 30.0    # never auto-set below this (avoids alerting on every lending rate)
_MIN_APY_CEILING = 300.0   # never auto-set above this
_SMOOTH_FACTOR   = 0.20    # 20% weight on new calibrated value; 80% on existing (slow convergence)
_MIN_CALIBRATION_SAMPLES = 6   # need at least 6 evaluated predictions before adjusting


def calibrate_alert_thresholds() -> dict:
    """
    Upgrade #6: Auto-calibrate alert thresholds based on historical prediction accuracy.

    Strategy:
      - Collect all accurate top-pick APYs from the feedback loop (where error_pct < 20%).
      - Set min_apy_alert to the 75th-percentile of those APYs.
        → Alerts fire on truly exceptional opportunities, not just average good picks.
      - Apply 80/20 smoothing against the current threshold so calibration is gradual.
      - Save the calibrated threshold back to alerts_config.json.

    Returns a summary dict for UI display.
    """
    from ai.feedback_loop import load_history
    history = load_history()

    # Collect APYs of accurate top predictions across all profiles
    accurate_apys = []
    for pred in (history.get("predictions") or []):
        if not pred.get("evaluated"):
            continue
        for profile_picks in (pred.get("profiles") or {}).values():
            for pick in profile_picks[:1]:   # only the top-ranked pick per profile
                if pick.get("accurate") and pick.get("predicted_apy") is not None:
                    try:
                        accurate_apys.append(float(pick["predicted_apy"]))
                    except (TypeError, ValueError):
                        pass

    if len(accurate_apys) < _MIN_CALIBRATION_SAMPLES:
        return {
            "calibrated": False,
            "reason": f"Need {_MIN_CALIBRATION_SAMPLES} samples, have {len(accurate_apys)}.",
            "samples": len(accurate_apys),
            "new_threshold": None,
        }

    # 75th percentile of accurate APYs — alert on truly exceptional opportunities
    accurate_apys.sort()
    p75_idx  = int(0.75 * (len(accurate_apys) - 1))
    p75_apy  = accurate_apys[p75_idx]
    p75_apy  = max(_MIN_APY_FLOOR, min(_MIN_APY_CEILING, p75_apy))

    config     = load_alerts_config()
    thresholds = config.setdefault("thresholds", {})
    old_thresh = float(thresholds.get("min_apy_alert", 150.0))

    # Smooth: 80% old value + 20% new calibrated value
    new_thresh = round(old_thresh * (1 - _SMOOTH_FACTOR) + p75_apy * _SMOOTH_FACTOR, 1)
    new_thresh = max(_MIN_APY_FLOOR, min(_MIN_APY_CEILING, new_thresh))

    thresholds["min_apy_alert"]         = new_thresh
    thresholds["_calibrated_at"]        = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    thresholds["_calibration_samples"]  = len(accurate_apys)
    thresholds["_raw_p75_apy"]          = round(p75_apy, 1)
    save_alerts_config(config)

    delta = new_thresh - old_thresh
    direction = "raised" if delta > 0.5 else ("lowered" if delta < -0.5 else "unchanged")
    logger.info(
        f"Smart Alert Tuning: threshold {direction} {old_thresh:.1f}% → {new_thresh:.1f}% "
        f"(p75={p75_apy:.1f}%, n={len(accurate_apys)})"
    )
    return {
        "calibrated":     True,
        "old_threshold":  old_thresh,
        "new_threshold":  new_thresh,
        "p75_apy":        round(p75_apy, 1),
        "direction":      direction,
        "samples":        len(accurate_apys),
        "reason":         f"75th-percentile of {len(accurate_apys)} accurate top-pick APYs = {p75_apy:.1f}%",
    }


def get_calibration_report() -> dict:
    """
    Return the latest calibration metadata from the saved config — used by the UI.
    """
    config     = load_alerts_config()
    thresholds = config.get("thresholds", {})
    return {
        "min_apy_alert":           thresholds.get("min_apy_alert", 150.0),
        "calibrated_at":           thresholds.get("_calibrated_at"),
        "calibration_samples":     thresholds.get("_calibration_samples"),
        "raw_p75_apy":             thresholds.get("_raw_p75_apy"),
        "new_arb_alert":           thresholds.get("new_arb_alert", True),
    }

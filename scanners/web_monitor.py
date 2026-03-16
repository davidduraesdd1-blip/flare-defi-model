"""
Web Monitor — Daily scan for new protocols, token listings, and ecosystem news
on the Flare DeFi network.

Data sources:
  Layer 1 — DeFi Llama API  (free, no key)  — detects new protocols on Flare
  Layer 2 — CoinGecko API   (free tier)      — new token listings on Flare
  Layer 3 — RSS feeds       (feedparser)     — official announcements & blogs
  Layer 4 — Claude API      (optional)       — AI-generated plain-English digest

Output: data/monitor_digest.json
Requires: pip install feedparser
Optional: pip install anthropic  (set ANTHROPIC_API_KEY in environment)

Called by the scheduler once daily at 8am.
"""

import json
import logging
import re
import time
import calendar
import os
from datetime import datetime, timezone
from pathlib import Path

import requests

from utils.file_io import atomic_json_write

logger = logging.getLogger(__name__)

# ─── Optional dependency: feedparser ─────────────────────────────────────────
try:
    import feedparser
    _FEEDPARSER_OK = True
except ImportError:
    _FEEDPARSER_OK = False
    logger.debug("feedparser not installed — RSS monitoring disabled. Run: pip install feedparser")

# ─── File paths ───────────────────────────────────────────────────────────────
_BASE_DIR          = Path(__file__).parent.parent
MONITOR_DIGEST_FILE = _BASE_DIR / "data" / "monitor_digest.json"

# ─── DeFi Llama: known Flare protocol slugs ───────────────────────────────────
# Used to distinguish "new" from "already tracked" when parsing DeFi Llama results.
# Slug is the URL-safe name DeFi Llama uses internally (may differ from display name).
_KNOWN_SLUGS_NORM = {
    "blazeswap", "sparkdex", "enosysdex", "enosys", "kinetic",
    "clearpool", "spectrafinance", "spectra", "upshift", "mysticfinance",
    "mystic", "cyclofinance", "cyclo", "firelight", "firelightfinance",
    "sceptre", "hyperliquid",
}

# ─── RSS feed list ────────────────────────────────────────────────────────────
RSS_FEEDS = [
    {"name": "Flare Network",  "url": "https://medium.com/feed/flarenetwork"},
    {"name": "BlazeSwap",      "url": "https://medium.com/feed/@blazeswap"},
    {"name": "SparkDEX",       "url": "https://medium.com/feed/sparkdex"},
    {"name": "Kinetic",        "url": "https://medium.com/feed/kinetic-market"},
    {"name": "Clearpool",      "url": "https://medium.com/feed/clearpool"},
    {"name": "Cyclo Finance",  "url": "https://medium.com/feed/cyclo-finance"},
    {"name": "Enosys",         "url": "https://medium.com/feed/enosys-global"},
    {"name": "Sceptre",        "url": "https://medium.com/feed/sceptre-fi"},
]

_DEFILLAMA_API  = "https://api.llama.fi"
_COINGECKO_API  = "https://api.coingecko.com/api/v3"
_FLARE_PLATFORM_IDS = {"flare-network", "flare"}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _norm(name: str) -> str:
    """Normalise a protocol name/slug for fuzzy matching (lowercase, no separators)."""
    return name.lower().replace(" ", "").replace("-", "").replace("_", "")


# ─── Layer 1: DeFi Llama — new protocol detection ─────────────────────────────

def fetch_defillama_protocols() -> dict:
    """
    Pull all protocols from DeFi Llama and identify ones on Flare
    that are NOT already tracked in config.py.

    Also captures live TVL for known protocols so the app can show
    whether any known protocol's TVL has moved significantly.
    """
    result = {"new_protocols": [], "known_tvl": {}, "error": None}
    try:
        resp = requests.get(f"{_DEFILLAMA_API}/protocols", timeout=25)
        resp.raise_for_status()
        protocols = resp.json()
    except Exception as e:
        result["error"] = f"DeFi Llama fetch failed: {e}"
        logger.warning(result["error"])
        return result

    for proto in protocols:
        chains = [c.lower() for c in (proto.get("chains") or [])]
        if "flare" not in chains:
            continue

        name     = proto.get("name", "")
        slug     = proto.get("slug", "")
        tvl      = proto.get("tvl") or 0
        name_n   = _norm(name)
        slug_n   = _norm(slug)

        is_known = (
            name_n in _KNOWN_SLUGS_NORM
            or slug_n in _KNOWN_SLUGS_NORM
            or any(k in name_n or k in slug_n for k in _KNOWN_SLUGS_NORM)
        )

        if is_known:
            result["known_tvl"][name] = {
                "tvl_usd":  round(tvl),
                "slug":     slug,
                "category": proto.get("category", ""),
            }
        else:
            result["new_protocols"].append({
                "name":        name,
                "slug":        slug,
                "tvl_usd":     round(tvl),
                "chains":      proto.get("chains", []),
                "category":    proto.get("category", "Unknown"),
                "url":         proto.get("url", ""),
                "description": (proto.get("description") or "")[:300],
            })
            logger.info(f"NEW PROTOCOL ON FLARE detected: {name} (TVL ${tvl:,.0f})")

    logger.info(
        f"DeFi Llama: {len(result['new_protocols'])} new protocol(s), "
        f"{len(result['known_tvl'])} known protocols with live TVL"
    )
    return result


# ─── Layer 2: CoinGecko — new token listings ──────────────────────────────────

def fetch_coingecko_flare_tokens() -> dict:
    """
    Check CoinGecko for tokens deployed on the Flare network.
    Compares against the addresses already in config.TOKENS to flag new ones.

    Note: CoinGecko free tier may rate-limit this endpoint on busy days;
    errors are non-fatal — the monitor continues without this layer.
    """
    result = {"new_tokens": [], "error": None}
    try:
        resp = requests.get(
            f"{_COINGECKO_API}/coins/list",
            params={"include_platform": "true"},
            timeout=35,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        coins = resp.json()
    except Exception as e:
        result["error"] = f"CoinGecko fetch failed: {e}"
        logger.warning(result["error"])
        return result

    try:
        from config import TOKENS
        known_addresses = {addr.lower() for addr in TOKENS.values() if addr}
    except Exception:
        known_addresses = set()

    for coin in coins:
        platforms = coin.get("platforms") or {}
        flare_address = None
        for platform_id, addr in platforms.items():
            if platform_id.lower() in _FLARE_PLATFORM_IDS and addr:
                flare_address = addr
                break
        if not flare_address:
            continue
        if flare_address.lower() in known_addresses:
            continue

        result["new_tokens"].append({
            "id":      coin.get("id", ""),
            "symbol":  (coin.get("symbol") or "").upper(),
            "name":    coin.get("name", ""),
            "address": flare_address,
        })

    logger.info(f"CoinGecko: {len(result['new_tokens'])} new Flare token(s) detected")
    return result


# ─── Layer 3: RSS news feeds ──────────────────────────────────────────────────

def fetch_rss_news(max_age_hours: int = 720) -> dict:  # 30-day default
    """
    Parse RSS/Atom feeds from Flare ecosystem blogs and protocol channels.
    Returns only articles published within the last max_age_hours.

    Requires feedparser: pip install feedparser
    Silently skips individual feeds that fail (network or parse errors).
    """
    result = {"items": [], "feeds_checked": 0, "feeds_ok": 0, "error": None}

    if not _FEEDPARSER_OK:
        result["error"] = "feedparser not installed — run: pip install feedparser"
        return result

    cutoff_ts = datetime.now(timezone.utc).timestamp() - (max_age_hours * 3600)

    for feed_cfg in RSS_FEEDS:
        result["feeds_checked"] += 1
        try:
            feed = feedparser.parse(feed_cfg["url"])
            if not feed.entries:
                logger.debug(f"RSS {feed_cfg['name']}: no entries")
                continue

            result["feeds_ok"] += 1
            for entry in feed.entries[:10]:  # cap at 10 per feed
                pub_ts = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_ts = float(calendar.timegm(entry.published_parsed))

                # Skip articles older than cutoff (allow None pub_ts through)
                if pub_ts and pub_ts < cutoff_ts:
                    continue

                summary_raw = entry.get("summary") or ""
                # Strip basic HTML tags for clean display
                summary_clean = re.sub(r"<[^>]+>", "", summary_raw)[:300].strip()

                result["items"].append({
                    "source":    feed_cfg["name"],
                    "title":     entry.get("title", "No title"),
                    "summary":   summary_clean,
                    "link":      entry.get("link", ""),
                    "published": entry.get("published", ""),
                    "pub_ts":    pub_ts,
                })

        except Exception as e:
            logger.debug(f"RSS {feed_cfg['name']} failed: {e}")

    # Sort by publish time, newest first
    result["items"].sort(key=lambda x: x.get("pub_ts") or 0, reverse=True)

    logger.info(
        f"RSS: {len(result['items'])} item(s) from "
        f"{result['feeds_ok']}/{result['feeds_checked']} feeds"
    )
    return result


# ─── Layer 4: Claude AI digest (optional) ────────────────────────────────────

def claude_digest(
    new_protocols: list,
    new_tokens:    list,
    news_items:    list,
) -> str:
    """
    Use Claude Haiku to generate a plain-English summary of today's findings.

    Returns an empty string (silently) if:
    - ANTHROPIC_API_KEY is not set
    - anthropic package is not installed
    - there is nothing new to summarise
    - the API call fails for any reason
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.debug("ANTHROPIC_API_KEY not set — skipping AI digest")
        return ""

    try:
        import anthropic
    except ImportError:
        logger.debug("anthropic package not installed — run: pip install anthropic")
        return ""

    sections = []

    if new_protocols:
        lines = "\n".join(
            f"- {p['name']} | Category: {p['category']} | TVL: ${p['tvl_usd']:,} | {p.get('description','')[:120]}"
            for p in new_protocols
        )
        sections.append(f"NEW PROTOCOLS ON FLARE NETWORK:\n{lines}")

    if new_tokens:
        lines = "\n".join(
            f"- {t['symbol']} ({t['name']}) — contract: {t['address']}"
            for t in new_tokens[:15]
        )
        sections.append(f"NEW TOKEN LISTINGS ON FLARE:\n{lines}")

    if news_items:
        lines = "\n".join(
            f"- [{item['source']}] {item['title']}: {item['summary'][:200]}"
            for item in news_items[:20]
        )
        sections.append(f"RECENT ANNOUNCEMENTS & NEWS:\n{lines}")

    if not sections:
        return "No new developments detected in the Flare DeFi ecosystem today."

    prompt = (
        "You are an analyst for a Flare blockchain DeFi portfolio tracker. "
        "Summarise the following ecosystem changes for a non-technical investor in 3–6 bullet points. "
        "Be concise and plain-English. Flag anything relevant to: yield opportunities, security risks, "
        "new places to earn, or protocol changes affecting existing positions. "
        "Do not fabricate information. If something is unclear, say so.\n\n"
        + "\n\n".join(sections)
    )

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        digest_text = msg.content[0].text.strip()
        logger.info("Claude AI digest generated.")
        return digest_text
    except Exception as e:
        logger.warning(f"Claude digest API call failed: {e}")
        return ""


# ─── Main entry point ─────────────────────────────────────────────────────────

def run_web_monitor() -> dict:
    """
    Execute all monitoring layers and persist results to data/monitor_digest.json.

    Called by scheduler.py once daily. Also callable directly:
        python -c "from scanners.web_monitor import run_web_monitor; run_web_monitor()"
    """
    t0 = time.monotonic()
    logger.info("─" * 50)
    logger.info(f"WEB MONITOR — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    digest: dict = {
        "generated_at":         datetime.utcnow().isoformat(),
        "new_protocols":        [],
        "known_tvl":            {},
        "new_tokens":           [],
        "news_items":           [],
        "ai_digest":            "",
        "sources_checked":      [],
        "errors":               [],
        "run_duration_seconds": 0,
    }

    # ── Layer 1: DeFi Llama ───────────────────────────────────────────────────
    logger.info("Layer 1/4 — DeFi Llama protocol scan...")
    try:
        dl = fetch_defillama_protocols()
        digest["new_protocols"] = dl.get("new_protocols", [])
        digest["known_tvl"]     = dl.get("known_tvl", {})
        digest["sources_checked"].append("defillama")
        if dl.get("error"):
            digest["errors"].append(dl["error"])
    except Exception as e:
        logger.warning(f"DeFi Llama layer error: {e}")
        digest["errors"].append(f"defillama: {e}")

    # ── Layer 2: CoinGecko ────────────────────────────────────────────────────
    logger.info("Layer 2/4 — CoinGecko token scan...")
    try:
        cg = fetch_coingecko_flare_tokens()
        digest["new_tokens"] = cg.get("new_tokens", [])
        digest["sources_checked"].append("coingecko")
        if cg.get("error"):
            digest["errors"].append(cg["error"])
    except Exception as e:
        logger.warning(f"CoinGecko layer error: {e}")
        digest["errors"].append(f"coingecko: {e}")

    # ── Layer 3: RSS ──────────────────────────────────────────────────────────
    logger.info("Layer 3/4 — RSS news feeds...")
    try:
        rss = fetch_rss_news()
        digest["news_items"] = rss.get("items", [])
        digest["sources_checked"].append("rss")
        if rss.get("error"):
            digest["errors"].append(rss["error"])
    except Exception as e:
        logger.warning(f"RSS layer error: {e}")
        digest["errors"].append(f"rss: {e}")

    # ── Layer 4: Claude digest ─────────────────────────────────────────────────
    logger.info("Layer 4/4 — AI digest (requires ANTHROPIC_API_KEY)...")
    try:
        digest["ai_digest"] = claude_digest(
            digest["new_protocols"],
            digest["new_tokens"],
            digest["news_items"],
        )
        if digest["ai_digest"]:
            digest["sources_checked"].append("claude_ai")
    except Exception as e:
        logger.warning(f"AI digest error: {e}")

    # ── Save ──────────────────────────────────────────────────────────────────
    digest["run_duration_seconds"] = round(time.monotonic() - t0, 1)
    MONITOR_DIGEST_FILE.parent.mkdir(exist_ok=True)
    if not atomic_json_write(MONITOR_DIGEST_FILE, digest):
        logger.warning("Could not save monitor digest.")

    logger.info(
        f"Web monitor done in {digest['run_duration_seconds']}s — "
        f"{len(digest['new_protocols'])} new protocol(s), "
        f"{len(digest['new_tokens'])} new token(s), "
        f"{len(digest['news_items'])} news item(s)"
    )
    logger.info("─" * 50)
    return digest

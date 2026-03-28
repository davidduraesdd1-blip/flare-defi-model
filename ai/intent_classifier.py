"""
ai/intent_classifier.py — DeFi Intent Taxonomy (#87)

Maps a user's free-text DeFi question to a structured intent using Claude Haiku.
Falls back to keyword matching if the Claude API is unavailable.
Results are cached for 5 minutes keyed on the normalised query string hash.
"""

import hashlib
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# ── Intent vocabulary ─────────────────────────────────────────────────────────

VALID_INTENTS = {
    "SWAP",
    "PROVIDE_LIQUIDITY",
    "STAKE",
    "BORROW",
    "LEND",
    "CLAIM_REWARDS",
    "BRIDGE",
    "PORTFOLIO_CHECK",
    "YIELD_HUNT",
    "RISK_ASSESSMENT",
    "OTHER",
}

# ── 5-minute TTL cache ────────────────────────────────────────────────────────
_intent_cache: dict[int, dict] = {}
_TTL = 300   # 5 minutes


def _cache_key(query: str) -> int:
    return int(hashlib.md5(query.lower().strip().encode()).hexdigest(), 16) % (10**10)


def _is_cached(key: int) -> dict | None:
    entry = _intent_cache.get(key)
    if entry and time.time() - entry["_ts"] < _TTL:
        return {k: v for k, v in entry.items() if k != "_ts"}
    return None


# ── Keyword fallback ──────────────────────────────────────────────────────────

_KEYWORD_RULES: list[tuple[list[str], str]] = [
    (["swap", "exchange", "convert", "trade"],              "SWAP"),
    (["add liquidity", "provide liquidity", " lp ", "pool", "liquidity provider"],
                                                            "PROVIDE_LIQUIDITY"),
    (["stake", "staking", "restake", "restaking"],          "STAKE"),
    (["borrow", "loan", "collateral", "leverage"],          "BORROW"),
    (["lend", "deposit", "supply", "earn"],                 "LEND"),
    (["claim", "harvest", "collect reward"],                "CLAIM_REWARDS"),
    (["bridge", "cross-chain", "transfer across"],          "BRIDGE"),
    (["portfolio", "balance", "holdings", "positions"],     "PORTFOLIO_CHECK"),
    (["apy", "yield", "best rate", "earn", "highest"],      "YIELD_HUNT"),
    (["risk", "safe", "audit", "exploit", "dangerous"],     "RISK_ASSESSMENT"),
]


def _keyword_classify(query: str) -> str:
    q = query.lower()
    for keywords, intent in _KEYWORD_RULES:
        for kw in keywords:
            if kw in q:
                return intent
    return "OTHER"


# ── Claude Haiku classification ───────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a DeFi intent classifier. Given a user query, classify it into exactly one "
    "primary intent and optionally one secondary intent.\n\n"
    "Primary intents: SWAP, PROVIDE_LIQUIDITY, STAKE, BORROW, LEND, CLAIM_REWARDS, "
    "BRIDGE, PORTFOLIO_CHECK, YIELD_HUNT, RISK_ASSESSMENT, OTHER\n\n"
    'Return JSON only:\n{"primary": "INTENT", "secondary": "INTENT_OR_NULL", '
    '"confidence": 0.0-1.0, "suggested_action": "1 sentence"}'
)


def _call_haiku(query: str) -> dict | None:
    """Call Claude Haiku to classify the query.  Returns parsed dict or None."""
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=256,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": query}],
        )
        text = (response.content[0].text or "").strip()
        # Strip optional markdown fences
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        parsed = json.loads(text)
        # Validate primary intent
        primary = str(parsed.get("primary", "OTHER")).upper()
        if primary not in VALID_INTENTS:
            primary = "OTHER"
        secondary = str(parsed.get("secondary") or "").upper() or None
        if secondary and secondary not in VALID_INTENTS:
            secondary = None
        confidence = float(parsed.get("confidence", 0.7))
        confidence = max(0.0, min(1.0, confidence))
        suggested  = str(parsed.get("suggested_action", ""))
        return {
            "primary":          primary,
            "secondary":        secondary,
            "confidence":       round(confidence, 2),
            "suggested_action": suggested,
            "source":           "claude_haiku",
        }
    except Exception as e:
        logger.debug("[IntentClassifier] Claude Haiku call failed: %s", e)
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def classify_defi_intent(user_query: str) -> dict:
    """Map a user's DeFi question to a structured intent taxonomy.

    Returns:
        {
            "primary":          str   — one of VALID_INTENTS
            "secondary":        str | None
            "confidence":       float  — 0.0-1.0
            "suggested_action": str
            "source":           "claude_haiku" | "keyword_fallback"
        }
    Wraps everything in try/except — never raises.
    """
    try:
        if not user_query or not user_query.strip():
            return {
                "primary": "OTHER", "secondary": None,
                "confidence": 0.0, "suggested_action": "",
                "source": "keyword_fallback",
            }

        key = _cache_key(user_query)
        cached = _is_cached(key)
        if cached:
            return cached

        # Try Claude Haiku first
        result = _call_haiku(user_query)

        # Keyword fallback
        if result is None:
            intent = _keyword_classify(user_query)
            result = {
                "primary":          intent,
                "secondary":        None,
                "confidence":       0.6,
                "suggested_action": _default_action(intent, user_query),
                "source":           "keyword_fallback",
            }

        # Cache with timestamp
        _intent_cache[key] = {**result, "_ts": time.time()}
        return result

    except Exception as e:
        logger.warning("[IntentClassifier] classify_defi_intent error: %s", e)
        return {
            "primary": "OTHER", "secondary": None,
            "confidence": 0.0, "suggested_action": "",
            "source": "keyword_fallback",
        }


def _default_action(intent: str, query: str) -> str:
    """Return a one-sentence suggested action based on intent keyword."""
    _actions = {
        "SWAP":              "Use a DEX aggregator like 1inch or Uniswap to swap tokens at best rates.",
        "PROVIDE_LIQUIDITY": "Browse the Opportunities tab to find the best LP pools for your risk level.",
        "STAKE":             "Check the top staking yields in the Opportunities tab.",
        "BORROW":            "Compare borrow rates on Aave v3 or Compound v3.",
        "LEND":              "Deposit stablecoins into Aave or Morpho for passive yield.",
        "CLAIM_REWARDS":     "Connect your wallet and claim pending rewards from your positions.",
        "BRIDGE":            "Use a cross-chain bridge like Li.Fi or Stargate to move funds.",
        "PORTFOLIO_CHECK":   "Visit the Portfolio tab to see all your positions and P&L.",
        "YIELD_HUNT":        "Check the Opportunities tab sorted by APY to find the best current yields.",
        "RISK_ASSESSMENT":   "Review protocol risk scores in the Opportunities tab.",
        "OTHER":             "Explore the Dashboard for a full overview of current DeFi opportunities.",
    }
    return _actions.get(intent, _actions["OTHER"])

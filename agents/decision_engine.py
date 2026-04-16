"""
agents/decision_engine.py — Claude-powered trade decision maker.

Uses claude-sonnet-4-6 with a conservative system prompt.
Returns a structured TradeDecision that the RiskGuard validates before execution.

The AI sees: market context, open positions, limits, opportunities.
The AI does NOT see: private keys, config.py limits, wallet addresses.
The RiskGuard is the final authority — the AI is advisory only.
"""

import json
import math
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

# Get API key, master switch, and model IDs from main config
try:
    from config import ANTHROPIC_API_KEY, ANTHROPIC_ENABLED, CLAUDE_MODEL
except ImportError:
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_ENABLED = False
    CLAUDE_MODEL      = "claude-sonnet-4-6"

# ─── Credit exhaustion circuit breaker ───────────────────────────────────────
# Mirrors the pattern used in llm_analysis.py.
# Once credit exhaustion is detected (HTTP 400 with "credit" body), all
# subsequent decide() calls return HOLD immediately without touching the API.
# Cleared on next app restart. Re-enable by funding credits + restarting.
# NOTE: initialised False — only set True when exhaustion is actually detected,
# regardless of ANTHROPIC_ENABLED (enabling AI at runtime must not stay frozen).
import threading as _threading
_credits_exhausted: bool = False
_credits_lock = _threading.Lock()

_SYSTEM_PROMPT = """You are a conservative autonomous DeFi yield optimizer.
Your job: analyze the market context and make ONE clear decision per cycle.

HARD RULES — never violate these:
1. Only recommend protocols from whitelisted_protocols in the context
2. size_usd must be <= limits.max_trade_usd (never exceed this)
3. If limits.open_slots == 0: action must be HOLD or EXIT_POSITION only
4. If limits.remaining_daily_loss_usd <= 0: action must be HOLD
5. Confidence < 0.65 → action must be HOLD
6. Never recommend leverage, perps, or borrowed funds
7. Prefer Kinetic lending (safest) over LP positions (higher risk) when uncertain
8. EXIT_POSITION if unrealized_pnl on any position is < -3% of size

MARKET ENVIRONMENT (composite_signal):
- The context includes market_context.composite_signal with score (-1.0 to +1.0)
- score < -0.3 (RISK_OFF): prefer HOLD or conservative lending; avoid new LP positions
- score -0.3 to +0.1 (NEUTRAL): normal operation; all whitelisted protocols eligible
- score > +0.3 (RISK_ON): higher conviction for LP and yield positions acceptable
- Always read the signal label and layer scores before deciding

SPECTRA PROTOCOL RULES (position_type matters):
- PT (Principal Token): fixed yield to maturity; safe to hold; include maturity_date in JSON
- YT (Yield Token): MUST include maturity_date; never enter within 21 days of maturity
- LP: include maturity_date; prefer pools with >30 days to maturity
- Always include maturity_date field when protocol == "spectra"

DECISION PRIORITY:
1. HOLD — when market is unclear, confidence is low, composite_signal is RISK_OFF, or no opportunity beats current positions
2. EXIT_POSITION — when existing positions should be closed
3. REBALANCE — when capital should move between existing positions
4. ENTER_POSITION — when a clear superior opportunity exists

EXTENDED REASONING REQUIREMENTS:
In your reasoning field, always include:
1. What the composite market signal says (score + interpretation)
2. Why THIS specific opportunity was chosen over alternatives
3. What would change this decision (key risk factors)

Respond with ONLY valid JSON. No markdown. No explanation outside the JSON.
Use exactly this schema:
{
  "action": "ENTER_POSITION" | "EXIT_POSITION" | "REBALANCE" | "HOLD",
  "chain": "flare" | "xrpl" | "none",
  "protocol": "kinetic" | "blazeswap" | "sparkdex" | "enosys" | "clearpool" | "spectra" | "xrpl_dex" | "xrpl_amm" | "none",
  "pool": "<pool name, empty string if HOLD>",
  "token_in": "<token symbol, empty if HOLD>",
  "token_out": "<token symbol, empty if HOLD>",
  "size_usd": <number, 0 if HOLD>,
  "expected_apy": <annualised % as decimal e.g. 0.085 for 8.5%, 0 if HOLD>,
  "confidence": <0.0 to 1.0>,
  "reasoning": "<1-4 sentences: market signal interpretation + choice rationale + key risk>",
  "risk_factors": ["<factor>", "<factor>"],
  "position_type": "<PT|YT|LP|LENDING|STAKING|empty>",
  "maturity_date": "<YYYY-MM-DD for Spectra positions, empty otherwise>",
  "composite_score": <copy market_context.composite_signal.score here, 0 if unavailable>,
  "alternatives_considered": ["<protocol considered but rejected>"]
}"""


@dataclass
class TradeDecision:
    action:       str
    chain:        str
    protocol:     str
    pool:         str
    token_in:     str
    token_out:    str
    size_usd:     float
    expected_apy: float
    confidence:   float
    reasoning:    str
    risk_factors:           list  = field(default_factory=list)
    position_type:          str   = ""          # PT | YT | LP | LENDING | STAKING
    maturity_date:          str   = ""          # YYYY-MM-DD for Spectra
    composite_score:        float = 0.0         # composite signal score at decision time
    alternatives_considered: list = field(default_factory=list)
    raw_response: str  = ""
    error:        str  = ""

    @property
    def is_hold(self) -> bool:
        return self.action == "HOLD"

    def to_dict(self) -> dict:
        return {
            "action":                  self.action,
            "chain":                   self.chain,
            "protocol":                self.protocol,
            "pool":                    self.pool,
            "token_in":                self.token_in,
            "token_out":               self.token_out,
            "size_usd":                self.size_usd,
            "expected_apy":            self.expected_apy,
            "confidence":              self.confidence,
            "reasoning":               self.reasoning,
            "risk_factors":            self.risk_factors,
            "position_type":           self.position_type,
            "maturity_date":           self.maturity_date,
            "composite_score":         self.composite_score,
            "alternatives_considered": self.alternatives_considered,
        }


def _safe_float(val, default: float = 0.0) -> float:
    """Convert val to float; return default if result is NaN, Inf, or conversion fails."""
    try:
        result = float(val or 0)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def _hold(reason: str) -> TradeDecision:
    """Return a safe HOLD decision."""
    return TradeDecision(
        action="HOLD", chain="none", protocol="none",
        pool="", token_in="", token_out="",
        size_usd=0, expected_apy=0, confidence=0,
        reasoning=reason, risk_factors=[],
        error=reason,
    )


def _parse_response(text: str) -> TradeDecision:
    """Parse Claude's JSON response into a TradeDecision."""
    # Strip markdown fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(l for l in lines if not l.startswith("```"))

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"Expected JSON object, got {type(data).__name__}")
    except (json.JSONDecodeError, ValueError) as _je:
        import logging as _log
        _log.getLogger(__name__).warning("[DecisionEngine] Claude JSON parse failed: %s", _je)
        return _hold(f"Decision parse failed: {_je}")

    confidence = float(data.get("confidence", 0))
    action     = str(data.get("action", "HOLD")).upper().strip()

    # Enforce confidence gate: any action other than HOLD requires confidence >= 0.65
    # The system prompt instructs the model to self-enforce this, but we verify here
    # so a miscalibrated model response cannot trigger a trade below threshold.
    if confidence < 0.65 and action not in ("HOLD",):
        action = "HOLD"

    return TradeDecision(
        action                 = action,
        chain                  = str(data.get("chain") or "none").lower().strip(),
        protocol               = str(data.get("protocol") or "none").lower().strip(),
        pool                   = str(data.get("pool") or ""),
        token_in               = str(data.get("token_in") or ""),
        token_out              = str(data.get("token_out") or ""),
        size_usd               = _safe_float(data.get("size_usd")),
        expected_apy           = _safe_float(data.get("expected_apy")),
        confidence             = confidence,
        reasoning              = str(data.get("reasoning") or ""),
        risk_factors           = list(data.get("risk_factors") or []),
        position_type          = str(data.get("position_type") or ""),
        maturity_date          = str(data.get("maturity_date") or ""),
        composite_score        = float(data.get("composite_score") or 0),
        alternatives_considered= list(data.get("alternatives_considered") or []),
        raw_response           = text,
    )


class DecisionEngine:
    """
    Calls Claude claude-sonnet-4-6 with market context and returns a TradeDecision.
    Falls back to HOLD on any error — never lets a bad API response trigger a trade.
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client  = None
        if ANTHROPIC_ENABLED and _ANTHROPIC_OK and self._api_key:
            try:
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except Exception as _e:
                import logging as _log
                _log.getLogger(__name__).warning("[DecisionEngine] Anthropic client init failed: %s", _e)

    def is_available(self) -> bool:
        return ANTHROPIC_ENABLED and self._client is not None

    def decide(self, context: dict) -> TradeDecision:
        """
        Call Claude with market context, parse structured JSON response.
        Always returns a valid TradeDecision — falls back to HOLD on any failure.
        """
        if not self.is_available():
            return _hold("Claude API unavailable — no API key or anthropic package missing")

        # Credit exhaustion circuit breaker — avoid repeated API calls after exhaustion
        global _credits_exhausted
        with _credits_lock:
            if _credits_exhausted:
                return _hold("Claude API credit balance exhausted — fund credits and restart")

        context_json = json.dumps(context, indent=2, default=str)
        user_message = (
            f"Here is the current market context. Make your decision now.\n\n"
            f"```json\n{context_json}\n```"
        )

        try:
            response = self._client.messages.create(
                model     = CLAUDE_MODEL,
                max_tokens = 512,
                system    = _SYSTEM_PROMPT,
                messages  = [{"role": "user", "content": user_message}],
            )
            raw = (response.content[0].text if response.content and hasattr(response.content[0], "text") else "")
            if not raw.strip():
                return _hold("Claude returned empty response")

            decision = _parse_response(raw)

            # Safety: if Claude somehow returned a non-HOLD with no reasoning
            if not decision.is_hold and not decision.reasoning:
                return _hold("Claude returned action with no reasoning — defaulting to HOLD")

            return decision

        except json.JSONDecodeError as e:
            return _hold(f"Claude response was not valid JSON: {e}")
        except Exception as e:
            err_str = str(e)
            # Detect credit exhaustion (HTTP 400 with "credit balance" in body)
            if "credit" in err_str.lower() and ("400" in err_str or "balance" in err_str.lower()):
                with _credits_lock:
                    _credits_exhausted = True
                import logging
                logging.getLogger(__name__).info(
                    "[DecisionEngine] Claude credit balance exhausted — disabling AI calls"
                )
                return _hold("Claude API credit balance exhausted — fund credits and restart")
            return _hold(f"Claude API error: {type(e).__name__}: {e}")

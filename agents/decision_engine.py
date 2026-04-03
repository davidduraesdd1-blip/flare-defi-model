"""
agents/decision_engine.py — Claude-powered trade decision maker.

Uses claude-sonnet-4-6 with a conservative system prompt.
Returns a structured TradeDecision that the RiskGuard validates before execution.

The AI sees: market context, open positions, limits, opportunities.
The AI does NOT see: private keys, config.py limits, wallet addresses.
The RiskGuard is the final authority — the AI is advisory only.
"""

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import anthropic
    _ANTHROPIC_OK = True
except ImportError:
    _ANTHROPIC_OK = False

# Get API key from main config
try:
    from config import ANTHROPIC_API_KEY
except ImportError:
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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

DECISION PRIORITY:
1. HOLD — when market is unclear, confidence is low, or no opportunity beats current positions
2. EXIT_POSITION — when existing positions should be closed
3. REBALANCE — when capital should move between existing positions
4. ENTER_POSITION — when a clear superior opportunity exists

Respond with ONLY valid JSON. No markdown. No explanation outside the JSON.
Use exactly this schema:
{
  "action": "ENTER_POSITION" | "EXIT_POSITION" | "REBALANCE" | "HOLD",
  "chain": "flare" | "xrpl" | "none",
  "protocol": "kinetic" | "blazeswap" | "sparkdex" | "xrpl_dex" | "xrpl_amm" | "none",
  "pool": "<pool name, empty string if HOLD>",
  "token_in": "<token symbol, empty if HOLD>",
  "token_out": "<token symbol, empty if HOLD>",
  "size_usd": <number, 0 if HOLD>,
  "expected_apy": <annualised % as decimal e.g. 0.085 for 8.5%, 0 if HOLD>,
  "confidence": <0.0 to 1.0>,
  "reasoning": "<plain English, 1-3 sentences explaining the decision>",
  "risk_factors": ["<factor>", "<factor>"]
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
    risk_factors: list = field(default_factory=list)
    raw_response: str  = ""
    error:        str  = ""

    @property
    def is_hold(self) -> bool:
        return self.action == "HOLD"

    def to_dict(self) -> dict:
        return {
            "action":       self.action,
            "chain":        self.chain,
            "protocol":     self.protocol,
            "pool":         self.pool,
            "token_in":     self.token_in,
            "token_out":    self.token_out,
            "size_usd":     self.size_usd,
            "expected_apy": self.expected_apy,
            "confidence":   self.confidence,
            "reasoning":    self.reasoning,
            "risk_factors": self.risk_factors,
        }


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

    data = json.loads(text)

    return TradeDecision(
        action        = str(data.get("action", "HOLD")).upper().strip(),
        chain         = str(data.get("chain", "none")).lower().strip(),
        protocol      = str(data.get("protocol", "none")).lower().strip(),
        pool          = str(data.get("pool", "")),
        token_in      = str(data.get("token_in", "")),
        token_out     = str(data.get("token_out", "")),
        size_usd      = float(data.get("size_usd", 0)),
        expected_apy  = float(data.get("expected_apy", 0)),
        confidence    = float(data.get("confidence", 0)),
        reasoning     = str(data.get("reasoning", "")),
        risk_factors  = list(data.get("risk_factors", [])),
        raw_response  = text,
    )


class DecisionEngine:
    """
    Calls Claude claude-sonnet-4-6 with market context and returns a TradeDecision.
    Falls back to HOLD on any error — never lets a bad API response trigger a trade.
    """

    def __init__(self, api_key: str = ""):
        self._api_key = api_key or ANTHROPIC_API_KEY or os.environ.get("ANTHROPIC_API_KEY", "")
        self._client  = None
        if _ANTHROPIC_OK and self._api_key:
            try:
                self._client = anthropic.Anthropic(api_key=self._api_key)
            except Exception:
                pass

    def is_available(self) -> bool:
        return self._client is not None

    def decide(self, context: dict) -> TradeDecision:
        """
        Call Claude with market context, parse structured JSON response.
        Always returns a valid TradeDecision — falls back to HOLD on any failure.
        """
        if not self.is_available():
            return _hold("Claude API unavailable — no API key or anthropic package missing")

        context_json = json.dumps(context, indent=2, default=str)
        user_message = (
            f"Here is the current market context. Make your decision now.\n\n"
            f"```json\n{context_json}\n```"
        )

        try:
            response = self._client.messages.create(
                model     = "claude-sonnet-4-6",
                max_tokens = 512,
                system    = _SYSTEM_PROMPT,
                messages  = [{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text if response.content else ""
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
            return _hold(f"Claude API error: {type(e).__name__}: {e}")

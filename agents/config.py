"""
agents/config.py — Hardcoded risk limits and agent constants.

CRITICAL: This file is the single source of truth for ALL risk limits.
The AI decision engine NEVER reads or modifies this file.
The RiskGuard enforces these limits independently before any execution.
Changing a value here changes it everywhere instantly.
"""

import json
import os
from pathlib import Path

# ─── Operating Mode ───────────────────────────────────────────────────────────
# PAPER        → simulate trades only, zero real transactions (default, always safe)
# LIVE_PHASE2  → real execution, hard $1,000 cap, requires 14-day paper gate
# LIVE_PHASE3  → real execution at scale, limits scale proportionally
#
# To change: set env var AGENT_MODE=LIVE_PHASE2 (never commit live keys to git)
# The UI also manages this; env var takes precedence.
OPERATING_MODE: str = os.environ.get("AGENT_MODE", "PAPER").upper()
assert OPERATING_MODE in ("PAPER", "LIVE_PHASE2", "LIVE_PHASE3"), \
    f"Invalid AGENT_MODE: {OPERATING_MODE}"

# ─── Paper Trading Settings ───────────────────────────────────────────────────
PAPER_STARTING_BALANCE_USD: float = 100_000.0   # virtual paper balance
PAPER_TRADING_GATE_DAYS: int     = 14           # days of paper required before live unlock

# ─── Phase 2 Hard Cap ─────────────────────────────────────────────────────────
# In LIVE_PHASE2, the bot wallet is pre-funded with exactly this amount.
# The bot will refuse any trade that would take total deployed capital above this.
PHASE2_WALLET_CAP_USD: float = 1_000.0

# ─── Position Sizing ──────────────────────────────────────────────────────────
# All percentages are of CURRENT wallet balance (recalculated every cycle).
MAX_TRADE_SIZE_PCT:    float = 0.02   # 2% per trade  (Quarter-Kelly for unproven model)
MAX_OPEN_POSITIONS:    int   = 3      # never more than 3 simultaneous positions
MIN_TRADE_SIZE_USD:    float = 5.0    # ignore signals smaller than this (gas inefficient)

# ─── Loss Limits ──────────────────────────────────────────────────────────────
MAX_DAILY_LOSS_PCT:   float = 0.02   # 2% daily → bot pauses until next UTC day
MAX_DRAWDOWN_PCT:     float = 0.10   # 10% from peak → full stop, manual restart required
COOLDOWN_AFTER_LOSS_SECONDS: int = 3600  # 60-min pause after any losing trade

# ─── Trade Quality Filters ────────────────────────────────────────────────────
MIN_NET_PROFIT_PCT:   float = 0.003   # 0.3% minimum expected profit after gas
MAX_SLIPPAGE_PCT:     float = 0.005   # 0.5% max tolerated slippage
MIN_CONFIDENCE:       float = 0.60    # Claude confidence below this → HOLD
MAX_REASONABLE_APY:   float = 2.00    # 200% cap — reject obviously wrong signals

# ─── Protocol Safety Filters ──────────────────────────────────────────────────
MIN_PROTOCOL_TVL_USD:    float = 1_000_000   # $1M minimum TVL
MIN_PROTOCOL_AGE_DAYS:   int   = 180         # 6-month minimum protocol age
AUDITED_ONLY:            bool  = True        # reject unaudited protocols unconditionally

# ─── Whitelisted Protocols ────────────────────────────────────────────────────
# ONLY these protocols can receive execution instructions.
# Adding a new protocol requires a code change here + approval. Never dynamic.
FLARE_PROTOCOL_WHITELIST: frozenset = frozenset({
    "kinetic",    # Lending — lowest risk, start here
    "blazeswap",  # DEX AMM — Uniswap V2 fork, audited
    "sparkdex",   # DEX CL — Uniswap V3 fork, audited
})

XRPL_PROTOCOL_WHITELIST: frozenset = frozenset({
    "xrpl_dex",   # Native CLOB order book
    "xrpl_amm",   # XLS-30 AMM (AMMDeposit/AMMWithdraw)
})

ALL_WHITELISTED_PROTOCOLS: frozenset = FLARE_PROTOCOL_WHITELIST | XRPL_PROTOCOL_WHITELIST

# ─── Whitelisted Actions ──────────────────────────────────────────────────────
ALLOWED_ACTIONS: frozenset = frozenset({
    "ENTER_POSITION",
    "EXIT_POSITION",
    "REBALANCE",
    "HOLD",
})

# ─── Flare Contract Addresses ─────────────────────────────────────────────────
# These MUST be verified from official protocol documentation before Phase 2.
# Wrong addresses = lost funds. Double-check every address before going live.
FLARE_CONTRACTS: dict = {
    # BlazeSwap — Uniswap V2 fork on Flare mainnet
    # Verify at: https://app.blazeswap.xyz / GitHub: blazeswap
    "blazeswap_router": os.environ.get(
        "BLAZESWAP_ROUTER_ADDRESS",
        "0xF5c69e34e7b36bA6C5cBaBfFDcc9Eb7B56B27254",  # TODO: verify before Phase 2
    ),
    # SparkDEX — Uniswap V3 fork on Flare mainnet
    # Verify at: https://sparkdex.ai / docs.sparkdex.ai
    "sparkdex_router": os.environ.get(
        "SPARKDEX_ROUTER_ADDRESS",
        "",  # TODO: set SPARKDEX_ROUTER_ADDRESS env var before Phase 2
    ),
    # Kinetic Finance — Compound V2 fork on Flare mainnet
    # From official docs.kinetic.market — verified
    "kinetic_comptroller": "0xeC7e541375D70c37262f619162502dB9131d6db5",
    "kinetic_kFLR":   "0xb84F771305d10607Dd086B2f89712c0CeD379407",
    "kinetic_kUSDT0": "0x76809aBd690B77488Ffb5277e0a8300a7e77B779",
    "kinetic_ksFLR":  "0x291487beC339c2fE5D83DD45F0a15EFC9Ac45656",
}

# ─── XRPL Settings ────────────────────────────────────────────────────────────
XRPL_NODE_URL: str  = "wss://xrplcluster.com"   # Ripple managed — most stable
XRPL_NODE_FALLBACK: str = "wss://s1.ripple.com"
# Only trade these pairs on XRPL — tightly scoped for Phase 2
XRPL_ALLOWED_PAIRS: frozenset = frozenset({
    "XRP/RLUSD",
    "XRP/USD",
})

# ─── Flare RPC ────────────────────────────────────────────────────────────────
FLARE_RPC_URLS: list = [
    "https://flare-api.flare.network/ext/C/rpc",
    "https://rpc.ankr.com/flare",
    "https://flare.public-rpc.com",
]
FLARE_CHAIN_ID: int = 14

# ─── Decision Loop ────────────────────────────────────────────────────────────
DECISION_LOOP_INTERVAL_SECONDS: int = 300   # 5-minute cycle
MAX_CONSECUTIVE_ERRORS: int        = 5      # pause loop after this many errors in a row

# ─── Wallet Storage ───────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).parent.parent
AGENT_DATA_DIR = _BASE_DIR / "data" / "agent"
AGENT_DATA_DIR.mkdir(parents=True, exist_ok=True)

WALLET_FILE:      Path = AGENT_DATA_DIR / "wallets.enc"     # AES-256-GCM encrypted
AGENT_STATE_FILE: Path = AGENT_DATA_DIR / "agent_state.json"
AGENT_DB_FILE:    Path = AGENT_DATA_DIR / "agent.db"

# ─── KDF Parameters (PBKDF2-HMAC-SHA256) ──────────────────────────────────────
# 480,000 iterations — OWASP 2024 recommended minimum for SHA-256
KDF_ITERATIONS: int   = 480_000
KDF_SALT_BYTES: int   = 32
AES_KEY_BYTES:  int   = 32   # AES-256
AES_NONCE_BYTES: int  = 12   # 96-bit nonce for GCM

# ─── Phase Gate State Key ─────────────────────────────────────────────────────
# Stored in agent_state.json — tracks paper trading days completed
PHASE_GATE_KEY: str      = "paper_days_completed"
LIVE_UNLOCK_KEY: str     = "live_manually_unlocked"
EMERGENCY_STOP_KEY: str  = "emergency_stop_active"

# ─── User Config Overrides (from Settings page UI) ────────────────────────────
# Users can adjust agent behaviour from the Settings page without editing code.
# Overrides are stored in agent_overrides.json and applied at every cycle start.
# Only numeric/bool constants are patchable. Whitelists are never overridable.
AGENT_OVERRIDES_FILE: Path = AGENT_DATA_DIR / "agent_overrides.json"

# Keys that are safe to override from the UI (must be numeric or bool)
_OVERRIDABLE_KEYS: frozenset = frozenset({
    "MAX_TRADE_SIZE_PCT",
    "MAX_DAILY_LOSS_PCT",
    "MAX_DRAWDOWN_PCT",
    "MIN_CONFIDENCE",
    "MAX_OPEN_POSITIONS",
    "COOLDOWN_AFTER_LOSS_SECONDS",
    "PAPER_STARTING_BALANCE_USD",
    "PHASE2_WALLET_CAP_USD",
    "MIN_TRADE_SIZE_USD",
    "MIN_NET_PROFIT_PCT",
    "MAX_SLIPPAGE_PCT",
    "MAX_REASONABLE_APY",
    "PAPER_TRADING_GATE_DAYS",
})


def _apply_overrides() -> None:
    """
    Load user overrides from the Settings page and patch module-level constants.
    Called at the START of every decision cycle by agent_runner.py.
    Safe to call repeatedly — only patches values in _OVERRIDABLE_KEYS.
    Never crashes the agent on failure.
    """
    try:
        if not AGENT_OVERRIDES_FILE.exists():
            return
        data = json.loads(AGENT_OVERRIDES_FILE.read_text(encoding="utf-8"))
        g = globals()
        for key, val in data.items():
            if key in _OVERRIDABLE_KEYS and key in g:
                g[key] = type(g[key])(val)   # cast to original type (float/int/bool)
    except Exception:
        pass  # Never let override loading crash the agent cycle


def save_overrides(overrides: dict) -> None:
    """Write user overrides to the overrides file. Called from Settings page."""
    try:
        AGENT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        AGENT_OVERRIDES_FILE.write_text(
            json.dumps(overrides, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


def load_overrides() -> dict:
    """Read current overrides from file. Returns {} if no file or parse error."""
    try:
        if AGENT_OVERRIDES_FILE.exists():
            return json.loads(AGENT_OVERRIDES_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

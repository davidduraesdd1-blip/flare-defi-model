"""
agentkit_wallet.py — Flare DeFi Model
Coinbase AgentKit EVM wallet integration.

Provides a persistent wallet for the DeFi model to:
  - Display current wallet balance (ETH/USDC on Base, FLR on Flare EVM)
  - Check gas estimates before recommending positions
  - (Future) Execute small test transactions to verify yield protocols

Requires a Coinbase Developer Platform (CDP) API key:
  https://portal.cdp.coinbase.com/

Setup:
  1. Create a CDP project at portal.cdp.coinbase.com
  2. Generate an API key (free tier supports wallet operations)
  3. Set in your .env file:
       CDP_API_KEY_NAME=<your key name>
       CDP_API_KEY_PRIVATE_KEY=<your private key>
       AGENTKIT_NETWORK=base-mainnet   # or flare-mainnet

Supported networks:
  - base-mainnet  (Coinbase L2, EVM-compatible)
  - base-sepolia  (testnet)
  - ethereum-mainnet
  (Flare is EVM-compatible but not yet a named AgentKit network;
   use ethereum-mainnet with custom RPC for Flare wallet ops)

Dependencies (install when ready):
  pip install coinbase-agentkit
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from config import CDP_API_KEY_NAME, CDP_API_KEY_PRIVATE, AGENTKIT_NETWORK, AGENTKIT_WALLET_FILE

logger = logging.getLogger(__name__)

# ─── Availability Guard ────────────────────────────────────────────────────────
try:
    from coinbase_agentkit import CoinbaseAgentkit, CoinbaseAgentkitOptions, WalletData
    _AGENTKIT_AVAILABLE = True
except ImportError:
    _AGENTKIT_AVAILABLE = False
    logger.debug("[AgentKit] coinbase-agentkit not installed — pip install coinbase-agentkit")

_wallet_lock = threading.Lock()
_wallet_instance: Optional[object] = None   # cached AgentKit wallet


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _is_configured() -> bool:
    """Return True if CDP API credentials are set."""
    return bool(CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE)


def _load_saved_wallet() -> Optional[dict]:
    """Load persisted wallet data from JSON file (seed for next session)."""
    try:
        if AGENTKIT_WALLET_FILE.exists():
            with open(AGENTKIT_WALLET_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.warning("[AgentKit] Could not load wallet file: %s", e)
    return None


def _save_wallet(wallet_data: dict):
    """Persist wallet seed data (address, keys) for next session."""
    try:
        AGENTKIT_WALLET_FILE.parent.mkdir(parents=True, exist_ok=True)
        import tempfile
        fd, tmp = tempfile.mkstemp(dir=AGENTKIT_WALLET_FILE.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(wallet_data, f, indent=2)
        os.replace(tmp, AGENTKIT_WALLET_FILE)
        logger.debug("[AgentKit] Wallet saved to %s", AGENTKIT_WALLET_FILE)
    except Exception as e:
        logger.warning("[AgentKit] Could not save wallet: %s", e)


# ─── Wallet Initialization ────────────────────────────────────────────────────

def get_wallet() -> Optional[object]:
    """
    Return a cached Coinbase AgentKit wallet instance.
    Creates a new wallet on first call; loads existing wallet on subsequent calls.

    Returns:
        AgentKit wallet object, or None if not configured / package not installed.
    """
    global _wallet_instance
    if not _AGENTKIT_AVAILABLE:
        return None
    if not _is_configured():
        return None

    with _wallet_lock:
        if _wallet_instance is not None:
            return _wallet_instance

        try:
            saved = _load_saved_wallet()
            opts = CoinbaseAgentkitOptions(
                cdp_api_key_name=CDP_API_KEY_NAME,
                cdp_api_key_private_key=CDP_API_KEY_PRIVATE,
                network_id=AGENTKIT_NETWORK,
            )
            if saved:
                logger.info("[AgentKit] Restoring existing wallet on %s", AGENTKIT_NETWORK)
                wallet_data = WalletData.from_dict(saved)
                kit = CoinbaseAgentkit(opts, wallet_data=wallet_data)
            else:
                logger.info("[AgentKit] Creating new wallet on %s", AGENTKIT_NETWORK)
                kit = CoinbaseAgentkit(opts)
                # Persist for next session
                _save_wallet(kit.wallet.export_data().to_dict())

            _wallet_instance = kit
            return kit
        except Exception as e:
            logger.error("[AgentKit] Wallet init failed: %s", e)
            return None


# ─── Wallet Operations ────────────────────────────────────────────────────────

def get_wallet_status() -> dict:
    """
    Return current wallet status: address, network, balances.

    Returns:
        dict with:
          available     : bool — True if AgentKit is configured and connected
          address       : str | None — wallet address
          network       : str — configured network
          balances      : dict {token: amount_str}
          error         : str | None
    """
    status = {
        "available": False,
        "address":   None,
        "network":   AGENTKIT_NETWORK,
        "balances":  {},
        "error":     None,
    }

    if not _AGENTKIT_AVAILABLE:
        status["error"] = "coinbase-agentkit not installed (pip install coinbase-agentkit)"
        return status

    if not _is_configured():
        status["error"] = (
            "CDP API key not set. Add CDP_API_KEY_NAME and CDP_API_KEY_PRIVATE_KEY "
            "to your .env file. Get a free key at portal.cdp.coinbase.com"
        )
        return status

    kit = get_wallet()
    if kit is None:
        status["error"] = "AgentKit wallet initialization failed — check logs"
        return status

    try:
        wallet = kit.wallet
        status["available"] = True
        status["address"]   = str(wallet.default_address)
        # Fetch balances
        balances = wallet.balances()
        status["balances"] = {str(k): str(v) for k, v in (balances or {}).items()}
    except Exception as e:
        status["error"] = f"Wallet query failed: {e}"

    return status


def get_wallet_address() -> Optional[str]:
    """Return the wallet's EVM address, or None if unavailable."""
    status = get_wallet_status()
    return status.get("address")


def get_setup_instructions() -> str:
    """Return human-readable setup instructions for the AgentKit wallet."""
    return (
        "**Coinbase AgentKit Wallet Setup**\n\n"
        "1. Create a free account at https://portal.cdp.coinbase.com/\n"
        "2. Create a project and generate an API key\n"
        "3. Add to your `.env` file:\n"
        "   ```\n"
        "   CDP_API_KEY_NAME=your-key-name\n"
        "   CDP_API_KEY_PRIVATE_KEY=your-private-key\n"
        "   AGENTKIT_NETWORK=base-mainnet\n"
        "   ```\n"
        "4. Install the package: `pip install coinbase-agentkit`\n"
        "5. Restart the app — the wallet will be auto-created and persisted\n\n"
        "The wallet supports: Base mainnet/testnet, Ethereum mainnet, and any EVM chain.\n"
        "Flare Network is EVM-compatible and can use the same wallet address."
    )

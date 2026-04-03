"""
agents/flare_executor.py — Phase 2/3 real execution on Flare Network.

Uses web3.py to interact with SparkDEX, BlazeSwap, and Kinetic.
Only activated when OPERATING_MODE == LIVE_PHASE2 or LIVE_PHASE3.
Every transaction is gas-estimated before submission.
If gas makes the trade unprofitable, the trade is rejected.

SAFETY: This module NEVER handles raw private keys in memory longer than
needed for signing. Keys are loaded, used, and immediately discarded.
"""

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import (
    FLARE_RPC_URLS, FLARE_CHAIN_ID, FLARE_CONTRACTS,
    MAX_SLIPPAGE_PCT, MIN_NET_PROFIT_PCT, OPERATING_MODE,
)
from agents.decision_engine import TradeDecision
from agents.position_monitor import PositionMonitor
from agents.audit_log import AuditLog

_monitor = PositionMonitor()
_audit   = AuditLog()

# ─── Minimal ABIs (only the functions we call) ────────────────────────────────
_ERC20_ABI = [
    {"name": "approve",  "type": "function", "inputs": [
        {"name": "spender", "type": "address"},
        {"name": "amount",  "type": "uint256"},
    ], "outputs": [{"type": "bool"}]},
    {"name": "allowance", "type": "function", "inputs": [
        {"name": "owner",   "type": "address"},
        {"name": "spender", "type": "address"},
    ], "outputs": [{"type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "inputs": [
        {"name": "account", "type": "address"},
    ], "outputs": [{"type": "uint256"}]},
    {"name": "decimals", "type": "function", "inputs": [], "outputs": [{"type": "uint8"}]},
]

# Kinetic CErc20 ABI (Compound V2 pattern)
_KINETIC_CERC20_ABI = [
    {"name": "mint",   "type": "function", "inputs": [
        {"name": "mintAmount", "type": "uint256"}
    ], "outputs": [{"type": "uint256"}]},
    {"name": "redeem", "type": "function", "inputs": [
        {"name": "redeemTokens", "type": "uint256"}
    ], "outputs": [{"type": "uint256"}]},
    {"name": "supplyRatePerBlock", "type": "function", "inputs": [],
     "outputs": [{"type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "inputs": [
        {"name": "account", "type": "address"}
    ], "outputs": [{"type": "uint256"}]},
]

try:
    from web3 import Web3
    from web3.middleware import ExtraDataToPOAMiddleware
    _WEB3_OK = True
except ImportError:
    _WEB3_OK = False


def _get_w3() -> Optional["Web3"]:
    """Return a connected Web3 instance, trying each RPC in order."""
    if not _WEB3_OK:
        return None
    for rpc in FLARE_RPC_URLS:
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 10}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if w3.is_connected():
                return w3
        except Exception:
            continue
    return None


def _estimate_gas_usd(w3: "Web3", gas_units: int) -> float:
    """Convert gas units to USD using current gas price + FLR price."""
    try:
        gas_price_wei = w3.eth.gas_price
        gas_cost_flr  = gas_units * gas_price_wei / 1e18
        # Fetch FLR price
        import requests
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "flare-networks", "vs_currencies": "usd"},
            timeout=5,
        )
        flr_usd = r.json().get("flare-networks", {}).get("usd", 0.018) if r.status_code == 200 else 0.018
        return gas_cost_flr * flr_usd
    except Exception:
        return 0.05  # conservative $0.05 fallback


class FlareExecutor:
    """
    Executes approved trades on Flare Network.
    Only call after RiskGuard.validate() returns approved=True.
    Only active in LIVE_PHASE2 / LIVE_PHASE3 modes.
    """

    def __init__(self):
        self._w3: Optional["Web3"] = None

    def _ensure_connected(self) -> bool:
        if self._w3 and self._w3.is_connected():
            return True
        self._w3 = _get_w3()
        return self._w3 is not None

    def is_available(self) -> bool:
        if OPERATING_MODE not in ("LIVE_PHASE2", "LIVE_PHASE3"):
            return False
        if not _WEB3_OK:
            return False
        return self._ensure_connected()

    def get_flr_balance_usd(self, address: str) -> float:
        """Return current FLR balance of the agent wallet in USD."""
        if not self._ensure_connected():
            return 0.0
        try:
            import requests
            balance_wei = self._w3.eth.get_balance(Web3.to_checksum_address(address))
            balance_flr = balance_wei / 1e18
            r = requests.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "flare-networks", "vs_currencies": "usd"},
                timeout=5,
            )
            flr_usd = r.json().get("flare-networks", {}).get("usd", 0.018) if r.status_code == 200 else 0.018
            return balance_flr * flr_usd
        except Exception:
            return 0.0

    def execute_kinetic_supply(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        private_key: str,
        token_address: str,
        ktoken_address: str,
        token_decimals: int = 6,
    ) -> dict:
        """
        Supply tokens to Kinetic (Compound V2 pattern).
        This is the lowest-risk action: lending/supplying to earn yield.
        """
        if not self._ensure_connected():
            return {"status": "error", "reason": "No RPC connection"}

        w3 = self._w3
        account = w3.eth.account.from_key(private_key)
        address = account.address

        try:
            # Convert USD size to token amount (approximate using token price)
            # For stablecoins: 1 token ≈ $1
            amount_raw = int(adjusted_size_usd * (10 ** token_decimals))

            # Build approve tx
            token_contract  = w3.eth.contract(
                address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI
            )
            ktoken_contract = w3.eth.contract(
                address=Web3.to_checksum_address(ktoken_address), abi=_KINETIC_CERC20_ABI
            )

            # Estimate gas for approve
            approve_gas = token_contract.functions.approve(
                Web3.to_checksum_address(ktoken_address), amount_raw
            ).estimate_gas({"from": address})
            approve_gas_usd = _estimate_gas_usd(w3, approve_gas)

            # Estimate gas for mint (supply)
            mint_gas = ktoken_contract.functions.mint(
                amount_raw
            ).estimate_gas({"from": address})
            mint_gas_usd = _estimate_gas_usd(w3, mint_gas)

            total_gas_usd = approve_gas_usd + mint_gas_usd

            # Profitability check
            daily_yield_usd = adjusted_size_usd * decision.expected_apy / 365
            if total_gas_usd > daily_yield_usd * 7:  # gas > 1 week of yield → reject
                return {
                    "status": "rejected",
                    "reason": f"Gas (${total_gas_usd:.4f}) > 1 week yield (${daily_yield_usd*7:.4f})",
                }

            nonce = w3.eth.get_transaction_count(address)
            gas_price = w3.eth.gas_price

            # Send approve
            approve_tx = token_contract.functions.approve(
                Web3.to_checksum_address(ktoken_address), amount_raw
            ).build_transaction({
                "chainId": FLARE_CHAIN_ID,
                "from": address,
                "nonce": nonce,
                "gas": int(approve_gas * 1.2),
                "gasPrice": gas_price,
            })
            signed_approve = w3.eth.account.sign_transaction(approve_tx, private_key)
            approve_hash   = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
            w3.eth.wait_for_transaction_receipt(approve_hash, timeout=60)

            # Send mint (supply)
            mint_tx = ktoken_contract.functions.mint(amount_raw).build_transaction({
                "chainId": FLARE_CHAIN_ID,
                "from": address,
                "nonce": nonce + 1,
                "gas": int(mint_gas * 1.2),
                "gasPrice": gas_price,
            })
            signed_mint = w3.eth.account.sign_transaction(mint_tx, private_key)
            mint_hash   = w3.eth.send_raw_transaction(signed_mint.raw_transaction)
            receipt     = w3.eth.wait_for_transaction_receipt(mint_hash, timeout=120)

            if receipt.status != 1:
                return {"status": "error", "reason": "Mint transaction reverted", "tx_hash": mint_hash.hex()}

            trade_id = _monitor.open_position(
                chain=decision.chain, protocol=decision.protocol,
                pool=decision.pool, action=decision.action,
                token_in=decision.token_in, token_out=decision.token_out,
                size_usd=adjusted_size_usd, entry_price=1.0,
                expected_apy=decision.expected_apy, confidence=decision.confidence,
                reasoning=decision.reasoning, mode="LIVE",
                gas_usd=total_gas_usd, tx_hash=mint_hash.hex(),
            )
            return {
                "status":    "success",
                "trade_id":  trade_id,
                "tx_hash":   mint_hash.hex(),
                "size_usd":  adjusted_size_usd,
                "gas_usd":   round(total_gas_usd, 6),
                "protocol":  "kinetic",
            }

        except Exception as e:
            _audit.log_error(f"FlareExecutor.execute_kinetic_supply error: {e}",
                             {"decision": decision.to_dict()})
            return {"status": "error", "reason": str(e)}

    def execute(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        private_key: str,
    ) -> dict:
        """
        Route to the correct execution method based on protocol.
        Primary entry point called by agent_runner.
        """
        if OPERATING_MODE not in ("LIVE_PHASE2", "LIVE_PHASE3"):
            return {"status": "blocked", "reason": f"Mode is {OPERATING_MODE}, not LIVE"}

        proto = decision.protocol
        if proto == "kinetic":
            # Supply USDT0 (USD0) to Kinetic — safest stablecoin strategy.
            # token_address = underlying USDT0 token (USD0 on Flare mainnet)
            # ktoken_address = kUSDT0 cToken that represents the supply position
            try:
                from config import TOKENS as _TOKENS
                _underlying = _TOKENS.get("USD0", "")
            except Exception:
                _underlying = ""
            if not _underlying:
                _audit.log_error(
                    "FlareExecutor: USDT0 underlying address not found in config.TOKENS['USD0'] — "
                    "verify token address before Phase 2",
                    {"decision": decision.to_dict()}
                )
                return {
                    "status": "rejected",
                    "reason": "USDT0 underlying token address missing from config — set TOKENS['USD0'] before live execution",
                }
            return self.execute_kinetic_supply(
                decision, adjusted_size_usd, private_key,
                token_address  = _underlying,
                ktoken_address = FLARE_CONTRACTS["kinetic_kUSDT0"],
                token_decimals = 6,
            )
        else:
            # SparkDEX and BlazeSwap require verified router addresses
            # TODO: implement swap execution when router addresses are confirmed
            _audit.log_error(
                f"FlareExecutor: protocol '{proto}' execution not yet implemented",
                {"decision": decision.to_dict()}
            )
            return {
                "status": "not_implemented",
                "reason": f"Direct swap execution for '{proto}' requires verified router "
                          "address. Set SPARKDEX_ROUTER_ADDRESS or BLAZESWAP_ROUTER_ADDRESS "
                          "env vars and implement swap logic before Phase 2.",
            }

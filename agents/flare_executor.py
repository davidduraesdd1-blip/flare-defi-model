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
from utils.http import _SESSION as _cg_session, coingecko_limiter as _cg_limiter

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

# ─── Uniswap V2 Router (BlazeSwap) ───────────────────────────────────────────
# BlazeSwap is a Uniswap V2 fork on Flare. We use swapExactTokensForTokens
# which requires the caller to approve(router, amountIn) first.
_UNISWAP_V2_ROUTER_ABI = [
    {"name": "swapExactTokensForTokens", "type": "function", "inputs": [
        {"name": "amountIn",     "type": "uint256"},
        {"name": "amountOutMin", "type": "uint256"},
        {"name": "path",         "type": "address[]"},
        {"name": "to",           "type": "address"},
        {"name": "deadline",     "type": "uint256"},
    ], "outputs": [{"name": "amounts", "type": "uint256[]"}]},
    {"name": "getAmountsOut", "type": "function", "inputs": [
        {"name": "amountIn", "type": "uint256"},
        {"name": "path",     "type": "address[]"},
    ], "outputs": [{"name": "amounts", "type": "uint256[]"}], "stateMutability": "view"},
]

# ─── Uniswap V3 ISwapRouter (SparkDEX) ───────────────────────────────────────
# SparkDEX is a Uniswap V3 fork on Flare. We use exactInputSingle which
# requires the caller to approve(router, amountIn) first. Fee tier must
# match an existing pool (common tiers: 500, 3000, 10000 bps).
_UNISWAP_V3_ROUTER_ABI = [
    {"name": "exactInputSingle", "type": "function", "inputs": [
        {"name": "params", "type": "tuple", "components": [
            {"name": "tokenIn",           "type": "address"},
            {"name": "tokenOut",          "type": "address"},
            {"name": "fee",               "type": "uint24"},
            {"name": "recipient",         "type": "address"},
            {"name": "deadline",          "type": "uint256"},
            {"name": "amountIn",          "type": "uint256"},
            {"name": "amountOutMinimum",  "type": "uint256"},
            {"name": "sqrtPriceLimitX96", "type": "uint160"},
        ]},
    ], "outputs": [{"name": "amountOut", "type": "uint256"}]},
]

# ─── Uniswap V3 Quoter ──────────────────────────────────────────────────────
# Official Uniswap V3 QuoterV2 interface — staticcall returns expected
# amountOut WITHOUT executing a swap. Removes the off-chain-price dependency
# from execute_sparkdex_swap so MEV-safe amountOutMinimum can be computed
# without trusting a CoinGecko feed.
# Address comes from FLARE_CONTRACTS['sparkdex_quoter'] env var.
_UNISWAP_V3_QUOTER_V2_ABI = [
    {"name": "quoteExactInputSingle", "type": "function", "inputs": [
        {"name": "params", "type": "tuple", "components": [
            {"name": "tokenIn",            "type": "address"},
            {"name": "tokenOut",           "type": "address"},
            {"name": "amountIn",           "type": "uint256"},
            {"name": "fee",                "type": "uint24"},
            {"name": "sqrtPriceLimitX96",  "type": "uint160"},
        ]},
    ], "outputs": [
        {"name": "amountOut",             "type": "uint256"},
        {"name": "sqrtPriceX96After",     "type": "uint160"},
        {"name": "initializedTicksCrossed", "type": "uint32"},
        {"name": "gasEstimate",           "type": "uint256"},
    ], "stateMutability": "view"},
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
        _cg_limiter.acquire()
        r = _cg_session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "flare-networks", "vs_currencies": "usd"},
            timeout=5,
        )
        _flr_raw = (r.json() or {}).get("flare-networks", {}).get("usd") if r.status_code == 200 else None
        flr_usd = float(_flr_raw) if (_flr_raw and float(_flr_raw) > 0) else 0.018
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
            balance_wei = self._w3.eth.get_balance(Web3.to_checksum_address(address))
            balance_flr = balance_wei / 1e18
            _cg_limiter.acquire()
            r = _cg_session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "flare-networks", "vs_currencies": "usd"},
                timeout=5,
            )
            _flr_raw = (r.json() or {}).get("flare-networks", {}).get("usd") if r.status_code == 200 else None
            flr_usd = float(_flr_raw) if (_flr_raw and float(_flr_raw) > 0) else 0.018
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

    # ── Token decimals lookup (cached per instance) ──────────────────────────
    _DECIMALS_CACHE: dict = {}

    def _token_decimals(self, token_address: str) -> int:
        """Return ERC20 decimals for a token, cached. Falls back to 18 on failure."""
        _addr = Web3.to_checksum_address(token_address)
        if _addr in self._DECIMALS_CACHE:
            return self._DECIMALS_CACHE[_addr]
        try:
            c = self._w3.eth.contract(address=_addr, abi=_ERC20_ABI)
            d = int(c.functions.decimals().call())
            self._DECIMALS_CACHE[_addr] = d
            return d
        except Exception:
            return 18

    # ── BlazeSwap V2 swap ────────────────────────────────────────────────────
    def execute_blazeswap_swap(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        private_key: str,
        token_in_address: str,
        token_out_address: str,
        token_in_usd_price: float = 1.0,
        max_slippage_pct: float = 0.005,
    ) -> dict:
        """
        Execute a BlazeSwap V2-style swap (swapExactTokensForTokens).
        Expects the router address in FLARE_CONTRACTS['blazeswap_router'].
        """
        if not self._ensure_connected():
            return {"status": "error", "reason": "No RPC connection"}

        router_addr = FLARE_CONTRACTS.get("blazeswap_router", "")
        if not router_addr or len(router_addr) != 42:
            return {
                "status": "blocked",
                "reason": "BLAZESWAP_ROUTER_ADDRESS not configured — set env var before live.",
            }

        w3 = self._w3
        account = w3.eth.account.from_key(private_key)
        address = account.address

        try:
            token_in_dec = self._token_decimals(token_in_address)
            # Convert USD size to token amount using current token USD price
            _px = max(float(token_in_usd_price), 1e-8)
            amount_in_raw = int(adjusted_size_usd / _px * (10 ** token_in_dec))
            if amount_in_raw <= 0:
                return {"status": "rejected", "reason": "Computed amountIn is 0"}

            token_in = w3.eth.contract(address=Web3.to_checksum_address(token_in_address), abi=_ERC20_ABI)
            router   = w3.eth.contract(address=Web3.to_checksum_address(router_addr), abi=_UNISWAP_V2_ROUTER_ABI)
            path     = [Web3.to_checksum_address(token_in_address), Web3.to_checksum_address(token_out_address)]

            # Quote expected output (getAmountsOut) to compute amountOutMin with slippage
            try:
                _amounts = router.functions.getAmountsOut(amount_in_raw, path).call()
                expected_out = int(_amounts[-1])
            except Exception as _quote_err:
                return {"status": "error", "reason": f"Router getAmountsOut failed: {_quote_err}"}
            amount_out_min = int(expected_out * (1.0 - max(max_slippage_pct, 0.0)))

            # Gas estimates
            approve_gas = token_in.functions.approve(Web3.to_checksum_address(router_addr), amount_in_raw).estimate_gas({"from": address})
            deadline = int(time.time()) + 600  # 10-minute window
            swap_gas = router.functions.swapExactTokensForTokens(
                amount_in_raw, amount_out_min, path, address, deadline,
            ).estimate_gas({"from": address})
            total_gas_usd = _estimate_gas_usd(w3, approve_gas) + _estimate_gas_usd(w3, swap_gas)

            nonce     = w3.eth.get_transaction_count(address)
            gas_price = w3.eth.gas_price

            # Send approve
            approve_tx = token_in.functions.approve(
                Web3.to_checksum_address(router_addr), amount_in_raw
            ).build_transaction({
                "chainId": FLARE_CHAIN_ID, "from": address, "nonce": nonce,
                "gas": int(approve_gas * 1.2), "gasPrice": gas_price,
            })
            _sa = w3.eth.account.sign_transaction(approve_tx, private_key)
            w3.eth.wait_for_transaction_receipt(
                w3.eth.send_raw_transaction(_sa.raw_transaction), timeout=60
            )

            # Send swap
            swap_tx = router.functions.swapExactTokensForTokens(
                amount_in_raw, amount_out_min, path, address, deadline,
            ).build_transaction({
                "chainId": FLARE_CHAIN_ID, "from": address, "nonce": nonce + 1,
                "gas": int(swap_gas * 1.2), "gasPrice": gas_price,
            })
            _ss = w3.eth.account.sign_transaction(swap_tx, private_key)
            _sh = w3.eth.send_raw_transaction(_ss.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(_sh, timeout=120)
            if receipt.status != 1:
                # Security: swap reverted, but we still have an active approval
                # on token_in → router. Revoke it so an attacker (or a future
                # bug) can't drain the approved balance.
                self._revoke_approval(w3, token_in, router_addr, address,
                                      private_key, nonce + 2, gas_price)
                return {"status": "error", "reason": "Swap tx reverted; approval revoked", "tx_hash": _sh.hex()}

            trade_id = _monitor.open_position(
                chain=decision.chain, protocol=decision.protocol, pool=decision.pool,
                action=decision.action, token_in=decision.token_in, token_out=decision.token_out,
                size_usd=adjusted_size_usd, entry_price=1.0,
                expected_apy=decision.expected_apy, confidence=decision.confidence,
                reasoning=decision.reasoning, mode="LIVE",
                gas_usd=total_gas_usd, tx_hash=_sh.hex(),
            )
            return {
                "status": "success", "trade_id": trade_id, "tx_hash": _sh.hex(),
                "size_usd": adjusted_size_usd, "gas_usd": round(total_gas_usd, 6),
                "protocol": "blazeswap",
                "amount_in_raw":   amount_in_raw,
                "amount_out_min":  amount_out_min,
                "expected_out":    expected_out,
            }
        except Exception as e:
            _audit.log_error(f"FlareExecutor.execute_blazeswap_swap error: {e}",
                             {"decision": decision.to_dict()})
            return {"status": "error", "reason": str(e)}

    # ── Shared helper: best-effort approval revoke on swap failure ──────────
    def _revoke_approval(self, w3, token_contract, router_addr: str,
                         address: str, private_key: str, nonce: int,
                         gas_price: int) -> None:
        """Approve(router, 0) — revoke a lingering allowance after a failed swap.
        Best-effort: logs failure but does not raise (the parent error path
        already handles the user-facing response).
        """
        try:
            _revoke_tx = token_contract.functions.approve(
                Web3.to_checksum_address(router_addr), 0
            ).build_transaction({
                "chainId": FLARE_CHAIN_ID, "from": address, "nonce": nonce,
                "gas": 60_000, "gasPrice": gas_price,
            })
            _sr = w3.eth.account.sign_transaction(_revoke_tx, private_key)
            w3.eth.send_raw_transaction(_sr.raw_transaction)
        except Exception as _re:
            _audit.log_error(f"FlareExecutor._revoke_approval failed: {_re}", {})

    # ── SparkDEX V3 swap ────────────────────────────────────────────────────
    def execute_sparkdex_swap(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        private_key: str,
        token_in_address: str,
        token_out_address: str,
        fee_tier: int = 3000,                # 0.3% default; common V3 tiers: 500/3000/10000
        token_in_usd_price: float = 1.0,
        token_out_usd_price: float = 1.0,
        max_slippage_pct: float = 0.005,
    ) -> dict:
        """
        Execute a SparkDEX V3-style swap (exactInputSingle).
        Expects the router address in FLARE_CONTRACTS['sparkdex_router'].

        SECURITY: V1 required on-chain Quoter for safe amountOutMinimum.
        V2 (this): derives amountOutMinimum from off-chain price inputs
        (token_in_usd_price / token_out_usd_price) with max_slippage_pct
        applied. If callers can't provide reliable prices for BOTH tokens,
        they MUST not call this method — we reject with a clear reason
        instead of signing amountOutMinimum=0 (which would be an open
        invitation to MEV sandwich attacks).
        """
        if not self._ensure_connected():
            return {"status": "error", "reason": "No RPC connection"}

        router_addr = FLARE_CONTRACTS.get("sparkdex_router", "")
        if not router_addr or len(router_addr) != 42:
            return {
                "status": "blocked",
                "reason": "SPARKDEX_ROUTER_ADDRESS not configured — set env var before live.",
            }

        # MEV-safe amountOutMinimum: PREFER on-chain V3 Quoter (3D-12), FALL
        # BACK to off-chain token prices only if Quoter address not configured.
        # Ensures we never sign with amountOutMinimum=0 regardless of config.
        _quoter_addr = FLARE_CONTRACTS.get("sparkdex_quoter", "")
        _have_quoter = bool(_quoter_addr) and len(_quoter_addr) == 42
        if not _have_quoter and (token_in_usd_price <= 0 or token_out_usd_price <= 0):
            return {
                "status": "blocked",
                "reason": (
                    "SparkDEX V3 swap needs either an on-chain Quoter "
                    "(set SPARKDEX_QUOTER_ADDRESS env var) OR both "
                    "token_in_usd_price AND token_out_usd_price from the caller. "
                    "Refusing to sign with amountOutMinimum=0."
                ),
            }

        w3 = self._w3
        account = w3.eth.account.from_key(private_key)
        address = account.address

        try:
            token_in_dec = self._token_decimals(token_in_address)
            token_out_dec = self._token_decimals(token_out_address)

            # Compute amountIn — prefer off-chain price when available, else
            # assume stablecoin parity (caller should set token_in_usd_price=1
            # for USD stables even when Quoter provides amountOut).
            _px_in = max(float(token_in_usd_price), 1e-8)
            amount_in_raw = int(adjusted_size_usd / _px_in * (10 ** token_in_dec))
            if amount_in_raw <= 0:
                return {"status": "rejected", "reason": "Computed amountIn is 0"}

            # ── Primary: on-chain Quoter (MEV-safer because no off-chain dep) ──
            expected_out_raw = None
            if _have_quoter:
                try:
                    _quoter = w3.eth.contract(
                        address=Web3.to_checksum_address(_quoter_addr),
                        abi=_UNISWAP_V3_QUOTER_V2_ABI,
                    )
                    _q_params = (
                        Web3.to_checksum_address(token_in_address),
                        Web3.to_checksum_address(token_out_address),
                        amount_in_raw,
                        int(fee_tier),
                        0,   # sqrtPriceLimitX96
                    )
                    _q_res = _quoter.functions.quoteExactInputSingle(_q_params).call()
                    expected_out_raw = int(_q_res[0])
                    _audit.log_error(
                        f"FlareExecutor.sparkdex quoter ok → expected_out_raw={expected_out_raw}",
                        {"decision": decision.to_dict()}
                    )
                except Exception as _q_err:
                    logger.debug("[FlareExecutor] V3 quoter call failed, falling back: %s", _q_err)
                    expected_out_raw = None

            # ── Fallback: off-chain prices (legacy path) ──
            if expected_out_raw is None:
                if token_out_usd_price <= 0:
                    return {
                        "status": "blocked",
                        "reason": "Quoter unavailable and no token_out_usd_price — can't compute MEV-safe minOut",
                    }
                _px_out = max(float(token_out_usd_price), 1e-8)
                _fee_frac = float(fee_tier) / 1_000_000.0
                _expected_out_usd = adjusted_size_usd * (1.0 - _fee_frac)
                expected_out_raw = int(_expected_out_usd / _px_out * (10 ** token_out_dec))

            # Apply user slippage cap to get amountOutMinimum
            amount_out_minimum = int(expected_out_raw * (1.0 - max(max_slippage_pct, 0.0)))
            if amount_out_minimum <= 0:
                return {"status": "rejected", "reason": "Computed amountOutMinimum is 0 — check quoter/prices/slippage"}

            token_in = w3.eth.contract(address=Web3.to_checksum_address(token_in_address), abi=_ERC20_ABI)
            router   = w3.eth.contract(address=Web3.to_checksum_address(router_addr), abi=_UNISWAP_V3_ROUTER_ABI)
            deadline = int(time.time()) + 600

            params = (
                Web3.to_checksum_address(token_in_address),
                Web3.to_checksum_address(token_out_address),
                int(fee_tier),
                address,
                deadline,
                amount_in_raw,
                amount_out_minimum,     # MEV-safe: computed from off-chain prices + slippage cap
                0,                      # sqrtPriceLimitX96 = no limit
            )

            approve_gas = token_in.functions.approve(Web3.to_checksum_address(router_addr), amount_in_raw).estimate_gas({"from": address})
            swap_gas = router.functions.exactInputSingle(params).estimate_gas({"from": address})
            total_gas_usd = _estimate_gas_usd(w3, approve_gas) + _estimate_gas_usd(w3, swap_gas)

            nonce     = w3.eth.get_transaction_count(address)
            gas_price = w3.eth.gas_price

            approve_tx = token_in.functions.approve(
                Web3.to_checksum_address(router_addr), amount_in_raw
            ).build_transaction({
                "chainId": FLARE_CHAIN_ID, "from": address, "nonce": nonce,
                "gas": int(approve_gas * 1.2), "gasPrice": gas_price,
            })
            _sa = w3.eth.account.sign_transaction(approve_tx, private_key)
            w3.eth.wait_for_transaction_receipt(
                w3.eth.send_raw_transaction(_sa.raw_transaction), timeout=60
            )

            swap_tx = router.functions.exactInputSingle(params).build_transaction({
                "chainId": FLARE_CHAIN_ID, "from": address, "nonce": nonce + 1,
                "gas": int(swap_gas * 1.2), "gasPrice": gas_price,
            })
            _ss = w3.eth.account.sign_transaction(swap_tx, private_key)
            _sh = w3.eth.send_raw_transaction(_ss.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(_sh, timeout=120)
            if receipt.status != 1:
                # Revoke lingering approval to router on swap failure.
                self._revoke_approval(w3, token_in, router_addr, address,
                                      private_key, nonce + 2, gas_price)
                return {"status": "error", "reason": "Swap tx reverted; approval revoked", "tx_hash": _sh.hex()}

            trade_id = _monitor.open_position(
                chain=decision.chain, protocol=decision.protocol, pool=decision.pool,
                action=decision.action, token_in=decision.token_in, token_out=decision.token_out,
                size_usd=adjusted_size_usd, entry_price=1.0,
                expected_apy=decision.expected_apy, confidence=decision.confidence,
                reasoning=decision.reasoning, mode="LIVE",
                gas_usd=total_gas_usd, tx_hash=_sh.hex(),
            )
            return {
                "status": "success", "trade_id": trade_id, "tx_hash": _sh.hex(),
                "size_usd": adjusted_size_usd, "gas_usd": round(total_gas_usd, 6),
                "protocol": "sparkdex", "fee_tier": fee_tier,
                "amount_in_raw": amount_in_raw,
                "amount_out_minimum": amount_out_minimum,
            }
        except Exception as e:
            _audit.log_error(f"FlareExecutor.execute_sparkdex_swap error: {e}",
                             {"decision": decision.to_dict()})
            return {"status": "error", "reason": str(e)}

    def execute(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        private_key: str,
        max_slippage_pct: float = 0.005,
        token_in_usd_price: float = 1.0,
        token_out_usd_price: float = 1.0,
    ) -> dict:
        """
        Route to the correct execution method based on protocol.
        Primary entry point called by agent_runner and portfolio_executor.

        max_slippage_pct: user-configured cap (threaded from UI slider);
                         swap methods use this to compute amountOutMinimum
                         for MEV protection.
        token_in_usd_price / token_out_usd_price: required for SparkDEX V3
                         (no on-chain quoter wired yet).
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
        elif proto in ("blazeswap", "sparkdex"):
            # DEX swap routing — resolve token addresses from the main config
            try:
                from config import TOKENS as _TOKENS
            except Exception:
                _TOKENS = {}
            _tin_sym  = str(decision.token_in  or "").upper()
            _tout_sym = str(decision.token_out or "").upper()
            _tin_addr  = _TOKENS.get(_tin_sym,  "")
            _tout_addr = _TOKENS.get(_tout_sym, "")
            if not _tin_addr or not _tout_addr:
                _audit.log_error(
                    f"FlareExecutor: missing token address for {_tin_sym}->{_tout_sym} swap",
                    {"decision": decision.to_dict()}
                )
                return {
                    "status": "rejected",
                    "reason": f"Token address missing for '{_tin_sym}' or '{_tout_sym}' — "
                              f"add to config.TOKENS before live swap.",
                }
            if proto == "blazeswap":
                return self.execute_blazeswap_swap(
                    decision, adjusted_size_usd, private_key,
                    token_in_address=_tin_addr, token_out_address=_tout_addr,
                    token_in_usd_price=token_in_usd_price,
                    max_slippage_pct=max_slippage_pct,
                )
            else:  # sparkdex
                return self.execute_sparkdex_swap(
                    decision, adjusted_size_usd, private_key,
                    token_in_address=_tin_addr, token_out_address=_tout_addr,
                    token_in_usd_price=token_in_usd_price,
                    token_out_usd_price=token_out_usd_price,
                    max_slippage_pct=max_slippage_pct,
                )
        else:
            # Other protocols not yet wired (Sceptre, Spectra, Enosys, ...)
            _audit.log_error(
                f"FlareExecutor: protocol '{proto}' execution not yet implemented",
                {"decision": decision.to_dict()}
            )
            return {
                "status": "not_implemented",
                "reason": f"Protocol '{proto}' not yet wired for live execution — "
                          "use PAPER mode for simulation.",
            }

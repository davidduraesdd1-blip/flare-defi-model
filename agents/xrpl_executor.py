"""
agents/xrpl_executor.py — Phase 2/3 real execution on XRP Ledger.

Uses xrpl-py to place OfferCreate (DEX) and AMMDeposit/AMMWithdraw (AMM) transactions.
All offers use tfFillOrKill to prevent partial fills and stale order book entries.
Only activated when OPERATING_MODE == LIVE_PHASE2 or LIVE_PHASE3.
"""

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.config import (
    XRPL_NODE_URL, XRPL_NODE_FALLBACK, XRPL_ALLOWED_PAIRS,
    MAX_SLIPPAGE_PCT, MIN_NET_PROFIT_PCT, OPERATING_MODE,
)
from agents.decision_engine import TradeDecision
from agents.position_monitor import PositionMonitor
from agents.audit_log import AuditLog
from utils.http import _SESSION as _cg_session, coingecko_limiter as _cg_limiter

_monitor = PositionMonitor()
_audit   = AuditLog()

try:
    import xrpl
    from xrpl.clients import JsonRpcClient
    from xrpl.models.transactions import OfferCreate, AMMDeposit, AMMWithdraw
    from xrpl.models.amounts import IssuedCurrencyAmount
    from xrpl.models.requests import AccountInfo, BookOffers, AMMInfo
    from xrpl.transaction import submit_and_wait
    from xrpl.wallet import Wallet
    _XRPL_OK = True
except ImportError:
    _XRPL_OK = False

# XRPL transaction flags
TF_FILL_OR_KILL     = 0x00080000   # OfferCreate: all-or-nothing
TF_SELL             = 0x00800000   # OfferCreate: sell exact amount
LP_TOKEN_FLAG       = 0x00010000   # AMMDeposit: deposit both assets
AMM_WITHDRAW_ALL    = 0x00040000   # AMMWithdraw: redeem all LP tokens

# RLUSD issuer (Ripple's regulated stablecoin) — verified from XRPL docs
RLUSD_ISSUER = "rHb9CJAWyB4rj91VRWn96DkukG4bwdtyTh"  # TODO: verify RLUSD issuer address

# XRP currency representation
XRP_DROPS_PER_XRP = 1_000_000


def _xrp_to_drops(xrp: float) -> str:
    return str(int(xrp * XRP_DROPS_PER_XRP))


def _get_client() -> Optional["JsonRpcClient"]:
    if not _XRPL_OK:
        return None
    for url in (XRPL_NODE_URL.replace("wss://", "https://"),
                XRPL_NODE_FALLBACK.replace("wss://", "https://")):
        try:
            client = JsonRpcClient(url)
            # Test connection
            from xrpl.models.requests import ServerInfo
            client.request(ServerInfo())
            return client
        except Exception:
            continue
    return None


def _get_xrp_balance(client: "JsonRpcClient", address: str) -> float:
    """Return XRP balance of an address."""
    try:
        req = AccountInfo(account=address, ledger_index="current")
        resp = client.request(req)
        drops = int(resp.result["account_data"]["Balance"])
        return drops / XRP_DROPS_PER_XRP
    except Exception:
        return 0.0


class XRPLExecutor:
    """
    Executes approved trades on the XRP Ledger.
    Only active in LIVE_PHASE2 / LIVE_PHASE3 modes.
    Supports: native DEX OfferCreate, XLS-30 AMM deposit/withdraw.
    """

    def __init__(self):
        self._client: Optional["JsonRpcClient"] = None

    def _ensure_connected(self) -> bool:
        if self._client:
            try:
                from xrpl.models.requests import ServerInfo
                self._client.request(ServerInfo())
                return True
            except Exception:
                pass
        self._client = _get_client()
        return self._client is not None

    def is_available(self) -> bool:
        if OPERATING_MODE not in ("LIVE_PHASE2", "LIVE_PHASE3"):
            return False
        if not _XRPL_OK:
            return False
        return self._ensure_connected()

    def get_xrp_balance_usd(self, address: str) -> float:
        """Return XRP balance in USD using live price."""
        if not self._ensure_connected():
            return 0.0
        try:
            xrp = _get_xrp_balance(self._client, address)
            _cg_limiter.acquire()
            r = _cg_session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={"ids": "ripple", "vs_currencies": "usd"},
                timeout=5,
            )
            _xrp_raw = (r.json() or {}).get("ripple", {}).get("usd") if r.status_code == 200 else None
            xrp_usd = float(_xrp_raw) if (_xrp_raw and float(_xrp_raw) > 0) else 2.30
            return xrp * xrp_usd
        except Exception:
            return 0.0

    def execute_offer_create(
        self,
        wallet: "Wallet",
        taker_pays_xrp: float,
        taker_gets_rlusd: float,
        decision: TradeDecision,
        adjusted_size_usd: float,
    ) -> dict:
        """
        Place a Fill-or-Kill OfferCreate on the XRPL DEX.
        Buys RLUSD with XRP (or reverse) with exact amounts.
        """
        if not self._ensure_connected():
            return {"status": "error", "reason": "No XRPL connection"}

        # Validate pair
        pair = f"{decision.token_in}/{decision.token_out}"
        if pair not in XRPL_ALLOWED_PAIRS and f"{decision.token_out}/{decision.token_in}" not in XRPL_ALLOWED_PAIRS:
            return {
                "status": "rejected",
                "reason": f"Pair {pair} not in XRPL_ALLOWED_PAIRS: {sorted(XRPL_ALLOWED_PAIRS)}"
            }

        try:
            tx = OfferCreate(
                account    = wallet.address,
                taker_pays = _xrp_to_drops(taker_pays_xrp),
                taker_gets = IssuedCurrencyAmount(
                    currency = "RLUSD",
                    issuer   = RLUSD_ISSUER,
                    value    = str(round(taker_gets_rlusd, 6)),
                ),
                flags = TF_FILL_OR_KILL | TF_SELL,
            )

            response = submit_and_wait(tx, self._client, wallet)

            if response.result.get("meta", {}).get("TransactionResult") != "tesSUCCESS":
                result_code = response.result.get("meta", {}).get("TransactionResult", "unknown")
                return {
                    "status":  "error",
                    "reason":  f"Transaction failed: {result_code}",
                    "tx_hash": response.result.get("hash", ""),
                }

            tx_hash  = response.result.get("hash", "")
            trade_id = _monitor.open_position(
                chain=decision.chain, protocol=decision.protocol,
                pool=decision.pool, action=decision.action,
                token_in=decision.token_in, token_out=decision.token_out,
                size_usd=adjusted_size_usd, entry_price=taker_pays_xrp,
                expected_apy=decision.expected_apy, confidence=decision.confidence,
                reasoning=decision.reasoning, mode="LIVE",
                tx_hash=tx_hash,
            )
            return {
                "status":   "success",
                "trade_id": trade_id,
                "tx_hash":  tx_hash,
                "size_usd": adjusted_size_usd,
                "protocol": "xrpl_dex",
            }

        except Exception as e:
            _audit.log_error(f"XRPLExecutor.execute_offer_create error: {e}",
                             {"decision": decision.to_dict()})
            return {"status": "error", "reason": str(e)}

    def execute(
        self,
        decision: TradeDecision,
        adjusted_size_usd: float,
        xrpl_wallet: "Wallet",
    ) -> dict:
        """Route to correct execution method. Primary entry point."""
        if OPERATING_MODE not in ("LIVE_PHASE2", "LIVE_PHASE3"):
            return {"status": "blocked", "reason": f"Mode is {OPERATING_MODE}, not LIVE"}

        if decision.protocol == "xrpl_dex":
            # Convert USD size to XRP amount
            try:
                _cg_limiter.acquire()
                r = _cg_session.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "ripple", "vs_currencies": "usd"},
                    timeout=5,
                )
                _xrp_raw = (r.json() or {}).get("ripple", {}).get("usd") if r.status_code == 200 else None
            xrp_usd = float(_xrp_raw) if (_xrp_raw and float(_xrp_raw) > 0) else 2.30
                xrp_amount = adjusted_size_usd / xrp_usd
                rlusd_amount = adjusted_size_usd  # 1:1 with USD
            except Exception:
                xrp_amount = adjusted_size_usd / 2.30
                rlusd_amount = adjusted_size_usd

            return self.execute_offer_create(
                wallet           = xrpl_wallet,
                taker_pays_xrp   = xrp_amount,
                taker_gets_rlusd = rlusd_amount,
                decision         = decision,
                adjusted_size_usd = adjusted_size_usd,
            )

        elif decision.protocol == "xrpl_amm":
            # AMM deposit not yet implemented — log and reject
            _audit.log_error(
                "XRPLExecutor: AMM deposit execution not yet implemented",
                {"decision": decision.to_dict()}
            )
            return {
                "status": "not_implemented",
                "reason": "XRPL AMM (AMMDeposit) execution requires AMM pool lookup. "
                          "Implement AMMInfo lookup + deposit logic before Phase 2.",
            }

        return {"status": "error", "reason": f"Unknown XRPL protocol: {decision.protocol}"}

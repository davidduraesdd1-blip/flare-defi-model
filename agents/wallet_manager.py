"""
agents/wallet_manager.py — AES-256-GCM encrypted wallet storage + generation.

Security model:
- Private keys NEVER stored in plaintext, NEVER committed to git
- AES-256-GCM with PBKDF2-HMAC-SHA256 (480,000 iterations) key derivation
- Delegated signing key pattern: bot key can sign trades but CANNOT initiate withdrawals
- Each wallet type stored in a separate encrypted section
- Password set by David on first run, never stored anywhere

Wallet files live in data/agent/ which is git-ignored.
"""

import json
import os
import secrets
import struct
import tempfile
from pathlib import Path
from typing import Optional

from agents.config import (
    WALLET_FILE, KDF_ITERATIONS, KDF_SALT_BYTES,
    AES_KEY_BYTES, AES_NONCE_BYTES,
)

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False

try:
    from eth_account import Account as _EthAccount
    _WEB3_OK = True
except ImportError:
    _WEB3_OK = False

try:
    import xrpl.wallet as _xrpl_wallet_mod
    _XRPL_OK = True
except ImportError:
    _XRPL_OK = False


# ─── Encryption helpers ────────────────────────────────────────────────────────

def _derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 → 32-byte AES-256 key."""
    if not _CRYPTO_OK:
        raise RuntimeError("cryptography package not installed")
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=AES_KEY_BYTES,
        salt=salt,
        iterations=KDF_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def _encrypt(plaintext: str, password: str) -> bytes:
    """
    Encrypt plaintext string → binary blob.
    Format: [4B salt_len][salt][12B nonce][ciphertext+16B GCM tag]
    """
    salt  = secrets.token_bytes(KDF_SALT_BYTES)
    nonce = secrets.token_bytes(AES_NONCE_BYTES)
    key   = _derive_key(password, salt)
    aesgcm = AESGCM(key)
    ct = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    salt_len = struct.pack(">I", len(salt))
    return salt_len + salt + nonce + ct


def _decrypt(blob: bytes, password: str) -> str:
    """Decrypt binary blob produced by _encrypt()."""
    salt_len = struct.unpack(">I", blob[:4])[0]
    salt     = blob[4: 4 + salt_len]
    nonce    = blob[4 + salt_len: 4 + salt_len + AES_NONCE_BYTES]
    ct       = blob[4 + salt_len + AES_NONCE_BYTES:]
    key      = _derive_key(password, salt)
    aesgcm   = AESGCM(key)
    return aesgcm.decrypt(nonce, ct, None).decode("utf-8")


# ─── Wallet file structure ─────────────────────────────────────────────────────
# data/agent/wallets.enc is a JSON file where each key is a wallet section
# and each value is a hex-encoded encrypted blob.
# {
#   "flare": "deadbeef...",   # encrypted {"private_key": "0x...", "address": "0x..."}
#   "xrpl":  "deadbeef...",   # encrypted {"seed": "s...", "address": "r..."}
# }
# The JSON itself is NOT encrypted — only the blob values are.
# An attacker with the file still needs the password.

def _load_wallet_file() -> dict:
    if not WALLET_FILE.exists():
        return {}
    try:
        return json.loads(WALLET_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_wallet_file(data: dict) -> None:
    """Atomically write wallet data to disk using tempfile + os.replace to prevent corruption."""
    WALLET_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2)
    fd, tmp_path = tempfile.mkstemp(dir=WALLET_FILE.parent, prefix=".wallet_tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp_path, WALLET_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        raise
    # Restrict file permissions on non-Windows
    try:
        os.chmod(str(WALLET_FILE), 0o600)
    except Exception:
        pass


class WalletManager:
    """
    Generates, encrypts, and loads trading wallets.

    Phase 1 (PAPER): wallet is not needed — paper balance is virtual.
    Phase 2 (LIVE):  generate wallets, print addresses, fund them manually.
    """

    def wallets_exist(self) -> dict:
        """Return which wallets have been generated."""
        data = _load_wallet_file()
        return {
            "flare": "flare" in data,
            "xrpl":  "xrpl" in data,
        }

    # ── Flare wallet ───────────────────────────────────────────────────────────

    def generate_flare_wallet(self, password: str) -> dict:
        """
        Generate a new Flare (EVM) keypair, encrypt and save it.
        Returns {"address": "0x...", "saved": True}.
        NEVER returns the private key — load it only when signing.
        """
        if not _WEB3_OK:
            raise RuntimeError("web3 package not installed")
        if not _CRYPTO_OK:
            raise RuntimeError("cryptography package not installed")

        acct = _EthAccount.create()
        payload = json.dumps({
            "private_key": acct.key.hex(),
            "address":     acct.address,
        })
        blob = _encrypt(payload, password)
        data = _load_wallet_file()
        data["flare"] = blob.hex()
        data["flare_address"] = acct.address   # cache plaintext address for display
        _save_wallet_file(data)
        return {"address": acct.address, "saved": True}

    def get_flare_address(self) -> Optional[str]:
        """Return the stored Flare address without decrypting the key."""
        data = _load_wallet_file()
        if "flare" not in data:
            return None
        # We store the address in a separate plaintext section for display
        return data.get("flare_address")

    def load_flare_private_key(self, password: str) -> str:
        """Decrypt and return Flare private key. Call only at signing time."""
        data = _load_wallet_file()
        if "flare" not in data:
            raise ValueError("No Flare wallet found — generate one first")
        blob = bytes.fromhex(data["flare"])
        payload = json.loads(_decrypt(blob, password))
        return payload["private_key"]

    def save_flare_address(self, address: str) -> None:
        """Cache the public address so it can be displayed without the password."""
        data = _load_wallet_file()
        data["flare_address"] = address
        _save_wallet_file(data)

    # ── XRPL wallet ───────────────────────────────────────────────────────────

    def generate_xrpl_wallet(self, password: str) -> dict:
        """
        Generate a new XRPL keypair, encrypt and save it.
        Returns {"address": "r...", "saved": True}.
        """
        if not _XRPL_OK:
            raise RuntimeError("xrpl-py package not installed")
        if not _CRYPTO_OK:
            raise RuntimeError("cryptography package not installed")

        wallet = _xrpl_wallet_mod.Wallet.create()
        payload = json.dumps({
            "seed":    wallet.seed,
            "address": wallet.address,
        })
        blob = _encrypt(payload, password)
        data = _load_wallet_file()
        data["xrpl"]         = blob.hex()
        data["xrpl_address"] = wallet.address
        _save_wallet_file(data)
        return {"address": wallet.address, "saved": True}

    def get_xrpl_address(self) -> Optional[str]:
        """Return the stored XRPL address without decrypting the seed."""
        data = _load_wallet_file()
        return data.get("xrpl_address")

    def load_xrpl_wallet(self, password: str):
        """Decrypt and return xrpl.wallet.Wallet object. Call only at signing time."""
        if not _XRPL_OK:
            raise RuntimeError("xrpl-py package not installed")
        data = _load_wallet_file()
        if "xrpl" not in data:
            raise ValueError("No XRPL wallet found — generate one first")
        blob = bytes.fromhex(data["xrpl"])
        payload = json.loads(_decrypt(blob, password))
        return _xrpl_wallet_mod.Wallet.from_seed(payload["seed"])

    # ── Setup wizard ──────────────────────────────────────────────────────────

    def setup_wallets(self, password: str) -> dict:
        """
        Generate both wallets in one call. Called from the setup wizard in the UI.
        Returns addresses for both chains so David can fund them.
        """
        results = {}

        if _WEB3_OK and _CRYPTO_OK:
            flare_result = self.generate_flare_wallet(password)
            self.save_flare_address(flare_result["address"])
            results["flare"] = flare_result["address"]
        else:
            results["flare"] = "ERROR: web3 or cryptography package missing"

        if _XRPL_OK and _CRYPTO_OK:
            xrpl_result = self.generate_xrpl_wallet(password)
            results["xrpl"] = xrpl_result["address"]
        else:
            results["xrpl"] = "ERROR: xrpl-py or cryptography package missing"

        return results

    def verify_password(self, password: str) -> bool:
        """Verify the password is correct by attempting a decrypt."""
        data = _load_wallet_file()
        for key in ("flare", "xrpl"):
            if key in data:
                try:
                    blob = bytes.fromhex(data[key])
                    _decrypt(blob, password)
                    return True
                except Exception:
                    return False
        return False  # no wallets yet

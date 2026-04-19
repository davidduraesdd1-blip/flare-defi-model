"""
Shared HTTP helpers for all scanners.
- HTTPAdapter with exponential backoff retry (#12)
- SSRF domain allowlist (#10)
- Token bucket rate limiter (#11)
- requests-cache: in-memory response cache with per-domain TTLs
"""

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError
from urllib3.util.retry import Retry

# ── requests-cache: in-memory cache with per-domain TTLs ─────────────────────
# Falls back to a plain Session if requests-cache is not installed.
# Memory backend: zero disk I/O, safe on Streamlit Cloud ephemeral filesystem.
# Per-domain TTLs prevent stale prices (60s) while caching slower macro data (1h).
try:
    from requests_cache import CachedSession, NEVER_EXPIRE
    _URLS_EXPIRE_AFTER = {
        # Live price feeds — short TTL so data stays fresh
        "*/coingecko.com/*":       60,
        "*/pro-api.coingecko.com/*": 60,
        "*/binance.com/*":         30,
        "*/hyperliquid.xyz/*":     30,
        "*/alternative.me/*":      300,   # Fear & Greed: 5 min
        # DeFi protocol data — medium TTL
        "*/llama.fi/*":            300,   # DeFiLlama: 5 min
        "*/geckoterminal.com/*":   300,
        "*/curve.fi/*":            300,
        "*/lido.fi/*":             300,
        "*/pendle.finance/*":      300,
        "*/clearpool.finance/*":   300,
        # Macro / slow-moving data — long TTL
        "*/stlouisfed.org/*":      3600,  # FRED: 1 hour
        "*/coinmetrics.io/*":      3600,
        "*/deribit.com/*":         120,   # Options: 2 min
        # Default: 2 minutes for anything not matched above
        "*":                       120,
    }
    def _build_session():
        """CachedSession with retry/backoff, per-domain TTLs, memory backend."""
        session = CachedSession(
            backend="memory",
            urls_expire_after=_URLS_EXPIRE_AFTER,
            stale_if_error=True,   # serve stale on network errors rather than raising
        )
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
            ),
        })
        return session
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── orjson: 4x faster JSON parsing than stdlib json ──────────────────────────
# orjson is in requirements.txt; fall back to stdlib json if not installed.
try:
    import orjson as _orjson
    import json as _json_stdlib

    def _parse_json(content: bytes):
        try:
            return _orjson.loads(content)
        except Exception:
            # orjson rejects surrogate pairs and some non-standard UTF-8 — stdlib is more lenient
            return _json_stdlib.loads(content.decode("utf-8", errors="replace"))
except ImportError:
    import json as _json_fallback

    def _parse_json(content: bytes):
        return _json_fallback.loads(content)


# ─── Token Bucket Rate Limiter (#11) ─────────────────────────────────────────

class RateLimiter:
    """Thread-safe token bucket rate limiter for API calls."""

    def __init__(self, calls_per_second: float = 1.0):
        self._rate = max(calls_per_second, 0.01)
        self._tokens = self._rate
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """
        Block until a token is available.  Returns True on success, False on timeout.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                now = time.time()
                elapsed = now - self._last_refill
                self._tokens = min(self._rate, self._tokens + elapsed * self._rate)
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
            time.sleep(0.05)
        return False


# ─── Retry-aware shared session (#12) ────────────────────────────────────────
# When requests-cache is available _build_session() is already defined above
# (CachedSession variant). When it's not, define the plain fallback here.

if not _CACHE_AVAILABLE:
    def _build_session() -> requests.Session:
        """Fallback: plain Session when requests-cache is not installed."""
        session = requests.Session()
        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
            ),
        })
        return session


_SESSION = _build_session()

# ─── SSRF allowlist (#10) ─────────────────────────────────────────────────────
_ALLOWED_HOSTS: frozenset = frozenset({
    "api.llama.fi", "yields.llama.fi", "coins.llama.fi",
    "api.coingecko.com", "pro-api.coingecko.com",
    "api.alternative.me",
    "api.deribit.com",
    "www.deribit.com",            # Deribit public options API (macro_feeds.py)
    "flr-data-availability.flare.network",
    "explorer.flare.network",
    "api.routescan.io",
    "hub.snapshot.org",           # Snapshot governance GraphQL
    "li.quest",                   # Li.Fi bridge flows
    "api.cryptopanic.com",
    "api.stlouisfed.org",
    "fapi.binance.com", "api.binance.com",
    "api.hyperliquid.xyz",        # Hyperliquid perps (multi_scanner.py)
    "subgraph.blazeswap.xyz",     # Blazeswap subgraph fallback (flare_scanner.py)
    "api.clearpool.finance",      # Clearpool pool data (flare_scanner.py)
    "api.geckoterminal.com",      # GeckoTerminal DEX pools (flare_scanner.py)
    "community-api.coinmetrics.io",  # CoinMetrics on-chain data (macro_feeds.py)
    "api.curve.fi",               # Curve pool data (defi_protocols.py)
    "eth-api.lido.fi",            # Lido stETH APR (defi_protocols.py)
    "indexer.dydx.trade",         # dYdX v4 perpetuals (defi_protocols.py)
    "api-v2.pendle.finance",      # Pendle pools (defi_protocols.py)
    "fred.stlouisfed.org",        # FRED CSV download (macro_feeds.py)
    # discord.com + api.telegram.org removed 2026-04-18 along with their senders
    "ethena.fi",                  # Ethena sUSDe yield (#76)
    "www.ether.fi",               # ether.fi direct APY API (#71)
    "app.renzoprotocol.com",      # Renzo protocol points API (#71)
    "bridges.llama.fi",           # DeFiLlama bridge flows API (#85)
    "api.zerion.io",              # Zerion wallet portfolio API (#111)
    "flaremetrics.io",            # FTSO provider vote power + uptime (flare_scanner.py)
    "eth.llamarpc.com",           # Ethereum public RPC for ERC-4626 Multicall3 (defi_protocols.py)
    "rpc.ankr.com",               # Ankr public multi-chain RPC fallback (defi_protocols.py)
    "ethereum.publicnode.com",    # PublicNode Ethereum RPC fallback (defi_protocols.py)
    "cloudflare-eth.com",         # Cloudflare Ethereum RPC fallback (defi_protocols.py)
    "api.portals.fi",             # Portals token price API (flare_scanner.py)
    "api.kinetic.market",         # Kinetic lending rates (flare_scanner.py)
    "app.sceptre.fi",             # Sceptre staking rates (flare_scanner.py)
    "api.upshift.fi",             # Upshift vault APY (flare_scanner.py)
})


def is_safe_url(url: str) -> bool:
    """Return True if URL hostname is on the SSRF allowlist."""
    try:
        host = urlparse(url).hostname or ""
        return any(host == h or host.endswith("." + h) for h in _ALLOWED_HOSTS)
    except Exception:
        return False


# ─── Module-level rate limiter instances ─────────────────────────────────────
defillama_limiter  = RateLimiter(calls_per_second=1.0)
coingecko_limiter  = RateLimiter(calls_per_second=0.4)
deribit_limiter    = RateLimiter(calls_per_second=5.0)
default_limiter    = RateLimiter(calls_per_second=2.0)
fred_limiter       = RateLimiter(calls_per_second=2.0)
coinmetrics_limiter = RateLimiter(calls_per_second=0.5)


# ─── HTTP helpers ─────────────────────────────────────────────────────────────

def http_get(
    url: str,
    *,
    params: dict = None,
    headers: dict = None,
    timeout: int = 10,
    retries: int = 2,
    check_ssrf: bool = True,
) -> Optional[dict]:
    """
    GET ``url`` and return parsed JSON, or None on failure.
    Uses the shared retry-aware session. SSRF-checks by default.
    """
    if check_ssrf and not is_safe_url(url):
        logger.warning("SSRF blocked: %s", url)
        return None
    for attempt in range(retries + 1):
        try:
            r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return _parse_json(r.content)
        except HTTPError as e:
            status = getattr(e.response, "status_code", None)
            # 4xx = client/endpoint error — retrying will never help; fail immediately.
            # 404 logged at DEBUG (expected for deprecated endpoints).
            # All other 4xx logged at WARNING.
            if status and 400 <= status < 500:
                if status == 404:
                    logger.debug("GET %s → 404 Not Found (endpoint may have moved)", url)
                else:
                    logger.warning("GET %s failed after 1 attempt(s): %s %s", url, status, e)
                return None
            # 5xx / network error — retry with backoff
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            logger.warning("GET %s failed after %d attempt(s): %s", url, retries + 1, e)
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            logger.warning("GET %s failed after %d attempt(s): %s", url, retries + 1, e)
    return None


def http_post(
    url: str,
    payload: dict,
    *,
    headers: dict = None,
    timeout: int = 10,
    retries: int = 2,
    check_ssrf: bool = True,
) -> Optional[dict]:
    """
    POST JSON ``payload`` to ``url`` and return parsed JSON, or None on failure.
    """
    if check_ssrf and not is_safe_url(url):
        logger.warning("SSRF blocked: %s", url)
        return None
    for attempt in range(retries + 1):
        try:
            r = _SESSION.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            return _parse_json(r.content)
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            logger.warning("POST %s failed after %d attempt(s): %s", url, retries + 1, e)
    return None

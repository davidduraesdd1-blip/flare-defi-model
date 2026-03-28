"""
Shared HTTP helpers for all scanners.
- HTTPAdapter with exponential backoff retry (#12)
- SSRF domain allowlist (#10)
- Token bucket rate limiter (#11)
"""

import logging
import threading
import time
from typing import Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


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

def _build_session() -> requests.Session:
    """Build a requests.Session with retry/backoff and a browser-like User-Agent."""
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
    "discord.com",                # Discord webhook alerts (ai/alerts.py)
    "ethena.fi",                  # Ethena sUSDe yield (#76)
    "www.ether.fi",               # ether.fi direct APY API (#71)
    "app.renzoprotocol.com",      # Renzo protocol points API (#71)
    "bridges.llama.fi",           # DeFiLlama bridge flows API (#85)
    "api.zerion.io",              # Zerion wallet portfolio API (#111)
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
            return r.json()
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
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1.5 ** attempt)
                continue
            logger.warning("POST %s failed after %d attempt(s): %s", url, retries + 1, e)
    return None

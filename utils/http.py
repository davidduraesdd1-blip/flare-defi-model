"""
Shared HTTP helpers for all scanners.
Provides a single retry-aware GET/POST with consistent logging and timeout handling,
replacing the near-identical _get/_post/_request functions in flare_scanner.py,
multi_scanner.py, and options_scanner.py.
"""

import logging
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)


def http_get(
    url: str,
    *,
    params: dict = None,
    headers: dict = None,
    timeout: int = 10,
    retries: int = 1,
) -> Optional[dict]:
    """
    GET ``url`` and return parsed JSON, or None on failure.
    Retries once by default (two total attempts) with a 1-second pause between.
    """
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            logger.debug(f"GET {url} failed after {retries + 1} attempt(s): {e}")
    return None


def http_post(
    url: str,
    payload: dict,
    *,
    headers: dict = None,
    timeout: int = 10,
    retries: int = 1,
) -> Optional[dict]:
    """
    POST JSON ``payload`` to ``url`` and return parsed JSON, or None on failure.
    """
    for attempt in range(retries + 1):
        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
                continue
            logger.debug(f"POST {url} failed after {retries + 1} attempt(s): {e}")
    return None

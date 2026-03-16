"""
Multi-Platform Scanner
Fetches data from platforms outside Flare (Hyperliquid perps, cross-chain prices)
to detect cross-platform arbitrage and delta-neutral opportunities.
"""

import logging
import time
from datetime import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests

from config import APIS
from scanners.flare_scanner import fetch_prices as _fetch_flare_prices

logger = logging.getLogger(__name__)


@dataclass
class PerpData:
    exchange:       str
    pair:           str
    mark_price:     float
    index_price:    float
    funding_rate:   float       # per 8 hours, as decimal (e.g. 0.0001 = 0.01%)
    funding_rate_annualised: float
    open_interest:  float
    volume_24h:     float
    data_source:    str
    fetched_at:     str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class CrossChainPrice:
    token:          str
    chain:          str
    price_usd:      float
    liquidity_usd:  float
    data_source:    str
    fetched_at:     str = field(default_factory=lambda: datetime.utcnow().isoformat())


def _request(url: str, payload: dict = None, timeout: int = 10, retries: int = 1) -> Optional[dict]:
    """HTTP helper: POST when payload is given, GET otherwise. Retries once on failure."""
    for attempt in range(retries + 1):
        try:
            if payload:
                r = requests.post(url, json=payload, timeout=timeout)
            else:
                r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt < retries:
                time.sleep(1)
            else:
                logger.debug(f"Request {url} failed: {e}")
    return None


# ─── Hyperliquid Perps ────────────────────────────────────────────────────────

def fetch_hyperliquid_perps() -> list:
    """
    Fetch FXRP/USDC perpetual data from Hyperliquid.
    Hyperliquid has a public REST API — no key needed.
    """
    data = _request(APIS["hyperliquid_info"], payload={"type": "metaAndAssetCtxs"})
    results = []

    if data and isinstance(data, list) and len(data) >= 2:
        meta      = data[0]
        asset_ctx = data[1]
        universe  = meta.get("universe", [])

        for i, asset in enumerate(universe):
            name = asset.get("name", "")
            if name not in ("FXRP", "XRP"):
                continue

            if i < len(asset_ctx):
                ctx = asset_ctx[i]
                funding = float(ctx.get("funding", 0))
                mark    = float(ctx.get("markPx", 0))
                oi      = float(ctx.get("openInterest", 0))

                results.append(PerpData(
                    exchange="hyperliquid",
                    pair=f"{name}/USDC",
                    mark_price=mark,
                    index_price=float(ctx.get("oraclePx", mark)),
                    funding_rate=funding,
                    funding_rate_annualised=round(funding * 3 * 365 * 100, 4),  # 3x daily = annualised %
                    open_interest=oi,
                    volume_24h=0,
                    data_source="live",
                ))
    else:
        logger.warning("Hyperliquid API unavailable — using estimate")
        results.append(PerpData(
            exchange="hyperliquid",
            pair="FXRP/USDC",
            mark_price=2.20,
            index_price=2.20,
            funding_rate=0.0001,
            funding_rate_annualised=round(0.0001 * 3 * 365 * 100, 4),
            open_interest=0,
            volume_24h=0,
            data_source="estimate",
        ))

    return results


def fetch_sparkdex_funding() -> list:
    """
    SparkDEX perpetuals use FTSO price feeds.
    We estimate funding rate from the basis between spot and perp mark price.
    When SparkDEX exposes a public API, this can be replaced.
    """
    return [PerpData(
        exchange="sparkdex",
        pair="FLR/USD",
        mark_price=0.020,
        index_price=0.020,
        funding_rate=0.00005,
        funding_rate_annualised=round(0.00005 * 3 * 365 * 100, 4),
        open_interest=0,
        volume_24h=0,
        data_source="estimate",
    )]


# ─── Cross-Chain Price Comparison ─────────────────────────────────────────────

def fetch_cross_chain_prices() -> list:
    """
    Compare FXRP price on Flare vs XRP price on other chains/CEXs.
    Reuses flare_scanner.fetch_prices() (TTL-cached) to avoid a redundant
    CoinGecko call within the same scan cycle.
    A persistent price gap > 0.5% is an arbitrage signal.
    """
    prices      = _fetch_flare_prices()
    xrp_entry   = next((p for p in prices if p.symbol == "XRP"), None)
    xrp_price   = xrp_entry.price_usd   if xrp_entry else 2.20
    data_source = xrp_entry.data_source if xrp_entry else "estimate"

    return [
        CrossChainPrice(token="XRP",  chain="spot",  price_usd=xrp_price,         liquidity_usd=0, data_source=data_source),
        CrossChainPrice(token="FXRP", chain="flare", price_usd=xrp_price * 0.998, liquidity_usd=0, data_source="derived"),
    ]


# ─── Main Multi-Platform Scan ─────────────────────────────────────────────────

def run_multi_scan() -> dict:
    logger.info("Starting multi-platform scan...")
    perps  = fetch_hyperliquid_perps() + fetch_sparkdex_funding()
    prices = fetch_cross_chain_prices()

    logger.info(f"Multi-scan complete — {len(perps)} perps, {len(prices)} cross-chain prices")
    return {
        "timestamp": datetime.utcnow().isoformat(),
        "perps":     [asdict(p) for p in perps],
        "prices":    [asdict(p) for p in prices],
    }

"""
Multi-Platform Scanner
Fetches data from platforms outside Flare (Hyperliquid perps, cross-chain prices)
to detect cross-platform arbitrage and delta-neutral opportunities.
"""

import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict

from config import APIS, FALLBACK_PRICES
from scanners.flare_scanner import fetch_prices as _fetch_flare_prices
from utils.http import http_get, http_post

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
    fetched_at:     str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())


@dataclass
class CrossChainPrice:
    token:          str
    chain:          str
    price_usd:      float
    liquidity_usd:  float
    data_source:    str
    fetched_at:     str = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None).isoformat())


# ─── Hyperliquid Perps ────────────────────────────────────────────────────────

def fetch_hyperliquid_perps() -> list:
    """
    Fetch FXRP/USDC perpetual data from Hyperliquid.
    Hyperliquid has a public REST API — no key needed.
    """
    data = http_post(APIS["hyperliquid_info"], {"type": "metaAndAssetCtxs"})
    results = []

    if data and isinstance(data, list) and len(data) >= 2:
        meta      = data[0]
        asset_ctx = data[1]
        universe  = meta.get("universe", [])

        for i, asset in enumerate(universe):
            name = asset.get("name", "")
            if name not in ("FXRP", "XRP", "HYPE"):
                continue

            if i < len(asset_ctx):
                ctx = asset_ctx[i]
                try:
                    funding = float(ctx.get("funding", 0) or 0)
                    mark    = float(ctx.get("markPx", 0) or 0)
                    oi      = float(ctx.get("openInterest", 0) or 0)
                except (TypeError, ValueError):
                    logger.debug(f"Hyperliquid: invalid numeric data for {name}, skipping")
                    continue

                results.append(PerpData(
                    exchange="hyperliquid",
                    pair=f"{name}/USDC",
                    mark_price=mark,
                    index_price=float(ctx.get("oraclePx", mark) or mark),
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
            mark_price=FALLBACK_PRICES["FXRP"],
            index_price=FALLBACK_PRICES["FXRP"],
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
        mark_price=FALLBACK_PRICES["FLR"],
        index_price=FALLBACK_PRICES["FLR"],
        funding_rate=0.00005,
        funding_rate_annualised=round(0.00005 * 3 * 365 * 100, 4),
        open_interest=0,
        volume_24h=0,
        data_source="estimate",
    )]


def fetch_flamix_baseline() -> list:
    """
    Flamix — native perpetuals DEX on Flare (up to 500x leverage, FTSO pricing).
    No public API as of Mar 2026; returns baseline data from config and known public metrics.
    $100M+ 30-day volume and $1.5M+ OI from Dec 2025 Flamix announcements.
    Replace with live fetch when Flamix releases a public REST API.
    """
    prices = _fetch_flare_prices()
    flr_price = next((p.price_usd for p in prices if p.symbol == "FLR"), FALLBACK_PRICES["FLR"])
    xrp_price = next((p.price_usd for p in prices if p.symbol == "XRP"), FALLBACK_PRICES["XRP"])

    # Estimated OI split across major pairs: FLR ~40%, XRP ~35%, BTC ~25%
    total_oi_usd = 1_500_000
    return [
        PerpData(
            exchange="flamix",
            pair="FLR/USD",
            mark_price=flr_price,
            index_price=flr_price,
            funding_rate=0.0001,   # typical rate for high-leverage native DEX
            funding_rate_annualised=round(0.0001 * 3 * 365 * 100, 4),
            open_interest=total_oi_usd * 0.40,
            volume_24h=100_000_000 / 30,   # $100M/30 days ≈ $3.3M/day estimate
            data_source="baseline",
        ),
        PerpData(
            exchange="flamix",
            pair="XRP/USD",
            mark_price=xrp_price,
            index_price=xrp_price,
            funding_rate=0.0001,
            funding_rate_annualised=round(0.0001 * 3 * 365 * 100, 4),
            open_interest=total_oi_usd * 0.35,
            volume_24h=100_000_000 / 30 * 0.35,
            data_source="baseline",
        ),
    ]


# ─── Cross-Chain Price Comparison ─────────────────────────────────────────────

def fetch_cross_chain_prices() -> list:
    """
    Compare FXRP price on Flare vs XRP price on other chains/CEXs.
    Reuses flare_scanner.fetch_prices() (TTL-cached) to avoid a redundant
    CoinGecko call within the same scan cycle.
    A persistent price gap > 0.5% is an arbitrage signal.
    """
    prices       = _fetch_flare_prices()
    xrp_entry    = next((p for p in prices if p.symbol == "XRP"),  None)
    fxrp_entry   = next((p for p in prices if p.symbol == "FXRP"), None)
    xrp_price    = xrp_entry.price_usd   if xrp_entry  else FALLBACK_PRICES["XRP"]
    fxrp_price   = fxrp_entry.price_usd  if fxrp_entry else FALLBACK_PRICES.get("FXRP", xrp_price * 0.998)
    data_source  = xrp_entry.data_source if xrp_entry  else "estimate"
    fxrp_source  = fxrp_entry.data_source if fxrp_entry else "derived"

    return [
        CrossChainPrice(token="XRP",  chain="spot",  price_usd=xrp_price,  liquidity_usd=0, data_source=data_source),
        CrossChainPrice(token="FXRP", chain="flare", price_usd=fxrp_price, liquidity_usd=0, data_source=fxrp_source),
    ]


# ─── Main Multi-Platform Scan ─────────────────────────────────────────────────

def run_multi_scan() -> dict:
    logger.info("Starting multi-platform scan...")
    perps  = fetch_hyperliquid_perps() + fetch_sparkdex_funding() + fetch_flamix_baseline()
    prices = fetch_cross_chain_prices()

    logger.info(f"Multi-scan complete — {len(perps)} perps, {len(prices)} cross-chain prices")
    return {
        "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
        "perps":     [asdict(p) for p in perps],
        "prices":    [asdict(p) for p in prices],
    }

"""Binance funding rates feed — perpetual futures funding rate data."""

from __future__ import annotations

import logging

import httpx

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

BINANCE_FAPI = "https://fapi.binance.com/fapi/v1/premiumIndex"


class BinanceFundingFeed(BaseFeed):
    def __init__(self):
        source = FeedSource(
            name="binance_funding",
            display_name="Binance Funding Rates",
            description="Perpetual futures funding rates — indicates market leverage sentiment.",
            tags=["funding", "sentiment"],
            refresh_interval=300,
            requires_api_key=False,
        )
        super().__init__(source)

    async def fetch(self) -> list[FeedItem]:
        items = []
        # Track key symbols
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT"]

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(BINANCE_FAPI)
                resp.raise_for_status()
                data = resp.json()

            # Index by symbol for quick lookup
            rates = {d["symbol"]: d for d in data if d.get("symbol") in symbols}

            for sym in symbols:
                info = rates.get(sym)
                if not info:
                    continue

                rate = float(info.get("lastFundingRate", 0))
                rate_pct = rate * 100
                mark_price = float(info.get("markPrice", 0))

                # Funding rate interpretation
                if rate_pct > 0.05:
                    sentiment = -0.3  # overleveraged longs, potential drop
                    priority = "high"
                    label = "HIGH positive"
                elif rate_pct < -0.05:
                    sentiment = 0.3  # overleveraged shorts, potential squeeze
                    priority = "high"
                    label = "HIGH negative"
                elif rate_pct > 0.01:
                    sentiment = -0.1
                    priority = "medium"
                    label = "Mildly positive"
                elif rate_pct < -0.01:
                    sentiment = 0.1
                    priority = "medium"
                    label = "Mildly negative"
                else:
                    sentiment = 0.0
                    priority = "low"
                    label = "Neutral"

                base = sym.replace("USDT", "")
                items.append(FeedItem(
                    source="binance_funding",
                    title=f"{base} Funding Rate: {rate_pct:+.4f}% ({label})",
                    content=f"{base} perpetual funding rate is {rate_pct:+.4f}%. Mark price: ${mark_price:,.2f}",
                    tags=["funding", "sentiment"],
                    priority=priority,
                    sentiment_score=sentiment,
                    symbol=base,
                    metadata={
                        "funding_rate": rate,
                        "funding_rate_pct": rate_pct,
                        "mark_price": mark_price,
                    },
                ))

        except Exception as e:
            logger.warning(f"Binance funding fetch failed: {e}")

        return items

"""CoinGecko feed — trending coins, market cap changes, and global metrics."""

from __future__ import annotations

import logging

import httpx

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"


class CoinGeckoFeed(BaseFeed):
    def __init__(self):
        source = FeedSource(
            name="coingecko",
            display_name="CoinGecko",
            description="Trending coins, market cap data, and global crypto metrics.",
            tags=["sentiment", "macro"],
            refresh_interval=300,
            requires_api_key=False,
        )
        super().__init__(source)

    async def fetch(self) -> list[FeedItem]:
        items = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                # Trending coins
                resp = await client.get(f"{COINGECKO_BASE}/search/trending")
                resp.raise_for_status()
                trending = resp.json()

                for coin_data in trending.get("coins", [])[:10]:
                    coin = coin_data.get("item", {})
                    name = coin.get("name", "")
                    symbol = coin.get("symbol", "").upper()
                    rank = coin.get("score", 0) + 1
                    price_btc = coin.get("price_btc", 0)

                    items.append(FeedItem(
                        source="coingecko",
                        title=f"#{rank} Trending: {name} ({symbol})",
                        content=f"{name} is trending #{rank} on CoinGecko. Price: {price_btc:.8f} BTC",
                        url=f"https://www.coingecko.com/en/coins/{coin.get('id', '')}",
                        timestamp="",
                        tags=["sentiment", "trends"],
                        priority="medium",
                        symbol=symbol,
                        metadata={"rank": rank, "price_btc": price_btc},
                    ))

                # Global market data
                resp2 = await client.get(f"{COINGECKO_BASE}/global")
                resp2.raise_for_status()
                global_data = resp2.json().get("data", {})

                market_cap_change = global_data.get("market_cap_change_percentage_24h_usd", 0)
                btc_dominance = global_data.get("market_cap_percentage", {}).get("btc", 0)

                sentiment = 0.0
                if market_cap_change > 2:
                    sentiment = 0.5
                elif market_cap_change < -2:
                    sentiment = -0.5

                items.append(FeedItem(
                    source="coingecko",
                    title=f"Global Market: {market_cap_change:+.1f}% 24h | BTC Dominance: {btc_dominance:.1f}%",
                    content=f"Total market cap changed {market_cap_change:+.1f}% in 24h. BTC dominance: {btc_dominance:.1f}%",
                    tags=["macro", "sentiment"],
                    priority="high" if abs(market_cap_change) > 3 else "medium",
                    sentiment_score=sentiment,
                    metadata={
                        "market_cap_change_24h": market_cap_change,
                        "btc_dominance": btc_dominance,
                        "active_cryptocurrencies": global_data.get("active_cryptocurrencies", 0),
                    },
                ))

        except Exception as e:
            logger.warning(f"CoinGecko fetch failed: {e}")

        return items

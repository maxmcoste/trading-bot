"""Google Trends feed — search interest for crypto/stock keywords."""

from __future__ import annotations

import logging

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

KEYWORDS = ["Bitcoin", "Ethereum", "crypto crash", "buy crypto", "stock market"]


class GoogleTrendsFeed(BaseFeed):
    def __init__(self):
        source = FeedSource(
            name="google_trends",
            display_name="Google Trends",
            description="Search interest trends for crypto and market keywords.",
            tags=["trends", "sentiment"],
            refresh_interval=1800,  # 30 min — pytrends rate limits aggressively
            requires_api_key=False,
        )
        super().__init__(source)

    async def fetch(self) -> list[FeedItem]:
        items = []
        try:
            from pytrends.request import TrendReq

            pytrends = TrendReq(hl="en-US", tz=60)
            pytrends.build_payload(KEYWORDS, cat=0, timeframe="now 7-d")
            df = pytrends.interest_over_time()

            if df.empty:
                return []

            for kw in KEYWORDS:
                if kw not in df.columns:
                    continue
                current = int(df[kw].iloc[-1])
                avg_7d = float(df[kw].mean())
                change = ((current - avg_7d) / max(avg_7d, 1)) * 100

                # Spike detection
                if change > 50:
                    priority = "high"
                    sentiment = -0.2 if "crash" in kw.lower() else 0.2
                elif change > 20:
                    priority = "medium"
                    sentiment = -0.1 if "crash" in kw.lower() else 0.1
                else:
                    priority = "low"
                    sentiment = 0.0

                items.append(FeedItem(
                    source="google_trends",
                    title=f"Google Trends: \"{kw}\" interest {current}/100 ({change:+.0f}% vs 7d avg)",
                    content=f"Search interest for \"{kw}\" is {current}/100. 7-day avg: {avg_7d:.0f}. Change: {change:+.1f}%",
                    tags=["trends", "sentiment"],
                    priority=priority,
                    sentiment_score=sentiment,
                    metadata={
                        "keyword": kw,
                        "current_interest": current,
                        "avg_7d": avg_7d,
                        "change_pct": change,
                    },
                ))

        except ImportError:
            logger.debug("pytrends not installed — Google Trends feed disabled")
        except Exception as e:
            logger.warning(f"Google Trends fetch failed: {e}")

        return items

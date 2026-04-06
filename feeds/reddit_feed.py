"""Reddit feed — pulls top posts from crypto/trading subreddits (no auth required)."""

from __future__ import annotations

import logging

import httpx

from feeds.base_feed import BaseFeed
from feeds.models import FeedItem, FeedSource

logger = logging.getLogger(__name__)

SUBREDDITS = ["cryptocurrency", "bitcoin", "ethtrader", "wallstreetbets"]
REDDIT_BASE = "https://www.reddit.com/r/{sub}/hot.json"


class RedditFeed(BaseFeed):
    def __init__(self):
        source = FeedSource(
            name="reddit",
            display_name="Reddit",
            description="Top posts from crypto and trading subreddits.",
            tags=["social", "sentiment"],
            refresh_interval=300,
            requires_api_key=False,
        )
        super().__init__(source)

    async def fetch(self) -> list[FeedItem]:
        items = []
        headers = {"User-Agent": "TradingBot/1.0"}

        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            for sub in SUBREDDITS:
                try:
                    resp = await client.get(
                        REDDIT_BASE.format(sub=sub),
                        params={"limit": 5},
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    for post in data.get("data", {}).get("children", []):
                        p = post.get("data", {})
                        title = p.get("title", "")
                        score = p.get("score", 0)
                        num_comments = p.get("num_comments", 0)
                        permalink = p.get("permalink", "")

                        # Simple engagement-based priority
                        if score > 1000 or num_comments > 200:
                            priority = "high"
                        elif score > 200 or num_comments > 50:
                            priority = "medium"
                        else:
                            priority = "low"

                        items.append(FeedItem(
                            source="reddit",
                            title=f"r/{sub}: {title}",
                            content=p.get("selftext", "")[:500],
                            url=f"https://reddit.com{permalink}" if permalink else "",
                            timestamp=str(int(p.get("created_utc", 0))),
                            tags=["social", "sentiment"],
                            priority=priority,
                            metadata={
                                "subreddit": sub,
                                "score": score,
                                "num_comments": num_comments,
                                "upvote_ratio": p.get("upvote_ratio", 0),
                            },
                        ))
                except Exception as e:
                    logger.warning(f"Reddit r/{sub} fetch failed: {e}")

        return items

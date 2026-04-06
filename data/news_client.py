"""News client using NewsAPI.org with keyword-based sentiment scoring.

Uses substring matching (not word-level) so inflected forms like "surged",
"rallied", "plunged" all match their root keywords. Multi-word phrases
like "rate cut" are supported.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"
CACHE_TTL = 900  # 15 min


# ---------------------------------------------------------------------------
# Symbol → NewsAPI query mapping
# ---------------------------------------------------------------------------

SYMBOL_QUERY_MAP = {
    # Crypto (EUR and USD pairs map to the same underlying query)
    "BTC-USD":  "bitcoin OR BTC",
    "BTC-EUR":  "bitcoin OR BTC",
    "ETH-USD":  "ethereum OR ETH crypto",
    "ETH-EUR":  "ethereum OR ETH crypto",
    "SOL-USD":  "solana SOL crypto",
    "SOL-EUR":  "solana SOL crypto",
    "BNB-USD":  "binance BNB crypto",
    "XRP-USD":  "ripple XRP crypto",
    "XRP-EUR":  "ripple XRP crypto",
    "ADA-USD":  "cardano ADA crypto",
    "ADA-EUR":  "cardano ADA crypto",
    "DOGE-USD": "dogecoin DOGE crypto",
    "DOGE-EUR": "dogecoin DOGE crypto",
    "AVAX-USD": "avalanche AVAX crypto",
    "LINK-USD": "chainlink LINK crypto",
    "MATIC-USD":"polygon MATIC crypto",
    "DOT-USD":  "polkadot DOT crypto",
    # Stocks / ETFs
    "AAPL":  "Apple AAPL stock",
    "MSFT":  "Microsoft MSFT stock",
    "NVDA":  "Nvidia NVDA stock",
    "SPY":   "S&P 500 SPY ETF market",
    "QQQ":   "Nasdaq QQQ ETF tech",
    "TSLA":  "Tesla TSLA stock",
    "GOOGL": "Google Alphabet GOOGL stock",
    "AMZN":  "Amazon AMZN stock",
    "META":  "Meta Facebook META stock",
}


def get_query_for_symbol(symbol: str) -> str:
    """Return the optimal NewsAPI query string for a trading symbol."""
    if symbol in SYMBOL_QUERY_MAP:
        return SYMBOL_QUERY_MAP[symbol]
    # Generic fallback: strip common fiat suffixes
    base = symbol.replace("-USD", "").replace("-EUR", "").replace("-", " ")
    return base


# ---------------------------------------------------------------------------
# Sentiment keyword lists (substring-matched against lowercased text)
# ---------------------------------------------------------------------------

POSITIVE_KEYWORDS = [
    # Price movement
    "surge", "rally", "soar", "jump", "spike", "climb", "rise", "gain",
    "record", "all-time high", "ath", "bull", "bullish", "breakout", "bounce",
    # Fundamentals
    "beat", "profit", "revenue", "growth", "upgrade", "outperform",
    "strong", "positive", "optimistic", "recovery", "rebound", "exceed",
    # Crypto adoption
    "adoption", "partnership", "launch", "approve", "approval", "etf",
    "institutional", "accumulate", "hodl", "milestone", "breakthrough",
    # Macro positive
    "rate cut", "dovish", "stimulus", "liquidity", "easing",
]

NEGATIVE_KEYWORDS = [
    # Price movement
    "crash", "plunge", "tumble", "sink", "decline", "dump",
    "sell-off", "selloff", "bear", "bearish", "breakdown", "slump",
    "drop", "fall", "slide",
    # Fundamentals
    "miss", "loss", "downgrade", "underperform", "weak",
    "negative", "pessimistic", "warning", "concern",
    # Crypto-specific
    "hack", "exploit", "scam", "fraud", "ban", "crackdown",
    "sec", "lawsuit", "investigation", "seized", "shutdown", "rug pull",
    # Macro negative
    "rate hike", "hawkish", "inflation", "recession", "tariff", "sanction",
    "geopolitical", "war", "crisis", "default", "bankruptcy",
]


def calculate_sentiment(text: str) -> float:
    """Compute sentiment score in [-1.0, +1.0] via substring keyword hits.

    Uses substring matching so inflected forms (surged, gaining, dropped)
    match their roots. Multi-word phrases like "rate cut" are supported.
    Score is normalized by total hits (not total words) so that even short
    headlines produce meaningful non-zero scores.
    """
    if not text:
        return 0.0
    text_lower = text.lower()

    pos_hits = sum(1 for kw in POSITIVE_KEYWORDS if kw in text_lower)
    neg_hits = sum(1 for kw in NEGATIVE_KEYWORDS if kw in text_lower)

    total = pos_hits + neg_hits
    if total == 0:
        return 0.0

    score = (pos_hits - neg_hits) / total
    return round(max(-1.0, min(1.0, score)), 3)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class SentimentData:
    score: float  # -1.0 to 1.0
    news_count: int
    headlines: list[dict] = field(default_factory=list)
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class NewsClient:
    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("NEWSAPI_KEY", "")
        self._cache: dict[str, tuple[float, SentimentData]] = {}
        self._warned_no_key = False

    def get_query_for_symbol(self, symbol: str) -> str:
        return get_query_for_symbol(symbol)

    async def fetch_sentiment(self, symbol: str) -> SentimentData:
        now = time.time()
        if symbol in self._cache:
            ts, data = self._cache[symbol]
            if (now - ts) < CACHE_TTL:
                logger.debug(f"[news] {symbol} | cache hit | score={data.score:+.3f}")
                return data

        if not self.api_key:
            if not self._warned_no_key:
                logger.warning("NEWSAPI_KEY not configured — news sentiment disabled (suppressing further warnings)")
                self._warned_no_key = True
            return SentimentData(score=0.0, news_count=0, error="no_api_key")

        query = get_query_for_symbol(symbol)
        from_dt = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        logger.debug(f"[news] fetch {symbol} | query='{query}' | from={from_dt}")

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(NEWSAPI_URL, params={
                    "q": query,
                    "from": from_dt,
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                    "language": "en",
                    "apiKey": self.api_key,
                })
                resp.raise_for_status()
                data = resp.json()

        except httpx.TimeoutException as e:
            logger.error(f"[news] {symbol} timeout: {e}")
            return SentimentData(score=0.0, news_count=0, error="timeout")
        except httpx.HTTPStatusError as e:
            logger.error(f"[news] {symbol} HTTP {e.response.status_code}: {e.response.text[:200]}")
            return SentimentData(score=0.0, news_count=0, error=f"http_{e.response.status_code}")
        except Exception as e:
            logger.error(f"[news] {symbol} unexpected error: {type(e).__name__}: {e}")
            return SentimentData(score=0.0, news_count=0, error=str(e))

        total_results = data.get("totalResults", 0)
        articles = data.get("articles", []) or []
        logger.debug(f"[news] {symbol} | HTTP 200 | found={total_results} | using={len(articles)}")

        headlines: list[dict] = []
        total_score = 0.0
        pos_articles = 0
        neg_articles = 0

        for art in articles:
            title = art.get("title", "") or ""
            desc = art.get("description", "") or ""
            text = f"{title} {desc}"
            sent = calculate_sentiment(text)
            total_score += sent
            if sent > 0:
                pos_articles += 1
            elif sent < 0:
                neg_articles += 1
            headlines.append({
                "title": title,
                "source": (art.get("source") or {}).get("name", ""),
                "url": art.get("url", ""),
                "published_at": art.get("publishedAt", ""),
                "score": sent,
                "sentiment": sent,  # back-compat key
            })
            logger.debug(f"[news] {symbol} | score={sent:+.3f} | '{title[:70]}'")

        count = len(articles)
        avg_score = total_score / count if count else 0.0
        final_score = round(max(-1.0, min(1.0, avg_score)), 3)

        result = SentimentData(
            score=final_score,
            news_count=count,
            headlines=headlines,
            error=None if count > 0 else "no_articles",
        )
        self._cache[symbol] = (now, result)
        logger.info(
            f"[news] {symbol} | final_score={final_score:+.3f} | "
            f"articles={count} | pos={pos_articles} neg={neg_articles}"
        )
        return result


if __name__ == "__main__":
    async def main():
        logging.basicConfig(level=logging.DEBUG, format="%(asctime)s | %(levelname)s | %(message)s")
        c = NewsClient()
        for sym in ["BTC-USD", "ETH-USD", "AAPL"]:
            data = await c.fetch_sentiment(sym)
            print(f"\n{sym}: score={data.score:+.3f} articles={data.news_count} err={data.error}")
            for h in data.headlines[:3]:
                print(f"  [{h['score']:+.2f}] {h['title'][:80]}")

    asyncio.run(main())

#!/usr/bin/env python3
"""Standalone test for the news client.

Run from project root:
    python tests/test_news_client.py
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from data.news_client import NewsClient, calculate_sentiment, get_query_for_symbol

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)

TEST_SYMBOLS = ["BTC-USD", "ETH-USD", "AAPL", "SPY"]


def test_keyword_matching():
    print("\n" + "=" * 60)
    print("KEYWORD MATCHING UNIT TEST (no network)")
    print("=" * 60)
    cases = [
        ("Bitcoin surges to new all-time high on ETF approval",  +1.0),
        ("Ethereum plunges after exploit, investors panic",      -1.0),
        ("Apple beats earnings, stock jumps 5%",                 +1.0),
        ("Tesla misses revenue, shares tumble",                  -1.0),
        ("Market opens flat ahead of Fed meeting",                0.0),
    ]
    for text, expected_sign in cases:
        score = calculate_sentiment(text)
        ok = (
            (expected_sign > 0 and score > 0) or
            (expected_sign < 0 and score < 0) or
            (expected_sign == 0 and score == 0)
        )
        mark = "OK " if ok else "BAD"
        print(f"  [{mark}] score={score:+.3f}  '{text[:60]}'")


async def test_live_fetch():
    print("\n" + "=" * 60)
    print("LIVE NEWSAPI FETCH TEST")
    print("=" * 60)

    api_key = os.getenv("NEWSAPI_KEY", "")
    print(f"\nAPI Key: {'present (' + str(len(api_key)) + ' chars)' if api_key else 'MISSING'}")
    if not api_key:
        print("Skipping live test — set NEWSAPI_KEY in .env")
        return

    client = NewsClient()

    for symbol in TEST_SYMBOLS:
        print(f"\n--- {symbol} ---")
        print(f"  Query: '{get_query_for_symbol(symbol)}'")
        result = await client.fetch_sentiment(symbol)
        print(f"  Score:    {result.score:+.3f}")
        print(f"  Articles: {result.news_count}")
        print(f"  Error:    {result.error or 'none'}")
        if result.headlines:
            for h in result.headlines[:3]:
                print(f"    [{h['score']:+.2f}] {h['title'][:70]}")
        else:
            print("  *** No headlines returned")

    print("\n" + "=" * 60)
    print("If all scores are 0.00 but articles > 0: keyword matching issue")
    print("If articles == 0: query / API key / rate limit issue")
    print("=" * 60)


if __name__ == "__main__":
    test_keyword_matching()
    asyncio.run(test_live_fetch())

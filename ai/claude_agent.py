"""Claude AI agent for trade decision-making with retry logic and validation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from ai.system_prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
AUDIT_LOG = LOG_DIR / "claude_audit.log"


class StrategySignalSchema(BaseModel):
    signal: str
    strength: float


class TradeDecision(BaseModel):
    action: str
    asset_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    strategy_signals: dict[str, StrategySignalSchema] = {}
    dominant_strategy: str = ""
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    position_size_pct: float = 0.0
    time_horizon: str = "intraday"
    session: Optional[str] = None
    fear_greed_index: Optional[int] = None
    warnings: list[str] = Field(default_factory=list)


class ClaudeAgent:
    MODEL = "claude-sonnet-4-20250514"
    MAX_TOKENS = 1024
    MAX_RETRIES = 3
    BACKOFF_BASE = 1  # seconds
    MIN_INTERVAL = 3.0  # seconds between calls per symbol

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.client = anthropic.Anthropic(api_key=self.api_key)
        self._last_call: dict[str, float] = {}

    def _audit_log(self, symbol: str, prompt: str, response: str):
        try:
            if not hasattr(self, '_audit_logger'):
                from logging.handlers import RotatingFileHandler
                self._audit_logger = logging.getLogger("claude_audit")
                self._audit_logger.propagate = False
                handler = RotatingFileHandler(
                    AUDIT_LOG, maxBytes=8*1024*1024, backupCount=10)
                handler.setFormatter(logging.Formatter("%(message)s"))
                self._audit_logger.addHandler(handler)
            self._audit_logger.info(
                f"\n{'='*60}\n"
                f"Symbol: {symbol} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"PROMPT:\n{prompt[:500]}...\n"
                f"RESPONSE:\n{response[:500]}")
        except Exception:
            pass

    async def analyze(self, symbol: str, prompt: str) -> TradeDecision | None:
        # Rate limit per symbol
        now = time.time()
        last = self._last_call.get(symbol, 0.0)
        wait = self.MIN_INTERVAL - (now - last)
        if wait > 0:
            await asyncio.sleep(wait)

        for attempt in range(self.MAX_RETRIES):
            try:
                self._last_call[symbol] = time.time()

                response = await asyncio.to_thread(
                    self.client.messages.create,
                    model=self.MODEL,
                    max_tokens=self.MAX_TOKENS,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )

                text = response.content[0].text.strip()
                # Remove markdown backticks if present
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1]
                if text.endswith("```"):
                    text = text.rsplit("```", 1)[0]
                text = text.strip()

                self._audit_log(symbol, prompt, text)

                data = json.loads(text)
                decision = TradeDecision(**data)
                return decision

            except anthropic.RateLimitError:
                delay = self.BACKOFF_BASE * (2 ** attempt)
                logger.warning(f"Rate limited on {symbol}, retrying in {delay}s")
                await asyncio.sleep(delay)

            except anthropic.APIConnectionError as e:
                delay = self.BACKOFF_BASE * (2 ** attempt)
                logger.warning(f"API connection error for {symbol}: {e}, retrying in {delay}s")
                await asyncio.sleep(delay)

            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON from Claude for {symbol}: {e}")
                if attempt == self.MAX_RETRIES - 1:
                    return None

            except Exception as e:
                logger.error(f"Claude agent error for {symbol}: {e}")
                if attempt == self.MAX_RETRIES - 1:
                    return None

        return None


if __name__ == "__main__":
    agent = ClaudeAgent()
    print(f"Claude agent initialized. Model: {agent.MODEL}")
    print(f"Audit log: {AUDIT_LOG}")

    # Test TradeDecision validation
    sample = TradeDecision(
        action="BUY",
        asset_type="stock",
        confidence=0.82,
        reasoning="Strong bullish signals across multiple strategies.",
        dominant_strategy="mean_reversion",
        stop_loss_pct=1.5,
        take_profit_pct=3.0,
        position_size_pct=5.0,
    )
    print(f"Sample decision: {sample.action} conf={sample.confidence}")

"""Base class for all trading strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StrategySignal:
    name: str
    signal: str       # "bullish" | "bearish" | "neutral"
    strength: float   # 0.0 - 1.0
    reason: str
    enabled: bool


class BaseStrategy(ABC):
    @abstractmethod
    def analyze(self, data: dict) -> StrategySignal:
        pass

    @property
    @abstractmethod
    def docs_url(self) -> str:
        pass

    @property
    @abstractmethod
    def config_key(self) -> str:
        pass


if __name__ == "__main__":
    print("BaseStrategy and StrategySignal defined successfully.")
    sig = StrategySignal("test", "bullish", 0.8, "Test reason", True)
    print(f"Sample signal: {sig}")

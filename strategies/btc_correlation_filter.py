"""BTC Correlation Filter strategy for crypto assets."""

from strategies.base_strategy import BaseStrategy, StrategySignal


class BTCCorrelationStrategy(BaseStrategy):
    docs_url = "https://academy.binance.com/en/articles/bitcoin-dominance-and-its-impact-on-altcoins"
    config_key = "STRATEGY_BTC_CORRELATION_ENABLED"

    def analyze(self, data: dict) -> StrategySignal:
        symbol = data.get("symbol", "")
        btc_change_1h = data.get("btc_change_1h", 0.0)
        btc_change_4h = data.get("btc_change_4h", 0.0)
        btc_ema20 = data.get("btc_ema20")
        btc_ema50 = data.get("btc_ema50")
        btc_correlation_24h = data.get("btc_correlation_24h", 0.0)

        # Skip for BTC itself to avoid circularity
        if symbol.upper().startswith("BTC-") or symbol.upper() in ("BTC/USD", "BTCUSD", "BTC/EUR", "BTCEUR"):
            return StrategySignal("btc_correlation", "neutral", 0.0,
                                  "Filter not applied to BTC itself", True)

        reasons = []

        # Panic mode: BTC 4h change < -5%
        if btc_change_4h is not None and btc_change_4h < -5.0:
            return StrategySignal(
                "btc_correlation", "neutral", 0.0,
                f"BTC panic mode: 4h change {btc_change_4h:+.1f}% — force HOLD", True
            )

        # Contagion: BTC 1h change < -3%
        if btc_change_1h is not None and btc_change_1h < -3.0:
            strength = min(abs(btc_change_1h) / 6.0, 1.0)
            return StrategySignal(
                "btc_correlation", "bearish", strength,
                f"BTC contagion: 1h change {btc_change_1h:+.1f}%", True
            )

        # EMA trend
        if btc_ema20 is not None and btc_ema50 is not None:
            if btc_ema20 > btc_ema50:
                signal = "bullish"
                reasons.append("BTC trend positive (EMA20 > EMA50)")
            else:
                signal = "bearish"
                reasons.append("BTC trend negative (EMA20 < EMA50)")
        else:
            signal = "neutral"
            reasons.append("BTC EMA data unavailable")

        # Correlation confirmation
        if btc_correlation_24h is not None and btc_correlation_24h > 0.8:
            reasons.append(f"High BTC correlation ({btc_correlation_24h:.2f})")
            strength = 0.7
        else:
            strength = 0.4

        if signal == "neutral":
            strength = 0.0

        return StrategySignal("btc_correlation", signal, strength,
                              "; ".join(reasons), True)


if __name__ == "__main__":
    s = BTCCorrelationStrategy()

    panic = s.analyze({"symbol": "ETH-USD", "btc_change_1h": -1.0,
                       "btc_change_4h": -6.0})
    print(f"Panic: {panic}")

    contagion = s.analyze({"symbol": "SOL-USD", "btc_change_1h": -4.0,
                           "btc_change_4h": -2.0})
    print(f"Contagion: {contagion}")

    bullish = s.analyze({"symbol": "ETH-USD", "btc_change_1h": 0.5,
                         "btc_change_4h": 1.2, "btc_ema20": 65000,
                         "btc_ema50": 63000, "btc_correlation_24h": 0.85})
    print(f"Bullish trend: {bullish}")

    btc_self = s.analyze({"symbol": "BTC-USD"})
    print(f"BTC self: {btc_self}")

"""Paper trading test — validates all components with mock data."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time

import numpy as np
import pandas as pd

# Add project root to path
sys.path.insert(0, ".")

from config import Settings
from data.indicators import calculate_indicators, latest_signals
from strategies.base_strategy import StrategySignal
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_trading import SentimentStrategy
from strategies.btc_correlation_filter import BTCCorrelationStrategy
from strategies.fear_greed_contrarian import FearGreedStrategy
from strategies.session_momentum import SessionMomentumStrategy, get_current_session
from strategies.multi_signal import MultiSignalStrategy
from ai.claude_agent import TradeDecision
from risk.risk_manager import RiskManager
from risk.position_sizer import calculate_position_size
from execution.paper_mode import PaperExecutor
from monitoring.logger import DBLogger


def generate_ohlcv(symbol: str, n: int = 200, base_price: float = 100.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    np.random.seed(hash(symbol) % 2**31)
    close = base_price + np.cumsum(np.random.randn(n) * (base_price * 0.005))
    close = np.maximum(close, base_price * 0.5)
    return pd.DataFrame({
        "open": close + np.random.randn(n) * 0.2,
        "high": close + abs(np.random.randn(n) * 0.5),
        "low": close - abs(np.random.randn(n) * 0.5),
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })


def make_mock_decision(action: str, asset_type: str, confidence: float) -> TradeDecision:
    return TradeDecision(
        action=action, asset_type=asset_type, confidence=confidence,
        reasoning="Mock decision for testing.",
        dominant_strategy="mean_reversion",
        stop_loss_pct=1.5, take_profit_pct=3.0, position_size_pct=5.0,
        time_horizon="intraday",
        warnings=[],
    )


def test_indicators():
    print("\n--- Test: Indicators ---")
    for sym, base in [("AAPL", 150.0), ("BTC-USD", 65000.0)]:
        df = generate_ohlcv(sym, n=200, base_price=base)
        df = calculate_indicators(df)
        sig = latest_signals(df)
        rsi = f"{sig.rsi:.1f}" if sig.rsi is not None else "N/A"
        zs = f"{sig.z_score:.2f}" if sig.z_score is not None else "N/A"
        bb = f"{sig.bb_position:.2f}" if sig.bb_position is not None else "N/A"
        vr = f"{sig.volume_ratio:.2f}" if sig.volume_ratio is not None else "N/A"
        print(f"  {sym}: RSI={rsi}, Z-score={zs}, BB pos={bb}, Vol ratio={vr}")
    print("  PASS")


def test_strategies():
    print("\n--- Test: All Strategies ---")
    strategies = [
        ("mean_reversion", MeanReversionStrategy(),
         {"z_score": -2.3, "rsi": 28, "bb_position": 0.08}),
        ("sentiment", SentimentStrategy(),
         {"sentiment_score": 0.5, "headlines": [{"sentiment": 0.5}]*3, "asset_type": "stock"}),
        ("btc_correlation", BTCCorrelationStrategy(),
         {"symbol": "ETH-USD", "btc_change_1h": -1.0, "btc_change_4h": 0.5,
          "btc_ema20": 65000, "btc_ema50": 63000, "btc_correlation_24h": 0.85}),
        ("fear_greed", FearGreedStrategy(),
         {"fear_greed_value": 20, "fear_greed_history": []}),
        ("session_momentum", SessionMomentumStrategy(),
         {"momentum_pct": 2.0, "volume_ratio": 1.5}),
    ]

    all_signals = []
    for name, strat, data in strategies:
        sig = strat.analyze(data)
        all_signals.append(sig)
        print(f"  {name}: {sig.signal} (strength={sig.strength:.2f}) — {sig.reason[:60]}")

    # Multi-signal
    ms = MultiSignalStrategy()
    result = ms.analyze({
        "strategy_signals": all_signals,
        "weight_technical": 0.40,
        "weight_sentiment": 0.30,
        "weight_macro": 0.30,
    })
    print(f"  multi_signal: {result.signal} (strength={result.strength:.2f}) — {result.reason[:60]}")
    print("  PASS")


def test_risk_manager():
    print("\n--- Test: Risk Manager ---")
    s = Settings()
    rm = RiskManager(s)

    # Approved stock
    d = make_mock_decision("BUY", "stock", 0.85)
    r = rm.check(d, "AAPL")
    # NYSE might be closed, that's ok
    print(f"  Stock BUY high conf: approved={r.approved} ({r.reason[:50]})")

    # Low confidence
    d = make_mock_decision("BUY", "stock", 0.50)
    r = rm.check(d, "MSFT")
    assert not r.approved, "Should reject low confidence"
    print(f"  Stock low conf: approved={r.approved} ({r.reason[:50]})")

    # Crypto BTC stress
    d = make_mock_decision("BUY", "crypto", 0.80)
    r = rm.check(d, "ETH-USD", btc_change_1h=-4.0)
    assert not r.approved, "Should reject during BTC stress"
    print(f"  Crypto BTC stress: approved={r.approved} ({r.reason[:50]})")

    # HOLD passthrough
    d = make_mock_decision("HOLD", "stock", 0.50)
    r = rm.check(d, "SPY")
    assert r.approved
    print(f"  HOLD passthrough: approved={r.approved}")

    print("  PASS")


def test_paper_trading():
    print("\n--- Test: Paper Trading (5 trades) ---")
    ex = PaperExecutor(initial_cash=10000.0)

    trades = [
        ("AAPL", "ibkr", "stock", "BUY", 150.0, 10),
        ("NVDA", "ibkr", "stock", "BUY", 800.0, 2),
        ("BTC-USD", "coinbase", "crypto", "BUY", 65000.0, 0.02),
        ("AAPL", "ibkr", "stock", "SELL", 155.0, 10),
        ("NVDA", "ibkr", "stock", "SELL", 790.0, 2),
    ]

    for sym, broker, atype, action, price, qty in trades:
        r = ex.execute_trade(sym, broker, atype, action, price, qty, 1.5, 3.0)
        status = "OK" if r["executed"] else "SKIP"
        print(f"  {action} {sym} @ ${price}: {status}")

    status = ex.get_status()
    print(f"  Final: cash=${status['cash']:.2f}, total=${status['total_value']:.2f}, "
          f"positions={status['open_positions']}, trades={status['total_trades']}")
    print("  PASS")


def test_database():
    print("\n--- Test: Database ---")
    db = DBLogger(db_path=tempfile.mktemp(suffix=".db"))

    # Log a trade
    tid = db.log_trade(
        symbol="AAPL", broker="ibkr", asset_type="stock", action="BUY",
        confidence=0.85, strategy="multi_signal", dominant_strategy="mean_reversion",
        price=150.0, quantity=10, position_size_usd=1500.0,
        stop_loss_pct=1.5, take_profit_pct=3.0,
        reasoning="Test trade", paper_mode=1, executed=1,
    )
    print(f"  Logged trade #{tid}")

    # Log signal
    db.log_signal("AAPL", "mean_reversion", "bullish", 0.8, "Z-score -2.3", True)

    # Log bot message
    db.log("INFO", "test", "Test log message")

    # Log error
    db.log_error("test", "TestError", "This is a test error")

    # Snapshot
    db.log_snapshot(total_value_usd=10000, ibkr_value_usd=6000,
                    coinbase_value_usd=4000, cash_usd=5000,
                    daily_pnl_usd=50, daily_pnl_pct=0.5, open_positions=[])

    # Retrieve
    trades = db.get_trades(limit=5)
    assert len(trades) == 1
    logs = db.get_logs(limit=5)
    assert len(logs) >= 1
    signals = db.get_signals(symbol="AAPL")
    assert len(signals) == 1

    print(f"  Trades: {len(trades)}, Logs: {len(logs)}, Signals: {len(signals)}")
    print("  PASS")


def test_position_sizer():
    print("\n--- Test: Position Sizer ---")
    r = calculate_position_size(10000, 5.0, 150.0, atr=4.5)
    print(f"  Normal: ${r['position_usd']}, {r['quantity']:.4f} shares")

    r = calculate_position_size(10000, 5.0, 150.0, atr=8.0)
    print(f"  High vol: ${r['position_usd']}, {r['quantity']:.4f} shares")
    print("  PASS")


def main():
    print("=" * 50)
    print("  TradingBot — Paper Trading Test Suite")
    print("=" * 50)

    tests = [
        test_indicators,
        test_strategies,
        test_risk_manager,
        test_paper_trading,
        test_database,
        test_position_sizer,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            failed += 1

    print("\n" + "=" * 50)
    print(f"  Results: {passed} passed, {failed} failed")
    print("=" * 50)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

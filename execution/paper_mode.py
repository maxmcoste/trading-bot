"""Paper trading executor — simulates trades without real orders."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class PaperPosition:
    symbol: str
    broker: str
    asset_type: str
    direction: str  # "BUY" | "SELL"
    entry_price: float
    quantity: float
    position_usd: float
    stop_loss_pct: float
    take_profit_pct: float
    opened_at: str = ""
    trade_id: int = 0  # DB trade ID for close_trade()


@dataclass
class PaperPortfolio:
    cash: float = 10000.0
    positions: dict[str, PaperPosition] = field(default_factory=dict)
    trade_history: list[dict] = field(default_factory=list)

    @property
    def total_value(self) -> float:
        return self.cash + sum(p.position_usd for p in self.positions.values())


class PaperExecutor:
    def __init__(self, initial_cash: float = 10000.0):
        self.portfolio = PaperPortfolio(cash=initial_cash)

    def execute_trade(self, symbol: str, broker: str, asset_type: str,
                      action: str, price: float, quantity: float,
                      stop_loss_pct: float, take_profit_pct: float) -> dict:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        if action == "BUY":
            cost = price * quantity
            if cost > self.portfolio.cash:
                quantity = self.portfolio.cash / price
                cost = self.portfolio.cash

            self.portfolio.cash -= cost
            pos = PaperPosition(
                symbol=symbol, broker=broker, asset_type=asset_type,
                direction="BUY", entry_price=price, quantity=quantity,
                position_usd=cost, stop_loss_pct=stop_loss_pct,
                take_profit_pct=take_profit_pct, opened_at=ts,
            )
            self.portfolio.positions[symbol] = pos
            logger.info(f"[PAPER] BUY {quantity:.6f} {symbol} @ {price:.2f} = ${cost:.2f}")

            return {"executed": True, "action": "BUY", "symbol": symbol,
                    "quantity": quantity, "price": price, "cost": cost}

        elif action == "SELL" and symbol in self.portfolio.positions:
            pos = self.portfolio.positions.pop(symbol)
            revenue = price * pos.quantity
            pnl = revenue - pos.position_usd
            pnl_pct = (pnl / pos.position_usd) * 100 if pos.position_usd else 0
            self.portfolio.cash += revenue

            trade = {
                "symbol": symbol, "action": "SELL", "entry_price": pos.entry_price,
                "exit_price": price, "quantity": pos.quantity,
                "pnl_usd": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "closed_at": ts, "trade_id": pos.trade_id,
            }
            self.portfolio.trade_history.append(trade)
            logger.info(f"[PAPER] SELL {symbol} @ {price:.2f}, P&L: ${pnl:.2f} ({pnl_pct:+.1f}%)")

            return {"executed": True, **trade}

        return {"executed": False, "reason": "No position to sell" if action == "SELL" else "Unknown action"}

    def check_stops(self, current_prices: dict[str, float]) -> list[dict]:
        """Check stop-loss and take-profit for all open positions."""
        closed = []
        for symbol in list(self.portfolio.positions.keys()):
            pos = self.portfolio.positions[symbol]
            price = current_prices.get(symbol)
            if price is None:
                continue

            pnl_pct = ((price - pos.entry_price) / pos.entry_price) * 100

            if pnl_pct <= -pos.stop_loss_pct:
                result = self.execute_trade(symbol, pos.broker, pos.asset_type,
                                            "SELL", price, pos.quantity, 0, 0)
                result["trigger"] = "stop_loss"
                closed.append(result)
            elif pnl_pct >= pos.take_profit_pct:
                result = self.execute_trade(symbol, pos.broker, pos.asset_type,
                                            "SELL", price, pos.quantity, 0, 0)
                result["trigger"] = "take_profit"
                closed.append(result)

        return closed

    def get_status(self) -> dict:
        return {
            "cash": round(self.portfolio.cash, 2),
            "total_value": round(self.portfolio.total_value, 2),
            "open_positions": len(self.portfolio.positions),
            "total_trades": len(self.portfolio.trade_history),
        }


if __name__ == "__main__":
    ex = PaperExecutor(initial_cash=10000.0)

    ex.execute_trade("AAPL", "ibkr", "stock", "BUY", 150.0, 10, 1.5, 3.0)
    ex.execute_trade("BTC-USD", "coinbase", "crypto", "BUY", 65000.0, 0.01, 3.0, 6.0)

    print(f"Status: {ex.get_status()}")
    print(f"Positions: {list(ex.portfolio.positions.keys())}")

    # Simulate stop-loss hit
    closed = ex.check_stops({"AAPL": 147.0, "BTC-USD": 62000.0})
    print(f"Stop checks: {closed}")

    # Sell remaining
    ex.execute_trade("AAPL", "ibkr", "stock", "SELL", 155.0, 10, 0, 0)
    print(f"Final status: {ex.get_status()}")

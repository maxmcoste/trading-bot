"""IBKR order executor."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


class IBKRExecutor:
    def __init__(self, ibkr_client):
        self.ib = ibkr_client

    async def execute(self, symbol: str, action: str, quantity: float,
                      price: float, order_type: str = "MKT") -> dict:
        if not self.ib.connected:
            return {"executed": False, "reason": "IBKR not connected"}

        try:
            from ib_insync import Stock, MarketOrder, LimitOrder

            contract = Stock(symbol, "SMART", "USD")
            await asyncio.to_thread(self.ib.ib.qualifyContracts, contract)

            ib_action = "BUY" if action == "BUY" else "SELL"
            qty = int(quantity) if quantity >= 1 else 1

            if order_type == "MKT":
                order = MarketOrder(ib_action, qty)
            else:
                order = LimitOrder(ib_action, qty, price)

            trade = await asyncio.to_thread(self.ib.ib.placeOrder, contract, order)
            logger.info(f"[IBKR] {ib_action} {qty} {symbol} — order placed")

            return {
                "executed": True, "action": ib_action, "symbol": symbol,
                "quantity": qty, "price": price, "order_id": trade.order.orderId,
            }
        except Exception as e:
            logger.error(f"IBKR execution failed for {symbol}: {e}")
            return {"executed": False, "reason": str(e)}


if __name__ == "__main__":
    print("IBKRExecutor ready. Requires connected IBKRClient.")

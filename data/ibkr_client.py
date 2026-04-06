"""Interactive Brokers client for market data via ib_insync."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pandas as pd

logger = logging.getLogger(__name__)


class IBKRClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 7497, client_id: int = 1):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.ib = None
        self._connected = False

    async def connect(self):
        try:
            from ib_insync import IB
            self.ib = IB()
            await asyncio.to_thread(
                self.ib.connect, self.host, self.port, clientId=self.client_id
            )
            self._connected = True
            logger.info(f"Connected to IBKR at {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"IBKR connection failed: {e}")
            self._connected = False

    async def disconnect(self):
        if self.ib and self._connected:
            await asyncio.to_thread(self.ib.disconnect)
            self._connected = False
            logger.info("Disconnected from IBKR")

    async def reconnect(self):
        await self.disconnect()
        await asyncio.sleep(2)
        await self.connect()

    @property
    def connected(self) -> bool:
        return self._connected and self.ib is not None

    async def get_ohlcv(self, symbol: str, duration: str = "1 D",
                        bar_size: str = "5 mins", count: int = 100) -> pd.DataFrame:
        if not self.connected:
            logger.warning("IBKR not connected, returning empty DataFrame")
            return pd.DataFrame()

        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            await asyncio.to_thread(self.ib.qualifyContracts, contract)

            bars = await asyncio.to_thread(
                self.ib.reqHistoricalData,
                contract, endDateTime="", durationStr=duration,
                barSizeSetting=bar_size, whatToShow="TRADES",
                useRTH=True, formatDate=1,
            )

            if not bars:
                return pd.DataFrame()

            df = pd.DataFrame([{
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "volume": float(b.volume),
                "timestamp": b.date,
            } for b in bars])

            return df.tail(count)

        except Exception as e:
            logger.error(f"IBKR OHLCV fetch failed for {symbol}: {e}")
            return pd.DataFrame()

    async def get_current_price(self, symbol: str) -> float | None:
        if not self.connected:
            return None
        try:
            from ib_insync import Stock
            contract = Stock(symbol, "SMART", "USD")
            await asyncio.to_thread(self.ib.qualifyContracts, contract)
            ticker = self.ib.reqMktData(contract, snapshot=True)
            await asyncio.sleep(2)
            self.ib.cancelMktData(contract)
            return ticker.last if ticker.last and ticker.last > 0 else ticker.close
        except Exception as e:
            logger.error(f"IBKR price fetch failed for {symbol}: {e}")
            return None

    async def get_portfolio_value(self) -> float:
        if not self.connected:
            return 0.0
        try:
            account_values = await asyncio.to_thread(self.ib.accountValues)
            for av in account_values:
                if av.tag == "NetLiquidation" and av.currency == "USD":
                    return float(av.value)
            return 0.0
        except Exception as e:
            logger.error(f"IBKR portfolio value fetch failed: {e}")
            return 0.0


if __name__ == "__main__":
    client = IBKRClient()
    print(f"IBKR client configured: {client.host}:{client.port}")
    print("Note: connect() requires TWS/Gateway running")

"""Coinbase Advanced Trade client using the official coinbase-advanced-py SDK."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

logger = logging.getLogger(__name__)


class CoinbaseClient:
    def __init__(self, api_key: str = "", api_secret: str = "",
                 quote_currency: str = "EUR"):
        self.api_key = api_key
        # Handle escaped newlines in .env
        self.api_secret = api_secret.replace("\\n", "\n")
        self.quote_currency = quote_currency
        self._client = None
        self._portfolio_cache: dict | None = None
        self._portfolio_cache_ts: float = 0.0
        self._portfolio_uuid: str | None = None

    def _get_client(self):
        if self._client is None:
            from coinbase.rest import RESTClient
            self._client = RESTClient(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
        return self._client

    # ── Market Data ──────────────────────────────────────────

    async def get_ohlcv(self, symbol: str, granularity: str = "FIVE_MINUTE",
                        count: int = 100) -> pd.DataFrame:
        try:
            client = self._get_client()
            end = datetime.now(timezone.utc)
            start = end - timedelta(minutes=5 * count)

            candles = await asyncio.to_thread(
                client.get_candles,
                product_id=symbol,
                start=str(int(start.timestamp())),
                end=str(int(end.timestamp())),
                granularity=granularity,
            )

            candle_list = candles.get("candles", []) if isinstance(candles, dict) else getattr(candles, "candles", [])
            if not candle_list:
                return pd.DataFrame()

            rows = []
            for c in candle_list:
                if isinstance(c, dict):
                    rows.append({
                        "timestamp": datetime.fromtimestamp(int(c["start"]), tz=timezone.utc),
                        "open": float(c["open"]),
                        "high": float(c["high"]),
                        "low": float(c["low"]),
                        "close": float(c["close"]),
                        "volume": float(c["volume"]),
                    })
                else:
                    rows.append({
                        "timestamp": datetime.fromtimestamp(int(c.start), tz=timezone.utc),
                        "open": float(c.open),
                        "high": float(c.high),
                        "low": float(c.low),
                        "close": float(c.close),
                        "volume": float(c.volume),
                    })

            return pd.DataFrame(rows).sort_values("timestamp").tail(count).reset_index(drop=True)

        except Exception as e:
            logger.error(f"Coinbase OHLCV failed for {symbol}: {e}")
            return pd.DataFrame()

    async def get_current_price(self, symbol: str) -> float | None:
        try:
            client = self._get_client()
            product = await asyncio.to_thread(client.get_product, product_id=symbol)
            if isinstance(product, dict):
                return float(product.get("price", 0))
            return float(product.price) if hasattr(product, "price") else None
        except Exception as e:
            logger.error(f"Coinbase price failed for {symbol}: {e}")
            return None

    def _is_crypto_quote(self, symbol: str) -> bool:
        """Check if the quote currency is a crypto (not fiat)."""
        fiat = {"EUR", "USD", "GBP", "USDC", "USDT", "DAI", "CAD", "SGD"}
        parts = symbol.split("-")
        return len(parts) == 2 and parts[1] not in fiat

    async def get_cross_rate_ohlcv(self, symbol: str, granularity: str = "FIVE_MINUTE",
                                    count: int = 100) -> pd.DataFrame:
        """Get OHLCV for a crypto-to-crypto pair by computing cross-rate from EUR pairs.

        E.g., SOL-BTC = SOL-EUR / BTC-EUR (each candle divided element-wise).
        """
        base, quote = symbol.split("-")
        base_pair = f"{base}-{self.quote_currency}"
        quote_pair = f"{quote}-{self.quote_currency}"

        base_df, quote_df = await asyncio.gather(
            self.get_ohlcv(base_pair, granularity, count),
            self.get_ohlcv(quote_pair, granularity, count),
        )

        if base_df is None or base_df.empty or quote_df is None or quote_df.empty:
            return pd.DataFrame()

        # Merge on timestamp (inner join)
        merged = pd.merge(base_df, quote_df, on="timestamp", suffixes=("_base", "_quote"))
        if merged.empty:
            return pd.DataFrame()

        # Cross rate: base_eur / quote_eur
        result = pd.DataFrame({
            "timestamp": merged["timestamp"],
            "open": merged["open_base"] / merged["open_quote"],
            "high": merged["high_base"] / merged["high_quote"],
            "low": merged["low_base"] / merged["low_quote"],
            "close": merged["close_base"] / merged["close_quote"],
            "volume": merged["volume_base"],  # base volume in base currency units
        })
        return result.tail(count).reset_index(drop=True)

    async def get_cross_rate_price(self, symbol: str) -> float | None:
        """Get current cross-rate price for a crypto-to-crypto pair."""
        base, quote = symbol.split("-")
        base_pair = f"{base}-{self.quote_currency}"
        quote_pair = f"{quote}-{self.quote_currency}"
        base_price, quote_price = await asyncio.gather(
            self.get_current_price(base_pair),
            self.get_current_price(quote_pair),
        )
        if base_price and quote_price and quote_price > 0:
            return base_price / quote_price
        return None

    # ── Portfolio & Accounts ─────────────────────────────────

    async def get_accounts(self) -> list:
        """Fetch all Coinbase accounts with balances."""
        try:
            client = self._get_client()
            result = await asyncio.to_thread(client.get_accounts, limit=250)
            if isinstance(result, dict):
                return result.get("accounts", [])
            return list(getattr(result, "accounts", []))
        except Exception as e:
            logger.error(f"Coinbase accounts failed: {e}")
            return []

    async def _get_default_portfolio_uuid(self) -> str | None:
        """Get the UUID of the default portfolio (cached)."""
        if self._portfolio_uuid:
            return self._portfolio_uuid
        try:
            client = self._get_client()
            result = await asyncio.to_thread(client.get_portfolios)
            portfolios = result.get("portfolios", []) if isinstance(result, dict) else getattr(result, "portfolios", [])
            for p in portfolios:
                ptype = p.get("type", "") if isinstance(p, dict) else getattr(p, "type", "")
                if ptype == "DEFAULT":
                    self._portfolio_uuid = p.get("uuid", "") if isinstance(p, dict) else getattr(p, "uuid", "")
                    return self._portfolio_uuid
            # Fallback: return first portfolio
            if portfolios:
                p = portfolios[0]
                self._portfolio_uuid = p.get("uuid", "") if isinstance(p, dict) else getattr(p, "uuid", "")
                return self._portfolio_uuid
        except Exception as e:
            logger.error(f"Failed to get portfolio UUID: {e}")
        return None

    async def get_portfolio_breakdown(self) -> dict:
        """Return detailed portfolio using Coinbase's native breakdown API.

        This uses get_portfolio_breakdown which returns values already
        converted to the account's native currency (EUR).
        """
        now = time.time()
        if self._portfolio_cache and (now - self._portfolio_cache_ts) < 1:
            return self._portfolio_cache

        qc = self.quote_currency
        portfolio_uuid = await self._get_default_portfolio_uuid()

        if not portfolio_uuid:
            logger.warning("No portfolio UUID found, falling back to accounts API")
            return await self._portfolio_breakdown_fallback()

        try:
            client = self._get_client()
            bd = await asyncio.to_thread(client.get_portfolio_breakdown, portfolio_uuid)
            breakdown = getattr(bd, "breakdown", bd) if not isinstance(bd, dict) else bd.get("breakdown", bd)
        except Exception as e:
            logger.error(f"Portfolio breakdown API failed: {e}")
            return await self._portfolio_breakdown_fallback()

        # Parse totals from portfolio_balances
        pb = breakdown.get("portfolio_balances", {}) if isinstance(breakdown, dict) else getattr(breakdown, "portfolio_balances", {})
        if isinstance(pb, dict):
            total_val = float(pb.get("total_balance", {}).get("value", 0))
            crypto_val = float(pb.get("total_crypto_balance", {}).get("value", 0))
            cash_val = float(pb.get("total_cash_equivalent_balance", {}).get("value", 0))
        else:
            total_val = float(getattr(getattr(pb, "total_balance", None), "value", 0) or 0)
            crypto_val = float(getattr(getattr(pb, "total_crypto_balance", None), "value", 0) or 0)
            cash_val = float(getattr(getattr(pb, "total_cash_equivalent_balance", None), "value", 0) or 0)

        # Parse individual positions
        spots = breakdown.get("spot_positions", []) if isinstance(breakdown, dict) else getattr(breakdown, "spot_positions", [])
        holdings = []
        for pos in spots:
            if isinstance(pos, dict):
                asset = pos.get("asset", "")
                val = float(pos.get("total_balance_fiat", 0) or 0)
                amount = float(pos.get("total_balance_crypto", 0) or 0)
                is_cash = pos.get("is_cash", False)
            else:
                asset = getattr(pos, "asset", "")
                val = float(getattr(pos, "total_balance_fiat", 0) or 0)
                amount = float(getattr(pos, "total_balance_crypto", 0) or 0)
                is_cash = getattr(pos, "is_cash", False)

            if val > 0.01:
                holdings.append({
                    "currency": asset,
                    "balance": amount,
                    "value": round(val, 2),
                    "price": round(val / amount, 2) if amount > 0 else 0,
                    "type": "fiat" if is_cash else "crypto",
                })

        result = {
            "total": round(total_val, 2),
            "cash": round(cash_val, 2),
            "crypto": round(crypto_val, 2),
            "quote_currency": qc,
            "holdings": sorted(holdings, key=lambda h: h["value"], reverse=True),
            "num_assets": len(holdings),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            # Backward-compat aliases
            "total_usd": round(total_val, 2),
            "cash_usd": round(cash_val, 2),
            "crypto_usd": round(crypto_val, 2),
        }

        self._portfolio_cache = result
        self._portfolio_cache_ts = now
        return result

    async def _portfolio_breakdown_fallback(self) -> dict:
        """Fallback: build portfolio from accounts + price lookups."""
        accounts = await self.get_accounts()
        qc = self.quote_currency
        holdings = []
        total_val = 0.0
        cash_val = 0.0

        for acc in accounts:
            def _get(obj, key, default="0"):
                return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)

            def _bal(obj):
                if obj is None:
                    return 0.0
                return float(obj.get("value", 0)) if isinstance(obj, dict) else float(getattr(obj, "value", 0))

            currency = _get(acc, "currency", "")
            balance = _bal(_get(acc, "available_balance", None)) + _bal(_get(acc, "hold", None))
            if balance <= 0.001:
                continue

            if currency in ("USD", "EUR", "GBP", "USDC", "USDT"):
                cash_val += balance
                total_val += balance
                holdings.append({"currency": currency, "balance": balance, "value": round(balance, 2), "type": "fiat"})
            else:
                price = await self.get_current_price(f"{currency}-{qc}")
                if not price or price <= 0:
                    price = await self.get_current_price(f"{currency}-USD")
                    if price and price > 0:
                        price *= 0.87  # rough fallback
                value = (balance * price) if price and price > 0 else 0
                if value > 0.01:
                    total_val += value
                    holdings.append({"currency": currency, "balance": balance, "value": round(value, 2), "price": price, "type": "crypto"})

        return {
            "total": round(total_val, 2), "cash": round(cash_val, 2),
            "crypto": round(total_val - cash_val, 2), "quote_currency": qc,
            "holdings": sorted(holdings, key=lambda h: h["value"], reverse=True),
            "num_assets": len(holdings), "fetched_at": datetime.now(timezone.utc).isoformat(),
            "total_usd": round(total_val, 2), "cash_usd": round(cash_val, 2), "crypto_usd": round(total_val - cash_val, 2),
        }

    async def get_portfolio_value(self) -> float:
        breakdown = await self.get_portfolio_breakdown()
        return breakdown["total_usd"]

    async def test_connection(self) -> dict:
        try:
            accounts = await self.get_accounts()
            if not accounts:
                return {"connected": False, "error": "No accounts returned"}
            total = await self.get_portfolio_value()
            return {"connected": True, "accounts": len(accounts), "total_usd": total}
        except Exception as e:
            return {"connected": False, "error": str(e)}


if __name__ == "__main__":
    import os

    async def main():
        from config import settings
        client = CoinbaseClient(api_key=settings.COINBASE_API_KEY,
                                api_secret=settings.COINBASE_API_SECRET,
                                quote_currency=settings.DEFAULT_CURRENCY)

        print(f"Testing connection (quote={settings.DEFAULT_CURRENCY})...")
        status = await client.test_connection()
        print(f"  Connected: {status.get('connected')}")
        if not status.get("connected"):
            print(f"  Error: {status.get('error')}")
            return

        print(f"  Accounts: {status.get('accounts')}")
        print(f"  Total: {status.get('total_usd', 0):.2f} {settings.DEFAULT_CURRENCY}")

        print("\nPortfolio breakdown:")
        breakdown = await client.get_portfolio_breakdown()
        qc = breakdown.get("quote_currency", "EUR")
        print(f"  Total:  {breakdown['total']:.2f} {qc}")
        print(f"  Cash:   {breakdown['cash']:.2f} {qc}")
        print(f"  Crypto: {breakdown['crypto']:.2f} {qc}")
        for h in breakdown["holdings"]:
            print(f"    {h['currency']}: {h['balance']:.8f} = {h['value']:.2f} {qc} ({h['type']})")

    asyncio.run(main())

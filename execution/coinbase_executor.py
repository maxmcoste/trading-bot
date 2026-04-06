"""Coinbase Advanced Trade order executor using the official SDK.

Supports two execution paths:
1. Market orders — for tradeable pairs (e.g. BTC-EUR if available)
2. Convert — for crypto-to-fiat or crypto-to-crypto when no direct pair exists
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid

logger = logging.getLogger(__name__)


class CoinbaseExecutor:
    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.api_key = api_key
        self.api_secret = api_secret.replace("\\n", "\n")
        self._client = None
        self._valid_products: dict[str, dict] | None = None  # product_id -> {base_increment, quote_increment}
        self._accounts_cache: dict[str, str] | None = None  # currency -> account UUID

    def _get_client(self):
        if self._client is None:
            from coinbase.rest import RESTClient
            self._client = RESTClient(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
        return self._client

    async def _ensure_valid_products(self):
        """Cache tradeable product IDs with their size increments."""
        if self._valid_products is not None:
            return
        try:
            client = self._get_client()
            resp = await asyncio.to_thread(client.get_products, get_all_products=True)
            products = resp.get("products", []) if isinstance(resp, dict) else getattr(resp, "products", [])
            self._valid_products = {}
            for p in products:
                if isinstance(p, dict):
                    pid = p.get("product_id", "")
                    disabled = p.get("is_disabled", False)
                    trading_disabled = p.get("trading_disabled", False)
                    base_inc = p.get("base_increment", "0.00000001")
                    quote_inc = p.get("quote_increment", "0.01")
                else:
                    pid = getattr(p, "product_id", "")
                    disabled = getattr(p, "is_disabled", False)
                    trading_disabled = getattr(p, "trading_disabled", False)
                    base_inc = getattr(p, "base_increment", "0.00000001")
                    quote_inc = getattr(p, "quote_increment", "0.01")
                if pid and not disabled and not trading_disabled:
                    self._valid_products[pid] = {
                        "base_increment": float(base_inc) if base_inc else 1e-8,
                        "quote_increment": float(quote_inc) if quote_inc else 0.01,
                    }
            logger.info(f"[COINBASE] Cached {len(self._valid_products)} tradeable products")
        except Exception as e:
            logger.error(f"Failed to fetch product list: {e}")
            self._valid_products = {}

    def _round_to_increment(self, value: float, increment: float) -> str:
        """Round value DOWN to the nearest increment and return as string."""
        if increment <= 0:
            increment = 1e-8
        # Number of decimal places from the increment
        decimals = max(0, -int(math.floor(math.log10(increment))))
        rounded = math.floor(value / increment) * increment
        return f"{rounded:.{decimals}f}"

    async def _ensure_accounts(self):
        """Cache account UUIDs keyed by currency (needed for convert API)."""
        if self._accounts_cache is not None:
            return
        try:
            client = self._get_client()
            result = await asyncio.to_thread(client.get_accounts, limit=250)
            accounts = result.get("accounts", []) if isinstance(result, dict) else getattr(result, "accounts", [])
            self._accounts_cache = {}
            for acc in accounts:
                if isinstance(acc, dict):
                    currency = acc.get("currency", "")
                    acc_uuid = acc.get("uuid", "")
                else:
                    currency = getattr(acc, "currency", "")
                    acc_uuid = getattr(acc, "uuid", "")
                if currency and acc_uuid:
                    self._accounts_cache[currency] = acc_uuid
            logger.info(f"[COINBASE] Cached {len(self._accounts_cache)} account UUIDs")
        except Exception as e:
            logger.error(f"Failed to fetch accounts: {e}")
            self._accounts_cache = {}

    def _parse_order_response(self, order) -> dict:
        """Extract success/failure from SDK order response."""
        if isinstance(order, dict):
            success = order.get("success", False)
            order_id = (order.get("order_id", "")
                        or (order.get("success_response") or {}).get("order_id", ""))
            failure_reason = order.get("failure_reason", "")
            error_resp = order.get("error_response", {})
        else:
            success = getattr(order, "success", False)
            order_id = getattr(order, "order_id", "")
            sr = getattr(order, "success_response", None)
            if not order_id and sr:
                order_id = (sr.get("order_id", "") if isinstance(sr, dict)
                            else getattr(sr, "order_id", ""))
            failure_reason = getattr(order, "failure_reason", "")
            error_resp = getattr(order, "error_response", {})

        if not success:
            err_msg = failure_reason or ""
            if error_resp:
                detail = (error_resp.get("error", "") if isinstance(error_resp, dict)
                          else getattr(error_resp, "error", ""))
                preview = (error_resp.get("preview_failure_reason", "") if isinstance(error_resp, dict)
                           else getattr(error_resp, "preview_failure_reason", ""))
                err_msg = detail or preview or err_msg or "Order rejected"
            return {"success": False, "reason": err_msg or "Order rejected"}

        return {"success": True, "order_id": order_id}

    async def _execute_market_order(self, symbol: str, action: str,
                                     quote_size: float) -> dict:
        """Place a market order on a tradeable pair."""
        client = self._get_client()
        client_order_id = str(uuid.uuid4())
        product_info = (self._valid_products or {}).get(symbol, {})
        base_inc = product_info.get("base_increment", 1e-8)
        quote_inc = product_info.get("quote_increment", 0.01)

        if action.upper() == "BUY":
            order = await asyncio.to_thread(
                client.market_order_buy,
                client_order_id=client_order_id,
                product_id=symbol,
                quote_size=self._round_to_increment(quote_size, quote_inc),
            )
        else:
            order = await asyncio.to_thread(
                client.market_order_sell,
                client_order_id=client_order_id,
                product_id=symbol,
                base_size=self._round_to_increment(quote_size, base_inc),
            )

        result = self._parse_order_response(order)
        if result["success"]:
            return {
                "executed": True, "method": "market_order",
                "order_id": result["order_id"] or client_order_id,
            }
        return {"executed": False, "reason": result["reason"]}

    async def _execute_convert(self, from_currency: str, to_currency: str,
                                amount: str) -> dict:
        """Convert between currencies using the convert API (2-step: quote + commit)."""
        await self._ensure_accounts()

        from_account = (self._accounts_cache or {}).get(from_currency, "")
        to_account = (self._accounts_cache or {}).get(to_currency, "")

        if not from_account:
            return {"executed": False, "reason": f"No account found for {from_currency}"}
        if not to_account:
            return {"executed": False, "reason": f"No account found for {to_currency}"}

        client = self._get_client()

        # Step 1: Create quote
        try:
            quote = await asyncio.to_thread(
                client.create_convert_quote,
                from_account=from_account,
                to_account=to_account,
                amount=amount,
            )
        except Exception as e:
            return {"executed": False, "reason": f"Convert quote failed: {e}"}

        # Extract trade_id from quote
        trade = quote.get("trade", quote) if isinstance(quote, dict) else getattr(quote, "trade", quote)
        trade_id = (trade.get("id", "") if isinstance(trade, dict)
                    else getattr(trade, "id", ""))

        if not trade_id:
            return {"executed": False, "reason": "No trade_id in convert quote response"}

        # Step 2: Commit the trade
        try:
            commit = await asyncio.to_thread(
                client.commit_convert_trade,
                trade_id=trade_id,
                from_account=from_account,
                to_account=to_account,
            )
        except Exception as e:
            return {"executed": False, "reason": f"Convert commit failed: {e}"}

        return {
            "executed": True, "method": "convert",
            "trade_id": trade_id,
        }

    async def execute(self, symbol: str, action: str, quote_size: float,
                      price: float | None = None) -> dict:
        """Execute a trade on Coinbase.

        Tries market order first if the product pair is tradeable.
        Falls back to convert API for crypto<->fiat or crypto<->crypto.

        Args:
            symbol: Trading pair (e.g. "BTC-EUR", "ETH-EUR")
            action: "BUY" or "SELL"
            quote_size: Amount in quote currency (EUR) for BUY,
                        or amount of base currency for SELL
            price: Optional limit price (None = market order)
        """
        await self._ensure_valid_products()

        parts = symbol.split("-")
        if len(parts) != 2:
            return {"executed": False, "reason": f"Invalid symbol format: {symbol}"}

        base_currency, quote_currency = parts  # e.g. "BTC", "EUR"

        # Try market order if pair is directly tradeable
        if self._valid_products and symbol in self._valid_products:
            try:
                result = await self._execute_market_order(symbol, action, quote_size)
                if result["executed"]:
                    logger.info(f"[COINBASE] {action} {symbol} {quote_size:.2f} "
                                f"via market order — {result.get('order_id', '')}")
                    return {"executed": True, "action": action, "symbol": symbol,
                            "quote_size": quote_size, "method": "market_order",
                            "order_id": result.get("order_id", "")}
                reason = result.get("reason", "")
                # Don't fall back to convert for known non-recoverable errors
                reason_upper = reason.upper()
                if any(k in reason_upper for k in (
                    "INSUFFICIENT", "FUND", "MINIMUM", "TOO_SMALL",
                    "INVALID_LIMIT_PRICE", "INVALID_CANCEL",
                )):
                    logger.error(f"[COINBASE] {action} {symbol} failed: {reason}")
                    return {"executed": False, "reason": reason}
                # For SELL, also skip convert — market order should be the path
                if action.upper() == "SELL":
                    logger.error(f"[COINBASE] SELL {symbol} market order failed: {reason}")
                    return {"executed": False, "reason": reason}
                # For BUY failures, try convert as fallback
                logger.warning(f"[COINBASE] Market order failed for {symbol}: {reason}, "
                               f"trying convert...")
            except Exception as e:
                logger.warning(f"[COINBASE] Market order error for {symbol}: {e}, trying convert...")

        # Fall back to convert API
        if action.upper() == "BUY":
            # Convert EUR -> crypto (spend quote_size EUR to buy base_currency)
            result = await self._execute_convert(
                from_currency=quote_currency,
                to_currency=base_currency,
                amount=str(round(quote_size, 2)),
            )
        else:
            # Convert crypto -> EUR (sell quote_size of base_currency)
            result = await self._execute_convert(
                from_currency=base_currency,
                to_currency=quote_currency,
                amount=str(round(quote_size, 8)),
            )

        if result["executed"]:
            logger.info(f"[COINBASE] {action} {symbol} {quote_size:.2f} "
                        f"via convert — trade {result.get('trade_id', '')}")
            return {"executed": True, "action": action, "symbol": symbol,
                    "quote_size": quote_size, "method": "convert",
                    "trade_id": result.get("trade_id", "")}

        logger.error(f"[COINBASE] {action} {symbol} failed: {result['reason']}")
        return {"executed": False, "reason": result["reason"]}


if __name__ == "__main__":
    print("CoinbaseExecutor ready. Requires API key/secret for live orders.")

"""Position sizing based on ATR and portfolio value."""

from __future__ import annotations


def calculate_position_size(
    portfolio_value: float,
    position_size_pct: float,
    price: float,
    atr: float | None = None,
) -> dict:
    """Return position size in USD and quantity."""
    position_usd = portfolio_value * (position_size_pct / 100.0)

    # ATR-based adjustment: reduce size if volatility is high
    if atr is not None and price > 0:
        atr_pct = (atr / price) * 100
        if atr_pct > 3.0:
            position_usd *= 0.7  # reduce 30% for high volatility
        elif atr_pct > 2.0:
            position_usd *= 0.85

    quantity = position_usd / price if price > 0 else 0
    return {
        "position_usd": round(position_usd, 2),
        "quantity": round(quantity, 6),
        "price": price,
    }


if __name__ == "__main__":
    result = calculate_position_size(
        portfolio_value=10000, position_size_pct=5.0,
        price=150.0, atr=4.5,
    )
    print(f"Position: ${result['position_usd']} = {result['quantity']} shares @ ${result['price']}")

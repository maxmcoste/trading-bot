"""
Technical indicators calculated on OHLCV DataFrames using pandas_ta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import math

import pandas as pd
import pandas_ta as ta


@dataclass
class TechnicalSignals:
    rsi: Optional[float] = None
    macd_value: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_mid: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_position: Optional[float] = None  # 0-1
    volume_ratio: Optional[float] = None
    atr: Optional[float] = None
    ema_20: Optional[float] = None
    ema_50: Optional[float] = None
    z_score: Optional[float] = None


def _safe(v) -> Optional[float]:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Append all indicator columns to an OHLCV DataFrame."""
    df = df.copy()

    df.ta.rsi(length=14, append=True)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    df.ta.bbands(length=20, std=2, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.ema(length=20, append=True)
    df.ta.ema(length=50, append=True)
    df.ta.sma(length=30, col="volume", append=True)

    # Z-score: (close - SMA20) / STD20
    sma20 = df["close"].rolling(20).mean()
    std20 = df["close"].rolling(20).std()
    df["z_score"] = (df["close"] - sma20) / std20

    # Bollinger band position 0-1
    bb_l = f"BBL_20_2.0"
    bb_u = f"BBU_20_2.0"
    if bb_l in df.columns and bb_u in df.columns:
        span = df[bb_u] - df[bb_l]
        df["bb_position"] = (df["close"] - df[bb_l]) / span.replace(0, float("nan"))

    # Volume ratio
    vol_sma_col = [c for c in df.columns if "SMA_30" in c and "volume" in c.lower()]
    if not vol_sma_col:
        df["volume_sma30"] = df["volume"].rolling(30).mean()
        vol_sma_col = ["volume_sma30"]
    df["volume_ratio"] = df["volume"] / df[vol_sma_col[0]].replace(0, float("nan"))

    return df


def latest_signals(df: pd.DataFrame) -> TechnicalSignals:
    """Return TechnicalSignals from the last row of a computed DataFrame."""
    if df.empty:
        return TechnicalSignals()

    r = df.iloc[-1]

    macd_cols = [c for c in df.columns if c.startswith("MACD")]
    macd_val = _safe(r.get(macd_cols[0])) if len(macd_cols) > 0 else None
    macd_sig = _safe(r.get(macd_cols[1])) if len(macd_cols) > 1 else None
    macd_hist = _safe(r.get(macd_cols[2])) if len(macd_cols) > 2 else None

    return TechnicalSignals(
        rsi=_safe(r.get("RSI_14")),
        macd_value=macd_val,
        macd_signal=macd_sig,
        macd_histogram=macd_hist,
        bb_upper=_safe(r.get("BBU_20_2.0")),
        bb_mid=_safe(r.get("BBM_20_2.0")),
        bb_lower=_safe(r.get("BBL_20_2.0")),
        bb_position=_safe(r.get("bb_position")),
        volume_ratio=_safe(r.get("volume_ratio")),
        atr=_safe(r.get("ATRr_14")),
        ema_20=_safe(r.get("EMA_20")),
        ema_50=_safe(r.get("EMA_50")),
        z_score=_safe(r.get("z_score")),
    )


def calculate_btc_correlation(asset_df, btc_df, periods: int = 24) -> float:
    """Rolling Pearson correlation between asset and BTC percentage returns.

    Args:
        asset_df: OHLCV DataFrame of the asset.
        btc_df:   OHLCV DataFrame of BTC.
        periods:  number of candles (default 24 = ~2h on 5min candles).

    Returns:
        Float in [-1.0, +1.0], or 0.5 (neutral) if data is insufficient.
    """
    try:
        if asset_df is None or btc_df is None:
            return 0.5
        if len(asset_df) < periods or len(btc_df) < periods:
            return 0.5

        asset_returns = asset_df["close"].pct_change().dropna().tail(periods)
        btc_returns = btc_df["close"].pct_change().dropna().tail(periods)

        min_len = min(len(asset_returns), len(btc_returns))
        if min_len < 5:
            return 0.5

        corr = asset_returns.tail(min_len).corr(btc_returns.tail(min_len))
        if pd.isna(corr):
            return 0.5
        return round(float(corr), 3)
    except Exception:
        return 0.5


if __name__ == "__main__":
    import numpy as np

    np.random.seed(42)
    n = 200
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "open": close + np.random.randn(n) * 0.2,
        "high": close + abs(np.random.randn(n) * 0.5),
        "low": close - abs(np.random.randn(n) * 0.5),
        "close": close,
        "volume": np.random.randint(1000, 10000, n).astype(float),
    })

    df = calculate_indicators(df)
    sig = latest_signals(df)
    print("Technical Signals:")
    for k, v in sig.__dict__.items():
        print(f"  {k}: {v}")

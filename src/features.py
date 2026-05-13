from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class FeatureSnapshot:
    symbol: str
    close: float
    features: dict[str, float | str]


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False).mean()


def zscore(series: pd.Series, period: int = 50) -> pd.Series:
    mean = series.rolling(period).mean()
    std = series.rolling(period).std(ddof=0)
    return (series - mean) / std.replace(0, np.nan)


def build_features(symbol: str, df: pd.DataFrame, funding_rows: list[dict[str, Any]], mark_price: dict[str, Any]) -> FeatureSnapshot:
    work = df.copy()
    work["rsi_14"] = rsi(work["close"])
    work["atr_14"] = atr(work)
    work["ema_20"] = work["close"].ewm(span=20, adjust=False).mean()
    work["ema_50"] = work["close"].ewm(span=50, adjust=False).mean()
    work["return_1"] = work["close"].pct_change()
    work["return_12"] = work["close"].pct_change(12)
    work["z_close_50"] = zscore(work["close"], 50)
    work["volume_spike"] = work["volume"] / work["volume"].rolling(50).median()
    work["realized_vol_50"] = work["return_1"].rolling(50).std() * np.sqrt(50)
    work["taker_buy_ratio"] = work["taker_buy_base"] / work["volume"].replace(0, np.nan)
    latest = work.iloc[-1]
    close = float(latest["close"])
    funding_values = [float(row["fundingRate"]) for row in funding_rows if "fundingRate" in row]
    latest_funding = funding_values[-1] if funding_values else 0.0
    funding_z = 0.0
    if len(funding_values) >= 5 and np.std(funding_values) > 0:
        funding_z = float((latest_funding - np.mean(funding_values)) / np.std(funding_values))
    mark = float(mark_price.get("markPrice", close) or close)
    index = float(mark_price.get("indexPrice", close) or close)
    premium = (mark - index) / index if index else 0.0
    ema_distance = (close - float(latest["ema_50"])) / float(latest["ema_50"])
    trend = "bull" if latest["ema_20"] > latest["ema_50"] and latest["return_12"] > 0 else "bear"
    if abs(float(latest["return_12"])) < float(latest["realized_vol_50"] or 0) * 0.25:
        trend = "range"

    high_risk_score = 0.0
    high_risk_score += min(abs(float(latest["z_close_50"] or 0)) / 3, 1)
    high_risk_score += min(float(latest["volume_spike"] or 0) / 5, 1)
    high_risk_score += min(abs(funding_z) / 3, 1)
    high_risk_score += min(abs(premium) / 0.002, 1)
    high_risk_score /= 4

    features: dict[str, float | str] = {
        "rsi_14": round(float(latest["rsi_14"]), 4),
        "atr_14": round(float(latest["atr_14"]), 6),
        "atr_pct": round(float(latest["atr_14"]) / close, 6),
        "z_close_50": round(float(latest["z_close_50"]), 4),
        "volume_spike": round(float(latest["volume_spike"]), 4),
        "ema_50_distance": round(float(ema_distance), 6),
        "return_12": round(float(latest["return_12"]), 6),
        "realized_vol_50": round(float(latest["realized_vol_50"]), 6),
        "taker_buy_ratio": round(float(latest["taker_buy_ratio"]), 4),
        "funding_rate": round(latest_funding, 8),
        "funding_z": round(funding_z, 4),
        "mark_index_premium": round(premium, 6),
        "regime": trend,
        "high_risk_score": round(high_risk_score, 4),
    }
    return FeatureSnapshot(symbol=symbol, close=close, features=features)


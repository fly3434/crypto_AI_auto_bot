from __future__ import annotations

from dataclasses import dataclass
from itertools import islice, product
from typing import Any

import numpy as np
import pandas as pd

from .features import atr, rsi


@dataclass(frozen=True)
class OptimizedParams:
    rsi_buy: int
    rsi_sell: int
    atr_stop: float
    take_profit_r: float
    holding_bars: int
    leverage: int
    regime: str
    score: float
    trades: int
    win_rate: float


def optimize_params(df: pd.DataFrame, settings: dict[str, Any]) -> OptimizedParams | None:
    if not settings.get("enabled", True):
        return None
    work = df.copy()
    work["rsi"] = rsi(work["close"])
    work["atr"] = atr(work)
    work["ema_20"] = work["close"].ewm(span=20, adjust=False).mean()
    work["ema_50"] = work["close"].ewm(span=50, adjust=False).mean()
    work["regime"] = np.where(work["ema_20"] > work["ema_50"], "bull", "bear")
    best: OptimizedParams | None = None
    min_trades = int(settings.get("min_trades", 12))
    grid = product(
        settings.get("rsi_buy_values", [30]),
        settings.get("rsi_sell_values", [70]),
        settings.get("atr_stop_multipliers", [1.5]),
        settings.get("take_profit_r_values", [2.0]),
        settings.get("holding_bars", [16]),
        settings.get("leverages", [1]),
        ["bull", "bear", "any"],
    )
    max_evaluations = int(settings.get("max_evaluations", 600))
    for rsi_buy, rsi_sell, atr_stop, take_profit_r, holding_bars, leverage, regime in islice(grid, max_evaluations):
        returns: list[float] = []
        wins = 0
        for i in range(60, len(work) - int(holding_bars) - 1):
            row = work.iloc[i]
            if regime != "any" and row["regime"] != regime:
                continue
            side = 1 if row["rsi"] <= rsi_buy else -1 if row["rsi"] >= rsi_sell else 0
            if side == 0:
                continue
            entry = float(row["close"])
            stop_distance = max(float(row["atr"]) * float(atr_stop), entry * 0.002)
            tp_distance = stop_distance * float(take_profit_r)
            future = work.iloc[i + 1 : i + 1 + int(holding_bars)]
            pnl_r = _simulate_trade(side, entry, stop_distance, tp_distance, future)
            leveraged_return = pnl_r * (stop_distance / entry) * int(leverage)
            returns.append(leveraged_return)
            wins += int(pnl_r > 0)
        if len(returns) < min_trades:
            continue
        avg = float(np.mean(returns))
        downside = float(np.std([r for r in returns if r < 0]) or np.std(returns) or 1)
        score = avg / downside * np.sqrt(len(returns))
        candidate = OptimizedParams(
            rsi_buy=int(rsi_buy),
            rsi_sell=int(rsi_sell),
            atr_stop=float(atr_stop),
            take_profit_r=float(take_profit_r),
            holding_bars=int(holding_bars),
            leverage=int(leverage),
            regime=str(regime),
            score=round(float(score), 6),
            trades=len(returns),
            win_rate=round(wins / len(returns), 4),
        )
        if best is None or candidate.score > best.score:
            best = candidate
    return best


def _simulate_trade(side: int, entry: float, stop_distance: float, tp_distance: float, future: pd.DataFrame) -> float:
    for _, row in future.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if side == 1:
            if low <= entry - stop_distance:
                return -1.0
            if high >= entry + tp_distance:
                return tp_distance / stop_distance
        else:
            if high >= entry + stop_distance:
                return -1.0
            if low <= entry - tp_distance:
                return tp_distance / stop_distance
    exit_price = float(future.iloc[-1]["close"])
    return side * (exit_price - entry) / stop_distance

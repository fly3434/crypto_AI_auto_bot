from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CLOSED_TRADE_EVENTS = {"trade_closed", "closed_trade", "exit", "position_exit"}


def build_trade_memory(symbol: str, journal_path: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = settings or {}
    if not bool(settings.get("enabled", True)):
        return {"enabled": False, "symbol": symbol}

    max_closed_trades = int(settings.get("max_closed_trades", 20))
    max_examples = int(settings.get("max_examples", 5))
    max_mistakes = int(settings.get("max_mistakes", 5))

    path = Path(journal_path)
    if not path.exists():
        return {
            "enabled": True,
            "available": False,
            "symbol": symbol,
            "closed_trades": 0,
            "reason": "journal file does not exist yet",
        }

    trades = _load_recent_closed_trades(path, symbol, max_closed_trades)
    if not trades:
        return {
            "enabled": True,
            "available": False,
            "symbol": symbol,
            "closed_trades": 0,
            "reason": "no closed trades with realized pnl found",
        }

    wins = [trade for trade in trades if trade["pnl"] > 0]
    losses = [trade for trade in trades if trade["pnl"] < 0]
    pnl_values = [trade["pnl"] for trade in trades]
    pnl_pct_values = [trade["pnl_pct"] for trade in trades if trade.get("pnl_pct") is not None]

    return {
        "enabled": True,
        "available": True,
        "symbol": symbol,
        "closed_trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4),
        "loss_rate": round(len(losses) / len(trades), 4),
        "total_pnl": round(sum(pnl_values), 8),
        "avg_pnl": round(sum(pnl_values) / len(pnl_values), 8),
        "total_pnl_pct": _rounded_sum(pnl_pct_values),
        "avg_pnl_pct": _rounded_avg(pnl_pct_values),
        "recent_mistakes": [_mistake_summary(trade) for trade in losses[:max_mistakes]],
        "recent_examples": [_example_summary(trade) for trade in trades[:max_examples]],
    }


def _load_recent_closed_trades(path: Path, symbol: str, limit: int) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()

    for line in reversed(lines):
        if len(trades) >= limit:
            break
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        trade = _normalize_closed_trade(record)
        if not trade or trade["symbol"] != symbol:
            continue
        trades.append(trade)
    return trades


def _normalize_closed_trade(record: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(record.get("event_type", ""))
    if event_type not in CLOSED_TRADE_EVENTS and not bool(record.get("closed")):
        return None

    symbol = _first_str(record, ["symbol", "trade.symbol", "decision.symbol"])
    pnl = _first_float(record, ["net_pnl", "realized_pnl", "pnl", "profit", "trade.net_pnl", "trade.realized_pnl", "trade.pnl"])
    if not symbol or pnl is None:
        return None

    decision = record.get("decision") if isinstance(record.get("decision"), dict) else {}
    features = record.get("features") if isinstance(record.get("features"), dict) else {}
    if not features and isinstance(record.get("state"), dict):
        state_features = record["state"].get("features")
        features = state_features if isinstance(state_features, dict) else {}

    return {
        "ts": record.get("ts"),
        "symbol": symbol,
        "action": _first_str(record, ["action", "side", "decision.action", "trade.action", "trade.side"]),
        "confidence": _first_float(record, ["confidence", "decision.confidence", "trade.confidence"]),
        "pnl": pnl,
        "pnl_pct": _first_float(record, ["net_pnl_pct", "realized_pnl_pct", "pnl_pct", "return_pct", "trade.pnl_pct"]),
        "exit_reason": _first_str(record, ["exit_reason", "reason", "trade.exit_reason"]),
        "rationale": str(decision.get("rationale") or record.get("rationale") or "")[:240],
        "features": _compact_features(features),
    }


def _mistake_summary(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": trade.get("ts"),
        "action": trade.get("action"),
        "confidence": trade.get("confidence"),
        "pnl": trade.get("pnl"),
        "pnl_pct": trade.get("pnl_pct"),
        "exit_reason": trade.get("exit_reason"),
        "features": trade.get("features"),
        "lesson": "Similar setup recently lost money; reduce confidence or hold unless current evidence is stronger.",
    }


def _example_summary(trade: dict[str, Any]) -> dict[str, Any]:
    return {
        "ts": trade.get("ts"),
        "action": trade.get("action"),
        "confidence": trade.get("confidence"),
        "pnl": trade.get("pnl"),
        "pnl_pct": trade.get("pnl_pct"),
        "exit_reason": trade.get("exit_reason"),
        "features": trade.get("features"),
    }


def _compact_features(features: dict[str, Any]) -> dict[str, Any]:
    keep = [
        "rsi_14",
        "atr_pct",
        "zscore_50",
        "volume_spike",
        "ema_distance",
        "trend_regime",
        "realized_vol_50",
        "funding_pressure",
        "return_12",
    ]
    return {key: features[key] for key in keep if key in features}


def _first_str(data: dict[str, Any], paths: list[str]) -> str | None:
    for path in paths:
        value = _get_path(data, path)
        if value not in (None, ""):
            return str(value)
    return None


def _first_float(data: dict[str, Any], paths: list[str]) -> float | None:
    for path in paths:
        value = _get_path(data, path)
        if value in (None, ""):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _get_path(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _rounded_sum(values: list[float]) -> float | None:
    return round(sum(values), 8) if values else None


def _rounded_avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 8) if values else None

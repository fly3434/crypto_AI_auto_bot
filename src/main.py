from __future__ import annotations

import argparse
import dataclasses
import time
from typing import Any

from .ai_agent import OpenRouterAgent
from .config import cfg, load_config
from .exchange import BinanceFuturesClient
from .executor import TradeExecutor
from .features import build_features
from .journal import Journal
from .optimizer import optimize_params
from .position_manager import plan_position_action
from .risk import RiskManager
from .trade_memory import build_trade_memory


@dataclasses.dataclass(frozen=True)
class Runtime:
    app_config: Any
    exchange: BinanceFuturesClient
    ai: OpenRouterAgent
    risk_manager: RiskManager
    executor: TradeExecutor
    journal: Journal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once", action="store_true", help="Run a single scan cycle and exit.")
    args = parser.parse_args()

    runtime = build_runtime(args.config)
    if not bool(cfg(runtime.app_config, "mode.dry_run", True)):
        validate_live_auth(runtime.exchange, runtime.journal)

    while True:
        try:
            run_cycle(
                runtime.app_config.raw,
                runtime.exchange,
                runtime.ai,
                runtime.risk_manager,
                runtime.executor,
                runtime.journal,
            )
        finally:
            runtime.journal.upload_run_logs()
        if args.once:
            break
        time.sleep(int(cfg(runtime.app_config, "mode.loop_seconds", 300)))


def build_runtime(config_path: str = "config.yaml") -> Runtime:
    app_config = load_config(config_path)
    journal = Journal(
        cfg(app_config, "logging.journal_path", "logs/trading_journal.jsonl"),
        timezone_name=cfg(app_config, "logging.timezone", "Asia/Taipei"),
        gcs_bucket=cfg(app_config, "logging.gcs.bucket", ""),
        gcs_prefix=cfg(app_config, "logging.gcs.prefix", "trading-journal"),
        gcs_event_types=cfg(app_config, "logging.gcs.event_types", []),
    )
    exchange = BinanceFuturesClient(
        api_key=app_config.binance_api_key,
        api_secret=app_config.binance_api_secret,
        base_url=cfg(app_config, "binance.base_url", "https://fapi.binance.com"),
        recv_window_ms=cfg(app_config, "binance.recv_window_ms", 5000),
    )
    ai = OpenRouterAgent(app_config.openrouter_api_key, cfg(app_config, "openrouter", {}))
    risk_manager = RiskManager(cfg(app_config, "risk", {}), float(cfg(app_config, "mode.starting_equity_usdt", 1000)))
    executor = TradeExecutor(exchange, dry_run=bool(cfg(app_config, "mode.dry_run", True)))
    return Runtime(app_config, exchange, ai, risk_manager, executor, journal)


def run_once(config_path: str = "config.yaml") -> dict[str, Any]:
    runtime = build_runtime(config_path)
    try:
        if not bool(cfg(runtime.app_config, "mode.dry_run", True)):
            validate_live_auth(runtime.exchange, runtime.journal)
        return run_cycle(
            runtime.app_config.raw,
            runtime.exchange,
            runtime.ai,
            runtime.risk_manager,
            runtime.executor,
            runtime.journal,
        )
    finally:
        runtime.journal.upload_run_logs()


def run_cycle(
    config: dict[str, Any],
    exchange: BinanceFuturesClient,
    ai: OpenRouterAgent,
    risk_manager: RiskManager,
    executor: TradeExecutor,
    journal: Journal,
) -> dict[str, Any]:
    market_config = config.get("market", {})
    symbols = market_config.get("symbols", ["BTCUSDT"])
    timeframes = market_timeframes(market_config)
    primary_interval = str(market_config.get("primary_interval") or market_config.get("interval") or "6h")
    funding_limit = int(config.get("market", {}).get("funding_limit", 20))
    dry_run = bool(config.get("mode", {}).get("dry_run", True))
    equity = current_equity(exchange, float(config.get("mode", {}).get("starting_equity_usdt", 1000)))
    journal_path = config.get("logging", {}).get("journal_path", "logs/trading_journal.jsonl")
    trade_memory_settings = config.get("trade_memory", {})
    summary: dict[str, Any] = {"symbols": [], "orders": 0, "errors": 0}
    for symbol in symbols:
        try:
            funding = exchange.funding_rate(symbol, funding_limit)
            mark = exchange.mark_price(symbol)
            timeframe_features: dict[str, dict[str, Any]] = {}
            primary_df = None
            snapshot = None
            for timeframe in timeframes:
                df = exchange.klines(symbol, timeframe["interval"], timeframe["lookback_limit"])
                timeframe_snapshot = build_features(symbol, df, funding, mark)
                timeframe_features[timeframe["label"]] = {
                    "interval": timeframe["interval"],
                    "role": timeframe["role"],
                    "last_close": timeframe_snapshot.close,
                    "features": timeframe_snapshot.features,
                }
                if timeframe["interval"].lower() == primary_interval.lower():
                    primary_df = df
                    snapshot = timeframe_snapshot
            if primary_df is None or snapshot is None:
                first_timeframe = timeframes[0]
                primary_df = exchange.klines(symbol, first_timeframe["interval"], first_timeframe["lookback_limit"])
                snapshot = build_features(symbol, primary_df, funding, mark)
            optimized = optimize_params(primary_df, config.get("optimizer", {}))
            position = current_position_or_zero(exchange, symbol) if dry_run else current_position(exchange, symbol)
            state = {
                "symbol": symbol,
                "price": snapshot.close,
                "equity_usdt": equity,
                "current_position_amt": position,
                "monthly_target_return": config.get("mode", {}).get("target_monthly_return", 1.0),
                "features": snapshot.features,
                "primary_timeframe": primary_interval,
                "analysis_sequence": [timeframe["label"] for timeframe in timeframes],
                "timeframe_features": timeframe_features,
                "optimized_params": dataclasses.asdict(optimized) if optimized else None,
                "trade_memory": build_trade_memory(symbol, journal_path, trade_memory_settings),
                "risk_limits": config.get("risk", {}),
                "position_management": config.get("position_management", {}),
            }
            decision = ai.decide(state)
            risk = risk_manager.approve(decision, equity, snapshot.close)
            journal.write(
                "analysis",
                {
                    "symbol": symbol,
                    "state": state,
                    "decision": dataclasses.asdict(decision),
                    "risk": dataclasses.asdict(risk),
                },
            )
            if position != 0:
                position_settings = config.get("position_management", {})
                if not bool(position_settings.get("enabled", True)):
                    journal.write(
                        "position_guard",
                        {
                            "symbol": symbol,
                            "decision": dataclasses.asdict(decision),
                            "position_amt": position,
                            "reason": "Skipped new entry because an open position already exists for this symbol.",
                        },
                    )
                    summary["symbols"].append({"symbol": symbol, "action": decision.action, "status": "position_guard"})
                    continue

                plan = plan_position_action(position, decision, risk, position_settings)
                if plan.action == "hold":
                    journal.write(
                        "position_management",
                        {
                            "symbol": symbol,
                            "decision": dataclasses.asdict(decision),
                            "risk": dataclasses.asdict(risk),
                            "position_amt": position,
                            "plan": dataclasses.asdict(plan),
                        },
                    )
                    summary["symbols"].append(
                        {"symbol": symbol, "action": decision.action, "status": "position_hold", "reason": plan.reason}
                    )
                    continue

                close_result = executor.close_position(symbol, position)
                order_result: dict[str, Any] = {"position_plan": dataclasses.asdict(plan), "close": close_result}
                summary["orders"] += 1
                status = "position_closed"
                if plan.action == "close_and_reverse":
                    reverse_result = executor.execute(decision, risk, snapshot.close)
                    order_result["reverse"] = reverse_result
                    summary["orders"] += 1
                    status = "position_reversed"
                journal.write(
                    "position_management",
                    {
                        "symbol": symbol,
                        "decision": dataclasses.asdict(decision),
                        "risk": dataclasses.asdict(risk),
                        "position_amt": position,
                        "result": order_result,
                    },
                )
                summary["symbols"].append({"symbol": symbol, "action": decision.action, "status": status})
                continue

            if risk.approved:
                order_result = executor.execute(decision, risk, snapshot.close)
                journal.write("order", {"symbol": symbol, "decision": dataclasses.asdict(decision), "result": order_result})
                summary["orders"] += 1
                summary["symbols"].append({"symbol": symbol, "action": decision.action, "status": "order"})
            else:
                summary["symbols"].append({"symbol": symbol, "action": decision.action, "status": "rejected"})
        except Exception as exc:
            journal.write("error", {"symbol": symbol, "error": repr(exc)})
            summary["errors"] += 1
            summary["symbols"].append({"symbol": symbol, "status": "error", "error": repr(exc)})
    return summary


def market_timeframes(market_config: dict[str, Any]) -> list[dict[str, Any]]:
    fallback_interval = str(market_config.get("interval", "6h"))
    fallback_lookback = int(market_config.get("lookback_limit", 120))
    configured = market_config.get("timeframes")
    if not isinstance(configured, list) or not configured:
        return [
            {
                "label": fallback_interval.upper(),
                "interval": fallback_interval,
                "lookback_limit": fallback_lookback,
                "role": "primary_trade_signal",
            }
        ]

    timeframes: list[dict[str, Any]] = []
    for item in configured:
        if not isinstance(item, dict):
            continue
        interval = str(item.get("interval") or "").strip()
        if not interval:
            continue
        timeframes.append(
            {
                "label": str(item.get("label") or interval.upper()),
                "interval": interval,
                "lookback_limit": int(item.get("lookback_limit") or fallback_lookback),
                "role": str(item.get("role") or "supporting_context"),
            }
        )
    return timeframes or [
        {
            "label": fallback_interval.upper(),
            "interval": fallback_interval,
            "lookback_limit": fallback_lookback,
            "role": "primary_trade_signal",
        }
    ]


def current_equity(exchange: BinanceFuturesClient, fallback: float) -> float:
    try:
        account = exchange.account()
        return float(account.get("totalWalletBalance") or account.get("totalMarginBalance") or fallback)
    except Exception:
        return fallback


def current_position(exchange: BinanceFuturesClient, symbol: str) -> float:
    positions = exchange.position_risk(symbol)
    return sum(float(item.get("positionAmt", 0) or 0) for item in positions if item.get("symbol") == symbol)


def current_position_or_zero(exchange: BinanceFuturesClient, symbol: str) -> float:
    try:
        return current_position(exchange, symbol)
    except Exception:
        return 0.0


def validate_live_auth(exchange: BinanceFuturesClient, journal: Journal) -> None:
    try:
        account = exchange.account()
    except Exception as exc:
        journal.write("auth_check", {"ok": False, "error": repr(exc)})
        raise
    journal.write(
        "auth_check",
        {
            "ok": True,
            "can_trade": account.get("canTrade"),
            "total_wallet_balance": account.get("totalWalletBalance"),
        },
    )


if __name__ == "__main__":
    main()

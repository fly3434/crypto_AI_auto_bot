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
from .risk import RiskManager


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
    symbols = config.get("market", {}).get("symbols", ["BTCUSDT"])
    interval = config.get("market", {}).get("interval", "15m")
    lookback = int(config.get("market", {}).get("lookback_limit", 500))
    funding_limit = int(config.get("market", {}).get("funding_limit", 20))
    equity = current_equity(exchange, float(config.get("mode", {}).get("starting_equity_usdt", 1000)))
    summary: dict[str, Any] = {"symbols": [], "orders": 0, "errors": 0}
    for symbol in symbols:
        try:
            df = exchange.klines(symbol, interval, lookback)
            funding = exchange.funding_rate(symbol, funding_limit)
            mark = exchange.mark_price(symbol)
            snapshot = build_features(symbol, df, funding, mark)
            optimized = optimize_params(df, config.get("optimizer", {}))
            state = {
                "symbol": symbol,
                "price": snapshot.close,
                "equity_usdt": equity,
                "monthly_target_return": config.get("mode", {}).get("target_monthly_return", 1.0),
                "features": snapshot.features,
                "optimized_params": dataclasses.asdict(optimized) if optimized else None,
                "risk_limits": config.get("risk", {}),
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
            if risk.approved:
                position = current_position(exchange, symbol)
                if position != 0:
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


def current_equity(exchange: BinanceFuturesClient, fallback: float) -> float:
    try:
        account = exchange.account()
        return float(account.get("totalWalletBalance") or account.get("totalMarginBalance") or fallback)
    except Exception:
        return fallback


def current_position(exchange: BinanceFuturesClient, symbol: str) -> float:
    positions = exchange.position_risk(symbol)
    return sum(float(item.get("positionAmt", 0) or 0) for item in positions if item.get("symbol") == symbol)


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

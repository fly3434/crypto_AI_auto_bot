from __future__ import annotations

from typing import Any

from .ai_agent import TradeDecision
from .exchange import BinanceFuturesClient
from .risk import RiskResult


class TradeExecutor:
    def __init__(self, exchange: BinanceFuturesClient, dry_run: bool = True) -> None:
        self.exchange = exchange
        self.dry_run = dry_run

    def execute(self, decision: TradeDecision, risk: RiskResult, price: float) -> dict[str, Any]:
        side = "BUY" if decision.action == "BUY" else "SELL"
        close_side = "SELL" if side == "BUY" else "BUY"
        stop_price = price * (1 - decision.stop_loss_pct) if side == "BUY" else price * (1 + decision.stop_loss_pct)
        take_profit_price = price * (1 + decision.take_profit_pct) if side == "BUY" else price * (1 - decision.take_profit_pct)
        rules = self.exchange.symbol_rules(decision.symbol)
        quantity = rules.quantity(risk.quantity)
        stop_price_adjusted = rules.price(stop_price)
        take_profit_price_adjusted = rules.price(take_profit_price)
        precision = {
            "raw_quantity": risk.quantity,
            "quantity": quantity,
            "raw_stop_price": stop_price,
            "stop_price": stop_price_adjusted,
            "raw_take_profit_price": take_profit_price,
            "take_profit_price": take_profit_price_adjusted,
        }
        if self.dry_run:
            return {
                "dry_run": True,
                "precision": precision,
                "entry": {"symbol": decision.symbol, "side": side, "type": "MARKET", "quantity": quantity},
                "cancel_stale_algo_orders": {"symbol": decision.symbol},
                "stop": _algo_order_params(decision.symbol, close_side, "STOP_MARKET", stop_price_adjusted, quantity),
                "take_profit": _algo_order_params(
                    decision.symbol, close_side, "TAKE_PROFIT_MARKET", take_profit_price_adjusted, quantity
                ),
            }

        self.exchange.change_leverage(decision.symbol, risk.leverage)
        cancel_stale_algo_orders = self.exchange.cancel_all_algo_open_orders(decision.symbol)
        entry = self.exchange.new_order(symbol=decision.symbol, side=side, type="MARKET", quantity=quantity)
        stop = self.exchange.new_algo_order(
            **_algo_order_params(decision.symbol, close_side, "STOP_MARKET", stop_price_adjusted, quantity)
        )
        take_profit = self.exchange.new_algo_order(
            **_algo_order_params(decision.symbol, close_side, "TAKE_PROFIT_MARKET", take_profit_price_adjusted, quantity)
        )
        return {
            "dry_run": False,
            "precision": precision,
            "cancel_stale_algo_orders": cancel_stale_algo_orders,
            "entry": entry,
            "stop": stop,
            "take_profit": take_profit,
        }


def _algo_order_params(symbol: str, side: str, order_type: str, trigger_price: str, quantity: str) -> dict[str, Any]:
    return {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "quantity": quantity,
        "triggerPrice": trigger_price,
        "reduceOnly": "true",
        "workingType": "MARK_PRICE",
    }

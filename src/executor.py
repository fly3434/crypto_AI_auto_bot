from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation
from typing import Any

from .ai_agent import TradeDecision
from .exchange import BinanceFuturesClient
from .risk import RiskResult


class UnprotectedPositionError(RuntimeError):
    def __init__(self, symbol: str, result: dict[str, Any]) -> None:
        self.symbol = symbol
        self.result = result
        super().__init__(f"{symbol} entry may be unprotected after stop/take-profit failure.")


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
                "stop": _algo_order_params(decision.symbol, close_side, "STOP_MARKET", stop_price_adjusted),
                "take_profit": _algo_order_params(
                    decision.symbol, close_side, "TAKE_PROFIT_MARKET", take_profit_price_adjusted
                ),
            }

        self.exchange.change_leverage(decision.symbol, risk.leverage)
        cancel_stale_algo_orders = self.exchange.cancel_all_algo_open_orders(decision.symbol)
        entry = self.exchange.new_order(symbol=decision.symbol, side=side, type="MARKET", quantity=quantity)
        result: dict[str, Any] = {
            "dry_run": False,
            "precision": precision,
            "cancel_stale_algo_orders": cancel_stale_algo_orders,
            "entry": entry,
        }
        protective_orders = {
            "stop": _algo_order_params(decision.symbol, close_side, "STOP_MARKET", stop_price_adjusted),
            "take_profit": _algo_order_params(
                decision.symbol, close_side, "TAKE_PROFIT_MARKET", take_profit_price_adjusted
            ),
        }
        try:
            for label, params in protective_orders.items():
                result[label] = self.exchange.new_algo_order(**params)
        except Exception as exc:
            result["protective_order_error"] = repr(exc)
            result["fail_safe"] = self._fail_safe_close_position(decision.symbol, side)
            if not result["fail_safe"].get("closed"):
                raise UnprotectedPositionError(decision.symbol, result) from exc
            return result
        try:
            result["protective_order_check"] = self._check_protective_orders(decision.symbol, protective_orders)
            if not result["protective_order_check"].get("ok"):
                result["protective_order_warning"] = (
                    "Protective orders were accepted by Binance but were not confirmed in open algo orders."
                )
        except Exception as exc:
            result["protective_order_warning"] = (
                "Protective orders were accepted by Binance but open algo order confirmation failed."
            )
            result["protective_order_check_error"] = repr(exc)
        return result

    def close_position(self, symbol: str, position_amt: float) -> dict[str, Any]:
        rules = self.exchange.symbol_rules(symbol)
        if self.dry_run:
            side = "SELL" if position_amt > 0 else "BUY"
            quantity = rules.quantity(abs(position_amt))
            close_order = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity, "reduceOnly": "true"}
            return {
                "dry_run": True,
                "cancel_stale_algo_orders": {"symbol": symbol},
                "close": close_order,
            }

        latest_position_amt = self._current_position(symbol)
        if latest_position_amt == 0:
            return {
                "dry_run": False,
                "position_before": position_amt,
                "latest_position_amt": latest_position_amt,
                "status": "already_flat",
            }
        side = "SELL" if latest_position_amt > 0 else "BUY"
        quantity = rules.quantity(abs(latest_position_amt))
        close_order = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": quantity, "reduceOnly": "true"}
        cancel_stale_algo_orders = self.exchange.cancel_all_algo_open_orders(symbol)
        close = self.exchange.new_order(**close_order)
        return {
            "dry_run": False,
            "position_before": position_amt,
            "latest_position_amt": latest_position_amt,
            "cancel_stale_algo_orders": cancel_stale_algo_orders,
            "close": close,
        }

    def _check_protective_orders(
        self, symbol: str, expected_orders: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        attempts = []
        open_orders: list[dict[str, Any]] = []
        missing = list(expected_orders)
        for delay_seconds in (0.0, 0.5, 1.0):
            if delay_seconds:
                time.sleep(delay_seconds)
            open_orders = self.exchange.open_algo_orders(symbol)
            missing = _missing_protective_orders(open_orders, expected_orders)
            attempts.append({"missing": missing, "open_algo_order_count": len(open_orders)})
            if not missing:
                break
        return {
            "ok": not missing,
            "missing": missing,
            "attempts": attempts,
            "open_algo_order_count": len(open_orders),
        }

    def _fail_safe_close_position(self, symbol: str, entry_side: str) -> dict[str, Any]:
        try:
            latest_position_amt = self._current_position(symbol)
            if latest_position_amt == 0:
                cancel_stale_algo_orders = self.exchange.cancel_all_algo_open_orders(symbol)
                return {
                    "closed": True,
                    "status": "already_flat",
                    "latest_position_amt": latest_position_amt,
                    "cancel_stale_algo_orders": cancel_stale_algo_orders,
                }
            close_side = "SELL" if latest_position_amt > 0 else "BUY"
            if close_side == entry_side:
                return {
                    "closed": False,
                    "status": "position_side_mismatch",
                    "latest_position_amt": latest_position_amt,
                    "entry_side": entry_side,
                    "close_side": close_side,
                }
            rules = self.exchange.symbol_rules(symbol)
            quantity = rules.quantity(abs(latest_position_amt))
            close_order = {"symbol": symbol, "side": close_side, "type": "MARKET", "quantity": quantity, "reduceOnly": "true"}
            close = self.exchange.new_order(**close_order)
            cancel_stale_algo_orders = self.exchange.cancel_all_algo_open_orders(symbol)
            return {
                "closed": True,
                "latest_position_amt": latest_position_amt,
                "close": close,
                "cancel_stale_algo_orders": cancel_stale_algo_orders,
            }
        except Exception as exc:
            return {"closed": False, "error": repr(exc)}

    def _current_position(self, symbol: str) -> float:
        positions = self.exchange.position_risk(symbol)
        return sum(float(item.get("positionAmt", 0) or 0) for item in positions if item.get("symbol") == symbol)


def _algo_order_params(symbol: str, side: str, order_type: str, trigger_price: str) -> dict[str, Any]:
    return {
        "algoType": "CONDITIONAL",
        "symbol": symbol,
        "side": side,
        "type": order_type,
        "triggerPrice": trigger_price,
        "closePosition": "true",
        "workingType": "MARK_PRICE",
    }


def _missing_protective_orders(
    open_orders: list[dict[str, Any]], expected_orders: dict[str, dict[str, Any]]
) -> list[str]:
    missing = []
    for label, expected in expected_orders.items():
        if not any(_matches_algo_order(order, expected) for order in open_orders):
            missing.append(label)
    return missing


def _matches_algo_order(order: dict[str, Any], expected: dict[str, Any]) -> bool:
    order_type = order.get("type") or order.get("orderType")
    trigger_price = order.get("triggerPrice") or order.get("stopPrice")
    return (
        order.get("symbol") == expected["symbol"]
        and order.get("side") == expected["side"]
        and order_type == expected["type"]
        and _same_decimal(trigger_price, expected["triggerPrice"])
        and str(order.get("closePosition")).lower() == "true"
    )


def _same_decimal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return str(left) == str(right)

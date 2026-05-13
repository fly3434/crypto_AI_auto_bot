from decimal import Decimal

from src.ai_agent import TradeDecision
from src.exchange import SymbolRules
from src.executor import TradeExecutor
from src.risk import RiskResult


class FakeExchange:
    def __init__(self):
        self.orders = []
        self.algo_orders = []
        self.canceled_algo_symbols = []

    def symbol_rules(self, symbol):
        return SymbolRules(
            symbol=symbol,
            quantity_step=Decimal("0.001"),
            min_quantity=Decimal("0.001"),
            price_tick=Decimal("0.01"),
            min_notional=Decimal("5"),
        )

    def change_leverage(self, symbol, leverage):
        return {"symbol": symbol, "leverage": leverage}

    def new_order(self, **params):
        self.orders.append(params)
        return params

    def new_algo_order(self, **params):
        self.algo_orders.append(params)
        return params

    def cancel_all_algo_open_orders(self, symbol):
        self.canceled_algo_symbols.append(symbol)
        return {"symbol": symbol, "code": 200}


def test_executor_adjusts_quantity_and_prices_to_symbol_rules():
    decision = TradeDecision(
        action="BUY",
        confidence=0.75,
        symbol="ETHUSDT",
        leverage=5,
        risk_pct=0.01,
        stop_loss_pct=0.01,
        take_profit_pct=0.022,
        max_holding_minutes=240,
        rationale="test",
        high_risk_features_used=[],
    )
    risk = RiskResult(True, "Approved.", quantity=2.160807, leverage=5)

    result = TradeExecutor(FakeExchange(), dry_run=True).execute(decision, risk, price=2313.95)

    assert result["entry"]["quantity"] == "2.16"
    assert result["stop"]["triggerPrice"] == "2290.81"
    assert result["take_profit"]["triggerPrice"] == "2364.85"
    assert result["stop"]["quantity"] == "2.16"
    assert result["stop"]["reduceOnly"] == "true"
    assert "closePosition" not in result["stop"]
    assert result["precision"]["raw_quantity"] == 2.160807


def test_executor_uses_algo_endpoint_for_stop_and_take_profit_live_orders():
    exchange = FakeExchange()
    decision = TradeDecision(
        action="SELL",
        confidence=0.75,
        symbol="ETHUSDT",
        leverage=5,
        risk_pct=0.01,
        stop_loss_pct=0.01,
        take_profit_pct=0.022,
        max_holding_minutes=240,
        rationale="test",
        high_risk_features_used=[],
    )
    risk = RiskResult(True, "Approved.", quantity=2.160807, leverage=5)

    TradeExecutor(exchange, dry_run=False).execute(decision, risk, price=2313.95)

    assert exchange.orders == [{"symbol": "ETHUSDT", "side": "SELL", "type": "MARKET", "quantity": "2.16"}]
    assert exchange.canceled_algo_symbols == ["ETHUSDT"]
    assert [order["type"] for order in exchange.algo_orders] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert all(order["algoType"] == "CONDITIONAL" for order in exchange.algo_orders)
    assert all("triggerPrice" in order for order in exchange.algo_orders)
    assert all("stopPrice" not in order for order in exchange.algo_orders)
    assert all(order["quantity"] == "2.16" for order in exchange.algo_orders)
    assert all(order["reduceOnly"] == "true" for order in exchange.algo_orders)
    assert all("closePosition" not in order for order in exchange.algo_orders)

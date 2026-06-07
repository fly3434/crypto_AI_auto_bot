from decimal import Decimal

from src.ai_agent import TradeDecision
from src.exchange import SymbolRules
from src.executor import PositionNotFlatError, TradeExecutor, UnprotectedPositionError, _missing_protective_orders
from src.risk import RiskResult


class FakeExchange:
    def __init__(self):
        self.orders = []
        self.algo_orders = []
        self.canceled_algo_symbols = []
        self.positions = []

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

    def open_algo_orders(self, symbol):
        return [order for order in self.algo_orders if order["symbol"] == symbol]

    def cancel_all_algo_open_orders(self, symbol):
        self.canceled_algo_symbols.append(symbol)
        return {"symbol": symbol, "code": 200}

    def position_risk(self, symbol):
        return self.positions or [{"symbol": symbol, "positionAmt": "0"}]


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
    assert result["stop"]["closePosition"] == "true"
    assert "quantity" not in result["stop"]
    assert "reduceOnly" not in result["stop"]
    assert result["precision"]["raw_quantity"] == 2.160807


def test_executor_uses_close_position_algo_endpoint_for_stop_and_take_profit_live_orders():
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

    result = TradeExecutor(exchange, dry_run=False, position_visibility_delays=(0.0,)).execute(
        decision, risk, price=2313.95
    )

    assert exchange.orders == [{"symbol": "ETHUSDT", "side": "SELL", "type": "MARKET", "quantity": "2.16"}]
    assert exchange.canceled_algo_symbols == ["ETHUSDT"]
    assert result["entry_position_check"]["visible"] is False
    assert [order["type"] for order in exchange.algo_orders] == ["STOP_MARKET", "TAKE_PROFIT_MARKET"]
    assert all(order["algoType"] == "CONDITIONAL" for order in exchange.algo_orders)
    assert all("triggerPrice" in order for order in exchange.algo_orders)
    assert all("stopPrice" not in order for order in exchange.algo_orders)
    assert result["pre_entry_position_amt"] == 0.0
    assert all(order["closePosition"] == "true" for order in exchange.algo_orders)
    assert all("quantity" not in order for order in exchange.algo_orders)
    assert all("reduceOnly" not in order for order in exchange.algo_orders)
    assert exchange.open_algo_orders("ETHUSDT") == exchange.algo_orders


def test_executor_rejects_live_entry_when_position_is_not_flat():
    exchange = FakeExchange()
    exchange.positions = [{"symbol": "ETHUSDT", "positionAmt": "-1.5"}]
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

    try:
        TradeExecutor(exchange, dry_run=False).execute(decision, risk, price=2313.95)
    except PositionNotFlatError as exc:
        assert exc.symbol == "ETHUSDT"
        assert exc.position_amt == -1.5
    else:
        raise AssertionError("expected PositionNotFlatError")

    assert exchange.orders == []
    assert exchange.algo_orders == []
    assert exchange.canceled_algo_symbols == []


def test_protective_order_confirmation_matches_boolean_close_position_from_binance():
    expected = {
        "stop": {
            "algoType": "CONDITIONAL",
            "symbol": "ETHUSDT",
            "side": "SELL",
            "type": "STOP_MARKET",
            "triggerPrice": "2290.81",
            "closePosition": "true",
            "workingType": "MARK_PRICE",
        }
    }
    open_orders = [
        {
            "symbol": "ETHUSDT",
            "side": "SELL",
            "orderType": "STOP_MARKET",
            "triggerPrice": "2290.810",
            "closePosition": True,
        }
    ]

    assert _missing_protective_orders(open_orders, expected) == []


def test_executor_closes_existing_long_with_reduce_only_market_order():
    exchange = FakeExchange()
    exchange.positions = [{"symbol": "ETHUSDT", "positionAmt": "2.160807"}]

    result = TradeExecutor(exchange, dry_run=False).close_position("ETHUSDT", 2.160807)

    assert result["close"] == {
        "symbol": "ETHUSDT",
        "side": "SELL",
        "type": "MARKET",
        "quantity": "2.16",
        "reduceOnly": "true",
    }
    assert exchange.orders == [result["close"]]
    assert exchange.canceled_algo_symbols == ["ETHUSDT"]


def test_executor_closes_existing_short_with_buy_reduce_only_market_order_dry_run():
    result = TradeExecutor(FakeExchange(), dry_run=True).close_position("ETHUSDT", -2.160807)

    assert result["close"] == {
        "symbol": "ETHUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "2.16",
        "reduceOnly": "true",
    }


def test_executor_refreshes_position_before_live_close():
    exchange = FakeExchange()
    exchange.positions = [{"symbol": "ETHUSDT", "positionAmt": "0"}]

    result = TradeExecutor(exchange, dry_run=False).close_position("ETHUSDT", 2.160807)

    assert result["status"] == "already_flat"
    assert exchange.orders == []
    assert exchange.canceled_algo_symbols == []


def test_executor_does_not_close_entry_when_protective_orders_are_accepted_but_not_confirmed():
    class MissingOpenAlgoExchange(FakeExchange):
        def new_order(self, **params):
            self.orders.append(params)
            if params.get("reduceOnly") != "true":
                self.positions = [{"symbol": params["symbol"], "positionAmt": "-2.16"}]
            else:
                self.positions = [{"symbol": params["symbol"], "positionAmt": "0"}]
            return params

        def open_algo_orders(self, symbol):
            return []

    exchange = MissingOpenAlgoExchange()
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

    result = TradeExecutor(exchange, dry_run=False).execute(decision, risk, price=2313.95)

    assert result["protective_order_check"]["ok"] is False
    assert result["protective_order_check"]["missing"] == ["stop", "take_profit"]
    assert "protective_order_warning" in result
    assert "fail_safe" not in result
    assert exchange.orders == [{"symbol": "ETHUSDT", "side": "SELL", "type": "MARKET", "quantity": "2.16"}]


def test_executor_closes_entry_when_protective_order_creation_fails():
    class FailingAlgoExchange(FakeExchange):
        def new_order(self, **params):
            self.orders.append(params)
            if params.get("reduceOnly") != "true":
                self.positions = [{"symbol": params["symbol"], "positionAmt": "-2.16"}]
            else:
                self.positions = [{"symbol": params["symbol"], "positionAmt": "0"}]
            return params

        def new_algo_order(self, **params):
            if params["type"] == "TAKE_PROFIT_MARKET":
                raise RuntimeError("take profit rejected")
            return super().new_algo_order(**params)

    exchange = FailingAlgoExchange()
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

    result = TradeExecutor(exchange, dry_run=False, position_visibility_delays=(0.0,)).execute(
        decision, risk, price=2313.95
    )

    assert result["protective_order_error"] == "RuntimeError('take profit rejected')"
    assert result["fail_safe"]["closed"] is True
    assert exchange.orders[-1] == {
        "symbol": "ETHUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "2.16",
        "reduceOnly": "true",
    }


def test_fail_safe_waits_for_delayed_position_before_closing_unprotected_entry():
    class DelayedPositionExchange(FakeExchange):
        def __init__(self):
            super().__init__()
            self.position_reads = 0

        def position_risk(self, symbol):
            self.position_reads += 1
            if self.position_reads < 3:
                return [{"symbol": symbol, "positionAmt": "0"}]
            return [{"symbol": symbol, "positionAmt": "-2.16"}]

        def new_algo_order(self, **params):
            raise RuntimeError("stop rejected")

        def new_order(self, **params):
            self.orders.append(params)
            if params.get("reduceOnly") == "true":
                self.positions = [{"symbol": params["symbol"], "positionAmt": "0"}]
            return params

    exchange = DelayedPositionExchange()
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

    result = TradeExecutor(
        exchange,
        dry_run=False,
        position_visibility_delays=(0.0,),
        fail_safe_position_delays=(0.0, 0.0, 0.0),
    ).execute(decision, risk, price=2313.95)

    assert result["fail_safe"]["closed"] is True
    assert result["fail_safe"]["position_check"]["attempts"] == [
        {"delay_seconds": 0.0, "position_amt": 0.0},
        {"delay_seconds": 0.0, "position_amt": -2.16},
    ]
    assert exchange.orders[-1] == {
        "symbol": "ETHUSDT",
        "side": "BUY",
        "type": "MARKET",
        "quantity": "2.16",
        "reduceOnly": "true",
    }


def test_fail_safe_raises_when_unprotected_entry_position_never_becomes_visible():
    class InvisiblePositionExchange(FakeExchange):
        def new_algo_order(self, **params):
            raise RuntimeError("stop rejected")

    exchange = InvisiblePositionExchange()
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

    try:
        TradeExecutor(
            exchange,
            dry_run=False,
            position_visibility_delays=(0.0,),
            fail_safe_position_delays=(0.0, 0.0),
        ).execute(decision, risk, price=2313.95)
    except UnprotectedPositionError as exc:
        assert exc.result["fail_safe"]["closed"] is False
        assert exc.result["fail_safe"]["status"] == "position_not_visible_after_entry"
    else:
        raise AssertionError("expected UnprotectedPositionError")


def test_executor_does_not_close_entry_when_protective_order_confirmation_errors():
    class ConfirmationErrorExchange(FakeExchange):
        def new_order(self, **params):
            self.orders.append(params)
            if params.get("reduceOnly") != "true":
                self.positions = [{"symbol": params["symbol"], "positionAmt": "-2.16"}]
            return params

        def open_algo_orders(self, symbol):
            raise RuntimeError("confirmation unavailable")

    exchange = ConfirmationErrorExchange()
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

    result = TradeExecutor(exchange, dry_run=False).execute(decision, risk, price=2313.95)

    assert result["protective_order_check_error"] == "RuntimeError('confirmation unavailable')"
    assert "protective_order_warning" in result
    assert "fail_safe" not in result
    assert exchange.orders == [{"symbol": "ETHUSDT", "side": "SELL", "type": "MARKET", "quantity": "2.16"}]

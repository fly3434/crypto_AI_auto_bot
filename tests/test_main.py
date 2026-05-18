from src.main import current_position, current_position_or_zero


class FakeExchange:
    def position_risk(self, symbol):
        return [
            {"symbol": symbol, "positionAmt": "1.5"},
            {"symbol": "OTHERUSDT", "positionAmt": "99"},
        ]


def test_current_position_sums_matching_symbol_positions():
    assert current_position(FakeExchange(), "ETHUSDT") == 1.5


class FailingExchange:
    def position_risk(self, symbol):
        raise RuntimeError("missing credentials")


def test_current_position_or_zero_falls_back_when_position_endpoint_fails():
    assert current_position_or_zero(FailingExchange(), "ETHUSDT") == 0.0

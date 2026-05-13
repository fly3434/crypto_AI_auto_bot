from src.main import current_position


class FakeExchange:
    def position_risk(self, symbol):
        return [
            {"symbol": symbol, "positionAmt": "1.5"},
            {"symbol": "OTHERUSDT", "positionAmt": "99"},
        ]


def test_current_position_sums_matching_symbol_positions():
    assert current_position(FakeExchange(), "ETHUSDT") == 1.5


from src.main import current_position, current_position_or_zero, market_timeframes


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


def test_market_timeframes_uses_configured_multi_timeframe_order():
    config = {
        "interval": "6h",
        "lookback_limit": 120,
        "timeframes": [
            {"label": "1D", "interval": "1d", "lookback_limit": 60, "role": "macro"},
            {"label": "6H", "interval": "6h", "lookback_limit": 120, "role": "signal"},
            {"label": "2H", "interval": "2h", "lookback_limit": 180, "role": "entry"},
        ],
    }

    assert market_timeframes(config) == [
        {"label": "1D", "interval": "1d", "lookback_limit": 60, "role": "macro"},
        {"label": "6H", "interval": "6h", "lookback_limit": 120, "role": "signal"},
        {"label": "2H", "interval": "2h", "lookback_limit": 180, "role": "entry"},
    ]


def test_market_timeframes_keeps_single_interval_fallback():
    assert market_timeframes({"interval": "15m", "lookback_limit": 500}) == [
        {"label": "15M", "interval": "15m", "lookback_limit": 500, "role": "primary_trade_signal"}
    ]

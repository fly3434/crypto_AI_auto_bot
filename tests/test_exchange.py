from decimal import Decimal

from src.exchange import BinanceFuturesClient


class FakeClient(BinanceFuturesClient):
    def __init__(self):
        pass

    def exchange_info(self, symbol=None):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
                        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.0001", "minQty": "0.0001"},
                    ],
                },
                {
                    "symbol": "SOLUSDT",
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.0100"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
                        {"filterType": "MARKET_LOT_SIZE", "stepSize": "0.01", "minQty": "0.01"},
                        {"filterType": "MIN_NOTIONAL", "notional": "5"},
                    ],
                },
            ]
        }


def test_symbol_rules_selects_exact_symbol_not_first_exchange_info_result():
    client = FakeClient()
    client._symbol_rules_cache = {}

    rules = client.symbol_rules("SOLUSDT")

    assert rules.symbol == "SOLUSDT"
    assert rules.quantity_step == Decimal("0.01")
    assert rules.price_tick == Decimal("0.0100")
    assert rules.quantity(53.475936) == "53.47"
    assert rules.price(92.565) == "92.56"


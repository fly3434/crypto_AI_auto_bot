from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests


class BinanceAPIError(RuntimeError):
    def __init__(self, method: str, url: str, status_code: int, body: str) -> None:
        self.method = method
        self.url = url
        self.status_code = status_code
        self.body = body
        super().__init__(f"Binance API {method} {url} failed with HTTP {status_code}: {body}")


@dataclass(frozen=True)
class SymbolRules:
    symbol: str
    quantity_step: Decimal
    min_quantity: Decimal
    price_tick: Decimal
    min_notional: Decimal

    def quantity(self, value: float) -> str:
        adjusted = _floor_to_step(Decimal(str(value)), self.quantity_step)
        if adjusted < self.min_quantity:
            raise ValueError(f"{self.symbol} quantity {adjusted} is below min quantity {self.min_quantity}.")
        return _decimal_to_plain(adjusted)

    def price(self, value: float) -> str:
        adjusted = _floor_to_step(Decimal(str(value)), self.price_tick)
        return _decimal_to_plain(adjusted)


class BinanceFuturesClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://fapi.binance.com",
        recv_window_ms: int = 5000,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.base_url = base_url.rstrip("/")
        self.recv_window_ms = recv_window_ms
        self.session = requests.Session()
        self._symbol_rules_cache: dict[str, SymbolRules] = {}
        if api_key:
            self.session.headers.update({"X-MBX-APIKEY": api_key})

    def _request(self, method: str, path: str, params: dict[str, Any] | None = None, signed: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if signed:
            if not self.api_key or not self.api_secret:
                raise RuntimeError("Binance API key and secret are required for signed endpoints.")
            params["timestamp"] = int(time.time() * 1000)
            params["recvWindow"] = self.recv_window_ms
            query = urlencode(params, doseq=True)
            params["signature"] = hmac.new(self.api_secret, query.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}{path}"
        response = self.session.request(method, url, params=params, timeout=20)
        if response.status_code >= 400:
            raise BinanceAPIError(method, url, response.status_code, response.text)
        return response.json()

    def klines(self, symbol: str, interval: str, limit: int = 500) -> pd.DataFrame:
        rows = self._request("GET", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ]
        df = pd.DataFrame(rows, columns=columns)
        numeric = ["open", "high", "low", "close", "volume", "quote_volume", "taker_buy_base", "taker_buy_quote"]
        df[numeric] = df[numeric].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
        return df

    def funding_rate(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        return self._request("GET", "/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})

    def mark_price(self, symbol: str) -> dict[str, Any]:
        return self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})

    def exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol} if symbol else None
        return self._request("GET", "/fapi/v1/exchangeInfo", params)

    def symbol_rules(self, symbol: str) -> SymbolRules:
        if symbol in self._symbol_rules_cache:
            return self._symbol_rules_cache[symbol]
        info = self.exchange_info(symbol)
        symbols = info.get("symbols", [])
        symbol_info = next((item for item in symbols if item.get("symbol") == symbol), None)
        if not symbol_info:
            raise ValueError(f"No exchangeInfo found for {symbol}.")
        filters = {item["filterType"]: item for item in symbol_info.get("filters", [])}
        lot_size = filters.get("MARKET_LOT_SIZE") or filters.get("LOT_SIZE")
        price_filter = filters.get("PRICE_FILTER")
        min_notional_filter = filters.get("MIN_NOTIONAL", {})
        if not lot_size or not price_filter:
            raise ValueError(f"Missing LOT_SIZE or PRICE_FILTER for {symbol}.")
        rules = SymbolRules(
            symbol=symbol,
            quantity_step=Decimal(str(lot_size["stepSize"])),
            min_quantity=Decimal(str(lot_size["minQty"])),
            price_tick=Decimal(str(price_filter["tickSize"])),
            min_notional=Decimal(str(min_notional_filter.get("notional", "0"))),
        )
        self._symbol_rules_cache[symbol] = rules
        return rules

    def account(self) -> dict[str, Any]:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def position_risk(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"symbol": symbol} if symbol else None
        result = self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        return result if isinstance(result, list) else [result]

    def change_leverage(self, symbol: str, leverage: int) -> dict[str, Any]:
        return self._request("POST", "/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage}, signed=True)

    def new_order(self, **params: Any) -> dict[str, Any]:
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def new_algo_order(self, **params: Any) -> dict[str, Any]:
        return self._request("POST", "/fapi/v1/algoOrder", params, signed=True)

    def cancel_all_algo_open_orders(self, symbol: str) -> dict[str, Any]:
        return self._request("DELETE", "/fapi/v1/algoOpenOrders", {"symbol": symbol}, signed=True)


def _floor_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def _decimal_to_plain(value: Decimal) -> str:
    return format(value.normalize(), "f")

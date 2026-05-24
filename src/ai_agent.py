from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

import requests


Action = Literal["BUY", "SELL", "HOLD"]


@dataclass(frozen=True)
class TradeDecision:
    action: Action
    confidence: float
    symbol: str
    leverage: int
    risk_pct: float
    stop_loss_pct: float
    take_profit_pct: float
    max_holding_minutes: int
    rationale: str
    high_risk_features_used: list[str]


class OpenRouterAgent:
    def __init__(self, api_key: str, settings: dict[str, Any]) -> None:
        self.api_key = api_key
        self.settings = settings

    def decide(self, state: dict[str, Any]) -> TradeDecision:
        if not self.api_key:
            return TradeDecision(
                action="HOLD",
                confidence=0.0,
                symbol=state["symbol"],
                leverage=1,
                risk_pct=0.0,
                stop_loss_pct=0.0,
                take_profit_pct=0.0,
                max_holding_minutes=0,
                rationale="OPENROUTER_API_KEY is missing; fallback to HOLD.",
                high_risk_features_used=[],
            )
        payload = {
            "model": self.settings.get("model", "openai/gpt-4o-mini"),
            "temperature": self.settings.get("temperature", 0.1),
            "max_tokens": self.settings.get("max_tokens", 900),
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(state, ensure_ascii=False)},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.get("site_url", "http://localhost"),
            "X-Title": self.settings.get("app_title", "Crypto AI Auto Bot"),
        }
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, timeout=45)
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        return parse_decision(content, fallback_symbol=state["symbol"])


SYSTEM_PROMPT = """You are an aggressive but risk-aware crypto futures trading strategist.
Goal: attempt to double account equity in one month, but never bypass the risk rules provided.
Use market features, optimized parameters, trade_memory, news_context, and high-risk features only when they improve expected value.
When trade_memory is available, treat it as recent performance feedback for this symbol.
Prefer setups with positive recent expectancy, and reduce confidence or HOLD when similar recent trades lost money.
Do not blindly copy past trades; current market features and risk rules have priority.
When news_context is available, use it only as compact market narrative and risk-event context.
Do not trade from news alone, do not overreact to stale or low-importance news, and never bypass risk rules because of news.
Use the multi-timeframe data in timeframe_features in this exact order:
1. First review 1D only for macro direction, regime, and risk environment.
2. Then review 6H as the primary timeframe for deciding whether a BUY, SELL, or HOLD signal exists.
3. Finally review 2H only to confirm entry quality, stop-loss distance, take-profit distance, and whether price is too extended to chase.
The 6H primary timeframe should carry the most decision weight. Do not require all timeframes to agree.
Treat 1D and 2H as risk sizing and timing inputs, not automatic vetoes.
If 6H has a directional edge and risk can be defined with a reasonable stop, prefer BUY or SELL over HOLD.
When 1D moderately conflicts with 6H, usually reduce confidence, leverage, risk_pct, or holding time instead of defaulting to HOLD.
When 2H entry quality is imperfect but not extreme, still take the 6H signal with smaller size or a tighter plan instead of defaulting to HOLD.
Use HOLD only when there is no actionable directional edge, risk cannot be bounded, entry is clearly overextended, or news/risk events materially damage expected value.
For range regimes, actively consider mean-reversion BUY/SELL setups near range extremes instead of treating range as neutral by default.
If a setup is marginal but tradable, express that with confidence near the risk threshold and conservative sizing rather than action HOLD.
The top-level features field is the primary timeframe snapshot, usually 6H.
Return only valid JSON with these keys:
action: BUY, SELL, or HOLD.
confidence: number from 0 to 1.
symbol: trading symbol.
leverage: integer.
risk_pct: account equity risked if stop loss hits.
stop_loss_pct: distance from entry as decimal, e.g. 0.01.
take_profit_pct: distance from entry as decimal.
max_holding_minutes: integer.
rationale: concise reason for the trade or hold.
high_risk_features_used: array of feature names.
"""


def parse_decision(content: str, fallback_symbol: str) -> TradeDecision:
    data = _load_decision_object(content)
    action = str(data.get("action", "HOLD")).upper()
    if action not in {"BUY", "SELL", "HOLD"}:
        action = "HOLD"
    high_risk_features = data.get("high_risk_features_used", [])
    if not isinstance(high_risk_features, list):
        high_risk_features = []
    return TradeDecision(
        action=action,  # type: ignore[arg-type]
        confidence=_clamp(float(data.get("confidence", 0)), 0, 1),
        symbol=str(data.get("symbol") or fallback_symbol),
        leverage=max(1, int(data.get("leverage", 1))),
        risk_pct=_clamp(float(data.get("risk_pct", 0)), 0, 1),
        stop_loss_pct=max(0.0, float(data.get("stop_loss_pct", 0))),
        take_profit_pct=max(0.0, float(data.get("take_profit_pct", 0))),
        max_holding_minutes=max(0, int(data.get("max_holding_minutes", 0))),
        rationale=str(data.get("rationale", ""))[:1200],
        high_risk_features_used=[str(item) for item in high_risk_features[:12]],
    )


def _load_decision_object(content: str) -> dict[str, Any]:
    data: Any = json.loads(content)
    if isinstance(data, str):
        data = json.loads(data)
    if not isinstance(data, dict):
        raise ValueError(f"AI decision response must be a JSON object, got {type(data).__name__}.")
    return data


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)

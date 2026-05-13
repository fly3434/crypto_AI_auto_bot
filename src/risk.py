from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .ai_agent import TradeDecision


@dataclass(frozen=True)
class RiskResult:
    approved: bool
    reason: str
    quantity: float = 0.0
    leverage: int = 1


class RiskManager:
    def __init__(self, settings: dict[str, Any], starting_equity: float) -> None:
        self.settings = settings
        self.starting_equity = starting_equity
        self.stop_cooldowns: dict[str, datetime] = {}

    def approve(self, decision: TradeDecision, equity: float, price: float) -> RiskResult:
        if decision.action == "HOLD":
            return RiskResult(False, "AI chose HOLD.")
        if decision.confidence < float(self.settings.get("min_confidence", 0.58)):
            return RiskResult(False, "Confidence below threshold.")
        if decision.stop_loss_pct <= 0:
            return RiskResult(False, "Missing stop loss distance.")
        if self._daily_loss_exceeded(equity):
            return RiskResult(False, "Daily/account loss circuit breaker triggered.")
        cooldown_until = self.stop_cooldowns.get(decision.symbol)
        if cooldown_until and datetime.now(timezone.utc) < cooldown_until:
            return RiskResult(False, "Symbol is in stop-loss cooldown.")

        max_risk = float(self.settings.get("max_account_risk_per_trade", 0.01))
        risk_pct = min(decision.risk_pct, max_risk)
        leverage = min(decision.leverage, int(self.settings.get("max_leverage", 5)))
        risk_usdt = equity * risk_pct
        notional_by_stop = risk_usdt / decision.stop_loss_pct
        max_notional = equity * float(self.settings.get("max_position_notional_pct", 0.25)) * leverage
        notional = min(notional_by_stop, max_notional)
        quantity = notional / price if price > 0 else 0
        if quantity <= 0:
            return RiskResult(False, "Computed quantity is zero.")
        return RiskResult(True, "Approved.", quantity=round(quantity, 6), leverage=leverage)

    def record_stop_loss(self, symbol: str) -> None:
        minutes = int(self.settings.get("stop_loss_cooldown_minutes", 30))
        self.stop_cooldowns[symbol] = datetime.now(timezone.utc) + timedelta(minutes=minutes)

    def _daily_loss_exceeded(self, equity: float) -> bool:
        max_loss = float(self.settings.get("max_daily_loss_pct", 0.05))
        return equity < self.starting_equity * (1 - max_loss)


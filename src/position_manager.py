from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .ai_agent import TradeDecision
from .risk import RiskResult


PositionAction = Literal["none", "hold", "close", "close_and_reverse"]


@dataclass(frozen=True)
class PositionPlan:
    action: PositionAction
    reason: str
    current_side: str | None = None
    signal_side: str | None = None


def plan_position_action(
    position_amt: float,
    decision: TradeDecision,
    risk: RiskResult,
    settings: dict[str, Any],
) -> PositionPlan:
    if position_amt == 0:
        return PositionPlan("none", "No open position.")

    current_side = "BUY" if position_amt > 0 else "SELL"
    signal_side = decision.action if decision.action in {"BUY", "SELL"} else None
    if signal_side is None:
        return PositionPlan("hold", "AI chose HOLD while a position is open.", current_side, signal_side)
    if signal_side == current_side:
        return PositionPlan("hold", "Signal matches the open position side.", current_side, signal_side)

    close_confidence = float(settings.get("close_reversal_confidence", 0.70))
    if decision.confidence < close_confidence:
        return PositionPlan(
            "hold",
            f"Opposite signal confidence {decision.confidence:.2f} is below close threshold {close_confidence:.2f}.",
            current_side,
            signal_side,
        )

    if not bool(settings.get("reverse_on_strong_signal", True)):
        return PositionPlan("close", "Confirmed opposite signal; reverse entries are disabled.", current_side, signal_side)

    reverse_confidence = float(settings.get("reverse_confidence", 0.80))
    if decision.confidence < reverse_confidence:
        return PositionPlan(
            "close",
            f"Confirmed reversal for exit, but confidence {decision.confidence:.2f} is below reverse threshold {reverse_confidence:.2f}.",
            current_side,
            signal_side,
        )
    if not risk.approved:
        return PositionPlan(
            "close",
            f"Confirmed reversal for exit, but reverse entry was rejected: {risk.reason}",
            current_side,
            signal_side,
        )
    return PositionPlan("close_and_reverse", "Strong confirmed reversal; close and open the opposite side.", current_side, signal_side)

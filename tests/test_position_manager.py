from src.ai_agent import TradeDecision
from src.position_manager import plan_position_action
from src.risk import RiskResult


def decision(action="SELL", confidence=0.75):
    return TradeDecision(
        action=action,
        confidence=confidence,
        symbol="ETHUSDT",
        leverage=2,
        risk_pct=0.01,
        stop_loss_pct=0.01,
        take_profit_pct=0.02,
        max_holding_minutes=120,
        rationale="test",
        high_risk_features_used=[],
    )


def test_holds_when_signal_matches_existing_position():
    plan = plan_position_action(
        1.5,
        decision(action="BUY", confidence=0.9),
        RiskResult(True, "Approved.", quantity=1, leverage=1),
        {},
    )

    assert plan.action == "hold"
    assert plan.current_side == "BUY"
    assert plan.signal_side == "BUY"


def test_holds_when_opposite_signal_is_below_close_threshold():
    plan = plan_position_action(
        1.5,
        decision(action="SELL", confidence=0.69),
        RiskResult(True, "Approved.", quantity=1, leverage=1),
        {"close_reversal_confidence": 0.70},
    )

    assert plan.action == "hold"
    assert "below close threshold" in plan.reason


def test_closes_without_reversing_when_signal_is_not_strong_enough():
    plan = plan_position_action(
        1.5,
        decision(action="SELL", confidence=0.75),
        RiskResult(True, "Approved.", quantity=1, leverage=1),
        {"close_reversal_confidence": 0.70, "reverse_confidence": 0.80},
    )

    assert plan.action == "close"


def test_closes_and_reverses_when_opposite_signal_is_strong_and_risk_approved():
    plan = plan_position_action(
        1.5,
        decision(action="SELL", confidence=0.85),
        RiskResult(True, "Approved.", quantity=1, leverage=1),
        {"close_reversal_confidence": 0.70, "reverse_confidence": 0.80},
    )

    assert plan.action == "close_and_reverse"


def test_closes_only_when_reverse_entry_risk_is_rejected():
    plan = plan_position_action(
        1.5,
        decision(action="SELL", confidence=0.85),
        RiskResult(False, "Missing stop loss distance."),
        {"close_reversal_confidence": 0.70, "reverse_confidence": 0.80},
    )

    assert plan.action == "close"
    assert "reverse entry was rejected" in plan.reason

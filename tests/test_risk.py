from src.ai_agent import TradeDecision
from src.risk import RiskManager


def decision(confidence):
    return TradeDecision(
        action="BUY",
        confidence=confidence,
        symbol="BTCUSDT",
        leverage=1,
        risk_pct=0.01,
        stop_loss_pct=0.02,
        take_profit_pct=0.04,
        max_holding_minutes=90,
        rationale="tradable setup",
        high_risk_features_used=[],
    )


def test_aggressiveness_lowers_confidence_gate():
    risk = RiskManager(
        {
            "min_confidence": 0.55,
            "trading_aggressiveness": 100,
            "max_account_risk_per_trade": 0.01,
            "max_position_notional_pct": 1.0,
            "max_leverage": 1,
        },
        starting_equity=1000,
    )

    assert risk.approve(decision(0.46), equity=1000, price=100).approved


def test_conservative_aggressiveness_raises_confidence_gate():
    risk = RiskManager(
        {
            "min_confidence": 0.55,
            "trading_aggressiveness": 0,
            "max_account_risk_per_trade": 0.01,
            "max_position_notional_pct": 1.0,
            "max_leverage": 1,
        },
        starting_equity=1000,
    )

    result = risk.approve(decision(0.60), equity=1000, price=100)

    assert not result.approved
    assert "0.65" in result.reason

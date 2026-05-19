import json

import pytest

from src.ai_agent import parse_decision


def test_parse_decision_handles_json_object():
    decision = parse_decision(
        json.dumps(
            {
                "action": "BUY",
                "confidence": 0.72,
                "symbol": "BTCUSDT",
                "leverage": 3,
                "risk_pct": 0.01,
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.04,
                "max_holding_minutes": 90,
                "rationale": "trend continuation",
                "high_risk_features_used": ["volume_spike"],
            }
        ),
        fallback_symbol="ETHUSDT",
    )

    assert decision.action == "BUY"
    assert decision.symbol == "BTCUSDT"
    assert decision.high_risk_features_used == ["volume_spike"]


def test_parse_decision_handles_double_encoded_json_object():
    payload = json.dumps(
        {
            "action": "SELL",
            "confidence": 0.68,
            "symbol": "BTCUSDT",
            "leverage": 2,
            "risk_pct": 0.005,
            "stop_loss_pct": 0.01,
            "take_profit_pct": 0.02,
            "max_holding_minutes": 45,
            "rationale": "reversal setup",
            "high_risk_features_used": ["funding_z"],
        }
    )

    decision = parse_decision(json.dumps(payload), fallback_symbol="ETHUSDT")

    assert decision.action == "SELL"
    assert decision.symbol == "BTCUSDT"
    assert decision.high_risk_features_used == ["funding_z"]


def test_parse_decision_rejects_non_object_json():
    with pytest.raises(ValueError, match="JSON object"):
        parse_decision(json.dumps(["BUY"]), fallback_symbol="BTCUSDT")


def test_parse_decision_ignores_non_list_high_risk_features():
    decision = parse_decision(
        json.dumps(
            {
                "action": "HOLD",
                "confidence": 0.4,
                "high_risk_features_used": "volume_spike",
            }
        ),
        fallback_symbol="BTCUSDT",
    )

    assert decision.high_risk_features_used == []

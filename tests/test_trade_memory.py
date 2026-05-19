import json

from src.trade_memory import build_trade_memory


def test_build_trade_memory_summarizes_recent_closed_trades(tmp_path):
    path = tmp_path / "journal.jsonl"
    records = [
        {
            "ts": "2026-05-18T10:00:00+08:00",
            "event_type": "trade_closed",
            "symbol": "BTCUSDT",
            "decision": {"action": "BUY", "confidence": 0.72, "rationale": "trend continuation"},
            "features": {"rsi_14": 42, "trend_regime": "uptrend", "ignored": "large"},
            "net_pnl": 12.5,
            "pnl_pct": 0.025,
            "exit_reason": "take_profit",
        },
        {
            "ts": "2026-05-18T12:00:00+08:00",
            "event_type": "trade_closed",
            "symbol": "BTCUSDT",
            "decision": {"action": "SELL", "confidence": 0.81},
            "features": {"rsi_14": 78, "trend_regime": "uptrend"},
            "realized_pnl": -5.0,
            "pnl_pct": -0.01,
            "exit_reason": "stop_loss",
        },
        {
            "ts": "2026-05-18T13:00:00+08:00",
            "event_type": "trade_closed",
            "symbol": "ETHUSDT",
            "decision": {"action": "BUY", "confidence": 0.7},
            "pnl": 99,
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    memory = build_trade_memory("BTCUSDT", str(path), {"max_closed_trades": 20})

    assert memory["available"] is True
    assert memory["closed_trades"] == 2
    assert memory["win_rate"] == 0.5
    assert memory["total_pnl"] == 7.5
    assert memory["avg_pnl_pct"] == 0.0075
    assert len(memory["recent_mistakes"]) == 1
    assert memory["recent_mistakes"][0]["action"] == "SELL"
    assert memory["recent_mistakes"][0]["features"] == {"rsi_14": 78, "trend_regime": "uptrend"}


def test_build_trade_memory_handles_missing_journal(tmp_path):
    memory = build_trade_memory("BTCUSDT", str(tmp_path / "missing.jsonl"))

    assert memory["available"] is False
    assert memory["closed_trades"] == 0


def test_build_trade_memory_skips_non_object_json_lines(tmp_path):
    path = tmp_path / "journal.jsonl"
    records = [
        json.dumps("not an event"),
        json.dumps(
            {
                "event_type": "trade_closed",
                "symbol": "BTCUSDT",
                "pnl": 3.5,
            }
        ),
    ]
    path.write_text("\n".join(records), encoding="utf-8")

    memory = build_trade_memory("BTCUSDT", str(path))

    assert memory["available"] is True
    assert memory["closed_trades"] == 1

import json

from src.journal import Journal


def test_journal_uses_configured_timezone(tmp_path):
    path = tmp_path / "journal.jsonl"
    Journal(str(path), timezone_name="Asia/Taipei").write("test", {"ok": True})

    record = json.loads(path.read_text())
    assert record["ts"].endswith("+08:00")
    assert record["event_type"] == "test"
    assert record["ok"] is True


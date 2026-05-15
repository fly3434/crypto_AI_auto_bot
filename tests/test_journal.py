import json

from src.journal import Journal


def test_journal_uses_configured_timezone(tmp_path):
    path = tmp_path / "journal.jsonl"
    Journal(str(path), timezone_name="Asia/Taipei").write("test", {"ok": True})

    record = json.loads(path.read_text())
    assert record["ts"].endswith("+08:00")
    assert record["event_type"] == "test"
    assert record["ok"] is True


class FakeBlob:
    def __init__(self):
        self.upload = None

    def upload_from_string(self, payload, content_type):
        self.upload = {"payload": payload, "content_type": content_type}


class FakeBucket:
    def __init__(self):
        self.blobs = {}

    def blob(self, name):
        blob = FakeBlob()
        self.blobs[name] = blob
        return blob


class FakeStorageClient:
    def __init__(self):
        self.buckets = {}

    def bucket(self, name):
        bucket = FakeBucket()
        self.buckets[name] = bucket
        return bucket


def test_journal_uploads_matching_run_logs_to_gcs(tmp_path):
    client = FakeStorageClient()
    path = tmp_path / "journal.jsonl"
    journal = Journal(
        str(path),
        gcs_bucket="journal-bucket",
        gcs_prefix="runs",
        gcs_event_types=["error"],
        storage_client_factory=lambda: client,
    )

    journal.write("analysis", {"ok": True})
    journal.write("error", {"message": "failed"})

    uri = journal.upload_run_logs()

    assert uri.startswith("gs://journal-bucket/runs/")
    bucket = client.buckets["journal-bucket"]
    blob = next(iter(bucket.blobs.values()))
    assert '"event_type": "error"' in blob.upload["payload"]
    assert '"event_type": "analysis"' not in blob.upload["payload"]
    assert blob.upload["content_type"] == "application/jsonl; charset=utf-8"

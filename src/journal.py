from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo


class Journal:
    def __init__(
        self,
        path: str,
        timezone_name: str = "Asia/Taipei",
        gcs_bucket: str | None = None,
        gcs_prefix: str = "trading-journal",
        gcs_event_types: list[str] | None = None,
        storage_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.path = Path(path)
        self.timezone = ZoneInfo(timezone_name)
        self.gcs_bucket = gcs_bucket or os.getenv("JOURNAL_GCS_BUCKET", "")
        self.gcs_prefix = gcs_prefix.strip("/")
        self.gcs_event_types = set(gcs_event_types or [])
        self.storage_client_factory = storage_client_factory
        self._run_lines: list[str] = []
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(self.timezone).isoformat(),
            "event_type": event_type,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, default=str)
        print(line, flush=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        if self._should_export(event_type):
            self._run_lines.append(line)

    def upload_run_logs(self) -> str | None:
        if not self.gcs_bucket or not self._run_lines:
            return None

        client = self._build_storage_client()
        uploaded_at = datetime.now(timezone.utc)
        run_id = f"{uploaded_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        object_name = "/".join(
            part
            for part in [
                self.gcs_prefix,
                uploaded_at.strftime("%Y/%m/%d"),
                f"{run_id}.jsonl",
            ]
            if part
        )
        payload = "\n".join(self._run_lines) + "\n"
        bucket = client.bucket(self.gcs_bucket)
        blob = bucket.blob(object_name)
        blob.upload_from_string(payload, content_type="application/jsonl; charset=utf-8")
        self._run_lines.clear()
        return f"gs://{self.gcs_bucket}/{object_name}"

    def _should_export(self, event_type: str) -> bool:
        return bool(self.gcs_bucket) and (not self.gcs_event_types or event_type in self.gcs_event_types)

    def _build_storage_client(self) -> Any:
        if self.storage_client_factory is not None:
            return self.storage_client_factory()
        from google.cloud import storage

        return storage.Client()

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from flask import Flask, jsonify, request

from .main import run_once


def create_app() -> Flask:
    app = Flask(__name__)

    @app.get("/")
    def health() -> tuple[Any, int]:
        return jsonify({"ok": True, "service": "crypto-ai-auto-bot"}), 200

    @app.post("/run")
    def run_scheduled_cycle() -> tuple[Any, int]:
        config_path = os.getenv("CONFIG_PATH", "config.yaml")
        started_at = datetime.now(timezone.utc)
        try:
            summary = run_once(config_path)
        except Exception as exc:
            app.logger.exception("Scheduled trading cycle failed")
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": repr(exc),
                        "started_at": started_at.isoformat(),
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                500,
            )

        return (
            jsonify(
                {
                    "ok": True,
                    "trigger": request.headers.get("X-CloudScheduler-JobName", "manual"),
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                }
            ),
            200,
        )

    return app


app = create_app()

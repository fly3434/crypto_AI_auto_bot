from src import app as app_module


def test_health_endpoint_returns_ok():
    client = app_module.create_app().test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert response.get_json()["ok"] is True


def test_run_endpoint_triggers_single_cycle(monkeypatch):
    calls = []

    def fake_run_once(config_path):
        calls.append(config_path)
        return {"symbols": [], "orders": 0, "errors": 0}

    monkeypatch.setenv("CONFIG_PATH", "custom.yaml")
    monkeypatch.setattr(app_module, "run_once", fake_run_once)
    client = app_module.create_app().test_client()

    response = client.post("/run", headers={"X-CloudScheduler-JobName": "job"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["ok"] is True
    assert body["trigger"] == "job"
    assert body["summary"] == {"symbols": [], "orders": 0, "errors": 0}
    assert calls == ["custom.yaml"]

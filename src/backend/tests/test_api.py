from importlib import import_module

import pytest
from fastapi.testclient import TestClient

main = import_module("backend.main")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATABASE_PATH", tmp_path / "study-tracker.sqlite3")
    with TestClient(main.app) as test_client:
        yield test_client


def test_health_check(client):
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_timer_starts_idle(client):
    response = client.get("/api/timer")

    assert response.status_code == 200
    assert response.json() == {
        "status": "idle",
        "kind": None,
        "started_at": None,
        "ends_at": None,
        "remaining_seconds": 0,
        "duration_seconds": None,
    }


def test_timer_can_be_started_paused_resumed_and_stopped(client):
    started = client.post(
        "/api/timer/start", json={"kind": "focus", "duration_seconds": 60}
    )
    assert started.status_code == 200
    assert started.json()["status"] == "running"
    assert started.json()["duration_seconds"] == 60

    paused = client.post("/api/timer/pause")
    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["ends_at"] is None
    assert 1 <= paused.json()["remaining_seconds"] <= 60

    resumed = client.post("/api/timer/resume")
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"

    stopped = client.post("/api/timer/stop")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "idle"


def test_cannot_start_another_timer_while_one_is_running(client):
    client.post("/api/timer/start", json={"duration_seconds": 60})

    response = client.post("/api/timer/start")

    assert response.status_code == 409
    assert response.json()["detail"] == "A timer is already active"


def test_timer_rejects_durations_outside_the_allowed_range(client):
    response = client.post("/api/timer/start", json={"duration_seconds": 0})

    assert response.status_code == 422

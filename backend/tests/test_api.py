"""API tests using FastAPI's TestClient (agent calls mocked)."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from glean_client.client import GleanAgentError
from main import app

client = TestClient(app, raise_server_exceptions=False)


def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert "instance" in r.json()


def test_run_rejects_empty_prompt():
    r = client.post("/api/v1/agents/run", json={"prompt": ""})
    assert r.status_code == 422


def test_run_rejects_missing_input():
    r = client.post("/api/v1/agents/run", json={})
    assert r.status_code == 422


@patch("services.agent.agents.call_with_history", return_value="details")
def test_run_salesforce_id_only(mock_call):
    r = client.post("/api/v1/agents/run", json={"salesforce_id": "001xx000003DGb2"})
    assert r.status_code == 200
    assert r.json() == {"response": "details", "status": "success"}
    mock_call.assert_called_once_with("Salesforce ID: 001xx000003DGb2", [])


@patch("services.agent.agents.call_with_history", return_value="answer")
def test_run_success(mock_call):
    r = client.post(
        "/api/v1/agents/run",
        json={
            "prompt": "hi",
            "history": [{"role": "user", "content": "hello"}],
        },
    )
    assert r.status_code == 200
    assert r.json() == {"response": "answer", "status": "success"}
    mock_call.assert_called_once_with(
        "hi", [{"role": "user", "content": "hello"}]
    )


@patch(
    "services.agent.agents.call_with_history",
    side_effect=GleanAgentError("HTTP 403: nope", status_code=403),
)
def test_run_maps_glean_error_to_502(mock_call):
    r = client.post("/api/v1/agents/run", json={"prompt": "hi"})
    assert r.status_code == 502
    assert "403" in r.json()["detail"]


@patch(
    "services.agent.agents.call_with_history",
    side_effect=Exception("boom"),
)
def test_run_unhandled_error_returns_500(mock_call):
    r = client.post("/api/v1/agents/run", json={"prompt": "hi"})
    assert r.status_code == 500
    assert r.json() == {"detail": "Internal server error"}

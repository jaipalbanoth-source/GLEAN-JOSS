"""Direct A2 agent run tests (agent calls mocked)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from glean_client.client import GleanAgentError
from main import app

client = TestClient(app, raise_server_exceptions=False)

A2_OUT = {"preselect": True, "selected_combination_id": "QA-1", "top_3": []}


@patch("config.A2_AGENT_ID", "a2-agent-id")
@patch("services.sow.agents.call_input_json", return_value=A2_OUT)
def test_a2_run_success(mock_call):
    r = client.post(
        "/api/v1/agents/a2/run", json={"payload": '{"project_id": "p1"}'}
    )
    assert r.status_code == 200
    assert r.json() == {"result": A2_OUT}
    mock_call.assert_called_once_with(
        "a2-agent-id", {"QA Payload": '{"project_id": "p1"}'}
    )


@patch("config.A2_AGENT_ID", "a2-agent-id")
@patch("services.sow.agents.call_input_json", return_value=A2_OUT)
def test_a2_run_strips_payload(mock_call):
    r = client.post("/api/v1/agents/a2/run", json={"payload": "  hello  "})
    assert r.status_code == 200
    mock_call.assert_called_once_with("a2-agent-id", {"QA Payload": "hello"})


@patch("config.A2_AGENT_ID", None)
def test_a2_run_returns_400_when_not_configured():
    r = client.post("/api/v1/agents/a2/run", json={"payload": "hello"})
    assert r.status_code == 400
    assert "A2_AGENT_ID" in r.json()["detail"]


@patch("config.A2_AGENT_ID", "a2-agent-id")
@patch(
    "services.sow.agents.call_input_json",
    side_effect=GleanAgentError("HTTP 403: nope", status_code=403),
)
def test_a2_run_maps_glean_error_to_502(mock_call):
    r = client.post("/api/v1/agents/a2/run", json={"payload": "hello"})
    assert r.status_code == 502
    assert "403" in r.json()["detail"]


@patch("config.A2_AGENT_ID", "a2-agent-id")
@patch(
    "services.sow.agents.call_input_json",
    side_effect=ValueError("no JSON object found"),
)
def test_a2_run_maps_bad_json_to_502(mock_call):
    r = client.post("/api/v1/agents/a2/run", json={"payload": "hello"})
    assert r.status_code == 502
    assert "invalid JSON" in r.json()["detail"]


@pytest.mark.parametrize("payload", ["", "   "])
def test_a2_run_rejects_empty_payload(payload):
    r = client.post("/api/v1/agents/a2/run", json={"payload": payload})
    assert r.status_code == 422


def test_a2_run_rejects_missing_payload():
    r = client.post("/api/v1/agents/a2/run", json={})
    assert r.status_code == 422

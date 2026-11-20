"""SOW A1 run tests (agent calls mocked)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from glean_client.client import GleanAgentError
from main import app
from services.sow import run_a1

client = TestClient(app, raise_server_exceptions=False)

A1_OUT = {"project_id": "p1", "fields": {}, "errors": []}


@patch("services.sow.agents.call_input_json", return_value=A1_OUT)
def test_run_a1_calls_a1_agent_only(mock_call):
    result = run_a1("001xx000003DGb2AAA")

    assert result == A1_OUT
    mock_call.assert_called_once()
    args = mock_call.call_args
    assert args.args[1] == {"Salesforce Opportunity ID": "001xx000003DGb2AAA"}


@patch("services.sow.agents.call_input_json", return_value=A1_OUT)
def test_sow_analyze_endpoint_runs_a1_only(mock_analyze):
    r = client.post("/api/v1/sow/analyze", json={"opportunity_id": "001xx000003DGb2"})
    assert r.status_code == 200
    assert r.json() == {"a1": A1_OUT}
    mock_analyze.assert_called_once()
    assert mock_analyze.call_args.args[1] == {
        "Salesforce Opportunity ID": "001xx000003DGb2"
    }


def test_sow_analyze_rejects_bad_id():
    r = client.post("/api/v1/sow/analyze", json={"opportunity_id": "not-valid!"})
    assert r.status_code == 422


def test_sow_analyze_rejects_wrong_length():
    r = client.post("/api/v1/sow/analyze", json={"opportunity_id": "001xx000003DGb"})
    assert r.status_code == 422


@patch(
    "services.sow.agents.call_input_json",
    side_effect=GleanAgentError("HTTP 403: nope", status_code=403),
)
def test_sow_analyze_maps_glean_error_to_502(mock_analyze):
    r = client.post("/api/v1/sow/analyze", json={"opportunity_id": "001xx000003DGb2"})
    assert r.status_code == 502
    assert "403" in r.json()["detail"]


@patch(
    "services.sow.agents.call_input_json",
    side_effect=ValueError("no JSON object found"),
)
def test_sow_analyze_maps_bad_json_to_502(mock_analyze):
    r = client.post("/api/v1/sow/analyze", json={"opportunity_id": "001xx000003DGb2"})
    assert r.status_code == 502
    assert "invalid JSON" in r.json()["detail"]


def test_call_input_json_parses_fenced_json():
    from glean_client import agents

    with patch("glean_client.client.run_agent_input", return_value='```json\n{"a": 1}\n```'):
        assert agents.call_input_json("agent-x", {"k": "v"}) == {"a": 1}


def test_call_input_json_raises_when_no_json():
    from glean_client import agents

    with patch("glean_client.client.run_agent_input", return_value="no json here"):
        with pytest.raises(ValueError):
            agents.call_input_json("agent-x", {"k": "v"})


def test_call_input_json_tolerates_trailing_text():
    from glean_client import agents

    out = '{"reply": "ok", "status": "ready"} Let me know if you need anything else.'
    with patch("glean_client.client.run_agent_input", return_value=out):
        assert agents.call_input_json("agent-x", {"k": "v"}) == {"reply": "ok", "status": "ready"}


def test_call_input_json_tolerates_second_object():
    from glean_client import agents

    out = '{"a": 1}\n{"b": 2}'
    with patch("glean_client.client.run_agent_input", return_value=out):
        assert agents.call_input_json("agent-x", {"k": "v"}) == {"a": 1}


def test_call_input_json_skips_user_inputs_echo():
    from glean_client import agents

    echo = ('<user_inputs><field_value name="X" label="X">'
            '{"message": "hi"}</field_value></user_inputs>')
    out = echo + '{"reply": "ok", "status": "ready"}'
    with patch("glean_client.client.run_agent_input", return_value=out):
        assert agents.call_input_json("agent-x", {"k": "v"}) == {"reply": "ok", "status": "ready"}


def test_contacts_extract_json_skips_echo():
    from services.contacts import _extract_json

    echo = ('<user_inputs><field_value name="Contacts Chat Payload">'
            '{"project_id": "p1"}</field_value></user_inputs>')
    out = echo + '{"reply": "Got it", "status": "ready", "changes": []}'
    assert _extract_json(out)["reply"] == "Got it"

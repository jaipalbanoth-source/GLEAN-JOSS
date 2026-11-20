"""Tests for the Task Builder (A3) flow: payload context, apply, API.

Salesforce is always faked here — .env has real credentials and tests must
never query or write to the real org.
"""
import json as _json
from unittest.mock import patch

from fastapi.testclient import TestClient

import  config
from main import app
from services import tasks as svc

client = TestClient(app, raise_server_exceptions=False)


def _fake_sf(monkeypatch):
    monkeypatch.setattr(svc.salesforce, "make_client", lambda: object())


def _add(name="Data Migration"):
    return {
        "op": "add", "task_id": None,
        "task": {"name": name, "assignee_id": "A-101", "assignee_name": "Rahul Patil",
                 "billable": True, "active": True, "utilization": 50,
                 "start": "2026-11-01", "end": "2026-12-15"},
    }


def test_context_shape(monkeypatch):
    _fake_sf(monkeypatch)
    ctx = svc.context("006FAKESHAPE0001A")
    assert set(ctx) >= {"project_id", "revision", "today", "project", "assignees", "tasks"}
    assert ctx["revision"] == 1
    assert ctx["tasks"] == []
    assert len(ctx["assignees"]) >= 1
    assert ctx["project"]["default_billable"] is True


def test_apply_add_update_deactivate_local(monkeypatch):
    _fake_sf(monkeypatch)
    pid = "006FAKEAPPLY0001A"
    r1 = svc.apply(pid, [_add()])
    assert r1["revision"] == 2
    assert len(r1["tasks"]) == 1
    tid = r1["tasks"][0]["task_id"]
    assert tid
    assert r1["tasks"][0]["name"] == "Data Migration"
    assert r1["applied"][0]["op"] == "add"
    assert r1["salesforce"].startswith("local")

    upd = {"op": "update", "task_id": tid, "task_name": "Data Migration",
           "changes": {"end": {"from": "2026-12-15", "to": "2026-11-30"}}}
    r2 = svc.apply(pid, [upd])
    assert r2["revision"] == 3
    assert r2["tasks"][0]["end"] == "2026-11-30"

    r3 = svc.apply(pid, [{"op": "deactivate", "task_id": tid, "task_name": "Data Migration"}])
    assert r3["revision"] == 4
    assert r3["tasks"][0]["active"] is False


def test_chat_requires_agent_id(monkeypatch):
    monkeypatch.setattr(config, "A3_AGENT_ID", None)
    try:
        svc.chat("006FAKECHAT00001A", "create something", [])
        raise AssertionError("expected NotConfigured")
    except svc.NotConfigured:
        pass


@patch("services.tasks.agents.call_input_json")
def test_chat_endpoint_and_payload(mock_call, monkeypatch):
    _fake_sf(monkeypatch)
    monkeypatch.setattr(config, "A3_AGENT_ID", "agent-x")
    mock_call.return_value = {"reply": "ok", "status": "no_change", "changes": [],
                              "missing": [], "candidates": [], "assignee_load": [], "rule_hits": []}
    pid = "006FAKECHAT00002A"
    r = client.post(f"/api/v1/projects/{pid}/tasks/chat",
                    json={"message": "hello", "chat_history": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    assert r.json()["status"] == "no_change"
    agent_id, payload_arg = mock_call.call_args[0]
    assert agent_id == "agent-x"
    assert config.A3_INPUT_KEY in payload_arg
    payload = _json.loads(payload_arg[config.A3_INPUT_KEY])
    assert payload["message"] == "hello"
    assert payload["chat_history"] == [{"role": "user", "content": "hi"}]
    assert payload["project_id"] == pid
    assert "assignees" in payload and "tasks" in payload and "project" in payload


def test_chat_endpoint_without_agent(monkeypatch):
    monkeypatch.setattr(config, "A3_AGENT_ID", None)
    r = client.post("/api/v1/projects/006FAKECHAT00003A/tasks/chat", json={"message": "hello"})
    assert r.status_code == 422


def test_apply_endpoint(monkeypatch):
    _fake_sf(monkeypatch)
    r = client.post("/api/v1/projects/006FAKEENDP00001A/tasks/apply", json={"changes": [_add()]})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["revision"] >= 1
    assert body["applied"][0]["op"] == "add"
    assert "salesforce" in body


def test_context_endpoint(monkeypatch):
    _fake_sf(monkeypatch)
    r = client.get("/api/v1/projects/006FAKECONX00001A/tasks")
    assert r.status_code == 200
    assert r.json()["project_id"] == "006FAKECONX00001A"

"""Tests for the Glean HTTP client (network mocked)."""

from unittest.mock import MagicMock, patch

import pytest

from glean_client.client import (
    GleanAgentError,
    run_agent,
    run_agent_messages,
)


def _response(status_code=200, payload=None):
    resp = MagicMock()
    resp.ok = status_code < 400
    resp.status_code = status_code
    resp.text = str(payload)
    resp.json.return_value = payload if payload is not None else {}
    return resp


@patch("glean_client.client.requests.post")
def test_run_agent_success(mock_post):
    mock_post.return_value = _response(
        payload={
            "run": {"status": "success"},
            "messages": [{"content": [{"text": "hello"}]}],
        }
    )

    assert run_agent("agent-1", "hi") == "hello"

    payload = mock_post.call_args.kwargs["json"]
    assert payload["agent_id"] == "agent-1"
    assert payload["messages"] == [
        {"role": "USER", "content": [{"text": "hi", "type": "text"}]}
    ]


@patch("glean_client.client.requests.post")
def test_run_agent_http_error(mock_post):
    mock_post.return_value = _response(status_code=403, payload={"error": "nope"})

    with pytest.raises(GleanAgentError) as exc:
        run_agent("agent-1", "hi")

    assert exc.value.status_code == 403


@patch("glean_client.client.requests.post")
def test_run_agent_messages_multi_turn(mock_post):
    mock_post.return_value = _response(
        payload={
            "run": {"status": "success"},
            "messages": [{"content": [{"text": "ok"}]}],
        }
    )
    messages = [
        {"role": "USER", "content": [{"type": "text", "text": "a"}]},
        {"role": "ASSISTANT", "content": [{"type": "text", "text": "b"}]},
        {"role": "USER", "content": [{"type": "text", "text": "c"}]},
    ]

    text, status = run_agent_messages("agent-1", messages)

    assert text == "ok"
    assert status == "success"
    assert mock_post.call_args.kwargs["json"]["messages"] == messages


@patch("glean_client.client.requests.post")
def test_run_status_failure(mock_post):
    mock_post.return_value = _response(
        payload={"run": {"status": "error"}, "messages": []}
    )

    with pytest.raises(GleanAgentError):
        run_agent("agent-1", "hi")

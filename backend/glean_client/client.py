# pyrefly: ignore[missing-import]
import truststore

truststore.inject_into_ssl()

import requests

from config import GLEAN_API_TOKEN, GLEAN_INSTANCE

BASE_URL = f"https://{GLEAN_INSTANCE}-be.glean.com/rest/api/v1"
HEADERS = {
    "Authorization": f"Bearer {GLEAN_API_TOKEN}",
    "Content-Type": "application/json",
}

SUCCESS_STATUSES = ("success", "completed", None)


class GleanAgentError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _extract_text(data: dict) -> str:
    return "".join(
        c.get("text", "")
        for m in data.get("messages", [])
        for c in m.get("content", [])
    )


def _post_run(payload: dict, timeout: int = 300) -> tuple[str, str | None]:
    r = requests.post(
        f"{BASE_URL}/agents/runs/wait",
        headers=HEADERS,
        json=payload,
        timeout=timeout,
    )

    if not r.ok:
        raise GleanAgentError(
            f"Glean API error: HTTP {r.status_code}: {r.text}",
            status_code=r.status_code,
        )

    data = r.json()
    status = data.get("run", {}).get("status")
    if status not in SUCCESS_STATUSES:
        raise GleanAgentError(f"Run status: {status} | {data}")

    return _extract_text(data), status


def run_agent_messages(
    agent_id: str, messages: list[dict], timeout: int = 300
) -> tuple[str, str | None]:
    """Run an agent with a full message list.

    Each message: {"role": "USER"|"ASSISTANT", "content": [{"type": "text", "text": ...}]}
    Returns (text, run_status).
    """
    return _post_run({"agent_id": agent_id, "messages": messages}, timeout=timeout)


def run_agent_input(
    agent_id: str, input_payload: dict, timeout: int = 300
) -> str:
    """Run an agent with a structured input object (form fields, not chat)."""
    text, _ = _post_run(
        {"agent_id": agent_id, "input": input_payload}, timeout=timeout
    )
    return text


def run_agent(agent_id: str, prompt: str, timeout: int = 300) -> str:
    messages = [{"role": "USER", "content": [{"text": prompt, "type": "text"}]}]
    text, _ = run_agent_messages(agent_id, messages, timeout=timeout)
    return text

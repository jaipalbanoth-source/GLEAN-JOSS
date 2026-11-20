import json
import re

from config import AGENT_ID
from . import client
from .client import run_agent, run_agent_messages


def call(prompt: str) -> str:
    return run_agent(AGENT_ID, prompt)


def call_with_history(prompt: str, history: list[dict] | None = None) -> str:
    """history: [{"role": "user"|"assistant", "content": str}] oldest first."""
    messages = [
        {
            "role": "USER" if h["role"] == "user" else "ASSISTANT",
            "content": [{"type": "text", "text": h["content"]}],
        }
        for h in history or []
    ]
    messages.append({"role": "USER", "content": [{"type": "text", "text": prompt}]})
    text, _ = run_agent_messages(AGENT_ID, messages)
    return text


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    # Some agents echo their input form before replying:
    # <user_inputs><field_value ...>{payload}</field_value></user_inputs>{"reply":...}
    # Drop the echo so we parse the agent's answer, not the payload we sent.
    text = re.sub(r"<user_inputs>.*?</user_inputs>", "", text, flags=re.DOTALL).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object found in agent output")
    # Models sometimes add trailing text/markdown after the object
    # ("Extra data" from json.loads): decode the first {...} that parses.
    decoder = json.JSONDecoder()
    idx = start
    while idx <= end:
        if text[idx] == "{":
            try:
                obj, _ = decoder.raw_decode(text, idx)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
        idx += 1
    raise ValueError("no valid JSON object found in agent output")


def call_json(prompt: str) -> dict:
    return _parse_json(call(prompt))


def call_input_json(agent_id: str, input_payload: dict) -> dict:
    """Run an agent with a structured input object and parse JSON output."""
    text = client.run_agent_input(agent_id, input_payload)
    return _parse_json(text)

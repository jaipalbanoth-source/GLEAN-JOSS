import config
from glean_client import agents


def run_a1(opp_id: str) -> dict:
    """Run the A1 agent only (SOW / opportunity extraction)."""
    return agents.call_input_json(
        config.A1_AGENT_ID, {config.A1_INPUT_KEY: opp_id}
    )


def run_a2(payload_text: str) -> dict:
    """Run the A2 agent directly with free-form text as its input payload."""
    return agents.call_input_json(
        config.A2_AGENT_ID, {config.A2_INPUT_KEY: payload_text}
    )

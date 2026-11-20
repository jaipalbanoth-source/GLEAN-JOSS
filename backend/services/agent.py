import schemas
from glean_client import agents


def compose_prompt(prompt: str | None, salesforce_id: str | None) -> str:
    if prompt and salesforce_id:
        return f"{prompt}\n\nSalesforce ID: {salesforce_id}"
    if salesforce_id:
        return f"Salesforce ID: {salesforce_id}"
    return prompt or ""


def run_chat(request: schemas.ChatRequest) -> tuple[str, str | None]:
    history = [m.model_dump() for m in request.history]
    prompt = compose_prompt(request.prompt, request.salesforce_id)
    text = agents.call_with_history(prompt, history)
    return text, "success"

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1)


class ChatRequest(BaseModel):
    prompt: str | None = Field(default=None, max_length=4000)
    salesforce_id: str | None = Field(default=None, max_length=64)
    history: list[ChatMessage] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def _require_input(self) -> "ChatRequest":
        self.prompt = (self.prompt or "").strip() or None
        self.salesforce_id = (self.salesforce_id or "").strip() or None
        if not self.prompt and not self.salesforce_id:
            raise ValueError("prompt or salesforce_id is required")
        return self


class ChatResponse(BaseModel):
    response: str
    status: str | None = None


class HealthResponse(BaseModel):
    status: str
    instance: str


class A2RunRequest(BaseModel):
    payload: str = Field(max_length=20000)

    @model_validator(mode="after")
    def _require_payload(self) -> "A2RunRequest":
        self.payload = self.payload.strip()
        if not self.payload:
            raise ValueError("payload is required")
        return self


class A2RunResponse(BaseModel):
    result: dict


class AnalyzeRequest(BaseModel):
    opportunity_id: str = Field(pattern=r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$")


class AnalyzeResponse(BaseModel):
    a1: dict

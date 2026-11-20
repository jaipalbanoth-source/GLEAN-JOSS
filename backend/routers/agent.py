from fastapi import APIRouter, HTTPException

import config, schemas
from config import GLEAN_INSTANCE
from glean_client.client import GleanAgentError
from services import agent as agent_service
from services import sow as sow_service

router = APIRouter(prefix="/api/v1")


@router.get("/health", response_model=schemas.HealthResponse)
def health() -> schemas.HealthResponse:
    return schemas.HealthResponse(status="ok", instance=GLEAN_INSTANCE)


@router.post("/agents/run", response_model=schemas.ChatResponse)
def run_agent(request: schemas.ChatRequest) -> schemas.ChatResponse:
    try:
        text, status = agent_service.run_chat(request)
    except GleanAgentError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return schemas.ChatResponse(response=text, status=status)


@router.post("/agents/a2/run", response_model=schemas.A2RunResponse)
def run_a2(request: schemas.A2RunRequest) -> schemas.A2RunResponse:
    if not config.A2_AGENT_ID:
        raise HTTPException(
            status_code=400,
            detail="A2 agent is not configured (set A2_AGENT_ID in .env)",
        )
    try:
        result = sow_service.run_a2(request.payload)
    except GleanAgentError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=502, detail=f"Agent returned invalid JSON: {e}"
        ) from e
    return schemas.A2RunResponse(result=result)


@router.post("/sow/analyze", response_model=schemas.AnalyzeResponse)
def analyze(request: schemas.AnalyzeRequest) -> schemas.AnalyzeResponse:
    try:
        a1 = sow_service.run_a1(request.opportunity_id.strip())
    except GleanAgentError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=502, detail=f"Agent returned invalid JSON: {e}"
        ) from e
    return schemas.AnalyzeResponse(a1=a1)

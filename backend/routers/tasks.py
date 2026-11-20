"""Task Builder (A3) endpoints, at /api/v1/projects/{project_id}/tasks."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import salesforce, tasks as svc

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])

class TaskChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_history: list[dict[str, str]] = Field(default_factory=list, max_length=40)

class TaskChangesBody(BaseModel):
    changes: list[dict[str, Any]] = Field(default_factory=list)

class TaskApplyBody(TaskChangesBody):
    # One id per Create/Update click; resend the same id when retrying.
    request_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    # Revision the PM's screen was built from (from GET .../tasks or the last apply).
    expected_revision: int | None = None

@router.get("")
def tasks_context(project_id: str):
    try:
        return svc.context(project_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/chat")
def task_chat(project_id: str, body: TaskChatBody):
    try:
        return svc.chat(project_id, body.message.strip(), body.chat_history)
    except svc.NotConfigured as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(422, f"A3 returned invalid JSON: {e}")
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/validate")
def task_validate(project_id: str, body: TaskChangesBody):
    """Dry-run the TK rules (no writes). Use it to enable/disable the Create button."""
    try:
        return svc.validate(project_id, body.changes)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/apply")
def task_apply(project_id: str, body: TaskApplyBody):
    try:
        res = svc.apply(project_id, body.changes, body.request_id, body.expected_revision)
    except svc.ValidationFailed as e:
        raise HTTPException(422, {"message": "A rule was broken", "hits": e.hits})
    except svc.RevisionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce write failed: {e.detail}")
    if not res["ok"] and not res["applied"]:  # nothing was saved
        errs = "; ".join(r["error"] for r in res["results"] if r.get("error"))
        raise HTTPException(502, {"message": f"Salesforce write failed: {errs}", "results": res["results"]})
    return res  # ok=false here means some changes saved and some did not: see results

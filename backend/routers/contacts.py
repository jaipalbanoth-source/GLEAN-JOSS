"""Jade Contacts (A4) endpoints, at /api/v1/projects/{project_id}/contacts."""
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import contacts as svc, salesforce

router = APIRouter(prefix="/projects/{project_id}/contacts", tags=["contacts"])

class ContactChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_history: list[dict[str, str]] = Field(default_factory=list, max_length=40)

class ContactChangesBody(BaseModel):
    changes: list[dict[str, Any]] = Field(default_factory=list)

class ContactApplyBody(ContactChangesBody):
    request_id: str | None = Field(default=None, min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    expected_revision: int | None = None

@router.get("")
def contacts_context(project_id: str):
    try:
        return svc.context(project_id)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/chat")
def contacts_chat(project_id: str, body: ContactChatBody):
    try:
        return svc.chat(project_id, body.message.strip(), body.chat_history)
    except svc.NotConfigured as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(422, f"A4 returned invalid JSON: {e}")
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/validate")
def contacts_validate(project_id: str, body: ContactChangesBody):
    try:
        return svc.validate(project_id, body.changes)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce read failed: {e.detail}")

@router.post("/apply")
def contacts_apply(project_id: str, body: ContactApplyBody):
    try:
        return svc.apply(project_id, body.changes, body.request_id, body.expected_revision)
    except svc.ValidationFailed as e:
        raise HTTPException(422, {"message": "A rule was broken", "hits": e.hits})
    except svc.RevisionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))
    except salesforce.SalesforceError as e:
        raise HTTPException(502, f"Salesforce write failed (nothing was saved): {e.detail}")

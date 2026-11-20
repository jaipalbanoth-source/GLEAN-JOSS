from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import resources as svc, salesforce

router = APIRouter(
    prefix="/projects/{project_id}/resources",
    tags=["resources"],
)

class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    chat_history: list[dict[str, str]] = Field(
        default_factory=list,
        max_length=40,
    )

class ChangesBody(BaseModel):
    changes: list[dict[str, Any]] = Field(default_factory=list)

class ApplyBody(ChangesBody):
    request_id: str | None = None
    expected_revision: int | None = None

def run(fn, *args):
    try:
        return fn(*args)
    except svc.NotConfigured as error:
        raise HTTPException(422, str(error))
    except svc.RevisionConflict as error:
        raise HTTPException(409, str(error))
    except svc.ValidationFailed as error:
        raise HTTPException(
            422,
            {
                "message": "A resource rule was broken",
                "hits": error.hits,
            },
        )
    except ValueError as error:
        raise HTTPException(422, str(error))
    except salesforce.SalesforceError as error:
        raise HTTPException(502, error.detail)

@router.get("")
def get_resources(project_id: str):
    return run(svc.context, project_id)

@router.post("/chat")
def resource_chat(project_id: str, body: ChatBody):
    return run(
        svc.chat,
        project_id,
        body.message.strip(),
        body.chat_history,
    )

@router.post("/validate")
def resource_validate(project_id: str, body: ChangesBody):
    return run(svc.validate, project_id, body.changes)

@router.post("/apply")
def resource_apply(project_id: str, body: ApplyBody):
    return run(
        svc.apply,
        project_id,
        body.changes,
        body.request_id,
        body.expected_revision,
    )

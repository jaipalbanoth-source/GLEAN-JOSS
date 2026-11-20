"""Opportunity polling + review endpoints, mounted at /api/v1/opportunities."""
import re
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from services import opportunities as svc

router = APIRouter(prefix="/opportunities", tags=["opportunities"])
_ID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


class FieldsBody(BaseModel):
    fields: dict[str, Any] = {}
    resolve: list[str] = []


class QaBody(BaseModel):
    combination_id: str
    reason: Optional[str] = None


def _run(fn, *args):
    try:
        return fn(*args)
    except svc.NotFound:
        raise HTTPException(404, "Opportunity not found")
    except svc.Conflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


def _check(oid: str) -> str:
    if not _ID.match(oid):
        raise HTTPException(422, "Invalid Salesforce ID")
    return oid


@router.get("")
def list_opportunities():
    return svc.manager.meta()


@router.post("/poll")
def poll_now():
    svc.manager.poll_once()
    return svc.manager.meta()


@router.get("/{oid}")
def get_opportunity(oid: str):
    return _run(svc.manager.detail, _check(oid))


@router.post("/{oid}/analyze")
def analyze(oid: str):
    _run(svc.manager.reanalyze, _check(oid))
    return svc.manager.detail(oid)


@router.put("/{oid}/fields")
def save_fields(oid: str, body: FieldsBody):
    _run(svc.manager.save_fields, _check(oid), body.fields, body.resolve)
    return svc.manager.detail(oid)


@router.put("/{oid}/qa")
def set_qa(oid: str, body: QaBody):
    _run(svc.manager.set_qa, _check(oid), body.combination_id, body.reason)
    return svc.manager.detail(oid)


@router.post("/{oid}/submit")
def submit(oid: str, x_user_email: str = Header(default="")):
    result = _run(svc.manager.submit, _check(oid), x_user_email)
    return {"submission": result, "opportunity": svc.manager.detail(oid)}
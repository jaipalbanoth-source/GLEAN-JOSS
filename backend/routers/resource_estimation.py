"""Read-only Opportunity estimation and allocation lookup endpoint."""

from fastapi import APIRouter, HTTPException

from services import resource_estimation as svc
from services import salesforce

router = APIRouter(
    prefix="/opportunities",
    tags=["opportunities"],
)

@router.get("/{opportunity_id}/resource-estimation")
def get_resource_estimation(opportunity_id: str):
    try:
        return svc.lookup(opportunity_id)

    except svc.OpportunityNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    except salesforce.SalesforceError as exc:
        raise HTTPException(status_code=502, detail=exc.detail) from exc

"""Read persisted project estimation and resource allocations from an Opportunity."""

from __future__ import annotations

import html
import json
import re
from typing import Any

from services import salesforce

OPPORTUNITY_ID = re.compile(r"^006[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?$")

OPPORTUNITY_FIELDS = (
    "Id",
    "Name",
    "Account.Name",
    "StageName",
    "Amount",
    "PO_number__c",
    "Project_version_number__c",
    "Project_start_date__c",
    "Project_end_date__c",
    "Bill_Rate__c",
    "Budget_Amount__c",
    "Total_Hours__c",
    "Estimation_Details__c",
    "Resource_Lines__c",
)

class OpportunityNotFound(ValueError):
    pass

def _tidy(text: str) -> str:
    """Repair the usual Salesforce long-text corruptions of JSON."""
    t = html.unescape(text)
    for bad, good in (
        ("\u2018", '"'), ("\u2019", '"'), ("\u201c", '"'), ("\u201d", '"'),
        ("\u2013", "-"), ("\u2014", "-"),
    ):
        t = t.replace(bad, good)
    t = re.sub(r",(\s*[}\]])", r"\1", t)          # trailing commas
    if "'" in t and '"' not in t:
        t = t.replace("'", '"')                    # single-quoted JSON
    return t.strip()

def _lenient_loads(text: str) -> tuple[Any, str | None]:
    """Return (value, note) or (None, error_message).
    Tries strict JSON first, then repairs, then double-encoded strings."""
    attempts = (
        (text, None),
        (_tidy(text), "JSON formatting repaired automatically — data loaded"),
    )
    last_err = "empty input"
    for candidate, note in attempts:
        if not candidate:
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as e:
            last_err = f"{e.msg} (line {e.lineno}, column {e.colno})"
            continue
        if isinstance(value, str):
            try:
                value = json.loads(value)
                note = (note + "; " if note else "") + "decoded twice (double-encoded)"
            except json.JSONDecodeError as e:
                last_err = f"{e.msg} (line {e.lineno}, column {e.colno})"
                continue
        return value, note
    return None, last_err

def parse_json_field(
    raw: Any,
    field_name: str,
    expected_key: str,
    warnings: list[str],
    fields_info: dict[str, dict],
) -> dict[str, Any]:
    info: dict[str, Any] = {"ok": False, "error": None, "raw_preview": None, "note": None}
    fields_info[field_name] = info

    if raw in (None, ""):
        info["ok"] = True
        info["note"] = "empty"
        return {expected_key: []}

    if not isinstance(raw, str):
        info["error"] = "value is not stored as text"
        warnings.append(f"{field_name} is not stored as text; showing no data.")
        return {expected_key: []}

    text = raw.strip().lstrip("\ufeff")
    info["raw_preview"] = text[:400]

    value, detail = _lenient_loads(text)
    if value is None:
        info["error"] = detail
        warnings.append(
            f"{field_name} contains invalid JSON ({detail}); showing no data — see the raw preview below."
        )
        return {expected_key: []}

    if detail:
        info["note"] = detail

    if isinstance(value, list):
        value = {expected_key: value}
        info["note"] = f"stored as a bare JSON list; shown under '{expected_key}'"

    if not isinstance(value, dict):
        info["error"] = f"expected a JSON object or list, got {type(value).__name__}"
        warnings.append(f"{field_name} must contain a JSON object or list; showing no data.")
        return {expected_key: []}

    if not isinstance(value.get(expected_key, []), list):
        warnings.append(f"{field_name}.{expected_key} must be a list; showing that part empty.")
        value[expected_key] = []

    info["ok"] = True
    return value

def lookup_record(opportunity_id: str, sf: Any) -> dict[str, Any]:
    if not OPPORTUNITY_ID.fullmatch(opportunity_id):
        raise ValueError(
            "Opportunity ID must be a 15- or 18-character ID beginning with 006."
        )

    if not hasattr(sf, "query"):
        raise ValueError(
            "Resource lookup requires configured Salesforce credentials."
        )

    fields = ", ".join(OPPORTUNITY_FIELDS)

    records = sf.query(
        f"SELECT {fields} "
        f"FROM Opportunity "
        f"WHERE Id = '{opportunity_id}' "
        f"LIMIT 1"
    )

    if not records:
        raise OpportunityNotFound(
            f"Opportunity {opportunity_id} was not found."
        )

    return records[0]

def lookup(opportunity_id: str, sf: Any | None = None) -> dict[str, Any]:
    sf = sf or salesforce.make_client()
    record = lookup_record(opportunity_id.strip(), sf)

    warnings: list[str] = []
    fields_info: dict[str, dict] = {}

    estimation = parse_json_field(
        record.get("Estimation_Details__c"),
        "Estimation_Details__c",
        "groups",
        warnings,
        fields_info,
    )

    allocation = parse_json_field(
        record.get("Resource_Lines__c"),
        "Resource_Lines__c",
        "lines",
        warnings,
        fields_info,
    )

    return {
        "opportunity": {
            "id": record.get("Id"),
            "name": record.get("Name"),
            "account_name": (record.get("Account") or {}).get("Name"),
            "stage": record.get("StageName"),
            "amount": record.get("Amount"),
            "po_number": record.get("PO_number__c"),
            "project_version": record.get("Project_version_number__c"),
            "start_date": record.get("Project_start_date__c"),
            "end_date": record.get("Project_end_date__c"),
            "bill_rate": record.get("Bill_Rate__c"),
            "budget_amount": record.get("Budget_Amount__c"),
            "total_hours": record.get("Total_Hours__c"),
        },
        "estimation": estimation,
        "resource_allocation": allocation,
        "fields": fields_info,
        "warnings": warnings,
        "salesforce": (
            "real"
            if isinstance(sf, salesforce.SalesforceClient)
            else "fake"
        ),
    }

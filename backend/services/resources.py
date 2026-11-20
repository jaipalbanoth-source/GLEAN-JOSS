"""A5 Resource Management: proposal-only agent with host-side validation and write-back."""
import hashlib
import itertools
import json
import os
import re
import threading
from datetime import date

import  config
from glean_client import agents
from services import masters, opportunities, salesforce

FIELDS = {
    "name": "Name", "project_id": "Opportunity__c", "role": "Role__c",
    "level": "Level__c", "tier": "Tier__c", "location": "Location__c",
    "technology": "Technology__c", "bill_rate": "Bill_Rate__c",
    "hours": "Hours__c", "budget": "Budget__c",
    "allocation_pct": "Allocation__c", "associate_id": "Associate_Id__c",
    "associate_name": "Associate_Name__c", "start": "Start_Date__c",
    "end": "End_Date__c", "active": "Active__c", "source": "Source__c",
    "request_key": "Request_Key__c", "revision": "Revision__c",
}

LINE_FIELDS = (
    "role", "level", "tier", "location", "technology", "bill_rate",
    "hours", "allocation_pct", "associate_id", "associate_name", "start", "end",
)

_REQUEST = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_LOCK = threading.RLock()
_APPLY_LOCK = threading.Lock()
_STORE = {}
_IDS = itertools.count(1)

class NotConfigured(RuntimeError):
    pass

class RevisionConflict(Exception):
    pass

class ValidationFailed(Exception):
    def __init__(self, hits):
        self.hits = hits

def _real(sf):
    return isinstance(sf, salesforce.SalesforceClient)

def _num(value, name):
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")

def _date(value):
    return date.fromisoformat(str(value))

def _body(values):
    return {FIELDS[k]: v for k, v in values.items() if k in FIELDS}

def _state(project_id):
    with _LOCK:
        return _STORE.setdefault(project_id, {"revision": 1, "rows": []})

def _project(project_id):
    """Read the A1 header retained by OpportunityManager."""
    record = opportunities.manager.detail(project_id)

    if not record.get("a1"):
        raise ValueError("Wait for A1 analysis to finish before using Resource Management")

    fields = record.get("final_fields") or {}
    start = fields.get("start_date")
    finish = fields.get("finish_date")

    if not start or not finish:
        raise ValueError(
            "A1 must provide project start_date and finish_date before resources can be managed"
        )

    return {
        "start_date": start,
        "finish_date": finish,
        "execution_model": fields.get("execution_model"),
        "margin_target_pct": _num(fields.get("margin_target") or 0, "margin_target_pct"),
        "tolerance_pct": _num(fields.get("tolerance_pct") or 0, "tolerance_pct"),
        # A1 does not return resource-line totals. Use Salesforce Opportunity Amount.
        "estimate_budget_total": _num(record.get("amount") or 0, "Opportunity amount"),
    }

def _from_sf(record):
    return {
        "id": record["Id"],
        **{key: record.get(FIELDS[key]) for key in LINE_FIELDS},
        "budget": record.get(FIELDS["budget"]) or 0,
        "active": bool(record.get(FIELDS["active"])),
        "source": record.get(FIELDS["source"]) or "chat",
    }

def _read(project_id, sf):
    if _real(sf):
        columns = ["Id"] + [
            FIELDS[key]
            for key in (*LINE_FIELDS, "budget", "active", "source", "revision")
        ]

        rows = sf.query(
            f"SELECT {', '.join(columns)} "
            f"FROM {config.SF_RESOURCE_OBJECT} "
            f"WHERE {FIELDS['project_id']} = '{project_id}' "
            "ORDER BY CreatedDate"
        )

        revision = max(
            [int(row.get(FIELDS["revision"]) or 0) for row in rows] + [1]
        )
        return [_from_sf(row) for row in rows], revision

    state = _state(project_id)
    with _LOCK:
        return [dict(row) for row in state["rows"]], state["revision"]

def _directory():
    return [dict(associate) for associate in getattr(masters, "DIRECTORY", [])]

def _associate(associate_id):
    finder = getattr(masters, "find_associate", None)
    if finder:
        return finder(associate_id)

    return next(
        (
            associate
            for associate in _directory()
            if associate.get("associate_id") == associate_id
        ),
        None,
    )

def context(project_id, sf=None):
    sf = sf or salesforce.make_client()
    resources, revision = _read(project_id, sf)
    project = _project(project_id)

    result = {
        "project_id": project_id,
        "revision": revision,
        "today": date.today().isoformat(),
        "project": project,
        "resources": resources,
        # Replace this with a real cross-project allocation source later.
        "allocations": [],
    }

    if os.getenv("RESOURCES_INCLUDE_DIRECTORY", "1") == "1":
        result["directory"] = [
            {
                "id": associate.get("associate_id"),
                "name": associate.get("name"),
                "email": associate.get("email"),
                "location": associate.get("location"),
                "status": associate.get("status"),
            }
            for associate in _directory()
        ]

    return result

def chat(project_id, message, chat_history):
    if not config.A5_AGENT_ID:
        raise NotConfigured("A5 agent is not configured (set A5_AGENT_ID in .env)")

    payload = context(project_id)
    payload.update(message=message, chat_history=chat_history)

    return agents.call_input_json(
        config.A5_AGENT_ID,
        {config.A5_INPUT_KEY: json.dumps(payload)},
    )

def _normalise_change(existing, change, index):
    operation = change.get("op")
    line_id = change.get("line_id")
    incoming = change.get("line") or {}

    if operation == "add":
        row = {key: incoming.get(key) for key in LINE_FIELDS}
        row["id"] = f"new-{index}"
        row["active"] = True
        row["source"] = "chat"

    elif operation in ("update", "deactivate"):
        if line_id not in existing:
            raise ValueError(f"Resource line '{line_id}' was not found")

        row = dict(existing[line_id])

        if operation == "deactivate":
            row["active"] = False
        else:
            row.update(
                {
                    key: value
                    for key, value in incoming.items()
                    if key in LINE_FIELDS and value is not None
                }
            )
    else:
        raise ValueError(f"Unknown resource operation '{operation}'")

    row["budget"] = round(
        _num(row.get("hours"), "hours") * _num(row.get("bill_rate"), "bill_rate"),
        2,
    )

    return operation, line_id, row

def _validate(ctx, changes):
    hits = []
    normalised = []

    existing = {row["id"]: dict(row) for row in ctx["resources"]}
    final = dict(existing)

    project = ctx["project"]
    project_start = _date(project["start_date"])
    project_finish = _date(project["finish_date"])

    for index, change in enumerate(changes or []):
        try:
            operation, old_id, row = _normalise_change(existing, change, index)
            line_id = old_id or row["id"]

            if operation == "add":
                missing = [
                    key for key in LINE_FIELDS
                    if row.get(key) in (None, "")
                ]

                if missing:
                    hits.append(
                        {
                            "rule": "RM-02",
                            "level": "BLOCK",
                            "line_id": line_id,
                            "message": "Missing: " + ", ".join(missing),
                        }
                    )
                    continue

            if row.get("active"):
                associate = _associate(row.get("associate_id"))

                if not associate or associate.get("status") != "active":
                    hits.append(
                        {
                            "rule": "RM-02",
                            "level": "BLOCK",
                            "line_id": line_id,
                            "message": "Associate must exist and be active.",
                        }
                    )
                    continue

                row["associate_id"] = associate["associate_id"]
                row["associate_name"] = associate["name"]

                start = _date(row["start"])
                end = _date(row["end"])

                if end < start or start < project_start or end > project_finish:
                    hits.append(
                        {
                            "rule": "RM-04",
                            "level": "BLOCK",
                            "line_id": line_id,
                            "message": (
                                f"Dates must be within {project_start} to {project_finish}."
                            ),
                        }
                    )
                    continue

                if (
                    _num(row["hours"], "hours") < 0
                    or _num(row["bill_rate"], "bill_rate") < 0
                    or _num(row["allocation_pct"], "allocation_pct") <= 0
                ):
                    hits.append(
                        {
                            "rule": "RM-04",
                            "level": "BLOCK",
                            "line_id": line_id,
                            "message": (
                                "Hours and bill rate must be non-negative; "
                                "allocation must be greater than zero."
                            ),
                        }
                    )
                    continue

            final[line_id] = row
            normalised.append(
                {"op": operation, "line_id": old_id, "line": row}
            )

        except ValueError as error:
            hits.append(
                {
                    "rule": "RM-04",
                    "level": "BLOCK",
                    "line_id": change.get("line_id") or f"new-{index}",
                    "message": str(error),
                }
            )

    for line_id, row in final.items():
        if not row.get("active"):
            continue

        other_allocation = sum(
            _num(allocation.get("allocation_pct") or 0, "allocation_pct")
            for allocation in ctx.get("allocations", [])
            if allocation.get("associate_id") == row.get("associate_id")
        )

        total_allocation = other_allocation + _num(
            row["allocation_pct"],
            "allocation_pct",
        )

        if total_allocation > 100:
            hits.append(
                {
                    "rule": "RM-03",
                    "level": "WARN",
                    "line_id": line_id,
                    "message": (
                        f"{row['associate_name']} is allocated "
                        f"{total_allocation:g}% ({other_allocation:g}% other projects + "
                        f"{row['allocation_pct']}% here)."
                    ),
                }
            )

    active = [row for row in final.values() if row.get("active")]
    unassigned = [row["id"] for row in active if not row.get("associate_id")]

    recomputed_total = round(
        sum(_num(row.get("budget") or 0, "budget") for row in active),
        2,
    )

    estimate_total = project["estimate_budget_total"]

    variance_pct = round(
        ((recomputed_total - estimate_total) / estimate_total * 100),
        2,
    ) if estimate_total else 0

    margin_flag = bool(
        estimate_total
        and abs(variance_pct) > project["tolerance_pct"]
    )

    if margin_flag:
        hits.append(
            {
                "rule": "RM-05",
                "level": "WARN",
                "line_id": "",
                "message": (
                    f"Resource budget variance is {variance_pct:g}% against the estimate; "
                    f"tolerance is {project['tolerance_pct']:g}%."
                ),
            }
        )

    budget_summary = {
        "recomputed_total": recomputed_total,
        "estimate_total": estimate_total,
        "variance_pct": variance_pct,
        "tolerance_pct": project["tolerance_pct"],
        "margin_flag": margin_flag,
    }

    return hits, normalised, budget_summary, unassigned

def validate(project_id, changes):
    ctx = context(project_id)
    hits, _, budget_summary, unassigned = _validate(ctx, changes)

    return {
        "ok": not any(hit["level"] == "BLOCK" for hit in hits),
        "hits": hits,
        "budget_summary": budget_summary,
        "unassigned_line_ids": unassigned,
        "revision": ctx["revision"],
    }

def _request_id(project_id, changes, request_id):
    if request_id:
        if not _REQUEST.match(request_id):
            raise ValueError(
                "request_id must be 8-64 letters, digits, '-' or '_'"
            )
        return request_id

    raw = json.dumps([project_id, changes], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]

def _write(sf, project_id, request_id, changes, revision):
    if _real(sf):
        requests = []

        for index, change in enumerate(changes):
            row = change["line"]

            if change["op"] == "add":
                data = {
                    **row,
                    "name": f"{row['role']} - {row['associate_name']}"[:80],
                    "project_id": project_id,
                    "request_key": f"{request_id}-{index}",
                    "revision": revision,
                }

                requests.append(
                    {
                        "method": "POST",
                        "object": config.SF_RESOURCE_OBJECT,
                        "body": _body(data),
                    }
                )

            else:
                requests.append(
                    {
                        "method": "PATCH",
                        "object": config.SF_RESOURCE_OBJECT,
                        "id": change["line_id"],
                        "body": _body({**row, "revision": revision}),
                    }
                )

        result = sf.composite(requests, all_or_none=True)

        return [
            {
                "op": change["op"],
                "line_id": response.get("id") or change["line_id"],
            }
            for change, response in zip(changes, result)
        ]

    state = _state(project_id)
    written = []

    with _LOCK:
        for change in changes:
            if change["op"] == "add":
                line_id = f"RS{next(_IDS):08d}"
                state["rows"].append({**change["line"], "id": line_id})
                written.append({"op": "add", "line_id": line_id})

            else:
                line_id = change["line_id"]
                row = next(row for row in state["rows"] if row["id"] == line_id)
                row.update(change["line"])
                written.append({"op": change["op"], "line_id": line_id})

        state["revision"] = revision

    return written

def apply(project_id, changes, request_id=None, expected_revision=None):
    if not changes:
        raise ValueError("No changes to apply")

    sf = salesforce.make_client()

    with _APPLY_LOCK:
        ctx = context(project_id, sf)

        if (
            expected_revision is not None
            and expected_revision != ctx["revision"]
        ):
            raise RevisionConflict(
                f"Resources changed since load (now revision {ctx['revision']}). "
                "Refresh and retry."
            )

        hits, normalised, budget_summary, unassigned = _validate(ctx, changes)

        if any(hit["level"] == "BLOCK" for hit in hits):
            raise ValidationFailed(hits)

        written = _write(
            sf,
            project_id,
            _request_id(project_id, changes, request_id),
            normalised,
            ctx["revision"] + 1,
        )

        resources, revision = _read(project_id, sf)

    return {
        "ok": True,
        "revision": revision,
        "resources": resources,
        "applied": written,
        "budget_summary": budget_summary,
        "unassigned_line_ids": unassigned,
        "warnings": [hit for hit in hits if hit["level"] != "BLOCK"],
        "salesforce": (
            "real"
            if _real(sf)
            else "local (Salesforce not configured)"
        ),
    }

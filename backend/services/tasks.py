"""Task Builder flow (A3): payload, agent call, server-side rules, Salesforce write-back.

A3 is a pure function: the host builds the full Task Chat Payload each turn,
calls the agent, and applies its "changes" only after the PM clicks Create /
Update. A3 never writes and keeps no memory.

Source of truth for tasks
  - real Salesforce: the JOSS_Task__c object (SF_TASK_OBJECT), read with SOQL
  - fake client (no SF credentials): an in-memory mirror, so the demo still works

Assignees and the project header are still stubs (swap behind context()).

Write safety (real mode)
  - Unique_Key__c = <opportunity id>|<lowercase name>  -> task names unique per project
  - Request_Key__c = <request_id>-<change index>      -> retries never create duplicates
  - Revision__c                                        -> bumped on every change set
  - every change set is re-validated here (TK-01..06); the host never trusts the agent alone
"""
import hashlib
import itertools
import json
import logging
import os
import re
import threading
from datetime import date

import config
from glean_client import agents
from . import salesforce

log = logging.getLogger(__name__)

# Task field -> Salesforce API name. Override per key with
# SF_TASK_FIELD_MAP='{"name":"Subject",...}' in .env.
SF_FIELD_MAP = {
    "name": "Name",
    "assignee_id": "Assignee_Id__c",
    "assignee_name": "Assignee_Name__c",
    "billable": "Billable__c",
    "active": "Active__c",
    "utilization": "Utilization__c",
    "start": "Start_Date__c",
    "end": "End_Date__c",
    "project_id": "Opportunity__c",
    "unique_key": "Unique_Key__c",
    "request_key": "Request_Key__c",
    "revision": "Revision__c",
}
try:
    SF_FIELD_MAP.update(json.loads(os.getenv("SF_TASK_FIELD_MAP", "") or "{}"))
except Exception:  # noqa: BLE001  (bad override should not crash the app)
    log.warning("SF_TASK_FIELD_MAP is not valid JSON: ignored")

TASK_FIELDS = ("name", "assignee_id", "billable", "active", "utilization", "start", "end")  # the 7 required
EDITABLE = set(TASK_FIELDS) | {"assignee_name"}
_READ_KEYS = TASK_FIELDS + ("assignee_name", "revision")

# TK-05: execution models whose tasks must all be non-billable (fill in when known).
FORCE_NON_BILLABLE_MODELS: set[str] = set()
# TK-04: level used when an assignee's peak utilisation exceeds their allocation.
# A6 treats it as WARN; the Task Builder design says "utilisation <= allocation", so BLOCK here.
TK04_PEAK_LEVEL = "BLOCK"

_SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

STUB_PROJECT = {
    "start_date": "2026-10-01",
    "finish_date": "2027-03-31",
    "execution_model": "Time & Materials",
    "default_billable": True,
}

STUB_ASSIGNEES = [
    {"associate_id": "A-100", "name": "Sara Khan", "email": "sara.khan@example.com",
     "source": "resource_pool", "role": "Project Manager",
     "allocation_start": "2026-10-01", "allocation_end": "2027-03-31", "allocation_pct": 100},
    {"associate_id": "A-101", "name": "Rahul Patil", "email": "rahul.patil@example.com",
     "source": "resource_pool", "role": "Developer",
     "allocation_start": "2026-10-01", "allocation_end": "2027-03-31", "allocation_pct": 100},
    {"associate_id": "A-102", "name": "Priya Nair", "email": "priya.nair@example.com",
     "source": "resource_pool", "role": "Data Engineer",
     "allocation_start": "2026-10-15", "allocation_end": "2027-02-28", "allocation_pct": 80},
    {"associate_id": "A-103", "name": "Amit Shah", "email": "amit.shah@example.com",
     "source": "resource_pool", "role": "QA Engineer",
     "allocation_start": "2026-11-01", "allocation_end": "2027-03-31", "allocation_pct": 50},
]

_LOCK = threading.RLock()
_APPLY_LOCK = threading.Lock()  # one apply at a time per process
_STORE: dict[str, dict] = {}  # fake mode only
_FAKE_IDS = itertools.count(1)

class NotConfigured(RuntimeError):
    pass

class ValidationFailed(Exception):
    def __init__(self, hits: list[dict]):
        super().__init__("Task changes break a rule")
        self.hits = hits

class RevisionConflict(Exception):
    pass

def _is_real(sf) -> bool:
    return isinstance(sf, salesforce.SalesforceClient)

def _store(project_id: str) -> dict:
    with _LOCK:
        return _STORE.setdefault(project_id, {"revision": 1, "tasks": [], "requests": {}})

def has_logged_hours(task_id: str) -> bool:
    """STUB: timesheet hours live in JOSS. Swap for a real lookup later.
    For tests/demo, MOCK_LOGGED_HOURS_TASK_IDS='id1,id2' marks tasks as having hours."""
    ids = {x.strip() for x in os.getenv("MOCK_LOGGED_HOURS_TASK_IDS", "").split(",") if x.strip()}
    return task_id in ids

# ---------- reading ----------

def _from_sf(rec: dict) -> dict:
    g = lambda k: rec.get(SF_FIELD_MAP[k])  # noqa: E731
    return {
        "task_id": rec["Id"],
        "name": g("name"),
        "assignee_id": g("assignee_id"),
        "assignee_name": g("assignee_name"),
        "billable": bool(g("billable")),
        "active": bool(g("active")),
        "utilization": int(g("utilization") or 0),
        "start": g("start"),
        "end": g("end"),
        "has_logged_hours": has_logged_hours(rec["Id"]),
    }

def _read(project_id: str, sf) -> tuple[list[dict], int]:
    """(tasks, current revision) for one project."""
    if _is_real(sf):
        if not _SF_ID_RE.match(project_id):
            raise ValueError("project_id is not a valid Salesforce Opportunity Id")
        cols = ["Id"] + sorted({SF_FIELD_MAP[k] for k in _READ_KEYS})
        soql = (f"SELECT {', '.join(cols)} FROM {config.SF_TASK_OBJECT} "
                f"WHERE {SF_FIELD_MAP['project_id']} = '{project_id}' ORDER BY CreatedDate")
        recs = sf.query(soql)
        revs = [int(r.get(SF_FIELD_MAP["revision"]) or 0) for r in recs]
        return [_from_sf(r) for r in recs], max(revs + [1])
    st = _store(project_id)
    with _LOCK:
        return [dict(t) for t in st["tasks"]], st["revision"]

def context(project_id: str, sf=None) -> dict:
    """Everything A3 needs about this project (minus the PM's message/history)."""
    tasks, revision = _read(project_id, sf or salesforce.make_client())
    return {
        "project_id": project_id,
        "revision": revision,
        "today": date.today().isoformat(),
        "project": dict(STUB_PROJECT),
        "assignees": [dict(a) for a in STUB_ASSIGNEES],
        "tasks": tasks,
    }

def chat(project_id: str, message: str, chat_history: list[dict]) -> dict:
    """Build the Task Chat Payload and run A3 on it."""
    if not config.A3_AGENT_ID:
        raise NotConfigured("A3 agent is not configured (set A3_AGENT_ID in .env)")
    payload = context(project_id)
    payload["message"] = message
    payload["chat_history"] = chat_history
    return agents.call_input_json(config.A3_AGENT_ID, {config.A3_INPUT_KEY: json.dumps(payload)})

# ---------- rules (TK-01 .. TK-06), enforced on the host ----------

def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "y", "yes", "1"):
        return True
    if s in ("false", "n", "no", "0"):
        return False
    raise ValueError(f"'{v}' is not Yes/No")

def _coerce(field: str, v):
    """Normalise one task field; empty -> None; bad value -> ValueError."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if field in ("billable", "active"):
        return _to_bool(v)
    if field == "utilization":
        f = float(v)
        if isinstance(v, bool) or f != int(f):
            raise ValueError(f"Utilization '{v}' must be a whole number")
        return int(f)
    if field in ("start", "end"):
        return date.fromisoformat(str(v).strip()).isoformat()
    return str(v).strip()

def _peak(tasks, assignee_id: str, s: date, e: date) -> int:
    """Highest total utilisation of this assignee on any day in [s, e]."""
    mine = [(date.fromisoformat(t["start"]), date.fromisoformat(t["end"]), t["utilization"])
            for t in tasks
            if t.get("active") and t.get("assignee_id") == assignee_id
            and t.get("start") and t.get("end") and t.get("utilization")]
    points = {s} | {a for a, _, _ in mine if s <= a <= e}
    return max(sum(u for a, b, u in mine if a <= p <= b) for p in points)

def _validate(ctx: dict, changes: list[dict]) -> tuple[list[dict], list[dict]]:
    """Return (rule hits, normalised changes). Normalised list lines up with `changes`
    whenever there is no BLOCK hit."""
    hits: list[dict] = []
    norm: list[dict] = []

    def hit(rule, task, msg, level="BLOCK"):
        hits.append({"rule": rule, "level": level, "task": task or "", "message": msg})

    proj = ctx["project"]
    pool = {a["associate_id"]: a for a in ctx["assignees"]}
    final = {t["task_id"]: dict(t) for t in ctx["tasks"]}  # state after the change set
    touched: list[str] = []

    for i, ch in enumerate(changes or []):
        op = ch.get("op")
        label = ch.get("task_name") or (ch.get("task") or {}).get("name") or ch.get("task_id")
        try:
            if op == "add":
                t = {k: _coerce(k, v) for k, v in (ch.get("task") or {}).items() if k in EDITABLE}
                t["active"] = True
                tid = f"new-{i}"
                final[tid] = {**t, "task_id": tid, "has_logged_hours": False}
                touched.append(tid)
                norm.append({"op": "add", "task_id": None, "task": t})
            elif op in ("update", "deactivate"):
                tid = ch.get("task_id")
                cur = final.get(tid)
                if cur is None:
                    hit("TK-00", label, "Task not found in this project.")
                    continue
                if op == "deactivate":
                    cur["active"] = False
                    norm.append({"op": "deactivate", "task_id": tid, "task_name": cur.get("name")})
                    continue
                delta = {}
                for k, v in (ch.get("changes") or {}).items():
                    if k not in EDITABLE:
                        hit("TK-00", label, f"Field '{k}' cannot be changed.")
                        continue
                    delta[k] = _coerce(k, v.get("to") if isinstance(v, dict) else v)
                if cur.get("has_logged_hours"):
                    hit("TK-06", cur.get("name"), "This task has logged timesheet hours, so it can only be deactivated.")
                    continue
                cur.update(delta)
                if cur.get("active"):
                    touched.append(tid)
                norm.append({"op": "update", "task_id": tid, "task_name": cur.get("name"), "changes": delta})
            else:
                hit("TK-00", label, f"Unknown operation '{op}'.")
        except ValueError as e:
            hit("TK-01", label, str(e))

    names: dict[str, int] = {}
    for t in final.values():
        key = (t.get("name") or "").strip().lower()
        names[key] = names.get(key, 0) + 1

    ps, pf = date.fromisoformat(proj["start_date"]), date.fromisoformat(proj["finish_date"])
    for tid in dict.fromkeys(touched):
        t = final[tid]
        label = t.get("name") or tid
        missing = [f for f in TASK_FIELDS if t.get(f) is None]
        if missing:
            hit("TK-01", label, "Missing: " + ", ".join(missing))
            continue
        if names[t["name"].strip().lower()] > 1:
            hit("TK-01", label, f"A task named '{t['name']}' already exists in this project (active or inactive).")
        a = pool.get(t["assignee_id"])
        if a is None:
            hit("TK-02", label, "Assignee is not in the project's assignee list.")
            continue
        s, e = date.fromisoformat(t["start"]), date.fromisoformat(t["end"])
        if e < s:
            hit("TK-03", label, "End date is before start date.")
            continue
        if s < ps or e > pf:
            hit("TK-03", label, f"Dates must be within the project ({ps} to {pf}).")
        a_s, a_e = date.fromisoformat(a["allocation_start"]), date.fromisoformat(a["allocation_end"])
        if s < a_s or e > a_e:
            hit("TK-03", label, f"Dates must be within {a['name']}'s allocation ({a_s} to {a_e}).")
        if not 1 <= t["utilization"] <= 100:
            hit("TK-04", label, "Utilization must be between 1 and 100.")
            continue
        peak = _peak(final.values(), t["assignee_id"], s, e)
        if peak > a["allocation_pct"]:
            hit("TK-04", label, f"{a['name']} would peak at {peak}% (allocation is {a['allocation_pct']}%).",
                TK04_PEAK_LEVEL)
        if t["billable"] and proj.get("execution_model") in FORCE_NON_BILLABLE_MODELS:
            hit("TK-05", label, f"{proj['execution_model']} projects must be non-billable.")
    return hits, norm

def validate(project_id: str, changes: list[dict]) -> dict:
    """Dry run of the rules for the UI (enable/disable the Create button)."""
    ctx = context(project_id)
    hits, _ = _validate(ctx, changes)
    return {"ok": not any(h["level"] == "BLOCK" for h in hits), "hits": hits, "revision": ctx["revision"]}

# ---------- writing ----------

def _sf_body(data: dict) -> dict:
    """Map task fields to Salesforce API names; skip unmapped keys."""
    return {SF_FIELD_MAP[k]: v for k, v in data.items() if k in SF_FIELD_MAP}

def _ukey(project_id: str, name: str) -> str:
    return f"{project_id}|{name.strip().lower()}"

def _request_id(project_id: str, changes: list[dict], given: str | None) -> str:
    if given:
        if not _REQUEST_ID_RE.match(given):
            raise ValueError("request_id must be 8-64 letters, digits, '-' or '_'")
        return given
    raw = json.dumps([project_id, changes], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:20]

def _applied_adds(sf, project_id: str, rid: str) -> dict[int, str]:
    """Adds of this request that already exist: {change index: task_id}."""
    if _is_real(sf):
        key = SF_FIELD_MAP["request_key"]
        recs = sf.query(f"SELECT Id, {key} FROM {config.SF_TASK_OBJECT} WHERE {key} LIKE '{rid}-%'")
        out = {}
        for r in recs:
            m = re.fullmatch(re.escape(rid) + r"-(\d+)", r.get(key) or "")
            if m:
                out[int(m.group(1))] = r["Id"]
        return out
    with _LOCK:
        return dict(_store(project_id)["requests"].get(rid, {}))

def _failure(e: salesforce.SalesforceError) -> dict:
    msg = e.detail
    if e.has_code("DUPLICATE_VALUE"):
        if SF_FIELD_MAP["request_key"] in msg:  # same change set sent twice
            m = re.search(r"id:\s*([A-Za-z0-9]{15,18})", msg)
            return {"status": "already_applied", "task_id": m.group(1) if m else None}
        if SF_FIELD_MAP["unique_key"] in msg:
            return {"status": "failed", "task_id": None,
                    "error": "TK-01: a task with this name already exists in this project."}
    return {"status": "failed", "task_id": None, "error": msg}

def _write(sf, project_id: str, rid: str, i: int, ch: dict, rev: int) -> dict:
    """Apply ONE normalised change. Returns {status, task_id, applied | error}."""
    real = _is_real(sf)
    op, tid = ch["op"], ch.get("task_id")
    try:
        if op == "add":
            t = ch["task"]
            if real:
                tid = sf.create_sobject(config.SF_TASK_OBJECT, _sf_body({
                    **t, "project_id": project_id, "active": True,
                    "unique_key": _ukey(project_id, t["name"]),
                    "request_key": f"{rid}-{i}", "revision": rev}))
            else:
                tid = f"TA{next(_FAKE_IDS):08d}"
                log.info("Salesforce not configured: task %s staged locally", tid)
                with _LOCK:
                    st = _store(project_id)
                    st["tasks"].append({**t, "task_id": tid, "has_logged_hours": False})
                    st["requests"].setdefault(rid, {})[i] = tid
            applied = {"op": "add", "task_id": tid, "name": t["name"]}
        elif op == "update":
            delta = ch["changes"]
            if real and delta:
                extra = {"revision": rev}
                if "name" in delta:
                    extra["unique_key"] = _ukey(project_id, delta["name"])
                sf.update_sobject(config.SF_TASK_OBJECT, tid, _sf_body({**delta, **extra}))
            if not real:
                with _LOCK:
                    next(x for x in _store(project_id)["tasks"] if x["task_id"] == tid).update(delta)
            applied = {"op": "update", "task_id": tid, "fields": sorted(delta)}
        else:  # deactivate (tasks are never deleted)
            if real:
                sf.update_sobject(config.SF_TASK_OBJECT, tid, _sf_body({"active": False, "revision": rev}))
            else:
                with _LOCK:
                    next(x for x in _store(project_id)["tasks"] if x["task_id"] == tid)["active"] = False
            applied = {"op": "deactivate", "task_id": tid}
    except salesforce.SalesforceError as e:
        return _failure(e)
    return {"status": "ok", "task_id": tid, "applied": applied}

def apply(project_id: str, changes: list[dict], request_id: str | None = None,
          expected_revision: int | None = None) -> dict:
    """Validate, then write A3's ready changes one by one.

    Raises ValidationFailed (rule broken), RevisionConflict (tasks changed since the
    PM's screen loaded) or salesforce.SalesforceError (read failed). Write failures are
    reported per change in result["results"] (ok / already_applied / failed / not_attempted).
    """
    if not changes:
        raise ValueError("No changes to apply")
    sf = salesforce.make_client()
    with _APPLY_LOCK:
        ctx = context(project_id, sf)
        rid = _request_id(project_id, changes, request_id)
        done = _applied_adds(sf, project_id, rid)  # retry of an earlier call?
        if not done and expected_revision is not None and expected_revision != ctx["revision"]:
            raise RevisionConflict(
                f"Tasks changed since you loaded them (now revision {ctx['revision']}). Refresh and retry.")

        pending_idx = [i for i in range(len(changes)) if i not in done]
        hits, norm = _validate(ctx, [changes[i] for i in pending_idx])
        if any(h["level"] == "BLOCK" for h in hits):
            raise ValidationFailed(hits)
        norm_by_idx = dict(zip(pending_idx, norm))

        rev = ctx["revision"] if done else ctx["revision"] + 1
        results, applied, failed = [], [], False
        for i, ch in enumerate(changes):
            op = ch.get("op")
            if i in done:
                results.append({"i": i, "op": "add", "status": "already_applied", "task_id": done[i]})
                continue
            if failed:
                results.append({"i": i, "op": op, "status": "not_attempted", "task_id": ch.get("task_id")})
                continue
            res = _write(sf, project_id, rid, i, norm_by_idx[i], rev)
            results.append({"i": i, "op": op, "status": res["status"], "task_id": res.get("task_id"),
                            **({"error": res["error"]} if res.get("error") else {})})
            if res["status"] == "ok":
                applied.append(res["applied"])
            elif res["status"] == "failed":
                failed = True

        if applied and not _is_real(sf):
            with _LOCK:
                _store(project_id)["revision"] = rev
        tasks, revision = _read(project_id, sf)
    return {
        "ok": not failed,
        "request_id": rid,
        "revision": revision,
        "tasks": tasks,
        "applied": applied,
        "results": results,
        "salesforce": "real" if _is_real(sf) else "local (Salesforce not configured)",
    }

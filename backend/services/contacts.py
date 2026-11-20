"""Jade Contacts flow (A4): payload, agent call, server-side rules, Salesforce write-back.

A4 is a pure function: the host builds the full Contacts Chat Payload each turn, calls the
agent, and writes its "changes" only after the PM clicks Create / Update. A4 never writes.

Source of truth for role holders
  - real Salesforce: the JOSS_Contact__c object (SF_CONTACT_OBJECT), read with SOQL
  - fake client (no SF credentials): an in-memory mirror, so the demo still works

Write safety (real mode)
  - the WHOLE change set goes in ONE Salesforce composite call with allOrNone=true,
    so a handover (old holder's end date + new holder) is applied as one unit or not at all
  - Request_Key__c = <request_id>-<change index> on every new row -> retries never duplicate
  - Revision__c bumped on every change set
  - every change set is re-validated here (JC-01..JC-05); the host never trusts the agent alone
"""
import hashlib
import itertools
import json
import logging
import os
import re
import threading
from datetime import date, timedelta

import config
from glean_client import agents
from . import masters, salesforce
from .tasks import STUB_PROJECT  # same project header as the Task Builder (stub for now)

log = logging.getLogger(__name__)

SF_FIELD_MAP = {
    "name": "Name",
    "role": "Role__c",
    "associate_id": "Associate_Id__c",
    "associate_name": "Associate_Name__c",
    "start": "Start_Date__c",
    "end": "End_Date__c",
    "active": "Active__c",
    "source": "Source__c",
    "project_id": "Opportunity__c",
    "request_key": "Request_Key__c",
    "revision": "Revision__c",
}
try:
    SF_FIELD_MAP.update(json.loads(os.getenv("SF_CONTACT_FIELD_MAP", "") or "{}"))
except Exception:  # noqa: BLE001
    log.warning("SF_CONTACT_FIELD_MAP is not valid JSON: ignored")

_READ_KEYS = ("role", "associate_id", "associate_name", "start", "end", "active", "source", "revision")
ADD_REQUIRED = ("role", "associate_id", "start", "end")
UPDATABLE = ("start", "end", "source")
SOURCES = ("chat", "handover")

_SF_ID_RE = re.compile(r"^[A-Za-z0-9]{15}([A-Za-z0-9]{3})?$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

_LOCK = threading.RLock()
_APPLY_LOCK = threading.Lock()
_STORE: dict[str, dict] = {}  # fake mode only
_FAKE_IDS = itertools.count(1)

class NotConfigured(RuntimeError):
    pass

class ValidationFailed(Exception):
    def __init__(self, hits: list[dict]):
        super().__init__("Contact changes break a rule")
        self.hits = hits

class RevisionConflict(Exception):
    pass

def _is_real(sf) -> bool:
    return isinstance(sf, salesforce.SalesforceClient)

def _store(project_id: str) -> dict:
    with _LOCK:
        return _STORE.setdefault(project_id, {"revision": 1, "contacts": [], "requests": {}})

def _extract_json(raw: str | dict) -> dict:
    """Extract and parse the first valid JSON object from agent output.
    Safely ignores leading markdown code fences and trailing extra commentary/data."""
    if isinstance(raw, dict):
        return raw
    s = (raw or "").strip()
    if not s:
        raise ValueError("Agent returned empty response")

    # Strip code block fences if wrapped
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\s*```$", "", s)
        s = s.strip()

    # Drop the agent's echo of the input form, if present:
    # <user_inputs><field_value ...>{payload}</field_value></user_inputs>{reply}
    s = re.sub(r"<user_inputs>.*?</user_inputs>", "", s, flags=re.DOTALL).strip()

    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response: {s[:120]}")

    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(s[start:])
    if not isinstance(obj, dict):
        raise ValueError("Agent response root must be a JSON object")
    return obj

# ---------- reading ----------

def _from_sf(rec: dict) -> dict:
    g = lambda k: rec.get(SF_FIELD_MAP[k])  # noqa: E731
    return {
        "id": rec["Id"],
        "role": g("role"),
        "associate_id": g("associate_id"),
        "associate_name": g("associate_name"),
        "start": g("start"),
        "end": g("end"),
        "active": bool(g("active")),
        "source": g("source") or "chat",
    }

def _read(project_id: str, sf) -> tuple[list[dict], int]:
    """(contacts, current revision) for one project."""
    if _is_real(sf):
        if not _SF_ID_RE.match(project_id):
            raise ValueError("project_id is not a valid Salesforce Opportunity Id")
        cols = ["Id"] + sorted({SF_FIELD_MAP[k] for k in _READ_KEYS})
        soql = (f"SELECT {', '.join(cols)} FROM {config.SF_CONTACT_OBJECT} "
                f"WHERE {SF_FIELD_MAP['project_id']} = '{project_id}' ORDER BY CreatedDate")
        recs = sf.query(soql)
        revs = [int(r.get(SF_FIELD_MAP["revision"]) or 0) for r in recs]
        return [_from_sf(r) for r in recs], max(revs + [1])
    st = _store(project_id)
    with _LOCK:
        return [dict(c) for c in st["contacts"]], st["revision"]

def context(project_id: str, sf=None) -> dict:
    """Everything A4 needs about this project (minus the PM's message/history)."""
    contacts, revision = _read(project_id, sf or salesforce.make_client())
    ctx = {
        "project_id": project_id,
        "revision": revision,
        "today": date.today().isoformat(),
        "project": {"start_date": STUB_PROJECT["start_date"], "finish_date": STUB_PROJECT["finish_date"]},
        "contacts": contacts,
        "role_master": [dict(r) for r in masters.ROLE_MASTER],
        "handover_rule": dict(masters.HANDOVER_RULE),
    }
    # Test only. In production leave this out: the A4 agent uses its Employee Search tool.
    if os.getenv("CONTACTS_INCLUDE_DIRECTORY", "1") == "1":
        ctx["directory"] = [dict(a) for a in masters.DIRECTORY]
    return ctx

def chat(project_id: str, message: str, chat_history: list[dict]) -> dict:
    """Build the Contacts Chat Payload and run A4 on it."""
    if not config.A4_AGENT_ID:
        raise NotConfigured("A4 agent is not configured (set A4_AGENT_ID in .env)")
    payload = context(project_id)
    payload["message"] = message
    payload["chat_history"] = chat_history
    raw = agents.call_input_json(config.A4_AGENT_ID, {config.A4_INPUT_KEY: json.dumps(payload)})
    return _extract_json(raw)

# ---------- rules (JC-01 .. JC-05), enforced on the host ----------

def _d(v) -> date:
    return date.fromisoformat(str(v).strip())

def _role(name) -> dict | None:
    n = (name or "").strip().lower()
    return next((r for r in masters.ROLE_MASTER if r["role"].lower() == n), None)

def uncovered_pm_ranges(contacts, start: date, finish: date) -> list[dict]:
    """Date ranges in [start, finish] with no active Project Manager (JC-05)."""
    spans = sorted((_d(c["start"]), _d(c["end"])) for c in contacts
                   if c.get("active") and c.get("role") == masters.PM_ROLE and c.get("start") and c.get("end"))
    gaps, cur = [], start
    for s, e in spans:
        if e < cur:
            continue
        if s > cur:
            gaps.append({"from": cur.isoformat(), "to": min(s - timedelta(days=1), finish).isoformat()})
        cur = max(cur, e + timedelta(days=1))
        if cur > finish:
            break
    if cur <= finish:
        gaps.append({"from": cur.isoformat(), "to": finish.isoformat()})
    return gaps

def _validate(ctx: dict, changes: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (rule hits, normalised changes, uncovered PM ranges).
    The normalised list lines up with `changes` whenever there is no BLOCK hit."""
    hits: list[dict] = []
    norm: list[dict] = []

    def hit(rule, who, msg, level="BLOCK"):
        hits.append({"rule": rule, "level": level, "contact": who or "", "message": msg})

    ps, pf = _d(ctx["project"]["start_date"]), _d(ctx["project"]["finish_date"])
    final = {c["id"]: dict(c) for c in ctx["contacts"]}
    touched: list[str] = []

    for i, ch in enumerate(changes or []):
        op, c = ch.get("op"), ch.get("contact") or {}
        label = c.get("associate_name") or ch.get("contact_id") or f"change {i + 1}"
        try:
            if op == "add":
                role = _role(c.get("role"))
                if role is None:
                    valid = ", ".join(r["role"] for r in masters.ROLE_MASTER)
                    hit("JC-00", label, f"Role '{c.get('role')}' is not valid. Valid roles: {valid}.")
                    continue
                missing = [f for f in ADD_REQUIRED if not c.get(f)]
                if missing:
                    hit("JC-00", label, "Missing: " + ", ".join(missing))
                    continue
                a = masters.find_associate(c["associate_id"])
                if a is None or a.get("status") != "active":
                    hit("JC-04", label, "Associate must exist in the directory and be active.")
                    continue
                src = c.get("source") or "chat"
                if src not in SOURCES:
                    hit("JC-00", label, f"Unknown source '{src}'.")
                    continue
                row = {"role": role["role"], "associate_id": a["associate_id"], "associate_name": a["name"],
                       "start": _d(c["start"]).isoformat(), "end": _d(c["end"]).isoformat(),
                       "active": True, "source": src}
                tid = f"new-{i}"
                final[tid] = {**row, "id": tid}
                touched.append(tid)
                norm.append({"op": "add", "contact_id": None, "contact": row})
            elif op in ("update", "deactivate"):
                cid = ch.get("contact_id")
                cur = final.get(cid)
                if cur is None or str(cid).startswith("new-"):
                    hit("JC-00", label, "Contact not found in this project.")
                    continue
                if op == "deactivate":
                    cur["active"] = False
                    norm.append({"op": "deactivate", "contact_id": cid})
                    continue
                if (c.get("associate_id") and c["associate_id"] != cur["associate_id"]) or \
                        (c.get("role") and c["role"].strip().lower() != (cur["role"] or "").lower()):
                    hit("JC-00", label, "An update cannot change the role or the person. End this holder and add a new one.")
                    continue
                delta = {}
                for k in UPDATABLE:
                    if c.get(k) is not None and c[k] != "":
                        v = _d(c[k]).isoformat() if k in ("start", "end") else str(c[k])
                        if k == "source" and v not in SOURCES:
                            hit("JC-00", label, f"Unknown source '{v}'.")
                        elif v != cur.get(k):
                            delta[k] = v
                cur.update(delta)
                touched.append(cid)
                norm.append({"op": "update", "contact_id": cid, "changes": delta,
                             "handover": delta.get("source") == "handover" or cur.get("source") == "handover"})
            else:
                hit("JC-00", label, f"Unknown operation '{op}'.")
        except (ValueError, TypeError) as e:
            hit("JC-00", label, f"Invalid value: {e}")

    for cid in dict.fromkeys(touched):
        c = final[cid]
        if not c.get("active"):
            continue
        label = c.get("associate_name") or cid
        s, e = _d(c["start"]), _d(c["end"])
        if e < s:
            rule = "JC-03" if c.get("source") == "handover" else "JC-02"
            hit(rule, label, f"End date {e} is before start date {s}."
                + (" Suggest a later start for the new holder, or remove the previous holder." if rule == "JC-03" else ""))
            continue
        if s < ps or e > pf:
            hit("JC-02", label, f"Dates must be within the project ({ps} to {pf}).")

    for role in {final[cid]["role"] for cid in touched if final[cid].get("active")}:
        r = _role(role)
        if not (r and r["single_holder"]):
            continue
        rows = sorted((_d(c["start"]), _d(c["end"]), c.get("associate_name"))
                      for c in final.values()
                      if c.get("active") and c.get("role") == role and _d(c["end"]) >= _d(c["start"]))
        latest_end, latest_name = None, None
        for s2, e2, n2 in rows:
            if latest_end is not None and s2 <= latest_end:
                hit("JC-01", role, f"{role} has two holders on {s2}: {latest_name} and {n2}. "
                    "A handover must end the previous holder the day before the new start.")
                break
            if latest_end is None or e2 > latest_end:
                latest_end, latest_name = e2, n2

    gaps = uncovered_pm_ranges(final.values(), ps, pf)
    if gaps:
        hit("JC-05", masters.PM_ROLE, "No Project Manager on: " + ", ".join(f"{g['from']} to {g['to']}" for g in gaps)
            + ". This blocks submit.", "WARN")
    return hits, norm, gaps

def validate(project_id: str, changes: list[dict]) -> dict:
    """Dry run of the rules for the UI (enable/disable the Create button)."""
    ctx = context(project_id)
    hits, _, gaps = _validate(ctx, changes)
    return {"ok": not any(h["level"] == "BLOCK" for h in hits), "hits": hits,
            "uncovered_pm_ranges": gaps, "revision": ctx["revision"]}

# ---------- writing ----------

def _sf_body(data: dict) -> dict:
    return {SF_FIELD_MAP[k]: v for k, v in data.items() if k in SF_FIELD_MAP}

def _request_id(project_id: str, changes: list[dict], given: str | None) -> str:
    if given:
        if not _REQUEST_ID_RE.match(given):
            raise ValueError("request_id must be 8-64 letters, digits, '-' or '_'")
        return given
    return hashlib.sha1(json.dumps([project_id, changes], sort_keys=True, default=str).encode()).hexdigest()[:20]

def _applied_adds(sf, project_id: str, rid: str) -> dict[int, str]:
    """Adds of this request that already exist: {change index: contact id}."""
    if _is_real(sf):
        key = SF_FIELD_MAP["request_key"]
        recs = sf.query(f"SELECT Id, {key} FROM {config.SF_CONTACT_OBJECT} WHERE {key} LIKE '{rid}-%'")
        out = {}
        for r in recs:
            m = re.fullmatch(re.escape(rid) + r"-(\d+)", r.get(key) or "")
            if m:
                out[int(m.group(1))] = r["Id"]
        return out
    with _LOCK:
        return dict(_store(project_id)["requests"].get(rid, {}))

def _record_name(c: dict) -> str:
    return f"{c['role']} - {c['associate_name']}"[:80]

def _write_all(sf, project_id: str, rid: str, norm: list[dict], rev: int) -> list[dict]:
    """Write the whole change set as ONE unit. Returns per-change {op, contact_id}."""
    out = []
    if _is_real(sf):
        subs = []
        for i, ch in enumerate(norm):
            if ch["op"] == "add":
                body = _sf_body({**ch["contact"], "name": _record_name(ch["contact"]), "active": True,
                                 "project_id": project_id, "request_key": f"{rid}-{i}", "revision": rev})
                subs.append({"method": "POST", "object": config.SF_CONTACT_OBJECT, "body": body})
            elif ch["op"] == "update":
                subs.append({"method": "PATCH", "object": config.SF_CONTACT_OBJECT, "id": ch["contact_id"],
                             "body": _sf_body({**ch["changes"], "revision": rev})})
            else:
                subs.append({"method": "PATCH", "object": config.SF_CONTACT_OBJECT, "id": ch["contact_id"],
                             "body": _sf_body({"active": False, "revision": rev})})
        res = sf.composite(subs, all_or_none=True)
        for ch, r in zip(norm, res):
            cid = r.get("id") or ch.get("contact_id")
            out.append({"op": ch["op"], "contact_id": cid})
        return out
    with _LOCK:
        st = _store(project_id)
        for i, ch in enumerate(norm):
            if ch["op"] == "add":
                cid = f"CO{next(_FAKE_IDS):08d}"
                st["contacts"].append({**ch["contact"], "id": cid})
                st["requests"].setdefault(rid, {})[i] = cid
            else:
                cid = ch["contact_id"]
                row = next(x for x in st["contacts"] if x["id"] == cid)
                row.update(ch["changes"] if ch["op"] == "update" else {"active": False})
            out.append({"op": ch["op"], "contact_id": cid})
        st["revision"] = rev
    return out

def apply(project_id: str, changes: list[dict], request_id: str | None = None,
          expected_revision: int | None = None) -> dict:
    """Validate, then write the whole change set atomically.

    Raises ValidationFailed (JC rule broken), RevisionConflict (contacts changed since the
    PM's screen loaded) or salesforce.SalesforceError (read or write failed; nothing saved).
    JC-05 (PM coverage) is only a WARN here and is returned as uncovered_pm_ranges.
    """
    if not changes:
        raise ValueError("No changes to apply")
    sf = salesforce.make_client()
    with _APPLY_LOCK:
        ctx = context(project_id, sf)
        rid = _request_id(project_id, changes, request_id)
        done = _applied_adds(sf, project_id, rid)  # retry of a change set that already went through?
        if done:
            contacts, revision = _read(project_id, sf)
            return {"ok": True, "request_id": rid, "revision": revision, "contacts": contacts, "applied": [],
                    "results": [{"i": i, "op": ch.get("op"), "status": "already_applied",
                                 "contact_id": done.get(i, ch.get("contact_id"))} for i, ch in enumerate(changes)],
                    "uncovered_pm_ranges": uncovered_pm_ranges(contacts, _d(ctx["project"]["start_date"]),
                                                               _d(ctx["project"]["finish_date"])),
                    "salesforce": "real" if _is_real(sf) else "local (Salesforce not configured)"}
        if expected_revision is not None and expected_revision != ctx["revision"]:
            raise RevisionConflict(
                f"Contacts changed since you loaded them (now revision {ctx['revision']}). Refresh and retry.")
        hits, norm, gaps = _validate(ctx, changes)
        if any(h["level"] == "BLOCK" for h in hits):
            raise ValidationFailed(hits)
        written = _write_all(sf, project_id, rid, norm, ctx["revision"] + 1)
        contacts, revision = _read(project_id, sf)
    return {
        "ok": True,
        "request_id": rid,
        "revision": revision,
        "contacts": contacts,
        "applied": written,
        "results": [{"i": i, "op": w["op"], "status": "ok", "contact_id": w["contact_id"]} for i, w in enumerate(written)],
        "uncovered_pm_ranges": gaps,
        "warnings": [h for h in hits if h["level"] != "BLOCK"],
        "salesforce": "real" if _is_real(sf) else "local (Salesforce not configured)",
    }
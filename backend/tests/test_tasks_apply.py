"""Server-side rules + Salesforce write path for the Task Builder (no network)."""
import pytest

import  config
from services import salesforce, tasks as svc

PID = "006bm00000ZZFsr"  # 15-char Salesforce-style id

class StubSF(salesforce.SalesforceClient):
    """In-memory JOSS_Task__c that enforces the two unique keys like Salesforce does."""

    def __init__(self):
        super().__init__("https://x.my.salesforce.com", access_token="t")
        self.rows, self.n = {}, 0

    def create_sobject(self, obj, body):
        for r in self.rows.values():
            for f in ("Unique_Key__c", "Request_Key__c"):
                if body.get(f) and r.get(f) == body[f]:
                    raise salesforce.SalesforceError("dup", 400, [{
                        "errorCode": "DUPLICATE_VALUE",
                        "message": f"duplicate value found: {f} duplicates value on record with id: {r['Id']}"}])
        self.n += 1
        rid = f"a0X{self.n:012d}"
        self.rows[rid] = {"Id": rid, **body}
        return rid

    def update_sobject(self, obj, rid, body):
        self.rows[rid].update(body)

    def query(self, soql):
        if "LIKE" in soql:
            prefix = soql.split("LIKE '")[1].split("%")[0]
            return [{"Id": r["Id"], "Request_Key__c": r.get("Request_Key__c")}
                    for r in self.rows.values() if (r.get("Request_Key__c") or "").startswith(prefix)]
        pid = soql.split("= '")[1].split("'")[0]
        return [dict(r) for r in self.rows.values() if r.get("Opportunity__c") == pid]

def _add(name="Data Migration", who="A-101", util=50, start="2026-11-01", end="2026-12-15", billable=True):
    return {"op": "add", "task_id": None, "task": {
        "name": name, "assignee_id": who, "assignee_name": "x", "billable": billable, "active": True,
        "utilization": util, "start": start, "end": end}}

def _real(monkeypatch):
    sf = StubSF()
    monkeypatch.setattr(svc.salesforce, "make_client", lambda: sf)
    monkeypatch.setattr(config, "SF_TASK_OBJECT", "JOSS_Task__c")
    return sf

def _rules(exc):
    return {h["rule"] for h in exc.value.hits}

def test_add_writes_keys_and_revision(monkeypatch):
    sf = _real(monkeypatch)
    res = svc.apply(PID, [_add()], request_id="req-0001-aaaa", expected_revision=1)
    assert res["ok"] and res["revision"] == 2
    row = next(iter(sf.rows.values()))
    assert row["Unique_Key__c"] == f"{PID}|data migration"
    assert row["Request_Key__c"] == "req-0001-aaaa-0"
    assert row["Opportunity__c"] == PID and row["Revision__c"] == 2 and row["Active__c"] is True

def test_retry_with_same_request_id_creates_nothing_new(monkeypatch):
    sf = _real(monkeypatch)
    svc.apply(PID, [_add()], request_id="req-0001-aaaa")
    res = svc.apply(PID, [_add()], request_id="req-0001-aaaa", expected_revision=1)  # stale rev is fine on retry
    assert res["results"][0]["status"] == "already_applied"
    assert len(sf.rows) == 1

def test_duplicate_name_case_insensitive_blocked(monkeypatch):
    _real(monkeypatch)
    svc.apply(PID, [_add()], request_id="req-0001-aaaa")
    with pytest.raises(svc.ValidationFailed) as e:
        svc.apply(PID, [_add(name="data migration", who="A-100")], request_id="req-0002-aaaa")
    assert "TK-01" in _rules(e)

def test_two_adds_same_name_in_one_message_blocked(monkeypatch):
    _real(monkeypatch)
    with pytest.raises(svc.ValidationFailed) as e:
        svc.apply(PID, [_add(name="Build"), _add(name="BUILD", who="A-100")], request_id="req-0003-aaaa")
    assert "TK-01" in _rules(e)

def test_missing_field_blocked(monkeypatch):
    _real(monkeypatch)
    ch = _add()
    del ch["task"]["end"]
    with pytest.raises(svc.ValidationFailed) as e:
        svc.apply(PID, [ch], request_id="req-0004-aaaa")
    assert "TK-01" in _rules(e)

def test_unknown_assignee_blocked(monkeypatch):
    _real(monkeypatch)
    with pytest.raises(svc.ValidationFailed) as e:
        svc.apply(PID, [_add(who="A-999")], request_id="req-0005-aaaa")
    assert "TK-02" in _rules(e)

def test_dates_outside_assignee_window_blocked(monkeypatch):
    _real(monkeypatch)
    with pytest.raises(svc.ValidationFailed) as e:  # Priya starts 2026-10-15
        svc.apply(PID, [_add(who="A-102", start="2026-10-01")], request_id="req-0006-aaaa")
    assert "TK-03" in _rules(e)

def test_peak_utilisation_over_allocation_blocked(monkeypatch):
    _real(monkeypatch)
    svc.apply(PID, [_add(util=60)], request_id="req-0007-aaaa")
    with pytest.raises(svc.ValidationFailed) as e:  # 60 + 50 = 110 > 100
        svc.apply(PID, [_add(name="Build", util=50)], request_id="req-0008-aaaa")
    assert "TK-04" in _rules(e)

def test_stale_revision_conflict(monkeypatch):
    _real(monkeypatch)
    svc.apply(PID, [_add()], request_id="req-0009-aaaa")
    with pytest.raises(svc.RevisionConflict):
        svc.apply(PID, [_add(name="Build", who="A-100")], request_id="req-0010-aaaa", expected_revision=1)

def test_rename_updates_unique_key_and_revision(monkeypatch):
    sf = _real(monkeypatch)
    res = svc.apply(PID, [_add()], request_id="req-0011-aaaa")
    tid = res["tasks"][0]["task_id"]
    svc.apply(PID, [{"op": "update", "task_id": tid, "task_name": "Data Migration",
                     "changes": {"name": {"from": "Data Migration", "to": "Data Move"}}}],
              request_id="req-0012-aaaa", expected_revision=2)
    assert sf.rows[tid]["Name"] == "Data Move"
    assert sf.rows[tid]["Unique_Key__c"] == f"{PID}|data move" and sf.rows[tid]["Revision__c"] == 3

def test_logged_hours_task_can_only_be_deactivated(monkeypatch):
    sf = _real(monkeypatch)
    tid = svc.apply(PID, [_add()], request_id="req-0013-aaaa")["tasks"][0]["task_id"]
    monkeypatch.setenv("MOCK_LOGGED_HOURS_TASK_IDS", tid)
    with pytest.raises(svc.ValidationFailed) as e:
        svc.apply(PID, [{"op": "update", "task_id": tid, "changes": {"end": {"to": "2026-12-20"}}}],
                  request_id="req-0014-aaaa")
    assert "TK-06" in _rules(e)
    res = svc.apply(PID, [{"op": "deactivate", "task_id": tid, "task_name": "Data Migration"}],
                    request_id="req-0015-aaaa")
    assert res["ok"] and sf.rows[tid]["Active__c"] is False  # deactivated, never deleted

def test_salesforce_failure_marks_rest_not_attempted(monkeypatch):
    sf = _real(monkeypatch)
    orig = sf.create_sobject
    calls = {"n": 0}

    def flaky(obj, body):
        calls["n"] += 1
        if calls["n"] == 2:
            raise salesforce.SalesforceError("x", 400, [{"errorCode": "FIELD_CUSTOM_VALIDATION_EXCEPTION",
                                                          "message": "End date must be on or after start date."}])
        return orig(obj, body)

    sf.create_sobject = flaky
    res = svc.apply(PID, [_add(name="A", who="A-100"), _add(name="B", who="A-101"), _add(name="C", who="A-103", util=10)],
                    request_id="req-0016-aaaa")
    assert [r["status"] for r in res["results"]] == ["ok", "failed", "not_attempted"]
    assert res["ok"] is False and len(res["applied"]) == 1

def test_fake_mode_still_works(monkeypatch):
    monkeypatch.setattr(svc.salesforce, "make_client", salesforce.FakeSalesforce)
    svc._STORE.clear()
    res = svc.apply("PROJ-FAKE", [_add()], request_id="req-0017-aaaa")
    assert res["ok"] and res["salesforce"].startswith("local") and res["revision"] == 2
    again = svc.apply("PROJ-FAKE", [_add()], request_id="req-0017-aaaa")
    assert again["results"][0]["status"] == "already_applied" and len(again["tasks"]) == 1

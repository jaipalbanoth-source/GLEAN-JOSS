"""Tests for the resource estimation lookup (lenient JSON parsing of SF fields)."""
import json

from services import resource_estimation as svc

OID = "006FAKE00000001"  # 15-char: 006 + 12


class FakeSF:
    def __init__(self, record):
        self.record = record

    def query(self, soql):
        return [self.record] if self.record else []


def rec(**kw):
    base = {
        "Id": OID, "Name": "Oracle Rollout", "Account": {"Name": "Acme"},
        "StageName": "Closed Won", "Amount": 100000,
    }
    base.update(kw)
    return base


def test_valid_object_fields():
    out = svc.lookup(OID, FakeSF(rec(
        Estimation_Details__c=json.dumps({"groups": [{"resource_code": "DEV", "count": 2}]}),
        Resource_Lines__c=json.dumps({"lines": [{"associate_name": "Priya", "line_number": 1}]}),
    )))
    assert out["estimation"]["groups"][0]["resource_code"] == "DEV"
    assert out["resource_allocation"]["lines"][0]["associate_name"] == "Priya"
    assert out["fields"]["Resource_Lines__c"]["ok"] is True
    assert out["warnings"] == []


def test_bare_list_is_wrapped():
    out = svc.lookup(OID, FakeSF(rec(
        Resource_Lines__c='[{"associate_name": "Rahul", "line_number": 1}]',
        Estimation_Details__c=None,
    )))
    assert out["resource_allocation"]["lines"][0]["associate_name"] == "Rahul"
    info = out["fields"]["Resource_Lines__c"]
    assert info["ok"] is True
    assert "bare JSON list" in info["note"]
    assert out["estimation"]["groups"] == []
    assert out["fields"]["Estimation_Details__c"]["note"] == "empty"


def test_double_encoded_string():
    inner = json.dumps({"lines": [{"associate_name": "Amit"}]})
    out = svc.lookup(OID, FakeSF(rec(Resource_Lines__c=json.dumps(inner))))
    assert out["resource_allocation"]["lines"][0]["associate_name"] == "Amit"
    assert "double-encoded" in out["fields"]["Resource_Lines__c"]["note"]


def test_smart_quotes_and_trailing_comma():
    raw = "{\u201clines\u201d: [{\u201cassociate_name\u201d: \u201cNeha\u201d},]}"
    out = svc.lookup(OID, FakeSF(rec(Resource_Lines__c=raw)))
    assert out["resource_allocation"]["lines"][0]["associate_name"] == "Neha"


def test_single_quoted_json():
    raw = "[{'associate_name': 'Sara', 'line_number': 2}]"
    out = svc.lookup(OID, FakeSF(rec(Resource_Lines__c=raw)))
    assert out["resource_allocation"]["lines"][0]["associate_name"] == "Sara"


def test_invalid_json_reports_error_and_preview():
    raw = "not json {oops"
    out = svc.lookup(OID, FakeSF(rec(Resource_Lines__c=raw)))
    info = out["fields"]["Resource_Lines__c"]
    assert info["ok"] is False
    assert "column" in info["error"]
    assert info["raw_preview"].startswith("not json")
    assert out["resource_allocation"]["lines"] == []
    assert any("invalid JSON" in w for w in out["warnings"])


def test_non_object_non_list_rejected():
    out = svc.lookup(OID, FakeSF(rec(Resource_Lines__c='"just a string"')))
    assert out["fields"]["Resource_Lines__c"]["ok"] is False
    assert "decoded twice" not in (out["fields"]["Resource_Lines__c"]["note"] or "")

"""OpportunityManager + poller tests (Salesforce faked, agents mocked)."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from main import app
from services.opportunities import Conflict, NotFound, OpportunityManager
from services.salesforce import FakeSalesforce

client = TestClient(app, raise_server_exceptions=False)


def a1_out(oid="006FAKE0000001A"):
    return {
        "project_id": oid,
        "fields": {
            "customer_name": {
                "value": "Acme", "confidence": 0.9, "review_state": "auto",
                "conflict": None, "source": "sow", "evidence": "Acme Corp",
            },
            "start_date": {
                "value": "2026-01-01", "confidence": 0.5, "review_state": "review",
                "conflict": {"sow_value": "2026-01-01", "sf_value": "2026-02-01"},
                "source": "sow", "evidence": None,
            },
        },
        "follow_up_questions": [{"field": "margin", "question": "What margin?"}],
        "warnings": [], "errors": [],
    }


A2 = {
    "preselect": True, "selected_combination_id": "QA-101", "best_score": 0.8,
    "top_3": [
        {"combination_id": "QA-101", "name": "Impl", "score": 0.8},
        {"combination_id": "QA-102", "name": "Upgrade", "score": 0.6},
    ],
}


def make_manager(auto=True):
    with patch("services.opportunities.AUTO_ANALYZE", auto), \
         patch("services.opportunities.sow.run_a1", side_effect=lambda oid: a1_out(oid)), \
         patch("services.opportunities.sow.run_a2", return_value=A2):
        m = OpportunityManager(client=FakeSalesforce())
        m.poll_once()
    return m


def analyzed_manager():
    return make_manager(auto=True)


# ---- polling ---------------------------------------------------------------

def test_poll_auto_analyzes_new_opportunities():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    rec = m._records[oid]
    assert rec["status"] == "ready"
    assert rec["a2_status"] == "ready"
    assert rec["qa_choice"] == "QA-101"
    assert rec["a1"]["project_id"] == oid


def test_poll_does_not_reanalyze_existing():
    client_ = FakeSalesforce()
    with patch("services.opportunities.AUTO_ANALYZE", True), \
         patch("services.opportunities.sow.run_a1", return_value=a1_out()) as r1, \
         patch("services.opportunities.sow.run_a2", return_value=A2):
        m = OpportunityManager(client=client_)
        m.poll_once()
        n = r1.call_count
        m.poll_once()
        assert r1.call_count == n  # no second analysis for known ids


def test_poll_flags_sf_changed_after_modification():
    client_ = FakeSalesforce()
    with patch("services.opportunities.AUTO_ANALYZE", True), \
         patch("services.opportunities.sow.run_a1", side_effect=lambda oid: a1_out(oid)), \
         patch("services.opportunities.sow.run_a2", return_value=A2):
        m = OpportunityManager(client=client_)
        m.poll_once()
        oid = client_.opps[0]["id"]
        assert m._records[oid]["sf_changed"] is False
        client_.opps[0]["modified"] = "2027-01-01T00:00:00.000+0000"
        m.poll_once()
        assert m._records[oid]["sf_changed"] is True


def test_poll_keeps_records_on_salesforce_error():
    m = analyzed_manager()
    with patch.object(type(m.client), "fetch_recent", side_effect=RuntimeError("boom")):
        assert m.poll_once() == []
    assert m.last_error and "boom" in m.last_error
    assert m.meta()["opportunities"]  # list still served from last good poll


# ---- lifecycle -------------------------------------------------------------

def test_start_is_idempotent():
    m = OpportunityManager(client=FakeSalesforce())
    with patch("services.opportunities.AUTO_ANALYZE", False):
        m.start()
        try:
            first = m._thread
            assert first.is_alive()
            m.start()
            assert m._thread is first  # second start must not spawn another poller
        finally:
            m.stop()


# ---- reanalyze -------------------------------------------------------------

def test_reanalyze_claim_is_atomic():
    m = make_manager(auto=False)
    oid = "006FAKE0000001A"
    m._analyze = lambda o: None  # hold status at "queued" to inspect the claim
    m.reanalyze(oid)
    assert m._records[oid]["status"] == "queued"
    with pytest.raises(Conflict):
        m.reanalyze(oid)  # double-click while queued


def test_reanalyze_blocked_after_submit():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    m.save_fields(oid, {"start_date": "x"}, resolve=["start_date"])
    m.submit(oid)
    with pytest.raises(Conflict):
        m.reanalyze(oid)


def test_detail_unknown_id_raises_not_found():
    m = make_manager(auto=False)
    with pytest.raises(NotFound):
        m.detail("does-not-exist")


# ---- field edits -----------------------------------------------------------

def test_save_fields_rejects_unknown_and_merges_edits():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    with pytest.raises(ValueError):
        m.save_fields(oid, {"nope": "v"}, [])
    m.save_fields(oid, {"start_date": "2026-03-03"}, ["start_date"])
    d = m.detail(oid)
    assert d["final_fields"]["start_date"] == "2026-03-03"
    assert d["final_fields"]["customer_name"] == "Acme"
    assert "start_date" not in d["unresolved_conflicts"]


def test_submit_requires_conflicts_resolved_and_is_idempotent():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    with pytest.raises(Conflict):
        m.submit(oid)
    m.save_fields(oid, {"start_date": "2026-01-01"}, ["start_date"])
    sub = m.submit(oid, user="pm@example.com")
    assert sub["joss_id"].startswith("JOSS-")
    assert sub["simulated"] is True
    assert m.submit(oid)["joss_id"] == sub["joss_id"]


# ---- QA choice -------------------------------------------------------------

def test_set_qa_validates_and_requires_override_reason():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    with pytest.raises(ValueError):
        m.set_qa(oid, "QA-999", None)  # not one of the suggestions
    with pytest.raises(ValueError):
        m.set_qa(oid, "QA-102", "")  # override without a reason
    rec = m._records[oid]
    assert rec["qa_choice"] == "QA-101"  # unchanged after the rejected calls

def test_set_qa_override_with_reason():
    m = analyzed_manager()
    oid = "006FAKE0000001A"
    m.set_qa(oid, "QA-102", "Client prefers upgrade track")
    rec = m._records[oid]
    assert rec["qa_choice"] == "QA-102"
    assert rec["qa_overridden"] is True
    assert rec["qa_reason"] == "Client prefers upgrade track"


# ---- HTTP smoke ------------------------------------------------------------

def test_opportunities_meta_endpoint():
    r = client.get("/api/v1/opportunities")
    assert r.status_code == 200
    body = r.json()
    assert "opportunities" in body
    assert "last_polled_at" in body


def test_opportunities_unknown_id_404():
    r = client.get("/api/v1/opportunities/006FAKE0000099A")
    assert r.status_code == 404

"""Polls Salesforce, auto-runs A1 then A2 for new opportunities, and holds the review state.

State is IN MEMORY: run ONE uvicorn worker (a restart forgets analyses and re-runs A1 for the
current top 5). Blocking Glean calls run in a small thread pool.
"""
import copy
import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from services import joss_dummy, salesforce, sow

log = logging.getLogger(__name__)
POLL_INTERVAL_S = float(os.getenv("SF_POLL_INTERVAL_S", "30"))
RECENT_LIMIT = int(os.getenv("SF_RECENT_LIMIT", "5"))
PARALLEL = int(os.getenv("AUTO_ANALYZE_PARALLEL", "2"))
AUTO_ANALYZE = os.getenv("AUTO_ANALYZE", "1") == "1"


class NotFound(KeyError):
    pass


class Conflict(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class OpportunityManager:
    def __init__(self, client=None):
        self._lock = threading.RLock()
        self._records, self._recent = {}, []
        self._client, self._pool = client, None
        self._stop, self._thread = threading.Event(), None
        self.last_polled_at, self.last_error = None, None

    # ---- lifecycle ------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            self._client = salesforce.make_client()
        return self._client

    def start(self):
        if self._thread and self._thread.is_alive():
            return  # already polling (e.g. lifespan ran twice)
        self._pool = ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="analyze")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="sf-poller", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._pool:
            self._pool.shutdown(wait=False, cancel_futures=True)

    def _loop(self):
        while True:
            self.poll_once()
            if self._stop.wait(POLL_INTERVAL_S):
                return

    # ---- polling --------------------------------------------------------
    def poll_once(self):
        try:
            opps = self.client.fetch_recent(RECENT_LIMIT)
        except Exception as e:  # keep polling; show the error in the UI
            log.warning("Salesforce poll failed: %s", e)
            with self._lock:
                self.last_error, self.last_polled_at = str(e), _now()
            return []
        new_ids = []
        with self._lock:
            self.last_error, self.last_polled_at = None, _now()
            self._recent = [o["id"] for o in opps]
            for o in opps:
                rec = self._records.get(o["id"])
                if rec is None:
                    self._records[o["id"]] = self._blank(o)
                    new_ids.append(o["id"])
                else:
                    rec.update({k: o[k] for k in ("name", "account", "stage", "amount", "modified")})
                    rec["sf_changed"] = bool(rec["analyzed_modified"]) and o["modified"] != rec["analyzed_modified"]
        if AUTO_ANALYZE:
            for oid in new_ids:
                self._enqueue(oid)
        return new_ids

    @staticmethod
    def _blank(o):
        return {**{k: o[k] for k in ("id", "name", "account", "stage", "amount", "created", "modified")},
                "status": "idle", "a1": None, "a1_error": None, "analyzed_at": None, "analyzed_modified": None,
                "sf_changed": False, "a2_status": "pending", "a2": None, "a2_error": None,
                "edits": {}, "resolved": [], "qa_choice": None, "qa_reason": None, "qa_overridden": False,
                "submission": None}

    # ---- analysis -------------------------------------------------------
    def _enqueue(self, oid):
        with self._lock:
            self._records[oid]["status"] = "queued"
        if self._pool is None:
            self._analyze(oid)  # tests / no pool: run inline
        else:
            self._pool.submit(self._analyze, oid)

    def _analyze(self, oid):
        with self._lock:
            rec = self._records[oid]
            rec.update(status="analyzing", a1_error=None, a1=None, a2=None, a2_error=None, a2_status="pending",
                       edits={}, resolved=[], qa_choice=None, qa_reason=None, qa_overridden=False)
            modified = rec["modified"]
        try:
            a1 = sow.run_a1(oid)
        except Exception as e:
            log.warning("A1 failed for %s: %s", oid, e)
            with self._lock:
                rec.update(status="failed", a1_error=str(e), a2_status="skipped")
            return
        with self._lock:
            rec.update(status="ready", a1=a1, analyzed_at=_now(), analyzed_modified=modified,
                       sf_changed=False, a2_status="running")
        try:  # an A2 failure never affects A1's result
            a2 = sow.run_a2(json.dumps({"project_id": oid, "a1_output": a1}))
        except Exception as e:
            log.warning("A2 failed for %s: %s", oid, e)
            with self._lock:
                rec.update(a2_status="failed", a2_error=str(e))
            return
        with self._lock:
            rec.update(a2_status="ready", a2=a2)
            if a2.get("preselect") and a2.get("selected_combination_id"):
                rec["qa_choice"] = a2["selected_combination_id"]

    def reanalyze(self, oid):
        with self._lock:
            rec = self._get(oid)
            if rec["status"] in ("queued", "analyzing") or rec["a2_status"] == "running":
                raise Conflict("Analysis is already running")
            if rec["submission"]:
                raise Conflict("Already submitted to JOSS")
            # claim inside the same lock as the check so a double-click
            # cannot enqueue the same opportunity twice
            self._enqueue(oid)

    # ---- reads ----------------------------------------------------------
    def _get(self, oid):
        if oid not in self._records:
            raise NotFound(oid)
        return self._records[oid]

    def meta(self):
        with self._lock:
            rows = []
            for oid in self._recent:
                r = self._records[oid]
                rows.append({**{k: r[k] for k in ("id", "name", "account", "stage", "amount", "created", "status",
                                                  "a2_status", "qa_choice", "sf_changed", "analyzed_at", "a1_error")},
                             "best_score": (r["a2"] or {}).get("best_score"),
                             "submitted": bool(r["submission"])})
            return {"opportunities": rows, "last_polled_at": self.last_polled_at, "poll_interval_s": POLL_INTERVAL_S,
                    "source": getattr(self._client, "source", "unknown") if self._client else "unknown",
                    "last_error": self.last_error, "recent_limit": RECENT_LIMIT}

    @staticmethod
    def final_fields(rec):
        fields = (rec["a1"] or {}).get("fields") or {}
        return {k: rec["edits"][k] if k in rec["edits"] else (f or {}).get("value") for k, f in fields.items()}

    @staticmethod
    def unresolved(rec):
        fields = (rec["a1"] or {}).get("fields") or {}
        return [k for k, f in fields.items()
                if (f or {}).get("conflict") and k not in rec["resolved"] and k not in rec["edits"]]

    def detail(self, oid):
        with self._lock:
            rec = copy.deepcopy(self._get(oid))
            rec["final_fields"], rec["unresolved_conflicts"] = self.final_fields(rec), self.unresolved(rec)
            return rec

    # ---- user edits -----------------------------------------------------
    def save_fields(self, oid, fields, resolve):
        with self._lock:
            rec = self._get(oid)
            if rec["submission"]:
                raise Conflict("Already submitted to JOSS")
            known = (rec["a1"] or {}).get("fields")
            if known is None:
                raise ValueError("A1 has not produced fields yet")
            bad = [k for k in [*fields, *resolve] if k not in known]
            if bad:
                raise ValueError(f"Unknown field(s): {', '.join(bad)}")
            rec["edits"].update(fields)
            rec["resolved"] = sorted(set(rec["resolved"]) | set(resolve))

    def set_qa(self, oid, combination_id, reason):
        with self._lock:
            rec = self._get(oid)
            if rec["submission"]:
                raise Conflict("Already submitted to JOSS")
            a2 = rec["a2"]
            if rec["a2_status"] != "ready" or not a2:
                raise ValueError("A2 has not produced options yet")
            if combination_id not in [t.get("combination_id") for t in a2.get("top_3", [])]:
                raise ValueError("Choose one of the suggested combinations")
            suggested = a2.get("selected_combination_id") if a2.get("preselect") else None
            overridden = bool(suggested) and combination_id != suggested
            if overridden and not (reason or "").strip():
                raise ValueError("A reason is required when you change the suggested QA type.")
            rec.update(qa_choice=combination_id, qa_overridden=overridden, qa_reason=(reason or "").strip() or None)

    # ---- submit (dummy JOSS) ---------------------------------------------
    def submit(self, oid, user=""):
        with self._lock:
            rec = self._get(oid)
            if rec["submission"]:
                return rec["submission"]  # idempotent
            if rec["status"] != "ready":
                raise Conflict("Wait for the SOW analysis to finish")
            open_conflicts = self.unresolved(rec)
            if open_conflicts:
                raise Conflict("Resolve SOW vs Salesforce conflicts first: " + ", ".join(open_conflicts))
            fields = {k: v for k, v in self.final_fields(rec).items() if v not in (None, "")}
            qa = None
            if rec["qa_choice"]:
                top = next((t for t in (rec["a2"] or {}).get("top_3", []) if t.get("combination_id") == rec["qa_choice"]), {})
                qa = {"combination_id": rec["qa_choice"], "name": top.get("name"), "score": top.get("score"),
                      "overridden": rec["qa_overridden"], "reason": rec["qa_reason"]}
            package = {"opportunity_id": oid, "fields": fields, "qa": qa, "submitted_by": user or "unknown"}
            rec["submission"] = joss_dummy.submit(package)
            return rec["submission"]


manager = OpportunityManager()
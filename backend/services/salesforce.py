"""Salesforce access for the poller and the Task Builder.

SalesforceClient  - real: Salesforce REST API (SOQL query, create, update).
FakeSalesforce    - development: five fixed sample opportunities.

Env vars (put them in .env):
  SF_INSTANCE_URL   https://yourcompany.my.salesforce.com
  SF_ACCESS_TOKEN   a ready access token   (OR the two below for client-credentials OAuth)
  SF_CLIENT_ID / SF_CLIENT_SECRET
  SF_ORDER_BY       CreatedDate (default) or LastModifiedDate
  SF_WHERE          optional SOQL filter, e.g.  StageName != 'Closed Lost'
  SF_USE_FAKE=1     force the fake client
"""
import logging
import os
import threading
from urllib.parse import urlencode

import requests

import  config  # noqa: F401  (makes sure .env is loaded)

log = logging.getLogger(__name__)
API_VERSION = os.getenv("SF_API_VERSION", "v59.0")

class SalesforceError(Exception):
    """A failed Salesforce call. `errors` is Salesforce's error list, e.g.
    [{"errorCode": "DUPLICATE_VALUE", "message": "...", "fields": []}]."""

    def __init__(self, message, status=None, errors=None):
        super().__init__(message)
        self.status = status
        self.errors = errors or []

    def has_code(self, code: str) -> bool:
        return any(e.get("errorCode") == code for e in self.errors if isinstance(e, dict))

    @property
    def detail(self) -> str:
        """Salesforce's own message when there is one (shorter and PM-readable)."""
        for e in self.errors:
            if isinstance(e, dict) and e.get("message"):
                return e["message"]
        return str(self)

def build_soql(limit: int, where: str = "", order_by: str = "CreatedDate") -> str:
    q = "SELECT Id, Name, Account.Name, StageName, Amount, CreatedDate, LastModifiedDate FROM Opportunity"
    if where:
        q += f" WHERE {where}"
    return f"{q} ORDER BY {order_by} DESC LIMIT {int(limit)}"

class SalesforceClient:
    source = "salesforce"

    def __init__(self, instance_url, access_token="", client_id="", client_secret="",
                 where="", order_by="CreatedDate", timeout=30):
        self.instance_url = instance_url.rstrip("/")
        self._token = access_token
        self._token_lock = threading.Lock()
        self.client_id, self.client_secret = client_id, client_secret
        self.where, self.order_by, self.timeout = where, order_by, timeout

    def _can_refresh(self):
        return bool(self.client_id and self.client_secret)

    def _login(self, expect_token=None):
        """Fetch a fresh access token. expect_token: if another thread already
        replaced the token we used, skip the redundant login."""
        with self._token_lock:
            if expect_token is not None and self._token and self._token != expect_token:
                return
            r = requests.post(f"{self.instance_url}/services/oauth2/token", timeout=self.timeout, data={
                "grant_type": "client_credentials", "client_id": self.client_id, "client_secret": self.client_secret})
            if not r.ok:
                raise SalesforceError(f"Salesforce login failed: HTTP {r.status_code}", r.status_code)
            self._token = r.json()["access_token"]

    def _request(self, method: str, url: str, json_body=None):
        """Authenticated REST call with one token refresh on 401."""
        for attempt in (0, 1):
            if not self._token:
                if not self._can_refresh():
                    raise SalesforceError("Set SF_ACCESS_TOKEN or SF_CLIENT_ID + SF_CLIENT_SECRET")
                self._login()
            token = self._token
            r = requests.request(method, url, json=json_body,
                                 headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
            if r.status_code == 401 and attempt == 0 and self._can_refresh():
                self._login(expect_token=token)
                continue
            break
        if not r.ok:
            try:
                errors = r.json()
            except ValueError:
                errors = []
            if not isinstance(errors, list):
                errors = []
            raise SalesforceError(
                f"Salesforce {method} failed: HTTP {r.status_code} {r.text[:200]}", r.status_code, errors)
        return r

    def query(self, soql: str) -> list[dict]:
        """Run a SOQL query and return all records (follows nextRecordsUrl)."""
        r = self._request("GET", f"{self.instance_url}/services/data/{API_VERSION}/query?" + urlencode({"q": soql}))
        data = r.json()
        records = list(data.get("records", []))
        while not data.get("done", True) and data.get("nextRecordsUrl"):
            data = self._request("GET", self.instance_url + data["nextRecordsUrl"]).json()
            records.extend(data.get("records", []))
        return records

    def create_sobject(self, object_name: str, body: dict) -> str:
        r = self._request(
            "POST",
            f"{self.instance_url}/services/data/{API_VERSION}/sobjects/{object_name}/",
            body,
        )
        return (r.json() or {}).get("id", "")

    def update_sobject(self, object_name: str, record_id: str, body: dict) -> None:
        self._request(
            "PATCH",
            f"{self.instance_url}/services/data/{API_VERSION}/sobjects/{object_name}/{record_id}",
            body,
        )
    def composite(self, subrequests: list[dict], all_or_none: bool = True) -> list[dict]:
        """Run several sobject writes in ONE call. With all_or_none=True Salesforce rolls
        everything back if any sub-request fails (used for handovers)."""
        if not 0 < len(subrequests) <= 25:
            raise SalesforceError("composite needs 1 to 25 sub-requests")
        base = f"/services/data/{API_VERSION}/sobjects"
        payload = {"allOrNone": bool(all_or_none), "compositeRequest": [
            {"method": s["method"], "referenceId": f"r{i}",
             "url": f"{base}/{s['object']}" + (f"/{s['id']}" if s["method"] != "POST" else ""),
             "body": s["body"]} for i, s in enumerate(subrequests)]}
        r = self._request("POST", f"{self.instance_url}/services/data/{API_VERSION}/composite", payload)
        out = []
        for item in r.json().get("compositeResponse", []):
            status, body = item.get("httpStatusCode", 0), item.get("body")
            out.append({"status": status, "id": body.get("id") if isinstance(body, dict) else None, "body": body})
        bad = [o for o in out if o["status"] >= 400]
        if bad:  # the root cause is the one that is not PROCESSING_HALTED
            errs = [e for o in bad if isinstance(o["body"], list) for e in o["body"] if isinstance(e, dict)]
            root = [e for e in errs if e.get("errorCode") != "PROCESSING_HALTED"] or errs
            raise SalesforceError(f"Salesforce composite failed: {root[:1]}", bad[0]["status"], root)
        return out
    

    def fetch_recent(self, limit=5):
        if not self._token:
            if not self._can_refresh():
                raise SalesforceError("Set SF_ACCESS_TOKEN or SF_CLIENT_ID + SF_CLIENT_SECRET")
            self._login()
        for attempt in (0, 1):
            token = self._token
            r = requests.get(
                f"{self.instance_url}/services/data/{API_VERSION}/query",
                params={"q": build_soql(limit, self.where, self.order_by)},
                headers={"Authorization": f"Bearer {token}"}, timeout=self.timeout)
            if r.status_code == 401 and attempt == 0 and self._can_refresh():
                self._login(expect_token=token)  # token expired: re-login once
                continue
            break
        if not r.ok:
            raise SalesforceError(f"Salesforce query failed: HTTP {r.status_code} {r.text[:200]}", r.status_code)
        return [{"id": x["Id"], "name": x["Name"], "account": (x.get("Account") or {}).get("Name"),
                 "stage": x.get("StageName"), "amount": x.get("Amount"),
                 "created": x["CreatedDate"], "modified": x["LastModifiedDate"]}
                for x in r.json().get("records", [])]

class FakeSalesforce:
    source = "fake"

    def __init__(self):
        names = [("Acme Corp", "Oracle Fusion Rollout"), ("Globex", "CRM Implementation"),
                 ("Initech", "Data Platform Migration"), ("Umbrella", "HCM Integration"),
                 ("Hooli", "Independent Testing Services")]
        self.opps = [{"id": f"006FAKE{i:07d}A", "name": n, "account": a, "stage": "Closed Won",
                      "amount": 100000 * i, "created": f"2026-10-{20 - i:02d}T09:00:00.000+0000",
                      "modified": f"2026-10-{20 - i:02d}T09:00:00.000+0000"}
                     for i, (a, n) in enumerate(names, 1)]

    def fetch_recent(self, limit=5):
        return [dict(o) for o in self.opps[:limit]]

def make_client():
    url, token = os.getenv("SF_INSTANCE_URL", ""), os.getenv("SF_ACCESS_TOKEN", "")
    cid, secret = os.getenv("SF_CLIENT_ID", ""), os.getenv("SF_CLIENT_SECRET", "")
    if os.getenv("SF_USE_FAKE") == "1" or not (url and (token or (cid and secret))):
        log.warning("Salesforce credentials not set: using FAKE opportunities")
        return FakeSalesforce()
    return SalesforceClient(url, token, cid, secret, os.getenv("SF_WHERE", ""),
                            os.getenv("SF_ORDER_BY", "CreatedDate"))

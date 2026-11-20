import os
import re
from pathlib import Path

# pyrefly: ignore[missing-import]
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing env var: {name}")
    return value.strip()


def _clean_instance(raw: str) -> str:
    # accepts "2e70ec5d", "2e70ec5d-be.glean.com", "https://2e70ec5d-be.glean.com/"
    raw = re.sub(r"^https?://", "", raw.strip())
    raw = raw.split("/")[0]
    raw = re.sub(r"(-be)?\.glean\.com$", "", raw)
    return raw


GLEAN_INSTANCE = _clean_instance(_require("GLEAN_INSTANCE"))
GLEAN_API_TOKEN = _require("GLEAN_API_TOKEN")
AGENT_ID = os.getenv("MY_AGENT_ID")

A1_AGENT_ID = os.getenv("A1_AGENT_ID") or os.getenv("MY_AGENT_ID")
A2_AGENT_ID = os.getenv("A2_AGENT_ID")
A1_INPUT_KEY = os.getenv("A1_INPUT_KEY", "Salesforce Opportunity ID")
A2_INPUT_KEY = os.getenv("A2_INPUT_KEY", "QA Payload")

# A3 — JOSS Task Builder (chat → task create/update/deactivate proposals)
A3_AGENT_ID = os.getenv("A3_AGENT_ID")
A3_INPUT_KEY = os.getenv("A3_INPUT_KEY", "Task Chat Payload")

# Salesforce task object used for real task write-back (adjust to your org)
SF_TASK_OBJECT = os.getenv("SF_TASK_OBJECT", "JOSS_Task__c")
A4_AGENT_ID = os.getenv("A4_AGENT_ID", "x")
A4_INPUT_KEY = os.getenv("A4_INPUT_KEY", "Contacts Chat Payload")
SF_CONTACT_OBJECT = os.getenv("SF_CONTACT_OBJECT", "JOSS_Contact__c")
# A5 — JOSS Resource Management
A5_AGENT_ID = os.getenv("A5_AGENT_ID")
A5_INPUT_KEY = os.getenv("A5_INPUT_KEY", "Resource Chat Payload")
SF_RESOURCE_OBJECT = os.getenv("SF_RESOURCE_OBJECT", "JOSS_Resource__c")


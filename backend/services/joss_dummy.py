"""Stand-in for the real JOSS write (decision D1). Replace submit() when the API exists."""
import zlib
from datetime import datetime, timezone

STEPS = ["Validate package", "Write project header", "Write QA profile", "Confirm with JOSS"]


def submit(package: dict) -> dict:
    n = zlib.crc32(package["opportunity_id"].encode()) % 90000 + 10000
    return {"joss_id": f"JOSS-{n}", "simulated": True, "steps": [{"name": s, "status": "done"} for s in STEPS],
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "package": package}
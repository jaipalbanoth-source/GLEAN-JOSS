"""Mock master data for the Contacts (A4) flow. Swap each function for a real source later."""

PM_ROLE = "Project Manager"

ROLE_MASTER = [
    {"role": "Project Manager", "single_holder": True},
    {"role": "Tech Lead", "single_holder": True},
    {"role": "Delivery Manager", "single_holder": True},
    {"role": "Solution Architect", "single_holder": False},
    {"role": "Business Analyst", "single_holder": False},
]

HANDOVER_RULE = {"previous_end_offset_days": 1}

DIRECTORY = [
    {"associate_id": "A-100", "name": "Sara Khan", "email": "sara.khan@example.com", "location": "Pune", "status": "active"},
    {"associate_id": "A-101", "name": "Rahul Patil", "email": "rahul.patil@example.com", "location": "Pune", "status": "active"},
    {"associate_id": "A-102", "name": "Priya Nair", "email": "priya.nair@example.com", "location": "Bengaluru", "status": "active"},
    {"associate_id": "A-103", "name": "Amit Shah", "email": "amit.shah@example.com", "location": "Mumbai", "status": "active"},
    {"associate_id": "A-104", "name": "Rahul Verma", "email": "rahul.verma@example.com", "location": "Hyderabad", "status": "active"},
    {"associate_id": "A-105", "name": "Neha Joshi", "email": "neha.joshi@example.com", "location": "Pune", "status": "inactive"},
]

def find_associate(associate_id: str) -> dict | None:
    """The ONE place the host looks an associate up (JC-04). Replace with a real lookup."""
    return next((a for a in DIRECTORY if a["associate_id"] == associate_id), None)

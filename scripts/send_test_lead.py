"""Pošle podepsaný testovací lead na běžící server: python scripts/send_test_lead.py"""
import json
import os
import time
import uuid

import httpx

from app.security import sign

secret = os.environ["WEBHOOK_SECRET"]
lead = {
    "event_id": f"evt-{uuid.uuid4().hex[:10]}",
    "source": "meta_lead_ads",
    "full_name": "Jan Novák",
    "phone": "+420777123456",
    "email": "jan@example.com",
    "municipality": "Kladno",
    "property_type": "byt",
    "area_m2": 68,
    "condition": "puvodni",
    "declared_debts_czk": 300000,
    "consent_processing": True,
}
body = json.dumps(lead).encode()
ts = str(int(time.time()))
r = httpx.post("http://localhost:8000/webhook/lead", content=body,
               headers={"X-Timestamp": ts, "X-Signature": sign(secret, ts, body), "Content-Type": "application/json"})
print(r.status_code, r.json())
time.sleep(0.5)
print(json.dumps(httpx.get(f"http://localhost:8000/leads/{lead['event_id']}", headers={"X-Api-Key": secret}).json(),
                 indent=2, ensure_ascii=False))

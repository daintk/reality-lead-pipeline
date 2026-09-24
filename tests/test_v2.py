import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app import security
from app.legal import Light, evaluate
from app.sources.isir_stream import CursorStore, IsirStream, build_request, parse_events

SECRET = "test-secret"

STREAM_XML = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:getIsirWsPublicDataResponse xmlns:ns2="http://isirpublicws.cca.cz/types/">
 <data><id>1002</id><datumZalozeniUdalosti>2026-09-24T10:00:00</datumZalozeniUdalosti>
  <datumZverejneniUdalosti>2026-09-24T10:05:00</datumZverejneniUdalosti>
  <spisovaZnacka>KSPH 60 INS 1234 / 2026</spisovaZnacka><typUdalosti>12</typUdalosti>
  <popisUdalosti>Oznámení o zpeněžení mimo dražbu</popisUdalosti></data>
 <data><id>1001</id><datumZalozeniUdalosti>2026-09-24T09:00:00</datumZalozeniUdalosti>
  <datumZverejneniUdalosti>2026-09-24T09:01:00</datumZverejneniUdalosti>
  <spisovaZnacka>KSBR 27 INS 99 / 2026</spisovaZnacka><typUdalosti>1</typUdalosti>
  <popisUdalosti>Insolvenční návrh</popisUdalosti></data>
 <status><stav>OK</stav></status>
</ns2:getIsirWsPublicDataResponse></S:Body></S:Envelope>"""


# --- právní semafor ---------------------------------------------------------
def test_legal_light():
    assert evaluate(insolvency_records=1, insolvency_checked=True, has_execution=False).light == Light.red
    assert evaluate(insolvency_records=0, insolvency_checked=True, has_execution=True).light == Light.red
    assert evaluate(insolvency_records=0, insolvency_checked=False, has_execution=None).light == Light.orange
    assert evaluate(insolvency_records=0, insolvency_checked=True, has_execution=False).light == Light.green
    assert evaluate(insolvency_records=0, insolvency_checked=False, has_execution=None,
                    is_auction=True).light == Light.green


# --- ISIR stream ------------------------------------------------------------
def test_stream_parse_sorted_and_filter():
    events = parse_events(STREAM_XML)
    assert [e.id for e in events] == [1001, 1002]
    assert [e.id for e in events if e.is_sale_opportunity()] == [1002]
    assert "<idPodnetu>1000</idPodnetu>" in build_request(1000)


@pytest.mark.anyio
async def test_stream_cursor_advances_and_survives_restart(tmp_path):
    seen_ids = []

    def handler(request: httpx.Request):
        seen_ids.append(request.content.decode())
        return httpx.Response(200, text=STREAM_XML)

    cursor = CursorStore(tmp_path / "cursor.json")
    stream = IsirStream(cursor, rate_per_sec=100, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    sales = await stream.sale_opportunities()
    assert len(sales) == 1
    assert CursorStore(tmp_path / "cursor.json").load() == 1002  # nový proces naváže
    await stream.fetch_new()
    assert "<idPodnetu>1002</idPodnetu>" in seen_ids[-1]


# --- API: semafor v balíčku, PDF, dražby, status ----------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CRM_DRY_RUN", "true")
    monkeypatch.setenv("ISIR_ENABLED", "false")
    monkeypatch.setenv("ISIR_STREAM_ENABLED", "false")
    from app.main import app
    with TestClient(app) as c:
        yield c


def _post_lead(client, **over):
    lead = {
        "event_id": "evt-v2-0001", "full_name": "Jana Dvořáková", "phone": "+420602111222",
        "municipality": "Plzeň", "property_type": "dum", "area_m2": 120, "condition": "dobry",
        "declared_execution": True, "consent_processing": True,
    }
    lead.update(over)
    body = json.dumps(lead).encode()
    ts = str(int(time.time()))
    return client.post("/webhook/lead", content=body,
                       headers={"X-Timestamp": ts, "X-Signature": security.sign(SECRET, ts, body)})


def test_package_has_red_light_for_execution(client):
    assert _post_lead(client).status_code == 202
    pkg = client.get("/leads/evt-v2-0001", headers={"X-Api-Key": SECRET}).json()["package"]
    assert pkg["legal"]["light"] == "cervena"


def test_pdf_generated(client):
    _post_lead(client, event_id="evt-v2-pdf1")
    r = client.get("/leads/evt-v2-pdf1/pdf", headers={"X-Api-Key": SECRET})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert client.get("/leads/evt-v2-pdf1/pdf").status_code == 401


def test_auction_scan_ranks_deals(client):
    r = client.post("/auctions/scan?min_discount=0.2", headers={"X-Api-Key": SECRET}).json()
    assert r["count"] >= 1
    discounts = [d["discount_vs_market"] for d in r["deals"]]
    assert discounts == sorted(discounts, reverse=True) and min(discounts) >= 0.2


def test_status_dashboard(client):
    client.post("/auctions/scan", headers={"X-Api-Key": SECRET})
    s = client.get("/status", headers={"X-Api-Key": SECRET}).json()
    assert s["overall"] == "ok" and s["sources"]["drazby:file"]["state"] == "ok"


@pytest.fixture
def anyio_backend():
    return "asyncio"

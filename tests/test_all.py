import json
import time
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient

from app import security
from app.http_utils import RetryableError, with_retry
from app.models import Condition, PropertyType
from app.privacy import mask_email, mask_name, mask_phone
from app.sources.isir import IsirClient, build_request, parse_response
from app.valuation import PriceMap, compute

SECRET = "test-secret"

ISIR_FOUND = """<?xml version="1.0"?>
<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:getIsirWsCuzkDataResponse xmlns:ns2="http://isirws.cca.cz/types/">
  <data><ic>12345678</ic><cisloSenatu>60</cisloSenatu><druhVec>INS</druhVec><bcVec>1234</bcVec>
  <rocnik>2025</rocnik><druhStavKonkursu>MORATORIUM</druhStavKonkursu>
  <urlDetailRizeni>https://isir.justice.cz/detail</urlDetailRizeni></data>
  <stav><pocetVysledku>1</pocetVysledku></stav>
</ns2:getIsirWsCuzkDataResponse></S:Body></S:Envelope>"""

ISIR_EMPTY = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<ns2:getIsirWsCuzkDataResponse xmlns:ns2="http://isirws.cca.cz/types/">
<stav><kodChyby>WS2</kodChyby></stav></ns2:getIsirWsCuzkDataResponse></S:Body></S:Envelope>"""


def lead_payload(**over):
    p = {
        "event_id": "evt-000001",
        "full_name": "Jan Novák",
        "phone": "+420 777 123 456",
        "email": "jan@example.com",
        "municipality": "Kladno",
        "property_type": "byt",
        "area_m2": 68,
        "condition": "puvodni",
        "declared_debts_czk": 300000,
        "consent_processing": True,
    }
    p.update(over)
    return p


def signed(body: bytes):
    ts = str(int(time.time()))
    return {"X-Timestamp": ts, "X-Signature": security.sign(SECRET, ts, body), "Content-Type": "application/json"}


# --- security ---------------------------------------------------------------
def test_signature_ok_and_tampered():
    ts = str(int(time.time()))
    sig = security.sign(SECRET, ts, b"{}")
    assert security.verify(SECRET, ts, sig, b"{}")
    assert not security.verify(SECRET, ts, sig, b'{"x":1}')
    assert not security.verify(SECRET, str(int(time.time()) - 3600), sig, b"{}")  # replay


# --- privacy ----------------------------------------------------------------
def test_masking():
    assert mask_name("Jan Novák") == "J*** N***"
    assert "123" not in mask_phone("+420777123456")[:-3]
    assert mask_email("jan@example.com") == "j***@example.com"


# --- valuation --------------------------------------------------------------
def test_valuation_formula():
    pm = PriceMap({("kladno", "byt"): 62000}, "test")
    v = compute(price_map=pm, municipality="KLADNO", ptype=PropertyType.byt, area_m2=68,
                condition=Condition.puvodni, debts_czk=300000, discount=0.25, fixed_costs_czk=60000)
    assert v.market_value_czk == round(68 * 62000 * 0.88)
    assert v.recommended_purchase_czk == round(v.market_value_czk * 0.75 - 300000 - 60000)


def test_valuation_never_negative_and_unknown_city():
    pm = PriceMap({("kladno", "byt"): 62000}, "test")
    v = compute(price_map=pm, municipality="Kladno", ptype=PropertyType.byt, area_m2=20,
                condition=Condition.k_rekonstrukci, debts_czk=5_000_000, discount=0.25, fixed_costs_czk=0)
    assert v.recommended_purchase_czk == 0
    assert compute(price_map=pm, municipality="Nikde", ptype=PropertyType.byt, area_m2=50,
                   condition=Condition.dobry, debts_czk=0, discount=0.2, fixed_costs_czk=0) is None


def test_price_map_csv_diacritics():
    pm = PriceMap.from_csv("data/price_map.sample.csv")
    assert pm.price_per_m2("plzen", PropertyType.byt) == 70000


# --- ISIR -------------------------------------------------------------------
def test_isir_request_requires_birthdate_for_person():
    with pytest.raises(ValueError):
        build_request(surname="Novák")
    xml = build_request(surname="Novák", first_name="Jan", birth_date=date(1980, 1, 2))
    assert "<typ:datumNarozeni>1980-01-02</typ:datumNarozeni>" in xml


def test_isir_parse():
    recs = parse_response(ISIR_FOUND)
    assert recs[0].spisova_znacka == "60 INS 1234/2025"
    assert parse_response(ISIR_EMPTY) == []


@pytest.mark.anyio
async def test_isir_client_retries_on_503():
    calls = {"n": 0}

    def handler(request: httpx.Request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] < 2 else httpx.Response(200, text=ISIR_FOUND)

    client = IsirClient("https://isir.test/ws", rate_per_sec=100, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    recs = await client.search(ico="12345678")
    assert calls["n"] == 2 and len(recs) == 1


@pytest.mark.anyio
async def test_retry_gives_up():
    async def boom():
        raise RetryableError("x")

    with pytest.raises(RetryableError):
        await with_retry(boom, attempts=2, base_delay=0.01)


# --- API end-to-end ---------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("CRM_DRY_RUN", "true")
    monkeypatch.setenv("ISIR_ENABLED", "false")
    from app.main import app
    with TestClient(app) as c:
        yield c


def test_rejects_unsigned(client):
    r = client.post("/webhook/lead", content=json.dumps(lead_payload()))
    assert r.status_code == 401


def test_rejects_without_consent(client):
    body = json.dumps(lead_payload(consent_processing=False)).encode()
    assert client.post("/webhook/lead", content=body, headers=signed(body)).status_code == 422


def test_full_flow_and_idempotency(client):
    body = json.dumps(lead_payload()).encode()
    r = client.post("/webhook/lead", content=body, headers=signed(body))
    assert r.status_code == 202 and r.json()["status"] == "accepted"

    status = client.get("/leads/evt-000001", headers={"X-Api-Key": SECRET}).json()
    assert status["status"] == "done"
    pkg = status["package"]
    assert pkg["within_sla"] is True
    assert pkg["valuation"]["recommended_purchase_czk"] > 0
    assert pkg["contact"]["phone"] == "+420777123456"

    again = client.post("/webhook/lead", content=body, headers=signed(body))
    assert again.json()["status"] == "duplicate"


@pytest.fixture
def anyio_backend():
    return "asyncio"

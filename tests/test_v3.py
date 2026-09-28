"""v0.4: katastr (ČÚZK WSDP), exekuce (CEE API), levný filtr před placenými výpisy."""
from datetime import date, datetime, timezone

import httpx
import pytest

from app.config import Settings
from app.crm import CrmClient
from app.legal import Light, evaluate
from app.models import LeadIn
from app.pipeline import Pipeline
from app.sources import cee, cuzk
from app.sources.isir import IsirClient
from app.valuation import PriceMap

LV_XML = """<S:Envelope xmlns:S="http://schemas.xmlsoap.org/soap/envelope/"><S:Body>
<generujLVResponse xmlns="http://katastr.cuzk.cz/sestavy/types/v3.1">
 <lv><cisloLv>427</cisloLv>
  <vlastnici><vlastnik>Novák Jan</vlastnik><vlastnik>Nováková Eva</vlastnik></vlastnici>
  <omezeni>Zástavní právo smluvní – Hypoteční banka a.s.</omezeni>
  <omezeni>Exekuční příkaz k prodeji nemovitosti</omezeni>
 </lv>
</generujLVResponse></S:Body></S:Envelope>"""


# --- ČÚZK ----------------------------------------------------------------------
def test_cuzk_request_has_auth_and_lv():
    xml = cuzk.build_request(username="u", password="p", lv_number="427", ku_code=None, municipality="Knovíz")
    assert "<wsse:Username>u</wsse:Username>" in xml
    assert "<typ:cisloLv>427</typ:cisloLv>" in xml
    assert "<typ:obec>Knovíz</typ:obec>" in xml


def test_cuzk_parse_owners_and_risk_flags():
    chk = cuzk.parse_response(LV_XML, lv_number="427")
    assert chk.checked and chk.owners == ["Novák Jan", "Nováková Eva"]
    assert len(chk.encumbrances) == 2
    assert chk.risk_flags == chk.encumbrances  # zástava i exekuce jsou riziková


@pytest.mark.anyio
async def test_cuzk_client_retries_on_503():
    calls = []

    def handler(request: httpx.Request):
        calls.append(1)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, text=LV_XML)

    c = cuzk.WsdpClient("https://wsdp.test/lv", "u", "p", rate_per_sec=100,
                        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    chk = await c.lookup(lv_number="427", municipality="Knovíz")
    assert chk.checked and len(calls) == 2


# --- CEE ------------------------------------------------------------------------
def test_cee_query_requires_birthdate_for_person():
    with pytest.raises(ValueError):
        cee.build_query(ico=None, full_name="Jan Novák", birth_date=None)
    assert cee.build_query(ico="12345678", full_name=None, birth_date=None) == {"ico": "12345678"}


def test_cee_parse_variants():
    assert cee.parse_response([]).count == 0
    assert cee.parse_response({"pocet": 3}).count == 3
    chk = cee.parse_response({"exekuce": [{"spisovaZnacka": "123 EX 45/2024"}]})
    assert chk.count == 1 and chk.case_numbers == ["123 EX 45/2024"]
    with pytest.raises(RuntimeError):
        cee.parse_response({"error": "unauthorized"})


# --- semafor s katastrem ---------------------------------------------------------
def test_legal_cadastre_flags():
    red = evaluate(insolvency_records=0, insolvency_checked=True, has_execution=False,
                   cadastre_flags=["Exekuční příkaz k prodeji"], cadastre_checked=True)
    assert red.light == Light.red
    orange = evaluate(insolvency_records=0, insolvency_checked=True, has_execution=False,
                      cadastre_flags=["Zástavní právo smluvní"], cadastre_checked=True)
    assert orange.light == Light.orange and "Zástavní" in orange.reasons[0]
    green = evaluate(insolvency_records=0, insolvency_checked=True, has_execution=False,
                     cadastre_flags=[], cadastre_checked=True)
    assert green.light == Light.green


# --- jádro: levný filtr a přednost ověřených dat -----------------------------------
class _FakeCadastre:
    name = "cuzk:fake"
    calls = 0

    async def lookup(self, *, lv_number, municipality):
        self.calls += 1
        return cuzk.parse_response(LV_XML, lv_number=lv_number)


class _FakeCee:
    name = "cee:fake"
    calls = 0

    async def lookup(self, *, ico, full_name, birth_date):
        self.calls += 1
        return cee.parse_response({"pocet": 2})


def _pipeline(cad, exe, **env) -> Pipeline:
    s = Settings(webhook_secret="x", isir_enabled=False, crm_dry_run=True, **env)
    return Pipeline(settings=s, isir=IsirClient("https://isir.test"), crm=CrmClient("", True),
                    price_map=PriceMap.from_csv(s.price_map_path), cadastre=cad, execution=exe)


def _lead(**over) -> LeadIn:
    base = dict(event_id="evt-v3-0001", full_name="Jan Novák", phone="+420602111222",
                birth_date=date(1980, 1, 1), municipality="Kladno", lv_number="427",
                property_type="dum", area_m2=120, condition="dobry", declared_execution=False,
                consent_processing=True, consent_registry_check=True)
    return LeadIn(**{**base, **over})


@pytest.mark.anyio
async def test_paid_lookups_run_and_override_declared_data():
    cad, exe = _FakeCadastre(), _FakeCee()
    pkg = await _pipeline(cad, exe, paid_lookup_min_value_czk=0).process(_lead(), datetime.now(timezone.utc))
    assert cad.calls == 1 and exe.calls == 1
    assert pkg.cadastre.checked and pkg.execution.count == 2
    assert pkg.legal.light == Light.red  # majitel tvrdil "bez exekuce", CEE i LV říkají opak


@pytest.mark.anyio
async def test_paid_lookups_skipped_below_threshold():
    cad, exe = _FakeCadastre(), _FakeCee()
    pkg = await _pipeline(cad, exe, paid_lookup_min_value_czk=10**10).process(_lead(), datetime.now(timezone.utc))
    assert cad.calls == 0 and exe.calls == 0
    assert "pod prahem" in pkg.cadastre.reason
    assert pkg.legal.light == Light.orange  # bez ověření nic neprohlašujeme za zelené


@pytest.mark.anyio
async def test_paid_lookups_skipped_without_consent():
    cad, exe = _FakeCadastre(), _FakeCee()
    pkg = await _pipeline(cad, exe, paid_lookup_min_value_czk=0).process(
        _lead(consent_registry_check=False), datetime.now(timezone.utc))
    assert cad.calls == 0 and exe.calls == 0
    assert "souhlas" in pkg.execution.reason

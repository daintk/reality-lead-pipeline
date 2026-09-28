"""Katastr nemovitostí – oficiální Webové služby dálkového přístupu ČÚZK (WSDP).

Žádný scraping aplikace Nahlížení do KN (ČÚZK ho v podmínkách zakazuje). WSDP je
placené strojové rozhraní (SOAP, WS-Security UsernameToken) na zákaznický účet klienta;
výpis LV v XML stojí 100 Kč, proto se volá až po levném filtru (viz Pipeline).

Adaptér je vyměnitelný: v demu běží `DisabledCadastreSource`, v produkci `WsdpClient`
s účtem klienta. Konkrétní názvy operací se dolaďují podle dokumentace WSDP 3.1,
která je přístupná po založení účtu – transport, autentizace, limity a parsování jsou hotové.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Protocol
from xml.sax.saxutils import escape

import httpx

from app.http_utils import RateLimiter, raise_for_retry, with_retry
from app.models import CadastreCheck

SOAP_NS = "http://schemas.xmlsoap.org/soap/envelope/"
WSSE_NS = "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
WSDP_NS = "http://katastr.cuzk.cz/sestavy/types/v3.1"

# Slova ve výpisu, která pro investora znamenají „stop a ověř“.
RISK_KEYWORDS = ("exeku", "zástav", "insolven", "předkup", "věcné břemeno", "plomba")


class CadastreSource(Protocol):
    name: str

    async def lookup(self, *, lv_number: str, municipality: str) -> CadastreCheck: ...


class DisabledCadastreSource:
    """Bez účtu ČÚZK: nic nestahujeme, ale složka jasně říká, co chybí."""

    name = "cuzk:off"

    async def lookup(self, *, lv_number: str, municipality: str) -> CadastreCheck:
        return CadastreCheck(checked=False, reason="ČÚZK WSDP není nakonfigurováno (účet klienta)")


def build_request(*, username: str, password: str, lv_number: str, ku_code: str | None, municipality: str) -> str:
    """SOAP obálka s WS-Security UsernameToken a požadavkem na výpis LV v XML."""
    where = f"<typ:kodKu>{escape(ku_code)}</typ:kodKu>" if ku_code else f"<typ:obec>{escape(municipality)}</typ:obec>"
    return (
        f'<soapenv:Envelope xmlns:soapenv="{SOAP_NS}" xmlns:wsse="{WSSE_NS}" xmlns:typ="{WSDP_NS}">'
        "<soapenv:Header><wsse:Security><wsse:UsernameToken>"
        f"<wsse:Username>{escape(username)}</wsse:Username>"
        f"<wsse:Password>{escape(password)}</wsse:Password>"
        "</wsse:UsernameToken></wsse:Security></soapenv:Header>"
        "<soapenv:Body><typ:generujLVRequest>"
        f"{where}<typ:cisloLv>{escape(lv_number)}</typ:cisloLv><typ:format>xml</typ:format>"
        "</typ:generujLVRequest></soapenv:Body></soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_response(xml_text: str, *, lv_number: str) -> CadastreCheck:
    """Z XML výpisu vytáhne vlastníky a omezení (část C listu vlastnictví)."""
    root = ET.fromstring(xml_text)
    owners: list[str] = []
    encumbrances: list[str] = []
    for el in root.iter():
        name = _local(el.tag).lower()
        text = (el.text or "").strip()
        if name in {"kodchyby", "chyba"} and text:
            raise RuntimeError(f"ČÚZK WSDP chyba: {text}")
        if name in {"vlastnik", "nazevvlastnika", "opravnenysubjekt"} and text:
            owners.append(text)
        elif name in {"omezeni", "typomezeni", "popisomezeni", "jinyzapis"} and text:
            encumbrances.append(text)
    if not owners and not encumbrances:
        raise RuntimeError("ČÚZK WSDP: neočekávaný formát odpovědi")
    risky = [e for e in encumbrances if any(k in e.lower() for k in RISK_KEYWORDS)]
    return CadastreCheck(
        checked=True,
        reason="ověřeno přes ČÚZK WSDP",
        lv_number=lv_number,
        owners=owners,
        encumbrances=encumbrances,
        risk_flags=risky,
    )


class WsdpClient:
    name = "cuzk:wsdp"

    def __init__(
        self,
        endpoint: str,
        username: str,
        password: str,
        rate_per_sec: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.username = username
        self.password = password
        self.limiter = RateLimiter(rate_per_sec)
        self.client = client or httpx.AsyncClient(timeout=20.0)

    async def lookup(self, *, lv_number: str, municipality: str, ku_code: str | None = None) -> CadastreCheck:
        body = build_request(
            username=self.username, password=self.password,
            lv_number=lv_number, ku_code=ku_code, municipality=municipality,
        )

        async def call() -> str:
            await self.limiter.acquire()
            resp = await self.client.post(
                self.endpoint, content=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            )
            raise_for_retry(resp)
            return resp.text

        return parse_response(await with_retry(call), lv_number=lv_number)

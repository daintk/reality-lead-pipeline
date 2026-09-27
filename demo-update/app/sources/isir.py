"""Klient oficiální webové služby Insolvenčního rejstříku (ISIR WS – služba „CUZK“).

Používá veřejné SOAP rozhraní Ministerstva spravedlnosti – žádný scraping HTML,
žádné obcházení CAPTCHA. Vyhledání podle IČ, nebo podle jména + data narození.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date
from xml.sax.saxutils import escape

import httpx

from app.http_utils import RateLimiter, raise_for_retry, with_retry
from app.models import InsolvencyRecord

TYPES_NS = "http://isirws.cca.cz/types/"


def build_request(
    *,
    ico: str | None = None,
    surname: str | None = None,
    first_name: str | None = None,
    birth_date: date | None = None,
    max_results: int = 20,
) -> str:
    fields: list[tuple[str, str]] = []
    if ico:
        fields.append(("ic", ico))
    else:
        if not (surname and birth_date):
            raise ValueError("Fyzická osoba: je potřeba příjmení + datum narození (jinak falešné shody)")
        fields.append(("nazevOsoby", surname))
        if first_name:
            fields.append(("jmeno", first_name))
        fields.append(("datumNarozeni", birth_date.isoformat()))
    fields.append(("maxPocetVysledku", str(max_results)))
    fields.append(("filtrAktualniRizeni", "T"))
    fields.append(("vyhledatPresnouShoduJmen", "T"))

    inner = "".join(f"<typ:{k}>{escape(v)}</typ:{k}>" for k, v in fields)
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:typ="{TYPES_NS}"><soapenv:Header/><soapenv:Body>'
        f"<typ:getIsirWsCuzkDataRequest>{inner}</typ:getIsirWsCuzkDataRequest>"
        "</soapenv:Body></soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_response(xml_text: str) -> list[InsolvencyRecord]:
    root = ET.fromstring(xml_text)
    records: list[InsolvencyRecord] = []
    for el in root.iter():
        if _local(el.tag) == "kodChyby" and (el.text or "").strip() not in {"", "WS2"}:
            # WS2 = nic nenalezeno; ostatní kódy jsou chyby
            raise RuntimeError(f"ISIR chyba: {el.text}")
        if _local(el.tag) != "data":
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in el}
        if not d.get("bcVec"):
            continue
        sp = f"{d.get('cisloSenatu', '')} {d.get('druhVec', '')} {d.get('bcVec')}/{d.get('rocnik', '')}".strip()
        records.append(
            InsolvencyRecord(
                spisova_znacka=sp,
                stav=d.get("druhStavKonkursu") or None,
                url=d.get("urlDetailRizeni") or None,
            )
        )
    return records


class IsirClient:
    def __init__(self, endpoint: str, rate_per_sec: float = 1.0, client: httpx.AsyncClient | None = None):
        self.endpoint = endpoint
        self.limiter = RateLimiter(rate_per_sec)
        self.client = client or httpx.AsyncClient(timeout=15.0)

    async def search(self, **kwargs) -> list[InsolvencyRecord]:
        body = build_request(**kwargs)

        async def call() -> str:
            await self.limiter.acquire()
            resp = await self.client.post(
                self.endpoint,
                content=body.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            )
            raise_for_retry(resp)
            return resp.text

        return parse_response(await with_retry(call))

"""Stream nových událostí z Insolvenčního rejstříku (oficiální ISIR WS „public“).

Služba vrací události se vzrůstajícím ID. Pamatujeme si poslední zpracované ID (kurzor)
a každých pár minut si řekneme o novější. Filtrujeme události, které znamenají
prodej majetku (zpeněžení, dražba, prodej mimo dražbu) – to jsou reálné obchodní příležitosti.
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.http_utils import RateLimiter, raise_for_retry, with_retry

TYPES_NS = "http://isirpublicws.cca.cz/types/"
DEFAULT_ENDPOINT = "https://isir.justice.cz:8443/isir_public_ws/IsirWsPublicService"
DEFAULT_KEYWORDS = ("zpeněž", "dražb", "prodej", "kupní smlouv")


@dataclass(frozen=True)
class IsirEvent:
    id: int
    spisova_znacka: str
    typ: str
    popis: str
    zverejneno: str
    dokument_url: str | None

    def is_sale_opportunity(self, keywords: tuple[str, ...] = DEFAULT_KEYWORDS) -> bool:
        text = self.popis.lower()
        return any(k in text for k in keywords)


def build_request(last_id: int) -> str:
    return (
        '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" '
        f'xmlns:typ="{TYPES_NS}"><soapenv:Header/><soapenv:Body>'
        f"<typ:getIsirWsPublicIdDataRequest><idPodnetu>{int(last_id)}</idPodnetu>"
        "</typ:getIsirWsPublicIdDataRequest></soapenv:Body></soapenv:Envelope>"
    )


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def parse_events(xml_text: str) -> list[IsirEvent]:
    root = ET.fromstring(xml_text)
    events: list[IsirEvent] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "status":
            st = {_local(c.tag): (c.text or "").strip() for c in el}
            if st.get("stav") == "CHYBA" and st.get("kodChyby") != "WS2":
                raise RuntimeError(f"ISIR chyba {st.get('kodChyby')}: {st.get('popisChyby')}")
        if name != "data":
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in el}
        if not d.get("id"):
            continue
        events.append(
            IsirEvent(
                id=int(d["id"]),
                spisova_znacka=d.get("spisovaZnacka", ""),
                typ=d.get("typUdalosti", ""),
                popis=d.get("popisUdalosti", ""),
                zverejneno=d.get("datumZverejneniUdalosti", ""),
                dokument_url=d.get("dokumentUrl") or None,
            )
        )
    return sorted(events, key=lambda e: e.id)


class CursorStore:
    """Kurzor v souboru – po restartu navážeme tam, kde jsme skončili (nic neztratíme)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, default: int = 0) -> int:
        try:
            return int(json.loads(self.path.read_text())["last_id"])
        except (FileNotFoundError, KeyError, ValueError):
            return default

    def save(self, last_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"last_id": last_id}))
        tmp.replace(self.path)  # atomický zápis


class IsirStream:
    def __init__(
        self,
        cursor: CursorStore,
        endpoint: str = DEFAULT_ENDPOINT,
        rate_per_sec: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.cursor = cursor
        self.endpoint = endpoint
        self.limiter = RateLimiter(rate_per_sec)
        self.client = client or httpx.AsyncClient(timeout=60.0)

    async def fetch_new(self) -> list[IsirEvent]:
        last_id = self.cursor.load()

        async def call() -> str:
            await self.limiter.acquire()
            resp = await self.client.post(
                self.endpoint,
                content=build_request(last_id).encode(),
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '""'},
            )
            raise_for_retry(resp)
            return resp.text

        events = parse_events(await with_retry(call))
        if events:
            self.cursor.save(events[-1].id)
        return events

    async def sale_opportunities(self) -> list[IsirEvent]:
        return [e for e in await self.fetch_new() if e.is_sale_opportunity()]

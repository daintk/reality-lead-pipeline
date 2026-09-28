"""Centrální evidence exekucí – oficiální API Exekutorské komory ČR (ceecr.cz, sekce Pro vývojáře).

Lustrace je placená (60 Kč za výpis) a jde na registrovaný účet klienta s kreditem.
Proto se volá jen u leadů, které prošly levným filtrem, a jen se souhlasem s lustrací.
Žádný scraping webového formuláře CEE.

Komora od 1. 10. 2026 nasazuje novou verzi API – klient je stavěný na ni: cesta, klíč
i mapování odpovědi jsou v konfiguraci, ne natvrdo v kódu. `DisabledExecutionSource`
běží v demu; v produkci `CeeClient` s přístupem klienta.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Protocol

import httpx

from app.http_utils import RateLimiter, raise_for_retry, with_retry
from app.models import ExecutionCheck


class ExecutionSource(Protocol):
    name: str

    async def lookup(
        self, *, ico: str | None, full_name: str | None, birth_date: date | None
    ) -> ExecutionCheck: ...


class DisabledExecutionSource:
    name = "cee:off"

    async def lookup(self, *, ico: str | None, full_name: str | None, birth_date: date | None) -> ExecutionCheck:
        return ExecutionCheck(checked=False, reason="CEE API není nakonfigurováno (účet klienta)")


def build_query(*, ico: str | None, full_name: str | None, birth_date: date | None) -> dict[str, str]:
    """Dotaz na osobu: IČ pro firmu, jméno + datum narození pro fyzickou osobu (jinak falešné shody)."""
    if ico:
        return {"ico": ico}
    if not (full_name and birth_date):
        raise ValueError("Fyzická osoba: je potřeba jméno + datum narození")
    return {"jmeno": full_name, "datumNarozeni": birth_date.isoformat()}


def parse_response(data: Any) -> ExecutionCheck:
    """Odpověď: buď seznam exekucí, nebo objekt s počtem – obojí převedeme na jednotný výsledek."""
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        if "error" in data or "chyba" in data:
            raise RuntimeError(f"CEE chyba: {data.get('error') or data.get('chyba')}")
        items = data.get("exekuce") or data.get("items") or data.get("data") or []
        if not items and isinstance(data.get("pocet"), int):
            items = [None] * data["pocet"]
    else:
        raise RuntimeError("CEE: neočekávaný formát odpovědi")
    return ExecutionCheck(
        checked=True,
        reason="ověřeno přes CEE API",
        count=len(items),
        case_numbers=[str(i.get("spisovaZnacka") or i.get("cj") or "") for i in items if isinstance(i, dict)],
    )


class CeeClient:
    name = "cee:api"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        lookup_path: str = "/lustrace",
        rate_per_sec: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.lookup_path = lookup_path
        self.limiter = RateLimiter(rate_per_sec)
        self.client = client or httpx.AsyncClient(timeout=20.0)

    async def lookup(self, *, ico: str | None, full_name: str | None, birth_date: date | None) -> ExecutionCheck:
        params = build_query(ico=ico, full_name=full_name, birth_date=birth_date)

        async def call() -> Any:
            await self.limiter.acquire()
            resp = await self.client.get(
                self.base_url + self.lookup_path, params=params,
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            )
            raise_for_retry(resp)
            return resp.json()

        return parse_response(await with_retry(call))

"""Odeslání hotové složky do CRM (GoHighLevel Inbound Webhook, nebo jakýkoli webhook)."""
from __future__ import annotations

import logging

import httpx

from app.http_utils import raise_for_retry, with_retry
from app.models import LeadPackage

log = logging.getLogger("crm")


class CrmClient:
    def __init__(self, webhook_url: str, dry_run: bool = True, client: httpx.AsyncClient | None = None):
        self.webhook_url = webhook_url
        self.dry_run = dry_run or not webhook_url
        self.client = client or httpx.AsyncClient(timeout=10.0)

    async def send(self, package: LeadPackage) -> dict:
        payload = package.model_dump(mode="json")
        if self.dry_run:
            log.info("CRM dry-run: složka %s připravena (%d ms)", package.event_id, package.processing_ms)
            return {"status": "dry_run"}

        async def call() -> dict:
            resp = await self.client.post(
                self.webhook_url,
                json=payload,
                headers={"Idempotency-Key": package.event_id},
            )
            raise_for_retry(resp)
            return {"status": "sent", "http": resp.status_code}

        return await with_retry(call, attempts=3, base_delay=0.5, max_delay=4)

    async def send_event(self, kind: str, data: dict) -> dict:
        """Příležitost z aktivního monitoringu (dražba, prodej v insolvenci) – bez osobních údajů."""
        payload = {"type": kind, "data": data}
        if self.dry_run:
            log.info("CRM dry-run: %s %s", kind, data.get("spisova_znacka"))
            return {"status": "dry_run"}

        async def call() -> dict:
            resp = await self.client.post(self.webhook_url, json=payload)
            raise_for_retry(resp)
            return {"status": "sent", "http": resp.status_code}

        return await with_retry(call, attempts=3, base_delay=0.5, max_delay=4)

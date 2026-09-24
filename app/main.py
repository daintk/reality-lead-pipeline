"""FastAPI aplikace – příjem webhooků, monitoring zdrojů, složky do CRM, stav systému."""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from pydantic import ValidationError

from app import pdf, security
from app.config import get_settings
from app.crm import CrmClient
from app.health import HealthRegistry
from app.models import LeadIn, LeadPackage
from app.pipeline import LeadStore, Pipeline
from app.sources.auctions import FileAuctionSource
from app.sources.isir import IsirClient
from app.sources.isir_stream import CursorStore, IsirStream
from app.valuation import PriceMap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


async def isir_poller(app: FastAPI) -> None:
    """Aktivní vstup: každých N sekund stáhne nové události ISIR a prodeje majetku pošle do CRM."""
    s = app.state.settings
    stream = IsirStream(CursorStore(s.isir_stream_cursor))
    health: HealthRegistry = app.state.health
    while True:
        try:
            for ev in await stream.sale_opportunities():
                await app.state.pipeline.crm.send_event("isir_prodej_majetku", ev.__dict__)
            health.ok("isir_stream")
        except Exception as exc:  # zdroj může vypadnout – zkusíme to příště, aplikace běží dál
            log.warning("ISIR stream: %s", exc)
            health.error("isir_stream", str(exc))
        await asyncio.sleep(s.poll_interval_sec)


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if not s.webhook_secret:
        raise RuntimeError("Chybí WEBHOOK_SECRET – bez něj server nespustíme")
    app.state.settings = s
    app.state.health = HealthRegistry()
    app.state.store = LeadStore(s.retention_hours)
    app.state.pipeline = Pipeline(
        settings=s,
        isir=IsirClient(s.isir_endpoint, s.isir_rate_per_sec),
        crm=CrmClient(s.crm_webhook_url, s.crm_dry_run),
        price_map=PriceMap.from_csv(s.price_map_path),
        health=app.state.health,
    )
    app.state.auctions = FileAuctionSource(s.auctions_file)
    task = asyncio.create_task(isir_poller(app)) if s.isir_stream_enabled else None
    yield
    if task:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="Reality Lead Pipeline", version="0.2.0", lifespan=lifespan)


def _auth(x_api_key: str | None) -> None:
    if x_api_key != app.state.settings.webhook_secret:
        raise HTTPException(status_code=401, detail="neautorizováno")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/status")
async def status(x_api_key: str | None = Header(default=None)) -> dict:
    _auth(x_api_key)
    return app.state.health.snapshot()


async def _run(event_id: str, lead: LeadIn, received_at: datetime) -> None:
    store: LeadStore = app.state.store
    health: HealthRegistry = app.state.health
    try:
        package = await app.state.pipeline.process(lead, received_at)
        crm_result = await app.state.pipeline.crm.send(package)
        health.ok("crm")
        health.leads_processed += 1
        store.put(event_id, {"status": "done", "crm": crm_result, "package": package.model_dump(mode="json")})
    except Exception as exc:
        log.exception("zpracování %s selhalo", event_id)
        health.leads_failed += 1
        health.error("crm", type(exc).__name__)
        store.put(event_id, {"status": "failed", "error": type(exc).__name__})


@app.post("/webhook/lead", status_code=202)
async def webhook_lead(
    request: Request,
    background: BackgroundTasks,
    x_timestamp: str | None = Header(default=None),
    x_signature: str | None = Header(default=None),
) -> dict:
    body = await request.body()
    if not security.verify(app.state.settings.webhook_secret, x_timestamp, x_signature, body):
        raise HTTPException(status_code=401, detail="neplatný podpis")
    try:
        lead = LeadIn.model_validate(json.loads(body))
    except (ValidationError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="neplatná data") from exc

    store: LeadStore = app.state.store
    if store.seen(lead.event_id):  # idempotence – Make/n8n mohou poslat znovu
        return {"status": "duplicate", "event_id": lead.event_id}

    store.put(lead.event_id, {"status": "processing"})
    background.add_task(_run, lead.event_id, lead, datetime.now(timezone.utc))
    return {"status": "accepted", "event_id": lead.event_id}


@app.get("/leads/{event_id}")
async def lead_status(event_id: str, x_api_key: str | None = Header(default=None)) -> dict:
    _auth(x_api_key)
    item = app.state.store.get(event_id)
    if not item:
        raise HTTPException(status_code=404, detail="nenalezeno nebo již smazáno (retence)")
    return item


@app.get("/leads/{event_id}/pdf")
async def lead_pdf(event_id: str, x_api_key: str | None = Header(default=None)) -> Response:
    _auth(x_api_key)
    item = app.state.store.get(event_id)
    if not item or item.get("status") != "done":
        raise HTTPException(status_code=404, detail="složka není připravená")
    package = LeadPackage.model_validate(item["package"])
    return Response(
        content=pdf.render(package),
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="slozka-{event_id}.pdf"'},
    )


@app.post("/auctions/scan")
async def auctions_scan(min_discount: float = 0.2, x_api_key: str | None = Header(default=None)) -> dict:
    _auth(x_api_key)
    deals = await app.state.pipeline.scan_auctions(app.state.auctions, min_discount=min_discount)
    for d in deals:
        await app.state.pipeline.crm.send_event("drazba_prilezitost", d)
    return {"count": len(deals), "deals": deals}

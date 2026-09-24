"""Analytické jádro: lead -> kontrola ISIR -> ocenění -> složka -> CRM (do 60 s)."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import Settings
from app.crm import CrmClient
from app.health import HealthRegistry
from app.legal import evaluate as legal_evaluate
from app.models import Condition, InsolvencyCheck, LeadIn, LeadPackage, PropertyType
from app.privacy import mask_email, mask_name, mask_phone
from app.sources.auctions import Auction, AuctionSource
from app.sources.isir import IsirClient
from app.valuation import PriceMap, compute

log = logging.getLogger("pipeline")


class LeadStore:
    """Jednoduché úložiště s retencí (GDPR). V produkci Postgres/Redis se stejným TTL."""

    def __init__(self, retention_hours: int) -> None:
        self.retention = timedelta(hours=retention_hours)
        self._items: dict[str, tuple[datetime, dict]] = {}

    def seen(self, event_id: str) -> bool:
        self.purge()
        return event_id in self._items

    def put(self, event_id: str, status: dict) -> None:
        self._items[event_id] = (datetime.now(timezone.utc), status)

    def get(self, event_id: str) -> dict | None:
        self.purge()
        item = self._items.get(event_id)
        return item[1] if item else None

    def purge(self) -> None:
        cutoff = datetime.now(timezone.utc) - self.retention
        for k in [k for k, (ts, _) in self._items.items() if ts < cutoff]:
            del self._items[k]


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        isir: IsirClient,
        crm: CrmClient,
        price_map: PriceMap,
        health: HealthRegistry | None = None,
    ):
        self.s = settings
        self.isir = isir
        self.crm = crm
        self.price_map = price_map
        self.health = health or HealthRegistry()

    async def _insolvency(self, lead: LeadIn) -> InsolvencyCheck:
        if not self.s.isir_enabled:
            return InsolvencyCheck(checked=False, reason="ISIR vypnut v konfiguraci")
        if not lead.consent_registry_check:
            return InsolvencyCheck(checked=False, reason="bez souhlasu klienta – neověřujeme")
        try:
            if lead.ico:
                recs = await self.isir.search(ico=lead.ico)
            elif lead.birth_date:
                parts = lead.full_name.split()
                recs = await self.isir.search(
                    surname=parts[-1], first_name=parts[0] if len(parts) > 1 else None,
                    birth_date=lead.birth_date,
                )
            else:
                return InsolvencyCheck(checked=False, reason="chybí IČ nebo datum narození")
            self.health.ok("isir")
            return InsolvencyCheck(checked=True, reason="ověřeno přes ISIR WS", records=recs)
        except Exception as exc:  # výpadek zdroje nesmí shodit celý lead
            log.warning("ISIR nedostupný: %s", exc)
            self.health.error("isir", str(exc))
            return InsolvencyCheck(checked=False, reason="ISIR dočasně nedostupný – doplnit ručně")

    async def process(self, lead: LeadIn, received_at: datetime) -> LeadPackage:
        t0 = time.monotonic()
        warnings: list[str] = []

        # ISIR má vlastní časový limit, aby celé zpracování stihlo SLA.
        try:
            insolvency = await asyncio.wait_for(self._insolvency(lead), timeout=self.s.sla_seconds * 0.5)
        except asyncio.TimeoutError:
            insolvency = InsolvencyCheck(checked=False, reason="ISIR timeout – doplnit ručně")
        if not insolvency.checked:
            warnings.append(insolvency.reason)

        valuation = compute(
            price_map=self.price_map,
            municipality=lead.municipality,
            ptype=lead.property_type,
            area_m2=lead.area_m2,
            condition=lead.condition,
            debts_czk=lead.declared_debts_czk,
            discount=self.s.purchase_discount,
            fixed_costs_czk=self.s.fixed_costs_czk,
        )
        if valuation is None:
            warnings.append(f"obec '{lead.municipality}' není v cenové mapě – ocenit ručně")
        if insolvency.records:
            warnings.append(f"nalezeno {len(insolvency.records)} insolvenční řízení – prověřit")

        legal = legal_evaluate(
            insolvency_records=len(insolvency.records),
            insolvency_checked=insolvency.checked,
            has_execution=lead.declared_execution,
        )

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        package = LeadPackage(
            event_id=lead.event_id,
            received_at=received_at,
            processed_at=datetime.now(timezone.utc),
            processing_ms=elapsed_ms,
            within_sla=elapsed_ms / 1000 <= self.s.sla_seconds,
            # Minimalizace dat: posíláme jen to, co obchodník potřebuje.
            contact={
                "name": lead.full_name,
                "phone": lead.phone,
                "email": lead.email,
                "source": lead.source,
            },
            property={
                "municipality": lead.municipality,
                "street": lead.street,
                "lv_number": lead.lv_number,
                "type": lead.property_type.value,
                "area_m2": lead.area_m2,
                "condition": lead.condition.value,
            },
            insolvency=insolvency,
            valuation=valuation,
            legal=legal,
            warnings=warnings,
        )
        if not package.within_sla:
            self.health.sla_breaches += 1
        log.info(
            "lead %s zpracován za %d ms (%s, %s, %s)",
            lead.event_id, elapsed_ms, mask_name(lead.full_name), mask_phone(lead.phone), mask_email(lead.email),
        )
        return package

    async def scan_auctions(self, source: AuctionSource, min_discount: float = 0.2) -> list[dict]:
        """Projde nadcházející dražby a vrátí ty, kde je nejnižší podání výrazně pod tržní hodnotou."""
        try:
            auctions = await source.upcoming()
            self.health.ok(f"drazby:{source.name}")
        except Exception as exc:
            self.health.error(f"drazby:{source.name}", str(exc))
            raise
        deals: list[dict] = []
        for a in auctions:
            deal = self._rate_auction(a)
            if deal and deal["discount_vs_market"] >= min_discount:
                deals.append(deal)
        return sorted(deals, key=lambda d: d["discount_vs_market"], reverse=True)

    def _rate_auction(self, a: Auction) -> dict | None:
        try:
            ptype = PropertyType(a.typ)
        except ValueError:
            return None
        v = compute(
            price_map=self.price_map, municipality=a.obec, ptype=ptype, area_m2=a.plocha_m2,
            condition=Condition.puvodni, debts_czk=0, discount=0, fixed_costs_czk=0,
        )
        if v is None:
            return None
        discount = 1 - a.nejnizsi_podani_czk / v.market_value_czk
        return {
            "spisova_znacka": a.spisova_znacka,
            "obec": a.obec,
            "typ": a.typ,
            "plocha_m2": a.plocha_m2,
            "termin": a.termin.isoformat(),
            "nejnizsi_podani_czk": a.nejnizsi_podani_czk,
            "odhadni_cena_czk": a.odhadni_cena_czk,
            "trzni_hodnota_avm_czk": v.market_value_czk,
            "discount_vs_market": round(discount, 3),
            "legal": legal_evaluate(insolvency_records=0, insolvency_checked=False,
                                    has_execution=None, is_auction=True).model_dump(mode="json"),
            "url": a.url,
        }

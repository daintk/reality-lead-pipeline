"""Konfigurace z proměnných prostředí (žádná tajemství v kódu)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Sdílené tajemství pro ověření podpisu příchozích webhooků (Make / n8n).
    webhook_secret: str = field(default_factory=lambda: os.getenv("WEBHOOK_SECRET", ""))

    # Odchozí webhook do CRM (GoHighLevel -> Workflow -> Inbound Webhook trigger).
    crm_webhook_url: str = field(default_factory=lambda: os.getenv("CRM_WEBHOOK_URL", ""))
    crm_dry_run: bool = field(default_factory=lambda: _bool("CRM_DRY_RUN", True))

    # Oficiální webová služba ISIR (Ministerstvo spravedlnosti), žádný scraping.
    isir_endpoint: str = field(
        default_factory=lambda: os.getenv(
            "ISIR_ENDPOINT", "https://isir.justice.cz:8443/isir_cuzk_ws/IsirWsCuzkService"
        )
    )
    isir_enabled: bool = field(default_factory=lambda: _bool("ISIR_ENABLED", False))
    isir_rate_per_sec: float = field(default_factory=lambda: float(os.getenv("ISIR_RATE_PER_SEC", "1")))

    # Stream událostí ISIR (nové insolvence / prodeje majetku).
    isir_stream_enabled: bool = field(default_factory=lambda: _bool("ISIR_STREAM_ENABLED", False))
    isir_stream_cursor: str = field(default_factory=lambda: os.getenv("ISIR_STREAM_CURSOR", "state/isir_cursor.json"))
    poll_interval_sec: int = field(default_factory=lambda: int(os.getenv("POLL_INTERVAL_SEC", "300")))

    # Zdroj dražeb (vyměnitelný adaptér). V demu ukázková data.
    auctions_file: str = field(default_factory=lambda: os.getenv("AUCTIONS_FILE", "data/auctions.sample.json"))

    # Cenová mapa (CSV). V produkci nahradit licencovaným API poskytovatele.
    price_map_path: str = field(
        default_factory=lambda: os.getenv("PRICE_MAP_PATH", "data/price_map.sample.csv")
    )

    # Parametry výpočtu doporučené nákupní ceny.
    purchase_discount: float = field(default_factory=lambda: float(os.getenv("PURCHASE_DISCOUNT", "0.25")))
    fixed_costs_czk: int = field(default_factory=lambda: int(os.getenv("FIXED_COSTS_CZK", "60000")))

    # SLA: celá složka musí být v CRM do 60 s od přijetí impulsu.
    sla_seconds: float = field(default_factory=lambda: float(os.getenv("SLA_SECONDS", "60")))

    # GDPR: jak dlouho držet zpracované leady v paměti/úložišti.
    retention_hours: int = field(default_factory=lambda: int(os.getenv("RETENTION_HOURS", "72")))


def get_settings() -> Settings:
    return Settings()

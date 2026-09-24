"""Datové modely – validace vstupu i výstupu."""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.legal import LegalCheck


class PropertyType(str, Enum):
    byt = "byt"
    dum = "dum"
    pozemek = "pozemek"


class Condition(str, Enum):
    novostavba = "novostavba"
    dobry = "dobry"
    puvodni = "puvodni"
    k_rekonstrukci = "k_rekonstrukci"


class LeadIn(BaseModel):
    """Lead z formuláře / Meta Lead Ads (přes Make nebo n8n).

    Majitel sám vyplnil formulář a souhlasil se zpracováním – to je čistý právní základ.
    """

    event_id: str = Field(min_length=6, max_length=100, description="ID události pro idempotenci")
    source: str = Field(default="web_form", max_length=50)
    full_name: str = Field(min_length=2, max_length=120)
    phone: str = Field(min_length=9, max_length=20)
    email: EmailStr | None = None
    birth_date: date | None = Field(default=None, description="Jen pokud dal souhlas s lustrací")
    ico: str | None = Field(default=None, pattern=r"^\d{8}$")

    municipality: str = Field(min_length=2, max_length=80)
    street: str | None = Field(default=None, max_length=120)
    lv_number: str | None = Field(default=None, max_length=10, description="Číslo listu vlastnictví")
    property_type: PropertyType
    area_m2: float = Field(gt=5, lt=100_000)
    condition: Condition = Condition.dobry
    declared_debts_czk: int = Field(default=0, ge=0, description="Dluhy, které uvedl sám majitel")
    declared_execution: bool | None = Field(default=None, description="Majitel uvedl exekuci (None = neví/neuvedl)")

    consent_processing: bool = Field(description="Souhlas se zpracováním osobních údajů")
    consent_registry_check: bool = Field(
        default=False, description="Souhlas s ověřením v insolvenčním rejstříku"
    )

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, v: str) -> str:
        digits = "".join(ch for ch in v if ch.isdigit() or ch == "+")
        if len(digits.lstrip("+")) < 9:
            raise ValueError("neplatné telefonní číslo")
        return digits

    @field_validator("consent_processing")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if not v:
            raise ValueError("bez souhlasu se zpracováním lead nezpracujeme")
        return v


class InsolvencyRecord(BaseModel):
    spisova_znacka: str
    stav: str | None = None
    url: str | None = None


class InsolvencyCheck(BaseModel):
    checked: bool
    reason: str
    records: list[InsolvencyRecord] = []


class Valuation(BaseModel):
    price_per_m2: int
    market_value_czk: int
    discount: float
    debts_czk: int
    fixed_costs_czk: int
    recommended_purchase_czk: int
    price_map_source: str


class LeadPackage(BaseModel):
    """Kompletní „složka“, která odchází do CRM."""

    event_id: str
    received_at: datetime
    processed_at: datetime
    processing_ms: int
    within_sla: bool
    contact: dict
    property: dict
    insolvency: InsolvencyCheck
    valuation: Valuation | None
    legal: LegalCheck | None = None
    warnings: list[str] = []

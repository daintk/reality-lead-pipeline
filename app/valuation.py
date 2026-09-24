"""AVM kalkulátor: tržní hodnota podle cenové mapy -> doporučená nákupní cena.

Vzorec (parametry v .env):
    tržní_hodnota    = plocha_m2 * cena_za_m2(obec, typ) * koeficient_stavu
    doporučená_cena  = tržní_hodnota * (1 - sleva) - dluhy - fixní_náklady
"""
from __future__ import annotations

import csv
import unicodedata
from pathlib import Path

from app.models import Condition, PropertyType, Valuation

CONDITION_COEF: dict[Condition, float] = {
    Condition.novostavba: 1.10,
    Condition.dobry: 1.00,
    Condition.puvodni: 0.88,
    Condition.k_rekonstrukci: 0.75,
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


class PriceMap:
    """Cenová mapa: (obec, typ) -> Kč/m². Zdroj lze vyměnit za licencované API."""

    def __init__(self, prices: dict[tuple[str, str], int], source: str) -> None:
        self.prices = prices
        self.source = source

    @classmethod
    def from_csv(cls, path: str | Path) -> "PriceMap":
        prices: dict[tuple[str, str], int] = {}
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                prices[(_norm(row["obec"]), row["typ"].strip())] = int(row["cena_m2"])
        return cls(prices, source=f"csv:{Path(path).name}")

    def price_per_m2(self, municipality: str, ptype: PropertyType) -> int | None:
        return self.prices.get((_norm(municipality), ptype.value))


def compute(
    *,
    price_map: PriceMap,
    municipality: str,
    ptype: PropertyType,
    area_m2: float,
    condition: Condition,
    debts_czk: int,
    discount: float,
    fixed_costs_czk: int,
) -> Valuation | None:
    ppm2 = price_map.price_per_m2(municipality, ptype)
    if ppm2 is None:
        return None
    market = round(area_m2 * ppm2 * CONDITION_COEF[condition])
    recommended = max(0, round(market * (1 - discount) - debts_czk - fixed_costs_czk))
    return Valuation(
        price_per_m2=ppm2,
        market_value_czk=market,
        discount=discount,
        debts_czk=debts_czk,
        fixed_costs_czk=fixed_costs_czk,
        recommended_purchase_czk=recommended,
        price_map_source=price_map.source,
    )

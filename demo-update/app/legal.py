"""Právní semafor – upozorní, kdy nemovitost nejde koupit běžnou cestou.

Není to právní rada: je to filtr, který obchodníkovi řekne „tady se zastav a ověř“.
Konečné posouzení dělá vždy právník klienta.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class Light(str, Enum):
    green = "zelena"
    orange = "oranzova"
    red = "cervena"


class LegalCheck(BaseModel):
    light: Light
    reasons: list[str]
    next_step: str


def evaluate(
    *,
    insolvency_records: int,
    insolvency_checked: bool,
    has_execution: bool | None,
    is_auction: bool = False,
) -> LegalCheck:
    if is_auction:
        return LegalCheck(
            light=Light.green,
            reasons=["nemovitost se prodává v dražbě – standardní cesta k nabytí"],
            next_step="zkontrolovat dražební vyhlášku, jistotu a termín",
        )
    if insolvency_records > 0:
        return LegalCheck(
            light=Light.red,
            reasons=["vlastník je v insolvenčním řízení – o majetku rozhoduje insolvenční správce"],
            next_step="jednat s insolvenčním správcem, ne s vlastníkem",
        )
    if has_execution:
        return LegalCheck(
            light=Light.red,
            reasons=["na vlastníka je vedena exekuce – nakládání s majetkem je omezené"],
            next_step="neplatit zálohu; ověřit postup s exekutorem a právníkem",
        )
    reasons: list[str] = []
    if not insolvency_checked:
        reasons.append("insolvence neověřena")
    if has_execution is None:
        reasons.append("exekuce neověřeny")
    if reasons:
        return LegalCheck(light=Light.orange, reasons=reasons, next_step="doplnit lustraci před nabídkou ceny")
    return LegalCheck(light=Light.green, reasons=["bez nalezených omezení"], next_step="pokračovat v jednání")

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
    cadastre_flags: list[str] | None = None,
    cadastre_checked: bool | None = None,
) -> LegalCheck:
    """`cadastre_flags` = riziková omezení z části C listu vlastnictví (ČÚZK); None = katastr nedotazován."""
    flags = cadastre_flags or []
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
            reasons=["na vlastníka je vedena exekuce (CEE) – nakládání s majetkem je omezené"],
            next_step="neplatit zálohu; ověřit postup s exekutorem a právníkem",
        )
    hard = [f for f in flags if any(k in f.lower() for k in ("exeku", "insolven"))]
    if hard:
        return LegalCheck(
            light=Light.red,
            reasons=[f"na listu vlastnictví je zapsáno: {f}" for f in hard],
            next_step="neplatit zálohu; ověřit s exekutorem / správcem a právníkem",
        )
    reasons: list[str] = []
    if not insolvency_checked:
        reasons.append("insolvence neověřena")
    if has_execution is None:
        reasons.append("exekuce neověřeny")
    if cadastre_checked is False:
        reasons.append("list vlastnictví neověřen v katastru")
    for f in flags:  # zástava, předkupní právo, věcné břemeno, plomba – prodej jde, ale s podmínkami
        reasons.append(f"na LV zapsáno: {f}")
    if reasons:
        return LegalCheck(light=Light.orange, reasons=reasons, next_step="doplnit lustraci před nabídkou ceny")
    ok = "insolvence ani exekuce nenalezeny" + (", LV bez rizikových zápisů" if cadastre_checked else "")
    return LegalCheck(light=Light.green, reasons=[ok], next_step="pokračovat v jednání")

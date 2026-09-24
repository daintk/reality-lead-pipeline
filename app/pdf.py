"""PDF složka leadu – jedna strana pro obchodníka: nemovitost, výpočet, rizika."""
from __future__ import annotations

import logging
from pathlib import Path

from fpdf import FPDF

from app.models import LeadPackage

logging.getLogger("fontTools").setLevel(logging.WARNING)

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
LIGHT_COLORS = {"zelena": (34, 139, 34), "oranzova": (230, 140, 0), "cervena": (200, 30, 30)}
LABELS = {"byt": "byt", "dum": "dům", "pozemek": "pozemek", "novostavba": "novostavba", "dobry": "dobrý",
          "puvodni": "původní", "k_rekonstrukci": "k rekonstrukci"}
LIGHT_LABELS = {"zelena": "ZELENÁ", "oranzova": "ORANŽOVÁ", "cervena": "ČERVENÁ"}


def _czk(v: int | None) -> str:
    return "–" if v is None else f"{v:,} Kč".replace(",", " ")


def render(package: LeadPackage) -> bytes:
    pdf = FPDF(format="A4")
    pdf.add_font("DejaVu", "", str(FONT_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONT_DIR / "DejaVuSans-Bold.ttf"))
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()

    pdf.set_font("DejaVu", "B", 18)
    pdf.cell(0, 10, "Složka leadu", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(110, 110, 110)
    pdf.cell(
        0, 5,
        f"ID {package.event_id} · přijato {package.received_at:%d.%m.%Y %H:%M} UTC · "
        f"zpracováno za {package.processing_ms} ms",
        new_x="LMARGIN", new_y="NEXT",
    )
    pdf.set_text_color(0, 0, 0)
    pdf.ln(4)

    if package.legal:
        r, g, b = LIGHT_COLORS[package.legal.light.value]
        pdf.set_fill_color(r, g, b)
        pdf.set_text_color(255, 255, 255)
        pdf.set_font("DejaVu", "B", 12)
        pdf.cell(0, 9, f"  Právní semafor: {LIGHT_LABELS[package.legal.light.value]}", fill=True,
                 new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(0, 0, 0)
        pdf.set_font("DejaVu", "", 10)
        for reason in package.legal.reasons:
            pdf.multi_cell(0, 6, f"• {reason}", new_x="LMARGIN", new_y="NEXT")
        pdf.multi_cell(0, 6, f"Další krok: {package.legal.next_step}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(3)

    def section(title: str, rows: list[tuple[str, str]]) -> None:
        pdf.set_font("DejaVu", "B", 12)
        pdf.cell(0, 8, title, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("DejaVu", "", 10)
        for k, v in rows:
            pdf.cell(55, 6, k)
            pdf.multi_cell(0, 6, v, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

    p = package.property
    section("Nemovitost", [
        ("Obec", str(p.get("municipality") or "–")),
        ("Ulice", str(p.get("street") or "–")),
        ("Číslo LV", str(p.get("lv_number") or "–")),
        ("Typ / plocha", f"{LABELS.get(p.get('type'), p.get('type'))} · {p.get('area_m2'):g} m²"),
        ("Stav", LABELS.get(p.get("condition"), str(p.get("condition")))),
    ])
    c = package.contact
    section("Kontakt", [
        ("Jméno", str(c.get("name") or "–")),
        ("Telefon", str(c.get("phone") or "–")),
        ("E-mail", str(c.get("email") or "–")),
        ("Zdroj", str(c.get("source") or "–")),
    ])
    v = package.valuation
    section("Ocenění", [
        ("Cena za m²", _czk(v.price_per_m2) if v else "–"),
        ("Tržní hodnota", _czk(v.market_value_czk) if v else "neoceněno"),
        ("Dluhy", _czk(v.debts_czk) if v else "–"),
        ("Doporučená nákupní cena", _czk(v.recommended_purchase_czk) if v else "–"),
        ("Zdroj cen", v.price_map_source if v else "–"),
    ])
    if package.warnings:
        section("Upozornění", [("", w) for w in package.warnings])

    pdf.set_font("DejaVu", "", 8)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 4, "Ocenění je orientační a nenahrazuje znalecký posudek. "
                         "Právní semafor není právní rada – před nákupem ověřte s právníkem.")
    return bytes(pdf.output())

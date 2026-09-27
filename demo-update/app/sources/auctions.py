"""Zdroj dražeb nemovitostí – vyměnitelný adaptér.

Jádro nezajímá, odkud dražby přijdou. V produkci se sem zapojí zdroj, ke kterému má
klient právo (licencovaný datový feed, datový přístup od provozovatele portálu apod.).
V demu čteme ukázková data ze souboru.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Auction:
    spisova_znacka: str
    obec: str
    typ: str  # byt / dum / pozemek
    plocha_m2: float
    odhadni_cena_czk: int
    nejnizsi_podani_czk: int
    termin: datetime
    url: str | None = None


class AuctionSource(Protocol):
    name: str

    async def upcoming(self) -> list[Auction]: ...


class FileAuctionSource:
    name = "file"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    async def upcoming(self) -> list[Auction]:
        rows = json.loads(self.path.read_text(encoding="utf-8"))
        return [
            Auction(**{**r, "termin": datetime.fromisoformat(r["termin"].replace("Z", "+00:00"))})
            for r in rows
        ]

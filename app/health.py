"""Stav zdrojů pro dashboard /status – klient vidí, že všechno běží."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class SourceHealth:
    name: str
    last_ok: datetime | None = None
    last_error: datetime | None = None
    last_error_msg: str | None = None
    ok_count: int = 0
    error_count: int = 0

    @property
    def state(self) -> str:
        if self.last_ok is None and self.last_error is None:
            return "ceka"
        if self.last_error and (self.last_ok is None or self.last_error > self.last_ok):
            return "chyba"
        return "ok"


@dataclass
class HealthRegistry:
    sources: dict[str, SourceHealth] = field(default_factory=dict)
    leads_processed: int = 0
    leads_failed: int = 0
    sla_breaches: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def _get(self, name: str) -> SourceHealth:
        return self.sources.setdefault(name, SourceHealth(name))

    def ok(self, name: str) -> None:
        s = self._get(name)
        s.last_ok = datetime.now(timezone.utc)
        s.ok_count += 1

    def error(self, name: str, msg: str) -> None:
        s = self._get(name)
        s.last_error = datetime.now(timezone.utc)
        s.last_error_msg = msg[:200]
        s.error_count += 1

    def snapshot(self) -> dict:
        states = [s.state for s in self.sources.values()]
        overall = "chyba" if "chyba" in states else "ok"
        return {
            "overall": overall,
            "uptime_since": self.started_at.isoformat(),
            "leads": {"processed": self.leads_processed, "failed": self.leads_failed,
                      "sla_breaches": self.sla_breaches},
            "sources": {
                n: {"state": s.state, "ok": s.ok_count, "errors": s.error_count,
                    "last_ok": s.last_ok.isoformat() if s.last_ok else None,
                    "last_error": s.last_error_msg}
                for n, s in self.sources.items()
            },
        }

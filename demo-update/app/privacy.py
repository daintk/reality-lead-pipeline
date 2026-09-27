"""GDPR pomocníci: do logů nikdy nejdou celé osobní údaje."""
from __future__ import annotations


def mask_name(name: str) -> str:
    parts = name.split()
    return " ".join(p[0] + "***" for p in parts if p)


def mask_phone(phone: str) -> str:
    return phone[:-6] + "***" + phone[-3:] if len(phone) > 6 else "***"


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, domain = email.split("@", 1)
    return local[:1] + "***@" + domain

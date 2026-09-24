# Reality Lead Pipeline

Tohle je automatizovaný analytický backend pro investice do nemovitostí. Hlídá registry, přijímá leady z reklam, oceňuje nemovitosti podle cenové mapy, upozorní na právní rizika a hotovou složku (JSON + PDF) pošle do CRM GoHighLevel **pod 60 sekund**.

Data bere jen z oficiálních rozhraní. Nic neobchází CAPTCHA ani ochrany, takže ho nemá kdo zablokovat.

![CI](../../actions/workflows/ci.yml/badge.svg)

```
 AKTIVNÍ VSTUPY                          PASIVNÍ VSTUP
 ┌─────────────────────┐ ┌────────────┐  ┌──────────────────────────┐
 │ ISIR stream událostí│ │ Dražby     │  │ Formulář / Meta Lead Ads │
 │ (oficiální SOAP WS) │ │ (adaptér)  │  │  → Make / n8n → webhook  │
 └──────────┬──────────┘ └─────┬──────┘  └────────────┬─────────────┘
            └──────────────────┼──────────────────────┘
                               ▼
                 ┌───────────────────────────┐
                 │     ANALYTICKÉ JÁDRO      │
                 │ ISIR lustrace (se souhl.) │
                 │ AVM ocenění               │
                 │ Právní semafor 🟢🟠🔴      │
                 │ Složka JSON + PDF         │
                 └─────────────┬─────────────┘
                               ▼
              GoHighLevel  ·  /status dashboard
```

## Funkce

| Oblast | Řešení |
|---|---|
| **Webhook API** | FastAPI, `202 Accepted` okamžitě, zpracování na pozadí, měření SLA 60 s |
| **ISIR stream** | oficiální webová služba ISIR, kurzor v souboru (po restartu se nic neztratí), filtr událostí „zpeněžení / dražba / prodej“ |
| **Dražby** | vyměnitelný adaptér, řazení podle slevy nejnižšího podání proti tržní hodnotě |
| **Ocenění (AVM)** | cenová mapa × plocha × koeficient stavu, parametry v `.env` |
| **Právní semafor** | insolvence / exekuce → červená a doporučený další krok (správce, exekutor) |
| **PDF složka** | jednostránkový podklad pro obchodníka s českou diakritikou |
| **CRM** | GoHighLevel Inbound Webhook, `Idempotency-Key`, retry, dry-run |
| **Odolnost** | token-bucket rate limit, exponenciální backoff, výpadek zdroje neshodí lead, `/status` se stavem zdrojů |
| **Bezpečnost** | HMAC-SHA256 podpis webhooků, ochrana proti replay, API klíč, tajemství jen v `.env` |
| **GDPR** | zpracování jen se souhlasem, lustrace jen s extra souhlasem, maskované logy, automatické mazání (retence) |
| **Kvalita** | 19 testů (pytest), ruff, GitHub Actions CI, Docker (non-root) |

## API

| Metoda | Cesta | Popis |
|---|---|---|
| `POST` | `/webhook/lead` | příjem leadu (hlavičky `X-Timestamp`, `X-Signature`) |
| `GET` | `/leads/{id}` | stav a kompletní složka (JSON) |
| `GET` | `/leads/{id}/pdf` | PDF složka ([ukázka](docs/ukazka-slozky.pdf)) |
| `POST` | `/auctions/scan` | projde dražby, vrátí a pošle do CRM ty nejvýhodnější |
| `GET` | `/status` | dashboard: stav zdrojů, počty leadů, porušení SLA |
| `GET` | `/health` | health-check pro monitoring |

Interaktivní dokumentace běží po spuštění na `/docs`.

## Vzorec ocenění

```
tržní_hodnota   = plocha_m2 × cena_za_m2(obec, typ) × koeficient_stavu
doporučená_cena = tržní_hodnota × (1 − sleva) − dluhy − fixní_náklady      (min. 0)
sleva_dražby    = 1 − nejnižší_podání / tržní_hodnota
```

## Spuštění

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # vyplň WEBHOOK_SECRET
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload
python -m scripts.send_test_lead   # v druhém terminálu
```

Testy: `pytest -q` · Docker: `docker build -t lead-pipeline . && docker run --env-file .env -p 8000:8000 lead-pipeline`

## Zdroje dat v produkci

| Zdroj | Přístup |
|---|---|
| Insolvenční rejstřík | oficiální webová služba ISIR (zdarma) |
| Katastr nemovitostí | webové služby dálkového přístupu ČÚZK (WSDP), účet klienta |
| Centrální evidence exekucí | oficiální placená lustrace, jen u konkrétních leadů |
| Dražby | zdroj, ke kterému má klient licenci / datový přístup |
| Cenová mapa | licencované API nebo vlastní data klienta |

Autor: Daniel Andrijčuk · [ruststudio.cz](https://ruststudio.cz) · Licence MIT

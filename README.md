# Reality Lead Pipeline

Tohle je automatizovaný analytický backend pro investice do nemovitostí. Hlídá registry, přijímá leady z reklam, oceňuje nemovitosti podle cenové mapy, upozorní na právní rizika a hotovou složku (JSON + PDF) pošle do CRM GoHighLevel **pod 60 sekund**.

Data bere jen z oficiálních rozhraní. Nic neobchází CAPTCHA ani ochrany, takže ho nemá kdo zablokovat.

![CI](../../actions/workflows/ci.yml/badge.svg)

## Zadání vs. řešení

| Zadání | Řešení v tomto repu |
|---|---|
| Automatické stahování dat z ČÚZK, CEE, ISIR | **Oficiální strojová rozhraní** týchž registrů: ISIR WS (zdarma), ČÚZK WSDP (100 Kč/LV), CEE API (60 Kč/výpis) – `app/sources/` |
| Proxy, IP rotace, obcházení CAPTCHA, rate-limity pro chod 24/7 | Není co obcházet – oficiální rozhraní IP neblokuje. Šetrný rate limiting, retry s backoffem, fronta, `/status` – `app/http_utils.py` |
| Webhook přijímač (FastAPI) pro formuláře / Meta Ads přes Make/n8n | `POST /webhook/lead`, HMAC podpis, ochrana proti replay, idempotence, `202` okamžitě – `app/main.py` |
| AVM & DTV kalkulátor, spárování s cenovou mapou, výpočet nákupní ceny | Vyměnitelný zdroj cen (CSV / API klienta), vzorce v `.env` – `app/valuation.py` |
| Složka JSON/PDF do GoHighLevel do 60 s | `app/crm.py` + `app/pdf.py`, měření SLA, `Idempotency-Key`, retry |
| Architektura, která se nezhroutí při prvním zablokování IP | Výpadek kteréhokoli zdroje lead neshodí – složka odejde s upozorněním „doplnit ručně“ |

**Proč ne scraping katastru:** ČÚZK ve svých podmínkách automatizované vytěžování aplikace Nahlížení do KN zakazuje. Bot na investice v milionech nemůže stát na něčem, co může úřad zítra vypnout – a proxy + anti-CAPTCHA služby stojí měsíčně víc než placené výpisy jen u prověřených leadů.

**Kde scraping zůstává:** Portál dražeb má API jen pro exekutorské úřady, ne pro čtení. Veřejný seznam dražeb se proto čte šetrně jako běžný prohlížeč – izolovaný adaptér `AuctionSource`, který se při změně zdroje upraví sám o sobě.

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
                 │ AVM ocenění → levný filtr │
                 │ ČÚZK WSDP + CEE (placené) │
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
| **Katastr (ČÚZK)** | oficiální WSDP (SOAP, WS-Security), výpis LV v XML → vlastníci a omezení z části C; rizikové zápisy (exekuce, zástava, plomba…) jdou do semaforu |
| **Exekuce (CEE)** | oficiální API Exekutorské komory, lustrace podle IČ nebo jméno + datum narození; ověřený údaj má přednost před tím, co uvedl majitel |
| **Levný filtr** | placené výpisy (100 + 60 Kč) až po ocenění a jen nad prahem tržní hodnoty (`PAID_LOOKUP_MIN_VALUE_CZK`) a se souhlasem – náklady pod kontrolou |
| **Dražby** | vyměnitelný adaptér, řazení podle slevy nejnižšího podání proti tržní hodnotě |
| **Ocenění (AVM)** | cenová mapa × plocha × koeficient stavu, parametry v `.env` |
| **Právní semafor** | insolvence / exekuce (CEE nebo LV) → červená; zástava, předkupní právo, plomba → oranžová; vždy doporučený další krok |
| **PDF složka** | jednostránkový podklad pro obchodníka s českou diakritikou |
| **CRM** | GoHighLevel Inbound Webhook, `Idempotency-Key`, retry, dry-run |
| **Odolnost** | token-bucket rate limit, exponenciální backoff, výpadek zdroje neshodí lead, `/status` se stavem zdrojů |
| **Bezpečnost** | HMAC-SHA256 podpis webhooků, ochrana proti replay, API klíč, tajemství jen v `.env` |
| **GDPR** | zpracování jen se souhlasem, lustrace jen s extra souhlasem, maskované logy, automatické mazání (retence) |
| **Kvalita** | 29 testů (pytest), ruff, GitHub Actions CI, Docker (non-root) |

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
| Katastr nemovitostí | ČÚZK WSDP 3.1 (XML), zákaznický účet klienta, 100 Kč za LV; bezplatné zkušební prostředí – `app/sources/cuzk.py` |
| Centrální evidence exekucí | oficiální API EK ČR (registrace + kredit), 60 Kč za výpis; nová verze API od 1. 10. 2026 – `app/sources/cee.py` |
| Dražby | veřejný seznam Portálu dražeb (API pro čtení neexistuje), šetrné čtení; další zdroje jako samostatné adaptéry – `app/sources/auctions.py` |
| Cenová mapa / DTV | zdroj klienta (API nebo data) – vyměnitelný modul `app/valuation.py` |

Bez přístupů klienta běží ČÚZK a CEE ve stavu „vypnuto“: složka i semafor to výslovně uvedou (oranžová, „doplnit lustraci“), nic se nepředstírá.

Autor: Daniel Andrijčuk · Všechna práva vyhrazena

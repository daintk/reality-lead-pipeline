"""Veřejné živé demo – klient si pipeline vyzkouší v prohlížeči.

Zapíná se jen přes DEMO_MODE=true. Nesbírá žádné osobní údaje (kontakt je ukázkový),
nic neposílá do CRM a má jednoduchý limit požadavků na IP adresu.
"""
from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from app import pdf
from app.models import Condition, InsolvencyCheck, InsolvencyRecord, LeadIn, LeadPackage, PropertyType

router = APIRouter()

_hits: dict[str, deque] = defaultdict(deque)
LIMIT_PER_MIN = 20


def _rate_limit(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= LIMIT_PER_MIN:
        raise HTTPException(status_code=429, detail="Příliš mnoho požadavků, zkuste to za minutu.")
    q.append(now)


class DemoLead(BaseModel):
    municipality: str = Field(min_length=2, max_length=80)
    property_type: PropertyType
    area_m2: float = Field(gt=5, lt=100_000)
    condition: Condition
    declared_debts_czk: int = Field(default=0, ge=0, le=1_000_000_000)
    declared_execution: bool | None = None
    simulate_insolvency: bool = False


@router.get("/", response_class=HTMLResponse)
async def demo_page(request: Request) -> str:
    cities = sorted({obec for obec, _ in request.app.state.pipeline.price_map.prices})
    options = "".join(
        f'<option value="{c}"{" selected" if c == "kladno" else ""}>{c.title()}</option>' for c in cities
    )
    return PAGE.replace("{{CITIES}}", options)


@router.post("/demo/lead")
async def demo_lead(data: DemoLead, request: Request) -> dict:
    _rate_limit(request)
    event_id = f"demo-{uuid.uuid4().hex[:10]}"
    lead = LeadIn(
        event_id=event_id,
        source="demo",
        full_name="Ukázkový Klient",
        phone="+420000000000",
        email="demo@example.com",
        consent_processing=True,
        **data.model_dump(exclude={"simulate_insolvency"}),
    )
    # V demu nelustrujeme skutečné osoby – odpověď ISIR simulujeme.
    # V ostrém provozu jde dotaz na oficiální webovou službu ISIR (app/sources/isir.py).
    records = (
        [InsolvencyRecord(spisova_znacka="KSPH 60 INS 12345/2026", stav="MORATORIUM")]
        if data.simulate_insolvency else []
    )
    insolvency = InsolvencyCheck(checked=True, reason="ověřeno v ISIR (v demu simulace)", records=records)
    package = await request.app.state.pipeline.process(
        lead, datetime.now(timezone.utc), insolvency_override=insolvency
    )
    request.app.state.store.put(event_id, {"status": "done", "package": package.model_dump(mode="json")})
    return package.model_dump(mode="json")


@router.get("/demo/pdf/{event_id}")
async def demo_pdf(event_id: str, request: Request) -> Response:
    if not event_id.startswith("demo-"):
        raise HTTPException(status_code=404)
    item = request.app.state.store.get(event_id)
    if not item:
        raise HTTPException(status_code=404, detail="Složka už vypršela, vytvořte novou.")
    package = LeadPackage.model_validate(item["package"])
    return Response(pdf.render(package), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="slozka-{event_id}.pdf"'})


@router.get("/demo/auctions")
async def demo_auctions(request: Request) -> dict:
    _rate_limit(request)
    p = request.app.state.pipeline
    deals = await p.scan_auctions(request.app.state.auctions, min_discount=0.0)
    return {"deals": deals}


@router.get("/demo/status")
async def demo_status(request: Request) -> dict:
    return request.app.state.health.snapshot()


PAGE = """<!doctype html>
<html lang="cs"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Reality Lead Pipeline – živé demo</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--text:#16181d;--muted:#5b6270;--line:#e3e6eb;--accent:#1f5eff;
--green:#1a8f3a;--orange:#d97a00;--red:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--text:#e8eaef;--muted:#9aa1ad;--line:#2a2f3a;--accent:#6b93ff}}
*{box-sizing:border-box}body{margin:0;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--text)}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:28px;margin:0 0 4px}h2{font-size:19px;margin:0 0 12px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px;margin-top:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
label{display:block;font-size:13px;color:var(--muted);margin-bottom:4px}
input,select{width:100%;padding:10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--text);font-size:15px}
button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:11px 18px;font-size:15px;font-weight:600;cursor:pointer}
button.ghost{background:transparent;color:var(--accent);border:1px solid var(--accent)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-top:8px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:12px}.kpi b{display:block;font-size:22px}
.light{display:inline-block;padding:4px 10px;border-radius:999px;color:#fff;font-weight:600;font-size:14px}
.zelena{background:var(--green)}.oranzova{background:var(--orange)}.cervena{background:var(--red)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:8px;border-bottom:1px solid var(--line)}
.tablewrap{overflow-x:auto}.hidden{display:none}.steps{margin:0;padding-left:20px}
</style></head><body><div class="wrap">
<h1>Reality Lead Pipeline</h1>
<div class="muted">Živé demo: lead → ocenění → právní semafor → složka pro CRM. Ukázková data, žádné skutečné osoby.</div>

<div class="card"><h2>1. Pošlete lead jako z formuláře nebo Meta Ads</h2>
<div class="grid">
<div><label>Obec</label><select id="city">{{CITIES}}</select></div>
<div><label>Typ</label><select id="type"><option value="byt">byt</option><option value="dum">dům</option><option value="pozemek">pozemek</option></select></div>
<div><label>Plocha (m²)</label><input id="area" type="number" value="68" min="6"></div>
<div><label>Stav</label><select id="cond"><option value="novostavba">novostavba</option><option value="dobry">dobrý</option><option value="puvodni" selected>původní</option><option value="k_rekonstrukci">k rekonstrukci</option></select></div>
<div><label>Dluhy (Kč)</label><input id="debts" type="number" value="300000" min="0"></div>
<div><label>Insolvence v ISIR (simulace)</label><select id="ins"><option value="false" selected>bez záznamu</option><option value="true">probíhá řízení</option></select></div>
<div><label>Exekuce v CEE (simulace)</label><select id="exe"><option value="false" selected>bez záznamu</option><option value="true">vedena exekuce</option></select></div>
</div>
<div class="row"><button id="send">Zpracovat lead</button><span id="err" class="muted"></span></div>
</div>

<div id="result" class="card hidden"><h2>2. Hotová složka</h2>
<div class="row" style="margin-top:0"><span id="light" class="light"></span><span id="next" class="muted"></span></div>
<ol id="steps" style="list-style:none;padding:0;margin:4px 0 14px;line-height:1.9"></ol>
<div class="kpis">
<div class="kpi"><span class="muted">Tržní hodnota</span><b id="market"></b></div>
<div class="kpi"><span class="muted">Doporučená nákupní cena</span><b id="buy"></b></div>
<div class="kpi"><span class="muted">Cena za m²</span><b id="ppm"></b></div>
<div class="kpi"><span class="muted">Zpracováno za</span><b id="ms"></b></div>
</div>
<div class="row"><a id="pdf" target="_blank"><button class="ghost">Otevřít PDF složku</button></a>
<span class="muted">V ostrém provozu odchází do GoHighLevel automaticky.</span></div>
<div class="muted" style="margin-top:10px;font-size:14px">Insolvence (ISIR) a exekuce (CEE) se v ostrém provozu ověřují automaticky přes oficiální rozhraní. V demu jsou odpovědi simulované, protože nelustrujeme skutečné osoby.</div>
</div>

<div class="card"><h2>3. Dražební příležitosti seřazené podle slevy</h2>
<div class="muted">Nejnižší podání porovnané s tržní hodnotou z cenové mapy. V demu ukázková data.</div>
<div class="row"><button class="ghost" id="scan">Projít dražby</button></div>
<div class="tablewrap"><table id="deals" class="hidden"><thead><tr><th>Spis. značka</th><th>Obec</th><th>Typ</th><th>m²</th><th>Nejnižší podání</th><th>Tržní hodnota</th><th>Sleva</th><th>Termín</th></tr></thead><tbody></tbody></table></div>
</div>

<div class="card"><h2>Jak to funguje v ostrém provozu</h2>
<ol class="steps">
<li>Lead z formuláře / Meta Ads přijde přes Make nebo n8n na podepsaný webhook.</li>
<li>Systém ověří insolvenci (oficiální webová služba ISIR), případně katastr a exekuce přes oficiální rozhraní.</li>
<li>Ocení nemovitost podle cenové mapy a vašich vzorců a vyhodnotí právní rizika.</li>
<li>Do 60 sekund pošle složku (JSON + PDF) do GoHighLevel.</li>
</ol>
<div class="muted" style="margin-top:10px">Kód: <a href="https://github.com/daintk/reality-lead-pipeline">github.com/daintk/reality-lead-pipeline</a> · Autor: Daniel Andrijčuk</div>
</div>
</div>
<script>
const czk=v=>v==null?'–':new Intl.NumberFormat('cs-CZ').format(v)+' Kč';
const labels={zelena:'ZELENÁ',oranzova:'ORANŽOVÁ',cervena:'ČERVENÁ'};
document.getElementById('send').onclick=async()=>{
 const err=document.getElementById('err');err.textContent='Zpracovávám…';
 const exe=document.getElementById('exe').value;
 const body={municipality:document.getElementById('city').value,property_type:document.getElementById('type').value,
  area_m2:+document.getElementById('area').value,condition:document.getElementById('cond').value,
  declared_debts_czk:+document.getElementById('debts').value||0,declared_execution:exe===''?null:exe==='true',simulate_insolvency:document.getElementById('ins').value==='true'};
 try{const r=await fetch('/demo/lead',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();if(!r.ok)throw new Error(d.detail&&d.detail[0]?d.detail[0].msg:(d.detail||'Chyba'));
  err.textContent='';const v=d.valuation||{};
  document.getElementById('market').textContent=czk(v.market_value_czk);
  document.getElementById('buy').textContent=czk(v.recommended_purchase_czk);
  document.getElementById('ppm').textContent=czk(v.price_per_m2);
  document.getElementById('ms').textContent=d.processing_ms<1?'< 1 ms':d.processing_ms+' ms';
  const l=document.getElementById('light');l.className='light '+d.legal.light;l.textContent='Právní semafor: '+labels[d.legal.light];
  document.getElementById('next').textContent=d.legal.reasons.join(', ')+' → '+d.legal.next_step;
  document.getElementById('pdf').href='/demo/pdf/'+d.event_id;
  const ins=d.insolvency.records.length?('probíhá řízení '+d.insolvency.records[0].spisova_znacka):'bez záznamu';
  const exeTxt=body.declared_execution?'vedena exekuce':'bez záznamu';
  const steps=[
   ['Lead přijat a zkontrolován (v ostrém provozu přes podepsaný webhook)',true],
   ['ISIR – insolvence: '+ins,!d.insolvency.records.length],
   ['CEE – exekuce: '+exeTxt,!body.declared_execution],
   ['ČÚZK – list vlastnictví (v ostrém provozu přes webové služby ČÚZK)',true],
   ['Ocenění podle cenové mapy: '+czk(v.recommended_purchase_czk)+' doporučená nákupní cena',true],
   ['Složka JSON + PDF připravena k odeslání do GoHighLevel',true]];
  const ol=document.getElementById('steps');ol.innerHTML='';
  steps.forEach(([t,ok])=>{const li=document.createElement('li');li.textContent=(ok?'✅ ':'⚠️ ')+t;ol.appendChild(li)});
  document.getElementById('result').classList.remove('hidden');
 }catch(e){err.textContent='Chyba: '+e.message}};
document.getElementById('scan').onclick=async()=>{
 const r=await fetch('/demo/auctions');const d=await r.json();const tb=document.querySelector('#deals tbody');tb.innerHTML='';
 d.deals.forEach(x=>{const tr=document.createElement('tr');
  [x.spisova_znacka,x.obec,x.typ,x.plocha_m2,czk(x.nejnizsi_podani_czk),czk(x.trzni_hodnota_avm_czk),
   Math.round(x.discount_vs_market*100)+' %',new Date(x.termin).toLocaleDateString('cs-CZ')]
  .forEach(t=>{const td=document.createElement('td');td.textContent=t;tr.appendChild(td)});tb.appendChild(tr)});
 document.getElementById('deals').classList.remove('hidden')};
</script></body></html>"""

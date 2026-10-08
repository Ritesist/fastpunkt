"""Static, dependency-free profile viewer (one HTML file). Works offline and on mobile."""
from __future__ import annotations

import json
from pathlib import Path

CSS = """
:root{--bg:#F6F8FB;--panel:#fff;--ink:#1C222B;--muted:#5B6675;--line:#DDE3EA;--accent:#2F4FB8;--ok:#2E7D5B;--warn:#A86A00;--bad:#B23B3B}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif}
header{padding:14px 20px;background:var(--panel);border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:center;flex-wrap:wrap}
header h1{font-size:18px;margin:0;color:var(--accent)}header span{color:var(--muted);font-size:13px}
main{display:grid;grid-template-columns:340px 1fr;gap:16px;padding:16px;max-width:1400px;margin:0 auto}
@media(max-width:860px){main{grid-template-columns:1fr}}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px}
input[type=search]{width:100%;padding:9px 10px;border:1px solid var(--line);border-radius:8px;font:inherit}
#list{max-height:72vh;overflow:auto;margin-top:10px}#list div{padding:8px;border-radius:8px;cursor:pointer;border-bottom:1px solid #EEF1F5}
#list div:hover,#list div.sel{background:#EEF2FB}#list small{color:var(--muted);display:block}
.states{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}.st{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line)}
.st.available{color:var(--ok);border-color:#BFDCCD}.st.not_available,.st.not_applicable{color:var(--muted)}.st.ambiguous,.st.blocked{color:var(--warn);border-color:#EBD3A6}.st.failed{color:var(--bad);border-color:#E7C0C0}
h2{font-size:20px;margin:0 0 4px}h3{font-size:15px;margin:18px 0 6px;color:var(--accent)}
.sum p{margin:6px 0}.chip{font-size:11px;color:var(--accent);background:#EEF2FB;border-radius:6px;padding:0 5px;margin-left:3px;cursor:pointer;text-decoration:none}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid #EEF1F5;padding:5px 6px;text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:500}tr.hl{background:#FFF7E0}.muted{color:var(--muted)}a{color:var(--accent)}
.unk li{color:var(--muted)}code{font-size:12px;background:#F2F4F7;padding:1px 4px;border-radius:4px;word-break:break-all}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const $ = s => document.querySelector(s);
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => v === null || v === undefined ? '' : typeof v === 'object' ? (v.amount !== undefined ? `${v.amount.toLocaleString('en')} ${v.currency||''}` : v.url ? `<a href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title||v.platform||v.url)}</a>${v.date?' · '+esc(v.date):''}` : esc(JSON.stringify(v))) : esc(v);
function list(q){
  q = (q||'').toLowerCase();
  $('#list').innerHTML = D.map((e,i)=>({e,i})).filter(({e})=>!q || (e.organisation_number+' '+(e.legal_identity.legal_name||'')).toLowerCase().includes(q))
    .map(({e,i})=>`<div data-i="${i}"><b>${esc(e.legal_identity.legal_name||'(no identity)')}</b><small>${e.organisation_number} · ${esc(e.legal_identity.industry||'')}</small></div>`).join('');
  document.querySelectorAll('#list div').forEach(d=>d.onclick=()=>show(+d.dataset.i));
}
function show(i){
  const e = D[i]; const ev = Object.fromEntries(e.evidence.map(x=>[x.id,x]));
  document.querySelectorAll('#list div').forEach(d=>d.classList.toggle('sel', +d.dataset.i===i));
  const claims = e.claims.filter(c=>c.availability==='available');
  const bySec = {}; claims.forEach(c=>(bySec[c.section]=bySec[c.section]||[]).push(c));
  const names = {legal_identity:'Legal identity',annual_accounts:'Annual accounts',leadership_workplaces:'Leadership and workplaces',website_profiles:'Website and company profiles',hiring_activity:'Hiring and dated activity'};
  $('#profile').innerHTML = `<h2>${esc(e.legal_identity.legal_name||e.organisation_number)}</h2>
   <div class="muted">Org. ${e.organisation_number} · run ${esc(e.run.run_id)} · ${esc(e.run.completed_at)} · refresh: ${esc((e.refresh||{}).status||'')}</div>
   <div class="states">${Object.entries(e.sections).map(([k,v])=>`<span class="st ${v.state}" title="${esc(v.reason||'')}">${names[k]}: ${v.state.replace('_',' ')}</span>`).join('')}</div>
   <h3>Summary</h3><div class="sum">${e.summary.sentences.map(s=>`<p>${esc(s.text)}${s.claim_ids.map(c=>`<a class="chip" href="#${c}">${c}</a>`).join('')}</p>`).join('') || '<p class="muted">No published facts.</p>'}</div>
   ${e.changes.length?`<h3>Changes since the previous run</h3><table><tr><th>Field</th><th>Before</th><th>After</th></tr>${e.changes.map(c=>`<tr><td>${esc(c.key)}</td><td>${c.before.map(fmt).join('<br>')}</td><td>${c.after.map(fmt).join('<br>')}</td></tr>`).join('')}</table>`:''}
   <h3>Unknowns</h3><ul class="unk">${e.summary.unknowns.map(u=>`<li>${esc(u.section||u.field)}: ${esc(u.state)}. ${esc(u.reason)}</li>`).join('') || '<li>None</li>'}</ul>
   ${Object.entries(bySec).map(([s,cs])=>`<h3>${names[s]||s}</h3><table><tr><th>Field</th><th>Value</th><th>Period / date</th><th>Source</th></tr>${cs.map(c=>`<tr id="${c.id}"><td>${esc(c.field.replaceAll('_',' '))}</td><td>${fmt(c.value)}</td><td>${esc(c.reporting_period||c.effective_date||'')}</td><td>${c.evidence_ids.map(id=>ev[id]?`<a href="${esc(ev[id].source_url)}" target="_blank" rel="noopener" title="${esc(ev[id].claim_span)}">${esc(ev[id].source_name)}</a> <span class="muted">${esc(ev[id].retrieved_at)}</span>`:'').join('<br>')}</td></tr>`).join('')}</table>`).join('')}
   <h3>Website candidates considered</h3><table><tr><th>URL</th><th>Found via</th><th>Outcome</th></tr>${e.website_candidates.map(c=>`<tr><td><code>${esc(c.url)}</code></td><td>${esc(c.discovery)}</td><td>${esc(c.status)}${c.verification?' ('+esc(c.verification)+')':''} <span class="muted">${esc(c.reason||'')}</span></td></tr>`).join('') || '<tr><td colspan=3 class="muted">None</td></tr>'}</table>`;
  document.querySelectorAll('.chip').forEach(a=>a.onclick=()=>{document.querySelectorAll('tr.hl').forEach(t=>t.classList.remove('hl'));const t=document.getElementById(a.getAttribute('href').slice(1));if(t)t.classList.add('hl');});
}
$('#q').oninput = e => list(e.target.value);
list(''); if (D.length) show(0);
"""


def render(inp: str, out: str):
    envs = [json.loads(l) for l in Path(inp).read_text(encoding="utf-8").splitlines() if l.strip()]
    data = json.dumps(envs, ensure_ascii=False).replace("</", "<\\/")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fastpunkt company profiles</title><style>{CSS}</style></head><body>
<header><h1>Fastpunkt</h1><span>{len(envs)} company profiles. Every fact links to its source. Unknowns are stated, never zero.</span></header>
<main><section class="panel"><input id="q" type="search" placeholder="Search name or organisation number"><div id="list"></div></section>
<section class="panel" id="profile"></section></main>
<script type="application/json" id="data">{data}</script><script>{JS}</script></body></html>"""
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(envs)} profiles)")

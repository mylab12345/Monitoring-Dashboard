let portsData=[],portsError=null;
async function loadPorts(){
  const d=await fetchJSON('/api/ports');
  // Distinguish "API failed" from "genuinely nothing listening" — both used to
  // render the same reassuring empty state.
  portsError=(!d||d.error||!Array.isArray(d.ports))
    ? ((d&&d.error)||'the API did not respond') : null;
  portsData=(d&&Array.isArray(d.ports))?d.ports:[];
  const count=$('#portsCount'),count2=$('#portsCount2');
  const txt=portsError?'error':(portsData.length?portsData.length+' open':'');
  if(count){count.textContent=txt;count.className='badge'+(portsError?' fail':'');}
  if(count2){count2.textContent=txt;count2.className='badge'+(portsError?' fail':'');}
  if(!portsError)stampUpdated('netUpdated');
  renderPortsOverview();renderPortsTable();
}
function exportPortsCSV(){
  if(!portsData.length){toast('Nothing to export','info');return;}
  exportCSV('monitoring-ports-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv',
    ['Protocol','Address','Port','Process'],
    portsData.map(p=>[protoLabel(p.proto),p.addr,p.port,cleanProc(p.process)]));
}
function protoLabel(p){return p&&p.endsWith('6')?p.slice(0,-1).toUpperCase()+'/6':(p||'').toUpperCase();}
// `ss` reports listeners as: users:(("python",pid=1415,fd=9),("x",pid=2,fd=3))
// Parse every ("name",pid=N) pair into "name (pid)". The old two-replace
// version left a dangling "))" and leaked fd numbers into the Ports card.
function cleanProc(s){
  if(!s)return '';
  const seen=new Set(),out=[];
  const re=/\("([^"]+)",pid=(\d+)/g;
  let m;
  while((m=re.exec(s))!==null){
    const k=m[1]+'/'+m[2];
    if(seen.has(k))continue;
    seen.add(k);
    out.push(m[1]+' ('+m[2]+')');
  }
  // Fall back to a light cleanup if the ss output format ever changes.
  return out.length?out.join(', '):s.replace(/^users:\(\(/,'').replace(/\)\)$/,'').replace(/"/g,'');
}
function renderPortsOverview(){
  const el=$('#portsList');
  if(!el)return;
  if(portsError){el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('globe',22)+'</div><strong>Port data unavailable</strong><span class="dim">'+esc(portsError)+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadPorts()">'+icon('refresh',13)+' Retry</button></div>';return;}
  if(!portsData.length){el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('globe',22)+'</div><strong>No listening ports</strong><span class="dim">Nothing accepting connections right now.</span></div>';return;}
  el.innerHTML=portsData.slice(0,7).map(p=>`
    <div class="check-row">
      <span class="pill neutral" style="width:52px;justify-content:center">${esc(protoLabel(p.proto))}</span>
      <span class="mono" style="font-size:.72rem">${esc(p.addr)}:<b style="color:var(--text)">${esc(p.port)}</b></span>
      <span class="mono dim" style="font-size:.62rem;margin-left:auto;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:45%">${esc(cleanProc(p.process))}</span>
    </div>`).join('')
    +(portsData.length>7?'<div class="dim" style="font-size:.68rem;margin-top:8px">+'+(portsData.length-7)+' more — see Network</div>':'');
}
function renderPortsTable(){
  const tb=$('#portsRows');
  if(!tb)return;
  if(portsError){tb.innerHTML='<tr><td colspan="4" class="dim">Port data unavailable — '+esc(portsError)+' <a href="#" onclick="loadPorts();return false;">Retry</a></td></tr>';return;}
  if(!portsData.length){tb.innerHTML='<tr><td colspan="4" class="dim">No listening ports</td></tr>';return;}
  const qEl=$('#portsSearch');
  const q=(qEl&&qEl.value||'').toLowerCase();
  const rows=q?portsData.filter(p=>String(p.port).includes(q)||(p.addr||'').toLowerCase().includes(q)
    ||(p.proto||'').toLowerCase().includes(q)||cleanProc(p.process).toLowerCase().includes(q)):portsData;
  if(!rows.length){tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">No ports match your filter</td></tr>';return;}
  tb.innerHTML=rows.map(p=>'<tr>'
    +'<td><span class="pill neutral" style="min-width:56px;justify-content:center">'+esc(protoLabel(p.proto))+'</span></td>'
    +'<td class="mono dim">'+esc(p.addr)+'</td>'
    +'<td class="mono"><b>'+esc(p.port)+'</b></td>'
    +'<td class="mono dim" style="font-size:.68rem">'+(esc(cleanProc(p.process))||'—')+'</td></tr>').join('');
}
let nicsData=[],nicPrev={};
async function loadNetwork(){
  const el=$('#nicList');
  try{
  const d=await fetchJSON('/api/network');
  if(!d||!d.nics)throw new Error('the API did not respond');
  nicsData=d.nics;
  stampUpdated('netUpdated');
  const maxTx=Math.max(1,...d.nics.map(n=>Math.max(n.sent_mb,n.recv_mb)));
  const now=Date.now();
  el.innerHTML='<div class="grid g3">'+d.nics.map(n=>{
    let rx='—',tx='—';
    const pr=nicPrev[n.name];
    if(pr){
      const dt=(now-pr.t)/1000;
      if(dt>0.5){
        rx=fmtRate(Math.max(0,(n.recv_mb-pr.rb)*1048576/dt));
        tx=fmtRate(Math.max(0,(n.sent_mb-pr.sb)*1048576/dt));
      }
    }
    nicPrev[n.name]={t:now,rb:n.recv_mb,sb:n.sent_mb};
    return `
    <div class="nic-card ${n.up?'up':''}">
      <div class="nic-head">
        <span class="nic-name">${icon('network',14)} ${esc(n.name)}</span>
        <span class="pill ${n.up?'ok':'fail'}">${n.up?'up':'down'}</span>
      </div>
      <div class="nic-lines">
        IPv4: <b>${esc(n.ipv4)||'—'}</b><br>
        IPv6: <b>${esc(n.ipv6)||'—'}</b><br>
        MAC: <b>${esc(n.mac)||'—'}</b>${n.speed?` · <b>${n.speed} Mb/s</b>`:''}
      </div>
      <div class="nic-rate">
        <span class="rate rx"><span class="rv">↓ ${rx}</span><span class="rk">receive</span></span>
        <span class="rate tx"><span class="rv">↑ ${tx}</span><span class="rk">send</span></span>
      </div>
      <div class="nic-traffic">
        <span class="dir down" title="${n.recv_mb} MB received"><span>↓</span><span class="bar"><i style="width:${Math.max(4,n.recv_mb/maxTx*100)}%"></i></span></span>
        <span class="dir up" title="${n.sent_mb} MB sent"><span>↑</span><span class="bar"><i style="width:${Math.max(4,n.sent_mb/maxTx*100)}%"></i></span></span>
      </div>
    </div>`;}).join('')+'</div>';
  }catch(e){
    if(el)el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('network',22)+'</div><strong>Network data unavailable</strong><span class="dim">'+esc(e.message||'request failed')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadNetwork()">'+icon('refresh',13)+' Retry</button></div>';
  }
}

// ================================================================
// Processes
// ================================================================

let procData=[],procSort={key:'cpu',dir:-1};
async function loadProcesses(){
  try{
    const d=await fetchJSON('/api/processes?limit='+(S.procLimit||25));
    if(!d||!d.processes)throw new Error('the API did not respond');
    procData=d.processes||[];
    const t=$('#procTotal');if(t)t.textContent=d.total+' total';
    renderProcesses();
  }catch(e){
    const t=$('#procTotal');if(t)t.textContent='unavailable';
    const tb=$('#procRows');
    if(tb)tb.innerHTML='<tr><td colspan="7"><div class="empty-state"><div class="empty-icon">'+icon('cpu',22)+'</div><strong>Processes unavailable</strong><span class="dim">'+esc(e.message||'request failed')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadProcesses()">'+icon('refresh',13)+' Retry</button></div></td></tr>';
  }
}
function renderProcesses(){
  const tb=$('#procRows');
  if(!tb)return;
  const q=($('#procSearch').value||'').toLowerCase();
  let rows=procData.filter(p=>!q||p.name.toLowerCase().includes(q)||String(p.pid).includes(q)||(p.user||'').toLowerCase().includes(q));
  rows.sort((a,b)=>{
    const key=procSort.key,dir=procSort.dir;
    let x=a[key],y=b[key];
    if(key==='name'){x=x.toLowerCase();y=y.toLowerCase();return x<y?-dir:x>y?dir:0;}
    return (x-y)*dir;
  });
  $$('#procTable th.sortable').forEach(th=>{
    const k=th.dataset.key;
    th.querySelector('.arrow').textContent=procSort.key===k?(procSort.dir>0?'▲':'▼'):'';
  });
  if(!rows.length){tb.innerHTML='<tr><td colspan="7" class="dim" style="text-align:center;padding:22px">No processes match your filter</td></tr>';return;}
  tb.innerHTML=rows.map(p=>'<tr>'
    +'<td class="mono dim">'+p.pid+'</td>'
    +'<td class="mono strong">'+esc(p.name)+'</td>'
    +'<td class="mono dim" style="font-size:.72rem">'+esc(p.user)+'</td>'
    +'<td><span class="cell-bars '+(p.cpu>=50?'hot':p.cpu>=20?'warm':'')+'"><span class="num">'+p.cpu+'</span><span class="mini"><i style="width:'+Math.min(100,p.cpu)+'%"></i></span></span></td>'
    +'<td><span class="cell-bars '+(p.mem>=60?'hot':p.mem>=30?'warm':'')+'"><span class="num">'+p.mem+'</span><span class="mini"><i style="width:'+Math.min(100,p.mem)+'%"></i></span></span></td>'
    +'<td><span class="pill '+(p.status==='running'?'ok':p.status==='zombie'?'fail':'neutral')+'">'+esc(p.status)+'</span></td>'
    +'<td><span class="row-actions"><button class="icon-btn danger" title="Kill PID '+p.pid+'" onclick="killProcess('+p.pid+',\''+jsq(p.name)+'\')">'+icon('x',13)+'</button></span></td>'
  +'</tr>').join('');
}
$$('#procTable th.sortable').forEach(th=>th.addEventListener('click',()=>{
  const k=th.dataset.key;
  if(procSort.key===k)procSort.dir*=-1;
  else procSort={key:k,dir:k==='name'||k==='pid'?1:-1};
  renderProcesses();
}));
function setProcLimit(v){S.procLimit=+v;saveSettings();loadProcesses();}
async function killProcess(pid,name){
  const ok=await confirmDlg('Kill process','Terminate “'+name+'” (PID '+pid+')? It will be force-killed if it ignores SIGTERM.',true);
  if(!ok)return;
  logActivity('process','Kill process '+name+' (PID '+pid+')');
  const j=await postJSON('/api/process/kill',{pid});
  if(j.error){toast('Kill failed: '+j.error,'err');logActivity('process','Kill failed: '+name+' (PID '+pid+')',false);}
  else{toast(j.result||'Process terminated');logActivity('process','Terminated '+name+' (PID '+pid+')');loadProcesses();}
}

// ================================================================
// Services (filter + search + pagination)
// ================================================================

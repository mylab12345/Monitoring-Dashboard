let procData=[],procSort={key:'cpu',dir:-1};
async function loadProcesses(){
  try{
    const d=await fetchJSON('/api/processes?limit='+(S.procLimit||25));
    if(!d||!d.processes)throw new Error('the API did not respond');
    procData=d.processes||[];
    const t=$('#procTotal');if(t)t.textContent=d.total+' total';
    stampUpdated('procUpdated');
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
    // aria-sort keeps screen readers in sync with the visual arrow.
    th.setAttribute('aria-sort',procSort.key===k?(procSort.dir>0?'ascending':'descending'):'none');
  });
  if(!rows.length){tb.innerHTML='<tr><td colspan="7" class="dim" style="text-align:center;padding:22px">No processes match your filter</td></tr>';return;}
  tb.innerHTML=rows.map(p=>'<tr>'
    +'<td class="mono dim">'+p.pid+'</td>'
    +'<td class="mono strong">'+esc(p.name)+'</td>'
    +'<td class="mono dim" style="font-size:.72rem">'+esc(p.user)+'</td>'
    +'<td><span class="cell-bars '+(p.cpu>=50?'hot':p.cpu>=20?'warm':'')+'"><span class="num">'+p.cpu+'</span><span class="mini"><i style="width:'+Math.min(100,p.cpu)+'%"></i></span></span></td>'
    +'<td><span class="cell-bars '+(p.mem>=60?'hot':p.mem>=30?'warm':'')+'"><span class="num">'+p.mem+'</span><span class="mini"><i style="width:'+Math.min(100,p.mem)+'%"></i></span></span></td>'
    +'<td><span class="pill '+(p.status==='running'?'ok':p.status==='zombie'?'fail':'neutral')+'">'+esc(p.status)+'</span></td>'
    +'<td><span class="row-actions">'
      +'<button class="icon-btn proc-info" data-pid=\"'+p.pid+'\" title=\"Process details for PID '+p.pid+'\" aria-label=\"Details for '+esc(p.name)+'\">'+icon('info',13)+'</button>'
      +'<button class=\"icon-btn proc-nice\" data-pid=\"'+p.pid+'\" title=\"Change priority of PID '+p.pid+'\" aria-label=\"Change priority of '+esc(p.name)+'\">'+icon('gauge',13)+'</button>'
      +'<button class="icon-btn danger proc-kill" data-pid="'+p.pid+'" data-name="'+esc(p.name)+'" title="Kill PID '+p.pid+'" aria-label="Kill '+esc(p.name)+' (PID '+p.pid+')">'+icon('x',13)+'</button></span></td>'
  +'</tr>').join('');
  // Bind via data attributes to avoid inline JS injection via process names
  $$('#procRows .proc-kill').forEach(b=>{
    b.addEventListener('click',()=>killProcess(+b.dataset.pid,b.dataset.name));
  });
  $$('#procRows .proc-info').forEach(b=>{
    b.addEventListener('click',()=>showProcDetail(+b.dataset.pid));
  });
  $$('#procRows .proc-nice').forEach(b=>{
    b.addEventListener('click',()=>setProcPriority(+b.dataset.pid));
  });
}
$$('#procTable th.sortable').forEach(th=>{
  // Keyboard-accessible sorting: headers are focusable and respond to
  // Enter/Space exactly like a click.
  th.tabIndex=0;
  th.setAttribute('role','columnheader');
  if(!th.title)th.title='Sort by '+th.textContent.trim();
  const sortBy=()=>{
    const k=th.dataset.key;
    if(procSort.key===k)procSort.dir*=-1;
    else procSort={key:k,dir:k==='name'||k==='pid'?1:-1};
    renderProcesses();
  };
  th.addEventListener('click',sortBy);
  th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sortBy();}});
});
function setProcLimit(v){S.procLimit=+v;saveSettings();loadProcesses();}
function exportProcessesCSV(){
  const q=($('#procSearch').value||'').toLowerCase();
  const rows=procData
    .filter(p=>!q||p.name.toLowerCase().includes(q)||String(p.pid).includes(q)||(p.user||'').toLowerCase().includes(q))
    .map(p=>[p.pid,p.name,p.user,p.cpu,p.mem,p.status]);
  if(!rows.length){toast('Nothing to export','info');return;}
  exportCSV('monitoring-processes-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv',
    ['PID','Process','User','CPU %','Memory %','State'],rows);
}
async function killProcess(pid,name){
  const key='kill:'+pid;
  if(!beginAction(key))return; // a kill for this pid is already in flight
  try{
    const ok=await confirmDlg('Kill process','Terminate “'+name+'” (PID '+pid+')? It will be force-killed if it ignores SIGTERM.',true);
    if(!ok)return;
    logActivity('process','Kill process '+name+' (PID '+pid+')');
    const j=await postJSON('/api/process/kill',{pid});
    if(j.error){toast('Kill failed: '+j.error,'err');logActivity('process','Kill failed: '+name+' (PID '+pid+')',false);}
    else if(j.verified===false){
      toast((j.result||'Kill signal sent')+' — still running, it may need a moment','info');
      logActivity('process','Kill signalled (unverified): '+name+' (PID '+pid+')');
      loadProcesses();
    }
    else{toast(j.result||'Process terminated');logActivity('process','Terminated '+name+' (PID '+pid+')');loadProcesses();}
  }finally{endAction(key);}
}

// ================================================================
// Services (filter + search + pagination)
// ================================================================

// ================================================================
// Process details / tree / priority / history (OS handling features)
// ================================================================
function procDetailRows(d){
  const row=(k,v)=>'<div class="kv"><span>'+esc(k)+'</span><strong>'+esc(String(v==null?'—':v))+'</strong></div>';
  return row('User',d.user)+row('State',d.status)+row('Started',d.started)
    +row('CPU %',d.cpu)+row('Memory %',d.mem)
    +row('RSS',d.rss_bytes!=null?(d.rss_bytes/1048576).toFixed(1)+' MB':'—')
    +row('Threads',d.threads)+row('Nice',d.nice)
    +row('CPU affinity',d.affinity&&d.affinity.length?d.affinity.join(','):'—')
    +row('Parent',d.parent?d.parent+' ('+d.ppid+')':d.ppid)
    +row('Children',d.children&&d.children.length?d.children.map(c=>c.name+' ('+c.pid+')').join(', ').slice(0,120):'—')
    +row('Unit',d.unit||'—')
    +row('Open files',d.open_files!=null?d.open_files:'—')
    +row('I/O read/write',d.io_read_bytes!=null
      ?((d.io_read_bytes/1048576).toFixed(1)+' / '+(d.io_write_bytes/1048576).toFixed(1)+' MB'):'—')
    +row('Executable',d.exe||'—')+row('Working dir',d.cwd||'—')
    +row('Command',d.cmdline||'—');
}
async function showProcDetail(pid){
  try{
    const d=await fetchJSON('/api/process/'+pid);
    if(!d||d.error){toast('Details unavailable: '+((d&&d.error)||'request failed'),'err');return;}
    await openModal({title:'Process '+pid+' — '+(d.name||''),html:procDetailRows(d),
      confirmText:'Close',cancelText:'Close',icon:'cpu'});
  }catch(e){toast('Details unavailable: '+(e&&e.message||'request failed'),'err');}
}
function procTreeNode(n,depth){
  const pad='&nbsp;'.repeat(depth*3);
  let html='<div class="kv"><span>'+pad+esc(n.name)+' <span class="dim">('+n.pid+')</span></span>'
    +'<strong>CPU '+n.cpu_total+'% · MEM '+n.mem_total+'%</strong></div>';
  for(const c of (n.children||[]))html+=procTreeNode(c,depth+1);
  return html;
}
async function showProcTree(){
  try{
    const d=await fetchJSON('/api/process/tree');
    if(!d||d.error){toast('Tree unavailable: '+((d&&d.error)||'request failed'),'err');return;}
    const tree=d.tree||[];
    const html='<div class="dim" style="margin-bottom:6px">'+d.total+' processes, subtree totals shown</div>'
      +tree.slice(0,25).map(n=>procTreeNode(n,0)).join('');
    await openModal({title:'Process tree',html,confirmText:'Close',cancelText:'Close',icon:'cpu'});
  }catch(e){toast('Tree unavailable: '+(e&&e.message||'request failed'),'err');}
}
async function setProcPriority(pid){
  const r=await openModal({
    title:'Change priority — PID '+pid,
    text:'Lower priority (higher nice value) for a hog, or pin it to specific CPUs. Values are re-checked after applying.',
    fields:[
      {key:'nice',label:'Nice (-20..19, higher = nicer)',type:'number',value:'10'},
      {key:'affinity',label:'CPU affinity (e.g. 0,1 or 0-3, optional)',value:''}
    ],
    confirmText:'Apply',icon:'gauge'
  });
  if(!r)return;
  const body={pid};
  if(r.values.nice!=='')body.nice=+r.values.nice;
  if(r.values.affinity)body.affinity=r.values.affinity;
  if(body.nice===undefined&&body.affinity===undefined){toast('Nothing to change','info');return;}
  try{
    const key='prio:'+pid;
    if(!beginAction(key))return;
    try{
      const j=await postJSON('/api/process/priority',body);
      if(j&&j.error){toast('Priority change failed: '+j.error,'err');return;}
      toast((j.result||'Priority updated')+(j.verified===false?' — state not confirmed':''),'ok');
      logActivity('process','Changed priority of PID '+pid);
      loadProcesses();
    }finally{endAction(key);}
  }catch(e){toast('Priority change failed: '+(e&&e.message||'request failed'),'err');}
}
async function showProcHistory(){
  try{
    const d=await fetchJSON('/api/process/history?minutes=120');
    if(!d||d.error){toast('History unavailable: '+((d&&d.error)||'request failed'),'err');return;}
    const series=d.series||[];
    if(!series.length){
      await openModal({title:'Process resource history',
        text:'No recorded history yet. The dashboard snapshots top consumers once a minute; check back in a few minutes.',
        confirmText:'Close',cancelText:'Close',icon:'clock'});
      return;
    }
    const html='<div class="dim" style="margin-bottom:6px">Who consumed CPU over the last 2 hours (minute snapshots)</div>'
      +series.map(s=>{
        const peak=Math.max(...s.samples.map(x=>x.cpu||0));
        const first=s.samples[0],last=s.samples[s.samples.length-1];
        return '<div class="kv"><span>'+esc(s.name)+' <span class="dim">('+s.pid+')</span></span>'
          +'<strong>peak '+peak+'% · '+(first&&last?esc(new Date(first.t*1000).toLocaleTimeString())+'→'+esc(new Date(last.t*1000).toLocaleTimeString()):'—')+'</strong></div>';
      }).join('');
    await openModal({title:'Process resource history',html,confirmText:'Close',cancelText:'Close',icon:'clock'});
  }catch(e){toast('History unavailable: '+(e&&e.message||'request failed'),'err');}
}

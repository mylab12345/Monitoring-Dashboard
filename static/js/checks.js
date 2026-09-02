const CHECK_META={'disk':'disk','update':'package','broken':'alert','service':'gear','kernel':'zap','zombie':'pulse'};
function checkIcon(name){
  const n=name.toLowerCase();
  for(const k in CHECK_META)if(n.includes(k))return CHECK_META[k];
  return 'check';
}
async function updateChecks(){
  const list=$('#checksList');
  const data=await fetchJSON('/api/checks');
  if(!data||data.error||!Array.isArray(data)){
    list.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('alert',22)+'</div><strong>Checks unavailable</strong>'
      +'<span class="dim">'+esc((data&&data.error)||'the API did not respond')+'</span>'
      +'<button class="btn btn-sm" style="margin-top:10px" onclick="updateChecks()">'+icon('refresh',13)+' Retry</button></div>';
    const hb0=$('#healthScoreBadge');
    if(hb0){hb0.textContent='Health: --';hb0.className='badge';}
    const cb0=$('#checksBadge');
    if(cb0){cb0.textContent='unavailable';cb0.className='badge';}
    return;
  }
  const issues=data.filter(c=>c.status==='warn'||c.status==='fail').length;
  const fails=data.filter(c=>c.status==='fail').length;
  const warns=issues-fails;              // warn-only, so failures are not charged twice
  const score=Math.max(0,100-Math.round((warns*20)+(fails*40)));
  const hb=$('#healthScoreBadge');
  if(hb){
    hb.textContent='Health: '+score+'%';
    hb.className='badge '+(score>=90?'ok':score>=70?'warn':'fail');
  }
  const badge=$('#checksBadge');
  // Distinguish hard failures from warnings instead of lumping both into
  // an amber "N issues" pill.
  const parts=[];
  if(warns)parts.push(warns+(warns===1?' warning':' warnings'));
  if(fails)parts.push(fails+(fails===1?' failure':' failures'));
  badge.textContent=parts.length?parts.join(' · '):'all clear';
  badge.className='badge '+(fails?'fail':warns?'warn':'ok');
  list.innerHTML=data.map(c=>`
    <div class="check-row ${c.status}">
      <div class="check-icon">${icon(checkIcon(c.name),15)}</div>
      <div class="check-info">
        <div class="name">${esc(c.name)}</div>
        <div class="detail" title="${esc(c.detail)}">${esc(c.detail)}</div>
      </div>
      ${c.status==='ok'?'':'<button class="btn btn-sm" type="button" onclick="showTab(\'troubleshooting\')">Review &amp; fix</button>'}
      <span class="pill ${c.status==='ok'?'ok':c.status==='fail'?'fail':'warn'}">${c.status}</span>
    </div>`).join('');
  const dot=$('#dotOverview');
  if(dot){dot.hidden=issues===0;dot.textContent=issues;}
  refreshSelfRepair();
}

// ================================================================
// Maintenance actions
// ================================================================
const FIX_META={
  update:{icon:'refresh',label:'Update Lists',desc:'Refresh repositories'},
  upgrade:{icon:'up',label:'Upgrade',desc:'Install available updates'},
  'full-upgrade':{icon:'up',label:'Full Upgrade',desc:'Upgrade incl. new kernels'},
  autoremove:{icon:'trash',label:'Autoremove',desc:'Remove unused packages'},
  clean:{icon:'sparkles',label:'Clean Cache',desc:'Clear package caches'},
  'fix-broken':{icon:'wrench',label:'Fix Broken',desc:'Repair packages'},
  'clear-logs':{icon:'terminal',label:'Clear Logs',desc:'Vacuum the journal'}
};
function renderFixButtons(){
  $('#fixGrid').innerHTML=Object.entries(FIX_META).map(([k,m])=>`
    <button class="fix-btn" onclick="runFix('${k}',this)">
      <span class="fix-ic">${icon(m.icon,14)}</span>
      <span>${esc(m.label)}<span class="fdesc">${esc(m.desc)}</span></span>
    </button>`).join('');
}
async function runFix(action,btn){
  const term=$('#fixTerminal'),out=$('#fixResult');
  const meta=FIX_META[action]||{icon:'wrench',desc:action};
  if(QUICK_CONFIRM[action]){
    const ok=await confirmDlg(meta.label||action,QUICK_CONFIRM[action],true);
    if(!ok)return;
  }
  const old=btn?btn.innerHTML:null;
  if(btn){btn.disabled=true;btn.innerHTML='<span class="spinner">'+icon('refresh',15)+'</span><span>Running…</span>';}
  term.hidden=false;
  out.innerHTML='<span class="dim">$ monitoring fix '+esc(action)+' …</span>';
  logActivity('fix','Run fix: '+meta.label);
  const j=await postJSON('/api/fix',{action});
  if(btn){btn.disabled=false;btn.innerHTML=old;}
  const res=(j&&(j.result||j.error))||'Done';
  out.innerHTML='<span class="dim">$ monitoring fix '+esc(action)+'</span>\n'+esc(res.length>600?res.slice(0,600)+'…':res);
  if(j&&j.error){
    toast('Fix failed: '+j.error,'err');
    logActivity('fix','Fix failed: '+meta.label,false);
    if(detectReadOnlyMount(j.error)){
      const repaired=await offerSelfRepair(()=>runFix(action));
      if(repaired)refreshSelfRepair();
    }
  }
  else{toast(meta.desc||('Completed: '+action));logActivity('fix','Fix completed: '+meta.label);}
  updateChecks();
}

// ================================================================
// Service mount namespace self-repair
// ================================================================
// The monitoring service runs with ProtectSystem=full, so /usr, /etc and
// /boot are read-only inside its mount namespace. Older monitoring.service
// units lacked ReadWritePaths=/usr /etc /boot /efi, which makes every package
// operation fail with "required filesystem is read-only". The dashboard can
// repair this itself through the whitelisted monitoring-self-repair helper
// (service-account sudo): it patches the unit, runs daemon-reload and
// restarts the service, so no root shell is needed.
function detectReadOnlyMount(text){
  return /read[- ]?only/i.test(String(text||''))
    && /(filesystem|file system|\/usr|\/etc)/i.test(String(text||''));
}
async function waitForApi(timeoutMs=30000,intervalMs=1500){
  const t0=Date.now();
  while(Date.now()-t0<timeoutMs){
    try{const r=await apiFetch('/api/health');if(r.ok)return true;}catch(e){}
    await new Promise(r=>setTimeout(r,intervalMs));
  }
  return false;
}
async function refreshSelfRepair(){
  const btn=$('#repairMountBtn'),st=$('#repairMountStatus');
  if(!btn&&!st)return;
  let d=null;
  try{d=await fetchJSON('/api/self-repair');}catch(e){}
  if(!btn)return;
  if(!d||d.error){btn.disabled=true;if(st)st.textContent='status unavailable';return;}
  btn.disabled=!d.actionable;
  if(st){
    if(d.actionable){
      const ro=(d.namespace_read_only||[]).join(' · ')||'/usr · /etc · /boot';
      st.textContent='package paths are read-only in the service namespace ('+esc(ro)+') — repair recommended';
    }else{
      st.textContent=String(d.message||'mount namespace is correct').slice(0,110);
    }
  }
}
async function repairMountManual(){
  const done=await offerSelfRepair(()=>updateChecks());
  if(done)refreshSelfRepair();
}
async function offerSelfRepair(afterRepair){
  let st=null;
  try{st=await fetchJSON('/api/self-repair');}catch(e){}
  if(!st||st.error){toast('Could not check service mount status','err');return false;}
  if(!st.actionable){toast(st.message||'Service mount namespace is already correct','info');return false;}
  const ok=await openModal({
    title:'Repair monitoring service mount namespace',
    text:'The dashboard runs with ProtectSystem=full, so /usr, /etc and /boot are read-only inside its namespace and package operations are blocked.',
    html:'<div class="fix-preview">'
      +'<div class="kv"><span>Action</span><strong>Patch monitoring.service + daemon-reload + restart</strong></div>'
      +'<div class="kv"><span>Read-only paths</span><strong>'+esc(((st.namespace_read_only||[]).join(' · ')||'/usr · /etc · /boot'))+'</strong></div>'
      +'<div class="kv"><span>Impact</span><strong>dashboard restarts for a few seconds</strong></div>'
      +'</div>',
    confirmText:'Repair & restart',cancelText:'Cancel',danger:true,icon:'wrench',
    note:'Runs through the monitoring service account\'s whitelisted sudo helper — no root shell.'
  });
  if(!ok)return false;
  toast('Repairing service mount namespace — the dashboard will restart briefly…','info');
  logActivity('fix','Repair service mount namespace');
  const j=await postJSON('/api/self-repair',{});
  if(j&&j.error){
    // A real error response (helper missing/denied). The restart may still be
    // in flight if the connection dropped mid-request, so verify below.
    toast('Repair request: '+String(j.error).slice(0,120),'err');
  }
  const up=await waitForApi();
  if(!up){toast('Dashboard did not return after the restart — check: monitoring status','err');return false;}
  const after=await fetchJSON('/api/self-repair');
  if(after&&after.blocked){
    toast('Repair did not clear the read-only mount: '+String(after.message||'see diagnostics').slice(0,120),'err');
    return false;
  }
  toast('Service mount namespace repaired — package paths are writable','ok');
  if(afterRepair)await afterRepair();
  return true;
}

// ================================================================
// Disks / ports / network
// ================================================================
// Worst non-root mount, so a full /home or /var is not hidden behind a
// comfortable-looking root percentage on the Disk card.
let worstMount=null;
function updateDiskNote(){
  const note=$('#diskNote');
  if(!note)return;
  // Only call out another mount when it is actually at or above the warn
  // threshold — otherwise the note is just noise on a healthy machine.
  if(worstMount&&worstMount.mount!=='/'&&worstMount.percent>=S.th.diskWarn){
    note.innerHTML='root filesystem · <b>'+esc(worstMount.mount)+'</b> '+worstMount.percent+'%';
    note.title=worstMount.mount+' is at '+worstMount.percent+'%';
  }else{
    note.textContent='root filesystem';
    note.removeAttribute('title');
  }
}
async function loadDisks(){
  const d=await fetchJSON('/api/disks');
  const el=$('#disksList');
  if(d&&Array.isArray(d.disks)&&d.disks.length){
    worstMount=d.disks.slice().sort((a,b)=>b.percent-a.percent)[0];
    updateDiskNote();
    checkDiskAlerts(d.disks);
  }
  if(!el)return;
  if(!d||!d.disks){el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('disk',22)+'</div><strong>Disk data unavailable</strong><span class="dim">'+(d&&d.error?esc(d.error):'the API did not respond')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadDisks()">'+icon('refresh',13)+' Retry</button></div>';return;}
  if(!d.disks.length){el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('disk',22)+'</div><strong>No mounted filesystems</strong></div>';return;}
  el.innerHTML=d.disks.map(k=>`
    <div class="disk-row">
      <div class="disk-head">
        <span class="mount">${esc(k.mount)}</span>
        <span class="meta">${esc(k.device)} · ${esc(k.fstype)}</span>
        <span class="size">${k.used_gb} / ${k.total_gb} GB</span>
        <span class="pill ${k.percent>=85?'fail':k.percent>=60?'warn':'ok'}">${k.percent}%</span>
      </div>
      <div class="progress-track"><div class="progress-fill ${statusClass(k.percent)}" style="width:${Math.min(100,k.percent)}%"></div></div>
    </div>`).join('');
}

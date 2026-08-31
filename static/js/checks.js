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
}

// ================================================================
// Maintenance actions
// ================================================================
const FIX_META={
  update:{icon:'refresh',label:'Update Lists',desc:'Refresh repositories'},
  upgrade:{icon:'up',label:'Upgrade',desc:'Install available updates'},
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
  if(j&&j.error){toast('Fix failed: '+j.error,'err');logActivity('fix','Fix failed: '+meta.label,false);}
  else{toast(meta.desc||('Completed: '+action));logActivity('fix','Fix completed: '+meta.label);}
  updateChecks();
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

let alertRecords=[];
try{alertRecords=JSON.parse(localStorage.getItem('monitoring:alerts')||'[]');}catch(e){alertRecords=[];}
const activeAlerts={}; // key -> level
function persistAlerts(){try{localStorage.setItem('monitoring:alerts',JSON.stringify(alertRecords.slice(0,200)));}catch(e){}}
function recordAlert(level,metric,msg,value){
  alertRecords.unshift({id:Date.now()+'-'+Math.random().toString(36).slice(2,7),t:Date.now(),level,metric,msg,value});
  alertRecords=alertRecords.slice(0,200);
  persistAlerts();renderAlerts();refreshAlertBadge();
}
function checkAlerts(d){
  if(!S.alerts)return;
  const tempC=(typeof d.temp==='string'?parseFloat(d.temp):d.temp);
  const rules=[
    {k:'cpu',label:'CPU usage',v:d.cpu_percent,warn:S.th.cpuWarn,crit:S.th.cpuCrit,unit:'%'},
    {k:'ram',label:'Memory usage',v:d.ram_percent,warn:S.th.ramWarn,crit:S.th.ramCrit,unit:'%'},
    {k:'disk',label:'Disk usage',v:d.disk_percent,warn:S.th.diskWarn,crit:S.th.diskCrit,unit:'%'},
    {k:'temp',label:'Temperature',v:isNaN(tempC)?null:tempC,warn:S.th.tempWarn,crit:9999,unit:'°C'}
  ];
  rules.forEach(r=>{
    const level=(r.v!=null&&r.crit!=null&&r.v>=r.crit)?'crit':(r.v!=null&&r.v>=r.warn)?'warn':null;
    const prev=activeAlerts[r.k]||null;
    if(level&&level!==prev){
      activeAlerts[r.k]=level;
      const msg=r.label+' '+r.v.toFixed(1)+r.unit+' ≥ '+(level==='crit'?r.crit:r.warn)+r.unit+' threshold';
      recordAlert(level,r.k,msg,r.v);
      toast(msg,level==='crit'?'err':'info');
    }else if(!level&&prev){
      delete activeAlerts[r.k];
      recordAlert('ok',r.k,r.label+' back to normal ('+r.v.toFixed(1)+r.unit+')',r.v);
    }
  });
  refreshAlertBadge();
}
// Non-root filesystems were never alerted on: /api/status only reports "/".
// Keyed per mount so each volume latches independently.
function checkDiskAlerts(disks){
  if(!S.alerts||!Array.isArray(disks))return;
  disks.forEach(dk=>{
    if(dk.mount==='/')return;                 // already covered by checkAlerts
    const v=dk.percent;
    if(typeof v!=='number'||isNaN(v))return;
    const key='mount:'+dk.mount;
    const level=v>=S.th.diskCrit?'crit':v>=S.th.diskWarn?'warn':null;
    const prev=activeAlerts[key]||null;
    if(level&&level!==prev){
      activeAlerts[key]=level;
      const msg='Disk '+dk.mount+' '+v.toFixed(1)+'% ≥ '+(level==='crit'?S.th.diskCrit:S.th.diskWarn)+'% threshold';
      recordAlert(level,'disk',msg,v);
      toast(msg,level==='crit'?'err':'info');
    }else if(!level&&prev){
      delete activeAlerts[key];
      recordAlert('ok','disk','Disk '+dk.mount+' back to normal ('+v.toFixed(1)+'%)',v);
    }
  });
  refreshAlertBadge();
}
function refreshAlertBadge(){
  const n=Object.keys(activeAlerts).length;
  const bell=$('#bellCount'),dot=$('#dotAlerts');
  if(bell){bell.hidden=n===0;bell.textContent=n;}
  if(dot){dot.hidden=n===0;dot.textContent=n;}
  const badge=$('#alertStatusBadge');
  if(badge){badge.textContent=S.alerts?'on':'off';badge.className='badge '+(S.alerts?(n?'fail':'ok'):'');}
}
let alertFilter='all';
function setAlertFilter(f){
  alertFilter=f;
  $$('#alertFilterSeg .seg-btn').forEach(b=>{
    const on=b.dataset.f===f;
    b.classList.toggle('active',on);
    b.setAttribute('aria-pressed',on?'true':'false');
  });
  renderAlerts();
}
// Full date+time for alert entries: alerts can be days old, so a bare HH:MM
// timestamp was ambiguous.
function alertWhen(ts){
  const d=new Date(ts),now=new Date();
  const sameDay=d.toDateString()===now.toDateString();
  return sameDay?todayAt(ts):(d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+todayAt(ts));
}
function filteredAlerts(){
  if(alertFilter==='all')return alertRecords;
  if(alertFilter==='ok')return alertRecords.filter(a=>a.level==='ok');
  return alertRecords.filter(a=>a.level===alertFilter);
}
function renderAlerts(){
  const crit=alertRecords.filter(a=>a.level==='crit').length;
  const warn=alertRecords.filter(a=>a.level==='warn').length;
  $('#alertCritCount').textContent=crit;
  $('#alertWarnCount').textContent=warn;
  $('#alertEventCount').textContent=alertRecords.length;
  const el=$('#alertList');
  if(!alertRecords.length){
    el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('shield',22)+'</div><strong>No alerts recorded</strong><span class="dim">Everything looks healthy. Alerts appear when thresholds set in Settings are exceeded.</span></div>';
    return;
  }
  const rows=filteredAlerts();
  if(!rows.length){
    el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('shield',22)+'</div><strong>No '+esc(alertFilter)+' alerts</strong><span class="dim">Change the filter to see other events.</span></div>';
    return;
  }
  el.innerHTML=rows.map(a=>`
    <div class="alert-item ${a.level==='ok'?'ok':a.level}">
      <span class="ai-dot"></span>
      <div class="ai-body"><div class="ai-title">${esc(a.msg)}</div>
      <div class="ai-sub">${esc(a.metric)} · ${esc(a.level)}</div></div>
      <span class="ai-time" title="${esc(new Date(a.t).toLocaleString())}">${alertWhen(a.t)}</span>
    </div>`).join('');
}
function exportAlertsCSV(){
  if(!alertRecords.length){toast('Nothing to export','info');return;}
  exportCSV('monitoring-alerts-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv',
    ['Time','Level','Metric','Message','Value'],
    alertRecords.map(a=>[new Date(a.t).toISOString(),a.level,a.metric,a.msg,a.value??'']));
}
async function clearAlerts(){
  if(!alertRecords.length){toast('Alert history is already empty','info');return;}
  const ok=await confirmDlg('Clear alert history',
    'Delete all '+alertRecords.length+' recorded alert event(s) from this browser? This cannot be undone.',true);
  if(!ok)return;
  alertRecords=[];persistAlerts();renderAlerts();refreshAlertBadge();
  toast('Alert history cleared','info');
  logActivity('system','Cleared alert history');
}

// ================================================================
// Diagnose / Troubleshooting Hub (guided diagnostics)
// ================================================================

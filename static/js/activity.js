let activity=[];
try{activity=JSON.parse(localStorage.getItem('monitoring:activity')||'[]');}catch(e){activity=[];}
function logActivity(kind,title,ok){
  activity.unshift({t:Date.now(),kind,title,ok:ok!==false});
  activity=activity.slice(0,100);
  try{localStorage.setItem('monitoring:activity',JSON.stringify(activity));}catch(e){}
  renderActivity();
}
const ACTIVITY_ICONS={fix:'wrench',service:'gear',vm:'monitor',process:'cpu',system:'gear',theme:'sun',logs:'terminal'};
function renderActivity(){
  const mini=$('#activityMini'),full=$('#activityFull');
  const html=list=>list.map(a=>`
    <div class="activity-item ${a.ok?'ok':'err'}">
      <span class="ac-ic">${icon(ACTIVITY_ICONS[a.kind]||'pulse',12)}</span>
      <div class="ac-body"><div class="ac-title">${esc(a.title)}</div>
      <div class="ac-time">${todayAt(a.t)}</div></div>
    </div>`).join('');
  if(mini){
    if(!activity.length){mini.innerHTML='<div class="empty-state" style="padding:22px"><div class="empty-icon">'+icon('pulse',20)+'</div><strong>No activity yet</strong><span class="dim">Actions you take will appear here.</span></div>';}
    else mini.innerHTML=html(activity.slice(0,6));
    $('#activityBadge').textContent=activity.length?activity.length+' events':'';
  }
  if(full){
    if(!activity.length){full.innerHTML='<div class="empty-state" style="padding:22px"><div class="empty-icon">'+icon('pulse',20)+'</div><strong>No activity yet</strong></div>';}
    else full.innerHTML=html(activity);
    const b=$('#activityBadge2');if(b)b.textContent=activity.length+' events';
  }
}
async function clearActivity(){
  if(!activity.length){toast('Activity log is already empty','info');return;}
  const ok=await confirmDlg('Clear activity log',
    'Delete all '+activity.length+' recorded action(s) from this browser? This local audit trail cannot be recovered.',true);
  if(!ok)return;
  activity=[];try{localStorage.setItem('monitoring:activity','[]');}catch(e){}
  renderActivity();toast('Activity log cleared','info');
}
function exportActivityCSV(){
  if(!activity.length){toast('Nothing to export','info');return;}
  exportCSV('monitoring-activity-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv',
    ['Time','Kind','Action','Outcome'],
    activity.map(a=>[new Date(a.t).toISOString(),a.kind,a.title,a.ok?'ok':'failed']));
}

// ================================================================
// Health checks
// ================================================================

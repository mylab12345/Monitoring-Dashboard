let diagData=null, diagFilter='all', diagCatFilter='all', diagExpanded={}, diagOpenAll=false, diagGroupOpen={};
let diagFixOutcomes={}, diagVerifyState={};
let diagHistory=[];
try{diagHistory=JSON.parse(localStorage.getItem('monitoring:diag-history')||'[]');}catch(e){diagHistory=[];}
const DIAG_CATS=['cpu','memory','disk','services','network','packages','kernel'];
const DIAG_CAT_ICON={cpu:'cpu',memory:'mem',disk:'disk',services:'gear',network:'network',packages:'package',kernel:'zap'};
const DIAG_CAT_LABEL={cpu:'CPU & Processes',memory:'Memory & Swap',disk:'Disk & Storage',services:'Services & Daemons',network:'Network',packages:'Packages & Updates',kernel:'Kernel & Hardware'};

function diagGrade(score){
  if(score>=95)return{label:'Excellent',color:'#34d399'};
  if(score>=80)return{label:'Good',color:'#2dd4bf'};
  if(score>=60)return{label:'Fair',color:'#fbbf24'};
  if(score>=40)return{label:'At Risk',color:'#fb923c'};
  return{label:'Poor',color:'#f87171'};
}
function diagSev(sev){const s=String(sev||'info').toLowerCase();return s==='critical'?'crit':s==='warning'?'warn':s==='info'?'info':'warn';}
function diagSevOf(i){return i&&(i.severity||(i.state==='critical'?'critical':i.state==='warning'?'warning':'info'));}
function diagAllIssues(d){const iss=(d&&d.issues)||{};return [].concat(iss.critical||[],iss.warnings||[],iss.info||[]);}
function diagFind(id){return diagAllIssues(diagData).find(i=>String(i.id)===String(id));}
function diagMatches(i){
  if(diagFilter!=='all'&&diagSev(diagSevOf(i))!==diagFilter)return false;
  if(diagCatFilter!=='all'&&i.category!==diagCatFilter)return false;
  return true;
}
function diagFixIdOf(i){return i&&(i.fix||(i.recommended_fix&&i.recommended_fix.id));}

async function loadTroubleshooting(silent){
  const list=$('#troubleshootList'),btn=$('#diagScanBtn');
  if(!silent){
    if(list)list.innerHTML='<div class="diag-loading">'
      +'<div class="skel" style="height:58px;margin-bottom:10px"></div>'
      +'<div class="skel" style="height:58px;margin-bottom:10px"></div>'
      +'<div class="skel" style="height:58px"></div></div>';
    if(btn){btn.disabled=true;btn.innerHTML=icon('refresh',14)+' Scanning…';}
    const hg=$('#healthGrade');if(hg){hg.textContent='diagnosing…';hg.className='pill grade';}
  }
  try{
    const r=await apiFetch('/api/troubleshooting');
    if(!r.ok)throw new Error('HTTP '+r.status);
    const data=await r.json();
    diagData=data;
    renderTroubleshooting();
    refreshTroubleBadge();
    const st=$('#diagScanTime');if(st)st.textContent='last scan '+todayAt(Date.now());
    if(!silent)addDiagHistory({type:'scan',score:data.health_score||0,total:data.total||0,
      crit:((data.issues||{}).critical||[]).length,warn:((data.issues||{}).warnings||[]).length,info:((data.issues||{}).info||[]).length});
  }catch(e){
    console.error('Failed to load troubleshooting data:',e);
    if(list)list.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('alert',24)+'</div><strong>Diagnostics unavailable</strong><span class="dim">'+(String(e.message||'').indexOf('HTTP')>=0?'The diagnostics service returned an error.':'Could not reach the diagnostics service.')+'</span><button class="btn btn-sm" style="margin-top:12px" onclick="loadTroubleshooting(false)">'+icon('refresh',13)+' Try again</button></div>';
    const tb=$('#totalIssuesBadge');if(tb)tb.textContent='unavailable';
    const hg=$('#healthGrade');if(hg){hg.textContent='offline';hg.className='pill grade warn';}
  }finally{
    if(btn){btn.disabled=false;btn.innerHTML=icon('refresh',14)+' Run Scan';}
  }
}

function drawDiagSpark(){
  const cv=$('#diagScoreSpark');if(!cv||!cv.getContext)return;
  const pts=diagHistory.filter(h=>h.type==='scan').slice(0,30).reverse().map(h=>h.score||0);
  if(pts.length<2){cv.style.display='none';return;}
  cv.style.display='block';
  const dpr=Math.min(window.devicePixelRatio||1,2);
  const w=cv.clientWidth||240,h=40;
  if(cv.width!==w*dpr||cv.height!==h*dpr){cv.width=w*dpr;cv.height=h*dpr;}
  const ctx=cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const lo=Math.min(40,...pts),hi=Math.max(100,...pts);
  const span=Math.max(10,hi-lo);
  const x=i=>2+((i/(pts.length-1))||0)*(w-4);
  const y=v=>h-5-((v-lo)/span)*(h-12);
  const grad=ctx.createLinearGradient(0,0,w,0);
  grad.addColorStop(0,'rgba(34,211,238,.9)');
  grad.addColorStop(1,'rgba(45,212,191,.9)');
  ctx.beginPath();
  pts.forEach((v,i)=>{const px=x(i),py=y(v);i===0?ctx.moveTo(px,py):ctx.lineTo(px,py);});
  ctx.strokeStyle=grad;ctx.lineWidth=2;ctx.lineJoin='round';ctx.lineCap='round';ctx.stroke();
}

function renderTroubleshooting(){
  const d=diagData;if(!d)return;
  const score=Math.max(0,Math.min(100,Number(d.health_score)||0));
  const grade=diagGrade(score);
  const maxDash=326.7;
  const sv=$('#healthScoreValue');if(sv)sv.textContent=score;
  const bar=$('#healthScoreBar');
  if(bar){bar.style.strokeDashoffset=maxDash-(score/100)*maxDash;bar.style.stroke=grade.color;}
  const hg=$('#healthGrade');
  if(hg){hg.textContent=grade.label;hg.style.color=grade.color;hg.className='pill grade';}
  const crit=((d.issues||{}).critical||[]).length,
        warn=((d.issues||{}).warnings||[]).length,
        info=((d.issues||{}).info||[]).length;
  const set=(id,v)=>{const el=$(id);if(el)el.textContent=v;};
  set('#issueCritCount',crit);set('#issueWarnCount',warn);set('#issueInfoCount',info);
  set('#totalIssuesBadge',(d.total||0)+' total');
  $('#healthSummary').textContent=crit
    ?'⚠ '+crit+' critical issue(s) — address them first, then re-scan.'
    :warn
      ?'No critical issues. '+warn+' warning(s) and '+info+' note(s) to review below.'
      :info
        ?'All critical checks pass. '+info+' minor note(s) can be cleaned up.'
        :'All systems healthy — no issues detected across any monitored area.';
  const sy=d.system||{};
  set('#diagHost',sy.hostname||'--');
  set('#diagUptime',sy.uptime_s?fmtDur(sy.uptime_s):'--');
  renderDiagCaps();
  renderDiagCats();
  renderDiagGroups();
  renderDiagHistory();
  drawDiagSpark();
  const fb=$('#fixAllBtn');
  if(fb)fb.disabled=(d.total||0)===0;
}

function renderDiagCaps(){
  const el=$('#diagCaps');if(!el||!diagData)return;
  const c=diagData.capabilities||{},s=diagData.system||{};
  const chips=[];
  chips.push('<span class="cap-chip '+(c.root?'ok':'')+'">'+icon('shield',11)+'root '+(c.root?'on':'limited')+'</span>');
  if(c.sudo)chips.push('<span class="cap-chip ok">'+icon('wrench',11)+'passwordless sudo</span>');
  if(c.pkg_manager)chips.push('<span class="cap-chip">'+icon('package',11)+esc(c.pkg_manager)+'</span>');
  if(c.systemd)chips.push('<span class="cap-chip">'+icon('gear',11)+'systemd</span>');
  if(c.journal)chips.push('<span class="cap-chip">'+icon('terminal',11)+'journal</span>');
  if(s.os)chips.push('<span class="cap-chip" title="'+esc(s.os)+'">'+icon('terminal',11)+esc(String(s.os).split(' ').slice(0,3).join(' '))+'</span>');
  el.innerHTML=chips.join('');
}

function renderDiagCats(){
  const el=$('#diagCats');if(!el||!diagData)return;
  const cats=diagData.categories||{};
  el.innerHTML=DIAG_CATS.map(k=>{
    const c=cats[k]||{label:DIAG_CAT_LABEL[k],icon:DIAG_CAT_ICON[k],state:'ok',count:0};
    const cls=c.state==='critical'?'crit':c.state==='warning'?'warn':c.state==='info'?'info':'ok';
    return '<button class="cat-chip '+cls+(diagCatFilter===k?' sel':'')+'" onclick="setDiagCat(\''+k+'\')" title="'+((c.count||0)?c.count+' issue(s) — '+esc(c.label):esc(c.label))+' — all clear">'
      +icon(c.icon||DIAG_CAT_ICON[k]||'alert',14)+'<span>'+esc(c.label)+'</span><b>'+(c.count||0)+'</b></button>';
  }).join('');
}

function setDiagCat(k){
  diagCatFilter=diagCatFilter===k?'all':k;
  renderDiagCats();renderDiagGroups();
}
function setDiagFilter(f){
  diagFilter=f;
  $$('#diagFilter .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.f===f));
  renderDiagGroups();
}
function toggleDiagExpandAll(){
  diagOpenAll=!diagOpenAll;
  diagAllIssues(diagData).forEach(i=>{diagExpanded[i.id]=diagOpenAll;});
  if(diagOpenAll)DIAG_CATS.forEach(k=>{diagGroupOpen[k]=true;});
  else diagGroupOpen={};
  const b=$('#diagExpandBtn');
  if(b)b.innerHTML=icon('down',14)+(diagOpenAll?' Collapse all':' Expand all');
  renderDiagGroups();
}

function renderDiagGroups(){
  const el=$('#troubleshootList');if(!el||!diagData)return;
  const all=diagAllIssues(diagData);
  const fb=$('#fixAllBtn');if(fb)fb.disabled=!all.length;
  if(!all.length){
    el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('checkCircle',30)+'</div><strong>All systems healthy!</strong><span class="dim">No issues detected across CPU, memory, disk, services, network, packages, kernel.</span></div>';
    return;
  }
  const cats=diagData.categories||{};
  let html='';
  DIAG_CATS.forEach(k=>{
    const c=cats[k];if(!c)return;
    const its=(c.issues||[]).filter(diagMatches);
    if(!its.length)return;
    const cls=c.state==='critical'?'crit':c.state==='warning'?'warn':c.state==='info'?'info':'ok';
    const open=diagOpenAll||!!diagGroupOpen[k];
    html+='<details class="diag-group '+cls+'" data-group="'+k+'" '+(open?'open':'')+'>'
      +'<summary class="diag-group-head">'
        +'<span class="dg-ic">'+icon(c.icon||DIAG_CAT_ICON[k]||'alert',16)+'</span>'
        +'<span class="dg-name">'+esc(c.label||k)+'</span>'
        +'<span class="pill dg-state '+cls+'">'+esc(c.state==='ok'?'ok':cls)+'</span>'
        +'<span class="dg-count">'+its.length+' issue'+(its.length>1?'s':'')+'</span>'
        +'<span class="dg-chev">'+icon('down',14)+'</span>'
      +'</summary>'
      +'<div class="diag-group-body">'+its.map(renderDiagIssue).join('')+'</div>'
    +'</details>';
  });
  el.innerHTML=html||('<div class="empty-state"><div class="empty-icon">'+icon('search',22)+'</div><strong>No issues match the current filters</strong><span class="dim">Switch the severity filter or category chips below to see more.</span></div>');
  el.querySelectorAll('details.diag-group').forEach(d=>{
    d.addEventListener('toggle',()=>{diagGroupOpen[d.dataset.group]=d.open;});
  });
}

function renderDiagIssue(i){
  const id=esc(String(i.id||''));
  const sev=diagSev(diagSevOf(i));
  const open=!!diagExpanded[i.id];
  const fixable=!!diagFixIdOf(i);
  return '<div class="diag-issue issue-item '+sev+(open?' open':'')+'" data-id="'+id+'">'
    +'<div class="issue-row" role="button" tabindex="0" aria-expanded="'+open+'" onclick="toggleDiagIssue(\''+id+'\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();toggleDiagIssue(\''+id+'\');}">'
      +'<span class="issue-icon">'+icon(i.icon||'alert',18)+'</span>'
      +'<span class="issue-main">'
        +'<span class="issue-title"><span class="severity-badge '+sev+'">'+sev.toUpperCase()+'</span>'+esc(i.name)+'</span>'
        +'<span class="issue-detail">'+esc(i.detail)+'</span>'
      +'</span>'
      +'<span class="issue-tags">'
        +'<span class="cat-tag">'+esc(i.category||'system')+'</span>'
        +(fixable?'<span class="cat-tag fix" title="A safe one-click fix is available">fixable</span>':'')
      +'</span>'
      +'<span class="issue-chev">'+icon('down',14)+'</span>'
    +'</div>'
    +'<div class="issue-flow" '+(open?'':'hidden')+'>'+issueFlowHtml(i)+'</div>'
  +'</div>';
}

function toggleDiagIssue(id){
  diagExpanded[id]=!diagExpanded[id];
  const el=document.querySelector('.diag-issue[data-id="'+id+'"]');
  if(el){
    el.classList.toggle('open',!!diagExpanded[id]);
    const flow=el.querySelector('.issue-flow');
    if(flow)flow.hidden=!diagExpanded[id];
    const row=el.querySelector('.issue-row');
    if(row)row.setAttribute('aria-expanded',String(!!diagExpanded[id]));
  }
}

function issueFlowHtml(i){
  const fix=i.recommended_fix||{};
  const fixId=i.fix||fix.id;
  const outcome=diagFixOutcomes[i.id];
  const verify=diagVerifyState[i.id];
  return '<div class="flow">'
    +flowStep('Problem','<p>'+esc(i.detail||'—')+'</p>')
    +flowStep('Evidence',evidenceHtml(i.evidence||[]))
    +flowStep('Impact','<p>'+esc(i.impact||'Impact not assessed for this check — monitor the metric over time.')+'</p>')
    +flowStep('Recommended Fix',fixRecoHtml(i))
    +flowStep('Verify',verifyHtml(i,verify))
    +'</div>'
    +deepHtml(i)
    +(verify&&verify.resolved?'<div class="verify-banner ok">'+icon('checkCircle',14)+'<span><b>Auto-verification passed:</b> '+esc(verify.message||'issue resolved')+'</span></div>':'')
    +(outcome?outcomeBannerHtml(outcome):'');
}

function flowStep(label,content){
  return '<div class="flow-step"><span class="flow-num">'+icon('pulse',13)+'</span>'
    +'<div class="flow-body"><div class="flow-label">'+esc(label)+'</div><div class="flow-content">'+content+'</div></div></div>';
}
function evidenceHtml(evs){
  if(!evs||!evs.length)return '<p class="dim small">No automated evidence was collected for this check.</p>';
  return '<div class="evidence-table">'+evs.map(e=>'<div class="ev-row"><span class="ev-k">'+esc(e.label)+'</span><span class="ev-v">'+esc(e.value)+'</span>'+(e.cmd?'<code class="ev-c">'+esc(e.cmd)+'</code>':'')+'</div>').join('')+'</div>';
}
function commandListHtml(cmds){
  if(!cmds||!cmds.length)return '';
  return '<div class="cmd-list">'+cmds.map(c=>'<div class="cmd-copy-row"><code>'+esc(c)+'</code><button class="icon-btn" type="button" title="Copy command" aria-label="Copy command" onclick="copyDiagCommand(this)">'+icon('copy',12)+'</button></div>').join('')+'</div>';
}
async function copyDiagCommand(btn){
  const code=btn&&btn.parentElement&&btn.parentElement.querySelector('code');
  if(!code)return;
  const text=code.textContent||'';
  try{await navigator.clipboard.writeText(text);toast('Command copied','ok');}
  catch(e){
    const ta=document.createElement('textarea');ta.value=text;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();toast('Command copied','ok');
  }
}
function fixRecoHtml(i){
  const fix=i.recommended_fix||{};
  const fixId=i.fix||fix.id;
  const risk=fix.risk||'low';
  const cmds=(fix.commands||[]);
  if(!fixId){
    return '<div class="fix-reco noact">'
      +(fix.label?'<div class="fix-reco-head"><strong>'+esc(fix.label)+'</strong><span class="pill risk-'+esc(risk)+'">'+esc(risk)+' risk · manual</span></div>':'')
      +'<p class="dim small">'+esc(fix.description||'No automated fix is offered. Follow the commands below manually and use Verify to confirm.')+'</p>'
      +commandListHtml(cmds)
      +'</div>';
  }
  return '<div class="fix-reco">'
    +'<div class="fix-reco-head"><strong>'+esc(fix.label||fixId)+'</strong><span class="pill risk-'+esc(risk)+'">'+esc(risk)+' risk</span></div>'
    +'<p class="dim small">'+esc(fix.description||'Apply this fix; verification runs automatically afterwards.')+'</p>'
    +commandListHtml(cmds)
    +'<div class="fix-actions"><button class="btn btn-sm btn-primary" id="fixBtn-'+esc(i.id)+'" onclick="confirmDiagFix(\''+esc(i.id)+'\')">'+icon('wrench',13)+' Apply Fix</button></div>'
  +'</div>';
}
function verifyHtml(i,state){
  const steps=(i.verify||[]);
  if(!steps.length)return '<p class="dim small">No automated check — observe the affected metric after any change.</p>';
  const html=steps.map(s=>'<div class="verify-row">'+(s.cmd?'<code class="ev-c">'+esc(s.cmd)+'</code>':'')+'<span class="verify-expect">'+esc(s.label)+': <b>'+esc(s.value)+'</b></span></div>').join('');
  if(!state)return html;
  const st=state.state==='unknown'?'info':state.resolved?'ok':'warn';
  return html+'<div class="verify-banner '+st+'">'+icon(state.resolved?'checkCircle':state.state==='unknown'?'info':'alert',14)+'<span>'+esc(state.message||'verification complete')+'</span></div>';
}
function deepHtml(i){
  const d=i.deep;if(!d)return '';
  let body='';
  if(d.kind==='procs'){
    const rows=d.rows||[];
    body='<div class="table-wrap diag-deep-table"><table class="table"><thead><tr><th>PID</th><th>Process</th><th>User</th><th>CPU %</th><th>MEM %</th><th>RSS</th></tr></thead><tbody>'
      +rows.map(r=>'<tr><td class="mono dim">'+(r.pid??'—')+'</td><td class="mono strong">'+esc(r.name||'—')+'</td><td class="mono dim" style="font-size:.68rem">'+esc(r.user||'—')+'</td><td class="mono">'+esc(r.cpu)+'</td><td class="mono">'+esc(r.mem)+'</td><td class="mono dim">'+esc(r.rss_mb!=null?r.rss_mb+' MB':'—')+'</td></tr>').join('')
      +'</tbody></table></div>';
  }else if(d.kind==='logs'){
    body='<pre class="diag-log-sample">'+((d.lines||[]).map(l=>esc(l)).join('\n')||'no samples')+'</pre>';
  }else if(d.kind==='services'){
    body='<div class="diag-svc-list">'+((d.rows||[]).map(r=>'<div class="diag-svc"><span class="mono">'+esc(r.name)+'</span><span class="pill '+(r.status==='ok'?'ok':'fail')+'">'+esc(r.status)+'</span></div>').join(''))+'</div>';
  }else if(d.kind==='metrics'&&d.rows){
    body='<div class="kv-grid">'+d.rows.map(r=>'<div class="kv"><span>'+esc(r.label)+'</span><strong>'+esc(r.value)+'</strong></div>').join('')+'</div>';
  }
  if(!body)return '';
  return '<details class="diag-deep"><summary>'+icon('search',13)+' Deep diagnostics — '+esc(d.title||'details')+'</summary>'+body+'</details>';
}
function outcomeBannerHtml(o){
  const m=o.status==='resolved'?['ok','Fixed & verified']
    :o.status==='applied'?['ok','Fix applied']
    :o.status==='failed'?['err','Fix failed']
    :['warn','Already handled'];
  return '<div class="verify-banner '+m[0]+'">'+icon(o.status==='failed'?'alert':m[0]==='ok'?'checkCircle':'info',14)+'<span><b>'+esc(m[1])+'</b> — '+esc(o.message||'')+'</span></div>';
}
function renderDiagFlow(id){
  const el=document.querySelector('.diag-issue[data-id="'+id+'"] .issue-flow');
  const i=diagFind(id);
  if(el&&i)el.innerHTML=issueFlowHtml(i);
}

async function confirmDiagFix(id){
  const i=diagFind(id);if(!i)return;
  const fix=i.recommended_fix||{};
  const fixId=diagFixIdOf(i);
  if(!fixId)return;
  const risk=fix.risk||'low';
  const cmds=(fix.commands||[]);
  const ok=await openModal({
    title:'Apply fix — '+i.name,
    text:'This applies the recommended fix for “'+fixId+'” to this host.',
    html:'<div class="fix-preview">'
      +(fix.label?'<div class="kv"><span>Action</span><strong>'+esc(fix.label)+'</strong></div>':'')
      +'<div class="kv"><span>Risk level</span><strong>'+esc(risk)+'</strong></div>'
      +(cmds.length?'<div class="cmd-list">'+cmds.map(c=>'<code>'+esc(c)+'</code>').join('')+'</div>':'')
      +'</div>',
    confirmText:'Apply fix',cancelText:'Cancel',
    danger:risk==='high',icon:'wrench',
    note:'A targeted verification runs automatically after the fix.'
  });
  if(!ok)return;
  applyDiagFix(id,fixId);
}

async function applyDiagFix(id,fixId){
  const btn=$('#fixBtn-'+id);
  const old=btn?btn.innerHTML:'';
  if(btn){btn.disabled=true;btn.innerHTML=icon('refresh',13)+' Fixing & verifying…';}
  let failed=false;
  try{
    const result=await postJSON('/api/troubleshooting/fix-all',{fixes:[fixId]});
    if(!result||result.error)throw new Error((result&&result.error)||'request failed');
    const okEntry=(result.fixed||[]).find(f=>f.fix===fixId);
    const badEntry=(result.failed||[]).find(f=>f.fix===fixId);
    if(badEntry){
      failed=true;
      diagFixOutcomes[id]={status:'failed',message:badEntry.output||'Fix failed'};
      toast('Fix failed: '+String(badEntry.output||'Unknown error').slice(0,120),'err');
      logActivity('fix','Diagnose fix failed: '+fixId,false);
      if(detectReadOnlyMount(badEntry.output)){
        // Package ops are blocked by the service's read-only mount namespace.
        // Repair it through the service account, then retry this same fix.
        const repaired=await offerSelfRepair(()=>applyDiagFix(id,fixId));
        if(repaired){renderDiagFlow(id);loadTroubleshooting(true);return;}
      }
    }else if(okEntry){
      diagFixOutcomes[id]={status:'applied',message:String(okEntry.output||'Fix applied').slice(0,200)};
      toast('Fix applied — verifying…','info');
      logActivity('fix','Diagnose fix applied: '+fixId);
    }else{
      diagFixOutcomes[id]={status:'noop',message:((result.skipped||[])[0]&&(result.skipped||[])[0].output)||'Nothing to do'};
      toast('Fix completed — nothing needed changing','info');
      logActivity('fix','Diagnose fix (no-op): '+fixId);
    }
    // Automatic post-fix verification
    const v=await verifyDiagIssues([id]);
    if(v[id]){
      diagVerifyState[id]=v[id];
      if(v[id].resolved){
        diagFixOutcomes[id].status='resolved';
        diagFixOutcomes[id].message=(diagFixOutcomes[id].message||'')+' · verified';
        toast('Verified: '+fixId+' resolved ✔','ok');
      }else if(!failed){
        toast('Fix ran — issue still present, review the verification details','info');
      }
    }
    addDiagHistory({type:'fix',fix:fixId,id,resolved:!!(v[id]&&v[id].resolved),ok:!failed});
    renderDiagFlow(id);
    loadTroubleshooting(true);
  }catch(e){
    failed=true;
    diagFixOutcomes[id]={status:'failed',message:'Request failed: '+e.message};
    toast('Error applying fix: '+e.message,'err');
    renderDiagFlow(id);
  }
  if(btn){btn.disabled=false;btn.innerHTML=old;}
}

async function verifyDiagIssues(ids){
  if(!ids||!ids.length)return{};
  try{
    const j=await postJSON('/api/troubleshooting/verify',{issue_ids:ids});
    if(!j||j.error)return{};
    return (j&&j.results)||{};
  }catch(e){return{};}
}

async function fixAllIssues(){
  if(!diagData)return;
  const all=diagAllIssues(diagData).filter(i=>diagFixIdOf(i));
  if(!all.length){toast('No safe fixes available for current issues','info');return;}
  const fixIds=[...new Set(all.map(diagFixIdOf))];
  const ok=await openModal({
    title:'Fix all detected issues',
    text:'This applies '+fixIds.length+' safe fix action(s) for '+all.length+' detected issue(s). Each result is verified automatically afterwards.',
    html:'<div class="cmd-list">'+fixIds.map(f=>'<code>'+esc(f)+'</code>').join('')+'</div>',
    confirmText:'Fix all',cancelText:'Cancel',danger:true,icon:'wrench',
    note:'Post-fix verification runs automatically.'
  });
  if(!ok)return;
  const btn=$('#fixAllBtn'),old=btn?btn.innerHTML:'';
  if(btn){btn.disabled=true;btn.innerHTML=icon('refresh',16)+' Fixing & verifying…';}
  const ids=all.map(i=>i.id);
  let result={fixed:[],failed:[],skipped:[]},v={};
  try{
    result=await postJSON('/api/troubleshooting/fix-all',{fixes:fixIds});
    if(!result||result.error)throw new Error((result&&result.error)||'request failed');
    v=await verifyDiagIssues(ids);
    Object.keys(v).forEach(k=>{diagVerifyState[k]=v[k];});
    (result.fixed||[]).forEach(f=>{
      const i=all.find(x=>diagFixIdOf(x)===f.fix);
      if(i)diagFixOutcomes[i.id]={status:(v[i.id]&&v[i.id].resolved)?'resolved':'applied',message:String(f.output||'Fix applied').slice(0,200)};
    });
    (result.failed||[]).forEach(f=>{
      const i=all.find(x=>diagFixIdOf(x)===f.fix);
      if(i)diagFixOutcomes[i.id]={status:'failed',message:String(f.output||'Fix failed').slice(0,200)};
    });
    renderFixResults(result,v);
    if((result.failed||[]).some(f=>detectReadOnlyMount(f.output))){
      // One or more fixes were blocked by the read-only service mount
      // namespace; repair it through the service account, then re-run.
      const repaired=await offerSelfRepair(()=>fixAllIssues());
      if(repaired)return;
    }
    const resolved=Object.values(v).filter(x=>x&&x.resolved).length;
    addDiagHistory({type:'fix-all',fixed:(result.fixed||[]).length,failed:(result.failed||[]).length,
      skipped:(result.skipped||[]).length,resolved,ok:(result.failed||[]).length===0});
    toast('Fix all: '+(result.fixed||[]).length+' fixed, '+(result.failed||[]).length+' failed, '+resolved+' verified','ok');
    logActivity('fix','Fix all diagnostics ('+fixIds.join(', ')+')',(result.failed||[]).length===0);
    renderDiagGroups();
    loadTroubleshooting(true);
  }catch(e){
    toast('Error fixing issues: '+e.message,'err');
    renderFixResults({fixed:[],failed:[{fix:'request',output:e.message}],skipped:[]},{});
  }finally{
    if(btn){btn.disabled=false;btn.innerHTML=old;}
  }
}

function renderFixResults(result,v){
  const div=$('#fixResults');if(!div)return;
  div.hidden=false;
  const resolved=Object.values(v||{}).filter(x=>x&&x.resolved).length;
  div.innerHTML='<div class="fix-summary">'
    +'<div class="fix-stat success">'+(result.fixed||[]).length+' Fixed</div>'
    +'<div class="fix-stat error">'+(result.failed||[]).length+' Failed</div>'
    +'<div class="fix-stat skip">'+(result.skipped||[]).length+' Skipped · '+resolved+' verified</div>'
    +'</div>'
    +(result.fixed||[]).map(f=>'<div class="fix-item success"><strong>'+esc(f.fix)+'</strong>: '+esc(f.output)+'</div>').join('')
    +(result.failed||[]).map(f=>'<div class="fix-item error"><strong>'+esc(f.fix)+'</strong>: '+esc(f.output)+'</div>').join('')
    +(result.skipped||[]).map(f=>'<div class="fix-item skip"><strong>'+esc(f.fix)+'</strong>: '+esc(f.output)+'</div>').join('');
  div.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function refreshTroubleBadge(){
  const badge=$('#dotTrouble');
  const crit=(diagData&&diagData.issues&&diagData.issues.critical&&diagData.issues.critical.length)||0;
  const warn=(diagData&&diagData.issues&&diagData.issues.warnings&&diagData.issues.warnings.length)||0;
  if(!badge)return;
  badge.hidden=crit===0&&warn===0;
  badge.textContent=crit+warn;
}

// ---- Troubleshooting history / timeline (localStorage) ----
function saveDiagHistory(){try{localStorage.setItem('monitoring:diag-history',JSON.stringify(diagHistory));}catch(e){}}
function addDiagHistory(e){
  e.ts=e.ts||Date.now();
  diagHistory.unshift(e);
  diagHistory=diagHistory.slice(0,40);
  saveDiagHistory();
  renderDiagHistory();
}
function clearDiagHistory(){
  diagHistory=[];saveDiagHistory();renderDiagHistory();
  toast('Troubleshooting history cleared','info');
}
function renderDiagHistory(){
  const el=$('#diagHistory');if(!el)return;
  const b=$('#diagHistBadge');
  if(b)b.textContent=diagHistory.length?(diagHistory.length+' events'):'';
  if(!diagHistory.length){
    el.innerHTML='<div class="empty-state" style="padding:18px"><div class="empty-icon">'+icon('history',20)+'</div><strong>No troubleshooting history yet</strong><span class="dim">Scans and fix attempts will build a timeline here.</span></div>';
    return;
  }
  drawDiagSpark();
  el.innerHTML=diagHistory.slice(0,12).map(h=>{
    const cls=h.ok===false?'err':'ok';
    const d=new Date(h.ts||h.t||Date.now());
    const stamp=String(d.getFullYear())+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0')+' '+todayAt(d.getTime());
    let title='',sub='';
    if(h.type==='scan'){title='Health scan — score '+(h.score??'--');sub=(h.crit||0)+' critical · '+(h.warn||0)+' warnings · '+(h.info||0)+' info · '+(h.total||0)+' total';}
    else if(h.type==='fix'){title='Fix applied: '+h.fix;sub=h.resolved?'auto-verification passed — resolved':'fix complete, still monitoring';}
    else if(h.type==='fix-all'){title='Fix all — '+(h.fixed||0)+' fixed, '+(h.failed||0)+' failed';sub=(h.resolved||0)+' verified resolved · '+(h.skipped||0)+' skipped';}
    else{title=h.title||'Diagnostics event';sub=h.detail||'';}
    return '<div class="history-item '+cls+'"><span class="h-dot"></span><div class="h-body"><div class="h-title">'+esc(title)+'</div><div class="h-sub">'+esc(sub)+'</div></div><div class="h-time">'+esc(stamp)+'</div></div>';
  }).join('');
}

// ---- Diagnostic report export ----
function diagReportMarkdown(){
  const d=diagData;if(!d)return '# Monitoring Diagnostic Report\n\nNo scan has been run yet.';
  const g=diagGrade(d.health_score||0);
  const sy=d.system||{},caps=d.capabilities||{};
  const L=[];
  L.push('# Monitoring Diagnostic Report');L.push('');
  L.push('- Generated: '+new Date().toISOString());
  L.push('- Host: '+(sy.hostname||'—'));
  L.push('- System: '+(sy.os||'—')+' · '+(sy.kernel||'—')+' · '+(sy.arch||'—'));
  L.push('- CPU: '+(sy.cpu_model||'—')+' ('+(sy.cores||'—')+' cores)');
  L.push('- Health score: **'+(d.health_score||0)+' / 100 — '+g.label+'**');
  L.push('- Issues: '+(d.total||0)+' ('+((d.issues||{}).critical||[]).length+' critical, '+((d.issues||{}).warnings||[]).length+' warnings, '+((d.issues||{}).info||[]).length+' info)');
  L.push('- Capabilities: '+(caps.root?'root':'user')+(caps.sudo?' · passwordless sudo':'')+(caps.pkg_manager?' · '+caps.pkg_manager:'')+(caps.systemd?' · systemd':''));
  L.push('');
  const cats=d.categories||{};
  DIAG_CATS.forEach(k=>{
    const c=cats[k];if(!c||!(c.issues||[]).length)return;
    L.push('## '+c.label);L.push('');
    (c.issues||[]).forEach(i=>{
      L.push('### ['+String(i.severity||'info').toUpperCase()+'] '+i.name);L.push('');
      L.push('**Problem:** '+i.detail);L.push('');
      L.push('**Evidence:**');L.push('');
      (i.evidence||[]).forEach(e=>{L.push('- '+e.label+': '+e.value+(e.cmd?' (`'+e.cmd+'`)':''));});
      if(!(i.evidence||[]).length)L.push('- _no automated evidence_');
      L.push('');
      L.push('**Impact:** '+(i.impact||'—'));L.push('');
      const f=i.recommended_fix;
      L.push('**Recommended fix:** '+((f&&f.label)?f.label:'manual guidance only')+' (risk: '+((f&&f.risk)||'n/a')+')');
      if(f&&f.commands&&f.commands.length){L.push('');L.push('```');f.commands.forEach(c=>L.push(c));L.push('```');}
      L.push('');
      L.push('**Verify:**');L.push('');
      (i.verify||[]).forEach(v=>{L.push('- '+v.label+': '+v.value+(v.cmd?' (`'+v.cmd+'`)':''));});
      if(!(i.verify||[]).length)L.push('- _observe manually_');
      L.push('');
    });
  });
  if(diagHistory.length){
    L.push('## Recent troubleshooting activity');L.push('');
    diagHistory.slice(0,8).forEach(h=>{
      const ts=new Date(h.ts||h.t||Date.now()).toISOString();
      if(h.type==='scan')L.push('- ['+ts+'] Health scan (score '+(h.score||0)+', '+(h.total||0)+' issues)');
      else if(h.type==='fix')L.push('- ['+ts+'] Fix '+(h.fix||'')+(h.resolved?' — verified':' — not verified'));
      else L.push('- ['+ts+'] Fix all ('+(h.fixed||0)+' fixed, '+(h.failed||0)+' failed)');
    });
    L.push('');
  }
  L.push('_Generated locally by Monitoring — no data leaves this host._');
  return L.join('\n');
}
async function copyDiagReport(){
  const md=diagReportMarkdown();
  let done=false;
  try{await navigator.clipboard.writeText(md);done=true;}
  catch(e){
    try{
      const ta=document.createElement('textarea');
      ta.value=md;ta.style.position='fixed';ta.style.opacity='0';
      document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();done=true;
    }catch(e2){}
  }
  if(done)toast('Diagnostic report copied to clipboard');
  else toast('Copy failed — use Export .md instead','err');
  logActivity('system','Copied diagnostic report');
}
function downloadDiagReport(){
  const md=diagReportMarkdown();
  const blob=new Blob([md],{type:'text/markdown;charset=utf-8'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='monitoring-diagnostic-'+new Date().toISOString().replace(/[:.]/g,'-')+'.md';
  a.click();
  URL.revokeObjectURL(a.href);
  toast('Diagnostic report downloaded','info');
  logActivity('system','Exported diagnostic report');
}


// ================================================================
// Activity audit trail (local)
// ================================================================

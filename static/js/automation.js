// ================================================================
// Automation tab: outbound notifications, scheduled actions,
// maintenance window, auto-remediation rules, run log and the
// persistent (server-side) alert history.
// ================================================================
async function loadAutomation(){
  loadNotifyConfig();
  loadWindow();
  loadSchedules();
  loadRules();
  loadRuns();
  loadServerAlerts();
}

// ---------------------------------------------------- notifications
async function loadNotifyConfig(){
  const badge=$('#notifyBadge');
  try{
    const d=await fetchJSON('/api/notify/config');
    if(!d)return;
    $('#notifyKind').value=d.kind||'webhook';
    $('#notifyUrl').value=d.url||'';
    $('#notifyToken').value=d.token_set?(d.token_hint||'••••'):'';
    $('#notifyEnabled').checked=!!d.enabled;
    if(badge)badge.innerHTML=d.enabled
      ?'<span class="pill ok">on · '+esc(d.kind||'?')+'</span>'
      :'<span class="pill neutral">off</span>';
  }catch(e){if(badge)badge.textContent='unavailable';}
}
async function saveNotifyConfig(){
  try{
    const body={
      kind:$('#notifyKind').value,
      url:$('#notifyUrl').value.trim(),
      enabled:$('#notifyEnabled').checked
    };
    const tok=$('#notifyToken').value.trim();
    // Keep the existing token when the masked hint is left untouched.
    if(tok&&!tok.includes('…'))body.token=tok;
    const j=await postJSON('/api/notify/config',body);
    if(j&&j.error){toast('Save failed: '+j.error,'err');return;}
    toast('Notification settings saved','ok');
    logActivity('system','Updated notification settings');
    loadNotifyConfig();
  }catch(e){toast('Save failed: '+(e&&e.message||'request failed'),'err');}
}
async function testNotify(){
  try{
    toast('Sending test notification…','info');
    const j=await postJSON('/api/notify/test',{});
    if(j&&j.error){toast('Test failed: '+j.error,'err');return;}
    if(j&&j.sent)toast('Test notification sent','ok');
    else toast('Notification not sent: '+((j&&j.detail)||'disabled or not configured'),'err');
  }catch(e){toast('Test failed: '+(e&&e.message||'request failed'),'err');}
}

// ------------------------------------------------- maintenance window
async function loadWindow(){
  const badge=$('#windowBadge');
  try{
    const d=await fetchJSON('/api/maintenance/window');
    if(!d)return;
    $('#windowEnabled').checked=!!d.enabled;
    $('#windowStart').value=d.start||'02:00';
    const sel=$('#windowActions');
    if(sel){
      const want=d.actions||[];
      [...sel.options].forEach(o=>{o.selected=want.includes(o.value);});
    }
    if(badge)badge.innerHTML=d.enabled
      ?'<span class="pill ok">on · '+esc(d.start||'')+'</span>'
      :'<span class="pill neutral">off</span>';
  }catch(e){if(badge)badge.textContent='unavailable';}
}
async function saveWindow(){
  try{
    const actions=[...($('#windowActions')?.selectedOptions||[])].map(o=>o.value);
    const j=await postJSON('/api/maintenance/window',{
      enabled:$('#windowEnabled').checked,
      start:$('#windowStart').value||'02:00',
      actions
    });
    if(j&&j.error){toast('Save failed: '+j.error,'err');return;}
    toast('Maintenance window saved','ok');
    logActivity('system','Updated maintenance window');
    loadWindow();
  }catch(e){toast('Save failed: '+(e&&e.message||'request failed'),'err');}
}

// ------------------------------------------------------- schedules
const SCHED_ACTIONS=[['update','Update lists'],['full-upgrade','Full upgrade'],
  ['upgrade','Upgrade'],['autoremove','Autoremove'],['clean','Clean cache'],
  ['fix-broken','Fix broken'],['clear-logs','Clear logs'],['snapshot','Snapshot']];
async function loadSchedules(){
  const tb=$('#schedRows'),badge=$('#schedBadge');
  try{
    const d=await fetchJSON('/api/schedule?all=1');
    const rows=(d&&d.items)||[];
    if(badge)badge.textContent=rows.filter(r=>r.status==='pending').length+' pending';
    if(!rows.length){tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">Nothing scheduled</td></tr>';return;}
    tb.innerHTML=rows.map(s=>'<tr>'
      +'<td class="mono dim" style="font-size:.72rem;white-space:nowrap">'+esc(new Date(s.run_at*1000).toLocaleString())+'</td>'
      +'<td class="mono strong" style="font-size:.72rem">'+esc(s.action)+'</td>'
      +'<td><span class="pill '+(s.status==='done'?'ok':s.status==='failed'?'fail':'info')+'">'+esc(s.status)+'</span></td>'
      +'<td style="text-align:right">'+(s.status==='pending'
        ?'<button class="icon-btn danger sched-del" data-id="'+s.id+'" title="Cancel" aria-label="Cancel scheduled action">'+icon('x',12)+'</button>':'')+'</td>'
      +'</tr>').join('');
    $$('#schedRows .sched-del').forEach(b=>b.addEventListener('click',async()=>{
      const ok=await confirmDlg('Cancel scheduled action','Remove this scheduled action?',true);
      if(!ok)return;
      await apiFetch('/api/schedule/'+b.dataset.id,{method:'DELETE'}).catch(()=>{});
      toast('Schedule cancelled','ok');
      loadSchedules();
    }));
  }catch(e){
    tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">Schedule unavailable</td></tr>';
    if(badge)badge.textContent='unavailable';
  }
}
async function addSchedule(){
  const opts=SCHED_ACTIONS.map(a=>'<option value="'+a[0]+'">'+a[1]+'</option>').join('');
  const when=new Date(Date.now()+3600000);
  const pad=n=>String(n).padStart(2,'0');
  const def=when.getFullYear()+'-'+pad(when.getMonth()+1)+'-'+pad(when.getDate())+' '+pad(when.getHours())+':'+pad(when.getMinutes());
  const r=await openModal({
    title:'Schedule an action',
    text:'Runs a whitelisted maintenance action at the given local time and records the outcome.',
    html:'<label for="mf-action">Action</label><select id="mf-action" data-field="action">'+opts+'</select>',
    fields:[{key:'run_at',label:'Run at (YYYY-MM-DD HH:MM)',value:def}],
    confirmText:'Schedule',icon:'clock'
  });
  if(!r)return;
  const action=(r.values.action||'clean');
  const run_at=(r.values.run_at||'').trim();
  const j=await postJSON('/api/schedule',{action,run_at});
  if(j&&j.error){toast('Schedule failed: '+j.error,'err');return;}
  toast('Action scheduled','ok');
  logActivity('system','Scheduled '+action+' at '+run_at);
  loadSchedules();
}

// ----------------------------------------------------------- rules
async function loadRules(){
  const tb=$('#rulesRows'),badge=$('#rulesBadge');
  try{
    const d=await fetchJSON('/api/rules');
    const rows=(d&&d.rules)||[];
    if(badge)badge.textContent=rows.filter(r=>r.enabled).length+' active';
    if(!rows.length){tb.innerHTML='<tr><td colspan="5" class="dim" style="text-align:center;padding:18px">No rules — add a runaway-process or service-watch rule</td></tr>';return;}
    tb.innerHTML=rows.map(r=>{
      const cfg=r.config||{};
      const cfgTxt=r.type==='runaway'
        ?('cpu≥'+(cfg.cpu_pct??90)+'%'+(cfg.mem_pct?('/mem≥'+cfg.mem_pct+'%'):'')+' for '+Math.round((cfg.duration_s||300)/60)+'m · '+(cfg.action||'alert'))
        :('watch '+esc(cfg.unit||'?')+' · max '+(cfg.max_per_hour||3)+'/h');
      return '<tr>'
        +'<td><span class="pill '+(r.type==='runaway'?'warn':'info')+'">'+esc(r.type)+'</span></td>'
        +'<td class="mono strong" style="font-size:.72rem">'+esc(r.name)+'</td>'
        +'<td class="dim" style="font-size:.7rem">'+cfgTxt+'</td>'
        +'<td><button class="icon-btn rule-toggle" data-id="'+r.id+'" data-on="'+(r.enabled?1:0)+'" title="Toggle rule" aria-label="Toggle rule">'
          +'<span class="pill '+(r.enabled?'ok':'neutral')+'">'+(r.enabled?'on':'off')+'</span></button></td>'
        +'<td style="text-align:right"><button class="icon-btn danger rule-del" data-id="'+r.id+'" title="Delete rule" aria-label="Delete rule">'+icon('x',12)+'</button></td>'
        +'</tr>';
    }).join('');
    $$('#rulesRows .rule-del').forEach(b=>b.addEventListener('click',async()=>{
      const ok=await confirmDlg('Delete rule','Delete this automation rule?',true);
      if(!ok)return;
      await apiFetch('/api/rules/'+b.dataset.id,{method:'DELETE'}).catch(()=>{});
      toast('Rule deleted','ok');
      loadRules();
    }));
    $$('#rulesRows .rule-toggle').forEach(b=>b.addEventListener('click',async()=>{
      await postJSON('/api/rules/'+b.dataset.id,{enabled:b.dataset.on!=='1'}).catch(()=>{});
      loadRules();
    }));
  }catch(e){
    tb.innerHTML='<tr><td colspan="5" class="dim" style="text-align:center;padding:18px">Rules unavailable</td></tr>';
    if(badge)badge.textContent='unavailable';
  }
}
async function addRule(){
  const r=await openModal({
    title:'Add automation rule',
    text:'Runaway rules watch processes; service-watch rules restart failed units (max N per hour).',
    html:'<label for="mf-type">Type</label><select id="mf-type" data-field="type">'
      +'<option value="runaway">Runaway process (alert)</option>'
      +'<option value="renice">Runaway process (auto-renice)</option>'
      +'<option value="service_watch">Service auto-recovery</option></select>',
    fields:[
      {key:'name',label:'Name',value:'',placeholder:'e.g. web worker hog'},
      {key:'v1',label:'CPU % (runaway) or Unit name (service)',value:'',placeholder:'90  or  nginx.service'},
      {key:'v2',label:'Minutes above threshold (runaway) or Max restarts/hour (service)',value:'5'}
    ],
    confirmText:'Add rule',icon:'zap'
  });
  if(!r)return;
  const type=r.values.type||'runaway';
  let body;
  if(type==='service_watch'){
    body={type:'service_watch',name:r.values.name,config:{unit:(r.values.v1||'').trim(),max_per_hour:+(r.values.v2||3)}};
  }else{
    body={type:'runaway',name:r.values.name,config:{
      cpu_pct:+(r.values.v1||90),
      duration_s:Math.max(30,Math.round((+(r.values.v2||5))*60)),
      action:type==='renice'?'renice':'alert',
      nice:10
    }};
  }
  const j=await postJSON('/api/rules',body);
  if(j&&j.error){toast('Rule failed: '+j.error,'err');return;}
  toast('Rule added','ok');
  logActivity('system','Added automation rule: '+(r.values.name||type));
  loadRules();
}

// -------------------------------------------------------- run log
async function loadRuns(){
  const tb=$('#runsRows'),badge=$('#runsBadge');
  try{
    const d=await fetchJSON('/api/runs?limit=40');
    const rows=(d&&d.runs)||[];
    if(badge)badge.textContent=rows.length+' runs';
    if(!rows.length){tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">No automation runs yet</td></tr>';return;}
    tb.innerHTML=rows.map(r=>'<tr>'
      +'<td class="mono dim" style="font-size:.7rem;white-space:nowrap">'+esc(new Date(r.ts*1000).toLocaleString())+'</td>'
      +'<td class="dim" style="font-size:.72rem">'+esc(r.kind)+'</td>'
      +'<td class="mono strong" style="font-size:.72rem">'+esc(r.action)+'</td>'
      +'<td><span class="pill '+(r.outcome==='success'||r.outcome==='restarted'?'ok':r.outcome.includes('fail')?'fail':'info')+'">'+esc(r.outcome)+'</span></td>'
      +'</tr>').join('');
  }catch(e){
    tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">Run log unavailable</td></tr>';
    if(badge)badge.textContent='unavailable';
  }
}

// --------------------------------------------- server alert history
async function loadServerAlerts(){
  const tb=$('#alertHistRows'),badge=$('#alertHistBadge');
  try{
    const d=await fetchJSON('/api/alerts/history?limit=40');
    const rows=(d&&d.alerts)||[];
    if(badge)badge.textContent=rows.length+' alerts';
    if(!rows.length){tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">No server-recorded alerts yet — threshold breaches are recorded here and survive restarts</td></tr>';return;}
    tb.innerHTML=rows.map(a=>'<tr>'
      +'<td class="mono dim" style="font-size:.7rem;white-space:nowrap">'+esc(new Date(a.ts*1000).toLocaleString())+'</td>'
      +'<td><span class="pill '+(a.level==='critical'?'fail':'warn')+'">'+esc(a.level)+'</span></td>'
      +'<td class="strong" style="font-size:.72rem">'+esc(a.name)+(a.resolved_ts?' <span class="pill ok">resolved</span>':'')+'</td>'
      +'<td class="mono dim" style="font-size:.72rem">'+(a.value!=null?esc(String(a.value)):'—')+'</td>'
      +'</tr>').join('');
  }catch(e){
    tb.innerHTML='<tr><td colspan="4" class="dim" style="text-align:center;padding:18px">Alert history unavailable</td></tr>';
    if(badge)badge.textContent='unavailable';
  }
}

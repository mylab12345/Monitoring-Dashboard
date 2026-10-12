// ================================================================
// Security tab: host posture (firewall/MAC/secure boot), kernel
// hardening scorecard, logins/SSH, exposure, package integrity,
// security updates and the persistent server audit trail.
// ================================================================
async function loadSecurity(){
  const body=$('#secFirewallBody');
  if(body)body.innerHTML='<div class="skel" style="height:100px"></div>';
  try{
    const d=await fetchJSON('/api/security/posture');
    if(!d)throw new Error('the API did not respond');
    renderFirewall(d);
    renderHardening(d.hardening||{});
    renderLogins(d.logins||{});
    renderExposure(d.exposure||{});
    renderIntegrity(d.integrity||{});
    renderSecUpdates(d.security_updates||{});
    loadSecurityAudit();
  }catch(e){
    ['secFirewallBody','secHardeningBody','secLoginsBody','secExposureBody',
     'secIntegrityBody','secUpdatesBody'].forEach(id=>{
      const el=$('#'+id);
      if(el)el.innerHTML='<div class="empty-state"><strong>Unavailable</strong>'
        +'<span class="dim">'+esc(e&&e.message||'request failed')+'</span></div>';
    });
    loadSecurityAudit();
  }
}

function renderFirewall(d){
  const el=$('#secFirewallBody'),badge=$('#secFirewallBadge');
  if(!el)return;
  const fw=d.firewall||{},mac=d.mac||{};
  const sb=d.secure_boot;
  const fwOk=(fw.active_services||[]).length>0;
  if(badge)badge.innerHTML='<span class="pill '+(fwOk?'ok':'warn')+'">'+(fwOk?'protected':'no firewall')+'</span>';
  el.innerHTML='<div class="kv"><span>Firewall services</span><strong>'
      +((fw.active_services||[]).length?esc(fw.active_services.join(', ')):'none active')+'</strong></div>'
    +'<div class="kv"><span>MAC framework</span><strong>'
      +'<span class="pill '+(mac.mode==='enforcing'?'ok':mac.framework==='none'?'neutral':'info')+'">'
      +esc((mac.framework||'none')+' · '+(mac.mode||'?'))+'</span></strong></div>'
    +'<div class="kv"><span>Secure Boot</span><strong>'
      +(sb===true?'<span class="pill ok">enabled</span>':sb===false?'<span class="pill neutral">disabled</span>':'<span class="pill neutral">unknown</span>')
      +'</strong></div>'
    +(fw.detail?'<div class="dim" style="font-size:.72rem;margin-top:6px">'+esc(fw.detail)+'</div>':'');
}

function renderHardening(h){
  const el=$('#secHardeningBody'),badge=$('#secHardeningBadge');
  if(!el)return;
  const items=h.items||[];
  if(badge)badge.innerHTML='<span class="pill grade '+(h.grade==='A'?'ok':'warn')+'">'+esc(String(h.grade||'?'))+' · '+(h.score||0)+'/'+(h.max_score||0)+'</span>';
  el.innerHTML=items.map(it=>'<div class="kv"><span>'+esc(it.label)+'</span><strong>'
    +'<span class="pill '+(it.ok?'ok':it.known?'warn':'neutral')+'">'
    +(it.known?esc(String(it.value)):'n/a')+(it.known?'':' · ')+((it.ok||!it.known)?'':' ≥'+it.want_at_least)
    +'</span></strong></div>').join('');
}

async function applyHardening(profile){
  try{
    const ok=await confirmDlg('Apply kernel hardening',
      'Apply the "'+profile+'" kernel hardening profile? Current values are saved and can be reverted with one click.',false);
    if(!ok)return;
    logActivity('security','Apply hardening profile: '+profile);
    const j=await postJSON('/api/security/hardening',{action:'apply',profile});
    if(j&&j.error){toast('Hardening failed: '+j.error,'err');return;}
    toast('Hardening profile applied: '+profile,'ok');
    renderHardening(j.state||{});
  }catch(e){toast('Hardening failed: '+(e&&e.message||'request failed'),'err');}
}
async function revertHardening(){
  try{
    const ok=await confirmDlg('Revert hardening','Restore the original kernel hardening values?',false);
    if(!ok)return;
    logActivity('security','Revert hardening profile');
    const j=await postJSON('/api/security/hardening',{action:'revert'});
    if(j&&j.error){toast('Revert failed: '+j.error,'err');return;}
    toast('Kernel hardening reverted','ok');
    renderHardening(j.state||{});
  }catch(e){toast('Revert failed: '+(e&&e.message||'request failed'),'err');}
}

function renderLogins(l){
  const el=$('#secLoginsBody'),badge=$('#secLoginsBadge');
  if(!el)return;
  const fa=l.failed_auth||{};
  const burst=l.brute_force_suspected;
  if(badge)badge.innerHTML='<span class="pill '+(burst?'fail':(fa.count_24h?'warn':'ok'))+'">'
    +(fa.count_24h==null?'unknown':(fa.count_24h+' failed/24h'))+'</span>';
  let html='<div class="kv"><span>Active sessions</span><strong>'+((l.sessions||[]).length)+'</strong></div>';
  for(const s of (l.sessions||[]).slice(0,4)){
    html+='<div class="kv"><span>'+esc(s.user)+'</span><strong>'+esc(s.terminal)+' · '+esc(String(s.since||''))+'</strong></div>';
  }
  if(burst)html+='<div class="perm-banner" role="note" style="margin-top:8px">Possible SSH brute-force: '+fa.count_24h+' failed auth attempts in 24h.</div>';
  for(const f of (fa.samples||[]).slice(0,2)){
    html+='<div class="dim" style="font-size:.68rem;margin-top:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(f)+'</div>';
  }
  el.innerHTML=html;
}

function renderExposure(x){
  const el=$('#secExposureBody'),badge=$('#secExposureBadge');
  if(!el)return;
  const open=x.open_to_network||[];
  if(badge)badge.innerHTML='<span class="pill '+(open.length?'info':'ok')+'">'+open.length+' open</span>';
  if(!x.known){el.innerHTML='<div class="dim">ss unavailable</div>';return;}
  if(!open.length){el.innerHTML='<div class="kv"><span>Listeners</span><strong>loopback only</strong></div>';return;}
  el.innerHTML=open.slice(0,8).map(p=>'<div class="kv"><span>'+esc(p.addr)+':'+esc(p.port)+'</span><strong>'+esc(p.process||'—')+'</strong></div>').join('')
    +(open.length>8?'<div class="dim" style="font-size:.72rem">+ '+(open.length-8)+' more</div>':'');
}

function renderIntegrity(i){
  const el=$('#secIntegrityBody'),badge=$('#secIntegrityBadge');
  if(!el)return;
  const mod=i.modified_count||0;
  if(badge)badge.innerHTML=i.checked
    ?'<span class="pill '+(mod?'fail':'ok')+'">'+(mod?mod+' modified':'clean')+'</span>'
    :'<span class="pill neutral">n/a</span>';
  if(!i.checked){
    el.innerHTML='<div class="dim" style="font-size:.78rem">'+esc(i.detail||'No integrity backend available')+'</div>';
    return;
  }
  el.innerHTML='<div class="kv"><span>Backend</span><strong>'+esc(i.backend||'—')+'</strong></div>'
    +'<div class="kv"><span>Modified files</span><strong>'+(mod?('<span class="pill fail">'+mod+'</span>'):'<span class="pill ok">0</span>')+'</strong></div>'
    +(i.modified||[]).slice(0,4).map(m=>'<div class="dim" style="font-size:.68rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(m)+'</div>').join('');
}

function renderSecUpdates(u){
  const el=$('#secUpdatesBody'),badge=$('#secUpdatesBadge');
  if(!el)return;
  const n=u.security_count;
  if(badge)badge.innerHTML=u.security_known
    ?'<span class="pill '+(n?'warn':'ok')+'">'+(n||0)+' security</span>'
    :'<span class="pill neutral">unknown</span>';
  el.innerHTML='<div class="kv"><span>Package manager</span><strong>'+esc(u.manager||'—')+'</strong></div>'
    +'<div class="kv"><span>Security updates</span><strong>'+(u.security_known?String(n):'not tracked by this manager')+'</strong></div>'
    +'<div class="kv"><span>Total pending</span><strong>'+String(u.total_count||0)+'</strong></div>'
    +(u.security_known&&n?'<div class="toolbar-right" style="margin-top:8px;justify-content:flex-start"><button class="btn btn-sm" onclick="showTab(\'maintain\')">Open System &amp; Kernel</button></div>':'');
}

async function loadSecurityAudit(){
  const tb=$('#auditRows'),badge=$('#secAuditBadge');
  if(!tb)return;
  try{
    const d=await fetchJSON('/api/audit?limit=50');
    const rows=(d&&d.entries)||[];
    if(badge)badge.textContent=rows.length+' entries';
    if(!rows.length){tb.innerHTML='<tr><td colspan="3" class="dim" style="text-align:center;padding:18px">No audit entries yet</td></tr>';return;}
    tb.innerHTML=rows.map(r=>'<tr>'
      +'<td class="mono dim" style="font-size:.7rem;white-space:nowrap">'+esc(new Date(r.ts*1000).toLocaleString())+'</td>'
      +'<td class="mono strong" style="font-size:.72rem">'+esc(r.event)+'</td>'
      +'<td class="dim" style="font-size:.7rem;max-width:520px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+esc(r.detail||'')+'</td>'
      +'</tr>').join('');
  }catch(e){
    tb.innerHTML='<tr><td colspan="3" class="dim" style="text-align:center;padding:18px">Audit trail unavailable</td></tr>';
    if(badge)badge.textContent='unavailable';
  }
}

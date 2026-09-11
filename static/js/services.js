let svcData=[],svcAvailable=true,svcFilter='all',svcPage=0;
const SVC_PAGE_SIZE=12;
$$('#svcFilter .seg-btn').forEach(b=>b.addEventListener('click',()=>{
  $$('#svcFilter .seg-btn').forEach(x=>{
    const on=x===b;
    x.classList.toggle('active',on);
    x.setAttribute('aria-pressed',on?'true':'false');
  });
  svcFilter=b.dataset.f;svcPage=0;renderServices();
}));
async function loadServices(){
  try{
    const d=await fetchJSON('/api/services');
    if(!d)throw new Error('the API did not respond');
    svcAvailable=!!d.available;
    svcData=d.services||[];
    $('#svcTotal').textContent=svcAvailable?svcData.length+' units':'';
    stampUpdated('svcUpdated');
    renderServices();
    // Permission check (non-blocking): warn up front when service control
    // would be denied instead of erroring after the click.
    updatePermBanner('svcPermBanner','monitoring-systemctl','Service start/stop/restart');
  }catch(e){
    $('#svcTotal').textContent='unavailable';
    const tb=$('#svcRows');
    if(tb)tb.innerHTML='<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">'+icon('gear',22)+'</div><strong>Services unavailable</strong><span class="dim">'+esc(e.message||'request failed')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadServices()">'+icon('refresh',13)+' Retry</button></div></td></tr>';
    const pg=$('#svcPager');if(pg)pg.hidden=true;
  }
}
let svcSort={key:'unit',dir:1};
function filteredServices(){
  const q=($('#svcSearch').value||'').toLowerCase();
  let rows=svcData.filter(s=>!q||s.unit.toLowerCase().includes(q)||(s.description||'').toLowerCase().includes(q));
  if(svcFilter==='active')rows=rows.filter(s=>s.active==='active');
  else if(svcFilter==='failed')rows=rows.filter(s=>s.active==='failed');
  else if(svcFilter==='other')rows=rows.filter(s=>s.active!=='active'&&s.active!=='failed');
  const{key,dir}=svcSort;
  rows.sort((a,b)=>{
    const x=String(a[key]||'').toLowerCase(),y=String(b[key]||'').toLowerCase();
    return x<y?-dir:x>y?dir:0;
  });
  return rows;
}
// Keyboard-accessible column sorting for the services table.
$$('#svcTable th.sortable').forEach(th=>{
  th.tabIndex=0;
  th.setAttribute('role','columnheader');
  if(!th.title)th.title='Sort by '+th.textContent.trim();
  const sortBy=()=>{
    const k=th.dataset.key;
    if(svcSort.key===k)svcSort.dir*=-1;
    else svcSort={key:k,dir:1};
    svcPage=0;renderServices();
  };
  th.addEventListener('click',sortBy);
  th.addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();sortBy();}});
});
function svcPageGo(dir){svcPage=Math.max(0,svcPage+dir);renderServices();}
const svcIconBtn=(unit,act,ic,t,danger)=>'<button class="icon-btn svc-action'+(danger?' danger':'')+'" data-unit="'+esc(unit)+'" data-action="'+esc(act)+'" title="'+esc(t)+'" aria-label="'+esc(t)+' '+esc(unit)+'">'+icon(ic,12)+'</button>';
function renderServices(){
  const tb=$('#svcRows');
  if(!tb)return;
  if(!svcAvailable){tb.innerHTML='<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">'+icon('gear',22)+'</div><strong>systemctl not available</strong><span class="dim">This system does not use systemd.</span></div></td></tr>';$('#svcPager').hidden=true;return;}
  $$('#svcTable th.sortable').forEach(th=>{
    const k=th.dataset.key;
    const arrow=th.querySelector('.arrow');
    if(arrow)arrow.textContent=svcSort.key===k?(svcSort.dir>0?'▲':'▼'):'';
    th.setAttribute('aria-sort',svcSort.key===k?(svcSort.dir>0?'ascending':'descending'):'none');
  });
  const rows=filteredServices();
  const pages=Math.max(1,Math.ceil(rows.length/SVC_PAGE_SIZE));
  svcPage=Math.min(svcPage,pages-1);
  const slice=rows.slice(svcPage*SVC_PAGE_SIZE,(svcPage+1)*SVC_PAGE_SIZE);
  $('#svcPager').hidden=rows.length<=SVC_PAGE_SIZE;
  $('#svcRange').textContent=rows.length?(svcPage*SVC_PAGE_SIZE+1)+'–'+Math.min(rows.length,(svcPage+1)*SVC_PAGE_SIZE)+' of '+rows.length:'0';
  $('#svcPrev').disabled=svcPage===0;
  $('#svcNext').disabled=svcPage>=pages-1;
  if(!rows.length){tb.innerHTML='<tr><td colspan="5" class="dim" style="text-align:center;padding:22px">No services match</td></tr>';return;}
  tb.innerHTML=slice.map(s=>{
    const pill=s.active==='active'?(s.sub==='running'?'ok':'info'):s.active==='failed'?'fail':'neutral';
    return '<tr>'
      +'<td class="mono strong">'+esc(s.unit)+'</td>'
      +'<td class="dim" style="font-size:.72rem;max-width:340px;overflow:hidden;text-overflow:ellipsis">'+esc(s.description)+'</td>'
      +'<td><span class="pill '+pill+'">'+esc(s.active)+'</span></td>'
      +'<td class="mono dim" style="font-size:.7rem">'+esc(s.sub)+'</td>'
      +'<td><span class="row-actions">'
        +svcIconBtn(s.unit,'start','play','Start')
        +svcIconBtn(s.unit,'stop','stop','Stop')
        +svcIconBtn(s.unit,'restart','restart','Restart')
        +svcIconBtn(s.unit,'enable','up','Enable')
        +svcIconBtn(s.unit,'disable','x','Disable',true)
      +'</span></td></tr>';
  }).join('');
  // Bind via data attributes to avoid inline JS injection via unit names
  $$('#svcRows .svc-action').forEach(b=>{
    b.addEventListener('click',()=>svcAction(b.dataset.unit,b.dataset.action));
  });
}
// Descriptions shown in the confirmation dialog for disruptive actions.
const SVC_CONFIRM={
  stop:{text:u=>'Stop “'+u+'”? Anything depending on it will lose the service until it is started again.',danger:true},
  restart:{text:u=>'Restart “'+u+'”? Connections and in-flight work handled by this unit may be interrupted.',danger:false},
  disable:{text:u=>'Disable “'+u+'”? It will no longer start automatically at boot (it is not stopped now).',danger:false}
};
function exportServicesCSV(){
  const rows=filteredServices().map(s=>[s.unit,s.description,s.active,s.sub,s.load]);
  if(!rows.length){toast('Nothing to export','info');return;}
  exportCSV('monitoring-services-'+new Date().toISOString().slice(0,19).replace(/[:T]/g,'-')+'.csv',
    ['Unit','Description','Active','Sub-state','Load'],rows);
}
async function svcAction(name,action){
  const key='svc:'+name+':'+action;
  if(!beginAction(key))return; // this exact action is already running
  try{
    const c=SVC_CONFIRM[action];
    if(c){
      if(!await confirmDlg(action.charAt(0).toUpperCase()+action.slice(1)+' service',c.text(name),c.danger))return;
    }
    logActivity('service',action+' service '+name);
    const j=await postJSON('/api/service/action',{name,action});
    if(j.error){toast(action+' failed: '+j.error,'err');logActivity('service',action+' '+name+' failed',false);}
    else if(j.verified===false){
      // Post-action verification came back with an unexpected state — the
      // command succeeded but the unit is not where we expected it yet.
      toast((j.result||'OK')+' — unit reports “'+(j.state||'unknown')+'”','info');
      logActivity('service',action+' '+name+': state '+(j.state||'unknown'));
      loadServices();
    }
    else{toast(j.result||'OK');loadServices();}
  }finally{endAction(key);}
}

// ================================================================
// VMs
// ================================================================

let svcData=[],svcAvailable=true,svcFilter='all',svcPage=0;
const SVC_PAGE_SIZE=12;
$$('#svcFilter .seg-btn').forEach(b=>b.addEventListener('click',()=>{
  $$('#svcFilter .seg-btn').forEach(x=>x.classList.toggle('active',x===b));
  svcFilter=b.dataset.f;svcPage=0;renderServices();
}));
async function loadServices(){
  try{
    const d=await fetchJSON('/api/services');
    if(!d)throw new Error('the API did not respond');
    svcAvailable=!!d.available;
    svcData=d.services||[];
    $('#svcTotal').textContent=svcAvailable?svcData.length+' units':'';
    renderServices();
  }catch(e){
    $('#svcTotal').textContent='unavailable';
    const tb=$('#svcRows');
    if(tb)tb.innerHTML='<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">'+icon('gear',22)+'</div><strong>Services unavailable</strong><span class="dim">'+esc(e.message||'request failed')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadServices()">'+icon('refresh',13)+' Retry</button></div></td></tr>';
    const pg=$('#svcPager');if(pg)pg.hidden=true;
  }
}
function filteredServices(){
  const q=($('#svcSearch').value||'').toLowerCase();
  let rows=svcData.filter(s=>!q||s.unit.toLowerCase().includes(q)||(s.description||'').toLowerCase().includes(q));
  if(svcFilter==='active')rows=rows.filter(s=>s.active==='active');
  else if(svcFilter==='failed')rows=rows.filter(s=>s.active==='failed');
  else if(svcFilter==='other')rows=rows.filter(s=>s.active!=='active'&&s.active!=='failed');
  return rows;
}
function svcPageGo(dir){svcPage=Math.max(0,svcPage+dir);renderServices();}
const svcIconBtn=(unit,act,ic,t,danger)=>'<button class="icon-btn svc-action'+(danger?' danger':'')+'" data-unit="'+esc(unit)+'" data-action="'+esc(act)+'" title="'+esc(t)+'" aria-label="'+esc(t)+' '+esc(unit)+'">'+icon(ic,12)+'</button>';
function renderServices(){
  const tb=$('#svcRows');
  if(!tb)return;
  if(!svcAvailable){tb.innerHTML='<tr><td colspan="5"><div class="empty-state"><div class="empty-icon">'+icon('gear',22)+'</div><strong>systemctl not available</strong><span class="dim">This system does not use systemd.</span></div></td></tr>';$('#svcPager').hidden=true;return;}
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
async function svcAction(name,action){
  if(action==='stop'||action==='disable'){
    if(!await confirmDlg(action+' service','Are you sure you want to '+action+' “'+name+'”?',action==='stop'))return;
  }
  logActivity('service',action+' service '+name);
  const j=await postJSON('/api/service/action',{name,action});
  if(j.error){toast(action+' failed: '+j.error,'err');logActivity('service',action+' '+name+' failed',false);}
  else{toast(j.result||'OK');loadServices();}
}

// ================================================================
// VMs
// ================================================================

// ================================================================
// OS health cards (Overview tab): PSI pressure, memory breakdown,
// storage health (inodes / SMART) and the persistent long-term chart.
// ================================================================
async function loadOsHealth(){
  const body=$('#osHealthBody'),badge=$('#psiBadge');
  if(!body)return;
  try{
    const d=await fetchJSON('/api/memory');
    if(!d)throw new Error('the API did not respond');
    const psi=d.psi||{};
    const br=d.breakdown||{};
    const oom=d.oom||{};
    const fmtB=v=>v==null?'—':v>=1073741824?(v/1073741824).toFixed(1)+' GB':(v/1048576).toFixed(0)+' MB';
    const psiRow=(k,label)=>{
      const p=psi[k];
      if(!p||!p.some)return '<div class="kv"><span>'+esc(label)+'</span><strong>n/a (kernel without PSI)</strong></div>';
      const some=p.some.avg10||0,full=p.full?p.full.avg10||0:0;
      const hot=some>=20||full>=10;
      return '<div class="kv"><span>'+esc(label)+'</span><strong>'
        +'<span class="pill '+(hot?'warn':'ok')+'">some '+some.toFixed(1)+'%</span> '
        +'<span class="pill '+(full>=10?'warn':'neutral')+'">full '+full.toFixed(1)+'%</span></strong></div>';
    };
    let html=psiRow('cpu','CPU pressure (avg10)')
      +psiRow('memory','Memory pressure (avg10)')
      +psiRow('io','I/O pressure (avg10)');
    if(br.memtotal!=null){
      const cache=(br.cached||0)+(br.buffers||0);
      html+='<div class="kv"><span>Page cache + buffers</span><strong>'+fmtB(cache)+'</strong></div>'
        +'<div class="kv"><span>Slab (reclaimable)</span><strong>'+fmtB(br.slab||0)+' ('+fmtB(br.sreclaimable||0)+')</strong></div>'
        +'<div class="kv"><span>Commit</span><strong>'+fmtB(br.committed_as||0)+' / '+fmtB(br.commitlimit||0)+'</strong></div>';
    }
    if(oom.known){
      html+='<div class="kv"><span>OOM-killer (24h)</span><strong>'
        +'<span class="pill '+(oom.count?'fail':'ok')+'">'+(oom.count||0)+' events</span></strong></div>';
    }
    body.innerHTML=html;
    if(badge)badge.textContent=psi.cpu?'PSI live':'no PSI';
  }catch(e){
    body.innerHTML='<div class="empty-state"><strong>Pressure data unavailable</strong>'
      +'<span class="dim">'+esc(e&&e.message||'request failed')+'</span></div>';
    if(badge)badge.textContent='unavailable';
  }
}

async function loadDiskHealth(){
  const body=$('#diskHealthBody'),badge=$('#diskHealthBadge');
  if(!body)return;
  try{
    const d=await fetchJSON('/api/diskhealth');
    if(!d)throw new Error('the API did not respond');
    const inodes=d.inodes||[],devs=d.devices||[];
    let html='';
    const worst=inodes.slice().sort((a,b)=>(b.percent||0)-(a.percent||0))[0];
    if(worst){
      const hot=worst.percent>=85;
      html+='<div class="kv"><span>Inode usage (worst mount)</span><strong>'
        +'<span class="pill '+(hot?'warn':'ok')+'">'+esc(worst.mount)+' '+worst.percent+'%</span></strong></div>';
    }
    for(const dev of devs.slice(0,4)){
      html+='<div class="kv"><span>/dev/'+esc(dev.device)+'</span><strong>'
        +(dev.ssd?'<span class="pill info">SSD</span>':'<span class="pill neutral">HDD</span>')
        +(dev.discard_granularity&&dev.discard_granularity!=='0B'&&dev.discard_granularity!=='0'?' <span class="pill ok">TRIM</span>':' <span class="pill warn">no TRIM</span>')
        +'</strong></div>';
    }
    for(const dev of devs.slice(0,3)){
      html+='<div class="kv"><span>SMART /dev/'+esc(dev.device)+'</span><strong>'
        +'<button class="btn btn-sm smart-btn" data-disk="'+esc(dev.device)+'">Check</button>'
        +'<span class="smart-result" id="smart-'+esc(dev.device)+'"></span></strong></div>';
    }
    if(!html)html='<div class="empty-state"><strong>No storage data</strong></div>';
    body.innerHTML=html;
    if(badge)badge.textContent=devs.length?devs.length+' disks':'--';
    $$('#diskHealthBody .smart-btn').forEach(b=>b.addEventListener('click',()=>{
      const out=$('#smart-'+b.dataset.disk);
      if(out)out.textContent=' checking…';
      fetchJSON('/api/diskhealth?disk='+encodeURIComponent(b.dataset.disk)).then(d=>{
        if(!d||!d.smart)return;
        const s=d.smart;
        if(out)out.innerHTML=s.healthy===true?'<span class="pill ok">PASSED</span>'
          :s.healthy===false?'<span class="pill fail">FAILED</span>'
          :'<span class="pill neutral">'+esc((s.detail||'unknown').slice(0,60))+'</span>';
      }).catch(()=>{if(out)out.innerHTML='<span class="pill fail">error</span>';});
    }));
  }catch(e){
    body.innerHTML='<div class="empty-state"><strong>Storage health unavailable</strong>'
      +'<span class="dim">'+esc(e&&e.message||'request failed')+'</span></div>';
    if(badge)badge.textContent='unavailable';
  }
}

let longRangeHours=24,longData=null;
function setLongRange(h,btn){
  longRangeHours=h;
  $$('#longRangeSeg .seg-btn').forEach(b=>{
    const on=b===btn;
    b.classList.toggle('active',on);
    b.setAttribute('aria-pressed',on?'true':'false');
  });
  loadLongHistory();
}
async function loadLongHistory(){
  const badge=$('#longHistBadge'),note=$('#longHistNote'),canvas=$('#longChart');
  if(!canvas)return;
  try{
    const d=await fetchJSON('/api/history/long?hours='+longRangeHours);
    if(!d)throw new Error('the API did not respond');
    longData=d.samples||[];
    if(badge)badge.textContent=d.available===false?'no store':(longData.length+' pts');
    if(note)note.textContent=d.available===false
      ? 'Persistent history is unavailable (SQLite store not writable).'
      : 'Minute averages · '+longRangeHours+'h — survives restarts';
    drawLongChart();
  }catch(e){
    if(badge)badge.textContent='unavailable';
    if(note)note.textContent=String((e&&e.message)||'request failed');
  }
}
function drawLongChart(){
  const canvas=$('#longChart');
  if(!canvas||!longData)return;
  const dpr=window.devicePixelRatio||1;
  const w=canvas.clientWidth||300,h=120;
  canvas.width=w*dpr;canvas.height=h*dpr;
  const ctx=canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const data=longData.slice();
  if(data.length<2){
    ctx.fillStyle='rgba(148,163,184,.8)';
    ctx.font='12px sans-serif';
    ctx.fillText(data.length?'Collecting samples…':'No history yet — data appears within a minute',10,h/2);
    return;
  }
  const padL=28,padR=6,padT=8,padB=16;
  const plotW=w-padL-padR,plotH=h-padT-padB;
  // grid + axis
  ctx.strokeStyle='rgba(148,163,184,.18)';
  ctx.fillStyle='rgba(148,163,184,.7)';
  ctx.font='9px sans-serif';
  for(const v of [0,50,100]){
    const y=padT+plotH*(1-v/100);
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
    ctx.fillText(v+'%',2,y+3);
  }
  const t0=data[0].ts,t1=data[data.length-1].ts||t0+1;
  const xOf=t=>padL+plotW*((t-t0)/Math.max(1,(t1-t0)));
  const drawSeries=(key,color)=>{
    ctx.strokeStyle=color;ctx.lineWidth=1.6;
    ctx.beginPath();
    let started=false;
    for(const s of data){
      const v=s[key];
      if(v==null)continue;
      const x=xOf(s.ts),y=padT+plotH*(1-Math.max(0,Math.min(100,v))/100);
      if(!started){ctx.moveTo(x,y);started=true;}else ctx.lineTo(x,y);
    }
    ctx.stroke();
  };
  drawSeries('cpu','#22d3ee');
  drawSeries('ram','#a78bfa');
  ctx.fillStyle='rgba(34,211,238,.9)';ctx.fillText('CPU',padL+4,padT+10);
  ctx.fillStyle='rgba(167,139,250,.9)';ctx.fillText('RAM',padL+34,padT+10);
}
window.addEventListener('resize',()=>{try{drawLongChart();}catch(e){}});

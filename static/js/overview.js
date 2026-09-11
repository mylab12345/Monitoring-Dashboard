const SPARK_H=40;
function smoothPath(ctx,pts){
  ctx.beginPath();
  pts.forEach((p,i)=>{
    if(!i){ctx.moveTo(p[0],p[1]);return;}
    const px=(pts[i-1][0]+p[0])/2,py=(pts[i-1][1]+p[1])/2;
    ctx.quadraticCurveTo(pts[i-1][0],pts[i-1][1],px,py);
  });
  const last=pts[pts.length-1];
  ctx.lineTo(last[0],last[1]);
}
function drawSpark(cv,data,color){
  if(!cv)return;
  const w=cv.clientWidth;
  if(!w||w<10)return;
  const dpr=Math.min(window.devicePixelRatio||1,2);
  cv.width=w*dpr;cv.height=SPARK_H*dpr;
  const ctx=cv.getContext('2d');
  ctx.scale(dpr,dpr);
  ctx.clearRect(0,0,w,SPARK_H);
  if(!data||data.length<2)return;
  const max=Math.max(20,...data);
  const pts=data.map((v,i)=>[1+(i/(data.length-1))*(w-2), SPARK_H-4-(v/max)*(SPARK_H-10)]);
  const g=ctx.createLinearGradient(0,0,0,SPARK_H);
  g.addColorStop(0,color+'3d');g.addColorStop(1,color+'00');
  smoothPath(ctx,pts);
  ctx.strokeStyle=color;ctx.lineWidth=1.6;ctx.lineJoin='round';ctx.stroke();
  ctx.lineTo(w-1,SPARK_H);ctx.lineTo(1,SPARK_H);ctx.closePath();
  ctx.fillStyle=g;ctx.fill();
  const lastP=pts[pts.length-1];
  ctx.beginPath();ctx.arc(lastP[0],lastP[1],2.4,0,7);ctx.fillStyle=color;ctx.fill();
}
function drawSparks(){
  const c=histData.map(s=>s.cpu),m=histData.map(s=>s.ram),d=histData.map(s=>s.disk);
  drawSpark($('#cpuSpark'),c,'#22d3ee');
  drawSpark($('#ramSpark'),m,'#a78bfa');
  drawSpark($('#diskSpark'),d,'#34d399');
}

// ================================================================
// Main time-series chart (CPU / RAM / NET, hover tooltip)
// ================================================================
let histData=[];                       // ascending samples from /api/history (+local)
const chartCfg={range:S.chartRange,series:{cpu:true,ram:true,disk:false,net:false},matrix:false};
let chartHover=null;
const SERIES_META={
  cpu:{color:'#22d3ee',label:'CPU',get:s=>s.cpu,fmt:v=>v.toFixed(1)+' %'},
  ram:{color:'#a78bfa',label:'RAM',get:s=>s.ram,fmt:v=>v.toFixed(1)+' %'},
  disk:{color:'#34d399',label:'Disk',get:s=>s.disk,fmt:v=>v.toFixed(1)+' %'},
  net:{color:'#60a5fa',label:'Net',get:s=>(s.sent_bps+s.recv_bps)/1048576,fmt:v=>v.toFixed(2)+' MB/s'}
};
function toggleMatrixMode(){
  chartCfg.matrix=!chartCfg.matrix;
  const btn=$('#lg-matrix');
  if(btn)btn.classList.toggle('on',chartCfg.matrix);
  if(chartCfg.matrix){
    ['cpu','ram','disk','net'].forEach(k=>{chartCfg.series[k]=true;const b=$('#lg-'+k);if(b)b.classList.add('on');});
  }
  drawMainChart();
}
// Actions that delete data / mutate packages get a confirmation, matching the
// treatment kill/service actions already receive.
const QUICK_CONFIRM={
  'clean':'Clear the package manager caches?',
  'clear-logs':'Vacuum the systemd journal and remove log files older than 7 days? This permanently deletes log history.',
  'full-upgrade':'Run a full system upgrade — including new kernels? This may take several minutes and services may restart. A reboot can be required afterwards.',
  'upgrade':'Install all available package updates? This may take several minutes and services may restart.',
  'autoremove':'Remove packages that are no longer required? Review the output afterwards to confirm nothing important was removed.'
};
async function runQuickAction(action){
  const key='fix:'+action;
  if(!beginAction(key)){toast('That action is already running…','info');return;}
  try{
    const label=(FIX_META[action]&&FIX_META[action].label)||action;
    if(QUICK_CONFIRM[action]){
      const ok=await confirmDlg(label,QUICK_CONFIRM[action],true);
      if(!ok)return;
    }
    toast('Running: '+label+'…','info');
    const j=await postJSON('/api/fix',{action});
    if(j&&j.error){
      toast('Action failed: '+j.error,'err');
      logActivity('fix','Quick action failed: '+label,false);
      return;
    }
    toast('Completed: '+label,'ok');
    logActivity('fix','Quick action: '+label);
    updateChecks();updateStatus();
  }finally{endAction(key);}
}
// Overview "Top Hogs" — sortable by CPU or memory. The API already returns a
// union of the top-CPU and top-memory processes, so both views are free.
let hogsSort=(localStorage.getItem('monitoring:hogsSort')==='mem')?'mem':'cpu';
function setHogsSort(k){
  hogsSort=(k==='mem')?'mem':'cpu';
  try{localStorage.setItem('monitoring:hogsSort',hogsSort);}catch(e){}
  $$('#hogsSortSeg .seg-btn').forEach(b=>{
    const on=b.dataset.k===hogsSort;
    b.classList.toggle('active',on);
    b.setAttribute('aria-pressed',on?'true':'false');
  });
  loadOverviewHogs();
}
async function loadOverviewHogs(){
  const el=$('#overviewHogsList');
  const badge=$('#overviewHogsCount');
  if(!el)return;
  try{
    const d=await fetchJSON('/api/processes?limit=5');
    if(!d||!d.processes){el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('cpu',20)+'</div><strong>Process data unavailable</strong></div><button class="btn btn-sm" style="margin-top:10px" onclick="loadOverviewHogs()">'+icon('refresh',13)+' Retry</button>';return;}
    const key=hogsSort;
    const procs=d.processes.slice().sort((a,b)=>(b[key]||0)-(a[key]||0)).slice(0,5);
    if(badge)badge.textContent='by '+(key==='mem'?'memory':'CPU');
    const em=(on,txt)=>on?'<b style="color:var(--text)">'+txt+'</b>':txt;
    el.innerHTML=procs.map(p=>`
      <div class="hog-row">
        <div class="hog-info">
          <div class="hog-name">${esc(p.name)} <span class="dim" style="font-size:.68rem">(${p.pid})</span></div>
          <div class="hog-meta">${em(key==='cpu','CPU: '+p.cpu+'%')} · ${em(key==='mem','RAM: '+p.mem+'%')}</div>
        </div>
        <button class="icon-btn danger hog-kill" data-pid="${p.pid}" data-name="${esc(p.name)}"
                title="Kill ${esc(p.name)} (PID ${p.pid})" aria-label="Kill ${esc(p.name)} (PID ${p.pid})">${icon('x',12)}</button>
      </div>`).join('');
    // Bind via JS rather than an inline onclick: process names are attacker-
    // controlled and entity-escaping is undone by the HTML parser before the
    // JS parser sees the attribute.
    $$('#overviewHogsList .hog-kill').forEach(b=>b.addEventListener('click',()=>{
      killProcess(+b.dataset.pid,b.dataset.name);
    }));
  }catch(e){
    el.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('cpu',20)+'</div><strong>Failed to load processes</strong>'
      +'<span class="dim">'+esc(e&&e.message||'request failed')+'</span></div>'
      +'<button class="btn btn-sm" style="margin-top:10px" onclick="loadOverviewHogs()">'+icon('refresh',13)+' Retry</button>';
  }
}
async function loadHistory(){
  const d=await fetchJSON('/api/history?points='+chartCfg.range);
  if(d&&Array.isArray(d.samples)){
    histData=d.samples;
    if(!histData.length)histData=localHist.slice(-chartCfg.range);
  }
  $('#chartEmpty').hidden=histData.length>1;
  drawMainChart();drawSparks();
}
function setRange(r,btn){
  chartCfg.range=r;S.chartRange=r;saveSettings();
  $$('#rangeSeg .seg-btn, #rangeSegSetting .seg-btn').forEach(b=>b.classList.toggle('active',+b.dataset.r===r));
  loadHistory();
}
function toggleSeries(k){
  chartCfg.series[k]=!chartCfg.series[k];
  $('#lg-'+k).classList.toggle('on',chartCfg.series[k]);
  drawMainChart();
}
function drawMainChart(){
  const cv=$('#mainChart'),box=$('#chartBox');
  if(!cv||!box||currentTab!=='overview')return;
  const w=box.clientWidth,h=box.clientHeight;
  if(!w||!h)return;
  const dpr=Math.min(window.devicePixelRatio||1,2);
  cv.width=w*dpr;cv.height=h*dpr;
  const ctx=cv.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);
  const data=histData.slice(-chartCfg.range);
  $('#chartEmpty').hidden=data.length>1;
  if(data.length<2)return;
  const cs=getComputedStyle(document.documentElement);
  const grid=cs.getPropertyValue('--border').trim()||'rgba(148,163,184,.13)';
  const label=cs.getPropertyValue('--text-3').trim()||'#5d6b84';
  const netOn=chartCfg.series.net;
  const padL=34,padR=netOn?44:12,padT=10,padB=20;
  const iw=w-padL-padR,ih=h-padT-padB;
  const xAt=i=>padL+(i/(data.length-1))*iw;
  let netMax=1;
  if(netOn){netMax=Math.max(0.1,...data.map(SERIES_META.net.get))*1.15;}
  const yPct=v=>padT+ih-(Math.min(100,Math.max(0,v))/100)*ih;
  const yNet=v=>padT+ih-(Math.min(netMax,Math.max(0,v))/netMax)*ih;

  // grid + left axis labels (0..100)
  ctx.font='9px JetBrains Mono, monospace';
  ctx.fillStyle=label;ctx.strokeStyle=grid;ctx.lineWidth=1;
  [0,25,50,75,100].forEach(p=>{
    const y=Math.round(yPct(p))+.5;
    ctx.beginPath();ctx.moveTo(padL,y);ctx.lineTo(w-padR,y);ctx.stroke();
    ctx.fillText(String(p),6,y+3);
  });
  if(netOn){
    ctx.fillStyle=SERIES_META.net.color;
    ctx.fillText(netMax.toFixed(1),w-padR+6,padT+8);
  }
  // x labels
  ctx.fillStyle=label;
  const ticks=Math.min(5,data.length);
  for(let i=0;i<ticks;i++){
    const idx=Math.round(i/(ticks-1)*(data.length-1));
    const txt=todayAt((data[idx].t||0)*1000);
    const x=xAt(idx);
    ctx.textAlign=i===0?'left':i===ticks-1?'right':'center';
    ctx.fillText(txt,x,h-6);
  }
  ctx.textAlign='left';

  // series areas + lines
  for(const k of Object.keys(chartCfg.series)){
    if(!chartCfg.series[k])continue;
    const meta=SERIES_META[k];
    if(!meta)continue;
    const yAt=k==='net'?yNet:yPct;
    const pts=data.map((s,i)=>[xAt(i),yAt(meta.get(s))]);
    const g=ctx.createLinearGradient(0,padT,0,padT+ih);
    g.addColorStop(0,meta.color+'3a');g.addColorStop(1,meta.color+'00');
    smoothPath(ctx,pts);
    ctx.strokeStyle=meta.color;ctx.lineWidth=1.8;ctx.lineJoin='round';ctx.stroke();
    ctx.lineTo(pts[pts.length-1][0],padT+ih);ctx.lineTo(pts[0][0],padT+ih);ctx.closePath();
    ctx.fillStyle=g;ctx.fill();
  }
  // hover crosshair
  if(chartHover!=null&&chartHover>=0&&chartHover<data.length){
    const x=xAt(chartHover);
    ctx.strokeStyle=label;ctx.setLineDash([3,3]);ctx.lineWidth=1;
    ctx.beginPath();ctx.moveTo(x,padT);ctx.lineTo(x,padT+ih);ctx.stroke();
    ctx.setLineDash([]);
    for(const k of Object.keys(chartCfg.series)){
      if(!chartCfg.series[k])continue;
      const meta=SERIES_META[k];
      if(!meta)continue;
      const yy=k==='net'?yNet(meta.get(data[chartHover])):yPct(meta.get(data[chartHover]));
      ctx.beginPath();ctx.arc(x,yy,3.2,0,7);
      ctx.fillStyle=meta.color;ctx.fill();
      ctx.strokeStyle=cs.getPropertyValue('--surface').trim()||'#10131a';
      ctx.lineWidth=1.5;ctx.stroke();
    }
    positionTip(x);
  }
}
function chartBoxEvents(){
  const box=$('#chartBox'),cv=$('#mainChart'),tip=$('#chartTip');
  if(!box)return;
  const idxFromEvent=e=>{
    const r=cv.getBoundingClientRect();
    const x=e.clientX-r.left;
    const data=histData.slice(-chartCfg.range);
    if(data.length<2)return null;
    const padL=34,padR=chartCfg.series.net?44:12;
    const iw=r.width-padL-padR;
    const frac=Math.min(1,Math.max(0,(x-padL)/iw));
    return Math.round(frac*(data.length-1));
  };
  box.addEventListener('mousemove',e=>{
    if(!histData.length)return;
    const mouseX=e.clientX-cv.getBoundingClientRect().left;
    chartHover=idxFromEvent(e);
    drawMainChart();
    if(chartHover==null){tip.style.opacity=0;return;}
    const data=histData.slice(-chartCfg.range);
    const s=data[chartHover];
    let html='<div class="tt-time">'+todayAt((s.t||0)*1000)+'</div>';
    for(const k of Object.keys(chartCfg.series)){
      if(!chartCfg.series[k])continue;
      const meta=SERIES_META[k];
      if(!meta)continue;
      html+='<div class="tt-row"><i style="background:'+meta.color+'"></i>'+meta.label+'<b>'+meta.fmt(meta.get(s))+'</b></div>';
    }
    tip.innerHTML=html;
    tip.style.opacity=1;
    positionTip(mouseX);
  });
  box.addEventListener('mouseleave',()=>{chartHover=null;tip.style.opacity=0;drawMainChart();});
}
function positionTip(x){
  const tip=$('#chartTip'),box=$('#chartBox');
  if(!tip||!box)return;
  const bw=box.clientWidth,tw=tip.offsetWidth;
  let left=x+14;
  if(left+tw>bw-8)left=x-tw-14;
  tip.style.left=Math.max(0,left)+'px';
  tip.style.top='10px';
}

// ================================================================
// Rings + animated numbers
// ================================================================
const RING_C=188.5;
function setRing(id,pct){
  const el=$('#'+id),txt=$('#'+id.replace('Ring','Pct'));
  if(!el)return;
  el.style.strokeDashoffset=String(RING_C*(1-Math.min(100,Math.max(0,pct))/100));
  el.style.stroke=(pct>=85?'#f87171':pct>=60?'#fbbf24':'');
  if(txt)txt.textContent=Math.round(pct)+'%';
}
function animNum(el,to,dec){
  if(!el)return;
  // Default to 1 decimal: `undefined` made toFixed() round to whole numbers,
  // so a 7.4% reading rendered as "7" while its delta chip said "+0.4".
  if(dec==null)dec=1;
  if(!isFinite(to))to=0;
  const from=parseFloat(el.dataset.v||0)||0;
  if(REDUCED||Math.abs(to-from)<0.05){el.textContent=to.toFixed(dec);el.dataset.v=to;return;}
  const t0=performance.now(),dur=650;
  (function step(t){
    const k=Math.min(1,(t-t0)/dur),e=1-Math.pow(1-k,3);
    el.textContent=(from+(to-from)*e).toFixed(dec);
    if(k<1)requestAnimationFrame(step);else el.dataset.v=to;
  })(t0);
  el.dataset.v=to;
}

// ================================================================
// Live status + KPI + alerts engine
// ================================================================
let prevStatus=null,prevNetRate=null;
const localHist=[]; // client-side fallback when /api/history is empty
function deltaFor(el,cur,prev){
  if(!el)return;
  if(prev==null){el.hidden=true;return;}
  const d=+(cur-prev).toFixed(1);
  el.hidden=false;
  if(Math.abs(d)<0.05){el.textContent='— 0.0';el.className='delta';return;}
  el.textContent=(d>0?'▲ +':'▼ ')+Math.abs(d).toFixed(1);
  el.className='delta '+(d>0?'up':'down');
}
async function updateStatus(){
  const d=await fetchJSON('/api/status');
  const chip=$('#liveChip'),conn=$('#connDot');
  if(!d||d.error){
    // /api/status is a metrics/readiness endpoint. Keep API connectivity
    // separate: a slow or unavailable host metric must not be shown as the
    // whole dashboard being offline.
    chip.classList.add('off');conn.classList.remove('off');conn.classList.add('degraded');
    if(prevStatus===null){
      const probe=await fetchJSON('/api/health');
      if(probe&&probe.status==='ok'){
        $('#hostChip').textContent='API online · metrics unavailable';
        $('#hostChip').title=lastApiError||'The Flask API is reachable, but live status failed.';
      }else{
        conn.classList.add('off');
        $('#hostChip').textContent='API offline';
        $('#hostChip').title=lastApiError||'Could not reach the Monitoring API.';
      }
    }
    return;
  }
  chip.classList.remove('off');conn.classList.remove('off','degraded');
  const p=prevStatus;
  // CPU
  animNum($('#cpuVal'),d.cpu_percent);
  setRing('cpuRing',d.cpu_percent);
  $('#cpuVal').className='val '+statusClass(d.cpu_percent);
  $('#cpuSub').textContent=(d.cpu_count||1)+' cores';
  $('#cpuNote').innerHTML=(d.cpu_freq_mhz?'<b>'+d.cpu_freq_mhz+'</b> MHz · ':'')+'load <b>'+esc(String(d.load_1))+'</b>';
  deltaFor($('#cpuDelta'),d.cpu_percent,p&&p.cpu_percent);
  // RAM
  animNum($('#ramVal'),d.ram_percent);
  setRing('ramRing',d.ram_percent);
  $('#ramVal').className='val '+statusClass(d.ram_percent);
  $('#memSub').textContent=d.ram_used_gb+' / '+d.ram_total_gb+' GB';
  $('#ramNote').innerHTML='swap <b>'+(d.swap_total_gb>0?d.swap_used_gb+'/'+d.swap_total_gb+' GB ('+Math.round(d.swap_percent)+'%)':'off')+'</b>';
  deltaFor($('#ramDelta'),d.ram_percent,p&&p.ram_percent);
  // Disk
  animNum($('#diskVal'),d.disk_percent);
  setRing('diskRing',d.disk_percent);
  $('#diskVal').className='val '+statusClass(d.disk_percent);
  $('#diskSub').textContent=d.disk_used_gb+' / '+d.disk_total_gb+' GB';
  deltaFor($('#diskDelta'),d.disk_percent,p&&p.disk_percent);
  // Uptime / load / temp / battery
  $('#uptimeVal').textContent=fmtDur(d.uptime_sec);
  const boot=new Date(Date.now()-d.uptime_sec*1000);
  $('#bootTime').textContent='booted '+String(boot.getHours()).padStart(2,'0')+':'+String(boot.getMinutes()).padStart(2,'0');
  const nc=Math.max(1,d.cpu_count||1);
  [['lb1','lbv1',d.load_1],['lb5','lbv5',d.load_5],['lb15','lbv15',d.load_15]].forEach(([b,v,val])=>{
    $('#'+b).style.width=Math.min(100,val/nc*100)+'%';
    $('#'+v).textContent=val;
  });
  let note='';
  if(d.temp&&d.temp!=='N/A')note+=icon('thermo',11)+' <b>'+esc(String(d.temp))+'</b>  ';
  if(d.battery)note+=icon('battery',12)+' <b>'+d.battery.percent+'%</b>'+(d.battery.plugged?' ⚡':'');
  $('#uptimeNote').innerHTML=note||'load 1m · 5m · 15m';
  const tdel=$('#tempDelta');
  if(d.temp&&d.temp!=='N/A'){tdel.hidden=false;tdel.textContent=d.temp;}
  else tdel.hidden=true;
  // Net rate (client-side derivative)
  let rateStr='—';
  if(prevNetRate){
    const dt=(Date.now()-prevNetRate.t)/1000;
    if(dt>0.5){
      const rbps=Math.max(0,(d.net_recv_bytes-prevNetRate.rb)/dt);
      const sbps=Math.max(0,(d.net_sent_bytes-prevNetRate.sb)/dt);
      rateStr='↓ '+fmtRate(rbps)+' · ↑ '+fmtRate(sbps);
    }
  }
  prevNetRate={t:Date.now(),rb:d.net_recv_bytes,sb:d.net_sent_bytes};
  // System card + strip
  $('#osInfo').textContent=d.os;
  $('#hostnameInfo').textContent=d.hostname;
  $('#kernelInfo').textContent=d.kernel;
  $('#procsCount').textContent=d.processes;
  $('#netVal').textContent='↓ '+d.net_recv+' · ↑ '+d.net_sent;
  $('#usersVal').textContent=d.users||'0';
  $('#hostChip').textContent=d.hostname||'--';
  $('#stripHost').innerHTML='<b>'+esc(d.hostname)+'</b>';
  $('#stripOs').textContent=d.os;
  $('#stripKernel').textContent=d.kernel;
  $('#stripUptime').textContent=fmtDur(d.uptime_sec);
  $('#stripNet').textContent=rateStr;
  // client-side history fallback (when server sampler empty)
  if(!histData.length){
    localHist.push({t:Date.now()/1000,cpu:d.cpu_percent,ram:d.ram_percent,disk:d.disk_percent,
      sent_bps:0,recv_bps:0,temp:parseFloat(d.temp)||null,load1:d.load_1});
    if(localHist.length>chartCfg.range)localHist.shift();
    if(localHist.length>1){histData=localHist.slice();$('#chartEmpty').hidden=true;drawMainChart();drawSparks();}
  }
  checkAlerts(d);
  prevStatus=d;
}

// system strip facts (static-ish)
let sysInfo=null;
async function loadSystemInfo(){
  const d=await fetchJSON('/api/systeminfo');
  if(!d||d.error)return;
  sysInfo=d;
  $('#stripCpu').textContent=d.cpu_model;
  $('#stripCpu').title=d.cpu_cores+' threads · '+d.virtualization;
}

// ================================================================
// Alerting engine (local thresholds)
// ================================================================

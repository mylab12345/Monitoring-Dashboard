let statusTimer=null,histCounter=0;
function restartTimers(){
  if(statusTimer)clearInterval(statusTimer);
  statusTimer=setInterval(()=>{
    if(document.hidden)return;
    updateStatus();
    if(++histCounter>=Math.max(2,Math.round(S.refreshMs/2000)))  {histCounter=0;loadHistory();}
  },S.refreshMs);
}

// ================================================================
// Command palette
// ================================================================
let paletteOpen=false;
function openPalette(){
  if(paletteOpen)return;
  paletteOpen=true;
  let filter='',sel=0;
  const items=[
    {g:'Go to',label:'Overview',icon:'gauge',hint:MOD+'1',run:()=>showTab('overview')},
    {g:'Go to',label:'Processes',icon:'cpu',hint:MOD+'2',run:()=>showTab('processes')},
    {g:'Go to',label:'Network',icon:'network',hint:MOD+'3',run:()=>showTab('network')},
    {g:'Go to',label:'Services',icon:'gear',hint:MOD+'4',run:()=>showTab('services')},
    {g:'Go to',label:'Virtual Machines',icon:'monitor',hint:MOD+'5',run:()=>showTab('vms')},
    {g:'Go to',label:'Logs',icon:'terminal',hint:MOD+'6',run:()=>showTab('logs')},
    {g:'Go to',label:'Alerts & Activity',icon:'pulse',hint:MOD+'7',run:()=>showTab('alerts')},
    {g:'Go to',label:'Diagnose & Troubleshooting',icon:'zap',hint:MOD+'8',run:()=>showTab('troubleshooting')},
    {g:'Go to',label:'System & Kernel Tool',icon:'wrench',run:()=>showTab('maintain')},
    {g:'Go to',label:'Settings',icon:'gear',hint:MOD+'9',run:()=>showTab('settings')},
    {g:'Go to',label:'Help & About',icon:'help',hint:MOD+'0',run:()=>showTab('help')},
    {g:'Actions',label:'Refresh current view',icon:'refresh',hint:'R',run:refreshCurrent},
    {g:'Actions',label:'Refresh everything',icon:'refresh',run:refreshAll},
    {g:'Actions',label:'Toggle dark / light theme',icon:'sun',hint:MOD+'T',run:cycleTheme},
    {g:'Actions',label:'Show keyboard shortcuts',icon:'keyboard',hint:'?',run:openShortcuts},
    {g:'Actions',label:'Clear alert history',icon:'trash',run:clearAlerts},
    {g:'Actions',label:'Clear activity log',icon:'trash',run:clearActivity},
    {g:'Actions',label:'Launch desktop window',icon:'monitor',run:openDesktopApp},
    {g:'Actions',label:'Open in browser',icon:'globe',run:openInBrowser}
  ];
  const bd=document.createElement('div');
  bd.className='palette-backdrop';
  bd.innerHTML='<div class="palette" role="dialog" aria-label="Command palette">'
    +'<div class="p-input">'+icon('search',16)+'<input id="palInput" placeholder="Search tabs and actions…" autocomplete="off" spellcheck="false"></div>'
    +'<div class="p-list" id="palList"></div></div>';
  document.body.appendChild(bd);
  const input=$('#palInput'),list=$('#palList');
  const filtered=()=>{const q=filter.trim().toLowerCase();return q?items.filter(i=>i.label.toLowerCase().includes(q)):items;};
  function render(){
    const its=filtered();
    sel=Math.max(0,Math.min(sel,its.length-1));
    if(!its.length){list.innerHTML='<div class="p-empty">No results for “'+esc(filter)+'”</div>';return;}
    let html='',lastG=null;
    its.forEach((it,i)=>{
      if(it.g!==lastG){html+='<div class="p-group">'+esc(it.g)+'</div>';lastG=it.g;}
      html+='<button class="p-item '+(i===sel?'sel':'')+'" data-i="'+i+'">'
        +icon(it.icon,15)+'<span>'+esc(it.label)+'</span>'
        +(it.hint?'<span class="hint">'+esc(it.hint)+'</span>':'')+'</button>';
    });
    list.innerHTML=html;
    const selEl=list.querySelector('.p-item.sel');
    if(selEl&&selEl.scrollIntoView)selEl.scrollIntoView({block:'nearest'});
    list.querySelectorAll('.p-item').forEach(b=>b.addEventListener('click',()=>{
      const it=filtered()[+b.dataset.i];
      close();it.run();
    }));
  }
  function close(){paletteOpen=false;bd.remove();}
  function pick(){const its=filtered();if(its[sel]){const it=its[sel];close();it.run();}}
  input.addEventListener('input',()=>{filter=input.value;sel=0;render();});
  input.addEventListener('keydown',e=>{
    if(e.key==='Escape'){e.stopPropagation();close();}
    else if(e.key==='ArrowDown'){e.preventDefault();sel=Math.min(filtered().length-1,sel+1);render();}
    else if(e.key==='ArrowUp'){e.preventDefault();sel=Math.max(0,sel-1);render();}
    else if(e.key==='Enter'){e.preventDefault();pick();}
  });
  bd.addEventListener('mousedown',e=>{if(e.target===bd)close();});
  render();input.focus();
}

// ================================================================
// Keyboard shortcuts + clock
// ================================================================
function refreshCurrent(){
  const fn=TAB_LOADERS[currentTab];
  if(fn)runTabLoader(currentTab,$('#tab-'+currentTab));
  if(currentTab==='overview'||!fn){updateStatus();updateChecks();loadHistory();}
}
function refreshAll(){
  updateStatus();updateChecks();loadHistory();loadSystemInfo();loadDisks();loadPorts();loadOverviewHogs();
  loadProcesses();loadServices();loadVMs();loadNetwork();loadLogs();
  if(currentTab==='troubleshooting')loadTroubleshooting(true);
  if(currentTab==='maintain')loadMaintain(true);
}
function focusSearch(){
  const panel=$('#tab-'+currentTab);
  if(panel){const inp=panel.querySelector('input[type=search]');if(inp){inp.focus();inp.select();}}
}
document.addEventListener('keydown',e=>{
  const mod=e.metaKey||e.ctrlKey;
  if(mod&&e.key.toLowerCase()==='k'){e.preventDefault();openPalette();return;}
  if(mod&&e.key.toLowerCase()==='t'){e.preventDefault();cycleTheme();return;}
  if(mod&&/^[0-9]$/.test(e.key)){e.preventDefault();showTab(TAB_NAMES[(+e.key+9)%10]);return;}
  if(mod&&e.key==='ArrowRight'){e.preventDefault();const i=TAB_NAMES.indexOf(currentTab);showTab(TAB_NAMES[(i+1)%TAB_NAMES.length]);return;}
  if(mod&&e.key==='ArrowLeft'){e.preventDefault();const i=TAB_NAMES.indexOf(currentTab);showTab(TAB_NAMES[(i-1+TAB_NAMES.length)%TAB_NAMES.length]);return;}
  if(isTyping(e)||mod)return;
  if(e.key==='/'&&document.activeElement===document.body){e.preventDefault();focusSearch();return;}
  if(e.key==='?'){e.preventDefault();openShortcuts();return;}
  if(e.key.toLowerCase()==='r'){refreshCurrent();}
  if(e.key==='Escape'&&window.innerWidth<=768){
    sidebar.classList.remove('open');
    menuToggle.setAttribute('aria-expanded','false');
  }
});
function tickClock(){$('#clock').textContent=nowTime();}
// Guarded timers skip while hidden, so data was stale until the next tick after
// returning. Refresh immediately when the tab becomes visible again.
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden)refreshCurrent();
});
// Coalesce resize redraws into one frame instead of redrawing per event.
let resizeRaf=0;
window.addEventListener('resize',()=>{
  if(resizeRaf)cancelAnimationFrame(resizeRaf);
  resizeRaf=requestAnimationFrame(()=>{resizeRaf=0;drawMainChart();drawSparks();});
});

// ================================================================
// Init
// ================================================================
(async()=>{
  $('#palKbd').textContent=MOD+' K';
  $('#palKbd2').textContent=MOD+' K';
  // Quick Admin Actions: use the shared SVG icon set (was emoji, which render
  // inconsistently and carry no accessible name) and a platform-correct hint.
  [['#qaRefresh','refresh','Refresh All'],['#qaUpgrade','up','Full Upgrade'],
   ['#qaClean','sparkles','Clean Cache'],
   ['#qaUpdate','up','Update Lists'],['#qaVacuum','trash','Vacuum Journal'],
   ['#qaPalette','keyboard','Command Palette ('+MOD+' K)']].forEach(([sel,ic,label])=>{
    const b=$(sel);
    if(b)b.innerHTML=icon(ic,13)+' <span>'+esc(label)+'</span>';
  });
  $('#kCtrlK').textContent=IS_MAC?'⌘':MOD;
  $('#kCtrlN').textContent=IS_MAC?'⌘':MOD;
  $('#kCtrlT').textContent=IS_MAC?'⌘':MOD;
  const v=await fetchJSON('/api/version');
  if(v){
    const vs='v'+(v.version||'');
    $('#footerVersion').textContent=vs;
    $('#brandVer').textContent=vs+' · local-first';
  }
  tickClock();
})();

applyTheme();
renderFixButtons();
renderActivity();
renderAlerts();
refreshAlertBadge();
renderSettings();
renderDiagHistory();
chartBoxEvents();
$('#logWrap').checked=!!S.logWrap;
toggleLogWrap(); // apply wrap setting
// Load the Overview (the default tab) only. The other tabs fetch on first
// visit via TAB_LOADERS — booting all of them fired ~11 requests up front,
// most for panels the user could not see.
updateStatus();updateChecks();loadHistory();loadSystemInfo();loadDisks();loadPorts();loadOverviewHogs();
restartTimers();
// /api/checks shells out to apt/dpkg/systemctl/journalctl — the most expensive
// endpoint here. It was the only timer without a visibility guard, so it kept
// running on backgrounded tabs indefinitely.
setInterval(()=>{if(!document.hidden)updateChecks();},60000);
setInterval(()=>{if(!document.hidden)loadDisks();},30000);
setInterval(()=>{if(!document.hidden)loadOverviewHogs();},15000);
setInterval(()=>{if(!document.hidden&&$('#procAuto').checked&&$('#tab-processes').classList.contains('active'))loadProcesses();},5000);
setInterval(()=>{if(!document.hidden&&$('#logAuto').checked&&$('#tab-logs').classList.contains('active'))loadLogs();},5000);
// Opt-in auto-refresh for the Services, VMs and Network tabs (all off by
// default: services/VM listings shell out on the backend, so only poll when
// the operator asks for it).
setInterval(()=>{const c=$('#svcAuto');if(!document.hidden&&c&&c.checked&&$('#tab-services').classList.contains('active'))loadServices();},10000);
setInterval(()=>{const c=$('#vmAuto');if(!document.hidden&&c&&c.checked&&$('#tab-vms').classList.contains('active'))loadVMs();},10000);
setInterval(()=>{const c=$('#netAuto');if(!document.hidden&&c&&c.checked&&$('#tab-network').classList.contains('active')){loadNetwork();loadPorts();}},5000);
setInterval(tickClock,1000);

// Deep link
const initial=location.hash.replace('#','');
if(initial&&document.getElementById('tab-'+initial))showTab(initial);

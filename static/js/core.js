// Global error guard: a runtime error in any feature area is surfaced as a
// non-fatal toast instead of silently freezing the UI. Because the app is now
// split into separate <script> files, a parse error in one file also cannot
// stop the others from loading.
window.addEventListener('error',function(e){
  try{
    const msg=(e&&e.message)||'Unknown error';
    console.error('Monitoring runtime error:',msg,e);
    if(typeof toast==='function')toast('Unexpected error: '+msg,'err');
  }catch(_){}
});

const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const IS_MAC=/Mac|iP(hone|ad|od)/.test(navigator.platform||navigator.userAgent||'');
const MOD=IS_MAC?'⌘':'Ctrl';
const REDUCED=matchMedia('(prefers-reduced-motion: reduce)').matches;

const ICON_PATHS={
  refresh:'<path d="M21 12a9 9 0 1 1-2.64-6.36M21 3v6h-6"/>',
  search:'<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.35-4.35"/>',
  x:'<path d="M18 6L6 18M6 6l12 12"/>',
  play:'<path d="M7 5v14l11-7z"/>',
  stop:'<rect x="6" y="6" width="12" height="12" rx="2"/>',
  restart:'<path d="M3 12a9 9 0 1 1 2.64 6.36L3 21v-6M3 15h6"/>',
  power:'<path d="M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0"/>',
  trash:'<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  resize:'<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/>',
  copy:'<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a15.3 15.3 0 0 1 0 18 15.3 15.3 0 0 1 0-18z"/>',
  box:'<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.3 7l8.7 5 8.7-5M12 22V12"/>',
  monitor:'<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  terminal:'<path d="M4 17l6-6-6-6M12 19h8"/>',
  gauge:'<path d="M12 14l4-4M20.5 14.5A8.5 8.5 0 0 1 3.5 14.5"/><circle cx="12" cy="14" r="8.5"/>',
  disk:'<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>',
  package:'<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><path d="M3.3 7l8.7 5 8.7-5M12 22V12"/>',
  alert:'<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><path d="M12 9v4M12 17h.01"/>',
  zap:'<path d="M13 2L3 14h7l-1 8 10-12h-7l1-8z"/>',
  pulse:'<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
  check:'<path d="M20 6L9 17l-5-5"/>',
  checkCircle:'<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5 4.5-4.5"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/>',
  help:'<circle cx="12" cy="12" r="9"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3M12 17h.01"/>',
  shield:'<path d="M12 2l8 4v6c0 5-3.5 8.5-8 10-4.5-1.5-8-5-8-10V6z"/>',
  cpu:'<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>',
  mem:'<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10v4M10 10v4M14 10v4M18 10v4"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
  wrench:'<path d="M14.7 6.3a4.5 4.5 0 0 0 5.6 5.6L17 15l-3-3 3.3-3.3a4.5 4.5 0 0 0-5.6-5.6L8.5 6.3A4.5 4.5 0 0 0 3 13l3 3-4 4 2 2 4-4 3 3a4.5 4.5 0 0 0 6.7-5.5z"/>',
  sparkles:'<path d="M12 3l1.9 4.8 5.1.4-4 3.3 1.2 5-4.2-2.9L7.8 16.5 9 11.5 5 8.2l5.1-.4z"/><path d="M19 3v4M17 5h4"/>',
  up:'<path d="M12 19V5M5 12l7-7 7 7"/>',
  keyboard:'<rect x="2" y="4" width="20" height="16" rx="2"/><path d="M6 8h.01M10 8h.01M14 8h.01M18 8h.01M6 12h.01M10 12h.01M14 12h.01M18 12h.01M7 16h10"/>',
  thermo:'<path d="M14 14.76V5a2 2 0 1 0-4 0v9.76a4 4 0 1 0 4 0z"/>',
  battery:'<rect x="2" y="7" width="16" height="10" rx="2"/><path d="M22 11v2"/>',
  gear:'<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h0a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51h0a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v0a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>',
  network:'<rect x="2" y="2" width="20" height="8" rx="2"/><rect x="2" y="14" width="20" height="8" rx="2"/><path d="M6 6h.01M6 18h.01"/>',
  chart:'<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/>',
  layers:'<path d="M12 2l9 5-9 5-9-5 9-5z"/><path d="M3 12l9 5 9-5M3 17l9 5 9-5"/>',
  sun:'<circle cx="12" cy="12" r="4.5"/><path d="M12 2v2.5M12 19.5V22M2 12h2.5M19.5 12H22M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M19.4 4.6l-1.8 1.8M6.4 17.6l-1.8 1.8"/>',
  moon:'<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  users:'<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  down:'<path d="M6 9l6 6 6-6"/>',
  up2:'<path d="M6 15l6-6 6 6"/>',
  history:'<path d="M3 12a9 9 0 1 0 2.64-6.36L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 3"/>',
  loader:'<path d="M12 2a10 10 0 0 1 10 10"/><path d="M22 17a10 10 0 0 1-4.5 4.5"/>',
  folder:'<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>'
};
const icon=(n,s=15)=>'<svg width="'+s+'" height="'+s+'" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'+(ICON_PATHS[n]||'')+'</svg>';

let busy=0;
// Active-request indicator: spins the toolbar refresh icon while any API
// request is in flight (CSS: .icon-btn.spinning svg { animation: spin }).
function spinRefresh(){
  const b=$('#refreshBtn');
  if(b)b.classList.toggle('spinning',busy>0);
}
let lastApiError='';
let apiToken='';
try{apiToken=localStorage.getItem('monitoring:token')||'';}catch(e){}
function authHeaders(h){
  const out=Object.assign({},h||{});
  if(apiToken)out['Authorization']='Bearer '+apiToken;
  return out;
}
function clearApiToken(){apiToken='';try{localStorage.removeItem('monitoring:token');}catch(e){}}
async function promptApiToken(){
  if(apiToken){clearApiToken();} // stored token was rejected — ask again
  const r=await openModal({
    title:'Authentication required',
    text:'This dashboard is protected by an access token. Enter it to continue.',
    fields:[{key:'token',label:'Access token',type:'password'}],
    confirmText:'Unlock',cancelText:'Cancel',icon:'shield'
  });
  const tok=r&&r.values&&r.values.token?r.values.token.trim():'';
  if(!tok)return false;
  apiToken=tok;
  try{localStorage.setItem('monitoring:token',apiToken);}catch(e){}
  return true;
}
async function fetchJSON(url,opts){
  busy++;spinRefresh();
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),15000);
  try{
    const r=await fetch(url,Object.assign({},opts||{},{signal:controller.signal,headers:authHeaders(Object.assign({},(opts&&opts.headers)||{}, {'Accept':'application/json'}))}));
    if(r.status===401){
      lastApiError='Authentication required';
      if(await promptApiToken())return fetchJSON(url,opts);
      throw new Error('Authentication required');
    }
    const text=await r.text();
    let data=null;
    try{data=text?JSON.parse(text):null;}catch(e){throw new Error('API returned invalid JSON (HTTP '+r.status+')');}
    if(!r.ok)throw new Error((data&&data.error)||('API request failed (HTTP '+r.status+')'));
    lastApiError='';
    return data;
  }catch(e){
    lastApiError=e&&e.name==='AbortError'?'API request timed out':(e&&e.message)||'API request failed';
    console.warn('Monitoring API:',url,lastApiError);
    return null;
  }finally{clearTimeout(timeout);busy--;spinRefresh();}
}
async function postJSON(url,body){
  busy++;spinRefresh();
  const controller=new AbortController();
  // Privileged actions (apt upgrade, vm resize, fix-all) can legitimately run
  // for minutes; give them a generous timeout instead of hanging forever.
  const timeout=setTimeout(()=>controller.abort(),320000);
  try{
    const r=await fetch(url,{method:'POST',signal:controller.signal,headers:authHeaders({'Content-Type':'application/json'}),body:JSON.stringify(body)});
    if(r.status===401){
      if(await promptApiToken())return postJSON(url,body);
      return {error:'Authentication required'};
    }
    const text=await r.text();
    let data=null;
    try{data=text?JSON.parse(text):null;}catch(e){
      return {error:'API returned invalid JSON (HTTP '+r.status+')'};
    }
    // Surface HTTP failures as {error} even when the body omits the key, so
    // callers that only test `j.error` cannot mistake a 4xx/5xx for success.
    if(!r.ok)return {error:(data&&data.error)||('Request failed (HTTP '+r.status+')')};
    return data||{};
  }catch(e){return {error:'request failed'};}
  finally{clearTimeout(timeout);busy--;spinRefresh();}
}
// Auth-aware plain fetch for endpoints that return JSON but are not wrapped
// by fetchJSON/postJSON (e.g. the diagnostics report). Attaches the Bearer
// token, prompts once on 401, and never recurses forever.
async function apiFetch(url,opts){
  const r=await fetch(url,Object.assign({},opts||{},{headers:authHeaders(Object.assign({},(opts&&opts.headers)||{}, {'Accept':'application/json'}))}));
  if(r.status===401){
    if(await promptApiToken())return apiFetch(url,opts);
    throw new Error('Authentication required');
  }
  return r;
}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function jsq(s){return esc(s).replace(/'/g,"\\'");}
function isTyping(e){const t=e.target;return t&&(t.tagName==='INPUT'||t.tagName==='SELECT'||t.tagName==='TEXTAREA'||t.isContentEditable);}
function fmtDur(sec){
  const d=Math.floor(sec/86400),h=Math.floor((sec%86400)/3600),m=Math.floor((sec%3600)/60);
  if(d>0)return d+'d '+h+'h';
  if(h>0)return h+'h '+m+'m';
  return m+'m';
}
function fmtRate(bps){
  if(bps==null||isNaN(bps))return '—';
  if(bps<1024)return bps.toFixed(0)+' B/s';
  if(bps<1048576)return (bps/1024).toFixed(1)+' KB/s';
  if(bps<1073741824)return (bps/1048576).toFixed(2)+' MB/s';
  return (bps/1073741824).toFixed(2)+' GB/s';
}
function fmtBytesMB(mb){return mb>=1024?(mb/1024).toFixed(2)+' GB':mb.toFixed(1)+' MB';}
function statusClass(v){return v>=85?'hot':v>=60?'warm':'';}
function nowTime(){const t=new Date();return [t.getHours(),t.getMinutes(),t.getSeconds()].map(n=>String(n).padStart(2,'0')).join(':');}
function todayAt(ts){const d=new Date(ts);return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0')+':'+String(d.getSeconds()).padStart(2,'0');}

// ================================================================
// Settings store (localStorage, this browser only)
// ================================================================
const DEFAULT_SETTINGS={
  theme:'system', refreshMs:3000, chartRange:450,
  alerts:true, confirmDanger:true, procLimit:25, logWrap:true,
  th:{cpuWarn:80,cpuCrit:92,ramWarn:85,ramCrit:95,diskWarn:85,diskCrit:93,tempWarn:75}
};
let S=loadSettings();
function loadSettings(){
  try{
    const raw=JSON.parse(localStorage.getItem('monitoring:settings')||'{}');
    return Object.assign({},DEFAULT_SETTINGS,raw,{th:Object.assign({},DEFAULT_SETTINGS.th,(raw||{}).th||{})});
  }catch(e){return JSON.parse(JSON.stringify(DEFAULT_SETTINGS));}
}
function saveSettings(){try{localStorage.setItem('monitoring:settings',JSON.stringify(S));}catch(e){}}
function setSetting(k,v){S[k]=v;saveSettings();if(k==='alerts')refreshAlertBadge();}

// ================================================================
// Theme
// ================================================================
function applyTheme(){
  const t=S.theme==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):S.theme;
  document.documentElement.dataset.theme=t;
  const btn=$('#themeBtn');
  if(btn)btn.innerHTML=icon(t==='dark'?'sun':'moon',15);
  $$('#themeSeg .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.t===S.theme));
}
function setTheme(t,btn){S.theme=t;saveSettings();applyTheme();if(btn)syncThemeSeg();}
function syncThemeSeg(){$$('#themeSeg .seg-btn').forEach(b=>b.classList.toggle('active',b.dataset.t===S.theme));}
function cycleTheme(){setTheme(document.documentElement.dataset.theme==='dark'?'light':'dark');logActivity('theme','Switched to '+S.theme+' theme',true);}
matchMedia('(prefers-color-scheme: light)').addEventListener('change',()=>{if(S.theme==='system')applyTheme();});

// ================================================================
// Toasts
// ================================================================
function toast(msg,kind){
  const t=document.createElement('div');
  t.className='toast '+(kind||'ok');
  t.innerHTML='<span class="t-icon">'+icon(kind==='err'?'alert':kind==='info'?'info':'checkCircle',14)+'</span>'
    +'<span class="t-msg"></span>'
    +'<button class="t-close" title="Dismiss">'+icon('x',12)+'</button>'
    +'<span class="t-bar"></span>';
  t.querySelector('.t-msg').textContent=msg;
  t.querySelector('.t-close').onclick=()=>killToast(t);
  $('#toasts').appendChild(t);
  requestAnimationFrame(()=>{t.querySelector('.t-bar').style.animation='shrink 4.2s linear forwards';});
  setTimeout(()=>killToast(t),4300);
}
function killToast(t){t.classList.add('out');setTimeout(()=>t.remove(),320);}

// ================================================================
// Modal (confirmation + forms + custom html)
// ================================================================
function openModal({title,text='',html='',fields=[],confirmText='Confirm',cancelText='Cancel',danger=false,icon:ic='help',note=''}){
  return new Promise(res=>{
    let settled=false;
    const bd=document.createElement('div');
    bd.className='modal-backdrop';
    const fieldsHtml=fields.map(f=>
      '<label for="mf-'+f.key+'">'+esc(f.label)+'</label>'
      +'<input id="mf-'+f.key+'" data-field="'+f.key+'" type="'+esc(f.type||'text')+'" value="'+esc(f.value||'')+'"'
      +' placeholder="'+esc(f.placeholder||'')+'"'+(f.type==='number'?' inputmode="decimal"':'')+' autocomplete="off">').join('');
    bd.innerHTML='<div class="modal '+(danger?'danger':'')+'" role="dialog" aria-modal="true" aria-label="'+esc(title)+'">'
      +'<div class="m-icon">'+icon(ic,18)+'</div>'
      +'<h3>'+esc(title)+'</h3>'
      +(text?'<p class="m-text">'+esc(text)+'</p>':'')
      +(html?'<div class="m-body">'+html+'</div>':'')
      +(fieldsHtml?'<div class="m-body">'+fieldsHtml+'</div>':'')
      +(note?'<p class="m-note">'+esc(note)+'</p>':'')
      +'<div class="m-actions"><button class="btn" data-act="cancel">'+esc(cancelText)+'</button>'
      +'<button class="btn '+(danger?'btn-danger':'btn-primary')+'" data-act="ok">'+esc(confirmText)+'</button></div></div>';
    document.body.appendChild(bd);
    const done=v=>{if(settled)return;settled=true;bd.remove();document.removeEventListener('keydown',escH);res(v);};
    const escH=e=>{if(e.key==='Escape'){e.stopPropagation();done(null);}};
    document.addEventListener('keydown',escH);
    bd.querySelector('[data-act=cancel]').onclick=()=>done(null);
    bd.querySelector('[data-act=ok]').onclick=()=>{
      const values={};
      bd.querySelectorAll('[data-field]').forEach(i=>values[i.dataset.field]=i.value.trim());
      done({values});
    };
    bd.addEventListener('mousedown',e=>{if(e.target===bd)done(null);});
    const first=bd.querySelector('input');
    if(first){first.focus();first.select&&first.select();}
    else bd.querySelector('[data-act=ok]').focus();
    bd.addEventListener('keydown',e=>{
      if(e.key==='Enter'&&e.target.tagName!=='INPUT')bd.querySelector('[data-act=ok]').click();
    });
  });
}
async function confirmDlg(title,text,danger){
  if(!S.confirmDanger)return {values:null}; // skip prompt when disabled
  return openModal({title,text,confirmText:'Confirm',danger,icon:danger?'alert':'help'});
}
function openShortcuts(){
  const rows=[
    ['Command palette',MOD+' K'],['Next / previous tab',MOD+' → / ←'],
    ['Jump to tab',MOD+' 1–9, 0'],['Refresh current view','R'],
    ['Toggle theme',MOD+' T'],['Focus search box','/'],
    ['Close dialog','Esc']
  ].map(r=>'<div class="shortcut-row"><span>'+esc(r[0])+'</span><span class="keys">'
    +r[1].split(' + ').map(k=>'<span class="kbd">'+esc(k)+'</span>').join(' ')+'</span></div>').join('');
  openModal({title:'Keyboard Shortcuts',html:rows,confirmText:'Close',cancelText:'Close',icon:'keyboard',danger:false});
}

// ================================================================
// Tabs
// ================================================================

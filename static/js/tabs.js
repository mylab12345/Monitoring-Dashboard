const TAB_NAMES=['overview','processes','network','services','vms','logs','alerts','troubleshooting','settings','help','maintain'];
// Tab loaders are lazy thunks (not direct function references) so that:
//   * load order between files stops mattering, and
//   * a missing/broken loader only fails when that tab is opened, not at boot.
const TAB_LOADERS={
  overview:null,
  processes:()=>loadProcesses(),
  network:()=>{loadNetwork();loadPorts();},
  services:()=>loadServices(),
  vms:()=>loadVMs(),
  logs:()=>loadLogs(),
  alerts:()=>renderAlerts(),
  troubleshooting:()=>loadTroubleshooting(),
  settings:()=>renderSettings(),
  help:()=>fillHelp(),
  maintain:()=>loadMaintain(true)
};
let currentTab='overview';
function runTabLoader(name,panel){
  const fn=TAB_LOADERS[name];
  if(!fn)return;
  try{
    // Most loaders are async. A synchronous try/catch alone does not catch a
    // rejected Promise, which previously left tab buttons looking unresponsive.
    Promise.resolve(fn()).catch(err=>{
      console.error('tab loader failed:',name,err);
      toast('Failed to load '+name+': '+(err&&err.message?err.message:String(err)),'err');
      if(panel&&!panel.querySelector('.empty-state')){
        panel.insertAdjacentHTML('afterbegin','<div class="empty-state"><strong>Tab data unavailable</strong>'
          +'<span class="dim">'+esc(err&&err.message?err.message:String(err))+'</span></div>');
      }
    });
  }catch(err){
    console.error('tab loader failed:',name,err);
    toast('Failed to load '+name+': '+(err&&err.message?err.message:String(err)),'err');
  }
}
function showTab(name){
  if(!TAB_NAMES.includes(name))return;
  currentTab=name;
  $$('.nav-btn').forEach(b=>{b.classList.toggle('active',b.dataset.tab===name);b.removeAttribute('aria-current');if(b.dataset.tab===name)b.setAttribute('aria-current','page');});
  $$('.tab-panel').forEach(p=>p.classList.toggle('active',p.id==='tab-'+name));
  const btn=$('.nav-btn[data-tab="'+name+'"]');
  $('#pageTitle').textContent=btn?btn.querySelector('span').textContent:name;
  $('#pageSub').textContent=btn?(btn.dataset.sub||''):'';
  if(location.hash!=='#'+name)history.replaceState(null,'','#'+name);
  const panel=$('#tab-'+name);
  if(panel&&!REDUCED){panel.style.animation='none';void panel.offsetWidth;panel.style.animation='';}
  runTabLoader(name,panel);
  drawMainChart();drawSparks();
  if(window.innerWidth<=768){$('.sidebar').classList.remove('open');$('#menuToggle').setAttribute('aria-expanded','false');}
}
$$('.nav-btn').forEach(b=>b.addEventListener('click',()=>showTab(b.dataset.tab)));

// ================================================================
// Sidebar rail (collapse) + mobile drawer
// ================================================================
const shell=$('#appShell');
if(localStorage.getItem('monitoring:rail')==='1')shell.classList.add('rail');
$('#sidebarToggle').addEventListener('click',()=>{
  shell.classList.add('rail');localStorage.setItem('monitoring:rail','1');
});
$('#railExpand').addEventListener('click',()=>{
  shell.classList.remove('rail');localStorage.setItem('monitoring:rail','0');
});
const menuToggle=$('#menuToggle'),sidebar=$('.sidebar');
menuToggle.addEventListener('click',e=>{
  e.stopPropagation();
  const isOpen=sidebar.classList.toggle('open');
  menuToggle.setAttribute('aria-expanded',isOpen?'true':'false');
});
document.addEventListener('click',e=>{
  if(window.innerWidth<=768&&!sidebar.contains(e.target)&&!menuToggle.contains(e.target)){
    sidebar.classList.remove('open');menuToggle.setAttribute('aria-expanded','false');
  }
});

// ================================================================
// Sparklines (metric cards)
// ================================================================

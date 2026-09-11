async function fillHelp(){
  const v=await fetchJSON('/api/version');
  if(!v){
    const el=$('#helpVersion');
    if(el)el.textContent='unavailable';
    toast('Could not load version info: '+(lastApiError||'request failed'),'err');
    return;
  }
  $('#helpVersion').textContent='v'+(v.version||'--');
  $('#helpHome').textContent=v.home||'--';
  $('#helpPython').textContent='Python '+(v.python||'--');
  $('#helpUser').textContent=v.root?'root (full control)':'user'+(v.sudo?' (passwordless sudo)':'');
  $('#helpUrl').textContent=location.origin;
  if(typeof loadAppUpdate==='function')loadAppUpdate(true);
}
function openDesktopApp(){
  toast('Launching desktop window…','info');
  postJSON('/api/open_app',{}).then(j=>{
    if(j&&j.error)toast(j.error,'err');
    else if(j&&j.result)toast(j.result,'ok');
  }).catch(()=>{});
}
function openInBrowser(){window.open(location.origin,'_blank');}

// ================================================================
// Settings UI
// ================================================================
function renderSettings(){
  syncThemeSeg();
  $$('.refreshSel').forEach(el=>el.value=String(S.refreshMs));
  $('#setConfirm').checked=!!S.confirmDanger;
  $('#setAlerts').checked=!!S.alerts;
  $('#procLimit').value=String(S.procLimit);
  ['cpuWarn','cpuCrit','ramWarn','ramCrit','diskWarn','diskCrit','tempWarn'].forEach(k=>{const el=$('#th-'+k);if(el)el.value=S.th[k];});
  $$('#rangeSeg .seg-btn, #rangeSegSetting .seg-btn').forEach(b=>b.classList.toggle('active',+b.dataset.r===chartCfg.range));
}
function saveThresholds(){
  // Validate before saving: warn must stay below crit for each metric pair,
  // otherwise alerts could never fire (or fire permanently).
  const read=k=>{const el=$('#th-'+k);return el&&el.value!==''?Math.max(1,Math.min(120,parseFloat(el.value)||S.th[k])):S.th[k];};
  const next={cpuWarn:read('cpuWarn'),cpuCrit:read('cpuCrit'),ramWarn:read('ramWarn'),ramCrit:read('ramCrit'),
              diskWarn:read('diskWarn'),diskCrit:read('diskCrit'),tempWarn:read('tempWarn')};
  const bad=[['CPU','cpuWarn','cpuCrit'],['Memory','ramWarn','ramCrit'],['Disk','diskWarn','diskCrit']]
    .filter(([,w,c])=>next[w]>=next[c]).map(([label])=>label);
  if(bad.length){toast(bad.join(', ')+': warn threshold must be lower than crit','err');return;}
  Object.assign(S.th,next);
  saveSettings();renderSettings();
  toast('Thresholds saved');logActivity('system','Updated alert thresholds');
}
// Export / import the full settings object so a tuned configuration can be
// moved between browsers or backed up.
function exportSettings(){
  downloadText('monitoring-settings.json',JSON.stringify(S,null,2),'application/json');
  toast('Settings exported','info');
}
function importSettings(){
  const inp=document.createElement('input');
  inp.type='file';inp.accept='.json,application/json';
  inp.onchange=()=>{
    const f=inp.files&&inp.files[0];
    if(!f)return;
    const rd=new FileReader();
    rd.onload=()=>{
      try{
        const raw=JSON.parse(String(rd.result));
        if(!raw||typeof raw!=='object')throw new Error('not an object');
        S=Object.assign({},DEFAULT_SETTINGS,raw,{th:Object.assign({},DEFAULT_SETTINGS.th,raw.th||{})});
        saveSettings();applyTheme();renderSettings();restartTimers();
        toast('Settings imported','ok');logActivity('system','Imported settings from file');
      }catch(e){toast('Import failed: not a valid settings file','err');}
    };
    rd.readAsText(f);
  };
  inp.click();
}
function resetSettings(){
  openModal({title:'Reset all settings',text:'Restore default theme, thresholds, intervals and data settings?',danger:true,confirmText:'Reset',icon:'refresh'}).then(r=>{
    if(!r)return;
    S=JSON.parse(JSON.stringify(DEFAULT_SETTINGS));
    saveSettings();
    applyTheme();renderSettings();setRefresh(String(S.refreshMs));setRange(S.chartRange);
    toast('Settings reset to defaults','info');
  });
}
function setRefresh(ms){
  S.refreshMs=+ms;saveSettings();
  $$('.refreshSel').forEach(el=>el.value=String(S.refreshMs));
  restartTimers();
  toast('Refresh interval: '+(S.refreshMs/1000)+'s','info');
}

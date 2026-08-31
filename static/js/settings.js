async function fillHelp(){
  const v=await fetchJSON('/api/version');
  if(!v)return;
  $('#helpVersion').textContent='v'+(v.version||'--');
  $('#helpHome').textContent=v.home||'--';
  $('#helpPython').textContent='Python '+(v.python||'--');
  $('#helpUser').textContent=v.root?'root (full control)':'user'+(v.sudo?' (passwordless sudo)':'');
  $('#helpUrl').textContent=location.origin;
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
  ['cpuWarn','cpuCrit','ramWarn','ramCrit','diskWarn','diskCrit','tempWarn'].forEach(k=>{
    const el=$('#th-'+k);
    if(el&&el.value!=='')S.th[k]=Math.max(1,Math.min(120,parseFloat(el.value)||S.th[k]));
  });
  saveSettings();toast('Thresholds saved');logActivity('system','Updated alert thresholds');
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

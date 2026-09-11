let logText='';
async function loadLogs(){
  const view=$('#logView');
  try{
  const lines=$('#logLines').value;
  const prio=$('#logPrio').value;
  const grep=$('#logGrep').value;
  const d=await fetchJSON('/api/logs?lines='+lines+'&prio='+prio+'&grep='+encodeURIComponent(grep));
  if(!d||d.error)throw new Error((d&&d.error)||'the API did not respond');
  logText=(d&&d.logs)||'No log output.';
  const ls=logText.split('\n');
  const tag=$('#logCountTag');
  tag.hidden=false;tag.textContent=ls.length+' lines';
  stampUpdated('logUpdated');
  view.innerHTML=(logText==='No log output.'?'<span class="ll">No log output.</span>':ls.map(l=>{
    const cls=/\b(error|fail|fatal|critical|panic|segfault|emerg|alert|crit)\b/i.test(l)?'err'
      :/\bwarn/i.test(l)?'warn'
      :/\bnotice\b/i.test(l)?'notice'
      :/\binfo\b/i.test(l)?'info':'';
    return '<span class="ll '+cls+'">'+esc(l)+'</span>';
  }).join(''));
  if($('#logAuto').checked)view.scrollTop=view.scrollHeight;
  }catch(e){
    const tag=$('#logCountTag');
    if(tag){tag.hidden=false;tag.textContent='error';}
    if(view)view.innerHTML='<span class="ll err">Failed to load logs: '+esc(e.message||'request failed')+' — <a href="#" onclick="loadLogs();return false;">retry</a></span>';
  }
}
function toggleLogWrap(){
  const v=$('#logView');
  v.classList.toggle('nowrap',!$('#logWrap').checked);
  S.logWrap=$('#logWrap').checked;saveSettings();
}
async function copyLogs(){
  if(!logText){toast('Nothing to copy yet','info');return;}
  await copyText(logText,'Logs copied to clipboard');
}
function downloadLogs(){
  if(!logText){toast('Nothing to download yet','info');return;}
  downloadText('monitoring-journal-'+new Date().toISOString().replace(/[:.]/g,'-')+'.log',logText);
  toast('Log file downloaded','info');
}

// ================================================================
// Help / desktop
// ================================================================

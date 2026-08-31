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
  try{await navigator.clipboard.writeText(logText);toast('Logs copied to clipboard');return;}
  catch(e){}
  // Fallback for non-secure contexts / older browsers
  try{
    const ta=document.createElement('textarea');
    ta.value=logText;ta.style.position='fixed';ta.style.opacity='0';
    document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();
    toast('Logs copied to clipboard');
  }catch(e2){toast('Copy failed','err');}
}
function downloadLogs(){
  const blob=new Blob([logText],{type:'text/plain'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob);
  a.download='monitoring-journal-'+new Date().toISOString().replace(/[:.]/g,'-')+'.log';
  a.click();URL.revokeObjectURL(a.href);
  toast('Log file downloaded','info');
}

// ================================================================
// Help / desktop
// ================================================================

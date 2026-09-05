const VM_STATE_PILL={running:'ok',paused:'warn',shutoff:'neutral'};
async function loadVMs(){
  const container=$('#vmList');
  const vms=await fetchJSON('/api/vms');
  if(!vms){
    container.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('alert',22)+'</div><strong>VM data unavailable</strong><span class="dim">'+esc(lastApiError||'The API did not respond')+'</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadVMs()">'+icon('refresh',13)+' Retry</button></div>';
    return;
  }
  if(!Array.isArray(vms)||vms.length===0){
    container.innerHTML='<div class="empty-state"><div class="empty-icon">'+icon('monitor',22)+'</div><strong>No VMs found</strong><span class="dim">Make sure libvirt is running and you can access it (libvirt group or root).</span></div>';
    return;
  }
  container.innerHTML='<div class="grid g2">'+vms.map(vm=>{
    const up=vm.state==='running'||vm.state==='paused';
    return `
    <div class="card hoverable" style="padding:16px 18px;background:var(--surface-2)">
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div class="m-chip" style="--mc:${up?'#34d399':'#9aa3b2'};--mc-bg:${up?'rgba(52,211,153,.12)':'rgba(148,163,184,.12)'};--mc-line:${up?'rgba(52,211,153,.28)':'rgba(148,163,184,.25)'};width:36px;height:36px">${icon('monitor',17)}</div>
        <div style="min-width:0;flex:1">
          <div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">
            <strong style="font-size:.95rem;letter-spacing:-.01em">${esc(vm.name)}</strong>
            <span class="pill ${VM_STATE_PILL[vm.state]||'neutral'}">${esc(vm.state)}</span>
          </div>
          <div class="mono dim" style="font-size:.66rem;margin-top:3px">
            Memory <b style="color:var(--text-2)">${esc(vm.mem)}</b> · vCPUs <b style="color:var(--text-2)">${esc(vm.vcpus)}</b>
          </div>
        </div>
        <div class="btn-group" style="margin:0">
          ${(up
            ?`<button class="btn btn-sm vm-action" data-name="${esc(vm.name)}" data-action="reboot">${icon('restart',12)}Reboot</button>
              <button class="btn btn-sm vm-action" data-name="${esc(vm.name)}" data-action="shutdown">${icon('power',12)}Shutdown</button>
              <button class="btn btn-sm btn-danger vm-action" data-name="${esc(vm.name)}" data-action="destroy">${icon('stop',12)}Force Off</button>`
            :`<button class="btn btn-sm btn-primary vm-action" data-name="${esc(vm.name)}" data-action="start">${icon('play',12)}Start</button>`)}
          <button class="btn btn-sm vm-resize" data-name="${esc(vm.name)}">${icon('resize',12)}Resize Disk</button>
          <button class="btn btn-sm vm-config" data-name="${esc(vm.name)}" data-vcpus="${esc(vm.vcpus)}" data-mem="${esc(vm.mem)}">${icon('gear',12)}Configure</button>
        </div>
      </div>
    </div>`;
  }).join('')+'</div>';
  // Bind via data attributes to avoid inline JS injection via VM names
  $$('#vmList .vm-action').forEach(b=>{
    b.addEventListener('click',()=>vmAction(b.dataset.name,b.dataset.action));
  });
  $$('#vmList .vm-resize').forEach(b=>{
    b.addEventListener('click',()=>openResizeModal(b.dataset.name));
  });
  $$('#vmList .vm-config').forEach(b=>{
    b.addEventListener('click',()=>openConfigModal(b.dataset.name,b.dataset.vcpus,b.dataset.mem));
  });
}
async function vmAction(name,action){
  if(action==='destroy'&&!await confirmDlg('Force off VM','Force power-off “'+name+'”? Unsaved data in the guest will be lost.',true))return;
  logActivity('vm',action+' VM '+name);
  const j=await postJSON('/api/vm/action',{name,action});
  if(j.error){toast(action+' failed: '+j.error,'err');logActivity('vm',action+' '+name+' failed',false);}
  else{toast(j.result||'OK');loadVMs();}
}
async function openResizeModal(name){
  let hint='/var/lib/libvirt/images/'+name+'.qcow2';
  try{
    const info=await fetchJSON('/api/vm_info/'+encodeURIComponent(name));
    const blob=[info&&info.disks,info&&info.virsh].filter(Boolean).join('\n');
    const m=blob.match(/\/\S+\.(qcow2|qcow|img|raw)/);
    if(m)hint=m[0];
  }catch(e){}
  const r=await openModal({
    title:'Resize virtual disk',
    text:'Grow the disk image for “'+name+'”. Existing data is preserved; the guest filesystem may still need extending afterwards.',
    icon:'resize',confirmText:'Resize',
    fields:[
      {key:'disk_path',label:'Disk path',value:hint,placeholder:'/var/lib/libvirt/images/vm.qcow2'},
      {key:'size',label:'New size (GB)',type:'number',placeholder:'40'}
    ]
  });
  if(!r)return;
  const size=parseInt(r.values.size,10);
  if(!size||size<1){toast('Enter a valid size in GB','err');return;}
  logActivity('vm','Resize disk of '+name+' to '+size+' GB');
  const j=await postJSON('/api/vm_resize',{name,disk_path:r.values.disk_path,new_size_gb:size});
  if(j.error){toast('Resize failed: '+j.error,'err');logActivity('vm','Resize of '+name+' failed',false);}
  else toast(j.result||'Resize queued');
}
async function openConfigModal(name,currentVcpus,currentMem){
  const memMatch=(currentMem||'').match(/([\d.]+)\s*(GB|MB)/i);
  let currentRamGB=0;
  if(memMatch){
    currentRamGB=parseFloat(memMatch[1]);
    if(memMatch[2].toUpperCase()==='MB')currentRamGB=Math.round(currentRamGB/1024*10)/10;
  }
  const vcpus=parseInt(currentVcpus,10)||1;
  const r=await openModal({
    title:'Configure '+name,
    html:'<div style="font-size:.82rem;color:var(--text-2);margin-bottom:8px">'
      +(location.hostname==='localhost'||location.hostname==='127.0.0.1'
        ?'Changes take effect immediately on a running VM (hot-add).'
        :'Changes take effect immediately on a running VM (hot-add). To decrease, power off the VM first.')
      +'</div>',
    icon:'gear',confirmText:'Apply',
    fields:[
      {key:'vcpus',label:'vCPUs',type:'number',value:String(vcpus),placeholder:'2'},
      {key:'ram_gb',label:'RAM (GB)',type:'number',value:String(currentRamGB||4),placeholder:'4'}
    ],
    note:'Current: '+vcpus+' vCPUs, '+esc(currentMem)
  });
  if(!r)return;
  const newVcpus=parseInt(r.values.vcpus,10);
  const newRamGB=parseFloat(r.values.ram_gb);
  if(!newVcpus||newVcpus<1){toast('Enter a valid vCPU count (1–256)','err');return;}
  if(!newRamGB||newRamGB<0.25){toast('Enter a valid RAM value (≥ 0.25 GB)','err');return;}
  logActivity('vm','Configure '+name+': vcpus='+newVcpus+' ram='+newRamGB+'GB');
  const j=await postJSON('/api/vm_config',{name,vcpus:newVcpus,ram_gb:newRamGB});
  if(j.error){toast('Configure failed: '+j.error,'err');logActivity('vm','Configure '+name+' failed',false);}
  else{toast(j.result||'Configuration updated');loadVMs();}
}

// ================================================================
// Logs
// ================================================================

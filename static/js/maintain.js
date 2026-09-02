// ================================================================
// System & Kernel tool: repair, full upgrade, performance tuning
// ================================================================
let maintainData = null;
let mntBusy = false;

function mntSevPill(status, okLabel, warnLabel) {
  const cls = status === 'ok' ? 'ok' : (status === 'needs-rebuild' || status === 'stale' || status === 'fail' ? 'warn' : 'info');
  const label = status === 'ok' ? okLabel : (status === 'needs-rebuild' ? warnLabel : status);
  return '<span class="pill ' + cls + '">' + esc(label) + '</span>';
}

async function loadMaintain(silent) {
  const list = $('#mntChips'), btn = $('#mntScanBtn');
  if (!silent && btn) {
    btn.disabled = true;
    btn.innerHTML = icon('refresh', 13) + ' Checking…';
  }
  try {
    const r = await apiFetch('/api/maintain');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    maintainData = d;
    renderMaintain();
    const st = $('#mntScanTime');
    if (st) st.textContent = 'last checked ' + todayAt(Date.now());
  } catch (e) {
    console.error('Failed to load system & kernel status:', e);
    if (list) list.innerHTML = '<div class="empty-state"><div class="empty-icon">' + icon('alert', 20) + '</div><strong>Status unavailable</strong><span class="dim">' + esc(e.message || 'the API did not respond') + '</span><button class="btn btn-sm" style="margin-top:10px" onclick="loadMaintain(false)">' + icon('refresh', 13) + ' Retry</button></div>';
    const hs = $('#mntHelperState');
    if (hs) { hs.textContent = 'unavailable'; hs.className = 'pill fail'; }
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('refresh', 13) + ' Re-check'; }
  }
}

function renderMaintain() {
  const d = maintainData; if (!d) return;
  const helper = d.helper || 'missing';
  const hs = $('#mntHelperState');
  if (hs) {
    hs.textContent = helper === 'root' ? 'root access' : helper === 'sudo' ? 'sudo helper ready' : helper === 'denied' ? 'helper denied' : 'helper not installed';
    hs.className = 'pill ' + (helper === 'root' || helper === 'sudo' ? 'ok' : helper === 'denied' ? 'fail' : 'warn');
  }

  // ---- status chips
  const chips = $('#mntChips');
  const kern = d.kernel || {}, ini = d.initramfs || {}, pkgs = d.packages || {}, sys = d.system || {};
  if (chips) {
    const c = [];
    if (kern.running) c.push('<span class="cap-chip" title="Running kernel">' + icon('cpu', 11) + esc(kern.running) + '</span>');
    if (kern.installed && kern.installed.length > 1) c.push('<span class="cap-chip" title="' + kern.installed.length + ' installed kernels">' + icon('layers', 11) + esc(kern.installed.length) + ' kernels</span>');
    if (d.reboot_required) c.push('<span class="cap-chip" style="color:var(--amber)">' + icon('power', 11) + 'reboot required</span>');
    if (ini.status === 'needs-rebuild') c.push('<span class="cap-chip" style="color:var(--amber)">' + icon('zap', 11) + 'initramfs needs rebuild</span>');
    if (d.module_map && d.module_map.exists && d.module_map.stale) c.push('<span class="cap-chip" style="color:var(--amber)">' + icon('zap', 11) + 'module map stale</span>');
    if (pkgs.needs_repair) c.push('<span class="cap-chip" style="color:var(--red)">' + icon('alert', 11) + 'package db needs repair</span>');
    if (d.fwupd && d.fwupd.active === true) c.push('<span class="cap-chip">' + icon('info', 11) + 'fwupd active</span>');
    if (d.perf && d.perf.applied) c.push('<span class="cap-chip" style="color:var(--green)">' + icon('gauge', 11) + 'perf profile: ' + esc(d.perf.profile || 'applied') + '</span>');
    if (!c.length) c.push('<span class="cap-chip">' + icon('checkCircle', 11) + 'no issues detected</span>');
    chips.innerHTML = c.join('');
  }

  // ---- upgrade card
  const upd = d.updates || {};
  const badge = $('#mntUpgradeBadge');
  if (badge) {
    badge.textContent = upd.count ? upd.count + ' update' + (upd.count === 1 ? '' : 's') + (upd.manager ? ' (' + upd.manager + ')' : '') : 'up to date';
    badge.className = 'badge ' + (upd.count ? 'warn' : 'ok');
  }
  const ul = $('#mntUpgradeList');
  if (ul) {
    if (!upd.manager) {
      ul.innerHTML = '<div class="empty-state" style="padding:14px"><div class="empty-icon">' + icon('package', 18) + '</div><strong>No supported package manager</strong><span class="dim">Full upgrade is unavailable on this host.</span></div>';
    } else if (!upd.count) {
      ul.innerHTML = '<div class="empty-state" style="padding:14px"><div class="empty-icon">' + icon('checkCircle', 18) + '</div><strong>All packages are up to date</strong><span class="dim">Run it anyway to refresh lists and re-sync state.</span></div>';
    } else {
      ul.innerHTML = '<div class="mnt-list-head">Top ' + Math.min(upd.packages.length, 8) + ' of ' + upd.count + ' pending:</div>'
        + upd.packages.slice(0, 8).map(p => '<div class="mnt-row"><span class="mnt-name">' + esc(p.name) + '</span><span class="dim small">' + esc(p.version || '') + '</span></div>').join('');
    }
  }
  const rn = $('#mntRebootNote');
  if (rn) rn.hidden = !d.reboot_required && !(kern.installed && kern.installed.length && kern.installed[0] !== kern.running);

  // ---- repair card
  const rs = $('#mntRepairState');
  const repairNeeded = !!(d.reboot_required || (ini.status === 'needs-rebuild') ||
    (d.module_map && d.module_map.exists && d.module_map.stale) ||
    (pkgs.needs_repair) || (kern.installed && kern.installed.length && kern.installed[0] !== kern.running));
  if (rs) {
    if (helper !== 'root' && helper !== 'sudo') { rs.textContent = 'helper not installed'; rs.className = 'pill fail'; }
    else if (repairNeeded) { rs.textContent = 'repair recommended'; rs.className = 'pill warn'; }
    else { rs.textContent = 'healthy'; rs.className = 'pill ok'; }
  }
  const steps = $('#mntRepairSteps');
  if (steps) {
    const rows = [];
    const stateOf = (ok, why) => '<span class="pill ' + (ok ? 'ok' : 'warn') + '">' + (ok ? 'ok' : 'attention') + '</span>' + (why ? '<span class="dim small"> ' + esc(why) + '</span>' : '');
    rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('package', 13) + ' Package database</span>' +
      stateOf(!pkgs.needs_repair, pkgs.needs_repair ? 'incomplete transactions detected' : (pkgs.manager ? pkgs.manager : 'no manager')) + '</div>');
    rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('layers', 13) + ' Kernel module map</span>' +
      stateOf(!(d.module_map && d.module_map.exists && d.module_map.stale), (d.module_map && !d.module_map.exists) ? 'not generated for running kernel' : '') + '</div>');
    rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('zap', 13) + ' Initramfs images</span>' +
      stateOf(ini.status !== 'needs-rebuild', (ini.status === 'needs-rebuild') ? (ini.stale || []).join(', ') + ' kernel(s) stale/missing' : (ini.tool || 'no tool')) + '</div>');
    rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('power', 13) + ' Reboot status</span>' +
      stateOf(!d.reboot_required, d.reboot_required ? ((d.reboot_packages || []).join(', ') || 'updates need a reboot') : 'no reboot needed') + '</div>');
    rows.push('<label class="mnt-check"><input type="checkbox" id="mntForceInitramfs"> <span><strong>Force initramfs rebuild</strong><span class="dim small"> advanced — rebuilds images for every installed kernel even when they look current.</span></span></label>');
    steps.innerHTML = rows.join('');
  }
  const rb = $('#mntRepairBtn');
  if (rb) rb.disabled = helper !== 'root' && helper !== 'sudo';

  // ---- fix-all card
  renderFixAll(d);

  // ---- perf card
  renderPerf(d.perf);

  // ---- kernel & firmware card
  const kv = $('#mntKernelKV');
  if (kv) {
    const rows = [];
    rows.push('<div class="kv"><span>Running kernel</span><strong class="mono">' + esc(kern.running || '—') + '</strong></div>');
    rows.push('<div class="kv"><span>Newest installed</span><strong class="mono">' + esc(kern.latest || '—') + '</strong></div>');
    rows.push('<div class="kv"><span>Active kernel</span><strong>' + (kern.running_is_latest ? 'newest — no reboot needed' : 'older than installed — reboot to activate') + '</strong></div>');
    rows.push('<div class="kv"><span>OS / arch</span><strong>' + esc((sys.os || '—') + ' · ' + (sys.arch || '—')) + '</strong></div>');
    rows.push('<div class="kv"><span>Firmware daemon</span><strong>' + (d.fwupd && d.fwupd.active ? 'fwupd active — check vendor updates' : 'fwupd inactive/absent') + '</strong></div>');
    kv.innerHTML = rows.join('');
  }
  renderKernelErrors();
}

function renderFixAll(d) {
  const fa = d.fix_all || {};
  const issues = Array.isArray(fa.issues) ? fa.issues : [];
  const helperReady = d.helper === 'root' || d.helper === 'sudo';

  const badge = $('#mntFixAllBadge');
  if (badge) {
    if (!helperReady) { badge.textContent = 'helper not installed'; badge.className = 'badge'; }
    else if (issues.length) { badge.textContent = issues.length + ' issue' + (issues.length === 1 ? '' : 's') + ' found'; badge.className = 'badge warn'; }
    else { badge.textContent = 'all clear'; badge.className = 'badge ok'; }
  }
  const st = $('#mntFixAllState');
  if (st) {
    st.textContent = !helperReady ? 'unavailable' : issues.length ? 'fix recommended' : 'healthy';
    st.className = 'pill ' + (!helperReady ? 'fail' : issues.length ? 'warn' : 'ok');
  }
  const list = $('#mntFixAllIssues');
  if (list) {
    if (!helperReady) {
      list.innerHTML = '<div class="empty-state" style="padding:12px"><div class="empty-icon">' + icon('shield', 18) + '</div><strong>Privileged helper not available</strong><span class="dim">Run install.sh / update.sh (sudo) to enable Fix All.</span></div>';
    } else if (!issues.length) {
      list.innerHTML = '<div class="empty-state" style="padding:12px"><div class="empty-icon">' + icon('checkCircle', 18) + '</div><strong>No system &amp; kernel issues detected</strong><span class="dim">Fix All is still available to run the full upgrade and re-sync boot state.</span></div>';
    } else {
      const sevIcon = { error: 'alert', warn: 'zap', info: 'info' };
      list.innerHTML = issues.map(it =>
        '<div class="mnt-row"><span class="mnt-name">' + icon(sevIcon[it.severity] || 'info', 13) + ' ' + esc(it.label) + '</span>'
        + '<span class="pill ' + (it.severity === 'error' ? 'fail' : it.severity === 'warn' ? 'warn' : 'info') + '">' + esc(it.severity) + '</span></div>'
      ).join('');
    }
  }
  const btn = $('#mntFixAllBtn');
  if (btn) btn.disabled = !helperReady || mntBusy;
}

function renderPerf(perf) {
  const badge = $('#mntPerfBadge');
  if (badge) {
    badge.textContent = perf && perf.applied ? 'profile: ' + (perf.profile || 'applied') : 'kernel defaults';
    badge.className = 'badge ' + (perf && perf.applied ? 'ok' : '');
  }
  const kv = $('#mntPerfKV');
  if (kv) {
    if (!perf || perf.helper === 'missing') {
      kv.innerHTML = '<div class="empty-state" style="padding:10px"><div class="empty-icon">' + icon('gauge', 18) + '</div><strong>Performance helper not installed</strong><span class="dim">Run install.sh / update.sh to enable tuning.</span></div>';
    } else {
      const rows = [
        '<div class="kv"><span>CPU governor</span><strong class="mono">' + esc((perf.governors || []).join(', ') || 'unmanaged (no cpufreq)') + '</strong></div>',
        '<div class="kv"><span>vm.swappiness</span><strong class="mono">' + esc(String(perf.swappiness ?? '—')) + '</strong></div>',
        '<div class="kv"><span>I/O schedulers</span><strong class="mono">' + esc((perf.schedulers || []).join(', ') || '—') + '</strong></div>',
        '<div class="kv"><span>Persistence</span><strong>' + (perf.applied ? (perf.systemd ? 'sysctl drop-in + systemd unit' : 'sysctl drop-in (no systemd)') : 'not applied') + '</strong></div>'
      ];
      kv.innerHTML = rows.join('');
    }
  }
  const grid = $('#mntPerfProfiles');
  if (grid) {
    const P = [
      { id: 'balanced', label: 'Balanced', desc: 'schedutil · swappiness 10 · mq-deadline', ic: 'gauge' },
      { id: 'performance', label: 'Max performance', desc: 'performance governor · swappiness 10 · none', ic: 'zap' },
      { id: 'powersave', label: 'Powersave', desc: 'powersave · swappiness 60', ic: 'battery' }
    ];
    grid.innerHTML = P.map(p =>
      '<button class="mnt-profile' + (perf && perf.profile === p.id ? ' on' : '') + '" onclick="applyPerfProfile(\'' + p.id + '\',this)">'
      + '<span class="mnt-profile-ic">' + icon(p.ic, 15) + '</span>'
      + '<span class="mnt-profile-txt"><strong>' + esc(p.label) + '</strong><span class="dim small">' + esc(p.desc) + '</span></span>'
      + '</button>').join('');
  }
  const rv = $('#mntPerfRevert');
  if (rv) rv.disabled = !(perf && perf.applied);
  const note = $('#mntPerfNote');
  if (note) note.textContent = perf && perf.applied ? 'stored for reboot — revert anytime' : 'changes persist until reverted';

  // Read-only performance health facts + tuning hints (computed app-side).
  const health = (perf && perf.health) || {};
  const hc = $('#mntPerfHealth');
  if (hc) {
    const rows = [];
    const load = Array.isArray(health.load) ? health.load.map(v => v.toFixed(1)) : null;
    if (load) rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('pulse', 13) + ' Load average (1/5/15m)</span><span class="mono small">' + esc(load.join(' / ')) + '</span></div>');
    if (health.swap_used_pct !== undefined && health.swap_used_pct !== null) {
      rows.push('<div class="mnt-row"><span class="mnt-name">' + icon('mem', 13) + ' Swap in use</span><span class="mono small">' + esc(health.swap_used_pct + '%') + (health.swap_used_mb !== undefined && health.swap_used_mb !== null ? ' (' + health.swap_used_mb + ' MB)' : '') + '</span></div>');
    }
    const hints = Array.isArray(health.hints) ? health.hints : [];
    if (hints.length) rows.push('<div class="mnt-list-head" style="margin-top:6px">Performance hints</div>'
      + hints.map(h => '<div class="mnt-row"><span class="mnt-name">' + icon('sparkles', 13) + ' ' + esc(h) + '</span></div>').join(''));
    if (rows.length) hc.innerHTML = rows.join('');
    else hc.innerHTML = '';
  }
}

function renderKernelErrors() {
  const el = $('#mntKernelErrors');
  if (!el) return;
  // Recent kernel error lines come from the health check (cheap, already cached).
  fetchJSON('/api/checks').then(ck => {
    if (!ck || !Array.isArray(ck)) { el.innerHTML = ''; return; }
    const k = ck.find(c => /kernel/i.test(c.name || ''));
    if (!k || k.status === 'ok') {
      el.innerHTML = '<div class="empty-state" style="padding:12px"><div class="empty-icon">' + icon('checkCircle', 18) + '</div><strong>No kernel errors this boot</strong><span class="dim">Latest kernel log lines are clean.</span></div>';
      return;
    }
    el.innerHTML = '<div class="mnt-list-head">' + esc(k.detail) + ' — <button class="linklike" onclick="showTab(\'logs\')">open logs</button></div>'
      + '<p class="dim small">Run the Diagnose scan for the exact error lines and targeted guidance, or use Repair above after confirming the cause.</p>';
  }).catch(() => { el.innerHTML = ''; });
}

// ---- actions ----------------------------------------------------
async function runMaintainUpgrade() {
  const d = maintainData || {};
  const n = (d.updates && d.updates.count) || 0;
  const ok = await openModal({
    title: 'Run full system upgrade',
    text: n
      ? 'Install ' + n + ' available update(s) — including new kernels and library upgrades that plain "Upgrade" defers.'
      : 'No updates are currently pending, but the upgrade will refresh package lists and re-sync state.',
    html: '<div class="fix-preview"><div class="kv"><span>Operation</span><strong>refresh lists + full-upgrade (incl. kernels)</strong></div>'
      + '<div class="kv"><span>Manager</span><strong>' + esc(d.updates && d.updates.manager || 'n/a') + '</strong></div>'
      + '<div class="kv"><span>Impact</span><strong>may take several minutes; services may restart</strong></div></div>',
    confirmText: 'Upgrade now', cancelText: 'Cancel', danger: true, icon: 'up',
    note: 'A reboot may be required afterwards to activate new kernels.'
  });
  if (!ok) return;
  const btn = $('#mntUpgradeBtn'), term = $('#mntUpgradeTerm'), out = $('#mntUpgradeResult');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner">' + icon('refresh', 15) + '</span> Upgrading…'; }
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring full-upgrade …</span>';
  logActivity('fix', 'System & Kernel: full system upgrade');
  try {
    const j = await postJSON('/api/maintain/upgrade', {});
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring full-upgrade</span>\n' + esc(String(res).slice(0, 2000));
    if (j && j.error) {
      toast('Upgrade failed: ' + j.error, 'err');
      logActivity('fix', 'System & Kernel: upgrade failed', false);
      if (detectReadOnlyMount(j.error)) {
        const repaired = await offerSelfRepair(() => runMaintainUpgrade());
        if (repaired) return;
      }
    } else {
      toast('Full system upgrade completed', 'ok');
      logActivity('fix', 'System & Kernel: full system upgrade completed');
    }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Upgrade error: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('up', 14) + ' Update & Full Upgrade'; }
  }
  loadMaintain(true);
  updateChecks();
  if (typeof loadTroubleshooting === 'function') loadTroubleshooting(true);
}

async function runMaintainRepair() {
  const force = !!(document.getElementById('mntForceInitramfs') || {}).checked;
  const ok = await openModal({
    title: 'Repair system & kernel software',
    text: 'Runs the safe repair sequence through the whitelisted helper:',
    html: '<div class="cmd-list">'
      + '<code>package-db</code> finish interrupted transactions & repair dependencies'
      + '<code>module-map</code> regenerate kernel module dependencies (depmod)'
      + '<code>initramfs</code>' + (force ? ' force rebuild all kernels' : ' rebuild stale/missing images only') + '</div>',
    confirmText: 'Repair now', cancelText: 'Cancel', danger: true, icon: 'wrench',
    note: force ? 'Force rebuild was selected — this can take a long time.' : 'No reboot is performed.'
  });
  if (!ok) return;
  const btn = $('#mntRepairBtn'), term = $('#mntRepairTerm'), out = $('#mntRepairResult');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner">' + icon('refresh', 15) + '</span> Repairing…'; }
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring maintain --repair' + (force ? ' --force-initramfs' : '') + ' …</span>';
  logActivity('fix', 'System & Kernel: repair' + (force ? ' (force initramfs)' : ''));
  try {
    const j = await postJSON('/api/maintain/repair', { force_initramfs: force });
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring maintain --repair</span>\n' + esc(String(res).slice(0, 2000));
    if (j && j.error) {
      toast('Repair failed: ' + j.error, 'err');
      logActivity('fix', 'System & Kernel: repair failed', false);
      if (detectReadOnlyMount(j.error)) {
        const repaired = await offerSelfRepair(() => runMaintainRepair());
        if (repaired) return;
      }
    } else {
      toast('System & kernel repair completed', 'ok');
      logActivity('fix', 'System & Kernel: repair completed');
    }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Repair error: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('check', 14) + ' Repair System & Kernel'; }
  }
  loadMaintain(true);
  updateChecks();
  if (typeof loadTroubleshooting === 'function') loadTroubleshooting(true);
}

async function runMaintainFixAll() {
  const force = !!(document.getElementById('mntFixAllForceInitramfs') || {}).checked;
  const noUpgrade = !!(document.getElementById('mntFixAllNoUpgrade') || {}).checked;
  const d = maintainData || {};
  const n = ((d.fix_all && d.fix_all.count) || 0);
  const ok = await openModal({
    title: 'Fix all system & kernel issues',
    text: n
      ? n + ' issue(s) were detected. This runs the complete, verified fix pipeline:'
      : 'No issues were detected, but Fix All can still re-sync boot state and run the full upgrade. This runs the complete, verified fix pipeline:',
    html: '<div class="cmd-list">'
      + '<code>package-db</code> finish interrupted transactions & repair dependencies'
      + '<code>module-map</code> regenerate kernel module dependencies (depmod)'
      + '<code>initramfs</code>' + (force ? ' force rebuild all kernels' : ' rebuild stale/missing images only')
      + '<code>bootloader</code> refresh the boot menu for the newest kernel'
      + '<code>fwupd</code> refresh firmware metadata (never installs firmware)'
      + (noUpgrade ? '' : '<code>full-upgrade</code> refresh lists + install all updates including new kernels')
      + '<code>autoremove</code> remove orphaned/obsolete packages'
      + '<code>clean</code> clear the package cache'
      + '</div>',
    confirmText: 'Fix everything', cancelText: 'Cancel', danger: true, icon: 'wrench',
    note: noUpgrade ? 'Upgrade skipped — repair, boot, firmware and cleanup only.' : 'A reboot may be required afterwards; no reboot is performed automatically.'
  });
  if (!ok) return;
  mntBusy = true;
  const btn = $('#mntFixAllBtn'), term = $('#mntFixAllTerm'), out = $('#mntFixAllResult');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner">' + icon('refresh', 15) + '</span> Fixing…'; }
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring maintain --fix-all' + (force ? ' --force-initramfs' : '') + (noUpgrade ? ' --no-upgrade' : '') + ' …</span>';
  logActivity('fix', 'System & Kernel: fix-all' + (force ? ' (force initramfs)' : '') + (noUpgrade ? ' (no upgrade)' : ''));
  try {
    const j = await postJSON('/api/maintain/fix-all', { force_initramfs: force, no_upgrade: noUpgrade });
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring maintain --fix-all</span>\n' + esc(String(res).slice(0, 6000));
    if (j && j.error) {
      toast('Fix-all failed: ' + j.error, 'err');
      logActivity('fix', 'System & Kernel: fix-all failed', false);
      if (detectReadOnlyMount(j.error)) {
        const repaired = await offerSelfRepair(() => runMaintainFixAll());
        if (repaired) return;
      }
    } else {
      const warns = (String(res).match(/^\[fail\]/gm) || []).length;
      if (warns) { toast('Fix-all completed with ' + warns + ' warning(s) — see output', 'warn'); }
      else { toast('All system & kernel issues fixed', 'ok'); }
      logActivity('fix', 'System & Kernel: fix-all completed' + (warns ? ' (' + warns + ' warnings)' : ''));
    }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Fix-all error: ' + e.message, 'err');
  } finally {
    mntBusy = false;
    if (btn) { btn.disabled = false; btn.innerHTML = icon('wrench', 14) + ' Fix All System & Kernel Issues'; }
  }
  loadMaintain(true);
  updateChecks();
  if (typeof loadTroubleshooting === 'function') loadTroubleshooting(true);
}

async function applyPerfProfile(profile, btn) {
  const meta = { balanced: ['Balanced', 'schedutil governor, swappiness 10, mq-deadline I/O'],
                 performance: ['Max performance', 'performance governor, swappiness 10, none I/O'],
                 powersave: ['Powersave', 'powersave governor, swappiness 60'] };
  const ok = await openModal({
    title: 'Apply performance profile: ' + meta[profile][0],
    text: meta[profile][1] + '. Applied live and persisted across reboots (sysctl drop-in + systemd unit).',
    html: '<div class="fix-preview"><div class="kv"><span>Revert</span><strong>one click restores your exact original values</strong></div>'
      + '<div class="kv"><span>Scope</span><strong>CPU governor · vm.swappiness · I/O scheduler</strong></div></div>',
    confirmText: 'Apply & persist', cancelText: 'Cancel', icon: 'gauge'
  });
  if (!ok) return;
  const term = $('#mntPerfTerm'), out = $('#mntPerfResult');
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring-perf --apply --profile ' + esc(profile) + ' …</span>';
  logActivity('fix', 'System & Kernel: apply perf profile ' + profile);
  if (btn) { btn.disabled = true; }
  try {
    const j = await postJSON('/api/maintain/perf', { profile });
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring-perf --apply --profile ' + esc(profile) + '</span>\n' + esc(String(res).slice(0, 2000));
    if (j && j.error) { toast('Profile failed: ' + j.error, 'err'); logActivity('fix', 'System & Kernel: perf apply failed', false); }
    else { toast('Performance profile "' + meta[profile][0] + '" applied', 'ok'); logActivity('fix', 'System & Kernel: perf profile applied (' + profile + ')'); }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Perf error: ' + e.message, 'err');
  } finally {
    if (btn) btn.disabled = false;
  }
  loadMaintain(true);
}

async function revertPerf() {
  const ok = await openModal({
    title: 'Revert performance tuning',
    text: 'Restores the exact governor, swappiness and I/O scheduler values captured before tuning, and removes the persistence files.',
    confirmText: 'Revert', cancelText: 'Cancel', danger: true, icon: 'history'
  });
  if (!ok) return;
  const term = $('#mntPerfTerm'), out = $('#mntPerfResult');
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring-perf --revert …</span>';
  logActivity('fix', 'System & Kernel: revert performance tuning');
  try {
    const j = await postJSON('/api/maintain/perf-revert', {});
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring-perf --revert</span>\n' + esc(String(res).slice(0, 2000));
    if (j && j.error) { toast('Revert failed: ' + j.error, 'err'); logActivity('fix', 'System & Kernel: perf revert failed', false); }
    else { toast('Performance tuning reverted', 'ok'); logActivity('fix', 'System & Kernel: perf reverted'); }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Revert error: ' + e.message, 'err');
  }
  loadMaintain(true);
}

// ================================================================
// Dashboard self-update (Help tab): check + one-click update
// ================================================================
let appUpdateData = null;

async function loadAppUpdate(silent) {
  const btn = $('#appUpdateCheckBtn');
  if (!silent && btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner">' + icon('refresh', 13) + '</span> Checking…'; }
  try {
    const r = await apiFetch('/api/app-update');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    appUpdateData = d;
    renderAppUpdate();
  } catch (e) {
    console.error('Failed to check dashboard update:', e);
    const kv = $('#appUpdateKV');
    if (kv) kv.innerHTML = '<div class="empty-state" style="padding:12px"><strong>Update check unavailable</strong><span class="dim">' + esc(e.message || 'API did not respond') + '</span></div>';
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('refresh', 13) + ' Check for updates'; }
  }
}

function renderAppUpdate() {
  const d = appUpdateData;
  const badge = $('#appUpdateBadge');
  if (badge) {
    if (d && d.available) { badge.textContent = 'update available'; badge.className = 'badge warn'; }
    else if (d && d.installed) { badge.textContent = 'up to date'; badge.className = 'badge ok'; }
    else { badge.textContent = '—'; badge.className = 'badge'; }
  }
  const st = $('#appUpdateState');
  if (st) {
    st.textContent = d && d.available ? 'update recommended' : (d && d.installed ? 'current' : 'unavailable');
    st.className = 'pill ' + (d && d.available ? 'warn' : (d && d.installed ? 'ok' : 'fail'));
  }
  const kv = $('#appUpdateKV');
  if (kv) {
    if (!d) { kv.innerHTML = ''; return; }
    kv.innerHTML = '<div class="kv"><span>Installed version</span><strong class="mono">v' + esc(d.installed || '—') + '</strong></div>'
      + '<div class="kv"><span>Latest version</span><strong class="mono">' + (d.latest ? 'v' + esc(d.latest) : '—') + '</strong></div>'
      + '<div class="kv"><span>Update source</span><strong class="mono">' + esc(d.source || 'github') + '</strong></div>'
      + (d.error ? '<p class="dim small" style="margin-top:8px">' + icon('info', 12) + ' ' + esc(d.error) + '</p>' : '');
  }
  const run = $('#appUpdateRunBtn');
  if (run) {
    const canRun = !!(d && d.available && d.helper === 'sudo' || d && d.available && d.helper === 'root');
    run.disabled = !canRun;
    run.title = canRun ? '' : 'Update available check failed or helper missing — try install.sh';
  }
}

async function runAppUpdate() {
  const d = appUpdateData || {};
  const ok = await openModal({
    title: 'Update Monitoring dashboard',
    text: 'Applies the latest version using the installed update.sh --remote. The dashboard service restarts automatically during the update — the page will be briefly unavailable.',
    html: '<div class="fix-preview"><div class="kv"><span>From</span><strong class="mono">v' + esc(d.installed || '—') + '</strong></div>'
      + '<div class="kv"><span>To</span><strong class="mono">' + (d.latest ? 'v' + esc(d.latest) : '—') + '</strong></div></div>',
    confirmText: 'Update & restart', cancelText: 'Cancel', danger: true, icon: 'download',
    note: 'Wait about a minute after confirming, then reload this page.'
  });
  if (!ok) return;
  const btn = $('#appUpdateRunBtn'), term = $('#appUpdateTerm'), out = $('#appUpdateResult');
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner">' + icon('refresh', 14) + '</span> Updating…'; }
  term.hidden = false;
  out.innerHTML = '<span class="dim">$ monitoring-self-update --update …</span>';
  logActivity('fix', 'Dashboard: self-update started');
  try {
    const j = await postJSON('/api/app-update/run', {});
    const res = (j && (j.result || j.error)) || 'Done';
    out.innerHTML = '<span class="dim">$ monitoring-self-update --update</span>\n' + esc(String(res).slice(0, 800));
    if (j && j.error) { toast('Update failed to start: ' + j.error, 'err'); logActivity('fix', 'Dashboard: self-update failed to start', false); }
    else { toast('Update started — dashboard will restart', 'ok'); logActivity('fix', 'Dashboard: self-update started'); }
  } catch (e) {
    out.innerHTML += '\n' + esc(String(e.message || e));
    toast('Update error: ' + e.message, 'err');
  } finally {
    if (btn) { btn.disabled = false; btn.innerHTML = icon('download', 14) + ' Update now'; }
  }
}

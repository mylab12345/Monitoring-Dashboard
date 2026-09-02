# System & Kernel Maintenance Tool — Design & Assessment

**Version 2.6.0** — new dashboard tool for system/kernel software repair,
full system upgrade, and reversible performance tuning.
**Version 2.7.0** — adds the one-click **Fix All System & Kernel Issues**
pipeline, in-dashboard self-update (Help → Dashboard Update), a read-only
performance-health panel, and `update.sh --check` (see
`SYSTEM_KERNEL_IMPROVEMENTS.md` for the full review).

---

## 1. What was evaluated (the "check" the request asked for)

### 1.1 System update (refreshing package lists)
Already present: Overview → Quick Admin → **Update Lists** (`/api/fix update`),
which runs `apt-get update` / `dnf makecache` / `zypper refresh` /
`pacman -Sy` / `apk update` through the validated `monitoring-package` helper.

Gaps found:
- **Stale "Pending Updates" after a mutation.** The upgradable-package list
  is cached for 5 minutes; after running update/upgrade the dashboard kept
  showing the old count. → Fixed: successful mutations now clear the cache
  (`clear_updatable_cache()` + `_cache_clear("_checks"/"_diag"/"maintain")`).
- No combined "refresh + upgrade" quick action. → Added **Full Upgrade**
  (Overview Quick Admin + Maintenance grid + the new tool).

### 1.2 System upgrade (installing everything, including new kernels)
Gaps found:
- `apt upgrade` deliberately **defers new kernels and dependency changes**;
  the old dashboard had no way to run a true full upgrade. → Added the
  `full-upgrade` action:
  - apt: `apt-get full-upgrade -y -qq` (with `apt-get update` first, inside
    the package mutation lock)
  - dnf/yum: `upgrade` (already includes kernels, refreshes metadata)
  - zypper: `dist-upgrade -y --quiet` (with `refresh` first)
  - pacman: `-Syu --noconfirm` (already full)
  - apk: `upgrade --quiet`
- No **reboot-required awareness**. → The new tool reports pending reboot
  state (`/run/reboot-required`) and "running kernel is not the newest
  installed kernel" in both the Diagnose scan and the new tab.

### 1.3 Entire system performance
Gaps found:
- Kernel performance knobs (CPU governor, `vm.swappiness`, I/O scheduler)
  were not visible or controllable from the dashboard.
- No diagnostics surfaced conservative defaults or thermal throttling risk.
- The dashboard itself had stale caches after mutations (see 1.1) and the
  kernel-error check was advisory only.

Addressed by:
- New **Performance Tuning** card (reversible profiles, see §3).
- New Diagnose issues: `perf_tuning` (info), `thermal_throttle` (warning),
  `kernel_update_pending` (info).
- Cache invalidation on every successful mutation so Overview/Checks/Diagnose
  reflect reality immediately.

## 2. New "System & Kernel" dashboard tool

A dedicated sidebar tab (**Diagnose → System & Kernel**) with four cards:

| Card | Endpoint | Privileged helper | What it does |
|---|---|---|---|
| System Upgrade | `POST /api/maintain/upgrade` | `monitoring-package` | refresh + full-upgrade incl. kernels, honest exit status |
| System & Kernel Repair | `POST /api/maintain/repair` | `monitoring-maintain` | package-db → module-map → initramfs repair sequence |
| Performance Tuning | `POST /api/maintain/perf` / `perf-revert` | `monitoring-perf` | governor + swappiness + I/O scheduler, persisted, reversible |
| Kernel & Firmware | `GET /api/maintain` | read-only checks | running/newest kernel, reboot state, fwupd, kernel errors |

The status endpoint (`GET /api/maintain`, cached 30 s) merges helper JSON
with app-side facts (distro, uptime, pending updates, temp) and degrades
gracefully when a helper is missing (`helper: "missing"`, never a 500).

## 3. Security model (unchanged principles)

- New privileged helpers `privileged/monitoring-maintain` and
  `privileged/monitoring-perf` are whitelisted in four places that must stay
  in sync:
  1. `monitor/commands.py::privileged_tool()` allow-list
  2. `monitor/privileges.py::_PRIVILEGE_HELPERS` (reported by `/api/privileges`)
  3. `sudoers/monitoring` (NOPASSWD entries, validated with `visudo` by the
     installer)
  4. `update.sh` / `install.sh` install every `privileged/monitoring-*` glob,
     so new helpers are picked up automatically
- Helpers accept only fixed argv shapes, validate every value against
  allow-lists, never invoke a shell, and print one result line per step so a
  partially failed repair is never reported as "Done".
- `monitoring-perf` stores the pre-tuning values once in
  `/etc/monitoring-perf.orig` and never overwrites them; `--revert` restores
  them exactly and removes the sysctl drop-in, config and systemd unit.
- Mutating endpoints are rate-limited (5–10/min) and token-gated by the
  global auth; every action is written to the audit log.

## 4. What the repair sequence actually runs (per distro)

| Step | Debian/Ubuntu | RHEL/Fedora | openSUSE | Arch | Alpine |
|---|---|---|---|---|---|
| package-db | `dpkg --configure -a` → `apt-get install -f -y` → `dpkg --configure -a` | `rpm --rebuilddb` | `rpm --rebuilddb` | `pacman -Dk` | `apk fix` |
| module-map | `depmod -a` | `depmod -a` | `depmod -a` | `depmod -a` | `depmod -a` (if present) |
| initramfs | `update-initramfs -u -k <ver>` | `dracut --force --kver <ver>` | `dracut --force --kver <ver>` | — (no initramfs) | `mkinitfs -o /boot/initramfs-<ver> <ver>` |

The initramfs step only rebuilds kernels whose image is **missing or older
than the newest module** — unless `--force-initramfs` is requested in the UI.
A kernel that was updated but never finished its initrd generation is the
classic cause of "boot fails after update", so this is the kernel-software
fix the request asked for, done safely.

## 5. Files changed

- `privileged/monitoring-maintain` (new), `privileged/monitoring-perf` (new),
  `privileged/monitoring-package` (+`full-upgrade` action)
- `monitor/maintain.py` (new blueprint), `monitor/fixes.py` (refactored
  `run_fix_action()` shared by `/api/fix` and the tool; cache invalidation),
  `monitor/packages.py` (+`full-upgrade`, `clear_updatable_cache()`),
  `monitor/diagnostics.py` (+3 issues), `monitor/commands.py`,
  `monitor/privileges.py`, `monitor/__init__.py`
- `templates/index.html` (new tab), `static/js/maintain.js` (new),
  `static/js/checks.js` (+Full Upgrade in maintenance grid),
  `static/js/bootstrap.js` (+palette entry, quick-admin icon),
  `static/js/tabs.js`, `static/css/dashboard.css`
- `sudoers/monitoring` (+2 helper entries), `VERSION` (2.6.0),
  `tests/test_app.py` (+8 tests), `README.md`

## 6. Upgrade path

```bash
cd <checkout> && sudo ./update.sh        # installs helpers + sudoers + app, restarts service
```

No migration needed: the new blueprint loads defensively and the UI only
shows the new tab when `monitor/maintain.py` registered successfully.

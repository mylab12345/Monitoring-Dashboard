# System Update, System Upgrade & System Performance — Review & Improvements

Scope: how the Monitoring dashboard updates itself, upgrades the OS (including
kernels) and tunes performance — plus the new one-click **Fix All System &
Kernel Issues** tool. Implemented in v2.7.0.

---

## 1. Where things stood (review findings)

| Area | Before (v2.6.0) | Gap found |
|---|---|---|
| Dashboard self-update | CLI only (`monitoring update` / `sudo ./update.sh --remote`); no way to know an update exists from the UI | No in-dashboard update check or one-click update; `update.sh` had no read-only check mode |
| OS update / upgrade | `System Upgrade` card → `monitoring-package` (`apt update`+`full-upgrade`, `dnf upgrade`, `zypper dist-upgrade`, `pacman -Syu`, `apk upgrade`); pending list + reboot warning | Solid, but no combined flow that also fixes the boot chain |
| Kernel repair | `System & Kernel Repair` (`--repair`): package-db, module map, initramfs | No bootloader refresh, no firmware-metadata refresh, no orphan/cache cleanup |
| Performance | `Performance Tuning` profiles (governor / swappiness / I/O scheduler) with one-click revert | No read-only performance-health view (load, swap) and no guidance connecting the knobs to the live machine |
| "Fix all" | Only the Troubleshooting tab's diagnostics-based fix-all (packages/logs/services/zombies) | Nothing that fixed **system & kernel** issues end-to-end in one click |

## 2. What was improved

### 2.1 New tool: "Fix All System & Kernel Issues" (System & Kernel tab)

A new full-width card at the top of the System & Kernel tab. It scans for
detected issues (badge + per-issue list) and runs the complete, verified fix
pipeline in one click:

1. **package-db** — finish interrupted transactions / repair dependencies
   (`dpkg --configure -a` + `apt -f install`, `rpm --rebuilddb`,
   `pacman -Dk`, `apk fix`)
2. **module-map** — regenerate the kernel module dependency database
   (`depmod -a`)
3. **initramfs** — rebuild missing/stale initramfs images (optional
   `--force-initramfs` rebuilds all kernels)
4. **bootloader** — refresh the boot menu for the newest kernel
   (`update-grub` / `grub2-mkconfig` / `grub-mkconfig`)
5. **fwupd** — refresh firmware metadata (`fwupdmgr refresh --force`; firmware
   is never installed)
6. **package-refresh** — refresh package lists
7. **full-upgrade** — full system upgrade including new kernels
   (skippable via "Skip system upgrade" checkbox → `--no-upgrade`)
8. **autoremove** — remove orphaned/obsolete packages
9. **clean** — clear the package cache

Every step prints its own `[ok]/[skip]/[fail]` line; the exit code is 0 only
when all *required* steps succeeded (optional cleanup steps that fail are
counted as warnings, never hidden). Backed by:

- `privileged/monitoring-maintain --fix-all [--force-initramfs] [--no-upgrade]`
- `POST /api/maintain/fix-all` (rate-limited, token-gated, audited)
- Extended `--check` output (`fix_all.issues`) so the UI can show
  "3 issues found" and enable/disable the button honestly.

### 2.2 Dashboard self-update ("system update" for Monitoring itself)

- **`update.sh --check`** — read-only, no root: prints
  `monitoring-update-check installed=… latest=… update_available=0|1`
- **New whitelisted helper `monitoring-self-update`**:
  - `--check` — installed vs. latest GitHub version (JSON; degrades gracefully offline)
  - `--update [--branch …]` — applies the update via the installed
    `update.sh --remote` in a **detached session**, so the HTTP response
    completes before the service restarts itself
- **Help → Dashboard Update card** — shows installed/latest versions, an
  "update available" badge, and an **Update now** button
- Whitelisted in `sudoers/monitoring` and `monitor/commands.py`
  (`privileged_tool`), installed automatically by `install.sh`/`update.sh`
  (they glob `privileged/monitoring-*`).

### 2.3 System performance improvements

- **Performance-health panel** on the Performance Tuning card (read-only, no
  sudo): load average (1/5/15), swap usage, plus concrete tuning hints that
  connect the knobs to the live machine — e.g. high `vm.swappiness`,
  all-CPUs-on-`performance`, legacy I/O schedulers, load exceeding CPU count,
  newer kernel installed but not active.
- The existing reversible profiles (balanced / max performance / powersave)
  remain untouched; Fix All deliberately does **not** change tuning knobs.

### 2.4 Auto-heal of the read-only service namespace (exit 78)

When the installed `monitoring.service` is an **old unit** (pre-`ReadWritePaths=/usr /etc /boot /efi`),
every package operation fails with exit 78 (`required filesystem is read-only`)
because `ProtectSystem=full` makes /usr, /etc and /boot read-only in the
service mount namespace. v2.7.1 makes the dashboard fix this by itself:

- `monitoring-package` and `monitoring-maintain --repair/--fix-all` return the
  same exit-78 contract with guidance (pre-checked before any transaction).
- The fix endpoints (`/api/fix`, `/api/maintain/upgrade`, `/api/maintain/repair`,
  `/api/maintain/fix-all`, `/api/troubleshooting/fix-all`) detect exit 78 and
  automatically run `monitoring-self-repair --apply` (patch unit + daemon-reload
  + scheduled restart through systemd), returning `read_only_mount` +
  `repair_scheduled` flags.
- The UI waits for the dashboard to come back after the restart, then
  **re-runs the failed action once** automatically (`autoRepairAndRetry`).
- Long helper output is no longer tail-truncated: the diagnostic head
  ("required filesystem is read-only: /usr, /etc") is preserved, and
  `detectReadOnlyMount()` also matches `ReadWritePaths` / `exit 78`.

### 2.5 Honesty & safety guarantees (kept from the existing design)

- All mutating work goes through whitelisted, argv-validating privileged
  helpers — never a shell, never `sudo ALL`.
- Every step's result is reported; the UI can never show success for an
  operation that did not run.
- Read-only checks for status; mutating steps only touch well-known system
  locations with fixed command lists.
- The self-update is detached and non-blocking; the dashboard restarts itself.

## 3. How to verify

```bash
bash tests/run_all.sh          # full regression suite (helpers, API, JS, syntax)
sudo bash install.sh           # (re)install with the new helper + sudoers entry
./update.sh --check            # read-only update check
```

Then open **System & Kernel** → run *Fix All System & Kernel Issues*, and
**Help** → *Dashboard Update*.

## 4. Notes / future ideas (not implemented)

- Streaming (SSE) progress for long fix-all runs instead of one response.
- Scheduled/automatic updates (opt-in) and unattended-upgrades integration.
- Firmware upgrades (`fwupdmgr update`) behind an extra explicit confirmation.
- zram / transparent-hugepage tuning profiles as an opt-in "advanced" set.
- Boot-time performance comparison before/after profile changes.

# Overview Tab — Audit & Fixes (v2.4.2)

Audit of the **Overview** tab and the fixes applied. Every issue was reproduced
against a running instance before the fix and re-verified after.

**Headline:** the dashboard was completely non-functional. A JavaScript syntax
error aborted the entire inline app script at parse time, so *no* tab rendered
any data. That bug was present on `main`.

---

## Fixed

### P0 — Syntax error killed the whole dashboard

`templates/index.html` → `chartBoxEvents()`. A duplicated tooltip loop had been
pasted outside its `for` block, leaving an orphaned `continue` and a stray `}`:

```
SyntaxError: Illegal continue statement: no surrounding iteration statement
```

A syntax error is raised at **parse** time, so not one statement executed:
`updateStatus()`, `showTab()`, `updateChecks()`, the chart, the command palette,
theme switching and the clock never ran. Every metric card showed `--`, every
panel stayed a grey skeleton, and the sidebar was inert.

**Fix:** removed the four orphaned lines and restored the `positionTip()` call
so the tooltip tracks the cursor (it was previously unreachable).

**Guard:** `tests/run_all.sh` now extracts the inline script and runs
`node --check` on it; `FrontendIntegrity.test_inline_script_parses` does the
same under unittest. Both were confirmed to **fail** on the pre-fix file and
pass after — no static string check can catch this class of bug.

### P1 — "Vacuum Journal" silently did nothing, then reported success

The button posted `vacuum-journal`; `api_fix()` only implements `clear-logs`, so
it fell through to `200 {"result": "Unknown action"}`. The UI only inspects
`error`, so it toasted **"Completed"** and wrote a success line to the audit
trail for a privileged action that never ran.

**Fix (three layers):**
- Button now posts `clear-logs`; `FIX_ALIASES` also maps `vacuum-journal` → `clear-logs`.
- `api_fix()` validates against `FIX_ACTIONS` and returns **400 `{"error": …}`**
  for anything unknown (no more success-shaped failures).
- `postJSON()` converts any non-2xx into `{error}` even when the body omits the
  key, so callers that only test `j.error` cannot misread a 4xx/5xx as success.

### P2 — Health score double-penalised failures

`warns` already included failures, so each `fail` cost 20 + 40 = 60 points.

| warn | fail | before | after |
|---:|---:|---:|---:|
| 0 | 1 | 40 % | **60 %** |
| 1 | 1 | 20 % | **40 %** |
| 2 | 1 | **0 %** | **20 %** |

Two warnings plus one failure read as *0 % — total system failure* on a
basically healthy box. The badge also lumped both into an amber "N issues"; it
now reads `2 warnings · 1 failure` and turns red when any check fails. The
sidebar dot still counts all issues.

### P3 — Metric values were rounded to whole numbers

All three `animNum()` call sites omitted the `dec` argument, and
`toFixed(undefined)` defaults to 0 digits — so `7.4 %` rendered as `7` while the
delta chip beside it read `▲ +0.4`, contradicting itself. `animNum()` now
defaults to one decimal and guards non-finite input.

### P4 — Listening Ports leaked raw `ss` internals

`cleanProc()` stripped one prefix and one suffix, so real output became
`python,pid=1415,fd=9))` and multi-listener rows were mangled. It now parses
every `("name",pid=N)` pair into `python (1415)`, de-duplicates, and falls back
to a light cleanup if the format changes.

### P5 — Top Hogs over-fetched and hid memory hogs

Requested `limit=6`, which returns **10** rows (the API returns a CPU∪memory
union at `limit*2`) to render 5. It also sorted by CPU only, so a process at
90 % RAM / 0 % CPU never appeared despite being in the payload. Now requests
`limit=5` and has a **CPU / MEM toggle** (persisted); the badge shows the active
key and the sorted metric is emphasised per row.

### P6 — Non-root filesystems were never alerted on

Alerting only read `status.disk_percent` (always `/`), so a full `/home` raised
nothing. Added `checkDiskAlerts()`, which latches per mount and emits recovery
events. The Disk card note names the worst mount, but only once it reaches the
warn threshold.

### P7 — Accessibility

- Sparkline and chart canvases: `role="img"` + descriptive `aria-label`.
- Metric values: accessible names (deliberately **not** `aria-live` — a value
  updating every 3 s would flood the speech queue).
- Icon-only refresh buttons: real `aria-label`s (`title` alone is unreliable).
- Sort toggle exposes `aria-pressed`; toolbars are labelled groups.

Not done: a full `role="tablist"` conversion. The sidebar is four separate
`<nav>` groups, so correct tab semantics need a restructure — worth doing, but
too invasive to bundle with a hotfix.

### P8 — Polling

- `updateChecks` was the **only** timer without a `document.hidden` guard, and
  it is the most expensive endpoint (`apt`/`dpkg`/`systemctl`/`journalctl`). Now guarded.
- Process polling now also requires the Processes tab to be active.
- Boot loaded all five hidden tabs (~11 requests); now Overview-only, with the
  rest lazy-loading via `TAB_LOADERS` on first visit.
- Added a `visibilitychange` catch-up refresh, and resize redraws are coalesced
  into one `requestAnimationFrame`.

### P9 — Panel error states

Ports rendered an API failure identically to a genuinely empty list. Errors are
now distinguished from empty everywhere, and Health / Hogs / Ports all show the
reason plus a **Retry** button (matching Disks, which already did this).

### P10 — Misc

- Quick actions use the shared SVG icon set instead of emoji, and the palette
  hint respects the platform (`Ctrl` vs `⌘`) instead of hardcoding `⌘K`.
- Destructive quick actions (Clean Cache, Vacuum Journal) now confirm first, in
  both the toolbar and Maintenance Actions.
- Top Hogs kill buttons bind via `addEventListener` + `data-pid` instead of an
  inline `onclick` built from the process name. `jsq()` escapes `'` to `&#39;`,
  but the HTML parser decodes entities *before* the JS parser runs, so a process
  named `x');alert(1)//` (14 chars — fits Linux's 15-char `comm` limit) escaped
  the string literal. Low severity (needs a local attacker who can name a
  process) but a genuine injection shape in a UI that runs privileged actions.
- Inline styles moved into `.quick-toolbar` / `.hog-*` CSS classes.

---

## Verification

| Suite | Result |
|---|---|
| `tests/run_all.sh` (unit + security + JS parse) | **48 tests, all pass** |
| Live DOM render (jsdom against the running API) | **31/31** |
| Health-score & disk-alert logic (injected fixtures) | **19/19** |
| All 10 tabs + 21 interactions + chart hover sweep | **0 runtime errors** |
| API endpoints | 12/12 → 200 |
| Token auth | 401 without, 200 with |

Negative control: served the pre-fix template from a clean server and confirmed
the suites reproduce the original `SyntaxError` with every metric stuck at `--`.

Note: Flask caches templates, so a server restart is required when swapping
`templates/index.html` for before/after comparisons.

---

# Maintenance Actions — Read-Only Filesystem Guard & Broken-Package Recovery (v2.4.3)

Field report: clicking **"Install available updates"** (the `upgrade-packages`
fix) ran `apt-get upgrade` while a system filesystem was mounted read-only.
dpkg died mid-unpack:

```
error processing archive coreutils_9.4-3ubuntu6.3_amd64.deb (--unpack):
unable to create '/usr/bin/[.dpkg-new': Read-only file system
```

That stranded `coreutils` as **half-installed**, and the existing "Fix Broken"
action (`dpkg --configure -a`) cannot repair that state:

```
package coreutils is not ready for configuration
cannot configure (current status 'half-installed')
```

## Fixed

### P0 — Upgrade ran blindly on a read-only filesystem

`privileged/monitoring-package` now runs a **write preflight** before any
mutating action (update/upgrade/autoremove/clean/fix-broken): every path the
package manager must write to (`/usr`, `/var/lib/dpkg`, `/var/lib/apt`,
`/var/cache/apt`; per-manager equivalents) is checked via mount flags
(`/proc/mounts` ro detection) **and** `statvfs(ST_RDONLY)`/`access(W_OK)` —
the flag check matters because `access(W_OK)` alone returns True for root.
When a path is read-only the helper refuses with **exit code 3**, prints the
offending paths and the remount instructions, and touches nothing.

### P1 — "Fix Broken" could not repair half-installed packages

The apt `fix-broken` action is now the full operator-grade sequence:
`dpkg --audit` → `dpkg --configure -a` → enumerate half-installed packages
(`dpkg-query -f='${db:Status-Status} ${binary:Package}'`, with a `dpkg -l`
fallback) → `dpkg --remove --force-remove-reinstreq` per package (clears the
broken registration without deleting remaining files) → `apt-get install -f`
→ `apt-get install --reinstall` for each recovered package (bounded at 20 per
run, the rest reported for manual review). The apt `upgrade` action also runs
`dpkg --configure -a` first and aborts with guidance instead of compounding
the breakage.

### P2 — No way to grant the missing permission from the dashboard

New privileged helper **`monitoring-remount-rw`** (whitelisted in
`sudoers/monitoring`, reported by `/api/privileges` and `monitoring
check-privileges`) remounts read-only block filesystems at `/`, `/usr`,
`/var` read-write, re-verifies the mount flags afterwards, and refuses to
touch pseudo filesystems, snapshot mounts or `/boot`/EFI. Exposed as the
**"Remount RW"** maintenance button and as the one-click fix for the new
critical **"Filesystem Read-Only"** diagnostic issue (with post-fix
verification).

### P3 — Backend misreported failures and went stale

- `/api/fix` and `/api/troubleshooting/fix-all` now translate helper exit
  codes: a read-only refusal (rc 3) is reported as a failure with the
  offending mounts and the exact `mount -o remount,rw …` command — never as
  "Completed".
- Every package mutation invalidates the cached updatable-package list, so
  `/api/updates`, `/api/checks` and the Diagnose tab reflect the new state
  immediately instead of up to 5 minutes later.
- Broken-package detection now uses `dpkg --audit` **plus** the
  half-installed/unpacked state scan, and `/api/checks` gains a **"Disk
  Writable"** health check.

### P4 — `update.sh` deployed helpers from stale sources

When run from the installed copy (`monitoring update`), `update.sh` installed
the privileged helpers and sudoers fragment **before** pulling the new
sources from GitHub, so updated helpers were silently kept old. The pull now
happens first, and the updater re-verifies the service account's passwordless
sudo after reinstalling the sudoers fragment.

## Verification

- `tests/run_all.sh`: helper validation includes the new exit-code-3
  preflight (exercised against a real read-only mount from `/proc/mounts`
  when one exists), `_fs_readonly` unit tests, remount-helper usage
  validation, sudoers-coverage checks for every privileged helper, and a
  FIX_META ↔ `FIX_ACTIONS` sync test.
- `monitoring-package --manager apt --action fix-broken` verified as a no-op
  on a healthy dpkg database; preflight verified to return rc 3 against a
  read-only mount without invoking the package manager.

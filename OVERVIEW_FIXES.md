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

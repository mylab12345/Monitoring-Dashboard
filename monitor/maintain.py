"""System & Kernel tool: repair, full system upgrade, performance tuning.

The dashboard's new one-click tool for fixing system and kernel software
problems. It drives three whitelisted privileged helpers:

* ``monitoring-maintain --check/--repair`` — package-database repair,
  kernel module map regeneration and initramfs rebuild for kernels whose
  initrd is missing or stale.
* ``monitoring-package`` (via fixes.run_fix_action) — refresh + full system
  upgrade including new kernels (apt full-upgrade / dnf upgrade / zypper
  dist-upgrade / pacman -Syu / apk upgrade).
* ``monitoring-perf`` — reversible performance tuning (CPU governor,
  vm.swappiness, I/O scheduler) with persistence and one-click revert.

Every mutating endpoint is rate-limited, token-gated by the global auth and
records an audit event; every response carries the helper's exit status so
the UI can never report success for an operation that did not execute.
"""
import json
import os
import re
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from .commands import PASSWORDLESS_SUDO, privileged_tool, run_privileged
from .common import APP_VERSION, _audit, _cache_clear, _cached
from .fixes import run_fix_action
from .metrics import _pretty_distro, _read_temp_c
from .packages import _updatable_packages
from .security import rate_limit

bp = Blueprint("maintain", __name__)

MAINTAIN_HELPER = "monitoring-maintain"
PERF_HELPER = "monitoring-perf"
SELF_UPDATE_HELPER = "monitoring-self-update"
GOVERNOR_PROFILES = ("balanced", "performance", "powersave")
IOSCHED_ALLOWED = ("none", "noop", "deadline", "cfq", "mq-deadline", "kyber",
                   "bfq")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _helper_path(name):
    try:
        return privileged_tool(name)
    except ValueError:
        return ""


def _run_helper(name, args=None, timeout=120):
    """Run a whitelisted helper, returning (code, out, err) even on failure."""
    if not os.path.isfile(_helper_path(name)):
        return -1, "", f"{name} helper is not installed — run install.sh or update.sh (sudo) and retry"
    return run_privileged(name, args, timeout=timeout)


def _maintain_status():
    """Merge helper status + app-side facts into one JSON report.

    Never raises: a broken or missing helper degrades to a status payload
    with helper: "missing"/"denied" and the app-side facts still populated.
    """
    report = {
        "helper": "missing",
        "helper_path": _helper_path(MAINTAIN_HELPER),
        "kernel": None,
        "reboot_required": False,
        "reboot_packages": [],
        "initramfs": None,
        "module_map": None,
        "packages": None,
        "fwupd": None,
        "perf": None,
        "updates": {"manager": None, "count": 0, "packages": []},
        "system": None,
        "capabilities": {"root": os.geteuid() == 0,
                         "sudo": bool(PASSWORDLESS_SUDO),
                         "helper_ready": False},
        "timestamp": None,
    }
    path = _helper_path(MAINTAIN_HELPER)
    if path and os.path.isfile(path):
        report["helper"] = ("root" if os.geteuid() == 0
                            else "sudo" if PASSWORDLESS_SUDO else "no-sudo")
        code, out, err = run_privileged(MAINTAIN_HELPER, ["--check"], timeout=60)
        if code == 0:
            try:
                report.update(json.loads(out or "{}"))
            except (ValueError, TypeError):
                report["helper_error"] = (out or err or "")[:300]
        else:
            report["helper"] = "denied"
            report["helper_error"] = ((err or "") + (out or "")).strip()[:300]
    report["capabilities"]["helper_ready"] = report["helper"] in ("root", "sudo")

    # App-side facts that do not need the helper.
    updates = _updatable_packages()
    report["updates"] = {
        "manager": updates.get("manager"),
        "count": updates.get("count", 0),
        "packages": updates.get("packages", [])[:20],
    }
    try:
        import time as _time
        import psutil
        report["system"] = {
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "arch": os.uname().machine,
            "uptime_s": int(_time.time() - psutil.boot_time()),
            "temp_c": _read_temp_c(),
        }
    except Exception:
        pass
    report["perf"] = _perf_status()
    report["perf"]["health"] = _perf_health(report)
    report["fix_all"] = _fix_all_report(report)
    report["timestamp"] = datetime.now().isoformat()
    return report


def _fix_all_report(report):
    """Merge helper-side and app-side detected issues for the Fix All tool.

    Helper-side issues come from monitoring-maintain --check (package-db,
    module-map, initramfs, bootloader, fwupd); app-side facts add pending
    updates, a required reboot and an un-activated newer kernel. The UI only
    enables the one-click Fix All button when this list is non-empty.
    """
    issues = []
    seen = set()

    def add(issue_id, label, severity):
        if issue_id in seen:
            return
        seen.add(issue_id)
        issues.append({"id": issue_id, "label": label,
                       "severity": severity})

    for item in ((report.get("fix_all") or {}).get("issues") or []):
        if isinstance(item, dict) and item.get("id") and item.get("label"):
            add(str(item["id"])[:40], str(item["label"])[:180],
                str(item.get("severity") or "warn")[:10])

    pkgs = report.get("packages") or {}
    ini = report.get("initramfs") or {}
    kern = report.get("kernel") or {}
    if pkgs.get("needs_repair"):
        add("package-db", "Package database needs repair", "error")
    if (report.get("module_map") or {}).get("stale"):
        add("module-map", "Kernel module map is stale", "error")
    if ini.get("status") == "needs-rebuild":
        stale = (ini.get("stale") or [])[:3]
        add("initramfs",
            "Initramfs missing/stale for " + (", ".join(stale) if stale
                                              else "installed kernels"),
            "error")
    if (report.get("bootloader") or {}).get("needs_refresh"):
        add("bootloader", "Boot menu not refreshed for the newest kernel",
            "warn")
    if (report.get("fwupd") or {}).get("refresh_recommended"):
        add("fwupd", "Firmware metadata should be refreshed", "info")
    if report.get("reboot_required"):
        reboot_pkgs = (report.get("reboot_packages") or [])[:3]
        add("reboot", "Reboot required"
            + (f" ({', '.join(reboot_pkgs)})" if reboot_pkgs else ""), "warn")
    if (kern.get("installed") and kern.get("running")
            and kern["installed"]
            and kern["installed"][0] != kern["running"]):
        add("kernel-activate",
            f"Newer kernel installed ({kern['installed'][0]}) — reboot to "
            f"activate it", "warn")
    updates = report.get("updates") or {}
    if updates.get("count"):
        add("updates", f"{updates['count']} package update(s) available",
            "info")
    return {"issues": issues, "count": len(issues),
            "healthy": len(issues) == 0}


def _perf_health(report):
    """Read-only system-performance health facts and hints for the perf card.

    These are cheap /proc reads — no privileged access, no side effects. The
    hints connect the knobs the perf helper exposes to the live state of the
    machine so the operator can see WHY a tuning change may help.
    """
    facts = {"load": None, "cpus": None, "swap_total_mb": None,
             "swap_used_mb": None, "swap_used_pct": None, "hints": []}
    try:
        with open("/proc/loadavg", "r") as fh:
            parts = fh.read().split()
        facts["load"] = [float(p) for p in parts[:3]] if len(parts) >= 3 else None
    except (OSError, ValueError):
        pass
    try:
        import psutil
        facts["cpus"] = psutil.cpu_count() or None
        sm = psutil.swap_memory()
        if sm and sm.total:
            facts["swap_total_mb"] = round(sm.total / 1048576, 1)
            facts["swap_used_mb"] = round(sm.used / 1048576, 1)
            facts["swap_used_pct"] = round(100.0 * sm.used / sm.total, 1)
    except Exception:
        pass

    perf = report.get("perf") or {}
    swappiness = perf.get("swappiness")
    if isinstance(swappiness, int) and swappiness > 60:
        facts["hints"].append(
            f"vm.swappiness is high ({swappiness}) — the 'balanced' profile "
            "sets 10, which keeps interactive apps in RAM instead of paging.")
    governors = perf.get("governors") or []
    if governors and set(governors) == {"performance"}:
        facts["hints"].append(
            "All CPUs run the 'performance' governor — 'balanced' "
            "(schedutil/ondemand) usually gives the same speed with much "
            "lower power draw and heat.")
    schedulers = perf.get("schedulers") or []
    legacy = [s for s in schedulers if s in ("cfq", "deadline", "noop")]
    if legacy:
        facts["hints"].append(
            "Legacy I/O scheduler(s) in use: " + ", ".join(sorted(legacy))
            + " — the modern blk-mq schedulers ('mq-deadline' or 'none') are "
            "generally faster on NVMe/SSD.")
    load = facts["load"]
    if load and facts["cpus"] and load[0] > facts["cpus"]:
        facts["hints"].append(
            f"Load average {load[0]:.1f} exceeds {facts['cpus']} CPU(s) — "
            "check the Processes tab for the top consumers before tuning.")
    kern = report.get("kernel") or {}
    if kern.get("installed") and kern.get("running") \
            and kern["installed"] and kern["installed"][0] != kern["running"]:
        facts["hints"].append(
            "A newer kernel is installed but not active — a reboot applies "
            "its performance fixes and features.")
    return facts


def _perf_status():
    """Current performance-knob status from the monitoring-perf helper."""
    if not os.path.isfile(_helper_path(PERF_HELPER)):
        return {"helper": "missing"}
    code, out, err = run_privileged(PERF_HELPER, ["--check"], timeout=30)
    if code != 0:
        return {"helper": "denied", "error": ((err or "") + (out or "")).strip()[:200]}
    try:
        return json.loads(out or "{}")
    except (ValueError, TypeError):
        return {"helper": "broken", "error": (out or err or "")[:200]}


def _clear_related_caches():
    for key in ("maintain", "_checks", "_diag"):
        _cache_clear(key)


@bp.route("/api/maintain")
@rate_limit("60 per minute")
def api_maintain():
    """Full status for the System & Kernel tool (cached 30s)."""
    try:
        return jsonify(_cached("maintain", 30, _maintain_status))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "helper": "error"}), 500


@bp.route("/api/maintain/repair", methods=["POST"])
@rate_limit("5 per minute")
def api_maintain_repair():
    """Run the safe system & kernel repair sequence."""
    data = request.get_json(silent=True) or {}
    force_initramfs = bool(data.get("force_initramfs"))
    if not os.path.isfile(_helper_path(MAINTAIN_HELPER)):
        return jsonify({"error": "monitoring-maintain helper is not installed "
                                 "— run install.sh or update.sh (sudo) and retry"}), 503
    args = ["--repair"] + (["--force-initramfs"] if force_initramfs else [])
    code, out, err = run_privileged(MAINTAIN_HELPER, args, timeout=1200)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _clear_related_caches()
        _audit("maintain-repair", outcome="ok", force_initramfs=force_initramfs)
        return jsonify({"result": message[-2000:] or "Repair completed",
                        "exit_code": code, "action": "repair"})
    _audit("maintain-repair", outcome="failed", exit_code=code,
           force_initramfs=force_initramfs)
    return jsonify({"error": f"repair failed (exit {code}): "
                             f"{message[-2000:] or 'see helper output'}",
                    "exit_code": code, "action": "repair"}), 500


@bp.route("/api/maintain/upgrade", methods=["POST"])
@rate_limit("5 per minute")
def api_maintain_upgrade():
    """Refresh package lists and run a full system upgrade (incl. kernels)."""
    payload, status = run_fix_action("full-upgrade")
    _clear_related_caches()
    _audit("maintain-upgrade", outcome="ok" if status == 200 else "failed")
    return payload, status


@bp.route("/api/maintain/fix-all", methods=["POST"])
@rate_limit("3 per minute")
def api_maintain_fix_all():
    """One-click fix of every detected system & kernel software issue.

    Drives ``monitoring-maintain --fix-all``: package-db repair, kernel
    module map, initramfs rebuild, bootloader refresh, firmware metadata
    refresh, package-list refresh, full system upgrade (incl. new kernels;
    skippable), orphaned-package removal and cache cleanup. Every step is
    reported as its own [ok]/[skip]/[fail] line so the UI can never claim a
    fix ran when it did not.
    """
    data = request.get_json(silent=True) or {}
    force_initramfs = bool(data.get("force_initramfs"))
    no_upgrade = bool(data.get("no_upgrade"))
    if not os.path.isfile(_helper_path(MAINTAIN_HELPER)):
        return jsonify({"error": "monitoring-maintain helper is not installed "
                                 "— run install.sh or update.sh (sudo) and retry"}), 503
    args = ["--fix-all"]
    if force_initramfs:
        args.append("--force-initramfs")
    if no_upgrade:
        args.append("--no-upgrade")
    code, out, err = run_privileged(MAINTAIN_HELPER, args, timeout=2400)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _clear_related_caches()
        _audit("maintain-fix-all", outcome="ok",
               force_initramfs=force_initramfs, no_upgrade=no_upgrade)
        return jsonify({"result": message[-6000:] or "Fix-all completed",
                        "exit_code": code, "action": "fix-all"})
    _audit("maintain-fix-all", outcome="failed", exit_code=code,
           force_initramfs=force_initramfs, no_upgrade=no_upgrade)
    return jsonify({"error": f"fix-all failed (exit {code}): "
                             f"{message[-6000:] or 'see helper output'}",
                    "exit_code": code, "action": "fix-all"}), 500


# ---------------------------------------------------------------------------
# Dashboard self-update (system update for Monitoring itself)
# ---------------------------------------------------------------------------
def _app_update_status():
    """Installed vs. latest Monitoring version, via the whitelisted helper.

    Read-only; never raises — a missing helper or offline host degrades to a
    status payload the UI can render instead of a 500.
    """
    info = {
        "helper": "missing",
        "helper_path": _helper_path(SELF_UPDATE_HELPER),
        "installed": APP_VERSION,
        "latest": None,
        "available": False,
        "source": "github",
        "home": None,
        "error": None,
        "checked_at": None,
    }
    path = _helper_path(SELF_UPDATE_HELPER)
    if path and os.path.isfile(path):
        info["helper"] = ("root" if os.geteuid() == 0
                          else "sudo" if PASSWORDLESS_SUDO else "no-sudo")
        code, out, err = run_privileged(SELF_UPDATE_HELPER, ["--check"],
                                        timeout=30)
        if code == 0:
            try:
                data = json.loads(out or "{}")
                for key in ("installed", "latest", "available", "source",
                            "home", "error"):
                    if key in data:
                        info[key] = data[key]
            except (ValueError, TypeError):
                info["error"] = (out or err or "")[:200]
        else:
            info["helper"] = "denied"
            info["error"] = ((err or "") + (out or "")).strip()[:200]
    if info.get("installed") is None:
        info["installed"] = APP_VERSION
    info["checked_at"] = datetime.now().isoformat()
    return info


@bp.route("/api/app-update")
@rate_limit("20 per minute")
def api_app_update():
    try:
        return jsonify(_cached("app-update", 60, _app_update_status))
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "helper": "error"}), 500


@bp.route("/api/app-update/run", methods=["POST"])
@rate_limit("1 per minute")
def api_app_update_run():
    """Start a dashboard self-update through the installed update.sh --remote.

    The helper launches the update in a detached session and returns
    immediately: update.sh restarts the service itself, so this HTTP response
    completes before the dashboard process is replaced. The UI then asks the
    operator to wait and reload.
    """
    data = request.get_json(silent=True) or {}
    branch = data.get("branch")
    if branch is not None:
        if not isinstance(branch, str) or not BRANCH_RE.fullmatch(branch):
            return jsonify({"error": "invalid branch name"}), 400
    if not os.path.isfile(_helper_path(SELF_UPDATE_HELPER)):
        return jsonify({"error": "monitoring-self-update helper is not "
                                 "installed — run install.sh or update.sh "
                                 "(sudo) and retry"}), 503
    args = ["--update"] + (["--branch", branch] if branch else [])
    code, out, err = run_privileged(SELF_UPDATE_HELPER, args, timeout=60)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _cache_clear("app-update")
        _audit("app-update", outcome="started", branch=branch or "main")
        return jsonify({"result": message[:600] or "Update started",
                        "exit_code": code, "action": "app-update"})
    _audit("app-update", outcome="failed", exit_code=code)
    return jsonify({"error": message[:600] or "update could not be started",
                    "exit_code": code, "action": "app-update"}), 500


@bp.route("/api/maintain/perf", methods=["POST"])
@rate_limit("10 per minute")
def api_maintain_perf():
    """Apply a performance profile (governor, swappiness, I/O scheduler)."""
    data = request.get_json(silent=True) or {}
    profile = data.get("profile")
    if not isinstance(profile, str) or profile not in GOVERNOR_PROFILES:
        return jsonify({"error": "profile must be one of: "
                                 + ", ".join(GOVERNOR_PROFILES)}), 400
    args = ["--apply", "--profile", profile]
    swappiness = data.get("swappiness")
    if swappiness is not None:
        try:
            swappiness = int(str(swappiness).strip())
        except (TypeError, ValueError):
            return jsonify({"error": "swappiness must be an integer"}), 400
        if not 0 <= swappiness <= 100:
            return jsonify({"error": "swappiness must be between 0 and 100"}), 400
        args += ["--swappiness", str(swappiness)]
    iosched = data.get("iosched")
    if iosched is not None:
        if not isinstance(iosched, str) or iosched not in IOSCHED_ALLOWED:
            return jsonify({"error": "iosched must be one of: "
                                     + ", ".join(IOSCHED_ALLOWED)}), 400
        args += ["--iosched", iosched]

    if not os.path.isfile(_helper_path(PERF_HELPER)):
        return jsonify({"error": "monitoring-perf helper is not installed "
                                 "— run install.sh or update.sh (sudo) and retry"}), 503
    code, out, err = run_privileged(PERF_HELPER, args, timeout=180)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _clear_related_caches()
        _audit("maintain-perf", profile=profile, outcome="ok")
        return jsonify({"result": message[-2000:] or "Profile applied",
                        "exit_code": code, "action": "perf",
                        "profile": profile})
    _audit("maintain-perf", profile=profile, outcome="failed", exit_code=code)
    return jsonify({"error": f"perf apply failed (exit {code}): "
                             f"{message[-2000:] or 'see helper output'}",
                    "exit_code": code, "action": "perf"}), 500


@bp.route("/api/maintain/perf-revert", methods=["POST"])
@rate_limit("10 per minute")
def api_maintain_perf_revert():
    """Restore the exact pre-tuning values and remove persistence files."""
    if not os.path.isfile(_helper_path(PERF_HELPER)):
        return jsonify({"error": "monitoring-perf helper is not installed "
                                 "— run install.sh or update.sh (sudo) and retry"}), 503
    code, out, err = run_privileged(PERF_HELPER, ["--revert"], timeout=120)
    message = ((out or "") + (err or "")).strip()
    if code == 0:
        _clear_related_caches()
        _audit("maintain-perf-revert", outcome="ok")
        return jsonify({"result": message[-2000:] or "Reverted",
                        "exit_code": code, "action": "perf-revert"})
    _audit("maintain-perf-revert", outcome="failed", exit_code=code)
    return jsonify({"error": f"perf revert failed (exit {code}): "
                             f"{message[-2000:] or 'see helper output'}",
                    "exit_code": code, "action": "perf-revert"}), 500

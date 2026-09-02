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
from datetime import datetime

from flask import Blueprint, jsonify, request

from .commands import PASSWORDLESS_SUDO, privileged_tool, run_privileged
from .common import _audit, _cache_clear, _cached
from .fixes import run_fix_action
from .metrics import _pretty_distro, _read_temp_c
from .packages import _updatable_packages
from .security import rate_limit

bp = Blueprint("maintain", __name__)

MAINTAIN_HELPER = "monitoring-maintain"
PERF_HELPER = "monitoring-perf"
GOVERNOR_PROFILES = ("balanced", "performance", "powersave")
IOSCHED_ALLOWED = ("none", "noop", "deadline", "cfq", "mq-deadline", "kyber",
                   "bfq")


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
        import time
        import psutil
        report["system"] = {
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "arch": os.uname().machine,
            "uptime_s": int(time.time() - psutil.boot_time()),
            "temp_c": _read_temp_c(),
        }
    except Exception:
        pass
    report["perf"] = _perf_status()
    report["timestamp"] = datetime.now().isoformat()
    return report


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

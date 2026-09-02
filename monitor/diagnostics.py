"""Guided troubleshooting: diagnostics scan, post-fix verification, fix-all.

This is the largest feature area; it is isolated in its own module so changes
here cannot break the metrics/processes/services/VMs/network endpoints.
"""
import os
import re
import shlex
import time
from datetime import datetime

from flask import Blueprint, jsonify, request

from .commands import PASSWORDLESS_SUDO, run, run_lines, run_privileged, which
from .common import MY_PID, _audit, _cached
from .metrics import _cpu_model, _pretty_distro, _read_temp_c
from .packages import (FIX_COMMANDS, PACKAGE_MUTATION_LOCK, _dpkg_audit,
                       _pkg_manager, _updatable_packages)
from .security import rate_limit

bp = Blueprint("diagnostics", __name__)

# Internal argv lists for the monitoring-package privileged helper. These are
# never concatenated with user input and never go through a shell.
TROUBLESHOOT_FIXES = {
    "apt": {
        "broken_packages": ["monitoring-package", "--manager", "apt", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "apt", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "apt", "--action", "autoremove"],
    },
    "dnf": {
        "broken_packages": ["monitoring-package", "--manager", "dnf", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "dnf", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "dnf", "--action", "autoremove"],
    },
    "yum": {
        "broken_packages": ["monitoring-package", "--manager", "yum", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "yum", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "yum", "--action", "autoremove"],
    },
    "zypper": {
        "broken_packages": ["monitoring-package", "--manager", "zypper", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "zypper", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "zypper", "--action", "autoremove"],
    },
    "pacman": {
        "broken_packages": ["monitoring-package", "--manager", "pacman", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "pacman", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "pacman", "--action", "autoremove"],
    },
    "apk": {
        "broken_packages": ["monitoring-package", "--manager", "apk", "--action", "fix-broken"],
        "clean_cache": ["monitoring-package", "--manager", "apk", "--action", "clean"],
        "autoremove": ["monitoring-package", "--manager", "apk", "--action", "autoremove"],
    },
}

TROUBLESHOOT_CATEGORIES = {
    "cpu":      {"label": "CPU & Processes",    "icon": "cpu"},
    "memory":   {"label": "Memory & Swap",      "icon": "mem"},
    "disk":     {"label": "Disk & Storage",     "icon": "disk"},
    "services": {"label": "Services & Daemons", "icon": "gear"},
    "network":  {"label": "Network",            "icon": "network"},
    "packages": {"label": "Packages & Updates", "icon": "package"},
    "kernel":   {"label": "Kernel & Hardware",  "icon": "zap"},
}
CATEGORY_ORDER = ["cpu", "memory", "disk", "services", "network", "packages", "kernel"]


def _top_processes(count=6, order="cpu"):
    """Small, fast process snapshot for deep-diagnostics tables."""
    try:
        import psutil
        rows = []
        for p in psutil.process_iter(["pid", "name", "username", "memory_percent", "status"]):
            try:
                if p.pid == MY_PID:
                    continue
                rss = p.memory_info().rss if hasattr(p, "memory_info") else 0
                rows.append({
                    "pid": p.info["pid"],
                    "name": p.info["name"] or "?",
                    "user": p.info["username"] or "-",
                    "cpu": round(p.cpu_percent(interval=None) or 0.0, 1),
                    "mem": round(p.info["memory_percent"] or 0.0, 1),
                    "rss_mb": round((rss or 0) / 1024 / 1024, 1),
                    "status": p.info["status"],
                })
            except Exception:
                continue
        key = "mem" if order == "mem" else "cpu"
        rows.sort(key=lambda r: r.get(key, 0), reverse=True)
        return rows[:count]
    except Exception:
        return []


def _diag_issue(iid, name, detail, category, severity, icon=None, fix=None,
                evidence=None, impact="", recommended_fix=None, verify=None,
                deep=None, sample=None, processes=None, services=None):
    """Build one enriched diagnostic issue (keeps legacy fields intact)."""
    rec = recommended_fix or {}
    return {
        "id": iid,
        "name": name,
        "detail": detail,
        "category": category,
        "severity": severity,
        "icon": icon or "alert",
        "evidence": evidence or [],
        "impact": impact or "",
        "verify": verify or [],
        "deep": deep,
        "sample": sample,
        "processes": processes,
        "services": services,
        "fix": rec.get("id") or fix,
        "recommended_fix": rec or None,
    }


def _diag_ev(label, value, cmd=None):
    e = {"label": label, "value": value}
    if cmd:
        e["cmd"] = cmd
    return e


def _diag_scan():
    issues = {"critical": [], "warnings": [], "info": []}
    all_issues = []

    def put(severity, issue):
        issues[severity].append(issue)
        all_issues.append(issue)

    try:
        import psutil
    except Exception:
        psutil = None

    # -------------------------------------------------- 1. Disk & storage
    try:
        du = psutil.disk_usage("/")
        total_gb, used_gb, free_gb = du.total / 1024**3, du.used / 1024**3, du.free / 1024**3
        pct = round(du.percent, 1)
        disk_ev = [
            _diag_ev("Root usage", f"{pct:.0f}%  ·  {used_gb:.1f} / {total_gb:.1f} GB used", "df -h /"),
            _diag_ev("Free space", f"{free_gb:.1f} GB", "df -h /"),
        ]
        disk_fix = {
            "id": "clear-logs",
            "label": "Free disk space",
            "description": "Vacuum the system journal and remove old compressed logs to reclaim space. This never touches recent logs.",
            "risk": "low",
            "commands": ["journalctl --vacuum-time=7d --vacuum-size=100M",
                         "find /var/log -type f -name '*.gz' -delete"],
        }
        disk_verify = [_diag_ev("Re-check root usage", "below 95%", "df -h /")]
        if pct >= 95:
            put("critical", _diag_issue(
                "disk_critical", "Critical Disk Space",
                f"Root filesystem is {pct:.0f}% full — only {free_gb:.1f} GB free.",
                "disk", "critical", "disk", fix="clear-logs", evidence=disk_ev,
                impact="Writes will fail: log rotation, package installs, temp files and application data. A full root filesystem can stop services and block logins.",
                recommended_fix=disk_fix, verify=disk_verify))
        elif pct >= 85:
            put("warnings", _diag_issue(
                "disk_warning", "Low Disk Space",
                f"Root filesystem is {pct:.0f}% full — {free_gb:.1f} GB free.",
                "disk", "warning", "disk", fix="clear-logs", evidence=disk_ev,
                impact="Continuous growth may soon trigger failed writes, truncated logs and service failures.",
                recommended_fix=disk_fix, verify=disk_verify))
        # inodes
        code, out, _ = run(["df", "-i", "/"])
        if code == 0 and out:
            lines = out.splitlines()
            if len(lines) > 1:
                parts = lines[1].split()
                if len(parts) >= 5:
                    try:
                        ipct = int(parts[4].replace("%", ""))
                        if ipct >= 90:
                            put("warnings", _diag_issue(
                                "disk_inodes", "Inodes Nearly Exhausted",
                                f"Root filesystem uses {ipct}% of inodes; new files may be refused even with free space.",
                                "disk", "warning", "disk", fix="clear-old-logs",
                                evidence=[_diag_ev("Inode usage", f"{ipct}%  ·  {parts[3]} free", "df -i /")],
                                impact="File creation will fail across the system, breaking caches, mail, logs and package operations.",
                                recommended_fix={
                                    "id": "clear-old-logs",
                                    "label": "Remove old log files",
                                    "description": "Delete compressed log files older than 30 days to free inodes in /var/log.",
                                    "risk": "low",
                                    "commands": ["find /var/log -type f -name '*.gz' -mtime +30 -delete"],
                                },
                                verify=[_diag_ev("Re-check inode usage", "below 90%", "df -i /")]))
                    except ValueError:
                        pass
    except Exception:
        pass

    # -------------------------------------------------- 2. Memory & swap
    try:
        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        mem_ev = [
            _diag_ev("Memory used", f"{mem.percent:.0f}%  ·  {mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB", "free -h"),
            _diag_ev("Swap used", f"{swap.percent:.0f}%  ·  {swap.used / 1024**3:.1f} / {swap.total / 1024**3:.1f} GB",
                     "free -h") if swap.total else None,
        ]
        mem_ev = [e for e in mem_ev if e]
        mem_fix = {
            "id": None,
            "label": "Free memory / tune workload",
            "description": "Use the Processes dashboard to inspect and stop a confirmed runaway process, or run the read-only commands below. No process is killed automatically.",
            "risk": "medium",
            "commands": [
                "ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20",
                "systemd-cgtop --iterations=1 --depth=2",
            ],
        }
        mem_verify = [_diag_ev("Re-check memory usage", "below 85%", "free -h")]
        deep_mem = {"kind": "procs", "title": "Top memory consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                    "rows": _top_processes(6, "mem")}
        top = _top_processes(1, "mem")
        top_txt = f"Top consumer: {top[0]['name']} (PID {top[0]['pid']}, {top[0]['mem']}%)" if top else "No consumer info"
        if mem.percent >= 95:
            put("critical", _diag_issue(
                "memory_critical", "Critical Memory Usage",
                f"System memory is {mem.percent:.0f}% full ({mem.used / 1024**3:.1f} / {mem.total / 1024**3:.1f} GB). {top_txt}",
                "memory", "critical", "mem", evidence=mem_ev, deep=deep_mem,
                impact="The kernel will swap aggressively, causing severe slowdowns; the OOM killer may terminate processes and services.",
                recommended_fix=mem_fix, verify=mem_verify))
        elif mem.percent >= 85:
            put("warnings", _diag_issue(
                "memory_warning", "High Memory Usage",
                f"System memory is {mem.percent:.0f}% full. {top_txt}",
                "memory", "warning", "mem", evidence=mem_ev, deep=deep_mem,
                impact="Memory pressure can degrade performance, increase swap activity and eventually trigger OOM kills.",
                recommended_fix=mem_fix, verify=mem_verify))
        if swap.total and swap.percent >= 40:
            put("warnings", _diag_issue(
                "swap_warning", "High Swap Usage",
                f"Swap is {swap.percent:.0f}% used ({swap.used / 1024**3:.1f} / {swap.total / 1024**3:.1f} GB) — sustained swapping slows the whole system.",
                "memory", "warning", "mem",
                evidence=[_diag_ev("Swap used", f"{swap.percent:.0f}%", "free -h"),
                          _diag_ev("Memory pressure", f"{mem.percent:.0f}% RAM used", "free -h")],
                impact="Disk-backed paging causes high latency for every process; heavy swapping also wears SSDs.",
                recommended_fix={
                    "id": None, "label": "Reduce memory pressure",
                    "description": "Use the Processes dashboard to stop only a confirmed runaway process. These read-only commands identify memory and swap consumers before you act.",
                    "risk": "medium", "commands": [
                        "ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20",
                        "vmstat 1 5",
                    ],
                },
                verify=[_diag_ev("Re-check swap usage", "below 40%", "free -h")]))
    except Exception:
        pass

    # -------------------------------------------------- 3. CPU & load
    try:
        cores = psutil.cpu_count(logical=True) or 1
        l1, l5, l15 = os.getloadavg()
        load_ev = [
            _diag_ev("Load average", f"{l1:.2f} / {l5:.2f} / {l15:.2f}  (over {cores} logical cores)", "uptime"),
        ]
        load_verify = [_diag_ev("Re-check load average", f"below {cores * 1.2:.0f} (load1)", "uptime")]
        deep_cpu = {"kind": "procs", "title": "Top CPU consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                    "rows": _top_processes(6, "cpu")}
        if l1 >= cores * 2:
            put("critical", _diag_issue(
                "cpu_load_critical", "Extreme CPU Load",
                f"Load average {l1:.2f} is more than {cores * 2:.0f} (2× {cores} cores) — the system is overloaded.",
                "cpu", "critical", "cpu", evidence=load_ev, deep=deep_cpu,
                impact="Interactive sessions stall, scheduled jobs miss deadlines, and timeouts cascade across services.",
                recommended_fix={
                    "id": None, "label": "Identify and tame the run-away workload",
                    "description": "Inspect the top CPU consumers below. Restarting or adjusting them is safer than killing blindly.",
                    "risk": "medium", "commands": ["top -b -n1 | head -20"],
                }, verify=load_verify))
        elif l1 >= cores * 1.2:
            put("warnings", _diag_issue(
                "cpu_load_high", "High CPU Load",
                f"Load average {l1:.2f} exceeds {cores} logical cores — run-away jobs may be competing for CPU.",
                "cpu", "warning", "cpu", evidence=load_ev, deep=deep_cpu,
                impact="Responsiveness drops; CPU-bound tasks queue up and can starve interactive work.",
                recommended_fix={
                    "id": None, "label": "Balance CPU demand",
                    "description": "Look at the top consumers; defer batch jobs or restart misbehaving processes found below.",
                    "risk": "medium", "commands": ["top -b -n1 | head -20"],
                }, verify=load_verify))
        top_cpu = _top_processes(1, "cpu")
        if top_cpu and top_cpu[0]["cpu"] >= 75:
            p = top_cpu[0]
            put("warnings", _diag_issue(
                "high_cpu_process", "Single Process Saturating CPU",
                f"{p['name']} (PID {p['pid']}) is using {p['cpu']}% CPU — it may be hung or in a hot loop.",
                "cpu", "warning", "cpu",
                evidence=[_diag_ev("Busiest process", f"{p['name']} — {p['cpu']}% CPU, {p['mem']}% MEM (PID {p['pid']})", f"ps -p {p['pid']} -o pid,pcpu,pmem,comm"),
                          _diag_ev("Status", p["status"], "ps aux")],
                impact="A single hot process can eat an entire core, delaying everything else and burning power.",
                recommended_fix={
                    "id": None, "label": "Review the process",
                    "description": "Stop or restart the process if it is stuck; only kill it after inspecting its behaviour in the Processes tab.",
                    "risk": "high", "commands": [f"ps -p {p['pid']} -o pid,pcpu,pmem,stat,cmd"],
                },
                verify=[_diag_ev("Re-check process CPU", "below 75%", "ps -eo pid,pcpu,comm --sort=-pcpu | head")],
                deep={"kind": "procs", "title": "Top CPU consumers", "columns": ["PID", "Process", "CPU %", "MEM %", "RSS"],
                      "rows": _top_processes(6, "cpu")}))
        # Thermal throttling risk: sustained high temperature degrades the
        # whole system (CPU clocks down, latency climbs).
        temp = _read_temp_c()
        if temp is not None and temp >= 85:
            put("warnings", _diag_issue(
                "thermal_throttle", "CPU Thermal Throttling Risk",
                f"CPU temperature is {temp:.0f} \u00b0C \u2014 the kernel may be throttling clocks to protect the hardware.",
                "cpu", "warning", "thermo",
                evidence=[_diag_ev("CPU temperature", f"{temp:.0f} \u00b0C", "sensors")],
                impact="Throttled CPUs reduce performance system-wide; sustained heat can also damage components.",
                recommended_fix={
                    "id": None, "label": "Reduce heat load",
                    "description": "Clean cooling vents, check fans, reduce ambient temperature, or lower sustained load (see Processes).",
                    "risk": "low", "commands": ["sensors", "ps -eo pid,pcpu,comm --sort=-pcpu | head -10"],
                },
                verify=[_diag_ev("Re-check temperature", "below 85 \u00b0C", "sensors")]))
    except Exception:
        pass

    # zombies
    try:
        zombies = [p for p in psutil.process_iter(["pid", "name", "status", "username"]) if p.info["status"] == "zombie"]
        if zombies:
            zinfo = [{"pid": z.info["pid"], "name": z.info["name"]} for z in zombies[:5]]
            put("warnings", _diag_issue(
                "zombie_processes", "Zombie Processes",
                f"{len(zombies)} zombie process(es) detected — they hold PIDs and process slots until their parent reaps them.",
                "cpu", "warning", "cpu", fix="clean-zombies", processes=zinfo,
                evidence=[_diag_ev("Zombie count", f"{len(zombies)}", "ps -eo stat,pid,comm | awk '$1 ~ /^Z/ {print}'")],
                impact="Zombies consume PIDs and kernel process table entries. Many of them can exhaust the PID limit and block new processes.",
                recommended_fix={
                    "id": "clean-zombies",
                    "label": "Reap zombie processes",
                    "description": "Sends SIGTERM to the parent processes of zombies so the kernel reaps them.",
                    "risk": "medium",
                    "commands": ["ps -eo stat,pid,ppid,comm | grep \\'^Z\\'"],
                },
                verify=[_diag_ev("Re-check zombie count", "0 zombies", "ps -eo stat | grep -c '^Z'")]))
    except Exception:
        pass

    # -------------------------------------------------- 4. Services
    if which("systemctl"):
        code, out, err = run(["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"])
        failed_services = []
        for line in (out or "").splitlines():
            parts = line.split()
            if parts:
                failed_services.append(parts[0])
        if failed_services:
            svc_rows = [{"name": s, "status": "failed"} for s in failed_services[:20]]
            put("critical", _diag_issue(
                "failed_services", "Failed Systemd Services",
                f"{len(failed_services)} service(s) failed: {', '.join(failed_services[:5])}{'…' if len(failed_services) > 5 else ''}",
                "services", "critical", "gear", fix="restart-failed-services", services=failed_services,
                evidence=[_diag_ev("Failed units", ", ".join(failed_services[:8]) + ("…" if len(failed_services) > 8 else ""), "systemctl --failed"),
                          _diag_ev("Unit count", f"{len(failed_services)} failed", "systemctl --failed | wc -l")],
                impact="Failed services can break network, storage, logging and application availability until repaired.",
                recommended_fix={
                    "id": "restart-failed-services",
                    "label": "Reset and restart failed services",
                    "description": "Clears failed state and restarts each failed unit; services that fail again stay visible.",
                    "risk": "medium",
                    "commands": ["systemctl reset-failed"] + [f"systemctl restart {shlex.quote(unit)}" for unit in failed_services[:8]],
                },
                verify=[_diag_ev("Re-check failed services", "0 failed", "systemctl --failed")],
                deep={"kind": "services", "title": "Failed units", "rows": svc_rows}))

    # -------------------------------------------------- 5. Network
    try:
        io = psutil.net_io_counters()
        errs = (io.errin or 0) + (io.errout or 0) + (io.dropin or 0) + (io.dropout or 0)
        if errs > 0:
            sev = "warning" if errs >= 100 else "info"
            put(sev, _diag_issue(
                "network_errors", "Network Errors Detected",
                f"Cumulative NIC errors/drops: {errs} (errin {io.errin}, errout {io.errout}, dropin {io.dropin}, dropout {io.dropout}).",
                "network", sev, "network",
                evidence=[_diag_ev("Interface counters", f"errin {io.errin} · errout {io.errout} · dropin {io.dropin} · dropout {io.dropout}", "ip -s link")],
                impact="Packet errors and drops cause retransmissions, timeouts, slow throughput and flaky connections.",
                recommended_fix={
                    "id": None, "label": "Inspect interfaces & cabling",
                    "description": "Check `ip -s link` for which interface is dropping; verify cables, duplex settings and driver warnings in dmesg.",
                    "risk": "low", "commands": ["ip -s link", "dmesg --level=err,warn | tail -40"],
                },
                verify=[_diag_ev("Re-check counters", "no new errors", "ip -s link")]))
        # interface down but configured with IPv4
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        import socket as _socket
        down = []
        for name, st in stats.items():
            if name.startswith("lo") or st.isup:
                continue
            has_ipv4 = any(a.family == _socket.AF_INET for a in addrs.get(name, []))
            if has_ipv4:
                down.append(name)
        if down:
            put("warnings", _diag_issue(
                "interface_down", "Network Interface Down",
                f"Configured interface(s) are down: {', '.join(down)}.",
                "network", "warning", "network",
                evidence=[_diag_ev("Interfaces down", ", ".join(down), "ip -br addr")],
                impact="Any service bound to these interfaces is unreachable; connectivity and remote access may be lost.",
                recommended_fix={
                    "id": None, "label": "Bring the interface up",
                    "description": "Use nmcli/ip to bring the link up, or check physical cabling and network-manager state.",
                    "risk": "medium", "commands": ["ip -br addr"] + [f"ip link set {shlex.quote(iface)} up" for iface in down],
                },
                verify=[_diag_ev("Re-check interface state", "up", "ip -br addr")]))
    except Exception:
        pass

    # -------------------------------------------------- 6. Packages
    mgr = _pkg_manager()
    dpkg_available, dpkg_healthy, dpkg_detail = _dpkg_audit()
    if dpkg_available and not dpkg_healthy:
        detail_lines = dpkg_detail.splitlines()
        evidence_value = "\n".join(detail_lines[:3])[:480] or "dpkg reported incomplete package state"
        put("critical", _diag_issue(
            "broken_packages", "Broken Packages",
            "Package database has unpacked, half-installed, or unconfigured packages.",
            "packages", "critical", "package", fix="fix-broken-packages",
            evidence=[_diag_ev("dpkg audit", evidence_value, "dpkg --audit")],
            impact="Package installs, upgrades and removals can fail; broken dependencies can also break applications.",
            recommended_fix={
                "id": "fix-broken-packages",
                "label": "Repair package database",
                "description": f"Reconfigures pending packages and repairs dependencies with {mgr or 'the detected package manager'}.",
                "risk": "medium",
                "commands": (["dpkg --configure -a", "apt install -f -y"]
                             if mgr == "apt" else []),
            },
            verify=[_diag_ev("Re-run dpkg audit", "no pending package state", "dpkg --audit")]))
    updates = _updatable_packages()
    check_commands = {
        "apt": "apt list --upgradable",
        "dnf": "dnf check-update",
        "yum": "yum check-update",
        "zypper": "zypper list-updates",
        "pacman": "pacman -Qu",
        "apk": "apk version -l '<'",
    }
    check_command = check_commands.get(updates["manager"], "")
    upd_ev = [_diag_ev("Packages upgradable", f"{updates['count']} ({updates['manager'] or 'n/a'})", check_command)]
    upgrade_commands = {
        "apt": "apt update && apt upgrade -y",
        "dnf": "dnf upgrade -y",
        "yum": "yum upgrade -y",
        "zypper": "zypper update -y",
        "pacman": "pacman -Syu --noconfirm",
        "apk": "apk update && apk upgrade",
    }
    upd_fix = {
        "id": "upgrade-packages",
        "label": "Install available updates",
        "description": f"Runs the package-manager upgrade for {updates['manager'] or 'your system'}.",
        "risk": "medium",
        "commands": [upgrade_commands[updates["manager"]]] if updates["manager"] in upgrade_commands else [],
    }
    upd_verify = [_diag_ev("Re-check pending updates", "0 packages", check_command)]
    if updates["count"] > 50:
        put("warnings", _diag_issue(
            "many_updates", "Many Pending Updates",
            f"{updates['count']} packages can be updated ({updates['manager'] or 'unknown'}).",
            "packages", "warning", "package", fix="upgrade-packages", evidence=upd_ev,
            impact="Unpatched packages accumulate security and stability bugs; a large backlog also lengthens future update windows.",
            recommended_fix=upd_fix, verify=upd_verify))
    elif updates["count"] > 0:
        put("info", _diag_issue(
            "pending_updates", "Pending Updates",
            f"{updates['count']} packages upgradable ({updates['manager'] or 'n/a'}).",
            "packages", "info", "package", fix="upgrade-packages", evidence=upd_ev,
            impact="Updates fix bugs and security issues; keeping them current reduces risk.",
            recommended_fix=upd_fix, verify=upd_verify))

    # -------------------------------------------------- 7. Kernel
    code, lines = run_lines(["journalctl", "-k", "-b", "-p", "err..alert", "--no-pager", "-q", "-n", "20"])
    kernel_cmd = "journalctl -k -b -p err..alert --no-pager -q -n 20"
    if code != 0:
        code, lines = run_lines(["dmesg", "--level=err,crit,alert,emerg"])
        kernel_cmd = "dmesg --level=err,crit,alert,emerg"
        lines = lines[-20:]
    error_lines = lines
    if error_lines:
        text = "\n".join(error_lines).lower()
        commands = [kernel_cmd + " | tail -80"]
        guidance = "Review the exact component in the log before changing the system; kernel faults do not have a safe universal automatic fix."
        if any(k in text for k in ("i/o error", "blk_update", "buffer i/o", "nvme", "ata1:", "ata2:", "ata error")):
            guidance = "A storage fault is indicated. Back up important data first, identify the affected disk, then inspect its health."
            commands += [
                "lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL,SERIAL",
                "for d in $(lsblk -dn -o NAME,TYPE | awk '$2==\"disk\" {print \"/dev/\"$1}'); do smartctl -H \"$d\"; done",
            ]
        elif any(k in text for k in ("ext4-fs error", "xfs", "btrfs error", "filesystem error")):
            guidance = "A filesystem fault is indicated. Back up data and schedule an offline filesystem check; never run fsck on a mounted filesystem."
            commands += ["findmnt -no SOURCE,FSTYPE,OPTIONS /", "systemctl --failed --no-pager"]
        elif any(k in text for k in ("out of memory", "oom-killer", "killed process")):
            guidance = "The kernel ran out of memory. Identify the largest consumers in the Processes dashboard before restarting or stopping one."
            commands += ["ps -eo pid,user,%mem,rss,comm --sort=-rss | head -20", "journalctl -k -b -g 'oom|killed process' --no-pager"]
        elif any(k in text for k in ("firmware", "microcode", "acpi", "bios")):
            guidance = "Firmware or platform code is implicated. Check vendor firmware updates and current microcode before changing drivers."
            commands += ["fwupdmgr get-updates", "journalctl -k -b -g 'firmware|microcode|acpi|bios' --no-pager"]
        put("warnings", _diag_issue(
            "kernel_errors", "Kernel Errors Detected",
            f"{len(error_lines)} current-boot kernel error line(s) found.",
            "kernel", "warning", "zap", evidence=[
                _diag_ev("Error lines (last 20)", f"{len(error_lines)}", kernel_cmd),
                _diag_ev("Sample", error_lines[0][:180], kernel_cmd + " | tail"),
            ],
            sample=error_lines[:3],
            impact="Kernel errors can indicate failing hardware, driver bugs, memory pressure or filesystem corruption.",
            recommended_fix={
                "id": None, "label": "Resolve the reported kernel component",
                "description": guidance,
                "risk": "medium", "commands": commands,
            },
            verify=[_diag_ev("Re-check current-boot kernel errors", "no new errors after remediation/reboot", kernel_cmd)],
            deep={"kind": "logs", "title": "Current-boot kernel errors", "lines": error_lines[:8]}))

    # Kernel update pending: a new kernel is installed but not yet running.
    try:
        import glob as _glob
        running = os.uname().release
        installed = sorted(
            (os.path.basename(p) for p in _glob.glob("/lib/modules/*")
             if re.fullmatch(r"[0-9][A-Za-z0-9._+-]*", os.path.basename(p))),
            reverse=True)
        if installed and installed[0] != running:
            put("info", _diag_issue(
                "kernel_update_pending", "Kernel Update Not Active Yet",
                f"Running kernel {running} is not the newest installed kernel ({installed[0]}).",
                "kernel", "info", "zap",
                evidence=[_diag_ev("Running kernel", running, "uname -r"),
                          _diag_ev("Newest installed", installed[0], "ls /lib/modules")],
                impact="The security and hardware fixes in the new kernel only apply after a reboot. "
                       "Schedule one when convenient; a reboot with pending updates is normal.",
                recommended_fix={
                    "id": None, "label": "Reboot to activate the new kernel",
                    "description": "No automatic reboot is performed. Close applications and reboot when convenient, then re-check.",
                    "risk": "low", "commands": ["uname -r", "reboot"],
                },
                verify=[_diag_ev("Re-check running kernel", "newest installed kernel", "uname -r")]))
    except Exception:
        pass

    # Performance tuning opportunity: conservative kernel defaults.
    try:
        governor = None
        try:
            with open("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") as fh:
                governor = fh.read().strip()
        except OSError:
            pass
        swappiness = None
        try:
            with open("/proc/sys/vm/swappiness") as fh:
                swappiness = fh.read().strip()
        except OSError:
            pass
        if governor in ("powersave", "conservative") or (
                swappiness is not None and swappiness.isdigit()
                and int(swappiness) >= 20):
            put("info", _diag_issue(
                "perf_tuning", "Performance Tuning Available",
                "Kernel is on conservative defaults (" + (
                    f"governor {governor}, " if governor else "") + (
                    f"swappiness {swappiness}" if swappiness else "defaults") + ")",
                "cpu", "info", "cpu",
                evidence=[_diag_ev("CPU governor", governor or "unmanaged (no cpufreq)", "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
                          _diag_ev("vm.swappiness", swappiness or "n/a", "sysctl vm.swappiness")],
                impact="Balanced tuning (schedutil governor, lower swappiness, modern I/O scheduler) can reduce latency and swap churn on interactive workloads.",
                recommended_fix={
                    "id": None, "label": "Apply a performance profile",
                    "description": "Open System & Kernel → Performance Tuning in the dashboard to apply a balanced or maximum-performance profile (reversible).",
                    "risk": "low", "commands": ["cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor", "sysctl vm.swappiness"],
                },
                verify=[_diag_ev("Re-check governor/swappiness", "profile applied", "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor; sysctl vm.swappiness")]))
    except Exception:
        pass

    # -------------------------------------------------- 8. Log hygiene (disk)
    code, old_lines = run_lines(["find", "/var/log", "-type", "f", "-name", "*.gz", "-mtime", "+30"])
    old_logs = len(old_lines)
    if old_logs > 10:
        put("info", _diag_issue(
            "old_logs", "Old Log Files",
            f"{old_logs} compressed log files older than 30 days still stored.",
            "disk", "info", "terminal", fix="clear-old-logs",
            evidence=[_diag_ev("Old logs", f"{old_logs} files", "find /var/log -name '*.gz' -mtime +30 | wc -l")],
            impact="Rotated logs consume inodes and disk space that belong to active data.",
            recommended_fix={
                "id": "clear-old-logs",
                "label": "Delete old rotated logs",
                "description": "Removes compressed logs older than 30 days; active logs are untouched.",
                "risk": "low", "commands": ["find /var/log -type f -name '*.gz' -mtime +30 -delete"],
            },
            verify=[_diag_ev("Re-check old logs", "10 or fewer", "find /var/log -name '*.gz' -mtime +30 | wc -l")]))
    code, out, err = run(["journalctl", "--disk-usage"])
    if out:
        match = re.search(r'(\d+(?:\.\d+)?)([KMGT]?)B', out)
        if match:
            size_mb = float(match.group(1))
            unit = match.group(2)
            if unit == "K":
                size_mb = size_mb / 1024
            elif unit == "G":
                size_mb = size_mb * 1024
            elif unit == "T":
                size_mb = size_mb * 1024 * 1024
            if size_mb > 2048:
                put("info", _diag_issue(
                    "large_journal", "Large Journal",
                    f"System journal uses {match.group(1)}{unit} — above the 2 GB comfort mark.",
                    "disk", "info", "terminal", fix="vacuum-journal",
                    evidence=[_diag_ev("Journal size", f"{match.group(1)}{unit}", "journalctl --disk-usage")],
                    impact="A growing journal consumes disk space and makes log searches slower.",
                    recommended_fix={
                        "id": "vacuum-journal",
                        "label": "Vacuum journal",
                        "description": "Trims the journal to 7 days / 100 MB.",
                        "risk": "low", "commands": ["journalctl --vacuum-time=7d --vacuum-size=100M"],
                    },
                    verify=[_diag_ev("Re-check journal size", "≤ 2 GB", "journalctl --disk-usage")]))

    # -------------------------------------------------- Summary
    total = len(all_issues)
    health_score = max(0, 100 - len(issues["critical"]) * 20 - len(issues["warnings"]) * 10 - len(issues["info"]) * 3)
    if health_score >= 95:
        grade = "Excellent"
    elif health_score >= 80:
        grade = "Good"
    elif health_score >= 60:
        grade = "Fair"
    elif health_score >= 40:
        grade = "At Risk"
    else:
        grade = "Poor"

    categories = {}
    for key in CATEGORY_ORDER:
        meta = TROUBLESHOOT_CATEGORIES.get(key, {"label": key, "icon": ""})
        cat = [i for i in all_issues if i.get("category") == key]
        state = "ok"
        if any(i["severity"] == "critical" for i in cat):
            state = "critical"
        elif any(i["severity"] == "warning" for i in cat):
            state = "warning"
        elif cat:
            state = "info"
        categories[key] = {
            "label": meta["label"], "icon": meta["icon"],
            "count": len(cat), "state": state, "issues": cat,
        }

    system = {}
    try:
        import psutil as _p
        import platform as _pl
        memv = _p.virtual_memory()
        diskv = _p.disk_usage("/")
        system = {
            "hostname": os.uname().nodename,
            "os": _pretty_distro(),
            "kernel": f"{os.uname().sysname} {os.uname().release}",
            "arch": os.uname().machine,
            "cpu_model": _cpu_model(),
            "cores": _p.cpu_count(logical=True) or 1,
            "uptime_s": int(time.time() - _p.boot_time()),
            "memory_total_gb": round(memv.total / 1024**3, 1),
            "memory_used_gb": round(memv.used / 1024**3, 1),
            "disk_total_gb": round(diskv.total / 1024**3, 1),
            "disk_used_gb": round(diskv.used / 1024**3, 1),
            "load": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else [],
            "temp_c": _read_temp_c(),
        }
    except Exception:
        pass

    capabilities = {
        "root": os.geteuid() == 0,
        "sudo": PASSWORDLESS_SUDO,
        "pkg_manager": mgr,
        "systemd": which("systemctl"),
        "journal": which("journalctl"),
    }

    return {
        "issues": issues,
        "total": total,
        "health_score": health_score,
        "grade": grade,
        "categories": categories,
        "capabilities": capabilities,
        "system": system,
        "timestamp": datetime.now().isoformat(),
    }


@bp.route("/api/troubleshooting")
@rate_limit("20 per minute")
def api_troubleshooting():
    """Guided diagnostics: enriched issues, categories, evidence & fixes."""
    try:
        return jsonify(_cached("_diag", 10, _diag_scan))
    except Exception as e:
        return jsonify({"issues": {"critical": [], "warnings": [], "info": []},
                        "total": 0, "health_score": 100, "error": str(e)}), 500


def _verify_issue(iid):
    """Run the targeted check for one issue id."""
    def res(resolved, message, evidence=None):
        return {"resolved": bool(resolved),
                "state": "resolved" if resolved else "still_present",
                "message": message, "evidence": evidence or []}

    try:
        import psutil
    except Exception:
        psutil = None

    try:
        if iid in ("disk_critical", "disk_warning", "disk_inodes"):
            pct = psutil.disk_usage("/").percent
            if iid == "disk_inodes":
                _, out, _ = run(["df", "-i", "/"])
                parts = (out.splitlines() or ["", ""])[1].split() if out and len(out.splitlines()) > 1 else []
                ipct = int(parts[4].replace("%", "")) if len(parts) >= 5 else 0
                return res(ipct < 90, f"Inode usage {ipct}%",
                           [_diag_ev("Inode usage", f"{ipct}%", "df -i /")])
            return res(pct < 85, f"Root filesystem at {pct:.1f}%",
                       [_diag_ev("Root usage", f"{pct:.1f}%", "df -h /")])
        if iid in ("memory_critical", "memory_warning", "swap_warning"):
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()
            if iid == "swap_warning":
                return res(swap.total == 0 or swap.percent < 40, f"Swap at {swap.percent:.1f}%",
                           [_diag_ev("Swap usage", f"{swap.percent:.1f}%", "free -h")])
            return res(mem.percent < 85, f"Memory at {mem.percent:.1f}%",
                       [_diag_ev("Memory usage", f"{mem.percent:.1f}%", "free -h")])
        if iid in ("cpu_load_high", "cpu_load_critical"):
            cores = psutil.cpu_count(logical=True) or 1
            l1 = os.getloadavg()[0]
            threshold = cores * 2 if iid == "cpu_load_critical" else cores * 1.2
            return res(l1 < threshold, f"Load1 {l1:.2f} (cores {cores})",
                       [_diag_ev("Load average", f"{l1:.2f}", "uptime")])
        if iid == "high_cpu_process":
            top = _top_processes(1, "cpu")
            pct = top[0]["cpu"] if top else 0
            return res(pct < 75, f"Busiest process {pct:.1f}% CPU",
                       [_diag_ev("Busiest process", f"{pct:.1f}%", "ps -eo pcpu,comm --sort=-pcpu | head")])
        if iid == "zombie_processes":
            zb = [p for p in psutil.process_iter(["status"]) if p.info["status"] == "zombie"]
            return res(not zb, f"{len(zb)} zombie(s)", [_diag_ev("Zombies", str(len(zb)), "ps -eo stat | grep -c '^Z'")])
        if iid == "failed_services":
            _, out, _ = run(["systemctl", "--failed", "--no-pager", "--quiet"])
            n = len([l for l in out.splitlines() if l.strip()])
            return res(n == 0, f"{n} failed service(s)", [_diag_ev("Failed units", str(n), "systemctl --failed")])
        if iid == "broken_packages":
            available, healthy, detail = _dpkg_audit()
            if not available:
                return res(True, "dpkg is not installed on this host")
            evidence = "clean" if healthy else (detail.splitlines()[0][:240] if detail else "incomplete package state")
            return res(healthy, "dpkg audit clean" if healthy else "dpkg reports incomplete package state",
                       [_diag_ev("dpkg audit", evidence, "dpkg --audit")])
        if iid in ("many_updates", "pending_updates"):
            u = _updatable_packages()
            check_cmd = {
                "apt": "apt list --upgradable", "dnf": "dnf check-update",
                "yum": "yum check-update", "zypper": "zypper list-updates",
                "pacman": "pacman -Qu", "apk": "apk version -l '<'",
            }.get(u["manager"], "")
            return res(u["count"] == 0, f"{u['count']} pending update(s)",
                       [_diag_ev("Pending updates", str(u["count"]), check_cmd)])
        if iid == "kernel_errors":
            cmd = "journalctl -k -b -p err..alert --no-pager -q"
            argv = ["journalctl", "-k", "-b", "-p", "err..alert", "--no-pager", "-q"]
            code, out, _ = run(argv)
            if code != 0:
                cmd = "dmesg --level=err,crit,alert,emerg"
                argv = ["dmesg", "--level=err,crit,alert,emerg"]
                _, out, _ = run(argv)
            n = len([line for line in (out or "").splitlines() if line.strip()])
            return res(n == 0, f"{n} current-boot kernel error line(s)",
                       [_diag_ev("Kernel errors", str(n), cmd)])
        if iid == "network_errors":
            io = psutil.net_io_counters()
            errs = (io.errin or 0) + (io.errout or 0) + (io.dropin or 0) + (io.dropout or 0)
            return res(errs == 0, f"{errs} errors/drops", [_diag_ev("Counters", str(errs), "ip -s link")])
        if iid == "interface_down":
            stats = psutil.net_if_stats()
            addrs = psutil.net_if_addrs()
            import socket as _s
            down = [n for n, st in stats.items() if not st.isup and not n.startswith("lo")
                    and any(a.family == _s.AF_INET for a in addrs.get(n, []))]
            return res(not down, "all configured interfaces up" if not down else "down: " + ", ".join(down),
                       [_diag_ev("Interfaces", "up" if not down else ", ".join(down), "ip -br addr")])
        if iid == "old_logs":
            _, out, _ = run(["find", "/var/log", "-type", "f", "-name", "*.gz", "-mtime", "+30"])
            n = len([l for l in out.splitlines() if l.strip()])
            return res(n <= 10, f"{n} old log file(s)", [_diag_ev("Old logs", str(n), "find /var/log -name '*.gz' -mtime +30 | wc -l")])
        if iid == "large_journal":
            _, out, _ = run(["journalctl", "--disk-usage"])
            m = re.search(r'(\d+(?:\.\d+)?)([KMGT]?)B', out or "")
            if m:
                size_mb = float(m.group(1))
                unit = m.group(2)
                if unit == "K":
                    size_mb /= 1024
                elif unit == "G":
                    size_mb *= 1024
                elif unit == "T":
                    size_mb *= 1024 * 1024
                return res(size_mb <= 2048, f"Journal {m.group(1)}{unit}",
                           [_diag_ev("Journal size", f"{m.group(1)}{unit}", "journalctl --disk-usage")])
            return res(True, "journalctl unavailable")
    except Exception as e:
        return res(False, "verification error: " + str(e)[:200])
    return res(False, "no automated verification for this issue")


@bp.route("/api/troubleshooting/verify", methods=["POST"])
@rate_limit("30 per minute")
def api_troubleshooting_verify():
    """Targeted post-fix verification for one or more issue ids."""
    data = request.json or {}
    ids = list(data.get("issue_ids") or [])
    if data.get("issue_id"):
        ids.append(data["issue_id"])
    ids = [str(i)[:64] for i in ids][:60]
    results = {i: _verify_issue(i) for i in ids}
    return jsonify({"results": results, "timestamp": datetime.now().isoformat()})


@bp.route("/api/troubleshooting/fix-all", methods=["POST"])
@rate_limit("5 per minute")
def api_troubleshooting_fix_all():
    """Fix all detected issues with sudo access."""
    results = {"fixed": [], "failed": [], "skipped": []}
    mgr = _pkg_manager()

    data = request.json or {}
    fixes_to_run = data.get("fixes", [])
    if not isinstance(fixes_to_run, list):
        fixes_to_run = []
    fixes_to_run = [str(f)[:80] for f in fixes_to_run if isinstance(f, str)][:20]

    try:
        issues_data = _diag_scan()
    except Exception as exc:
        return jsonify({"fixed": [], "failed": [{"fix": "scan", "output": str(exc)[:300]}], "skipped": []}), 500

    all_issues = issues_data.get("issues", {}).get("critical", []) + \
                 issues_data.get("issues", {}).get("warnings", []) + \
                 issues_data.get("issues", {}).get("info", [])

    fixes_needed = set()
    for issue in all_issues:
        fix_id = issue.get("fix")
        if fix_id:
            fixes_needed.add(str(fix_id)[:80])

    if fixes_to_run:
        fixes_needed = fixes_needed.intersection(set(fixes_to_run))

    def record_fix(fix_result, code=0):
        if code == 0:
            results["fixed"].append(fix_result)
        else:
            results["failed"].append(fix_result)

    def helper_run(name, args=None, timeout=60):
        return run_privileged(name, args, timeout=timeout)

    def helper_list(name, argv):
        if not argv or argv[0] != name:
            raise ValueError("internal fix command mismatch")
        if name == "monitoring-package":
            # Package databases use a host-wide lock. Keep the same lock as
            # /api/fix so Diagnose Fix All cannot race an Overview action.
            with PACKAGE_MUTATION_LOCK:
                return helper_run(argv[0], argv[1:])
        return helper_run(argv[0], argv[1:])

    for fix in fixes_needed:
        fix_result = {"fix": fix, "output": ""}
        code = 0
        try:
            if fix == "fix-broken-packages" and mgr:
                cmd = TROUBLESHOOT_FIXES.get(mgr, {}).get("broken_packages", [])
                if cmd:
                    code, out, err = helper_list("monitoring-package", cmd)
                    fix_result["output"] = (out or err or "Done").strip()[-500:]
                    record_fix(fix_result, code)
                else:
                    fix_result["output"] = f"No fix available for {mgr}"
                    results["skipped"].append(fix_result)

            elif fix == "clear-logs":
                c1, o1, e1 = helper_run("monitoring-journal-vacuum")
                c2, o2, e2 = helper_run("monitoring-clean-old-logs", ["--min-age-days", "7"])
                code = 0 if c1 == 0 and c2 == 0 else 1
                fix_result["output"] = ((o1 or e1 or "") + (o2 or e2 or "")).strip()[-500:] or "Logs cleared"
                record_fix(fix_result, code)

            elif fix == "clear-old-logs":
                code, out, err = helper_run("monitoring-clean-old-logs", ["--min-age-days", "30"])
                fix_result["output"] = (out or err or "Old logs cleared").strip()[-500:]
                record_fix(fix_result, code)

            elif fix == "vacuum-journal":
                code, out, err = helper_run("monitoring-journal-vacuum")
                fix_result["output"] = (out or err or "Journal vacuumed").strip()[-500:]
                record_fix(fix_result, code)

            elif fix == "update-packages" and mgr:
                cmd = FIX_COMMANDS.get(mgr, {}).get("update", [])
                if cmd:
                    code, out, err = helper_list("monitoring-package", cmd)
                    fix_result["output"] = (out or err or "Done").strip()[-500:]
                    record_fix(fix_result, code)

            elif fix == "upgrade-packages" and mgr:
                cmd = FIX_COMMANDS.get(mgr, {}).get("upgrade", [])
                if cmd:
                    code, out, err = helper_list("monitoring-package", cmd)
                    fix_result["output"] = (out or err or "Upgrade complete").strip()[-500:]
                    record_fix(fix_result, code)
                else:
                    fix_result["output"] = f"No upgrade command available for {mgr}"
                    results["skipped"].append(fix_result)

            elif fix == "restart-failed-services":
                if which("systemctl"):
                    code, out, err = helper_run("monitoring-systemctl", ["reset-failed"], timeout=30)
                    _, failed_out, _ = run(["systemctl", "--failed", "--no-pager", "--plain", "--no-legend"])
                    failed_units = []
                    for line in failed_out.splitlines():
                        parts = line.split()
                        if parts:
                            failed_units.append(parts[0])
                    c2, o2, e2 = 0, "", ""
                    for unit in failed_units[:20]:
                        cr, orr, er = helper_run("monitoring-systemctl", ["restart", unit], timeout=30)
                        c2 = c2 or cr
                        o2 += orr or er or ""
                    code = 0 if code == 0 and c2 == 0 else 1
                    fix_result["output"] = ((out or "") + (o2 or "")).strip()[-500:] or "Reset complete"
                    record_fix(fix_result, code)

            elif fix == "clean-zombies":
                code, out, err = helper_run("monitoring-zombie-clean")
                fix_result["output"] = (out or err or "Zombie cleanup complete").strip()[-500:]
                record_fix(fix_result, code)

            else:
                fix_result["output"] = f"Unknown fix: {fix}"
                results["skipped"].append(fix_result)

        except Exception as e:
            fix_result["output"] = str(e)
            results["failed"].append(fix_result)

    if not fixes_needed:
        results["skipped"].append({"fix": "all", "output": "No fixes needed - system is healthy!"})

    _audit("fix-all", fixed=",".join(r["fix"] for r in results["fixed"]),
           failed=",".join(r["fix"] for r in results["failed"]),
           skipped=",".join(r["fix"] for r in results["skipped"]))
    return jsonify(results)

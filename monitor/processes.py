"""Process listing, inspection, bounded control and resource history."""
import os
import time

from flask import Blueprint, jsonify, request

from .commands import run, run_privileged
from .common import MY_PID, _audit, _cached, _int_or
from .security import rate_limit

bp = Blueprint("processes", __name__)

_PRIMED = {"flag": False}


def _prime_processes():
    """Prime CPU percent for all processes so first real call returns values."""
    if _PRIMED["flag"]:
        return
    try:
        import psutil
        for p in psutil.process_iter():
            try:
                p.cpu_percent(interval=None)
            except Exception:
                continue
        _PRIMED["flag"] = True
    except Exception:
        pass


def _build_processes(limit):
    import psutil
    # Prime on first call if not yet done
    if not _PRIMED["flag"]:
        _prime_processes()
    procs = []
    attrs = ["pid", "name", "username", "memory_percent", "status", "nice"]
    for p in psutil.process_iter(attrs):
        try:
            if p.pid == MY_PID:
                continue
            info = p.info
            cpu = p.cpu_percent(interval=None)
            # On first scan after priming, cpu will still be 0.0; that's expected.
            # Second scan will have real values.
            procs.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "user": info["username"] or "-",
                "cpu": round(cpu, 1),
                "mem": round(info["memory_percent"] or 0.0, 1),
                "status": info["status"],
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    if not _PRIMED["flag"]:
        _PRIMED["flag"] = True  # subsequent polls return real CPU values
    top_cpu = sorted(procs, key=lambda x: x["cpu"], reverse=True)[:limit]
    top_mem = sorted(procs, key=lambda x: x["mem"], reverse=True)[:limit]
    seen, merged = set(), []
    for p in top_cpu + top_mem:
        if p["pid"] not in seen:
            seen.add(p["pid"])
            merged.append(p)
    merged.sort(key=lambda x: x["cpu"], reverse=True)
    return {"processes": merged[:limit * 2], "total": len(procs)}


@bp.route("/api/processes")
@rate_limit("60 per minute")
def api_processes():
    limit = min(max(_int_or(request.args.get("limit"), 25), 5), 200)
    try:
        data = _cached(f"_procs_{limit}", 3, lambda: _build_processes(limit))
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _pid_gone(pid):
    """True when the pid no longer exists (or only lingers as a zombie)."""
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return True
        try:
            return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return True
    except Exception:
        return True  # cannot verify — do not fail the action over it


def top_processes_snapshot(limit=8):
    """Cheap top-CPU snapshot shared by the sampler and the automation rules.

    Reuses psutil's cached Process objects (same priming contract as the
    /api/processes endpoint) and never raises.
    """
    try:
        import psutil
        if not _PRIMED["flag"]:
            _prime_processes()
        rows = []
        for p in psutil.process_iter(["pid", "name", "username",
                                      "memory_percent"]):
            try:
                if p.pid == MY_PID:
                    continue
                info = p.info
                rows.append({"pid": info["pid"], "name": info["name"] or "?",
                             "user": info["username"] or "-",
                             "cpu": round(p.cpu_percent(interval=None), 1),
                             "mem": round(info["memory_percent"] or 0.0, 1)})
            except Exception:  # noqa: BLE001 — process vanished mid-scan
                continue
        rows.sort(key=lambda r: r["cpu"], reverse=True)
        return rows[:limit]
    except Exception:  # noqa: BLE001
        return []


def _process_unit(pid):
    """Best-effort systemd unit that owns a process (from its cgroup path)."""
    try:
        with open(f"/proc/{pid}/cgroup") as f:
            text = f.read()
        for line in text.splitlines():
            for part in line.split("/"):
                for suffix in (".service", ".scope", ".slice", ".socket",
                               ".timer", ".mount"):
                    if part.endswith(suffix):
                        return part
    except Exception:  # noqa: BLE001
        pass
    return None


def _process_detail(pid):
    """Rich single-process view; permission-limited fields degrade to None."""
    import psutil
    p = psutil.Process(pid)
    with p.oneshot():
        info = {
            "pid": pid,
            "name": p.name(),
            "cmdline": " ".join(p.cmdline())[:500] or p.name(),
            "user": None,
            "status": p.status(),
            "started": None,
            "cpu": None,
            "mem": round(p.memory_percent(), 1),
            "rss_bytes": None,
            "threads": p.num_threads(),
            "nice": None,
            "affinity": None,
            "ppid": None,
            "parent": None,
            "children": [],
            "unit": _process_unit(pid),
            "cwd": None,
            "exe": None,
            "open_files": None,
            "io_read_bytes": None,
            "io_write_bytes": None,
        }
        try:
            info["user"] = p.username()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["nice"] = p.nice()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["affinity"] = p.cpu_affinity()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["cpu"] = round(p.cpu_percent(interval=None), 1)
        except Exception:  # noqa: BLE001
            pass
        try:
            info["rss_bytes"] = p.memory_info().rss
        except Exception:  # noqa: BLE001
            pass
        try:
            info["started"] = time.strftime("%Y-%m-%d %H:%M:%S",
                                            time.localtime(p.create_time()))
        except Exception:  # noqa: BLE001
            pass
        try:
            info["ppid"] = p.ppid()
            if info["ppid"] and info["ppid"] > 0:
                info["parent"] = psutil.Process(info["ppid"]).name()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["children"] = [{"pid": c.pid, "name": c.name()}
                                for c in p.children()][:30]
        except Exception:  # noqa: BLE001
            pass
        try:
            info["cwd"] = p.cwd()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["exe"] = p.exe()
        except Exception:  # noqa: BLE001
            pass
        try:
            info["open_files"] = len(p.open_files())
        except Exception:  # noqa: BLE001
            pass
        try:
            io = p.io_counters()
            info["io_read_bytes"] = io.read_bytes
            info["io_write_bytes"] = io.write_bytes
        except Exception:  # noqa: BLE001
            pass
    return info


@bp.route("/api/process/<int:pid>")
@rate_limit("60 per minute")
def api_process_detail(pid):
    if pid <= 0 or pid > 1000000:
        return jsonify({"error": "invalid pid"}), 400
    try:
        return jsonify(_process_detail(pid))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 404 if "process" in str(e).lower() \
            else 500


@bp.route("/api/process/tree")
@rate_limit("30 per minute")
def api_process_tree():
    """Process forest with per-subtree CPU/RAM rollups (capped at 400 nodes)."""
    try:
        import psutil
        procs = {}
        for p in psutil.process_iter(["pid", "name", "ppid", "username",
                                      "memory_percent"]):
            try:
                info = p.info
                if info["pid"] <= 0:
                    continue
                procs[info["pid"]] = {
                    "pid": info["pid"],
                    "name": info["name"] or "?",
                    "ppid": info["ppid"] or 0,
                    "user": info["username"] or "-",
                    "cpu": round(p.cpu_percent(interval=None), 1),
                    "mem": round(info["memory_percent"] or 0.0, 1),
                    "children": [],
                }
            except Exception:  # noqa: BLE001
                continue
        roots = []
        for proc in procs.values():
            parent = procs.get(proc["ppid"])
            if parent is not None and parent is not proc:
                parent["children"].append(proc)
            else:
                roots.append(proc)

        def rollup(node):
            cpu = node["cpu"]
            mem = node["mem"]
            for child in node["children"]:
                c_cpu, c_mem = rollup(child)
                cpu += c_cpu
                mem += c_mem
            node["cpu_total"] = round(cpu, 1)
            node["mem_total"] = round(mem, 1)
            node["children"].sort(key=lambda n: n["cpu_total"], reverse=True)
            return cpu, mem

        for r in roots:
            rollup(r)
        roots.sort(key=lambda n: n["cpu_total"], reverse=True)
        total = {"nodes": 0}

        def prune(node):
            if total["nodes"] >= 400:
                node["children"] = []
                return
            total["nodes"] += 1
            for child in list(node["children"]):
                prune(child)

        for r in roots:
            prune(r)
        return jsonify({"tree": roots, "total": len(procs)})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


def _helper_installed(name):
    """True when a whitelisted helper is installed in PRIVILEGE_DIR."""
    try:
        from .commands import privileged_tool
        return os.path.exists(privileged_tool(name))
    except Exception:  # noqa: BLE001
        return False


def _renice(pid, nice):
    """renice via the whitelisted helper; plain renice fallback for dev
    checkouts where the helper is not installed. Returns (ok, detail)."""
    if _helper_installed("monitoring-nice"):
        code, out, err = run_privileged("monitoring-nice",
                                        ["renice", str(pid), str(nice)],
                                        timeout=15)
        return code == 0, ((err or out) or "").strip()[:200]
    code, out, err = run(["renice", str(nice), "-p", str(pid)], timeout=10)
    return code == 0, ((err or out) or "renice failed").strip()[:200]


def _set_affinity(pid, cpus):
    """taskset via the whitelisted helper with the same dev fallback."""
    if _helper_installed("monitoring-nice"):
        code, out, err = run_privileged("monitoring-nice",
                                        ["affinity", str(pid), cpus],
                                        timeout=15)
        return code == 0, ((err or out) or "").strip()[:200]
    code, out, err = run(["taskset", "-cp", cpus, str(pid)], timeout=10)
    return code == 0, ((err or out) or "taskset failed").strip()[:200]


@bp.route("/api/process/priority", methods=["POST"])
@rate_limit("20 per minute")
def api_process_priority():
    """Adjust nice level and/or CPU affinity through the monitoring-nice helper.

    Same safety rules as kill: never init, never self, validated pid, and the
    resulting state is re-read and reported (post-action verification).
    """
    data = request.json or {}
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid pid"}), 400
    if pid <= 1 or pid > 1000000:
        return jsonify({"error": "invalid pid range"}), 400
    if pid == MY_PID:
        return jsonify({"error": "refusing to change own priority"}), 400
    try:
        import psutil
        if not psutil.pid_exists(pid):
            return jsonify({"error": f"No process with pid {pid}"}), 404
    except Exception:  # noqa: BLE001
        pass
    nice = data.get("nice")
    affinity = data.get("affinity")
    if nice is None and affinity is None:
        return jsonify({"error": "nice or affinity is required"}), 400
    if nice is not None:
        try:
            nice = int(nice)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid nice value"}), 400
        if not -20 <= nice <= 19:
            return jsonify({"error": "nice must be between -20 and 19"}), 400
    if affinity is not None:
        cpus = str(affinity)
        if not cpus or len(cpus) > 64 or any(
                c not in "0123456789,-" for c in cpus):
            return jsonify({"error": "invalid affinity list"}), 400
    ok, detail = True, ""
    if nice is not None:
        ok, detail = _renice(pid, nice)
    if ok and affinity is not None:
        ok, detail = _set_affinity(pid, cpus)
    if not ok:
        _audit("process-priority", pid=pid, outcome="failed", detail=detail)
        return jsonify({"error": detail or "priority change failed",
                        "exit_code": 1}), 500
    # Post-action verification: re-read the process priority state.
    verified_nice = verified_affinity = None
    try:
        import psutil
        p = psutil.Process(pid)
        try:
            verified_nice = p.nice()
        except Exception:  # noqa: BLE001
            pass
        try:
            verified_affinity = p.cpu_affinity()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass
    verified = True
    if nice is not None and verified_nice is not None:
        verified = verified_nice == nice
    _audit("process-priority", pid=pid, nice=nice,
           affinity=affinity if affinity is not None else None,
           verified=verified)
    return jsonify({"result": f"Priority of pid {pid} updated",
                    "verified": verified, "nice": verified_nice,
                    "affinity": verified_affinity})


@bp.route("/api/process/history")
@rate_limit("30 per minute")
def api_process_history():
    """Per-process resource history from the persistent top-consumers log —
    answers 'what caused the CPU spike earlier?'. ?minutes=5..1440."""
    minutes = min(max(_int_or(request.args.get("minutes", 60), 60), 5), 1440)
    try:
        from . import store
        rows = store.process_history(time.time() - minutes * 60)
        series = {}
        for row in rows:
            entry = series.setdefault(str(row["pid"]), {
                "pid": row["pid"], "name": row["name"], "user": row["user"],
                "samples": []})
            entry["samples"].append({"t": row["ts"], "cpu": row["cpu"],
                                     "mem": row["mem"]})
        out = sorted(series.values(),
                     key=lambda e: max((s["cpu"] or 0) for s in e["samples"]) if e["samples"] else 0,
                     reverse=True)[:20]
        return jsonify({"series": out, "minutes": minutes,
                        "available": store.available()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/process/kill", methods=["POST"])
@rate_limit("30 per minute")
def api_process_kill():
    data = request.json or {}
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return jsonify({"error": "invalid pid"}), 400
    if pid <= 1:
        return jsonify({"error": "refusing to kill init"}), 400
    if pid == MY_PID:
        return jsonify({"error": "refusing to kill self"}), 400
    if pid > 1000000:
        return jsonify({"error": "invalid pid range"}), 400
    try:
        import psutil
        p = psutil.Process(pid)
        name = p.name()
        p.terminate()
        try:
            p.wait(timeout=3)
        except psutil.TimeoutExpired:
            p.kill()
            try:
                p.wait(timeout=2)
            except psutil.TimeoutExpired:
                pass
        # Post-action verification: confirm the pid is actually gone (a zombie
        # entry may briefly remain until the parent reaps it).
        verified = _pid_gone(pid)
        _audit("process-kill", pid=pid, name=name, via="direct",
               verified=verified)
        # No server-side HTML-escaping: the frontend esc()s every value it
        # renders (escaping here as well would double-escape the display).
        return jsonify({"result": f"Terminated {name} (pid {pid})",
                        "verified": verified})
    except psutil.NoSuchProcess:
        return jsonify({"error": f"No process with pid {pid}"}), 404
    except (psutil.AccessDenied, psutil.TimeoutExpired, PermissionError):
        code, out, err = run_privileged("monitoring-kill", ["--force", str(pid)], timeout=15)
        if code != 0:
            return jsonify({"error": (((err or out) or "kill failed").strip()[:200])}), 403
        verified = _pid_gone(pid)
        _audit("process-kill", pid=pid, via="helper", verified=verified)
        return jsonify({"result": f"Killed pid {pid} (privileged helper)",
                        "verified": verified})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

"""Process listing and bounded kill control."""
from flask import Blueprint, jsonify, request
from markupsafe import escape

from .commands import run_privileged
from .common import MY_PID, _audit, _cached, _int_or
from .security import rate_limit

bp = Blueprint("processes", __name__)

_PRIMED = {"flag": False}


def _build_processes(limit):
    import psutil
    procs = []
    attrs = ["pid", "name", "username", "memory_percent", "status", "nice"]
    for p in psutil.process_iter(attrs):
        try:
            if p.pid == MY_PID:
                continue
            info = p.info
            cpu = p.cpu_percent(interval=None)
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
def api_processes():
    limit = min(max(_int_or(request.args.get("limit"), 25), 5), 200)
    try:
        data = _cached(f"_procs_{limit}", 3, lambda: _build_processes(limit))
        return jsonify(data)
    except Exception as e:
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
        _audit("process-kill", pid=pid, name=name, via="direct")
        return jsonify({"result": f"Terminated {escape(name)} (pid {pid})"})
    except psutil.NoSuchProcess:
        return jsonify({"error": f"No process with pid {pid}"}), 404
    except (psutil.AccessDenied, psutil.TimeoutExpired, PermissionError):
        code, out, err = run_privileged("monitoring-kill", ["--force", str(pid)], timeout=15)
        if code != 0:
            return jsonify({"error": escape(((err or out) or "kill failed").strip()[:200])}), 403
        _audit("process-kill", pid=pid, via="helper")
        return jsonify({"result": f"Killed pid {pid} (privileged helper)"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

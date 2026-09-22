"""Journal log viewer (journalctl with syslog fallback)."""
import os

from flask import Blueprint, jsonify, request

from .commands import run, which
from .common import _int_or
from .security import rate_limit

bp = Blueprint("logs", __name__)


@bp.route("/api/logs")
@rate_limit("60 per minute")
def api_logs():
    lines = min(max(_int_or(request.args.get("lines", 60), 60), 10), 500)
    prio = min(max(_int_or(request.args.get("prio", 0), 0), 0), 7)  # journalctl -p range
    # Plain length-limit only: matching is a Python substring test (never a
    # shell/regex), so stripping characters would just corrupt searches like
    # "C++", "a=b" or "foo|bar".
    grep = (request.args.get("grep", "") or "")[:80].strip()
    if which("journalctl"):
        argv = ["journalctl", "--no-pager", "-n", str(lines), "-o", "short"]
        if prio:
            argv += ["-p", str(prio)]
        # Log reads are handled through group access (systemd-journal/adm);
        # sudo is intentionally not granted for reading journal content.
        code, out, err = run(argv, timeout=15, sudo=False)
        if grep and out:
            out = "\n".join(l for l in out.splitlines() if grep.lower() in l.lower())
        if code != 0 and not out:
            out = err or "journalctl failed"
    else:
        for cand in ("/var/log/syslog", "/var/log/messages"):
            if os.path.exists(cand):
                code, out, _ = run(["tail", "-n", str(lines), cand], timeout=10, sudo=False)
                break
        else:
            out = "No journalctl and no readable syslog found."
        if grep and out:
            out = "\n".join(l for l in out.splitlines() if grep.lower() in l.lower())
    # Limit total output size to prevent memory exhaustion
    return jsonify({"logs": out[-60000:]})

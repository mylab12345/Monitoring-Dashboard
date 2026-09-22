"""Index page and liveness/version endpoints (no system data)."""
import os
import sys
from datetime import datetime

from flask import Blueprint, jsonify, render_template

from .commands import PASSWORDLESS_SUDO
from .common import APP_HOME, APP_VERSION, LOAD_ERRORS, PRIVILEGE_DIR
from .security import rate_limit

bp = Blueprint("web", __name__)


@bp.route("/")
def index():
    return render_template("index.html")


@bp.route("/api/health")
@rate_limit("120 per minute")
def api_health():
    """Cheap liveness/readiness probe used by the UI and service monitors.

    Independent of psutil and external commands so a partially degraded host
    can still report that the Flask API is alive. ``modules`` lists any feature
    module that failed to load (graceful degradation reporting).
    """
    return jsonify({
        "status": "ok",
        "service": "Monitoring API",
        "version": APP_VERSION,
        "pid": os.getpid(),
        "time": datetime.now().isoformat(),
        "modules": [m["module"] for m in LOAD_ERRORS],
    })


@bp.route("/api/version")
@rate_limit("60 per minute")
def api_version():
    return jsonify({
        "app": "Monitoring",
        "version": APP_VERSION,
        "python": sys.version.split()[0],
        "home": APP_HOME,
        "euid": os.geteuid(),
        "root": os.geteuid() == 0,
        "sudo": PASSWORDLESS_SUDO,
        "privilege_dir": PRIVILEGE_DIR,
        "service_user": "monitoring",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })

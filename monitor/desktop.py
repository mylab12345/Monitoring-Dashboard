"""Desktop app integration."""
import os
import shutil
import subprocess

from flask import Blueprint, jsonify, request

from .common import _audit
from .security import rate_limit

bp = Blueprint("desktop", __name__)


@bp.route("/api/open_app", methods=["POST"])
@rate_limit("5 per minute")
def api_open_app():
    """Open the native desktop window (monitoring-app) from the dashboard.
    Only meaningful when browsing on the same machine."""
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        return jsonify({"error": "No graphical session on the server — run 'monitoring-app' locally instead."}), 400
    launcher = shutil.which("monitoring-app") or "/usr/local/bin/monitoring-app"
    if not os.path.exists(launcher):
        return jsonify({"error": "monitoring-app launcher not installed"}), 404
    subprocess.Popen([launcher], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    _audit("open-app")
    return jsonify({"result": "Desktop window launched"})

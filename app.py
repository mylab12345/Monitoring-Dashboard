#!/usr/bin/env python3
"""
Monitoring — Universal Linux System Dashboard (entrypoint).

This file is a thin shim: the application lives in the `monitor` package
(``monitor/``). ``create_app()`` imports and registers each feature blueprint
defensively, so a syntax error, bad import or broken route in one feature area
can no longer take down the whole dashboard — it degrades gracefully and is
reported by ``/api/health``.

The import is deliberately defensive: if ``monitor/`` is missing (e.g. after an
update performed by an *older* update.sh that did not know about the new
directory), this file prints a clear, actionable error instead of a cryptic
traceback, so a `git pull`/update can never leave the operator guessing.

Run:  python3 app.py   (binds 127.0.0.1:$MONITORING_PORT by default, secure)
Requirements: flask, psutil  (see requirements.txt)
Privileges:   the service runs as the non-login account "monitoring".
              Privileged controls (systemd/package/journal/libvirt/qemu/kill)
              go through installed helper commands that the account may
              execute passwordlessly via sudo. No shell is ever used and no
              sudo ALL is granted.
"""
import logging
import os
import sys

# The application lives in the `monitor` package (see monitor/__init__.py).
try:
    from monitor import create_app
    from monitor.common import APP_BIND, APP_PORT, APP_VERSION, AUTH_TOKEN, LOG
    IMPORT_ERROR = None
except Exception as exc:  # noqa: BLE001 — must not mask the real reason
    create_app = None
    APP_BIND = os.environ.get("MONITORING_BIND", "127.0.0.1")
    APP_PORT = 8050
    APP_VERSION = "unknown"
    AUTH_TOKEN = ""
    LOG = logging.getLogger("monitoring")
    IMPORT_ERROR = str(exc)

app = create_app() if create_app is not None else None


def _configure_logging():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log_file = os.environ.get("MONITORING_LOG_FILE")
    if log_file:
        try:
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            fh = logging.FileHandler(log_file)
            fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
            LOG.addHandler(fh)
        except Exception as exc:
            LOG.warning("Could not open audit log file %s: %s", log_file, exc)


if __name__ == "__main__":
    _configure_logging()

    if app is None:
        LOG.error("FATAL: could not load the 'monitor/' application package.")
        LOG.error("Reason: %s", IMPORT_ERROR)
        LOG.error("")
        LOG.error("This usually means the app was only partially updated — the")
        LOG.error("modular backend lives in the 'monitor/' directory next to this")
        LOG.error("file, and it is missing or unreadable.")
        LOG.error("")
        LOG.error("Fix (pick one):")
        LOG.error("  1. sudo ./update.sh            # from an up-to-date checkout")
        LOG.error("  2. sudo bash install.sh         # full (re)install")
        LOG.error("  3. git -C <checkout> pull  &&  sudo <checkout>/update.sh")
        LOG.error("")
        LOG.error("Then restart: monitoring restart  (or: systemctl restart monitoring)")
        sys.exit(1)

    LOG.info("Starting Monitoring v%s (pid %d, port %d, bind %s, auth %s)",
             APP_VERSION, os.getpid(), APP_PORT, APP_BIND,
             "enabled" if AUTH_TOKEN else "disabled — set MONITORING_TOKEN to protect the API")
    if not AUTH_TOKEN and APP_BIND != "127.0.0.1":
        LOG.warning("No MONITORING_TOKEN set and binding %s — the dashboard grants full "
                    "system control to anyone who can reach port %d. Set MONITORING_TOKEN "
                    "or bind 127.0.0.1 (MONITORING_BIND) if this host is on a network.",
                    APP_BIND, APP_PORT)

    # Production server: prefer waitress (a real, hardened WSGI server) when
    # installed; fall back to Flask's built-in server for minimal installs.
    use_waitress = os.environ.get("MONITORING_SERVER", "waitress").lower() != "flask"
    if use_waitress:
        try:
            from waitress import serve as _waitress_serve
            _waitress_serve(app, host=APP_BIND, port=APP_PORT, threads=8,
                            ident="monitoring", channel_timeout=120)
            sys.exit(0)
        except ImportError:
            pass
    app.run(host=APP_BIND, port=APP_PORT, debug=False, threaded=True)

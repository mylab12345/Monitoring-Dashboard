"""Monitoring — modular Flask application factory.

The original single-file ``app.py`` (2,300+ lines) has been split into this
package so that a fault in one feature area — a syntax error, a bad import, a
broken route — can no longer take down the entire dashboard. ``create_app()``
imports and registers each blueprint defensively and reports any module that
failed to load, so the app degrades gracefully instead of crashing.
"""
import importlib
import logging  # noqa: F401  (re-exported for app.py)
import os

from .common import APP_HOME, LOG, LOAD_ERRORS

# (module_name, blueprint_attribute) for every feature area. Registration
# order is not significant: each blueprint is registered independently and a
# failure in one is contained (recorded in LOAD_ERRORS) rather than fatal.
BLUEPRINTS = [
    ("monitor.web", "bp"),
    ("monitor.privileges", "bp"),
    ("monitor.metrics", "bp"),
    ("monitor.processes", "bp"),
    ("monitor.services", "bp"),
    ("monitor.logs", "bp"),
    ("monitor.system", "bp"),
    ("monitor.packages", "bp"),
    ("monitor.selfrepair", "bp"),
    ("monitor.fixes", "bp"),
    ("monitor.maintain", "bp"),
    ("monitor.diagnostics", "bp"),
    ("monitor.desktop", "bp"),
    ("monitor.vms", "bp"),
]


def create_app():
    """Build the Flask application, registering each feature module safely.

    Flask and the security integration are imported lazily so lightweight
    utilities such as :mod:`monitor.common` remain usable by maintenance
    scripts and tests on systems where the web dependencies are not installed.
    Calling ``create_app`` still fails clearly when those dependencies are
    actually needed.
    """
    from flask import Flask

    from .security import register_security

    app = Flask(
        "monitoring",
        template_folder=os.path.join(APP_HOME, "templates"),
        static_folder=os.path.join(APP_HOME, "static"),
    )
    app.config["SECRET_KEY"] = os.environ.get("MONITORING_SECRET_KEY", os.urandom(32).hex())
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["PERMANENT_SESSION_LIFETIME"] = 3600

    # ProxyFix: handle X-Forwarded-For / X-Forwarded-Proto when behind reverse proxy
    # This ensures client IPs are correct for rate limiting and logging.
    try:
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    except ImportError:
        pass

    register_security(app)

    # Start the background metrics sampler once (idempotent).
    from .metrics import start_sampler
    start_sampler()

    # Register feature blueprints defensively: a broken module must not
    # prevent the rest of the dashboard from starting.
    for mod_name, bp_attr in BLUEPRINTS:
        try:
            mod = importlib.import_module(mod_name)
            bp = getattr(mod, bp_attr)
            app.register_blueprint(bp)
        except Exception as exc:  # noqa: BLE001
            LOG.exception("failed to load feature module %s; continuing without it", mod_name)
            LOAD_ERRORS.append({"module": mod_name, "error": str(exc)})

    return app

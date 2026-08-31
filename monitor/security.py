"""Token auth, rate limiting, security headers and error handlers.

The limiter is constructed lazily in :func:`register_security` (the
``rate_limit`` decorator tolerates a missing limiter), so feature blueprints
can import and apply ``@rate_limit`` at decoration time without a live
limiter instance.
"""
import hmac
from functools import wraps

from flask import jsonify, request

from .common import AUTH_TOKEN, LOG

try:
    from flask_limiter import Limiter
    from flask_limiter.util import get_remote_address
    HAS_LIMITER = True
except ImportError:
    HAS_LIMITER = False

# Populated by register_security(); read lazily by rate_limit().
limiter = None


def register_security(app):
    """Attach the limiter, token gate, security headers and error handlers."""
    global limiter
    if HAS_LIMITER:
        limiter = Limiter(
            app=app,
            key_func=get_remote_address,
            default_limits=["600 per minute"],
            storage_uri="memory://",
            strategy="fixed-window",
        )
    app.before_request(_require_token)
    app.after_request(_security_headers)
    app.register_error_handler(404, _not_found)
    app.register_error_handler(405, _method_not_allowed)
    app.register_error_handler(500, _internal_error)
    app.register_error_handler(429, _rate_limited)


def rate_limit(limit_string):
    """Decorator for custom rate limits on specific endpoints.

    Uses flask-limiter when available; otherwise no-op.
    The decorator is evaluated at import time but the limiter is resolved
    lazily at request time to allow graceful degradation.
    """
    def decorator(f):
        # If limiter is already available, apply directly for efficiency
        if HAS_LIMITER and limiter is not None:
            return limiter.limit(limit_string)(f)

        @wraps(f)
        def wrapped(*args, **kwargs):
            if HAS_LIMITER and limiter:
                # Apply limit on each request via limiter's wrapper
                return limiter.limit(limit_string)(f)(*args, **kwargs)
            return f(*args, **kwargs)
        # Preserve original function for introspection
        wrapped._rate_limit = limit_string
        return wrapped
    return decorator


def _token_ok():
    # Only accept token via headers, never via query params (would leak in logs)
    supplied = request.headers.get("X-Monitoring-Token", "")
    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        supplied = authz[7:].strip()
    if not supplied:
        return False
    # Constant-time compare to prevent timing attacks
    return hmac.compare_digest(supplied.encode("utf-8", "ignore"), AUTH_TOKEN.encode("utf-8"))


def _require_token():
    """Gate every /api/* route behind the token when MONITORING_TOKEN is set.

    The index page and static assets stay loadable (they contain no data) so
    the UI can prompt for the token; without it every API call returns 401.
    """
    if not AUTH_TOKEN or not request.path.startswith("/api/"):
        return None
    # Allow health check without auth for liveness probes? No, protect all /api
    # Health is cheap and reveals version, but should still require token when set
    # to avoid information disclosure. Keep it protected.
    if _token_ok():
        return None
    return jsonify({"error": "unauthorized"}), 401, {"WWW-Authenticate": "Bearer"}


def _security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-XSS-Protection", "0")
    # Prevent MIME sniffing and clickjacking
    resp.headers.setdefault("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'")
    if AUTH_TOKEN and request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _not_found(e):
    return jsonify({"error": "not found"}), 404


def _method_not_allowed(e):
    return jsonify({"error": "method not allowed"}), 405


def _internal_error(e):
    LOG.exception("Unhandled error on %s", request.path)
    return jsonify({"error": "internal server error"}), 500


def _rate_limited(e):
    return jsonify({"error": "rate limit exceeded, try again later"}), 429

"""Outbound notifications: webhook / ntfy / Gotify (stdlib HTTP only).

Alerts used to live only in the browser. This module lets the dashboard push
notifications to a generic webhook, an ntfy.sh topic or a Gotify server so
threshold breaches and long-running action results reach the administrator
even when no dashboard tab is open.

Everything is opt-in and best-effort: with no channel configured nothing is
sent, and a network failure is logged — never raised into a request path.
"""
import json
import urllib.error
import urllib.request

from flask import Blueprint, jsonify, request

from .common import LOG, _audit
from .security import rate_limit
from . import store

bp = Blueprint("notify", __name__)

KV_KEY = "notify_config"
# Allowed channel kinds and their per-field validation (URL schemes only —
# requests are made with urllib, never through a shell).
KINDS = ("webhook", "ntfy", "gotify")
MAX_URL = 500
MAX_TOKEN = 200
MAX_TEXT = 1500


def _valid_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://")) \
        and len(url) <= MAX_URL and " " not in url and "\n" not in url \
        and "\r" not in url


def _valid_token(token):
    return isinstance(token, str) and len(token) <= MAX_TOKEN \
        and "\n" not in token and "\r" not in token and " " not in token


def get_config():
    cfg = store.kv_get(KV_KEY, {}) or {}
    return cfg if isinstance(cfg, dict) else {}


def _masked(cfg):
    """Config safe to echo back to the UI (tokens half-hidden)."""
    out = {k: v for k, v in cfg.items() if k != "token"}
    token = cfg.get("token") or ""
    out["token_set"] = bool(token)
    if token and len(token) > 4:
        out["token_hint"] = token[:2] + "…" + token[-2:]
    return out


def _http_post(url, payload, headers, timeout=8):
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    for key, val in headers.items():
        req.add_header(key, val)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def send_notification(title, message):
    """Push one notification to the configured channel. Returns (sent, detail).

    Never raises; used from the sampler, automation thread and action paths.
    """
    cfg = get_config()
    if not cfg.get("enabled"):
        return False, "notifications disabled"
    kind = cfg.get("kind")
    url = cfg.get("url", "")
    if kind not in KINDS or not _valid_url(url):
        return False, "no valid channel configured"
    title = str(title)[:200]
    message = str(message)[:MAX_TEXT]
    try:
        if kind == "webhook":
            _http_post(url, {"title": title, "message": message,
                             "source": "monitoring"}, {})
        elif kind == "ntfy":
            # ntfy: POST JSON with "topic" or use the topic URL directly.
            req = urllib.request.Request(url, data=message.encode("utf-8"),
                                         method="POST")
            req.add_header("Title", title)
            token = cfg.get("token") or ""
            if token:
                req.add_header("Authorization", "Bearer " + token)
            with urllib.request.urlopen(req, timeout=8) as resp:
                pass
        elif kind == "gotify":
            token = cfg.get("token") or ""
            if not _valid_token(token):
                return False, "gotify requires an app token"
            base = url.rstrip("/")
            _http_post(base + "/message?token=" + token,
                       {"title": title, "message": message,
                        "priority": int(cfg.get("priority", 5)
                                        if str(cfg.get("priority", 5)).isdigit()
                                        else 5)}, {})
        LOG.info("notification sent via %s: %s", kind, title)
        return True, "sent"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        LOG.warning("notification via %s failed: %s", kind, exc)
        return False, str(exc)[:200]
    except Exception as exc:  # noqa: BLE001 — never break the caller
        LOG.warning("notification error: %s", exc)
        return False, str(exc)[:200]


@bp.route("/api/notify/config", methods=["GET", "POST"])
@rate_limit("20 per minute")
def api_notify_config():
    if request.method == "GET":
        return jsonify(_masked(get_config()))
    data = request.get_json(silent=True) or {}
    kind = data.get("kind", "")
    url = str(data.get("url", "") or "")
    token = str(data.get("token", "") or "")
    enabled = bool(data.get("enabled"))
    if kind not in KINDS:
        return jsonify({"error": "kind must be webhook, ntfy or gotify"}), 400
    if not _valid_url(url):
        return jsonify({"error": "invalid url"}), 400
    if token and not _valid_token(token):
        return jsonify({"error": "invalid token"}), 400
    priority = 5
    try:
        priority = min(max(int(data.get("priority", 5)), 1), 10)
    except (TypeError, ValueError):
        priority = 5
    cfg = {"enabled": enabled, "kind": kind, "url": url, "token": token,
           "priority": priority}
    store.kv_set(KV_KEY, cfg)
    _audit("notify-config", kind=kind, enabled=enabled)
    return jsonify({"ok": True, "config": _masked(cfg)})


@bp.route("/api/notify/test", methods=["POST"])
@rate_limit("5 per minute")
def api_notify_test():
    sent, detail = send_notification("Monitoring test",
                                     "Test notification from the Monitoring "
                                     "dashboard.")
    _audit("notify-test", outcome="sent" if sent else "failed", detail=detail)
    return jsonify({"sent": sent, "detail": detail})

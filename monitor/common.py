"""Shared configuration, logging, and small utilities.

Kept dependency-light on purpose: this module must not import Flask or
psutil, so every part of the app can import it without a heavy import chain.
"""
import logging
import os
import re
import threading
import time

# The app home (a repo checkout or /opt/monitoring). This file lives one level
# below the app root (monitor/common.py), so the default is the parent of this
# package's directory.
APP_HOME = os.environ.get("MONITORING_HOME") or os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))


def _env_int(name, default, lo, hi):
    """Read an integer env var safely; fall back to default when invalid."""
    try:
        v = int(os.environ.get(name, str(default)).strip())
    except (TypeError, ValueError):
        v = default
    return v if lo <= v <= hi else default


APP_PORT = _env_int("MONITORING_PORT", 8050, 1, 65535)
APP_BIND = os.environ.get("MONITORING_BIND", "127.0.0.1")
# Validate bind address: allow IP/hostname characters only, no shell metachars
if not re.fullmatch(r"[0-9A-Za-z_.:\-]*", APP_BIND):
    APP_BIND = "127.0.0.1"

# Optional shared-secret token. When set, every /api/* request must carry
# "Authorization: Bearer <token>" (or "X-Monitoring-Token: <token>").
AUTH_TOKEN = os.environ.get("MONITORING_TOKEN", "").strip()
PRIVILEGE_DIR = os.environ.get("MONITORING_PRIVILEGE_DIR", "/usr/local/lib/monitoring")
SUDOERS_FILE = "/etc/sudoers.d/monitoring"

LOG = logging.getLogger("monitoring")


def _audit(event, **fields):
    """Record a privileged/mutating action for the operator's audit trail."""
    try:
        details = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
        LOG.info("action=%s%s%s", event, " " if details else "", details)
    except Exception:
        pass


def _read_version():
    try:
        with open(os.path.join(APP_HOME, "VERSION")) as f:
            return f.read().strip()
    except Exception:
        return "dev"


APP_VERSION = _read_version()

# PID of this process; used to exclude the dashboard itself from process lists.
MY_PID = os.getpid()


def _int_or(value, default=0):
    try:
        return int(str(value).strip())
    except Exception:
        return default


# Response cache — prevents duplicate work when multiple browser tabs or the
# desktop app poll the same endpoint simultaneously.
# Includes periodic eviction of expired entries to avoid unbounded growth.
_resp_cache = {}  # key -> (expiry, payload)
_resp_lock = threading.Lock()
_CACHE_MAX_SIZE = 128
_CACHE_CLEAN_INTERVAL = 60
_last_cache_clean = 0


def _cached(key, ttl, fn):
    """Return cached result of fn() if called within ttl seconds."""
    global _last_cache_clean
    now = time.time()
    with _resp_lock:
        # Periodic cleanup of expired entries
        if now - _last_cache_clean > _CACHE_CLEAN_INTERVAL:
            expired = [k for k, (exp, _) in _resp_cache.items() if exp <= now]
            for k in expired:
                _resp_cache.pop(k, None)
            # If still too large, evict oldest (by expiry)
            if len(_resp_cache) > _CACHE_MAX_SIZE:
                sorted_keys = sorted(_resp_cache.keys(), key=lambda k: _resp_cache[k][0])
                for k in sorted_keys[: len(_resp_cache) - _CACHE_MAX_SIZE]:
                    _resp_cache.pop(k, None)
            _last_cache_clean = now
        entry = _resp_cache.get(key)
        if entry and entry[0] > now:
            return entry[1]
    result = fn()
    with _resp_lock:
        _resp_cache[key] = (now + ttl, result)
    return result


def _cache_clear(key):
    """Drop one cached entry (used after a mutation that changes its data)."""
    with _resp_lock:
        _resp_cache.pop(key, None)


def _cache_clear_all():
    """Clear entire cache (used for testing or full reset)."""
    with _resp_lock:
        _resp_cache.clear()


# Feature modules that failed to load at startup. Surfaced by /api/health so
# an operator can see graceful degradation instead of a hard crash.
LOAD_ERRORS = []

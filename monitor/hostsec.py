"""Host security posture: firewall, MAC, secure boot, hardening, logins.

Complements the app-level security.py with the *host-level* picture an admin
needs: is the firewall on, is SELinux/AppArmor active, is Secure Boot on, are
the kernel hardening sysctls sane, who logged in, is anything hammering SSH.
Read-only by default; the only mutation is the reversible kernel-hardening
profile which goes through the whitelisted monitoring-sysctl helper.
"""
import re

from flask import Blueprint, jsonify, request

from .commands import run, run_privileged, which
from .common import _audit, _cached
from .osinfo import _mac_framework, _secure_boot
from .packages import _pkg_manager, _updatable_packages
from .security import rate_limit

bp = Blueprint("hostsec", __name__)

# Kernel hardening sysctl scorecard. (want) is the minimum value considered
# hardened; lower values are reported as findings. Keys are fixed — the
# monitoring-sysctl helper only ever touches exactly these.
HARDENING_KEYS = (
    ("kernel.randomize_va_space", 2, "Address space layout randomisation"),
    ("kernel.kptr_restrict", 1, "Kernel pointer restriction"),
    ("kernel.dmesg_restrict", 1, "Restrict dmesg to privileged users"),
    ("kernel.yama.ptrace_scope", 1, "Restrict ptrace to parent processes"),
    ("kernel.perf_event_paranoid", 2, "Restrict perf events"),
    ("kernel.unprivileged_bpf_disabled", 1, "Disable unprivileged BPF"),
)


def _read_sysctl(key):
    path = "/proc/sys/" + key.replace(".", "/")
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:  # noqa: BLE001
        return None


def _hardening_state():
    items = []
    score = max_score = 0
    for key, want, label in HARDENING_KEYS:
        value = _read_sysctl(key)
        known = value is not None
        ok = known and value >= want
        if known:
            max_score += 1
            score += 1 if ok else 0
        items.append({"key": key, "label": label, "value": value,
                      "want_at_least": want, "ok": ok, "known": known})
    return {"items": items, "score": score, "max_score": max_score,
            "grade": ("A" if score == max_score and max_score > 0 else
                      "B" if score >= max_score * 0.75 else
                      "C" if score >= max_score * 0.5 else
                      "D" if max_score else "?")}


def _firewall_state():
    """Best-effort firewall posture (several tools need root for detail)."""
    active = []
    for unit in ("ufw", "firewalld", "nftables", "iptables", "ipset"):
        code, out, _ = run(["systemctl", "is-active", unit], timeout=5)
        if (out or "").strip() == "active":
            active.append(unit)
    detail = ""
    known = bool(active)
    if which("ufw"):
        code, out, err = run(["ufw", "status"], timeout=8)
        text = (out or "") + (err or "")
        if "Status: active" in text:
            known = True
            active.append("ufw") if "ufw" not in active else None
        elif "Status: inactive" in text:
            known = True
        elif code != 0:
            detail = "ufw status requires root for details"
    return {"active_services": sorted(set(active)), "known": known or bool(active),
            "detail": detail}


def _exposure():
    """Listening sockets bound beyond loopback (ss — group-readable)."""
    code, out, _ = run(["ss", "-tulpnH"], timeout=10)
    if code != 0:
        return {"known": False, "open_to_network": []}
    open_list = []
    for line in (out or "").splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        local = parts[4]
        addr = local.rsplit(":", 1)[0] if ":" in local else local
        port = local.rsplit(":", 1)[1] if ":" in local else "?"
        if addr not in ("127.0.0.1", "::1", "localhost", "127.0.0.53"):
            proc = parts[-1] if len(parts) > 5 else ""
            open_list.append({"addr": addr, "port": port,
                              "process": proc[:80]})
    return {"known": True, "open_to_network": open_list[:40]}


def _login_stats():
    """Sessions, recent logins and SSH brute-force signals (journal)."""
    sessions = []
    code, out, _ = run(["who"], timeout=8)
    for line in (out or "").splitlines():
        parts = line.split(None, 4)
        if len(parts) >= 2:
            sessions.append({"user": parts[0], "terminal": parts[1],
                             "since": " ".join(parts[2:])})
    recent = []
    code, out, _ = run(["last", "-n", "20", "-a"], timeout=8)
    for line in (out or "").splitlines()[:20]:
        if line.startswith(("wtmp", "btmp")) or not line.strip():
            continue
        recent.append(line.strip()[:160])
    failed = {"count_24h": None, "samples": [], "known": False}
    if which("journalctl"):
        code, out, _ = run(["journalctl", "--since", "-24h", "-q", "--no-pager",
                            "_COMM=sshd"], timeout=15)
        if code == 0:
            failed["known"] = True
            hits = [l for l in (out or "").splitlines()
                    if ("Failed password" in l or "Invalid user" in l
                        or "authentication failure" in l
                        or "maximum authentication attempts" in l)]
            failed["count_24h"] = len(hits)
            failed["samples"] = hits[-5:]
    return {"sessions": sessions[:12], "recent_logins": recent,
            "failed_auth": failed,
            "brute_force_suspected": bool(failed.get("count_24h") or 0) >= 20}


def _package_integrity():
    """Modified system binaries check (debsums / rpm -V) — tamper signal."""
    mgr = _pkg_manager()
    if mgr == "apt" and which("debsums"):
        code, out, err = run(["debsums", "-c"], timeout=45)
        if code in (0, 1):
            modified = [l.strip() for l in (out or "").splitlines()
                        if l.strip()][:30]
            return {"backend": "debsums", "checked": True,
                    "modified": modified, "modified_count": len(modified)}
        return {"backend": "debsums", "checked": False,
                "detail": (err or out or "debsums failed")[:200]}
    if mgr in ("dnf", "yum", "zypper") and which("rpm"):
        code, out, err = run(["rpm", "-Va", "--nomtime", "--nosize",
                              "--nolinkto", "--nocaps", "--nodev"],
                             timeout=60)
        if code in (0, 1):
            modified = [l.strip() for l in (out or "").splitlines()
                        if l.strip()][:30]
            return {"backend": "rpm-verify", "checked": True,
                    "modified": modified, "modified_count": len(modified)}
        return {"backend": "rpm-verify", "checked": False,
                "detail": (err or out or "rpm -V failed")[:200]}
    return {"backend": None, "checked": False,
            "detail": "no integrity tool available (install debsums or use "
                      "rpm-based verify)"}


def _security_updates():
    """Split pending updates into security vs regular (per manager best-effort)."""
    mgr = _pkg_manager()
    total = _updatable_packages()
    info = {"manager": mgr, "security_count": None, "security_known": False,
            "total_count": total.get("count", 0)}
    try:
        if mgr in ("dnf", "yum") and which("updateinfo"):
            code, out, _ = run([mgr, "updateinfo", "list", "security",
                                "updates"], timeout=30)
            if code == 0:
                lines = [l for l in (out or "").splitlines() if l.strip()]
                info["security_count"] = len(lines)
                info["security_known"] = True
        elif mgr == "zypper":
            code, out, _ = run(["zypper", "--quiet", "list-patches",
                                "--category", "security"], timeout=45)
            if code == 0:
                lines = [l for l in (out or "").splitlines()
                         if l.strip() and not l.startswith(("S.No", "Repository"))]
                info["security_count"] = len(lines)
                info["security_known"] = True
        elif mgr == "apt":
            code, out, _ = run(["apt-get", "--just-print", "upgrade"], timeout=45)
            if code == 0:
                inst = [l for l in (out or "").splitlines()
                        if l.startswith("Inst ")]
                sec = [l for l in inst if "security" in l.lower()]
                info["security_count"] = len(sec)
                info["security_known"] = True
        # pacman/apk do not tag security updates — stay unknown, not wrong.
    except Exception:  # noqa: BLE001
        pass
    return info


@bp.route("/api/security/posture")
@rate_limit("30 per minute")
def api_security_posture():
    try:
        data = _cached("sec_posture", 120, lambda: {
            "firewall": _firewall_state(),
            "mac": _mac_framework(),
            "secure_boot": _secure_boot(),
            "hardening": _hardening_state(),
            "exposure": _exposure(),
            "logins": _login_stats(),
            "integrity": _package_integrity(),
            "security_updates": _security_updates(),
        })
        return jsonify(data)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/security/logins")
@rate_limit("30 per minute")
def api_security_logins():
    try:
        return jsonify(_cached("sec_logins", 60, _login_stats))
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/security/integrity")
@rate_limit("10 per minute")
def api_security_integrity():
    try:
        return jsonify(_package_integrity())
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/security/updates")
@rate_limit("30 per minute")
def api_security_updates():
    try:
        return jsonify(_security_updates())
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": str(e)}), 500


@bp.route("/api/security/hardening", methods=["GET", "POST"])
@rate_limit("20 per minute")
def api_security_hardening():
    if request.method == "GET":
        try:
            return jsonify(_hardening_state())
        except Exception as e:  # noqa: BLE001
            return jsonify({"error": str(e)}), 500
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    profile = data.get("profile", "standard")
    if action not in ("apply", "revert"):
        return jsonify({"error": "action must be apply or revert"}), 400
    if action == "apply" and profile not in ("standard", "hardened"):
        return jsonify({"error": "profile must be standard or hardened"}), 400
    argv = ["--apply", "--profile", profile] if action == "apply" else ["--revert"]
    code, out, err = run_privileged("monitoring-sysctl", argv, timeout=30)
    if code != 0:
        detail = ((err or out) or "helper failed").strip()[:300]
        _audit("hardening", action=action, profile=profile, outcome="failed",
               exit_code=code)
        return jsonify({"error": detail, "exit_code": code}), 500
    _audit("hardening", action=action, profile=profile, outcome="success")
    return jsonify({"result": (out or "done").strip()[:1000],
                    "state": _hardening_state()})


@bp.route("/api/audit")
@rate_limit("60 per minute")
def api_audit():
    """Server-side audit trail of every privileged action (persistent)."""
    from . import store
    limit = min(max(int(request.args.get("limit", 100) or 100), 1), 500)
    return jsonify({"entries": store.recent_audit(limit),
                    "available": store.available()})

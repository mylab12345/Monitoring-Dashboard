"""monitoring_hostns - run a privileged helper outside the service sandbox.

monitoring.service runs with ``ProtectSystem=full``. Whenever the unit that
the *running* service was started from lacks ``ReadWritePaths=`` for the
package paths (an older install, an update that was never followed by a
restart, a hand-edited unit, ...) /usr, /etc and /boot are read-only inside
the service mount namespace - even for root. Package transactions started
from the dashboard then fail with "required filesystem is read-only"
(exit 78) although the host itself is perfectly writable.

A root helper can leave that namespace without loosening the sandbox of the
dashboard process: it asks PID 1 (``systemd-run``) to run the *same* helper
with the *same, already validated* argv as a transient service. That unit:

* runs in the host mount namespace, so package paths are writable exactly as
  for ``sudo apt-get`` in a terminal;
* runs outside the monitoring.service cgroup, so a dashboard restart
  (needrestart after a python upgrade, self-repair, self-update) can no
  longer kill dpkg half-way through a transaction.

No privilege is added: the caller is already root through its whitelisted
sudo rule, nothing user-controlled reaches systemd-run, the dashboard
process itself keeps its read-only view of the system, and the monitoring
account still has no write permission anywhere under /usr or /etc.

Output is relayed through a root-only file in /run so the transient unit
never depends on the caller staying alive (a broken pipe must not be able to
abort a package manager).
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

ENV_FLAG = "MONITORING_HOST_NAMESPACE"
ENV_STATUS = "MONITORING_HOST_STATUS_FILE"
RUN_DIR = "/run/monitoring-helpers"
POLL_S = 0.5
# How long a finished/vanished unit may take to publish its status file.
SETTLE_S = 5.0


def in_host_namespace():
    """True inside the transient unit started by :func:`run_in_host`."""
    return os.environ.get(ENV_FLAG) == "1"


def available():
    """True when this process can hand work to PID 1 via systemd-run."""
    return (os.geteuid() == 0
            and not in_host_namespace()
            and os.path.isdir("/run/systemd/system")
            and shutil.which("systemd-run") is not None
            and shutil.which("systemctl") is not None)


def mount_read_only(path):
    """True/False for a read-only mount at `path`; None when unknown."""
    if not os.path.exists(path):
        return False
    findmnt = shutil.which("findmnt")
    if not findmnt:
        return None
    try:
        res = subprocess.run(
            [findmnt, "--noheadings", "--output", "OPTIONS", "--target", path],
            shell=False, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    out = (res.stdout or "").strip()
    if res.returncode != 0 or not out:
        return None
    return "ro" in {part.strip() for part in out.split(",")}


def read_only_paths(paths):
    """Subset of `paths` that is (known to be) mounted read-only here."""
    return [p for p in paths if mount_read_only(p) is True]


def report_status(code):
    """Publish the exit code for the waiting caller (transient side only)."""
    path = os.environ.get(ENV_STATUS, "")
    if not (in_host_namespace() and path):
        return
    # Only ever write inside our own root-owned run directory.
    real = os.path.realpath(path)
    if os.path.dirname(os.path.dirname(real)) != RUN_DIR:
        return
    try:
        tmp = real + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(str(int(code)))
        os.replace(tmp, real)
    except (OSError, ValueError):
        pass


def main_wrapper(main, argv):
    """Run a helper main(); always report its exit code when transient."""
    code = 1
    try:
        code = main(argv)
        return code
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        report_status(code if isinstance(code, int) else 1)


def _systemctl_state(unit):
    try:
        res = subprocess.run(["systemctl", "is-active", unit], shell=False,
                             capture_output=True, text=True, timeout=10)
        return (res.stdout or "").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _relay(fh, out):
    chunk = fh.read()
    if chunk:
        out.write(chunk)
        out.flush()


def run_in_host(helper_path, args, label):
    """Run `helper_path args` as a transient unit in the host namespace.

    Returns (exit_code, None) after the transient run finished, with its
    output relayed to stdout; or (None, reason) when it could not be started,
    in which case the caller must fall back to its own error handling.
    `label` must match [a-z0-9-]+ (it becomes part of the unit name).
    """
    if not available():
        return None, "systemd-run is not available in this context"
    try:
        os.makedirs(RUN_DIR, mode=0o700, exist_ok=True)
        os.chmod(RUN_DIR, 0o700)
        work = tempfile.mkdtemp(prefix="run-", dir=RUN_DIR)
        log_path = os.path.join(work, "output.log")
        status_path = os.path.join(work, "status")
        open(log_path, "wb").close()
    except OSError as exc:
        return None, "could not prepare {}: {}".format(RUN_DIR, exc)

    unit = "monitoring-{}-{}".format(
        label, os.path.basename(work)[len("run-"):].replace("_", "-").lower())
    argv = [
        "systemd-run", "--quiet", "--collect",
        "--unit", unit,
        "--description", "Monitoring dashboard: {} (host namespace)".format(label),
        "--property", "StandardInput=null",
        "--property", "StandardOutput=file:" + log_path,
        "--property", "StandardError=inherit",
        "--setenv", ENV_FLAG + "=1",
        "--setenv", ENV_STATUS + "=" + status_path,
        helper_path,
    ] + list(args)
    try:
        res = subprocess.run(argv, shell=False, capture_output=True, text=True,
                             timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(work, ignore_errors=True)
        return None, "systemd-run failed: {}".format(exc)
    if res.returncode != 0:
        shutil.rmtree(work, ignore_errors=True)
        return None, "systemd-run failed: {}".format(
            (res.stderr or res.stdout or "").strip()[:300])

    out = getattr(sys.stdout, "buffer", None)
    code = None
    gone_since = None
    with open(log_path, "rb") as fh:
        while True:
            if out is not None:
                _relay(fh, out)
            if os.path.exists(status_path):
                break
            state = _systemctl_state(unit)
            if state in ("active", "activating", "reloading", "deactivating"):
                gone_since = None
            else:
                # Finished (or never started) without a status yet: give the
                # helper a moment to publish it, then stop waiting.
                gone_since = gone_since or time.monotonic()
                if time.monotonic() - gone_since > SETTLE_S:
                    break
            time.sleep(POLL_S)
        if out is not None:
            _relay(fh, out)
    try:
        with open(status_path) as sf:
            code = int(sf.read().strip())
    except (OSError, ValueError):
        code = None
    shutil.rmtree(work, ignore_errors=True)
    if code is None:
        print("{}: transient unit {} ended without reporting a result; see "
              "journalctl -u {}".format(os.path.basename(helper_path), unit,
                                        unit), file=sys.stderr)
        return 1, None
    return code, None

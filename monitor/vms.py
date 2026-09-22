"""VM / libvirt management (list, actions, info, resize, config).

No server-side HTML-escaping in this module: the frontend esc()s every value
it renders (escaping here as well would double-escape the display).
"""
import importlib.util
import os

from flask import Blueprint, jsonify, request

from .commands import run, run_privileged, validate_disk_path, validate_vm_name, which
from .common import _audit
from .security import rate_limit

bp = Blueprint("vms", __name__)

VM_ACTIONS = ("start", "shutdown", "reboot", "reset", "destroy", "resume", "suspend")

if not os.environ.get("LIBVIRT_DEFAULT_URI"):
    os.environ["LIBVIRT_DEFAULT_URI"] = "qemu:///system"


def _parse_dominfo_mem_vcpus(dominfo):
    """Extract (mem, vcpus) display strings from `virsh dominfo` output."""
    mem, vcpus = "N/A", "N/A"
    for line in (dominfo or "").splitlines():
        if "Max memory" in line:
            try:
                # virsh reports KiB; convert to GiB for display.
                mem = f"{int(line.split(':')[1].strip().split()[0]) // 1024 // 1024} GB"
            except (IndexError, ValueError):
                mem = line.split(":", 1)[1].strip() if ":" in line else "N/A"
        elif "CPU(s)" in line:
            vcpus = line.split(":", 1)[1].strip() if ":" in line else "N/A"
    return mem, vcpus


@bp.route("/api/vms")
@rate_limit("60 per minute")
def api_vms():
    vms = []
    # One `virsh list --all` already yields name + state for every domain, so
    # only a single `dominfo` per VM is needed for mem/vCPUs (previously this
    # ran dominfo AND domstate per VM — 2N+1 subprocesses for N VMs).
    code, out, err = run(["virsh", "list", "--all"])
    if code == 0 and out.strip():
        for line in out.splitlines()[2:]:  # skip "Id Name State" header + dashes
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[1]
            state = " ".join(parts[2:])  # states may contain spaces ("shut off")
            if not validate_vm_name(name):
                continue
            _, dominfo, _ = run(["virsh", "dominfo", name])
            mem, vcpus = _parse_dominfo_mem_vcpus(dominfo)
            vms.append({"name": name, "state": state, "mem": mem, "vcpus": vcpus})
    if not vms:
        try:
            import libvirt
            conn = libvirt.open("qemu:///system")
            if conn:
                state_map = {0: "nodomain", 1: "running", 2: "blocked", 3: "paused",
                             4: "shutdown", 5: "shutoff", 6: "crashed", 7: "pmsuspended"}
                for dom in conn.listAllDomains():
                    info = dom.info()
                    # info()[1] is max memory in KiB, hence GiB after //1024//1024.
                    vms.append({"name": dom.name(), "state": state_map.get(info[0], "unknown"),
                                "mem": f"{info[1] // 1024 // 1024} GB", "vcpus": str(info[3])})
                conn.close()
        except Exception:
            pass
    return jsonify(vms)


@bp.route("/api/vm/action", methods=["POST"])
@rate_limit("20 per minute")
def api_vm_action():
    data = request.json or {}
    # libvirt is a Python module, not a binary — check it properly.
    libvirt_ok = which("virsh") or importlib.util.find_spec("libvirt") is not None
    if not libvirt_ok:
        return jsonify({"error": "virsh/libvirt not available on this system"}), 400
    name = data.get("name", "")
    action = data.get("action", "")

    if not validate_vm_name(name):
        return jsonify({"error": "invalid VM name"}), 400
    if action not in VM_ACTIONS:
        return jsonify({"error": "invalid action"}), 400

    code, out, err = run_privileged("monitoring-vm", [action, name], timeout=60)
    if code != 0:
        detail = ((err or "") + (out or "")).strip()[:300] or "helper failed"
        _audit("vm-action", action=action, name=name,
               outcome="failed", exit_code=code)
        return jsonify({"error": detail, "exit_code": code}), 500
    _audit("vm-action", action=action, name=name, outcome="success")
    return jsonify({"result": (out or err or f"{action} {name}: ok").strip()[:300]})


@bp.route("/api/vm_info/<name>")
@rate_limit("30 per minute")
def api_vm_info(name):
    if not validate_vm_name(name):
        return jsonify({"error": "invalid name"}), 400
    info = {}
    _, out, _ = run(["virsh", "dominfo", name], timeout=30)
    info["virsh"] = out
    _, out2, _ = run(["virsh", "domblklist", name], timeout=30)
    info["disks"] = out2
    return jsonify(info)


@bp.route("/api/vm_resize", methods=["POST"])
@rate_limit("10 per minute")
def api_vm_resize():
    data = request.json or {}
    name = data.get("name")
    disk_path = data.get("disk_path")
    new_size_gb = data.get("new_size_gb")
    if not (name and disk_path and new_size_gb):
        return jsonify({"error": "name, disk_path, new_size_gb required"}), 400
    if not validate_vm_name(name):
        return jsonify({"error": "invalid VM name"}), 400
    if not validate_disk_path(disk_path):
        return jsonify({"error": "invalid disk path"}), 400
    try:
        new_size_int = int(str(new_size_gb).strip())
    except (TypeError, ValueError):
        new_size_int = 0
    if not 1 <= new_size_int <= 10000:
        return jsonify({"error": "invalid size value (1–10000 GB)"}), 400
    # Resize through the whitelisted qemu helper which verifies the disk
    # actually belongs to the named domain before touching it.
    code, msg, err = run_privileged("monitoring-qemu", ["resize", name, disk_path, str(new_size_int)], timeout=120)
    if code != 0:
        detail = ((err or "") + (msg or "")).strip()[:400] or "helper failed"
        _audit("vm-resize", name=name, disk=disk_path, size_gb=new_size_int,
               outcome="failed", exit_code=code)
        return jsonify({"error": detail, "exit_code": code}), 500
    _audit("vm-resize", name=name, disk=disk_path, size_gb=new_size_int,
           outcome="success")
    return jsonify({"result": (msg or err or "Resize complete").strip()[:400],
                    "disk": disk_path, "size_gb": new_size_int})


@bp.route("/api/vm_config", methods=["POST"])
@rate_limit("10 per minute")
def api_vm_config():
    data = request.json or {}
    name = data.get("name")
    ram_gb = data.get("ram_gb")
    vcpus = data.get("vcpus")
    if not name:
        return jsonify({"error": "name required"}), 400
    if not validate_vm_name(name):
        return jsonify({"error": "invalid VM name"}), 400
    if ram_gb is None and vcpus is None:
        return jsonify({"error": "ram_gb and/or vcpus required"}), 400

    ram_mb = None
    if ram_gb is not None:
        try:
            # The VM tab intentionally accepts quarter-GB values (for small
            # guests), so do not truncate through int("0.5"). Convert to MiB
            # and require an exact MiB value instead.
            ram_value = float(str(ram_gb).strip())
            ram_mb_value = ram_value * 1024
            if not ram_value > 0 or not ram_mb_value.is_integer():
                raise ValueError
            ram_mb = int(ram_mb_value)
        except (TypeError, ValueError, OverflowError):
            return jsonify({"error": "invalid ram_gb value"}), 400
        if not 256 <= ram_mb <= 10485760:
            return jsonify({"error": "ram_gb must be 0.25–10240"}), 400

    vcpu_count = None
    if vcpus is not None:
        try:
            vcpu_count = int(str(vcpus).strip())
        except (TypeError, ValueError):
            return jsonify({"error": "invalid vcpus value"}), 400
        if not 1 <= vcpu_count <= 256:
            return jsonify({"error": "vcpus must be 1–256"}), 400

    _, state_raw, _ = run(["virsh", "-c", "qemu:///system", "domstate", name], timeout=10)
    is_running = "running" in (state_raw or "").lower()

    # One dominfo call serves both the memory and the vCPU section below.
    _, dominfo, _ = run(["virsh", "-c", "qemu:///system", "dominfo", name], timeout=15)
    current_max_kb = 0
    current_max_vcpus = 0
    for line in (dominfo or "").splitlines():
        if "Max memory" in line:
            try:
                current_max_kb = int(line.split(":")[1].strip().split()[0])
            except (IndexError, ValueError):
                pass
        elif "CPU(s)" in line:
            try:
                current_max_vcpus = int(line.split(":")[1].strip())
            except (IndexError, ValueError):
                pass

    errors = []
    notes = []
    # --- Memory ---
    if ram_mb is not None:
        new_max_kb = ram_mb * 1024
        if new_max_kb > current_max_kb:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setmaxmem", name, str(new_max_kb), "--config"],
                timeout=30,
            )
            if code != 0:
                errors.append(f"setmaxmem failed: {(out + err).strip()[:200]}")
            elif is_running:
                notes.append("max memory increase takes effect on next boot")

        if is_running and new_max_kb <= current_max_kb:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setmem", name, str(new_max_kb), "--live"],
                timeout=30,
            )
        else:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setmem", name, str(new_max_kb), "--config"],
                timeout=30,
            )
        if code != 0:
            errors.append(f"setmem failed: {(out + err).strip()[:200]}")

    # --- vCPUs ---
    if vcpu_count is not None:
        if vcpu_count > current_max_vcpus:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setvcpus", name, str(vcpu_count), "--config", "--maximum"],
                timeout=30,
            )
            if code != 0:
                errors.append(f"setvcpus max failed: {(out + err).strip()[:200]}")
            elif is_running:
                notes.append("vCPU increase beyond current max takes effect on next boot")

        if is_running and vcpu_count <= current_max_vcpus:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setvcpus", name, str(vcpu_count), "--live"],
                timeout=30,
            )
        else:
            code, out, err = run_privileged(
                "monitoring-vm-config",
                ["setvcpus", name, str(vcpu_count), "--config"],
                timeout=30,
            )
        if code != 0:
            errors.append(f"setvcpus failed: {(out + err).strip()[:200]}")

    if errors:
        _audit("vm-config", name=name, ram_gb=ram_gb, vcpus=vcpus, error="; ".join(errors))
        return jsonify({"error": "; ".join(errors)}), 500

    _audit("vm-config", name=name, ram_gb=ram_gb, vcpus=vcpus)
    result_msg = f"Configuration updated for {name}"
    if notes:
        result_msg += " (" + "; ".join(notes) + ")"
    return jsonify({"result": result_msg, "name": name,
                     "ram_gb": ram_gb, "vcpus": vcpus, "live": is_running})

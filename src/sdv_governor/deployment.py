"""Hardware doctor, SSH board deployment, and Mac-to-Linux semantic parity verifier (B1, B11, DEP-01..03)."""

from __future__ import annotations

import glob
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import socket
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from .replay import run_full_campaign_suite


def compute_source_tree_sha256(src_dir: Path) -> str:
    """Compute a deterministic SHA-256 across all .py files in `src/sdv_governor`."""
    h = hashlib.sha256()
    for py_file in sorted(src_dir.rglob("*.py")):
        rel = py_file.relative_to(src_dir).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(py_file.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def get_process_rss_kib() -> int:
    """Return current process maximum RSS in KiB."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        # macOS reports ru_maxrss in bytes
        return int(usage // 1024)
    # Linux reports ru_maxrss in KiB
    return int(usage)


def run_doctor(
    repo_root: Path,
    *,
    ssh_target: str = "ardu",
    probe_remote: bool = True,
) -> Dict[str, Any]:
    """Run non-coupled environment & hardware readiness check (`doctor`, Gate B1, DEP-01, DEP-02).

    Never fails if the external board is disconnected; reports remote status as PASS or BLOCKED_CONNECTION.
    """
    code_sha256 = compute_source_tree_sha256(repo_root / "src" / "sdv_governor")
    usb_serial_devices = sorted(glob.glob("/dev/cu.usbmodem*"))

    adb_status = "BLOCKED_CONNECTION"
    adb_devices: List[str] = []
    if shutil.which("adb"):
        try:
            out = subprocess.check_output(["adb", "devices"], text=True, timeout=3)
            lines = [ln.strip() for ln in out.splitlines()[1:] if ln.strip() and "\tdevice" in ln]
            adb_devices = lines
            if lines:
                adb_status = "PASS"
        except Exception:
            adb_status = "BLOCKED_CONNECTION"

    ssh_status = "BLOCKED_CONNECTION"
    remote_info: Dict[str, Any] = {}
    if probe_remote and ssh_target:
        try:
            cmd = [
                "ssh",
                "-o", "BatchMode=yes",
                "-o", "ConnectTimeout=1",
                ssh_target,
                "python3 -c 'import json, os, platform, socket; print(json.dumps({\"hostname\": socket.gethostname(), \"machine\": platform.machine(), \"system\": platform.system(), \"python\": platform.python_version(), \"pid\": os.getpid()}))'",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
            if res.returncode == 0 and res.stdout.strip():
                remote_info = json.loads(res.stdout.strip().splitlines()[-1])
                ssh_status = "PASS"
        except Exception as exc:
            ssh_status = "BLOCKED_CONNECTION"
            remote_info = {"error": str(exc)}

    return {
        "local_host": {
            "status": "PASS",
            "hostname": socket.gethostname(),
            "system": platform.system(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "pid": os.getpid(),
            "rss_kib": get_process_rss_kib(),
            "code_sha256": code_sha256,
            "standalone_ready": True,
        },
        "transports": {
            "usb_serial": {
                "status": "PASS" if usb_serial_devices else "BLOCKED_CONNECTION",
                "devices": usb_serial_devices,
            },
            "adb_usb": {
                "status": adb_status,
                "devices": adb_devices,
                "note": "Ventuno Q USB-C ADB requires external 7-24V DC barrel/pins power if not enumerating",
            },
            "ssh_lan": {
                "status": ssh_status,
                "target": ssh_target,
                "remote_info": remote_info,
            },
        },
        "overall_local_ready": True,
        "remote_board_status": ssh_status,
    }


def sync_and_run_remote_parity(
    repo_root: Path,
    *,
    ssh_target: str = "ardu",
    remote_dir: str = "/home/arduino/sdv-resource-governor",
) -> Dict[str, Any]:
    """Sync codebase to remote Linux board (`ssh ardu`), run tests & campaign, and verify semantic hash parity (DEP-03)."""
    local_code_sha = compute_source_tree_sha256(repo_root / "src" / "sdv_governor")
    local_campaign = run_full_campaign_suite(repo_root)
    local_rss_kib = get_process_rss_kib()

    # Check if remote is reachable
    doc = run_doctor(repo_root, ssh_target=ssh_target, probe_remote=True)
    if doc["remote_board_status"] != "PASS":
        return {
            "status": "BLOCKED_CONNECTION",
            "parity_verified": False,
            "reason": f"SSH target '{ssh_target}' is not currently reachable",
            "local_execution": {
                "hostname": local_campaign["runtime_telemetry"]["hostname"],
                "machine": local_campaign["runtime_telemetry"]["platform_machine"],
                "pid": local_campaign["runtime_telemetry"]["pid"],
                "wall_elapsed_ms": local_campaign["runtime_telemetry"]["wall_elapsed_ms"],
                "rss_kib": local_rss_kib,
                "code_sha256": local_code_sha,
                "campaign_semantic_sha256": local_campaign["campaign_semantic_sha256"],
            },
            "remote_execution": None,
        }

    # Sync files via tar over ssh
    sync_cmd = (
        f"COPYFILE_DISABLE=1 tar --exclude=\"._*\" -cf - -C {repo_root} "
        f"pyproject.toml README.md README.pdf FINAL_STATUS.md CLOSURE_STATUS_V2.json hardware_inventory.json "
        f"requirements_matrix.csv ASSUMPTIONS.yaml src schemas data docs ui tests artifacts/release_r8 artifacts/release_s6 artifacts/final_run "
        f"| ssh -o BatchMode=yes {ssh_target} 'mkdir -p {remote_dir} && tar -xf - -C {remote_dir}'"
    )
    sync_res = subprocess.run(sync_cmd, shell=True, capture_output=True, text=True, timeout=30)
    if sync_res.returncode != 0:
        return {
            "status": "BLOCKED_CONNECTION",
            "parity_verified": False,
            "reason": f"Remote tar sync failed: {sync_res.stderr.strip()}",
        }

    # Execute remote campaign and capture JSON output
    remote_py_snippet = (
        "import json; from pathlib import Path; "
        "from sdv_governor.deployment import compute_source_tree_sha256, get_process_rss_kib; "
        "from sdv_governor.replay import run_full_campaign_suite; "
        f"root = Path('{remote_dir}'); "
        "camp = run_full_campaign_suite(root); "
        "out = {"
        "'code_sha256': compute_source_tree_sha256(root / 'src' / 'sdv_governor'), "
        "'campaign_semantic_sha256': camp['campaign_semantic_sha256'], "
        "'semantic_summary': camp['semantic_summary'], "
        "'runtime_telemetry': camp['runtime_telemetry'], "
        "'rss_kib': get_process_rss_kib()"
        "}; "
        "print('REMOTE_PARITY_JSON:' + json.dumps(out, sort_keys=True))"
    )
    remote_cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        ssh_target,
        f"cd {remote_dir} && PYTHONPATH={remote_dir}/src {remote_dir}/.venv/bin/python -c \"{remote_py_snippet}\"",
    ]
    exec_res = subprocess.run(remote_cmd, capture_output=True, text=True, timeout=30)
    if exec_res.returncode != 0:
        return {
            "status": "REMOTE_EXECUTION_ERROR",
            "parity_verified": False,
            "reason": exec_res.stderr.strip() or exec_res.stdout.strip(),
        }

    remote_payload: Optional[Dict[str, Any]] = None
    for line in exec_res.stdout.splitlines():
        if line.startswith("REMOTE_PARITY_JSON:"):
            remote_payload = json.loads(line[len("REMOTE_PARITY_JSON:"):])
            break

    if remote_payload is None:
        return {
            "status": "REMOTE_PARSE_ERROR",
            "parity_verified": False,
            "reason": "Did not find REMOTE_PARITY_JSON marker in remote output",
        }

    code_hash_match = local_code_sha == remote_payload["code_sha256"]
    semantic_hash_match = local_campaign["campaign_semantic_sha256"] == remote_payload["campaign_semantic_sha256"]
    survey_hash_match = (
        local_campaign["semantic_summary"]["scenario_B_survey_primary_sha256"]
        == remote_payload["semantic_summary"]["scenario_B_survey_primary_sha256"]
    )
    rescue_hash_match = (
        local_campaign["semantic_summary"]["scenario_B_rescue_primary_sha256"]
        == remote_payload["semantic_summary"]["scenario_B_rescue_primary_sha256"]
    )
    cooling_hash_match = (
        local_campaign["semantic_summary"]["scenario_B_cooling_limited_sha256"]
        == remote_payload["semantic_summary"]["scenario_B_cooling_limited_sha256"]
    )

    parity_verified = all([
        code_hash_match,
        semantic_hash_match,
        survey_hash_match,
        rescue_hash_match,
        cooling_hash_match,
    ])

    return {
        "status": "PASS" if parity_verified else "PARITY_MISMATCH",
        "parity_verified": parity_verified,
        "code_sha256_match": code_hash_match,
        "campaign_semantic_sha256_match": semantic_hash_match,
        "scenario_B_survey_primary_match": survey_hash_match,
        "scenario_B_rescue_primary_match": rescue_hash_match,
        "scenario_B_cooling_limited_match": cooling_hash_match,
        "local_execution": {
            "hostname": local_campaign["runtime_telemetry"]["hostname"],
            "system": local_campaign["runtime_telemetry"]["platform_system"],
            "machine": local_campaign["runtime_telemetry"]["platform_machine"],
            "python_version": local_campaign["runtime_telemetry"]["python_version"],
            "pid": local_campaign["runtime_telemetry"]["pid"],
            "wall_elapsed_ms": local_campaign["runtime_telemetry"]["wall_elapsed_ms"],
            "rss_kib": local_rss_kib,
            "code_sha256": local_code_sha,
            "campaign_semantic_sha256": local_campaign["campaign_semantic_sha256"],
        },
        "remote_execution": {
            "ssh_target": ssh_target,
            "hostname": remote_payload["runtime_telemetry"]["hostname"],
            "system": remote_payload["runtime_telemetry"]["platform_system"],
            "machine": remote_payload["runtime_telemetry"]["platform_machine"],
            "python_version": remote_payload["runtime_telemetry"]["python_version"],
            "pid": remote_payload["runtime_telemetry"]["pid"],
            "wall_elapsed_ms": remote_payload["runtime_telemetry"]["wall_elapsed_ms"],
            "rss_kib": remote_payload["rss_kib"],
            "code_sha256": remote_payload["code_sha256"],
            "campaign_semantic_sha256": remote_payload["campaign_semantic_sha256"],
        },
    }

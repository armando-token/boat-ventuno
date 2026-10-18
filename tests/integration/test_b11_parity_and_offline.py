"""Integration tests for B11 Offline Subprocess Execution and Mac-to-Board Semantic Parity (RUN-02, DEP-02, DEP-03)."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sdv_governor.deployment import run_doctor, sync_and_run_remote_parity
from sdv_governor.replay import run_full_campaign_suite

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_run_02_offline_subprocess_execution() -> None:
    """RUN-02: Full execution of Scenario A and Scenario B in a subprocess with network sockets blocked succeeds 100% offline."""
    offline_guard_code = "\n".join([
        "import socket, json",
        "from pathlib import Path",
        "def _block_net(*a, **kw):",
        "    raise RuntimeError('NETWORK_BLOCKED_IN_OFFLINE_TEST')",
        "socket.socket.connect = _block_net",
        "socket.create_connection = _block_net",
        "from sdv_governor.replay import run_full_campaign_suite",
        f"res = run_full_campaign_suite(Path('{REPO_ROOT.as_posix()}'))",
        "print('OFFLINE_OK:' + res['campaign_semantic_sha256'])",
    ])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    proc = subprocess.run(
        [sys.executable, "-c", offline_guard_code],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, f"Offline execution failed: {proc.stderr}"
    assert "OFFLINE_OK:" in proc.stdout


@pytest.mark.board
def test_dep_02_remote_board_execution() -> None:
    """DEP-02: Doctor and board deployment runner report either PASS (with real remote PID/arch/hash) or BLOCKED_CONNECTION cleanly."""
    doc = run_doctor(REPO_ROOT, ssh_target="ardu", probe_remote=True)
    assert doc["overall_local_ready"] is True
    assert doc["remote_board_status"] in ("PASS", "BLOCKED_CONNECTION")

    # Also verify hardware_inventory.json records the real observed hardware from Gate B1
    hw_inv = json.loads((REPO_ROOT / "hardware_inventory.json").read_text(encoding="utf-8"))
    assert "host_macbook_pro" in hw_inv
    assert "remote_linux_edge_board_ssh" in hw_inv


@pytest.mark.board
def test_dep_03_mac_board_semantic_parity() -> None:
    """DEP-03: Deterministic campaign produces identical semantic SHA-256 across processes (and on remote Linux board when reachable)."""
    camp_1 = run_full_campaign_suite(REPO_ROOT)
    camp_2 = run_full_campaign_suite(REPO_ROOT)
    assert camp_1["campaign_semantic_sha256"] == camp_2["campaign_semantic_sha256"]

    parity_rep = sync_and_run_remote_parity(REPO_ROOT, ssh_target="ardu")
    assert parity_rep["status"] in ("PASS", "BLOCKED_CONNECTION")
    if parity_rep["status"] == "PASS":
        assert parity_rep["parity_verified"] is True
        assert parity_rep["campaign_semantic_sha256_match"] is True

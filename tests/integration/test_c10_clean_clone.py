"""C10: clean-clone reproducibility and board parity (CH-C10-01..03)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from sdv_governor.deployment import sync_and_run_remote_parity
from sdv_governor.replay import run_full_campaign_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_CAMPAIGN_SHA = "ebff0e22a74e20fc781a3d9af31e8f591e725f993194c2a0ba31d17fdf715c3c"
SCRIPT = REPO_ROOT / "scripts" / "verify_clean_clone.sh"


def test_ch_c10_01_verify_clean_clone_script_is_runnable() -> None:
    """CH-C10-01: the script exists, is syntactically valid, and the test fixture is versioned.

    The full run (git archive + pytest) is opt-in to avoid recursion: SDV_RUN_CLEAN_CLONE=1.
    """
    assert SCRIPT.is_file() and os.access(SCRIPT, os.X_OK)
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    assert (REPO_ROOT / "tests" / "fixtures" / "handoff_repro" / "resource_engine_repro.json").is_file()
    if os.environ.get("SDV_RUN_CLEAN_CLONE") != "1":
        return
    env = dict(os.environ, SDV_VENV_PYTHON=sys.executable)
    assert subprocess.run([str(SCRIPT)], env=env).returncode == 0


@pytest.mark.board
def test_ch_c10_02_board_parity_passes() -> None:
    """CH-C10-02: with the board connected, Mac hash == board hash (BLOCKED_CONNECTION tolerated)."""
    rep = sync_and_run_remote_parity(REPO_ROOT, ssh_target="ardu")
    assert rep["status"] in ("PASS", "BLOCKED_CONNECTION"), rep
    if rep["status"] == "PASS":
        assert rep["parity_verified"] is True
        assert rep["campaign_semantic_sha256_match"] is True


def test_ch_c10_03_campaign_hash_equal_in_versioned_only_copy(tmp_path: Path) -> None:
    """CH-C10-03: the campaign hash depends only on versioned files (same as working tree and official)."""
    if shutil.which("git") is None or not (REPO_ROOT / ".git").exists():
        pytest.skip("needs a git checkout to enumerate versioned files")
    files = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "-z"], capture_output=True, check=True
    ).stdout.decode().split("\0")
    for rel in filter(None, files):
        src = REPO_ROOT / rel
        if src.is_file():
            dst = tmp_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    copy_hash = run_full_campaign_suite(tmp_path)["campaign_semantic_sha256"]
    tree_hash = run_full_campaign_suite(REPO_ROOT)["campaign_semantic_sha256"]
    assert copy_hash == tree_hash == OFFICIAL_CAMPAIGN_SHA

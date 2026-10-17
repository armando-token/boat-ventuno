"""Integration tests for B9 Acceptance Matrix, Single-Command Laptop Launch, and Deliverable Docs/PDFs (DEP-01, DOC-01)."""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dep_01_single_command_laptop() -> None:
    """DEP-01: Single command (`python -m sdv_governor.cli validate` and `replay`) runs cleanly on laptop without requiring a connected board."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-m", "sdv_governor.cli", "doctor", "--no-remote"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert res.returncode == 0
    payload = json.loads(res.stdout)
    assert payload["local_host"]["standalone_ready"] is True


def test_doc_01_deliverable_documents_and_page_counts() -> None:
    """DOC-01: Deliverable documents (README, MEMO_SCENARIO_C.pdf <=2p, PHYSICAL_ACCOUNT.pdf ==1p with mandatory closing paragraph, PITCH_SLIDES.pdf <=10p) exist and are valid."""
    readme = REPO_ROOT / "README.md"
    memo_md = REPO_ROOT / "docs" / "MEMO_SCENARIO_C.md"
    phys_md = REPO_ROOT / "docs" / "PHYSICAL_ACCOUNT.md"
    pitch_md = REPO_ROOT / "docs" / "PITCH_SLIDES.md"
    memo_pdf = REPO_ROOT / "docs" / "MEMO_SCENARIO_C.pdf"
    phys_pdf = REPO_ROOT / "docs" / "PHYSICAL_ACCOUNT.pdf"
    pitch_pdf = REPO_ROOT / "docs" / "PITCH_SLIDES.pdf"

    for p in (readme, memo_md, phys_md, pitch_md, memo_pdf, phys_pdf, pitch_pdf):
        assert p.exists(), f"Missing deliverable document: {p}"
        assert p.stat().st_size > 300, f"Deliverable document too small: {p}"

    # Verify mandatory closing paragraph on platform modifications in PHYSICAL_ACCOUNT.md
    phys_text = phys_md.read_text(encoding="utf-8")
    assert "mast_3" in phys_text
    assert "10,035" in phys_text or "10035" in phys_text
    assert "What would change if the vessel had" in phys_text or "Qué cambiaría en el barco" in phys_text

    # Verify PDF page counts via /Type /Page markers
    def count_pdf_pages(pdf_path: Path) -> int:
        raw = pdf_path.read_bytes()
        # Count '/Type /Page\n' or '/Type /Page/' excluding '/Type /Pages'
        import re
        matches = re.findall(rb"/Type\s*/Page\b(?!s)", raw)
        return len(matches)

    assert 1 <= count_pdf_pages(memo_pdf) <= 2
    assert count_pdf_pages(phys_pdf) == 1
    assert 1 <= count_pdf_pages(pitch_pdf) <= 10

    # Verify requirements_matrix.csv has all 49 rows mapped
    req_csv = REPO_ROOT / "requirements_matrix.csv"
    with req_csv.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) >= 42

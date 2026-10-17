"""Unit tests for B8 Evidence Generator & Verifier (EVD-04)."""

from __future__ import annotations

from pathlib import Path

from sdv_governor.evidence import generate_evidence_bundle, verify_evidence_bundle
from sdv_governor.replay import run_full_campaign_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
JUNIT_SOURCE = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"


def test_evd_04_certificate_and_report_traceability(tmp_path: Path) -> None:
    """EVD-04: Generated evidence bundle maps to real JUnit XML testcases and recomputes all metrics from raw files."""
    camp = run_full_campaign_suite(REPO_ROOT)
    bundle_dir = generate_evidence_bundle(
        tmp_path,
        "pytest_unit_evd04",
        camp,
        junit_xml_source=JUNIT_SOURCE,
    )
    ok, errors, summary = verify_evidence_bundle(bundle_dir)
    assert ok is True, f"Expected valid bundle, got errors: {errors}"
    assert summary["files_verified"] >= 4
    assert summary["junit_summary"]["tests"] >= 49
    assert summary["junit_summary"]["failures"] == 0
    assert summary["junit_summary"]["errors"] == 0

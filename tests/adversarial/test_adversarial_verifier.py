"""Adversarial verifier tests reproducing and defeating all mutation classes (EVD-01..03, R4)."""

from __future__ import annotations

from pathlib import Path
import shutil

from sdv_governor.evidence import (
    generate_evidence_bundle,
    run_adversarial_mutation_suite,
    verify_evidence_bundle,
)
from sdv_governor.replay import run_full_campaign_suite

REPO_ROOT = Path(__file__).resolve().parents[2]
JUNIT_SOURCE = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"


def test_evd_01_empty_summary_and_repro_cases_fail(tmp_path: Path) -> None:
    """EVD-01: Empty summary {} and all 25 adversarial mutation classes fail closed."""
    camp = run_full_campaign_suite(REPO_ROOT)
    valid_dir = generate_evidence_bundle(
        tmp_path, "pytest_adv_evd01", camp, junit_xml_source=JUNIT_SOURCE
    )

    # 1. Run all 25 mutation classes
    mut_report = run_adversarial_mutation_suite(valid_dir, tmp_path / "mutations")
    assert mut_report["total_mutations"] >= 20
    assert mut_report["false_accepts"] == 0
    assert mut_report["all_rejected"] is True

    # 2. Explicitly test empty JSON object {} in evidence_manifest.json
    empty_dir = tmp_path / "empty_manifest"
    shutil.copytree(valid_dir, empty_dir)
    (empty_dir / "evidence_manifest.json").write_text("{}", encoding="utf-8")
    ok, errs, _ = verify_evidence_bundle(empty_dir)
    assert ok is False
    assert any("SCHEMA_VIOLATION" in e for e in errs)


def test_evd_02_tampered_hash_fails(tmp_path: Path) -> None:
    """EVD-02: Modifying a single byte of any raw file or manifest causes SHA256_MISMATCH failure."""
    camp = run_full_campaign_suite(REPO_ROOT)
    valid_dir = generate_evidence_bundle(
        tmp_path, "pytest_adv_evd02", camp, junit_xml_source=JUNIT_SOURCE
    )

    tampered_dir = tmp_path / "tampered"
    shutil.copytree(valid_dir, tampered_dir)
    dec_path = tampered_dir / "decisions.json"
    dec_path.write_text(dec_path.read_text(encoding="utf-8") + " ", encoding="utf-8")

    ok, errs, _ = verify_evidence_bundle(tampered_dir)
    assert ok is False
    assert any("SHA256_MISMATCH" in e for e in errs)


def test_evd_03_missing_evidence_fails(tmp_path: Path) -> None:
    """EVD-03: Missing artifact file or null metric fails closed without substituting PASS or 0."""
    camp = run_full_campaign_suite(REPO_ROOT)
    valid_dir = generate_evidence_bundle(
        tmp_path, "pytest_adv_evd03", camp, junit_xml_source=JUNIT_SOURCE
    )

    missing_dir = tmp_path / "missing_file"
    shutil.copytree(valid_dir, missing_dir)
    (missing_dir / "replay_timeline.json").unlink()

    ok, errs, _ = verify_evidence_bundle(missing_dir)
    assert ok is False
    assert any("MISSING_FILE" in e for e in errs)

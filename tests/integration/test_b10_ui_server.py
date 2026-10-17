"""Integration tests for B10 Single-Screen UI Server & Live Jury Demo Endpoints (DEM-01, DEM-02)."""

from __future__ import annotations

from pathlib import Path

from sdv_governor.server import dispatch_api_request

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_dem_01_scenario_b_90s_compressed_demo() -> None:
    """DEM-01: Scenario B 90 s compressed timeline and swappable priority profiles work via the UI backend API."""
    code_s, payload_s = dispatch_api_request(REPO_ROOT, "GET", "/api/state?priority_profile=survey_primary")
    code_r, payload_r = dispatch_api_request(REPO_ROOT, "GET", "/api/state?priority_profile=rescue_primary")

    assert code_s == 200
    assert code_r == 200
    assert len(payload_s["scenario_B_replay"]["timeline_ticks"]) == 91  # ticks 0..90 (90s compressed demo)
    assert payload_s["scenario_B_replay"]["semantic_sha256"] != payload_r["scenario_B_replay"]["semantic_sha256"]

    # Verify UI static assets exist and are non-empty
    for asset in ("index.html", "app.js", "styles.css"):
        p = REPO_ROOT / "ui" / asset
        assert p.exists() and p.stat().st_size > 200


def test_dem_02_live_card_mutation_admission() -> None:
    """DEM-02: Live card mutation and Scenario A admission evaluation via /api/admit_live completes in <=30 s."""
    # 1. Admit unmodified G6 onto G1+G2+G4 -> YES
    code_ok, res_ok = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "priority_profile_id": "survey_primary",
            "at_port": True,
            "overrides": {},
        },
    )
    assert code_ok == 200
    assert res_ok["decision"]["verdict"] == "YES"
    assert res_ok["decision"]["calculation_elapsed_ms"] <= 30000.0

    # 2. Mutate G6 mass to 6500 kg (exceeds 10,000 kg with G1+G2+G4 = 4535 + 6500 = 11,035 kg) -> YES_IF
    code_mut, res_mut = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "priority_profile_id": "survey_primary",
            "at_port": True,
            "overrides": {"mass_kg": 6500},
        },
    )
    assert code_mut == 200
    assert res_mut["decision"]["verdict"] == "YES_IF"
    assert any(w["resource_class"] == "mass_stability" for w in res_mut["decision"]["binding_constraints"])

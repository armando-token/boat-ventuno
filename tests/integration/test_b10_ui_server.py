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


def test_dem_03_golden_gate_environment_and_forecast_endpoints() -> None:
    """DEM-03: Verify /api/environment, /api/environment/windows, and /api/forecast adhere to Megaplan §7.1."""
    import json

    # 1. GET /api/environment
    code_env, payload_env = dispatch_api_request(REPO_ROOT, "GET", "/api/environment")
    assert code_env == 200
    env_bytes = json.dumps(payload_env).encode("utf-8")
    assert len(env_bytes) <= 40960, f"/api/environment payload size {len(env_bytes)} exceeds 40 KB limit"

    # 2. GET /api/environment/windows with valid params
    code_win, payload_win = dispatch_api_request(
        REPO_ROOT, "GET", "/api/environment/windows?guest=G4&op=VEHICLE_REPLACEMENT_AT_SEA"
    )
    assert code_win == 200
    win_bytes = json.dumps(payload_win).encode("utf-8")
    assert len(win_bytes) <= 30720, f"/api/environment/windows payload size {len(win_bytes)} exceeds 30 KB limit"

    # 3. GET /api/environment/windows with invalid guest -> 400
    code_inv_g, payload_inv_g = dispatch_api_request(
        REPO_ROOT, "GET", "/api/environment/windows?guest=NONEXISTENT&op=VEHICLE_REPLACEMENT_AT_SEA"
    )
    assert code_inv_g == 400
    assert payload_inv_g.get("error") == "INVALID_PARAM"
    assert payload_inv_g.get("field") == "guest"

    # 4. GET /api/environment/windows with invalid op -> 400
    code_inv_op, payload_inv_op = dispatch_api_request(
        REPO_ROOT, "GET", "/api/environment/windows?guest=G4&op=INVALID_OPERATION"
    )
    assert code_inv_op == 400
    assert payload_inv_op.get("error") == "INVALID_PARAM"
    assert payload_inv_op.get("field") == "op"

    # 5. GET /api/forecast
    code_fc, payload_fc = dispatch_api_request(REPO_ROOT, "GET", "/api/forecast")
    assert code_fc == 200
    fc_bytes = json.dumps(payload_fc).encode("utf-8")
    assert len(fc_bytes) <= 51200, f"/api/forecast payload size {len(fc_bytes)} exceeds 50 KB limit"


def test_dem_04_ui_bundled_parity() -> None:
    """DEM-04: Verify byte-for-byte parity between ui/ and _bundled/ui/ assets."""
    for asset in ("index.html", "app.js", "styles.css"):
        src_path = REPO_ROOT / "ui" / asset
        bundled_path = REPO_ROOT / "src" / "sdv_governor" / "_bundled" / "ui" / asset
        assert src_path.exists(), f"Source asset {src_path} missing"
        assert bundled_path.exists(), f"Bundled asset {bundled_path} missing"
        assert src_path.read_bytes() == bundled_path.read_bytes(), f"Asset {asset} differs between ui/ and _bundled/ui/"


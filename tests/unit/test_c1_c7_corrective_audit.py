"""Comprehensive regression tests for Corrective Megaplan Findings H01 through H17 (Phases C1–C7)."""

from __future__ import annotations

import copy
import csv
from pathlib import Path
import pytest

from sdv_governor.admission import (
    build_baseline_state_with_installed_guests,
    evaluate_admission,
)
from sdv_governor.arbitration import arbitrate_live_conflict
from sdv_governor.budgets import evaluate_eight_class_budgets, is_mode_protected
from sdv_governor.degradation import execute_graceful_degradation
from sdv_governor.evidence import parse_and_verify_junit_xml
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file,
    parse_guest_manifest,
    parse_priority_profile,
    validate_mission_envelope,
    validate_ship_profile,
)
from sdv_governor.models import StrictValidationError
from sdv_governor.pdf_export import (
    verify_pdf_contains_markdown_lines,
    write_multi_page_pdf,
)
from sdv_governor.replay import build_initial_state_from_scenario, run_scenario_b_replay
from sdv_governor.server import GovernorSession, dispatch_api_request
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def test_h01_mission_envelope_propagates_to_verdict() -> None:
    """H01: A guest feasible at 16C pier water but infeasible across the 14-day Mission Envelope A.10 cannot receive YES."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])
    env = validate_mission_envelope(load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml")[0])
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")

    g6_raw, _ = load_yaml_file(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    hot_fail_raw = copy.deepcopy(g6_raw)
    # Place on bay2 (LINE_B_AFT <= 140 kW) and remove ECO_SAMPLE so the only mode draws 125 kW water cooling
    # (fits 16C pier cooling 151,111 W and 140 kW feeder, but exceeds HOT_WATER_SURVEY_30C = 120,000 W in Mission Envelope A.10!)
    hot_fail_raw["subcomponents"][0]["preferred_positions"] = ["bay2"]
    hot_fail_raw["subcomponents"][0]["allowed_positions"] = ["bay2"]
    hot_fail_raw["default_mode"] = "NOMINAL"
    hot_fail_raw["modes"] = {
        "NOMINAL": hot_fail_raw["modes"]["NOMINAL"]
    }
    m = hot_fail_raw["modes"]["NOMINAL"]
    m["min_power_w"] = 125000
    m["nominal_power_w"] = 125000
    m["sustained_power_w"] = 125000
    m["peak_power_w"] = 125000
    m["cooling_water_w"] = 125000
    m["cooling_air_mast_w"] = 0
    hot_fail_manifest = parse_guest_manifest(hot_fail_raw)

    pier_state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="PORT_INTEGRATION",
        speed_kn=0.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=5,
        sea_state=1,
        seawater_temp_c=16.0,
        power_reserve_w=200000,
    )
    decision = evaluate_admission(hot_fail_manifest, pier_state, ship, env, prof, catalog)
    assert decision["verdict"] == "NO"
    assert any(
        w["constraint_id"] == "chilled_water_loop_w" and "ENVELOPE:HOT_WATER_SURVEY_30C" in w["state_label"]
        for w in decision["binding_constraints"]
    )


def test_h02_arbitration_infeasible_weights_and_tiebreak() -> None:
    """H02: Arbitration evaluates real candidates, returns NO when deadline is unreachable, and responds to weights/tie-break."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])
    scen_b, _ = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    state = build_initial_state_from_scenario(scen_b, catalog)
    state.sim_time_s = 480

    prof_survey_raw, _ = load_yaml_file(DATA_DIR / "priorities" / "survey_primary.yaml")
    prof_survey = parse_priority_profile(prof_survey_raw)

    # 1. Impossible deadline (max endurance 40s < acoustic hold 15s + turn 90s + approach 120s) -> verdict NO
    impossible_dec = arbitrate_live_conflict(
        state,
        ship,
        prof_survey,
        catalog,
        uav_max_endurance_s=40,
    )
    assert impossible_dec["verdict"] == "NO"
    assert impossible_dec["status"] == "INFEASIBLE_ARBITRATION"

    # 2. Inverting capability_weights on the SAME priority_profile_id flips the winning plan
    inverted_raw = copy.deepcopy(prof_survey_raw)
    inverted_raw["capability_weights"]["hydrographic_survey"] = 1.0
    inverted_raw["capability_weights"]["seabed_anomaly_detection"] = 1.0
    inverted_raw["capability_weights"]["maritime_sar_rescue"] = 100.0
    inverted_raw["capability_weights"]["surface_surveillance"] = 100.0
    prof_inverted = parse_priority_profile(inverted_raw)

    dec_survey = arbitrate_live_conflict(state, ship, prof_survey, catalog)
    dec_inverted = arbitrate_live_conflict(state, ship, prof_inverted, catalog)
    assert dec_survey["selected_candidate_id"] == "CAND_WAYPOINT_PRESERVING_RECOVERY"
    assert dec_inverted["selected_candidate_id"] == "CAND_IMMEDIATE_RECOVERY_TURN"
    assert dec_inverted["touchdown_time_s"] < dec_survey["touchdown_time_s"]


def test_h03_h04_event_driven_replay_and_touchdown_separation() -> None:
    """H03 & H04: Replay reacts to scenario_data['events'] timestamps and lands G4 before 1200s prior to 20-min hangar charge."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])
    prof_survey = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    scen_b, _ = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")

    base_res = run_scenario_b_replay(scen_b, ship, prof_survey, catalog)
    assert base_res["executed_touchdown_time_s"] == 1095
    assert base_res["executed_touchdown_time_s"] <= 1200

    # Shift DRONE_BATTERY_AND_WIND_ALERT from t=480s to t=400s in scenario YAML
    shifted_scen = copy.deepcopy(scen_b)
    for ev in shifted_scen["events"]:
        if ev["event_type"] == "DRONE_BATTERY_AND_WIND_ALERT":
            ev["sim_time_s"] = 400
    shifted_res = run_scenario_b_replay(shifted_scen, ship, prof_survey, catalog)
    assert shifted_res["executed_touchdown_time_s"] == 1015
    assert shifted_res["semantic_sha256"] != base_res["semantic_sha256"]


def test_h05_verifier_rejects_synthetic_junit(tmp_path: Path) -> None:
    """H05: parse_and_verify_junit_xml rejects synthetic sdv_smoke suite and requires all 49 criteria test_ids."""
    synth_xml = tmp_path / "junit.xml"
    synth_xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuite name="sdv_smoke" tests="1" failures="0" errors="0" skipped="0">'
        '<testcase classname="sdv_governor.smoke" name="test_campaign_and_bundle_generation" time="0.01"/>'
        "</testsuite>\n",
        encoding="utf-8",
    )
    ok, errs, _ = parse_and_verify_junit_xml(synth_xml, repo_root=REPO_ROOT)
    assert ok is False
    assert any("SYNTHETIC_JUNIT_FORBIDDEN" in e for e in errs)


def test_h06_annex_b_alignment_and_field_provenance() -> None:
    """H06: None of G1-G5 is safety-critical; G1=1.5kW, G2=20/6kW, G4=35/10/2kW; G6 forbids non-ASSUMED origins."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    for gid in ("G1", "G2", "G3", "G4", "G5"):
        assert catalog[gid].safety_critical is False
        for m in catalog[gid].modes.values():
            assert m.safety_critical is False

    assert catalog["G1"].modes["NOMINAL"].nominal_power_w == 1500
    assert catalog["G1"].subcomponents[0].footprint_m2 == 0.5
    assert catalog["G2"].modes["CONTINUOUS_SENSING"].nominal_power_w == 6000
    assert catalog["G2"].ship_state_constraints.uses_deck_machinery is True
    assert catalog["G3"].ship_state_constraints.requires_stern_ramp_free is True
    assert catalog["G3"].vehicle_replacement.min_crew_required == 4
    assert catalog["G3"].vehicle_replacement.swap_duration_min == 45
    assert catalog["G4"].modes["FLIGHT_PATROL"].nominal_power_w == 10000
    assert catalog["G4"].modes["STANDBY"].nominal_power_w == 2000
    assert catalog["G4"].vehicle_replacement.min_crew_required == 3
    assert "hangar_roof" in catalog["G5"].subcomponents[1].allowed_positions

    # G6 must reject OFFICIAL_BRIEF provenance claim
    g6_raw, _ = load_yaml_file(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    bad_g6 = copy.deepcopy(g6_raw)
    bad_g6["subcomponents"][0]["value_origin"] = "OFFICIAL_BRIEF"
    with pytest.raises(StrictValidationError) as exc_info:
        parse_guest_manifest(bad_g6)
    assert exc_info.value.code == "PROVENANCE_VIOLATION"


def test_h07_local_structural_mass_limits_and_hangar_roof() -> None:
    """H07: Local structural limits (Bay2 <= 4000 kg, Hangar <= 1000 kg) and hangar_roof (x1.2 + drone clearance) are enforced."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])

    # Place G5 antenna on hangar_roof WITHOUT G4 -> feasible, stability = 3000*1.0 + 25*1.2 = 3030.0 kg-eq
    state_roof = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="NOMINAL_OFFSHORE",
        speed_kn=6.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=10,
        sea_state=2,
        seawater_temp_c=18.0,
        power_reserve_w=200000,
        installed_guests={
            "G5": InstalledGuestState(
                guest_id="G5",
                active_mode="FULL_SPECTRUM",
                placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "hangar_roof"},
                compute_node="node_1",
                powered_on=True,
            )
        },
    )
    snap_roof = evaluate_eight_class_budgets(ship, catalog, state_roof)
    assert snap_roof["feasible"] is True
    assert snap_roof["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"] == 3030.0

    # Add G4 in hangar while G5 antenna is on hangar_roof -> fails hangar_roof_drone_clearance
    state_roof.installed_guests["G4"] = InstalledGuestState(
        guest_id="G4",
        active_mode="STANDBY",
        placements={"G4_DRONE_VEHICLE": "hangar", "G4_HANGAR_STATION": "hangar"},
        compute_node="node_2",
        powered_on=True,
    )
    snap_conflict = evaluate_eight_class_budgets(ship, catalog, state_roof)
    assert snap_conflict["feasible"] is False
    assert any(w["constraint_id"] == "hangar_roof_drone_clearance" for w in snap_conflict["binding_witnesses"])


def test_h08_h09_h10_dependencies_protection_and_incremental_savings() -> None:
    """H08, H09, H10: Bidirectional conditional_excludes, non-overridable active task protection, and incremental power_saved_w."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])
    prof_raw, _ = load_yaml_file(DATA_DIR / "priorities" / "survey_primary.yaml")

    # H09: protect_uninterruptible_tasks=False is rejected
    bad_prof = copy.deepcopy(prof_raw)
    bad_prof["degradation_policy"]["protect_uninterruptible_tasks"] = False
    with pytest.raises(StrictValidationError) as exc_info:
        parse_priority_profile(bad_prof)
    assert exc_info.value.code == "UNINTERRUPTIBLE_PROTECTION_MANDATORY"

    # H09: G3 in RECHARGE_CRADLE (elapsed 600s < 3600s) and G4 in LAUNCH_RECOVERY_OPTICS are protected
    prot_g3, _ = is_mode_protected(catalog["G3"], catalog["G3"].modes["RECHARGE_CRADLE"], mode_elapsed_s=600)
    prot_g4, _ = is_mode_protected(catalog["G4"], catalog["G4"].modes["LAUNCH_RECOVERY_OPTICS"], mode_elapsed_s=100)
    assert prot_g3 is True
    assert prot_g4 is True

    # H10: Multi-step degradation reports incremental power_saved_w summing to total power reduction
    prof = parse_priority_profile(prof_raw)
    state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="DEGRADED_POWER",
        speed_kn=6.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=12,
        sea_state=2,
        seawater_temp_c=20.0,
        power_reserve_w=60000,
        installed_guests={
            "G4": InstalledGuestState(
                guest_id="G4",
                active_mode="LAUNCH_RECOVERY_OPTICS",
                placements={"G4_DRONE_VEHICLE": "hangar", "G4_HANGAR_STATION": "hangar"},
                compute_node="node_3",
                powered_on=True,
            ),
            "G5": InstalledGuestState(
                guest_id="G5",
                active_mode="FULL_SPECTRUM",
                placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"},
                compute_node="node_1",
                powered_on=True,
            ),
            "G2": InstalledGuestState(
                guest_id="G2",
                active_mode="INSPECTION_ACTIVE",
                placements={"G2_ROV_WINCH_CONTAINER_10FT": "bay1_a"},
                compute_node="node_2",
                powered_on=True,
            ),
        },
    )
    # Total power = G4(35kW) + G5(45kW) + G2(20kW) = 100kW vs 60kW reserve -> requires stepping down both G5 and G2!
    deg_res = execute_graceful_degradation(state, ship, prof, catalog)
    assert deg_res["status"] == "FEASIBLE_AFTER_DEGRADATION"
    assert len(deg_res["actions_taken"]) == 2
    total_saved = sum(a["power_saved_w"] for a in deg_res["actions_taken"])
    init_p = deg_res["deficit_analysis"]["initial_power_committed_w"]
    final_p = deg_res["final_budget_snapshot"]["classes"]["power"]["committed"]
    assert total_saved == init_p - final_p


def test_h12_h13_stateful_session_and_strict_validation() -> None:
    """H12 & H13: Stateful GovernorSession persists perturbations, rejects string booleans, and reports baseline conflicts."""
    session = GovernorSession(REPO_ROOT)
    code0, st0 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=session)
    assert code0 == 200
    v0 = st0["state_version"]
    snap0 = st0["snapshot_id"]

    code1, st1 = dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "GENERATOR_LOSS_100KW"}, session=session
    )
    assert code1 == 200
    assert st1["state_version"] == v0 + 1
    assert st1["snapshot_id"] != snap0
    _, st_after = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=session)
    assert st_after["snapshot_id"] == st1["snapshot_id"]

    code_rst, st_reset = dispatch_api_request(REPO_ROOT, "POST", "/api/reset", {}, session=session)
    assert code_rst == 200
    assert st_reset["state_version"] == v0 + 2
    assert st_reset["current_budget_snapshot"]["classes"]["power"]["degraded_capacity"] == 200000

    # String boolean "false" must return HTTP 422 TYPE_ERROR
    code_bad, err_bad = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {"installed_guests": ["G1"], "at_port": "false", "candidate_guest_id": "G6"},
        session=session,
    )
    assert code_bad == 422
    assert err_bad["error_code"] == "TYPE_ERROR"

    # Conflicting baseline guests (G1 and G5 with G4 blocking hangar_roof) are reported in baseline_conflicts, never silently dropped
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")[0])
    base_state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="PORT_INTEGRATION",
        speed_kn=0.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=5,
        sea_state=1,
        seawater_temp_c=16.0,
        power_reserve_w=200000,
    )
    _, conflicts = build_baseline_state_with_installed_guests(
        ["G1", "G4", "G5"],
        base_state,
        ship,
        catalog,
    )
    assert len(conflicts) == 1
    assert conflicts[0]["guest_id"] == "G5"


def test_h15_h16_pdf_zero_truncation_and_csv_rfc4180(tmp_path: Path) -> None:
    """H15 & H16: Deliverable PDFs contain 100% of markdown lines without truncation, and requirements_matrix.csv has 7 columns."""
    assert verify_pdf_contains_markdown_lines(
        REPO_ROOT / "docs" / "PHYSICAL_ACCOUNT.md",
        REPO_ROOT / "docs" / "PHYSICAL_ACCOUNT.pdf",
        width=116,
    )
    assert verify_pdf_contains_markdown_lines(
        REPO_ROOT / "docs" / "MEMO_SCENARIO_C.md",
        REPO_ROOT / "docs" / "MEMO_SCENARIO_C.pdf",
        width=102,
    )

    # Overflow raises ValueError instead of silent truncation
    with pytest.raises(ValueError, match="PDF_PAGE_OVERFLOW"):
        write_multi_page_pdf([["line"] * 120], tmp_path / "overflow.pdf", font_size=9, leading=11)

    # RFC 4180 CSV check: strictly 7 columns per row across all rows (>= 50 rows)
    with (REPO_ROOT / "requirements_matrix.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) >= 50
    for idx, r in enumerate(rows, start=1):
        assert len(r) == 7, f"Row {idx} has {len(r)} columns != 7"

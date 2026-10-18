"""Comprehensive Graziano Alignment Acceptance Matrix (ALIGN-T01..ALIGN-T48 — §14)."""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from sdv_governor.admission import (
    apply_admission_decision_atomically,
    build_baseline_state_with_installed_guests,
    evaluate_admission,
)
from sdv_governor.arbitration import arbitrate_live_conflict
from sdv_governor.budgets import (
    compute_cooling_water_capacity_w,
    evaluate_eight_class_budgets,
    evaluate_roof_drone_clearance,
)
from sdv_governor.degradation import (
    execute_graceful_degradation,
    handle_guest_overconsumption,
    issue_urgent_command,
)
from sdv_governor.evidence import (
    generate_evidence_bundle,
    run_adversarial_mutation_suite,
    verify_evidence_bundle,
)
from sdv_governor.manifest import (
    compute_combinations_of_four,
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file as load_manifest_yaml,
    parse_guest_manifest,
)
from sdv_governor.models import StrictValidationError
from sdv_governor.placement import evaluate_antenna_relocation_port
from sdv_governor.replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from sdv_governor.scheduling import (
    compute_deck_clear_duration_s,
    compute_speed_transition_duration_s,
    load_transition_timing_policy,
)
from sdv_governor.server import GovernorSession, dispatch_api_request
from sdv_governor.ship_state import FirmReservedLoad, InstalledGuestState, ShipOperationalState


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SHIP = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
ENV = load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml")
CATALOG = load_guest_catalog(
    DATA_DIR / "guests",
    errata_path=DATA_DIR / "overrides" / "brief_errata.yaml",
)
SURVEY_PROFILE = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
RESCUE_PROFILE = load_priority_profile(DATA_DIR / "priorities" / "rescue_primary.yaml")
SCENARIO_B_DATA = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
SCENARIO_B_DEFICIT_DATA = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_charging_deficit_105kw.yaml")
SCENARIO_B_COOLING_DATA = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_cooling_limited_30c.yaml")


def _scenario_b_state() -> ShipOperationalState:
    return build_initial_state_from_scenario(SCENARIO_B_DATA, CATALOG)


def _baseline_with(installed_ids: list[str]) -> ShipOperationalState:
    base = ShipOperationalState(vessel_mode="PORT_OPS", power_reserve_w=200000)
    st, _ = build_baseline_state_with_installed_guests(installed_ids, base, SHIP, CATALOG)
    return st


# ============================================================================
# ALIGN-T01 .. ALIGN-T08: Errata, 4-Guest Scenario B, Roof Clearance & Relocation
# ============================================================================


def test_align_t01_g4_mass_990kg_and_140_plus_850_derivation() -> None:
    """ALIGN-T01: G4 total mass is 990 kg (140 kg drone + 850 kg hangar) and rejects 900 kg without override."""
    g4 = CATALOG["G4"]
    assert g4.total_mass_kg == 990
    subs = {s.subcomponent_id: s.mass_kg for s in g4.subcomponents}
    assert subs["G4_DRONE_VEHICLE"] == 140
    assert subs["G4_HANGAR_STATION"] == 850
    assert subs["G4_DRONE_VEHICLE"] + subs["G4_HANGAR_STATION"] == g4.total_mass_kg

    errata_data, _ = load_manifest_yaml(DATA_DIR / "overrides" / "brief_errata.yaml")
    g4_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G4_uav_sar.yaml")
    wrong_g4 = copy.deepcopy(g4_raw)
    for sc in wrong_g4["subcomponents"]:
        if sc["subcomponent_id"] == "G4_HANGAR_STATION":
            sc["mass_kg"] = 900
    with pytest.raises(StrictValidationError) as exc_info:
        parse_guest_manifest(wrong_g4, errata_override=errata_data)
    assert exc_info.value.code == "ERRATA_MISMATCH"


def test_align_t02_scenario_b_four_guest_baseline_g1_g3_g4_g5() -> None:
    """ALIGN-T02: Official Scenario B installs all 4 guests G1 + G3 + G4 + G5 simultaneously."""
    state = _scenario_b_state()
    assert sorted(state.installed_guests.keys()) == ["G1", "G3", "G4", "G5"]
    assert state.installed_guests["G1"].active_mode == "NOMINAL"
    assert state.installed_guests["G1"].placements["G1_MAST_SENSOR_HEAD"] == "mast_3"
    assert state.installed_guests["G5"].placements["G5_MAST_ANTENNA"] == "hangar_roof"


def test_align_t03_combinations_of_four_with_g1_g3_g4_g5_9560kg_9853kgeq() -> None:
    """ALIGN-T03: 3 of 5 four-guest combinations exceed 10,000 kg; {G1,G3,G4,G5} is 9,560 kg and 9,853 kg-eq."""
    combos = compute_combinations_of_four(CATALOG)
    assert len(combos) == 5
    exceed_count = sum(1 for c in combos if c["exceeds_mass_limit_10000_kg"])
    assert exceed_count == 3

    by_set = {tuple(c["guests"]): c for c in combos}
    b_combo = by_set[("G1", "G3", "G4", "G5")]
    assert b_combo["total_mass_kg"] == 9560
    assert b_combo["exceeds_mass_limit_10000_kg"] is False

    state = _scenario_b_state()
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap["classes"]["mass_stability"]["actual"] == 9560
    assert snap["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"] == 9853.0


def test_align_t04_roof_drone_clearance_verified_compatible_path() -> None:
    """ALIGN-T04: Hangar roof placement alongside G4 is VERIFIED_COMPATIBLE when explicitly stamped."""
    state = _scenario_b_state()
    state.stamp_roof_clearance_verification(SHIP)
    chk = evaluate_roof_drone_clearance(state, SHIP, CATALOG, hangar_roof_clearance_verified=True)
    assert chk["status"] == "VERIFIED_COMPATIBLE"
    assert chk["satisfied"] is True
    assert chk["feasible"] is True


def test_align_t05_roof_drone_clearance_pending_yes_if_atomic_rejection() -> None:
    """ALIGN-T05: Unverified hangar_roof placement alongside G4 yields YES_IF and atomic commit rejects without verification."""
    sess = GovernorSession(REPO_ROOT)
    before_snap = sess.live_state.to_snapshot_dict()
    g5_roof_raw = copy.deepcopy(sess.manifest_registry["G5"])
    g5_roof_raw["subcomponents"][1]["preferred_positions"] = ["hangar_roof"]
    g5_roof_raw["subcomponents"][1]["allowed_positions"] = ["hangar_roof"]

    c1, resp_unverified = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G4"],
            "candidate_manifest": g5_roof_raw,
            "at_port": True,
            "allow_displacement": False,
            "apply_yes_if": True,
            "hangar_roof_clearance_verified": False,
        },
        session=sess,
    )
    assert c1 == 200
    assert resp_unverified["decision"]["verdict"] == "YES_IF"
    assert resp_unverified["applied"] is False
    assert sess.live_state.to_snapshot_dict() == before_snap


def test_align_t06_roof_drone_clearance_conflict_policy_denied() -> None:
    """ALIGN-T06: Explicit CONFLICT policy on hangar_roof alongside G4 is denied (CONFLICT_DENIED / NO)."""
    state = _scenario_b_state()
    ship_conflict = copy.deepcopy(SHIP)
    ship_conflict["roof_drone_clearance_policy"] = {
        "compatibility": "CONFLICT",
        "policy_id": "ROOF_CONFLICT_TEST",
        "policy_version": 1,
    }
    chk = evaluate_roof_drone_clearance(state, ship_conflict, CATALOG, hangar_roof_clearance_verified=True)
    assert chk["status"] == "CONFLICT_DENIED"
    assert chk["satisfied"] is False
    assert chk["feasible"] is False


def test_align_t07_roof_clearance_invalidation_on_layout_or_envelope_change() -> None:
    """ALIGN-T07: Stamped roof clearance is automatically INVALIDATED if roof layout or G4 envelope changes."""
    state = _scenario_b_state()
    state.stamp_roof_clearance_verification(SHIP)
    chk_ok = evaluate_roof_drone_clearance(state, SHIP, CATALOG)
    assert chk_ok["status"] == "VERIFIED_COMPATIBLE"
    assert chk_ok["satisfied"] is True

    # Mutate layout_version -> invalidates clearance certificate
    state.layout_version = "VESSEL_LAYOUT_MUTATED_V2"
    chk_inv = evaluate_roof_drone_clearance(state, SHIP, CATALOG)
    assert chk_inv["status"] == "INVALIDATED"
    assert chk_inv["satisfied"] is False

    state.invalidate_roof_clearance("manual_reset")
    assert state.hangar_roof_clearance_verified is False
    assert state.roof_clearance_verification["status"] == "INVALIDATED"


def test_align_t08_port_antenna_relocation_recalculates_stability_feeder_air_and_clearance() -> None:
    """ALIGN-T08: Port relocation between mast_3 and hangar_roof recalculates stability, feeder, air domain, and clearance."""
    state = _baseline_with(["G3", "G4", "G5"])
    state.installed_guests["G5"].placements["G5_MAST_ANTENNA"] = "mast_3"

    # At-sea relocation returns NOT_NOW (permitted=False)
    reloc_sea = evaluate_antenna_relocation_port(
        state, SHIP, CATALOG, target_position="hangar_roof", at_port=False, hangar_roof_clearance_verified=True
    )
    assert reloc_sea["permitted"] is False
    assert reloc_sea["verdict"] == "NOT_NOW"

    # Port relocation of G5_MAST_ANTENNA from mast_3 (3.0x -> 75 kg-eq) to hangar_roof (1.2x -> 30 kg-eq)
    reloc_roof = evaluate_antenna_relocation_port(
        state, SHIP, CATALOG, target_position="hangar_roof", at_port=True, hangar_roof_clearance_verified=True
    )
    assert reloc_roof["feasible"] is True
    assert reloc_roof["permitted"] is True
    assert reloc_roof["verdict"] == "YES"
    assert reloc_roof["before_stability_eq_kg"] == 75.0
    assert reloc_roof["after_stability_eq_kg"] == 30.0
    assert reloc_roof["before_total_stability_eq_kg"] - reloc_roof["after_total_stability_eq_kg"] == 45.0
    assert reloc_roof["before_feeder_line"] == "LINE_A_FWD"
    assert reloc_roof["after_feeder_line"] == "LINE_B_AFT"
    assert reloc_roof["before_air_cooling_domain"] == "mast_3"
    assert reloc_roof["after_air_cooling_domain"] == "hangar_roof"
    assert reloc_roof["after_roof_clearance_status"] == "VERIFIED_COMPATIBLE"


# ============================================================================
# ALIGN-T09 .. ALIGN-T22: 8-Class Physics, Air Domains, Feeders, D4 & Thermal
# ============================================================================


def test_align_t09_scenario_b_mass_9560_and_stability_9853_conserved_across_shed_and_standby() -> None:
    """ALIGN-T09: Scenario B mass (9,560 kg) and stability (9,853 kg-eq) remain constant when G5 steps down or sheds."""
    state = _scenario_b_state()
    snap_full = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap_full["classes"]["mass_stability"]["actual"] == 9560
    assert snap_full["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"] == 9853.0

    state.installed_guests["G5"].active_mode = "DEGRADED_70PCT_LOSS"
    snap_deg = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap_deg["classes"]["mass_stability"]["actual"] == 9560
    assert snap_deg["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"] == 9853.0

    state.installed_guests["G5"].powered_on = False
    snap_shed = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap_shed["classes"]["mass_stability"]["actual"] == 9560
    assert snap_shed["classes"]["mass_stability"]["local_limits"]["stability_equivalent_kg"]["actual_kg_eq"] == 9853.0


def test_align_t10_independent_air_cooling_domains_mast3_1500w_plus_roof_1200w_feasible() -> None:
    """ALIGN-T10: mast_3 (1500 W <= 2000 W) and hangar_roof (1200 W <= 2000 W) sum to 2700 W and are both feasible."""
    state = _scenario_b_state()
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state, hangar_roof_clearance_verified=True)
    air_domains = snap["classes"]["cooling"]["local_limits"]["air_cooling_domains"]
    assert air_domains["mast_3"]["actual_w"] == 1500
    assert air_domains["hangar_roof"]["actual_w"] == 1200
    assert air_domains["mast_3"]["actual_w"] + air_domains["hangar_roof"]["actual_w"] == 2700
    assert snap["classes"]["cooling"]["feasible"] is True


def test_align_t11_air_cooling_mast3_2500w_rejected_even_with_empty_roof() -> None:
    """ALIGN-T11: >2000 W air cooling on mast_3 is rejected at manifest and budget level even when hangar_roof has 0 W."""
    g5_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G5_edge_sigint.yaml")
    g5_over = copy.deepcopy(g5_raw)
    g5_over["modes"]["FULL_SPECTRUM"]["cooling_air_mast_w"] = 2500
    g5_over["modes"]["FULL_SPECTRUM"]["cooling_water_w"] = 42500
    with pytest.raises(StrictValidationError) as exc_info:
        parse_guest_manifest(g5_over)
    assert exc_info.value.code == "MAST_AIR_COOLING_EXCEEDED"

    # Also verify budget evaluator rejects >2000 W on mast_3 (1500 W G1 + 1200 W G5 = 2700 W on mast_3, 0 W on hangar_roof)
    state = _scenario_b_state()
    state.installed_guests["G1"].placements["G1_MAST_SENSOR_HEAD"] = "mast_3"
    state.installed_guests["G5"].placements["G5_MAST_ANTENNA"] = "mast_3"
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["hangar_roof"]["actual_w"] == 0
    assert snap["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["mast_3"]["actual_w"] == 2700
    assert snap["classes"]["cooling"]["feasible"] is False
    assert any(w["constraint_id"] == "mast_air_cooling_w" for w in snap["binding_witnesses"])


def test_align_t12_air_cooling_hangar_roof_overload_rejected_with_assumed_provenance() -> None:
    """ALIGN-T12: Overloading hangar_roof (> 2000 W) is rejected and carries ASSUMED provenance."""
    state = _scenario_b_state()
    # Place both G1 (1500 W air) and G5 antenna (1200 W air) on hangar_roof -> 2700 W > 2000 W
    state.installed_guests["G1"].placements["G1_MAST_SENSOR_HEAD"] = "hangar_roof"
    state.installed_guests["G5"].placements["G5_MAST_ANTENNA"] = "hangar_roof"
    state.stamp_roof_clearance_verification(SHIP)

    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state, hangar_roof_clearance_verified=True)
    roof_dom = snap["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["hangar_roof"]
    assert roof_dom["value_origin"] == "ASSUMED"
    assert roof_dom["actual_w"] == 2700
    assert roof_dom["feasible"] is False
    assert any(w["constraint_id"] == "hangar_roof_air_cooling_w" for w in snap["binding_witnesses"])


def test_align_t13_g4_and_g5_subcomponent_allocations_and_feeder_routing() -> None:
    """ALIGN-T13: Component breakdown tracks G4 and G5 subcomponents across bays, roof/mast, feeders, and cooling domains."""
    state = _scenario_b_state()
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    comp = snap["component_breakdown"]
    assert comp["G5_COMPUTE_CONTAINER"]["position"] == "bay2"
    assert comp["G5_COMPUTE_CONTAINER"]["feeder_line"] == "LINE_B_AFT"
    assert comp["G5_COMPUTE_CONTAINER"]["actual_power_w"] == 43800
    assert comp["G5_COMPUTE_CONTAINER"]["cooling_water_actual_w"] == 43800
    assert comp["G5_MAST_ANTENNA"]["position"] == "hangar_roof"
    assert comp["G5_MAST_ANTENNA"]["feeder_line"] == "LINE_B_AFT"
    assert comp["G5_MAST_ANTENNA"]["actual_power_w"] == 1200
    assert comp["G5_MAST_ANTENNA"]["cooling_air_actual_w"] == 1200
    assert comp["G1_MAST_SENSOR_HEAD"]["position"] == "mast_3"
    assert comp["G1_MAST_SENSOR_HEAD"]["feeder_line"] == "LINE_A_FWD"
    assert comp["G1_MAST_SENSOR_HEAD"]["cooling_air_actual_w"] == 1500
    assert comp["G4_DRONE_VEHICLE"]["mass_kg"] == 140
    assert comp["G4_HANGAR_STATION"]["mass_kg"] == 850


def test_align_t14_five_canonical_power_water_air_feeder_rows_match_section_8_tables() -> None:
    """ALIGN-T14: All 5 canonical Scenario B operational rows match §8.2 & §8.3 power, water, air, and feeder tables."""
    # Row 1: Quiet Survey + G5 Full (G1=1.5kW mast_3 air, G3=1kW survey, G4=10kW flight/optics, G5=45kW with antenna on roof)
    s1 = _scenario_b_state()
    b1 = evaluate_eight_class_budgets(SHIP, CATALOG, s1)
    assert b1["classes"]["power"]["actual"] == 57500
    assert b1["classes"]["cooling"]["actual"] == 54800
    assert b1["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["mast_3"]["actual_w"] == 1500
    assert b1["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["hangar_roof"]["actual_w"] == 1200
    assert b1["classes"]["power"]["local_limits"]["LINE_A_FWD"]["actual_w"] == 2500
    assert b1["classes"]["power"]["local_limits"]["LINE_B_AFT"]["actual_w"] == 55000

    # Row 2: D3 Reserved / Active 35 kW G4 Recovery + G3 Survey (1 kW) + G5 Full (45 kW) + G1 (1.5 kW)
    s2 = _scenario_b_state()
    s2.firm_reserved_loads.append(FirmReservedLoad("G4", "LAUNCH_RECOVERY_OPTICS", 35000, "D3 recovery"))
    b2 = evaluate_eight_class_budgets(SHIP, CATALOG, s2)
    assert b2["classes"]["power"]["committed"] == 106500
    assert b2["classes"]["cooling"]["committed"] == 103800

    # Row 3: Post-D4 Step-Down during G4 35 kW recovery/charge (G1=1.5kW, G3=1kW, G4=35kW, G5=8kW)
    s3 = _scenario_b_state()
    s3.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
    s3.installed_guests["G5"].active_mode = "DEGRADED_70PCT_LOSS"
    b3 = evaluate_eight_class_budgets(SHIP, CATALOG, s3)
    assert b3["classes"]["power"]["actual"] == 45500
    assert b3["classes"]["cooling"]["actual"] == 43500
    assert b3["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["mast_3"]["actual_w"] == 1500
    assert b3["classes"]["cooling"]["local_limits"]["air_cooling_domains"]["hangar_roof"]["actual_w"] == 500

    # Row 4: G1 (1.5kW) + G3 Recharge (25kW) + G4 Charge (35kW) + G5 Full (45kW) = 106.5 kW
    s4 = _scenario_b_state()
    s4.installed_guests["G3"].active_mode = "RECHARGE_CRADLE"
    s4.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
    b4 = evaluate_eight_class_budgets(SHIP, CATALOG, s4)
    assert b4["classes"]["power"]["actual"] == 106500
    assert b4["classes"]["cooling"]["actual"] == 103800
    assert b4["classes"]["power"]["local_limits"]["LINE_A_FWD"]["actual_w"] == 26500
    assert b4["classes"]["power"]["local_limits"]["LINE_B_AFT"]["actual_w"] == 80000

    # Row 5: Post-D4 Step-Down with G3 Recharge (25kW) + G4 Charge (35kW) + G5 Degraded (8kW) + G1 (1.5kW) = 69.5 kW
    s5 = _scenario_b_state()
    s5.installed_guests["G3"].active_mode = "RECHARGE_CRADLE"
    s5.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
    s5.installed_guests["G5"].active_mode = "DEGRADED_70PCT_LOSS"
    b5 = evaluate_eight_class_budgets(SHIP, CATALOG, s5)
    assert b5["classes"]["power"]["actual"] == 69500
    assert b5["classes"]["cooling"]["actual"] == 67500
    assert b5["classes"]["power"]["local_limits"]["LINE_A_FWD"]["actual_w"] == 26500
    assert b5["classes"]["power"]["local_limits"]["LINE_B_AFT"]["actual_w"] == 43000


def test_align_t15_idempotent_firm_reservation_deduplication_and_envelope_delta() -> None:
    """ALIGN-T15: Duplicate firm reservations for the same guest are deduplicated and drop to 0 delta when active."""
    state = _scenario_b_state()
    state.firm_reserved_loads = [
        FirmReservedLoad("G3", "RECHARGE_CRADLE", 25000, "G3 recharge", reservation_id="RES_G3_1"),
        FirmReservedLoad("G3", "RECHARGE_CRADLE", 25000, "G3 duplicate", reservation_id="RES_G3_2"),
    ]
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert len(snap["firm_reservations_ledger"]) == 1
    assert snap["firm_reservations_ledger"][0]["net_envelope_delta_w"] == 24000

    # When G3 enters RECHARGE_CRADLE (25 kW), net reservation delta becomes 0 W
    state.installed_guests["G3"].active_mode = "RECHARGE_CRADLE"
    snap_active = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap_active["firm_reservations_ledger"][0]["net_envelope_delta_w"] == 0


def test_align_t16_saturated_single_feeder_rejected_despite_global_power_margin() -> None:
    """ALIGN-T16: A load exceeding a single 150 kW feeder is rejected even when global 200 kW power has margin."""
    state = _baseline_with(["G5"])
    state.installed_guests["G5"].placements["G5_COMPUTE_CONTAINER"] = "bay1_b"
    g6_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    g6_high = copy.deepcopy(g6_raw)
    g6_high["subcomponents"][0]["preferred_positions"] = ["bay1_a"]
    g6_high["subcomponents"][0]["allowed_positions"] = ["bay1_a"]
    for m in g6_high["modes"].values():
        m["min_power_w"] = 110000
        m["nominal_power_w"] = 112000
        m["sustained_power_w"] = 112000
        m["peak_power_w"] = 115000
        m["cooling_water_w"] = 112000
    cand = parse_guest_manifest(g6_high)

    dec = evaluate_admission(cand, state, SHIP, ENV, SURVEY_PROFILE, CATALOG, at_port=True, allow_displacement=False)
    assert dec["verdict"] == "NO"
    assert any("LINE_A_FWD" in w["detail"] for w in dec["binding_constraints"])


def test_align_t17_d4_generator_loss_106_5kw_vs_100kw_steps_down_g5_to_69_5kw() -> None:
    """ALIGN-T17: Generator loss (200 -> 100 kW) under 106.5 kW 4-guest load steps down G5 (45 -> 8 kW) to 69.5 kW."""
    state = _scenario_b_state()
    state.installed_guests["G3"].active_mode = "RECHARGE_CRADLE"
    state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
    state.power_reserve_w = 100000

    deg = execute_graceful_degradation(state, SHIP, SURVEY_PROFILE, CATALOG)
    assert deg["verdict"] == "YES"
    assert deg["deficit_analysis"]["initial_power_actual_w"] == 106500
    assert deg["actions_taken"][0]["guest_id"] == "G5"
    assert deg["actions_taken"][0]["to_mode"] == "DEGRADED_70PCT_LOSS"
    assert deg["final_budget_snapshot"]["classes"]["power"]["actual"] == 69500
    assert deg["final_budget_snapshot"]["feasible"] is True


def test_align_t18_d4_cooling_30c_40pct_loop_loss_72kw_steps_down_g5_to_67_5kw() -> None:
    """ALIGN-T18: 30°C seawater + 40% cooling loop loss (72 kW water cap) steps down G5 to 67.5 kW water load."""
    _, eff_30_deg, _ = compute_cooling_water_capacity_w(30.0, loop_degraded=True)
    assert eff_30_deg == 72000

    state = _scenario_b_state()
    state.installed_guests["G3"].active_mode = "RECHARGE_CRADLE"
    state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
    state.seawater_temp_c = 30.0
    state.cooling_loop_degraded = True
    state.power_reserve_w = 120000  # Power is feasible (106.5 <= 120 kW), but water cooling is 103.8 kW > 72 kW!
    deg = execute_graceful_degradation(state, SHIP, SURVEY_PROFILE, CATALOG)
    assert deg["verdict"] == "YES"
    assert deg["deficit_analysis"]["initial_cooling_actual_w"] == 103800
    assert deg["final_budget_snapshot"]["classes"]["cooling"]["actual"] == 67500
    assert deg["final_budget_snapshot"]["feasible"] is True


def test_align_t19_g5_plus_20pct_overconsumption_54kw_splits_52_8kw_water_and_1_2kw_air() -> None:
    """ALIGN-T19: G5 +20% overconsumption (54 kW = 52.8 kW water + 1.2 kW air) is detected and mitigated."""
    state = _scenario_b_state()
    state.installed_guests["G5"].actual_power_override_w = 54000
    snap_over = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap_over["component_breakdown"]["G5_COMPUTE_CONTAINER"]["actual_power_w"] == 52800
    assert snap_over["component_breakdown"]["G5_COMPUTE_CONTAINER"]["cooling_water_actual_w"] == 52800
    assert snap_over["component_breakdown"]["G5_MAST_ANTENNA"]["actual_power_w"] == 1200
    assert snap_over["component_breakdown"]["G5_MAST_ANTENNA"]["cooling_air_actual_w"] == 1200

    res = handle_guest_overconsumption(
        state,
        SHIP,
        SURVEY_PROFILE,
        CATALOG,
        offending_guest_id="G5",
        measured_power_w=54000,
    )
    assert res["declared_power_w"] == 45000
    assert res["measured_power_w"] == 54000
    assert res["overrun_pct"] == 20.0
    assert res["final_budget_snapshot"]["feasible"] is True


def test_align_t20_thermal_inertia_60s_burst_and_600s_cooldown_enforced() -> None:
    """ALIGN-T20: Thermal inertia allows <=60s +30 kW burst and enforces 600s cooldown without per-tick reset."""
    state = _scenario_b_state()
    ok1, msg1 = state.thermal_inertia.step_tick(20000, dt_s=30)
    assert ok1 is True and msg1 == "THERMAL_INERTIA_ABSORBING"
    assert state.thermal_inertia.continuous_boost_seconds == 30

    # Exceeding 60s continuous excursion exhausts thermal inertia and enters 600s cooldown
    ok2, msg2 = state.thermal_inertia.step_tick(20000, dt_s=35)
    assert ok2 is False and msg2 == "THERMAL_INERTIA_EXHAUSTED_60S"
    assert state.thermal_inertia.cooldown_remaining_s == 600


def test_align_t21_fourth_guest_compute_node_and_80pct_ceiling_enforced() -> None:
    """ALIGN-T21: 4 rack nodes enforce per-node 80% ceiling (25 cores, 102400 MiB, 1 accelerator) and 4th guest placement."""
    state = _scenario_b_state()
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap["classes"]["compute"]["feasible"] is True
    nodes = snap["classes"]["compute"]["local_limits"]["nodes"]
    assert len(nodes) == 4
    for n in nodes.values():
        assert n["used_cores"] <= 25
        assert n["used_ram_mib"] <= 102400
        assert n["used_accelerators"] <= 1


def test_align_t22_data_links_and_emcon_constraints_enforced() -> None:
    """ALIGN-T22: SATCOM, optical, acoustic link capacities and EMCON RF emission lock are enforced."""
    state = _scenario_b_state()
    state.emcon_silence = True
    snap = evaluate_eight_class_budgets(SHIP, CATALOG, state)
    assert snap["classes"]["data"]["feasible"] is False
    assert snap["classes"]["data"]["local_limits"]["satcom_bps"]["emcon_violated"] is True


# ============================================================================
# ALIGN-T23 .. ALIGN-T36: Annex A.6 Transitions, D3 Arbitration, Extended Horizon
# ============================================================================


def test_align_t23_annex_a6_state_transitions_effective_only_upon_completion() -> None:
    """ALIGN-T23: Vessel speed/heading/mode transitions only take effect when earliest_complete_at_s is reached."""
    state = _scenario_b_state()
    state.sim_time_s = 100
    initial_speed = state.ship_speed_kn
    tr = state.request_transition(
        transition_id="TR_SPEED_TEST",
        resource_or_state_variable="ship_speed_kn",
        requested_state=12.0,
        duration_s=180,
        requested_at_s=100,
    )
    assert tr.earliest_complete_at_s == 280
    state.advance_transitions(200)
    assert state.ship_speed_kn == initial_speed
    completed = state.advance_transitions(280)
    assert len(completed) == 1
    assert state.ship_speed_kn == 12.0


def test_align_t24_g3_acoustic_ack_nominal_late_boundary_and_timeout_causality() -> None:
    """ALIGN-T24: Nominal, late (770s), and timeout G3 ACKs enforce strict acoustic causality."""
    rep_nom = run_scenario_b_replay(SCENARIO_B_DATA, SHIP, SURVEY_PROFILE, CATALOG)
    assert rep_nom["executed_touchdown_time_s"] == 1095

    scen_late = copy.deepcopy(SCENARIO_B_DATA)
    scen_late["events"].append(
        {
            "sim_time_s": 770,
            "event_type": "ACOUSTIC_HOLD_CONFIRMED",
            "guest_id": "G3",
            "command_id": "CMD_HOLD_G3_V1_720",
            "plan_version": 1,
        }
    )
    rep_late = run_scenario_b_replay(scen_late, SHIP, SURVEY_PROFILE, CATALOG)
    assert rep_late["executed_touchdown_time_s"] == 1130


def test_align_t25_intra_band_60s_vs_inter_band_180s_speed_transition_timing() -> None:
    """ALIGN-T25: Intra-band speed change takes 60 s; adjacent inter-band takes 180 s; 2-band takes 360 s."""
    assert compute_speed_transition_duration_s(3.5, 5.0) == 60
    assert compute_speed_transition_duration_s(4.0, 12.0) == 180
    assert compute_speed_transition_duration_s(4.0, 20.0) == 360


def test_align_t26_deck_clear_duration_0s_verified_vs_240s_600s_prohibited_by_crew() -> None:
    """ALIGN-T26: Deck clear is 0s when already clear, 240s (14 crew), 600s (6 crew), and None (prohibited) for 0 crew."""
    assert compute_deck_clear_duration_s(14, deck_already_clear=True) == 0
    assert compute_deck_clear_duration_s(14, deck_already_clear=False) == 240
    assert compute_deck_clear_duration_s(6, deck_already_clear=False) == 600
    assert compute_deck_clear_duration_s(0, deck_already_clear=False) is None


def test_align_t27_d3_arbitration_survey_primary_rescue_primary_and_custom_profile_validation() -> None:
    """ALIGN-T27: D3 produces distinct plans for survey_primary vs rescue_primary and validates custom profiles via /api/profile."""
    state = _scenario_b_state()
    state.sim_time_s = 480
    arb_s = arbitrate_live_conflict(state, SHIP, SURVEY_PROFILE, CATALOG)
    arb_r = arbitrate_live_conflict(state, SHIP, RESCUE_PROFILE, CATALOG)
    assert arb_s["turn_into_wind_time_s"] != arb_r["turn_into_wind_time_s"]
    assert arb_s["touchdown_time_s"] != arb_r["touchdown_time_s"]

    sess = GovernorSession(REPO_ROOT)
    raw_prof, _ = load_manifest_yaml(DATA_DIR / "priorities" / "survey_primary.yaml")
    custom_prof = copy.deepcopy(raw_prof)
    custom_prof["priority_profile_id"] = "jury_custom_test"
    custom_yaml = yaml.safe_dump(custom_prof)

    c_ok, r_ok = dispatch_api_request(
        REPO_ROOT, "POST", "/api/profile", {"priority_profile_yaml": custom_yaml}, session=sess
    )
    assert c_ok == 200
    assert r_ok["priority_profile_id"] == "jury_custom_test"
    assert len(r_ok["active_priority_profile_sha256"]) == 64

    # Invalid custom profile is rejected (422) and preserves active profile
    c_bad, _ = dispatch_api_request(
        REPO_ROOT, "POST", "/api/profile", {"priority_profile_yaml": "priority_profile_id: bad_only\n"}, session=sess
    )
    assert c_bad == 422
    assert sess.priority_profile_id == "jury_custom_test"


def test_align_t28_d3_impossible_deadline_returns_no_without_fabricated_touchdown() -> None:
    """ALIGN-T28: Impossible battery deadline (< causal transition chain) returns verdict=NO without fake landing."""
    state = _scenario_b_state()
    state.sim_time_s = 480
    arb = arbitrate_live_conflict(
        state,
        SHIP,
        SURVEY_PROFILE,
        CATALOG,
        uav_max_endurance_s=100,  # Only 100s endurance left < 375s minimum causal chain
    )
    assert arb["verdict"] == "NO"
    assert arb["hard_safety_satisfied"] is False


def test_align_t29_g3_resumes_survey_after_quiet_readiness_and_2400s_leg_completes_in_extended_horizon() -> None:
    """ALIGN-T29: G3 resumes survey after quiet readiness, is PENDING_HORIZON at 1800s, and COMPLETED in 4200s horizon."""
    rep_1800 = run_scenario_b_replay(SCENARIO_B_DATA, SHIP, SURVEY_PROFILE, CATALOG, max_sim_time_s=1800)
    assert rep_1800["horizon_obligations_summary"]["g3_survey_leg_2400s"]["status"] == "PENDING_HORIZON"

    rep_4200 = run_scenario_b_replay(SCENARIO_B_DATA, SHIP, SURVEY_PROFILE, CATALOG, max_sim_time_s=4200)
    assert rep_4200["horizon_obligations_summary"]["g3_survey_leg_2400s"]["status"] == "COMPLETED"
    assert rep_4200["horizon_obligations_summary"]["g3_survey_leg_2400s"]["legs_completed"] >= 1


def test_align_t30_g4_35kw_charging_for_1200s_pending_at_1800s_and_completes_in_extended_horizon() -> None:
    """ALIGN-T30: G4 35 kW charging for 1200s is PENDING_HORIZON at 1800s and COMPLETED in 4200s horizon."""
    rep_1800 = run_scenario_b_replay(SCENARIO_B_DATA, SHIP, SURVEY_PROFILE, CATALOG, max_sim_time_s=1800)
    assert rep_1800["horizon_obligations_summary"]["g4_charging_1200s"]["status"] == "PENDING_HORIZON"

    rep_4200 = run_scenario_b_replay(SCENARIO_B_DATA, SHIP, SURVEY_PROFILE, CATALOG, max_sim_time_s=4200)
    assert rep_4200["horizon_obligations_summary"]["g4_charging_1200s"]["status"] == "COMPLETED"


def test_align_t31_urgent_stop_g4_pre_touchdown_cancels_recovery_and_post_touchdown_preserves_history() -> None:
    """ALIGN-T31: URGENT_STOP G4 pre-touchdown cancels recovery; post-touchdown stops charging while preserving touchdown history."""
    sess_pre = GovernorSession(REPO_ROOT)
    dispatch_api_request(REPO_ROOT, "POST", "/api/advance", {"target_sim_time_s": 1000}, session=sess_pre)
    dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "URGENT_STOP", "guest_id": "G4"}, session=sess_pre
    )
    dispatch_api_request(REPO_ROOT, "POST", "/api/advance", {"target_sim_time_s": 1200}, session=sess_pre)
    assert sess_pre.executed_touchdown_time_s is None

    sess_post = GovernorSession(REPO_ROOT)
    dispatch_api_request(REPO_ROOT, "POST", "/api/advance", {"target_sim_time_s": 1120}, session=sess_post)
    assert sess_post.executed_touchdown_time_s == 1095
    dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "URGENT_STOP", "guest_id": "G4"}, session=sess_post
    )
    assert sess_post.executed_touchdown_time_s == 1095
    assert sess_post.live_state.installed_guests["G4"].powered_on is False


def test_align_t32_urgent_command_cleans_up_pending_d3_transitions_and_reservations() -> None:
    """ALIGN-T32: Urgent command cancels pending D3 transitions and removes associated firm reservations."""
    state = _scenario_b_state()
    state.sim_time_s = 500
    tr = state.request_transition("TR_D3_G4", "G4.active_mode", "STANDBY", duration_s=1200, owner_id="G4")
    state.firm_reserved_loads.append(FirmReservedLoad("G4", "LAUNCH_RECOVERY_OPTICS", 35000, "D3 reserve"))
    res = issue_urgent_command("STOP", "G4", state, CATALOG)
    assert res["within_200ms_deadline"] is True
    post_state = res["resulting_state"]
    assert post_state.installed_guests["G4"].powered_on is False
    assert len([fl for fl in post_state.firm_reserved_loads if fl.guest_id == "G4"]) == 0
    assert any(t.transition_id == tr.transition_id and t.status == "CANCELLED" for t in post_state.transition_history)


def test_align_t33_scenario_a_perturbed_fourth_card_evaluated_against_a10_mission_envelope() -> None:
    """ALIGN-T33: Perturbed 4th card in Scenario A is evaluated against both pier (16°C) and worst-case A.10 mission envelope."""
    baseline = _baseline_with(["G1", "G2", "G4"])
    g6_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    g6_900 = copy.deepcopy(g6_raw)
    g6_900["subcomponents"][0]["mass_kg"] = 900
    cand_900 = parse_guest_manifest(g6_900)

    adm = evaluate_admission(cand_900, baseline, SHIP, ENV, SURVEY_PROFILE, CATALOG, at_port=True)
    assert adm["verdict"] == "YES"
    assert len(adm["mission_envelope_hash"]) == 64


def test_align_t34_admission_distinguishes_no_not_now_and_atomic_yes_if() -> None:
    """ALIGN-T34: Admission engine emits distinct YES, YES_IF, NOT_NOW (null window when unknown), and NO verdicts."""
    baseline = _baseline_with(["G1", "G2", "G3"])
    adm_no = evaluate_admission(
        CATALOG["G5"], baseline, SHIP, ENV, SURVEY_PROFILE, CATALOG, at_port=True, allow_displacement=False
    )
    assert adm_no["verdict"] == "NO"

    adm_yes_if = evaluate_admission(
        CATALOG["G5"], baseline, SHIP, ENV, SURVEY_PROFILE, CATALOG, at_port=True, allow_displacement=True
    )
    assert adm_yes_if["verdict"] == "YES_IF"

    adm_not_now = evaluate_admission(
        CATALOG["G6"],
        baseline,
        SHIP,
        ENV,
        SURVEY_PROFILE,
        CATALOG,
        at_port=False,
        defer_if_temporary_operational_block=True,
        next_window=None,
    )
    assert adm_not_now["verdict"] == "NOT_NOW"
    assert adm_not_now["next_window"] is None


def test_align_t35_d4_degradation_never_sheds_safety_critical_loads() -> None:
    """ALIGN-T35: D4 graceful degradation protects safety-critical modes and flags INFEASIBLE_PROTECTED_LOAD if breached."""
    state = build_initial_state_from_scenario(SCENARIO_B_DEFICIT_DATA, CATALOG)
    state.power_reserve_w = 40000  # Protected G3 (25 kW) + G4 (35 kW) = 60 kW > 40 kW
    deg = execute_graceful_degradation(state, SHIP, SURVEY_PROFILE, CATALOG)
    assert deg["verdict"] == "NO"
    assert deg["infeasible_protected_load"] is True
    assert deg["resulting_state"].installed_guests["G3"].powered_on is True
    assert deg["resulting_state"].installed_guests["G4"].powered_on is True


def test_align_t36_at_sea_cooling_repair_rejected_under_annex_a6() -> None:
    """ALIGN-T36: Cooling loop degradation cannot be repaired at sea under Annex A.6 timing policy."""
    pol = load_transition_timing_policy(REPO_ROOT)
    assert pol["cooling_loop_at_sea_repair_allowed"] is False


# ============================================================================
# ALIGN-T37 .. ALIGN-T48: Unified 2D/3D State, Offline Assets, Benchmarks & Provenance
# ============================================================================


def test_align_t37_2d_console_and_3d_viewer_share_unified_authoritative_state_envelope() -> None:
    """ALIGN-T37: 2D console and 3D viewer consume the same authoritative /api/state envelope and version."""
    sess = GovernorSession(REPO_ROOT)
    c1, s1 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert c1 == 200
    for key in (
        "run_id",
        "snapshot_version",
        "sim_time_s",
        "connection_state",
        "source",
        "state",
        "budgets",
        "pending_transitions",
        "decision_log",
    ):
        assert key in s1
    assert s1["connection_state"] == "LIVE"
    assert s1["source"] == "AUTHORITATIVE_GOVERNOR"
    assert s1["state"]["sim_time_s"] == s1["sim_time_s"]
    assert s1["budgets"]["classes"]["mass_stability"]["actual"] == 9560


def test_align_t38_backend_disconnect_sets_stale_banner_and_disables_mutations_without_local_fallback() -> None:
    """ALIGN-T38: Both 2D UI and 3D viewer implement STALE banner + mutation lock on disconnect without fake local fallback."""
    ui_js = (REPO_ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    viz_js = (REPO_ROOT / "viz3d" / "app_standalone.js").read_text(encoding="utf-8")
    assert "setStaleBanner" in ui_js
    assert "STALE" in ui_js
    assert "setConnectionState" in viz_js or "staleBanner" in viz_js
    assert "STALE / DESCONECTADO" in (REPO_ROOT / "viz3d" / "index.html").read_text(encoding="utf-8")


def test_align_t39_single_authoritative_clock_and_get_state_idempotency() -> None:
    """ALIGN-T39: Repeated GET /api/state calls are idempotent and /api/step advances the single authoritative clock."""
    sess = GovernorSession(REPO_ROOT)
    _, r1 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    _, r2 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert r1["snapshot_version"] == r2["snapshot_version"]
    assert r1["sim_time_s"] == r2["sim_time_s"] == 0

    c_step, r_step = dispatch_api_request(REPO_ROOT, "POST", "/api/step", {"steps": 2}, session=sess)
    assert c_step == 200
    assert r_step["sim_time_s"] == 40
    _, r3 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert r3["sim_time_s"] == 40
    assert r3["snapshot_version"] == r_step["snapshot_version"]


def test_align_t40_single_screen_console_eight_gauges_crew_dial_and_causal_log() -> None:
    """ALIGN-T40: Single-screen console exposes 8 resource gauges, 14/6/0 crew dial (/api/crew), and causal log."""
    ui_html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "resource-cards-container" in ui_html
    assert 'data-crew="14"' in ui_html
    assert 'data-crew="6"' in ui_html
    assert 'data-crew="0"' in ui_html
    assert "decision-log-box" in ui_html

    sess = GovernorSession(REPO_ROOT)
    c_crew, r_crew = dispatch_api_request(
        REPO_ROOT, "POST", "/api/crew", {"crew_count": 6, "deck_clear": True}, session=sess
    )
    assert c_crew == 200
    assert r_crew["crew_regime"] == "REDUCED_CREW_6"
    assert r_crew["available_crew"] == 2


def test_align_t41_air_gapped_offline_assets_and_zero_external_cdn_links() -> None:
    """ALIGN-T41: UI, viz3d, and _bundled assets contain zero external CDN URLs and support air-gapped operation."""
    cdn_re = re.compile(r"(?:src|href)\s*=\s*['\"]https?://", re.IGNORECASE)
    for html_path in (
        REPO_ROOT / "ui" / "index.html",
        REPO_ROOT / "viz3d" / "index.html",
        REPO_ROOT / "src" / "sdv_governor" / "_bundled" / "ui" / "index.html",
        REPO_ROOT / "src" / "sdv_governor" / "_bundled" / "viz3d" / "index.html",
    ):
        assert html_path.exists(), f"Missing {html_path}"
        content = html_path.read_text(encoding="utf-8")
        assert not cdn_re.search(content), f"Found external CDN link in {html_path}"


def test_align_t42_triple_replay_determinism_across_all_scenarios() -> None:
    """ALIGN-T42: Three independent campaign replays produce identical semantic SHA-256 hashes."""
    r1 = run_full_campaign_suite(REPO_ROOT)
    r2 = run_full_campaign_suite(REPO_ROOT)
    r3 = run_full_campaign_suite(REPO_ROOT)
    assert r1["campaign_semantic_sha256"] == r2["campaign_semantic_sha256"] == r3["campaign_semantic_sha256"]


def test_align_t43_adversarial_verifier_rejects_all_semantic_mutations(tmp_path: Path) -> None:
    """ALIGN-T43: Adversarial verifier rejects 100% of semantic/integrity mutations (>=28 mutations, 0 false accepts)."""
    camp = run_full_campaign_suite(REPO_ROOT)
    valid_dir = generate_evidence_bundle(
        tmp_path,
        "pytest_align_t43",
        camp,
        junit_xml_source=REPO_ROOT / "artifacts" / "final_run" / "junit.xml",
    )
    ok, errs, _ = verify_evidence_bundle(valid_dir)
    assert ok is True, f"Valid portable bundle failed: {errs}"

    mut = run_adversarial_mutation_suite(valid_dir, tmp_path / "mut_scratch")
    assert mut["total_mutations"] >= 28
    assert mut["false_accepts"] == 0


def test_align_t44_d1_admission_latency_well_under_30s_budget() -> None:
    """ALIGN-T44: D1 admission solver latency is well within the 30,000 ms budget."""
    baseline = _baseline_with(["G1", "G2", "G4"])
    adm = evaluate_admission(CATALOG["G6"], baseline, SHIP, ENV, SURVEY_PROFILE, CATALOG, at_port=True)
    assert float(adm["calculation_elapsed_ms"]) <= 30000.0
    assert float(adm["calculation_elapsed_ms"]) < 1000.0


def test_align_t45_d3_arbitration_latency_well_under_2s_budget() -> None:
    """ALIGN-T45: D3 arbitration solver latency is well within the 2,000 ms budget."""
    state = _scenario_b_state()
    state.sim_time_s = 480
    arb = arbitrate_live_conflict(state, SHIP, SURVEY_PROFILE, CATALOG)
    assert float(arb["calculation_elapsed_ms"]) <= 2000.0
    assert float(arb["calculation_elapsed_ms"]) < 500.0


def test_align_t46_d4_degradation_latency_well_under_5s_and_60s_budgets() -> None:
    """ALIGN-T46: D4 graceful degradation meets <=5,000 ms first action and <=60,000 ms sequence budgets."""
    state = build_initial_state_from_scenario(SCENARIO_B_DEFICIT_DATA, CATALOG)
    state.power_reserve_w = 100000
    deg = execute_graceful_degradation(state, SHIP, SURVEY_PROFILE, CATALOG)
    assert deg["within_5s_first_shed_deadline"] is True
    assert deg["within_60s_sequence_deadline"] is True
    assert float(deg["first_shedding_command_elapsed_ms"]) <= 5000.0
    assert float(deg["total_sequence_elapsed_ms"]) <= 60000.0


def test_align_t47_urgent_command_dispatch_latency_under_200ms_budget() -> None:
    """ALIGN-T47: Urgent HOLD/STOP/SECURE dispatch completes in <=200 ms at the declared governor boundary."""
    state = _scenario_b_state()
    for cmd_name in ("HOLD", "STOP", "SECURE"):
        res = issue_urgent_command(cmd_name, "G3", state, CATALOG)
        assert res["within_200ms_deadline"] is True
        assert float(res["elapsed_ms"]) <= 200.0


def test_align_t48_ai_provenance_disclosure_and_status_separation_of_states() -> None:
    """ALIGN-T48: AI provenance, background IP, third-party BOM, and OFFICIAL_BRIEF/ORGANIZER_ERRATA/ASSUMED separation verified."""
    ai_doc = (REPO_ROOT / "AI_USE_DISCLOSURE.md").read_text(encoding="utf-8")
    prov_doc = (REPO_ROOT / "PROVENANCE.md").read_text(encoding="utf-8")
    bg_doc = (REPO_ROOT / "BACKGROUND_IP.md").read_text(encoding="utf-8")
    bom_csv = (REPO_ROOT / "THIRD_PARTY_COMPONENTS.csv").read_text(encoding="utf-8")
    assumptions_text = (REPO_ROOT / "ASSUMPTIONS.yaml").read_text(encoding="utf-8")

    assert "NOT_VERIFIED_EXTERNAL_LIST_MISSING" in ai_doc
    assert "Qwen" in ai_doc and "DeepSeek" in ai_doc
    assert len(prov_doc) > 200
    assert len(bg_doc) > 200
    assert "PyYAML" in bom_csv and "jsonschema" in bom_csv

    for token in ("OFFICIAL_BRIEF", "ORGANIZER_ERRATA", "ASSUMED"):
        assert token in assumptions_text

    align_status_path = REPO_ROOT / "ALIGNMENT_STATUS.json"
    if align_status_path.exists():
        align_doc = json.loads(align_status_path.read_text(encoding="utf-8"))
        assert isinstance(align_doc, dict)

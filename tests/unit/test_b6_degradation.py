"""Unit tests for B6 Scenario B Graceful Degradation & Load Shedding (ONT-03, DEG-01..05)."""

from __future__ import annotations

import copy
from pathlib import Path
import pytest

from sdv_governor.degradation import evaluate_reconnection, execute_graceful_degradation
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file as load_manifest_yaml,
    parse_guest_manifest,
)
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _setup_deg():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    return catalog, ship, prof


def test_ont_03_part_of_coherence() -> None:
    """ONT-03: Subcomponent part_of must match parent guest_id, and shedding/mode step-down acts coherently on the parent guest."""
    g5_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G5_emergency_comms_edge.yaml")
    bad_g5 = copy.deepcopy(g5_raw)
    bad_g5["subcomponents"][1]["part_of"] = "G1"
    with pytest.raises(StrictValidationError) as exc_info:
        parse_guest_manifest(bad_g5)
    assert exc_info.value.code == "PART_OF_MISMATCH"


def test_deg_01_valid_deficit_105_vs_100() -> None:
    """DEG-01: In 105 kW vs 100 kW deficit (G3 25 kW uninterruptible charge + G4 35 kW + G5 45 kW), G3 and G4 are protected and G5 steps down 45 -> 8 kW (68 kW <= 100 kW)."""
    catalog, ship, prof = _setup_deg()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_charging_deficit_105kw.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    state.power_reserve_w = 100000

    res = execute_graceful_degradation(state, ship, prof, catalog)
    assert res["verdict"] == "YES"
    assert res["deficit_analysis"]["initial_power_actual_w"] == 105000
    assert res["deficit_analysis"]["power_actual_deficit_w"] == 5000
    assert len(res["actions_taken"]) == 1
    act = res["actions_taken"][0]
    assert act["action_type"] == "MODE_STEP_DOWN"
    assert act["guest_id"] == "G5"
    assert act["from_mode"] == "FULL_SPECTRUM"
    assert act["to_mode"] == "DEGRADED_70PCT_LOSS"
    assert res["final_budget_snapshot"]["classes"]["power"]["actual"] == 68000
    assert res["final_budget_snapshot"]["feasible"] is True


def test_deg_02_cooling_binding_before_power() -> None:
    """DEG-02: At 30 C with 40% cooling loop loss (72 kW cooling capacity vs 100 kW power), cooling binds first (79.8 kW > 72 kW) and steps down G5."""
    catalog, ship, prof = _setup_deg()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_cooling_limited_30c.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    state.seawater_temp_c = 30.0
    state.cooling_loop_degraded = True
    state.power_reserve_w = 100000

    res = execute_graceful_degradation(state, ship, prof, catalog)
    assert res["verdict"] == "YES"
    # Power actual is 1 + 35 + 45 = 81 kW <= 100 kW (no power deficit), but cooling is 1 + 35 + 43.8 = 79.8 kW > 72 kW!
    assert res["deficit_analysis"]["power_actual_deficit_w"] == 0
    assert res["deficit_analysis"]["cooling_actual_deficit_w"] == 7800
    assert res["actions_taken"][0]["guest_id"] == "G5"
    assert res["final_budget_snapshot"]["classes"]["cooling"]["actual"] == 43500
    assert res["final_budget_snapshot"]["classes"]["cooling"]["feasible"] is True


def test_deg_03_degradation_deadlines() -> None:
    """DEG-03: First shedding/step-down command completes in <=5 s and full sequence in <=60 s; hysteresis reconnection enforces cooldown."""
    catalog, ship, prof = _setup_deg()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_charging_deficit_105kw.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    state.power_reserve_w = 100000
    state.sim_time_s = 720

    res = execute_graceful_degradation(state, ship, prof, catalog)
    assert res["within_5s_first_shed_deadline"] is True
    assert res["within_60s_sequence_deadline"] is True

    deg_state = res["resulting_state"]
    deg_state.power_reserve_w = 200000
    # Immediately at T=730s (only 10s elapsed < 60s cooldown) -> reconnection blocked by cooldown
    deg_state.sim_time_s = 730
    rec_early = evaluate_reconnection(deg_state, ship, prof, catalog, target_guest_id="G5", target_mode="FULL_SPECTRUM")
    assert rec_early["reconnect_allowed"] is False
    assert rec_early["reason"] == "COOLDOWN_ACTIVE"

    # At T=800s (80s elapsed >= 60s cooldown and 200 kW reserve gives >15 kW hysteresis margin) -> reconnection allowed
    deg_state.sim_time_s = 800
    rec_ok = evaluate_reconnection(deg_state, ship, prof, catalog, target_guest_id="G5", target_mode="FULL_SPECTRUM")
    assert rec_ok["reconnect_allowed"] is True


def test_deg_04_safety_critical_never_shed() -> None:
    """DEG-04: Safety-critical load (G4) is never shed automatically; if protected loads exceed capacity, INFEASIBLE_PROTECTED_LOAD is emitted."""
    catalog, ship, prof = _setup_deg()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_charging_deficit_105kw.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    # Drop power reserve to 40 kW while G3 (25 kW uninterruptible) + G4 (35 kW safety-critical) = 60 kW > 40 kW
    state.power_reserve_w = 40000

    res = execute_graceful_degradation(state, ship, prof, catalog)
    assert res["verdict"] == "NO"
    assert res["status"] == "INFEASIBLE_PROTECTED_LOAD"
    assert res["infeasible_protected_load"] is True
    post_state = res["resulting_state"]
    assert post_state.installed_guests["G4"].powered_on is True
    assert post_state.installed_guests["G3"].powered_on is True
    assert any(a["action_type"] == "OPERATOR_EMERGENCY_ESCALATION" for a in res["actions_taken"])


def test_deg_05_no_fabricated_instantaneous_deficit() -> None:
    """DEG-05: When G3 is in survey (1 kW) + G4 (35 kW) + G5 (45 kW) = 81 kW actual without firm reservation, 81 kW <= 100 kW is not falsely reported as an actual deficit."""
    catalog, ship, prof = _setup_deg()
    state = ShipOperationalState(vessel_mode="UAV_RECOVERY_HEADING", power_reserve_w=100000, seawater_temp_c=24.0)
    state.installed_guests["G3"] = InstalledGuestState("G3", "SURVEY_QUIET", True, {"G3_AUV_CRADLE_BAY1_20FT": "bay1"}, "node_1")
    state.installed_guests["G4"] = InstalledGuestState("G4", "LAUNCH_RECOVERY_OPTICS", True, {"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"}, "node_2")
    state.installed_guests["G5"] = InstalledGuestState("G5", "FULL_SPECTRUM", True, {"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"}, "node_3")

    res = execute_graceful_degradation(state, ship, prof, catalog)
    assert res["deficit_analysis"]["initial_power_actual_w"] == 81000
    assert res["deficit_analysis"]["power_actual_deficit_w"] == 0
    assert res["verdict"] == "YES"
    assert len(res["actions_taken"]) == 0

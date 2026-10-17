"""Unit tests for B4 Scenario A Admission & Placement Solver (ONT-01..02, PHY-05, ADM-01..05)."""

from __future__ import annotations

import copy
from pathlib import Path

from sdv_governor.admission import evaluate_admission
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file as load_manifest_yaml,
    parse_guest_manifest,
)
from sdv_governor.replay import load_yaml_file
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _setup_adm():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    env = load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml")
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    return catalog, ship, env, prof


def test_ont_01_missing_requires() -> None:
    """ONT-01: Missing required dependency guest produces explicit dependency witness and rejection."""
    catalog, ship, env, prof = _setup_adm()
    g6_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    g6_mod = copy.deepcopy(g6_raw)
    g6_mod["dependencies"]["requires_guests"] = ["G1"]
    cand = parse_guest_manifest(g6_mod)

    state = ShipOperationalState(vessel_mode="PORT_OPS")
    dec = evaluate_admission(cand, state, ship, env, prof, catalog, at_port=True)
    assert dec["verdict"] == "NO"
    assert any(w["resource_class"] == "dependencies" and "G1" in w["detail"] for w in dec["binding_constraints"])


def test_ont_02_absolute_exclusion() -> None:
    """ONT-02: Absolute exclusion between candidate and installed guest prevents simultaneous admission."""
    catalog, ship, env, prof = _setup_adm()
    g6_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    g6_mod = copy.deepcopy(g6_raw)
    g6_mod["dependencies"]["excludes_guests"] = ["G2"]
    cand = parse_guest_manifest(g6_mod)

    state = ShipOperationalState(vessel_mode="PORT_OPS")
    state.installed_guests["G2"] = InstalledGuestState(
        "G2", "INSPECTION_ACTIVE", True, {"G2_ROV_WINCH_CONTAINER_10FT": "bay2"}, "node_1"
    )
    # Without displacement allowed -> NO
    dec_no = evaluate_admission(cand, state, ship, env, prof, catalog, at_port=True, allow_displacement=False)
    assert dec_no["verdict"] == "NO"
    assert any(w["resource_class"] == "dependencies" for w in dec_no["binding_constraints"])

    # With displacement allowed -> YES_IF (displace G2)
    dec_yes_if = evaluate_admission(cand, state, ship, env, prof, catalog, at_port=True, allow_displacement=True)
    assert dec_yes_if["verdict"] == "YES_IF"
    assert dec_yes_if["ranked_displacements"][0]["displace_guest_ids"] == ["G2"]


def test_phy_05_local_power_line_limit() -> None:
    """PHY-05: Local power line limit (150 kW per line) triggers rejection even when global reserve is 200 kW."""
    catalog, ship, env, prof = _setup_adm()
    # LINE_A_FWD (150 kW) feeds bay1, bay1_a, bay1_b, mast_1..3
    # Create a custom high-power card in bay1_a drawing 115 kW while G5 in bay1_b draws 45 kW -> 160 kW > 150 kW on LINE_A_FWD, even though 160 kW <= 200 kW global!
    state = ShipOperationalState(vessel_mode="PORT_OPS", power_reserve_w=200000)
    state.installed_guests["G5"] = InstalledGuestState(
        "G5", "FULL_SPECTRUM", True, {"G5_COMPUTE_CONTAINER": "bay1_b", "G5_MAST_ANTENNA": "mast_3"}, "node_1"
    )
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

    dec = evaluate_admission(cand, state, ship, env, prof, catalog, at_port=True, allow_displacement=False)
    assert dec["verdict"] == "NO"
    assert any("LINE_A_FWD" in w["detail"] for w in dec["binding_constraints"])


def test_adm_01_scenario_a_new_card() -> None:
    """ADM-01: Scenario A admission of new custom card G6 completes in <=30 s with real witness, placement, and A.10 envelope."""
    catalog, ship, env, prof = _setup_adm()
    state = ShipOperationalState(vessel_mode="PORT_OPS", power_reserve_w=200000)
    state.installed_guests["G1"] = InstalledGuestState("G1", "NOMINAL", True, {"G1_MAST_SENSOR_HEAD": "mast_3"}, "node_1")
    state.installed_guests["G2"] = InstalledGuestState("G2", "INSPECTION_ACTIVE", True, {"G2_ROV_WINCH_CONTAINER_10FT": "bay2"}, "node_2")
    state.installed_guests["G4"] = InstalledGuestState("G4", "LAUNCH_RECOVERY_OPTICS", True, {"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"}, "node_3")

    dec = evaluate_admission(catalog["G6"], state, ship, env, prof, catalog, at_port=True)
    assert dec["verdict"] == "YES"
    assert dec["calculation_elapsed_ms"] <= 30000.0
    assert dec["placement"]["subcomponent_positions"]["G6_HYDRO_SKID_10FT"] in ("bay1_a", "bay1_b")
    assert len(dec["mission_envelope_hash"]) == 64


def test_adm_02_jury_mutation_changes_verdict() -> None:
    """ADM-02: Mutating a number in a guest card (e.g., G6 mass from 420 kg to 6,000 kg) alters the verdict without editing code."""
    catalog, ship, env, prof = _setup_adm()
    state = ShipOperationalState(vessel_mode="PORT_OPS", power_reserve_w=200000)
    state.installed_guests["G2"] = InstalledGuestState("G2", "INSPECTION_ACTIVE", True, {"G2_ROV_WINCH_CONTAINER_10FT": "bay2"}, "node_1")
    state.installed_guests["G4"] = InstalledGuestState("G4", "LAUNCH_RECOVERY_OPTICS", True, {"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"}, "node_2")

    # With normal G6 (420 kg): 3500 + 990 + 420 = 4910 kg <= 10,000 kg -> YES
    dec_normal = evaluate_admission(catalog["G6"], state, ship, env, prof, catalog, at_port=True, allow_displacement=False)
    assert dec_normal["verdict"] == "YES"

    # Mutate G6 mass to 6000 kg: 3500 + 990 + 6000 = 10,490 kg > 10,000 kg -> NO (when displacement disabled) or YES_IF
    g6_raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    g6_heavy = copy.deepcopy(g6_raw)
    g6_heavy["subcomponents"][0]["mass_kg"] = 6000
    cand_heavy = parse_guest_manifest(g6_heavy)

    dec_heavy_no = evaluate_admission(cand_heavy, state, ship, env, prof, catalog, at_port=True, allow_displacement=False)
    assert dec_heavy_no["verdict"] == "NO"
    assert any(w["resource_class"] == "mass_stability" for w in dec_heavy_no["binding_constraints"])


def test_adm_03_pier_vs_mission_envelope() -> None:
    """ADM-03: Pier comfort condition (16 C) does not hide Sea State 5 / 30 C conditioning in the 14-day mission envelope."""
    catalog, ship, env, prof = _setup_adm()
    state = ShipOperationalState(vessel_mode="PORT_OPS", seawater_temp_c=16.0, power_reserve_w=200000)
    dec_g4 = evaluate_admission(catalog["G4"], state, ship, env, prof, catalog, at_port=True)
    assert dec_g4["verdict"] == "YES"
    assert dec_g4["conditioned_availability"]["conditioned"] is True
    assert any(
        w["window_id"] == "HIGH_SEA_STATE_5_WIND_28KN"
        for w in dec_g4["conditioned_availability"]["restricted_windows"]
    )


def test_adm_04_four_verdicts_complete() -> None:
    """ADM-04: All 4 verdicts (YES, YES_IF, NO, NOT_NOW) are produced with complete structured decision fields."""
    catalog, ship, env, prof = _setup_adm()
    state = ShipOperationalState(vessel_mode="PORT_OPS", power_reserve_w=200000)

    # 1. YES
    d_yes = evaluate_admission(catalog["G1"], state, ship, env, prof, catalog, at_port=True)
    assert d_yes["verdict"] == "YES"

    # 2. YES_IF: install G1, G2, G3 (9045 kg), then request G4 (990 kg -> 10,035 kg > 10,000 kg)
    for gid in ("G1", "G2", "G3"):
        d = evaluate_admission(catalog[gid], state, ship, env, prof, catalog, at_port=True)
        p = d["placement"]
        state.installed_guests[gid] = InstalledGuestState(gid, p["initial_mode"], True, p["subcomponent_positions"], p["compute_node"])

    d_yes_if = evaluate_admission(catalog["G4"], state, ship, env, prof, catalog, at_port=True, allow_displacement=True)
    assert d_yes_if["verdict"] == "YES_IF"
    assert len(d_yes_if["ranked_displacements"]) >= 1
    assert len(d_yes_if["capability_lost"]) >= 1

    # 3. NO: same state with allow_displacement=False
    d_no = evaluate_admission(catalog["G4"], state, ship, env, prof, catalog, at_port=True, allow_displacement=False)
    assert d_no["verdict"] == "NO"

    # 4. NOT_NOW: port-only guest requested at sea with defer_if_temporary_operational_block=True
    d_not_now = evaluate_admission(
        catalog["G5"], state, ship, env, prof, catalog,
        at_port=False, defer_if_temporary_operational_block=True,
        next_window={"start_s": 86400, "reason": "Next scheduled port call"}
    )
    assert d_not_now["verdict"] == "NOT_NOW"
    assert d_not_now["next_window"]["start_s"] == 86400


def test_adm_05_not_now_null_window_when_unknown() -> None:
    """ADM-05: When no future window is provably known, NOT_NOW returns next_window=null without inventing a future time."""
    catalog, ship, env, prof = _setup_adm()
    state = ShipOperationalState(vessel_mode="QUIET_SURVEY", ship_speed_kn=3.0, wind_kn=18)
    d_not_now = evaluate_admission(
        catalog["G5"], state, ship, env, prof, catalog,
        at_port=False, defer_if_temporary_operational_block=True, next_window=None
    )
    assert d_not_now["verdict"] == "NOT_NOW"
    assert d_not_now["next_window"] is None
    assert "next_window=null" in d_not_now["rationale"]

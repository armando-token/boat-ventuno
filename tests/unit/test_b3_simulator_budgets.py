"""Unit tests for B3 8-class physical resource budgets, thermal inertia, and deterministic replay (PHY-01..04, 06..10, RUN-01)."""

from __future__ import annotations

from pathlib import Path
import pytest

from sdv_governor.budgets import compute_cooling_water_capacity_w, evaluate_eight_class_budgets
from sdv_governor.manifest import load_guest_catalog, load_priority_profile
from sdv_governor.replay import load_yaml_file, run_scenario_b_replay
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _load_fixtures():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    return catalog, ship


def test_phy_01_zero_budget_and_infeasible_protected() -> None:
    """PHY-01: Zero power reserve cannot admit positive load and protected load exceeding capacity flags INFEASIBLE_PROTECTED_LOAD."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState(power_reserve_w=0, seawater_temp_c=20.0)
    state.installed_guests["G4"] = InstalledGuestState(
        guest_id="G4",
        active_mode="LAUNCH_RECOVERY_OPTICS",
        placements={"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"},
        compute_node="node_1",
        powered_on=True,
    )
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap["feasible"] is False
    assert snap["infeasible_protected_load"] is True
    assert "INFEASIBLE_PROTECTED_LOAD" in (snap["infeasible_reason"] or "")


def test_phy_02_cooling_curve_30c_and_degraded() -> None:
    """PHY-02: Seawater at 30 C yields 120,000 W nominal and 72,000 W with 40% loop degradation; 12 C yields 160,000 W."""
    nom_12, eff_12, _ = compute_cooling_water_capacity_w(12.0, loop_degraded=False)
    assert nom_12 == 160000
    assert eff_12 == 160000

    nom_30, eff_30_ok, _ = compute_cooling_water_capacity_w(30.0, loop_degraded=False)
    assert nom_30 == 120000
    assert eff_30_ok == 120000

    _, eff_30_deg, policy = compute_cooling_water_capacity_w(30.0, loop_degraded=True)
    assert eff_30_deg == 72000
    assert "LOOP_DEGRADED_40PCT" in policy

    # Below 12 C must conservatively clamp at 160 kW (no optimistic extrapolation)
    nom_8, _, pol_8 = compute_cooling_water_capacity_w(8.0, loop_degraded=False)
    assert nom_8 == 160000
    assert "CLAMPED_LOW_TEMP" in pol_8


def test_phy_03_coupled_heat_1_to_1() -> None:
    """PHY-03: Changing electrical modes changes thermal dissipation 1:1 without omissions."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState(power_reserve_w=200000, seawater_temp_c=20.0)
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"},
        compute_node="node_1",
        powered_on=True,
    )
    snap_45 = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap_45["classes"]["power"]["actual"] == 45000
    assert snap_45["classes"]["cooling"]["local_limits"]["total_thermal_dissipation_w"] == 45000
    assert snap_45["classes"]["cooling"]["actual"] == 43800
    assert snap_45["classes"]["cooling"]["local_limits"]["mast_air_cooling"]["actual_w"] == 1200

    state.installed_guests["G5"].active_mode = "DEGRADED_70PCT_LOSS"
    snap_8 = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap_8["classes"]["power"]["actual"] == 8000
    assert snap_8["classes"]["cooling"]["local_limits"]["total_thermal_dissipation_w"] == 8000
    assert snap_8["classes"]["cooling"]["actual"] == 7500
    assert snap_8["classes"]["cooling"]["local_limits"]["mast_air_cooling"]["actual_w"] == 500


def test_phy_04_sustained_vs_peak_tolerance() -> None:
    """PHY-04: Sustained loads (G4 35 kW for 20 min = 1200 s, G2 20 kW for 5 min = 300 s) cannot use 60 s thermal inertia boost."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState(power_reserve_w=200000, seawater_temp_c=30.0, cooling_loop_degraded=True)
    state.installed_guests["G4"] = InstalledGuestState(
        guest_id="G4",
        active_mode="LAUNCH_RECOVERY_OPTICS",
        placements={"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"},
        compute_node="node_1",
        powered_on=True,
    )
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap["classes"]["cooling"]["local_limits"]["thermal_inertia_available_boost_w"] == 0


def test_phy_06_mast_constraints() -> None:
    """PHY-06: Mast has 3 positions (2 fixed occupied, 1 free mast_3), <=60 kg, <=0.6 m2, no water, air <=2 kW."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState()
    state.installed_guests["G1"] = InstalledGuestState(
        guest_id="G1",
        active_mode="NOMINAL",
        placements={"G1_MAST_SENSOR_HEAD": "mast_3"},
        compute_node="node_1",
        powered_on=True,
    )
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap["classes"]["mast"]["feasible"] is True
    assert snap["classes"]["mast"]["free"] == 0

    # Adding G5 antenna also onto mast_3 violates single-occupant mast_3 constraint
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"},
        compute_node="node_2",
        powered_on=True,
    )
    snap_conflict = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap_conflict["classes"]["mast"]["feasible"] is False


def test_phy_07_mass_and_stability_limits() -> None:
    """PHY-07: Per-subcomponent stability multipliers (x1 bay, x1.2 hangar, x3 mast) and separate 10,000 kg / 12,000 kg-eq limits."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState()
    state.installed_guests["G4"] = InstalledGuestState(
        guest_id="G4",
        active_mode="STANDBY",
        placements={"G4_DRONE_VEHICLE": "hangar", "G4_HANGAR_STATION": "hangar"},
        compute_node="node_1",
    )
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="STANDBY",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"},
        compute_node="node_2",
    )
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    mass_info = snap["classes"]["mass_stability"]["local_limits"]
    assert mass_info["mass_margin_kg"]["actual_kg"] == 990 + 3025
    # G4 stability = 990 * 1.2 = 1188.0; G5 stability = 3000 * 1.0 + 25 * 3.0 = 3075.0 -> total = 4263.0 kg-eq
    assert mass_info["stability_equivalent_kg"]["actual_kg_eq"] == 4263.0


def test_phy_08_power_off_retains_physical_footprint() -> None:
    """PHY-08: Powering off a guest reduces electrical/cooling load to 0 W but retains bay, mast, mass, and stability."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState()
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"},
        compute_node="node_1",
        powered_on=False,
    )
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap["classes"]["power"]["actual"] == 0
    assert snap["classes"]["mass_stability"]["actual"] == 3025
    assert snap["classes"]["positions"]["local_limits"]["bay2_10ft"] == ["G5:G5_COMPUTE_CONTAINER"]
    assert snap["classes"]["mast"]["local_limits"]["guest_slot_mast_3"]["owner"] == "G5:G5_MAST_ANTENNA"


def test_phy_09_thermal_inertia_no_tick_reset() -> None:
    """PHY-09: Thermal inertia (+30 kW for 60 s = 1.8 MJ every 10 min) accumulates energy across ticks and enters 600 s cooldown."""
    state = ShipOperationalState()
    ti = state.thermal_inertia
    for _ in range(60):
        ok, status = ti.step_tick(30000, dt_s=1)
        assert ok is True
    assert ti.exhausted is True
    assert ti.cooldown_remaining_s == 600
    assert ti.available_boost_w(1) == 0

    ok_61, status_61 = ti.step_tick(5000, dt_s=1)
    assert ok_61 is False
    assert status_61 == "THERMAL_INERTIA_IN_COOLDOWN"


def test_phy_10_multidimensional_node_compute() -> None:
    """PHY-10: 4 nodes (25 allocatable cores, 102,400 MiB RAM, 1 whole accelerator per node; max 3 cluster accelerators)."""
    catalog, ship = _load_fixtures()
    state = ShipOperationalState()
    # Place G2 (1 accel), G4 (1 accel), G5 (1 accel) on node_1, node_2, node_3 -> 3 cluster accelerators used (max allowed = 3)
    state.installed_guests["G2"] = InstalledGuestState("G2", "INSPECTION_ACTIVE", True, {"G2_ROV_WINCH_CONTAINER_10FT": "bay1_a"}, "node_1")
    state.installed_guests["G4"] = InstalledGuestState("G4", "LAUNCH_RECOVERY_OPTICS", True, {"G4_DRONE_VEHICLE": "flight_deck", "G4_HANGAR_STATION": "hangar"}, "node_2")
    state.installed_guests["G5"] = InstalledGuestState("G5", "FULL_SPECTRUM", True, {"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "mast_3"}, "node_3")
    snap = evaluate_eight_class_budgets(ship, catalog, state)
    assert snap["classes"]["compute"]["feasible"] is True
    assert snap["classes"]["compute"]["local_limits"]["cluster_accelerators"]["used"] == 3
    assert snap["classes"]["compute"]["local_limits"]["cluster_accelerators"]["free"] == 0


def test_run_01_triple_replay_identical_hash() -> None:
    """RUN-01: Three consecutive executions of Scenario B replay produce identical semantic SHA-256 hashes."""
    catalog, ship = _load_fixtures()
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")

    r1 = run_scenario_b_replay(scen, ship, prof, catalog)
    r2 = run_scenario_b_replay(scen, ship, prof, catalog)
    r3 = run_scenario_b_replay(scen, ship, prof, catalog)

    assert r1["semantic_sha256"] == r2["semantic_sha256"] == r3["semantic_sha256"]
    assert len(r1["semantic_sha256"]) == 64

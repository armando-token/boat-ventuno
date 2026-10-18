"""Unit tests for Forecast Physics Projector (FC-UNIT-12, 13, 16, 17).

Verifies:
- FC-UNIT-12: Scalar screening (50 kW > 1.10 * 45 kW flags risk; 49 kW does not).
- FC-UNIT-13: Monotonic bisection finds crossing at h=37 with <= 8 evaluations.
- FC-UNIT-16: Non-mutation invariant (state serialization before and after projector is identical).
- FC-UNIT-17: Thermal inertia simulation under exact step_tick rules (60s continuous cap vs 1.8 MJ).
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from sdv_governor.forecast.projector import (
    bisection_first_infeasible,
    project_guest_physics,
    screen_scalar_risk,
    simulate_thermal_exhaustion,
    ASM_FCST_OVERRUN_THRESHOLD,
)
from sdv_governor.manifest import load_guest_catalog
from sdv_governor.replay import load_yaml_file
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState, ThermalInertiaState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _load_fixtures():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    return catalog, ship


def test_fc_unit_12_scalar_screen_overrun_threshold() -> None:
    """FC-UNIT-12: Proyector, barrido | ŷ_hi=50 kW, declared=45 kW -> riesgo; ŷ_hi=49 kW -> sin riesgo."""
    declared_w = 45000.0  # 45 kW

    # 1. Upper bound 50 kW: 50,000 > 1.10 * 45,000 (49,500) -> Risk detected
    risk_50, reason_50 = screen_scalar_risk(50000.0, declared_w, threshold_ratio=ASM_FCST_OVERRUN_THRESHOLD)
    assert risk_50 is True
    assert reason_50 is not None
    assert "OVERRUN" in reason_50

    # 2. Upper bound 49 kW: 49,000 <= 49,500 -> No risk detected
    risk_49, reason_49 = screen_scalar_risk(49000.0, declared_w, threshold_ratio=ASM_FCST_OVERRUN_THRESHOLD)
    assert risk_49 is False
    assert reason_49 is None

    # Verify also with direct kW values
    assert screen_scalar_risk(50.0, 45.0)[0] is True
    assert screen_scalar_risk(49.0, 45.0)[0] is False


def test_fc_unit_13_bisection_monotonic_crossing_h37() -> None:
    """FC-UNIT-13: Proyector, bisección | con un evaluador falso monótono con cruce en h=37 -> h*=37 con <= 8 llamadas."""
    eval_calls = 0

    def mock_evaluator(h: int) -> bool:
        nonlocal eval_calls
        eval_calls += 1
        # Feasible for h < 37, infeasible for h >= 37
        return h < 37

    h_star, evals_used = bisection_first_infeasible(mock_evaluator, min_h=1, max_h=60)

    assert h_star == 37
    assert evals_used <= 8
    assert eval_calls == evals_used

    # Additional boundary verification: crossing at extremes
    # Case: crossing at 1
    evals_at_1 = 0
    h_star_1, used_1 = bisection_first_infeasible(lambda h: False, min_h=1, max_h=60)
    assert h_star_1 == 1
    assert used_1 <= 8

    # Case: crossing at 60
    h_star_60, used_60 = bisection_first_infeasible(lambda h: h < 60, min_h=1, max_h=60)
    assert h_star_60 == 60
    assert used_60 <= 8

    # Case: all feasible (no breach in 60s)
    h_star_none, used_none = bisection_first_infeasible(lambda h: True, min_h=1, max_h=60)
    assert h_star_none is None
    assert used_none == 1  # only evaluated max_h=60


def test_fc_unit_16_state_immutability() -> None:
    """FC-UNIT-16: No muta el estado | state.clone() serializado antes y después del proyector es idéntico."""
    catalog, ship = _load_fixtures()

    state = ShipOperationalState(
        sim_time_s=100,
        vessel_mode="TRANSIT",
        power_reserve_w=50000,
        seawater_temp_c=16.0,
    )
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "hangar_roof"},
        compute_node="node_1",
        powered_on=True,
    )

    before_dump = asdict(state.clone())

    # Trajectory that breaches power limit at h ~ 30
    def traj_hi(h: int) -> float:
        return 45000.0 + h * 200.0

    def traj_mid(h: int) -> float:
        return 45000.0 + h * 150.0

    def traj_lo(h: int) -> float:
        return 45000.0 + h * 100.0

    result = project_guest_physics(
        ship_profile=ship,
        catalog=catalog,
        state=state,
        guest_id="G5",
        declared_w=45000.0,
        power_trajectory_hi=traj_hi,
        power_trajectory_mid=traj_mid,
        power_trajectory_lo=traj_lo,
        slope_w_per_s=150.0,
    )

    assert result is not None
    assert result["guest_id"] == "G5"
    assert result["evals_used"] <= 8

    after_dump = asdict(state.clone())

    assert before_dump == after_dump, "Original state was mutated by project_guest_physics!"
    assert state.installed_guests["G5"].actual_power_override_w is None
    assert state.thermal_inertia.accumulated_boost_energy_j == 0


def test_fc_unit_17_thermal_inertia_exhaustion_rules() -> None:
    """FC-UNIT-17: Inercia proyectada (reglas reales de step_tick: tope 60 s continuos y 1,8 MJ).
    
    Assertions:
    - exceso 10 kW desde estado limpio -> exhaust_in_s=60 (manda el tope de 60 s, no la energía)
    - exceso 30 kW -> 60
    - exceso 31 kW -> exhaust_in_s=0 (EXCEEDS_MAX_THERMAL_BOOST)
    - en cooldown -> 0
    """
    # 1. Clean state with 10 kW excess: exhausts at 60s due to continuous 60s limit
    clean_ti = ThermalInertiaState()
    res_10k = simulate_thermal_exhaustion(clean_ti, excess_heat_w=10000)
    assert res_10k["exhaust_in_s"] == 60
    # Energy accumulated: 60s * 10 kW = 600 kJ; remaining: 1.8 MJ - 0.6 MJ = 1.2 MJ
    assert res_10k["remaining_j"] == 1800000 - 600000

    # 2. Clean state with 30 kW excess: reaches 1.8 MJ and 60s simultaneously -> exhaust_in_s=60
    clean_ti2 = ThermalInertiaState()
    res_30k = simulate_thermal_exhaustion(clean_ti2, excess_heat_w=30000)
    assert res_30k["exhaust_in_s"] == 60
    assert res_30k["remaining_j"] == 0

    # 3. Excess exceeds maximum allowable boost (31 kW > 30 kW) -> exhaust_in_s=0 immediately
    clean_ti3 = ThermalInertiaState()
    res_31k = simulate_thermal_exhaustion(clean_ti3, excess_heat_w=31000)
    assert res_31k["exhaust_in_s"] == 0
    assert "EXCEEDS_MAX_THERMAL_BOOST" in res_31k["status"]

    # 4. In cooldown -> exhaust_in_s=0 immediately
    cooldown_ti = ThermalInertiaState(cooldown_remaining_s=120, exhausted=True)
    res_cool = simulate_thermal_exhaustion(cooldown_ti, excess_heat_w=10000)
    assert res_cool["exhaust_in_s"] == 0
    assert res_cool["status"] == "THERMAL_INERTIA_IN_COOLDOWN"

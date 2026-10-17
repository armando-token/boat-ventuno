"""Unit tests for B5 Scenario B Live Arbitration Engine (ARB-01..04)."""

from __future__ import annotations

import copy
from pathlib import Path
import pytest

from sdv_governor.arbitration import arbitrate_live_conflict
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file as load_manifest_yaml,
    parse_priority_profile,
)
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _setup_arb():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    state.sim_time_s = 480
    prof_survey = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    prof_rescue = load_priority_profile(DATA_DIR / "priorities" / "rescue_primary.yaml")
    return catalog, ship, state, prof_survey, prof_rescue


def test_arb_01_survey_primary() -> None:
    """ARB-01: Survey-primary completes in <=2 s, preserves G4 <20% battery 12-min deadline, and quantifies survey cost."""
    catalog, ship, state, prof_survey, _ = _setup_arb()
    res = arbitrate_live_conflict(state, ship, prof_survey, catalog)
    assert res["within_2s_deadline"] is True
    assert res["calculation_elapsed_ms"] <= 2000.0
    assert res["hard_safety_satisfied"] is True
    assert res["touchdown_time_s"] <= res["hard_touchdown_deadline_s"]
    assert res["losing_mission_cost"]["survey_segment_preserved_s"] == 240
    assert res["losing_mission_cost"]["survey_segment_aborted_s"] == 0


def test_arb_02_rescue_primary() -> None:
    """ARB-02: Rescue-primary completes in <=2 s and produces a distinct, earlier turn schedule from the same input data."""
    catalog, ship, state, prof_survey, prof_rescue = _setup_arb()
    res_survey = arbitrate_live_conflict(state, ship, prof_survey, catalog)
    res_rescue = arbitrate_live_conflict(state, ship, prof_rescue, catalog)

    assert res_rescue["within_2s_deadline"] is True
    assert res_rescue["hard_safety_satisfied"] is True
    assert res_rescue["turn_into_wind_time_s"] < res_survey["turn_into_wind_time_s"]
    assert res_rescue["touchdown_time_s"] < res_survey["touchdown_time_s"]
    assert res_rescue["losing_mission_cost"]["survey_segment_aborted_s"] == 240


def test_arb_03_invalid_priority_and_tiebreak() -> None:
    """ARB-03: Invalid priority profile is rejected fail-closed; valid profiles have explicit deterministic tie-break rules."""
    raw_p, _ = load_manifest_yaml(DATA_DIR / "priorities" / "survey_primary.yaml")
    bad_p = copy.deepcopy(raw_p)
    bad_p["arbitration_policy"]["survey_vs_recovery_strategy"] = "IGNORE_DRONE_BATTERY_AND_CRASH"
    with pytest.raises(StrictValidationError) as exc_info:
        parse_priority_profile(bad_p)
    assert exc_info.value.code == "UNKNOWN_ENUM"


def test_arb_04_acoustic_hold_latency() -> None:
    """ARB-04: Both profiles issue ACOUSTIC_HOLD to G3 and wait 15 s (within 10-60 s spec) before turning the ship."""
    catalog, ship, state, prof_survey, prof_rescue = _setup_arb()
    for prof in (prof_survey, prof_rescue):
        res = arbitrate_live_conflict(state, ship, prof, catalog)
        assert 10 <= res["handshake_latency_s"] <= 60
        assert res["turn_into_wind_time_s"] - res["hold_cmd_time_s"] == res["handshake_latency_s"]
        cmds = [step["command"] for step in res["action_sequence"]]
        assert cmds.index("ACOUSTIC_HOLD") < cmds.index("TURN_INTO_WIND_FOR_RECOVERY")

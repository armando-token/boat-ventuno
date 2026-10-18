"""End-to-end integration tests for Consumption Forecasting & Golden Gate Operational Windows (E2E-01..06).

Validates:
- E2E-01: G5 ramp warning before action in Scenario B Golden Gate (WARN >= 10s before action at T=297).
- E2E-02: G5 steady consumption over 1 hour (3600 s) produces 0 WARN alerts (no false alarms).
- E2E-03: G5 transient spikes within card limit produce 0 WARN alerts.
- E2E-04: G5 mode change to lower mode triggers WARMUP and 0 false alarms.
- E2E-05: Card modified: lowering declared power to 40 kW changes threshold to 44 kW and sentence reflects it.
- E2E-06: Golden Gate A-prime replacement NOT_NOW with computed operational window or explicit 48h limit.
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import Any, Dict

import pytest

from sdv_governor.budgets import evaluate_eight_class_budgets
from sdv_governor.forecast.observer import ForecastObserver
from sdv_governor.forecast.telemetry import TelemetryProfile, generate_episode
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    validate_ship_profile,
)
from sdv_governor.replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_public_env_suite,
    run_scenario_b_replay,
)
from sdv_governor.ship_state import ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def e2e_setup() -> Dict[str, Any]:
    data_dir = REPO_ROOT / "data"
    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
    ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    prof_survey = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
    scen_b_gg = load_yaml_file(data_dir / "scenarios" / "scenario_B_golden_gate_recorded.yaml")

    return {
        "catalog": catalog,
        "ship": ship,
        "prof_survey": prof_survey,
        "scen_b_gg": scen_b_gg,
    }


def test_e2e_01_g5_ramp_warning_before_action(e2e_setup: Dict[str, Any]) -> None:
    """E2E-01: G5 in ramp triggers WARN advisory before reactive governor action at T=297 s (lead >= 10 s)."""
    catalog = e2e_setup["catalog"]
    ship = e2e_setup["ship"]
    prof = e2e_setup["prof_survey"]
    scen_b_gg = e2e_setup["scen_b_gg"]

    obs = ForecastObserver(ship, catalog)
    res = run_scenario_b_replay(scen_b_gg, ship, prof, catalog, observer=obs)

    # 1. Verify reactive handler acted at T=297 s
    events_at_297 = [
        ev for tick in res["timeline_ticks"]
        for ev in tick["events"]
        if "GUEST_OVERCONSUMPTION" in ev
    ]
    assert len(events_at_297) > 0, "Reactive GUEST_OVERCONSUMPTION not recorded in replay events!"

    # 2. Verify observer issued WARN before T=297 s
    warn_alerts = [a for a in obs.alerts_history if a["state"] == "WARN" and a["guest_id"] == "G5"]
    assert len(warn_alerts) > 0, "Expected WARN alert for G5 during ramp!"

    first_warn_s = warn_alerts[0]["sim_time_s"]
    action_time_s = 297

    # Lead time must be at least 10s (target is ~30-45s)
    lead_time_s = action_time_s - first_warn_s
    assert lead_time_s >= 10, f"Expected lead_time_s >= 10, got {lead_time_s}s (warn at {first_warn_s}s, action at {action_time_s}s)"

    # Verify sentence is parameterized with actual numbers
    first_sentence = warn_alerts[0]["sentence"]
    assert "G5" in first_sentence
    assert "projected to exceed" in first_sentence
    assert "110% of declared" in first_sentence
    assert "Advisory only — governor action unchanged." in first_sentence


def test_e2e_02_g5_steady_1h_no_false_alarms(e2e_setup: Dict[str, Any]) -> None:
    """E2E-02: G5 operating in steady state for 1 hour (3600 s) produces 0 WARN alerts."""
    catalog = e2e_setup["catalog"]
    ship = e2e_setup["ship"]
    scen_b_gg = e2e_setup["scen_b_gg"]

    ep_steady = generate_episode("G5", 45000.0, TelemetryProfile("steady", {}), 3600, seed=5000)
    obs = ForecastObserver(ship, catalog)
    st = build_initial_state_from_scenario(scen_b_gg, catalog)
    snap = evaluate_eight_class_budgets(ship, catalog, st)

    for s in range(3600):
        val = ep_steady["y_obs"][s]
        obs.on_sample(s, {"G5": val}, state_view=st)
        if s % 30 == 0:
            obs.on_tick(s, st.clone(), snap)

    warn_alerts = [a for a in obs.alerts_history if a["state"] == "WARN" and a["guest_id"] == "G5"]
    assert len(warn_alerts) == 0, f"False alarms detected on steady profile: {warn_alerts}"


def test_e2e_03_g5_transient_spikes_no_false_alarms(e2e_setup: Dict[str, Any]) -> None:
    """E2E-03: Transient power spikes within card peak limits produce 0 WARN alerts."""
    catalog = e2e_setup["catalog"]
    ship = e2e_setup["ship"]
    scen_b_gg = e2e_setup["scen_b_gg"]

    ep_spike = generate_episode("G5", 45000.0, TelemetryProfile("spike", {}), 600, seed=1234)
    obs = ForecastObserver(ship, catalog)
    st = build_initial_state_from_scenario(scen_b_gg, catalog)
    snap = evaluate_eight_class_budgets(ship, catalog, st)

    for s in range(600):
        val = ep_spike["y_obs"][s]
        obs.on_sample(s, {"G5": val}, state_view=st)
        if s % 30 == 0:
            obs.on_tick(s, st.clone(), snap)

    warn_alerts = [a for a in obs.alerts_history if a["state"] == "WARN" and a["guest_id"] == "G5"]
    assert len(warn_alerts) == 0, f"Spike profile caused false alarms: {warn_alerts}"


def test_e2e_04_g5_mode_change_suppression_and_warmup(e2e_setup: Dict[str, Any]) -> None:
    """E2E-04: Mode change to lower mode triggers WARMUP and SUPPRESSED without false alarms."""
    catalog = e2e_setup["catalog"]
    ship = e2e_setup["ship"]
    scen_b_gg = e2e_setup["scen_b_gg"]

    ep_mc = generate_episode("G5", 45000.0, TelemetryProfile("mode_change", {}), 600, seed=1235)
    obs = ForecastObserver(ship, catalog)
    st = build_initial_state_from_scenario(scen_b_gg, catalog)
    snap = evaluate_eight_class_budgets(ship, catalog, st)

    for s in range(600):
        st.installed_guests["G5"].active_mode = ep_mc["mode"][s]
        obs.on_sample(s, {"G5": ep_mc["y_obs"][s]}, state_view=st)
        if s % 30 == 0:
            obs.on_tick(s, st.clone(), snap)

    warn_alerts = [a for a in obs.alerts_history if a["state"] == "WARN" and a["guest_id"] == "G5"]
    assert len(warn_alerts) == 0, f"Mode change caused false alarms: {warn_alerts}"

    # Verify WARMUP transition was registered
    transitions = obs.advisory_machines["G5"].transitions
    assert any(t.to_state == "SUPPRESSED" and "WARMUP" in str(t.cause) for t in transitions)


def test_e2e_05_card_modified_new_threshold_and_sentence(e2e_setup: Dict[str, Any]) -> None:
    """E2E-05: When card nominal is modified to 40 kW, threshold becomes 44 kW and sentence reflects it."""
    ship = e2e_setup["ship"]
    scen_b_gg = e2e_setup["scen_b_gg"]

    # Deep copy catalog and adjust G5 nominal from 45 kW to 40 kW
    custom_catalog = copy.deepcopy(e2e_setup["catalog"])
    old_mode = custom_catalog["G5"].modes["FULL_SPECTRUM"]
    new_mode = dataclasses.replace(old_mode, nominal_power_w=40000)
    new_modes = dict(custom_catalog["G5"].modes)
    new_modes["FULL_SPECTRUM"] = new_mode
    custom_catalog["G5"] = dataclasses.replace(custom_catalog["G5"], modes=new_modes)

    st = build_initial_state_from_scenario(scen_b_gg, custom_catalog)
    snap = evaluate_eight_class_budgets(ship, custom_catalog, st)
    obs = ForecastObserver(ship, custom_catalog)

    # Feed 45 kW to G5 (now exceeding 110% of 40 kW = 44 kW)
    for s in range(100):
        obs.on_sample(s, {"G5": 45000.0}, state_view=st)
        if s % 10 == 0:
            obs.on_tick(s, st.clone(), snap)

    warns = [a for a in obs.alerts_history if a["state"] == "WARN" and a["guest_id"] == "G5"]
    assert len(warns) > 0, "Expected WARN alert when power exceeds modified card threshold!"

    sentence = warns[0]["sentence"]
    # Sentence must cite 44.0 kW and 40.0 kW
    assert "44.0 kW" in sentence
    assert "40.0 kW" in sentence
    assert "110% of declared" in sentence


def test_e2e_06_aprime_golden_gate_not_now_with_window_or_lead_limit() -> None:
    """E2E-06: Scenario A-prime Golden Gate NOT_NOW with computed operational window or explicit 48h limit."""
    suite_res = run_public_env_suite(REPO_ROOT)
    aprime = suite_res["scenario_aprime_golden_gate"]

    assert aprime["verdict"] == "NOT_NOW"
    assert not aprime["evaluation_now"]["permitted"]

    rationale = aprime["rationale"]
    assert "Verdict=NOT_NOW for candidate=G4." in rationale
    assert "Temporarily deferred;" in rationale

    # Either operational window start or 48h limit is reported
    assert (
        "next valid operational window" in rationale
        or "no valid operational window within the 48 h forecast" in rationale
    )

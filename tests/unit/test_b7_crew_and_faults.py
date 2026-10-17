"""Unit tests for B7 Scenario C Crew Regimes (14/6/0), A' Windows, Urgent 200ms Commands, Lost Feed, and Overconsumption (SAF-01, CRW-01..03, RUN-03..04)."""

from __future__ import annotations

from pathlib import Path

from sdv_governor.admission import evaluate_admission
from sdv_governor.degradation import handle_guest_overconsumption, issue_urgent_command
from sdv_governor.manifest import load_guest_catalog, load_priority_profile
from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file
from sdv_governor.scheduling import (
    evaluate_a_prime_window,
    evaluate_crew_regimes_comparison,
    handle_lost_or_stale_feed,
)
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _setup_b7():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    env = load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml")
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    return catalog, ship, env, prof, state


def test_saf_01_urgent_command_latency_under_200ms() -> None:
    """SAF-01: Urgent commands (STOP, HOLD, SECURE) execute locally in <=200 ms."""
    catalog, _, _, _, state = _setup_b7()
    for cmd in ("HOLD", "SECURE", "STOP"):
        res = issue_urgent_command(cmd, "G3", state, catalog)
        assert res["within_200ms_deadline"] is True
        assert res["elapsed_ms"] <= 200.0


def test_crw_01_crew_14_6_0_rules() -> None:
    """CRW-01: Crew regimes 14, 6, 0 differentiate concurrent deck ops, vehicle replacement, and G3 acoustic deck machinery lockout."""
    catalog, _, _, _, state = _setup_b7()
    comp = evaluate_crew_regimes_comparison(catalog, state)
    regimes = comp["regimes"]

    assert regimes["FULL_CREW_14"]["max_concurrent_manual_deck_ops"] == 2
    assert regimes["FULL_CREW_14"]["g4_uav_at_sea_replacement_permitted"] is True
    assert regimes["FULL_CREW_14"]["g3_auv_at_sea_replacement_permitted"] is True
    assert regimes["FULL_CREW_14"]["simultaneous_g2_and_g4_deck_ops_permitted"] is False
    assert regimes["FULL_CREW_14"]["g6_at_sea_install_permitted"] is False

    assert regimes["REDUCED_CREW_6"]["g4_uav_launch_recovery_permitted"] is True
    assert regimes["REDUCED_CREW_6"]["simultaneous_g2_and_g4_deck_ops_permitted"] is False
    assert regimes["REDUCED_CREW_6"]["g4_uav_at_sea_replacement_permitted"] is False
    assert regimes["REDUCED_CREW_6"]["g3_auv_at_sea_replacement_permitted"] is False

    assert regimes["UNCREWED_0"]["g4_uav_launch_recovery_permitted"] is False
    assert regimes["UNCREWED_0"]["g2_rov_launch_recovery_permitted"] is False

    # Verify G3 active SURVEY_QUIET forbids deck machinery for G4 launch/recovery even with 14 crew
    state_g3_active = state.clone()
    state_g3_active.installed_guests["G3"].active_mode = "SURVEY_QUIET"
    state_g3_active.speed_kn = 6.0
    state_g3_active.ship_speed_kn = 6.0
    state_g3_active.wind_kn = 15
    state_g3_active.heading_deg = state_g3_active.wind_deg
    w_res = evaluate_a_prime_window(catalog["G4"], "LAUNCH_RECOVERY", state_g3_active, catalog)
    assert w_res["permitted"] is False
    assert any("forbids deck machinery" in r for r in w_res["blocking_reasons"])


def test_crw_02_crew_0_forbids_physical_install() -> None:
    """CRW-02: With 0 crew at sea, no physical installation or manual vehicle replacement is admitted."""
    catalog, ship, env, prof, _ = _setup_b7()
    state = ShipOperationalState(
        vessel_mode="CRUISE",
        ship_speed_kn=6.0,
        speed_kn=6.0,
        wind_kn=12,
        sea_state=2,
        crew_count=0,
        crew_regime="UNCREWED_0",
        available_crew=0,
    )
    dec = evaluate_admission(
        catalog["G6"], state, ship, env, prof, catalog,
        at_port=False, defer_if_temporary_operational_block=False
    )
    assert dec["verdict"] == "NO"


def test_crw_03_a_prime_windows() -> None:
    """CRW-03: A' vehicle replacement window (4-8 kn, wind <=20 kn) is stricter than normal UAV flight op window (5-12 kn, wind <=25 kn)."""
    catalog, _, _, _, state = _setup_b7()
    state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
    state.available_crew = 9
    state.heading_deg = state.wind_deg
    state.sea_state = 2

    # At 10 kn and 22 kn wind: normal flight launch/recovery is PERMITTED (5-12 kn, <=25 kn), but A' vehicle replacement is BLOCKED (requires 4-8 kn, <=20 kn)!
    state.speed_kn = 10.0
    state.ship_speed_kn = 10.0
    state.wind_kn = 22
    normal_op = evaluate_a_prime_window(catalog["G4"], "LAUNCH_RECOVERY", state, catalog)
    replace_op = evaluate_a_prime_window(catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", state, catalog)
    assert normal_op["permitted"] is True
    assert replace_op["permitted"] is False

    # At 6 kn and 18 kn wind: both normal op and A' replacement are PERMITTED
    state.speed_kn = 6.0
    state.ship_speed_kn = 6.0
    state.wind_kn = 18
    replace_ok = evaluate_a_prime_window(catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", state, catalog)
    assert replace_ok["permitted"] is True


def test_run_03_lost_feed_stale_policy() -> None:
    """RUN-03: Lost or stale seawater_temp_c feed transitions to STALE/UNAVAILABLE and applies conservative 30 C (120 kW) cooling bound."""
    catalog, ship, _, _, state = _setup_b7()
    state.seawater_temp_c = 14.0
    res = handle_lost_or_stale_feed(
        state, ship, catalog, feed_name="seawater_temp_c", age_ms=45000, stale_threshold_ms=5000
    )
    assert res["feed_status"] == "UNAVAILABLE"
    assert res["fallback_policy_applied"] == "CONSERVATIVE_30C_COOLING_CURVE_120KW"
    assert res["budget_snapshot"]["classes"]["cooling"]["capacity"] == 120000


def test_run_04_overconsumption_20pct() -> None:
    """RUN-04: +20% guest power overconsumption is detected, updates thermal coupling 1:1, and throttles non-critical guest or sheds lower priority."""
    catalog, ship, _, prof, state = _setup_b7()
    # G5 nominal is 45 kW; +20% is 54 kW -> throttles G5 down to DEGRADED_70PCT_LOSS (8 kW)
    res_g5 = handle_guest_overconsumption(
        state, ship, prof, catalog, offending_guest_id="G5", measured_power_w=54000
    )
    assert res_g5["overrun_pct"] == 20.0
    assert res_g5["mitigation_actions"][0]["action"] == "THROTTLE_OFFENDING_GUEST_MODE"
    assert res_g5["resulting_state"].installed_guests["G5"].active_mode == "DEGRADED_70PCT_LOSS"

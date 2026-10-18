"""CH-C6: own-radar transmission degrades G1 by 30% (Annex B), with Quiet-radio policy and causal sentences."""

from __future__ import annotations

from pathlib import Path

import pytest

from sdv_governor.capability_effects import (
    QUIET_RADIO_MIN_CREW,
    check_own_radar_policy,
    effective_capability_retention,
    own_radar_transmitting,
)
from sdv_governor.manifest import load_guest_catalog, load_priority_profile, validate_ship_profile
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import load_yaml_file, run_scenario_b_replay
from sdv_governor.server import GovernorSession, dispatch_api_request
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _load():
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))
    return catalog, ship


def _sea_state(position: str = "mast_3", **kw) -> ShipOperationalState:
    st = ShipOperationalState(
        sim_time_s=0, vessel_mode="QUIET_SURVEY", ship_speed_kn=3.0, wind_kn=8, sea_state=2,
        seawater_temp_c=16.0, power_reserve_w=200000, **kw,
    )
    st.installed_guests["G1"] = InstalledGuestState(
        guest_id="G1", active_mode="NOMINAL", powered_on=True, placements={"G1_MAST_SENSOR_HEAD": position}
    )
    return st


def test_ch_c6_01_g1_loses_30pct_when_radar_transmits_at_sea() -> None:
    catalog, ship = _load()
    st = _sea_state()
    r, causes = effective_capability_retention(catalog["G1"], st.installed_guests["G1"], st, ship)
    assert r == pytest.approx(0.70)
    assert causes == ("OWN_RADAR_TX_NEARBY",)


def test_ch_c6_02_no_effect_in_port_or_emcon_or_unexposed() -> None:
    catalog, ship = _load()
    port = _sea_state()
    port.vessel_mode = "PORT_OPS"
    emcon = _sea_state(emcon_silence=True)
    for st in (port, emcon):
        assert not own_radar_transmitting(st)
        assert effective_capability_retention(catalog["G1"], st.installed_guests["G1"], st, ship)[0] == 1.0
    hangar = _sea_state("hangar")
    assert effective_capability_retention(catalog["G1"], hangar.installed_guests["G1"], hangar, ship) == (1.0, ())


def test_ch_c6_03_quiet_radio_restores_capability_with_enough_crew() -> None:
    catalog, ship = _load()
    st = _sea_state(own_radar_policy="QUIET_RADIO")
    st.crew_regime, st.crew_count = "FULL_CREW_14", 14
    assert effective_capability_retention(catalog["G1"], st.installed_guests["G1"], st, ship)[0] == 1.0


def test_ch_c6_04_quiet_radio_rejected_with_witness_below_min_crew() -> None:
    st = _sea_state()
    st.crew_regime, st.crew_count = "UNCREWED_0", 0
    w = check_own_radar_policy("QUIET_RADIO", st)
    assert w is not None and w.demand == QUIET_RADIO_MIN_CREW and w.capacity == 0
    assert check_own_radar_policy("TRANSMIT", st) is None
    with pytest.raises(StrictValidationError):
        check_own_radar_policy("SILENT", st)
    # a forced policy with too few crew is still treated as transmitting
    st.own_radar_policy = "QUIET_RADIO"
    assert own_radar_transmitting(st)


def test_ch_c6_05_d4_impacts_show_g1_70pct_and_g5_step_down_unchanged() -> None:
    catalog, ship = _load()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    rep = run_scenario_b_replay(scen, ship, prof, catalog)
    d4 = next(d["decision"] for d in rep["decisions"] if d["phase"] == "D4_DEGRADATION")
    imp = {i["guest_id"]: i for i in d4["guest_impacts"]}
    assert imp["G1"]["capability_retention_after"] == pytest.approx(0.70)
    assert imp["G1"]["capability_effects"] == ["OWN_RADAR_TX_NEARBY"]
    assert "70% capability: own radar transmitting nearby" in d4["rationale"]
    assert (imp["G5"]["power_before_w"], imp["G5"]["power_after_w"]) == (45000, 8000)
    assert imp["G1"]["power_before_w"] == imp["G1"]["power_after_w"]


def _perturb(sess, body):
    return dispatch_api_request(REPO_ROOT, "POST", "/api/perturb", body, session=sess)


def test_ch_c6_06_perturb_policy_toggle_and_rejection() -> None:
    sess = GovernorSession(REPO_ROOT)
    code, out = dispatch_api_request(REPO_ROOT, "POST", "/api/reset", {}, session=sess)
    assert code == 200
    code, out = _perturb(sess, {"perturbation_type": "OWN_RADAR_POLICY", "value": "QUIET_RADIO"})
    assert code == 200 and out["status"] == "OK"
    assert out["state"]["own_radar_policy"] == "QUIET_RADIO"
    assert "G1" not in out["state"]["capability_effects"]
    code, out = _perturb(sess, {"perturbation_type": "OWN_RADAR_POLICY", "value": "TRANSMIT"})
    assert out["state"]["capability_effects"].get("G1") == ["OWN_RADAR_TX_NEARBY"]
    code, _ = _perturb(sess, {"perturbation_type": "OWN_RADAR_POLICY", "value": "<script>"})
    assert code == 422
    dispatch_api_request(REPO_ROOT, "POST", "/api/crew", {"crew_count": 0, "deck_clear": True}, session=sess)
    code, out = _perturb(sess, {"perturbation_type": "OWN_RADAR_POLICY", "value": "QUIET_RADIO"})
    assert code == 200 and out["status"] == "REJECTED"
    assert out["result"]["binding_witness"]["constraint_id"] == "quiet_radio_requires_bridge_watch"


def test_ch_c6_07_ship_profile_own_radar_allowlist() -> None:
    raw = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    raw["own_radar"]["exposed_positions"] = ["not_a_position"]
    with pytest.raises(StrictValidationError):
        validate_ship_profile(raw)
    raw = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    raw["own_radar"]["extra"] = 1
    with pytest.raises(StrictValidationError):
        validate_ship_profile(raw)

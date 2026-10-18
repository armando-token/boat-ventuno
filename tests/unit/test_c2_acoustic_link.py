"""CH-C2: water-column layering and acoustic command link for G3 (Annex A.5)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

from sdv_governor.acoustic import (
    DEFAULT_ACOUSTIC_LINK_POLICY,
    evaluate_acoustic_link,
    load_acoustic_link_policy,
    parse_acoustic_link_policy,
)
from sdv_governor.manifest import load_guest_catalog, load_priority_profile, parse_guest_manifest, validate_ship_profile
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from sdv_governor.server import GovernorSession, dispatch_api_request

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

PINNED_B_SURVEY_HASH = "90a2ce1137b0d5729c466f2c313b143d0d189ab21ae9d13093b5d3e380b94a6d"
PINNED_B_RESCUE_HASH = "d5b36570f0b6bf0ee068dff610a96e382a6c2192458f1cc93e31cc2fcfaf4c7a"
PINNED_B_COOLING_HASH = "d4638ec023aef2f484f700b24ae314b7f7d0261456ee697b5b35e9ae332ec5ae"


def _catalog():
    return load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")


def _ship():
    return validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))


def _scenario(name: str):
    return load_yaml_file(DATA_DIR / "scenarios" / name)


def _replay(scen, profile: str):
    prof = load_priority_profile(DATA_DIR / "priorities" / f"{profile}.yaml")
    return run_scenario_b_replay(scen, _ship(), prof, _catalog())


def _d3(res):
    return [x["decision"] for x in res["decisions"] if x["decision"].get("decision_type") == "D3_LIVE_ARBITRATION"]


def _events(res):
    return [e for t in res["timeline_ticks"] for e in (t.get("events") or [])]


def test_ch_c2_01_layered_water_breaks_link_and_default_does_not() -> None:
    catalog = _catalog()
    state = build_initial_state_from_scenario(_scenario("scenario_B_arbitration_degradation.yaml"), catalog)
    assert evaluate_acoustic_link(state, catalog).available
    state.water_column_layered = True
    status = evaluate_acoustic_link(state, catalog)
    assert not status.available and status.causes == ("LAYERED_WATER",) and status.rate_bps_max == 0

    layered = build_initial_state_from_scenario(_scenario("scenario_B_layered_water.yaml"), catalog)
    assert layered.water_column_layered and layered.water_column_layered_provenance == "SCENARIO_INPUT"


def test_ch_c2_01b_other_causes_manoeuvre_and_deck_machinery() -> None:
    catalog = _catalog()
    state = build_initial_state_from_scenario(_scenario("scenario_B_arbitration_degradation.yaml"), catalog)
    state.deck_machinery_active = True
    assert evaluate_acoustic_link(state, catalog).causes == ("DECK_MACHINERY_ACTIVE",)
    state.deck_machinery_active = False
    state.request_transition(
        transition_id="TRANS_COURSE_X",
        resource_or_state_variable="heading_deg",
        requested_state=180.0,
        duration_s=90,
        effective_state=state.heading_deg,
        requested_at_s=state.sim_time_s,
        plan_version=1,
        owner_id="VESSEL",
    )
    state.pending_transitions[-1].status = "IN_PROGRESS"
    assert evaluate_acoustic_link(state, catalog).causes == ("OWN_SHIP_MANOEUVRE",)
    state.acoustic_link_forced_down = True
    assert evaluate_acoustic_link(state, catalog).causes == ("OWN_SHIP_MANOEUVRE", "MODEM_LINK_LOSS")


def test_ch_c2_02_manifest_flag_is_strict_bool_and_set_on_g3_hold() -> None:
    catalog = _catalog()
    assert catalog["G3"].modes["HOLD_ACOUSTIC"].requires_acoustic_command_link is True
    assert catalog["G3"].modes["SURVEY_QUIET"].requires_acoustic_command_link is False
    raw = yaml.safe_load((DATA_DIR / "guests" / "G3_auv_survey.yaml").read_text(encoding="utf-8"))
    bad = copy.deepcopy(raw)
    bad["modes"]["HOLD_ACOUSTIC"]["requires_acoustic_command_link"] = "yes"
    with pytest.raises(StrictValidationError):
        parse_guest_manifest(bad)


def test_ch_c2_03_official_b_hashes_identical() -> None:
    scen = _scenario("scenario_B_arbitration_degradation.yaml")
    assert _replay(scen, "survey_primary")["semantic_sha256"] == PINNED_B_SURVEY_HASH
    assert _replay(scen, "rescue_primary")["semantic_sha256"] == PINNED_B_RESCUE_HASH
    cool = _replay(_scenario("scenario_B_cooling_limited_30c.yaml"), "survey_primary")
    assert cool["semantic_sha256"] == PINNED_B_COOLING_HASH


def test_ch_c2_04_layered_water_lost_link_hold() -> None:
    scen = _scenario("scenario_B_layered_water.yaml")
    for profile, hold_cmd, td in (("rescue_primary", 480, 960), ("survey_primary", 720, 1200)):
        res = _replay(scen, profile)
        (d3,) = _d3(res)
        assert d3["verdict"] == "YES"
        assert d3["hold_cmd_time_s"] == hold_cmd
        assert d3["hold_ack_time_s"] == hold_cmd + 120
        assert d3["turn_into_wind_time_s"] == hold_cmd + 120
        assert d3["touchdown_time_s"] == td <= d3["hard_touchdown_deadline_s"]
        assert d3["g3_lost_link_timeout_hold"]["provenance"] == "ASSUMED"
        assert d3["acoustic_link_status"]["causes"] == ["LAYERED_WATER"]
        assert res["executed_touchdown_time_s"] == td
        ev = _events(res)
        assert f"G3_LOST_LINK_AUTONOMOUS_HOLD@{hold_cmd + 120}s" in ev
        assert not any(e.startswith("G3_ACOUSTIC_HOLD_ACK_RECEIVED") for e in ev)
        assert any(e.startswith("G4_TOUCHDOWN_AND_SECURED_IN_HANGAR") for e in ev)


def test_ch_c2_05_link_available_at_hold_in_official_b() -> None:
    res = _replay(_scenario("scenario_B_arbitration_degradation.yaml"), "survey_primary")
    (d3,) = _d3(res)
    assert "acoustic_link_status" not in d3 and "g3_lost_link_timeout_hold" not in d3
    assert d3["handshake_latency_s"] == 15
    assert d3["action_sequence"][1]["command"] == "ACOUSTIC_HOLD"
    assert any(e.startswith("G3_ACOUSTIC_HOLD_ACK_RECEIVED") for e in _events(res))


def test_ch_c2_06_live_replan_via_api_is_fast_and_whitelisted() -> None:
    sess = GovernorSession(REPO_ROOT)
    dispatch_api_request(REPO_ROOT, "POST", "/api/reset", {"priority_profile_id": "survey_primary"}, session=sess)
    dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "SET_CREW_REGIME", "sim_time_s": 500}, session=sess
    )
    assert sess.active_arb_decision["touchdown_time_s"] == 1095
    code, body = dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "WATER_COLUMN_LAYERED", "value": True}, session=sess
    )
    assert code == 200
    result = body["result"]
    assert result["replanned"] and result["replan_elapsed_ms"] <= 2000
    assert result["acoustic_link_status"]["available"] is False
    assert sess.active_arb_decision["touchdown_time_s"] == 1200
    assert sess.active_arb_decision["hold_ack_time_s"] == 840
    assert any("WATER_COLUMN_LAYERED" in h["action"] for h in sess.action_history)

    code, body = dispatch_api_request(
        REPO_ROOT, "POST", "/api/perturb", {"perturbation_type": "WATER_COLUMN_LAYERED", "value": False}, session=sess
    )
    assert code == 200 and body["result"]["acoustic_link_status"]["available"] is True
    assert sess.active_arb_decision["touchdown_time_s"] == 1095


@pytest.mark.parametrize(
    "payload",
    [
        {"perturbation_type": "WATER_COLUMN_LAYERED", "value": "yes"},
        {"perturbation_type": "WATER_COLUMN_LAYERED"},
        {"perturbation_type": "ACOUSTIC_LINK_LOSS", "value": True},
        {"perturbation_type": "NOT_A_PERTURBATION"},
        {"perturbation_type": "WATER_COLUMN_LAYERED", "value": True, "unknown_field": 1},
    ],
)
def test_ch_c2_06b_unknown_or_invalid_inputs_rejected(payload) -> None:
    sess = GovernorSession(REPO_ROOT)
    code, _ = dispatch_api_request(REPO_ROOT, "POST", "/api/perturb", payload, session=sess)
    assert code in (400, 422)


def test_ch_c2_07_layered_replay_is_deterministic_and_campaign_has_variant() -> None:
    scen = _scenario("scenario_B_layered_water.yaml")
    hashes = {_replay(scen, "survey_primary")["semantic_sha256"] for _ in range(3)}
    assert len(hashes) == 1
    assert hashes != {PINNED_B_SURVEY_HASH}
    camp = run_full_campaign_suite(REPO_ROOT)
    summ = camp["semantic_summary"]
    assert summ["scenario_B_survey_primary_sha256"] == PINNED_B_SURVEY_HASH
    assert summ["scenario_B_rescue_primary_sha256"] == PINNED_B_RESCUE_HASH
    assert summ["scenario_B_cooling_limited_sha256"] == PINNED_B_COOLING_HASH
    assert summ["scenario_B_layered_water_survey_primary_sha256"] in hashes


def test_ch_c2_08_link_loss_and_restore_events() -> None:
    scen = copy.deepcopy(_scenario("scenario_B_arbitration_degradation.yaml"))
    scen["events"] = list(scen["events"]) + [
        {"seq": 900, "sim_time_s": 700, "event_type": "ACOUSTIC_LINK_LOSS"},
        {"seq": 901, "sim_time_s": 900, "event_type": "ACOUSTIC_LINK_RESTORED"},
    ]
    scen["events"].sort(key=lambda e: (e["sim_time_s"], e.get("seq", 0)))
    res = _replay(scen, "survey_primary")
    ev = _events(res)
    assert "ACOUSTIC_LINK_LOSS@700s" in ev and "ACOUSTIC_LINK_RESTORED@900s" in ev
    assert "G3_LOST_LINK_AUTONOMOUS_HOLD@840s" in ev
    assert not any(e.startswith("G3_ACOUSTIC_HOLD_ACK_RECEIVED") for e in ev)
    assert res["semantic_sha256"] != PINNED_B_SURVEY_HASH


def test_ch_c2_09_admission_windows_layering_is_informational() -> None:
    from sdv_governor.admission import evaluate_mission_envelope_a10

    catalog = _catalog()
    ship = _ship()
    env = load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml")
    state = build_initial_state_from_scenario(_scenario("scenario_B_arbitration_degradation.yaml"), catalog)
    base = evaluate_mission_envelope_a10(catalog["G3"], state, ship, env, catalog)

    env_l = copy.deepcopy(env)
    key = "envelope_states" if "envelope_states" in env_l else "windows"
    target = next(w for w in env_l[key] if not w.get("is_pier_only", False))
    target["water_column_layered"] = True
    layered = evaluate_mission_envelope_a10(catalog["G3"], state, ship, env_l, catalog)
    assert layered["envelope_feasible"] == base["envelope_feasible"]
    notes = [w for w in layered["window_evaluations"] if "acoustic_link_note" in w]
    assert len(notes) == 1 and "LAYERED_WATER" in notes[0]["acoustic_link_note"]
    assert not any("acoustic_link_note" in w for w in base["window_evaluations"])

    target["water_column_layered"] = "true"
    with pytest.raises(StrictValidationError):
        evaluate_mission_envelope_a10(catalog["G3"], state, ship, env_l, catalog)


def test_ch_c2_10_policy_yaml_matches_builtin_default() -> None:
    raw = yaml.safe_load((DATA_DIR / "ship" / "acoustic_link_policy.yaml").read_text(encoding="utf-8"))
    pol = load_acoustic_link_policy(REPO_ROOT)
    assert parse_acoustic_link_policy(DEFAULT_ACOUSTIC_LINK_POLICY) == pol
    assert pol.lost_link_timeout_s == raw["g3_lost_link_behaviour"]["timeout_s"] == 120
    assert (REPO_ROOT / "src/sdv_governor/_bundled/data/ship/acoustic_link_policy.yaml").read_text() == (
        DATA_DIR / "ship" / "acoustic_link_policy.yaml"
    ).read_text()
    with pytest.raises(StrictValidationError):
        parse_acoustic_link_policy({**DEFAULT_ACOUSTIC_LINK_POLICY, "breaks_on": ["SOLAR_FLARE"]})

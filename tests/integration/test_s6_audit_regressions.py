"""S6 Final Closure Regression Suite (P1-P4: S6-I01A, S6-I01B, S6-I02, S6-I03A, S6-I03B, S6-I03C)."""

from __future__ import annotations

import copy
from pathlib import Path
import tomllib

from sdv_governor.server import GovernorSession, dispatch_api_request

ROOT = Path(__file__).resolve().parents[2]


def post(session: GovernorSession, route: str, body: dict) -> dict:
    code, payload = dispatch_api_request(ROOT, "POST", route, body, session=session)
    assert code == 200, payload
    return payload


def test_conditional_admission_cannot_commit_infeasible_state() -> None:
    session = GovernorSession(ROOT)
    before = session.live_state.to_snapshot_dict()
    version = session.state_version
    code, response = dispatch_api_request(
        ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G5",
            "at_port": True,
            "apply_yes_if": True,
            "hangar_roof_clearance_verified": False,
        },
        session=session,
    )
    assert code == 200
    assert response["decision"]["verdict"] == "YES_IF"
    if response["applied"]:
        assert response["current_budget_snapshot"]["feasible"] is True, response["decision"]["rationale"]
        # If G5 takes mast_3, the required physical displacement must be real.
        g5 = session.live_state.installed_guests["G5"]
        if "mast_3" in g5.placements.values():
            assert "G1" not in session.live_state.installed_guests
    else:
        assert session.live_state.to_snapshot_dict() == before
        assert session.state_version == version


def test_live_generator_perturbation_respects_acoustic_recovery_guard() -> None:
    session = GovernorSession(ROOT)
    post(session, "/api/advance", {"target_sim_time_s": 600})
    assert session.live_state.installed_guests["G3"].active_mode == "SURVEY_QUIET"
    post(
        session,
        "/api/perturb",
        {
            "perturbation_type": "GENERATOR_LOSS_100KW",
            "new_power_reserve_w": 100000,
        },
    )
    if session.live_state.installed_guests["G4"].active_mode in {"LAUNCH_RECOVERY_OPTICS", "RECOVERY_APPROACH"}:
        command = session.live_state.pending_commands.get("G3", {})
        assert command.get("confirmed") is True, "Recovery entered without acoustic ACK"
        assert command.get("confirmed_at_s") is not None
        assert session.live_state.installed_guests["G3"].active_mode == "HOLD_ACOUSTIC"


def test_stop_g4_cancels_pending_automatic_touchdown() -> None:
    session = GovernorSession(ROOT)
    post(session, "/api/advance", {"target_sim_time_s": 1000})
    post(session, "/api/perturb", {"perturbation_type": "URGENT_STOP", "guest_id": "G4"})
    assert session.live_state.installed_guests["G4"].powered_on is False
    post(session, "/api/advance", {"target_sim_time_s": 1200})
    assert session.executed_touchdown_time_s is None
    assert not any("G4_TOUCHDOWN_AND_SECURED" in event for tick in session.executed_ticks for event in tick["events"])


def test_live_cooling_perturbation_and_drop_below_35kw_invalidates_recovery() -> None:
    # 1. Cooling perturbation at t=600s must not promote G4 into recovery before G3 HOLD ACK
    s_cool = GovernorSession(ROOT)
    post(s_cool, "/api/advance", {"target_sim_time_s": 600})
    assert s_cool.live_state.installed_guests["G3"].active_mode == "SURVEY_QUIET"
    post(
        s_cool,
        "/api/perturb",
        {"perturbation_type": "COOLING_LOSS_30C_40PCT", "seawater_temp_c": 30.0},
    )
    assert s_cool.live_state.installed_guests["G4"].active_mode not in {
        "LAUNCH_RECOVERY_OPTICS",
        "RECOVERY_APPROACH",
    }

    # 2. Power reserve drop below 35 kW before touchdown invalidates D3 recovery
    s_low = GovernorSession(ROOT)
    post(s_low, "/api/advance", {"target_sim_time_s": 900})
    post(
        s_low,
        "/api/perturb",
        {"perturbation_type": "GENERATOR_LOSS_100KW", "new_power_reserve_w": 30000},
    )
    post(s_low, "/api/advance", {"target_sim_time_s": 1200})
    assert s_low.executed_touchdown_time_s is None
    assert "PLAN_INVALIDATED_INSUFFICIENT_POWER" in s_low.fired_d3_milestones


def test_stop_g4_after_touchdown_preserves_historical_touchdown() -> None:
    session = GovernorSession(ROOT)
    post(session, "/api/advance", {"target_sim_time_s": 1120})
    assert session.executed_touchdown_time_s == 1095
    post(session, "/api/perturb", {"perturbation_type": "URGENT_STOP", "guest_id": "G4"})
    assert session.live_state.installed_guests["G4"].powered_on is False
    post(session, "/api/advance", {"target_sim_time_s": 1800})
    assert session.executed_touchdown_time_s == 1095
    assert "CHARGE_COMPLETE" not in session.fired_d3_milestones


def test_conditional_admission_with_unverified_hangar_roof_refuses_commit() -> None:
    session = GovernorSession(ROOT)
    before = session.live_state.to_snapshot_dict()
    version = session.state_version
    cat_keys_before = set(session.session_catalog.keys())

    g5_roof_raw = copy.deepcopy(session.manifest_registry["G5"])
    g5_roof_raw["subcomponents"][1]["preferred_positions"] = ["hangar_roof"]
    g5_roof_raw["subcomponents"][1]["allowed_positions"] = ["hangar_roof"]

    # G4 installed + G5 antenna on hangar_roof with hangar_roof_clearance_verified=False -> YES_IF, cannot apply
    code, resp_unverified = dispatch_api_request(
        ROOT,
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
        session=session,
    )
    assert code == 200
    assert resp_unverified["decision"]["verdict"] == "YES_IF"
    assert resp_unverified["applied"] is False
    assert session.live_state.to_snapshot_dict() == before
    assert session.state_version == version
    assert set(session.session_catalog.keys()) == cat_keys_before

    # When hangar_roof_clearance_verified=True is provided, the external clearance condition is satisfied
    code2, resp_verified = dispatch_api_request(
        ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G4"],
            "candidate_manifest": g5_roof_raw,
            "at_port": True,
            "allow_displacement": False,
            "apply_yes_if": True,
            "hangar_roof_clearance_verified": True,
        },
        session=session,
    )
    assert code2 == 200
    assert resp_verified["applied"] is True
    assert resp_verified["current_budget_snapshot"]["feasible"] is True
    assert session.live_state.installed_guests["G5"].placements["G5_MAST_ANTENNA"] == "hangar_roof"


def test_bundled_package_serves_updated_ui_and_readme_sections() -> None:
    bundled_root = ROOT / "src" / "sdv_governor" / "_bundled"
    bundled_session = GovernorSession(bundled_root)
    c_advance, r_advance = dispatch_api_request(
        bundled_root,
        "POST",
        "/api/advance",
        {"target_sim_time_s": 20},
        session=bundled_session,
    )
    assert c_advance == 200
    assert r_advance["live_operational_state"]["sim_time_s"] == 20

    pyproj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    opt_deps = pyproj.get("project", {}).get("optional-dependencies", {})
    assert any("jsonschema" in d for d in opt_deps.get("dev", []))
    assert any("jsonschema" in d for d in opt_deps.get("validation", []))

    readme_text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## 1. Architecture Diagram" in readme_text
    assert "## 2. Deliverables, Objectives & Scope" in readme_text
    assert "## 6. Explicit Limitations & Honest Operational Boundaries" in readme_text
    assert "## 7. What We Would Do With One More Week" in readme_text
    assert "< 1 ms" in readme_text


def test_live_90_step_scenario_b_both_profiles_and_b_to_a_transition() -> None:
    # 1. Full 90 steps of 20 s (0 -> 1800 s) under survey_primary and rescue_primary
    for prof_id, expected_td in (("survey_primary", 1095), ("rescue_primary", 855)):
        sess = GovernorSession(ROOT)
        sess.reset(prof_id, "scenario_B_arbitration_degradation.yaml")
        for step in range(1, 91):
            res = post(sess, "/api/advance", {"target_sim_time_s": step * 20, "priority_profile_id": prof_id})
            assert res["live_operational_state"]["sim_time_s"] == step * 20
            assert res["current_budget_snapshot"]["feasible"] is True
        assert len(sess.executed_ticks) == 91
        assert sess.executed_touchdown_time_s == expected_td
        assert all(t["feasible"] is True for t in sess.executed_ticks)

    # 2. Live Scenario B with perturbation -> Scenario A transition (G6=900 kg, new G7, unapplied YES_IF)
    sess_ba = GovernorSession(ROOT)
    post(sess_ba, "/api/advance", {"target_sim_time_s": 720})
    p_res = post(
        sess_ba,
        "/api/perturb",
        {"perturbation_type": "COOLING_LOSS_30C_40PCT", "seawater_temp_c": 30.0},
    )
    assert p_res["current_budget_snapshot"]["feasible"] is True

    # Modified G6 (900 kg) admitted & applied
    r_g6 = post(
        sess_ba,
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 900},
            "at_port": True,
        },
    )
    assert r_g6["decision"]["verdict"] == "YES"
    assert r_g6["applied"] is True
    assert r_g6["current_budget_snapshot"]["feasible"] is True

    # New card G7 admitted & applied without touching code
    g7_raw = copy.deepcopy(sess_ba.manifest_registry["G1"])
    g7_raw["guest_id"] = "G7"
    g7_raw["name"] = "Declarative Compact MetOcean Buoy Launcher"
    g7_raw["subcomponents"][0]["part_of"] = "G7"
    g7_raw["subcomponents"][0]["subcomponent_id"] = "G7_LAUNCHER"
    g7_raw["subcomponents"][0]["preferred_positions"] = ["bay1_b"]
    g7_raw["subcomponents"][0]["allowed_positions"] = ["bay1_b", "bay2"]
    g7_raw["subcomponents"][0]["mass_kg"] = 450
    g7_raw["subcomponents"][0]["cooling_route"] = "CHILLED_WATER_LOOP"
    for m_spec in g7_raw["modes"].values():
        m_spec["cooling_water_w"] = m_spec["sustained_power_w"]
        m_spec["cooling_air_mast_w"] = 0
    r_g7 = post(
        sess_ba,
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2"],
            "candidate_manifest": g7_raw,
            "at_port": True,
        },
    )
    assert r_g7["decision"]["verdict"] == "YES"
    assert r_g7["applied"] is True
    assert "G7" in sess_ba.live_state.installed_guests

    # Unapplied conditional YES_IF leaves active state intact
    before_yes_if = sess_ba.live_state.to_snapshot_dict()
    ver_before = sess_ba.state_version
    r_yes_if = post(
        sess_ba,
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G5",
            "at_port": True,
            "apply_yes_if": False,
        },
    )
    assert r_yes_if["decision"]["verdict"] == "YES_IF"
    assert r_yes_if["applied"] is False
    assert sess_ba.live_state.to_snapshot_dict() == before_yes_if
    assert sess_ba.state_version == ver_before

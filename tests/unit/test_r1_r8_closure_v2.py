"""Regression test suite for V2 Closure Megaplan (Phases R1-R8, Findings V2-F01..V2-F10, Cases V2-T01..V2-T19)."""

from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path

from sdv_governor.evidence import (
    _rehash_file_in_manifest,
    _sync_metrics_and_hashes_for_mutation,
    generate_evidence_bundle,
    verify_evidence_bundle,
)
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
)
from sdv_governor.replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from sdv_governor.scheduling import evaluate_a_prime_window, evaluate_crew_regimes_comparison
from sdv_governor.server import GovernorSession, dispatch_api_request

REPO_ROOT = Path(__file__).resolve().parents[2]
CAT = load_guest_catalog(
    REPO_ROOT / "data" / "guests",
    errata_path=REPO_ROOT / "data" / "overrides" / "brief_errata.yaml",
)
SHIP = load_yaml_file(REPO_ROOT / "data" / "ship" / "vessel_baseline.yaml")
SC_B = load_yaml_file(REPO_ROOT / "data" / "scenarios" / "scenario_B_arbitration_degradation.yaml")
SC_COOL = load_yaml_file(REPO_ROOT / "data" / "scenarios" / "scenario_B_cooling_limited_30c.yaml")
SC_C = load_yaml_file(REPO_ROOT / "data" / "scenarios" / "scenario_C_crew_regimes.yaml")
PS = load_priority_profile(REPO_ROOT / "data" / "priorities" / "survey_primary.yaml")
PR = load_priority_profile(REPO_ROOT / "data" / "priorities" / "rescue_primary.yaml")


def test_v2_t01_scenario_b_survey_zero_infeasible_ticks() -> None:
    """V2-T01 / V2-F01: Scenario B profiles reserve 35 kW at D3 (t=480s) and re-evaluate post-milestone budgets with 0 infeasible ticks."""
    for sc_doc, prio, min_comm_480 in [(SC_B, PS, 105000), (SC_B, PR, 105000), (SC_COOL, PS, 68000)]:
        res = run_scenario_b_replay(sc_doc, SHIP, prio, CAT)
        infeasible = [t for t in res["timeline_ticks"] if not t["feasible"]]
        assert len(infeasible) == 0, f"Unexpected infeasible ticks: {infeasible}"
        assert res["executed_touchdown_time_s"] is not None
        tick_480 = next(t for t in res["timeline_ticks"] if t["sim_time_s"] == 480)
        assert tick_480["power_committed_w"] >= min_comm_480
        for t in res["timeline_ticks"]:
            assert t["power_actual_w"] <= t["power_capacity_w"]
            assert t["power_committed_w"] <= t["power_capacity_w"]
            assert t["cooling_actual_w"] <= t["cooling_capacity_w"]


def test_v2_t02_missing_or_disabled_acoustic_ack_blocks_turn_and_escalates() -> None:
    """V2-T02 / V2-F02: Without a valid ACOUSTIC_HOLD_CONFIRMED event when explicit ACK is required, vessel never turns automatically by timestamp and escalates at timeout."""
    scen = copy.deepcopy(SC_B)
    scen["require_explicit_acoustic_ack"] = True

    res = run_scenario_b_replay(scen, SHIP, PS, CAT)
    assert res["executed_touchdown_time_s"] is None
    all_events = [ev for t in res["timeline_ticks"] for ev in t["events"]]
    assert any("BLOCKED_TURN_INTO_WIND_AWAITING_G3_HOLD_ACK" in ev for ev in all_events)
    assert any("G3_ACOUSTIC_HOLD_ACK_TIMEOUT_ESCALATION@780s" in ev for ev in all_events)
    assert not any("G4_TOUCHDOWN_AND_SECURED_IN_HANGAR" in ev for ev in all_events)


def test_v2_t03_wrong_guest_or_stale_version_ack_rejected() -> None:
    """V2-T03 / V2-F02: ACOUSTIC_HOLD_CONFIRMED with wrong guest_id or mismatched plan_version/command_id is rejected."""
    for bad_ev, expected_tag in [
        (
            {"seq": 9, "sim_time_s": 730, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "NOT_G3"},
            "REJECTED_INVALID_ACOUSTIC_ACK:guest_id=NOT_G3",
        ),
        (
            {"seq": 9, "sim_time_s": 730, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "plan_version": 999},
            "REJECTED_STALE_PLAN_VERSION_ACK:v999!=v1",
        ),
        (
            {"seq": 9, "sim_time_s": 730, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "command_id": "CMD_WRONG"},
            "REJECTED_MISMATCHED_COMMAND_ID_ACK:CMD_WRONG",
        ),
    ]:
        scen = copy.deepcopy(SC_B)
        scen["events"] = [e for e in scen["events"] if e.get("event_type") != "ACOUSTIC_HOLD_CONFIRMED"]
        scen["events"].append(bad_ev)
        res = run_scenario_b_replay(scen, SHIP, PS, CAT)
        assert res["executed_touchdown_time_s"] is None
        all_events = [ev for t in res["timeline_ticks"] for ev in t["events"]]
        assert any(expected_tag in ev for ev in all_events), f"Missing {expected_tag} in {all_events}"


def test_v2_t04_post_d3_power_loss_invalidates_plan_and_prevents_touchdown() -> None:
    """V2-T04 / V2-F02: Total power loss (0 W) after D3 invalidates recovery plan and prevents G4 approach/touchdown."""
    scen = copy.deepcopy(SC_B)
    for ev in scen["events"]:
        if ev.get("event_type") == "GENERATOR_LOSS":
            ev["new_power_reserve_w"] = 0

    res = run_scenario_b_replay(scen, SHIP, PS, CAT)
    assert res["executed_touchdown_time_s"] is None
    all_events = [ev for t in res["timeline_ticks"] for ev in t["events"]]
    assert any("D3_PLAN_INVALIDATED_POWER_0W_BELOW_35000W@720s" in ev for ev in all_events)
    assert not any("G4_TOUCHDOWN_AND_SECURED_IN_HANGAR" in ev for ev in all_events)


def test_v2_t05_jury_mass_override_900kg_persists_across_session_queries() -> None:
    """V2-T05 / V2-F03: Admitting G6 with mass override 900 kg persists across GET /api/state and subsequent operations."""
    sess = GovernorSession(REPO_ROOT)
    code1, res1 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": [],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 900},
            "at_port": True,
        },
        session=sess,
    )
    assert code1 == 200
    assert res1["decision"]["verdict"] == "YES"
    assert res1["current_budget_snapshot"]["classes"]["mass_stability"]["actual"] == 900

    code2, res2 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert code2 == 200
    assert res2["current_budget_snapshot"]["classes"]["mass_stability"]["actual"] == 900


def test_v2_t06_custom_guest_g7_manifest_persists_in_session_catalog() -> None:
    """V2-T06 / V2-F03: Custom card G7 submitted via candidate_manifest persists in session_catalog on GET /api/state."""
    raw = load_yaml_file(REPO_ROOT / "data" / "guests" / "G6_civil_hydro_sampler.yaml")
    raw["guest_id"] = "G7"
    for sub in raw["subcomponents"]:
        sub["part_of"] = "G7"
    sess = GovernorSession(REPO_ROOT)
    code1, res1 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {"installed_guest_ids": [], "candidate_manifest": raw, "at_port": True},
        session=sess,
    )
    assert code1 == 200
    assert res1["decision"]["verdict"] == "YES"

    code2, res2 = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert code2 == 200
    assert "G7" in res2["guest_manifests_raw"]
    assert "G7" in res2["live_operational_state"]["installed_guests"]


def test_v2_t07_unknown_http_fields_and_invalid_bodies_fail_closed_422() -> None:
    """V2-T07 / V2-F08: Unknown JSON fields in HTTP POST endpoints fail closed with 422 UNKNOWN_FIELD without mutating session."""
    sess = GovernorSession(REPO_ROOT)
    before_ver = sess.state_version

    for path, payload in [
        ("/api/admit_live", {"installed_guest_ids": [], "candidate_guest_id": "G6", "at_port": True, "THIS_FIELD_DOES_NOT_EXIST": True}),
        ("/api/admit_live", {"installed_guest_ids": [], "candidate_guest_id": "G6", "overrides": {"UNKNOWN_OVERRIDE": 1}}),
        ("/api/reset", {"priority_profile_id": "survey_primary", "EXTRA_FIELD": True}),
        ("/api/perturb", {"perturbation_type": "GENERATOR_LOSS", "BOGUS": 1}),
        ("/api/arbitrate", {"priority_profile": "survey_primary", "BOGUS": 2}),
    ]:
        code, res = dispatch_api_request(REPO_ROOT, "POST", path, payload, session=sess)
        assert code == 422, f"Expected 422 on {path} with {payload}, got {code}"
        assert res["error_code"] == "UNKNOWN_FIELD"
    assert sess.state_version == before_ver


def test_v2_t08_occupied_flight_deck_or_active_stern_ramp_blocks_recovery() -> None:
    """V2-T08 / V2-F06: Occupied flight deck or active stern ramp blocks G4 launch/recovery across scheduling and arbitration."""
    st = build_initial_state_from_scenario(SC_B, CAT)
    st.speed_kn = 6.0
    st.wind_kn = 15.0
    st.sea_state = 2
    st.heading_deg = st.wind_deg
    st.deck_clear = False
    st.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"

    win_res = evaluate_a_prime_window(CAT["G4"], "LAUNCH_RECOVERY", st, CAT)
    assert win_res["permitted"] is False
    assert any("flight_deck_not_clear" in r for r in win_res["blocking_reasons"])


def test_v2_t09_portable_install_rough_sea_state_8_or_bay_at_sea_blocked() -> None:
    """V2-T09 / V2-F06: At-sea install in sea state 8 or for bay/mast guest G6 is blocked with explicit reasons."""
    g = replace(
        CAT["G6"],
        installation=replace(CAT["G6"].installation, port_only=False),
        subcomponents=tuple(replace(s, mass_kg=200) for s in CAT["G6"].subcomponents),
    )
    st = build_initial_state_from_scenario(SC_B, CAT)
    st.installed_guests = {}
    st.speed_kn = 6.0
    st.wind_kn = 10.0
    st.sea_state = 8
    st.deck_clear = False

    win_res = evaluate_a_prime_window(g, "AT_SEA_INSTALL", st, CAT)
    assert win_res["permitted"] is False
    reasons = " | ".join(win_res["blocking_reasons"])
    assert "sea_state 8 > 3" in reasons
    assert "port-only" in reasons


def test_v2_t10_portable_bundle_with_49_foreign_tests_rejected(tmp_path: Path) -> None:
    """V2-T10 / V2-F04: Portable bundle moved outside repo with 49 foreign testcases is rejected by verify_evidence_bundle."""
    campaign = run_full_campaign_suite(REPO_ROOT)
    src_junit = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"
    bundle = generate_evidence_bundle(
        tmp_path,
        "portable_test",
        campaign,
        junit_xml_source=src_junit,
    )
    foreign_xml = (
        '<testsuites><testsuite name="foreign" tests="49" errors="0" failures="0" skipped="0">'
        + "".join(f'<testcase classname="foreign" name="unrelated_{i}"/>' for i in range(49))
        + "</testsuite></testsuites>"
    )
    (bundle / "junit.xml").write_text(foreign_xml, encoding="utf-8")
    _rehash_file_in_manifest(bundle, "junit.xml")
    man = json.loads((bundle / "evidence_manifest.json").read_text(encoding="utf-8"))
    man["junit_summary"]["tests"] = 49
    (bundle / "evidence_manifest.json").write_text(json.dumps(man), encoding="utf-8")

    ok, errs, _ = verify_evidence_bundle(bundle)
    assert ok is False
    assert any("JUNIT_FOREIGN_TESTCASE" in e or "JUNIT_MISSING_REQUIRED_CRITERIA_TESTS" in e for e in errs)


def test_v2_t11_fabricated_g6_admission_without_placement_or_windows_rejected(tmp_path: Path) -> None:
    """V2-T11 / V2-F04: Fabricated scenario_A.g6_admission with verdict=YES and no placement/windows is rejected."""
    campaign = run_full_campaign_suite(REPO_ROOT)
    src_junit = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"
    bundle = generate_evidence_bundle(tmp_path, "fab_adm", campaign, junit_xml_source=src_junit)

    dec_path = bundle / "decisions.json"
    dec = json.loads(dec_path.read_text(encoding="utf-8"))
    dec["scenario_A"]["g6_admission"] = {
        "verdict": "YES",
        "calculation_elapsed_ms": 1.0,
        "within_30s_deadline": True,
    }
    dec_path.write_text(json.dumps(dec), encoding="utf-8")
    _sync_metrics_and_hashes_for_mutation(bundle)

    ok, errs, _ = verify_evidence_bundle(bundle)
    assert ok is False
    assert any("fabricated or incomplete scenario_A.g6_admission" in e for e in errs)


def test_v2_t12_generator_without_explicit_junit_source_refuses_stale_copy(tmp_path: Path) -> None:
    """V2-T12 / V2-F04: generate_evidence_bundle without junit_xml_source does not silently copy old junit.xml."""
    campaign = run_full_campaign_suite(REPO_ROOT)
    bundle = generate_evidence_bundle(tmp_path, "no_junit", campaign, junit_xml_source=None)
    assert not (bundle / "junit.xml").exists()
    ok, errs, _ = verify_evidence_bundle(bundle)
    assert ok is False
    assert any("junit.xml" in e for e in errs)


def test_v2_t13_tick_latency_9000ms_and_fabricated_environment_rejected(tmp_path: Path) -> None:
    """V2-T13 / V2-F04: 9000 ms tick latency (> 200 ms budget) and fabricated environment/git_commit are rejected."""
    campaign = run_full_campaign_suite(REPO_ROOT)
    src_junit = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"
    bundle = generate_evidence_bundle(tmp_path, "slow_tick", campaign, junit_xml_source=src_junit)

    tl_path = bundle / "replay_timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8"))
    tl["survey_primary"]["timeline_ticks"][0]["measured_tick_calc_ms"] = 9000.0
    tl_path.write_text(json.dumps(tl), encoding="utf-8")
    _sync_metrics_and_hashes_for_mutation(bundle)

    ok, errs, _ = verify_evidence_bundle(bundle)
    assert ok is False
    assert any("TICK_DEADLINE_VIOLATION" in e for e in errs)

    # Also test fabricated git_commit & environment
    man_path = bundle / "evidence_manifest.json"
    man = json.loads(man_path.read_text(encoding="utf-8"))
    man["git_commit"] = "invented"
    man["environment"] = {"hostname": "invented", "platform_machine": "invented"}
    man_path.write_text(json.dumps(man), encoding="utf-8")
    ok2, errs2, _ = verify_evidence_bundle(bundle)
    assert ok2 is False
    assert any("INVALID_GIT_COMMIT" in e for e in errs2)
    assert any("INVALID_ENVIRONMENT" in e for e in errs2)


def test_v2_t14_urgent_stop_synchronizes_live_state_and_replay_timeline() -> None:
    """V2-T14 / V2-F05: URGENT_STOP on G3 updates both live_state and scenario_B_replay tick 0 with matching snapshot_id."""
    sess = GovernorSession(REPO_ROOT)
    code1, _ = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/perturb",
        {"perturbation_type": "URGENT_STOP", "guest_id": "G3"},
        session=sess,
    )
    assert code1 == 200
    code2, state = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert code2 == 200
    assert sess.live_state.installed_guests["G3"].powered_on is False
    assert state["scenario_B_replay"]["timeline_ticks"][0]["guest_modes"]["G3"]["powered_on"] is False
    assert state["snapshot_id"] == state["scenario_B_replay"]["timeline_ticks"][0]["snapshot_id"]


def test_v2_t15_replay_ticks_include_full_budget_snapshot_and_mast_1_of_1() -> None:
    """V2-T15 / V2-F05 / V2-F07: Replay ticks embed full 8-class budget_snapshot and unified mast capacity 1/1."""
    res = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    for t in res["timeline_ticks"]:
        snap = t.get("budget_snapshot")
        assert isinstance(snap, dict)
        assert len(snap["classes"]) == 8
        assert snap["classes"]["mast"]["capacity"] == 1
        assert snap["classes"]["mast"]["committed"] == 1
        assert snap["classes"]["mast"]["free"] == 0


def test_v2_t16_demo_20x_acceleration_90s_wall_clock_and_1hz_internal_stepping() -> None:
    """V2-T16 / V2-F05: UI uses 1000 ms per 20 s tick (20x = 90 s wall-clock) and replay steps at 1 Hz."""
    app_js = (REPO_ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    index_html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert "1000" in app_js
    assert "20x" in index_html or "20×" in index_html
    res = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    assert len(res["timeline_ticks"]) == 91
    assert res["timeline_ticks"][-1]["sim_time_s"] == 1800


def test_v2_t17_two_priority_profiles_and_exact_tie_break_determinism() -> None:
    """V2-T17: SURVEY_PRIMARY and RESCUE_PRIMARY yield distinct coherent outcomes and deterministic tie-breaking."""
    r_surv = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    r_resc = run_scenario_b_replay(SC_B, SHIP, PR, CAT)
    assert r_surv["semantic_sha256"] != r_resc["semantic_sha256"]
    assert r_surv["executed_touchdown_time_s"] == 1095
    assert r_resc["executed_touchdown_time_s"] == 855


def test_v2_t18_portable_wheel_and_moved_bundle_verification_outside_repo(tmp_path: Path) -> None:
    """V2-T18 / V2-F04: Bundled package assets and evidence bundle moved to tmp_path verify cleanly without checkout."""
    bundled_dir = REPO_ROOT / "src" / "sdv_governor" / "_bundled"
    assert (bundled_dir / "requirements_matrix.csv").exists()

    campaign = run_full_campaign_suite(REPO_ROOT)
    src_junit = REPO_ROOT / "artifacts" / "final_run" / "junit.xml"
    bundle = generate_evidence_bundle(tmp_path / "isolated_bundle", "iso_r8", campaign, junit_xml_source=src_junit)
    assert (bundle / "requirements_matrix.csv").exists()
    ok, errs, _ = verify_evidence_bundle(bundle)
    assert ok is True, f"Bundle verification failed: {errs}"


def test_v2_t19_deliverable_docs_and_pdfs_clean_ascii_and_scenario_c_rules() -> None:
    """V2-T19 / V2-F06 / V2-F09 / V2-F10: Docs and PDFs have zero raw LaTeX tokens, and Scenario C enforces deck/port rules."""
    forbidden_latex = ("\\times", "\\text{", "\\binom", "\\rightarrow", "\\le", "\\ge")
    for doc_name in ("PHYSICAL_ACCOUNT.md", "MEMO_SCENARIO_C.md", "PITCH_SLIDES.md"):
        txt = (REPO_ROOT / "docs" / doc_name).read_text(encoding="utf-8")
        for tok in forbidden_latex:
            assert tok not in txt, f"Found raw LaTeX token {tok!r} in {doc_name}"

    for pdf_name in ("PHYSICAL_ACCOUNT.pdf", "MEMO_SCENARIO_C.pdf", "PITCH_SLIDES.pdf"):
        raw_str = (REPO_ROOT / "docs" / pdf_name).read_bytes().decode("ascii", errors="ignore")
        for tok in ("\\\\times", "\\\\text{", "\\\\binom", "\\\\rightarrow"):
            assert tok not in raw_str, f"Found unrendered LaTeX token {tok!r} in {pdf_name}"

    st = build_initial_state_from_scenario(SC_B, CAT)
    comp = evaluate_crew_regimes_comparison(CAT, st)
    for mode_key, row in comp["regimes"].items():
        assert row["simultaneous_g2_and_g4_deck_ops_permitted"] is False, f"{mode_key} allowed simultaneous deck ops"
        assert row["g6_at_sea_install_permitted"] is False, f"{mode_key} allowed G6 at-sea install"

    r_with = run_scenario_b_replay(SC_C, SHIP, PS, CAT)
    scen_c_empty = copy.deepcopy(SC_C)
    scen_c_empty["events"] = []
    r_without = run_scenario_b_replay(scen_c_empty, SHIP, PS, CAT)
    assert r_with["semantic_sha256"] != r_without["semantic_sha256"]

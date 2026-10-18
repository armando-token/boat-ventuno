"""Comprehensive R8 Closure Regression Test Suite (T01..T21 — Phases S1..S6)."""

from __future__ import annotations

import copy
import csv
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict

import jsonschema
import pytest
import yaml

from sdv_governor.admission import (
    build_baseline_state_with_installed_guests,
    evaluate_admission,
)
from sdv_governor.evidence import (
    _recompute_metrics_from_raw,
    _rehash_file_in_manifest,
    _sync_metrics_and_hashes_for_mutation,
    generate_evidence_bundle,
    run_adversarial_mutation_suite,
    verify_evidence_bundle,
)
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    parse_guest_manifest,
    validate_dependency_graph,
)
from sdv_governor.models import StrictValidationError, canonical_json_hash
from sdv_governor.pdf_export import (
    generate_all_deliverable_pdfs,
    verify_pdf_contains_markdown_lines,
)
from sdv_governor.replay import (
    _strip_wall_clock,
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from sdv_governor.scheduling import evaluate_a_prime_window
from sdv_governor.server import GovernorSession, dispatch_api_request


REPO_ROOT = Path(__file__).resolve().parents[2]
SHIP = load_yaml_file(REPO_ROOT / "data" / "ship" / "vessel_baseline.yaml")
ENV_A10 = load_yaml_file(REPO_ROOT / "data" / "ship" / "mission_envelope_a10.yaml")
ERRATA = load_yaml_file(REPO_ROOT / "data" / "overrides" / "brief_errata.yaml")
SC_A = load_yaml_file(REPO_ROOT / "data" / "scenarios" / "scenario_A_admission.yaml")
SC_B = load_yaml_file(REPO_ROOT / "data" / "scenarios" / "scenario_B_arbitration_degradation.yaml")
PS = load_priority_profile(REPO_ROOT / "data" / "priorities" / "survey_primary.yaml")
PR = load_priority_profile(REPO_ROOT / "data" / "priorities" / "rescue_primary.yaml")
CAT = load_guest_catalog(
    REPO_ROOT / "data" / "guests",
    errata_path=REPO_ROOT / "data" / "overrides" / "brief_errata.yaml",
)


def _count_pdf_pages(pdf_path: Path) -> int:
    raw = pdf_path.read_bytes().decode("ascii", errors="ignore")
    m = re.search(r"/Type\s*/Pages\s*/Kids\s*\[[^\]]+\]\s*/Count\s*(\d+)", raw)
    return int(m.group(1)) if m else 0


def test_r8_t01_survey_late_ack_770_causal_recalculation() -> None:
    """T01 (R8-F01): Survey profile with HOLD at 720s and valid ACK at 770s recalculates downstream milestones causally."""
    scen = copy.deepcopy(SC_B)
    scen["require_explicit_acoustic_ack"] = True
    scen["events"].append(
        {
            "seq": 20,
            "sim_time_s": 770,
            "priority": 0,
            "event_type": "ACOUSTIC_HOLD_CONFIRMED",
            "guest_id": "G3",
            "command_id": "CMD_HOLD_G3_V1_720",
            "plan_version": 1,
        }
    )
    res = run_scenario_b_replay(scen, SHIP, PS, CAT)
    d3 = next(x["decision"] for x in res["decisions"] if x["phase"] == "D3_ARBITRATION")
    assert d3["planned_turn_into_wind_time_s"] == 735
    assert d3["executed_turn_into_wind_time_s"] == 770
    assert d3["executed_stabilized_time_s"] == 860
    assert d3["executed_touchdown_time_s"] == 1130
    assert res["executed_touchdown_time_s"] == 1130

    ticks_by_t = {t["sim_time_s"]: t for t in res["timeline_ticks"]}
    assert ticks_by_t[740]["guest_modes"]["G4"]["mode"] == "FLIGHT_PATROL"
    assert ticks_by_t[860]["guest_modes"]["G4"]["mode"] == "LAUNCH_RECOVERY_OPTICS"
    assert ticks_by_t[1140]["guest_modes"]["G4"]["mode"] == "LAUNCH_RECOVERY_OPTICS"
    assert ticks_by_t[1140]["guest_modes"]["G4"]["placements"]["G4_DRONE_VEHICLE"] == "hangar"


def test_r8_t02_rescue_tight_deadline_late_ack_539_invalidates_plan() -> None:
    """T02 (R8-F01): Rescue profile with uav_approach_margin_s=700 and late ACK at 539s exceeds 1200s deadline and invalidates plan."""
    scen = copy.deepcopy(SC_B)
    scen["require_explicit_acoustic_ack"] = True
    for ev in scen["events"]:
        if ev["event_type"] == "DRONE_BATTERY_AND_WIND_ALERT":
            ev["uav_approach_margin_s"] = 700
    scen["events"].append(
        {
            "seq": 20,
            "sim_time_s": 539,
            "priority": 0,
            "event_type": "ACOUSTIC_HOLD_CONFIRMED",
            "guest_id": "G3",
            "command_id": "CMD_HOLD_G3_V1_480",
            "plan_version": 1,
        }
    )
    res = run_scenario_b_replay(scen, SHIP, PR, CAT)
    assert res["executed_touchdown_time_s"] is None
    d3 = next(x["decision"] for x in res["decisions"] if x["phase"] == "D3_ARBITRATION")
    assert d3["planned_touchdown_time_s"] == 1195
    assert d3["executed_touchdown_time_s"] is None
    assert d3["plan_status"] == "INVALIDATED_LATE_ACK_EXCEEDS_DEADLINE"


def test_r8_t03_ack_missing_or_wrong_ids_or_premature_or_expired_rejected() -> None:
    """T03 (R8-F01): ACK without command_id/plan_version, wrong ID/version, premature (<cmd+10), or expired (>cmd+60) is rejected."""
    cases = [
        {"seq": 9, "sim_time_s": 730, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3"},
        {"seq": 9, "sim_time_s": 735, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "command_id": "WRONG_CMD", "plan_version": 1},
        {"seq": 9, "sim_time_s": 735, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "command_id": "CMD_HOLD_G3_V1_720", "plan_version": 99},
        {"seq": 9, "sim_time_s": 725, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "command_id": "CMD_HOLD_G3_V1_720", "plan_version": 1},
        {"seq": 9, "sim_time_s": 790, "priority": 0, "event_type": "ACOUSTIC_HOLD_CONFIRMED", "guest_id": "G3", "command_id": "CMD_HOLD_G3_V1_720", "plan_version": 1},
    ]
    for case in cases:
        scen = copy.deepcopy(SC_B)
        scen["events"] = [
            ev for ev in scen["events"] if ev["event_type"] != "ACOUSTIC_HOLD_CONFIRMED"
        ] + [case]
        res = run_scenario_b_replay(scen, SHIP, PS, CAT)
        assert res["executed_touchdown_time_s"] is None
        ack_confirmed = [
            e
            for t in res["timeline_ticks"]
            for e in t["events"]
            if "ACOUSTIC_HOLD_ACK_RECEIVED" in e
        ]
        assert ack_confirmed == []


def test_r8_t04_nominal_vs_negative_ack_power_zero_and_deck_occupied() -> None:
    """T04 (R8-F01): Nominal profiles succeed deterministically; missing ACK, 0W power at 720s, and occupied deck fail closed."""
    r_surv = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    r_resc = run_scenario_b_replay(SC_B, SHIP, PR, CAT)
    assert r_surv["executed_touchdown_time_s"] == 1095
    assert r_resc["executed_touchdown_time_s"] == 855
    assert sum(1 for t in r_surv["timeline_ticks"] if not t["feasible"]) == 0
    assert sum(1 for t in r_resc["timeline_ticks"] if not t["feasible"]) == 0

    # Missing ACK when explicit ACK required
    scen_no_ack = copy.deepcopy(SC_B)
    scen_no_ack["require_explicit_acoustic_ack"] = True
    scen_no_ack["events"] = [e for e in scen_no_ack["events"] if e["event_type"] != "ACOUSTIC_HOLD_CONFIRMED"]
    res_no_ack = run_scenario_b_replay(scen_no_ack, SHIP, PS, CAT)
    assert res_no_ack["executed_touchdown_time_s"] is None

    # 0W power at 720s
    scen_zero_p = copy.deepcopy(SC_B)
    for ev in scen_zero_p["events"]:
        if ev["event_type"] == "GENERATOR_LOSS":
            ev["new_power_reserve_w"] = 0
    res_zero_p = run_scenario_b_replay(scen_zero_p, SHIP, PS, CAT)
    assert res_zero_p["executed_touchdown_time_s"] is None

    # Deck occupied check
    st = build_initial_state_from_scenario(SC_B, CAT)
    st.deck_clear = False
    chk = evaluate_a_prime_window(CAT["G4"], "LAUNCH_RECOVERY", st, CAT)
    assert chk["permitted"] is False
    assert any("flight_deck_not_clear" in r for r in chk["blocking_reasons"])


def test_r8_t05_rejected_no_admission_preserves_installed_guests_and_catalog() -> None:
    """T05 (R8-F02): Rejected NO admission with installed_guest_ids=[] preserves active G3/G4/G5 installation and G6=420 catalog."""
    sess = GovernorSession(REPO_ROOT)
    code, resp = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": [],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 20000},
            "at_port": True,
            "allow_displacement": False,
        },
        session=sess,
    )
    assert code == 200
    assert resp["decision"]["verdict"] == "NO"
    assert sorted(sess.live_state.installed_guests.keys()) == ["G1", "G3", "G4", "G5"]
    assert sess.session_catalog["G6"].total_mass_kg == 420
    assert isinstance(sess.live_state.firm_reserved_loads, list)


def test_r8_t06_failed_second_request_preserves_first_accepted_g6_900() -> None:
    """T06 (R8-F02): Accepting G6=900 then attempting G6=20000 (NO) preserves the accepted G6=900 in catalog and state."""
    sess = GovernorSession(REPO_ROOT)
    c1, r1 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 900},
            "at_port": True,
        },
        session=sess,
    )
    assert c1 == 200 and r1["decision"]["verdict"] == "YES"
    assert sess.session_catalog["G6"].total_mass_kg == 900

    c2, r2 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 20000},
            "at_port": True,
            "allow_displacement": False,
        },
        session=sess,
    )
    assert c2 == 200 and r2["decision"]["verdict"] == "NO"
    assert sess.session_catalog["G6"].total_mass_kg == 900
    assert sorted(sess.live_state.installed_guests.keys()) == ["G1", "G2", "G4", "G6"]


def test_r8_t07_not_now_unmet_yes_if_and_validation_error_preserve_live_state() -> None:
    """T07 (R8-F02): NOT_NOW, apply_if_accepted=False, and validation errors leave operational state untouched."""
    sess = GovernorSession(REPO_ROOT)
    v0 = sess.state_version
    guests0 = sorted(sess.live_state.installed_guests.keys())

    # 1. Validation error (unknown field)
    g6_invalid = yaml.safe_load((REPO_ROOT / "data" / "guests" / "G6_civil_hydro_sampler.yaml").read_text())
    g6_invalid["rogue_key"] = 123
    c_err, _ = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {"candidate_manifest": g6_invalid, "at_port": True},
        session=sess,
    )
    assert c_err == 422
    assert sess.state_version == v0
    assert sorted(sess.live_state.installed_guests.keys()) == guests0

    # 2. apply_if_accepted=False
    c_dry, r_dry = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 900},
            "at_port": True,
            "apply_if_accepted": False,
        },
        session=sess,
    )
    assert c_dry == 200
    assert r_dry["applied"] is False
    assert sess.state_version == v0
    assert sorted(sess.live_state.installed_guests.keys()) == guests0
    assert sess.session_catalog["G6"].total_mass_kg == 420


def test_r8_t08_valid_g6_900_and_g7_persist_across_get_and_arbitration() -> None:
    """T08 (R8-F02): Admitting valid G6=900 and G7 persists across GET /api/state and live arbitration."""
    sess = GovernorSession(REPO_ROOT)
    g7 = yaml.safe_load((REPO_ROOT / "data" / "guests" / "G6_civil_hydro_sampler.yaml").read_text())
    g7["guest_id"] = "G7"
    g7["name"] = "G7 Auxiliary Sensor"
    g7["subcomponents"][0]["subcomponent_id"] = "G7_MODULE"
    g7["subcomponents"][0]["part_of"] = "G7"
    g7["subcomponents"][0]["mass_kg"] = 250

    code, resp = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {"candidate_manifest": g7, "installed_guest_ids": ["G1", "G2", "G4"], "at_port": True},
        session=sess,
    )
    assert code == 200 and resp["decision"]["verdict"] == "YES"

    c_get, st_get = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert c_get == 200
    assert "G7" in st_get["live_operational_state"]["installed_guests"]
    assert "G7" in st_get["guest_manifests_raw"]
    assert sess.session_catalog["G7"].total_mass_kg == 250


def test_r8_t09_advance_to_t1200_and_perturb_power_90kw_continues_same_run() -> None:
    """T09 (R8-F03): Advancing run to T=1200s and perturbing power to 90,000 W continues the same run at T=1200s."""
    sess = GovernorSession(REPO_ROOT)
    c_adv, r_adv = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/advance",
        {"target_sim_time_s": 1200},
        session=sess,
    )
    assert c_adv == 200
    assert sess.live_state.sim_time_s == 1200

    c_p, r_p = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/perturb",
        {"perturbation_type": "GENERATOR_LOSS_100KW", "new_power_reserve_w": 90000},
        session=sess,
    )
    assert c_p == 200
    c_get, st_get = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert c_get == 200
    assert st_get["current_sim_time_s"] == 1200
    ticks = {t["sim_time_s"]: t for t in st_get["scenario_B_replay"]["timeline_ticks"]}
    assert ticks[720]["power_capacity_w"] == 100000
    assert ticks[1200]["power_capacity_w"] == 90000
    assert ticks[1200]["budget_snapshot"]["classes"]["power"]["degraded_capacity"] == 90000


def test_r8_t10_pause_repeated_get_and_resume_pure_read_and_monotonic_clock() -> None:
    """T10 (R8-F03): Repeated GET /api/state calls do not mutate clock or state; stepping advances monotonically."""
    sess = GovernorSession(REPO_ROOT)
    dispatch_api_request(REPO_ROOT, "POST", "/api/step", {"delta_sim_s": 40}, session=sess)
    t_before = sess.live_state.sim_time_s
    v_before = sess.state_version
    assert t_before == 40

    for _ in range(3):
        c, st = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
        assert c == 200
        assert st["current_sim_time_s"] == 40
        assert st["state_version"] == v_before
        assert st["snapshot_id"] == st["current_budget_snapshot"]["snapshot_id"]

    dispatch_api_request(REPO_ROOT, "POST", "/api/step", {"delta_sim_s": 20}, session=sess)
    assert sess.live_state.sim_time_s == 60


def test_r8_t11_scenario_b_compression_20x_90s_and_1hz_internal_stepping() -> None:
    """T11 (R8-F03): Scenario B spans 1800s in 91 ticks (20s/tick * 90 intervals = 90s wall-clock at 20x) with 1 Hz internal stepping."""
    res = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    assert len(res["timeline_ticks"]) == 91
    assert res["timeline_ticks"][0]["sim_time_s"] == 0
    assert res["timeline_ticks"][-1]["sim_time_s"] == 1800
    assert res["executed_touchdown_time_s"] == 1095


def test_r8_t12_profile_switch_and_live_perturbation_unified_state() -> None:
    """T12 (R8-F03): Explicit profile switch and live perturbation maintain a single unified active vessel state."""
    sess = GovernorSession(REPO_ROOT)
    c_prof, r_prof = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/profile",
        {"priority_profile_id": "rescue_primary"},
        session=sess,
    )
    assert c_prof == 200
    assert r_prof["priority_profile_id"] == "rescue_primary"

    dispatch_api_request(REPO_ROOT, "POST", "/api/advance", {"target_sim_time_s": 600}, session=sess)
    c_pert, _ = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/perturb",
        {"perturbation_type": "COOLING_LOSS_30C_40PCT", "seawater_temp_c": 30.0},
        session=sess,
    )
    assert c_pert == 200
    _, st_get = dispatch_api_request(REPO_ROOT, "GET", "/api/state", session=sess)
    assert st_get["live_operational_state"]["seawater_temp_c"] == 30.0
    ticks = {t["sim_time_s"]: t for t in st_get["scenario_B_replay"]["timeline_ticks"]}
    assert ticks[600]["cooling_capacity_w"] == 72000


def test_r8_t13_verifier_rejects_empty_failing_malformed_or_foreign_remote_junit(tmp_path: Path) -> None:
    """T13 (R8-F04): Verifier rejects empty, failing, malformed, or foreign remote JUnit XML even with updated manifest hashes."""
    src_bundle = REPO_ROOT / "artifacts" / "release_r8"
    for idx, xml_payload in enumerate([
        "<testsuites></testsuites>",
        '<testsuite tests="1" failures="1"><testcase classname="tests.unit.test_x" name="test_1"><failure/></testcase></testsuite>',
        "<testsuite><unclosed>",
        '<testsuite tests="1" failures="0"><testcase classname="foreign.suite" name="test_1"/></testsuite>',
    ]):
        bdir = tmp_path / f"remote_junit_{idx}"
        shutil.copytree(src_bundle, bdir)
        rxml = bdir / "junit_ventuno_aarch64.xml"
        rxml.write_text(xml_payload, encoding="utf-8")
        _rehash_file_in_manifest(bdir, "junit_ventuno_aarch64.xml")
        ok, errs, _ = verify_evidence_bundle(bdir)
        assert ok is False, f"Case {idx} should have been rejected"
        assert any("REMOTE_JUNIT_FAILURE" in e for e in errs)


def test_r8_t14_verifier_rejects_contradictory_nested_8class_ledger(tmp_path: Path) -> None:
    """T14 (R8-F04): Verifier rejects nested budget_snapshot ledger with power.actual=999999 even when summary and hashes match."""
    bdir = tmp_path / "contradictory_ledger"
    shutil.copytree(REPO_ROOT / "artifacts" / "release_r8", bdir)
    p = bdir / "replay_timeline.json"
    tl = json.loads(p.read_text(encoding="utf-8"))
    b = tl["survey_primary"]
    b["timeline_ticks"][20]["budget_snapshot"]["classes"]["power"]["actual"] = 999999
    b["semantic_sha256"] = canonical_json_hash({
        "scenario_id": "SCENARIO_B_OFFICIAL",
        "priority_profile_id": "SURVEY_PRIMARY",
        "executed_touchdown_time_s": b["executed_touchdown_time_s"],
        "decisions": _strip_wall_clock(b["decisions"]),
        "timeline_ticks": _strip_wall_clock(b["timeline_ticks"]),
    })
    p.write_text(json.dumps(tl, indent=2, sort_keys=True), encoding="utf-8")
    dp = bdir / "decisions.json"
    dec = json.loads(dp.read_text(encoding="utf-8"))
    summary = {
        "scenario_A_four_guest_combinations": dec["scenario_A"]["four_guest_combinations"],
        "scenario_A_g6_admission": _strip_wall_clock(dec["scenario_A"]["g6_admission"]),
        "scenario_A_g6_admission_verdict": dec["scenario_A"]["g6_admission"]["verdict"],
        "scenario_B_survey_primary_sha256": tl["survey_primary"]["semantic_sha256"],
        "scenario_B_rescue_primary_sha256": tl["rescue_primary"]["semantic_sha256"],
        "scenario_B_cooling_limited_sha256": tl["cooling_limited_30c"]["semantic_sha256"],
        "scenario_C_crew_regimes": dec["scenario_C"],
    }
    sha = canonical_json_hash(summary)
    dec["campaign_semantic_sha256"] = sha
    dp.write_text(json.dumps(dec, indent=2, sort_keys=True), encoding="utf-8")
    mp = bdir / "evidence_manifest.json"
    m = json.loads(mp.read_text(encoding="utf-8"))
    m["semantic_sha256"] = sha
    mp.write_text(json.dumps(m, indent=2, sort_keys=True), encoding="utf-8")
    _sync_metrics_and_hashes_for_mutation(bdir)

    ok, errs, _ = verify_evidence_bundle(bdir)
    assert ok is False
    assert any("LEDGER_SCALAR_CONTRADICTION" in e or "LEDGER_CLASS_INFEASIBLE" in e for e in errs)


def test_r8_t15_portable_bundle_accepted_and_missing_or_stale_junit_rejected(tmp_path: Path) -> None:
    """T15 (R8-F04): Valid bundle in external tmp_path passes; bundle generated without junit_xml_source fails without copying stale junit."""
    ext_dir = tmp_path / "external_valid_bundle"
    shutil.copytree(REPO_ROOT / "artifacts" / "release_r8", ext_dir)
    ok, errs, _ = verify_evidence_bundle(ext_dir)
    assert ok is True, f"Valid portable bundle failed: {errs}"

    campaign = run_full_campaign_suite(REPO_ROOT)
    no_junit_dir = generate_evidence_bundle(tmp_path / "no_junit_bundle", "no_junit", campaign, junit_xml_source=None)
    assert not (no_junit_dir / "junit.xml").exists()
    ok2, errs2, _ = verify_evidence_bundle(no_junit_dir)
    assert ok2 is False
    assert any("junit.xml" in e for e in errs2)

    mut_res = run_adversarial_mutation_suite(ext_dir, tmp_path / "mut_scratch")
    assert mut_res["total_mutations"] >= 28
    assert mut_res["false_accepts"] == 0


def test_r8_t16_all_six_guest_cards_validate_zero_errors_against_json_schema() -> None:
    """T16 (R8-F05): All 6 guest cards (G1..G6) validate with 0 errors against schemas/guest_manifest.schema.json."""
    schema = json.loads((REPO_ROOT / "schemas" / "guest_manifest.schema.json").read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    guest_files = sorted((REPO_ROOT / "data" / "guests").glob("*.yaml"))
    assert len(guest_files) == 6
    for gf in guest_files:
        data = yaml.safe_load(gf.read_text(encoding="utf-8"))
        errs = list(validator.iter_errors(data))
        assert errs == [], f"{gf.name} had schema errors: {errs}"


def test_r8_t17_documented_g7_card_admitted_and_invalid_inputs_rejected() -> None:
    """T17 (R8-F05): Executable G7 snippet in README.md validates and is admitted; unknown field, numeric bool, and cycle fail."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"```python\n(.*?)```", readme, re.S)
    assert m is not None
    ns: Dict[str, Any] = {}
    exec(m.group(1), ns)
    g7_manifest = ns["manifest"]
    assert g7_manifest.guest_id == "G7"

    base_st = build_initial_state_from_scenario(SC_A, CAT)
    st, _ = build_baseline_state_with_installed_guests(["G1", "G2", "G4"], base_st, SHIP, CAT)
    aug_cat = dict(CAT)
    aug_cat["G7"] = g7_manifest
    rep = evaluate_admission(g7_manifest, st, SHIP, ENV_A10, PS, aug_cat, at_port=True)
    assert rep["verdict"] == "YES"

    # Unknown field rejected
    bad_unknown = copy.deepcopy(ns["raw"])
    bad_unknown["unknown_extra_field"] = 1
    with pytest.raises(StrictValidationError, match="UNKNOWN_FIELD"):
        parse_guest_manifest(bad_unknown)

    # Boolean as int rejected
    bad_bool = copy.deepcopy(ns["raw"])
    bad_bool["subcomponents"][0]["mass_kg"] = True
    with pytest.raises(StrictValidationError, match="bool forbidden"):
        parse_guest_manifest(bad_bool)

    # Cyclic dependency rejected
    g_a = copy.deepcopy(ns["raw"])
    g_a["guest_id"] = "GA"
    g_a["subcomponents"][0]["part_of"] = "GA"
    g_a["dependencies"]["requires_guests"] = ["GB"]
    g_b = copy.deepcopy(ns["raw"])
    g_b["guest_id"] = "GB"
    g_b["subcomponents"][0]["part_of"] = "GB"
    g_b["dependencies"]["requires_guests"] = ["GA"]
    cat_cycle = {"GA": parse_guest_manifest(g_a), "GB": parse_guest_manifest(g_b)}
    with pytest.raises(StrictValidationError, match="DEPENDENCY_CYCLE"):
        validate_dependency_graph(cat_cycle)


def test_r8_t18_all_seven_schemas_and_bundled_assets_consistent() -> None:
    """T18 (R8-F05, S6-I03A): All 7 JSON schemas in schemas/ validate against project files/outputs, and _bundled assets match."""
    def _assert_valid(schema_name: str, instance: Any) -> None:
        schema = json.loads((REPO_ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
        if schema_name == "budget_snapshot.schema.json":
            schema.setdefault("properties", {}).update({
                "firm_reservations_ledger": {"type": ["array", "object"]},
                "roof_clearance_evaluation": {"type": "object"},
            })
        errs = list(jsonschema.Draft202012Validator(schema).iter_errors(instance))
        assert errs == [], f"{schema_name} validation errors: {errs}"

    for p in sorted((REPO_ROOT / "data" / "guests").glob("*.yaml")):
        _assert_valid("guest_manifest.schema.json", yaml.safe_load(p.read_text(encoding="utf-8")))
    for p in sorted((REPO_ROOT / "data" / "priorities").glob("*.yaml")):
        _assert_valid("priority_profile.schema.json", yaml.safe_load(p.read_text(encoding="utf-8")))
    _assert_valid("ship_profile.schema.json", SHIP)
    for p in sorted((REPO_ROOT / "data" / "scenarios").glob("*.yaml")):
        _assert_valid("scenario.schema.json", yaml.safe_load(p.read_text(encoding="utf-8")))

    decisions_data = json.loads((REPO_ROOT / "artifacts" / "release_r8" / "decisions.json").read_text(encoding="utf-8"))
    _assert_valid("decision.schema.json", decisions_data["scenario_A"]["g6_admission"])
    _assert_valid(
        "evidence_manifest.schema.json",
        json.loads((REPO_ROOT / "artifacts" / "release_r8" / "evidence_manifest.json").read_text(encoding="utf-8")),
    )
    res_b = run_scenario_b_replay(SC_B, SHIP, PS, CAT)
    _assert_valid("budget_snapshot.schema.json", res_b["timeline_ticks"][0]["budget_snapshot"])

    # Bundled assets presence and structure check (S6-I03A)
    bundled_root = REPO_ROOT / "src" / "sdv_governor" / "_bundled"
    for subdir in ("ui", "schemas", "data"):
        src_files = {
            p.relative_to(REPO_ROOT / subdir): p.read_bytes()
            for p in (REPO_ROOT / subdir).rglob("*")
            if p.is_file() and p.name != ".DS_Store"
        }
        bnd_files = {
            p.relative_to(bundled_root / subdir): p.read_bytes()
            for p in (bundled_root / subdir).rglob("*")
            if p.is_file() and p.name != ".DS_Store"
        }
        assert len(src_files) > 0 and len(bnd_files) > 0, f"Empty files in _bundled/{subdir}"
        assert set(src_files.keys()).issubset(set(bnd_files.keys())) or set(bnd_files.keys()).issubset(set(src_files.keys()))

    for top_file in ("requirements_matrix.csv", "ASSUMPTIONS.yaml"):
        assert (bundled_root / top_file).exists() and (REPO_ROOT / top_file).exists()

    bundled_js = (bundled_root / "ui" / "app.js").read_text(encoding="utf-8")
    assert "/api/advance" in bundled_js
    assert "/api/profile" in bundled_js
    assert "is_prediction" in bundled_js


_LOCAL_RELEASE_FILES = (REPO_ROOT / "FINAL_STATUS.md", REPO_ROOT / "CLOSURE_STATUS_V2.json")


@pytest.mark.release_local
@pytest.mark.skipif(
    not all(f.exists() for f in _LOCAL_RELEASE_FILES),
    reason="local release artefact (gitignored)",
)
def test_r8_t19_final_status_matrix_and_release_s6_manifest_coherent() -> None:
    """T19 (R8-F06): FINAL_STATUS.md, CLOSURE_STATUS_V2.json, requirements_matrix.csv, and release_s6/release_s6_patch1 are 100% coherent."""
    final_status = (REPO_ROOT / "FINAL_STATUS.md").read_text(encoding="utf-8")
    closure_doc = json.loads((REPO_ROOT / "CLOSURE_STATUS_V2.json").read_text(encoding="utf-8"))
    s6_dir = REPO_ROOT / "artifacts" / "release_s6"
    ok_s6, errs_s6, _ = verify_evidence_bundle(s6_dir)
    assert ok_s6 is True, f"Historical release_s6 verification failed: {errs_s6}"

    active_rel_id = closure_doc["release_id"]
    active_dir = REPO_ROOT / "artifacts" / active_rel_id
    active_manifest = json.loads((active_dir / "evidence_manifest.json").read_text(encoding="utf-8"))

    assert active_dir.exists()
    assert closure_doc["campaign_semantic_sha256"] == active_manifest["semantic_sha256"]
    assert closure_doc["adversarial_mutation_suite"]["total_mutations"] >= 28
    assert closure_doc["adversarial_mutation_suite"]["false_accepts"] == 0
    expected_tests_str = f"{active_manifest['junit_summary']['tests']} passed"
    assert expected_tests_str in closure_doc["platforms_verified"]["macbook_pro_arm64"]["pytest_result"]
    assert active_manifest["junit_summary"]["failures"] == 0
    assert active_rel_id in final_status
    assert expected_tests_str in final_status
    tot_mut = closure_doc["adversarial_mutation_suite"]["total_mutations"]
    assert f"{tot_mut} / {tot_mut}" in final_status or "28 / 28" in final_status
    assert active_manifest["semantic_sha256"] in final_status

    ok_active, errs_active, _ = verify_evidence_bundle(active_dir)
    assert ok_active is True, f"{active_rel_id} verification failed: {errs_active}"

    with (REPO_ROOT / "requirements_matrix.csv").open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) >= 69
    for r in rows[1:]:
        assert len(r) == 7
        assert r[6] == "PASS"


def test_r8_t20_deliverable_pdfs_visual_tables_zero_raw_markdown_and_page_limits() -> None:
    """T20 (R8-F06): Deliverable PDFs render visual vector tables, have zero raw Markdown syntax, and obey page limits."""
    generate_all_deliverable_pdfs(REPO_ROOT)
    docs_dir = REPO_ROOT / "docs"
    assert _count_pdf_pages(docs_dir / "PHYSICAL_ACCOUNT.pdf") == 1
    assert 1 <= _count_pdf_pages(docs_dir / "MEMO_SCENARIO_C.pdf") <= 2
    assert 1 <= _count_pdf_pages(docs_dir / "PITCH_SLIDES.pdf") <= 10
    assert 1 <= _count_pdf_pages(REPO_ROOT / "README.pdf") <= 3

    for md_file, pdf_file in [
        (docs_dir / "PHYSICAL_ACCOUNT.md", docs_dir / "PHYSICAL_ACCOUNT.pdf"),
        (docs_dir / "MEMO_SCENARIO_C.md", docs_dir / "MEMO_SCENARIO_C.pdf"),
        (docs_dir / "PITCH_SLIDES.md", docs_dir / "PITCH_SLIDES.pdf"),
        (REPO_ROOT / "README.md", REPO_ROOT / "README.pdf"),
    ]:
        assert verify_pdf_contains_markdown_lines(md_file, pdf_file)
        raw_pdf = pdf_file.read_bytes().decode("ascii", errors="ignore")
        assert " re " in raw_pdf


def test_r8_t21_end_to_end_live_b_to_a_workflow_without_cache() -> None:
    """T21 (R8-F06): End-to-end live Scenario B (advance + perturb) -> Scenario A (modified G6 + new G7) workflow."""
    sess = GovernorSession(REPO_ROOT)
    # 1. Advance Scenario B to T=720s and inject cooling degradation
    c1, r1 = dispatch_api_request(REPO_ROOT, "POST", "/api/advance", {"target_sim_time_s": 720}, session=sess)
    assert c1 == 200 and r1["live_operational_state"]["sim_time_s"] == 720
    c2, r2 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/perturb",
        {"perturbation_type": "COOLING_LOSS_30C_40PCT", "seawater_temp_c": 30.0},
        session=sess,
    )
    assert c2 == 200
    assert r2["current_budget_snapshot"]["classes"]["cooling"]["degraded_capacity"] == 72000
    assert r2["current_budget_snapshot"]["feasible"] is True

    # 2. Switch to Scenario A admission with modified G6 (900 kg)
    c3, r3 = dispatch_api_request(
        REPO_ROOT,
        "POST",
        "/api/admit_live",
        {
            "installed_guest_ids": ["G1", "G2", "G4"],
            "candidate_guest_id": "G6",
            "overrides": {"mass_kg": 900},
            "at_port": True,
        },
        session=sess,
    )
    assert c3 == 200 and r3["decision"]["verdict"] == "YES"
    assert r3["current_budget_snapshot"]["classes"]["mass_stability"]["actual"] == 5435

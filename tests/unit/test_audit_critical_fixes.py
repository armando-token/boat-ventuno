"""Regression tests for the scope-audit critical fixes (D1 label, D3/D4 one-sentence rationale, UI decision log)."""

from __future__ import annotations

from pathlib import Path
from http.server import ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch
from urllib.request import urlopen

from sdv_governor.admission import evaluate_four_guest_combinations
from sdv_governor.arbitration import arbitrate_live_conflict
from sdv_governor.manifest import load_guest_catalog, load_priority_profile, validate_mission_envelope
from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file, run_scenario_b_replay
from sdv_governor.server import GovernorSession, serve_operator_ui

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _load():
    catalog = load_guest_catalog(DATA_DIR / "guests")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    survey = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    rescue = load_priority_profile(DATA_DIR / "priorities" / "rescue_primary.yaml")
    return catalog, ship, scen, survey, rescue


def _one_sentence(text: str) -> bool:
    return text.endswith(".") and text.count(". ") == 0


def test_civil_g5_catalog_and_nominal_temperature_match_bundle() -> None:
    for root in (REPO_ROOT, REPO_ROOT / "src" / "sdv_governor" / "_bundled"):
        guests = root / "data" / "guests"
        assert [p.name for p in guests.glob("G5_*.yaml")] == ["G5_emergency_comms_edge.yaml"]
        catalog = load_guest_catalog(guests)
        assert catalog["G5"].category == "EMERGENCY_COMMS_EDGE_COMPUTE"
        scenario = load_yaml_file(root / "data" / "scenarios" / "scenario_B_arbitration_degradation.yaml")
        state = build_initial_state_from_scenario(scenario, catalog)
        assert state.seawater_temp_c == 24.0
        schema = (root / "schemas" / "guest_manifest.schema.json").read_text(encoding="utf-8")
        assert '"EMERGENCY_COMMS_EDGE_COMPUTE"' in schema
    assert "foredeck_sar_mount:" in (REPO_ROOT / "viz3d" / "spec" / "vessel_spec.yaml").read_text()


def test_3d_entry_redirect_preserves_relative_asset_resolution() -> None:
    with patch("sdv_governor.server.ThreadingHTTPServer") as factory:
        serve_operator_ui(REPO_ROOT)
    handler = factory.call_args.args[1]
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    worker = Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{httpd.server_port}"
    try:
        with urlopen(base + "/viz3d", timeout=5) as response:
            assert response.url == base + "/viz3d/"
            assert b'css/app.css' in response.read()
        with urlopen(base + "/viz3d/css/app.css", timeout=5) as response:
            assert response.status == 200
            assert "text/css" in response.headers["Content-Type"]
        with urlopen(base + "/viz3d/app_standalone.js", timeout=5) as response:
            assert response.status == 200
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(timeout=5)


def test_d1_scenario_b_conditional_witness_names_real_binding_class() -> None:
    catalog, ship, _, survey, _ = _load()
    env = validate_mission_envelope(load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml"))
    rep = evaluate_four_guest_combinations(catalog, ship, env, survey)
    scen_b = next(c for c in rep["combinations"] if c["combination"] == ["G1", "G3", "G4", "G5"])
    classes = {w["resource_class"] for w in scen_b["fourth_guest_binding_constraints"]}
    assert classes == {"data"}, classes


def test_d3_captain_sentence_cost_and_margin_differ_by_profile() -> None:
    catalog, ship, scen, survey, rescue = _load()
    state = build_initial_state_from_scenario(scen, catalog)
    state.sim_time_s = 480
    r_s = arbitrate_live_conflict(state, ship, survey, catalog)
    r_r = arbitrate_live_conflict(state, ship, rescue, catalog)
    for r in (r_s, r_r):
        assert _one_sentence(r["rationale"]), r["rationale"]
        assert "margin" in r["rationale"] and "G3 loses" in r["rationale"]
        assert r["rationale_detail"]
        conds = {c["condition"] for c in r["conflict"]["incompatible_conditions"]}
        assert conds == {"ship_speed_kn", "heading", "deck_machinery"}
    assert r_s["rationale"] != r_r["rationale"]
    assert "SURVEY_PRIMARY" in r_s["rationale"] and "RESCUE_PRIMARY" in r_r["rationale"]


def test_d3_unverified_roof_refusal_names_roof_constraint() -> None:
    catalog, ship, scen, survey, _ = _load()
    state = build_initial_state_from_scenario(scen, catalog)
    state.sim_time_s = 480
    state.hangar_roof_clearance_verified = False
    res = arbitrate_live_conflict(state, ship, survey, catalog)
    assert res["verdict"] == "NO"
    assert "hangar_roof_drone_clearance" in res["rationale"]
    assert _one_sentence(res["rationale"])


def test_d4_min12_rationale_lists_every_guest_and_differs_by_profile() -> None:
    catalog, ship, scen, survey, rescue = _load()
    out = {}
    for prof in (survey, rescue):
        rep = run_scenario_b_replay(scen, ship, prof, catalog)
        d4 = next(d["decision"] for d in rep["decisions"] if d["phase"] == "D4_DEGRADATION")
        impacts = {i["guest_id"]: i for i in d4["guest_impacts"]}
        assert set(impacts) == {"G1", "G3", "G4", "G5"}
        for gid in impacts:
            assert gid in d4["rationale"]
        assert impacts["G5"]["loses"].startswith("70%")
        out[prof.priority_profile_id] = d4
    assert out["SURVEY_PRIMARY"]["rationale"] != out["RESCUE_PRIMARY"]["rationale"]
    assert "acoustic HOLD" in out["RESCUE_PRIMARY"]["guest_impacts"][1]["protected_reason"]


def test_ui_decision_log_reads_replay_shape_and_has_tick() -> None:
    catalog, ship, scen, survey, _ = _load()
    rep = run_scenario_b_replay(scen, ship, survey, catalog)
    sess = GovernorSession(REPO_ROOT)
    log = [e for e in sess.build_decision_log(rep["decisions"]) if e["type"].startswith("D")]
    d3 = next(e for e in log if e["type"] == "D3_ARBITRATION")
    assert d3["tick"] == 24
    assert "status=OK" not in d3["causal_sentence"] and "None" not in d3["causal_sentence"]
    assert d3["causal_sentence"].startswith("G4 battery")
    d4 = next(e for e in log if e["type"] == "D4_DEGRADATION")
    assert d4["causal_sentence"].startswith("Under SURVEY_PRIMARY")

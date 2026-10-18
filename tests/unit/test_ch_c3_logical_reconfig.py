"""CH-C3: logical (software-only) reconfiguration when a physical install is impossible (0 crew at sea)."""

from __future__ import annotations

import copy
import time
from pathlib import Path

import pytest

from sdv_governor.admission import build_baseline_state_with_installed_guests, evaluate_admission
from sdv_governor.degradation import DEPLOYED_VEHICLE_MODES
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    load_yaml_file as load_manifest_yaml,
    parse_guest_manifest,
    validate_mission_envelope,
    validate_ship_profile,
)
from sdv_governor.models import StrictValidationError
from sdv_governor.reconfiguration import ACTION_TYPES, propose_logical_reconfiguration
from sdv_governor.replay import load_yaml_file
from sdv_governor.server import GovernorSession, dispatch_api_request
from sdv_governor.ship_state import ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _load():
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))
    return catalog, ship


def _state(installed, catalog, ship, modes=None, crew=0):
    s0 = ShipOperationalState(
        sim_time_s=0, vessel_mode="TRANSIT", ship_speed_kn=12.0, wind_kn=8, sea_state=3,
        seawater_temp_c=16.0, power_reserve_w=200000,
        crew_count=crew, crew_regime="UNCREWED_0" if crew == 0 else "FULL_CREW_14",
    )
    st, _ = build_baseline_state_with_installed_guests(installed, s0, ship, catalog)
    for gid, mode in (modes or {}).items():
        st.installed_guests[gid].active_mode = mode
    return st


def _profile():
    return load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")


def _admit(cand, st, catalog, ship):
    env = validate_mission_envelope(load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml"))
    return evaluate_admission(cand, st, ship, env, _profile(), catalog, at_port=False)


def test_ch_c3_01_g4_at_sea_crew0_gets_retask_option() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3", "G5"], catalog, ship, {"G5": "DEGRADED_70PCT_LOSS"})
    d = _admit(catalog["G4"], st, catalog, ship)
    assert d["verdict"] == "NO"
    assert d["alternative_action"] == "LOGICAL_RECONFIGURATION"
    lr = d["logical_reconfiguration"]
    assert lr["status"] == "OPTIONS_AVAILABLE"
    retasks = [o for o in lr["options"] if o["kind"] == "RETASK_MODE"]
    assert retasks and retasks[0]["actions"][0]["target"] in ("G1", "G5")
    best = [o for o in lr["options"] if o["actions"][0]["target"] == "G5"][0]
    assert best["coverage_by_capability"]["maritime_sar_rescue"] > 0
    assert best["coverage_by_capability"]["surface_surveillance"] > 0
    assert "0 crew at sea: physical install of G4 impossible; proposed logical reconfiguration" in d["rationale"]


def test_ch_c3_02_g5_candidate_reallocates_compute_on_node_with_most_margin() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3"], catalog, ship)
    res = propose_logical_reconfiguration(catalog["G5"], st, ship, catalog, _profile())
    opt = [o for o in res["options"] if o["kind"] == "REALLOCATE_COMPUTE"]
    assert opt
    assert opt[0]["actions"][0]["to"] == "node_3"  # node_3 and node_4 tie on free cores (idle); lowest id wins
    assert 0 < opt[0]["coverage"] <= 1.0


def test_ch_c3_03_none_feasible_has_reason() -> None:
    catalog, ship = _load()
    st = _state(["G4"], catalog, ship)
    res = propose_logical_reconfiguration(catalog["G6"], st, ship, catalog, _profile())
    assert res["status"] == "NONE_FEASIBLE"
    assert res["options"] == []
    assert res["reason"]


def test_ch_c3_04_protected_and_vehicle_guests_never_retasked() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3", "G4", "G5"], catalog, ship, {"G3": "HOLD_ACOUSTIC", "G4": "FLIGHT_PATROL", "G5": "DEGRADED_70PCT_LOSS"})
    res = propose_logical_reconfiguration(catalog["G6"], st, ship, catalog, _profile())
    res2 = propose_logical_reconfiguration(catalog["G4"], st, ship, catalog, _profile())
    for r in (res, res2):
        for o in r["options"]:
            for a in o["actions"]:
                assert a["target"] not in ("G3", "G4") or a["action_type"] == "COMPUTE_PLACEMENT"
                if a["action_type"] == "MODE_CHANGE":
                    assert a["to"] not in DEPLOYED_VEHICLE_MODES.get(a["target"], ())


def test_ch_c3_05_actions_are_logical_only() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3", "G5"], catalog, ship, {"G5": "DEGRADED_70PCT_LOSS"})
    for cand in ("G4", "G5", "G6"):
        res = propose_logical_reconfiguration(catalog[cand], st, ship, catalog, _profile())
        assert len(res["options"]) <= 3
        for o in res["options"]:
            assert o["coverage"] >= 0.10
            for a in o["actions"]:
                assert a["action_type"] in ACTION_TYPES


def test_ch_c3_06_deterministic() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3", "G5"], catalog, ship, {"G5": "DEGRADED_70PCT_LOSS"})
    runs = []
    for _ in range(3):
        r = propose_logical_reconfiguration(catalog["G4"], st, ship, catalog, _profile())
        r.pop("elapsed_ms")
        runs.append(r)
    assert runs[0] == runs[1] == runs[2]


def _sea_session():
    sess = GovernorSession(REPO_ROOT)
    dispatch_api_request(REPO_ROOT, "POST", "/api/reset", {}, session=sess)
    catalog, ship = _load()
    sess.live_state = _state(["G1", "G3", "G5"], catalog, ship, {"G5": "DEGRADED_70PCT_LOSS"})
    return sess


def test_ch_c3_07_apply_requires_fresh_state_version_and_applies() -> None:
    sess = _sea_session()
    catalog, ship = _load()
    prop = propose_logical_reconfiguration(catalog["G4"], sess.live_state, ship, catalog, _profile())
    oid = [o for o in prop["options"] if o["kind"] == "RETASK_MODE"][0]["option_id"]
    before = copy.deepcopy(sess.live_state.to_snapshot_dict())
    n_actions = len(sess.action_history)
    body = {"option_id": oid, "candidate_guest_id": "G4", "operator_confirmed": True}
    code, out = dispatch_api_request(
        REPO_ROOT, "POST", "/api/apply_logical_reconfig", {**body, "state_version": sess.state_version + 5}, session=sess
    )
    assert code == 409 and out["status"] == "STALE_STATE_VERSION"
    assert sess.live_state.to_snapshot_dict() == before and len(sess.action_history) == n_actions
    code, _ = dispatch_api_request(
        REPO_ROOT, "POST", "/api/apply_logical_reconfig",
        {**body, "operator_confirmed": False, "state_version": sess.state_version}, session=sess,
    )
    assert code == 422
    code, out = dispatch_api_request(
        REPO_ROOT, "POST", "/api/apply_logical_reconfig", {**body, "state_version": sess.state_version}, session=sess
    )
    assert code == 200 and out["result"]["status"] == "LOGICAL_RECONFIG_APPLIED"
    assert sess.action_history[-1]["action"] == f"APPLY_LOGICAL_RECONFIG:{oid}"
    assert sess.live_state.installed_guests["G5"].active_mode == "FULL_SPECTRUM"
    code, _ = dispatch_api_request(
        REPO_ROOT, "POST", "/api/apply_logical_reconfig",
        {**body, "option_id": "NOT_AN_OPTION", "state_version": sess.state_version}, session=sess,
    )
    assert code == 422


def test_ch_c3_08_latency_p95_under_50ms() -> None:
    catalog, ship = _load()
    st = _state(["G1", "G3", "G5"], catalog, ship, {"G5": "DEGRADED_70PCT_LOSS"})
    prof = _profile()
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        propose_logical_reconfiguration(catalog["G4"], st, ship, catalog, prof)
        times.append((time.perf_counter() - t0) * 1000)
    times.sort()
    assert times[int(0.95 * len(times)) - 1] <= 50.0


def test_ch_c3_09_mistyped_logical_workload_rejected() -> None:
    raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G5_emergency_comms_edge.yaml")
    good = parse_guest_manifest(copy.deepcopy(raw))
    assert good.logical_workload["compute"]["cores"] == 4
    for mutate in (
        lambda r: r["logical_workload"]["compute"].__setitem__("cores", "4"),
        lambda r: r["logical_workload"]["compute"].__setitem__("cores", True),
        lambda r: r["logical_workload"].__setitem__("capability_fraction", 1.5),
        lambda r: r["logical_workload"].__setitem__("extra", 1),
        lambda r: r["logical_workload"]["provenance"].__setitem__("compute", "MAGIC"),
        lambda r: r.__setitem__("logical_workload", "oops"),
    ):
        bad = copy.deepcopy(raw)
        mutate(bad)
        with pytest.raises(StrictValidationError):
            parse_guest_manifest(bad)

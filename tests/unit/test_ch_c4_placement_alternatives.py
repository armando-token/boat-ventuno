"""CH-C4: D2_LEXICOGRAPHIC_V2 with ranked alternatives and rejected placements (plan B: scarcity as tie-break)."""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path

import pytest

from sdv_governor import admission, placement, replay
from sdv_governor.admission import build_baseline_state_with_installed_guests, evaluate_admission
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    parse_guest_manifest,
    validate_mission_envelope,
    validate_ship_profile,
)
from sdv_governor.manifest import load_yaml_file as load_manifest_yaml
from sdv_governor.placement import (
    D2_OBJECTIVE_ID,
    D2_SCARCITY_WEIGHTS,
    D2_SORT_KEY_FIELDS,
    _min_margin_bucket,
    find_feasible_placement,
)
from sdv_governor.replay import load_yaml_file
from sdv_governor.ship_state import ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

# sha256 of the (guest, installed, positions, node) sequence of the 105 placement searches in the official campaign,
# computed on the base commit c4909d5 before C4 (see artifacts/ch_c4/GOLDEN_DIFF.md)
OFFICIAL_PLACEMENTS_SHA256 = "908cc2c3f55ed6d8f7a4737889bbbbaf953b9b57320fd52afb23d60514f30052"


def _setup():
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))
    return catalog, ship


def _port_state(installed, catalog, ship) -> ShipOperationalState:
    s0 = ShipOperationalState(
        sim_time_s=0, vessel_mode="PORT_OPS", ship_speed_kn=0.0, wind_kn=8, sea_state=1,
        seawater_temp_c=16.0, power_reserve_w=200000,
    )
    state, _ = build_baseline_state_with_installed_guests(installed, s0, ship, catalog)
    return state


def _g5_result():
    catalog, ship = _setup()
    state = _port_state(["G1", "G3", "G4"], catalog, ship)
    return find_feasible_placement(catalog["G5"], state, ship, catalog)


def test_ch_c4_01_alternatives_and_rejected_with_occupied_mast() -> None:
    r = _g5_result()
    assert r["feasible"] and r["objective_id"] == D2_OBJECTIVE_ID
    assert r["placement_alternatives"] and r["rejected_placements"]
    assert len(r["placement_alternatives"]) <= 5 and len(r["rejected_placements"]) <= 5
    assert any(
        rej["subcomponent_positions"].get("G5_MAST_ANTENNA") == "mast_3" and "mast_3 already occupied by G1" in rej["why_rejected"]
        for rej in r["rejected_placements"]
    )
    for rej in r["rejected_placements"]:
        assert set(rej["binding"]) == {"resource_class", "constraint_id", "margin"}
    assert all(a["why_not_chosen"] for a in r["placement_alternatives"])


def test_ch_c4_02_chosen_has_minimum_key() -> None:
    r = _g5_result()
    chosen = r["chosen_key"]
    assert len(chosen) == len(r["sort_key_fields"]) == len(D2_SORT_KEY_FIELDS)
    for alt in r["placement_alternatives"]:
        assert chosen <= alt["key"]
    assert chosen[-1] == r["compute_node"]
    assert r["evaluated_count"] >= 1 + len(r["rejected_placements"])


def _synthetic_candidate(catalog, allowed):
    raw, _ = load_manifest_yaml(DATA_DIR / "guests" / "G6_civil_hydro_sampler.yaml")
    raw = copy.deepcopy(raw)
    sc = raw["subcomponents"][0]
    sc["preferred_positions"] = ["bay2"]
    sc["allowed_positions"] = allowed
    return parse_guest_manifest(raw)


def test_ch_c4_03_scarcity_breaks_ties_toward_cheaper_slot() -> None:
    catalog, ship = _setup()
    state = _port_state(["G2"], catalog, ship)  # bay2 (the preferred slot) is taken
    cand = _synthetic_candidate(catalog, ["bay2", "flight_deck", "bay1_a"])
    r = find_feasible_placement(cand, state, ship, catalog)
    assert r["subcomponent_positions"] == {"G6_HYDRO_SKID_10FT": "bay1_a"}
    alt = r["placement_alternatives"][0]
    assert alt["subcomponent_positions"] == {"G6_HYDRO_SKID_10FT": "flight_deck"}
    assert "consumes scarcer slots (scarcity 2 vs 1" in alt["why_not_chosen"]
    assert D2_SCARCITY_WEIGHTS["mast_3"] > max(v for k, v in D2_SCARCITY_WEIGHTS.items() if not k.startswith("mast"))


def test_ch_c4_04_margin_bucket_is_min_over_classes_and_precedes_stability() -> None:
    snap = {
        "classes": {
            "power": {"free": 50, "degraded_capacity": 100},
            "cooling": {"free": 3, "degraded_capacity": 100},
            "time": {"free": 1.0, "degraded_capacity": 0},
        }
    }
    assert _min_margin_bucket(snap) == 6  # floor(200 * 0.03)
    f = list(D2_SORT_KEY_FIELDS)
    assert f.index("-min_margin_bucket") < f.index("stab_eq") < f.index("node_used_cores") < f.index("nid")
    assert f.index("pref_penalty") < f.index("scarcity_cost")  # plan B ordering


def test_ch_c4_05_determinism_three_runs() -> None:
    runs = [json.dumps(
        {k: v for k, v in _g5_result().items() if k in placement.D2_REPORT_KEYS}, sort_keys=True
    ) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_ch_c4_06_official_placements_unchanged_and_golden_diff_documented() -> None:
    calls = []
    orig = placement.find_feasible_placement

    def wrapped(candidate, base_state, *a, **kw):
        r = orig(candidate, base_state, *a, **kw)
        calls.append(
            {
                "guest": candidate.guest_id,
                "installed": sorted(base_state.installed_guests),
                "mode": kw.get("mode_override"),
                "feasible": r["feasible"],
                "positions": r["subcomponent_positions"],
                "node": r["compute_node"],
            }
        )
        return r

    mods = [m for m in (placement, admission, replay) if hasattr(m, "find_feasible_placement")]
    try:
        for m in mods:
            m.find_feasible_placement = wrapped
        replay.run_full_campaign_suite(REPO_ROOT)
    finally:
        for m in mods:
            m.find_feasible_placement = orig
    digest = hashlib.sha256(json.dumps(calls, indent=1, sort_keys=True).encode("utf-8")).hexdigest()
    assert len(calls) == 229
    assert digest == OFFICIAL_PLACEMENTS_SHA256, "official placements changed: update artifacts/ch_c4/GOLDEN_DIFF.md"
    assert (REPO_ROOT / "artifacts" / "ch_c4" / "GOLDEN_DIFF.md").is_file()


def test_ch_c4_07_latency_of_admission_with_alternatives() -> None:
    catalog, ship = _setup()
    env = validate_mission_envelope(load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml"))
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    state = _port_state(["G1", "G3", "G4"], catalog, ship)
    evaluate_admission(catalog["G5"], state, ship, env, prof, catalog, at_port=True)
    times = []
    for _ in range(20):
        t0 = time.perf_counter()
        dec = evaluate_admission(catalog["G5"], state, ship, env, prof, catalog, at_port=True)
        times.append(time.perf_counter() - t0)
    times.sort()
    # measured p95 ~5 ms on the dev Mac (base ~5 ms); 100 ms is a loose CI bound far below the 1.5 s hard cap
    assert times[int(len(times) * 0.95) - 1] < 0.1
    assert dec["placement"]["objective_id"] == D2_OBJECTIVE_ID
    assert dec["placement_alternatives"] and dec["rejected_placements"]


def test_ch_c4_08_readme_d2_row_matches_sort_key_fields() -> None:
    row = next(l for l in (REPO_ROOT / "README.md").read_text(encoding="utf-8").splitlines() if l.startswith("| **D2**"))
    assert D2_OBJECTIVE_ID in row
    assert ", ".join(D2_SORT_KEY_FIELDS) in row

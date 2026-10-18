"""CH-C7: data-driven flight deck <-> stern ramp exclusivity (Annex A.3)."""

from __future__ import annotations

import json

import copy
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from sdv_governor.manifest import load_guest_catalog, load_priority_profile, parse_guest_manifest, validate_ship_profile
from sdv_governor.manifest import load_yaml_file as manifest_load_yaml
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file, run_scenario_b_replay
from sdv_governor.scheduling import evaluate_a_prime_window

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"

PINNED_B_SURVEY_HASH = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "golden_hashes.json").read_text())["scenario_B_survey_primary_sha256"]
PINNED_B_RESCUE_HASH = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "golden_hashes.json").read_text())["scenario_B_rescue_primary_sha256"]
PINNED_CAMPAIGN_HASH = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "golden_hashes.json").read_text())["campaign_semantic_sha256"]


def _catalog():
    return load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")


def _ship():
    return validate_ship_profile(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))


def test_ch_c7_01_golden_hashes_identical() -> None:
    from sdv_governor.replay import run_full_campaign_suite

    catalog, ship = _catalog(), _ship()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    for prof_name, expected in (("survey_primary", PINNED_B_SURVEY_HASH), ("rescue_primary", PINNED_B_RESCUE_HASH)):
        prof = load_priority_profile(DATA_DIR / "priorities" / f"{prof_name}.yaml")
        assert run_scenario_b_replay(scen, ship, prof, catalog)["semantic_sha256"] == expected
    assert run_full_campaign_suite(REPO_ROOT)["campaign_semantic_sha256"] == PINNED_CAMPAIGN_HASH


def test_ch_c7_02_synthetic_g7_on_ramp_rejected_while_g4_recovers() -> None:
    catalog, ship = _catalog(), _ship()
    scen = load_yaml_file(DATA_DIR / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    state = build_initial_state_from_scenario(scen, catalog)
    g3 = catalog["G3"]
    g7 = replace(g3, guest_id="G7", ship_state_constraints=replace(g3.ship_state_constraints, requires_stern_ramp_free=False, launch_recovery_area="stern_ramp"))
    catalog["G7"] = g7
    state.installed_guests["G4"].active_mode = "RECOVERY_APPROACH"
    state.installed_guests["G4"].powered_on = True
    res = evaluate_a_prime_window(g7, "LAUNCH_RECOVERY", state, catalog, ship_profile=ship)
    assert res["permitted"] is False
    assert any(r.startswith("area_mutual_exclusion") and "G4" in r for r in res["blocking_reasons"])

    state.installed_guests["G4"].active_mode = "FLIGHT_PATROL"
    res_idle = evaluate_a_prime_window(g7, "LAUNCH_RECOVERY", state, catalog, ship_profile=ship)
    assert not any(r.startswith("area_mutual_exclusion") for r in res_idle["blocking_reasons"])

    g7_none = replace(g7, ship_state_constraints=replace(g7.ship_state_constraints, launch_recovery_area="none"))
    state.installed_guests["G4"].active_mode = "RECOVERY_APPROACH"
    res_none = evaluate_a_prime_window(g7_none, "LAUNCH_RECOVERY", state, catalog, ship_profile=ship)
    assert not any(r.startswith("area_mutual_exclusion") for r in res_none["blocking_reasons"])


def test_ch_c7_03_asymmetric_flags_rejected_and_bad_area_rejected() -> None:
    raw = copy.deepcopy(load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml"))
    del raw["positions"]["hangar_and_flight_deck"]["stern_ramp"]["mutually_exclusive_with_flight_deck"]
    with pytest.raises(StrictValidationError) as exc:
        validate_ship_profile(raw)
    assert exc.value.code == "ASYMMETRIC_AREA_EXCLUSION"

    g_raw, _ = manifest_load_yaml(DATA_DIR / "guests" / "G4_uav_sar.yaml")
    bad = copy.deepcopy(g_raw)
    bad["ship_state_constraints"]["launch_recovery_area"] = "hangar"
    with pytest.raises(StrictValidationError):
        parse_guest_manifest(bad)
    bad["ship_state_constraints"]["launch_recovery_area"] = True
    with pytest.raises(StrictValidationError):
        parse_guest_manifest(bad)


def test_ch_c7_03b_manifest_areas_from_data() -> None:
    catalog = _catalog()
    assert catalog["G3"].ship_state_constraints.launch_recovery_area == "stern_ramp"
    assert catalog["G4"].ship_state_constraints.launch_recovery_area == "flight_deck"
    assert catalog["G1"].ship_state_constraints.launch_recovery_area == "none"


def test_ch_c7_04_no_hardcoded_g3_g4_exclusion_logic() -> None:
    out = subprocess.run(
        ["git", "grep", "-n", '"G3", "G4"', "--", "src/sdv_governor/scheduling.py", "src/sdv_governor/budgets.py", "src/sdv_governor/areas.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == ""

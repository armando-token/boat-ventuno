"""CH-C5: Scenario A states explicitly whether the verdict holds with one genset down."""

from __future__ import annotations

import copy
import subprocess
import sys
from pathlib import Path

import pytest

from sdv_governor.admission import (
    build_baseline_state_with_installed_guests,
    evaluate_admission,
    evaluate_mission_envelope_a10,
)
from sdv_governor.manifest import load_guest_catalog, load_priority_profile, validate_mission_envelope
from sdv_governor.models import StrictValidationError
from sdv_governor.replay import load_yaml_file
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _setup():
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    env = validate_mission_envelope(load_yaml_file(DATA_DIR / "ship" / "mission_envelope_a10.yaml"))
    prof = load_priority_profile(DATA_DIR / "priorities" / "survey_primary.yaml")
    return catalog, ship, env, prof


def _port_state() -> ShipOperationalState:
    return ShipOperationalState(
        sim_time_s=0, vessel_mode="PORT_OPS", ship_speed_kn=0.0, wind_kn=8, sea_state=1,
        seawater_temp_c=16.0, power_reserve_w=200000,
    )


def _admit(candidate: str, installed: list[str]):
    catalog, ship, env, prof = _setup()
    state, _ = build_baseline_state_with_installed_guests(installed, _port_state(), ship, catalog)
    return evaluate_admission(
        catalog[candidate], state, ship, env, prof, catalog, at_port=True, allow_displacement=False
    )


def test_ch_c5_01_g5_with_g1_g3_g4_holds_with_step_down() -> None:
    dec = _admit("G5", ["G1", "G3", "G4"])
    g = dec["one_genset_down"]
    assert g["window_id"] == "DEGRADED_GEN_LOSS_100KW_30C"
    assert g["holds"] == "YES_WITH_STEP_DOWN"
    assert g["power_reserve_w"] == 100000 and g["seawater_temp_c"] == 30.0
    assert g["binding_class"]
    assert "G5" in {sd["guest_id"] for sd in g["required_step_downs"]}
    for sd in g["required_step_downs"]:
        assert set(sd) == {"guest_id", "from_mode", "to_mode", "capability_lost"}
        assert sd["from_mode"] != sd["to_mode"]


def test_ch_c5_02_light_candidate_holds_without_changes() -> None:
    dec = _admit("G1", [])
    g = dec["one_genset_down"]
    assert g["holds"] == "YES"
    assert g["required_step_downs"] == []
    assert g["binding_class"] is None


def test_ch_c5_03_infeasible_even_with_step_down_is_no_with_binding_class() -> None:
    catalog, ship, env, prof = _setup()
    state, _ = build_baseline_state_with_installed_guests(["G1", "G3", "G4"], _port_state(), ship, catalog)
    state.installed_guests["G5"] = InstalledGuestState(
        "G5", "FULL_SPECTRUM", True, {"G5_MAST_ANTENNA": "hangar_roof"}, "node_1"
    )
    harsh = copy.deepcopy(env)
    for w in harsh["envelope_states"]:
        if w.get("represents") == "SINGLE_GENSET_LOSS":
            w["power_reserve_w"] = 0
    rep = evaluate_mission_envelope_a10(catalog["G5"], state, ship, harsh, catalog)
    g = rep["one_genset_down"]
    assert g["holds"] == "NO"
    assert g["binding_class"]
    assert rep["envelope_feasible"] is False
    assert "does NOT hold" in g["sentence"]


def test_ch_c5_04_envelope_without_single_genset_window_rejected() -> None:
    _, _, env, _ = _setup()
    bad = copy.deepcopy(env)
    for w in bad["envelope_states"]:
        w.pop("represents", None)
    with pytest.raises(StrictValidationError):
        validate_mission_envelope(bad)
    typo = copy.deepcopy(env)
    for w in typo["envelope_states"]:
        if "represents" in w:
            w["represents"] = "ONE_GENSET"
    with pytest.raises(StrictValidationError):
        validate_mission_envelope(typo)


def test_ch_c5_05_sentence_in_rationale_and_cli() -> None:
    dec = _admit("G5", ["G1", "G3", "G4"])
    sentence = dec["one_genset_down"]["sentence"]
    assert sentence in dec["rationale"]
    first, second = dec["rationale"].split(". ", 1)[0], dec["rationale"].split(". ", 2)[1]
    assert first.startswith("Verdict=") and second.startswith("With one genset down")

    proc = subprocess.run(
        [sys.executable, "-m", "sdv_governor.cli", "admit", "--candidate", "G5", "--installed", "G1,G3,G4"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": ""},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0
    assert "One genset down [YES_WITH_STEP_DOWN]" in proc.stderr
    assert sentence in proc.stdout.replace("\\u00b0", "\u00b0") or "one_genset_down" in proc.stdout


def test_ch_c5_05b_ui_has_genset_line_without_innerhtml_on_payload() -> None:
    app_js = (REPO_ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    html = (REPO_ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'id="genset-down-line"' in html
    assert "Un generador caído" in app_js
    assert "SE MANTIENE CON BAJADA DE MODO" in app_js


def test_ch_c5_06_deterministic() -> None:
    a = _admit("G5", ["G1", "G3", "G4"])["one_genset_down"]
    b = _admit("G5", ["G1", "G3", "G4"])["one_genset_down"]
    assert a == b

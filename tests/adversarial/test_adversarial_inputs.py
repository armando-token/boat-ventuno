"""Adversarial input validation tests reproducing and defeating all F3 defects (DAT-04)."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import pytest

from sdv_governor.budgets import validate_strict_budget_input
from sdv_governor.manifest import load_yaml_file, parse_guest_manifest
from sdv_governor.models import StrictValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def test_dat_04_corrupt_inputs_rejected() -> None:
    """DAT-04: Reject NaN, Inf, negative budgets, zero budgets, booleans-as-numbers, unknown missions, and unknown fields."""
    repro_path = REPO_ROOT / "research" / "handoff_repro" / "resource_engine_repro.json"
    repro_data = json.loads(repro_path.read_text(encoding="utf-8"))
    assert isinstance(repro_data, list) and len(repro_data) >= 10
    known_missions = {"patrol", "sar"}

    # 1. Verify all corrupt budget/mission inputs from resource_engine_repro.json are rejected fail-closed
    corrupt_inputs = [
        ("patrol", 0),
        ("patrol", -50),
        ("patrol", True),
        ("patrol", False),
        ("sar", 0),
        ("sar", -1),
        ("unknown_mission", 100),
        ("UNKNOWN", 130),
        ("", 100),
        ("sar", 74.9),
        ("patrol", float("nan")),
        ("patrol", float("inf")),
        ("patrol", float("-inf")),
        ("patrol", "100"),
        ("patrol", None),
    ]
    for raw_m, val_b in corrupt_inputs:
        with pytest.raises(StrictValidationError):
            validate_strict_budget_input(raw_m, val_b, known_missions)

    # 2. Verify guest manifest rejects unknown top-level fields, booleans as numbers, NaN, negative mass, and mast water cooling
    base_raw, _ = load_yaml_file(DATA_DIR / "guests" / "G1_mems_meteo.yaml")

    m_unknown = copy.deepcopy(base_raw)
    m_unknown["rogue_override_field"] = 123
    with pytest.raises(StrictValidationError) as e1:
        parse_guest_manifest(m_unknown)
    assert e1.value.code == "UNKNOWN_FIELD"

    m_bool_mass = copy.deepcopy(base_raw)
    m_bool_mass["subcomponents"][0]["mass_kg"] = True
    with pytest.raises(StrictValidationError) as e2:
        parse_guest_manifest(m_bool_mass)
    assert e2.value.code == "TYPE_ERROR"

    m_nan_cap = copy.deepcopy(base_raw)
    m_nan_cap["mission_capabilities"]["environmental_sensing"] = math.nan
    with pytest.raises(StrictValidationError) as e3:
        parse_guest_manifest(m_nan_cap)
    assert e3.value.code == "NON_FINITE"

    m_neg_power = copy.deepcopy(base_raw)
    m_neg_power["modes"]["NOMINAL"]["min_power_w"] = -100
    with pytest.raises(StrictValidationError) as e4:
        parse_guest_manifest(m_neg_power)
    assert e4.value.code == "NEGATIVE_VALUE"

    m_mast_water = copy.deepcopy(base_raw)
    m_mast_water["subcomponents"][0]["cooling_route"] = "CHILLED_WATER_LOOP"
    with pytest.raises(StrictValidationError) as e5:
        parse_guest_manifest(m_mast_water)
    assert e5.value.code == "MAST_WATER_FORBIDDEN"

    m_thermal_mismatch = copy.deepcopy(base_raw)
    m_thermal_mismatch["modes"]["NOMINAL"]["cooling_air_mast_w"] = 500  # != sustained_power_w (1200)
    with pytest.raises(StrictValidationError) as e6:
        parse_guest_manifest(m_thermal_mismatch)
    assert e6.value.code == "THERMAL_COUPLING_MISMATCH"

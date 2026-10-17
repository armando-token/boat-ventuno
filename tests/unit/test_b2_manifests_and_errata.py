"""Unit tests for B2 manifest schema validation, G4 850 kg errata, and 3-of-5 combinations regression (DAT-01..03)."""

from __future__ import annotations

import copy
from pathlib import Path
import pytest
import yaml

from sdv_governor.manifest import (
    compute_combinations_of_four,
    load_guest_catalog,
    load_yaml_file,
    parse_guest_manifest,
)
from sdv_governor.models import StrictValidationError

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def test_dat_01_six_valid_cards() -> None:
    """DAT-01: Six valid guest cards (G1..G5 official + G6 custom civil) validate against the same strict schema."""
    catalog = load_guest_catalog(
        DATA_DIR / "guests",
        errata_path=DATA_DIR / "overrides" / "brief_errata.yaml",
    )
    assert sorted(catalog.keys()) == ["G1", "G2", "G3", "G4", "G5", "G6"]
    assert catalog["G1"].total_mass_kg == 45
    assert catalog["G2"].total_mass_kg == 3500
    assert catalog["G3"].total_mass_kg == 5500
    assert catalog["G4"].total_mass_kg == 990
    assert catalog["G5"].total_mass_kg == 3025
    assert catalog["G6"].total_mass_kg == 420
    assert catalog["G6"].category == "CIVIL_HYDRO_SAMPLER"


def test_dat_02_g4_errata_850kg() -> None:
    """DAT-02: G4 uses Drone 140 kg + Hangar 850 kg = 990 kg per organizer errata; 900 kg without override fails."""
    errata_data, _ = load_yaml_file(DATA_DIR / "overrides" / "brief_errata.yaml")
    g4_raw, sha = load_yaml_file(DATA_DIR / "guests" / "G4_uav_sar.yaml")

    valid_g4 = parse_guest_manifest(g4_raw, source_sha256=sha, errata_override=errata_data)
    assert valid_g4.total_mass_kg == 990

    # Mutate hangar station back to un-corrected PDF 900 kg and verify fail-closed ERRATA_MISMATCH
    wrong_g4 = copy.deepcopy(g4_raw)
    for sc in wrong_g4["subcomponents"]:
        if sc["subcomponent_id"] == "G4_HANGAR_STATION":
            sc["mass_kg"] = 900

    with pytest.raises(StrictValidationError) as exc_info:
        parse_guest_manifest(wrong_g4, errata_override=errata_data)
    assert exc_info.value.code == "ERRATA_MISMATCH"


def test_dat_03_five_combinations_of_four() -> None:
    """DAT-03: Across the 5 combinations of 4 official guests (G1..G5), exactly 3 of 5 exceed 10,000 kg."""
    catalog = load_guest_catalog(
        DATA_DIR / "guests",
        errata_path=DATA_DIR / "overrides" / "brief_errata.yaml",
    )
    combos = compute_combinations_of_four(catalog)
    assert len(combos) == 5

    by_tuple = {tuple(c["guests"]): c for c in combos}
    assert by_tuple[("G1", "G2", "G3", "G4")]["total_mass_kg"] == 10035
    assert by_tuple[("G1", "G2", "G3", "G4")]["exceeds_mass_limit_10000_kg"] is True

    assert by_tuple[("G1", "G2", "G3", "G5")]["total_mass_kg"] == 12070
    assert by_tuple[("G1", "G2", "G3", "G5")]["exceeds_mass_limit_10000_kg"] is True

    assert by_tuple[("G1", "G2", "G4", "G5")]["total_mass_kg"] == 7560
    assert by_tuple[("G1", "G2", "G4", "G5")]["exceeds_mass_limit_10000_kg"] is False

    assert by_tuple[("G1", "G3", "G4", "G5")]["total_mass_kg"] == 9560
    assert by_tuple[("G1", "G3", "G4", "G5")]["exceeds_mass_limit_10000_kg"] is False

    assert by_tuple[("G2", "G3", "G4", "G5")]["total_mass_kg"] == 13015
    assert by_tuple[("G2", "G3", "G4", "G5")]["exceeds_mass_limit_10000_kg"] is True

    exceed_count = sum(1 for c in combos if c["exceeds_mass_limit_10000_kg"])
    assert exceed_count == 3

    # Verify per-subcomponent stability calculation on G5 (3000*1.0 + 25*3.0 = 3075.0 kg-eq, not 3025*3.0)
    g5_only = compute_combinations_of_four(catalog, guest_ids=("G1", "G2", "G4", "G5"))[0]
    g5_subs = [s for s in g5_only["subcomponents"] if s["guest_id"] == "G5"]
    assert sum(s["stability_eq_kg"] for s in g5_subs) == 3075.0

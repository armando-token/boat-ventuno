"""Integration tests for Forecast Observer Non-Interference (INV-1, INV-2, INV-3, INV-4, NI-01..03, FC-INT-01..06).

Pins campaign_semantic_sha256 and verifies that enabling the ForecastObserver
produces 100% bit-for-bit identical decision outputs, timeline ticks, and campaign hashes.
"""

from __future__ import annotations

import copy
import socket
from pathlib import Path
from typing import Any, Dict

import pytest

from sdv_governor.forecast.observer import ForecastObserver
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    validate_ship_profile,
)
from sdv_governor.replay import (
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Golden pinned hashes before forecast work (Megaplan F0, NI-01)
PINNED_CAMPAIGN_HASH = "fd806cda1c871f8c2c9db047f2b34f5547c70d2b60fe9111d4e35ba292646231"
PINNED_B_SURVEY_HASH = "90a2ce1137b0d5729c466f2c313b143d0d189ab21ae9d13093b5d3e380b94a6d"
PINNED_B_RESCUE_HASH = "d5b36570f0b6bf0ee068dff610a96e382a6c2192458f1cc93e31cc2fcfaf4c7a"
PINNED_B_COOLING_HASH = "d4638ec023aef2f484f700b24ae314b7f7d0261456ee697b5b35e9ae332ec5ae"
PINNED_C_CREW_HASH = "7ed9a261dc4f200bc92b33a8203551cc0b00eab1b4e56c894fc8576b1471a4d9"


@pytest.fixture(scope="module")
def common_governor_setup() -> Dict[str, Any]:
    data_dir = REPO_ROOT / "data"
    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
    ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    prof_survey = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
    prof_rescue = load_priority_profile(data_dir / "priorities" / "rescue_primary.yaml")
    scen_b = load_yaml_file(data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    scen_b_cool = load_yaml_file(data_dir / "scenarios" / "scenario_B_cooling_limited_30c.yaml")
    scen_c = load_yaml_file(data_dir / "scenarios" / "scenario_C_crew_regimes.yaml")

    return {
        "catalog": catalog,
        "ship": ship,
        "prof_survey": prof_survey,
        "prof_rescue": prof_rescue,
        "scen_b": scen_b,
        "scen_b_cool": scen_b_cool,
        "scen_c": scen_c,
    }


def test_ni01_pinned_campaign_semantic_hash() -> None:
    """NI-01 / FC-INT-01: Full campaign suite hash matches the exact golden pinned hash."""
    campaign_res = run_full_campaign_suite(REPO_ROOT)
    actual_campaign_hash = campaign_res["campaign_semantic_sha256"]
    assert actual_campaign_hash == PINNED_CAMPAIGN_HASH, (
        f"Campaign hash altered! Expected {PINNED_CAMPAIGN_HASH}, got {actual_campaign_hash}"
    )

    summary = campaign_res["semantic_summary"]
    assert summary["scenario_B_survey_primary_sha256"] == PINNED_B_SURVEY_HASH
    assert summary["scenario_B_rescue_primary_sha256"] == PINNED_B_RESCUE_HASH
    assert summary["scenario_B_cooling_limited_sha256"] == PINNED_B_COOLING_HASH
    assert summary["scenario_C_crew_regimes"]["scenario_c_event_semantic_sha256"] == PINNED_C_CREW_HASH


def test_ni01_observer_enabled_vs_disabled_equality(common_governor_setup: Dict[str, Any]) -> None:
    """NI-01 / FC-INT-01: For all scenarios, replay with observer=None and observer=ForecastObserver is bit-identical."""
    ship = common_governor_setup["ship"]
    catalog = common_governor_setup["catalog"]
    prof_survey = common_governor_setup["prof_survey"]
    prof_rescue = common_governor_setup["prof_rescue"]

    test_cases = [
        ("Scenario B Survey", common_governor_setup["scen_b"], prof_survey, PINNED_B_SURVEY_HASH),
        ("Scenario B Rescue", common_governor_setup["scen_b"], prof_rescue, PINNED_B_RESCUE_HASH),
        ("Scenario B Cooling 30C", common_governor_setup["scen_b_cool"], prof_survey, PINNED_B_COOLING_HASH),
        ("Scenario C Events", common_governor_setup["scen_c"], prof_survey, PINNED_C_CREW_HASH),
    ]

    for label, scen, prof, expected_hash in test_cases:
        res_none = run_scenario_b_replay(scen, ship, prof, catalog, observer=None)
        obs = ForecastObserver(ship, catalog)
        res_obs = run_scenario_b_replay(scen, ship, prof, catalog, observer=obs)

        assert res_none["semantic_sha256"] == expected_hash, f"{label} (none) hash mismatch"
        assert res_obs["semantic_sha256"] == expected_hash, f"{label} (obs) hash mismatch"
        assert res_none["semantic_sha256"] == res_obs["semantic_sha256"], (
            f"Observer interference detected on {label}!"
        )
        assert res_none["semantic_payload"]["decisions"] == res_obs["semantic_payload"]["decisions"], (
            f"Decisions mismatch on {label}"
        )
        assert res_none["semantic_payload"] == res_obs["semantic_payload"], f"Payload mismatch on {label}"


def test_ni02_payload_isolation(common_governor_setup: Dict[str, Any]) -> None:
    """NI-02 / FC-INT-02: Observer data strictly excluded from semantic_payload, decisions, and ticks."""
    ship = common_governor_setup["ship"]
    catalog = common_governor_setup["catalog"]
    prof_survey = common_governor_setup["prof_survey"]
    scen_b = common_governor_setup["scen_b"]

    obs = ForecastObserver(ship, catalog)
    res_obs = run_scenario_b_replay(scen_b, ship, prof_survey, catalog, observer=obs)

    # 1. semantic_payload must NOT contain observer keys
    payload = res_obs["semantic_payload"]
    assert "observer" not in payload
    assert "observer_semantic_sha256" not in payload
    assert "forecast" not in payload

    # 2. decisions must NOT contain any forecast keys
    for d in res_obs["decisions"]:
        assert not any(k.startswith("forecast") or k.startswith("observer") for k in d.keys())

    # 3. timeline_ticks must NOT contain observer keys
    for tick in res_obs["timeline_ticks"]:
        assert not any(k.startswith("forecast") or k.startswith("observer") for k in tick.keys())

    # 4. Observer data lives only at top level of result
    assert "observer" in res_obs
    assert res_obs["observer"] is not None
    assert "observer_semantic_sha256" in res_obs
    assert isinstance(res_obs["observer_semantic_sha256"], str)
    assert len(res_obs["observer_semantic_sha256"]) == 64


def test_ni03_state_preservation_per_tick(common_governor_setup: Dict[str, Any]) -> None:
    """NI-03 / FC-INT-03: State is completely unmutated by observer at every single tick."""
    ship = common_governor_setup["ship"]
    catalog = common_governor_setup["catalog"]
    prof_survey = common_governor_setup["prof_survey"]
    scen_b = common_governor_setup["scen_b"]

    res_none = run_scenario_b_replay(scen_b, ship, prof_survey, catalog, observer=None)
    obs = ForecastObserver(ship, catalog)
    res_obs = run_scenario_b_replay(scen_b, ship, prof_survey, catalog, observer=obs)

    ticks_none = res_none["timeline_ticks"]
    ticks_obs = res_obs["timeline_ticks"]
    assert len(ticks_none) == len(ticks_obs)

    for idx, (t_none, t_obs) in enumerate(zip(ticks_none, ticks_obs)):
        # Verify critical state variables match
        assert t_none["sim_time_s"] == t_obs["sim_time_s"], f"Tick {idx} sim_time mismatch"
        assert t_none["power_actual_w"] == t_obs["power_actual_w"], f"Tick {idx} power actual mismatch"
        assert t_none["power_committed_w"] == t_obs["power_committed_w"], f"Tick {idx} power committed mismatch"
        assert t_none["cooling_actual_w"] == t_obs["cooling_actual_w"], f"Tick {idx} cooling actual mismatch"
        assert t_none["thermal_inertia_status"] == t_obs["thermal_inertia_status"], f"Tick {idx} thermal status mismatch"
        assert t_none["events"] == t_obs["events"], f"Tick {idx} events mismatch"
        assert t_none["guest_modes"] == t_obs["guest_modes"], f"Tick {idx} guest modes mismatch"


def test_ni04_offline_socket_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """FC-INT-06 / INV-4: Replay with observer runs completely offline with socket blocked."""
    def _blocked_socket(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("NETWORK_ACCESS_FORBIDDEN: socket opened in offline runtime")

    monkeypatch.setattr(socket, "socket", _blocked_socket)

    # Must run both official campaign suite and observer replay without touching network
    campaign_res = run_full_campaign_suite(REPO_ROOT)
    assert campaign_res["campaign_semantic_sha256"] == PINNED_CAMPAIGN_HASH


def test_ni05_deterministic_observer_hash(common_governor_setup: Dict[str, Any]) -> None:
    """FC-INT-05 / INV-8: Observer semantic hash is 100% deterministic across consecutive runs."""
    ship = common_governor_setup["ship"]
    catalog = common_governor_setup["catalog"]
    prof_survey = common_governor_setup["prof_survey"]
    scen_b = common_governor_setup["scen_b"]

    obs1 = ForecastObserver(ship, catalog)
    res1 = run_scenario_b_replay(scen_b, ship, prof_survey, catalog, observer=obs1)

    obs2 = ForecastObserver(ship, catalog)
    res2 = run_scenario_b_replay(scen_b, ship, prof_survey, catalog, observer=obs2)

    assert res1["observer_semantic_sha256"] == res2["observer_semantic_sha256"]
    assert res1["observer"]["semantic"] == res2["observer"]["semantic"]

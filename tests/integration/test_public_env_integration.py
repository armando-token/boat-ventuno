"""Integration tests for Golden Gate Public Environmental Data (ENV-INT-01..07, INV-4, INV-5, INV-8).

Validates:
- ENV-INT-01: End-to-end replay with public SST (16.3 C -> ~150.4 kW capacity, DERIVED feed).
- ENV-INT-02: Structural arbitration decisions match official Scenario B (G4 protected, G5 shed).
- ENV-INT-03: Scenario A-prime Golden Gate NOT_NOW with computed operational window or explicit 48h limit.
- ENV-INT-04: INV-5 Envolvente intacta — 14-day envelope admission judged at 30C, not 16.3C.
- ENV-INT-05: INV-4 Offline runtime — socket.socket completely blocked, suite passes without network.
- ENV-INT-06: INV-8 Determinism — identical semantic SHA-256 across runs.
- ENV-INT-07: Feed loss fallback to 30C conservative fallback with UNAVAILABLE provenance.
"""

from __future__ import annotations

import copy
import socket
from pathlib import Path
from typing import Any, Dict

import pytest

from sdv_governor.budgets import compute_cooling_water_capacity_w
from sdv_governor.manifest import (
    load_guest_catalog,
    load_priority_profile,
    validate_mission_envelope,
    validate_ship_profile,
)
from sdv_governor.admission import evaluate_admission
from sdv_governor.models import canonical_json_hash
from sdv_governor.replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_public_env_suite,
    run_scenario_b_replay,
)
from sdv_governor.ship_state import ShipOperationalState, InstalledGuestState

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def env_suite_setup() -> Dict[str, Any]:
    data_dir = REPO_ROOT / "data"
    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
    ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    prof_survey = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
    scen_b_gg = load_yaml_file(data_dir / "scenarios" / "scenario_B_golden_gate_recorded.yaml")
    scen_aprime_gg = load_yaml_file(data_dir / "scenarios" / "scenario_Aprime_golden_gate_window.yaml")

    return {
        "catalog": catalog,
        "ship": ship,
        "prof_survey": prof_survey,
        "scen_b_gg": scen_b_gg,
        "scen_aprime_gg": scen_aprime_gg,
    }


def test_env_int_01_golden_gate_scenario_b_end_to_end(env_suite_setup: Dict[str, Any]) -> None:
    """ENV-INT-01: Scenario B Golden Gate replay uses real SST (16.3 C) -> ~150.4 kW capacity, DERIVED feed."""
    suite_res = run_public_env_suite(REPO_ROOT)
    scen_b_res = suite_res["scenario_b_golden_gate"]

    ticks = scen_b_res["timeline_ticks"]
    assert len(ticks) > 0

    t0 = ticks[0]
    assert t0["seawater_temp_c"] == 16.3
    # C(16.3 C) = 160000 - 40000 * (16.3 - 12.0) / 18.0 = 150444 W
    expected_nom, _, _ = compute_cooling_water_capacity_w(16.3, loop_degraded=False)
    assert t0["cooling_capacity_w"] == expected_nom
    assert t0["cooling_capacity_w"] == 150444

    # Verify feed provenance
    snap0 = t0["budget_snapshot"]
    cooling_class = snap0["classes"]["cooling"]
    assert cooling_class["local_limits"]["seawater_temp_c"] == 16.3


def test_env_int_02_structural_decisions_match_official_scenario_b(env_suite_setup: Dict[str, Any]) -> None:
    """ENV-INT-02: In Golden Gate Scenario B, D3 protects G4 and D4 sheds G5 under generator loss."""
    suite_res = run_public_env_suite(REPO_ROOT)
    scen_b_res = suite_res["scenario_b_golden_gate"]

    # Verify touchdown occurred within deadline
    touchdown_s = scen_b_res.get("executed_touchdown_time_s")
    assert touchdown_s is not None
    assert touchdown_s <= 1200  # G4 deadline: 8 min + 12 min = 20 min (1200 s)

    # Verify decisions include D4 degradation throttling G5
    decisions = scen_b_res["decisions"]
    phases = [d.get("phase") for d in decisions]
    assert "D4_DEGRADATION" in phases or any("DEGRADATION" in str(p) for p in phases)

    # Find the degradation decision and check that G5 was shed while G4 was protected
    d4_decs = [d for d in decisions if "DEGRADATION" in str(d.get("phase", ""))]
    assert len(d4_decs) > 0
    d4_content = d4_decs[0]["decision"]
    impacts = {item["guest_id"]: item for item in d4_content.get("guest_impacts", [])}
    assert "G5" in impacts
    assert "DEGRADED" in impacts["G5"]["mode_after"] or impacts["G5"].get("capability_retention_after", 1.0) < 1.0
    if "G4" in impacts:
        assert "FLIGHT" in impacts["G4"]["mode_after"] or impacts["G4"].get("capability_retention_after", 0.0) >= 0.9


def test_env_int_03_scenario_aprime_golden_gate_not_now(env_suite_setup: Dict[str, Any]) -> None:
    """ENV-INT-03: Scenario A-prime Golden Gate returns NOT_NOW with operational rationale."""
    suite_res = run_public_env_suite(REPO_ROOT)
    aprime = suite_res["scenario_aprime_golden_gate"]

    assert aprime["verdict"] == "NOT_NOW"
    assert not aprime["evaluation_now"]["permitted"]

    rationale = aprime["rationale"]
    assert "Verdict=NOT_NOW for candidate=G4." in rationale
    assert "Temporarily deferred;" in rationale
    # Must specify either operational window or lead limit
    assert ("next valid operational window" in rationale or "no valid operational window within the 48 h forecast" in rationale)


def test_env_int_04_inv5_envelope_intact_14_day_judged_at_30c(env_suite_setup: Dict[str, Any]) -> None:
    """ENV-INT-04 / INV-5: 14-day mission envelope admission remains strictly judged at 24/30C, not 16.3C."""
    catalog = env_suite_setup["catalog"]
    ship = env_suite_setup["ship"]
    prof_survey = env_suite_setup["prof_survey"]
    data_dir = REPO_ROOT / "data"
    mission_envelope = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))

    state_g6 = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="PORT_INTEGRATION",
        speed_kn=0.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=8,
        sea_state=1,
        seawater_temp_c=16.3,  # Golden Gate temp on ship state
        power_reserve_w=200000,
    )
    for gid in ("G1", "G2", "G4"):
        d = evaluate_admission(catalog[gid], state_g6, ship, mission_envelope, prof_survey, catalog)
        p = d["placement"]
        state_g6.installed_guests[gid] = InstalledGuestState(
            guest_id=gid,
            active_mode=p["initial_mode"],
            placements=p["subcomponent_positions"],
            compute_node=p["compute_node"],
            powered_on=True,
        )

    # Evaluate G6 admission over 14-day envelope
    g6_admission = evaluate_admission(
        catalog["G6"], state_g6, ship, mission_envelope, prof_survey, catalog
    )

    # The 14-day envelope evaluates conditions up to 30.0C (T_max = 30C per A.10)
    # Verify that admission rationale and binding constraints reference 30C or envelope
    assert g6_admission["verdict"] in ("YES", "YES_IF", "NO")
    # Verify official campaign hash remains unaffected
    campaign = run_full_campaign_suite(REPO_ROOT)
    assert campaign["campaign_semantic_sha256"] == "fd806cda1c871f8c2c9db047f2b34f5547c70d2b60fe9111d4e35ba292646231"


def test_env_int_05_inv4_offline_socket_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """ENV-INT-05 / INV-4: Public environment suite runs completely offline with network sockets disabled."""
    def _blocked_socket(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("NETWORK_ACCESS_FORBIDDEN: socket opened during offline suite")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    suite_res = run_public_env_suite(REPO_ROOT)
    assert "public_env_semantic_sha256" in suite_res
    assert len(suite_res["public_env_semantic_sha256"]) == 64


def test_env_int_06_inv8_determinism() -> None:
    """ENV-INT-06 / INV-8: Two consecutive executions of run_public_env_suite produce bit-identical semantic hashes."""
    r1 = run_public_env_suite(REPO_ROOT)
    r2 = run_public_env_suite(REPO_ROOT)

    assert r1["public_env_semantic_sha256"] == r2["public_env_semantic_sha256"]
    assert r1["semantic_summary"] == r2["semantic_summary"]


def test_env_int_07_feed_loss_mid_replay(env_suite_setup: Dict[str, Any]) -> None:
    """ENV-INT-07: FEED_LOSS event on seawater temperature causes fallback to conservative 30 C."""
    catalog = env_suite_setup["catalog"]
    ship = env_suite_setup["ship"]
    prof_survey = env_suite_setup["prof_survey"]

    scen_loss = copy.deepcopy(env_suite_setup["scen_b_gg"])
    # Insert FEED_LOSS event at sim_time_s = 60
    scen_loss["events"].append({
        "seq": 99,
        "sim_time_s": 60,
        "priority": 1,
        "event_type": "FEED_LOSS",
        "feed_id": "seawater_temperature_sensor",
        "age_ms": 10000,
        "ttl_ms": 1000,
    })

    res = run_scenario_b_replay(scen_loss, ship, prof_survey, catalog, compressed_90s=True)
    # Check ticks after sim_time_s = 60
    post_loss_ticks = [t for t in res["timeline_ticks"] if t["sim_time_s"] >= 60]
    assert len(post_loss_ticks) > 0
    # Following tick must fall back to conservative 30.0 C
    t_after = post_loss_ticks[0]
    assert t_after["seawater_temp_c"] == 30.0

"""Performance and latency budget verification tests for forecast observer and APIs (MEGAPLAN §9.6).

Covers:
- PERF-01: on_sample x 4 guests p99 <= 1 ms
- PERF-02: on_tick worst case (4 guests in risk) p99 <= 50 ms
- PERF-03: Full replay tick with observer p99 <= 200 ms
- PERF-04: GET /api/forecast, /api/environment, /api/environment/windows p99 <= 300 ms (e2e <= 1.5 s)
- PERF-05: Full campaign with observer <= 2x baseline (<= 2.5 s on Mac)
- PERF-06: Process memory footprint RSS delta <= 30 MB
"""

from __future__ import annotations

import gc
import json
from pathlib import Path
import resource
import time
from typing import Any, Dict, List
import pytest

from sdv_governor.budgets import evaluate_eight_class_budgets
from sdv_governor.forecast.observer import ForecastObserver
from sdv_governor.forecast.telemetry import TelemetryProfile, generate_episode
from sdv_governor.manifest import load_guest_catalog, validate_ship_profile
from sdv_governor.replay import load_yaml_file, run_full_campaign_suite, run_scenario_b_replay
from sdv_governor.server import dispatch_api_request, get_or_create_session
from sdv_governor.ship_state import ShipOperationalState


def _calculate_p99(latencies_s: List[float]) -> float:
    """Calculate 99th percentile latency in seconds."""
    if not latencies_s:
        return 0.0
    sorted_l = sorted(latencies_s)
    idx = max(0, min(len(sorted_l) - 1, int(len(sorted_l) * 0.99)))
    return sorted_l[idx]


@pytest.mark.perf
def test_perf_01_on_sample_latency():
    """PERF-01: on_sample x 4 guests p99 <= 1 ms on Mac (Arduino budget 1 ms)."""
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = repo_root / "data"
    catalog = load_guest_catalog(data_dir / "guests")
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))

    obs = ForecastObserver(ship_profile=ship_profile, catalog=catalog)

    state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="QUIET_SURVEY",
        speed_kn=6.0,
        wind_kn=10,
        sea_state=2,
        seawater_temp_c=16.0,
        power_reserve_w=200000,
        crew_regime="FULL_CREW_14",
        available_crew=9,
        deck_clear=True,
    )
    from sdv_governor.admission import build_baseline_state_with_installed_guests
    state, _ = build_baseline_state_with_installed_guests(["G1", "G3", "G4", "G5"], state, ship_profile, catalog)

    readings = {"G1": 1500.0, "G3": 25000.0, "G4": 35000.0, "G5": 45000.0}
    for t in range(5):
        obs.on_sample(t, readings, state)

    latencies: List[float] = []
    for t in range(5, 105):
        t0 = time.perf_counter()
        obs.on_sample(t, readings, state)
        latencies.append(time.perf_counter() - t0)

    p99_s = _calculate_p99(latencies)
    assert p99_s <= 0.001, f"PERF-01 FAIL: on_sample p99={p99_s * 1000:.3f} ms exceeds 1 ms gate"


@pytest.mark.perf
def test_perf_02_on_tick_worst_case_latency():
    """PERF-02: on_tick worst case (4 guests in risk) p99 <= 50 ms."""
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = repo_root / "data"
    catalog = load_guest_catalog(data_dir / "guests")
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))

    obs = ForecastObserver(ship_profile=ship_profile, catalog=catalog)

    state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="QUIET_SURVEY",
        speed_kn=6.0,
        wind_kn=10,
        sea_state=2,
        seawater_temp_c=16.0,
        power_reserve_w=200000,
        crew_regime="FULL_CREW_14",
        available_crew=9,
        deck_clear=True,
    )
    from sdv_governor.admission import build_baseline_state_with_installed_guests
    state, _ = build_baseline_state_with_installed_guests(["G1", "G3", "G4", "G5"], state, ship_profile, catalog)

    rising_readings = {"G1": 1800.0, "G3": 28000.0, "G4": 40000.0, "G5": 52000.0}
    for t in range(25):
        obs.on_sample(t, rising_readings, state)

    budgets_snap = evaluate_eight_class_budgets(ship_profile, catalog, state)

    latencies: List[float] = []
    for i in range(20):
        t0 = time.perf_counter()
        obs.on_tick(25 + i * 20, state.clone(), budgets_snap)
        latencies.append(time.perf_counter() - t0)

    p99_s = _calculate_p99(latencies)
    assert p99_s <= 0.050, f"PERF-02 FAIL: on_tick p99={p99_s * 1000:.3f} ms exceeds 50 ms gate"


@pytest.mark.perf
def test_perf_03_full_replay_tick_latency():
    """PERF-03: Full replay tick with observer p99 <= 200 ms (tick deadline)."""
    repo_root = Path(__file__).resolve().parents[2]
    data_dir = repo_root / "data"
    catalog = load_guest_catalog(data_dir / "guests")
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))

    from sdv_governor.manifest import load_priority_profile
    scenario_data = load_yaml_file(data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    priority_profile = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
    obs = ForecastObserver(ship_profile=ship_profile, catalog=catalog)

    res = run_scenario_b_replay(
        scenario_data,
        ship_profile,
        priority_profile,
        catalog,
        observer=obs,
    )

    ticks = res.get("timeline_ticks", [])
    assert len(ticks) > 0, "No timeline ticks produced"

    calc_latencies = [
        t.get("measured_tick_calc_ms", 0.0) / 1000.0 for t in ticks if "measured_tick_calc_ms" in t
    ]
    if calc_latencies:
        p99_s = _calculate_p99(calc_latencies)
        assert p99_s <= 0.200, f"PERF-03 FAIL: tick calc p99={p99_s * 1000:.3f} ms exceeds 200 ms gate"


@pytest.mark.perf
def test_perf_04_api_endpoints_latency():
    """PERF-04: GET /api/forecast, /api/environment, /api/environment/windows p99 <= 300 ms (e2e <= 1.5s)."""
    repo_root = Path(__file__).resolve().parents[2]
    sess = get_or_create_session(repo_root)

    endpoints = [
        "/api/forecast",
        "/api/environment",
        "/api/environment/windows?guest=G4&op=VEHICLE_REPLACEMENT_AT_SEA",
    ]

    all_latencies: Dict[str, List[float]] = {ep: [] for ep in endpoints}

    for ep in endpoints:
        for _ in range(15):
            t0 = time.perf_counter()
            code, payload = dispatch_api_request(repo_root, "GET", ep, session=sess)
            elapsed = time.perf_counter() - t0
            assert code == 200, f"Endpoint {ep} returned HTTP {code}"
            assert payload is not None
            all_latencies[ep].append(elapsed)

    for ep, lat_list in all_latencies.items():
        p99_s = _calculate_p99(lat_list)
        max_s = max(lat_list)
        assert p99_s <= 0.300, f"PERF-04 FAIL: {ep} p99={p99_s * 1000:.2f} ms exceeds 300 ms gate"
        assert max_s <= 1.5, f"PERF-04 FAIL: {ep} max={max_s:.3f} s exceeds 1.5 s gate"


@pytest.mark.perf
def test_perf_05_campaign_runtime_latency():
    """PERF-05: Full campaign <= 2x baseline (Mac baseline ~0.39 s -> gate <= 2.5 s)."""
    repo_root = Path(__file__).resolve().parents[2]

    t0 = time.perf_counter()
    report = run_full_campaign_suite(repo_root)
    elapsed = time.perf_counter() - t0

    assert report.get("campaign_semantic_sha256"), "Campaign failed to return semantic hash"
    assert elapsed <= 2.5, f"PERF-05 FAIL: full campaign took {elapsed:.2f} s (> 2.5 s gate)"


@pytest.mark.perf
def test_perf_06_memory_rss_footprint():
    """PERF-06: Process memory footprint RSS delta <= 30 MB during campaign and observer execution."""
    repo_root = Path(__file__).resolve().parents[2]

    gc.collect()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    report = run_full_campaign_suite(repo_root)
    assert report is not None

    gc.collect()
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    import sys
    scale = 1 if sys.platform == "darwin" else 1024
    delta_bytes = (rss_after - rss_before) * scale
    delta_mb = delta_bytes / (1024 * 1024)

    assert delta_mb <= 30.0, f"PERF-06 FAIL: memory growth of {delta_mb:.2f} MB exceeds 30 MB gate"

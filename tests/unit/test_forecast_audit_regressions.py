"""Regression tests for the PR #6 forecast / public-data audit fixes.

Each test pins one defect that was found in the audit so it cannot silently return:
fake fallbacks, unguarded windows, unbounded h*, canned sentences, tick-count hysteresis,
missing STALE/UNAVAILABLE after dropouts, and the fake /api/forecast payload.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from sdv_governor.env_windows import GuardBand, availability_timeline, describe_no_window
from sdv_governor.envdata import cooling_capacity_outlook, load_active_snapshot, load_guard_bands
from sdv_governor.forecast.advisory import AdvisoryStateMachine, advisory_sentence
from sdv_governor.forecast.holt import holt_update, interp_quantile, load_forecast_params
from sdv_governor.forecast.projector import project_guest_physics
from sdv_governor.manifest import load_guest_catalog
from sdv_governor.replay import load_yaml_file
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SNAP_DIR = DATA_DIR / "environment" / "golden_gate" / "20261018T0300Z"
ANCHOR = "2026-10-18T03:00:00Z"


def _catalog():
    return load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")


def _g5_state() -> ShipOperationalState:
    state = ShipOperationalState(sim_time_s=100, vessel_mode="TRANSIT", power_reserve_w=50000, seawater_temp_c=16.0)
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "hangar_roof"},
        compute_node="node_1",
        powered_on=True,
    )
    return state


def test_interp_quantile_is_linear_and_clamped() -> None:
    q = {10: 0.02, 30: 0.06}
    assert interp_quantile(q, 20) == pytest.approx(0.04)
    assert interp_quantile(q, 1) == pytest.approx(0.02)
    assert interp_quantile(q, 60) == pytest.approx(0.06)
    assert interp_quantile({}, 20, default=0.07) == pytest.approx(0.07)


def test_holt_declares_stale_then_unavailable_on_dropout() -> None:
    params = load_forecast_params(DATA_DIR / "forecast" / "holt_params.yaml")
    st = None
    for t in range(60):
        st = holt_update(st, t=t, z=1.0, mode="FULL_SPECTRUM", params=params)
    assert st.status == "OK"
    statuses = {}
    for t in range(60, 75):
        st = holt_update(st, t=t, z=None, mode="FULL_SPECTRUM", params=params)
        statuses[t] = st.status
    assert statuses[60] == "OK"
    assert "STALE" in statuses.values()
    assert statuses[74] == "UNAVAILABLE"


def test_advisory_hysteresis_uses_simulated_seconds_and_records_lead() -> None:
    m = AdvisoryStateMachine("G5")
    assert m.update(0, h_star_hi=30.0) == "WATCH"
    # 20 s cadence: the second risk tick already spans >= 3 s of risk
    assert m.update(20, h_star_hi=30.0) == "WARN"
    assert m.first_warn_t == 20
    assert m.update(40, h_star_hi=30.0, reactive_breach_active=True) == "BREACH_OBSERVED"
    assert m.breach_observed_t == 40
    assert m.lead_s == 20


def test_projector_band_is_bounded_and_honest_when_point_does_not_cross() -> None:
    catalog = _catalog()
    ship = load_yaml_file(DATA_DIR / "ship" / "vessel_baseline.yaml")
    p = project_guest_physics(
        ship_profile=ship,
        catalog=catalog,
        state=_g5_state(),
        guest_id="G5",
        declared_w=45000.0,
        power_trajectory_hi=lambda h: 45000.0 + h * 200.0,
        power_trajectory_mid=lambda h: 45000.0 + h * 10.0,
        power_trajectory_lo=lambda h: 45000.0,
        slope_w_per_s=10.0,
    )
    assert p is not None
    assert p["evals_used"] <= 8
    lo, hi = p["h_star_band_s"]
    assert lo <= p["h_star_s"] <= 60 and hi <= 60
    assert p["point_forecast_crosses"] is False
    assert p["band_open_upper"] is True
    sentence = advisory_sentence(p, guest_name="G5 Test")
    assert "point forecast stays below within 60 s" in sentence
    assert "G5 G5" not in sentence
    assert f">{hi}" in sentence


def test_flat_slope_is_not_printed_as_signed_zero() -> None:
    p = {
        "guest_id": "G5", "slope_w_per_s": -0.3, "threshold_w": 49500, "declared_w": 45000,
        "h_star_s": 40, "h_star_band_s": [30, 50], "witness": {"class": "power", "component": "G5 feeder",
        "projected_w": 49600, "capacity_w": 49500},
    }
    s = advisory_sentence(p, guest_name="Edge")
    assert "flat (|slope| < 0.5 W/s)" in s
    assert "-0 W/s" not in s


def test_guard_bands_derived_only_when_bound_to_snapshot(tmp_path: Path) -> None:
    raw_sha = hashlib.sha256((SNAP_DIR / "env_series.json").read_bytes()).hexdigest()
    derived = load_guard_bands(SNAP_DIR, series_raw_sha256=raw_sha)
    assert derived["status"] == "DERIVED"
    assert derived["guard"]["wind_kn"] > 0 and derived["guard"]["wave_hs_m"] > 0

    mismatch = load_guard_bands(SNAP_DIR, series_raw_sha256="0" * 64)
    assert mismatch["status"] == "DEFAULT"
    assert mismatch["reason"]

    copy = tmp_path / "snap"
    shutil.copytree(SNAP_DIR, copy)
    gb = json.loads((copy / "guard_bands.json").read_text())
    gb["gate_g_env_status"] = "FAIL"
    (copy / "guard_bands.json").write_text(json.dumps(gb))
    assert load_guard_bands(copy, series_raw_sha256=raw_sha)["status"] == "DEFAULT"


def test_active_snapshot_exposes_derived_guard() -> None:
    snap = load_active_snapshot(REPO_ROOT)
    assert snap["guard_bands"]["status"] == "DERIVED"


def test_outlook_is_anchored_over_48h() -> None:
    snap = load_active_snapshot(REPO_ROOT)
    rows = cooling_capacity_outlook(snap["series"], anchor_utc=ANCHOR, loop_degraded=False)
    assert len(rows) == 49
    assert rows[0]["sim_time_s"] == 0 and rows[-1]["sim_time_s"] == 172800


def test_window_timeline_fails_closed_without_wind() -> None:
    catalog = _catalog()
    state = ShipOperationalState(
        tick_seq=0, sim_time_s=0, vessel_mode="NOMINAL_OFFSHORE", speed_kn=6.0, heading_deg=0.0,
        wind_deg=0.0, wind_kn=5, sea_state=1, available_crew=10, crew_count=14,
        deck_clear=True, deck_clear_verified=True,
    )
    series = [{"t_utc": ANCHOR, "sim_time_s": 0, "wave_hs_m": 0.3, "wind_kn": None, "provenance": "DERIVED"}]
    tl = availability_timeline(
        catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", state, catalog, None, series, ANCHOR,
        step_s=3600, horizon_s=0, guard=GuardBand(wind_kn=0.0, wave_hs_m=0.0),
    )
    assert tl[0]["permitted"] is False
    assert any("ENV_UNAVAILABLE" in r for r in tl[0]["blocking_reasons"])
    nw = describe_no_window(tl, min_duration_s=1800)
    assert nw["start_s"] is None and "no valid operational window" in nw["reason"]


def test_forecast_view_is_real_replay_and_labelled() -> None:
    from sdv_governor.server import GovernorSession

    view = GovernorSession(REPO_ROOT).forecast_view()
    assert view["enabled"] is True
    assert view["source"].startswith("REPLAY:")
    assert view["telemetry_provenance"] == "SIMULATED"
    assert view["advisory_only"] is True
    warns = [a for a in view["alerts"] if a["state"] == "WARN"]
    assert warns, "replay of the recorded G5 ramp must raise at least one WARN"
    assert all(a["h_star_s"] <= 60 for a in warns)

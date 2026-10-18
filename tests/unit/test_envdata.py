"""Unit tests for Environmental Runtime Engine (envdata.py).

Implements ENV-UNIT-01 through ENV-UNIT-12 and ENV-UNIT-16.
Strictly offline, deterministic, fail-closed (INV-4, INV-6, INV-8).
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from unittest.mock import patch

import pytest

from sdv_governor.models import StrictValidationError
from sdv_governor.ship_state import FeedReading, ShipOperationalState
from sdv_governor.budgets import compute_cooling_water_capacity_w
from sdv_governor.envdata import (
    EnvSample,
    wave_height_to_sea_state_wmo,
    mps_to_kn,
    parse_ndbc_table,
    validate_env_series,
    snapshot_sha256,
    load_active_snapshot,
    sample_at,
    build_env_feed_readings,
    cooling_capacity_outlook,
    env_events_for_scenario,
)


# ---------------------------------------------------------------------------
# ENV-UNIT-01: WMO 3700 Sea State Code Table Boundary Tests
# ---------------------------------------------------------------------------
def test_wmo_3700_boundary_conditions() -> None:
    """ENV-UNIT-01: WMO 3700 boundaries (f(0.10)=1, f(0.11)=2, f(0.50)=2, f(1.25)=3,

    f(1.26)=4, f(1.42)=4, f(2.50)=4, f(2.51)=5, f(0.0)=0).
    """
    assert wave_height_to_sea_state_wmo(0.0) == 0
    assert wave_height_to_sea_state_wmo(0.10) == 1
    assert wave_height_to_sea_state_wmo(0.11) == 2
    assert wave_height_to_sea_state_wmo(0.50) == 2
    assert wave_height_to_sea_state_wmo(1.25) == 3
    assert wave_height_to_sea_state_wmo(1.26) == 4
    assert wave_height_to_sea_state_wmo(1.42) == 4
    assert wave_height_to_sea_state_wmo(2.50) == 4
    assert wave_height_to_sea_state_wmo(2.51) == 5

    # Additional standard boundaries
    assert wave_height_to_sea_state_wmo(4.00) == 5
    assert wave_height_to_sea_state_wmo(4.01) == 6
    assert wave_height_to_sea_state_wmo(6.00) == 6
    assert wave_height_to_sea_state_wmo(6.01) == 7
    assert wave_height_to_sea_state_wmo(9.00) == 7
    assert wave_height_to_sea_state_wmo(9.01) == 8
    assert wave_height_to_sea_state_wmo(14.00) == 8
    assert wave_height_to_sea_state_wmo(14.01) == 9


# ---------------------------------------------------------------------------
# ENV-UNIT-02: Unit Conversion m/s to kn (ASM-ENV-UNITS)
# ---------------------------------------------------------------------------
def test_mps_to_kn_conversion() -> None:
    """ENV-UNIT-02: m/s -> kn conversion (8.0 -> 15.55 +- 0.01; 10.3 -> 20.02 +- 0.01)."""
    assert mps_to_kn(8.0) == pytest.approx(15.55, abs=0.01)
    assert mps_to_kn(10.3) == pytest.approx(20.02, abs=0.01)


# ---------------------------------------------------------------------------
# ENV-UNIT-03: NOAA NDBC Meteorological Table Parser
# ---------------------------------------------------------------------------
def test_ndbc_parser() -> None:
    """ENV-UNIT-03: NDBC parser: 'MM' -> None, 2-line header, descending -> ascending order, by column name."""
    sample_raw_ndbc = (
        "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS  PTDY  TIDE\n"
        "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi   hPa    ft\n"
        "2026 10 18 03 00 320  8.0 10.0   1.6    13    MM  280 1013.2  14.5  16.1    MM   MM    MM    MM\n"
        "2026 10 18 02 00 310  7.5  9.5   1.5    12    MM  275 1013.0  14.2  16.0    MM   MM    MM    MM\n"
        "2026 10 18 01 00 300  7.0  9.0    MM    12    MM  270 1012.8  14.0  15.9    MM   MM    MM    MM\n"
    )
    rows = parse_ndbc_table(sample_raw_ndbc)
    assert len(rows) == 3

    # Check ascending chronological order (reversed from file order)
    assert rows[0]["t_utc"] == "2026-10-18T01:00:00Z"
    assert rows[1]["t_utc"] == "2026-10-18T02:00:00Z"
    assert rows[2]["t_utc"] == "2026-10-18T03:00:00Z"

    # Check MM -> None
    assert rows[0]["WVHT"] is None
    assert rows[2]["WVHT"] == 1.6
    assert rows[2]["APD"] is None
    assert rows[2]["WSPD"] == 8.0
    assert rows[2]["WTMP"] == 16.1


# ---------------------------------------------------------------------------
# ENV-UNIT-04: Causal Linear Interpolation
# ---------------------------------------------------------------------------
def test_causal_linear_interpolation() -> None:
    """ENV-UNIT-04: Linear interpolation between 16.0 (h0) and 17.0 (h1) at +1800s -> 16.5;

    Strictly causal: does not use future rows for observations.
    """
    anchor = "2026-10-18T03:00:00Z"
    forecast_series = [
        {"t_utc": "2026-10-18T03:00:00Z", "lead_s": 0, "sst_c": 16.0, "provenance": "DERIVED", "source_id": "openmeteo"},
        {"t_utc": "2026-10-18T04:00:00Z", "lead_s": 3600, "sst_c": 17.0, "provenance": "DERIVED", "source_id": "openmeteo"},
    ]
    # Forecast query at sim_time_s = 1800 (midpoint)
    sample = sample_at(forecast_series, 1800, anchor)
    assert sample.sst_c == pytest.approx(16.5, abs=0.01)
    assert sample.sim_time_s == 1800
    assert sample.t_utc == "2026-10-18T03:30:00Z"

    # Strictly causal check with observations:
    # Observations at t=0 and t=3600. When queried at t=1800, t=3600 is in the future and cannot be used!
    obs_series = [
        {"t_utc": "2026-10-18T03:00:00Z", "sst_c": 15.8, "provenance": "MEASURED", "source_id": "NDBC-46237"},
        {"t_utc": "2026-10-18T04:00:00Z", "sst_c": 16.4, "provenance": "MEASURED", "source_id": "NDBC-46237"},
    ]
    sample_obs = sample_at(obs_series, 1800, anchor)
    assert sample_obs.provenance == "MEASURED"
    # Must use only the observation <= 1800s (i.e. t=0, 15.8C), NOT interpolating from future observation at t=3600s
    assert sample_obs.sst_c == 15.8


# ---------------------------------------------------------------------------
# ENV-UNIT-05: Sea State Recalculated from Interpolated Hs
# ---------------------------------------------------------------------------
def test_sea_state_from_interpolated_hs() -> None:
    """ENV-UNIT-05: Hs 1.20 -> 1.30 at midpoint = 1.25 -> SS 3 (never interpolates the discrete category)."""
    anchor = "2026-10-18T03:00:00Z"
    series = [
        {"t_utc": "2026-10-18T03:00:00Z", "lead_s": 0, "wave_hs_m": 1.20, "sea_state_wmo": 3, "provenance": "DERIVED"},
        {"t_utc": "2026-10-18T04:00:00Z", "lead_s": 3600, "wave_hs_m": 1.30, "sea_state_wmo": 4, "provenance": "DERIVED"},
    ]
    sample = sample_at(series, 1800, anchor)
    assert sample.wave_hs_m == pytest.approx(1.25, abs=0.01)
    # 1.25 m is <= 1.25 m, which falls in WMO 3 (Slight), NOT 4 (Moderate)
    assert sample.sea_state_wmo == 3


# ---------------------------------------------------------------------------
# ENV-UNIT-06: Non-Monotonic Timestamps Fail Closed
# ---------------------------------------------------------------------------
def test_validate_env_series_non_monotonic_time() -> None:
    """ENV-UNIT-06: Non-monotonic or duplicate timestamps raise StrictValidationError('ENV_SNAPSHOT_INVALID')."""
    bad_payload = {
        "schema_version": "sdv-env-series/1.0",
        "site_id": "golden_gate",
        "anchor_utc": "2026-10-18T03:00:00Z",
        "series": {
            "forecast_GG_BAR": [
                {"t_utc": "2026-10-18T03:00:00Z", "sst_c": 16.0},
                {"t_utc": "2026-10-18T02:00:00Z", "sst_c": 16.1},  # Decreasing!
            ]
        },
    }
    with pytest.raises(StrictValidationError) as exc:
        validate_env_series(bad_payload)
    assert exc.value.code == "ENV_SNAPSHOT_INVALID"

    # Duplicate timestamp
    dup_payload = copy.deepcopy(bad_payload)
    dup_payload["series"]["forecast_GG_BAR"][1]["t_utc"] = "2026-10-18T03:00:00Z"
    with pytest.raises(StrictValidationError) as exc_dup:
        validate_env_series(dup_payload)
    assert exc_dup.value.code == "ENV_SNAPSHOT_INVALID"


# ---------------------------------------------------------------------------
# ENV-UNIT-07: NaN, Inf, and Out-of-Range Rejection
# ---------------------------------------------------------------------------
def test_validate_env_series_nan_inf_and_out_of_range() -> None:
    """ENV-UNIT-07: NaN, inf, or out of range (sst_c=55) rejected with ENV_SNAPSHOT_INVALID."""
    base_payload = {
        "schema_version": "sdv-env-series/1.0",
        "site_id": "golden_gate",
        "anchor_utc": "2026-10-18T03:00:00Z",
        "series": {
            "forecast_GG_BAR": [
                {"t_utc": "2026-10-18T03:00:00Z", "sst_c": 16.0, "wave_hs_m": 1.4, "wind_kn": 15.0}
            ]
        },
    }

    # sst_c = 55.0 (out of physical range [-2, 40])
    p1 = copy.deepcopy(base_payload)
    p1["series"]["forecast_GG_BAR"][0]["sst_c"] = 55.0
    with pytest.raises(StrictValidationError) as exc1:
        validate_env_series(p1)
    assert exc1.value.code == "ENV_SNAPSHOT_INVALID"

    # NaN
    p2 = copy.deepcopy(base_payload)
    p2["series"]["forecast_GG_BAR"][0]["sst_c"] = float("nan")
    with pytest.raises(StrictValidationError) as exc2:
        validate_env_series(p2)
    assert exc2.value.code == "ENV_SNAPSHOT_INVALID"

    # Inf
    p3 = copy.deepcopy(base_payload)
    p3["series"]["forecast_GG_BAR"][0]["wave_hs_m"] = float("inf")
    with pytest.raises(StrictValidationError) as exc3:
        validate_env_series(p3)
    assert exc3.value.code == "ENV_SNAPSHOT_INVALID"

    # gust_kn < wind_kn
    p4 = copy.deepcopy(base_payload)
    p4["series"]["forecast_GG_BAR"][0]["wind_kn"] = 25.0
    p4["series"]["forecast_GG_BAR"][0]["gust_kn"] = 20.0
    with pytest.raises(StrictValidationError) as exc4:
        validate_env_series(p4)
    assert exc4.value.code == "ENV_SNAPSHOT_INVALID"


# ---------------------------------------------------------------------------
# ENV-UNIT-08: Lead Time Over 48 Hours Returns UNAVAILABLE
# ---------------------------------------------------------------------------
def test_sample_at_lead_over_48h() -> None:
    """ENV-UNIT-08: Lead > 48 h (172800 s) -> EnvSample.provenance == 'UNAVAILABLE'."""
    anchor = "2026-10-18T03:00:00Z"
    series = [
        {"t_utc": "2026-10-18T03:00:00Z", "lead_s": 0, "sst_c": 16.3, "provenance": "DERIVED"},
        {"t_utc": "2026-10-18T04:00:00Z", "lead_s": 3600, "sst_c": 16.4, "provenance": "DERIVED"},
    ]
    sample = sample_at(series, 172801, anchor)
    assert sample.provenance == "UNAVAILABLE"
    assert sample.sst_c is None

    sample_huge = sample_at(series, 300000, anchor)
    assert sample_huge.provenance == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# ENV-UNIT-09: TTL Expiration Triggers Conservative 30 °C Fallback
# ---------------------------------------------------------------------------
def test_feed_ttl_expiration_conservative_fallback() -> None:
    """ENV-UNIT-09: Expired feed TTL in vessel state falls back to 30 C conservative fallback."""
    state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="NOMINAL_OFFSHORE",
        speed_kn=6.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=15,
        sea_state=3,
        seawater_temp_c=16.3,
        seawater_temp_is_stale=False,
    )
    # Fresh feed
    feed = FeedReading(
        feed_id="seawater_temperature_sensor",
        value=16.3,
        unit="degC",
        provenance="DERIVED",
        source_id="openmeteo:marine:GG_BAR",
        source_time_ns=1_000_000_000,
        ttl_ms=5_400_000,
        age_ms=1000,
        fallback_value=30.0,
        fallback_reason="A.10 conservative 30C",
    )
    state.feeds["seawater_temperature_sensor"] = feed
    temp_c, note = state.effective_seawater_temp_c()
    assert temp_c == pytest.approx(16.3)
    assert "FRESH" in note

    # Stale feed (age_ms > ttl_ms: 6,000,000 ms > 5,400,000 ms)
    stale_feed = copy.deepcopy(feed)
    stale_feed.age_ms = 6_000_000
    state.feeds["seawater_temperature_sensor"] = stale_feed

    temp_c_stale, note_stale = state.effective_seawater_temp_c()
    assert temp_c_stale == 30.0
    assert note_stale == "UNAVAILABLE:CONSERVATIVE_30C_FALLBACK"


# ---------------------------------------------------------------------------
# ENV-UNIT-10: FeedReading Built from Snapshot
# ---------------------------------------------------------------------------
def test_feed_reading_from_snapshot() -> None:
    """ENV-UNIT-10: FeedReading from snapshot: provenance=='DERIVED', source_id starts with 'openmeteo:', validate() succeeds."""
    sample = EnvSample(
        t_utc="2026-10-18T03:00:00Z",
        sim_time_s=0,
        sst_c=16.3,
        wave_hs_m=1.42,
        sea_state_wmo=4,
        wind_kn=16.1,
        gust_kn=19.8,
        wind_dir_deg=296.0,
        provenance="DERIVED",
        source_id="openmeteo:marine+forecast:GG_BAR",
        lead_s=0,
    )
    feeds = build_env_feed_readings(sample, sim_time_s=0)
    assert "seawater_temperature_sensor" in feeds
    reading = feeds["seawater_temperature_sensor"]

    assert reading.provenance == "DERIVED"
    assert reading.source_id.startswith("openmeteo:")
    assert reading.value == 16.3
    assert reading.ttl_ms == 5_400_000
    assert reading.fallback_value == 30.0
    assert reading.fallback_reason == "A.10 conservative 30C"

    # Must pass validation without error
    reading.validate()


# ---------------------------------------------------------------------------
# ENV-UNIT-11: MEASURED Feed with Synthetic source_id Rejected
# ---------------------------------------------------------------------------
def test_feed_reading_measured_synthetic_rejected() -> None:
    """ENV-UNIT-11: MEASURED with synthetic source_id causes validate() to raise MEASURED_WITHOUT_PHYSICAL_SOURCE_ID."""
    feed = FeedReading(
        feed_id="seawater_temperature_sensor",
        value=16.3,
        unit="degC",
        provenance="MEASURED",
        source_id="synthetic",
        source_time_ns=1_000_000_000,
        ttl_ms=5_400_000,
        fallback_value=30.0,
        fallback_reason="A.10 conservative 30C",
    )
    with pytest.raises(StrictValidationError) as exc:
        feed.validate()
    assert exc.value.code == "MEASURED_WITHOUT_PHYSICAL_SOURCE_ID"

    # Also test series validation rejection
    bad_series = {
        "schema_version": "sdv-env-series/1.0",
        "site_id": "golden_gate",
        "anchor_utc": "2026-10-18T03:00:00Z",
        "series": {
            "test_series": [
                {"t_utc": "2026-10-18T03:00:00Z", "sst_c": 16.0, "provenance": "MEASURED", "source_id": "synthetic"}
            ]
        },
    }
    with pytest.raises(StrictValidationError) as exc2:
        validate_env_series(bad_series)
    assert exc2.value.code == "MEASURED_WITHOUT_PHYSICAL_SOURCE_ID"


# ---------------------------------------------------------------------------
# ENV-UNIT-12: Cooling Capacity Outlook Uses Physics Formula (Mocked + Spy)
# ---------------------------------------------------------------------------
def test_cooling_capacity_outlook() -> None:
    """ENV-UNIT-12: cooling_capacity_outlook: 16.3 C -> 150 444 W +- 1; 30 C -> 120 000;

    degraded loop x0.6; verifies call to compute_cooling_water_capacity_w.
    """
    series = [
        {"t_utc": "2026-10-18T03:00:00Z", "sim_time_s": 0, "sst_c": 16.3},
        {"t_utc": "2026-10-18T04:00:00Z", "sim_time_s": 3600, "sst_c": 30.0},
    ]

    with patch("sdv_governor.envdata.compute_cooling_water_capacity_w", wraps=compute_cooling_water_capacity_w) as spy:
        outlook = cooling_capacity_outlook(series, loop_degraded=False)
        assert spy.call_count == 2
        assert len(outlook) == 2

        # 16.3 C -> 150 444 W +- 1
        assert outlook[0]["cooling_nominal_w"] == pytest.approx(150444, abs=1)
        assert outlook[0]["cooling_effective_w"] == pytest.approx(150444, abs=1)

        # 30.0 C -> 120 000 W
        assert outlook[1]["cooling_nominal_w"] == 120000
        assert outlook[1]["cooling_effective_w"] == 120000

    # Test loop_degraded=True -> 0.60x
    outlook_degraded = cooling_capacity_outlook(series, loop_degraded=True)
    assert outlook_degraded[0]["cooling_effective_w"] == int(round(150444 * 0.60))
    assert outlook_degraded[1]["cooling_effective_w"] == int(round(120000 * 0.60))


# ---------------------------------------------------------------------------
# ENV-UNIT-16: Snapshot Hash Stability and Tamper Detection
# ---------------------------------------------------------------------------
def test_snapshot_sha256_stability_and_tamper_detection(tmp_path: Path) -> None:
    """ENV-UNIT-16: snapshot_sha256 is stable; modifying 1 byte in env_series.json causes load_active_snapshot to reject with ENV_SNAPSHOT_INVALID."""
    site_dir = tmp_path / "data" / "environment" / "golden_gate"
    snap_name = "20261018T0300Z"
    snap_dir = site_dir / snap_name
    snap_dir.mkdir(parents=True)

    # ACTIVE file pointing to snapshot
    (site_dir / "ACTIVE").write_text(snap_name, encoding="utf-8")

    series_data = {
        "schema_version": "sdv-env-series/1.0",
        "site_id": "golden_gate",
        "anchor_utc": "2026-10-18T03:00:00Z",
        "series": {
            "forecast_GG_BAR": [
                {"t_utc": "2026-10-18T03:00:00Z", "lead_s": 0, "sst_c": 16.3, "wave_hs_m": 1.42, "sea_state_wmo": 4, "wind_kn": 16.1}
            ]
        },
    }
    series_bytes = json.dumps(series_data, sort_keys=True).encode("utf-8")
    (snap_dir / "env_series.json").write_bytes(series_bytes)

    series_sha = hashlib.sha256(series_bytes).hexdigest()
    manifest_data = {
        "schema_version": "sdv-env-snapshot/1.0",
        "site_id": "golden_gate",
        "anchor_utc": "2026-10-18T03:00:00Z",
        "normalized_sha256": series_sha,
    }
    (snap_dir / "manifest.json").write_text(json.dumps(manifest_data, sort_keys=True), encoding="utf-8")

    # Hash stability
    h1 = snapshot_sha256(snap_dir)
    h2 = snapshot_sha256(snap_dir)
    assert h1 == h2
    assert len(h1) == 64

    # Successfully loaded
    loaded = load_active_snapshot(tmp_path, site_id="golden_gate")
    assert loaded["snapshot_sha256"] == h1

    # Tamper with 1 byte of env_series.json
    tampered_bytes = bytearray(series_bytes)
    tampered_bytes[-1] = (tampered_bytes[-1] + 1) % 256
    (snap_dir / "env_series.json").write_bytes(bytes(tampered_bytes))

    with pytest.raises(StrictValidationError) as exc:
        load_active_snapshot(tmp_path, site_id="golden_gate")
    assert exc.value.code == "ENV_SNAPSHOT_INVALID"

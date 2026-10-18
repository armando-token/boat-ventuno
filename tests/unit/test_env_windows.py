"""Unit tests for Deck Availability Windows (env_windows.py).

Implements ENV-UNIT-13 through ENV-UNIT-15.
Strictly offline, deterministic (INV-4, INV-7, INV-8).
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List

import pytest

from sdv_governor.manifest import load_guest_catalog
from sdv_governor.ship_state import ShipOperationalState
from sdv_governor.env_windows import (
    OPS,
    GuardBand,
    availability_timeline,
    find_next_window,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"


def _make_timeline(
    permitted_list: List[bool],
    hs_list: List[float],
    wind_kn: float = 14.0,
    step_s: int = 3600,
    start_utc: str = "2026-10-18T03:00:00Z",
) -> List[Dict[str, Any]]:
    base_dt = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
    tl: List[Dict[str, Any]] = []
    for idx, (p, hs) in enumerate(zip(permitted_list, hs_list)):
        sim_s = idx * step_s
        dt = base_dt + timedelta(seconds=sim_s)
        tl.append({
            "sim_time_s": sim_s,
            "t_utc": dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "permitted": p,
            "blocking_reasons": [] if p else ["SEA_STATE > 3"],
            "wind_kn": int(round(wind_kn)),
            "sea_state": 3 if hs <= 1.25 else 4,
            "wave_hs_m": hs,
            "raw_wave_hs_m": hs,
            "raw_wind_kn": wind_kn,
            "provenance": "DERIVED",
            "source_id": "openmeteo:marine+forecast:GG_BAR",
        })
    return tl


# ---------------------------------------------------------------------------
# ENV-UNIT-13: Synthetic Timeline Window Search
# ---------------------------------------------------------------------------
def test_find_next_window_synthetic_timeline() -> None:
    """ENV-UNIT-13: Timeline [F,F,T,T,T,F,T] with min_duration=2h starts at index 2 and lasts 3h; with min=4h returns None."""
    bools = [False, False, True, True, True, False, True]
    hs = [1.5, 1.5, 1.1, 1.1, 1.1, 1.6, 1.1]
    tl = _make_timeline(bools, hs, step_s=3600)

    # 2 hours minimum duration: contiguous run [2, 3, 4] is 3 hours >= 2 hours
    w_2h = find_next_window(tl, min_duration_s=2 * 3600, now_sim_s=0, resolution_s=3600)
    assert w_2h is not None
    assert w_2h["start_s"] == 2 * 3600  # Index 2 = 7200 s
    assert w_2h["duration_s"] == 3 * 3600  # 3 hours = 10800 s
    assert w_2h["end_s"] == 5 * 3600  # 18000 s
    assert w_2h["start_utc"] == "2026-10-18T05:00:00Z"

    # 4 hours minimum duration: run [2, 3, 4] is 3h < 4h, and index 6 is 1h < 4h -> None
    w_4h = find_next_window(tl, min_duration_s=4 * 3600, now_sim_s=0, resolution_s=3600)
    assert w_4h is None


# ---------------------------------------------------------------------------
# ENV-UNIT-14: Un-Canned Window Phrase (INV-7)
# ---------------------------------------------------------------------------
def test_window_phrase_uniqueness_and_content() -> None:
    """ENV-UNIT-14: Changing Hs from 1.18 to 1.10 changes the text; contains start_utc, Hs value, and source."""
    bools = [True, True]
    tl_118 = _make_timeline(bools, [1.18, 1.18], step_s=3600, start_utc="2026-10-18T03:00:00Z")
    tl_110 = _make_timeline(bools, [1.10, 1.10], step_s=3600, start_utc="2026-10-18T03:00:00Z")

    w1 = find_next_window(tl_118, min_duration_s=3600)
    w2 = find_next_window(tl_110, min_duration_s=3600)

    assert w1 is not None and w2 is not None

    # Text must differ when physical witness value changes
    assert w1["reason"] != w2["reason"]
    assert w1["phrase"] != w2["phrase"]

    # Must contain Hs value
    assert "1.18" in w1["reason"]
    assert "1.10" in w2["reason"]

    # Must contain start_utc
    assert "2026-10-18T03:00:00Z" in w1["reason"]
    assert "2026-10-18T03:00:00Z" in w1["phrase"]

    # Must contain source
    assert "openmeteo" in w1["reason"].lower() or "gg_bar" in w1["reason"].lower()


# ---------------------------------------------------------------------------
# ENV-UNIT-15: Guard Band Closes Marginal Window
# ---------------------------------------------------------------------------
def test_guard_band_closure() -> None:
    """ENV-UNIT-15: Hs 1.20 + guard 0.15 = 1.35 -> SS 4 -> operation requiring SS <= 3 is blocked."""
    catalog = load_guest_catalog(DATA_DIR / "guests", errata_path=DATA_DIR / "overrides" / "brief_errata.yaml")
    g4 = catalog["G4"]

    base_state = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="NOMINAL_OFFSHORE",
        speed_kn=6.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=14,
        sea_state=3,
        available_crew=10,
        crew_count=14,
        deck_clear=True,
        deck_clear_verified=True,
    )

    series = [
        {"t_utc": "2026-10-18T03:00:00Z", "sim_time_s": 0, "wave_hs_m": 1.20, "wind_kn": 14.0, "provenance": "DERIVED"}
    ]

    # Without guard band: Hs 1.20 -> SS 3 -> G4 VEHICLE_REPLACEMENT_AT_SEA is permitted (max_sea_state = 3)
    no_guard = GuardBand(wind_kn=0.0, wave_hs_m=0.0)
    tl_open = availability_timeline(
        g4,
        "VEHICLE_REPLACEMENT_AT_SEA",
        base_state,
        catalog,
        None,
        series,
        "2026-10-18T03:00:00Z",
        step_s=3600,
        horizon_s=0,
        guard=no_guard,
    )
    assert len(tl_open) == 1
    assert tl_open[0]["sea_state"] == 3
    assert tl_open[0]["permitted"] is True

    # With safety guard band: Hs 1.20 + 0.15 = 1.35 -> SS 4 -> window is CLOSED (not permitted)
    with_guard = GuardBand(wind_kn=0.0, wave_hs_m=0.15)
    tl_closed = availability_timeline(
        g4,
        "VEHICLE_REPLACEMENT_AT_SEA",
        base_state,
        catalog,
        None,
        series,
        "2026-10-18T03:00:00Z",
        step_s=3600,
        horizon_s=0,
        guard=with_guard,
    )
    assert len(tl_closed) == 1
    assert tl_closed[0]["wave_hs_m"] == 1.35
    assert tl_closed[0]["sea_state"] == 4
    assert tl_closed[0]["permitted"] is False
    assert any("sea_state" in r.lower() for r in tl_closed[0]["blocking_reasons"])

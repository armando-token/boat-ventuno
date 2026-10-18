"""Operational deck availability timeline and window computation from environmental forecasts.

Part of E1: Public real environmental data runtime engine (Golden Gate).
Uses ONLY Python standard library + PyYAML (INV-9).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import StrictValidationError
from .ship_state import ShipOperationalState
from .manifest import GuestManifest
from .scheduling import evaluate_a_prime_window
from .envdata import sample_at, wave_height_to_sea_state_wmo


OPS: Tuple[str, ...] = (
    "LAUNCH_RECOVERY",
    "VEHICLE_REPLACEMENT_AT_SEA",
    "AT_SEA_INSTALL",
)


@dataclass(frozen=True)
class GuardBand:
    """Conservative safety margins derived from backtesting (§5.10)."""

    wind_kn: float = 3.0
    wave_hs_m: float = 0.15
    sst_c: float = 0.0

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)


def availability_timeline(
    guest: GuestManifest,
    operation_type: str,
    base_state: ShipOperationalState,
    catalog: Dict[str, GuestManifest],
    ship_profile: Optional[Dict[str, Any]],
    env_series: Any,
    anchor_utc: str,
    *,
    step_s: int = 900,
    horizon_s: int = 172800,
    guard: Optional[GuardBand] = None,
) -> List[Dict[str, Any]]:
    """Compute operational deck availability timeline across a forecast horizon (§5.8.3).

    For each step:
    - Clones base_state.
    - Sets speed_kn to center of guest's operational speed window (ASM-ENV-SPEED-ASSUMED).
    - Applies guard bands to forecast wind and wave height before rounding / sea state classification.
    - Calls scheduling.evaluate_a_prime_window without modifying it.
    - Returns timeline dicts.
    """
    if guard is None:
        guard = GuardBand(wind_kn=3.0, wave_hs_m=0.15, sst_c=0.0)

    # Determine center of operation speed window (ASM-ENV-SPEED-ASSUMED)
    if operation_type == "LAUNCH_RECOVERY":
        min_s = guest.ship_state_constraints.min_speed_kn_for_operation
        max_s = guest.ship_state_constraints.max_speed_kn_for_operation
        center_speed = (min_s + max_s) / 2.0
    elif operation_type == "VEHICLE_REPLACEMENT_AT_SEA":
        vr = guest.vehicle_replacement
        center_speed = (vr.min_speed_kn + vr.max_speed_kn) / 2.0
    elif operation_type == "AT_SEA_INSTALL":
        center_speed = 6.0  # Center of [4.0, 8.0] kn per Annex A.4
    else:
        center_speed = float(base_state.speed_kn)

    timeline: List[Dict[str, Any]] = []

    for sim_s in range(0, horizon_s + 1, step_s):
        sample = sample_at(env_series, sim_s, anchor_utc)
        st = base_state.clone()
        st.speed_kn = center_speed
        st.ship_speed_kn = center_speed

        raw_hs = sample.wave_hs_m if sample.wave_hs_m is not None else 0.0
        eff_hs = raw_hs + guard.wave_hs_m
        eff_ss = wave_height_to_sea_state_wmo(eff_hs)

        raw_wind = sample.wind_kn if sample.wind_kn is not None else 0.0
        eff_wind = raw_wind + guard.wind_kn

        st.wind_kn = int(round(eff_wind))
        st.sea_state = int(eff_ss)

        # Call official evaluation function untouched
        res = evaluate_a_prime_window(guest, operation_type, st, catalog, ship_profile)

        timeline.append({
            "sim_time_s": sim_s,
            "t_utc": sample.t_utc,
            "permitted": res["permitted"],
            "blocking_reasons": res.get("blocking_reasons", []),
            "wind_kn": st.wind_kn,
            "sea_state": st.sea_state,
            "wave_hs_m": round(eff_hs, 2),
            "raw_wave_hs_m": sample.wave_hs_m,
            "raw_wind_kn": sample.wind_kn,
            "provenance": sample.provenance,
            "source_id": sample.source_id,
        })

    return timeline


def find_next_window(
    timeline: List[Dict[str, Any]],
    *,
    min_duration_s: int,
    now_sim_s: int = 0,
    source_id: str = "openmeteo:marine+forecast:GG_BAR",
    guard_band: Optional[GuardBand] = None,
    resolution_s: int = 3600,
) -> Optional[Dict[str, Any]]:
    """Find first contiguous window with permitted=True of duration >= min_duration_s and start >= now_sim_s (§5.8.3).

    Returns dictionary suitable for explanations.build_decision_rationale and API response.
    """
    if not timeline:
        return None

    step_dt = resolution_s
    if len(timeline) >= 2:
        step_dt = timeline[1]["sim_time_s"] - timeline[0]["sim_time_s"]
        if step_dt <= 0:
            step_dt = resolution_s

    # Filter to eligible entries
    eligible = [item for item in timeline if item["sim_time_s"] >= now_sim_s]
    if not eligible:
        return None

    current_run: List[Dict[str, Any]] = []
    runs: List[List[Dict[str, Any]]] = []

    for item in eligible:
        if item.get("permitted", False):
            current_run.append(item)
        else:
            if current_run:
                runs.append(current_run)
                current_run = []
    if current_run:
        runs.append(current_run)

    for run in runs:
        # Contiguous span duration
        duration_s = len(run) * step_dt
        if duration_s >= min_duration_s:
            start_s = run[0]["sim_time_s"]
            end_s = start_s + duration_s
            start_utc = run[0].get("t_utc", "")
            end_utc = run[-1].get("t_utc", "")

            first_pt = run[0]
            hs_val = first_pt.get("raw_wave_hs_m")
            if hs_val is None:
                hs_val = first_pt.get("wave_hs_m", 0.0)
            wind_val = first_pt.get("raw_wind_kn")
            if wind_val is None:
                wind_val = first_pt.get("wind_kn", 0.0)
            ss_val = first_pt.get("sea_state", 0)

            guard_hs = guard_band.wave_hs_m if guard_band else 0.15
            guard_wind = guard_band.wind_kn if guard_band else 3.0
            source_tag = first_pt.get("source_id") or source_id

            hours_len = duration_s // 3600
            dur_str = f"{hours_len} h" if hours_len > 0 else f"{duration_s} s"

            reason = (
                f"starts at {start_utc} ({dur_str} long: sea state {ss_val} "
                f"(Hs {hs_val:.2f} m + {guard_hs:.2f} m guard <= 1.25 m) "
                f"and wind {int(round(wind_val))} kn + {int(round(guard_wind))} kn guard <= 20 kn; "
                f"source {source_tag})"
            )

            phrase = (
                f"next valid operational window starts at T+{start_s}s "
                f"({start_utc}, {dur_str} long: sea state {ss_val} "
                f"(Hs {hs_val:.2f} m + {guard_hs:.2f} m guard <= 1.25 m) "
                f"and wind {int(round(wind_val))} kn + {int(round(guard_wind))} kn guard <= 20 kn; "
                f"source {source_tag})"
            )

            return {
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": duration_s,
                "start_utc": start_utc,
                "end_utc": end_utc,
                "reason": reason,
                "phrase": phrase,
                "resolution_s": resolution_s,
                "source": source_tag,
                "guard_band": guard_band.to_dict() if guard_band else GuardBand().to_dict(),
            }

    return None

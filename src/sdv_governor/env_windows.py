"""Operational deck availability timeline and window computation from environmental forecasts.

Part of E1: Public real environmental data runtime engine (Golden Gate).
Uses ONLY Python standard library + PyYAML (INV-9).
"""

from __future__ import annotations

import copy
import math
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

    wind_kn: float = 5.0
    wave_hs_m: float = 0.5
    sst_c: float = 1.0
    status: str = "DEFAULT"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def coerce(cls, guard: Any) -> "GuardBand":
        """Accept a GuardBand, a load_guard_bands() dict, or a plain {wind_kn, wave_hs_m, sst_c} dict."""
        if isinstance(guard, GuardBand):
            return guard
        if guard is None:
            return cls()
        if isinstance(guard, dict):
            inner = guard.get("guard", guard)
            return cls(
                wind_kn=float(inner.get("wind_kn", inner.get("guard_wind_kn", cls.wind_kn))),
                wave_hs_m=float(inner.get("wave_hs_m", inner.get("guard_hs_m", cls.wave_hs_m))),
                sst_c=float(inner.get("sst_c", inner.get("guard_sst_c", cls.sst_c))),
                status=str(guard.get("status", "CUSTOM")),
            )
        raise StrictValidationError("ENV_GUARD_INVALID", f"Unsupported guard band type {type(guard).__name__}", "guard")


def operation_limits(guest: GuestManifest, operation_type: str) -> Dict[str, Any]:
    """Weather/speed limits that scheduling.evaluate_a_prime_window applies for this guest/operation."""
    if operation_type == "LAUNCH_RECOVERY":
        sc = guest.ship_state_constraints
        return {
            "max_sea_state": int(sc.max_sea_state_for_operation),
            "max_wind_kn": float(sc.max_wind_kn_for_operation),
            "speed_kn": [float(sc.min_speed_kn_for_operation), float(sc.max_speed_kn_for_operation)],
        }
    if operation_type == "VEHICLE_REPLACEMENT_AT_SEA":
        vr = guest.vehicle_replacement
        return {
            "max_sea_state": int(vr.max_sea_state),
            "max_wind_kn": float(vr.max_wind_kn),
            "speed_kn": [float(vr.min_speed_kn), float(vr.max_speed_kn)],
        }
    if operation_type == "AT_SEA_INSTALL":
        inst = guest.installation
        max_wind = min(20, inst.sea_install_max_wind_kn) if inst.sea_install_max_wind_kn > 0 else 20
        return {"max_sea_state": 3, "max_wind_kn": float(max_wind), "speed_kn": [4.0, 8.0]}
    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown operation_type '{operation_type}'", "operation_type")


# ASM-ENV-MINWIN: default minimum window when the card does not declare a duration.
DEFAULT_MIN_WINDOW_S = 1800
# Plan §5.8.3: G4 launch/recovery cycle = 15 min (ASM-ENV-MINWIN-G4-LR).
G4_LAUNCH_RECOVERY_S = 900


def operation_min_duration_s(guest: GuestManifest, operation_type: str) -> Tuple[int, str]:
    """Minimum contiguous window for the operation, taken from the guest card when declared."""
    if operation_type == "VEHICLE_REPLACEMENT_AT_SEA":
        mins = int(getattr(guest.vehicle_replacement, "swap_duration_min", 0) or 0)
        if mins > 0:
            return mins * 60, "card.vehicle_replacement.swap_duration_min"
    elif operation_type == "AT_SEA_INSTALL":
        mins = int(getattr(guest.installation, "install_time_min", 0) or 0)
        if mins > 0:
            return mins * 60, "card.installation.install_time_min"
    elif operation_type == "LAUNCH_RECOVERY" and guest.guest_id == "G4":
        return G4_LAUNCH_RECOVERY_S, "ASM-ENV-MINWIN-G4-LR"
    return DEFAULT_MIN_WINDOW_S, "ASM-ENV-MINWIN"


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
    guard: Any = None,
) -> List[Dict[str, Any]]:
    """Compute operational deck availability timeline across a forecast horizon (§5.8.3).

    For each step:
    - Clones base_state.
    - Sets speed_kn to center of guest's operational speed window (ASM-ENV-SPEED-ASSUMED).
    - Applies guard bands to forecast wind and wave height before rounding / sea state classification.
    - Fails closed (INV-6): a step with no wind or no Hs is NOT permitted (ENV_UNAVAILABLE); missing
      values are never replaced by zero.
    - Calls scheduling.evaluate_a_prime_window without modifying it.
    """
    gb = GuardBand.coerce(guard)
    limits = operation_limits(guest, operation_type)
    center_speed = (limits["speed_kn"][0] + limits["speed_kn"][1]) / 2.0
    if operation_type == "AT_SEA_INSTALL":
        center_speed = 6.0  # Center of [4.0, 8.0] kn per Annex A.4

    timeline: List[Dict[str, Any]] = []

    for sim_s in range(0, horizon_s + 1, step_s):
        sample = sample_at(env_series, sim_s, anchor_utc)
        row: Dict[str, Any] = {
            "sim_time_s": sim_s,
            "t_utc": sample.t_utc,
            "raw_wave_hs_m": sample.wave_hs_m,
            "raw_wind_kn": sample.wind_kn,
            "provenance": sample.provenance,
            "source_id": sample.source_id,
            "assumed_speed_kn": center_speed,
            "limits": limits,
            "guard": gb.to_dict(),
        }
        if sample.wave_hs_m is None or sample.wind_kn is None:
            missing = [n for n, v in (("wave_hs_m", sample.wave_hs_m), ("wind_kn", sample.wind_kn)) if v is None]
            row.update(
                permitted=False,
                blocking_reasons=[f"ENV_UNAVAILABLE: no {', '.join(missing)} at {sample.t_utc} (fail-closed)"],
                wind_kn=None,
                sea_state=None,
                wave_hs_m=None,
            )
            timeline.append(row)
            continue

        st = base_state.clone()
        st.speed_kn = center_speed
        st.ship_speed_kn = center_speed

        eff_hs = float(sample.wave_hs_m) + gb.wave_hs_m
        eff_ss = wave_height_to_sea_state_wmo(eff_hs)
        eff_wind = float(sample.wind_kn) + gb.wind_kn

        st.wind_kn = int(math.ceil(eff_wind - 1e-9))
        st.sea_state = int(eff_ss)

        res = evaluate_a_prime_window(guest, operation_type, st, catalog, ship_profile)

        row.update(
            permitted=res["permitted"],
            blocking_reasons=res.get("blocking_reasons", []),
            wind_kn=st.wind_kn,
            sea_state=st.sea_state,
            wave_hs_m=round(eff_hs, 2),
        )
        timeline.append(row)

    return timeline


def find_next_window(
    timeline: List[Dict[str, Any]],
    *,
    min_duration_s: int,
    now_sim_s: int = 0,
    source_id: str = "openmeteo:marine+forecast:GG_BAR",
    guard_band: Any = None,
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
            wind_val = first_pt.get("raw_wind_kn")
            ss_val = first_pt.get("sea_state")
            lim = first_pt.get("limits") or {}
            gb = GuardBand.coerce(guard_band if guard_band is not None else first_pt.get("guard"))
            source_tag = first_pt.get("source_id") or source_id

            hours_len = duration_s // 3600
            dur_str = f"{hours_len} h" if hours_len > 0 else f"{duration_s} s"

            ss_lim = lim.get("max_sea_state")
            wind_lim = lim.get("max_wind_kn")
            ss_part = (
                f"sea state {ss_val} (Hs {hs_val:.2f} m + {gb.wave_hs_m:.2f} m guard)"
                + (f" <= {ss_lim}" if ss_lim is not None else "")
            )
            wind_part = (
                f"wind {wind_val:.1f} kn + {gb.wind_kn:.1f} kn guard"
                + (f" <= {wind_lim:g} kn" if wind_lim is not None else "")
            )
            body = f"{dur_str} long: {ss_part} and {wind_part}; guard {gb.status}; source {source_tag}"
            reason = f"starts at {start_utc} ({body})"
            phrase = f"next valid operational window starts at T+{start_s}s ({start_utc}, {body})"

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
                "guard_band": gb.to_dict(),
            }

    return None


def describe_no_window(timeline: List[Dict[str, Any]], *, min_duration_s: int) -> Dict[str, Any]:
    """Un-canned explanation (INV-7) when no window of min_duration_s exists in the timeline."""
    if not timeline:
        return {"start_s": None, "reason": "no forecast timeline available (fail-closed); re-evaluate on next snapshot"}
    horizon_h = (timeline[-1]["sim_time_s"] - timeline[0]["sim_time_s"]) // 3600
    hs = [r["raw_wave_hs_m"] for r in timeline if r.get("raw_wave_hs_m") is not None]
    wind = [r["raw_wind_kn"] for r in timeline if r.get("raw_wind_kn") is not None]
    ss = [r["sea_state"] for r in timeline if r.get("sea_state") is not None]
    n_unavail = sum(1 for r in timeline if r.get("sea_state") is None)
    lim = timeline[0].get("limits") or {}
    gb = GuardBand.coerce(timeline[0].get("guard"))
    n_open = sum(1 for r in timeline if r.get("permitted"))
    parts = []
    if ss:
        parts.append(f"guarded sea state {min(ss)}–{max(ss)} vs limit {lim.get('max_sea_state')}")
    if hs:
        parts.append(f"Hs {min(hs):.2f}–{max(hs):.2f} m + {gb.wave_hs_m:.2f} m guard")
    if wind:
        parts.append(f"wind {min(wind):.1f}–{max(wind):.1f} kn + {gb.wind_kn:.1f} kn guard vs limit {lim.get('max_wind_kn')} kn")
    if n_unavail:
        parts.append(f"{n_unavail} step(s) without data (fail-closed)")
    parts.append(f"{n_open} isolated open step(s) shorter than {min_duration_s // 60} min")
    return {
        "start_s": None,
        "reason": (
            f"no valid operational window within the {horizon_h} h forecast (required >= {min_duration_s // 60} min; "
            + "; ".join(parts)
            + f"; guard {gb.status}); re-evaluate on next snapshot"
        ),
    }

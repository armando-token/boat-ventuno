"""Vessel state representation, feed freshness/provenance tracking, and maneuver state machine."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from .models import StrictValidationError, VALID_PROVENANCE_LABELS, require_finite_number, require_int, require_str


@dataclass
class FeedReading:
    feed_id: str
    value: Optional[float | int]
    unit: str
    provenance: str
    source_id: str
    source_time_ns: int
    ttl_ms: int
    age_ms: int = 0
    fallback_value: Optional[float | int] = None
    fallback_reason: Optional[str] = None

    def validate(self) -> None:
        require_str(self.provenance, f"feed.{self.feed_id}.provenance", allowed_enum=VALID_PROVENANCE_LABELS)
        if self.provenance == "MEASURED":
            if not self.source_id or self.source_id in ("constant", "literal", "config"):
                raise StrictValidationError(
                    "PROVENANCE_VIOLATION",
                    f"Feed '{self.feed_id}' labelled MEASURED must originate from a physical sensor source_id, not '{self.source_id}'",
                    f"feed.{self.feed_id}",
                )
            if self.source_time_ns <= 0:
                raise StrictValidationError(
                    "PROVENANCE_VIOLATION",
                    f"Feed '{self.feed_id}' labelled MEASURED must have a positive monotonic source_time_ns",
                    f"feed.{self.feed_id}.source_time_ns",
                )

    @property
    def is_stale(self) -> bool:
        return self.age_ms > self.ttl_ms or self.value is None or self.provenance == "UNAVAILABLE"

    def effective_value(self) -> Tuple[Optional[float | int], str, str]:
        """Return (value, effective_provenance, status_note) applying conservative fallback on stale/lost feed."""
        self.validate()
        if not self.is_stale and self.value is not None:
            return self.value, self.provenance, "FRESH"
        if self.fallback_value is not None:
            return self.fallback_value, "UNAVAILABLE", f"STALE_FALLBACK ({self.fallback_reason or 'conservative envelope bound'})"
        return None, "UNAVAILABLE", "UNAVAILABLE_NO_FALLBACK"

    def to_dict(self) -> Dict[str, Any]:
        eff_val, eff_prov, status_note = self.effective_value()
        return {
            "feed_id": self.feed_id,
            "raw_value": self.value,
            "effective_value": eff_val,
            "unit": self.unit,
            "provenance": eff_prov,
            "raw_provenance": self.provenance,
            "source_id": self.source_id,
            "source_time_ns": str(self.source_time_ns),
            "age_ms": self.age_ms,
            "ttl_ms": self.ttl_ms,
            "is_stale": self.is_stale,
            "status": status_note,
        }


@dataclass
class ThermalInertiaState:
    max_boost_power_w: int = 30000
    max_boost_duration_s: int = 60
    max_energy_j: int = 1800000
    recharge_cooldown_s: int = 600
    accumulated_boost_energy_j: int = 0
    continuous_boost_seconds: int = 0
    cooldown_remaining_s: int = 0
    exhausted: bool = False

    def available_boost_w(self, duration_needed_s: int = 1) -> int:
        """Return thermal inertia boost available (0 if sustained duration > 60 s or in cooldown/exhausted)."""
        if duration_needed_s > self.max_boost_duration_s:
            return 0
        if self.exhausted or self.cooldown_remaining_s > 0:
            return 0
        if self.continuous_boost_seconds >= self.max_boost_duration_s:
            return 0
        remaining_energy_j = self.max_energy_j - self.accumulated_boost_energy_j
        if remaining_energy_j <= 0:
            return 0
        return min(self.max_boost_power_w, remaining_energy_j // max(1, duration_needed_s))

    def step_tick(self, excess_heat_w: int, dt_s: int = 1) -> Tuple[bool, str]:
        """Advance thermal inertia by dt_s seconds without ever resetting peak tolerance per tick."""
        if excess_heat_w > 0:
            if self.exhausted or self.cooldown_remaining_s > 0:
                return False, "THERMAL_INERTIA_IN_COOLDOWN"
            if excess_heat_w > self.max_boost_power_w:
                return False, f"EXCEEDS_MAX_THERMAL_BOOST ({excess_heat_w} W > {self.max_boost_power_w} W)"
            added_j = excess_heat_w * dt_s
            if self.accumulated_boost_energy_j + added_j > self.max_energy_j or (
                self.continuous_boost_seconds + dt_s > self.max_boost_duration_s
            ):
                self.accumulated_boost_energy_j = self.max_energy_j
                self.continuous_boost_seconds = self.max_boost_duration_s
                self.exhausted = True
                self.cooldown_remaining_s = self.recharge_cooldown_s
                return False, "THERMAL_INERTIA_EXHAUSTED_60S"
            self.accumulated_boost_energy_j += added_j
            self.continuous_boost_seconds += dt_s
            if self.accumulated_boost_energy_j >= self.max_energy_j or self.continuous_boost_seconds >= self.max_boost_duration_s:
                self.exhausted = True
                self.cooldown_remaining_s = self.recharge_cooldown_s
            return True, "THERMAL_INERTIA_ABSORBING"
        else:
            # Below continuous cooling capacity: count down recharge cooldown
            if self.cooldown_remaining_s > 0:
                self.cooldown_remaining_s = max(0, self.cooldown_remaining_s - dt_s)
                if self.cooldown_remaining_s == 0:
                    self.exhausted = False
                    self.accumulated_boost_energy_j = 0
                    self.continuous_boost_seconds = 0
            elif self.accumulated_boost_energy_j > 0:
                # Gradual decay only after cooldown criteria met; does NOT reset instantaneously
                decay_j = (self.max_energy_j * dt_s) // self.recharge_cooldown_s
                self.accumulated_boost_energy_j = max(0, self.accumulated_boost_energy_j - decay_j)
                if self.accumulated_boost_energy_j == 0:
                    self.continuous_boost_seconds = 0
            return True, "THERMAL_NOMINAL"


@dataclass
class InstalledGuestState:
    guest_id: str
    active_mode: str
    powered_on: bool = True
    placements: Dict[str, str] = field(default_factory=dict)
    compute_node: str = "node_1"
    mode_elapsed_s: int = 0
    battery_pct: Optional[float] = None
    max_flight_remaining_s: Optional[int] = None
    acoustic_hold_confirmed: bool = False
    actual_power_override_w: Optional[int] = None
    last_transition_time_s: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FirmReservedLoad:
    guest_id: str
    reserved_mode: str
    reserved_power_w: int
    reason: str
    reservation_id: str = ""


@dataclass
class ShipOperationalState:
    sim_time_s: int = 0
    tick_seq: int = 0
    vessel_mode: str = "CRUISE"
    maneuver_phase: str = "STEADY_COURSE"
    ship_speed_kn: float = 10.0
    speed_kn: Optional[float] = None
    heading_deg: float = 0.0
    wind_deg: float = 0.0
    wind_kn: int = 14
    sea_state: int = 2
    seawater_temp_c: Optional[float] = 24.0
    seawater_temp_is_stale: bool = False
    crew_count: int = 14
    crew_regime: str = "FULL_CREW_14"
    available_crew: int = 9
    deck_clear: bool = True
    deck_machinery_active: bool = False
    emcon_silence: bool = False
    power_reserve_w: int = 200000
    cooling_loop_degraded: bool = False
    satcom_available_bps: int = 8000000
    installed_guests: Dict[str, InstalledGuestState] = field(default_factory=dict)
    firm_reserved_loads: List[FirmReservedLoad] = field(default_factory=list)
    thermal_inertia: ThermalInertiaState = field(default_factory=ThermalInertiaState)
    feeds: Dict[str, FeedReading] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.speed_kn is not None:
            self.ship_speed_kn = float(self.speed_kn)
        else:
            self.speed_kn = float(self.ship_speed_kn)

    def clone(self) -> "ShipOperationalState":
        c = copy.deepcopy(self)
        c.speed_kn = float(c.ship_speed_kn)
        return c

    def effective_seawater_temp_c(self) -> Tuple[float, str]:
        if self.seawater_temp_is_stale or self.seawater_temp_c is None:
            return 30.0, "UNAVAILABLE:CONSERVATIVE_30C_FALLBACK"
        if "seawater_temperature_sensor" in self.feeds:
            val, prov, status = self.feeds["seawater_temperature_sensor"].effective_value()
            if val is not None:
                return float(val), f"{prov}:{status}"
            return 30.0, "UNAVAILABLE:CONSERVATIVE_30C_FALLBACK"
        return float(self.seawater_temp_c), "SIMULATED:DIRECT"

    def to_snapshot_dict(self) -> Dict[str, Any]:
        eff_temp, temp_prov = self.effective_seawater_temp_c()
        return {
            "sim_time_s": self.sim_time_s,
            "tick_seq": self.tick_seq,
            "vessel_mode": self.vessel_mode,
            "maneuver_phase": self.maneuver_phase,
            "ship_speed_kn": self.ship_speed_kn,
            "wind_kn": self.wind_kn,
            "sea_state": self.sea_state,
            "seawater_temp_c": eff_temp,
            "seawater_temp_provenance": temp_prov,
            "crew_count": self.crew_count,
            "deck_clear": self.deck_clear,
            "deck_machinery_active": self.deck_machinery_active,
            "emcon_silence": self.emcon_silence,
            "power_reserve_w": self.power_reserve_w,
            "cooling_loop_degraded": self.cooling_loop_degraded,
            "satcom_available_bps": self.satcom_available_bps,
            "installed_guests": {k: v.to_dict() for k, v in sorted(self.installed_guests.items())},
            "firm_reserved_loads": [asdict(r) for r in self.firm_reserved_loads],
            "thermal_inertia": asdict(self.thermal_inertia),
            "feeds": {k: v.to_dict() for k, v in sorted(self.feeds.items())},
        }

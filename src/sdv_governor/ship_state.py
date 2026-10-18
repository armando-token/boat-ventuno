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
            if not self.source_id or self.source_id in ("constant", "literal", "config", "synthetic", "simulated", "virtual") or "synthetic" in self.source_id.lower():
                raise StrictValidationError(
                    "MEASURED_WITHOUT_PHYSICAL_SOURCE_ID",
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
    survey_leg_target_s: int = 2400
    survey_leg_elapsed_s: int = 0
    survey_legs_completed: int = 0
    survey_leg_aborted_count: int = 0
    survey_leg_status: str = "NONE"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FirmReservedLoad:
    guest_id: str
    reserved_mode: Any = "RESERVED"
    reserved_power_w: Any = 0
    reason: str = ""
    reservation_id: str = ""
    origin: str = "SCENARIO_OR_ARBITRATION"
    created_at_s: int = 0
    activation_condition: str = "SCHEDULED_MODE_TRANSITION"
    cancellation_condition: str = "URGENT_STOP_OR_MODE_COMPLETED"

    def __post_init__(self) -> None:
        if isinstance(self.reserved_mode, (int, float)) and not isinstance(self.reserved_mode, bool):
            if isinstance(self.reserved_power_w, str) and not self.reason:
                self.reason = self.reserved_power_w
            self.reserved_power_w = int(self.reserved_mode)
            self.reserved_mode = "RESERVED"
        else:
            self.reserved_mode = str(self.reserved_mode)
            self.reserved_power_w = int(self.reserved_power_w)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StateTransition:
    transition_id: str
    resource_or_state_variable: str
    effective_state: Any
    requested_state: Any
    requested_at_s: int
    started_at_s: Optional[int]
    earliest_complete_at_s: Optional[int]
    completed_at_s: Optional[int] = None
    dependency_ids: List[str] = field(default_factory=list)
    confirmation_policy: str = "NONE"
    status: str = "REQUESTED"
    reason_constraint_id: str = ""
    timing_policy_ref: str = ""
    duration_s: int = 0
    plan_version: int = 0
    owner_id: str = "VESSEL"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


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
    pending_commands: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    hangar_roof_clearance_verified: bool = False
    layout_version: str = "VESSEL_BASELINE_LAYOUT_V1"
    g4_recovery_envelope_version: int = 1
    roof_clearance_verification: Optional[Dict[str, Any]] = None
    deck_clear_verified: bool = True
    quiet_acoustic_state: bool = True
    requested_speed_kn: Optional[float] = None
    requested_heading_deg: Optional[float] = None
    requested_vessel_mode: Optional[str] = None
    pending_transitions: List[StateTransition] = field(default_factory=list)
    transition_history: List[StateTransition] = field(default_factory=list)
    own_radar_policy: str = "TRANSMIT"

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
            feed = self.feeds["seawater_temperature_sensor"]
            if feed.is_stale:
                return 30.0, "UNAVAILABLE:CONSERVATIVE_30C_FALLBACK"
            val, prov, status = feed.effective_value()
            if val is not None:
                return float(val), f"{prov}:{status}"
            return 30.0, "UNAVAILABLE:CONSERVATIVE_30C_FALLBACK"
        return float(self.seawater_temp_c), "SIMULATED:DIRECT"

    def stamp_roof_clearance_verification(
        self,
        ship_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Stamp a layout-scoped roof drone clearance verification certificate on this state (§7.1)."""
        policy = (ship_profile or {}).get("roof_drone_clearance_policy", {})
        policy_id = str(policy.get("policy_id", "ROOF_OUTSIDE_G4_RECOVERY_V1"))
        policy_version = int(policy.get("policy_version", 1))
        assumption_ref = str(policy.get("assumption_ref", "ASM-ROOF-G4-CLEARANCE"))
        antenna_zone = str(policy.get("antenna_zone", "ROOF_ANTENNA_OUTSIDE_RECOVERY_ENVELOPE"))
        excluded_zones = list(policy.get("excluded_zones", ["G4_ROTOR_DISC", "G4_RECOVERY_CORRIDOR"]))
        antenna_pos = "hangar_roof"
        if "G5" in self.installed_guests:
            antenna_pos = self.installed_guests["G5"].placements.get("G5_MAST_ANTENNA", "hangar_roof")
        cert: Dict[str, Any] = {
            "verified": True,
            "status": "VERIFIED",
            "policy_id": policy_id,
            "policy_version": policy_version,
            "constraint_id": "hangar_roof_drone_clearance",
            "assumption_ref": assumption_ref,
            "layout_version": self.layout_version,
            "g4_recovery_envelope_version": self.g4_recovery_envelope_version,
            "antenna_guest_id": "G5",
            "antenna_subcomponent_id": "G5_MAST_ANTENNA",
            "antenna_position": antenna_pos,
            "drone_guest_id": "G4",
            "antenna_zone": antenna_zone,
            "excluded_zones": excluded_zones,
            "covered_components": ["G5:G5_MAST_ANTENNA", "G4:G4_DRONE_VEHICLE", "G4:G4_HANGAR_STATION"],
            "verified_at_sim_time_s": self.sim_time_s,
            "invalidated": False,
            "invalidation_reason": None,
        }
        self.roof_clearance_verification = cert
        self.hangar_roof_clearance_verified = True
        return cert

    def invalidate_roof_clearance(self, reason: str) -> None:
        """Invalidate roof drone clearance verification upon layout, envelope, or antenna relocation change (§7.1, ALIGN-T07)."""
        self.hangar_roof_clearance_verified = False
        base = dict(self.roof_clearance_verification) if isinstance(self.roof_clearance_verification, dict) else {}
        base.update(
            {
                "verified": False,
                "status": "INVALIDATED",
                "invalidated": True,
                "invalidation_reason": str(reason),
                "invalidated_at_sim_time_s": self.sim_time_s,
                "layout_version": self.layout_version,
                "g4_recovery_envelope_version": self.g4_recovery_envelope_version,
            }
        )
        self.roof_clearance_verification = base

    def _get_variable_current_value(self, var_name: str) -> Any:
        if var_name in ("ship_speed_kn", "speed_kn"):
            return self.ship_speed_kn
        if var_name == "heading_deg":
            return self.heading_deg
        if var_name == "vessel_mode":
            return self.vessel_mode
        if var_name == "maneuver_phase":
            return self.maneuver_phase
        if var_name == "quiet_acoustic_state":
            return self.quiet_acoustic_state
        if var_name in ("deck_clear", "deck_clear_verified"):
            return self.deck_clear and self.deck_clear_verified
        return getattr(self, var_name, None)

    def _set_requested_variable(self, var_name: str, val: Any) -> None:
        if var_name in ("ship_speed_kn", "speed_kn"):
            self.requested_speed_kn = float(val) if val is not None else None
        elif var_name == "heading_deg":
            self.requested_heading_deg = float(val) if val is not None else None
        elif var_name == "vessel_mode":
            self.requested_vessel_mode = str(val) if val is not None else None

    def _apply_completed_transition(self, tr: StateTransition) -> None:
        tr.effective_state = tr.requested_state
        var_name = tr.resource_or_state_variable
        if var_name in ("ship_speed_kn", "speed_kn"):
            spd = float(tr.requested_state)
            self.ship_speed_kn = spd
            self.speed_kn = spd
            self.requested_speed_kn = None
        elif var_name == "heading_deg":
            self.heading_deg = float(tr.requested_state)
            self.requested_heading_deg = None
        elif var_name == "vessel_mode":
            self.vessel_mode = str(tr.requested_state)
            self.requested_vessel_mode = None
        elif var_name == "maneuver_phase":
            self.maneuver_phase = str(tr.requested_state)
        elif var_name == "quiet_acoustic_state":
            self.quiet_acoustic_state = bool(tr.requested_state)
        elif var_name in ("deck_clear", "deck_clear_verified"):
            val = bool(tr.requested_state)
            self.deck_clear = val
            self.deck_clear_verified = val
        elif hasattr(self, var_name):
            setattr(self, var_name, tr.requested_state)

    def _check_dependency_satisfied(self, dep_id: str, up_to_time_s: int) -> Tuple[bool, int]:
        if dep_id in ("G3_ACOUSTIC_HOLD_ACK", "HOLD_ACK"):
            g3 = self.installed_guests.get("G3")
            if g3 is not None and g3.acoustic_hold_confirmed:
                cmd = self.pending_commands.get("G3", {})
                conf_t = int(cmd.get("confirmed_at_s", self.sim_time_s))
                if conf_t <= up_to_time_s:
                    return True, conf_t
            # Also check if there is a transition with this ID
        for tr in reversed(self.pending_transitions + self.transition_history):
            if tr.transition_id == dep_id and tr.status == "COMPLETED":
                comp_t = int(tr.completed_at_s if tr.completed_at_s is not None else tr.requested_at_s)
                if comp_t <= up_to_time_s:
                    return True, comp_t
        return False, up_to_time_s

    def request_transition(
        self,
        transition_id: str,
        resource_or_state_variable: str,
        requested_state: Any,
        duration_s: int = 0,
        *,
        effective_state: Any = None,
        requested_at_s: Optional[int] = None,
        dependency_ids: Optional[List[str]] = None,
        confirmation_policy: str = "NONE",
        reason_constraint_id: str = "",
        timing_policy_ref: str = "",
        plan_version: int = 0,
        owner_id: str = "VESSEL",
    ) -> StateTransition:
        """Register a causal state transition request without mutating effective state before completion (§9.1, ALIGN-T23)."""
        req_t = self.sim_time_s if requested_at_s is None else int(requested_at_s)
        cur_eff = (
            self._get_variable_current_value(resource_or_state_variable)
            if effective_state is None
            else effective_state
        )
        self._set_requested_variable(resource_or_state_variable, requested_state)

        # Cancel any prior pending transition with the same transition_id
        for existing in self.pending_transitions:
            if existing.transition_id == transition_id and existing.status in ("REQUESTED", "WAITING_DEPENDENCY", "IN_PROGRESS"):
                existing.status = "CANCELLED"

        deps = list(dependency_ids or [])
        dur = max(0, int(duration_s))

        all_deps_ok = True
        latest_dep_t = req_t
        for d_id in deps:
            ok, dt = self._check_dependency_satisfied(d_id, req_t)
            if not ok:
                all_deps_ok = False
                break
            latest_dep_t = max(latest_dep_t, dt)

        if all_deps_ok:
            start_t = max(req_t, latest_dep_t)
            earliest_t = start_t + dur
            if dur == 0 and start_t <= self.sim_time_s:
                tr = StateTransition(
                    transition_id=transition_id,
                    resource_or_state_variable=resource_or_state_variable,
                    effective_state=requested_state,
                    requested_state=requested_state,
                    requested_at_s=req_t,
                    started_at_s=start_t,
                    earliest_complete_at_s=earliest_t,
                    completed_at_s=earliest_t,
                    dependency_ids=deps,
                    confirmation_policy=confirmation_policy,
                    status="COMPLETED",
                    reason_constraint_id=reason_constraint_id,
                    timing_policy_ref=timing_policy_ref,
                    duration_s=dur,
                    plan_version=plan_version,
                    owner_id=owner_id,
                )
                self._apply_completed_transition(tr)
                self.pending_transitions.append(tr)
                self.transition_history.append(tr)
                return tr
            status = "IN_PROGRESS"
            started_at: Optional[int] = start_t
            earliest_complete: Optional[int] = earliest_t
        else:
            status = "WAITING_DEPENDENCY"
            started_at = None
            earliest_complete = None

        tr = StateTransition(
            transition_id=transition_id,
            resource_or_state_variable=resource_or_state_variable,
            effective_state=cur_eff,
            requested_state=requested_state,
            requested_at_s=req_t,
            started_at_s=started_at,
            earliest_complete_at_s=earliest_complete,
            completed_at_s=None,
            dependency_ids=deps,
            confirmation_policy=confirmation_policy,
            status=status,
            reason_constraint_id=reason_constraint_id,
            timing_policy_ref=timing_policy_ref,
            duration_s=dur,
            plan_version=plan_version,
            owner_id=owner_id,
        )
        self.pending_transitions.append(tr)
        return tr

    def advance_transitions(
        self,
        up_to_time_s: int,
        events_log: Optional[List[str]] = None,
    ) -> List[StateTransition]:
        """Advance pending state transitions up to `up_to_time_s`, respecting dependency completion times (§9.1)."""
        completed_now: List[StateTransition] = []
        changed = True
        while changed:
            changed = False
            for tr in self.pending_transitions:
                if tr.status in ("COMPLETED", "CANCELLED", "BLOCKED"):
                    continue
                all_deps_ok = True
                latest_dep_t = tr.requested_at_s
                for d_id in tr.dependency_ids:
                    ok, dt = self._check_dependency_satisfied(d_id, up_to_time_s)
                    if not ok:
                        all_deps_ok = False
                        break
                    latest_dep_t = max(latest_dep_t, dt)

                if all_deps_ok and tr.status in ("REQUESTED", "WAITING_DEPENDENCY"):
                    start_t = max(tr.requested_at_s, latest_dep_t)
                    if start_t <= up_to_time_s:
                        tr.started_at_s = start_t
                        tr.earliest_complete_at_s = start_t + int(tr.duration_s)
                        tr.status = "IN_PROGRESS"
                        changed = True
                        if events_log is not None:
                            events_log.append(f"TRANSITION_STARTED:{tr.transition_id}@{start_t}s")

                if (
                    tr.status == "IN_PROGRESS"
                    and tr.earliest_complete_at_s is not None
                    and up_to_time_s >= tr.earliest_complete_at_s
                ):
                    comp_t = int(tr.earliest_complete_at_s)
                    tr.completed_at_s = comp_t
                    tr.status = "COMPLETED"
                    self._apply_completed_transition(tr)
                    if tr not in self.transition_history:
                        self.transition_history.append(tr)
                    completed_now.append(tr)
                    changed = True
                    if events_log is not None:
                        events_log.append(
                            f"TRANSITION_COMPLETED:{tr.transition_id}:{tr.resource_or_state_variable}={tr.requested_state}@{comp_t}s"
                        )
        return completed_now

    def cancel_transitions_for_plan(
        self,
        plan_version: Optional[int] = None,
        owner_id: Optional[str] = None,
        reason: str = "URGENT_CANCEL",
    ) -> None:
        """Cancel pending/in-progress transitions matching `plan_version` and/or `owner_id` without erasing completed history (§9.4)."""
        for tr in self.pending_transitions:
            if tr.status not in ("REQUESTED", "WAITING_DEPENDENCY", "IN_PROGRESS"):
                continue
            if plan_version is not None and tr.plan_version != plan_version:
                continue
            if owner_id is not None and tr.owner_id != owner_id:
                continue
            tr.status = "CANCELLED"
            if reason:
                tr.reason_constraint_id = reason
            self._set_requested_variable(tr.resource_or_state_variable, None)
            if tr not in self.transition_history:
                self.transition_history.append(tr)

    def to_snapshot_dict(self) -> Dict[str, Any]:
        eff_temp, temp_prov = self.effective_seawater_temp_c()
        return {
            "sim_time_s": self.sim_time_s,
            "tick_seq": self.tick_seq,
            "vessel_mode": self.vessel_mode,
            "maneuver_phase": self.maneuver_phase,
            "ship_speed_kn": self.ship_speed_kn,
            "requested_speed_kn": self.requested_speed_kn,
            "heading_deg": self.heading_deg,
            "requested_heading_deg": self.requested_heading_deg,
            "wind_kn": self.wind_kn,
            "sea_state": self.sea_state,
            "seawater_temp_c": eff_temp,
            "seawater_temp_provenance": temp_prov,
            "crew_count": self.crew_count,
            "deck_clear": self.deck_clear,
            "deck_clear_verified": self.deck_clear_verified,
            "quiet_acoustic_state": self.quiet_acoustic_state,
            "deck_machinery_active": self.deck_machinery_active,
            "emcon_silence": self.emcon_silence,
            "own_radar_policy": self.own_radar_policy,
            "power_reserve_w": self.power_reserve_w,
            "cooling_loop_degraded": self.cooling_loop_degraded,
            "satcom_available_bps": self.satcom_available_bps,
            "hangar_roof_clearance_verified": self.hangar_roof_clearance_verified,
            "layout_version": self.layout_version,
            "g4_recovery_envelope_version": self.g4_recovery_envelope_version,
            "roof_clearance_verification": copy.deepcopy(self.roof_clearance_verification),
            "installed_guests": {k: v.to_dict() for k, v in sorted(self.installed_guests.items())},
            "firm_reserved_loads": [asdict(r) for r in self.firm_reserved_loads],
            "thermal_inertia": asdict(self.thermal_inertia),
            "feeds": {k: v.to_dict() for k, v in sorted(self.feeds.items())},
            "pending_commands": copy.deepcopy(self.pending_commands),
            "pending_transitions": [
                t.to_dict()
                for t in self.pending_transitions
                if t.status in ("REQUESTED", "WAITING_DEPENDENCY", "IN_PROGRESS")
            ],
            "transition_history": [t.to_dict() for t in self.transition_history],
        }

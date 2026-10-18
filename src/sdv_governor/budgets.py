"""Eight-class physical resource accounting, local bay/hangar/ramp limits, and coupled thermal equations (B3, PHY-01..10, H07, H17)."""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional, Set, Tuple

from .models import (
    BindingConstraintWitness,
    ComponentAllocationSpec,
    GuestManifest,
    GuestModeSpec,
    ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
    StrictValidationError,
    SubcomponentSpec,
    require_finite_number,
    require_int,
    require_str,
)
from .ship_state import FirmReservedLoad, ShipOperationalState


def is_mode_protected(
    manifest: GuestManifest,
    mode_spec: GuestModeSpec,
    mode_elapsed_s: int = 0,
    battery_pct: Optional[float] = None,
) -> Tuple[bool, str]:
    """Determine if a guest's active mode is protected from automatic shedding and why (H06, H09, DEG-01, DEG-04).

    Protection applies if:
    1. The guest or mode is designated `safety_critical: true` (never shed automatically).
    2. The mode is `uninterruptible: true` and its active protected window has not yet completed
       (`min_done_duration_s == 0` or `mode_elapsed_s < mode_spec.min_done_duration_s`).
    3. The guest is in an active aircraft recovery/landing mode (`LAUNCH_RECOVERY_OPTICS` or `RECOVERY_APPROACH`)
       where cutting power mid-recovery would ditch the airframe.
    """
    if manifest.safety_critical or mode_spec.safety_critical:
        return True, "SAFETY_CRITICAL_GUEST"
    if mode_spec.uninterruptible:
        if mode_spec.min_done_duration_s <= 0 or mode_elapsed_s < mode_spec.min_done_duration_s:
            return True, f"UNINTERRUPTIBLE_ACTIVE_WINDOW ({mode_elapsed_s}s/{mode_spec.min_done_duration_s}s)"
        return False, f"UNINTERRUPTIBLE_WINDOW_COMPLETED ({mode_elapsed_s}s>={mode_spec.min_done_duration_s}s)"
    if mode_spec.mode_name in ("LAUNCH_RECOVERY_OPTICS", "RECOVERY_APPROACH"):
        if mode_spec.min_done_duration_s <= 0 or mode_elapsed_s < mode_spec.min_done_duration_s:
            return True, "ACTIVE_AIRCRAFT_RECOVERY_PROTECTION"
    if battery_pct is not None and manifest.ship_state_constraints.low_battery_threshold_pct is not None:
        if battery_pct < manifest.ship_state_constraints.low_battery_threshold_pct:
            return True, f"EMERGENCY_LOW_BATTERY_BELOW_{manifest.ship_state_constraints.low_battery_threshold_pct}PCT"
    return False, "NOT_PROTECTED"


def compute_cooling_water_capacity_w(
    seawater_temp_c: float,
    *,
    loop_degraded: bool = False,
) -> Tuple[int, int, str]:
    """Compute nominal and effective chilled-water loop capacity in Watts (B3, PHY-02).

    Official formula (Annex A.2):
        C(T) = 160 - 40 * (T - 12) / 18  [kW]  for 12 <= T <= 30 °C.
    Out-of-range policy (documented in ASSUMPTIONS.yaml ASM-03):
        - If T < 12 °C: clamp conservatively at 160,000 W (never extrapolate optimistically above 160 kW).
        - If T > 30 °C: apply linear penalty below 120,000 W clamped at >= 0 W and tag OUT_OF_ENVELOPE_HIGH_TEMP.
    When `loop_degraded=True` (40% loss), effective capacity is 0.60 * nominal capacity (72,000 W at 30 °C).
    """
    t = require_finite_number(seawater_temp_c, "seawater_temp_c", min_value=-5.0, max_value=60.0)
    policy_note = "OFFICIAL_INTERVAL_12_TO_30C"
    if t < 12.0:
        nominal_kw = 160.0
        policy_note = "CLAMPED_LOW_TEMP_CONSERVATIVE_160KW"
    elif t <= 30.0:
        nominal_kw = 160.0 - 40.0 * (t - 12.0) / 18.0
    else:
        nominal_kw = max(0.0, 160.0 - 40.0 * (t - 12.0) / 18.0)
        policy_note = "OUT_OF_ENVELOPE_HIGH_TEMP_PENALTY"

    nominal_w = int(round(nominal_kw * 1000.0))
    effective_w = int(round(nominal_w * (0.60 if loop_degraded else 1.0)))
    if loop_degraded:
        policy_note += "+LOOP_DEGRADED_40PCT"
    return nominal_w, effective_w, policy_note


def validate_strict_budget_input(
    raw_mission: Any,
    raw_budget_w: Any,
    known_missions: Set[str],
) -> Tuple[str, int]:
    """Fail-closed validator for legacy/external mission & budget inputs (reproduces and defeats F3 defects, DAT-04)."""
    mission = require_str(raw_mission, "mission", allowed_enum=known_missions)
    budget_w = require_int(raw_budget_w, "power_budget_w", min_value=1, allow_zero=False)
    return mission, budget_w


def evaluate_roof_drone_clearance(
    state: ShipOperationalState,
    ship_profile: Optional[Dict[str, Any]],
    catalog: Dict[str, GuestManifest],
    *,
    hangar_roof_clearance_verified: Optional[bool] = None,
    allow_pending_conditional: bool = False,
) -> Dict[str, Any]:
    """Evaluate `hangar_roof_drone_clearance` policy and verification stamp (L2, ALIGN-T05..T07).

    Returns a structured evaluation dictionary with `status` in:
    - `NOT_APPLICABLE`: either `hangar_roof` is unoccupied or `G4` is not installed.
    - `VERIFIED_COMPATIBLE`: policy is compatible/conditional and clearance is verified with a valid stamp.
    - `PENDING_VERIFICATION`: policy is conditional (`COMPATIBLE_CONDITIONAL`) and clearance is not yet verified.
    - `INVALIDATED`: verification stamp was invalidated by layout/envelope/policy change or explicit invalidation.
    - `CONFLICT_DENIED`: policy has `compatibility == "CONFLICT"` (always denied even if `hangar_roof_clearance_verified=True`).
    """
    sp = ship_profile if isinstance(ship_profile, dict) else {}
    roof_occupants: List[str] = []
    for gid, ginst in sorted(state.installed_guests.items()):
        if gid in catalog:
            for sc in catalog[gid].subcomponents:
                pos = ginst.placements.get(sc.subcomponent_id, sc.preferred_positions[0])
                if pos == "hangar_roof":
                    roof_occupants.append(f"{gid}:{sc.subcomponent_id}")
        else:
            for sc_id, pos in sorted(ginst.placements.items()):
                if pos == "hangar_roof":
                    roof_occupants.append(f"{gid}:{sc_id}")

    g4_installed = "G4" in state.installed_guests
    requires_clearance = len(roof_occupants) > 0 and g4_installed
    state_label = f"T+{state.sim_time_s}s:{state.vessel_mode}"

    policy_cfg = sp.get("roof_drone_clearance_policy", {}) if isinstance(sp, dict) else {}
    compatibility = str(policy_cfg.get("compatibility", "COMPATIBLE_CONDITIONAL")).upper()
    policy_id = str(policy_cfg.get("policy_id", "ROOF_OUTSIDE_G4_RECOVERY_V1"))
    policy_version = int(policy_cfg.get("policy_version", 1))
    expected_layout_version = str(
        policy_cfg.get("layout_version", getattr(state, "layout_version", "VESSEL_BASELINE_LAYOUT_V1"))
    )
    state_layout_version = str(getattr(state, "layout_version", expected_layout_version))
    state_g4_env_version = int(getattr(state, "g4_recovery_envelope_version", 1))

    if not requires_clearance:
        return {
            "constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
            "status": "NOT_APPLICABLE",
            "satisfied": True,
            "feasible": True,
            "requires_clearance": False,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": g4_installed,
            "verified": True,
            "compatibility": compatibility,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "layout_version": state_layout_version,
            "reason": "NO_ROOF_DRONE_INTERACTION",
            "required_condition": None,
            "witness": None,
        }

    if compatibility in ("CONFLICT", "FORBIDDEN", "INCOMPATIBLE", "DENIED"):
        reason = (
            f"hangar_roof_drone_clearance policy '{policy_id}' compatibility={compatibility} forbids "
            f"hangar_roof occupancy ({roof_occupants}) with G4 VTOL recovery envelope"
        )
        witness = BindingConstraintWitness(
            resource_class="positions",
            constraint_id=ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
            state_label=state_label,
            demand=1,
            capacity=0,
            margin=-1,
            unit="clearance",
            provenance="DERIVED",
            detail=reason,
        )
        return {
            "constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
            "status": "CONFLICT_DENIED",
            "satisfied": False,
            "feasible": False,
            "requires_clearance": True,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": True,
            "verified": False,
            "compatibility": compatibility,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "layout_version": state_layout_version,
            "reason": reason,
            "required_condition": reason,
            "witness": witness,
        }

    ver_stamp = getattr(state, "roof_clearance_verification", None)
    is_invalidated = False
    invalidation_reason: Optional[str] = None

    if state_layout_version != expected_layout_version:
        is_invalidated = True
        invalidation_reason = (
            f"layout_version mismatch: state='{state_layout_version}' != policy='{expected_layout_version}'"
        )

    if isinstance(ver_stamp, dict) and ver_stamp:
        if bool(ver_stamp.get("invalidated", False)) or ver_stamp.get("verified") is False:
            is_invalidated = True
            invalidation_reason = str(
                ver_stamp.get("invalidation_reason")
                or ver_stamp.get("reason")
                or "roof_clearance_verification stamp marked invalidated/unverified"
            )
        elif str(ver_stamp.get("layout_version", expected_layout_version)) != state_layout_version:
            is_invalidated = True
            invalidation_reason = (
                f"stamp layout_version '{ver_stamp.get('layout_version')}' != state '{state_layout_version}'"
            )
        elif int(ver_stamp.get("policy_version", policy_version)) != policy_version:
            is_invalidated = True
            invalidation_reason = (
                f"stamp policy_version {ver_stamp.get('policy_version')} != policy {policy_version}"
            )
        elif int(ver_stamp.get("g4_recovery_envelope_version", 1)) != state_g4_env_version:
            is_invalidated = True
            invalidation_reason = (
                f"stamp g4_recovery_envelope_version {ver_stamp.get('g4_recovery_envelope_version')} != state {state_g4_env_version}"
            )

    if is_invalidated:
        eff_verified = False
    elif hangar_roof_clearance_verified is not None:
        eff_verified = bool(hangar_roof_clearance_verified)
    else:
        eff_verified = bool(getattr(state, "hangar_roof_clearance_verified", False))

    cond_text = (
        f"Verify hangar_roof antenna ({roof_occupants}) clearance outside G4 VTOL rotor disc & recovery corridor "
        f"under policy {policy_id} v{policy_version} ({expected_layout_version})"
    )

    if eff_verified:
        return {
            "constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
            "status": "VERIFIED_COMPATIBLE",
            "satisfied": True,
            "feasible": True,
            "requires_clearance": True,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": True,
            "verified": True,
            "compatibility": compatibility,
            "policy_id": policy_id,
            "policy_version": policy_version,
            "layout_version": state_layout_version,
            "reason": f"Verified compatible under {policy_id} v{policy_version} ({state_layout_version})",
            "required_condition": cond_text,
            "witness": None,
        }

    status_str = "INVALIDATED" if is_invalidated else "PENDING_VERIFICATION"
    satisfied_flag = bool(allow_pending_conditional and not is_invalidated)
    detail_reason = (
        f"hangar_roof_drone_clearance INVALIDATED: {invalidation_reason}"
        if is_invalidated
        else "hangar_roof placement with G4 installed requires explicit drone clearance verification"
    )
    witness = (
        None
        if satisfied_flag
        else BindingConstraintWitness(
            resource_class="positions",
            constraint_id=ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
            state_label=state_label,
            demand=1,
            capacity=0,
            margin=-1,
            unit="clearance",
            provenance="DERIVED",
            detail=detail_reason,
        )
    )
    return {
        "constraint_id": ROOF_DRONE_CLEARANCE_CONSTRAINT_ID,
        "status": status_str,
        "satisfied": satisfied_flag,
        "feasible": satisfied_flag,
        "requires_clearance": True,
        "hangar_roof_occupants": roof_occupants,
        "g4_installed": True,
        "verified": False,
        "compatibility": compatibility,
        "policy_id": policy_id,
        "policy_version": policy_version,
        "layout_version": state_layout_version,
        "reason": detail_reason,
        "required_condition": cond_text,
        "witness": witness,
    }


def _resolve_component_allocations(
    manifest: GuestManifest,
    mode_spec: GuestModeSpec,
) -> Dict[str, ComponentAllocationSpec]:
    """Return per-component allocations for `mode_spec`, synthesizing a single-component entry if omitted."""
    if mode_spec.component_allocations:
        return dict(mode_spec.component_allocations)
    if len(manifest.subcomponents) == 1:
        sc0 = manifest.subcomponents[0]
        return {
            sc0.subcomponent_id: ComponentAllocationSpec(
                subcomponent_id=sc0.subcomponent_id,
                nominal_power_w=mode_spec.nominal_power_w,
                sustained_power_w=mode_spec.sustained_power_w,
                peak_power_w=mode_spec.peak_power_w,
                cooling_water_w=mode_spec.cooling_water_w,
                cooling_air_w=mode_spec.cooling_air_mast_w,
                source_kind=mode_spec.value_origin,
                assumption_ref=None,
            )
        }
    # Fallback for any synthetic multi-component test fixture constructed directly without parse_guest_manifest
    out: Dict[str, ComponentAllocationSpec] = {}
    rem_nom = mode_spec.nominal_power_w
    rem_sust = mode_spec.sustained_power_w
    rem_peak = mode_spec.peak_power_w
    rem_cw = mode_spec.cooling_water_w
    rem_ca = mode_spec.cooling_air_mast_w
    water_scs = [s for s in manifest.subcomponents if s.cooling_route == "CHILLED_WATER_LOOP"]
    air_scs = [s for s in manifest.subcomponents if s.cooling_route == "MAST_AIR_2KW"]
    primary_water_id = water_scs[0].subcomponent_id if water_scs else manifest.subcomponents[0].subcomponent_id
    primary_air_id = air_scs[0].subcomponent_id if air_scs else primary_water_id

    for sc in manifest.subcomponents:
        c_w = rem_cw if sc.subcomponent_id == primary_water_id else 0
        c_a = rem_ca if sc.subcomponent_id == primary_air_id else 0
        s_p = c_w + c_a
        n_p = min(rem_nom, s_p) if sc.subcomponent_id != primary_water_id else max(0, mode_spec.nominal_power_w - (mode_spec.sustained_power_w - s_p))
        p_p = s_p if sc.subcomponent_id != primary_water_id else max(s_p, mode_spec.peak_power_w - (mode_spec.sustained_power_w - s_p))
        out[sc.subcomponent_id] = ComponentAllocationSpec(
            subcomponent_id=sc.subcomponent_id,
            nominal_power_w=n_p,
            sustained_power_w=s_p,
            peak_power_w=p_p,
            cooling_water_w=c_w,
            cooling_air_w=c_a,
            source_kind=mode_spec.value_origin,
            assumption_ref=None,
        )
    return out


def _select_primary_water_compute_subcomponent(
    manifest: GuestManifest,
    comp_allocs: Dict[str, ComponentAllocationSpec],
) -> str:
    """Identify the primary water-cooled compute subcomponent that receives power/thermal overrun."""
    for sc in manifest.subcomponents:
        alloc = comp_allocs.get(sc.subcomponent_id)
        if sc.cooling_route == "CHILLED_WATER_LOOP" and alloc is not None and alloc.cooling_water_w > 0:
            return sc.subcomponent_id
    for sc in manifest.subcomponents:
        if sc.cooling_route == "CHILLED_WATER_LOOP":
            return sc.subcomponent_id
    for sc in manifest.subcomponents:
        alloc = comp_allocs.get(sc.subcomponent_id)
        if alloc is not None and alloc.nominal_power_w > 0:
            return sc.subcomponent_id
    return manifest.subcomponents[0].subcomponent_id


def evaluate_eight_class_budgets(
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    state: ShipOperationalState,
    *,
    calc_elapsed_ms: Optional[float] = None,
    source_time_ns: int = 0,
    hangar_roof_clearance_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    """Evaluate all 8 resource classes with global, local feeder, air-domain, and per-component constraints."""
    t0_ns = time.perf_counter_ns()
    witnesses: List[BindingConstraintWitness] = []
    state_label = f"T+{state.sim_time_s}s:{state.vessel_mode}"

    # 1. POWER (Global reserve + Local feeder lines LINE_A_FWD / LINE_B_AFT + Sustained vs Peak)
    line_feeds: Dict[str, Set[str]] = {
        line_id: set(line_spec["feeds_positions"])
        for line_id, line_spec in ship_profile["power"]["local_power_lines"].items()
    }
    line_max_w: Dict[str, int] = {
        line_id: int(line_spec["max_power_w"])
        for line_id, line_spec in ship_profile["power"]["local_power_lines"].items()
    }
    line_actual_w: Dict[str, int] = {line_id: 0 for line_id in line_feeds}
    line_committed_w: Dict[str, int] = {line_id: 0 for line_id in line_feeds}

    actual_power_w = 0
    committed_power_w = 0
    protected_power_w = 0

    # 2. COOLING (Chilled-water loop + Separate air cooling domains mast_3 and hangar_roof + Thermal inertia)
    eff_temp_c, temp_prov = state.effective_seawater_temp_c()
    nom_cool_w, eff_cool_w, cool_policy = compute_cooling_water_capacity_w(
        eff_temp_c, loop_degraded=state.cooling_loop_degraded
    )
    actual_cooling_water_w = 0
    committed_cooling_water_w = 0
    max_sustained_duration_s = 0

    default_mast_air_cap_w = int(ship_profile["mast"]["max_air_cooling_per_position_w"])
    raw_air_domains = (
        ship_profile.get("air_cooling_domains")
        or ship_profile.get("cooling", {}).get("air_cooling_domains")
        or {}
    )
    mast_3_dom_cfg = raw_air_domains.get("mast_3", {})
    hangar_roof_dom_cfg = raw_air_domains.get("hangar_roof", {})
    air_domain_cap_w: Dict[str, int] = {
        "mast_3": int(
            mast_3_dom_cfg.get(
                "max_air_cooling_w",
                mast_3_dom_cfg.get("capacity_w", default_mast_air_cap_w),
            )
        ),
        "hangar_roof": int(
            hangar_roof_dom_cfg.get(
                "max_air_cooling_w",
                hangar_roof_dom_cfg.get("capacity_w", default_mast_air_cap_w),
            )
        ),
    }
    air_domain_origin: Dict[str, str] = {
        "mast_3": str(mast_3_dom_cfg.get("value_origin", "OFFICIAL_BRIEF")),
        "hangar_roof": str(hangar_roof_dom_cfg.get("value_origin", "ASSUMED")),
    }
    air_domain_actual_w: Dict[str, int] = {"mast_3": 0, "hangar_roof": 0}
    air_domain_committed_w: Dict[str, int] = {"mast_3": 0, "hangar_roof": 0}
    air_domain_occupants: Dict[str, List[str]] = {"mast_3": [], "hangar_roof": []}

    # 3. POSITIONS & 4. MAST & 5. MASS / STABILITY (Global + Local Bay1<=8t, Bay2<=4t, Hangar<=1t, SternRamp)
    pos_occupants: Dict[str, List[str]] = {
        "bay1": [],
        "bay1_a": [],
        "bay1_b": [],
        "bay2": [],
        "hangar": [],
        "hangar_roof": [],
        "flight_deck": [],
        "stern_ramp": [],
        "mast_1": ["FIXED_SHIP_NAV_RADAR"],
        "mast_2": ["FIXED_SHIP_COMMS"],
        "mast_3": [],
    }
    pos_mass_kg: Dict[str, int] = {k: 0 for k in pos_occupants}
    pos_footprint_m2: Dict[str, float] = {k: 0.0 for k in pos_occupants}
    total_mass_kg = 0
    total_stability_eq_kg = 0.0
    mast_3_mass_kg = 0
    mast_3_footprint_m2 = 0.0
    vtol_airframe_mass_kg = 0

    stability_mult_by_pos = {
        "bay1": 1.0,
        "bay1_a": 1.0,
        "bay1_b": 1.0,
        "bay2": 1.0,
        "stern_ramp": 1.0,
        "rack": 1.0,
        "hangar": 1.2,
        "hangar_roof": 1.2,
        "flight_deck": 1.2,
        "mast_1": 3.0,
        "mast_2": 3.0,
        "mast_3": 3.0,
    }

    # 6. DATA / BANDWIDTH
    total_lan_bps = 0
    max_port_lan_bps = 0
    total_satcom_bps = 0

    # 7. COMPUTE RACK (Per-node cores, RAM, accelerators + shared storage)
    node_specs = ship_profile["compute_rack"]["nodes"]
    node_usage: Dict[str, Dict[str, int]] = {
        nid: {"cores": 0, "ram_mib": 0, "accelerators": 0} for nid in node_specs
    }
    total_storage_gb = 0

    component_breakdown: Dict[str, Dict[str, Any]] = {}
    guest_committed_before_res_w: Dict[str, int] = {}

    for gid, ginst in sorted(state.installed_guests.items()):
        if gid not in catalog:
            raise StrictValidationError("UNKNOWN_GUEST", f"Installed guest '{gid}' not in catalog", f"installed_guests.{gid}")
        manifest = catalog[gid]
        if ginst.active_mode not in manifest.modes:
            raise StrictValidationError(
                "UNKNOWN_MODE",
                f"Guest '{gid}' active_mode '{ginst.active_mode}' not in {sorted(manifest.modes.keys())}",
                f"installed_guests.{gid}.active_mode",
            )
        mode_spec = manifest.modes[ginst.active_mode]
        comp_allocs = _resolve_component_allocations(manifest, mode_spec)
        primary_overrun_sc_id = _select_primary_water_compute_subcomponent(manifest, comp_allocs)

        p_actual_guest = (
            ginst.actual_power_override_w
            if (ginst.powered_on and ginst.actual_power_override_w is not None)
            else (mode_spec.nominal_power_w if ginst.powered_on else 0)
        )
        overrun_w = max(0, p_actual_guest - mode_spec.nominal_power_w) if ginst.powered_on else 0
        underrun_w = max(0, mode_spec.nominal_power_w - p_actual_guest) if ginst.powered_on else 0

        guest_comm_sum = 0

        # Physical footprint, mass, and stability ALWAYS remain even when powered_on=False (PHY-08)
        for sc in manifest.subcomponents:
            pos = ginst.placements.get(sc.subcomponent_id, sc.preferred_positions[0])
            if pos not in pos_occupants:
                raise StrictValidationError("INVALID_POSITION", f"Invalid position '{pos}'", f"{gid}.{sc.subcomponent_id}")
            pos_occupants[pos].append(f"{gid}:{sc.subcomponent_id}")
            pos_mass_kg[pos] += sc.mass_kg
            pos_footprint_m2[pos] = round(pos_footprint_m2[pos] + sc.footprint_m2, 3)
            total_mass_kg += sc.mass_kg
            mult = stability_mult_by_pos[pos]
            stab_eq_sc = round(sc.mass_kg * mult, 3)
            total_stability_eq_kg = round(total_stability_eq_kg + stab_eq_sc, 3)
            if pos == "mast_3":
                mast_3_mass_kg += sc.mass_kg
                mast_3_footprint_m2 = round(mast_3_footprint_m2 + sc.footprint_m2, 3)
            if "DRONE" in sc.subcomponent_id or "VEHICLE" in sc.subcomponent_id:
                if pos in ("hangar", "flight_deck"):
                    vtol_airframe_mass_kg = max(vtol_airframe_mass_kg, sc.mass_kg)

            alloc = comp_allocs[sc.subcomponent_id]
            feeder_line_id: Optional[str] = None
            for line_id, fed_set in line_feeds.items():
                if pos in fed_set:
                    feeder_line_id = line_id
                    break

            if ginst.powered_on:
                is_primary_overrun_target = sc.subcomponent_id == primary_overrun_sc_id
                sc_overrun_w = overrun_w if is_primary_overrun_target else 0
                sc_underrun_w = underrun_w if is_primary_overrun_target else 0
                comp_act_p_w = max(0, alloc.nominal_power_w + sc_overrun_w - sc_underrun_w)
                comp_comm_p_w = max(comp_act_p_w, alloc.sustained_power_w)

                if sc.cooling_route == "CHILLED_WATER_LOOP" or alloc.cooling_water_w > 0:
                    comp_water_act_w = max(0, alloc.cooling_water_w + sc_overrun_w - sc_underrun_w)
                    comp_water_comm_w = max(comp_water_act_w, alloc.cooling_water_w)
                    comp_air_act_w = alloc.cooling_air_w
                    comp_air_comm_w = alloc.cooling_air_w
                elif sc.cooling_route == "MAST_AIR_2KW" or alloc.cooling_air_w > 0:
                    comp_water_act_w = 0
                    comp_water_comm_w = 0
                    comp_air_act_w = max(0, alloc.cooling_air_w + sc_overrun_w - sc_underrun_w)
                    comp_air_comm_w = max(comp_air_act_w, alloc.cooling_air_w)
                else:
                    comp_water_act_w = 0
                    comp_water_comm_w = 0
                    comp_air_act_w = 0
                    comp_air_comm_w = 0

                actual_power_w += comp_act_p_w
                committed_power_w += comp_comm_p_w
                guest_comm_sum += comp_comm_p_w

                actual_cooling_water_w += comp_water_act_w
                committed_cooling_water_w += comp_water_comm_w

                if feeder_line_id is not None:
                    line_actual_w[feeder_line_id] += comp_act_p_w
                    line_committed_w[feeder_line_id] += comp_comm_p_w

                air_dom_key: Optional[str] = None
                if comp_air_act_w > 0 or comp_air_comm_w > 0 or sc.cooling_route == "MAST_AIR_2KW":
                    air_dom_key = "hangar_roof" if pos == "hangar_roof" else "mast_3"
                    air_domain_actual_w[air_dom_key] += comp_air_act_w
                    air_domain_committed_w[air_dom_key] += comp_air_comm_w
                    if f"{gid}:{sc.subcomponent_id}" not in air_domain_occupants[air_dom_key]:
                        air_domain_occupants[air_dom_key].append(f"{gid}:{sc.subcomponent_id}")
            else:
                comp_act_p_w = 0
                comp_comm_p_w = 0
                comp_water_act_w = 0
                comp_water_comm_w = 0
                comp_air_act_w = 0
                comp_air_comm_w = 0
                air_dom_key = (
                    ("hangar_roof" if pos == "hangar_roof" else "mast_3")
                    if sc.cooling_route == "MAST_AIR_2KW"
                    else None
                )

            component_breakdown[sc.subcomponent_id] = {
                "guest_id": gid,
                "subcomponent_id": sc.subcomponent_id,
                "active_mode": ginst.active_mode,
                "powered_on": ginst.powered_on,
                "position": pos,
                "feeder_line": feeder_line_id,
                "cooling_route": sc.cooling_route,
                "air_cooling_domain": air_dom_key,
                "mass_kg": sc.mass_kg,
                "stability_multiplier": mult,
                "stability_eq_kg": stab_eq_sc,
                "nominal_power_w": alloc.nominal_power_w if ginst.powered_on else 0,
                "actual_power_w": comp_act_p_w,
                "committed_power_w": comp_comm_p_w,
                "peak_power_w": alloc.peak_power_w if ginst.powered_on else 0,
                "cooling_water_actual_w": comp_water_act_w,
                "cooling_water_committed_w": comp_water_comm_w,
                "cooling_air_actual_w": comp_air_act_w,
                "cooling_air_committed_w": comp_air_comm_w,
                "reserved_delta_w": 0,
                "source_kind": alloc.source_kind,
                "assumption_ref": alloc.assumption_ref,
            }

        guest_committed_before_res_w[gid] = guest_comm_sum

        # Shared storage remains allocated while installed
        total_storage_gb += mode_spec.shared_storage_gb

        if ginst.powered_on:
            is_prot, _ = is_mode_protected(
                manifest,
                mode_spec,
                mode_elapsed_s=ginst.mode_elapsed_s,
                battery_pct=ginst.battery_pct,
            )
            if is_prot:
                protected_power_w += p_actual_guest

            max_sustained_duration_s = max(max_sustained_duration_s, mode_spec.sustained_duration_s)

            # Data links
            total_lan_bps += mode_spec.backbone_lan_bps
            max_port_lan_bps = max(max_port_lan_bps, mode_spec.backbone_lan_bps)
            total_satcom_bps += mode_spec.satcom_bps

            # Compute node usage (normalize 'node4' -> 'node_4' if needed)
            nid = ginst.compute_node
            if nid not in node_usage:
                norm_nid = re.sub(r"^node(\d+)$", r"node_\1", str(nid))
                if norm_nid in node_usage:
                    nid = norm_nid
                else:
                    raise StrictValidationError("INVALID_COMPUTE_NODE", f"Unknown compute_node '{nid}'", f"{gid}.compute_node")
            node_usage[nid]["cores"] += mode_spec.compute_cores
            node_usage[nid]["ram_mib"] += mode_spec.compute_ram_mib
            node_usage[nid]["accelerators"] += mode_spec.compute_accelerators

    # Deduplicate and envelope firm reserved loads by guest_id (§6.2, ALIGN-T15)
    enveloped_res_by_guest: Dict[str, FirmReservedLoad] = {}
    for res_load in state.firm_reserved_loads:
        prev = enveloped_res_by_guest.get(res_load.guest_id)
        if prev is None or res_load.reserved_power_w > prev.reserved_power_w:
            enveloped_res_by_guest[res_load.guest_id] = res_load

    firm_reservations_ledger: List[Dict[str, Any]] = []
    for res_gid, res_load in sorted(enveloped_res_by_guest.items()):
        current_guest_committed = guest_committed_before_res_w.get(res_gid, 0)
        delta_reserve_w = max(0, res_load.reserved_power_w - current_guest_committed)
        committed_power_w += delta_reserve_w
        committed_cooling_water_w += delta_reserve_w

        target_sc_id: Optional[str] = None
        target_feeder_id: Optional[str] = None
        if res_gid in catalog:
            res_man = catalog[res_gid]
            res_mode_spec = res_man.modes.get(res_man.default_mode, next(iter(res_man.modes.values())))
            res_allocs = _resolve_component_allocations(res_man, res_mode_spec)
            target_sc_id = _select_primary_water_compute_subcomponent(res_man, res_allocs)
            if target_sc_id in component_breakdown:
                component_breakdown[target_sc_id]["committed_power_w"] += delta_reserve_w
                component_breakdown[target_sc_id]["cooling_water_committed_w"] += delta_reserve_w
                component_breakdown[target_sc_id]["reserved_delta_w"] += delta_reserve_w
                target_feeder_id = component_breakdown[target_sc_id]["feeder_line"]
                if target_feeder_id in line_committed_w:
                    line_committed_w[target_feeder_id] += delta_reserve_w
            else:
                # Guest is not currently installed; infer feeder from preferred position of primary water subcomponent
                sc_obj = next((s for s in res_man.subcomponents if s.subcomponent_id == target_sc_id), res_man.subcomponents[0])
                pref_pos = sc_obj.preferred_positions[0]
                for lid, fed_set in line_feeds.items():
                    if pref_pos in fed_set:
                        target_feeder_id = lid
                        line_committed_w[lid] += delta_reserve_w
                        break

        firm_reservations_ledger.append(
            {
                "reservation_id": res_load.reservation_id or f"RES_{res_gid}_{res_load.reserved_power_w}W",
                "guest_id": res_gid,
                "target_subcomponent_id": target_sc_id,
                "feeder_line": target_feeder_id,
                "reserved_power_w": res_load.reserved_power_w,
                "current_committed_before_reservation_w": current_guest_committed,
                "net_envelope_delta_w": delta_reserve_w,
                "reason": res_load.reason,
                "origin": res_load.origin,
                "created_at_s": res_load.created_at_s,
                "activation_condition": res_load.activation_condition,
                "cancellation_condition": res_load.cancellation_condition,
            }
        )

    # Check Class 1: POWER feasibility
    power_cap_w = state.power_reserve_w
    power_feasible = (
        actual_power_w <= power_cap_w
        and committed_power_w <= power_cap_w
        and all(
            line_committed_w[lid] <= line_max_w[lid] and line_actual_w[lid] <= line_max_w[lid]
            for lid in line_feeds
        )
    )
    if actual_power_w > power_cap_w or committed_power_w > power_cap_w:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="power",
                constraint_id="global_power_reserve_w",
                state_label=state_label,
                demand=max(actual_power_w, committed_power_w),
                capacity=power_cap_w,
                margin=power_cap_w - max(actual_power_w, committed_power_w),
                unit="W",
                provenance="DERIVED",
                detail=f"actual={actual_power_w}W, committed={committed_power_w}W vs reserve={power_cap_w}W",
            )
        )
    for lid in sorted(line_feeds.keys()):
        line_dem = max(line_actual_w[lid], line_committed_w[lid])
        if line_dem > line_max_w[lid]:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="power",
                    constraint_id=f"local_line_{lid}",
                    state_label=state_label,
                    demand=line_dem,
                    capacity=line_max_w[lid],
                    margin=line_max_w[lid] - line_dem,
                    unit="W",
                    provenance="DERIVED",
                    detail=f"Local feeder {lid} demand {line_dem}W exceeds {line_max_w[lid]}W limit",
                )
            )

    # Check Class 2: COOLING feasibility (Sustained > 60s cannot use 60s thermal inertia boost — PHY-04)
    inertia_boost_w = state.thermal_inertia.available_boost_w(
        duration_needed_s=max(1, max_sustained_duration_s)
    )
    effective_water_cap_with_inertia_w = eff_cool_w + inertia_boost_w
    mast_air_cap_w = air_domain_cap_w["mast_3"]
    hangar_roof_air_cap_w = air_domain_cap_w["hangar_roof"]
    actual_cooling_air_mast_w = air_domain_actual_w["mast_3"]
    actual_cooling_air_hangar_roof_w = air_domain_actual_w["hangar_roof"]
    total_cooling_air_w = actual_cooling_air_mast_w + actual_cooling_air_hangar_roof_w

    air_domains_feasible = all(
        air_domain_actual_w[dom_id] <= air_domain_cap_w[dom_id]
        and air_domain_committed_w[dom_id] <= air_domain_cap_w[dom_id]
        for dom_id in ("mast_3", "hangar_roof")
    )
    cooling_feasible = (
        actual_cooling_water_w <= eff_cool_w
        and committed_cooling_water_w <= eff_cool_w
        and air_domains_feasible
    )
    if actual_cooling_water_w > eff_cool_w or committed_cooling_water_w > eff_cool_w:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="cooling",
                constraint_id="chilled_water_loop_w",
                state_label=state_label,
                demand=max(actual_cooling_water_w, committed_cooling_water_w),
                capacity=eff_cool_w,
                margin=eff_cool_w - max(actual_cooling_water_w, committed_cooling_water_w),
                unit="W",
                provenance=temp_prov.split(":")[0] if ":" in temp_prov else "DERIVED",
                detail=(
                    f"Water cooling demand (actual={actual_cooling_water_w}W, committed={committed_cooling_water_w}W) "
                    f"> capacity {eff_cool_w}W at T={eff_temp_c}C ({cool_policy}); "
                    f"sustained_duration={max_sustained_duration_s}s > 60s thermal inertia window"
                ),
            )
        )
    if air_domain_actual_w["mast_3"] > mast_air_cap_w or air_domain_committed_w["mast_3"] > mast_air_cap_w:
        dem_m3 = max(air_domain_actual_w["mast_3"], air_domain_committed_w["mast_3"])
        witnesses.append(
            BindingConstraintWitness(
                resource_class="cooling",
                constraint_id="mast_air_cooling_w",
                state_label=state_label,
                demand=dem_m3,
                capacity=mast_air_cap_w,
                margin=mast_air_cap_w - dem_m3,
                unit="W",
                provenance=air_domain_origin["mast_3"],
                detail=f"mast_3 air cooling {dem_m3}W > {mast_air_cap_w}W limit",
            )
        )
    if air_domain_actual_w["hangar_roof"] > hangar_roof_air_cap_w or air_domain_committed_w["hangar_roof"] > hangar_roof_air_cap_w:
        dem_hr = max(air_domain_actual_w["hangar_roof"], air_domain_committed_w["hangar_roof"])
        witnesses.append(
            BindingConstraintWitness(
                resource_class="cooling",
                constraint_id="hangar_roof_air_cooling_w",
                state_label=state_label,
                demand=dem_hr,
                capacity=hangar_roof_air_cap_w,
                margin=hangar_roof_air_cap_w - dem_hr,
                unit="W",
                provenance=air_domain_origin["hangar_roof"],
                detail=f"hangar_roof air cooling {dem_hr}W > {hangar_roof_air_cap_w}W limit",
            )
        )

    # Check Class 3: POSITIONS feasibility (exclusivity + local bay/hangar/ramp/roof rules — H07, L2)
    bay1_split_conflict = len(pos_occupants["bay1"]) > 0 and (
        len(pos_occupants["bay1_a"]) > 0 or len(pos_occupants["bay1_b"]) > 0
    )
    slot_overcrowded = any(
        len(pos_occupants[slot]) > 1
        for slot in ("bay1", "bay1_a", "bay1_b", "bay2", "mast_3", "hangar_roof", "flight_deck", "stern_ramp")
    )
    deck_ramp_conflict = len(pos_occupants["flight_deck"]) > 0 and len(pos_occupants["stern_ramp"]) > 0

    bay1_total_mass_kg = pos_mass_kg["bay1"] + pos_mass_kg["bay1_a"] + pos_mass_kg["bay1_b"]
    bay1_max_mass_kg = int(ship_profile["positions"]["bays"]["bay1"].get("max_mass_kg", 8000))
    bay2_total_mass_kg = pos_mass_kg["bay2"]
    bay2_max_mass_kg = int(ship_profile["positions"]["bays"]["bay2"].get("max_mass_kg", 4000))
    hangar_total_mass_kg = pos_mass_kg["hangar"] + pos_mass_kg["flight_deck"]
    hangar_max_mass_kg = int(ship_profile["positions"]["hangar_and_flight_deck"]["hangar"].get("max_total_mass_kg", 1000))
    hangar_max_airframe_kg = int(ship_profile["positions"]["hangar_and_flight_deck"]["hangar"].get("max_vtol_airframe_mass_kg", 150))
    stern_ramp_mass_kg = pos_mass_kg["stern_ramp"]
    stern_ramp_max_kg = int(ship_profile["positions"]["hangar_and_flight_deck"]["stern_ramp"].get("cradle_assisted_max_mass_kg", 5500))

    hangar_roof_Spec = ship_profile["positions"]["hangar_and_flight_deck"].get("hangar_roof", {})
    hangar_roof_max_mass_kg = int(hangar_roof_Spec.get("max_mass_kg", 150))
    hangar_roof_max_fp_m2 = float(hangar_roof_Spec.get("max_footprint_m2", 2.0))

    # Determine effective roof clearance verification flag
    if hangar_roof_clearance_verified is None:
        eff_roof_verified_arg: Optional[bool] = None
    else:
        eff_roof_verified_arg = bool(
            hangar_roof_clearance_verified or getattr(state, "hangar_roof_clearance_verified", False)
        )
    roof_clearance_eval = evaluate_roof_drone_clearance(
        state,
        ship_profile,
        catalog,
        hangar_roof_clearance_verified=eff_roof_verified_arg,
    )
    hangar_roof_clearance_violation = not bool(roof_clearance_eval["satisfied"])

    positions_feasible = (
        not bay1_split_conflict
        and not slot_overcrowded
        and not deck_ramp_conflict
        and bay1_total_mass_kg <= bay1_max_mass_kg
        and bay2_total_mass_kg <= bay2_max_mass_kg
        and hangar_total_mass_kg <= hangar_max_mass_kg
        and vtol_airframe_mass_kg <= hangar_max_airframe_kg
        and stern_ramp_mass_kg <= stern_ramp_max_kg
        and pos_mass_kg["hangar_roof"] <= hangar_roof_max_mass_kg
        and pos_footprint_m2["hangar_roof"] <= hangar_roof_max_fp_m2
        and not hangar_roof_clearance_violation
    )

    if bay1_split_conflict or slot_overcrowded or deck_ramp_conflict:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="positions",
                constraint_id="bay_or_deck_slot_exclusivity",
                state_label=state_label,
                demand=1,
                capacity=0,
                margin=-1,
                unit="slot",
                provenance="DERIVED",
                detail=f"Position conflict: bay1_split={bay1_split_conflict}, overcrowded={slot_overcrowded}, deck_ramp={deck_ramp_conflict}",
            )
        )
    if bay1_total_mass_kg > bay1_max_mass_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="positions",
                constraint_id="bay1_local_mass_limit_8000kg",
                state_label=state_label,
                demand=bay1_total_mass_kg,
                capacity=bay1_max_mass_kg,
                margin=bay1_max_mass_kg - bay1_total_mass_kg,
                unit="kg",
                provenance="DERIVED",
                detail=f"Bay 1 total mass {bay1_total_mass_kg} kg exceeds local limit {bay1_max_mass_kg} kg (Annex A.3)",
            )
        )
    if bay2_total_mass_kg > bay2_max_mass_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="positions",
                constraint_id="bay2_local_mass_limit_4000kg",
                state_label=state_label,
                demand=bay2_total_mass_kg,
                capacity=bay2_max_mass_kg,
                margin=bay2_max_mass_kg - bay2_total_mass_kg,
                unit="kg",
                provenance="DERIVED",
                detail=f"Bay 2 mass {bay2_total_mass_kg} kg exceeds local limit {bay2_max_mass_kg} kg (Annex A.3)",
            )
        )
    if hangar_total_mass_kg > hangar_max_mass_kg or vtol_airframe_mass_kg > hangar_max_airframe_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="positions",
                constraint_id="hangar_local_mass_limit",
                state_label=state_label,
                demand=max(hangar_total_mass_kg, vtol_airframe_mass_kg),
                capacity=hangar_max_mass_kg if hangar_total_mass_kg > hangar_max_mass_kg else hangar_max_airframe_kg,
                margin=(hangar_max_mass_kg - hangar_total_mass_kg) if hangar_total_mass_kg > hangar_max_mass_kg else (hangar_max_airframe_kg - vtol_airframe_mass_kg),
                unit="kg",
                provenance="DERIVED",
                detail=f"Hangar mass {hangar_total_mass_kg}/{hangar_max_mass_kg} kg or VTOL airframe {vtol_airframe_mass_kg}/{hangar_max_airframe_kg} kg exceeded",
            )
        )
    if stern_ramp_mass_kg > stern_ramp_max_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="positions",
                constraint_id="stern_ramp_mass_limit",
                state_label=state_label,
                demand=stern_ramp_mass_kg,
                capacity=stern_ramp_max_kg,
                margin=stern_ramp_max_kg - stern_ramp_mass_kg,
                unit="kg",
                provenance="DERIVED",
                detail=f"Stern ramp craft mass {stern_ramp_mass_kg} kg exceeds {stern_ramp_max_kg} kg limit",
            )
        )
    if hangar_roof_clearance_violation and roof_clearance_eval.get("witness") is not None:
        witnesses.append(roof_clearance_eval["witness"])

    # Check Class 4: MAST feasibility (mast_3 single guest slot, <=60 kg, <=0.6 m2, <=2 kW air)
    mast_max_mass_kg = int(ship_profile["mast"]["max_mass_per_position_kg"])
    mast_max_fp_m2 = float(ship_profile["mast"]["max_footprint_per_position_m2"])
    mast_feasible = (
        len(pos_occupants["mast_3"]) <= 1
        and mast_3_mass_kg <= mast_max_mass_kg
        and mast_3_footprint_m2 <= mast_max_fp_m2
        and actual_cooling_air_mast_w <= mast_air_cap_w
    )
    if not mast_feasible:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="mast",
                constraint_id="mast_3_slot_limits",
                state_label=state_label,
                demand=len(pos_occupants["mast_3"]),
                capacity=1,
                margin=1 - len(pos_occupants["mast_3"]),
                unit="slot",
                provenance="DERIVED",
                detail=(
                    f"mast_3 occupants={pos_occupants['mast_3']} (max 1), "
                    f"mass={mast_3_mass_kg}/{mast_max_mass_kg}kg, footprint={mast_3_footprint_m2}/{mast_max_fp_m2}m2"
                ),
            )
        )

    # Check Class 5: MASS & STABILITY feasibility (10,000 kg mass and 12,000 kg-eq stability + local bay mass limits)
    max_mass_kg = int(ship_profile["mass_and_stability"]["max_total_guest_mass_kg"])
    max_stab_eq_kg = float(ship_profile["mass_and_stability"]["max_stability_equivalent_kg"])
    mass_stab_feasible = (
        total_mass_kg <= max_mass_kg
        and total_stability_eq_kg <= max_stab_eq_kg
        and bay1_total_mass_kg <= bay1_max_mass_kg
        and bay2_total_mass_kg <= bay2_max_mass_kg
    )
    if total_mass_kg > max_mass_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="mass_stability",
                constraint_id="max_total_guest_mass_kg",
                state_label=state_label,
                demand=total_mass_kg,
                capacity=max_mass_kg,
                margin=max_mass_kg - total_mass_kg,
                unit="kg",
                provenance="DERIVED",
                detail=f"Total installed mass {total_mass_kg} kg exceeds {max_mass_kg} kg limit",
            )
        )
    if total_stability_eq_kg > max_stab_eq_kg:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="mass_stability",
                constraint_id="max_stability_equivalent_kg",
                state_label=state_label,
                demand=total_stability_eq_kg,
                capacity=max_stab_eq_kg,
                margin=round(max_stab_eq_kg - total_stability_eq_kg, 3),
                unit="kg_eq",
                provenance="DERIVED",
                detail=f"Stability equivalent {total_stability_eq_kg} kg-eq exceeds {max_stab_eq_kg} kg-eq limit",
            )
        )

    # Check Class 6: DATA / BANDWIDTH feasibility (Backbone LAN 10 Gbps / 1 Gbps port + SATCOM + EMCON silence)
    max_lan_bps = int(ship_profile["data_links"]["backbone_lan"]["max_total_bps"])
    max_port_bps = int(ship_profile["data_links"]["backbone_lan"]["max_per_port_bps"])
    satcom_cap_bps = 0 if state.emcon_silence else state.satcom_available_bps
    emcon_violated = any(
        state.emcon_silence and ginst.powered_on and catalog[gid].modes[ginst.active_mode].forbids_emcon_silence
        for gid, ginst in state.installed_guests.items()
    )
    data_feasible = (
        total_lan_bps <= max_lan_bps
        and max_port_lan_bps <= max_port_bps
        and total_satcom_bps <= satcom_cap_bps
        and not emcon_violated
    )
    if not data_feasible:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="data",
                constraint_id="satcom_or_emcon_or_lan",
                state_label=state_label,
                demand=total_satcom_bps,
                capacity=satcom_cap_bps,
                margin=satcom_cap_bps - total_satcom_bps,
                unit="bps",
                provenance="DERIVED",
                detail=(
                    f"SATCOM={total_satcom_bps}/{satcom_cap_bps} bps, "
                    f"LAN={total_lan_bps}/{max_lan_bps} bps, emcon_violated={emcon_violated}"
                ),
            )
        )

    # Check Class 7: COMPUTE RACK feasibility (Per-node 25 cores / 102,400 MiB / 1 accel + max 3 cluster accelerators)
    max_storage_gb = int(ship_profile["compute_rack"]["shared_storage_allocatable_gb"])
    max_cluster_acc = int(ship_profile["compute_rack"]["cluster_allocatable_accelerators"])
    total_cluster_acc = sum(u["accelerators"] for u in node_usage.values())

    compute_feasible = total_storage_gb <= max_storage_gb and total_cluster_acc <= max_cluster_acc
    for nid, usage in sorted(node_usage.items()):
        nspec = node_specs[nid]
        if (
            usage["cores"] > int(nspec["allocatable_cores"])
            or usage["ram_mib"] > int(nspec["allocatable_ram_mib"])
            or usage["accelerators"] > int(nspec["allocatable_accelerators"])
        ):
            compute_feasible = False
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="compute",
                    constraint_id=f"rack_{nid}_capacity",
                    state_label=state_label,
                    demand=usage["cores"],
                    capacity=int(nspec["allocatable_cores"]),
                    margin=int(nspec["allocatable_cores"]) - usage["cores"],
                    unit="cores",
                    provenance="DERIVED",
                    detail=(
                        f"{nid} usage cores={usage['cores']}/{nspec['allocatable_cores']}, "
                        f"ram_mib={usage['ram_mib']}/{nspec['allocatable_ram_mib']}, "
                        f"accel={usage['accelerators']}/{nspec['allocatable_accelerators']}"
                    ),
                )
            )
    if total_cluster_acc > max_cluster_acc:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="compute",
                constraint_id="cluster_allocatable_accelerators",
                state_label=state_label,
                demand=total_cluster_acc,
                capacity=max_cluster_acc,
                margin=max_cluster_acc - total_cluster_acc,
                unit="accelerators",
                provenance="DERIVED",
                detail=f"Cluster accelerators {total_cluster_acc} > 80% indivisible pool {max_cluster_acc}",
            )
        )
    if total_storage_gb > max_storage_gb:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="compute",
                constraint_id="shared_storage_allocatable_gb",
                state_label=state_label,
                demand=total_storage_gb,
                capacity=max_storage_gb,
                margin=max_storage_gb - total_storage_gb,
                unit="GB",
                provenance="DERIVED",
                detail=f"Shared storage {total_storage_gb} GB > allocatable {max_storage_gb} GB",
            )
        )

    # Check Class 8: TIME / DEADLINES feasibility (measured elapsed time if not provided — H17)
    measured_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 4)
    effective_calc_ms = float(calc_elapsed_ms) if calc_elapsed_ms is not None else measured_ms
    time_cap_ms = 200.0
    time_feasible = 0.0 <= effective_calc_ms <= time_cap_ms

    # Check protected load infeasibility (PHY-01, DEG-04)
    infeasible_protected_load = protected_power_w > power_cap_w or (power_cap_w == 0 and actual_power_w > 0)
    infeasible_reason = None
    if infeasible_protected_load:
        infeasible_reason = (
            f"INFEASIBLE_PROTECTED_LOAD: protected_power_w={protected_power_w}W, "
            f"actual_power_w={actual_power_w}W vs power_reserve_w={power_cap_w}W"
        )

    overall_feasible = all([
        power_feasible,
        cooling_feasible,
        positions_feasible,
        mast_feasible,
        mass_stab_feasible,
        data_feasible,
        compute_feasible,
        time_feasible,
    ]) and not infeasible_protected_load

    roof_eval_dict = dict(roof_clearance_eval)
    if isinstance(roof_eval_dict.get("witness"), BindingConstraintWitness):
        roof_eval_dict["witness"] = roof_eval_dict["witness"].to_dict()

    air_domains_report = {
        "mast_3": {
            "capacity_w": mast_air_cap_w,
            "committed_w": air_domain_committed_w["mast_3"],
            "actual_w": actual_cooling_air_mast_w,
            "free_w": mast_air_cap_w - air_domain_committed_w["mast_3"],
            "feasible": (
                actual_cooling_air_mast_w <= mast_air_cap_w
                and air_domain_committed_w["mast_3"] <= mast_air_cap_w
            ),
            "value_origin": air_domain_origin["mast_3"],
            "occupants": list(air_domain_occupants["mast_3"]),
        },
        "hangar_roof": {
            "capacity_w": hangar_roof_air_cap_w,
            "committed_w": air_domain_committed_w["hangar_roof"],
            "actual_w": actual_cooling_air_hangar_roof_w,
            "free_w": hangar_roof_air_cap_w - air_domain_committed_w["hangar_roof"],
            "feasible": (
                actual_cooling_air_hangar_roof_w <= hangar_roof_air_cap_w
                and air_domain_committed_w["hangar_roof"] <= hangar_roof_air_cap_w
            ),
            "value_origin": air_domain_origin["hangar_roof"],
            "occupants": list(air_domain_occupants["hangar_roof"]),
        },
    }

    return {
        "tick_seq": state.tick_seq,
        "sim_time_s": state.sim_time_s,
        "vessel_mode": state.vessel_mode,
        "feasible": overall_feasible,
        "infeasible_protected_load": infeasible_protected_load,
        "infeasible_reason": infeasible_reason,
        "binding_witnesses": [w.to_dict() for w in witnesses],
        "component_breakdown": component_breakdown,
        "firm_reservations_ledger": firm_reservations_ledger,
        "roof_clearance_evaluation": roof_eval_dict,
        "classes": {
            "power": {
                "unit": "W",
                "capacity": int(ship_profile["power"]["nominal_guest_reserve_w"]),
                "degraded_capacity": power_cap_w,
                "committed": committed_power_w,
                "actual": actual_power_w,
                "free": power_cap_w - committed_power_w,
                "feasible": power_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    lid: {
                        "max_power_w": line_max_w[lid],
                        "committed_w": line_committed_w[lid],
                        "actual_w": line_actual_w[lid],
                        "free_w": line_max_w[lid] - line_committed_w[lid],
                        "feasible": line_committed_w[lid] <= line_max_w[lid] and line_actual_w[lid] <= line_max_w[lid],
                    }
                    for lid in sorted(line_feeds.keys())
                },
            },
            "cooling": {
                "unit": "W",
                "capacity": nom_cool_w,
                "degraded_capacity": eff_cool_w,
                "committed": committed_cooling_water_w,
                "actual": actual_cooling_water_w,
                "free": eff_cool_w - committed_cooling_water_w,
                "feasible": cooling_feasible,
                "provenance": temp_prov.split(":")[0] if ":" in temp_prov else "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "seawater_temp_c": eff_temp_c,
                    "curve_policy": cool_policy,
                    "thermal_inertia_available_boost_w": inertia_boost_w,
                    "effective_water_cap_with_inertia_w": effective_water_cap_with_inertia_w,
                    "mast_air_cooling": {
                        "capacity_w": mast_air_cap_w,
                        "committed_w": air_domain_committed_w["mast_3"],
                        "actual_w": actual_cooling_air_mast_w,
                        "free_w": mast_air_cap_w - actual_cooling_air_mast_w,
                        "feasible": actual_cooling_air_mast_w <= mast_air_cap_w,
                    },
                    "hangar_roof_air_cooling": {
                        "capacity_w": hangar_roof_air_cap_w,
                        "committed_w": air_domain_committed_w["hangar_roof"],
                        "actual_w": actual_cooling_air_hangar_roof_w,
                        "free_w": hangar_roof_air_cap_w - actual_cooling_air_hangar_roof_w,
                        "feasible": actual_cooling_air_hangar_roof_w <= hangar_roof_air_cap_w,
                    },
                    "air_cooling_domains": air_domains_report,
                    "total_thermal_dissipation_w": actual_cooling_water_w + total_cooling_air_w,
                },
            },
            "positions": {
                "unit": "slots",
                "capacity": 5,
                "degraded_capacity": 5,
                "committed": sum(1 for k in ("bay1", "bay1_a", "bay1_b", "bay2", "hangar", "hangar_roof") if pos_occupants[k]),
                "actual": sum(1 for k in ("bay1", "bay1_a", "bay1_b", "bay2", "hangar", "hangar_roof") if pos_occupants[k]),
                "free": max(0, 5 - sum(1 for k in ("bay1", "bay1_a", "bay1_b", "bay2", "hangar") if pos_occupants[k])),
                "feasible": positions_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "bay1_20ft": pos_occupants["bay1"],
                    "bay1_a_10ft": pos_occupants["bay1_a"],
                    "bay1_b_10ft": pos_occupants["bay1_b"],
                    "bay1_total_mass_kg": bay1_total_mass_kg,
                    "bay1_max_mass_kg": bay1_max_mass_kg,
                    "bay2_10ft": pos_occupants["bay2"],
                    "bay2_total_mass_kg": bay2_total_mass_kg,
                    "bay2_max_mass_kg": bay2_max_mass_kg,
                    "hangar": pos_occupants["hangar"],
                    "hangar_roof": pos_occupants["hangar_roof"],
                    "hangar_total_mass_kg": hangar_total_mass_kg,
                    "hangar_max_mass_kg": hangar_max_mass_kg,
                    "flight_deck": pos_occupants["flight_deck"],
                    "stern_ramp": pos_occupants["stern_ramp"],
                    "hangar_roof_drone_clearance": roof_eval_dict,
                },
            },
            "mast": {
                "unit": "slots",
                "capacity": 1,
                "degraded_capacity": 1,
                "committed": len(pos_occupants["mast_3"]),
                "actual": len(pos_occupants["mast_3"]),
                "free": max(0, 1 - len(pos_occupants["mast_3"])),
                "feasible": mast_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "total_physical_slots": 3,
                    "ship_reserved_slots": 2,
                    "guest_allocatable_slots": 1,
                    "fixed_occupied": ["mast_1", "mast_2"],
                    "guest_slot_mast_3": {
                        "owner": pos_occupants["mast_3"][0] if pos_occupants["mast_3"] else None,
                        "mass_kg": mast_3_mass_kg,
                        "max_mass_kg": mast_max_mass_kg,
                        "footprint_m2": mast_3_footprint_m2,
                        "max_footprint_m2": mast_max_fp_m2,
                        "air_cooling_w": actual_cooling_air_mast_w,
                        "max_air_cooling_w": mast_air_cap_w,
                    },
                },
            },
            "mass_stability": {
                "unit": "kg",
                "capacity": max_mass_kg,
                "degraded_capacity": max_mass_kg,
                "committed": total_mass_kg,
                "actual": total_mass_kg,
                "free": max_mass_kg - total_mass_kg,
                "feasible": mass_stab_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "mass_margin_kg": {
                        "max_kg": max_mass_kg,
                        "actual_kg": total_mass_kg,
                        "free_kg": max_mass_kg - total_mass_kg,
                        "feasible": total_mass_kg <= max_mass_kg,
                    },
                    "stability_equivalent_kg": {
                        "max_kg_eq": max_stab_eq_kg,
                        "actual_kg_eq": total_stability_eq_kg,
                        "free_kg_eq": round(max_stab_eq_kg - total_stability_eq_kg, 3),
                        "feasible": total_stability_eq_kg <= max_stab_eq_kg,
                    },
                    "bay1_mass_kg": {
                        "max_kg": bay1_max_mass_kg,
                        "actual_kg": bay1_total_mass_kg,
                        "free_kg": bay1_max_mass_kg - bay1_total_mass_kg,
                        "feasible": bay1_total_mass_kg <= bay1_max_mass_kg,
                    },
                    "bay2_mass_kg": {
                        "max_kg": bay2_max_mass_kg,
                        "actual_kg": bay2_total_mass_kg,
                        "free_kg": bay2_max_mass_kg - bay2_total_mass_kg,
                        "feasible": bay2_total_mass_kg <= bay2_max_mass_kg,
                    },
                },
            },
            "data": {
                "unit": "bps",
                "capacity": int(ship_profile["data_links"]["satcom"]["nominal_bps"]),
                "degraded_capacity": satcom_cap_bps,
                "committed": total_satcom_bps,
                "actual": total_satcom_bps,
                "free": satcom_cap_bps - total_satcom_bps,
                "feasible": data_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "backbone_lan_bps": {
                        "max_total_bps": max_lan_bps,
                        "actual_total_bps": total_lan_bps,
                        "max_per_port_bps": max_port_bps,
                        "highest_port_bps": max_port_lan_bps,
                        "feasible": total_lan_bps <= max_lan_bps and max_port_lan_bps <= max_port_bps,
                    },
                    "satcom_bps": {
                        "available_bps": satcom_cap_bps,
                        "actual_bps": total_satcom_bps,
                        "free_bps": satcom_cap_bps - total_satcom_bps,
                        "emcon_silence": state.emcon_silence,
                        "emcon_violated": emcon_violated,
                    },
                },
            },
            "compute": {
                "unit": "cores",
                "capacity": 100,
                "degraded_capacity": 100,
                "committed": sum(u["cores"] for u in node_usage.values()),
                "actual": sum(u["cores"] for u in node_usage.values()),
                "free": 100 - sum(u["cores"] for u in node_usage.values()),
                "feasible": compute_feasible,
                "provenance": "DERIVED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "shared_storage_gb": {
                        "allocatable_gb": max_storage_gb,
                        "used_gb": total_storage_gb,
                        "free_gb": max_storage_gb - total_storage_gb,
                        "feasible": total_storage_gb <= max_storage_gb,
                    },
                    "cluster_accelerators": {
                        "allocatable": max_cluster_acc,
                        "used": total_cluster_acc,
                        "free": max_cluster_acc - total_cluster_acc,
                        "feasible": total_cluster_acc <= max_cluster_acc,
                    },
                    "nodes": {
                        nid: {
                            "allocatable_cores": int(node_specs[nid]["allocatable_cores"]),
                            "used_cores": node_usage[nid]["cores"],
                            "free_cores": int(node_specs[nid]["allocatable_cores"]) - node_usage[nid]["cores"],
                            "allocatable_ram_mib": int(node_specs[nid]["allocatable_ram_mib"]),
                            "used_ram_mib": node_usage[nid]["ram_mib"],
                            "free_ram_mib": int(node_specs[nid]["allocatable_ram_mib"]) - node_usage[nid]["ram_mib"],
                            "allocatable_accelerators": int(node_specs[nid]["allocatable_accelerators"]),
                            "used_accelerators": node_usage[nid]["accelerators"],
                            "free_accelerators": int(node_specs[nid]["allocatable_accelerators"]) - node_usage[nid]["accelerators"],
                        }
                        for nid in sorted(node_usage.keys())
                    },
                },
            },
            "time": {
                "unit": "ms",
                "capacity": time_cap_ms,
                "degraded_capacity": time_cap_ms,
                "committed": effective_calc_ms,
                "actual": effective_calc_ms,
                "free": round(time_cap_ms - effective_calc_ms, 4),
                "feasible": time_feasible,
                "provenance": "MEASURED",
                "source_time_ns": str(source_time_ns),
                "local_limits": {
                    "tick_deadline_ms": 200.0,
                    "scenario_b_arbitration_deadline_ms": 2000.0,
                    "scenario_a_admission_deadline_ms": 30000.0,
                },
            },
        },
    }

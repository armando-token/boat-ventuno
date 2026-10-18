"""Scenario C Crew Regime Scheduler (14/6/0), A' Operational Windows, Annex A.6 Transition Timing, and Lost-Feed Resilience (B7, ROB-03)."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import yaml

from . import budgets as _budgets_mod
from .budgets import evaluate_eight_class_budgets
from .models import BindingConstraintWitness, GuestManifest, StrictValidationError
from .ship_state import ShipOperationalState

DEFAULT_TRANSITION_TIMING_POLICY: Dict[str, Any] = {
    "schema_version": "sdv-transition-timing-policy/1.0",
    "policy_id": "ANNEX_A6_CAUSAL_TIMING_POLICY_V1",
    "policy_version": 1,
    "recovery_target_speed_kn": 5.0,
    "course_settle_s": 90,
    "speed_inter_band_per_band_s": 180,
    "speed_intra_band_s": 60,
    "enter_quiet_state_s": 180,
    "exit_quiet_state_s": 0,
    "deck_clear_crew_14_s": 240,
    "deck_clear_crew_6_s": 600,
    "deck_clear_crew_0_s": None,
    "human_confirm_crew_14_s": 30,
    "human_confirm_crew_6_s": 120,
    "human_confirm_crew_0_s": 0,
    "generator_restart_s": 780,
    "cooling_loop_at_sea_repair_allowed": False,
    "deadline_boundary_inclusive": True,
    "speed_bands_kn": {
        "slow_survey": {"min_kn": 3.0, "max_kn": 5.0},
        "patrol_transit": {"min_kn": 10.0, "max_kn": 14.0},
        "fast_dash": {"min_kn": 18.0, "max_kn": 25.0},
    },
}

_SPEED_BAND_ORDER: Dict[str, int] = {
    "slow_survey": 0,
    "patrol_transit": 1,
    "fast_dash": 2,
}


def load_transition_timing_policy(repo_root: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load `data/ship/transition_timing_policy.yaml` if available, falling back to `DEFAULT_TRANSITION_TIMING_POLICY`."""
    policy = copy.deepcopy(DEFAULT_TRANSITION_TIMING_POLICY)
    candidates: List[Path] = []
    if repo_root is not None:
        candidates.append(Path(repo_root) / "data" / "ship" / "transition_timing_policy.yaml")
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "ship" / "transition_timing_policy.yaml")
    candidates.append(Path(__file__).resolve().parent / "_bundled" / "data" / "ship" / "transition_timing_policy.yaml")
    for p in candidates:
        if p.exists():
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                policy.update(raw)
            break
    return policy


def classify_speed_band(speed_kn: float) -> str:
    """Classify vessel speed (kn) into Annex A.6 operational speed band (`slow_survey`, `patrol_transit`, `fast_dash`)."""
    spd = float(speed_kn)
    if spd <= 5.0:
        return "slow_survey"
    if spd <= 14.0:
        return "patrol_transit"
    return "fast_dash"


def compute_speed_transition_duration_s(
    from_speed_kn: float,
    to_speed_kn: float,
    policy: Optional[Dict[str, Any]] = None,
) -> int:
    """Compute Annex A.6 / §9.2 speed change duration in seconds (60 s intra-band, 180 s per inter-band crossing)."""
    pol = policy or DEFAULT_TRANSITION_TIMING_POLICY
    f_spd = float(from_speed_kn)
    t_spd = float(to_speed_kn)
    if abs(t_spd - f_spd) < 1e-6:
        return 0
    b_from = classify_speed_band(f_spd)
    b_to = classify_speed_band(t_spd)
    band_diff = abs(_SPEED_BAND_ORDER[b_to] - _SPEED_BAND_ORDER[b_from])
    if band_diff == 0:
        return int(pol.get("speed_intra_band_s", 60))
    return int(band_diff * int(pol.get("speed_inter_band_per_band_s", 180)))


def compute_course_settle_duration_s(
    from_heading_deg: float = 0.0,
    to_heading_deg: float = 180.0,
    policy: Optional[Dict[str, Any]] = None,
    *,
    heading_delta_deg: Optional[float] = None,
    tolerance_deg: float = 15.0,
) -> int:
    """Compute Annex A.6 course change + roll settle duration in seconds (90 s if heading change > tolerance)."""
    pol = policy or DEFAULT_TRANSITION_TIMING_POLICY
    if heading_delta_deg is not None:
        delta = abs(float(heading_delta_deg))
    else:
        raw_diff = (float(to_heading_deg) - float(from_heading_deg) + 180.0) % 360.0 - 180.0
        delta = abs(raw_diff)
    if delta <= tolerance_deg:
        return 0
    return int(pol.get("course_settle_s", 90))


def compute_quiet_transition_duration_s(
    target_quiet: bool | str = True,
    policy: Optional[Dict[str, Any]] = None,
    *,
    current_quiet: Optional[bool] = None,
    direction: Optional[str] = None,
) -> int:
    """Compute Annex A.6 transition duration to enter (180 s) or exit (0 s) quiet acoustic state."""
    pol = policy or DEFAULT_TRANSITION_TIMING_POLICY
    if direction is not None:
        entering = str(direction).upper() in ("ENTER", "ENTER_QUIET", "TO_QUIET", "QUIET")
    elif isinstance(target_quiet, str):
        entering = target_quiet.upper() in ("ENTER", "ENTER_QUIET", "TO_QUIET", "QUIET", "TRUE")
    else:
        entering = bool(target_quiet)

    if entering:
        if current_quiet is True:
            return 0
        return int(pol.get("enter_quiet_state_s", 180))
    return int(pol.get("exit_quiet_state_s", 0))


def _resolve_crew_count(crew_count_or_regime: int | str) -> int:
    if isinstance(crew_count_or_regime, str):
        key = crew_count_or_regime.strip().upper()
        if "14" in key or key == "FULL":
            return 14
        if "6" in key or key == "REDUCED":
            return 6
        if "0" in key or "UNCREWED" in key:
            return 0
        return int(crew_count_or_regime)
    return int(crew_count_or_regime)


def compute_deck_clear_duration_s(
    crew_count: int | str = 14,
    deck_already_clear: bool = False,
    policy: Optional[Dict[str, Any]] = None,
    *,
    deck_clear: Optional[bool] = None,
    deck_clear_verified: Optional[bool] = None,
) -> Optional[int]:
    """Compute Annex A.6 / A.8 deck clearing and safety verification duration (240 s for 14 crew, 600 s for 6, None for 0)."""
    pol = policy or DEFAULT_TRANSITION_TIMING_POLICY
    c_cnt = _resolve_crew_count(crew_count)
    is_clear = deck_already_clear if deck_clear is None else bool(deck_clear)
    if deck_clear_verified is False:
        is_clear = False
    if is_clear:
        return 0
    if c_cnt >= 14:
        return int(pol.get("deck_clear_crew_14_s", 240))
    if c_cnt >= 6:
        return int(pol.get("deck_clear_crew_6_s", 600))
    return pol.get("deck_clear_crew_0_s", None)


def compute_human_confirmation_duration_s(
    crew_count: int | str = 14,
    policy: Optional[Dict[str, Any]] = None,
) -> int:
    """Compute Annex A.8 human confirmation latency (30 s for 14 crew, 120 s for 6 crew, 0 s for 0 crew)."""
    pol = policy or DEFAULT_TRANSITION_TIMING_POLICY
    c_cnt = _resolve_crew_count(crew_count)
    if c_cnt >= 14:
        return int(pol.get("human_confirm_crew_14_s", 30))
    if c_cnt >= 6:
        return int(pol.get("human_confirm_crew_6_s", 120))
    return int(pol.get("human_confirm_crew_0_s", 0))


def evaluate_roof_drone_clearance_safe(
    state: ShipOperationalState,
    ship_profile: Optional[Dict[str, Any]],
    catalog: Dict[str, GuestManifest],
    *,
    hangar_roof_clearance_verified: Optional[bool] = None,
) -> Dict[str, Any]:
    """Evaluate `hangar_roof_drone_clearance` using `budgets.evaluate_roof_drone_clearance` when available, with full fallback."""
    sp = ship_profile or {}
    fn = getattr(_budgets_mod, "evaluate_roof_drone_clearance", None)
    if callable(fn):
        try:
            if hangar_roof_clearance_verified is not None:
                res = fn(
                    state,
                    sp,
                    catalog,
                    hangar_roof_clearance_verified=hangar_roof_clearance_verified,
                )
            else:
                res = fn(state, sp, catalog)
        except TypeError:
            res = fn(state, sp, catalog)
        if isinstance(res, dict):
            satisfied = bool(res.get("satisfied", res.get("feasible", True)))
            res.setdefault("satisfied", satisfied)
            res.setdefault("feasible", satisfied)
            if not satisfied and not res.get("witness"):
                w_list = res.get("witnesses") or res.get("binding_witnesses") or []
                if w_list:
                    w0 = w_list[0]
                    res["witness"] = w0 if isinstance(w0, BindingConstraintWitness) else BindingConstraintWitness(**w0)
                else:
                    res["witness"] = BindingConstraintWitness(
                        resource_class="positions",
                        constraint_id="hangar_roof_drone_clearance",
                        state_label=f"T+{state.sim_time_s}s:{state.vessel_mode}",
                        demand=1,
                        capacity=0,
                        margin=-1,
                        unit="clearance",
                        provenance="DERIVED",
                        detail=str(res.get("reason", "hangar_roof occupied with G4 without valid roof clearance verification")),
                    )
            elif isinstance(res.get("witness"), dict):
                res["witness"] = BindingConstraintWitness(**res["witness"])
            return res

    # Fallback evaluation if budgets.evaluate_roof_drone_clearance is not yet loaded
    roof_occupants: List[str] = []
    for gid, ginst in sorted(state.installed_guests.items()):
        for sc_id, pos in ginst.placements.items():
            if pos == "hangar_roof":
                roof_occupants.append(f"{gid}:{sc_id}")
    g4_installed = "G4" in state.installed_guests
    requires_clearance = len(roof_occupants) > 0 and g4_installed

    policy_cfg = sp.get("roof_drone_clearance_policy", {}) if isinstance(sp, dict) else {}
    compatibility = str(policy_cfg.get("compatibility", "CONDITIONAL")).upper()
    policy_id = str(policy_cfg.get("policy_id", "ROOF_OUTSIDE_G4_RECOVERY_V1"))
    policy_version = int(policy_cfg.get("policy_version", 1))
    layout_version = str(policy_cfg.get("layout_version", "VESSEL_BASELINE_LAYOUT_V1"))

    if not requires_clearance:
        return {
            "satisfied": True,
            "feasible": True,
            "requires_clearance": False,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": g4_installed,
            "verified": True,
            "compatibility": compatibility,
            "policy_id": policy_id,
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
            constraint_id="hangar_roof_drone_clearance",
            state_label=f"T+{state.sim_time_s}s:{state.vessel_mode}",
            demand=1,
            capacity=0,
            margin=-1,
            unit="clearance",
            provenance="DERIVED",
            detail=reason,
        )
        return {
            "satisfied": False,
            "feasible": False,
            "requires_clearance": True,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": True,
            "verified": False,
            "compatibility": compatibility,
            "policy_id": policy_id,
            "reason": reason,
            "required_condition": reason,
            "witness": witness,
        }

    # Check verification stamp or explicit boolean
    state_stamp = getattr(state, "roof_clearance_verification", None)
    is_valid_fn = getattr(state, "is_roof_clearance_valid", None)
    if hangar_roof_clearance_verified is not None:
        eff_verified = bool(hangar_roof_clearance_verified)
        if eff_verified and isinstance(state_stamp, dict) and state_stamp:
            # If stamp was explicitly invalidated (e.g. verified=False or mismatched layout/policy), check it
            if state_stamp.get("verified") is False or state_stamp.get("invalidated", False):
                eff_verified = False
    elif callable(is_valid_fn):
        try:
            eff_verified = bool(is_valid_fn(sp))
        except TypeError:
            eff_verified = bool(is_valid_fn())
    elif isinstance(state_stamp, dict) and state_stamp:
        eff_verified = (
            bool(state_stamp.get("verified", False))
            and not bool(state_stamp.get("invalidated", False))
            and str(state_stamp.get("policy_id", policy_id)) == policy_id
            and int(state_stamp.get("policy_version", policy_version)) == policy_version
            and str(state_stamp.get("layout_version", layout_version)) == layout_version
            and str(state_stamp.get("antenna_position", "hangar_roof")) == "hangar_roof"
        )
    else:
        eff_verified = bool(getattr(state, "hangar_roof_clearance_verified", False))

    if eff_verified:
        return {
            "satisfied": True,
            "feasible": True,
            "requires_clearance": True,
            "hangar_roof_occupants": roof_occupants,
            "g4_installed": True,
            "verified": True,
            "compatibility": compatibility,
            "policy_id": policy_id,
            "reason": f"VERIFIED:{policy_id}:v{policy_version}:{layout_version}",
            "required_condition": None,
            "witness": None,
        }

    cond_text = (
        f"Verify G4 VTOL drone rotor clearance over hangar_roof antenna under policy {policy_id} "
        f"(excluded zones: G4_ROTOR_DISC, G4_RECOVERY_CORRIDOR; stability factor x1.2)"
    )
    witness = BindingConstraintWitness(
        resource_class="positions",
        constraint_id="hangar_roof_drone_clearance",
        state_label=f"T+{state.sim_time_s}s:{state.vessel_mode}",
        demand=1,
        capacity=0,
        margin=-1,
        unit="clearance",
        provenance="DERIVED",
        detail=cond_text,
    )
    return {
        "satisfied": False,
        "feasible": False,
        "requires_clearance": True,
        "hangar_roof_occupants": roof_occupants,
        "g4_installed": True,
        "verified": False,
        "compatibility": compatibility,
        "policy_id": policy_id,
        "reason": cond_text,
        "required_condition": cond_text,
        "witness": witness,
    }


CREW_REGIMES: Dict[str, Dict[str, Any]] = {
    "FULL_CREW_14": {
        "crew_total": 14,
        "watch_bridge_engine": 5,
        "available_for_guest_ops": 9,
        "max_concurrent_manual_deck_ops": 2,
        "manual_launch_recovery_permitted": True,
        "at_sea_vehicle_replacement_permitted": True,
        "fault_intervention_permitted": True,
        "deck_clear_duration_s": 240,
        "human_confirmation_s": 30,
    },
    "REDUCED_CREW_6": {
        "crew_total": 6,
        "watch_bridge_engine": 4,
        "available_for_guest_ops": 2,
        "max_concurrent_manual_deck_ops": 1,
        "manual_launch_recovery_permitted": True,
        "at_sea_vehicle_replacement_permitted": False,
        "fault_intervention_permitted": True,
        "deck_clear_duration_s": 600,
        "human_confirmation_s": 120,
    },
    "UNCREWED_0": {
        "crew_total": 0,
        "watch_bridge_engine": 0,
        "available_for_guest_ops": 0,
        "max_concurrent_manual_deck_ops": 0,
        "manual_launch_recovery_permitted": False,
        "at_sea_vehicle_replacement_permitted": False,
        "fault_intervention_permitted": False,
        "deck_clear_duration_s": None,
        "human_confirmation_s": 0,
    },
}


def evaluate_a_prime_window(
    guest: GuestManifest,
    operation_type: str,
    state: ShipOperationalState,
    catalog: Dict[str, GuestManifest],
    ship_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate whether an operation ('LAUNCH_RECOVERY', 'VEHICLE_REPLACEMENT_AT_SEA', 'AT_SEA_INSTALL', 'SURVEY_START') is permitted.

    Explicitly separates normal flight launch/recovery window (5-12 kn, wind <= 25 kn) from the
    stricter A' at-sea vehicle replacement / installation window (4-8 kn, wind <= 20 kn, sea_state <= 3),
    enforces flight deck clearance (`state.deck_clear` and `deck_clear_verified`), stern ramp exclusivity,
    at-sea package mass limit (`<= 250 kg`), port-only bay/mast installation rules (Annex A.4, A.5),
    acoustic survey deck-machinery lockout (`G3` `forbids_deck_machinery_while_active`), `hangar_roof_drone_clearance`
    for `G4` launch/recovery, `quiet_acoustic_state` for `G3` `SURVEY_START`, and pending transition completion guards.
    """
    reasons: List[str] = []
    sc = guest.ship_state_constraints

    # 0. Check pending / in-progress transitions on state.pending_transitions (a requested transition does NOT satisfy a guard until COMPLETED)
    pending_transitions = getattr(state, "pending_transitions", []) or []
    active_blocking_transitions: List[str] = []
    for tr in pending_transitions:
        tr_status = getattr(tr, "status", tr.get("status") if isinstance(tr, dict) else "COMPLETED")
        if tr_status in ("COMPLETED", "CANCELLED"):
            continue
        tr_id = getattr(tr, "transition_id", tr.get("transition_id") if isinstance(tr, dict) else "UNKNOWN")
        tr_type = getattr(tr, "transition_type", tr.get("transition_type") if isinstance(tr, dict) else "")
        tr_owner = getattr(tr, "owner_id", tr.get("owner_id") if isinstance(tr, dict) else "")
        if operation_type == "LAUNCH_RECOVERY" and (
            tr_owner in (guest.guest_id, "VESSEL", "SHIP")
            or any(k in str(tr_type).upper() or k in str(tr_id).upper() for k in ("SPEED", "COURSE", "HEADING", "DECK", "HUMAN", "TURN"))
        ):
            active_blocking_transitions.append(f"{tr_id}({tr_status})")
            reasons.append(f"pending_transition_not_completed: {tr_id} status={tr_status} (guard requires COMPLETED)")
        elif operation_type == "SURVEY_START" and (
            tr_owner in (guest.guest_id, "VESSEL", "SHIP")
            or any(k in str(tr_type).upper() or k in str(tr_id).upper() for k in ("QUIET", "SPEED", "COURSE"))
        ):
            active_blocking_transitions.append(f"{tr_id}({tr_status})")
            reasons.append(f"pending_transition_not_completed: {tr_id} status={tr_status} (guard requires COMPLETED)")
        elif operation_type in ("VEHICLE_REPLACEMENT_AT_SEA", "AT_SEA_INSTALL") and (
            tr_owner in (guest.guest_id, "VESSEL", "SHIP")
            or any(k in str(tr_type).upper() or k in str(tr_id).upper() for k in ("DECK", "SPEED", "COURSE"))
        ):
            active_blocking_transitions.append(f"{tr_id}({tr_status})")
            reasons.append(f"pending_transition_not_completed: {tr_id} status={tr_status} (guard requires COMPLETED)")

    # 1. Flight deck / working deck clearance check (Annex A.5 & A.6)
    deck_verified = bool(getattr(state, "deck_clear_verified", state.deck_clear))
    if operation_type in ("LAUNCH_RECOVERY", "VEHICLE_REPLACEMENT_AT_SEA", "AT_SEA_INSTALL"):
        if not state.deck_clear or not deck_verified:
            reasons.append("flight_deck_not_clear: state.deck_clear is False or deck_clear_verified is False (Annex A.5/A.6)")

    # 2. Stern ramp & flight deck exclusivity check (Annex A.5)
    if sc.requires_stern_ramp_free or guest.guest_id in ("G3", "G4"):
        for other_gid, other_inst in state.installed_guests.items():
            if other_gid == guest.guest_id:
                continue
            for _, pos in other_inst.placements.items():
                if pos == "stern_ramp":
                    reasons.append(f"stern_ramp occupied by {other_gid} (Annex A.5 exclusivity)")

    # 3. Check if any active guest forbids deck machinery (e.g. G3 in SURVEY_QUIET)
    if operation_type != "SURVEY_START":
        for gid, ginst in state.installed_guests.items():
            if gid == guest.guest_id or not ginst.powered_on:
                continue
            other_man = catalog.get(gid)
            if other_man and other_man.ship_state_constraints.forbids_deck_machinery_while_active:
                if ginst.active_mode in ("SURVEY_QUIET", "QUIET_SURVEY_ACTIVE"):
                    reasons.append(f"Active guest {gid} ({ginst.active_mode}) forbids deck machinery due to acoustic survey")

    roof_clearance_satisfied = True
    if operation_type == "LAUNCH_RECOVERY":
        min_spd = sc.min_speed_kn_for_operation
        max_spd = sc.max_speed_kn_for_operation
        max_wind = sc.max_wind_kn_for_operation
        max_ss = sc.max_sea_state_for_operation
        req_crew = 2 if guest.guest_id in ("G2", "G4") else 0

        if not (min_spd <= state.speed_kn <= max_spd):
            reasons.append(f"speed {state.speed_kn} kn outside normal op window [{min_spd}, {max_spd}] kn")
        if state.wind_kn > max_wind:
            reasons.append(f"wind {state.wind_kn} kn > max {max_wind} kn")
        if state.sea_state > max_ss:
            reasons.append(f"sea_state {state.sea_state} > max {max_ss}")
        if sc.requires_heading_into_wind_for_launch_recovery and abs(state.heading_deg - state.wind_deg) > 15.0:
            reasons.append(f"vessel heading {state.heading_deg} deg not aligned into wind {state.wind_deg} deg")
        if state.available_crew < req_crew:
            reasons.append(f"available_crew {state.available_crew} < required {req_crew} for manual launch/recovery")

        if guest.guest_id == "G4":
            clearance_eval = evaluate_roof_drone_clearance_safe(state, ship_profile or {}, catalog)
            roof_clearance_satisfied = bool(clearance_eval["satisfied"])
            if not roof_clearance_satisfied:
                reasons.append(
                    f"hangar_roof_drone_clearance: {clearance_eval.get('reason', 'hangar_roof clearance not verified for G4 launch/recovery')}"
                )

    elif operation_type == "SURVEY_START":
        quiet_ok = bool(getattr(state, "quiet_acoustic_state", state.vessel_mode == "QUIET_SURVEY"))
        if not quiet_ok:
            reasons.append(
                "quiet_acoustic_state_not_established: vessel requires 180s quiet acoustic state settle before G3 SURVEY_START (Annex A.6)"
            )
        min_spd = sc.min_speed_kn_for_operation
        max_spd = sc.max_speed_kn_for_operation
        if not (min_spd <= state.speed_kn <= max_spd):
            reasons.append(f"speed {state.speed_kn} kn outside survey speed window [{min_spd}, {max_spd}] kn")

    elif operation_type == "VEHICLE_REPLACEMENT_AT_SEA":
        vr = guest.vehicle_replacement
        if not vr.supported_at_sea:
            reasons.append(f"guest {guest.guest_id} does not support at-sea vehicle replacement")
        else:
            if not (vr.min_speed_kn <= state.speed_kn <= vr.max_speed_kn):
                reasons.append(f"speed {state.speed_kn} kn outside A' replacement window [{vr.min_speed_kn}, {vr.max_speed_kn}] kn")
            if state.wind_kn > vr.max_wind_kn:
                reasons.append(f"wind {state.wind_kn} kn > A' replacement max {vr.max_wind_kn} kn")
            if state.sea_state > vr.max_sea_state:
                reasons.append(f"sea_state {state.sea_state} > A' replacement max {vr.max_sea_state}")
            if state.available_crew < vr.min_crew_required:
                reasons.append(f"available_crew {state.available_crew} < A' replacement required {vr.min_crew_required}")

    elif operation_type == "AT_SEA_INSTALL":
        inst = guest.installation
        port_only_positions = {"bay1", "bay1_a", "bay1_b", "bay2", "mast_1", "mast_2", "mast_3"}
        if inst.port_only:
            reasons.append(f"guest {guest.guest_id} is port_only=True")
        for subc in guest.subcomponents:
            if all(p in port_only_positions for p in subc.allowed_positions):
                reasons.append(
                    f"subcomponent {subc.subcomponent_id} requires bay/mast position {subc.allowed_positions} which is port-only (Annex A.5)"
                )
        if guest.total_mass_kg > 250:
            reasons.append(
                f"guest {guest.guest_id} total_mass_kg {guest.total_mass_kg} kg > 250 kg at-sea package install limit (Annex A.4)"
            )
        if state.sea_state > 3:
            reasons.append(f"sea_state {state.sea_state} > 3 A' at-sea install limit (Annex A.4)")
        eff_max_wind = min(20, inst.sea_install_max_wind_kn) if inst.sea_install_max_wind_kn > 0 else 20
        if state.wind_kn > eff_max_wind:
            reasons.append(f"wind {state.wind_kn} kn > A' install max {eff_max_wind} kn (Annex A.4)")
        if not (4.0 <= state.speed_kn <= 8.0):
            reasons.append(f"speed {state.speed_kn} kn outside A' at-sea install window [4.0, 8.0] kn (Annex A.4)")
        if not inst.port_only and not (inst.sea_install_min_speed_kn <= state.speed_kn <= inst.sea_install_max_speed_kn):
            reasons.append(
                f"speed {state.speed_kn} kn outside guest install window [{inst.sea_install_min_speed_kn}, {inst.sea_install_max_speed_kn}] kn"
            )
        if state.crew_count <= 0 or state.available_crew < max(1, inst.min_crew_required):
            reasons.append(f"available_crew {state.available_crew} < required {max(1, inst.min_crew_required)}")
    else:
        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown operation_type '{operation_type}'", "operation_type")

    return {
        "guest_id": guest.guest_id,
        "operation_type": operation_type,
        "permitted": len(reasons) == 0,
        "blocking_reasons": reasons,
        "pending_transitions_blocking": active_blocking_transitions,
        "roof_clearance_satisfied": roof_clearance_satisfied,
        "vessel_speed_kn": state.speed_kn,
        "wind_kn": state.wind_kn,
        "sea_state": state.sea_state,
        "available_crew": state.available_crew,
        "deck_clear": state.deck_clear,
    }


def evaluate_crew_regimes_comparison(
    catalog: Dict[str, GuestManifest],
    base_state: ShipOperationalState,
    ship_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compare operational capability, concurrency, A.6 transition timings, and blocked tasks across 14, 6, and 0 crew regimes (Scenario C)."""
    regime_reports: Dict[str, Any] = {}
    timing_policy = load_transition_timing_policy()

    for regime_id, rspec in CREW_REGIMES.items():
        st = base_state.clone()
        crew_total = int(rspec["crew_total"])
        st.crew_count = crew_total
        st.crew_regime = regime_id
        st.available_crew = int(rspec["available_for_guest_ops"])

        # Test key operations under this regime in calm A' speed (6 kn, wind 15 kn, heading into wind)
        st.speed_kn = 6.0
        st.ship_speed_kn = 6.0
        st.wind_kn = 15
        st.heading_deg = st.wind_deg
        st.sea_state = 2
        st.deck_clear = True
        if hasattr(st, "deck_clear_verified"):
            st.deck_clear_verified = True
        # Ensure roof clearance is verified when evaluating crew capability isolation
        st.hangar_roof_clearance_verified = True
        if hasattr(st, "stamp_roof_clearance_verification"):
            st.stamp_roof_clearance_verification(ship_profile or {})

        # Assume G3 paused so we isolate crew effects alongside acoustic rules
        if "G3" in st.installed_guests:
            st.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"

        g4_launch = evaluate_a_prime_window(catalog["G4"], "LAUNCH_RECOVERY", st, catalog, ship_profile=ship_profile)
        g4_replace = evaluate_a_prime_window(catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog, ship_profile=ship_profile)
        g3_replace = evaluate_a_prime_window(catalog["G3"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog, ship_profile=ship_profile)
        g6_sea_install = evaluate_a_prime_window(catalog["G6"], "AT_SEA_INSTALL", st, catalog, ship_profile=ship_profile)

        # At 3 kn (ROV window), G2 launch is evaluated separately from 6 kn G4 flight window
        st_rov = st.clone()
        st_rov.speed_kn = 3.0
        st_rov.ship_speed_kn = 3.0
        g2_launch = evaluate_a_prime_window(catalog["G2"], "LAUNCH_RECOVERY", st_rov, catalog, ship_profile=ship_profile)
        g2_replace = evaluate_a_prime_window(catalog["G2"], "VEHICLE_REPLACEMENT_AT_SEA", st, catalog, ship_profile=ship_profile)

        deck_clear_s = compute_deck_clear_duration_s(crew_total, deck_already_clear=False, policy=timing_policy)
        human_confirm_s = compute_human_confirmation_duration_s(crew_total, policy=timing_policy)

        # Under Annex A.5, G4 launch/recovery requires flight_deck clear of other ongoing deck operations,
        # and G2 ([0, 5] kn) vs G4 ([5, 12] kn into wind) have disjoint operational envelopes.
        regime_reports[regime_id] = {
            "crew_total": crew_total,
            "watch_bridge_engine": rspec["watch_bridge_engine"],
            "available_for_guest_ops": rspec["available_for_guest_ops"],
            "max_concurrent_manual_deck_ops": rspec["max_concurrent_manual_deck_ops"],
            "deck_clear_duration_s": deck_clear_s,
            "human_confirmation_s": human_confirm_s,
            "annex_a6_transition_rules": {
                "deck_clear_if_occupied_s": deck_clear_s,
                "deck_clear_if_already_verified_s": 0 if crew_total > 0 else 0,
                "manual_deck_clearing_supported": deck_clear_s is not None,
                "human_confirmation_latency_s": human_confirm_s,
                "course_settle_s": int(timing_policy["course_settle_s"]),
                "speed_intra_band_s": int(timing_policy["speed_intra_band_s"]),
                "speed_inter_band_per_band_s": int(timing_policy["speed_inter_band_per_band_s"]),
                "enter_quiet_state_s": int(timing_policy["enter_quiet_state_s"]),
                "exit_quiet_state_s": int(timing_policy["exit_quiet_state_s"]),
                "generator_restart_s": int(timing_policy["generator_restart_s"]),
                "cooling_loop_at_sea_repair_allowed": bool(timing_policy["cooling_loop_at_sea_repair_allowed"]),
            },
            "g4_uav_launch_recovery_permitted": g4_launch["permitted"],
            "g4_uav_at_sea_replacement_permitted": g4_replace["permitted"],
            "g3_auv_at_sea_replacement_permitted": g3_replace["permitted"],
            "g2_rov_launch_recovery_permitted": g2_launch["permitted"],
            "g2_rov_at_sea_replacement_permitted": g2_replace["permitted"],
            "g6_at_sea_install_permitted": g6_sea_install["permitted"],
            "simultaneous_g2_and_g4_deck_ops_permitted": False,
            "simultaneous_g2_g4_block_reason": (
                "Annex A.5 requires flight deck clear of other ongoing deck operations during G4 launch/recovery, "
                "and G2 winch window (0-5 kn) conflicts with G4 flight recovery window (5-12 kn into wind)."
            ),
            "autonomous_guests_always_operational": ["G1", "G3", "G5", "G6"],
            "blocking_diagnostics": {
                "G4_launch": g4_launch["blocking_reasons"],
                "G4_replacement": g4_replace["blocking_reasons"],
                "G3_replacement": g3_replace["blocking_reasons"],
                "G2_launch": g2_launch["blocking_reasons"],
                "G2_replacement": g2_replace["blocking_reasons"],
                "G6_at_sea_install": g6_sea_install["blocking_reasons"],
            },
        }

    return {
        "scenario_id": "SCENARIO_C_CREW_REGIMES",
        "transition_timing_policy": timing_policy,
        "regimes": regime_reports,
    }


def handle_lost_or_stale_feed(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    *,
    feed_name: str,
    age_ms: int,
    stale_threshold_ms: int = 5000,
) -> Dict[str, Any]:
    """Handle a stale or unavailable telemetry feed fail-safe without crashing (ROB-03)."""
    work_state = state.clone()
    status_label = "UNAVAILABLE" if age_ms < 0 or age_ms >= 30000 else ("STALE" if age_ms >= stale_threshold_ms else "MEASURED")
    fallback_applied = "NONE"

    if feed_name == "seawater_temp_c":
        if status_label in ("STALE", "UNAVAILABLE"):
            work_state.seawater_temp_c = None
            work_state.seawater_temp_is_stale = True
            fallback_applied = "CONSERVATIVE_30C_COOLING_CURVE_120KW"
    elif feed_name.startswith("guest_power:"):
        gid = feed_name.split(":", 1)[1]
        if gid in work_state.installed_guests and status_label in ("STALE", "UNAVAILABLE"):
            ginst = work_state.installed_guests[gid]
            mode_spec = catalog[gid].modes[ginst.active_mode]
            # Apply conservative sustained power + 10% safety margin when power telemetry is lost
            conservative_w = int(round(max(mode_spec.nominal_power_w, mode_spec.sustained_power_w) * 1.10))
            ginst.actual_power_override_w = conservative_w
            fallback_applied = f"CONSERVATIVE_POWER_RESERVE_{conservative_w}W"
    elif feed_name == "satcom_link":
        if status_label in ("STALE", "UNAVAILABLE"):
            work_state.satcom_available_bps = int(ship_profile["data_links"]["satcom"]["min_degraded_bps"])
            fallback_applied = f"CONSERVATIVE_MIN_SATCOM_{work_state.satcom_available_bps}BPS"
    else:
        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown telemetry feed '{feed_name}'", "feed_name")

    snap = evaluate_eight_class_budgets(ship_profile, catalog, work_state)
    return {
        "feed_name": feed_name,
        "age_ms": age_ms,
        "feed_status": status_label,
        "fallback_policy_applied": fallback_applied,
        "budget_snapshot": snap,
        "resulting_state": work_state,
    }

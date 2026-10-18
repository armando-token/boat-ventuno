"""Scenario B (D3) Live Conflict Arbitration & Candidate-Based Multi-Objective Solver (B5, ARB-01..04, H02, L5)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .acoustic import (
    AcousticLinkPolicy,
    default_acoustic_link_policy,
    evaluate_acoustic_link,
    g3_requires_acoustic_command_link,
)
from .budgets import evaluate_eight_class_budgets
from .models import (
    BindingConstraintWitness,
    GuestManifest,
    PriorityProfile,
    canonical_json_hash,
)
from .scheduling import (
    DEFAULT_TRANSITION_TIMING_POLICY,
    compute_course_settle_duration_s,
    compute_deck_clear_duration_s,
    compute_human_confirmation_duration_s,
    compute_quiet_transition_duration_s,
    compute_speed_transition_duration_s,
    evaluate_roof_drone_clearance_safe,
)
from .ship_state import ShipOperationalState


def build_g3_g4_conflict(catalog: Dict[str, GuestManifest]) -> Dict[str, Any]:
    """Derive the G3-vs-G4 ship-state conflict directly from both manifests (Scenario B, D3)."""
    g3 = catalog["G3"].ship_state_constraints
    g4 = catalog["G4"].ship_state_constraints
    reason = next(
        (
            str(r.get("reason"))
            for r in catalog["G3"].dependencies.conditional_excludes
            if r.get("other_guest_id") == "G4"
        ),
        "",
    )
    speed_overlap = not (
        g3.max_speed_kn_for_operation < g4.min_speed_kn_for_operation
        or g4.max_speed_kn_for_operation < g3.min_speed_kn_for_operation
    )
    conditions = [
        {
            "condition": "ship_speed_kn",
            "G3_requires": [g3.min_speed_kn_for_operation, g3.max_speed_kn_for_operation],
            "G4_requires": [g4.min_speed_kn_for_operation, g4.max_speed_kn_for_operation],
            "compatible": speed_overlap,
        },
        {
            "condition": "heading",
            "G3_requires": "steady survey track",
            "G4_requires": "into wind" if g4.requires_heading_into_wind_for_launch_recovery else "any",
            "compatible": not g4.requires_heading_into_wind_for_launch_recovery,
        },
        {
            "condition": "deck_machinery",
            "G3_requires": "forbidden" if g3.forbids_deck_machinery_while_active else "allowed",
            "G4_requires": "active" if g4.uses_deck_machinery else "not used",
            "compatible": not (g3.forbids_deck_machinery_while_active and g4.uses_deck_machinery),
        },
    ]
    return {
        "guests": ["G3", "G4"],
        "g3_mode": "SURVEY_QUIET",
        "g4_modes": ["RECOVERY_APPROACH", "LAUNCH_RECOVERY_OPTICS"],
        "incompatible_conditions": [c for c in conditions if not c["compatible"]],
        "manifest_reason": reason,
    }


def build_captain_sentence(arb: Dict[str, Any]) -> str:
    """One sentence a captain can read: hard constraint, winner, cost to the loser and the drone risk."""
    prof = arb.get("priority_profile_id")
    deadline = arb.get("hard_touchdown_deadline_s")
    pct = arb.get("uav_battery_pct")
    if arb.get("verdict") != "YES":
        bw = (arb.get("binding_constraints") or [{}])[0]
        return (
            f"Under {prof}, no safe plan recovers G4 (battery {pct}%, deadline T+{deadline}s) because "
            f"{bw.get('resource_class', 'unknown')}/{bw.get('constraint_id', 'unknown')} binds "
            f"({bw.get('detail', 'no detail')}), so the governor refuses and escalates to the captain."
        )
    cost = arb.get("losing_mission_cost", {})
    if cost.get("survey_segment_preserved_s", 0) > 0:
        g3_action = (
            f"lets G3 finish {cost['survey_segment_preserved_s']}s of its waypoint and holds it at T+{arb.get('hold_cmd_time_s')}s"
        )
    else:
        g3_action = f"holds G3 immediately at T+{arb.get('hold_cmd_time_s')}s"
    if arb.get("g3_lost_link_timeout_hold"):
        g3_action += (
            f" (acoustic link down: {', '.join(arb['acoustic_link_status']['causes'])}; "
            f"G3 holds autonomously after {arb['g3_lost_link_timeout_hold']['timeout_s']}s timeout)"
        )
    return (
        f"G4 battery {pct}% forces touchdown by T+{deadline}s, so under {prof} the governor {g3_action}, "
        f"lands G4 at T+{arb.get('touchdown_time_s')}s with {deadline - arb.get('touchdown_time_s')}s battery margin, "
        f"and G3 loses {cost.get('total_survey_interruption_min')} min of survey "
        f"({cost.get('survey_segment_aborted_s')}s of leg to re-run)."
    )


def _evaluate_candidate_feasibility_and_score(
    *,
    candidate_id: str,
    strategy_label: str,
    hold_delay_s: int,
    handshake_s: int,
    maneuver_to_wind_s: int,
    recovery_approach_s: int,
    charging_duration_s: int,
    pre_stepdown_g5: bool,
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    uav_battery_pct: float,
    uav_max_endurance_s: int,
    remaining_survey_waypoint_s: int,
    timing_policy: Optional[Dict[str, Any]] = None,
    lost_link: bool = False,
) -> Dict[str, Any]:
    """Evaluate physical feasibility across all 8 classes, weather/deck/ramp/acoustic/roof rules, A.6 timings, and score candidate."""
    pol = timing_policy or DEFAULT_TRANSITION_TIMING_POLICY
    t_alert = state.sim_time_s
    hard_deadline_s = t_alert + uav_max_endurance_s
    target_recovery_speed_kn = float(pol.get("recovery_target_speed_kn", 5.0))

    hold_cmd_time_s = t_alert + hold_delay_s
    hold_ack_time_s = hold_cmd_time_s + handshake_s
    turn_into_wind_time_s = hold_ack_time_s

    course_settle_s = compute_course_settle_duration_s(state.heading_deg, state.wind_deg, pol)
    if course_settle_s <= 0:
        course_settle_s = int(pol.get("course_settle_s", 90))
    speed_change_s = compute_speed_transition_duration_s(
        float(state.speed_kn if state.speed_kn is not None else state.ship_speed_kn),
        target_recovery_speed_kn,
        pol,
    )
    human_confirm_s = compute_human_confirmation_duration_s(state.crew_count, pol)
    human_confirm_done_s = t_alert + int(human_confirm_s or 0)

    deck_already_clear = bool(state.deck_clear and getattr(state, "deck_clear_verified", True))
    deck_clear_s = compute_deck_clear_duration_s(
        state.crew_count,
        deck_already_clear=deck_already_clear,
        policy=pol,
    )
    if deck_already_clear:
        deck_ready_s = t_alert
    elif deck_clear_s is not None:
        deck_ready_s = turn_into_wind_time_s + int(deck_clear_s)
    else:
        deck_ready_s = hard_deadline_s + 9999

    course_ready_s = turn_into_wind_time_s + course_settle_s
    speed_ready_s = turn_into_wind_time_s + speed_change_s
    maneuver_ready_s = max(course_ready_s, speed_ready_s)
    t_ready_s = max(maneuver_ready_s, human_confirm_done_s, deck_ready_s)

    stabilized_time_s = t_ready_s
    touchdown_time_s = stabilized_time_s + recovery_approach_s
    charging_complete_time_s = touchdown_time_s + charging_duration_s

    return_course_s = int(pol.get("course_settle_s", 90))
    return_speed_s = compute_speed_transition_duration_s(target_recovery_speed_kn, 3.0, pol)
    enter_quiet_s = compute_quiet_transition_duration_s(True, pol, current_quiet=False)
    g3_return_course_speed_ready_s = touchdown_time_s + max(return_course_s, return_speed_s)
    g3_quiet_ready_s = g3_return_course_speed_ready_s + enter_quiet_s

    preserved_s = min(remaining_survey_waypoint_s, hold_delay_s)
    aborted_s = max(0, remaining_survey_waypoint_s - preserved_s)
    g3_reposition_s = 360 if aborted_s > 0 else 0
    g3_survey_resume_time_s = g3_quiet_ready_s + g3_reposition_s
    g3_remaining_leg_s = 2400 if aborted_s > 0 else max(0, 2400 - hold_cmd_time_s)
    g3_leg_repeat_complete_s = g3_survey_resume_time_s + g3_remaining_leg_s

    planned_transitions = [
        {
            "transition_id": f"{candidate_id}:HUMAN_CONFIRM",
            "transition_type": "HUMAN_CONFIRMATION",
            "owner_id": "BRIDGE_WATCH",
            "start_time_s": t_alert,
            "end_time_s": human_confirm_done_s,
            "duration_s": int(human_confirm_s or 0),
            "from_value": "PENDING",
            "to_value": "CONFIRMED",
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:G3_ACOUSTIC_HOLD",
            "transition_type": "LOST_LINK_TIMEOUT_HOLD" if lost_link else "ACOUSTIC_HOLD_HANDSHAKE",
            "owner_id": "G3",
            "start_time_s": hold_cmd_time_s,
            "end_time_s": hold_ack_time_s,
            "duration_s": handshake_s,
            "from_value": "SURVEY_QUIET",
            "to_value": "HOLD_ACOUSTIC",
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:EXIT_QUIET",
            "transition_type": "EXIT_QUIET_STATE",
            "owner_id": "VESSEL",
            "start_time_s": turn_into_wind_time_s,
            "end_time_s": turn_into_wind_time_s,
            "duration_s": 0,
            "from_value": True,
            "to_value": False,
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:SPEED_TO_5KN",
            "transition_type": "SPEED_CHANGE",
            "owner_id": "VESSEL",
            "start_time_s": turn_into_wind_time_s,
            "end_time_s": speed_ready_s,
            "duration_s": speed_change_s,
            "from_value": float(state.speed_kn if state.speed_kn is not None else state.ship_speed_kn),
            "to_value": target_recovery_speed_kn,
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:COURSE_INTO_WIND",
            "transition_type": "COURSE_SETTLE",
            "owner_id": "VESSEL",
            "start_time_s": turn_into_wind_time_s,
            "end_time_s": course_ready_s,
            "duration_s": course_settle_s,
            "from_value": state.heading_deg,
            "to_value": state.wind_deg,
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:DECK_CLEAR",
            "transition_type": "DECK_CLEAR_VERIFICATION",
            "owner_id": "DECK_CREW",
            "start_time_s": t_alert if deck_already_clear else turn_into_wind_time_s,
            "end_time_s": deck_ready_s,
            "duration_s": 0 if deck_already_clear else deck_clear_s,
            "from_value": deck_already_clear,
            "to_value": True,
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:G4_RECOVERY_APPROACH",
            "transition_type": "UAV_RECOVERY_APPROACH",
            "owner_id": "G4",
            "start_time_s": stabilized_time_s,
            "end_time_s": touchdown_time_s,
            "duration_s": recovery_approach_s,
            "from_value": "FLIGHT_PATROL",
            "to_value": "TOUCHDOWN_HANGAR",
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:G4_HANGAR_CHARGE",
            "transition_type": "UAV_HANGAR_CHARGE_1200S",
            "owner_id": "G4",
            "start_time_s": touchdown_time_s,
            "end_time_s": charging_complete_time_s,
            "duration_s": charging_duration_s,
            "from_value": "LAUNCH_RECOVERY_OPTICS",
            "to_value": "STANDBY",
            "status": "PLANNED",
        },
        {
            "transition_id": f"{candidate_id}:RETURN_QUIET_SURVEY",
            "transition_type": "ENTER_QUIET_STATE",
            "owner_id": "VESSEL",
            "start_time_s": touchdown_time_s,
            "end_time_s": g3_quiet_ready_s,
            "duration_s": g3_quiet_ready_s - touchdown_time_s,
            "from_value": "UAV_RECOVERY_HEADING",
            "to_value": "QUIET_SURVEY",
            "status": "PLANNED",
        },
    ]

    witnesses: List[BindingConstraintWitness] = []
    state_label = f"T+{t_alert}s:{candidate_id}"

    # 1. Check hard battery touchdown deadline (<20% -> must land within 12 min / 720 s or ditch; inclusive <= 1200 s)
    if uav_battery_pct < 20.0 and touchdown_time_s > hard_deadline_s:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="time",
                constraint_id="g4_battery_emergency_touchdown_deadline_s",
                state_label=state_label,
                demand=touchdown_time_s,
                capacity=hard_deadline_s,
                margin=hard_deadline_s - touchdown_time_s,
                unit="s",
                provenance="DERIVED",
                detail=f"Candidate {candidate_id} touchdown at T+{touchdown_time_s}s exceeds hard battery deadline T+{hard_deadline_s}s",
            )
        )

    # 2. Check mandatory acoustic HOLD handshake window (10-60 s before ship turn)
    g3_man = catalog.get("G3")
    min_hs = g3_man.ship_state_constraints.acoustic_hold_handshake_min_s if g3_man else 10
    max_hs = g3_man.ship_state_constraints.acoustic_hold_handshake_max_s if g3_man else 60
    handshake_out_of_window = not lost_link and not (min_hs <= handshake_s <= max_hs)
    if handshake_out_of_window or (turn_into_wind_time_s - hold_cmd_time_s) < min_hs:
        witnesses.append(
            BindingConstraintWitness(
                resource_class="time",
                constraint_id="g3_acoustic_hold_handshake_window_s",
                state_label=state_label,
                demand=turn_into_wind_time_s - hold_cmd_time_s,
                capacity=min_hs,
                margin=(turn_into_wind_time_s - hold_cmd_time_s) - min_hs,
                unit="s",
                provenance="DERIVED",
                detail=f"Acoustic HOLD wait {turn_into_wind_time_s - hold_cmd_time_s}s outside [{min_hs}, {max_hs}]s",
            )
        )

    # 3. Check G4 flight-deck, roof clearance, weather, sea-state, stern-ramp, and crew constraints
    g4_man = catalog.get("G4")
    if g4_man is not None:
        sc4 = g4_man.ship_state_constraints
        if not deck_already_clear and (deck_clear_s is None or touchdown_time_s > hard_deadline_s):
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="positions",
                    constraint_id="flight_deck_clear_for_recovery",
                    state_label=state_label,
                    demand=deck_ready_s if deck_clear_s is not None else 1,
                    capacity=hard_deadline_s - recovery_approach_s if deck_clear_s is not None else 0,
                    margin=(hard_deadline_s - recovery_approach_s - deck_ready_s) if deck_clear_s is not None else -1,
                    unit="s" if deck_clear_s is not None else "deck_clear",
                    provenance="DERIVED",
                    detail=(
                        f"Occupied flight deck requires {deck_clear_s}s clearing under crew={state.crew_count} "
                        f"(deck_ready_s={deck_ready_s}s, touchdown={touchdown_time_s}s > deadline={hard_deadline_s}s)"
                        if deck_clear_s is not None
                        else "Flight deck is not clear and 0-crew regime cannot clear deck at sea"
                    ),
                )
            )
        roof_eval = evaluate_roof_drone_clearance_safe(state, ship_profile, catalog)
        if not roof_eval["satisfied"] and roof_eval.get("witness") is not None:
            w_roof = roof_eval["witness"]
            witnesses.append(
                BindingConstraintWitness(
                    resource_class=w_roof.resource_class,
                    constraint_id="hangar_roof_drone_clearance",
                    state_label=state_label,
                    demand=w_roof.demand,
                    capacity=w_roof.capacity,
                    margin=w_roof.margin,
                    unit=w_roof.unit,
                    provenance=w_roof.provenance,
                    detail=w_roof.detail,
                )
            )
        if state.wind_kn > sc4.max_wind_kn_for_operation:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="positions",
                    constraint_id="g4_recovery_max_wind_kn",
                    state_label=state_label,
                    demand=state.wind_kn,
                    capacity=sc4.max_wind_kn_for_operation,
                    margin=sc4.max_wind_kn_for_operation - state.wind_kn,
                    unit="kn",
                    provenance="DERIVED",
                    detail=f"Wind {state.wind_kn} kn exceeds G4 recovery limit {sc4.max_wind_kn_for_operation} kn",
                )
            )
        if state.sea_state > sc4.max_sea_state_for_operation:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="positions",
                    constraint_id="g4_recovery_max_sea_state",
                    state_label=state_label,
                    demand=state.sea_state,
                    capacity=sc4.max_sea_state_for_operation,
                    margin=sc4.max_sea_state_for_operation - state.sea_state,
                    unit="sea_state",
                    provenance="DERIVED",
                    detail=f"Sea state {state.sea_state} exceeds G4 recovery limit {sc4.max_sea_state_for_operation}",
                )
            )
        if state.crew_count == 0 or state.crew_regime == "UNCREWED_0" or state.available_crew < 2:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="positions",
                    constraint_id="g4_recovery_min_crew",
                    state_label=state_label,
                    demand=2,
                    capacity=state.available_crew if state.crew_count > 0 else 0,
                    margin=(state.available_crew if state.crew_count > 0 else 0) - 2,
                    unit="crew",
                    provenance="DERIVED",
                    detail=f"Available crew {state.available_crew} (regime {state.crew_regime}) insufficient for G4 recovery",
                )
            )

    # 4. Simulate the recovery/charging state and check 8-class physical budgets
    trial_state = state.clone()
    trial_state.vessel_mode = "UAV_RECOVERY_HEADING"
    trial_state.heading_deg = trial_state.wind_deg
    trial_state.ship_speed_kn = target_recovery_speed_kn
    trial_state.speed_kn = target_recovery_speed_kn
    if "G3" in trial_state.installed_guests:
        trial_state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
        trial_state.installed_guests["G3"].acoustic_hold_confirmed = True
    if "G4" in trial_state.installed_guests:
        trial_state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
        trial_state.installed_guests["G4"].battery_pct = uav_battery_pct
    if pre_stepdown_g5 and "G5" in trial_state.installed_guests:
        if "DEGRADED_70PCT_LOSS" in catalog["G5"].modes:
            trial_state.installed_guests["G5"].active_mode = "DEGRADED_70PCT_LOSS"

    snap = evaluate_eight_class_budgets(ship_profile, catalog, trial_state)
    if not snap["feasible"]:
        existing_cids = {w.constraint_id for w in witnesses}
        for bw in snap["binding_witnesses"]:
            if bw["constraint_id"] not in existing_cids:
                witnesses.append(BindingConstraintWitness(**bw))

    feasible = len(witnesses) == 0

    # Quantify losing-mission cost (G3 survey impact)
    reposition_penalty_min = 6.0 if aborted_s > 0 else 0.0
    recovery_window_min = round(((stabilized_time_s - turn_into_wind_time_s) + recovery_approach_s) / 60.0, 2)
    post_landing_stabilize_min = 7.75 if aborted_s == 0 else 13.75
    total_survey_interruption_min = round(
        (handshake_s / 60.0) + recovery_window_min + post_landing_stabilize_min + reposition_penalty_min,
        2,
    )

    # Compute multi-objective score from priority_profile weights and ranks (H02)
    survey_w = (
        priority_profile.capability_weights.get("hydrographic_survey", 50.0)
        + priority_profile.capability_weights.get("seabed_anomaly_detection", 50.0)
    )
    rescue_w = (
        priority_profile.capability_weights.get("maritime_sar_rescue", 50.0)
        + priority_profile.capability_weights.get("surface_surveillance", 50.0)
    )
    spectrum_w = priority_profile.capability_weights.get("wideband_spectrum_monitoring", 30.0)

    preserved_fraction = preserved_s / max(1, remaining_survey_waypoint_s)
    safety_margin_s = hard_deadline_s - touchdown_time_s
    safety_margin_fraction = safety_margin_s / max(1, uav_max_endurance_s)
    g5_stepdown_penalty = (0.70 * spectrum_w) if pre_stepdown_g5 else 0.0

    survey_score_component = preserved_fraction * survey_w - (total_survey_interruption_min / 40.0) * survey_w
    rescue_score_component = safety_margin_fraction * rescue_w * 2.2
    utility_score = round(survey_score_component + rescue_score_component - g5_stepdown_penalty, 4)

    displacement_cost = round(total_survey_interruption_min * 10.0 + (70.0 if pre_stepdown_g5 else 0.0), 4)

    cand_result = {
        "candidate_id": candidate_id,
        "strategy_label": strategy_label,
        "feasible": feasible,
        "utility_score": utility_score,
        "displacement_cost": displacement_cost,
        "pre_stepdown_g5": pre_stepdown_g5,
        "hold_cmd_time_s": hold_cmd_time_s,
        "hold_ack_time_s": hold_ack_time_s,
        "handshake_latency_s": handshake_s,
        "turn_into_wind_time_s": turn_into_wind_time_s,
        "course_settle_s": course_settle_s,
        "speed_change_s": speed_change_s,
        "target_recovery_speed_kn": target_recovery_speed_kn,
        "deck_clear_duration_s": 0 if deck_already_clear else deck_clear_s,
        "human_confirmation_s": human_confirm_s,
        "human_confirm_done_s": human_confirm_done_s,
        "deck_ready_s": deck_ready_s,
        "t_ready_s": t_ready_s,
        "stabilized_time_s": stabilized_time_s,
        "touchdown_time_s": touchdown_time_s,
        "charging_start_time_s": touchdown_time_s,
        "charging_complete_time_s": charging_complete_time_s,
        "g3_return_course_speed_ready_s": g3_return_course_speed_ready_s,
        "g3_quiet_ready_s": g3_quiet_ready_s,
        "g3_leg_repeat_complete_s": g3_leg_repeat_complete_s,
        "simulated_transition_to_touchdown_s": touchdown_time_s - t_alert,
        "hard_touchdown_deadline_s": hard_deadline_s,
        "safety_margin_s": safety_margin_s,
        "planned_transitions": planned_transitions,
        "losing_mission_cost": {
            "losing_guest_id": "G3",
            "survey_segment_preserved_s": preserved_s,
            "survey_segment_aborted_s": aborted_s,
            "acoustic_handshake_wait_s": handshake_s,
            "reposition_penalty_min": reposition_penalty_min,
            "total_survey_interruption_min": total_survey_interruption_min,
            "g3_mode_during_recovery": "HOLD_ACOUSTIC",
            "g3_quiet_ready_s": g3_quiet_ready_s,
            "g3_leg_repeat_complete_s": g3_leg_repeat_complete_s,
            "g5_pre_stepped_down": pre_stepdown_g5,
        },
        "binding_witnesses": [w.to_dict() for w in witnesses],
        "budget_snapshot": snap,
    }
    if lost_link:
        cand_result["lost_link_hold"] = True
    return cand_result


def arbitrate_live_conflict(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    uav_battery_pct: float = 18.0,
    uav_max_endurance_s: int = 720,
    uav_approach_margin_s: int = 360,
    remaining_survey_waypoint_s: int = 240,
) -> Dict[str, Any]:
    """Arbitrate Scenario B live conflict between G3 (quiet acoustic survey) and G4 (<20% battery emergency recovery).

    Generates operational candidate plans using Annex A.6 / §9.2 transition timings, filters by hard physical/weather/
    deck/acoustic/roof/budget constraints, and selects the highest-scoring feasible candidate under `priority_profile`.
    If no candidate is physically feasible, returns `verdict="NO"` with explicit `binding_constraints`.
    """
    t0_ns = time.perf_counter_ns()
    t_alert = state.sim_time_s
    hard_deadline_s = t_alert + uav_max_endurance_s
    timing_policy = DEFAULT_TRANSITION_TIMING_POLICY
    target_recovery_speed_kn = float(timing_policy.get("recovery_target_speed_kn", 5.0))

    g3_manifest = catalog["G3"]
    handshake_s = g3_manifest.ship_state_constraints.acoustic_hold_default_s or 15
    link_status = evaluate_acoustic_link(state, catalog)
    lost_link = (not link_status.available) and g3_requires_acoustic_command_link(catalog)
    if lost_link:
        # No acoustic command path: G3 only holds on its own timeout (ASSUMED autonomous behaviour).
        handshake_s = default_acoustic_link_policy().lost_link_timeout_s
    maneuver_to_wind_s = int(timing_policy.get("course_settle_s", 90))
    recovery_approach_s = max(60, uav_approach_margin_s - maneuver_to_wind_s)
    charging_duration_s = catalog["G4"].modes["LAUNCH_RECOVERY_OPTICS"].sustained_duration_s
    partial_wp_s = max(0, remaining_survey_waypoint_s // 2)

    raw_candidates_specs = [
        (
            "CAND_WAYPOINT_PRESERVING_RECOVERY",
            "MINIMAL_RECOVERABLE_PAUSE",
            remaining_survey_waypoint_s,
            False,
        ),
        (
            "CAND_PARTIAL_WAYPOINT_RECOVERY",
            "PARTIAL_WAYPOINT_PAUSE",
            partial_wp_s,
            False,
        ),
        (
            "CAND_IMMEDIATE_RECOVERY_TURN",
            "IMMEDIATE_FULL_SUSPENSION",
            0,
            False,
        ),
        (
            "CAND_IMMEDIATE_RECOVERY_WITH_G5_STEPDOWN",
            "IMMEDIATE_FULL_SUSPENSION",
            0,
            True,
        ),
        (
            "CAND_WAYPOINT_PRESERVING_WITH_G5_STEPDOWN",
            "MINIMAL_RECOVERABLE_PAUSE",
            remaining_survey_waypoint_s,
            True,
        ),
        (
            "CAND_FULL_LEG_COMPLETION_UNSAFE",
            "DEFER_UNTIL_LEG_END",
            1920,
            False,
        ),
    ]

    evaluated_candidates: List[Dict[str, Any]] = []
    for cid, strat_lbl, delay_s, step_g5 in raw_candidates_specs:
        cand_eval = _evaluate_candidate_feasibility_and_score(
            candidate_id=cid,
            strategy_label=strat_lbl,
            hold_delay_s=delay_s,
            handshake_s=handshake_s,
            maneuver_to_wind_s=maneuver_to_wind_s,
            recovery_approach_s=recovery_approach_s,
            charging_duration_s=charging_duration_s,
            pre_stepdown_g5=step_g5,
            state=state,
            ship_profile=ship_profile,
            priority_profile=priority_profile,
            catalog=catalog,
            uav_battery_pct=uav_battery_pct,
            uav_max_endurance_s=uav_max_endurance_s,
            remaining_survey_waypoint_s=remaining_survey_waypoint_s,
            timing_policy=timing_policy,
            lost_link=lost_link,
        )
        evaluated_candidates.append(cand_eval)

    feasible_candidates = [c for c in evaluated_candidates if c["feasible"]]

    if not feasible_candidates:
        elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
        # Collect binding witnesses from the immediate-turn candidate
        primary_failed = next(
            (c for c in evaluated_candidates if c["candidate_id"] == "CAND_IMMEDIATE_RECOVERY_TURN"),
            evaluated_candidates[0],
        )
        result = {
            "decision_type": "D3_LIVE_ARBITRATION",
            "verdict": "NO",
            "verdict_code": "DENIED",
            "status": "INFEASIBLE_ARBITRATION",
            "priority_profile_id": priority_profile.priority_profile_id,
            "strategy_applied": "NONE_FEASIBLE",
            "selected_candidate_id": None,
            "alert_time_s": t_alert,
            "uav_battery_pct": uav_battery_pct,
            "hard_touchdown_deadline_s": hard_deadline_s,
            "target_recovery_speed_kn": target_recovery_speed_kn,
            "hold_cmd_time_s": None,
            "hold_ack_time_s": None,
            "handshake_latency_s": handshake_s,
            "turn_into_wind_time_s": None,
            "t_ready_s": None,
            "stabilized_time_s": None,
            "touchdown_time_s": None,
            "charging_complete_time_s": None,
            "g3_quiet_ready_s": None,
            "g3_leg_repeat_complete_s": None,
            "simulated_transition_to_touchdown_s": None,
            "planned_transitions": primary_failed["planned_transitions"],
            "hard_safety_satisfied": False,
            "binding_constraints": primary_failed["binding_witnesses"],
            "evaluated_candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "strategy_label": c["strategy_label"],
                    "feasible": c["feasible"],
                    "utility_score": c["utility_score"],
                    "t_ready_s": c["t_ready_s"],
                    "touchdown_time_s": c["touchdown_time_s"],
                    "binding_witnesses": c["binding_witnesses"],
                }
                for c in evaluated_candidates
            ],
            "action_sequence": [],
            "losing_mission_cost": primary_failed["losing_mission_cost"],
            "tie_break_rule": priority_profile.tie_break_order,
            "rationale_detail": (
                f"D3 Arbitration INFEASIBLE (verdict=NO) under profile={priority_profile.priority_profile_id}: "
                f"no candidate plan satisfies hard physical/weather/deck/roof/budget constraints."
            ),
            "conflict": build_g3_g4_conflict(catalog),
            "calculation_elapsed_ms": elapsed_ms,
            "within_2s_deadline": elapsed_ms <= 2000.0,
            "priority_profile_sha256": priority_profile.source_sha256,
            "state_hash_before": canonical_json_hash(state.to_snapshot_dict()),
        }
        result["rationale"] = build_captain_sentence(result)
        return result

    # Sort feasible candidates deterministically using utility_score and priority_profile.tie_break_order
    if priority_profile.tie_break_order == "LOWER_DISPLACEMENT_COST_THEN_LEXICOGRAPHIC":
        feasible_candidates.sort(
            key=lambda c: (-c["utility_score"], c["displacement_cost"], c["candidate_id"])
        )
    else:
        feasible_candidates.sort(
            key=lambda c: (-c["utility_score"], -c["safety_margin_s"], c["candidate_id"])
        )

    winner = feasible_candidates[0]
    hold_cmd_time_s = winner["hold_cmd_time_s"]
    hold_ack_time_s = winner["hold_ack_time_s"]
    turn_into_wind_time_s = winner["turn_into_wind_time_s"]
    stabilized_time_s = winner["stabilized_time_s"]
    touchdown_time_s = winner["touchdown_time_s"]
    charging_complete_time_s = winner["charging_complete_time_s"]
    losing_cost = winner["losing_mission_cost"]

    action_sequence: List[Dict[str, Any]] = []
    if hold_cmd_time_s > t_alert:
        action_sequence.append(
            {
                "time_s": t_alert,
                "target": "G3",
                "command": "CONTINUE_QUIET_SURVEY_TO_WAYPOINT",
                "duration_s": hold_cmd_time_s - t_alert,
                "reason": (
                    f"Priority profile {priority_profile.priority_profile_id} preserves {losing_cost['survey_segment_preserved_s']}s "
                    f"survey sub-segment while keeping G4 touchdown at T+{touchdown_time_s}s <= deadline T+{hard_deadline_s}s"
                ),
            }
        )
    action_sequence.extend(
        [
            {
                "time_s": hold_cmd_time_s,
                "target": "G3",
                **(
                    {
                        "command": "LOST_LINK_TIMEOUT_HOLD",
                        "expected_ack_time_s": hold_ack_time_s,
                        "lost_link_timeout_s": handshake_s,
                        "link_causes": list(link_status.causes),
                        "reason": (
                            f"Acoustic command link unavailable ({', '.join(link_status.causes)}): no ACK is possible; "
                            f"G3 enters autonomous hold/loiter on its {handshake_s}s timeout (ASSUMED)"
                        ),
                    }
                    if lost_link
                    else {
                        "command": "ACOUSTIC_HOLD",
                        "expected_ack_time_s": hold_ack_time_s,
                        "handshake_window_s": [
                            g3_manifest.ship_state_constraints.acoustic_hold_handshake_min_s,
                            g3_manifest.ship_state_constraints.acoustic_hold_handshake_max_s,
                        ],
                        "reason": "Command G3 AUV to enter submerged acoustic HOLD loiter before ship alters course/speed",
                    }
                ),
            },
            {
                "time_s": turn_into_wind_time_s,
                "target": "VESSEL_NAVIGATION",
                "command": "TURN_INTO_WIND_FOR_RECOVERY",
                "new_vessel_mode": "UAV_RECOVERY_HEADING",
                "target_speed_kn": target_recovery_speed_kn,
                "course_settle_s": winner["course_settle_s"],
                "speed_change_s": winner["speed_change_s"],
                "reason": "Acoustic HOLD confirmed; turn vessel into wind (5-12 kn window at 5.0 kn) and stabilize roll for G4 landing",
            },
            {
                "time_s": stabilized_time_s,
                "target": "G4",
                "command": "ACTIVATE_RECOVERY_APPROACH",
                "new_mode": "LAUNCH_RECOVERY_OPTICS",
                "power_Allocated_w": 35000,
                "expected_touchdown_time_s": touchdown_time_s,
                "reason": f"Recover G4 drone safely onto flight deck by T+{touchdown_time_s}s (<= hard deadline T+{hard_deadline_s}s)",
            },
            {
                "time_s": touchdown_time_s,
                "target": "G4",
                "command": "CONFIRM_TOUCHDOWN_AND_START_35KW_CHARGE",
                "new_mode": "LAUNCH_RECOVERY_OPTICS",
                "charging_duration_s": charging_duration_s,
                "charging_complete_time_s": charging_complete_time_s,
                "reason": "G4 landed and secured on deck before 12-min battery deadline; start 20-min (1200 s) 35 kW hangar recharge",
            },
        ]
    )

    elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
    rationale_detail = (
        f"D3 Arbitration under profile={priority_profile.priority_profile_id} selected candidate={winner['candidate_id']} "
        f"(utility_score={winner['utility_score']}): G4 battery={uav_battery_pct}% (<20%) imposes hard landing deadline T+{hard_deadline_s}s. "
        + (
            f"Acoustic link unavailable ({', '.join(link_status.causes)}): G3 cannot be commanded and holds autonomously at "
            f"T+{hold_ack_time_s}s after its {handshake_s}s timeout (ASSUMED). "
            if lost_link
            else f"Issued ACOUSTIC_HOLD to G3 at T+{hold_cmd_time_s}s, confirmed at T+{hold_ack_time_s}s ({handshake_s}s acoustic latency), "
        )
        +
        f"vessel turns into wind at T+{turn_into_wind_time_s}s (target {target_recovery_speed_kn} kn, ready at T+{stabilized_time_s}s), "
        f"G4 touchdown at T+{touchdown_time_s}s (margin={winner['safety_margin_s']}s), "
        f"followed by 1200s 35 kW hangar charge until T+{charging_complete_time_s}s and G3 quiet survey resume at T+{winner['g3_quiet_ready_s']}s. "
        f"Losing mission cost on G3: interruption={losing_cost['total_survey_interruption_min']} min, "
        f"aborted_segment={losing_cost['survey_segment_aborted_s']}s."
    )

    result = {
        "decision_type": "D3_LIVE_ARBITRATION",
        "verdict": "YES",
        "verdict_code": "FEASIBLE",
        "status": "FEASIBLE",
        "priority_profile_id": priority_profile.priority_profile_id,
        "strategy_applied": winner["strategy_label"],
        "selected_candidate_id": winner["candidate_id"],
        "selected_utility_score": winner["utility_score"],
        "alert_time_s": t_alert,
        "uav_battery_pct": uav_battery_pct,
        "hard_touchdown_deadline_s": hard_deadline_s,
        "target_recovery_speed_kn": target_recovery_speed_kn,
        "hold_cmd_time_s": hold_cmd_time_s,
        "hold_ack_time_s": hold_ack_time_s,
        "handshake_latency_s": handshake_s,
        "turn_into_wind_time_s": turn_into_wind_time_s,
        "course_settle_s": winner["course_settle_s"],
        "speed_change_s": winner["speed_change_s"],
        "deck_clear_duration_s": winner["deck_clear_duration_s"],
        "human_confirmation_s": winner["human_confirmation_s"],
        "human_confirm_done_s": winner["human_confirm_done_s"],
        "deck_ready_s": winner["deck_ready_s"],
        "t_ready_s": winner["t_ready_s"],
        "stabilized_time_s": stabilized_time_s,
        "touchdown_time_s": touchdown_time_s,
        "charging_complete_time_s": charging_complete_time_s,
        "g3_return_course_speed_ready_s": winner["g3_return_course_speed_ready_s"],
        "g3_quiet_ready_s": winner["g3_quiet_ready_s"],
        "g3_leg_repeat_complete_s": winner["g3_leg_repeat_complete_s"],
        "simulated_transition_to_touchdown_s": winner["simulated_transition_to_touchdown_s"],
        "planned_transitions": winner["planned_transitions"],
        "hard_safety_satisfied": touchdown_time_s <= hard_deadline_s,
        "binding_constraints": [],
        "evaluated_candidates": [
            {
                "candidate_id": c["candidate_id"],
                "strategy_label": c["strategy_label"],
                "feasible": c["feasible"],
                "utility_score": c["utility_score"],
                "displacement_cost": c["displacement_cost"],
                "t_ready_s": c["t_ready_s"],
                "touchdown_time_s": c["touchdown_time_s"],
                "safety_margin_s": c["safety_margin_s"],
                "binding_witnesses": c["binding_witnesses"],
            }
            for c in evaluated_candidates
        ],
        "action_sequence": action_sequence,
        "losing_mission_cost": losing_cost,
        "tie_break_rule": priority_profile.tie_break_order,
        "rationale_detail": rationale_detail,
        "conflict": build_g3_g4_conflict(catalog),
        "calculation_elapsed_ms": elapsed_ms,
        "within_2s_deadline": elapsed_ms <= 2000.0,
        "priority_profile_sha256": priority_profile.source_sha256,
        "state_hash_before": canonical_json_hash(state.to_snapshot_dict()),
    }
    if lost_link:
        result["acoustic_link_status"] = link_status.to_dict()
        result["g3_lost_link_timeout_hold"] = {
            "timeout_s": handshake_s,
            "action": default_acoustic_link_policy().lost_link_action,
            "provenance": "ASSUMED",
        }
    result["rationale"] = build_captain_sentence(result)
    return result

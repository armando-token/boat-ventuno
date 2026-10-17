"""Scenario B (D3) Live Conflict Arbitration & Candidate-Based Multi-Objective Solver (B5, ARB-01..04, H02)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from .budgets import evaluate_eight_class_budgets
from .models import (
    BindingConstraintWitness,
    GuestManifest,
    PriorityProfile,
    canonical_json_hash,
)
from .ship_state import ShipOperationalState


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
) -> Dict[str, Any]:
    """Evaluate physical feasibility across all 8 classes, weather/deck/ramp/acoustic rules, and score candidate."""
    t_alert = state.sim_time_s
    hard_deadline_s = t_alert + uav_max_endurance_s

    hold_cmd_time_s = t_alert + hold_delay_s
    hold_ack_time_s = hold_cmd_time_s + handshake_s
    turn_into_wind_time_s = hold_ack_time_s
    stabilized_time_s = turn_into_wind_time_s + maneuver_to_wind_s
    touchdown_time_s = turn_into_wind_time_s + maneuver_to_wind_s + recovery_approach_s
    charging_complete_time_s = touchdown_time_s + charging_duration_s

    witnesses: List[BindingConstraintWitness] = []
    state_label = f"T+{t_alert}s:{candidate_id}"

    # 1. Check hard battery touchdown deadline (<20% -> must land within 12 min / 720 s or ditch)
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
    if not (min_hs <= handshake_s <= max_hs) or (turn_into_wind_time_s - hold_cmd_time_s) < min_hs:
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

    # 3. Check G4 flight-deck, weather, sea-state, stern-ramp, and crew constraints
    g4_man = catalog.get("G4")
    if g4_man is not None:
        sc4 = g4_man.ship_state_constraints
        if not state.deck_clear:
            witnesses.append(
                BindingConstraintWitness(
                    resource_class="positions",
                    constraint_id="flight_deck_clear_for_recovery",
                    state_label=state_label,
                    demand=1,
                    capacity=0,
                    margin=-1,
                    unit="deck_clear",
                    provenance="DERIVED",
                    detail="Flight deck is not clear for G4 recovery",
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
    trial_state.ship_speed_kn = 7.0
    trial_state.speed_kn = 7.0
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
        for bw in snap["binding_witnesses"]:
            witnesses.append(BindingConstraintWitness(**bw))

    feasible = len(witnesses) == 0

    # Quantify losing-mission cost (G3 survey impact)
    preserved_s = min(remaining_survey_waypoint_s, hold_delay_s)
    aborted_s = max(0, remaining_survey_waypoint_s - preserved_s)
    reposition_penalty_min = 6.0 if aborted_s > 0 else 0.0
    recovery_window_min = round((maneuver_to_wind_s + recovery_approach_s) / 60.0, 2)
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
    sigint_w = priority_profile.capability_weights.get("wideband_spectrum_monitoring", 30.0)

    preserved_fraction = preserved_s / max(1, remaining_survey_waypoint_s)
    safety_margin_s = hard_deadline_s - touchdown_time_s
    safety_margin_fraction = safety_margin_s / max(1, uav_max_endurance_s)
    g5_stepdown_penalty = (0.70 * sigint_w) if pre_stepdown_g5 else 0.0

    survey_score_component = preserved_fraction * survey_w - (total_survey_interruption_min / 40.0) * survey_w
    rescue_score_component = safety_margin_fraction * rescue_w * 2.2
    utility_score = round(survey_score_component + rescue_score_component - g5_stepdown_penalty, 4)

    displacement_cost = round(total_survey_interruption_min * 10.0 + (70.0 if pre_stepdown_g5 else 0.0), 4)

    return {
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
        "stabilized_time_s": stabilized_time_s,
        "touchdown_time_s": touchdown_time_s,
        "charging_start_time_s": touchdown_time_s,
        "charging_complete_time_s": charging_complete_time_s,
        "hard_touchdown_deadline_s": hard_deadline_s,
        "safety_margin_s": safety_margin_s,
        "losing_mission_cost": {
            "losing_guest_id": "G3",
            "survey_segment_preserved_s": preserved_s,
            "survey_segment_aborted_s": aborted_s,
            "acoustic_handshake_wait_s": handshake_s,
            "reposition_penalty_min": reposition_penalty_min,
            "total_survey_interruption_min": total_survey_interruption_min,
            "g3_mode_during_recovery": "HOLD_ACOUSTIC",
            "g5_pre_stepped_down": pre_stepdown_g5,
        },
        "binding_witnesses": [w.to_dict() for w in witnesses],
        "budget_snapshot": snap,
    }


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

    Generates operational candidate plans, filters by hard physical/weather/deck/acoustic/budget constraints,
    and selects the highest-scoring feasible candidate under `priority_profile` (`H02`, `ARB-01..04`).
    If no candidate is physically feasible, returns `verdict="NO"` with explicit `binding_constraints`.
    """
    t0_ns = time.perf_counter_ns()
    t_alert = state.sim_time_s
    hard_deadline_s = t_alert + uav_max_endurance_s

    g3_manifest = catalog["G3"]
    handshake_s = g3_manifest.ship_state_constraints.acoustic_hold_default_s or 15
    maneuver_to_wind_s = 90
    recovery_approach_s = max(60, uav_approach_margin_s - maneuver_to_wind_s)
    charging_duration_s = catalog["G4"].modes["LAUNCH_RECOVERY_OPTICS"].sustained_duration_s

    raw_candidates_specs = [
        (
            "CAND_WAYPOINT_PRESERVING_RECOVERY",
            "MINIMAL_RECOVERABLE_PAUSE",
            remaining_survey_waypoint_s,
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
        )
        evaluated_candidates.append(cand_eval)

    feasible_candidates = [c for c in evaluated_candidates if c["feasible"]]

    if not feasible_candidates:
        elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
        # Collect binding witnesses from the primary immediate-turn candidate
        primary_failed = evaluated_candidates[1]
        return {
            "decision_type": "D3_LIVE_ARBITRATION",
            "verdict": "NO",
            "status": "INFEASIBLE_ARBITRATION",
            "priority_profile_id": priority_profile.priority_profile_id,
            "strategy_applied": "NONE_FEASIBLE",
            "selected_candidate_id": None,
            "alert_time_s": t_alert,
            "uav_battery_pct": uav_battery_pct,
            "hard_touchdown_deadline_s": hard_deadline_s,
            "hold_cmd_time_s": None,
            "hold_ack_time_s": None,
            "handshake_latency_s": handshake_s,
            "turn_into_wind_time_s": None,
            "touchdown_time_s": None,
            "hard_safety_satisfied": False,
            "binding_constraints": primary_failed["binding_witnesses"],
            "evaluated_candidates": [
                {
                    "candidate_id": c["candidate_id"],
                    "feasible": c["feasible"],
                    "utility_score": c["utility_score"],
                    "touchdown_time_s": c["touchdown_time_s"],
                    "binding_witnesses": c["binding_witnesses"],
                }
                for c in evaluated_candidates
            ],
            "action_sequence": [],
            "losing_mission_cost": primary_failed["losing_mission_cost"],
            "tie_break_rule": priority_profile.tie_break_order,
            "rationale": (
                f"D3 Arbitration INFEASIBLE (verdict=NO) under profile={priority_profile.priority_profile_id}: "
                f"no candidate plan satisfies hard physical/weather/budget constraints."
            ),
            "calculation_elapsed_ms": elapsed_ms,
            "within_2s_deadline": elapsed_ms <= 2000.0,
            "priority_profile_sha256": priority_profile.source_sha256,
            "state_hash_before": canonical_json_hash(state.to_snapshot_dict()),
        }

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
                "command": "ACOUSTIC_HOLD",
                "expected_ack_time_s": hold_ack_time_s,
                "handshake_window_s": [
                    g3_manifest.ship_state_constraints.acoustic_hold_handshake_min_s,
                    g3_manifest.ship_state_constraints.acoustic_hold_handshake_max_s,
                ],
                "reason": "Command G3 AUV to enter submerged acoustic HOLD loiter before ship alters course/speed",
            },
            {
                "time_s": turn_into_wind_time_s,
                "target": "VESSEL_NAVIGATION",
                "command": "TURN_INTO_WIND_FOR_RECOVERY",
                "new_vessel_mode": "UAV_RECOVERY_HEADING",
                "target_speed_kn": 7.0,
                "reason": "Acoustic HOLD confirmed; turn vessel into wind (5-12 kn window) and stabilize roll for G4 landing",
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
    rationale = (
        f"D3 Arbitration under profile={priority_profile.priority_profile_id} selected candidate={winner['candidate_id']} "
        f"(utility_score={winner['utility_score']}): G4 battery={uav_battery_pct}% (<20%) imposes hard landing deadline T+{hard_deadline_s}s. "
        f"Issued ACOUSTIC_HOLD to G3 at T+{hold_cmd_time_s}s, confirmed at T+{hold_ack_time_s}s ({handshake_s}s acoustic latency), "
        f"vessel turns into wind at T+{turn_into_wind_time_s}s, G4 touchdown at T+{touchdown_time_s}s (margin={winner['safety_margin_s']}s), "
        f"followed by 1200s 35 kW hangar charge until T+{charging_complete_time_s}s. "
        f"Losing mission cost on G3: interruption={losing_cost['total_survey_interruption_min']} min, "
        f"aborted_segment={losing_cost['survey_segment_aborted_s']}s."
    )

    return {
        "decision_type": "D3_LIVE_ARBITRATION",
        "verdict": "YES",
        "status": "FEASIBLE",
        "priority_profile_id": priority_profile.priority_profile_id,
        "strategy_applied": winner["strategy_label"],
        "selected_candidate_id": winner["candidate_id"],
        "selected_utility_score": winner["utility_score"],
        "alert_time_s": t_alert,
        "uav_battery_pct": uav_battery_pct,
        "hard_touchdown_deadline_s": hard_deadline_s,
        "hold_cmd_time_s": hold_cmd_time_s,
        "hold_ack_time_s": hold_ack_time_s,
        "handshake_latency_s": handshake_s,
        "turn_into_wind_time_s": turn_into_wind_time_s,
        "stabilized_time_s": stabilized_time_s,
        "touchdown_time_s": touchdown_time_s,
        "charging_complete_time_s": charging_complete_time_s,
        "hard_safety_satisfied": touchdown_time_s <= hard_deadline_s,
        "binding_constraints": [],
        "evaluated_candidates": [
            {
                "candidate_id": c["candidate_id"],
                "strategy_label": c["strategy_label"],
                "feasible": c["feasible"],
                "utility_score": c["utility_score"],
                "displacement_cost": c["displacement_cost"],
                "touchdown_time_s": c["touchdown_time_s"],
                "safety_margin_s": c["safety_margin_s"],
                "binding_witnesses": c["binding_witnesses"],
            }
            for c in evaluated_candidates
        ],
        "action_sequence": action_sequence,
        "losing_mission_cost": losing_cost,
        "tie_break_rule": priority_profile.tie_break_order,
        "rationale": rationale,
        "calculation_elapsed_ms": elapsed_ms,
        "within_2s_deadline": elapsed_ms <= 2000.0,
        "priority_profile_sha256": priority_profile.source_sha256,
        "state_hash_before": canonical_json_hash(state.to_snapshot_dict()),
    }

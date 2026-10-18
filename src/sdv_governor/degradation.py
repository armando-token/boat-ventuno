"""Scenario B (D4) Graceful Degradation, Load Shedding, Hysteresis Reconnection, and Overconsumption Mitigation (B6, B7, DEG-01..05, H09, H10, H17)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .budgets import evaluate_eight_class_budgets, is_mode_protected
from .models import GuestManifest, PriorityProfile
from .ship_state import ShipOperationalState


def execute_graceful_degradation(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    reconnection_hysteresis_margin_w: int = 15000,
) -> Dict[str, Any]:
    """Execute deterministic D4 graceful degradation when power or chilled-water cooling is constrained.

    Enforces:
    - Separation of actual instantaneous draw vs firm committed/reserved draw (DEG-05: 81 kW actual vs 105 kW committed).
    - Non-overridable protection of `safety_critical` and active `uninterruptible` loads (e.g., G3 25 kW recharge
      while `mode_elapsed_s < min_done_duration_s`, H09, DEG-01, DEG-04).
    - Step-by-step `power_saved_w` and `cooling_water_saved_w` computed relative to the immediately preceding mode
      of that step (H10).
    - Mode step-down (e.g., G5 45 kW -> 8 kW) before full shedding.
    - Explicit `INFEASIBLE_PROTECTED_LOAD` if protected loads alone exceed capacity.
    """
    t0_ns = time.perf_counter_ns()
    work_state = state.clone()

    initial_snap = evaluate_eight_class_budgets(ship_profile, catalog, work_state)
    actions_taken: List[Dict[str, Any]] = []
    balance_steps: List[Dict[str, Any]] = []

    p_actual_0 = int(initial_snap["classes"]["power"]["actual"])
    p_comm_0 = int(initial_snap["classes"]["power"]["committed"])
    p_cap_0 = int(initial_snap["classes"]["power"]["degraded_capacity"])

    c_actual_0 = int(initial_snap["classes"]["cooling"]["actual"])
    c_comm_0 = int(initial_snap["classes"]["cooling"]["committed"])
    c_cap_0 = int(initial_snap["classes"]["cooling"]["degraded_capacity"])

    initial_deficit_summary = {
        "initial_power_actual_w": p_actual_0,
        "initial_power_committed_w": p_comm_0,
        "power_capacity_w": p_cap_0,
        "power_actual_deficit_w": max(0, p_actual_0 - p_cap_0),
        "power_committed_deficit_w": max(0, p_comm_0 - p_cap_0),
        "initial_cooling_actual_w": c_actual_0,
        "initial_cooling_committed_w": c_comm_0,
        "cooling_capacity_w": c_cap_0,
        "cooling_actual_deficit_w": max(0, c_actual_0 - c_cap_0),
        "cooling_committed_deficit_w": max(0, c_comm_0 - c_cap_0),
    }

    first_shed_ms: Optional[float] = None

    def _needs_mitigation(snap: Dict[str, Any]) -> bool:
        return (
            not snap["classes"]["power"]["feasible"]
            or not snap["classes"]["cooling"]["feasible"]
            or not snap["classes"]["data"]["feasible"]
        )

    current_snap = initial_snap

    # Phase 1: Prefer mode downgrade (step-down) in shed_order_lowest_priority_first before turning off guests
    if _needs_mitigation(current_snap) and priority_profile.prefer_mode_downgrade_before_shed:
        for gid in priority_profile.shed_order_lowest_priority_first:
            if not _needs_mitigation(current_snap):
                break
            if gid not in work_state.installed_guests or not work_state.installed_guests[gid].powered_on:
                continue
            ginst = work_state.installed_guests[gid]
            manifest = catalog[gid]
            cur_mode_spec = manifest.modes[ginst.active_mode]

            # Do not step down G3 while it is in HOLD_ACOUSTIC for an active D3 recovery plan
            if gid == "G3" and ginst.active_mode == "HOLD_ACOUSTIC":
                continue

            # Non-overridable protection check (H09, DEG-01, DEG-04)
            is_prot, _ = is_mode_protected(
                manifest,
                cur_mode_spec,
                mode_elapsed_s=ginst.mode_elapsed_s,
                battery_pct=ginst.battery_pct,
            )
            if is_prot:
                continue

            # Find lower-power operational modes
            candidate_modes = sorted(
                [
                    m for m in manifest.modes.values()
                    if (
                        m.sustained_power_w < cur_mode_spec.sustained_power_w
                        or m.cooling_water_w < cur_mode_spec.cooling_water_w
                        or m.cooling_air_mast_w < cur_mode_spec.cooling_air_mast_w
                    )
                ],
                key=lambda m: (-m.capability_retention_ratio, m.sustained_power_w),
            )
            for alt_mode in candidate_modes:
                if not _needs_mitigation(current_snap):
                    break
                prev_mode = ginst.active_mode
                prev_spec = manifest.modes[prev_mode]
                p_saved = prev_spec.sustained_power_w - alt_mode.sustained_power_w
                c_saved = prev_spec.cooling_water_w - alt_mode.cooling_water_w
                c_air_saved = prev_spec.cooling_air_mast_w - alt_mode.cooling_air_mast_w
                if p_saved <= 0 and c_saved <= 0 and c_air_saved <= 0:
                    continue

                # Only apply a mode step-down if it actually reduces the violated/binding budget metric
                trial_state = work_state.clone()
                trial_state.installed_guests[gid].active_mode = alt_mode.mode_name
                trial_snap = evaluate_eight_class_budgets(ship_profile, catalog, trial_state)

                p_comm_before = int(current_snap["classes"]["power"]["committed"])
                p_act_before = int(current_snap["classes"]["power"]["actual"])
                p_cap_now = int(current_snap["classes"]["power"]["degraded_capacity"])
                c_comm_before = int(current_snap["classes"]["cooling"]["committed"])
                c_act_before = int(current_snap["classes"]["cooling"]["actual"])
                c_cap_now = int(current_snap["classes"]["cooling"]["degraded_capacity"])

                reduces_violated_metric = False
                if p_comm_before > p_cap_now and int(trial_snap["classes"]["power"]["committed"]) < p_comm_before:
                    reduces_violated_metric = True
                if p_act_before > p_cap_now and int(trial_snap["classes"]["power"]["actual"]) < p_act_before:
                    reduces_violated_metric = True
                if (
                    not current_snap["classes"]["power"]["feasible"]
                    and p_comm_before <= p_cap_now
                    and p_act_before <= p_cap_now
                    and p_saved > 0
                ):
                    reduces_violated_metric = True
                if c_comm_before > c_cap_now and int(trial_snap["classes"]["cooling"]["committed"]) < c_comm_before:
                    reduces_violated_metric = True
                if c_act_before > c_cap_now and int(trial_snap["classes"]["cooling"]["actual"]) < c_act_before:
                    reduces_violated_metric = True
                if (
                    not current_snap["classes"]["cooling"]["feasible"]
                    and c_comm_before <= c_cap_now
                    and c_act_before <= c_cap_now
                    and (c_saved > 0 or c_air_saved > 0)
                ):
                    reduces_violated_metric = True
                if (
                    not current_snap["classes"]["data"]["feasible"]
                    and int(trial_snap["classes"]["data"]["actual"]) < int(current_snap["classes"]["data"]["actual"])
                ):
                    reduces_violated_metric = True

                if not reduces_violated_metric:
                    continue

                ginst.active_mode = alt_mode.mode_name
                ginst.last_transition_time_s = work_state.sim_time_s
                ginst.mode_elapsed_s = 0
                if first_shed_ms is None:
                    first_shed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)

                current_snap = trial_snap
                step_record = {
                    "step_index": len(actions_taken) + 1,
                    "action_type": "MODE_STEP_DOWN",
                    "guest_id": gid,
                    "from_mode": prev_mode,
                    "to_mode": alt_mode.mode_name,
                    "power_saved_w": p_saved,
                    "cooling_water_saved_w": c_saved,
                    "cooling_air_saved_w": c_air_saved,
                    "capability_retention_ratio": alt_mode.capability_retention_ratio,
                    "post_step_power_actual_w": current_snap["classes"]["power"]["actual"],
                    "post_step_power_committed_w": current_snap["classes"]["power"]["committed"],
                    "post_step_cooling_actual_w": current_snap["classes"]["cooling"]["actual"],
                    "post_step_cooling_committed_w": current_snap["classes"]["cooling"]["committed"],
                    "post_step_feasible": current_snap["feasible"],
                    "min_cooldown_before_reconnect_s": alt_mode.min_cooldown_s,
                    "required_hysteresis_margin_w": reconnection_hysteresis_margin_w,
                }
                actions_taken.append(step_record)
                balance_steps.append(step_record)

    # Phase 2: Full electrical load shedding (powered_on = False) of non-protected guests if still in deficit
    if _needs_mitigation(current_snap):
        for gid in priority_profile.shed_order_lowest_priority_first:
            if not _needs_mitigation(current_snap):
                break
            if gid not in work_state.installed_guests or not work_state.installed_guests[gid].powered_on:
                continue
            ginst = work_state.installed_guests[gid]
            manifest = catalog[gid]
            cur_mode_spec = manifest.modes[ginst.active_mode]

            # Non-overridable protection check (H09, DEG-01, DEG-04)
            is_prot, _ = is_mode_protected(
                manifest,
                cur_mode_spec,
                mode_elapsed_s=ginst.mode_elapsed_s,
                battery_pct=ginst.battery_pct,
            )
            if is_prot:
                continue

            prev_mode = ginst.active_mode
            prev_spec = manifest.modes[prev_mode]
            ginst.powered_on = False
            ginst.last_transition_time_s = work_state.sim_time_s
            if first_shed_ms is None:
                first_shed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)

            current_snap = evaluate_eight_class_budgets(ship_profile, catalog, work_state)
            step_record = {
                "step_index": len(actions_taken) + 1,
                "action_type": "SHED_LOAD_POWER_OFF",
                "guest_id": gid,
                "from_mode": prev_mode,
                "to_mode": "POWERED_OFF_PHYSICAL_INSTALLED",
                "power_saved_w": prev_spec.sustained_power_w,
                "cooling_water_saved_w": prev_spec.cooling_water_w,
                "cooling_air_saved_w": prev_spec.cooling_air_mast_w,
                "capability_retention_ratio": 0.0,
                "physical_footprint_retained": True,
                "post_step_power_actual_w": current_snap["classes"]["power"]["actual"],
                "post_step_power_committed_w": current_snap["classes"]["power"]["committed"],
                "post_step_cooling_actual_w": current_snap["classes"]["cooling"]["actual"],
                "post_step_cooling_committed_w": current_snap["classes"]["cooling"]["committed"],
                "post_step_feasible": current_snap["feasible"],
                "min_cooldown_before_reconnect_s": prev_spec.min_cooldown_s,
                "required_hysteresis_margin_w": reconnection_hysteresis_margin_w,
            }
            actions_taken.append(step_record)
            balance_steps.append(step_record)

    total_elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)
    if first_shed_ms is None:
        first_shed_ms = total_elapsed_ms

    # Check if protected loads alone still exceed available capacity (INFEASIBLE_PROTECTED_LOAD — PHY-01 / DEG-04)
    infeasible_protected = current_snap["infeasible_protected_load"] or not current_snap["feasible"]
    status_code = "INFEASIBLE_PROTECTED_LOAD" if infeasible_protected else "FEASIBLE_AFTER_DEGRADATION"
    if not actions_taken and not infeasible_protected:
        status_code = "NO_DEGRADATION_REQUIRED_FEASIBLE"

    if infeasible_protected:
        actions_taken.append(
            {
                "step_index": len(actions_taken) + 1,
                "action_type": "OPERATOR_EMERGENCY_ESCALATION",
                "status": "INFEASIBLE_PROTECTED_LOAD",
                "detail": (
                    "All non-protected guests have been downgraded/shed, but protected loads "
                    "(safety-critical / active uninterruptible / active aircraft recovery) still exceed degraded vessel capacity. "
                    "Automatic shedding of protected loads is FORBIDDEN."
                ),
                "binding_witnesses": current_snap["binding_witnesses"],
            }
        )

    return {
        "decision_type": "D4_GRACEFUL_DEGRADATION",
        "status": status_code,
        "verdict": "NO" if infeasible_protected else "YES",
        "infeasible_protected_load": infeasible_protected,
        "priority_profile_id": priority_profile.priority_profile_id,
        "deficit_analysis": initial_deficit_summary,
        "actions_taken": actions_taken,
        "balance_after_each_step": balance_steps,
        "final_budget_snapshot": current_snap,
        "resulting_state": work_state,
        "first_shedding_command_elapsed_ms": first_shed_ms,
        "total_sequence_elapsed_ms": total_elapsed_ms,
        "within_5s_first_shed_deadline": first_shed_ms <= 5000.0,
        "within_60s_sequence_deadline": total_elapsed_ms <= 60000.0,
    }


def evaluate_reconnection(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    target_guest_id: str,
    target_mode: str,
    reconnection_hysteresis_margin_w: int = 15000,
) -> Dict[str, Any]:
    """Evaluate whether a previously downgraded or shed guest can reconnect to `target_mode` with cooldown and hysteresis."""
    ginst = state.installed_guests[target_guest_id]
    manifest = catalog[target_guest_id]
    target_spec = manifest.modes[target_mode]

    elapsed_since_last_s = state.sim_time_s - ginst.last_transition_time_s
    required_cooldown_s = target_spec.min_cooldown_s
    if elapsed_since_last_s < required_cooldown_s:
        return {
            "reconnect_allowed": False,
            "guest_id": target_guest_id,
            "target_mode": target_mode,
            "reason": "COOLDOWN_ACTIVE",
            "elapsed_since_transition_s": elapsed_since_last_s,
            "required_cooldown_s": required_cooldown_s,
        }

    trial_state = state.clone()
    trial_state.installed_guests[target_guest_id].powered_on = True
    trial_state.installed_guests[target_guest_id].active_mode = target_mode
    trial_state.installed_guests[target_guest_id].last_transition_time_s = state.sim_time_s
    trial_state.installed_guests[target_guest_id].mode_elapsed_s = 0

    snap = evaluate_eight_class_budgets(ship_profile, catalog, trial_state)
    power_free_w = int(snap["classes"]["power"]["free"])
    cooling_free_w = int(snap["classes"]["cooling"]["free"])

    if not snap["feasible"] or power_free_w < reconnection_hysteresis_margin_w:
        return {
            "reconnect_allowed": False,
            "guest_id": target_guest_id,
            "target_mode": target_mode,
            "reason": "INSUFFICIENT_HYSTERESIS_MARGIN",
            "post_reconnect_power_free_w": power_free_w,
            "post_reconnect_cooling_free_w": cooling_free_w,
            "required_hysteresis_margin_w": reconnection_hysteresis_margin_w,
            "binding_witnesses": snap["binding_witnesses"],
        }

    return {
        "reconnect_allowed": True,
        "guest_id": target_guest_id,
        "target_mode": target_mode,
        "reason": "COOLDOWN_AND_HYSTERESIS_SATISFIED",
        "post_reconnect_power_free_w": power_free_w,
        "post_reconnect_cooling_free_w": cooling_free_w,
        "resulting_state": trial_state,
    }


def handle_guest_overconsumption(
    state: ShipOperationalState,
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    offending_guest_id: str,
    measured_power_w: int,
) -> Dict[str, Any]:
    """Detect and mitigate +10-20% guest power overconsumption (`actual_power > manifest_power`, RUN-04)."""
    work_state = state.clone()
    ginst = work_state.installed_guests[offending_guest_id]
    manifest = catalog[offending_guest_id]
    cur_spec = manifest.modes[ginst.active_mode]
    declared_w = cur_spec.nominal_power_w
    overrun_w = max(0, measured_power_w - declared_w)
    overrun_pct = round((overrun_w / max(1, declared_w)) * 100.0, 2)

    ginst.actual_power_override_w = measured_power_w
    snap_overrun = evaluate_eight_class_budgets(ship_profile, catalog, work_state)

    mitigation_actions: List[Dict[str, Any]] = []
    is_prot, prot_reason = is_mode_protected(
        manifest,
        cur_spec,
        mode_elapsed_s=ginst.mode_elapsed_s,
        battery_pct=ginst.battery_pct,
    )
    if is_prot:
        # Protected / safety-critical guest overconsuming: do NOT throttle it; shed/downgrade lower-priority guests if budget violated
        mitigation_actions.append(
            {
                "action": "PROTECT_CRITICAL_GUEST_AND_REBALANCE_OTHERS",
                "guest_id": offending_guest_id,
                "protection_reason": prot_reason,
                "measured_power_w": measured_power_w,
                "declared_power_w": declared_w,
                "overrun_pct": overrun_pct,
            }
        )
        if not snap_overrun["feasible"]:
            deg_res = execute_graceful_degradation(work_state, ship_profile, priority_profile, catalog)
            work_state = deg_res["resulting_state"]
            mitigation_actions.extend(deg_res["actions_taken"])
    else:
        # Non-critical guest overconsuming: throttle to lower mode if available, otherwise shed or rebalance
        lower_modes = sorted(
            [m for m in manifest.modes.values() if m.sustained_power_w < cur_spec.sustained_power_w],
            key=lambda m: -m.sustained_power_w,
        )
        if lower_modes:
            target_m = lower_modes[0]
            prev_m = ginst.active_mode
            ginst.active_mode = target_m.mode_name
            ginst.actual_power_override_w = None
            ginst.mode_elapsed_s = 0
            mitigation_actions.append(
                {
                    "action": "THROTTLE_OFFENDING_GUEST_MODE",
                    "guest_id": offending_guest_id,
                    "from_mode": prev_m,
                    "to_mode": target_m.mode_name,
                    "measured_power_w": measured_power_w,
                    "declared_power_w": declared_w,
                    "overrun_pct": overrun_pct,
                }
            )
        elif not snap_overrun["feasible"]:
            ginst.powered_on = False
            mitigation_actions.append(
                {
                    "action": "ISOLATE_OFFENDING_NON_CRITICAL_GUEST",
                    "guest_id": offending_guest_id,
                }
            )

    final_snap = evaluate_eight_class_budgets(ship_profile, catalog, work_state)
    return {
        "event": "GUEST_OVERCONSUMPTION_DETECTED",
        "offending_guest_id": offending_guest_id,
        "safety_critical": manifest.safety_critical,
        "protected_load": is_prot,
        "declared_power_w": declared_w,
        "measured_power_w": measured_power_w,
        "overrun_w": overrun_w,
        "overrun_pct": overrun_pct,
        "mitigation_actions": mitigation_actions,
        "final_budget_snapshot": final_snap,
        "resulting_state": work_state,
    }


def issue_urgent_command(
    command: str,
    target_guest_id: str,
    state: ShipOperationalState,
    catalog: Dict[str, GuestManifest],
    *,
    force_power_off: bool = True,
    respect_protected_post_touchdown_charge: bool = False,
) -> Dict[str, Any]:
    """Execute urgent local command (`STOP`, `HOLD`, `SECURE`) within <= 200 ms deadline (SAF-01, ROB-04, H17, ALIGN-T31, ALIGN-T32)."""
    t0_ns = time.perf_counter_ns()
    cmd_upper = command.strip().upper()
    if cmd_upper not in {"STOP", "HOLD", "SECURE"}:
        raise ValueError(f"Unsupported urgent command '{command}'")

    work_state = state.clone()
    ginst = work_state.installed_guests[target_guest_id]
    manifest = catalog[target_guest_id]
    prev_mode = ginst.active_mode
    post_touchdown_secured_in_hangar = (
        target_guest_id == "G4"
        and ginst.placements.get("G4_DRONE_VEHICLE") == "hangar"
        and prev_mode == "LAUNCH_RECOVERY_OPTICS"
    )

    if cmd_upper == "STOP":
        if post_touchdown_secured_in_hangar and (respect_protected_post_touchdown_charge or not force_power_off):
            ginst.powered_on = True
            new_state_label = "PROTECTED_POST_TOUCHDOWN_CHARGE_RESPECTED"
        else:
            ginst.powered_on = False
            new_state_label = "POWERED_OFF_STOPPED"
    elif cmd_upper == "HOLD":
        if "HOLD_ACOUSTIC" in manifest.modes:
            ginst.active_mode = "HOLD_ACOUSTIC"
            ginst.acoustic_hold_confirmed = True
        elif "STANDBY" in manifest.modes:
            ginst.active_mode = "STANDBY"
        new_state_label = ginst.active_mode
    else:  # SECURE
        if "STANDBY" in manifest.modes:
            ginst.active_mode = "STANDBY"
        else:
            ginst.powered_on = False
        new_state_label = ginst.active_mode if ginst.powered_on else "SECURED_OFF"

    if hasattr(work_state, "cancel_transitions_for_plan"):
        work_state.cancel_transitions_for_plan(
            owner_id=target_guest_id,
            reason=f"URGENT_{cmd_upper}",
        )

    if not ginst.powered_on:
        work_state.firm_reserved_loads = [
            fl for fl in work_state.firm_reserved_loads if fl.guest_id != target_guest_id
        ]
        work_state.pending_commands.pop(target_guest_id, None)

    ginst.last_transition_time_s = work_state.sim_time_s
    ginst.mode_elapsed_s = 0
    elapsed_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 4)
    return {
        "command": cmd_upper,
        "target_guest_id": target_guest_id,
        "previous_mode": prev_mode,
        "new_state": new_state_label,
        "powered_on": ginst.powered_on,
        "post_touchdown_secured_in_hangar": post_touchdown_secured_in_hangar,
        "protected_physical_process_respected": post_touchdown_secured_in_hangar,
        "elapsed_ms": elapsed_ms,
        "deadline_ms": 200.0,
        "within_200ms_deadline": 0.0 <= elapsed_ms <= 200.0,
        "provenance": "MEASURED",
        "resulting_state": work_state,
    }


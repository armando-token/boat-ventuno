"""Deterministic event-driven scenario simulator and replay engine (B3, B5, B6, B10, RUN-01, DEP-03, H03, H04, H11, R1, R3)."""

from __future__ import annotations

from pathlib import Path
import os
import platform
import socket
import time
from typing import Any, Dict, List, Optional, Set
import yaml

from .admission import evaluate_admission, evaluate_four_guest_combinations
from .arbitration import arbitrate_live_conflict
from .budgets import compute_cooling_water_capacity_w, evaluate_eight_class_budgets
from .degradation import (
    evaluate_reconnection,
    execute_graceful_degradation,
    handle_guest_overconsumption,
    issue_urgent_command,
)
from .manifest import (
    load_guest_catalog,
    load_priority_profile,
    validate_mission_envelope,
    validate_scenario_definition,
    validate_ship_profile,
)
from .models import (
    GuestManifest,
    PriorityProfile,
    StrictValidationError,
    canonical_json_hash,
    require_dict,
)
from .scheduling import (
    evaluate_a_prime_window,
    evaluate_crew_regimes_comparison,
    handle_lost_or_stale_feed,
)
from .ship_state import (
    FirmReservedLoad,
    InstalledGuestState,
    ShipOperationalState,
)


def load_yaml_file(path: Path) -> Dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return require_dict(raw, str(path.name))


def build_initial_state_from_scenario(
    scenario_data: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
) -> ShipOperationalState:
    """Construct initial ShipOperationalState from a validated scenario YAML definition."""
    validate_scenario_definition(scenario_data, catalog)
    init = scenario_data.get("initial_state") or scenario_data.get("initial_vessel_state") or {}
    installed: Dict[str, InstalledGuestState] = {}

    raw_guests = init.get("installed_guests") or scenario_data.get("installed_guests") or {}
    if isinstance(raw_guests, list):
        for gspec in raw_guests:
            gid = gspec["guest_id"]
            if gid not in catalog:
                raise StrictValidationError("UNKNOWN_MISSION", f"Scenario guest '{gid}' not in catalog", f"installed_guests.{gid}")
            installed[gid] = InstalledGuestState(
                guest_id=gid,
                active_mode=gspec.get("mode") or gspec.get("active_mode"),
                placements=dict(gspec["placements"]),
                compute_node=gspec["compute_node"],
                powered_on=bool(gspec.get("powered_on", True)),
                battery_pct=gspec.get("battery_pct"),
                mode_elapsed_s=int(gspec.get("leg_elapsed_s", gspec.get("mode_elapsed_s", 0))),
            )
    elif isinstance(raw_guests, dict):
        for gid, gspec in sorted(raw_guests.items()):
            if gid not in catalog:
                raise StrictValidationError("UNKNOWN_MISSION", f"Scenario guest '{gid}' not in catalog", f"installed_guests.{gid}")
            installed[gid] = InstalledGuestState(
                guest_id=gid,
                active_mode=gspec.get("mode") or gspec.get("active_mode"),
                placements=dict(gspec["placements"]),
                compute_node=gspec["compute_node"],
                powered_on=bool(gspec.get("powered_on", True)),
            )

    firm_loads: List[FirmReservedLoad] = []
    raw_firm = init.get("firm_reserved_loads") or scenario_data.get("firm_reserved_loads") or []
    for idx, fl in enumerate(raw_firm):
        firm_loads.append(
            FirmReservedLoad(
                reservation_id=fl.get("reservation_id", f"RES_{idx+1}"),
                guest_id=fl["guest_id"],
                reserved_mode=fl.get("reserved_mode") or fl.get("mode_name", "RECHARGE_CRADLE"),
                reserved_power_w=int(fl["reserved_power_w"]),
                reason=fl["reason"],
            )
        )

    spd = float(init.get("ship_speed_kn", init.get("speed_kn", 6.0)))
    crew_cnt = int(init.get("crew_count", 14))
    default_regime = "FULL_CREW_14" if crew_cnt >= 14 else ("REDUCED_CREW_6" if crew_cnt >= 6 else "UNCREWED_0")
    default_avail_crew = 9 if crew_cnt >= 14 else (2 if crew_cnt >= 6 else 0)
    return ShipOperationalState(
        tick_seq=0,
        sim_time_s=int(init.get("sim_time_s", 0)),
        vessel_mode=init.get("vessel_mode", "QUIET_SURVEY"),
        ship_speed_kn=spd,
        speed_kn=spd,
        heading_deg=float(init.get("heading_deg", 90.0)),
        wind_deg=float(init.get("wind_deg", 270.0)),
        wind_kn=int(init.get("wind_kn", 18)),
        sea_state=int(init.get("sea_state", 3)),
        seawater_temp_c=float(init.get("seawater_temp_c", 24.0)),
        cooling_loop_degraded=bool(init.get("cooling_loop_degraded", False)),
        power_reserve_w=int(init.get("power_reserve_w", 200000)),
        emcon_silence=bool(init.get("emcon_silence", False)),
        satcom_available_bps=int(init.get("satcom_available_bps", 8000000)),
        crew_count=crew_cnt,
        crew_regime=init.get("crew_regime", default_regime),
        available_crew=int(init.get("available_crew", default_avail_crew)),
        deck_clear=bool(init.get("deck_clear", True)),
        installed_guests=installed,
        firm_reserved_loads=firm_loads,
    )


def _process_acoustic_hold_ack_event(
    *,
    ev: Dict[str, Any],
    ev_time: int,
    state: ShipOperationalState,
    fired_d3_milestones: Set[str],
    events_at_tick: List[str],
) -> bool:
    """Validate and apply an ACOUSTIC_HOLD_CONFIRMED event against state.pending_commands['G3'] (R1.2, V2-F02, R8-F01)."""
    if "guest_id" not in ev or ev.get("guest_id") is None:
        events_at_tick.append(f"REJECTED_INCOMPLETE_ACOUSTIC_ACK:missing_guest_id@{ev_time}s")
        return False
    ack_gid = str(ev["guest_id"])
    if ack_gid != "G3":
        events_at_tick.append(f"REJECTED_INVALID_ACOUSTIC_ACK:guest_id={ack_gid}@{ev_time}s")
        return False

    pending_cmd = state.pending_commands.get("G3")
    if pending_cmd is None or ev_time < int(pending_cmd.get("issued_at_s", 0)):
        events_at_tick.append(f"IGNORED_PREMATURE_ACOUSTIC_ACK_NO_ACTIVE_CMD@{ev_time}s")
        return False

    if "command_id" in ev and ev.get("command_id") is not None:
        if str(ev["command_id"]) != str(pending_cmd.get("command_id")):
            events_at_tick.append(
                f"REJECTED_MISMATCHED_COMMAND_ID_ACK:{ev['command_id']}!={pending_cmd.get('command_id')}@{ev_time}s"
            )
            return False

    if "plan_version" in ev and ev.get("plan_version") is not None:
        if int(ev["plan_version"]) != int(pending_cmd.get("plan_version", -1)):
            events_at_tick.append(
                f"REJECTED_STALE_PLAN_VERSION_ACK:v{ev['plan_version']}!=v{pending_cmd.get('plan_version')}@{ev_time}s"
            )
            return False

    if (
        "command_id" not in ev
        or ev.get("command_id") is None
        or "plan_version" not in ev
        or ev.get("plan_version") is None
    ):
        events_at_tick.append(
            f"REJECTED_INCOMPLETE_ACOUSTIC_ACK:missing_command_id_or_plan_version@{ev_time}s"
        )
        return False

    min_ack_at_s = int(pending_cmd.get("issued_at_s", 0)) + 10
    if ev_time < min_ack_at_s:
        events_at_tick.append(f"REJECTED_PREMATURE_ACOUSTIC_ACK:{ev_time}s<{min_ack_at_s}s")
        return False

    timeout_at_s = int(pending_cmd.get("timeout_at_s", int(pending_cmd["issued_at_s"]) + 60))
    if ev_time > timeout_at_s:
        events_at_tick.append(f"REJECTED_EXPIRED_ACOUSTIC_ACK:{ev_time}s>{timeout_at_s}s")
        return False

    pending_cmd["confirmed"] = True
    pending_cmd["confirmed_at_s"] = ev_time
    if "G3" in state.installed_guests and state.installed_guests["G3"].powered_on:
        state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
        state.installed_guests["G3"].acoustic_hold_confirmed = True
        state.installed_guests["G3"].mode_elapsed_s = 0
    fired_d3_milestones.add("HOLD_ACK")
    events_at_tick.append(f"G3_ACOUSTIC_HOLD_ACK_RECEIVED@{ev_time}s")
    events_at_tick.append(f"G3_ENTERED_HOLD_ACOUSTIC@{ev_time}s")
    return True


def _strip_wall_clock(obj: Any, parent_key: str = "") -> Any:
    """Strip wall-clock timing fields so semantic hashes are 100% deterministic across platforms."""
    _wall_clock_keys = {"measured_tick_calc_ms", "measured_latency_ms", "calculation_time_ms", "calculation_elapsed_ms"}
    if isinstance(obj, dict):
        if parent_key == "time" and "local_limits" in obj and "scenario_a_admission_deadline_ms" in obj.get("local_limits", {}):
            return {
                k: _strip_wall_clock(v, k)
                for k, v in obj.items()
                if k not in ("actual", "committed", "free")
            }
        return {
            k: _strip_wall_clock(v, k)
            for k, v in obj.items()
            if k not in _wall_clock_keys
        }
    if isinstance(obj, list):
        return [_strip_wall_clock(x, parent_key) for x in obj]
    return obj


def run_scenario_b_replay(
    scenario_data: Dict[str, Any],
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    compressed_90s: bool = True,
    initial_state_override: Optional[ShipOperationalState] = None,
    consumed_event_indices: Optional[Set[int]] = None,
    fired_d3_milestones_init: Optional[Set[str]] = None,
    active_arb_decision_init: Optional[Dict[str, Any]] = None,
    active_plan_version_init: int = 0,
    executed_touchdown_time_s_init: Optional[int] = None,
    stop_at_sim_time_s: Optional[int] = None,
    superseded_event_types: Optional[Set[str]] = None,
    return_continuation_state: bool = False,
) -> Dict[str, Any]:
    """Execute deterministic 1 Hz event-driven Scenario B/C replay (90 s compressed 20x demo over 1800 s).

    Enforces:
    - True 1 Hz (`dt = 1 s`) internal simulation stepping across all 1800 s of mission time (R1.3).
    - Firm planned reservation (`35 kW`) registered upon D3 recovery plan approval and post-transition
      budget re-verification so Scenario B never enters an unmitigated infeasible state (R1.1, V2-F01).
    - Strict causal `ACOUSTIC_HOLD` -> `ACOUSTIC_HOLD_CONFIRMED` command-ACK verification (`guest_id == 'G3'`,
      `command_id`, `plan_version`, timeout) and resource precondition re-verification before executing
      `TURN_INTO_WIND`, `RECOVERY_APPROACH`, or `TOUCHDOWN` (R1.2, V2-F02, R8-F01).
    - Full Scenario C event execution (`REQUEST_DECK_MACHINERY`, `EVALUATE_CREW_TRANSITION`,
      `GUEST_OVERCONSUMPTION`, `FEED_LOSS`) (R3.3, V2-F06).
    - `measured_tick_calc_ms` measured after `evaluate_eight_class_budgets` completes (R1.3, V2-F05).
    """
    t0_wall_ns = time.perf_counter_ns()
    validate_ship_profile(ship_profile)
    state = (
        initial_state_override.clone()
        if initial_state_override is not None
        else build_initial_state_from_scenario(scenario_data, catalog)
    )

    decisions: List[Dict[str, Any]] = []
    timeline_ticks: List[Dict[str, Any]] = []

    total_ticks = 90 if compressed_90s else 60
    dt_s = 20 if compressed_90s else 30
    base_sim_time_s = int(state.sim_time_s)
    start_tick = base_sim_time_s // dt_s
    max_sim_time_s = total_ticks * dt_s
    target_end_sim_s = min(max_sim_time_s, int(stop_at_sim_time_s)) if stop_at_sim_time_s is not None else max_sim_time_s
    end_tick = max(start_tick, target_end_sim_s // dt_s)

    raw_events = list(scenario_data.get("events", []))
    sorted_events = sorted(
        enumerate(raw_events),
        key=lambda item: (
            int(item[1]["sim_time_s"]),
            int(item[1].get("priority", 10)),
            int(item[1].get("seq", item[0] + 1)),
        ),
    )
    executed_event_indices: Set[int] = set(consumed_event_indices) if consumed_event_indices is not None else set()
    if base_sim_time_s > 0:
        for ev_idx, ev in sorted_events:
            if int(ev["sim_time_s"]) < base_sim_time_s:
                executed_event_indices.add(ev_idx)

    superseded_types: Set[str] = set(superseded_event_types) if superseded_event_types is not None else set()

    # If the scenario explicitly supplies ACOUSTIC_HOLD_CONFIRMED / ACOUSTIC_LINK_LOSS events or disables
    # automatic acoustic ACK, do not synthesize an automatic modem ACK at hold_ack_t (R1.2, V2-F02).
    has_explicit_acoustic_ack_control = (
        any(e.get("event_type") in ("ACOUSTIC_HOLD_CONFIRMED", "ACOUSTIC_LINK_LOSS") for e in raw_events)
        or bool(scenario_data.get("acoustic_link_failed", False))
        or bool(scenario_data.get("require_explicit_acoustic_ack", False))
    )

    arb_decision: Optional[Dict[str, Any]] = (
        dict(active_arb_decision_init) if active_arb_decision_init is not None else None
    )
    fired_d3_milestones: Set[str] = (
        set(fired_d3_milestones_init) if fired_d3_milestones_init is not None else set()
    )
    active_plan_version = int(active_plan_version_init)
    executed_touchdown_time_s: Optional[int] = executed_touchdown_time_s_init
    inertia_status = "THERMAL_NOMINAL"

    for tick in range(start_tick, end_tick + 1):
        t_tick_ns = time.perf_counter_ns()
        state.tick_seq = tick
        target_sim_time_s = tick * dt_s
        sub_steps = [base_sim_time_s] if tick == start_tick else list(range(state.sim_time_s + 1, target_sim_time_s + 1))

        events_at_tick: List[str] = []

        for sim_s in sub_steps:
            state.sim_time_s = sim_s

            # Advance elapsed time by 1 s (1 Hz internal step) for all powered-on guests
            if sim_s > base_sim_time_s:
                for ginst in state.installed_guests.values():
                    if ginst.powered_on:
                        ginst.mode_elapsed_s += 1

            # 1. Process all scenario events scheduled at or before sim_s
            for ev_idx, ev in sorted_events:
                if ev_idx in executed_event_indices:
                    continue
                ev_time = int(ev["sim_time_s"])
                if ev_time <= sim_s:
                    executed_event_indices.add(ev_idx)
                    ev_type = str(ev["event_type"])
                    if ev_type in superseded_types:
                        continue

                    if ev_type == "MISSION_START":
                        events_at_tick.append(f"EVENT_MISSION_START@{ev_time}s")

                    elif ev_type == "DRONE_BATTERY_AND_WIND_ALERT":
                        bat_pct = float(ev.get("battery_pct", 18.0))
                        max_rem_s = int(ev.get("max_flight_remaining_s", 720))
                        app_margin_s = int(ev.get("uav_approach_margin_s", 360))
                        rem_wp_s = int(ev.get("remaining_survey_waypoint_s", 240))
                        if "wind_kn" in ev:
                            state.wind_kn = int(ev["wind_kn"])
                        if "sea_state" in ev:
                            state.sea_state = int(ev["sea_state"])
                        if "G4" in state.installed_guests:
                            state.installed_guests["G4"].battery_pct = bat_pct
                            state.installed_guests["G4"].max_flight_remaining_s = max_rem_s

                        events_at_tick.append(f"ALERT_G4_BATTERY_{bat_pct}PCT@{ev_time}s")
                        alert_state = state.clone()
                        alert_state.sim_time_s = ev_time
                        arb_decision = arbitrate_live_conflict(
                            alert_state,
                            ship_profile,
                            priority_profile,
                            catalog,
                            uav_battery_pct=bat_pct,
                            uav_max_endurance_s=max_rem_s,
                            uav_approach_margin_s=app_margin_s,
                            remaining_survey_waypoint_s=rem_wp_s,
                        )
                        active_plan_version += 1
                        fired_d3_milestones.clear()
                        state.pending_commands.pop("G3", None)

                        # Register firm 35 kW reservation for D3-approved G4 recovery & hangar charge (R1.1, V2-F01)
                        if arb_decision.get("verdict") == "YES" and "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
                            state.firm_reserved_loads = [
                                fl for fl in state.firm_reserved_loads if fl.guest_id != "G4"
                            ] + [
                                FirmReservedLoad(
                                    reservation_id=f"D3_PLAN_V{active_plan_version}_G4_RECOVERY",
                                    guest_id="G4",
                                    reserved_mode="LAUNCH_RECOVERY_OPTICS",
                                    reserved_power_w=35000,
                                    reason="D3 committed UAV recovery approach and 20-min hangar charge (35 kW)",
                                )
                            ]

                        sem_arb = dict(arb_decision)
                        sem_arb.pop("calculation_elapsed_ms", None)
                        sem_arb["plan_version"] = active_plan_version
                        if sem_arb.get("verdict") == "YES":
                            sem_arb["planned_hold_cmd_time_s"] = int(sem_arb["hold_cmd_time_s"])
                            sem_arb["planned_hold_ack_time_s"] = int(sem_arb["hold_ack_time_s"])
                            sem_arb["planned_turn_into_wind_time_s"] = int(sem_arb["turn_into_wind_time_s"])
                            sem_arb["planned_stabilized_time_s"] = int(
                                sem_arb.get("stabilized_time_s", int(sem_arb["turn_into_wind_time_s"]) + 90)
                            )
                            sem_arb["planned_touchdown_time_s"] = int(sem_arb["touchdown_time_s"])
                            sem_arb["planned_charging_complete_time_s"] = int(
                                sem_arb.get("charging_complete_time_s", int(sem_arb["touchdown_time_s"]) + 1200)
                            )
                            sem_arb["executed_hold_cmd_time_s"] = None
                            sem_arb["executed_hold_ack_time_s"] = None
                            sem_arb["executed_turn_into_wind_time_s"] = None
                            sem_arb["executed_stabilized_time_s"] = None
                            sem_arb["executed_touchdown_time_s"] = None
                            sem_arb["executed_charging_complete_time_s"] = None
                        arb_decision = sem_arb
                        decisions.append(
                            {
                                "phase": "D3_ARBITRATION",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_arb,
                            }
                        )

                    elif ev_type == "ACOUSTIC_HOLD_CONFIRMED":
                        _process_acoustic_hold_ack_event(
                            ev=ev,
                            ev_time=ev_time,
                            state=state,
                            fired_d3_milestones=fired_d3_milestones,
                            events_at_tick=events_at_tick,
                        )

                    elif ev_type == "ACOUSTIC_LINK_LOSS":
                        events_at_tick.append(f"ACOUSTIC_LINK_LOSS@{ev_time}s")

                    elif ev_type == "SHIP_HEADING_STABILIZED_INTO_WIND":
                        if (
                            "G3" in state.installed_guests
                            and state.installed_guests["G3"].acoustic_hold_confirmed
                            and state.power_reserve_w >= 35000
                            and state.deck_clear
                        ):
                            state.vessel_mode = str(ev.get("vessel_mode", "UAV_RECOVERY_HEADING"))
                            spd_ev = float(ev.get("ship_speed_kn", 7.0))
                            state.ship_speed_kn = spd_ev
                            state.speed_kn = spd_ev
                            state.heading_deg = state.wind_deg
                            events_at_tick.append(f"SHIP_HEADING_STABILIZED_INTO_WIND@{ev_time}s")
                        else:
                            events_at_tick.append(f"REJECTED_HEADING_TURN_PRECONDITIONS_UNMET@{ev_time}s")

                    elif ev_type == "GENERATOR_LOSS":
                        # Apply any D3 milestones scheduled at or before ev_time FIRST so state at ev_time is causally exact
                        td_pre = _apply_due_d3_milestones(
                            state=state,
                            up_to_time_s=ev_time,
                            arb_decision=arb_decision,
                            active_plan_version=active_plan_version,
                            fired_d3_milestones=fired_d3_milestones,
                            events_at_tick=events_at_tick,
                            has_explicit_acoustic_ack_control=has_explicit_acoustic_ack_control,
                        )
                        if td_pre is not None and executed_touchdown_time_s is None:
                            executed_touchdown_time_s = td_pre

                        new_res_w = int(ev.get("new_power_reserve_w", 100000))
                        state.power_reserve_w = new_res_w
                        state.vessel_mode = "DEGRADED_POWER"
                        events_at_tick.append(f"PERTURBATION_GENERATOR_LOSS_TO_{new_res_w // 1000}KW@{ev_time}s")

                        # If power reserve drops below 35 kW before G4 recovery approach, invalidate the 35 kW recovery plan (R1.2, V2-F02)
                        if new_res_w < 35000 and "TOUCHDOWN" not in fired_d3_milestones:
                            fired_d3_milestones.add("PLAN_INVALIDATED_INSUFFICIENT_POWER")
                            state.firm_reserved_loads = [
                                fl for fl in state.firm_reserved_loads
                                if not fl.reservation_id.startswith("D3_PLAN_")
                            ]
                            events_at_tick.append(
                                f"D3_PLAN_INVALIDATED_POWER_{new_res_w}W_BELOW_35000W@{ev_time}s"
                            )

                        deg_decision = execute_graceful_degradation(state, ship_profile, priority_profile, catalog)
                        state = deg_decision["resulting_state"]
                        active_plan_version += 1
                        sem_deg = {
                            k: v
                            for k, v in deg_decision.items()
                            if k not in ("resulting_state", "first_shedding_command_elapsed_ms", "total_sequence_elapsed_ms")
                        }
                        sem_deg["plan_version"] = active_plan_version
                        decisions.append(
                            {
                                "phase": "D4_DEGRADATION",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_deg,
                            }
                        )

                    elif ev_type == "COOLING_LOOP_FAILURE":
                        td_pre = _apply_due_d3_milestones(
                            state=state,
                            up_to_time_s=ev_time,
                            arb_decision=arb_decision,
                            active_plan_version=active_plan_version,
                            fired_d3_milestones=fired_d3_milestones,
                            events_at_tick=events_at_tick,
                            has_explicit_acoustic_ack_control=has_explicit_acoustic_ack_control,
                        )
                        if td_pre is not None and executed_touchdown_time_s is None:
                            executed_touchdown_time_s = td_pre

                        state.cooling_loop_degraded = bool(ev.get("cooling_loop_degraded", True))
                        if "seawater_temp_c" in ev:
                            state.seawater_temp_c = float(ev["seawater_temp_c"])
                        if "new_power_reserve_w" in ev:
                            state.power_reserve_w = int(ev["new_power_reserve_w"])
                        state.vessel_mode = "DEGRADED_THERMAL"
                        events_at_tick.append(
                            f"PERTURBATION_COOLING_LOOP_40PCT_LOSS_AT_{int(state.seawater_temp_c or 30)}C@{ev_time}s"
                        )

                        deg_decision = execute_graceful_degradation(state, ship_profile, priority_profile, catalog)
                        state = deg_decision["resulting_state"]
                        active_plan_version += 1
                        sem_deg = {
                            k: v
                            for k, v in deg_decision.items()
                            if k not in ("resulting_state", "first_shedding_command_elapsed_ms", "total_sequence_elapsed_ms")
                        }
                        sem_deg["plan_version"] = active_plan_version
                        decisions.append(
                            {
                                "phase": "D4_DEGRADATION",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_deg,
                            }
                        )

                    elif ev_type == "POWER_COOLING_RESTORATION":
                        state.power_reserve_w = int(ev.get("new_power_reserve_w", 200000))
                        state.cooling_loop_degraded = bool(ev.get("cooling_loop_degraded", False))
                        if "seawater_temp_c" in ev:
                            state.seawater_temp_c = float(ev["seawater_temp_c"])
                        events_at_tick.append(
                            f"RESTORATION_RESERVE_{state.power_reserve_w // 1000}KW_COOLING_NOMINAL@{ev_time}s"
                        )

                        rec_gid = str(ev.get("reconnect_guest_id", "G5"))
                        rec_mode = str(ev.get("reconnect_mode", "FULL_SPECTRUM"))
                        if rec_gid in state.installed_guests:
                            rec_decision = evaluate_reconnection(
                                state,
                                ship_profile,
                                priority_profile,
                                catalog,
                                target_guest_id=rec_gid,
                                target_mode=rec_mode,
                            )
                            if rec_decision["reconnect_allowed"]:
                                state = rec_decision["resulting_state"]
                                events_at_tick.append(f"{rec_gid}_RECONNECTED_TO_{rec_mode}@{ev_time}s")
                            active_plan_version += 1
                            sem_rec = {k: v for k, v in rec_decision.items() if k != "resulting_state"}
                            sem_rec["plan_version"] = active_plan_version
                            decisions.append(
                                {
                                    "phase": "D4_RECONNECTION",
                                    "tick": tick,
                                    "sim_time_s": sim_s,
                                    "event_time_s": ev_time,
                                    "plan_version": active_plan_version,
                                    "decision": sem_rec,
                                }
                            )

                    # Scenario C Operational Events (R3.3, V2-F06)
                    elif ev_type == "REQUEST_DECK_MACHINERY":
                        req_gid = str(ev.get("guest_id", "G4"))
                        target_manifest = catalog.get(req_gid, catalog["G4"])
                        win_res = evaluate_a_prime_window(target_manifest, "LAUNCH_RECOVERY", state, catalog)
                        active_plan_version += 1
                        status_tag = "PERMITTED" if win_res["permitted"] else "BLOCKED"
                        events_at_tick.append(f"DECK_MACHINERY_REQUEST_{req_gid}_{status_tag}@{ev_time}s")
                        decisions.append(
                            {
                                "phase": "SCENARIO_C_DECK_MACHINERY_GUARD",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": win_res,
                            }
                        )

                    elif ev_type in ("EVALUATE_CREW_TRANSITION", "CREW_REGIME_CHANGE"):
                        new_crew = int(ev.get("crew_count", state.crew_count))
                        state.crew_count = new_crew
                        if new_crew >= 14:
                            state.crew_regime = "FULL_CREW_14"
                            state.available_crew = 9
                        elif new_crew >= 6:
                            state.crew_regime = "REDUCED_CREW_6"
                            state.available_crew = 2
                        else:
                            state.crew_regime = "UNCREWED_0"
                            state.available_crew = 0

                        g4_launch_chk = evaluate_a_prime_window(catalog["G4"], "LAUNCH_RECOVERY", state, catalog)
                        g4_repl_chk = evaluate_a_prime_window(catalog["G4"], "VEHICLE_REPLACEMENT_AT_SEA", state, catalog)
                        install_chk = None
                        if "attempt_physical_install_guest_id" in ev:
                            inst_gid = str(ev["attempt_physical_install_guest_id"])
                            if inst_gid in catalog:
                                install_chk = evaluate_a_prime_window(catalog[inst_gid], "AT_SEA_INSTALL", state, catalog)

                        active_plan_version += 1
                        events_at_tick.append(f"CREW_TRANSITION_TO_{state.crew_regime}@{ev_time}s")
                        decisions.append(
                            {
                                "phase": "SCENARIO_C_CREW_TRANSITION",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": {
                                    "crew_count": state.crew_count,
                                    "crew_regime": state.crew_regime,
                                    "available_crew": state.available_crew,
                                    "g4_launch_recovery": g4_launch_chk,
                                    "g4_vehicle_replacement": g4_repl_chk,
                                    "physical_install_check": install_chk,
                                },
                            }
                        )

                    elif ev_type == "GUEST_OVERCONSUMPTION":
                        off_gid = str(ev.get("guest_id", "G5"))
                        meas_w = int(ev.get("measured_power_w", 54000))
                        ov_res = handle_guest_overconsumption(
                            state,
                            ship_profile,
                            priority_profile,
                            catalog,
                            offending_guest_id=off_gid,
                            measured_power_w=meas_w,
                        )
                        state = ov_res["resulting_state"]
                        active_plan_version += 1
                        events_at_tick.append(f"GUEST_OVERCONSUMPTION_{off_gid}_{meas_w}W_MITIGATED@{ev_time}s")
                        sem_ov = {k: v for k, v in ov_res.items() if k != "resulting_state"}
                        sem_ov["plan_version"] = active_plan_version
                        decisions.append(
                            {
                                "phase": "SCENARIO_C_OVERCONSUMPTION",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_ov,
                            }
                        )

                    elif ev_type == "FEED_LOSS":
                        raw_fid = str(ev.get("feed_id", "seawater_temp_c"))
                        mapped_fid = "seawater_temp_c" if "seawater" in raw_fid else raw_fid
                        age_ms = int(ev.get("age_ms", 5000))
                        ttl_ms = int(ev.get("ttl_ms", 1000))
                        fl_res = handle_lost_or_stale_feed(
                            state,
                            ship_profile,
                            catalog,
                            feed_name=mapped_fid,
                            age_ms=age_ms,
                            stale_threshold_ms=ttl_ms,
                        )
                        state = fl_res["resulting_state"]
                        active_plan_version += 1
                        events_at_tick.append(f"FEED_LOSS_{mapped_fid}_{fl_res['feed_status']}@{ev_time}s")
                        sem_fl = {k: v for k, v in fl_res.items() if k != "resulting_state"}
                        sem_fl["plan_version"] = active_plan_version
                        decisions.append(
                            {
                                "phase": "SCENARIO_C_FEED_LOSS",
                                "tick": tick,
                                "sim_time_s": sim_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_fl,
                            }
                        )

            # 2. Apply due scheduled milestones from active D3 arbitration plan at 1 Hz resolution
            prev_milestones_count = len(fired_d3_milestones)
            td_time = _apply_due_d3_milestones(
                state=state,
                up_to_time_s=sim_s,
                arb_decision=arb_decision,
                active_plan_version=active_plan_version,
                fired_d3_milestones=fired_d3_milestones,
                events_at_tick=events_at_tick,
                has_explicit_acoustic_ack_control=has_explicit_acoustic_ack_control,
            )
            if td_time is not None and executed_touchdown_time_s is None:
                executed_touchdown_time_s = td_time

            # If a milestone transition changed guest load, verify feasibility and trigger D4 if needed (R1.1, V2-F01)
            if len(fired_d3_milestones) > prev_milestones_count:
                chk_snap = evaluate_eight_class_budgets(ship_profile, catalog, state)
                if not chk_snap["feasible"] and not chk_snap["infeasible_protected_load"]:
                    deg_decision = execute_graceful_degradation(state, ship_profile, priority_profile, catalog)
                    state = deg_decision["resulting_state"]
                    active_plan_version += 1
                    sem_deg = {
                        k: v
                        for k, v in deg_decision.items()
                        if k not in ("resulting_state", "first_shedding_command_elapsed_ms", "total_sequence_elapsed_ms")
                    }
                    sem_deg["plan_version"] = active_plan_version
                    decisions.append(
                        {
                            "phase": "D4_DEGRADATION",
                            "tick": tick,
                            "sim_time_s": sim_s,
                            "event_time_s": sim_s,
                            "plan_version": active_plan_version,
                            "decision": sem_deg,
                        }
                    )

            # 3. Advance thermal inertia state by 1 s (1 Hz) without resetting credit (PHY-09, H11)
            if sim_s > base_sim_time_s:
                eff_temp_c, _ = state.effective_seawater_temp_c()
                _, eff_cool_w, _ = compute_cooling_water_capacity_w(
                    eff_temp_c, loop_degraded=state.cooling_loop_degraded
                )
                tick_water_heat_w = sum(
                    catalog[gid].modes[ginst.active_mode].cooling_water_w
                    for gid, ginst in state.installed_guests.items()
                    if ginst.powered_on and gid in catalog
                )
                excess_heat_w = max(0, tick_water_heat_w - eff_cool_w)
                _, inertia_status = state.thermal_inertia.step_tick(excess_heat_w, dt_s=1)

        # Evaluate 8-class budgets FIRST, then measure full tick calculation latency AFTER (R1.3, V2-F05)
        snap = evaluate_eight_class_budgets(
            ship_profile, catalog, state, source_time_ns=tick * 1_000_000_000
        )
        calc_ms = round((time.perf_counter_ns() - t_tick_ns) / 1_000_000.0, 4)
        snap["classes"]["time"]["committed"] = calc_ms
        snap["classes"]["time"]["actual"] = calc_ms
        snap["classes"]["time"]["free"] = round(200.0 - calc_ms, 4)
        snap["classes"]["time"]["feasible"] = 0.0 <= calc_ms <= 200.0

        timeline_ticks.append(
            {
                "tick": tick,
                "sim_time_s": state.sim_time_s,
                "snapshot_id": f"SNAP_V{active_plan_version + 1:04d}_T{state.sim_time_s:04d}S",
                "plan_version": active_plan_version,
                "vessel_mode": state.vessel_mode,
                "speed_kn": state.speed_kn,
                "wind_kn": state.wind_kn,
                "sea_state": state.sea_state,
                "seawater_temp_c": state.effective_seawater_temp_c()[0],
                "crew_regime": state.crew_regime,
                "deck_clear": state.deck_clear,
                "events": events_at_tick,
                "guest_modes": {
                    gid: {
                        "mode": ginst.active_mode,
                        "powered_on": ginst.powered_on,
                        "mode_elapsed_s": ginst.mode_elapsed_s,
                        "placements": dict(ginst.placements),
                        "compute_node": ginst.compute_node,
                    }
                    for gid, ginst in sorted(state.installed_guests.items())
                },
                "power_actual_w": snap["classes"]["power"]["actual"],
                "power_committed_w": snap["classes"]["power"]["committed"],
                "power_capacity_w": snap["classes"]["power"]["degraded_capacity"],
                "cooling_actual_w": snap["classes"]["cooling"]["actual"],
                "cooling_capacity_w": snap["classes"]["cooling"]["degraded_capacity"],
                "thermal_inertia_status": inertia_status,
                "thermal_inertia_accumulated_j": state.thermal_inertia.accumulated_boost_energy_j,
                "feasible": snap["feasible"],
                "measured_tick_calc_ms": calc_ms,
                "budget_snapshot": snap,
            }
        )

    wall_total_ms = round((time.perf_counter_ns() - t0_wall_ns) / 1_000_000.0, 3)

    semantic_ticks = _strip_wall_clock(timeline_ticks)
    semantic_decisions = _strip_wall_clock(decisions)
    semantic_payload = {
        "scenario_id": scenario_data.get("scenario_id", "SCENARIO_B"),
        "priority_profile_id": priority_profile.priority_profile_id,
        "executed_touchdown_time_s": executed_touchdown_time_s,
        "decisions": semantic_decisions,
        "timeline_ticks": semantic_ticks,
    }
    semantic_sha256 = canonical_json_hash(semantic_payload)

    result: Dict[str, Any] = {
        "scenario_id": scenario_data.get("scenario_id", "SCENARIO_B"),
        "priority_profile_id": priority_profile.priority_profile_id,
        "executed_touchdown_time_s": executed_touchdown_time_s,
        "semantic_sha256": semantic_sha256,
        "semantic_payload": semantic_payload,
        "timeline_ticks": timeline_ticks,
        "decisions": decisions,
        "runtime_telemetry": {
            "hostname": socket.gethostname(),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
            "python_version": platform.python_version(),
            "pid": os.getpid(),
            "wall_elapsed_ms": wall_total_ms,
            "provenance": "MEASURED",
        },
    }
    if return_continuation_state:
        result["continuation_state"] = {
            "ending_state": state.clone(),
            "consumed_event_indices": set(executed_event_indices),
            "fired_d3_milestones": set(fired_d3_milestones),
            "active_arb_decision": dict(arb_decision) if arb_decision is not None else None,
            "active_plan_version": active_plan_version,
            "executed_touchdown_time_s": executed_touchdown_time_s,
        }
    return result


def _apply_due_d3_milestones(
    *,
    state: ShipOperationalState,
    up_to_time_s: int,
    arb_decision: Optional[Dict[str, Any]],
    active_plan_version: int,
    fired_d3_milestones: Set[str],
    events_at_tick: List[str],
    has_explicit_acoustic_ack_control: bool = False,
) -> Optional[int]:
    """Apply due D3 arbitration milestones once each in strict causal order with precondition re-verification (R1.2, V2-F02, R8-F01)."""
    if arb_decision is None or arb_decision.get("verdict") != "YES":
        return None
    if (
        "PLAN_INVALIDATED_INSUFFICIENT_POWER" in fired_d3_milestones
        or "PLAN_INVALIDATED_DEADLINE_EXCEEDED" in fired_d3_milestones
    ):
        return None

    touchdown_recorded: Optional[int] = None
    hold_cmd_t = int(arb_decision.get("planned_hold_cmd_time_s", arb_decision["hold_cmd_time_s"]))
    hold_ack_t = int(arb_decision.get("planned_hold_ack_time_s", arb_decision["hold_ack_time_s"]))
    planned_turn_t = int(arb_decision.get("planned_turn_into_wind_time_s", arb_decision["turn_into_wind_time_s"]))
    planned_stab_t = int(
        arb_decision.get("planned_stabilized_time_s", arb_decision.get("stabilized_time_s", planned_turn_t + 90))
    )
    planned_td_t = int(arb_decision.get("planned_touchdown_time_s", arb_decision["touchdown_time_s"]))
    dl_t = int(arb_decision.get("hard_touchdown_deadline_s", 1200))
    planned_chg_done_t = int(
        arb_decision.get(
            "planned_charging_complete_time_s",
            arb_decision.get("charging_complete_time_s", planned_td_t + 1200),
        )
    )

    turn_stabilization_duration_s = max(0, planned_stab_t - planned_turn_t)
    recovery_approach_duration_s = max(0, planned_td_t - planned_stab_t)
    charging_duration_s = max(0, planned_chg_done_t - planned_td_t)

    # Step 1: Issue ACOUSTIC_HOLD command to G3 and register pending command expectation
    if up_to_time_s >= hold_cmd_t and "HOLD_CMD" not in fired_d3_milestones:
        fired_d3_milestones.add("HOLD_CMD")
        cmd_id = f"CMD_HOLD_G3_V{active_plan_version}_{hold_cmd_t}"
        state.pending_commands["G3"] = {
            "command_id": cmd_id,
            "guest_id": "G3",
            "operation": "ACOUSTIC_HOLD",
            "plan_version": active_plan_version,
            "issued_at_s": hold_cmd_t,
            "expected_ack_at_s": hold_ack_t,
            "timeout_at_s": hold_cmd_t + 60,
            "confirmed": False,
            "confirmed_at_s": None,
        }
        arb_decision["executed_hold_cmd_time_s"] = hold_cmd_t
        events_at_tick.append(f"G3_ACOUSTIC_HOLD_CMD_SENT@{hold_cmd_t}s")

    # Step 2: Acoustic modem ACK arrival (simulated link-layer ACK unless overridden by explicit scenario ACK events)
    if up_to_time_s >= hold_ack_t and "HOLD_ACK" not in fired_d3_milestones:
        pending_g3 = state.pending_commands.get("G3")
        if not has_explicit_acoustic_ack_control and pending_g3 is not None:
            _process_acoustic_hold_ack_event(
                ev={
                    "guest_id": "G3",
                    "command_id": pending_g3["command_id"],
                    "plan_version": pending_g3["plan_version"],
                },
                ev_time=hold_ack_t,
                state=state,
                fired_d3_milestones=fired_d3_milestones,
                events_at_tick=events_at_tick,
            )
        elif pending_g3 is not None and up_to_time_s >= int(pending_g3["timeout_at_s"]):
            if "HOLD_ACK_TIMEOUT" not in fired_d3_milestones:
                fired_d3_milestones.add("HOLD_ACK_TIMEOUT")
                events_at_tick.append(f"G3_ACOUSTIC_HOLD_ACK_TIMEOUT_ESCALATION@{up_to_time_s}s")

    pending_g3_after = state.pending_commands.get("G3", {})
    confirmed_ack_t = (
        int(pending_g3_after["confirmed_at_s"])
        if pending_g3_after.get("confirmed") is True and pending_g3_after.get("confirmed_at_s") is not None
        else None
    )
    if confirmed_ack_t is not None:
        arb_decision["executed_hold_ack_time_s"] = confirmed_ack_t
        turn_t = max(planned_turn_t, confirmed_ack_t)
        stab_t = turn_t + turn_stabilization_duration_s
        td_t = stab_t + recovery_approach_duration_s
        chg_done_t = td_t + charging_duration_s
    else:
        turn_t = planned_turn_t
        stab_t = planned_stab_t
        td_t = planned_td_t
        chg_done_t = planned_chg_done_t

    # Step 3: Turn vessel into wind ONLY if G3 acoustic HOLD ACK is confirmed and power/deck/deadline preconditions hold
    if up_to_time_s >= turn_t and "TURN_INTO_WIND" not in fired_d3_milestones:
        g3_ack_ok = (
            "HOLD_ACK" in fired_d3_milestones
            and confirmed_ack_t is not None
            and ("G3" not in state.installed_guests or state.installed_guests["G3"].active_mode == "HOLD_ACOUSTIC")
        )
        if not g3_ack_ok:
            if "BLOCKED_TURN_NO_HOLD_ACK" not in fired_d3_milestones:
                fired_d3_milestones.add("BLOCKED_TURN_NO_HOLD_ACK")
                events_at_tick.append(f"BLOCKED_TURN_INTO_WIND_AWAITING_G3_HOLD_ACK@{up_to_time_s}s")
            return None
        if td_t > dl_t:
            fired_d3_milestones.add("PLAN_INVALIDATED_DEADLINE_EXCEEDED")
            arb_decision["executed_touchdown_time_s"] = None
            arb_decision["plan_status"] = "INVALIDATED_LATE_ACK_EXCEEDS_DEADLINE"
            state.firm_reserved_loads = [
                fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
            ]
            events_at_tick.append(
                f"D3_PLAN_INVALIDATED_LATE_ACK_TOUCHDOWN_{td_t}S_EXCEEDS_DEADLINE_{dl_t}S@{up_to_time_s}s"
            )
            return None
        if state.power_reserve_w < 35000 or not state.deck_clear:
            fired_d3_milestones.add("PLAN_INVALIDATED_INSUFFICIENT_POWER")
            arb_decision["plan_status"] = "INVALIDATED_PRECONDITIONS"
            state.firm_reserved_loads = [
                fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
            ]
            events_at_tick.append(
                f"D3_PLAN_INVALIDATED_PRECONDITIONS_POWER_{state.power_reserve_w}W_DECK_{state.deck_clear}@{up_to_time_s}s"
            )
            return None

        fired_d3_milestones.add("TURN_INTO_WIND")
        arb_decision["executed_turn_into_wind_time_s"] = turn_t
        if state.vessel_mode not in ("DEGRADED_POWER", "DEGRADED_THERMAL"):
            state.vessel_mode = "UAV_RECOVERY_HEADING"
        state.heading_deg = state.wind_deg
        state.speed_kn = 7.0
        state.ship_speed_kn = 7.0
        events_at_tick.append(f"VESSEL_TURNED_INTO_WIND_FOR_G4_RECOVERY@{turn_t}s")

    # Step 4: Start 35 kW G4 recovery approach ONLY if vessel turned into wind and >= 35 kW reserve + deck_clear hold
    if up_to_time_s >= stab_t and "RECOVERY_APPROACH" not in fired_d3_milestones:
        if "TURN_INTO_WIND" not in fired_d3_milestones:
            return None
        if state.power_reserve_w < 35000 or not state.deck_clear:
            fired_d3_milestones.add("PLAN_INVALIDATED_INSUFFICIENT_POWER")
            state.firm_reserved_loads = [
                fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
            ]
            events_at_tick.append(
                f"D3_RECOVERY_APPROACH_ABORTED_POWER_{state.power_reserve_w}W@{up_to_time_s}s"
            )
            return None

        fired_d3_milestones.add("RECOVERY_APPROACH")
        arb_decision["executed_stabilized_time_s"] = stab_t
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
            state.installed_guests["G4"].mode_elapsed_s = 0
        events_at_tick.append(f"G4_STARTED_35KW_RECOVERY_APPROACH@{stab_t}s")

    # Step 5: Touchdown and hangar charge ONLY if recovery approach succeeded, power >= 35 kW, deck_clear, and <= deadline
    if up_to_time_s >= td_t and "TOUCHDOWN" not in fired_d3_milestones:
        if "RECOVERY_APPROACH" not in fired_d3_milestones:
            return None
        if state.power_reserve_w < 35000 or not state.deck_clear or td_t > dl_t:
            fired_d3_milestones.add("PLAN_INVALIDATED_INSUFFICIENT_POWER")
            state.firm_reserved_loads = [
                fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
            ]
            events_at_tick.append(
                f"D3_TOUCHDOWN_ABORTED_POWER_{state.power_reserve_w}W_DEADLINE_{dl_t}S@{up_to_time_s}s"
            )
            return None

        fired_d3_milestones.add("TOUCHDOWN")
        touchdown_recorded = td_t
        arb_decision["executed_touchdown_time_s"] = td_t
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].placements["G4_DRONE_VEHICLE"] = "hangar"
            state.installed_guests["G4"].battery_pct = 20.0
            state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
            state.installed_guests["G4"].mode_elapsed_s = 0
        events_at_tick.append(f"G4_TOUCHDOWN_AND_SECURED_IN_HANGAR@{td_t}s")
        events_at_tick.append(f"G4_STARTED_20MIN_35KW_HANGAR_CHARGE@{td_t}s")

    # Step 6: Complete 20-min 35 kW hangar charge and transition G4 to STANDBY
    if up_to_time_s >= chg_done_t and "CHARGE_COMPLETE" not in fired_d3_milestones:
        if "TOUCHDOWN" not in fired_d3_milestones:
            return touchdown_recorded
        fired_d3_milestones.add("CHARGE_COMPLETE")
        arb_decision["executed_charging_complete_time_s"] = chg_done_t
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].active_mode = "STANDBY"
            state.installed_guests["G4"].battery_pct = 100.0
            state.installed_guests["G4"].mode_elapsed_s = 0
        state.firm_reserved_loads = [
            fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
        ]
        events_at_tick.append(f"G4_COMPLETED_35KW_CHARGE_TO_STANDBY@{chg_done_t}s")

    return touchdown_recorded


def run_full_campaign_suite(repo_root: Path) -> Dict[str, Any]:
    """Run the complete deterministic evaluation across Scenario A (D1), Scenario B (D3/D4 under both profiles), and Scenario C."""
    t0_ns = time.perf_counter_ns()
    data_dir = repo_root / "data"
    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    mission_envelope = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))
    prof_survey = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
    prof_rescue = load_priority_profile(data_dir / "priorities" / "rescue_primary.yaml")

    scen_b = load_yaml_file(data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml")
    scen_b_cool = load_yaml_file(data_dir / "scenarios" / "scenario_B_cooling_limited_30c.yaml")
    scen_c = load_yaml_file(data_dir / "scenarios" / "scenario_C_crew_regimes.yaml")

    # 1. Scenario A: 5 four-guest combinations + G6 custom admission
    combos_report = evaluate_four_guest_combinations(catalog, ship_profile, mission_envelope, prof_survey)

    state_g6 = ShipOperationalState(
        tick_seq=0,
        sim_time_s=0,
        vessel_mode="PORT_INTEGRATION",
        speed_kn=0.0,
        heading_deg=0.0,
        wind_deg=0.0,
        wind_kn=8,
        sea_state=1,
        seawater_temp_c=15.0,
        power_reserve_w=200000,
    )
    for gid in ("G1", "G2", "G4"):
        d = evaluate_admission(catalog[gid], state_g6, ship_profile, mission_envelope, prof_survey, catalog)
        p = d["placement"]
        state_g6.installed_guests[gid] = InstalledGuestState(
            guest_id=gid,
            active_mode=p["initial_mode"],
            placements=p["subcomponent_positions"],
            compute_node=p["compute_node"],
            powered_on=True,
        )
    g6_admission = evaluate_admission(
        catalog["G6"], state_g6, ship_profile, mission_envelope, prof_survey, catalog
    )

    # 2. Scenario B under survey_primary and rescue_primary + cooling_limited_30c
    replay_b_survey = run_scenario_b_replay(scen_b, ship_profile, prof_survey, catalog)
    replay_b_rescue = run_scenario_b_replay(scen_b, ship_profile, prof_rescue, catalog)
    replay_b_cooling = run_scenario_b_replay(scen_b_cool, ship_profile, prof_survey, catalog)

    # 3. Scenario C crew regimes comparison + event replay
    state_c = build_initial_state_from_scenario(scen_b, catalog)
    crew_comparison = evaluate_crew_regimes_comparison(catalog, state_c)
    replay_c_events = run_scenario_b_replay(scen_c, ship_profile, prof_survey, catalog)
    crew_comparison["scenario_c_event_decisions"] = _strip_wall_clock(replay_c_events["decisions"])
    crew_comparison["scenario_c_event_semantic_sha256"] = replay_c_events["semantic_sha256"]

    semantic_summary = {
        "scenario_A_four_guest_combinations": combos_report,
        "scenario_A_g6_admission": _strip_wall_clock(g6_admission),
        "scenario_A_g6_admission_verdict": g6_admission["verdict"],
        "scenario_B_survey_primary_sha256": replay_b_survey["semantic_sha256"],
        "scenario_B_rescue_primary_sha256": replay_b_rescue["semantic_sha256"],
        "scenario_B_cooling_limited_sha256": replay_b_cooling["semantic_sha256"],
        "scenario_C_crew_regimes": crew_comparison,
    }
    campaign_semantic_sha256 = canonical_json_hash(semantic_summary)
    wall_ms = round((time.perf_counter_ns() - t0_ns) / 1_000_000.0, 3)

    return {
        "campaign_semantic_sha256": campaign_semantic_sha256,
        "semantic_summary": semantic_summary,
        "scenario_A": {
            "four_guest_combinations": combos_report,
            "g6_admission": g6_admission,
        },
        "scenario_B": {
            "survey_primary": replay_b_survey,
            "rescue_primary": replay_b_rescue,
            "cooling_limited_30c": replay_b_cooling,
        },
        "scenario_C": crew_comparison,
        "runtime_telemetry": {
            "hostname": socket.gethostname(),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
            "python_version": platform.python_version(),
            "pid": os.getpid(),
            "wall_elapsed_ms": wall_ms,
        },
    }

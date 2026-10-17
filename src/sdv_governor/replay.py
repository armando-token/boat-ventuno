"""Deterministic event-driven scenario simulator and replay engine (B3, B5, B6, B10, RUN-01, DEP-03, H03, H04, H11)."""

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
from .scheduling import evaluate_crew_regimes_comparison, handle_lost_or_stale_feed
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
                powered_on=True,
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
                powered_on=True,
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
        crew_count=int(init.get("crew_count", 14)),
        crew_regime=init.get("crew_regime", "FULL_CREW_14"),
        available_crew=int(init.get("available_crew", 9)),
        deck_clear=bool(init.get("deck_clear", True)),
        installed_guests=installed,
        firm_reserved_loads=firm_loads,
    )


def run_scenario_b_replay(
    scenario_data: Dict[str, Any],
    ship_profile: Dict[str, Any],
    priority_profile: PriorityProfile,
    catalog: Dict[str, GuestManifest],
    *,
    compressed_90s: bool = True,
) -> Dict[str, Any]:
    """Execute deterministic event-driven Scenario B replay (compressed 90 s 1 Hz timeline + full event sequence).

    Strictly reads and executes `scenario_data['events']` sorted by `(sim_time_s, priority, seq)` (H03),
    tracks causal acoustic HOLD command -> ACK correlation, separates G4 touchdown (`<= hard_touchdown_deadline_s`)
    from its 20-min (`1200 s`) 35 kW hangar charging phase (H04), maintains active plan versioning so D3
    milestones never overwrite D4 degradations, and advances `state.thermal_inertia.step_tick` every tick (H11).
    """
    t0_wall_ns = time.perf_counter_ns()
    validate_ship_profile(ship_profile)
    state = build_initial_state_from_scenario(scenario_data, catalog)

    decisions: List[Dict[str, Any]] = []
    timeline_ticks: List[Dict[str, Any]] = []

    total_ticks = 90 if compressed_90s else 60
    dt_s = 20 if compressed_90s else 30

    # Sort scenario events deterministically by (sim_time_s, priority, seq)
    raw_events = list(scenario_data.get("events", []))
    sorted_events = sorted(
        enumerate(raw_events),
        key=lambda item: (
            int(item[1]["sim_time_s"]),
            int(item[1].get("priority", 10)),
            int(item[1].get("seq", item[0] + 1)),
        ),
    )
    executed_event_indices: Set[int] = set()

    arb_decision: Optional[Dict[str, Any]] = None
    fired_d3_milestones: Set[str] = set()
    active_plan_version = 0
    pending_hold_cmd_time_s: Optional[int] = None
    executed_touchdown_time_s: Optional[int] = None

    for tick in range(total_ticks + 1):
        t_tick_ns = time.perf_counter_ns()
        state.tick_seq = tick
        prev_sim_time_s = state.sim_time_s if tick > 0 else int(scenario_data.get("initial_state", {}).get("sim_time_s", 0))
        state.sim_time_s = tick * dt_s if int(scenario_data.get("initial_state", {}).get("sim_time_s", 0)) == 0 else (
            int(scenario_data["initial_state"]["sim_time_s"]) + tick * dt_s
        )

        # Advance elapsed time in current mode for all powered-on guests
        if tick > 0:
            step_dt = max(1, state.sim_time_s - prev_sim_time_s)
            for ginst in state.installed_guests.values():
                if ginst.powered_on:
                    ginst.mode_elapsed_s += step_dt

        events_at_tick: List[str] = []

        # 1. Process all scenario events whose sim_time_s falls within the current tick window
        for ev_idx, ev in sorted_events:
            if ev_idx in executed_event_indices:
                continue
            ev_time = int(ev["sim_time_s"])
            if ev_time <= state.sim_time_s:
                executed_event_indices.add(ev_idx)
                ev_type = str(ev["event_type"])

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
                    sem_arb = dict(arb_decision)
                    sem_arb.pop("calculation_elapsed_ms", None)
                    sem_arb["plan_version"] = active_plan_version
                    decisions.append(
                        {
                            "phase": "D3_ARBITRATION",
                            "tick": tick,
                            "sim_time_s": state.sim_time_s,
                            "event_time_s": ev_time,
                            "plan_version": active_plan_version,
                            "decision": sem_arb,
                        }
                    )

                elif ev_type == "ACOUSTIC_HOLD_CONFIRMED":
                    # Causal check: an acoustic ACK is only valid if an ACOUSTIC_HOLD command has already been sent!
                    if pending_hold_cmd_time_s is not None and state.sim_time_s >= pending_hold_cmd_time_s:
                        if "G3" in state.installed_guests:
                            state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
                            state.installed_guests["G3"].acoustic_hold_confirmed = True
                            state.installed_guests["G3"].mode_elapsed_s = 0
                        fired_d3_milestones.add("HOLD_ACK")
                        events_at_tick.append(f"G3_ACOUSTIC_HOLD_ACK_RECEIVED@{ev_time}s")
                    else:
                        events_at_tick.append(f"IGNORED_PREMATURE_ACOUSTIC_ACK_NO_ACTIVE_CMD@{ev_time}s")

                elif ev_type == "SHIP_HEADING_STABILIZED_INTO_WIND":
                    if "G3" in state.installed_guests and state.installed_guests["G3"].acoustic_hold_confirmed:
                        state.vessel_mode = str(ev.get("vessel_mode", "UAV_RECOVERY_HEADING"))
                        spd_ev = float(ev.get("ship_speed_kn", 7.0))
                        state.ship_speed_kn = spd_ev
                        state.speed_kn = spd_ev
                        state.heading_deg = state.wind_deg
                        events_at_tick.append(f"SHIP_HEADING_STABILIZED_INTO_WIND@{ev_time}s")

                elif ev_type == "GENERATOR_LOSS":
                    # Apply any D3 milestones scheduled at or before ev_time FIRST so state at ev_time is causally exact
                    _apply_due_d3_milestones(
                        state=state,
                        up_to_time_s=ev_time,
                        arb_decision=arb_decision,
                        fired_d3_milestones=fired_d3_milestones,
                        events_at_tick=events_at_tick,
                    )
                    new_res_w = int(ev.get("new_power_reserve_w", 100000))
                    state.power_reserve_w = new_res_w
                    state.vessel_mode = "DEGRADED_POWER"
                    events_at_tick.append(f"PERTURBATION_GENERATOR_LOSS_TO_{new_res_w // 1000}KW@{ev_time}s")

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
                            "sim_time_s": state.sim_time_s,
                            "event_time_s": ev_time,
                            "plan_version": active_plan_version,
                            "decision": sem_deg,
                        }
                    )

                elif ev_type == "COOLING_LOOP_FAILURE":
                    _apply_due_d3_milestones(
                        state=state,
                        up_to_time_s=ev_time,
                        arb_decision=arb_decision,
                        fired_d3_milestones=fired_d3_milestones,
                        events_at_tick=events_at_tick,
                    )
                    state.cooling_loop_degraded = bool(ev.get("cooling_loop_degraded", True))
                    if "seawater_temp_c" in ev:
                        state.seawater_temp_c = float(ev["seawater_temp_c"])
                    if "new_power_reserve_w" in ev:
                        state.power_reserve_w = int(ev["new_power_reserve_w"])
                    state.vessel_mode = "DEGRADED_THERMAL"
                    events_at_tick.append(f"PERTURBATION_COOLING_LOOP_40PCT_LOSS_AT_{int(state.seawater_temp_c or 30)}C@{ev_time}s")

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
                            "sim_time_s": state.sim_time_s,
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
                    events_at_tick.append(f"RESTORATION_RESERVE_{state.power_reserve_w // 1000}KW_COOLING_NOMINAL@{ev_time}s")

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
                                "sim_time_s": state.sim_time_s,
                                "event_time_s": ev_time,
                                "plan_version": active_plan_version,
                                "decision": sem_rec,
                            }
                        )

        # 2. Apply due scheduled milestones from active D3 arbitration plan up to state.sim_time_s
        td_time = _apply_due_d3_milestones(
            state=state,
            up_to_time_s=state.sim_time_s,
            arb_decision=arb_decision,
            fired_d3_milestones=fired_d3_milestones,
            events_at_tick=events_at_tick,
        )
        if td_time is not None and executed_touchdown_time_s is None:
            executed_touchdown_time_s = td_time
        if "HOLD_CMD" in fired_d3_milestones and pending_hold_cmd_time_s is None and arb_decision is not None:
            pending_hold_cmd_time_s = arb_decision["hold_cmd_time_s"]

        # 3. Advance thermal inertia state per tick without resetting credit (PHY-09, H11)
        eff_temp_c, _ = state.effective_seawater_temp_c()
        _, eff_cool_w, _ = compute_cooling_water_capacity_w(eff_temp_c, loop_degraded=state.cooling_loop_degraded)
        tick_water_heat_w = sum(
            catalog[gid].modes[ginst.active_mode].cooling_water_w
            for gid, ginst in state.installed_guests.items()
            if ginst.powered_on and gid in catalog
        )
        excess_heat_w = max(0, tick_water_heat_w - eff_cool_w)
        if tick > 0:
            step_dt = max(1, state.sim_time_s - prev_sim_time_s)
            _, inertia_status = state.thermal_inertia.step_tick(excess_heat_w, dt_s=step_dt)
        else:
            inertia_status = "THERMAL_NOMINAL"

        calc_ms = (time.perf_counter_ns() - t_tick_ns) / 1_000_000.0
        snap = evaluate_eight_class_budgets(
            ship_profile, catalog, state, calc_elapsed_ms=round(calc_ms, 4), source_time_ns=tick * 1_000_000_000
        )

        timeline_ticks.append(
            {
                "tick": tick,
                "sim_time_s": state.sim_time_s,
                "plan_version": active_plan_version,
                "vessel_mode": state.vessel_mode,
                "events": events_at_tick,
                "guest_modes": {
                    gid: {
                        "mode": ginst.active_mode,
                        "powered_on": ginst.powered_on,
                        "mode_elapsed_s": ginst.mode_elapsed_s,
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
                "measured_tick_calc_ms": round(calc_ms, 4),
            }
        )

    wall_total_ms = round((time.perf_counter_ns() - t0_wall_ns) / 1_000_000.0, 3)

    # Canonical semantic payload (independent of wall clock, PID, or host)
    _wall_clock_keys = {"measured_tick_calc_ms", "measured_latency_ms", "calculation_time_ms"}

    def _strip_wall_clock(obj: Any, parent_key: str = "") -> Any:
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

    return {
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


def _apply_due_d3_milestones(
    *,
    state: ShipOperationalState,
    up_to_time_s: int,
    arb_decision: Optional[Dict[str, Any]],
    fired_d3_milestones: Set[str],
    events_at_tick: List[str],
) -> Optional[int]:
    """Apply due D3 arbitration milestones once each in strict causal order without clobbering D4 actions."""
    if arb_decision is None or arb_decision.get("verdict") != "YES":
        return None

    touchdown_recorded: Optional[int] = None
    hold_cmd_t = int(arb_decision["hold_cmd_time_s"])
    hold_ack_t = int(arb_decision["hold_ack_time_s"])
    turn_t = int(arb_decision["turn_into_wind_time_s"])
    stab_t = int(arb_decision.get("stabilized_time_s", turn_t + 90))
    td_t = int(arb_decision["touchdown_time_s"])
    chg_done_t = int(arb_decision.get("charging_complete_time_s", td_t + 1200))

    if up_to_time_s >= hold_cmd_t and "HOLD_CMD" not in fired_d3_milestones:
        fired_d3_milestones.add("HOLD_CMD")
        events_at_tick.append(f"G3_ACOUSTIC_HOLD_CMD_SENT@{hold_cmd_t}s")

    if up_to_time_s >= hold_ack_t and "HOLD_ACK" not in fired_d3_milestones:
        fired_d3_milestones.add("HOLD_ACK")
        if "G3" in state.installed_guests:
            state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
            state.installed_guests["G3"].acoustic_hold_confirmed = True
            state.installed_guests["G3"].mode_elapsed_s = 0
        events_at_tick.append(f"G3_ENTERED_HOLD_ACOUSTIC@{hold_ack_t}s")

    if up_to_time_s >= turn_t and "TURN_INTO_WIND" not in fired_d3_milestones:
        fired_d3_milestones.add("TURN_INTO_WIND")
        if state.vessel_mode not in ("DEGRADED_POWER", "DEGRADED_THERMAL"):
            state.vessel_mode = "UAV_RECOVERY_HEADING"
        state.heading_deg = state.wind_deg
        state.speed_kn = 7.0
        state.ship_speed_kn = 7.0
        events_at_tick.append(f"VESSEL_TURNED_INTO_WIND_FOR_G4_RECOVERY@{turn_t}s")

    if up_to_time_s >= stab_t and "RECOVERY_APPROACH" not in fired_d3_milestones:
        fired_d3_milestones.add("RECOVERY_APPROACH")
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
            state.installed_guests["G4"].mode_elapsed_s = 0
        events_at_tick.append(f"G4_STARTED_35KW_RECOVERY_APPROACH@{stab_t}s")

    if up_to_time_s >= td_t and "TOUCHDOWN" not in fired_d3_milestones:
        fired_d3_milestones.add("TOUCHDOWN")
        touchdown_recorded = td_t
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].placements["G4_DRONE_VEHICLE"] = "hangar"
            state.installed_guests["G4"].battery_pct = 20.0
            state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"
            state.installed_guests["G4"].mode_elapsed_s = 0
        events_at_tick.append(f"G4_TOUCHDOWN_AND_SECURED_IN_HANGAR@{td_t}s")
        events_at_tick.append(f"G4_STARTED_20MIN_35KW_HANGAR_CHARGE@{td_t}s")

    if up_to_time_s >= chg_done_t and "CHARGE_COMPLETE" not in fired_d3_milestones:
        fired_d3_milestones.add("CHARGE_COMPLETE")
        if "G4" in state.installed_guests and state.installed_guests["G4"].powered_on:
            state.installed_guests["G4"].active_mode = "STANDBY"
            state.installed_guests["G4"].battery_pct = 100.0
            state.installed_guests["G4"].mode_elapsed_s = 0
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

    # 2. Scenario B under survey_primary and rescue_primary
    replay_b_survey = run_scenario_b_replay(scen_b, ship_profile, prof_survey, catalog)
    replay_b_rescue = run_scenario_b_replay(scen_b, ship_profile, prof_rescue, catalog)
    replay_b_cooling = run_scenario_b_replay(scen_b_cool, ship_profile, prof_survey, catalog)

    # 3. Scenario C crew regimes comparison
    state_c = build_initial_state_from_scenario(scen_b, catalog)
    crew_comparison = evaluate_crew_regimes_comparison(catalog, state_c)

    semantic_summary = {
        "scenario_A_four_guest_combinations": combos_report,
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

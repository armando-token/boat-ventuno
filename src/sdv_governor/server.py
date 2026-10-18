"""Stateful HTTP JSON API and static UI server for the single-screen SDV Resource Governor console (B10, DEM-01, DEM-02, H12, H13, R2, R5, L6, L7)."""

from __future__ import annotations

import copy
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import parse_qs, urlparse
import yaml

from .admission import (
    apply_admission_decision_atomically,
    build_baseline_state_with_installed_guests,
    evaluate_admission,
    evaluate_four_guest_combinations,
)
from .acoustic import evaluate_acoustic_link
from .arbitration import arbitrate_live_conflict
from .budgets import compute_cooling_water_capacity_w, evaluate_eight_class_budgets
from .degradation import (
    evaluate_reconnection,
    execute_graceful_degradation,
    handle_guest_overconsumption,
    issue_urgent_command,
)
from .deployment import run_doctor
from .manifest import (
    load_guest_catalog,
    load_priority_profile,
    parse_priority_profile,
    validate_and_build_guest_manifest,
    validate_mission_envelope,
    validate_ship_profile,
)
from .models import (
    GuestManifest,
    PriorityProfile,
    StrictValidationError,
    require_bool,
    require_dict,
    require_finite_number,
    require_int,
    require_no_unknown_keys,
    require_str,
)
from .replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_scenario_b_replay,
)
from .scheduling import (
    evaluate_a_prime_window,
    evaluate_crew_regimes_comparison,
    handle_lost_or_stale_feed,
)
from .ship_state import FirmReservedLoad, InstalledGuestState, ShipOperationalState


CREW_COUNT_TO_REGIME: Dict[int, Tuple[str, int]] = {
    14: ("FULL_CREW_14", 9),
    6: ("REDUCED_CREW_6", 2),
    0: ("UNCREWED_0", 0),
}


def _format_witness_causal_sentence(
    prefix: str,
    witnesses: List[Dict[str, Any]],
    fallback_reason: str = "",
) -> str:
    """Build a deterministic 1-sentence causal explanation from binding witnesses (L6, §10.1)."""
    if witnesses:
        w = witnesses[0]
        r_cls = w.get("resource_class", "resource")
        c_id = w.get("constraint_id", "constraint")
        dem = w.get("demand", "?")
        cap = w.get("capacity", "?")
        mar = w.get("margin", "?")
        unit = w.get("unit", "")
        u_str = f" {unit}" if unit else ""
        detail = w.get("detail", "")
        if detail:
            return f"{prefix}: [{r_cls}.{c_id}] demand={dem}{u_str} vs capacity={cap}{u_str} (margin={mar}{u_str}) — {detail}."
        return f"{prefix}: [{r_cls}.{c_id}] demand={dem}{u_str} vs capacity={cap}{u_str} (margin={mar}{u_str})."
    if fallback_reason:
        return f"{prefix}: {fallback_reason}."
    return f"{prefix}."


ACOUSTIC_PERTURBATIONS = frozenset({"WATER_COLUMN_LAYERED", "ACOUSTIC_LINK_LOSS", "ACOUSTIC_LINK_RESTORED"})


def _replan_d3_after_link_change(
    sess: "GovernorSession",
    ship_profile: Dict[str, Any],
    priority_profile: Any,
) -> Optional[Dict[str, Any]]:
    """Re-arbitrate the live D3 plan when the acoustic link changes before G3 is confirmed on HOLD (A.5)."""
    dec = sess.active_arb_decision
    state = sess.live_state
    assert state is not None
    if (
        dec is None
        or dec.get("verdict") != "YES"
        or "HOLD_ACK" in sess.fired_d3_milestones
        or "TURN_INTO_WIND" in sess.fired_d3_milestones
        or any(k.startswith("PLAN_INVALIDATED") for k in sess.fired_d3_milestones)
    ):
        return None
    now = int(state.sim_time_s)
    deadline = int(dec["hard_touchdown_deadline_s"])
    stab = int(dec.get("planned_stabilized_time_s", dec["stabilized_time_s"]))
    touch = int(dec.get("planned_touchdown_time_s", dec["touchdown_time_s"]))
    hold_cmd = int(dec.get("planned_hold_cmd_time_s", dec["hold_cmd_time_s"]))
    course_settle = int(dec.get("course_settle_s", 90))
    t0 = time.perf_counter()
    arb = arbitrate_live_conflict(
        state.clone(),
        ship_profile,
        priority_profile,
        sess.session_catalog,
        uav_battery_pct=float(dec.get("uav_battery_pct", 18.0)),
        uav_max_endurance_s=max(1, deadline - now),
        uav_approach_margin_s=max(0, touch - stab) + course_settle,
        remaining_survey_waypoint_s=max(0, hold_cmd - now),
    )
    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    sess.active_plan_version += 1
    sess.fired_d3_milestones.clear()
    state.pending_commands.pop("G3", None)
    sem = dict(arb)
    sem.pop("calculation_elapsed_ms", None)
    sem["plan_version"] = sess.active_plan_version
    if sem.get("verdict") == "YES":
        sem["planned_hold_cmd_time_s"] = int(sem["hold_cmd_time_s"])
        sem["planned_hold_ack_time_s"] = int(sem["hold_ack_time_s"])
        sem["planned_turn_into_wind_time_s"] = int(sem["turn_into_wind_time_s"])
        sem["planned_stabilized_time_s"] = int(sem["stabilized_time_s"])
        sem["planned_touchdown_time_s"] = int(sem["touchdown_time_s"])
        sem["planned_charging_complete_time_s"] = int(sem["charging_complete_time_s"])
        g4_inst = state.installed_guests.get("G4")
        if g4_inst is not None and g4_inst.powered_on:
            state.firm_reserved_loads = [fl for fl in state.firm_reserved_loads if fl.guest_id != "G4"] + [
                FirmReservedLoad(
                    reservation_id=f"D3_PLAN_V{sess.active_plan_version}_G4_RECOVERY",
                    guest_id="G4",
                    reserved_mode="LAUNCH_RECOVERY_OPTICS",
                    reserved_power_w=35000,
                    reason="D3 committed UAV recovery approach and 20-min hangar charge (35 kW)",
                )
            ]
        for k in (
            "executed_hold_cmd_time_s",
            "executed_hold_ack_time_s",
            "executed_turn_into_wind_time_s",
            "executed_stabilized_time_s",
            "executed_touchdown_time_s",
            "executed_charging_complete_time_s",
        ):
            sem[k] = None
    else:
        state.firm_reserved_loads = [
            fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
        ]
        sess.executed_touchdown_time_s = None
    sess.active_arb_decision = sem
    sess.executed_decisions.append(
        {
            "phase": "D3_ARBITRATION",
            "tick": now // 20,
            "sim_time_s": now,
            "event_time_s": now,
            "plan_version": sess.active_plan_version,
            "decision": sem,
        }
    )
    return {"arbitration": sem, "elapsed_ms": elapsed_ms}


def _apply_crew_and_deck_updates(
    live_state: ShipOperationalState,
    crew_count: Optional[int] = None,
    deck_clear: Optional[bool] = None,
) -> List[str]:
    """Update crew_regime / available_crew (14 / 6 / 0) and deck_clear on live_state."""
    tags: List[str] = []
    if crew_count is not None:
        if crew_count not in CREW_COUNT_TO_REGIME:
            raise StrictValidationError(
                "INVALID_CREW_COUNT",
                f"crew_count must be one of 14, 6, or 0 (got {crew_count})",
                "crew_count",
            )
        regime_name, avail_crew = CREW_COUNT_TO_REGIME[crew_count]
        live_state.crew_regime = regime_name
        live_state.available_crew = avail_crew
        tags.append(f"CREW_REGIME:{regime_name}")
    if deck_clear is not None:
        live_state.deck_clear = deck_clear
        tags.append(f"DECK_CLEAR:{deck_clear}")
    return tags


def _resync_component_allocations(
    raw_cand: Dict[str, Any],
    mode_name: str,
    m_dict: Dict[str, Any],
) -> None:
    """Re-synchronize component_allocations for mode_name after live mode overrides (ALIGN-T33)."""
    alloc_containers: List[Dict[str, Any]] = []
    top_allocs = raw_cand.get("component_allocations")
    if isinstance(top_allocs, dict) and isinstance(top_allocs.get(mode_name), dict):
        alloc_containers.append(top_allocs[mode_name])
    mode_allocs = m_dict.get("component_allocations")
    if isinstance(mode_allocs, dict):
        alloc_containers.append(mode_allocs)

    if not alloc_containers:
        return

    subs = raw_cand.get("subcomponents", [])
    sub_by_id = {sc.get("subcomponent_id"): sc for sc in subs if isinstance(sc, dict)}

    nom_total = int(m_dict.get("nominal_power_w", 0))
    sust_total = int(m_dict.get("sustained_power_w", 0))
    peak_total = int(m_dict.get("peak_power_w", 0))
    cw_total = int(m_dict.get("cooling_water_w", 0))
    ca_total = int(m_dict.get("cooling_air_mast_w", 0))

    for alloc_map in alloc_containers:
        sub_ids = [sid for sid in alloc_map.keys() if isinstance(alloc_map.get(sid), dict)]
        if not sub_ids:
            continue

        air_sid: Optional[str] = None
        water_sid: Optional[str] = None
        for sid in sub_ids:
            sc_spec = sub_by_id.get(sid, {})
            route = str(sc_spec.get("cooling_route", "")).upper()
            if ("AIR" in route or route == "PASSIVE_AIR_MAST") and air_sid is None:
                air_sid = sid
            elif ("WATER" in route or route == "CHILLED_WATER_LOOP") and water_sid is None:
                water_sid = sid

        if water_sid is None:
            for sid in sub_ids:
                if sid != air_sid:
                    water_sid = sid
                    break
        if water_sid is None:
            water_sid = sub_ids[0]

        for sid in sub_ids:
            entry = alloc_map[sid]
            entry["nominal_power_w"] = 0
            entry["sustained_power_w"] = 0
            entry["peak_power_w"] = 0
            entry["cooling_water_w"] = 0
            if "cooling_air_w" in entry:
                entry["cooling_air_w"] = 0
            if "cooling_air_mast_w" in entry:
                entry["cooling_air_mast_w"] = 0

        if air_sid is not None and air_sid != water_sid:
            air_entry = alloc_map[air_sid]
            air_nom = min(nom_total, ca_total)
            air_sust = min(sust_total, ca_total)
            air_peak = min(peak_total, ca_total)
            air_entry["nominal_power_w"] = air_nom
            air_entry["sustained_power_w"] = air_sust
            air_entry["peak_power_w"] = air_peak
            air_entry["cooling_water_w"] = 0
            if "cooling_air_w" in air_entry or "cooling_air_mast_w" not in air_entry:
                air_entry["cooling_air_w"] = ca_total
            if "cooling_air_mast_w" in air_entry:
                air_entry["cooling_air_mast_w"] = ca_total

            w_entry = alloc_map[water_sid]
            w_entry["nominal_power_w"] = max(0, nom_total - air_nom)
            w_entry["sustained_power_w"] = max(0, sust_total - air_sust)
            w_entry["peak_power_w"] = max(0, peak_total - air_peak)
            w_entry["cooling_water_w"] = cw_total
        else:
            w_entry = alloc_map[water_sid]
            w_entry["nominal_power_w"] = nom_total
            w_entry["sustained_power_w"] = sust_total
            w_entry["peak_power_w"] = peak_total
            w_entry["cooling_water_w"] = cw_total
            if "cooling_air_w" in w_entry or "cooling_air_mast_w" not in w_entry:
                w_entry["cooling_air_w"] = ca_total
            if "cooling_air_mast_w" in w_entry:
                w_entry["cooling_air_mast_w"] = ca_total


class GovernorSession:
    """Persistent operational session state, guest catalog, and manifest registry across API calls (H12, R2, R5, R8-F02, R8-F03)."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.lock = threading.RLock()
        self.state_version: int = 0
        self.priority_profile_id: str = "survey_primary"
        self.scenario_file: str = "scenario_B_arbitration_degradation.yaml"
        self.live_state: Optional[ShipOperationalState] = None
        self.session_catalog: Dict[str, GuestManifest] = {}
        self.manifest_registry: Dict[str, Dict[str, Any]] = {}
        self.custom_profiles: Dict[str, PriorityProfile] = {}
        self.has_live_mutations: bool = False
        self.action_history: List[Dict[str, Any]] = []
        self.admission_attempts: List[Dict[str, Any]] = []
        self.run_id: str = "RUN_SCENARIO_B_0001"
        self.runner_status: str = "PAUSED"
        self.executed_ticks: List[Dict[str, Any]] = []
        self.executed_decisions: List[Dict[str, Any]] = []
        self.consumed_event_indices: set[int] = set()
        self.fired_d3_milestones: set[str] = set()
        self.active_arb_decision: Optional[Dict[str, Any]] = None
        self.active_plan_version: int = 0
        self.executed_touchdown_time_s: Optional[int] = None
        self.superseded_event_types: set[str] = set()
        self._forecast_cache: Optional[Dict[str, Any]] = None
        self.reset(self.priority_profile_id, self.scenario_file)

    @property
    def snapshot_id(self) -> str:
        sim_t = self.live_state.sim_time_s if self.live_state is not None else 0
        return f"SNAP_V{self.state_version:04d}_T{sim_t}S"

    def reset(
        self,
        priority_profile_id: str = "survey_primary",
        scenario_file: str = "scenario_B_arbitration_degradation.yaml",
    ) -> None:
        data_dir = self.repo_root / "data"
        self.session_catalog = load_guest_catalog(
            data_dir / "guests",
            errata_path=data_dir / "overrides" / "brief_errata.yaml",
        )
        self.manifest_registry = {}
        for ypath in sorted((data_dir / "guests").glob("*.yaml")):
            raw_g = yaml.safe_load(ypath.read_text(encoding="utf-8"))
            self.manifest_registry[raw_g["guest_id"]] = raw_g

        scen_path = data_dir / "scenarios" / scenario_file
        if not scen_path.exists():
            scenario_file = "scenario_B_arbitration_degradation.yaml"
            scen_path = data_dir / "scenarios" / scenario_file
        scen_data = load_yaml_file(scen_path)
        self.priority_profile_id = priority_profile_id
        self.scenario_file = scenario_file
        self.live_state = build_initial_state_from_scenario(scen_data, self.session_catalog)
        self.has_live_mutations = False
        self.state_version += 1
        self.run_id = f"RUN_{self.scenario_file.split('.')[0].upper()}_V{self.state_version:04d}"
        self.runner_status = "PAUSED"
        self.executed_ticks = []
        self.executed_decisions = []
        self.consumed_event_indices = set()
        self.fired_d3_milestones = set()
        self.active_arb_decision = None
        self.active_plan_version = 0
        self.executed_touchdown_time_s = None
        self.superseded_event_types = set()
        self.admission_attempts = []
        self.action_history = [
            {
                "state_version": self.state_version,
                "snapshot_id": self.snapshot_id,
                "sim_time_s": self.live_state.sim_time_s,
                "action": "SESSION_INITIALIZED",
                "priority_profile_id": self.priority_profile_id,
                "scenario_file": self.scenario_file,
                "run_id": self.run_id,
                "causal_sentence": f"Session {self.run_id} initialized at t=0s with profile '{self.priority_profile_id}'.",
            }
        ]

    def _sync_current_executed_tick(
        self,
        ship_profile: Dict[str, Any],
        event_tag: Optional[str] = None,
    ) -> Dict[str, Any]:
        assert self.live_state is not None
        snap = evaluate_eight_class_budgets(ship_profile, self.session_catalog, self.live_state)
        snap["snapshot_id"] = self.snapshot_id
        sim_t = int(self.live_state.sim_time_s)
        tick_idx = sim_t // 20
        existing_events: List[str] = []
        if self.executed_ticks and int(self.executed_ticks[-1]["sim_time_s"]) == sim_t:
            existing_events = list(self.executed_ticks[-1].get("events", []))
        if event_tag and event_tag not in existing_events:
            existing_events.append(event_tag)
        tick_entry = {
            "tick": tick_idx,
            "sim_time_s": sim_t,
            "snapshot_id": self.snapshot_id,
            "plan_version": self.active_plan_version,
            "vessel_mode": self.live_state.vessel_mode,
            "speed_kn": self.live_state.speed_kn,
            "wind_kn": self.live_state.wind_kn,
            "sea_state": self.live_state.sea_state,
            "seawater_temp_c": self.live_state.effective_seawater_temp_c()[0],
            "crew_regime": self.live_state.crew_regime,
            "deck_clear": self.live_state.deck_clear,
            "events": existing_events,
            "guest_modes": {
                gid: {
                    "mode": ginst.active_mode,
                    "powered_on": ginst.powered_on,
                    "mode_elapsed_s": ginst.mode_elapsed_s,
                    "placements": dict(ginst.placements),
                    "compute_node": ginst.compute_node,
                }
                for gid, ginst in sorted(self.live_state.installed_guests.items())
            },
            "power_actual_w": snap["classes"]["power"]["actual"],
            "power_committed_w": snap["classes"]["power"]["committed"],
            "power_capacity_w": snap["classes"]["power"]["degraded_capacity"],
            "cooling_actual_w": snap["classes"]["cooling"]["actual"],
            "cooling_capacity_w": snap["classes"]["cooling"]["degraded_capacity"],
            "thermal_inertia_status": "THERMAL_NOMINAL",
            "thermal_inertia_accumulated_j": self.live_state.thermal_inertia.accumulated_boost_energy_j,
            "feasible": snap["feasible"],
            "measured_tick_calc_ms": snap["classes"]["time"]["actual"],
            "budget_snapshot": snap,
            "is_prediction": False,
            "run_id": self.run_id,
        }
        if self.executed_ticks and int(self.executed_ticks[-1]["sim_time_s"]) == sim_t:
            self.executed_ticks[-1] = tick_entry
        else:
            self.executed_ticks.append(tick_entry)
        return snap

    def advance_to(
        self,
        target_sim_time_s: int,
        ship_profile: Dict[str, Any],
        priority_profile: Any,
    ) -> Dict[str, Any]:
        """Advance the single active session mission clock monotonically to target_sim_time_s (Phase S3, R8-F03)."""
        assert self.live_state is not None
        current_t = int(self.live_state.sim_time_s)
        target_t = max(current_t, min(1800, int(target_sim_time_s)))
        data_dir = self.repo_root / "data"
        scen_path = data_dir / "scenarios" / self.scenario_file
        if not scen_path.exists():
            scen_path = data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml"
        scen_data = load_yaml_file(scen_path)

        res = run_scenario_b_replay(
            scen_data,
            ship_profile,
            priority_profile,
            self.session_catalog,
            initial_state_override=self.live_state,
            consumed_event_indices=self.consumed_event_indices,
            fired_d3_milestones_init=self.fired_d3_milestones,
            active_arb_decision_init=self.active_arb_decision,
            active_plan_version_init=self.active_plan_version,
            executed_touchdown_time_s_init=self.executed_touchdown_time_s,
            stop_at_sim_time_s=target_t,
            superseded_event_types=self.superseded_event_types,
            return_continuation_state=True,
        )
        cont = res["continuation_state"]
        self.live_state = cont["ending_state"]
        self.consumed_event_indices = cont["consumed_event_indices"]
        self.fired_d3_milestones = cont["fired_d3_milestones"]
        self.active_arb_decision = cont["active_arb_decision"]
        self.active_plan_version = cont["active_plan_version"]
        self.executed_touchdown_time_s = cont["executed_touchdown_time_s"]

        existing_by_time = {int(t["sim_time_s"]): t for t in self.executed_ticks}
        for tick_item in res["timeline_ticks"]:
            t_sim = int(tick_item["sim_time_s"])
            tick_copy = copy.deepcopy(tick_item)
            tick_copy["is_prediction"] = False
            tick_copy["run_id"] = self.run_id
            if t_sim == current_t and t_sim in existing_by_time and existing_by_time[t_sim].get("events"):
                merged_ev = list(existing_by_time[t_sim]["events"])
                for ev_s in tick_copy.get("events", []):
                    if ev_s not in merged_ev:
                        merged_ev.append(ev_s)
                tick_copy["events"] = merged_ev
            existing_by_time[t_sim] = tick_copy
        self.executed_ticks = [existing_by_time[k] for k in sorted(existing_by_time.keys())]
        self.executed_decisions.extend(copy.deepcopy(res["decisions"]))
        self.state_version += 1
        if self.executed_ticks:
            self.executed_ticks[-1]["snapshot_id"] = self.snapshot_id
            if isinstance(self.executed_ticks[-1].get("budget_snapshot"), dict):
                self.executed_ticks[-1]["budget_snapshot"]["snapshot_id"] = self.snapshot_id
        self.runner_status = "COMPLETED" if self.live_state.sim_time_s >= 1800 else "PAUSED"
        self.action_history.append(
            {
                "state_version": self.state_version,
                "snapshot_id": self.snapshot_id,
                "sim_time_s": self.live_state.sim_time_s,
                "action": f"ADVANCE_CLOCK:{current_t}S->{self.live_state.sim_time_s}S",
                "run_id": self.run_id,
                "causal_sentence": f"Mission clock advanced deterministically from t={current_t}s to t={self.live_state.sim_time_s}s.",
            }
        )
        return res

    def build_unified_replay_view(
        self,
        ship_profile: Dict[str, Any],
        priority_profile: Any,
        scenario_file_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pure-read assembly of executed history + forward prediction from the active session state (Phase S3, R8-F03)."""
        assert self.live_state is not None
        data_dir = self.repo_root / "data"
        scen_name = scenario_file_override or self.scenario_file
        scen_path = data_dir / "scenarios" / scen_name
        if not scen_path.exists():
            scen_path = data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml"
        scen_data = load_yaml_file(scen_path)

        is_other_scenario = scenario_file_override is not None and scenario_file_override != self.scenario_file
        if is_other_scenario:
            return run_scenario_b_replay(
                scen_data,
                ship_profile,
                priority_profile,
                self.session_catalog,
            )

        current_t = int(self.live_state.sim_time_s)
        forward_res = run_scenario_b_replay(
            scen_data,
            ship_profile,
            priority_profile,
            self.session_catalog,
            initial_state_override=self.live_state if (self.has_live_mutations or current_t > 0) else None,
            consumed_event_indices=self.consumed_event_indices,
            fired_d3_milestones_init=self.fired_d3_milestones,
            active_arb_decision_init=self.active_arb_decision,
            active_plan_version_init=self.active_plan_version,
            executed_touchdown_time_s_init=self.executed_touchdown_time_s,
            superseded_event_types=self.superseded_event_types,
        )

        past_ticks = [copy.deepcopy(t) for t in self.executed_ticks if int(t["sim_time_s"]) < current_t]
        for pt in past_ticks:
            pt["is_prediction"] = False
            pt["run_id"] = self.run_id

        future_ticks: List[Dict[str, Any]] = []
        for ft in forward_res.get("timeline_ticks", []):
            ft_copy = copy.deepcopy(ft)
            ft_sim = int(ft_copy["sim_time_s"])
            if ft_sim == current_t:
                ft_copy["is_prediction"] = False
                ft_copy["run_id"] = self.run_id
                ft_copy["snapshot_id"] = self.snapshot_id
                if isinstance(ft_copy.get("budget_snapshot"), dict):
                    ft_copy["budget_snapshot"]["snapshot_id"] = self.snapshot_id
                if self.executed_ticks and int(self.executed_ticks[-1]["sim_time_s"]) == current_t:
                    prev_evs = list(self.executed_ticks[-1].get("events", []))
                    for ev_s in ft_copy.get("events", []):
                        if ev_s not in prev_evs:
                            prev_evs.append(ev_s)
                    ft_copy["events"] = prev_evs
            else:
                ft_copy["is_prediction"] = True
                ft_copy["run_id"] = self.run_id
            future_ticks.append(ft_copy)

        combined_ticks = past_ticks + future_ticks
        combined_decisions = copy.deepcopy(self.executed_decisions) + copy.deepcopy(forward_res.get("decisions", []))
        forward_res["timeline_ticks"] = combined_ticks
        forward_res["decisions"] = combined_decisions
        forward_res["run_id"] = self.run_id
        forward_res["current_sim_time_s"] = current_t
        return forward_res

    def build_decision_log(self, replay_decisions: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        """Assemble structured decision_log with 1-sentence causal explanations from real witnesses (L6, §10.1)."""
        entries: List[Dict[str, Any]] = []
        for act in self.action_history:
            c_sent = act.get("causal_sentence")
            if not c_sent:
                c_sent = f"Session v{act.get('state_version')} at t={act.get('sim_time_s', 0)}s executed {act.get('action')}."
            entries.append(
                {
                    "type": "SESSION_ACTION",
                    "state_version": act.get("state_version"),
                    "snapshot_id": act.get("snapshot_id"),
                    "sim_time_s": act.get("sim_time_s", 0),
                    "action": act.get("action"),
                    "causal_sentence": c_sent,
                }
            )
        for att in self.admission_attempts:
            c_sent = att.get("causal_sentence")
            if not c_sent:
                c_sent = f"Admission of {att.get('candidate_guest_id')} returned {att.get('verdict')} ({att.get('reason', 'unapplied')})."
            entries.append(
                {
                    "type": "ADMISSION_ATTEMPT",
                    "state_version": att.get("state_version"),
                    "snapshot_id": att.get("snapshot_id"),
                    "sim_time_s": att.get("sim_time_s", 0),
                    "candidate_guest_id": att.get("candidate_guest_id"),
                    "verdict": att.get("verdict"),
                    "causal_sentence": c_sent,
                }
            )
        for dec in replay_decisions or self.executed_decisions:
            # Replay decisions are shaped {"phase", "tick", "sim_time_s", "decision": {...}};
            # legacy entries may still carry {"decision_type", "details"}.
            d_type = dec.get("phase") or dec.get("decision_type") or "TIMELINE_DECISION"
            t_sim = dec.get("sim_time_s", 0)
            details = dec.get("decision") or dec.get("details") or {}
            c_sent = dec.get("causal_sentence") or details.get("rationale")
            if not c_sent:
                if "hold_cmd_time_s" in details or "touchdown_time_s" in details:
                    c_sent = (
                        f"t={t_sim}s [{d_type}]: {details.get('verdict', 'ARBITRATED')} "
                        f"(G3 HOLD commanded at t={details.get('hold_cmd_time_s')}s, "
                        f"G4 touchdown at t={details.get('touchdown_time_s')}s)."
                    )
                else:
                    actions = details.get("actions_taken", [])
                    if actions:
                        first_a = actions[0]
                        c_sent = (
                            f"t={t_sim}s [{d_type}]: {first_a.get('action', 'STEP_DOWN')} on {first_a.get('guest_id', '')} "
                            f"({first_a.get('from_mode', '')} -> {first_a.get('to_mode', '')}) -> "
                            f"{details.get('status', 'UNKNOWN')}."
                        )
                    else:
                        c_sent = f"t={t_sim}s [{d_type}]: {details.get('verdict') or details.get('status') or 'no action required'}."
            entries.append(
                {
                    "type": d_type,
                    "tick": dec.get("tick", t_sim),
                    "sim_time_s": t_sim,
                    "causal_sentence": c_sent,
                    "details": details,
                }
            )
        return entries

    def _load_env_snapshot(self) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """Load the validated active snapshot, or return the §7.1 fail-closed payload."""
        from . import envdata
        try:
            return envdata.load_active_snapshot(self.repo_root), None
        except (StrictValidationError, OSError, ValueError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            return None, {
                "available": False,
                "reason": "NO_ACTIVE_SNAPSHOT" if code == "ENV_SNAPSHOT_MISSING" else code,
                "detail": str(exc)[:300],
                "fallback": "A.10 30C",
            }

    def environment_view(self) -> Dict[str, Any]:
        """Return public environmental data snapshot view per Megaplan §7.1.

        GET /api/environment
        Everything comes from the validated snapshot (no literals); on any snapshot error the
        endpoint answers 200 {"available": false, ..., "fallback": "A.10 30C"} (fail-closed).
        """
        from . import envdata
        snap, unavailable = self._load_env_snapshot()
        if snap is None:
            return unavailable or {"available": False, "reason": "NO_ACTIVE_SNAPSHOT", "fallback": "A.10 30C"}

        manifest = snap["manifest"]
        series = snap["series"]
        anchor_utc = snap["anchor_utc"]
        sim_t = int(self.live_state.sim_time_s) if self.live_state else 0
        smp = envdata.sample_at(series, sim_time_s=sim_t, anchor_utc=anchor_utc)
        current = {
            "t_utc": smp.t_utc,
            "sst_c": smp.sst_c,
            "wind_kn": None if smp.wind_kn is None else round(float(smp.wind_kn), 1),
            "gust_kn": None if smp.gust_kn is None else round(float(smp.gust_kn), 1),
            "hs_m": None if smp.wave_hs_m is None else round(float(smp.wave_hs_m), 2),
            "sea_state_wmo": smp.sea_state_wmo,
            "provenance": smp.provenance,
            "source_id": smp.source_id,
            "lead_s": smp.lead_s,
        }
        sst_fallback = smp.sst_c is None
        c_nom, c_eff, c_note = compute_cooling_water_capacity_w(
            30.0 if sst_fallback else float(smp.sst_c),
            loop_degraded=bool(self.live_state.cooling_loop_degraded) if self.live_state else False,
        )
        gb = snap["guard_bands"]
        wb = gb.get("window_backtest") or {}
        return {
            "available": True,
            "site_id": snap["site_id"],
            "snapshot_id": snap["active_snapshot_name"],
            "snapshot_sha256": snap["snapshot_sha256"],
            "fetched_utc": manifest.get("fetched_utc"),
            "anchor_utc": anchor_utc,
            "attribution": sorted({
                f"{src.get('attribution')} ({src.get('license')})"
                for src in manifest.get("sources", [])
                if isinstance(src, dict) and src.get("attribution")
            }),
            "current": current,
            "cooling": {
                "nominal_w": float(c_nom),
                "effective_w": round(float(c_eff), 1),
                "sst_fallback": "A.10 30C" if sst_fallback else None,
                "policy_note": (
                    f"{c_note}. A.10 14-day admission envelope is still evaluated at 30 C (120.0 kW); "
                    "live cooling uses the snapshot SST."
                ),
            },
            "outlook_48h": envdata.cooling_capacity_outlook(series, anchor_utc=anchor_utc),
            "guard_bands": {
                "status": gb["status"],
                "reason": gb["reason"],
                "assumption": gb.get("assumption"),
                "guard_wind_kn": gb["guard"]["wind_kn"],
                "guard_hs_m": gb["guard"]["wave_hs_m"],
                "guard_sst_c": gb["guard"]["sst_c"],
                "quantile_used": gb.get("quantile_used"),
            },
            "validation": {
                "gate_g_env": gb.get("gate_g_env_status"),
                "period": gb.get("period"),
                "metrics": gb.get("metrics"),
                "window_false_open_guarded": {op: (v.get("guarded") or {}).get("false_open") for op, v in wb.items()},
                "window_false_open_raw": {op: (v.get("raw") or {}).get("false_open") for op, v in wb.items()},
            },
        }

    def windows_view(
        self,
        guest: str,
        op: str,
        ship_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return operational deck availability timeline and next window per Megaplan §7.1.

        GET /api/environment/windows?guest=G4&op=VEHICLE_REPLACEMENT_AT_SEA
        Uses the validated snapshot series (wind + Hs) and the snapshot guard bands; never fabricates
        a window. Steps without data are closed (fail-closed, INV-6).
        """
        from . import env_windows
        allowed_ops = ("LAUNCH_RECOVERY", "VEHICLE_REPLACEMENT_AT_SEA", "AT_SEA_INSTALL")
        if guest not in self.session_catalog:
            raise StrictValidationError("INVALID_PARAM", f"Guest '{guest}' not found in catalog", "guest")
        if op not in allowed_ops:
            raise StrictValidationError("INVALID_PARAM", f"Operation '{op}' not in allowed operations {allowed_ops}", "op")

        snap, unavailable = self._load_env_snapshot()
        if snap is None:
            return unavailable or {"available": False, "reason": "NO_ACTIVE_SNAPSHOT", "fallback": "A.10 30C"}

        cand_manifest = self.session_catalog[guest]
        now_sim_s = int(self.live_state.sim_time_s) if self.live_state else 0
        cur_eval = evaluate_a_prime_window(cand_manifest, op, self.live_state, self.session_catalog, ship_profile)
        min_dur, min_dur_src = env_windows.operation_min_duration_s(cand_manifest, op)
        timeline = env_windows.availability_timeline(
            cand_manifest,
            op,
            self.live_state,
            self.session_catalog,
            ship_profile,
            snap["series"],
            snap["anchor_utc"],
            step_s=3600,
            guard=snap["guard_bands"],
        )
        next_w = env_windows.find_next_window(timeline, min_duration_s=min_dur, now_sim_s=now_sim_s)
        if next_w is None:
            next_w = env_windows.describe_no_window(
                [r for r in timeline if r["sim_time_s"] >= now_sim_s], min_duration_s=min_dur
            )
        compact_timeline = [
            {
                "t_utc": item["t_utc"],
                "sim_time_s": item["sim_time_s"],
                "permitted": bool(item["permitted"]),
                "wind_kn_guarded": item.get("wind_kn"),
                "sea_state_guarded": item.get("sea_state"),
                "reasons": list(item.get("blocking_reasons", [])),
            }
            for item in timeline
        ]
        return {
            "available": True,
            "guest": guest,
            "op": op,
            "now_sim_s": now_sim_s,
            "snapshot_id": snap["active_snapshot_name"],
            "guard_band": env_windows.GuardBand.coerce(snap["guard_bands"]).to_dict(),
            "limits": env_windows.operation_limits(cand_manifest, op),
            "min_window_s": min_dur,
            "min_window_source": min_dur_src,
            "assumption": (
                "Timeline evaluates weather at the centre of the operation speed window "
                "(ASM-ENV-SPEED-ASSUMED); permitted_now uses the live speed, so a speed change "
                "(A.6 transition) may still be required."
            ),
            "permitted_now": bool(cur_eval.get("permitted", False)),
            "blocking_reasons_now": list(cur_eval.get("blocking_reasons", [])),
            "next_window": next_w,
            "timeline": compact_timeline,
        }

    def forecast_view(self) -> Dict[str, Any]:
        """Return the consumption observer view per Megaplan §7.1.

        GET /api/forecast
        The live session runs the official Scenario B, which has no guest power telemetry. The view
        therefore exposes the real ForecastObserver output of a deterministic replay of
        scenario_B_golden_gate_recorded (SIMULATED seeded telemetry), clearly labelled as such.
        If that replay cannot run, the endpoint answers enabled=false (never invented values).
        """
        with self.lock:
            cached = getattr(self, "_forecast_cache", None)
            if cached is not None:
                return cached
            try:
                from .forecast.observer import ForecastObserver
                data_dir = self.repo_root / "data"
                scen_name = "scenario_B_golden_gate_recorded.yaml"
                scen = load_yaml_file(data_dir / "scenarios" / scen_name)
                ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
                prof = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
                obs = ForecastObserver(ship_profile=ship, catalog=self.session_catalog)
                run_scenario_b_replay(scen, ship, prof, self.session_catalog, observer=obs)
                view = obs.forecast_view()
                ot = scen.get("observer_telemetry", {})
                view.update(
                    {
                        "source": f"REPLAY:{scen_name}",
                        "telemetry_provenance": "SIMULATED",
                        "telemetry_profile": {k: ot.get(k) for k in ("guest_id", "profile", "seed", "t0_s", "slope_w_per_s")},
                        "as_of_sim_time_s": 1800,
                        "units": {"y": "W", "fc": "W", "perf": "ms per on_tick (projector included)"},
                        "advisory_only": True,
                    }
                )
                self._forecast_cache = view
            except Exception as exc:  # never invent forecast values
                view = {
                    "enabled": False,
                    "reason": f"OBSERVER_UNAVAILABLE: {type(exc).__name__}: {str(exc)[:200]}",
                    "params_sha256": None,
                    "status_by_guest": {},
                    "alerts": [],
                    "series": {},
                    "perf": None,
                }
            return view


_SESSIONS_BY_ROOT: Dict[str, GovernorSession] = {}
_SESSIONS_LOCK = threading.RLock()


def get_or_create_session(repo_root: Path) -> GovernorSession:
    key = str(repo_root.resolve())
    with _SESSIONS_LOCK:
        if key not in _SESSIONS_BY_ROOT:
            _SESSIONS_BY_ROOT[key] = GovernorSession(repo_root)
        return _SESSIONS_BY_ROOT[key]


def reset_session(repo_root: Path) -> GovernorSession:
    key = str(repo_root.resolve())
    with _SESSIONS_LOCK:
        _SESSIONS_BY_ROOT[key] = GovernorSession(repo_root)
        return _SESSIONS_BY_ROOT[key]


def _serialize_priority_profile(prof: PriorityProfile) -> Dict[str, Any]:
    return {
        "priority_profile_id": prof.priority_profile_id,
        "name": prof.name,
        "description": prof.description,
        "source_sha256": prof.source_sha256,
        "capability_weights": dict(prof.capability_weights),
        "guest_priority_rank": dict(prof.guest_priority_rank),
        "survey_vs_recovery_strategy": prof.survey_vs_recovery_strategy,
        "pre_reserve_recovery_power": prof.pre_reserve_recovery_power,
        "max_acceptable_survey_pause_min": prof.max_acceptable_survey_pause_min,
        "tie_break_order": prof.tie_break_order,
        "prefer_mode_downgrade_before_shed": prof.prefer_mode_downgrade_before_shed,
        "protect_uninterruptible_tasks": prof.protect_uninterruptible_tasks,
        "shed_order_lowest_priority_first": list(prof.shed_order_lowest_priority_first),
    }


def _validate_custom_profile_domain(
    prof: PriorityProfile,
    session_catalog: Dict[str, GuestManifest],
    builtin_profiles: Dict[str, PriorityProfile],
) -> None:
    """Strictly verify custom profile guest IDs and capability weights against known domain (ALIGN-T27)."""
    known_gids: Set[str] = set(session_catalog.keys()) | {"G1", "G2", "G3", "G4", "G5", "G6"}
    for gid in prof.guest_priority_rank.keys():
        if gid not in known_gids:
            raise StrictValidationError(
                "UNKNOWN_GUEST_ID",
                f"Unknown guest_id '{gid}' in priority_profile.guest_priority_rank",
                f"priority_profile.guest_priority_rank.{gid}",
            )
    for idx, gid in enumerate(prof.shed_order_lowest_priority_first):
        if gid not in known_gids:
            raise StrictValidationError(
                "UNKNOWN_GUEST_ID",
                f"Unknown guest_id '{gid}' in shed_order_lowest_priority_first[{idx}]",
                f"priority_profile.degradation_policy.shed_order_lowest_priority_first[{idx}]",
            )

    known_caps: Set[str] = set()
    for bp in builtin_profiles.values():
        known_caps.update(bp.capability_weights.keys())
    for gm in session_catalog.values():
        known_caps.update(gm.mission_capabilities.keys())

    for cap_key in prof.capability_weights.keys():
        if cap_key not in known_caps:
            raise StrictValidationError(
                "UNKNOWN_CAPABILITY",
                f"Unknown capability '{cap_key}' in priority_profile.capability_weights",
                f"priority_profile.capability_weights.{cap_key}",
            )


def _augment_canonical_envelope(
    payload: Dict[str, Any],
    sess: GovernorSession,
    budget_snap: Dict[str, Any],
    replay_decisions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attach canonical §10.1 top-level fields while preserving all existing fields."""
    assert sess.live_state is not None
    state_dict = sess.live_state.to_snapshot_dict()
    pending_trans = state_dict.get("pending_transitions", list(state_dict.get("pending_commands", {}).values()))
    payload.setdefault("run_id", sess.run_id)
    payload["snapshot_version"] = sess.state_version
    payload.setdefault("state_version", sess.state_version)
    payload.setdefault("snapshot_id", sess.snapshot_id)
    payload["sim_time_s"] = sess.live_state.sim_time_s
    payload["connection_state"] = "LIVE"
    payload["source"] = "AUTHORITATIVE_GOVERNOR"
    payload["state"] = state_dict
    payload["budgets"] = budget_snap
    payload["pending_transitions"] = pending_trans
    payload["decision_log"] = sess.build_decision_log(replay_decisions)
    return payload


def _calculate_admit_next_window(
    cand_manifest: GuestManifest,
    trial_state: ShipOperationalState,
    aug_cat: Dict[str, GuestManifest],
    ship_profile: Dict[str, Any],
    repo_root: Path,
) -> Optional[Dict[str, Any]]:
    """If at sea and next_window is not provided, calculate next window using env_windows.find_next_window (§5.8.4)."""
    from . import envdata, env_windows
    try:
        snap = envdata.load_active_snapshot(repo_root)
    except (StrictValidationError, OSError, ValueError):
        return None
    op = "VEHICLE_REPLACEMENT_AT_SEA" if cand_manifest.guest_id in ("G3", "G4") else "AT_SEA_INSTALL"
    min_dur, _src = env_windows.operation_min_duration_s(cand_manifest, op)
    timeline = env_windows.availability_timeline(
        cand_manifest,
        op,
        trial_state,
        aug_cat,
        ship_profile,
        snap["series"],
        snap["anchor_utc"],
        guard=snap["guard_bands"],
    )
    nw = env_windows.find_next_window(timeline, min_duration_s=min_dur, now_sim_s=int(trial_state.sim_time_s))
    if nw is None:
        nw = env_windows.describe_no_window(
            [r for r in timeline if r["sim_time_s"] >= int(trial_state.sim_time_s)], min_duration_s=min_dur
        )
    return nw


def dispatch_api_request(
    repo_root: Path,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    session: Optional[GovernorSession] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Deterministic, stateful API dispatcher testable directly in unit/integration tests and used by ThreadingHTTPServer."""
    data_dir = repo_root / "data"
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    mission_envelope = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))
    builtin_profiles: Dict[str, PriorityProfile] = {
        "survey_primary": load_priority_profile(data_dir / "priorities" / "survey_primary.yaml"),
        "rescue_primary": load_priority_profile(data_dir / "priorities" / "rescue_primary.yaml"),
    }

    sess = session or get_or_create_session(repo_root)
    parsed_url = urlparse(path)
    route = parsed_url.path
    query = parse_qs(parsed_url.query)

    with sess.lock:
        profiles: Dict[str, PriorityProfile] = dict(builtin_profiles)
        profiles.update(sess.custom_profiles)

        try:
            if method == "POST" and route == "/api/reset":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(req, {"priority_profile_id", "scenario"}, "POST /api/reset")
                prof_id = require_str(req.get("priority_profile_id", "survey_primary"), "priority_profile_id")
                if prof_id not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                scen_file = require_str(req.get("scenario", "scenario_B_arbitration_degradation.yaml"), "scenario")
                sess.reset(prof_id, scen_file)
                assert sess.live_state is not None
                snap = sess._sync_current_executed_tick(ship_profile, "SESSION_RESET")
                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "runner_status": sess.runner_status,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "priority_profile_id": sess.priority_profile_id,
                    "active_priority_profile_sha256": profiles[sess.priority_profile_id].source_sha256,
                    "current_budget_snapshot": snap,
                    "action_history": sess.action_history,
                }
                return 200, _augment_canonical_envelope(resp, sess, snap)

            if method == "POST" and route == "/api/profile":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {
                        "priority_profile_id",
                        "scenario",
                        "priority_profile_data",
                        "priority_profile_yaml",
                        "crew_count",
                        "deck_clear",
                    },
                    "POST /api/profile",
                )
                custom_prof: Optional[PriorityProfile] = None
                if "priority_profile_yaml" in req and req["priority_profile_yaml"] is not None:
                    raw_yaml_str = require_str(req["priority_profile_yaml"], "priority_profile_yaml")
                    sha_hex = hashlib.sha256(raw_yaml_str.encode("utf-8")).hexdigest()
                    try:
                        parsed_yaml = yaml.safe_load(raw_yaml_str)
                    except Exception as exc:
                        raise StrictValidationError("YAML_PARSE_ERROR", str(exc), "priority_profile_yaml") from exc
                    raw_prof_dict = require_dict(parsed_yaml, "priority_profile_yaml")
                    custom_prof = parse_priority_profile(raw_prof_dict, source_sha256=sha_hex)
                    _validate_custom_profile_domain(custom_prof, sess.session_catalog, builtin_profiles)
                elif "priority_profile_data" in req and req["priority_profile_data"] is not None:
                    raw_prof_dict = require_dict(req["priority_profile_data"], "priority_profile_data")
                    canonical_bytes = json.dumps(raw_prof_dict, sort_keys=True).encode("utf-8")
                    sha_hex = hashlib.sha256(canonical_bytes).hexdigest()
                    custom_prof = parse_priority_profile(raw_prof_dict, source_sha256=sha_hex)
                    _validate_custom_profile_domain(custom_prof, sess.session_catalog, builtin_profiles)

                if custom_prof is not None:
                    prof_id = require_str(
                        req.get("priority_profile_id", custom_prof.priority_profile_id),
                        "priority_profile_id",
                    )
                    if prof_id != custom_prof.priority_profile_id:
                        raise StrictValidationError(
                            "PROFILE_ID_MISMATCH",
                            f"priority_profile_id '{prof_id}' does not match payload priority_profile_id '{custom_prof.priority_profile_id}'",
                            "priority_profile_id",
                        )
                else:
                    prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
                    if prof_id not in profiles:
                        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")

                scen_file = require_str(req.get("scenario", sess.scenario_file), "scenario")
                crew_cnt = require_int(req["crew_count"], "crew_count", min_value=0) if "crew_count" in req else None
                d_clear = require_bool(req["deck_clear"], "deck_clear") if "deck_clear" in req else None

                assert sess.live_state is not None
                crew_tags = _apply_crew_and_deck_updates(sess.live_state, crew_cnt, d_clear)
                if crew_tags:
                    sess.has_live_mutations = True

                if custom_prof is not None:
                    sess.custom_profiles[custom_prof.priority_profile_id] = custom_prof
                    profiles[custom_prof.priority_profile_id] = custom_prof

                sess.priority_profile_id = prof_id
                sess.scenario_file = scen_file
                sess.state_version += 1
                active_prof = profiles[prof_id]
                tag_suffix = f"+{','.join(crew_tags)}" if crew_tags else ""
                snap = sess._sync_current_executed_tick(ship_profile, f"PROFILE_SWITCHED:{prof_id}{tag_suffix}")
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"PROFILE_SWITCHED:{prof_id}{tag_suffix}",
                        "priority_profile_id": prof_id,
                        "priority_profile_sha256": active_prof.source_sha256,
                        "scenario_file": scen_file,
                        "run_id": sess.run_id,
                        "causal_sentence": (
                            f"Priority profile switched to '{prof_id}' (SHA-256={active_prof.source_sha256[:12]}...) "
                            f"with strategy {active_prof.survey_vs_recovery_strategy}."
                        ),
                    }
                )
                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "runner_status": sess.runner_status,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "priority_profile_id": sess.priority_profile_id,
                    "active_priority_profile_sha256": active_prof.source_sha256,
                    "active_priority_profile": _serialize_priority_profile(active_prof),
                    "available_priority_profiles": list(profiles.keys()),
                    "current_budget_snapshot": snap,
                    "action_history": sess.action_history,
                }
                return 200, _augment_canonical_envelope(resp, sess, snap)

            if method == "POST" and route == "/api/crew":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {"crew_count", "deck_clear", "priority_profile_id"},
                    "POST /api/crew",
                )
                if "priority_profile_id" in req:
                    prof_id = require_str(req["priority_profile_id"], "priority_profile_id")
                    if prof_id not in profiles:
                        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                    sess.priority_profile_id = prof_id

                crew_cnt = require_int(req["crew_count"], "crew_count", min_value=0) if "crew_count" in req else None
                d_clear = require_bool(req["deck_clear"], "deck_clear") if "deck_clear" in req else None
                assert sess.live_state is not None
                tags = _apply_crew_and_deck_updates(sess.live_state, crew_cnt, d_clear)
                sess.has_live_mutations = True
                sess.state_version += 1
                tag_label = ",".join(tags) if tags else "CREW_UPDATED"
                snap = sess._sync_current_executed_tick(ship_profile, tag_label)
                crew_comp = evaluate_crew_regimes_comparison(sess.session_catalog, sess.live_state)
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": tag_label,
                        "run_id": sess.run_id,
                        "causal_sentence": (
                            f"Crew regime set to {sess.live_state.crew_regime} "
                            f"(available_crew={sess.live_state.available_crew}, deck_clear={sess.live_state.deck_clear})."
                        ),
                    }
                )
                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "crew_regime": sess.live_state.crew_regime,
                    "available_crew": sess.live_state.available_crew,
                    "deck_clear": sess.live_state.deck_clear,
                    "scenario_C_crew_regimes": crew_comp,
                    "current_budget_snapshot": snap,
                    "action_history": sess.action_history,
                }
                return 200, _augment_canonical_envelope(resp, sess, snap)

            if method == "POST" and route in ("/api/advance", "/api/step", "/api/runner"):
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {
                        "target_sim_time_s",
                        "delta_sim_s",
                        "step_s",
                        "steps",
                        "runner_status",
                        "priority_profile_id",
                        "from_sim_time_s",
                        "expected_state_version",
                    },
                    f"POST {route}",
                )
                if "priority_profile_id" in req:
                    prof_id = require_str(req["priority_profile_id"], "priority_profile_id")
                    if prof_id not in profiles:
                        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                    sess.priority_profile_id = prof_id

                assert sess.live_state is not None
                cur_t = int(sess.live_state.sim_time_s)
                already_advanced = False
                if "expected_state_version" in req and req["expected_state_version"] is not None:
                    exp_ver = require_int(req["expected_state_version"], "expected_state_version", min_value=0)
                    if exp_ver < sess.state_version:
                        already_advanced = True
                if "from_sim_time_s" in req and req["from_sim_time_s"] is not None:
                    raw_from = req["from_sim_time_s"]
                    if isinstance(raw_from, float) and raw_from.is_integer():
                        raw_from = int(raw_from)
                    from_t = require_int(raw_from, "from_sim_time_s", min_value=0, max_value=1800)
                    if from_t < cur_t:
                        already_advanced = True

                if "target_sim_time_s" in req and req["target_sim_time_s"] is not None:
                    raw_target = req["target_sim_time_s"]
                    if isinstance(raw_target, float) and raw_target.is_integer():
                        raw_target = int(raw_target)
                    req_target = require_int(raw_target, "target_sim_time_s", min_value=0, max_value=1800)
                    if req_target <= cur_t and sess.executed_ticks:
                        already_advanced = True
                    target_t = max(cur_t, req_target)
                elif "delta_sim_s" in req and req["delta_sim_s"] is not None:
                    raw_delta = req["delta_sim_s"]
                    if isinstance(raw_delta, float) and raw_delta.is_integer():
                        raw_delta = int(raw_delta)
                    delta_s = require_int(raw_delta, "delta_sim_s", min_value=0, max_value=1800)
                    target_t = min(1800, cur_t + delta_s)
                elif "step_s" in req and req["step_s"] is not None:
                    raw_step = req["step_s"]
                    if isinstance(raw_step, float) and raw_step.is_integer():
                        raw_step = int(raw_step)
                    step_s = require_int(raw_step, "step_s", min_value=0, max_value=1800)
                    target_t = min(1800, cur_t + step_s)
                elif "steps" in req and req["steps"] is not None:
                    steps_n = require_int(req["steps"], "steps", min_value=0, max_value=90)
                    target_t = min(1800, cur_t + steps_n * 20)
                else:
                    target_t = min(1800, cur_t + 20)

                if not already_advanced and (target_t > cur_t or not sess.executed_ticks):
                    sess.advance_to(target_t, ship_profile, profiles[sess.priority_profile_id])
                if "runner_status" in req:
                    r_stat = require_str(req["runner_status"], "runner_status")
                    if r_stat not in ("PAUSED", "RUNNING", "COMPLETED"):
                        raise StrictValidationError("UNKNOWN_ENUM", f"Unknown runner_status '{r_stat}'", "runner_status")
                    sess.runner_status = r_stat

                snap = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
                snap["snapshot_id"] = sess.snapshot_id
                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "runner_status": sess.runner_status,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "idempotent_noop": already_advanced,
                    "priority_profile_id": sess.priority_profile_id,
                    "live_operational_state": sess.live_state.to_snapshot_dict(),
                    "current_budget_snapshot": snap,
                    "executed_ticks_count": len(sess.executed_ticks),
                    "action_history": sess.action_history,
                }
                return 200, _augment_canonical_envelope(resp, sess, snap)

            if method == "GET" and route == "/api/environment":
                res = sess.environment_view()
                return 200, res

            if method == "GET" and route == "/api/environment/windows":
                guest_id = query.get("guest", [None])[0]
                op_name = query.get("op", [None])[0]
                allowed_ops = ("LAUNCH_RECOVERY", "VEHICLE_REPLACEMENT_AT_SEA", "AT_SEA_INSTALL")
                if not guest_id or guest_id not in sess.session_catalog:
                    return 400, {
                        "error": "INVALID_PARAM",
                        "field": "guest",
                        "message": f"Guest '{guest_id}' not found in catalog",
                    }
                if not op_name or op_name not in allowed_ops:
                    return 400, {
                        "error": "INVALID_PARAM",
                        "field": "op",
                        "message": f"Operation '{op_name}' not in allowed operations {allowed_ops}",
                    }
                res = sess.windows_view(guest_id, op_name, ship_profile=ship_profile)
                return 200, res

            if method == "GET" and route == "/api/forecast":
                res = sess.forecast_view()
                return 200, res

            if method == "GET" and route == "/api/state":
                # Pure read: NEVER mutates state_version, priority_profile_id, scenario_file, or live_state (Phase S3, R8-F03)
                req_prof = query.get("priority_profile", [sess.priority_profile_id])[0]
                req_scen = query.get("scenario", [sess.scenario_file])[0]
                if req_prof not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile '{req_prof}'", "priority_profile")

                is_preview = (req_prof != sess.priority_profile_id) or (req_scen != sess.scenario_file)
                assert sess.live_state is not None
                eval_prof_obj = profiles[req_prof]
                active_prof_obj = profiles[sess.priority_profile_id]
                replay_res = sess.build_unified_replay_view(
                    ship_profile,
                    eval_prof_obj,
                    scenario_file_override=req_scen if req_scen != sess.scenario_file else None,
                )
                combos_res = evaluate_four_guest_combinations(
                    sess.session_catalog, ship_profile, mission_envelope, eval_prof_obj
                )
                current_budget = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
                current_budget["snapshot_id"] = sess.snapshot_id
                crew_comp = evaluate_crew_regimes_comparison(sess.session_catalog, sess.live_state)
                doc_info = run_doctor(repo_root, probe_remote=False)

                executed_ticks = [t for t in replay_res.get("timeline_ticks", []) if not t.get("is_prediction", False)]
                predicted_ticks = [t for t in replay_res.get("timeline_ticks", []) if t.get("is_prediction", False)]

                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "runner_status": sess.runner_status,
                    "is_preview": is_preview,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "current_sim_time_s": sess.live_state.sim_time_s,
                    "time_compression": {
                        "wall_clock_demo_s": 90,
                        "simulated_mission_s": 1800,
                        "acceleration_factor": 20.0,
                        "tick_interval_sim_s": 20,
                        "tick_interval_wall_ms": 1000,
                        "internal_substep_hz": 1.0,
                        "ui_gauge_publish_hz": 1.0,
                    },
                    "doctor": doc_info,
                    "priority_profile_id": sess.priority_profile_id,
                    "active_priority_profile_sha256": active_prof_obj.source_sha256,
                    "active_priority_profile": _serialize_priority_profile(active_prof_obj),
                    "evaluated_priority_profile_id": req_prof,
                    "evaluated_priority_profile_sha256": eval_prof_obj.source_sha256,
                    "available_priority_profiles": list(profiles.keys()),
                    "priority_profiles_detail": {
                        pid: _serialize_priority_profile(p_obj) for pid, p_obj in profiles.items()
                    },
                    "initial_budget_snapshot": current_budget,
                    "current_budget_snapshot": current_budget,
                    "live_operational_state": sess.live_state.to_snapshot_dict(),
                    "action_history": sess.action_history,
                    "admission_attempts": sess.admission_attempts,
                    "executed_timeline_ticks": executed_ticks,
                    "predicted_timeline_ticks": predicted_ticks,
                    "scenario_B_replay": replay_res,
                    "scenario_A_combinations": combos_res,
                    "scenario_C_crew_regimes": crew_comp,
                    "guest_manifests_raw": copy.deepcopy(sess.manifest_registry),
                }
                return 200, _augment_canonical_envelope(
                    resp, sess, current_budget, replay_decisions=replay_res.get("decisions", [])
                )

            if method == "POST" and route == "/api/admit_live":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {
                        "priority_profile_id",
                        "installed_guest_ids",
                        "installed_guests",
                        "at_port",
                        "allow_displacement",
                        "defer_if_temporary_operational_block",
                        "hangar_roof_clearance_verified",
                        "apply_if_admitted",
                        "apply_if_accepted",
                        "apply_yes_if",
                        "speed_kn",
                        "wind_kn",
                        "sea_state",
                        "seawater_temp_c",
                        "power_reserve_w",
                        "candidate_manifest",
                        "candidate_guest_id",
                        "overrides",
                        "next_window",
                    },
                    "POST /api/admit_live",
                )

                prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
                if prof_id not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                priority_profile = profiles[prof_id]

                at_port = require_bool(req.get("at_port", True), "at_port")
                allow_disp = require_bool(req.get("allow_displacement", True), "allow_displacement")
                defer_temp = require_bool(
                    req.get("defer_if_temporary_operational_block", True),
                    "defer_if_temporary_operational_block",
                )
                hangar_roof_verified = require_bool(
                    req.get("hangar_roof_clearance_verified", False),
                    "hangar_roof_clearance_verified",
                )
                raw_apply = req.get("apply_if_admitted", req.get("apply_if_accepted", True))
                apply_if_admitted = require_bool(raw_apply, "apply_if_admitted")
                apply_yes_if = require_bool(req.get("apply_yes_if", False), "apply_yes_if")

                assert sess.live_state is not None
                has_explicit_guest_list = ("installed_guest_ids" in req) or ("installed_guests" in req)
                if has_explicit_guest_list:
                    installed_ids = req.get("installed_guest_ids", req.get("installed_guests", []))
                    if not isinstance(installed_ids, list):
                        raise StrictValidationError("TYPE_ERROR", "installed_guest_ids must be a list of guest IDs", "installed_guest_ids")
                    for idx, gid in enumerate(installed_ids):
                        require_str(gid, f"installed_guest_ids[{idx}]")
                    base_state = ShipOperationalState(
                        tick_seq=sess.live_state.tick_seq,
                        sim_time_s=sess.live_state.sim_time_s,
                        vessel_mode="PORT_INTEGRATION" if at_port else "QUIET_SURVEY",
                        speed_kn=0.0 if at_port else require_finite_number(req.get("speed_kn", 6.0), "speed_kn", min_value=0.0),
                        heading_deg=0.0,
                        wind_deg=0.0,
                        wind_kn=require_int(req.get("wind_kn", 10), "wind_kn", min_value=0),
                        sea_state=require_int(req.get("sea_state", 2), "sea_state", min_value=0, max_value=9),
                        seawater_temp_c=require_finite_number(req.get("seawater_temp_c", 16.0), "seawater_temp_c", min_value=-5.0, max_value=60.0),
                        power_reserve_w=require_int(req.get("power_reserve_w", 200000), "power_reserve_w", min_value=0),
                        crew_regime=sess.live_state.crew_regime,
                        available_crew=sess.live_state.available_crew,
                        deck_clear=sess.live_state.deck_clear,
                    )
                    trial_state, baseline_conflicts = build_baseline_state_with_installed_guests(
                        installed_ids, base_state, ship_profile, sess.session_catalog
                    )
                else:
                    installed_ids = list(sess.live_state.installed_guests.keys())
                    baseline_conflicts = []
                    trial_state = sess.live_state.clone()
                    trial_state.vessel_mode = "PORT_INTEGRATION" if at_port else trial_state.vessel_mode
                    if at_port and "speed_kn" not in req:
                        trial_state.speed_kn = 0.0
                        trial_state.ship_speed_kn = 0.0
                    elif "speed_kn" in req:
                        spd_v = require_finite_number(req["speed_kn"], "speed_kn", min_value=0.0)
                        trial_state.speed_kn = spd_v
                        trial_state.ship_speed_kn = spd_v
                    if "wind_kn" in req:
                        trial_state.wind_kn = require_int(req["wind_kn"], "wind_kn", min_value=0)
                    if "sea_state" in req:
                        trial_state.sea_state = require_int(req["sea_state"], "sea_state", min_value=0, max_value=9)
                    if "seawater_temp_c" in req:
                        trial_state.seawater_temp_c = require_finite_number(req["seawater_temp_c"], "seawater_temp_c", min_value=-5.0, max_value=60.0)
                    if "power_reserve_w" in req:
                        trial_state.power_reserve_w = require_int(req["power_reserve_w"], "power_reserve_w", min_value=0)

                if "candidate_manifest" in req:
                    raw_cand = copy.deepcopy(require_dict(req["candidate_manifest"], "candidate_manifest"))
                else:
                    base_gid = require_str(req.get("candidate_guest_id", "G6"), "candidate_guest_id")
                    if base_gid in sess.manifest_registry:
                        raw_cand = copy.deepcopy(sess.manifest_registry[base_gid])
                    else:
                        matches = list((data_dir / "guests").glob(f"{base_gid}_*.yaml"))
                        if not matches:
                            raise StrictValidationError("UNKNOWN_GUEST", f"Unknown candidate_guest_id '{base_gid}'", "candidate_guest_id")
                        base_path = matches[0]
                        raw_cand = yaml.safe_load(base_path.read_text(encoding="utf-8"))

                    overrides = req.get("overrides", {})
                    if not isinstance(overrides, dict):
                        raise StrictValidationError("TYPE_ERROR", "overrides must be a JSON object", "overrides")
                    require_no_unknown_keys(
                        overrides,
                        {
                            "mass_kg",
                            "preferred_position",
                            "nominal_power_w",
                            "sustained_power_w",
                            "peak_power_w",
                            "cooling_water_w",
                            "cooling_air_mast_w",
                            "compute_cores",
                            "compute_ram_mib",
                            "compute_accelerators",
                            "satcom_bps",
                        },
                        "overrides",
                    )
                    if "mass_kg" in overrides and raw_cand.get("subcomponents"):
                        target_mass = require_int(
                            overrides["mass_kg"], "overrides.mass_kg", min_value=1, allow_zero=False
                        )
                        subs_list = raw_cand["subcomponents"]
                        if len(subs_list) == 1:
                            subs_list[0]["mass_kg"] = target_mass
                        else:
                            primary_idx = max(
                                range(len(subs_list)),
                                key=lambda i: int(subs_list[i].get("mass_kg", 0)),
                            )
                            other_mass = sum(
                                int(subs_list[i].get("mass_kg", 0))
                                for i in range(len(subs_list))
                                if i != primary_idx
                            )
                            if target_mass > other_mass:
                                new_primary_mass = target_mass - other_mass
                            else:
                                new_primary_mass = target_mass
                            subs_list[primary_idx]["mass_kg"] = new_primary_mass
                            prov_meta = subs_list[primary_idx].get("provenance_metadata")
                            if isinstance(prov_meta, dict):
                                prov_meta["derived_mass_kg"] = new_primary_mass
                                if isinstance(prov_meta.get("inputs"), dict) and isinstance(
                                    prov_meta["inputs"].get("total_mass_kg"), dict
                                ):
                                    prov_meta["inputs"]["total_mass_kg"]["value"] = new_primary_mass + other_mass

                    if "preferred_position" in overrides and raw_cand.get("subcomponents"):
                        pos = require_str(overrides["preferred_position"], "overrides.preferred_position")
                        subs_list = raw_cand["subcomponents"]
                        target_sc = subs_list[0]
                        if len(subs_list) > 1:
                            for sc_item in subs_list:
                                if pos in sc_item.get("allowed_positions", []):
                                    target_sc = sc_item
                                    break
                        target_sc["preferred_positions"] = [pos]
                        if pos not in target_sc["allowed_positions"]:
                            target_sc["allowed_positions"].append(pos)

                    def_m = raw_cand["default_mode"]
                    m_dict = raw_cand["modes"][def_m]

                    has_nom = "nominal_power_w" in overrides
                    has_sust = "sustained_power_w" in overrides
                    has_peak = "peak_power_w" in overrides
                    has_cw = "cooling_water_w" in overrides
                    has_ca = "cooling_air_mast_w" in overrides

                    if has_nom:
                        nom_w = require_int(overrides["nominal_power_w"], "overrides.nominal_power_w", min_value=0)
                        m_dict["nominal_power_w"] = nom_w
                        m_dict["min_power_w"] = min(int(m_dict["min_power_w"]), nom_w)
                        if not has_sust:
                            m_dict["sustained_power_w"] = max(nom_w, int(m_dict["sustained_power_w"]))
                        if not has_peak:
                            m_dict["peak_power_w"] = max(int(m_dict["sustained_power_w"]), int(m_dict["peak_power_w"]))

                    if has_sust:
                        sust_w = require_int(overrides["sustained_power_w"], "overrides.sustained_power_w", min_value=0)
                        m_dict["sustained_power_w"] = sust_w
                        if not has_nom:
                            m_dict["nominal_power_w"] = min(int(m_dict["nominal_power_w"]), sust_w)
                            m_dict["min_power_w"] = min(int(m_dict["min_power_w"]), int(m_dict["nominal_power_w"]))
                        if not has_peak:
                            m_dict["peak_power_w"] = max(sust_w, int(m_dict["peak_power_w"]))

                    if has_peak:
                        peak_w = require_int(overrides["peak_power_w"], "overrides.peak_power_w", min_value=0)
                        m_dict["peak_power_w"] = peak_w
                        if not has_sust and not has_nom:
                            m_dict["sustained_power_w"] = min(int(m_dict["sustained_power_w"]), peak_w)
                            m_dict["nominal_power_w"] = min(int(m_dict["nominal_power_w"]), int(m_dict["sustained_power_w"]))
                            m_dict["min_power_w"] = min(int(m_dict["min_power_w"]), int(m_dict["nominal_power_w"]))

                    if has_cw:
                        m_dict["cooling_water_w"] = require_int(
                            overrides["cooling_water_w"], "overrides.cooling_water_w", min_value=0
                        )
                    if has_ca:
                        m_dict["cooling_air_mast_w"] = require_int(
                            overrides["cooling_air_mast_w"], "overrides.cooling_air_mast_w", min_value=0
                        )

                    if (has_nom or has_sust) and not (has_cw or has_ca):
                        sust_final = int(m_dict["sustained_power_w"])
                        if int(m_dict["cooling_air_mast_w"]) == 0:
                            m_dict["cooling_water_w"] = sust_final
                        else:
                            air_w = min(2000, int(m_dict["cooling_air_mast_w"]), sust_final)
                            m_dict["cooling_air_mast_w"] = air_w
                            m_dict["cooling_water_w"] = sust_final - air_w
                    elif (has_cw or has_ca) and not (has_nom or has_sust):
                        cool_sum = int(m_dict["cooling_water_w"]) + int(m_dict["cooling_air_mast_w"])
                        m_dict["sustained_power_w"] = cool_sum
                        m_dict["nominal_power_w"] = min(int(m_dict["nominal_power_w"]), cool_sum)
                        m_dict["min_power_w"] = min(int(m_dict["min_power_w"]), int(m_dict["nominal_power_w"]))
                        if not has_peak:
                            m_dict["peak_power_w"] = max(int(m_dict["peak_power_w"]), cool_sum)

                    if "compute_cores" in overrides:
                        m_dict["compute_cores"] = require_int(
                            overrides["compute_cores"], "overrides.compute_cores", min_value=0
                        )
                    if "compute_ram_mib" in overrides:
                        m_dict["compute_ram_mib"] = require_int(
                            overrides["compute_ram_mib"], "overrides.compute_ram_mib", min_value=0
                        )
                    if "compute_accelerators" in overrides:
                        m_dict["compute_accelerators"] = require_int(
                            overrides["compute_accelerators"], "overrides.compute_accelerators", min_value=0
                        )
                    if "satcom_bps" in overrides:
                        m_dict["satcom_bps"] = require_int(
                            overrides["satcom_bps"], "overrides.satcom_bps", min_value=0
                        )

                    if any([has_nom, has_sust, has_peak, has_cw, has_ca]):
                        _resync_component_allocations(raw_cand, def_m, m_dict)

                cand_manifest = validate_and_build_guest_manifest(raw_cand)
                aug_cat = dict(sess.session_catalog)
                aug_cat[cand_manifest.guest_id] = cand_manifest

                req_next_window = req.get("next_window", None)
                if req_next_window is None and not at_port and defer_temp:
                    req_next_window = _calculate_admit_next_window(
                        cand_manifest, trial_state, aug_cat, ship_profile, repo_root
                    )

                decision = evaluate_admission(
                    cand_manifest,
                    trial_state,
                    ship_profile,
                    mission_envelope,
                    priority_profile,
                    aug_cat,
                    at_port=at_port,
                    allow_displacement=allow_disp,
                    defer_if_temporary_operational_block=defer_temp,
                    next_window=req_next_window,
                    hangar_roof_clearance_verified=hangar_roof_verified,
                )

                verdict = decision["verdict"]
                binding_w = decision.get("binding_constraints") or decision.get("binding_witnesses") or []
                conds = decision.get("conditions", [])
                decision["causal_sentence"] = _format_witness_causal_sentence(
                    f"Admission of {cand_manifest.guest_id} ({cand_manifest.total_mass_kg} kg) -> {verdict}",
                    binding_w,
                    fallback_reason="; ".join(conds) if conds else f"All 8 resource classes feasible across A.10 envelope",
                )

                app_res = apply_admission_decision_atomically(
                    decision,
                    cand_manifest,
                    trial_state,
                    ship_profile,
                    mission_envelope,
                    sess.session_catalog,
                    at_port=at_port,
                    apply_if_admitted=apply_if_admitted,
                    apply_yes_if=apply_yes_if,
                    hangar_roof_clearance_verified=hangar_roof_verified,
                )
                should_apply = bool(app_res["applied"])

                if should_apply:
                    # Atomic commit of catalog, manifest registry, live state, and state_version (Phase S2, R8-F02, S6-I02)
                    sess.session_catalog[cand_manifest.guest_id] = cand_manifest
                    sess.manifest_registry[cand_manifest.guest_id] = raw_cand
                    sess.live_state = app_res["state"]
                    sess.state_version += 1
                    sess.has_live_mutations = True
                    updated_snap = sess._sync_current_executed_tick(
                        ship_profile, f"ADMIT_LIVE_APPLIED:{cand_manifest.guest_id}"
                    )
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"ADMIT_LIVE:{cand_manifest.guest_id}->{verdict}",
                            "applied": True,
                            "applied_plan": app_res["applied_plan"],
                            "causal_sentence": decision["causal_sentence"],
                        }
                    )
                else:
                    # Non-mutating rejection / deferral / unapplied YES_IF: preserve active state & catalog intact (Phase S2, R8-F02, S6-I02)
                    updated_snap = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
                    updated_snap["snapshot_id"] = sess.snapshot_id
                    sess.admission_attempts.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "candidate_guest_id": cand_manifest.guest_id,
                            "candidate_total_mass_kg": cand_manifest.total_mass_kg,
                            "verdict": verdict,
                            "applied": False,
                            "reason": app_res["reason"],
                            "causal_sentence": decision["causal_sentence"],
                        }
                    )

                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "applied": should_apply,
                    "application_reason": app_res["reason"],
                    "applied_plan": app_res["applied_plan"],
                    "displaced_guest_ids": app_res["displaced_guest_ids"],
                    "candidate_guest_id": cand_manifest.guest_id,
                    "candidate_total_mass_kg": cand_manifest.total_mass_kg,
                    "installed_before": list(installed_ids),
                    "installed_after": list(sess.live_state.installed_guests.keys()),
                    "baseline_conflicts": baseline_conflicts,
                    "decision": decision,
                    "causal_sentence": decision["causal_sentence"],
                    "current_budget_snapshot": updated_snap,
                }
                return 200, _augment_canonical_envelope(resp, sess, updated_snap)

            if method == "POST" and route == "/api/perturb":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {
                        "perturbation_type",
                        "priority_profile_id",
                        "guest_id",
                        "measured_power_w",
                        "feed_name",
                        "age_ms",
                        "new_power_reserve_w",
                        "seawater_temp_c",
                        "sim_time_s",
                        "crew_count",
                        "deck_clear",
                        "value",
                    },
                    "POST /api/perturb",
                )
                perturbation = require_str(req.get("perturbation_type", "GENERATOR_LOSS_100KW"), "perturbation_type")
                prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
                if prof_id not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                priority_profile = profiles[prof_id]
                sess.priority_profile_id = prof_id

                assert sess.live_state is not None
                if "sim_time_s" in req:
                    req_sim_t = require_int(req["sim_time_s"], "sim_time_s", min_value=sess.live_state.sim_time_s, max_value=1800)
                    if req_sim_t > sess.live_state.sim_time_s:
                        sess.advance_to(req_sim_t, ship_profile, priority_profile)

                if perturbation == "SET_CREW_REGIME":
                    crew_cnt = require_int(req.get("crew_count", 14), "crew_count", min_value=0)
                    d_clear = require_bool(req["deck_clear"], "deck_clear") if "deck_clear" in req else None
                    tags = _apply_crew_and_deck_updates(sess.live_state, crew_cnt, d_clear)
                    sess.has_live_mutations = True
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"LIVE_PERTURBATION_{','.join(tags)}@{sess.live_state.sim_time_s}s",
                    )
                    c_sent = (
                        f"Crew regime changed to {sess.live_state.crew_regime} "
                        f"(available_crew={sess.live_state.available_crew}, deck_clear={sess.live_state.deck_clear})."
                    )
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}:{sess.live_state.crew_regime}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": {
                            "status": "CREW_REGIME_UPDATED",
                            "crew_regime": sess.live_state.crew_regime,
                            "available_crew": sess.live_state.available_crew,
                            "deck_clear": sess.live_state.deck_clear,
                            "causal_sentence": c_sent,
                            "budget_snapshot": snap_after,
                        },
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                if perturbation in ACOUSTIC_PERTURBATIONS:
                    if perturbation == "WATER_COLUMN_LAYERED":
                        if "value" not in req:
                            raise StrictValidationError("MISSING_FIELD", "WATER_COLUMN_LAYERED requires boolean 'value'", "value")
                        layered = require_bool(req["value"], "value")
                        sess.live_state.water_column_layered = layered
                        sess.live_state.water_column_layered_provenance = "OPERATOR_INPUT" if layered else "ASSUMED"
                        tag = f"WATER_COLUMN_LAYERED_{'ON' if layered else 'OFF'}"
                    elif "value" in req:
                        raise StrictValidationError("UNKNOWN_FIELD", f"'value' is not accepted for {perturbation}", "value")
                    else:
                        sess.live_state.acoustic_link_forced_down = perturbation == "ACOUSTIC_LINK_LOSS"
                        tag = perturbation
                    sess.has_live_mutations = True
                    sess.state_version += 1
                    link_status = evaluate_acoustic_link(sess.live_state, sess.session_catalog)
                    replan = _replan_d3_after_link_change(sess, ship_profile, priority_profile)
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile, f"LIVE_PERTURBATION_{tag}@{sess.live_state.sim_time_s}s"
                    )
                    c_sent = (
                        f"Acoustic link {'available' if link_status.available else 'UNAVAILABLE'}"
                        + (f" ({', '.join(link_status.causes)})" if link_status.causes else "")
                        + (
                            f"; G3 hold re-planned (plan v{sess.active_plan_version}): "
                            f"{replan['arbitration'].get('rationale')}"
                            if replan is not None
                            else "."
                        )
                    )
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    result = {
                        "status": "ACOUSTIC_LINK_UPDATED",
                        "acoustic_link_status": link_status.to_dict(),
                        "water_column_layered": sess.live_state.water_column_layered,
                        "replanned": replan is not None,
                        "replan_elapsed_ms": replan["elapsed_ms"] if replan is not None else None,
                        "arbitration": replan["arbitration"] if replan is not None else None,
                        "causal_sentence": c_sent,
                        "budget_snapshot": snap_after,
                    }
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": result,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                state = sess.live_state.clone()

                if perturbation == "GENERATOR_LOSS_100KW":
                    state.power_reserve_w = require_int(req.get("new_power_reserve_w", 100000), "new_power_reserve_w", min_value=0)
                    state.vessel_mode = "DEGRADED_POWER"
                    if state.power_reserve_w < 35000 and "TOUCHDOWN" not in sess.fired_d3_milestones:
                        sess.fired_d3_milestones.add("PLAN_INVALIDATED_INSUFFICIENT_POWER")
                        state.firm_reserved_loads = [
                            fl for fl in state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
                        ]
                        sess.executed_touchdown_time_s = None
                        if sess.active_arb_decision is not None:
                            sess.active_arb_decision["executed_touchdown_time_s"] = None
                            sess.active_arb_decision["plan_status"] = "INVALIDATED_INSUFFICIENT_POWER"
                    res = execute_graceful_degradation(state, ship_profile, priority_profile, sess.session_catalog)
                    sess.live_state = res["resulting_state"]
                    sess.has_live_mutations = True
                    sess.superseded_event_types.add("GENERATOR_LOSS")
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"LIVE_PERTURBATION_GENERATOR_LOSS_TO_{state.power_reserve_w // 1000}KW@{sess.live_state.sim_time_s}s",
                    )
                    res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                    res_clean["budget_snapshot"] = snap_after
                    acts = res_clean.get("actions_taken", [])
                    c_sent = (
                        f"Generator loss reduced power reserve to {state.power_reserve_w} W: "
                        + (
                            ", ".join(
                                f"{a.get('action')} {a.get('guest_id')} ({a.get('from_mode')}->{a.get('to_mode')})"
                                for a in acts
                            )
                            if acts
                            else f"envelope status {res_clean.get('status', 'OK')}"
                        )
                        + "."
                    )
                    res_clean["causal_sentence"] = c_sent
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": res_clean,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                if perturbation == "COOLING_LOSS_30C_40PCT":
                    state.seawater_temp_c = require_finite_number(req.get("seawater_temp_c", 30.0), "seawater_temp_c", min_value=-5.0, max_value=60.0)
                    state.cooling_loop_degraded = True
                    state.vessel_mode = "DEGRADED_THERMAL"
                    res = execute_graceful_degradation(state, ship_profile, priority_profile, sess.session_catalog)
                    sess.live_state = res["resulting_state"]
                    sess.has_live_mutations = True
                    sess.superseded_event_types.add("COOLING_LOOP_FAILURE")
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"LIVE_PERTURBATION_COOLING_LOSS@{sess.live_state.sim_time_s}s",
                    )
                    res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                    res_clean["budget_snapshot"] = snap_after
                    acts = res_clean.get("actions_taken", [])
                    c_sent = (
                        f"Cooling loop failure at {state.seawater_temp_c}C derated chilled-water capacity to {snap_after['classes']['cooling']['degraded_capacity']} W: "
                        + (
                            ", ".join(
                                f"{a.get('action')} {a.get('guest_id')} ({a.get('from_mode')}->{a.get('to_mode')})"
                                for a in acts
                            )
                            if acts
                            else f"envelope status {res_clean.get('status', 'OK')}"
                        )
                        + "."
                    )
                    res_clean["causal_sentence"] = c_sent
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": res_clean,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                if perturbation == "GUEST_OVERCONSUMPTION_20PCT":
                    gid = require_str(req.get("guest_id", "G5"), "guest_id")
                    if gid not in sess.session_catalog or gid not in state.installed_guests:
                        raise StrictValidationError("UNKNOWN_GUEST", f"Guest '{gid}' not installed", "guest_id")
                    measured_w = require_int(req.get("measured_power_w", 54000), "measured_power_w", min_value=1)
                    res = handle_guest_overconsumption(
                        state, ship_profile, priority_profile, sess.session_catalog,
                        offending_guest_id=gid, measured_power_w=measured_w
                    )
                    sess.live_state = res["resulting_state"]
                    sess.has_live_mutations = True
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"LIVE_PERTURBATION_OVERCONSUMPTION_{gid}@{sess.live_state.sim_time_s}s",
                    )
                    res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                    res_clean["budget_snapshot"] = snap_after
                    c_sent = f"Guest {gid} overconsumption ({measured_w} W measured) contained via {res_clean.get('action_taken', 'STEP_DOWN_OR_THROTTLE')}."
                    res_clean["causal_sentence"] = c_sent
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}:{gid}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": res_clean,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                if perturbation == "LOST_SEAWATER_TEMP_FEED":
                    fname = require_str(req.get("feed_name", "seawater_temp_c"), "feed_name")
                    age_ms = require_int(req.get("age_ms", 60000), "age_ms", min_value=0)
                    res = handle_lost_or_stale_feed(
                        state, ship_profile, sess.session_catalog, feed_name=fname, age_ms=age_ms
                    )
                    sess.live_state = res["resulting_state"]
                    sess.has_live_mutations = True
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"LIVE_PERTURBATION_LOST_FEED_{fname}@{sess.live_state.sim_time_s}s",
                    )
                    res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                    res_clean["budget_snapshot"] = snap_after
                    c_sent = (
                        f"Telemetry feed '{fname}' stale ({age_ms} ms > 5000 ms): conservative fallback "
                        f"applied ({res_clean.get('fallback_action', 'CONSERVATIVE_FALLBACK')})."
                    )
                    res_clean["causal_sentence"] = c_sent
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"PERTURB:{perturbation}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": res_clean,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                if perturbation in ("URGENT_STOP", "URGENT_HOLD", "URGENT_SECURE"):
                    cmd = perturbation.split("_", 1)[1]
                    gid = require_str(req.get("guest_id", "G3"), "guest_id")
                    if gid not in sess.session_catalog or gid not in state.installed_guests:
                        raise StrictValidationError("UNKNOWN_GUEST", f"Guest '{gid}' not installed", "guest_id")
                    res = issue_urgent_command(cmd, gid, state, sess.session_catalog)
                    sess.live_state = res["resulting_state"]
                    if gid == "G4" and not sess.live_state.installed_guests["G4"].powered_on:
                        sess.fired_d3_milestones.add("PLAN_INVALIDATED_G4_UNAVAILABLE")
                        sess.live_state.firm_reserved_loads = [
                            fl for fl in sess.live_state.firm_reserved_loads if not fl.reservation_id.startswith("D3_PLAN_")
                        ]
                        if "TOUCHDOWN" not in sess.fired_d3_milestones:
                            sess.executed_touchdown_time_s = None
                            if sess.active_arb_decision is not None:
                                sess.active_arb_decision["executed_touchdown_time_s"] = None
                                sess.active_arb_decision["plan_status"] = "INVALIDATED_G4_STOPPED_OR_UNAVAILABLE"
                    sess.has_live_mutations = True
                    sess.state_version += 1
                    snap_after = sess._sync_current_executed_tick(
                        ship_profile,
                        f"URGENT_{cmd}_{gid}@{sess.live_state.sim_time_s}s",
                    )
                    res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                    res_clean["budget_snapshot"] = snap_after
                    c_sent = (
                        f"Urgent {cmd} issued to {gid}: {res_clean.get('status', 'EXECUTED')} "
                        f"in {res_clean.get('measured_latency_ms', 0.0)} ms (<= 200 ms hard ceiling)."
                    )
                    res_clean["causal_sentence"] = c_sent
                    sess.action_history.append(
                        {
                            "state_version": sess.state_version,
                            "snapshot_id": sess.snapshot_id,
                            "sim_time_s": sess.live_state.sim_time_s,
                            "action": f"URGENT:{cmd}:{gid}",
                            "run_id": sess.run_id,
                            "causal_sentence": c_sent,
                        }
                    )
                    resp = {
                        "status": "OK",
                        "run_id": sess.run_id,
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "perturbation": perturbation,
                        "current_budget_snapshot": snap_after,
                        "result": res_clean,
                    }
                    return 200, _augment_canonical_envelope(resp, sess, snap_after)

                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown perturbation_type '{perturbation}'", "perturbation_type")

            if method == "POST" and route == "/api/arbitrate":
                req = require_dict(body if body is not None else {}, "request_body")
                require_no_unknown_keys(
                    req,
                    {
                        "priority_profile_id",
                        "uav_battery_pct",
                        "uav_max_endurance_s",
                        "uav_approach_margin_s",
                        "remaining_survey_waypoint_s",
                        "wind_kn",
                        "sea_state",
                    },
                    "POST /api/arbitrate",
                )
                prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
                if prof_id not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                sess.priority_profile_id = prof_id
                assert sess.live_state is not None
                state = sess.live_state.clone()
                if "wind_kn" in req:
                    state.wind_kn = require_int(req["wind_kn"], "wind_kn", min_value=0)
                if "sea_state" in req:
                    state.sea_state = require_int(req["sea_state"], "sea_state", min_value=0, max_value=9)
                bat_pct = require_finite_number(req.get("uav_battery_pct", 18.0), "uav_battery_pct", min_value=0.0, max_value=100.0)
                max_end_s = require_int(req.get("uav_max_endurance_s", 720), "uav_max_endurance_s", min_value=1)
                app_m_s = require_int(req.get("uav_approach_margin_s", 360), "uav_approach_margin_s", min_value=0)
                rem_wp_s = require_int(req.get("remaining_survey_waypoint_s", 240), "remaining_survey_waypoint_s", min_value=0)

                arb = arbitrate_live_conflict(
                    state,
                    ship_profile,
                    profiles[prof_id],
                    sess.session_catalog,
                    uav_battery_pct=bat_pct,
                    uav_max_endurance_s=max_end_s,
                    uav_approach_margin_s=app_m_s,
                    remaining_survey_waypoint_s=rem_wp_s,
                )
                c_sent = arb.get("rationale") or (
                    f"Arbitration under '{prof_id}' -> {arb.get('verdict')}: "
                    f"G3 HOLD commanded at t={arb.get('hold_cmd_time_s')}s, G4 touchdown at t={arb.get('touchdown_time_s')}s."
                )
                arb["causal_sentence"] = c_sent
                sess.state_version += 1
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"ARBITRATE:{prof_id}->{arb['verdict']}",
                        "run_id": sess.run_id,
                        "causal_sentence": c_sent,
                    }
                )
                snap_now = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
                snap_now["snapshot_id"] = sess.snapshot_id
                resp = {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "arbitration": arb,
                    "causal_sentence": c_sent,
                }
                return 200, _augment_canonical_envelope(resp, sess, snap_now)

            return 404, {"status": "ERROR", "message": f"Unknown API endpoint {method} {route}"}

        except StrictValidationError as exc:
            return 422, {
                "status": "VALIDATION_ERROR",
                "error_code": exc.code,
                "field_path": exc.field_path,
                "message": str(exc),
            }
        except Exception as exc:
            return 500, {"status": "INTERNAL_ERROR", "message": str(exc)}


def _resolve_static_dir(repo_root: Path, subdir: str) -> Path:
    candidate = (repo_root / subdir).resolve()
    if candidate.exists():
        return candidate
    bundled = (Path(__file__).resolve().parent / "_bundled" / subdir).resolve()
    if bundled.exists():
        return bundled
    return candidate


def _guess_mime_type(file_path: Path) -> str:
    if file_path.suffix in (".js", ".mjs"):
        return "application/javascript; charset=utf-8"
    if file_path.suffix == ".css":
        return "text/css; charset=utf-8"
    if file_path.suffix == ".html":
        return "text/html; charset=utf-8"
    if file_path.suffix == ".json":
        return "application/json; charset=utf-8"
    if file_path.suffix in (".yaml", ".yml"):
        return "text/yaml; charset=utf-8"
    if file_path.suffix == ".glb":
        return "model/gltf-binary"
    if file_path.suffix == ".svg":
        return "image/svg+xml"
    ctype, _ = mimetypes.guess_type(str(file_path))
    return ctype or "application/octet-stream"


def serve_operator_ui(repo_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local HTTP server for the single-screen SDV Resource Governor operator console and /viz3d viewer."""
    ui_dir = _resolve_static_dir(repo_root, "ui")
    viz3d_dir = _resolve_static_dir(repo_root, "viz3d")
    sess = get_or_create_session(repo_root)

    class GovernorRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_cors_and_source_headers(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("X-SDV-Source", "AUTHORITATIVE_GOVERNOR")
            self.send_header("X-SDV-Bridge-Mode", "LIVE_8765")

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self._send_cors_and_source_headers()
            self.end_headers()

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(code)
            self._send_cors_and_source_headers()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body_bytes)))
            self.end_headers()
            self.wfile.write(body_bytes)

        def do_GET(self) -> None:
            if self.path.startswith("/api/"):
                code, payload = dispatch_api_request(repo_root, "GET", self.path, session=sess)
                self._send_json(code, payload)
                return

            clean_path = urlparse(self.path).path
            if clean_path == "/viz3d":
                self.send_response(302)
                self._send_cors_and_source_headers()
                self.send_header("Location", "/viz3d/")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if clean_path in ("/", "/index.html"):
                target = ui_dir / "index.html"
            elif clean_path == "/app.js":
                target = ui_dir / "app.js"
            elif clean_path == "/styles.css":
                target = ui_dir / "styles.css"
            elif clean_path in ("/viz3d", "/viz3d/", "/viz3d/index.html"):
                target = viz3d_dir / "index.html"
            elif clean_path == "/viz3d/standalone.html":
                target = viz3d_dir / "standalone.html"
            elif clean_path.startswith("/viz3d/"):
                rel = clean_path[len("/viz3d/") :].lstrip("/")
                target = (viz3d_dir / rel).resolve()
                if not str(target).startswith(str(viz3d_dir)):
                    self._send_json(403, {"status": "FORBIDDEN", "path": clean_path})
                    return
            else:
                self._send_json(404, {"status": "NOT_FOUND", "path": clean_path})
                return

            if not target.exists() or not target.is_file():
                self._send_json(404, {"status": "MISSING_UI_ASSET", "file": target.name})
                return

            data = target.read_bytes()
            ctype = _guess_mime_type(target)
            self.send_response(200)
            self._send_cors_and_source_headers()
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
            try:
                body_dict = json.loads(raw_body) if raw_body.strip() else {}
            except Exception as exc:
                self._send_json(400, {"status": "INVALID_JSON", "message": str(exc)})
                return

            code, payload = dispatch_api_request(repo_root, "POST", self.path, body_dict, session=sess)
            self._send_json(code, payload)

    httpd = ThreadingHTTPServer((host, port), GovernorRequestHandler)
    print(f"SDV Resource Governor UI serving on http://{host}:{port} (3D viewer at http://{host}:{port}/viz3d)")
    httpd.serve_forever()

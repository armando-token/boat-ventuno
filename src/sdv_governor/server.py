"""Stateful HTTP JSON API and static UI server for the single-screen SDV Resource Governor console (B10, DEM-01, DEM-02, H12, H13, R2, R5)."""

from __future__ import annotations

import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import yaml

from .admission import (
    apply_admission_decision_atomically,
    build_baseline_state_with_installed_guests,
    evaluate_admission,
    evaluate_four_guest_combinations,
)
from .arbitration import arbitrate_live_conflict
from .budgets import evaluate_eight_class_budgets
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
    validate_and_build_guest_manifest,
    validate_mission_envelope,
    validate_ship_profile,
)
from .models import (
    GuestManifest,
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
from .scheduling import evaluate_crew_regimes_comparison, handle_lost_or_stale_feed
from .ship_state import InstalledGuestState, ShipOperationalState


class GovernorSession:
    """Persistent operational session state, guest catalog, and manifest registry across API calls (H12, R2, R5, R8-F02, R8-F03)."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.state_version: int = 0
        self.priority_profile_id: str = "survey_primary"
        self.scenario_file: str = "scenario_B_arbitration_degradation.yaml"
        self.live_state: Optional[ShipOperationalState] = None
        self.session_catalog: Dict[str, GuestManifest] = {}
        self.manifest_registry: Dict[str, Dict[str, Any]] = {}
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


_SESSIONS_BY_ROOT: Dict[str, GovernorSession] = {}


def get_or_create_session(repo_root: Path) -> GovernorSession:
    key = str(repo_root.resolve())
    if key not in _SESSIONS_BY_ROOT:
        _SESSIONS_BY_ROOT[key] = GovernorSession(repo_root)
    return _SESSIONS_BY_ROOT[key]


def reset_session(repo_root: Path) -> GovernorSession:
    key = str(repo_root.resolve())
    _SESSIONS_BY_ROOT[key] = GovernorSession(repo_root)
    return _SESSIONS_BY_ROOT[key]


def dispatch_api_request(
    repo_root: Path,
    method: str,
    path: str,
    body: Optional[Dict[str, Any]] = None,
    *,
    session: Optional[GovernorSession] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Deterministic, stateful API dispatcher testable directly in unit/integration tests and used by HTTPServer."""
    data_dir = repo_root / "data"
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    mission_envelope = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))
    profiles = {
        "survey_primary": load_priority_profile(data_dir / "priorities" / "survey_primary.yaml"),
        "rescue_primary": load_priority_profile(data_dir / "priorities" / "rescue_primary.yaml"),
    }

    sess = session or get_or_create_session(repo_root)
    parsed_url = urlparse(path)
    route = parsed_url.path
    query = parse_qs(parsed_url.query)

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
            return 200, {
                "status": "OK",
                "run_id": sess.run_id,
                "runner_status": sess.runner_status,
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "priority_profile_id": sess.priority_profile_id,
                "current_budget_snapshot": snap,
                "action_history": sess.action_history,
            }

        if method == "POST" and route == "/api/profile":
            req = require_dict(body if body is not None else {}, "request_body")
            require_no_unknown_keys(req, {"priority_profile_id", "scenario"}, "POST /api/profile")
            prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
            if prof_id not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
            scen_file = require_str(req.get("scenario", sess.scenario_file), "scenario")
            sess.priority_profile_id = prof_id
            sess.scenario_file = scen_file
            sess.state_version += 1
            assert sess.live_state is not None
            snap = sess._sync_current_executed_tick(ship_profile, f"PROFILE_SWITCHED:{prof_id}")
            sess.action_history.append(
                {
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "action": f"PROFILE_SWITCHED:{prof_id}",
                    "priority_profile_id": prof_id,
                    "scenario_file": scen_file,
                    "run_id": sess.run_id,
                }
            )
            return 200, {
                "status": "OK",
                "run_id": sess.run_id,
                "runner_status": sess.runner_status,
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "priority_profile_id": sess.priority_profile_id,
                "current_budget_snapshot": snap,
                "action_history": sess.action_history,
            }

        if method == "POST" and route in ("/api/advance", "/api/step", "/api/runner"):
            req = require_dict(body if body is not None else {}, "request_body")
            require_no_unknown_keys(
                req,
                {"target_sim_time_s", "delta_sim_s", "steps", "runner_status", "priority_profile_id"},
                f"POST {route}",
            )
            if "priority_profile_id" in req:
                prof_id = require_str(req["priority_profile_id"], "priority_profile_id")
                if prof_id not in profiles:
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
                sess.priority_profile_id = prof_id
            assert sess.live_state is not None
            cur_t = int(sess.live_state.sim_time_s)
            if "target_sim_time_s" in req:
                target_t = require_int(req["target_sim_time_s"], "target_sim_time_s", min_value=cur_t, max_value=1800)
            elif "delta_sim_s" in req:
                delta_s = require_int(req["delta_sim_s"], "delta_sim_s", min_value=0, max_value=1800)
                target_t = min(1800, cur_t + delta_s)
            elif "steps" in req:
                steps_n = require_int(req["steps"], "steps", min_value=0, max_value=90)
                target_t = min(1800, cur_t + steps_n * 20)
            else:
                target_t = min(1800, cur_t + 20)

            if target_t > cur_t or not sess.executed_ticks:
                sess.advance_to(target_t, ship_profile, profiles[sess.priority_profile_id])
            if "runner_status" in req:
                r_stat = require_str(req["runner_status"], "runner_status")
                if r_stat not in ("PAUSED", "RUNNING", "COMPLETED"):
                    raise StrictValidationError("UNKNOWN_ENUM", f"Unknown runner_status '{r_stat}'", "runner_status")
                sess.runner_status = r_stat

            snap = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
            snap["snapshot_id"] = sess.snapshot_id
            return 200, {
                "status": "OK",
                "run_id": sess.run_id,
                "runner_status": sess.runner_status,
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "sim_time_s": sess.live_state.sim_time_s,
                "priority_profile_id": sess.priority_profile_id,
                "live_operational_state": sess.live_state.to_snapshot_dict(),
                "current_budget_snapshot": snap,
                "executed_ticks_count": len(sess.executed_ticks),
                "action_history": sess.action_history,
            }

        if method == "GET" and route == "/api/state":
            # Pure read: NEVER mutates state_version, priority_profile_id, scenario_file, or live_state (Phase S3, R8-F03)
            req_prof = query.get("priority_profile", [sess.priority_profile_id])[0]
            req_scen = query.get("scenario", [sess.scenario_file])[0]
            if req_prof not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile '{req_prof}'", "priority_profile")

            is_preview = (req_prof != sess.priority_profile_id) or (req_scen != sess.scenario_file)
            assert sess.live_state is not None
            replay_res = sess.build_unified_replay_view(
                ship_profile,
                profiles[req_prof],
                scenario_file_override=req_scen if req_scen != sess.scenario_file else None,
            )
            combos_res = evaluate_four_guest_combinations(
                sess.session_catalog, ship_profile, mission_envelope, profiles[req_prof]
            )
            current_budget = evaluate_eight_class_budgets(ship_profile, sess.session_catalog, sess.live_state)
            current_budget["snapshot_id"] = sess.snapshot_id
            crew_comp = evaluate_crew_regimes_comparison(sess.session_catalog, sess.live_state)
            doc_info = run_doctor(repo_root, probe_remote=False)

            executed_ticks = [t for t in replay_res.get("timeline_ticks", []) if not t.get("is_prediction", False)]
            predicted_ticks = [t for t in replay_res.get("timeline_ticks", []) if t.get("is_prediction", False)]

            return 200, {
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
                "evaluated_priority_profile_id": req_prof,
                "available_priority_profiles": list(profiles.keys()),
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
                    },
                    "overrides",
                )
                if "mass_kg" in overrides and raw_cand.get("subcomponents"):
                    raw_cand["subcomponents"][0]["mass_kg"] = require_int(
                        overrides["mass_kg"], "overrides.mass_kg", min_value=1, allow_zero=False
                    )
                if "preferred_position" in overrides and raw_cand.get("subcomponents"):
                    pos = require_str(overrides["preferred_position"], "overrides.preferred_position")
                    raw_cand["subcomponents"][0]["preferred_positions"] = [pos]
                    if pos not in raw_cand["subcomponents"][0]["allowed_positions"]:
                        raw_cand["subcomponents"][0]["allowed_positions"].append(pos)
                if "nominal_power_w" in overrides:
                    def_m = raw_cand["default_mode"]
                    p_w = require_int(overrides["nominal_power_w"], "overrides.nominal_power_w", min_value=0)
                    raw_cand["modes"][def_m]["min_power_w"] = min(p_w, raw_cand["modes"][def_m]["min_power_w"])
                    raw_cand["modes"][def_m]["nominal_power_w"] = p_w
                    raw_cand["modes"][def_m]["sustained_power_w"] = max(p_w, raw_cand["modes"][def_m]["sustained_power_w"])
                    raw_cand["modes"][def_m]["peak_power_w"] = max(
                        raw_cand["modes"][def_m]["sustained_power_w"],
                        raw_cand["modes"][def_m]["peak_power_w"],
                    )
                    sust_w = raw_cand["modes"][def_m]["sustained_power_w"]
                    if raw_cand["modes"][def_m]["cooling_air_mast_w"] == 0:
                        raw_cand["modes"][def_m]["cooling_water_w"] = sust_w
                    else:
                        air_w = min(2000, sust_w)
                        raw_cand["modes"][def_m]["cooling_air_mast_w"] = air_w
                        raw_cand["modes"][def_m]["cooling_water_w"] = sust_w - air_w

            cand_manifest = validate_and_build_guest_manifest(raw_cand)
            aug_cat = dict(sess.session_catalog)
            aug_cat[cand_manifest.guest_id] = cand_manifest

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
                next_window=req.get("next_window", None),
                hangar_roof_clearance_verified=hangar_roof_verified,
            )

            verdict = decision["verdict"]
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
                    }
                )

            return 200, {
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
                "current_budget_snapshot": updated_snap,
            }

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
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"PERTURB:{perturbation}",
                        "run_id": sess.run_id,
                    }
                )
                return 200, {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "perturbation": perturbation,
                    "current_budget_snapshot": snap_after,
                    "result": res_clean,
                }

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
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"PERTURB:{perturbation}",
                        "run_id": sess.run_id,
                    }
                )
                return 200, {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "perturbation": perturbation,
                    "current_budget_snapshot": snap_after,
                    "result": res_clean,
                }

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
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"PERTURB:{perturbation}:{gid}",
                        "run_id": sess.run_id,
                    }
                )
                return 200, {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "perturbation": perturbation,
                    "current_budget_snapshot": snap_after,
                    "result": res_clean,
                }

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
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"PERTURB:{perturbation}",
                        "run_id": sess.run_id,
                    }
                )
                return 200, {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "perturbation": perturbation,
                    "current_budget_snapshot": snap_after,
                    "result": res_clean,
                }

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
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "sim_time_s": sess.live_state.sim_time_s,
                        "action": f"URGENT:{cmd}:{gid}",
                        "run_id": sess.run_id,
                    }
                )
                return 200, {
                    "status": "OK",
                    "run_id": sess.run_id,
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "perturbation": perturbation,
                    "current_budget_snapshot": snap_after,
                    "result": res_clean,
                }

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
            sess.state_version += 1
            sess.action_history.append(
                {
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "sim_time_s": sess.live_state.sim_time_s,
                    "action": f"ARBITRATE:{prof_id}->{arb['verdict']}",
                    "run_id": sess.run_id,
                }
            )
            return 200, {
                "status": "OK",
                "run_id": sess.run_id,
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "arbitration": arb,
            }

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


def serve_operator_ui(repo_root: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    """Start the local HTTP server for the single-screen SDV Resource Governor operator console."""
    ui_dir = repo_root / "ui"
    sess = get_or_create_session(repo_root)

    class GovernorRequestHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, code: int, payload: Dict[str, Any]) -> None:
            body_bytes = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
            self.send_response(code)
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
            if clean_path in ("/", "/index.html"):
                target = ui_dir / "index.html"
                ctype = "text/html; charset=utf-8"
            elif clean_path == "/app.js":
                target = ui_dir / "app.js"
                ctype = "application/javascript; charset=utf-8"
            elif clean_path == "/styles.css":
                target = ui_dir / "styles.css"
                ctype = "text/css; charset=utf-8"
            else:
                self._send_json(404, {"status": "NOT_FOUND", "path": clean_path})
                return

            if not target.exists():
                self._send_json(404, {"status": "MISSING_UI_ASSET", "file": target.name})
                return

            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
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

    httpd = HTTPServer((host, port), GovernorRequestHandler)
    print(f"SDV Resource Governor UI serving on http://{host}:{port}")
    httpd.serve_forever()

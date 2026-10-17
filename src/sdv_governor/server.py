"""Stateful HTTP JSON API and static UI server for the single-screen SDV Resource Governor console (B10, DEM-01, DEM-02, H12, H13)."""

from __future__ import annotations

import copy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse
import yaml

from .admission import (
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
    StrictValidationError,
    require_bool,
    require_finite_number,
    require_int,
    require_str,
)
from .replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from .scheduling import evaluate_crew_regimes_comparison, handle_lost_or_stale_feed
from .ship_state import InstalledGuestState, ShipOperationalState


class GovernorSession:
    """Persistent operational session state across API calls (H12)."""

    def __init__(self, repo_root: Path) -> None:
        self.repo_root = repo_root.resolve()
        self.state_version: int = 1
        self.priority_profile_id: str = "survey_primary"
        self.scenario_file: str = "scenario_B_arbitration_degradation.yaml"
        self.live_state: Optional[ShipOperationalState] = None
        self.action_history: List[Dict[str, Any]] = []
        self.reset(self.priority_profile_id, self.scenario_file)

    @property
    def snapshot_id(self) -> str:
        sim_t = self.live_state.sim_time_s if self.live_state is not None else 0
        return f"SNAP_V{self.state_version:04d}_T{sim_t}S"

    def reset(self, priority_profile_id: str = "survey_primary", scenario_file: str = "scenario_B_arbitration_degradation.yaml") -> None:
        data_dir = self.repo_root / "data"
        catalog = load_guest_catalog(
            data_dir / "guests",
            errata_path=data_dir / "overrides" / "brief_errata.yaml",
        )
        scen_path = data_dir / "scenarios" / scenario_file
        if not scen_path.exists():
            scenario_file = "scenario_B_arbitration_degradation.yaml"
            scen_path = data_dir / "scenarios" / scenario_file
        scen_data = load_yaml_file(scen_path)
        self.priority_profile_id = priority_profile_id
        self.scenario_file = scenario_file
        self.live_state = build_initial_state_from_scenario(scen_data, catalog)
        self.state_version += 1
        self.action_history = [
            {
                "state_version": self.state_version,
                "snapshot_id": self.snapshot_id,
                "action": "SESSION_INITIALIZED",
                "priority_profile_id": self.priority_profile_id,
                "scenario_file": self.scenario_file,
            }
        ]


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
    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
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
            req = body or {}
            prof_id = require_str(req.get("priority_profile_id", "survey_primary"), "priority_profile_id")
            if prof_id not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
            scen_file = require_str(req.get("scenario", "scenario_B_arbitration_degradation.yaml"), "scenario")
            sess.reset(prof_id, scen_file)
            assert sess.live_state is not None
            snap = evaluate_eight_class_budgets(ship_profile, catalog, sess.live_state)
            return 200, {
                "status": "OK",
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "priority_profile_id": sess.priority_profile_id,
                "current_budget_snapshot": snap,
                "action_history": sess.action_history,
            }

        if method == "GET" and route == "/api/state":
            req_prof = query.get("priority_profile", [sess.priority_profile_id])[0]
            req_scen = query.get("scenario", [sess.scenario_file])[0]
            if req_prof not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile '{req_prof}'", "priority_profile")

            if req_prof != sess.priority_profile_id or req_scen != sess.scenario_file:
                sess.priority_profile_id = req_prof
                sess.scenario_file = req_scen
                sess.state_version += 1
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": "PROFILE_OR_SCENARIO_SWITCHED",
                        "priority_profile_id": req_prof,
                        "scenario_file": req_scen,
                    }
                )

            scen_path = data_dir / "scenarios" / sess.scenario_file
            if not scen_path.exists():
                scen_path = data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml"

            scen_data = load_yaml_file(scen_path)
            replay_res = run_scenario_b_replay(scen_data, ship_profile, profiles[sess.priority_profile_id], catalog)
            combos_res = evaluate_four_guest_combinations(
                catalog, ship_profile, mission_envelope, profiles[sess.priority_profile_id]
            )
            assert sess.live_state is not None
            current_budget = evaluate_eight_class_budgets(ship_profile, catalog, sess.live_state)
            crew_comp = evaluate_crew_regimes_comparison(catalog, sess.live_state)
            doc_info = run_doctor(repo_root, probe_remote=False)

            guest_raw_map: Dict[str, Any] = {}
            for ypath in sorted((data_dir / "guests").glob("*.yaml")):
                raw_g = yaml.safe_load(ypath.read_text(encoding="utf-8"))
                guest_raw_map[raw_g["guest_id"]] = raw_g

            return 200, {
                "status": "OK",
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "time_compression": {
                    "wall_clock_demo_s": 90,
                    "simulated_mission_s": 1800,
                    "acceleration_factor": 20.0,
                    "tick_interval_sim_s": 20,
                },
                "doctor": doc_info,
                "priority_profile_id": sess.priority_profile_id,
                "available_priority_profiles": list(profiles.keys()),
                "initial_budget_snapshot": current_budget,
                "current_budget_snapshot": current_budget,
                "live_operational_state": sess.live_state.to_snapshot_dict(),
                "action_history": sess.action_history,
                "scenario_B_replay": replay_res,
                "scenario_A_combinations": combos_res,
                "scenario_C_crew_regimes": crew_comp,
                "guest_manifests_raw": guest_raw_map,
            }

        if method == "POST" and route == "/api/admit_live":
            req = body or {}
            prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
            if prof_id not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
            priority_profile = profiles[prof_id]

            installed_ids = req.get("installed_guest_ids", ["G1", "G2", "G3"])
            if not isinstance(installed_ids, list):
                raise StrictValidationError("TYPE_ERROR", "installed_guest_ids must be a list of guest IDs", "installed_guest_ids")
            for idx, gid in enumerate(installed_ids):
                require_str(gid, f"installed_guest_ids[{idx}]")

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

            base_state = ShipOperationalState(
                tick_seq=0,
                sim_time_s=0,
                vessel_mode="PORT_INTEGRATION" if at_port else "QUIET_SURVEY",
                speed_kn=0.0 if at_port else require_finite_number(req.get("speed_kn", 6.0), "speed_kn", min_value=0.0),
                heading_deg=0.0,
                wind_deg=0.0,
                wind_kn=require_int(req.get("wind_kn", 10), "wind_kn", min_value=0),
                sea_state=require_int(req.get("sea_state", 2), "sea_state", min_value=0, max_value=9),
                seawater_temp_c=require_finite_number(req.get("seawater_temp_c", 16.0), "seawater_temp_c", min_value=-5.0, max_value=60.0),
                power_reserve_w=require_int(req.get("power_reserve_w", 200000), "power_reserve_w", min_value=0),
            )

            # Never silently drop requested baseline guests (H13)
            state, baseline_conflicts = build_baseline_state_with_installed_guests(
                installed_ids, base_state, ship_profile, catalog
            )

            if "candidate_manifest" in req and isinstance(req["candidate_manifest"], dict):
                raw_cand = copy.deepcopy(req["candidate_manifest"])
            else:
                base_gid = require_str(req.get("candidate_guest_id", "G6"), "candidate_guest_id")
                matches = list((data_dir / "guests").glob(f"{base_gid}_*.yaml"))
                if not matches:
                    raise StrictValidationError("UNKNOWN_GUEST", f"Unknown candidate_guest_id '{base_gid}'", "candidate_guest_id")
                base_path = matches[0]
                raw_cand = yaml.safe_load(base_path.read_text(encoding="utf-8"))
                overrides = req.get("overrides", {})
                if not isinstance(overrides, dict):
                    raise StrictValidationError("TYPE_ERROR", "overrides must be a JSON object", "overrides")
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
            decision = evaluate_admission(
                cand_manifest,
                state,
                ship_profile,
                mission_envelope,
                priority_profile,
                catalog,
                at_port=at_port,
                allow_displacement=allow_disp,
                defer_if_temporary_operational_block=defer_temp,
                next_window=req.get("next_window", None),
                hangar_roof_clearance_verified=hangar_roof_verified,
            )

            sess.state_version += 1
            if decision["verdict"] == "YES" and decision.get("placement"):
                p = decision["placement"]
                state.installed_guests[cand_manifest.guest_id] = InstalledGuestState(
                    guest_id=cand_manifest.guest_id,
                    active_mode=p["initial_mode"],
                    powered_on=True,
                    placements=p["subcomponent_positions"],
                    compute_node=p["compute_node"],
                )
            sess.live_state = state
            aug_cat = dict(catalog)
            aug_cat[cand_manifest.guest_id] = cand_manifest
            updated_snap = evaluate_eight_class_budgets(ship_profile, aug_cat, sess.live_state)
            sess.action_history.append(
                {
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "action": f"ADMIT_LIVE:{cand_manifest.guest_id}->{decision['verdict']}",
                }
            )

            return 200, {
                "status": "OK",
                "state_version": sess.state_version,
                "snapshot_id": sess.snapshot_id,
                "candidate_guest_id": cand_manifest.guest_id,
                "candidate_total_mass_kg": cand_manifest.total_mass_kg,
                "installed_before": list(installed_ids),
                "baseline_conflicts": baseline_conflicts,
                "decision": decision,
                "current_budget_snapshot": updated_snap,
            }

        if method == "POST" and route == "/api/perturb":
            req = body or {}
            perturbation = require_str(req.get("perturbation_type", "GENERATOR_LOSS_100KW"), "perturbation_type")
            prof_id = require_str(req.get("priority_profile_id", sess.priority_profile_id), "priority_profile_id")
            if prof_id not in profiles:
                raise StrictValidationError("UNKNOWN_ENUM", f"Unknown priority_profile_id '{prof_id}'", "priority_profile_id")
            priority_profile = profiles[prof_id]
            sess.priority_profile_id = prof_id

            assert sess.live_state is not None
            state = sess.live_state.clone()
            # Ensure G4 is in active recovery if still in initial patrol state so D4 conflict is exercised
            if "G4" in state.installed_guests and state.installed_guests["G4"]. powered_on:
                if state.installed_guests["G4"].active_mode == "FLIGHT_PATROL":
                    state.installed_guests["G4"].active_mode = "LAUNCH_RECOVERY_OPTICS"

            if perturbation == "GENERATOR_LOSS_100KW":
                state.power_reserve_w = 100000
                state.vessel_mode = "DEGRADED_POWER"
                res = execute_graceful_degradation(state, ship_profile, priority_profile, catalog)
                sess.live_state = res["resulting_state"]
                sess.state_version += 1
                res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": f"PERTURB:{perturbation}",
                    }
                )
                return 200, {
                    "status": "OK",
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "perturbation": perturbation,
                    "result": res_clean,
                }

            if perturbation == "COOLING_LOSS_30C_40PCT":
                state.seawater_temp_c = 30.0
                state.cooling_loop_degraded = True
                state.vessel_mode = "DEGRADED_THERMAL"
                res = execute_graceful_degradation(state, ship_profile, priority_profile, catalog)
                sess.live_state = res["resulting_state"]
                sess.state_version += 1
                res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": f"PERTURB:{perturbation}",
                    }
                )
                return 200, {
                    "status": "OK",
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "perturbation": perturbation,
                    "result": res_clean,
                }

            if perturbation == "GUEST_OVERCONSUMPTION_20PCT":
                gid = require_str(req.get("guest_id", "G5"), "guest_id")
                measured_w = require_int(req.get("measured_power_w", 54000), "measured_power_w", min_value=1)
                res = handle_guest_overconsumption(
                    state, ship_profile, priority_profile, catalog,
                    offending_guest_id=gid, measured_power_w=measured_w
                )
                sess.live_state = res["resulting_state"]
                sess.state_version += 1
                res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": f"PERTURB:{perturbation}:{gid}",
                    }
                )
                return 200, {
                    "status": "OK",
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "perturbation": perturbation,
                    "result": res_clean,
                }

            if perturbation == "LOST_SEAWATER_TEMP_FEED":
                res = handle_lost_or_stale_feed(
                    state, ship_profile, catalog, feed_name="seawater_temp_c", age_ms=60000
                )
                sess.live_state = res["resulting_state"]
                sess.state_version += 1
                res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": f"PERTURB:{perturbation}",
                    }
                )
                return 200, {
                    "status": "OK",
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "perturbation": perturbation,
                    "result": res_clean,
                }

            if perturbation in ("URGENT_STOP", "URGENT_HOLD", "URGENT_SECURE"):
                cmd = perturbation.split("_", 1)[1]
                gid = require_str(req.get("guest_id", "G3"), "guest_id")
                res = issue_urgent_command(cmd, gid, state, catalog)
                sess.live_state = res["resulting_state"]
                sess.state_version += 1
                snap_after = evaluate_eight_class_budgets(ship_profile, catalog, sess.live_state)
                res_clean = {k: v for k, v in res.items() if k != "resulting_state"}
                res_clean["budget_snapshot"] = snap_after
                sess.action_history.append(
                    {
                        "state_version": sess.state_version,
                        "snapshot_id": sess.snapshot_id,
                        "action": f"URGENT:{cmd}:{gid}",
                    }
                )
                return 200, {
                    "status": "OK",
                    "state_version": sess.state_version,
                    "snapshot_id": sess.snapshot_id,
                    "perturbation": perturbation,
                    "result": res_clean,
                }

            return 400, {"status": "ERROR", "message": f"Unknown perturbation_type '{perturbation}'"}

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

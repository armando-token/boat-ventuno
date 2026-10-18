"""Command-line interface for SDV Resource Governor (B1..B11)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

from .admission import (
    build_baseline_state_with_installed_guests,
    evaluate_admission,
    evaluate_four_guest_combinations,
)
from .arbitration import arbitrate_live_conflict
from .degradation import execute_graceful_degradation
from .deployment import run_doctor, sync_and_run_remote_parity
from .evidence import (
    generate_evidence_bundle,
    run_adversarial_mutation_suite,
    verify_evidence_bundle,
)
from .manifest import (
    load_guest_catalog,
    load_priority_profile,
    validate_mission_envelope,
    validate_ship_profile,
)
from .replay import (
    build_initial_state_from_scenario,
    load_yaml_file,
    run_full_campaign_suite,
    run_scenario_b_replay,
)
from .server import serve_operator_ui
from .ship_state import FirmReservedLoad, InstalledGuestState, ShipOperationalState


def _default_repo_root() -> Path:
    # Support cwd, editable source layout (`src/sdv_governor/cli.py` -> parents[2]),
    # and packaged wheel installation (`Path(__file__).parent / "_bundled"`)
    cwd = Path.cwd().resolve()
    if (cwd / "data" / "guests").exists():
        return cwd
    candidate_root = Path(__file__).resolve().parents[2]
    if (candidate_root / "data" / "guests").exists():
        return candidate_root
    bundled_root = Path(__file__).resolve().parent / "_bundled"
    if (bundled_root / "data" / "guests").exists():
        return bundled_root
    return candidate_root


def _resolve_priority_profile_path(data_dir: Path, profile_arg: str) -> Path:
    cand = Path(profile_arg)
    if cand.exists():
        return cand.resolve()
    named = data_dir / "priorities" / f"{profile_arg}.yaml"
    if named.exists():
        return named
    if (data_dir / "priorities" / profile_arg).exists():
        return data_dir / "priorities" / profile_arg
    return named


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sdv-governor",
        description="Deterministic 8-class Resource Governor for Software-Defined Vessel",
    )
    parser.add_argument("--repo-root", type=Path, default=_default_repo_root(), help="Repository root directory")

    sub = parser.add_subparsers(dest="command", required=True)

    p_doc = sub.add_parser("doctor", help="Check local host and optional remote board readiness (B1)")
    p_doc.add_argument("--ssh-target", default="ardu", help="SSH host alias for remote Linux board")
    p_doc.add_argument("--no-remote", action="store_true", help="Skip remote SSH probe")

    sub.add_parser("validate", help="Validate all guest manifests, priority profiles, and baseline ship YAML files (B2)")

    p_adm = sub.add_parser("admit", help="Evaluate D1 Scenario A admission for a candidate guest (B4)")
    p_adm.add_argument("--candidate", required=True, help="Candidate guest ID (e.g., G4, G5, G6)")
    p_adm.add_argument("--installed", default="G1,G2,G3", help="Comma-separated installed guest IDs")
    p_adm.add_argument("--profile", default="survey_primary", help="Priority profile ID or YAML path")
    p_adm.add_argument("--at-sea", action="store_true", help="Evaluate at sea instead of at port")

    sub.add_parser("combinations", help="Evaluate all 5 four-guest combinations of G1..G5 (Errata 3 of 5 > 10,000 kg)")

    p_arb = sub.add_parser("arbitrate", help="Run D3 Scenario B live arbitration under chosen priority profile (B5)")
    p_arb.add_argument("--profile", default="survey_primary", help="Priority profile ID or YAML path")

    p_deg = sub.add_parser("degrade", help="Run D4 graceful degradation on a Scenario B state (B6)")
    p_deg.add_argument("--scenario", default="scenario_B_arbitration_degradation.yaml")
    p_deg.add_argument("--profile", default="survey_primary", help="Priority profile ID or YAML path")

    p_rep = sub.add_parser("replay", help="Run deterministic Scenario B replay and print semantic hash (B5/B6)")
    p_rep.add_argument("--scenario", default="scenario_B_arbitration_degradation.yaml")
    p_rep.add_argument("--profile", default="survey_primary", help="Priority profile ID or YAML path")
    p_rep.add_argument("--runs", type=int, default=1, help="Number of consecutive runs to verify identical semantic_sha256")

    p_camp = sub.add_parser("campaign", help="Run full campaign and generate verified evidence bundle in artifacts/<run_id>")
    p_camp.add_argument("--run-id", default=None, help="Explicit run ID (default: timestamped run_YYYYMMDD_HHMMSS)")
    p_camp.add_argument("--output-dir", type=Path, default=None, help="Output base directory (default: artifacts)")
    p_camp.add_argument("--junit-xml", type=Path, default=None, help="Path to pytest JUnit XML report to include")
    p_camp.add_argument("--with-remote-parity", action="store_true", help="Include remote Linux board parity run")
    p_camp.add_argument("--ssh-target", default="ardu", help="SSH host alias for remote Linux board parity")

    p_ver = sub.add_parser("verify-evidence", help="Verify an evidence bundle fail-closed (B8)")
    p_ver.add_argument("bundle_dir", nargs="?", type=Path, default=None, help="Path to artifacts/<run_id> directory")
    p_ver.add_argument("--bundle-dir", dest="bundle_dir_opt", type=Path, default=None, help="Path to artifacts/<run_id> directory")
    p_ver.add_argument("--run-mutations", action="store_true", help="Also run adversarial mutations in a scratch folder")

    p_dep = sub.add_parser("deploy-board", help="Sync codebase to remote Linux board and verify semantic parity (B1/B11)")
    p_dep.add_argument("--ssh-target", default="ardu")
    p_dep.add_argument("--run-id", default=None, help="Optional run identifier for remote parity")

    p_ui = sub.add_parser("serve-ui", aliases=["serve"], help="Start single-screen 2D operator console HTTP server (B10)")
    p_ui.add_argument("--host", default="127.0.0.1")
    p_ui.add_argument("--port", type=int, default=8765)

    p_pes = sub.add_parser("public-env-suite", help="Run Golden Gate public data scenario suite (recorded replay & A' window)")
    p_pes.add_argument("--json", action="store_true", help="Output results in JSON format")

    p_fcb = sub.add_parser("forecast-bench", help="Run forecast benchmark and latency gates (PERF / §10)")
    p_fcb.add_argument("--split", default="test", choices=["train", "calib", "test", "ood", "all"], help="Dataset split to evaluate")
    p_fcb.add_argument("--output-dir", type=Path, default=None, help="Directory to save forecast benchmark report JSON")

    args = parser.parse_args(argv)
    root: Path = args.repo_root.resolve()
    data_dir = root / "data"

    if args.command == "doctor":
        rep = run_doctor(root, ssh_target=args.ssh_target, probe_remote=not args.no_remote)
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0

    if args.command == "validate":
        catalog = load_guest_catalog(
            data_dir / "guests",
            errata_path=data_dir / "overrides" / "brief_errata.yaml",
        )
        prof_s = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
        prof_r = load_priority_profile(data_dir / "priorities" / "rescue_primary.yaml")
        ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
        env = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))
        print(json.dumps({
            "status": "VALIDATED",
            "guests_loaded": sorted(catalog.keys()),
            "guest_masses_kg": {gid: g.total_mass_kg for gid, g in sorted(catalog.items())},
            "priority_profiles": [prof_s.priority_profile_id, prof_r.priority_profile_id],
            "ship_profile_id": ship["vessel_id"],
            "mission_envelope_windows": len(env.get("envelope_states", env.get("windows", []))),
        }, indent=2, sort_keys=True))
        return 0

    catalog = load_guest_catalog(
        data_dir / "guests",
        errata_path=data_dir / "overrides" / "brief_errata.yaml",
    )
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    mission_envelope = validate_mission_envelope(load_yaml_file(data_dir / "ship" / "mission_envelope_a10.yaml"))

    if args.command == "combinations":
        prof = load_priority_profile(data_dir / "priorities" / "survey_primary.yaml")
        rep = evaluate_four_guest_combinations(catalog, ship_profile, mission_envelope, prof)
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0 if rep["errata_three_of_five_verified"] else 1

    if args.command == "admit":
        prof = load_priority_profile(_resolve_priority_profile_path(data_dir, args.profile))
        base_state = ShipOperationalState(
            tick_seq=0,
            sim_time_s=0,
            vessel_mode="QUIET_SURVEY" if args.at_sea else "PORT_INTEGRATION",
            speed_kn=6.0 if args.at_sea else 0.0,
            heading_deg=0.0,
            wind_deg=0.0,
            wind_kn=12,
            sea_state=2,
            seawater_temp_c=16.0,
            power_reserve_w=200000,
        )
        installed_ids = [x.strip() for x in args.installed.split(",") if x.strip()]
        state, baseline_conflicts = build_baseline_state_with_installed_guests(
            installed_ids, base_state, ship_profile, catalog
        )
        res = evaluate_admission(
            catalog[args.candidate],
            state,
            ship_profile,
            mission_envelope,
            prof,
            catalog,
            at_port=not args.at_sea,
            defer_if_temporary_operational_block=args.at_sea,
        )
        res["installed_before"] = list(state.installed_guests.keys())
        res["baseline_conflicts"] = baseline_conflicts
        print(json.dumps(res, indent=2, sort_keys=True))
        genset_down = res.get("one_genset_down")
        if genset_down:
            print(f"\u26a1 One genset down [{genset_down['holds']}]: {genset_down['sentence']}", file=sys.stderr)
        return 0

    if args.command == "arbitrate":
        prof = load_priority_profile(_resolve_priority_profile_path(data_dir, args.profile))
        scen = load_yaml_file(data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml")
        state = build_initial_state_from_scenario(scen, catalog)
        state.sim_time_s = 480
        res = arbitrate_live_conflict(state, ship_profile, prof, catalog)
        print(json.dumps(res, indent=2, sort_keys=True))
        return 0

    if args.command == "degrade":
        prof = load_priority_profile(_resolve_priority_profile_path(data_dir, args.profile))
        scen_path = Path(args.scenario)
        if not scen_path.exists():
            scen_path = data_dir / "scenarios" / args.scenario
        scen = load_yaml_file(scen_path)
        state = build_initial_state_from_scenario(scen, catalog)
        if "COOLING" in scen.get("scenario_id", ""):
            state.seawater_temp_c = 30.0
            state.cooling_loop_degraded = True
        else:
            state.sim_time_s = 720
            state.power_reserve_w = 100000
            if "G3" in state.installed_guests and scen.get("scenario_id") == "SCENARIO_B_ARBITRATION_DEGRADATION":
                state.installed_guests["G3"].active_mode = "HOLD_ACOUSTIC"
            if (
                "G4" in state.installed_guests
                and state.installed_guests["G4"].active_mode == "FLIGHT_PATROL"
                and not any(fl.guest_id == "G4" for fl in state.firm_reserved_loads)
            ):
                state.firm_reserved_loads.append(
                    FirmReservedLoad(
                        reservation_id="D3_PLAN_V1_G4_RECOVERY_35KW",
                        guest_id="G4",
                        reserved_mode="LAUNCH_RECOVERY_OPTICS",
                        reserved_power_w=35000,
                        reason="Planned G4 35 kW recovery approach + 20-min hangar charge",
                    )
                )
        res = execute_graceful_degradation(state, ship_profile, prof, catalog)
        res_out = {k: v for k, v in res.items() if k != "resulting_state"}
        print(json.dumps(res_out, indent=2, sort_keys=True))
        return 0

    if args.command == "replay":
        prof = load_priority_profile(_resolve_priority_profile_path(data_dir, args.profile))
        scen_path = Path(args.scenario)
        if not scen_path.exists():
            scen_path = data_dir / "scenarios" / args.scenario
        scen = load_yaml_file(scen_path)
        hashes: List[str] = []
        last_res: Dict[str, Any] = {}
        for _ in range(max(1, args.runs)):
            last_res = run_scenario_b_replay(scen, ship_profile, prof, catalog)
            hashes.append(last_res["semantic_sha256"])
        deterministic = len(set(hashes)) == 1
        print(json.dumps({
            "scenario_id": last_res["scenario_id"],
            "priority_profile_id": prof.priority_profile_id,
            "runs": len(hashes),
            "semantic_hashes": hashes,
            "deterministic_across_runs": deterministic,
            "runtime_telemetry": last_res["runtime_telemetry"],
        }, indent=2, sort_keys=True))
        return 0 if deterministic else 1

    if args.command == "campaign":
        run_id = args.run_id or datetime.now(timezone.utc).strftime("run_%Y%m%d_%H%M%S")
        camp = run_full_campaign_suite(root)
        parity_data = None
        if args.with_remote_parity:
            parity_data = sync_and_run_remote_parity(root, ssh_target=args.ssh_target)
        junit_src = args.junit_xml
        if junit_src is None and (root / "artifacts" / "validation_alignment_local" / "junit.xml").exists():
            junit_src = root / "artifacts" / "validation_alignment_local" / "junit.xml"
        bundle_dir = generate_evidence_bundle(
            root, run_id, camp, junit_xml_source=junit_src, remote_parity_data=parity_data
        )
        ok, errs, ver_rep = verify_evidence_bundle(bundle_dir)
        print(json.dumps({
            "run_id": run_id,
            "bundle_dir": str(bundle_dir),
            "campaign_semantic_sha256": camp["campaign_semantic_sha256"],
            "verified": ok,
            "verification_errors": errs,
            "verification_summary": ver_rep,
        }, indent=2, sort_keys=True))
        return 0 if ok else 1

    if args.command == "verify-evidence":
        target_bundle_dir = args.bundle_dir_opt or args.bundle_dir
        if target_bundle_dir is None:
            raise ValueError("Must provide bundle_dir positional argument or --bundle-dir")
        ok, errs, ver_rep = verify_evidence_bundle(target_bundle_dir)
        out: Dict[str, Any] = {
            "bundle_dir": str(target_bundle_dir),
            "verified": ok,
            "errors": errs,
            "summary": ver_rep,
        }
        if args.run_mutations and ok:
            mut_rep = run_adversarial_mutation_suite(
                target_bundle_dir, target_bundle_dir.parent / "_scratch_mutations"
            )
            out["adversarial_mutations"] = mut_rep
            ok = ok and mut_rep["all_rejected"]
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0 if ok else 1

    if args.command == "deploy-board":
        rep = sync_and_run_remote_parity(root, ssh_target=args.ssh_target)
        print(json.dumps(rep, indent=2, sort_keys=True))
        return 0 if rep.get("parity_verified") or rep.get("status") == "BLOCKED_CONNECTION" else 1

    if args.command in ("serve-ui", "serve"):
        serve_operator_ui(root, host=args.host, port=args.port)
        return 0

    if args.command == "public-env-suite":
        from .forecast.observer import ForecastObserver
        from .replay import run_public_env_suite
        pes_catalog = load_guest_catalog(
            data_dir / "guests", errata_path=data_dir / "overrides" / "brief_errata.yaml"
        )
        pes_ship = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
        res = run_public_env_suite(root, observer=ForecastObserver(ship_profile=pes_ship, catalog=pes_catalog))
        print(json.dumps(res, indent=2, sort_keys=True, default=str))
        return 0

    if args.command == "forecast-bench":
        bench_script = root / "tools" / "bench_forecast.py"
        if not bench_script.exists():
            # Never print canned metrics: without the benchmark tool there is no evidence.
            print(json.dumps({"status": "TOOL_NOT_AVAILABLE", "missing": str(bench_script)}, indent=2))
            return 2
        import subprocess
        cmd = [sys.executable, str(bench_script), "--split", args.split]
        out_dir = args.output_dir or (root / "docs" / "evidence")
        cmd.extend(["--out", str(out_dir)])
        proc = subprocess.run(cmd, cwd=str(root))
        return proc.returncode

    return 1


if __name__ == "__main__":
    sys.exit(main())

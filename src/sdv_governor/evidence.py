"""Fail-closed evidence artifact generator and independent semantic content verifier (B8, F2, EVD-01..04, H05, H16, H17)."""

from __future__ import annotations

import copy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

from .models import canonical_json_hash


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_for_non_finite(obj: Any, path_prefix: str = "root") -> List[str]:
    errors: List[str] = []
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            errors.append(f"NON_FINITE float at {path_prefix}: {obj}")
    elif isinstance(obj, str):
        if obj.strip().lower() in ("nan", "+nan", "-nan", "inf", "+inf", "-inf", "infinity", "-infinity"):
            errors.append(f"NON_FINITE string token at {path_prefix}: {obj!r}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            errors.extend(_scan_for_non_finite(v, f"{path_prefix}.{k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            errors.extend(_scan_for_non_finite(item, f"{path_prefix}[{idx}]"))
    return errors


def _recompute_metrics_from_raw(decisions_data: Dict[str, Any], timeline_data: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute all summary metrics directly from raw decisions.json and replay_timeline.json."""
    combos = decisions_data["scenario_A"]["four_guest_combinations"]["combinations"]
    exceed_10t = sum(1 for c in combos if int(c["total_mass_kg"]) > 10000)

    b_survey_ticks = timeline_data["survey_primary"]["timeline_ticks"]
    b_rescue_ticks = timeline_data["rescue_primary"]["timeline_ticks"]
    b_cool_ticks = timeline_data["cooling_limited_30c"]["timeline_ticks"]

    all_tick_latencies = [
        float(t["measured_tick_calc_ms"])
        for ticks in (b_survey_ticks, b_rescue_ticks, b_cool_ticks)
        for t in ticks
    ]
    max_tick_ms = round(max(all_tick_latencies) if all_tick_latencies else 0.0, 4)
    min_tick_ms = round(min(all_tick_latencies) if all_tick_latencies else 0.0, 4)

    total_decisions = (
        len(combos)
        + 1  # G6 admission
        + len(timeline_data["survey_primary"]["decisions"])
        + len(timeline_data["rescue_primary"]["decisions"])
        + len(timeline_data["cooling_limited_30c"]["decisions"])
    )

    return {
        "total_decisions_count": total_decisions,
        "errata_exceed_10000_kg_count": exceed_10t,
        "survey_primary_ticks_count": len(b_survey_ticks),
        "rescue_primary_ticks_count": len(b_rescue_ticks),
        "cooling_limited_ticks_count": len(b_cool_ticks),
        "max_tick_calc_ms": max_tick_ms,
        "min_tick_calc_ms": min_tick_ms,
        "survey_primary_semantic_sha256": timeline_data["survey_primary"]["semantic_sha256"],
        "rescue_primary_semantic_sha256": timeline_data["rescue_primary"]["semantic_sha256"],
        "cooling_limited_semantic_sha256": timeline_data["cooling_limited_30c"]["semantic_sha256"],
    }


def _load_required_test_names(repo_root: Optional[Path]) -> Set[str]:
    """Load the required pytest function names from `requirements_matrix.csv` if available."""
    req_names: Set[str] = set()
    if repo_root is not None:
        csv_path = repo_root / "requirements_matrix.csv"
        if csv_path.exists():
            with csv_path.open("r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    tid = (row.get("test_ids") or "").strip()
                    if "::" in tid:
                        req_names.add(tid.split("::")[-1].strip())
    return req_names


def parse_and_verify_junit_xml(
    junit_path: Path,
    *,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, List[str], Dict[str, int]]:
    """Parse JUnit XML using ElementTree and verify real per-testcase outcomes without synthetic fallbacks (EVD-03, EVD-04, H05)."""
    errors: List[str] = []
    if not junit_path.exists():
        return False, [f"Missing JUnit XML file: {junit_path}"], {}
    if junit_path.stat().st_size == 0:
        return False, [f"Zero-byte JUnit XML file: {junit_path}"], {}

    try:
        tree = ET.parse(junit_path)
        root = tree.getroot()
    except Exception as exc:
        return False, [f"Invalid XML in {junit_path.name}: {exc}"], {}

    suites = [root] if root.tag == "testsuite" else list(root.findall(".//testsuite"))
    if not suites:
        return False, ["JUnit XML contains no <testsuite> element"], {}

    for s in suites:
        if s.attrib.get("name") == "sdv_smoke":
            errors.append("SYNTHETIC_JUNIT_FORBIDDEN: synthetic 'sdv_smoke' fallback testsuite is rejected (H05)")

    attr_tests = sum(int(s.attrib.get("tests", "0")) for s in suites)
    attr_failures = sum(int(s.attrib.get("failures", "0")) for s in suites)
    attr_errors = sum(int(s.attrib.get("errors", "0")) for s in suites)
    attr_skipped = sum(int(s.attrib.get("skipped", "0")) for s in suites)

    testcases = list(root.findall(".//testcase"))
    actual_testcase_count = len(testcases)
    actual_failure_nodes = 0
    actual_error_nodes = 0
    actual_skipped_nodes = 0
    seen_test_keys: Set[Tuple[str, str]] = set()
    observed_test_names: Set[str] = set()

    for tc in testcases:
        cname = tc.attrib.get("classname", "")
        tname = tc.attrib.get("name", "")
        key = (cname, tname)
        if key in seen_test_keys:
            errors.append(f"JUNIT_DUPLICATE_TESTCASE: duplicate testcase ({cname}, {tname})")
        seen_test_keys.add(key)
        observed_test_names.add(tname)

        if tc.find("failure") is not None:
            actual_failure_nodes += 1
        if tc.find("error") is not None:
            actual_error_nodes += 1
        if tc.find("skipped") is not None:
            actual_skipped_nodes += 1

    if attr_tests <= 0 or actual_testcase_count <= 0:
        errors.append(f"JUnit XML has zero executed tests (attr_tests={attr_tests}, testcase_nodes={actual_testcase_count})")
    if attr_tests != actual_testcase_count:
        errors.append(f"JUnit XML test count mismatch: attribute tests={attr_tests} != <testcase> count={actual_testcase_count}")
    if attr_failures > 0 or actual_failure_nodes > 0:
        errors.append(f"JUnit XML contains test failures (attr_failures={attr_failures}, failure_nodes={actual_failure_nodes})")
    if attr_errors > 0 or actual_error_nodes > 0:
        errors.append(f"JUnit XML contains test errors (attr_errors={attr_errors}, error_nodes={actual_error_nodes})")
    if attr_skipped > 0 or actual_skipped_nodes > 0:
        errors.append(f"JUnit XML contains skipped tests (attr_skipped={attr_skipped}, skipped_nodes={actual_skipped_nodes})")

    # Verify all 49 acceptance criteria testcases from requirements_matrix.csv are present in JUnit XML (H05)
    if repo_root is None and junit_path.parent.parent.name == "artifacts":
        repo_root = junit_path.parent.parent.parent
    required_test_names = _load_required_test_names(repo_root)
    if required_test_names:
        missing_tests = sorted(required_test_names - observed_test_names)
        if missing_tests:
            errors.append(
                f"JUNIT_MISSING_REQUIRED_CRITERIA_TESTS: missing {len(missing_tests)} required acceptance tests "
                f"(e.g., {missing_tests[:5]})"
            )
    elif actual_testcase_count < 49:
        errors.append(
            f"JUNIT_INSUFFICIENT_COVERAGE: expected >= 49 acceptance testcases, found {actual_testcase_count}"
        )

    summary = {
        "tests": actual_testcase_count,
        "failures": max(attr_failures, actual_failure_nodes),
        "errors": max(attr_errors, actual_error_nodes),
        "skipped": max(attr_skipped, actual_skipped_nodes),
    }
    return len(errors) == 0, errors, summary


def _verify_independent_semantic_invariants(
    decisions_doc: Dict[str, Any],
    timeline_doc: Dict[str, Any],
    manifest_doc: Dict[str, Any],
) -> List[str]:
    """Independently verify physical, temporal, causal, and hash invariants on raw artifacts (Phase C6)."""
    errors: List[str] = []

    # 1. Verify Scenario A G6 admission invariants
    scen_a = decisions_doc.get("scenario_A", {})
    g6_adm = scen_a.get("g6_admission", {})
    if not g6_adm:
        errors.append("SEMANTIC_VIOLATION: missing scenario_A.g6_admission")
    else:
        el_ms = float(g6_adm.get("calculation_elapsed_ms", -1.0))
        if el_ms < 0.0 or el_ms > 30000.0 or not g6_adm.get("within_30s_deadline", False):
            errors.append(f"DEADLINE_VIOLATION: scenario_A.g6_admission elapsed_ms={el_ms} exceeds 30000 ms deadline")
        if g6_adm.get("verdict") == "YES":
            if g6_adm.get("binding_constraints"):
                errors.append("SEMANTIC_VIOLATION: g6_admission has verdict=YES with non-empty binding_constraints")
            cond_av = g6_adm.get("conditioned_availability", {})
            for w_ev in cond_av.get("window_evaluations", []):
                if not w_ev.get("feasible", False):
                    errors.append(
                        f"SEMANTIC_VIOLATION: g6_admission has verdict=YES but mission envelope window {w_ev.get('window_id')} is infeasible"
                    )

    # 2. Verify each Scenario B profile timeline, recomputable semantic hash, D3 touchdown, and D4 protected loads
    expected_scen_ids = {
        "survey_primary": ("SCENARIO_B_OFFICIAL", "SURVEY_PRIMARY"),
        "rescue_primary": ("SCENARIO_B_OFFICIAL", "RESCUE_PRIMARY"),
        "cooling_limited_30c": ("SCENARIO_B_COOLING_LIMITED_30C", "SURVEY_PRIMARY"),
    }

    for prof_key, (scen_id, prof_id) in expected_scen_ids.items():
        if prof_key not in timeline_doc:
            errors.append(f"SEMANTIC_VIOLATION: missing '{prof_key}' in replay_timeline.json")
            continue
        p_block = timeline_doc[prof_key]
        ticks = p_block.get("timeline_ticks", [])
        decs = p_block.get("decisions", [])
        exec_td_s = p_block.get("executed_touchdown_time_s")

        # Check complete 91-tick sequence (ticks 0..90) without omitted ticks
        if len(ticks) != 91:
            errors.append(f"TIMELINE_OMISSION_VIOLATION in '{prof_key}': expected 91 ticks (0..90), found {len(ticks)}")
        else:
            for idx, t in enumerate(ticks):
                if t.get("tick") != idx or t.get("sim_time_s") != idx * 20:
                    errors.append(
                        f"TIMELINE_SEQUENCE_VIOLATION in '{prof_key}' at index {idx}: tick={t.get('tick')}, sim_time_s={t.get('sim_time_s')}"
                    )
                    break

        # Check physical budget feasibility on every tick labeled feasible=True
        touchdown_events_in_ticks: List[int] = []
        for t in ticks:
            for ev_str in t.get("events", []):
                if "G4_TOUCHDOWN_AND_SECURED_IN_HANGAR" in ev_str:
                    touchdown_events_in_ticks.append(int(t.get("sim_time_s", 0)))
            if t.get("feasible") is True:
                p_act = int(t.get("power_actual_w", 0))
                p_com = int(t.get("power_committed_w", 0))
                p_cap = int(t.get("power_capacity_w", 0))
                c_act = int(t.get("cooling_actual_w", 0))
                c_cap = int(t.get("cooling_capacity_w", 0))
                if p_act > p_cap or p_com > p_cap or c_act > c_cap:
                    errors.append(
                        f"INFEASIBLE_SNAPSHOT_LABELED_YES in '{prof_key}' tick {t.get('tick')}: "
                        f"power={p_act}/{p_com}W vs {p_cap}W, cooling={c_act}W vs {c_cap}W"
                    )

        # Recompute semantic_sha256 directly from decisions + timeline_ticks
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

        sem_ticks = _strip_wall_clock(ticks)
        sem_decs = _strip_wall_clock(decs)
        sem_payload = {
            "scenario_id": scen_id,
            "priority_profile_id": prof_id,
            "executed_touchdown_time_s": exec_td_s,
            "decisions": sem_decs,
            "timeline_ticks": sem_ticks,
        }
        recomputed_prof_sha = canonical_json_hash(sem_payload)
        if recomputed_prof_sha != p_block.get("semantic_sha256"):
            errors.append(
                f"NON_RECOMPUTABLE_SEMANTIC_HASH in '{prof_key}': recorded={p_block.get('semantic_sha256')} != recomputed={recomputed_prof_sha}"
            )

        # Check D3 and D4 decisions inside profile
        for d_entry in decs:
            phase = d_entry.get("phase")
            dec = d_entry.get("decision", {})
            if phase == "D3_ARBITRATION" and dec.get("verdict") == "YES":
                td_s = dec.get("touchdown_time_s")
                dl_s = dec.get("hard_touchdown_deadline_s")
                hold_cmd_s = dec.get("hold_cmd_time_s")
                turn_s = dec.get("turn_into_wind_time_s")
                if td_s is None or dl_s is None or int(td_s) > int(dl_s):
                    errors.append(
                        f"LATE_TOUCHDOWN_VIOLATION in '{prof_key}': D3 touchdown_time_s={td_s} > hard_touchdown_deadline_s={dl_s}"
                    )
                if exec_td_s is None or int(exec_td_s) > int(dl_s) or not touchdown_events_in_ticks:
                    errors.append(
                        f"UNEXECUTED_OR_LATE_TOUCHDOWN in '{prof_key}': executed_touchdown_time_s={exec_td_s}, "
                        f"tick_events={touchdown_events_in_ticks}, deadline={dl_s}"
                    )
                if hold_cmd_s is None or turn_s is None or (int(turn_s) - int(hold_cmd_s)) < 10:
                    errors.append(
                        f"ACOUSTIC_CAUSALITY_VIOLATION in '{prof_key}': turn_into_wind_time_s={turn_s} - hold_cmd_time_s={hold_cmd_s} < 10s"
                    )
            elif phase == "D4_DEGRADATION":
                for act in dec.get("actions_taken", []):
                    if act.get("action_type") == "SHED_LOAD_POWER_OFF":
                        shed_gid = act.get("guest_id")
                        from_m = act.get("from_mode")
                        if (shed_gid == "G3" and from_m == "RECHARGE_CRADLE") or (
                            shed_gid == "G4" and from_m in ("LAUNCH_RECOVERY_OPTICS", "RECOVERY_APPROACH")
                        ):
                            errors.append(
                                f"PROTECTED_LOAD_SHED_VIOLATION in '{prof_key}': protected guest {shed_gid}({from_m}) was shed"
                            )

    # 3. Recompute top-level campaign_semantic_sha256 and compare against decisions.json and evidence_manifest.json
    if not errors:
        sem_summary = {
            "scenario_A_four_guest_combinations": decisions_doc["scenario_A"]["four_guest_combinations"],
            "scenario_A_g6_admission_verdict": decisions_doc["scenario_A"]["g6_admission"]["verdict"],
            "scenario_B_survey_primary_sha256": timeline_doc["survey_primary"]["semantic_sha256"],
            "scenario_B_rescue_primary_sha256": timeline_doc["rescue_primary"]["semantic_sha256"],
            "scenario_B_cooling_limited_sha256": timeline_doc["cooling_limited_30c"]["semantic_sha256"],
            "scenario_C_crew_regimes": decisions_doc["scenario_C"],
        }
        recomputed_camp_sha = canonical_json_hash(sem_summary)
        if recomputed_camp_sha != decisions_doc.get("campaign_semantic_sha256"):
            errors.append(
                f"CAMPAIGN_HASH_MISMATCH in decisions.json: recorded={decisions_doc.get('campaign_semantic_sha256')} != recomputed={recomputed_camp_sha}"
            )
        if recomputed_camp_sha != manifest_doc.get("semantic_sha256"):
            errors.append(
                f"CAMPAIGN_HASH_MISMATCH in evidence_manifest.json: recorded={manifest_doc.get('semantic_sha256')} != recomputed={recomputed_camp_sha}"
            )

    return errors


def generate_evidence_bundle(
    repo_root: Path,
    run_id: str,
    campaign_result: Dict[str, Any],
    *,
    junit_xml_source: Optional[Path] = None,
    remote_parity_data: Optional[Dict[str, Any]] = None,
) -> Path:
    """Create a complete, hash-chained evidence bundle in `artifacts/<run_id>/`.

    Never generates a synthetic JUnit XML fallback (H05). If `junit_xml_source` is not explicitly supplied,
    copies an existing real pytest JUnit XML report from `artifacts/release_c9/junit.xml` or
    `artifacts/final_run/junit.xml` if present; otherwise leaves `junit.xml` absent so verification fails closed.
    """
    bundle_dir = repo_root / "artifacts" / run_id
    bundle_dir.mkdir(parents=True, exist_ok=True)

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        git_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(repo_root), text=True
        ).strip()
    except Exception:
        git_commit = "0" * 40

    decisions_payload = {
        "run_id": run_id,
        "generated_at_utc": now_utc,
        "campaign_semantic_sha256": campaign_result["campaign_semantic_sha256"],
        "scenario_A": campaign_result["scenario_A"],
        "scenario_C": campaign_result["scenario_C"],
    }
    decisions_path = bundle_dir / "decisions.json"
    decisions_path.write_text(json.dumps(decisions_payload, indent=2, sort_keys=True), encoding="utf-8")

    timeline_payload = {
        "run_id": run_id,
        "generated_at_utc": now_utc,
        "survey_primary": {
            "semantic_sha256": campaign_result["scenario_B"]["survey_primary"]["semantic_sha256"],
            "executed_touchdown_time_s": campaign_result["scenario_B"]["survey_primary"]["executed_touchdown_time_s"],
            "decisions": campaign_result["scenario_B"]["survey_primary"]["decisions"],
            "timeline_ticks": campaign_result["scenario_B"]["survey_primary"]["timeline_ticks"],
        },
        "rescue_primary": {
            "semantic_sha256": campaign_result["scenario_B"]["rescue_primary"]["semantic_sha256"],
            "executed_touchdown_time_s": campaign_result["scenario_B"]["rescue_primary"]["executed_touchdown_time_s"],
            "decisions": campaign_result["scenario_B"]["rescue_primary"]["decisions"],
            "timeline_ticks": campaign_result["scenario_B"]["rescue_primary"]["timeline_ticks"],
        },
        "cooling_limited_30c": {
            "semantic_sha256": campaign_result["scenario_B"]["cooling_limited_30c"]["semantic_sha256"],
            "executed_touchdown_time_s": campaign_result["scenario_B"]["cooling_limited_30c"]["executed_touchdown_time_s"],
            "decisions": campaign_result["scenario_B"]["cooling_limited_30c"]["decisions"],
            "timeline_ticks": campaign_result["scenario_B"]["cooling_limited_30c"]["timeline_ticks"],
        },
    }
    timeline_path = bundle_dir / "replay_timeline.json"
    timeline_path.write_text(json.dumps(timeline_payload, indent=2, sort_keys=True), encoding="utf-8")

    recomputed_metrics = _recompute_metrics_from_raw(decisions_payload, timeline_payload)
    metrics_payload = {
        "run_id": run_id,
        "generated_at_utc": now_utc,
        "metrics": recomputed_metrics,
        "runtime_telemetry": campaign_result["runtime_telemetry"],
    }
    metrics_path = bundle_dir / "metrics_summary.json"
    metrics_path.write_text(json.dumps(metrics_payload, indent=2, sort_keys=True), encoding="utf-8")

    junit_target = bundle_dir / "junit.xml"
    if junit_xml_source is not None and junit_xml_source.exists():
        if junit_xml_source.resolve() != junit_target.resolve():
            shutil.copy2(junit_xml_source, junit_target)
    elif not junit_target.exists():
        # Look for an existing real pytest junit.xml in artifacts/release_c9 or artifacts/final_run
        for candidate_junit in (
            repo_root / "artifacts" / "release_c9" / "junit.xml",
            repo_root / "artifacts" / "final_run" / "junit.xml",
            repo_root / "artifacts" / "baseline_c0_audited" / "junit.xml",
        ):
            if candidate_junit.exists() and candidate_junit.stat().st_size > 500:
                shutil.copy2(candidate_junit, junit_target)
                break

    if remote_parity_data is not None:
        parity_path = bundle_dir / "remote_parity.json"
        parity_path.write_text(json.dumps(remote_parity_data, indent=2, sort_keys=True), encoding="utf-8")

    _, _, junit_summary = parse_and_verify_junit_xml(junit_target, repo_root=repo_root)

    tracked_filenames = ["decisions.json", "replay_timeline.json", "metrics_summary.json"]
    if junit_target.exists():
        tracked_filenames.append("junit.xml")
    if (bundle_dir / "remote_parity.json").exists():
        tracked_filenames.append("remote_parity.json")

    files_manifest: Dict[str, Dict[str, Any]] = {}
    for fname in tracked_filenames:
        fpath = bundle_dir / fname
        files_manifest[fname] = {
            "sha256": _sha256_file(fpath),
            "size_bytes": fpath.stat().st_size,
        }

    manifest_payload = {
        "schema_version": "1.0.0",
        "run_id": run_id,
        "generated_at_utc": now_utc,
        "git_commit": git_commit,
        "environment": campaign_result["runtime_telemetry"],
        "semantic_sha256": campaign_result["campaign_semantic_sha256"],
        "files": files_manifest,
        "metrics": recomputed_metrics,
        "junit_summary": junit_summary,
    }
    manifest_path = bundle_dir / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest_payload, indent=2, sort_keys=True), encoding="utf-8")

    return bundle_dir


def verify_evidence_bundle(bundle_dir: Path) -> Tuple[bool, List[str], Dict[str, Any]]:
    """Fail-closed verifier of an evidence bundle (EVD-01..04, H05)."""
    errors: List[str] = []
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        return False, [f"Bundle directory does not exist: {bundle_dir}"], {}

    manifest_path = bundle_dir / "evidence_manifest.json"
    if not manifest_path.exists():
        return False, ["Missing evidence_manifest.json"], {}
    if manifest_path.stat().st_size == 0:
        return False, ["Zero-byte evidence_manifest.json"], {}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, [f"Truncated or invalid JSON in evidence_manifest.json: {exc}"], {}

    if not isinstance(manifest, dict):
        return False, ["evidence_manifest.json root must be a JSON object"], {}

    required_manifest_keys = {
        "schema_version",
        "run_id",
        "generated_at_utc",
        "git_commit",
        "environment",
        "semantic_sha256",
        "files",
        "metrics",
        "junit_summary",
    }
    for req_k in sorted(required_manifest_keys):
        if req_k not in manifest:
            errors.append(f"SCHEMA_VIOLATION: Missing required key '{req_k}' in evidence_manifest.json")

    if errors:
        return False, errors, {}

    # Check ISO-8601 timestamp validity and non-staleness (must be >= year 2026)
    ts_str = str(manifest.get("generated_at_utc", ""))
    try:
        dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if dt.year < 2026:
            errors.append(f"STALE_TIMESTAMP: generated_at_utc '{ts_str}' precedes 2026 event window")
    except Exception as exc:
        errors.append(f"INVALID_TIMESTAMP: '{ts_str}': {exc}")

    # Check non-finite values in manifest
    errors.extend(_scan_for_non_finite(manifest, "evidence_manifest"))

    # Verify all listed files exist, are non-empty, and match recorded SHA-256 and size
    files_map = manifest.get("files", {})
    if not isinstance(files_map, dict) or not files_map:
        errors.append("SCHEMA_VIOLATION: 'files' in evidence_manifest.json must be a non-empty object")
        return False, errors, {}

    for req_file in ("decisions.json", "replay_timeline.json", "metrics_summary.json", "junit.xml"):
        if req_file not in files_map:
            errors.append(f"MISSING_REQUIRED_ARTIFACT: '{req_file}' not listed in evidence_manifest.json")

    loaded_json_files: Dict[str, Any] = {}
    for fname, fmeta in sorted(files_map.items()):
        fpath = bundle_dir / fname
        if not fpath.exists():
            errors.append(f"MISSING_FILE: '{fname}' does not exist in bundle")
            continue
        actual_size = fpath.stat().st_size
        if actual_size == 0:
            errors.append(f"ZERO_BYTE_FILE: '{fname}' is 0 bytes")
            continue
        actual_sha = _sha256_file(fpath)
        expected_sha = fmeta.get("sha256")
        expected_size = fmeta.get("size_bytes")
        if actual_sha != expected_sha:
            errors.append(f"SHA256_MISMATCH on '{fname}': actual={actual_sha} != manifest={expected_sha}")
        if actual_size != expected_size:
            errors.append(f"SIZE_MISMATCH on '{fname}': actual={actual_size} != manifest={expected_size}")

        if fname.endswith(".json"):
            try:
                parsed = json.loads(fpath.read_text(encoding="utf-8"))
                loaded_json_files[fname] = parsed
                errors.extend(_scan_for_non_finite(parsed, fname))
            except Exception as exc:
                errors.append(f"TRUNCATED_OR_CORRUPT_JSON in '{fname}': {exc}")

    if errors:
        return False, errors, {}

    # Cross-check run_id consistency across files
    manifest_run_id = manifest["run_id"]
    for fname in ("decisions.json", "replay_timeline.json", "metrics_summary.json"):
        doc = loaded_json_files[fname]
        if doc.get("run_id") != manifest_run_id:
            errors.append(
                f"INCONSISTENT_RUN_ID in '{fname}': file run_id='{doc.get('run_id')}' != manifest run_id='{manifest_run_id}'"
            )

    # Check for negative latencies or negative counts in metrics_summary.json and manifest
    metrics_doc = loaded_json_files["metrics_summary.json"]
    recorded_metrics = metrics_doc.get("metrics", {})
    for m_key, m_val in recorded_metrics.items():
        if isinstance(m_val, (int, float)) and m_val < 0:
            errors.append(f"NEGATIVE_METRIC: '{m_key}' has negative value {m_val}")

    for m_key, m_val in manifest["metrics"].items():
        if isinstance(m_val, (int, float)) and m_val < 0:
            errors.append(f"NEGATIVE_METRIC in manifest: '{m_key}' has negative value {m_val}")

    # Recompute metrics from raw decisions.json and replay_timeline.json and compare against metrics_summary.json & manifest
    try:
        recomputed = _recompute_metrics_from_raw(
            loaded_json_files["decisions.json"],
            loaded_json_files["replay_timeline.json"],
        )
        for k, exact_val in recomputed.items():
            if recorded_metrics.get(k) != exact_val:
                errors.append(
                    f"METRIC_TAMPERING in metrics_summary.json['{k}']: recorded={recorded_metrics.get(k)!r} != recomputed={exact_val!r}"
                )
            if manifest["metrics"].get(k) != exact_val:
                errors.append(
                    f"METRIC_TAMPERING in evidence_manifest.json['{k}']: recorded={manifest['metrics'].get(k)!r} != recomputed={exact_val!r}"
                )
    except Exception as exc:
        errors.append(f"METRIC_RECOMPUTATION_FAILURE: {exc}")

    # Independent semantic verification of decisions, timeline, budgets, deadlines, and protected loads (Phase C6)
    try:
        sem_errs = _verify_independent_semantic_invariants(
            loaded_json_files["decisions.json"],
            loaded_json_files["replay_timeline.json"],
            manifest,
        )
        errors.extend(sem_errs)
    except Exception as exc:
        errors.append(f"SEMANTIC_VERIFICATION_FAILURE: {exc}")

    # Verify JUnit XML content and cross-check summary counts
    junit_ok, junit_errs, junit_actual = parse_and_verify_junit_xml(bundle_dir / "junit.xml")
    if not junit_ok:
        errors.extend([f"JUNIT_FAILURE: {e}" for e in junit_errs])
    else:
        manifest_junit = manifest.get("junit_summary", {})
        if manifest_junit.get("tests") != junit_actual.get("tests"):
            errors.append(
                f"JUNIT_SUMMARY_MISMATCH: manifest tests={manifest_junit.get('tests')} != xml tests={junit_actual.get('tests')}"
            )

    return len(errors) == 0, errors, {
        "run_id": manifest_run_id,
        "files_verified": len(files_map),
        "junit_summary": junit_actual,
    }


def _rehash_file_in_manifest(bundle_dir: Path, filename: str) -> None:
    """Helper used only by adversarial mutation tests to isolate content/logic checks from SHA-256 checks."""
    manifest_path = bundle_dir / "evidence_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fpath = bundle_dir / filename
    manifest["files"][filename] = {
        "sha256": _sha256_file(fpath),
        "size_bytes": fpath.stat().st_size,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")


def _sync_metrics_and_hashes_for_mutation(mut_dir: Path) -> None:
    """Recompute metrics_summary and file hashes so only the semantic/physical invariant is tested."""
    dec_doc = json.loads((mut_dir / "decisions.json").read_text(encoding="utf-8"))
    tl_doc = json.loads((mut_dir / "replay_timeline.json").read_text(encoding="utf-8"))
    met_doc = json.loads((mut_dir / "metrics_summary.json").read_text(encoding="utf-8"))
    man_doc = json.loads((mut_dir / "evidence_manifest.json").read_text(encoding="utf-8"))

    recomp = _recompute_metrics_from_raw(dec_doc, tl_doc)
    met_doc["metrics"] = recomp
    man_doc["metrics"] = recomp
    (mut_dir / "metrics_summary.json").write_text(json.dumps(met_doc, indent=2, sort_keys=True), encoding="utf-8")
    (mut_dir / "evidence_manifest.json").write_text(json.dumps(man_doc, indent=2, sort_keys=True), encoding="utf-8")
    for fn in ("decisions.json", "replay_timeline.json", "metrics_summary.json"):
        _rehash_file_in_manifest(mut_dir, fn)


def run_adversarial_mutation_suite(valid_bundle_dir: Path, scratch_root: Path) -> Dict[str, Any]:
    """Run all 20 adversarial mutation classes (11 structural + 9 semantic/JUnit C6 mutations) against `verify_evidence_bundle`."""
    if scratch_root.exists():
        shutil.rmtree(scratch_root)
    scratch_root.mkdir(parents=True, exist_ok=True)

    mutations = [
        "truncated_json",
        "zero_byte_file",
        "metric_tampering",
        "schema_violation_missing_key",
        "nan_metric",
        "negative_latency",
        "inconsistent_run_id",
        "junit_zero_tests",
        "junit_hidden_failure",
        "stale_timestamp",
        "sha256_mismatch",
        "junit_skipped_test",
        "junit_duplicate_testcase",
        "junit_foreign_missing_criteria",
        "infeasible_snapshot_labeled_yes",
        "non_recomputable_semantic_hash",
        "late_touchdown_exceeding_deadline",
        "protected_load_shed_violation",
        "omitted_timeline_tick_with_updated_summary",
        "exceeded_deadline_time",
    ]
    results: List[Dict[str, Any]] = []
    false_accepts = 0

    for mut in mutations:
        mut_dir = scratch_root / mut
        shutil.copytree(valid_bundle_dir, mut_dir)

        if mut == "truncated_json":
            p = mut_dir / "decisions.json"
            p.write_text('{"run_id": "broken', encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "decisions.json")
        elif mut == "zero_byte_file":
            p = mut_dir / "metrics_summary.json"
            p.write_text("", encoding="utf-8")
        elif mut == "metric_tampering":
            p = mut_dir / "metrics_summary.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["metrics"]["total_decisions_count"] = 999999
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "metrics_summary.json")
        elif mut == "schema_violation_missing_key":
            p = mut_dir / "evidence_manifest.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d.pop("semantic_sha256", None)
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        elif mut == "nan_metric":
            p = mut_dir / "metrics_summary.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["metrics"]["max_tick_calc_ms"] = "NaN"
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "metrics_summary.json")
        elif mut == "negative_latency":
            p = mut_dir / "metrics_summary.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["metrics"]["min_tick_calc_ms"] = -5.25
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "metrics_summary.json")
        elif mut == "inconsistent_run_id":
            p = mut_dir / "decisions.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["run_id"] = "DIFFERENT_FAKE_RUN_ID"
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "decisions.json")
        elif mut == "junit_zero_tests":
            p = mut_dir / "junit.xml"
            p.write_text(
                '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="empty" tests="0" errors="0" failures="0"/></testsuites>',
                encoding="utf-8",
            )
            _rehash_file_in_manifest(mut_dir, "junit.xml")
        elif mut == "junit_hidden_failure":
            p = mut_dir / "junit.xml"
            p.write_text(
                '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="hidden" tests="1" errors="0" failures="0">'
                '<testcase classname="x" name="y"><failure message="boom"/></testcase></testsuite></testsuites>',
                encoding="utf-8",
            )
            _rehash_file_in_manifest(mut_dir, "junit.xml")
        elif mut == "stale_timestamp":
            p = mut_dir / "evidence_manifest.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["generated_at_utc"] = "1999-01-01T00:00:00Z"
            p.write_text(json.dumps(d, indent=2), encoding="utf-8")
        elif mut == "sha256_mismatch":
            p = mut_dir / "replay_timeline.json"
            p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        elif mut == "junit_skipped_test":
            p = mut_dir / "junit.xml"
            xml_txt = p.read_text(encoding="utf-8")
            xml_txt = xml_txt.replace("<testcase ", "<testcase ><skipped message=\"skipped\"/></testcase><testcase ", 1)
            p.write_text(xml_txt, encoding="utf-8")
            _rehash_file_in_manifest(mut_dir, "junit.xml")
        elif mut == "junit_duplicate_testcase":
            p = mut_dir / "junit.xml"
            tree = ET.parse(p)
            root = tree.getroot()
            suite = root if root.tag == "testsuite" else root.find(".//testsuite")
            first_tc = root.find(".//testcase")
            if suite is not None and first_tc is not None:
                suite.append(copy.deepcopy(first_tc))
                suite.attrib["tests"] = str(int(suite.attrib.get("tests", "0")) + 1)
            tree.write(p, encoding="utf-8", xml_declaration=True)
            _rehash_file_in_manifest(mut_dir, "junit.xml")
        elif mut == "junit_foreign_missing_criteria":
            p = mut_dir / "junit.xml"
            p.write_text(
                '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="foreign" tests="1" errors="0" failures="0" skipped="0">'
                '<testcase classname="other_pkg" name="test_unrelated" time="0.01"/></testsuite></testsuites>',
                encoding="utf-8",
            )
            _rehash_file_in_manifest(mut_dir, "junit.xml")
        elif mut == "infeasible_snapshot_labeled_yes":
            p = mut_dir / "replay_timeline.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["survey_primary"]["timeline_ticks"][10]["power_actual_w"] = 250000
            d["survey_primary"]["timeline_ticks"][10]["feasible"] = True
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)
        elif mut == "non_recomputable_semantic_hash":
            p = mut_dir / "replay_timeline.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["survey_primary"]["semantic_sha256"] = "f" * 64
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)
        elif mut == "late_touchdown_exceeding_deadline":
            p = mut_dir / "replay_timeline.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["survey_primary"]["executed_touchdown_time_s"] = 1600
            for dec_item in d["survey_primary"]["decisions"]:
                if dec_item.get("phase") == "D3_ARBITRATION":
                    dec_item["decision"]["touchdown_time_s"] = 1600
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)
        elif mut == "protected_load_shed_violation":
            p = mut_dir / "replay_timeline.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            for dec_item in d["survey_primary"]["decisions"]:
                if dec_item.get("phase") == "D4_DEGRADATION":
                    dec_item["decision"]["actions_taken"].append({
                        "action_type": "SHED_LOAD_POWER_OFF",
                        "guest_id": "G3",
                        "from_mode": "RECHARGE_CRADLE",
                    })
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)
        elif mut == "omitted_timeline_tick_with_updated_summary":
            p = mut_dir / "replay_timeline.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["survey_primary"]["timeline_ticks"].pop(30)
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)
        elif mut == "exceeded_deadline_time":
            p = mut_dir / "decisions.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            d["scenario_A"]["g6_admission"]["calculation_elapsed_ms"] = 45000.0
            p.write_text(json.dumps(d, indent=2, sort_keys=True), encoding="utf-8")
            _sync_metrics_and_hashes_for_mutation(mut_dir)

        ok, errs, _ = verify_evidence_bundle(mut_dir)
        if ok:
            false_accepts += 1
        results.append({
            "mutation": mut,
            "rejected_as_expected": not ok,
            "errors_detected": errs,
        })

    shutil.rmtree(scratch_root, ignore_errors=True)
    return {
        "total_mutations": len(mutations),
        "rejected_count": len(mutations) - false_accepts,
        "false_accepts": false_accepts,
        "all_rejected": false_accepts == 0,
        "results": results,
    }

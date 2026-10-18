"""Unit tests for Forecast Advisory and Observer (FC-UNIT-14, 15, 20).

Verifies:
- FC-UNIT-14: Non-canned sentence generator reflecting exact slope, guest, kW, s, and witness.
- FC-UNIT-15: Advisory state machine temporal hysteresis (3s to WARN, 10s to QUIET).
- FC-UNIT-20: AST import verification: only standard library, yaml, and sdv_governor allowed (INV-9).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Set

import pytest

from sdv_governor.forecast.advisory import AdvisoryStateMachine, advisory_sentence
from sdv_governor.forecast.observer import ForecastObserver
from sdv_governor.ship_state import InstalledGuestState, ShipOperationalState

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_fc_unit_14_non_canned_sentence_generator() -> None:
    """FC-UNIT-14: Frase no enlatada (INV-7) | cambiar la pendiente de 25 a 40 W/s cambia el texto; contiene el guest_id, kW, s y el testigo."""
    base_projection = {
        "guest_id": "G5",
        "h_star_s": 38,
        "h_star_band_s": [31, 47],
        "witness": {
            "class": "power",
            "component": "G5 feeder",
            "capacity_w": 49500,
            "projected_w": 49600,
        },
        "declared_w": 45000,
        "threshold_w": 49500,
        "slope_w_per_s": 25.4,
    }

    # Sentence with slope = 25 W/s
    sent_25 = advisory_sentence(base_projection, guest_name="Multibeam sonar", src="telemetry:G5")

    # Sentence with slope = 40 W/s
    proj_40 = dict(base_projection)
    proj_40["slope_w_per_s"] = 40.0
    sent_40 = advisory_sentence(proj_40, guest_name="Multibeam sonar", src="telemetry:G5")

    # 1. Changing slope from 25 to 40 W/s alters the text
    assert sent_25 != sent_40
    assert "+25 W/s" in sent_25
    assert "+40 W/s" in sent_40

    # 2. Contains guest_id, kW figures, seconds, and binding witness
    for s in (sent_25, sent_40):
        assert "G5" in s
        assert "Multibeam sonar" in s
        assert "kW" in s
        assert "49.5 kW" in s
        assert "45.0 kW" in s
        assert "in ~38 s" in s
        assert "31–47 s" in s
        assert "power G5 feeder 49.6/49.5 kW" in s
        assert "Advisory only" in s


def test_fc_unit_15_advisory_state_machine_hysteresis() -> None:
    """FC-UNIT-15: Histéresis WARN | 2 s en riesgo -> no hay WARN; 3 s -> WARN; 9 s fuera -> sigue; 10 s -> QUIET."""
    sm = AdvisoryStateMachine("G5")
    assert sm.state == "QUIET"

    # Step 1: Second 1 in risk (h* = 40s <= 45s) -> NOT WARN (transitions to WATCH)
    s1 = sm.update(t=1, h_star_hi=40.0, status="OK")
    assert s1 != "WARN"
    assert s1 == "WATCH"

    # Step 2: Second 2 in risk -> STILL NOT WARN
    s2 = sm.update(t=2, h_star_hi=40.0, status="OK")
    assert s2 != "WARN"
    assert s2 == "WATCH"
    assert sm.risk_streak_s == 2

    # Step 3: Second 3 in risk -> Transitions to WARN!
    s3 = sm.update(t=3, h_star_hi=40.0, status="OK")
    assert s3 == "WARN"
    assert sm.risk_streak_s == 3

    # Steps 4..12: Clear for 9 consecutive seconds (h* > 60s or None) -> Stays in WARN
    for tick in range(4, 13):
        st = sm.update(t=tick, h_star_hi=75.0, status="OK")
        assert st == "WARN", f"At tick {tick} (clear for {tick-3} s), expected state to remain WARN"

    assert sm.clear_streak_s == 9
    assert sm.state == "WARN"

    # Step 13: 10th consecutive second clear -> Exits to QUIET
    s13 = sm.update(t=13, h_star_hi=75.0, status="OK")
    assert s13 == "QUIET"
    assert sm.state == "QUIET"

    # Verify audit transitions trail
    trans = sm.transitions
    assert len(trans) >= 3
    assert trans[0].from_state == "QUIET" and trans[0].to_state == "WATCH"
    assert trans[1].from_state == "WATCH" and trans[1].to_state == "WARN"
    assert trans[2].from_state == "WARN" and trans[2].to_state == "QUIET"
    for tr in trans:
        assert isinstance(tr.to_dict(), dict)
        assert tr.t > 0
        assert tr.cause


def test_fc_unit_15_telemetry_suppression_and_reactive_breach() -> None:
    """FC-UNIT-15 bonus: Test SUPPRESSED and BREACH_OBSERVED state transitions."""
    sm = AdvisoryStateMachine("G4")

    # WARMUP suppresses
    assert sm.update(t=1, h_star_hi=30.0, status="WARMUP") == "SUPPRESSED"
    # Returns to QUIET when OK
    assert sm.update(t=2, h_star_hi=90.0, status="OK") == "QUIET"

    # STALE suppresses
    assert sm.update(t=3, h_star_hi=30.0, status="STALE") == "SUPPRESSED"
    assert sm.update(t=4, h_star_hi=90.0, status="OK") == "QUIET"

    # Reactive breach overrides
    assert sm.update(t=5, h_star_hi=30.0, status="OK", reactive_breach_active=True) == "BREACH_OBSERVED"
    # When resolved, exits
    assert sm.update(t=6, h_star_hi=90.0, status="OK", reactive_breach_active=False) == "QUIET"


def test_fc_unit_20_only_stdlib_yaml_and_package_imports() -> None:
    """FC-UNIT-20: Solo stdlib (INV-9) | análisis AST de forecast/*.py, envdata.py y env_windows.py: imports ⊂ {stdlib, yaml, sdv_governor}."""
    stdlib_modules: Set[str] = set(sys.builtin_module_names)
    if hasattr(sys, "stdlib_module_names"):
        stdlib_modules.update(sys.stdlib_module_names)

    # Standard python stdlib modules that might not be in builtin_module_names
    stdlib_modules.update({
        "typing", "dataclasses", "pathlib", "math", "time", "copy", "json",
        "hashlib", "os", "sys", "ast", "re", "collections", "itertools",
        "functools", "enum", "abc", "contextlib", "datetime", "warnings",
        "unittest", "io", "string", "types",
    })

    allowed_external_roots: Set[str] = {"yaml", "sdv_governor"}

    # Target directory for forecast
    src_dir = REPO_ROOT / "src" / "sdv_governor"
    forecast_dir = src_dir / "forecast"

    target_files = list(forecast_dir.glob("*.py"))
    # Also include envdata.py and env_windows.py if present
    for extra in ("envdata.py", "env_windows.py"):
        extra_path = src_dir / extra
        if extra_path.is_file():
            target_files.append(extra_path)

    assert len(target_files) >= 4, f"Expected at least 4 forecast files, found {len(target_files)}"

    for fpath in target_files:
        content = fpath.read_text(encoding="utf-8")
        tree = ast.parse(content, filename=str(fpath))

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root_pkg = alias.name.split(".")[0]
                    assert (
                        root_pkg in stdlib_modules or root_pkg in allowed_external_roots
                    ), f"Disallowed import '{alias.name}' in {fpath.name} (root: {root_pkg})"

            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # Relative import within package -> allowed
                    continue
                if node.module:
                    root_pkg = node.module.split(".")[0]
                    assert (
                        root_pkg in stdlib_modules or root_pkg in allowed_external_roots
                    ), f"Disallowed from-import '{node.module}' in {fpath.name} (root: {root_pkg})"


def test_forecast_observer_export_and_non_mutation() -> None:
    """Test ForecastObserver on_sample, on_tick, and export separation."""
    from dataclasses import asdict
    from sdv_governor.budgets import evaluate_eight_class_budgets
    from sdv_governor.manifest import load_guest_catalog
    from sdv_governor.replay import load_yaml_file

    catalog = load_guest_catalog(REPO_ROOT / "data" / "guests")
    ship = load_yaml_file(REPO_ROOT / "data" / "ship" / "vessel_baseline.yaml")

    observer = ForecastObserver(ship_profile=ship, catalog=catalog)

    state = ShipOperationalState(
        sim_time_s=10,
        vessel_mode="TRANSIT",
        power_reserve_w=50000,
        seawater_temp_c=16.0,
    )
    state.installed_guests["G5"] = InstalledGuestState(
        guest_id="G5",
        active_mode="FULL_SPECTRUM",
        placements={"G5_COMPUTE_CONTAINER": "bay2", "G5_MAST_ANTENNA": "hangar_roof"},
        compute_node="node_1",
        powered_on=True,
    )

    before_dump = asdict(state.clone())

    # Feed 25 samples of telemetry
    for t in range(1, 26):
        telemetry = {"G5": 45000.0 + (t * 250.0)}
        observer.on_sample(sim_time_s=t, telemetry_readings=telemetry, state_view=state)

    after_dump = asdict(state.clone())
    assert before_dump == after_dump, "on_sample must NOT mutate state_view!"

    # Tick at t=25
    budgets_snap = evaluate_eight_class_budgets(ship, catalog, state)
    observer.on_tick(sim_time_s=25, state_clone=state.clone(), budgets_snapshot=budgets_snap)

    exp = observer.export()
    assert "semantic" in exp
    assert "perf" in exp

    sem = exp["semantic"]
    assert sem["enabled"] is True
    assert sem["params_sha256"]
    assert "G5" in sem["status_by_guest"]
    assert isinstance(sem["alerts"], list)
    assert isinstance(sem["transitions"], list)

    fcs = sem["forecast_by_guest"].get("G5")
    if fcs:
        for h_str, val in fcs.items():
            assert isinstance(val, int), f"Forecast for {h_str} should be integer Watt, got {type(val)}"

    perf = exp["perf"]
    assert "p50_ms" in perf
    assert "p99_ms" in perf
    assert perf["total_calc_ms"] >= 0.0

    fv = observer.forecast_view()
    assert "enabled" in fv
    assert "series" in fv
    assert "G5" in fv["series"]
    assert len(fv["series"]["G5"]["t"]) > 0


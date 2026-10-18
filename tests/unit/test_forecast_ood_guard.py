"""OOD guard for the forecast advisory (fix for gate G-OOD).

- OOD-01: guard hysteresis (enter >= 1.9x reference residual sigma, leave < 1.6x) and band scale.
- OOD-02: while OOD, widened-band-only risk stays at WATCH; WARN needs gated point risk for 6 s.
- OOD-03: outside OOD the original 3 s rule is unchanged.
- OOD-04: advisory sentence discloses OOD mode and the widening factor.
- OOD-05: constants re-derived from the calibration split (no tuning on the benchmark 'ood' split).
- OOD-06: production observer on the first OOD block: no WARN on non-breaching episodes, ramps warned >= 10 s ahead.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from sdv_governor.forecast.advisory import AdvisoryStateMachine, advisory_sentence
from sdv_governor.forecast.holt import holt_update
from sdv_governor.forecast.ood import (
    OOD_RATIO_OFF,
    OOD_RATIO_ON,
    OOD_SIGMA_REF,
    OOD_WARN_PERSIST_S,
    OodGuard,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _var(ratio: float) -> float:
    return (ratio * OOD_SIGMA_REF) ** 2


def test_ood_01_guard_hysteresis_and_band_scale() -> None:
    g = OodGuard()
    assert g.update(_var(1.0)) is False and g.band_scale == 1.0
    assert g.update(_var(OOD_RATIO_ON - 0.05)) is False
    assert g.update(_var(OOD_RATIO_ON + 0.05)) is True
    assert g.update(_var(OOD_RATIO_OFF + 0.05)) is True  # hysteresis band: stays OOD
    assert g.band_scale == pytest.approx(OOD_RATIO_OFF + 0.05, rel=1e-6)
    assert g.update(_var(OOD_RATIO_OFF - 0.05)) is False
    assert g.band_scale == 1.0


def _p(h_hi: float, point_crosses: bool, h_point: float) -> dict:
    return {"h_star_band_s": [h_hi, 60], "h_star_s": h_point, "point_forecast_crosses": point_crosses}


def test_ood_02_band_only_risk_is_capped_at_watch_and_point_risk_needs_persistence() -> None:
    m = AdvisoryStateMachine("G5")
    for t in range(30):
        m.update(t, p=_p(1, False, 60), ood_active=True)
    assert m.state == "WATCH"
    assert all(tr.to_state != "WARN" for tr in m.transitions)

    m = AdvisoryStateMachine("G5")
    states = [m.update(100 + i, p=_p(1, True, 30), ood_active=True) for i in range(OOD_WARN_PERSIST_S)]
    assert states[OOD_WARN_PERSIST_S - 2] != "WARN"
    assert states[-1] == "WARN"
    assert m.transitions[-1].cause == f"OOD_POINT_RISK_LE_45S_FOR_{OOD_WARN_PERSIST_S}S"


def test_ood_03_in_distribution_rule_unchanged() -> None:
    m = AdvisoryStateMachine("G5")
    states = [m.update(t, p=_p(30, False, 60)) for t in range(3)]
    assert states == ["WATCH", "WATCH", "WARN"]
    assert m.transitions[-1].cause == "RISK_LE_45S_FOR_3S"


def test_ood_04_sentence_discloses_ood_mode() -> None:
    p = {
        "guest_id": "G5", "h_star_s": 30, "h_star_band_s": [5, 40], "point_forecast_crosses": True,
        "witness": {"class": "power", "component": "G5 feeder", "capacity_w": 49500, "projected_w": 49600},
        "declared_w": 45000, "threshold_w": 49500, "slope_w_per_s": 20.0,
    }
    assert "Unfamiliar telemetry" not in advisory_sentence(p, "G5")
    s = advisory_sentence(dict(p, ood_ratio=3.04), "G5")
    assert "Unfamiliar telemetry (residual noise x3.0 calibration)" in s
    assert s.endswith("Advisory only — governor action unchanged.")


def _sigmas(split: str, kinds: tuple) -> list:
    from sdv_governor.forecast.observer import _load_params
    from sdv_governor.forecast.telemetry import get_dataset_split
    from sdv_governor.manifest import load_guest_catalog

    data = REPO_ROOT / "data"
    cat = load_guest_catalog(data / "guests", errata_path=data / "overrides" / "brief_errata.yaml")
    params, _ = _load_params()
    out = []
    for ep in get_dataset_split(split, cat):
        if ep["meta"]["profile_kind"] not in kinds:
            continue
        st = None
        for i, t in enumerate(ep["t"]):
            y = ep["y_obs"][i]
            d = float(ep["declared_w"][i])
            st = holt_update(st=st, t=int(t), z=(y / d if y is not None else None), mode=ep["mode"][i], params=params)
            if st.status == "OK":
                out.append(math.sqrt(st.resid_ewm_var))
    return sorted(out)


def test_ood_05_constants_rederived_from_calibration_split() -> None:
    noisy = ("steady", "ramp", "lag", "dropout", "mode_change")
    calib = _sigmas("calib", noisy)
    median = calib[len(calib) // 2]
    p99 = calib[int(len(calib) * 0.99)]
    assert median == pytest.approx(OOD_SIGMA_REF, rel=0.05)
    # Calibration-like telemetry essentially never enters OOD mode.
    assert p99 < OOD_RATIO_OFF * OOD_SIGMA_REF < OOD_RATIO_ON * OOD_SIGMA_REF


def test_ood_06_production_observer_on_first_ood_block() -> None:
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    import bench_forecast as bench
    from sdv_governor.forecast.telemetry import get_dataset_split
    from sdv_governor.manifest import load_guest_catalog, validate_ship_profile
    from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file

    data = REPO_ROOT / "data"
    cat = load_guest_catalog(data / "guests", errata_path=data / "overrides" / "brief_errata.yaml")
    ship = validate_ship_profile(load_yaml_file(data / "ship" / "vessel_baseline.yaml"))
    base = build_initial_state_from_scenario(
        load_yaml_file(data / "scenarios" / "scenario_B_arbitration_degradation.yaml"), cat
    )
    harness = bench.ProductionObserverHarness(ship, cat, base, bench.load_params_with_fallback(data / "forecast" / "holt_params.yaml"))
    false_warns, ramp_hits, ramps = 0, 0, 0
    for ep in get_dataset_split("ood", cat)[:10]:
        r = harness.run_episode(ep)
        tc = ep.get("truth_cross_s")
        kind = ep["meta"]["profile_kind"]
        if tc is None:
            false_warns += len(r["warn_entries"])
        elif kind in ("ramp", "lag"):
            ramps += 1
            pre = [w for w in r["warn_entries"] if w <= tc]
            if pre and r["state_by_t"][tc] == "WARN" and tc - pre[-1] >= 10:
                ramp_hits += 1
    assert false_warns == 0
    assert ramps >= 3 and ramp_hits >= ramps - 1

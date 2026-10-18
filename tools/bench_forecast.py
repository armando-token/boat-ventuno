#!/usr/bin/env python3
"""Benchmark and baseline comparison tool for E2 (Predictive Consumption Observer).

Evaluates baseline forecasters (B0 Persistence, B1 Declared, B2 OLS30) against
the proposed damped Holt-Winters forecaster (M1 Holt Damped) on synthetic telemetry
splits ('test', 'ood', 'train', 'calib', 'control_steady_1h') per §6.10, §10, and F11
of MEGAPLAN_FORECAST_Y_DATOS_PUBLICOS_GOLDEN_GATE.md.

Only Python standard library (+ optional PyYAML for config loading) is used.
No numpy, scipy, or scikit-learn.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
from pathlib import Path
import random
import statistics
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Ensure src is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

from sdv_governor.forecast.telemetry import (
    SPLIT_SEEDS,
    TelemetryProfile,
    compute_truth_cross_s,
    generate_episode,
    get_dataset_split,
)
from sdv_governor.forecast.holt import (
    HoltParams,
    HoltState,
    create_initial_holt_state,
    holt_forecast,
    holt_update,
)
from sdv_governor.forecast.conformal import compute_conformal_band
from sdv_governor.manifest import load_guest_catalog


# ---------------------------------------------------------------------------
# Baseline Implementations per §6.10
# ---------------------------------------------------------------------------

class BaselinePersistence:
    """B0 persistence: y_hat_{t+h} = y_t (last valid observation)."""

    def __init__(self, horizons: Sequence[int]):
        self.horizons = tuple(horizons)
        self.last_valid: Optional[float] = None

    def reset(self) -> None:
        self.last_valid = None

    def update(self, t: int, y: Optional[float], mode: str, declared_w: float) -> None:
        if y is not None and math.isfinite(y) and y >= 0:
            self.last_valid = float(y)

    def forecast(self, t: int, declared_w: float) -> Dict[int, float]:
        val = self.last_valid if self.last_valid is not None else declared_w
        return {h: val for h in self.horizons}


class BaselineDeclared:
    """B1 declared: y_hat_{t+h} = declared_w (what current system does)."""

    def __init__(self, horizons: Sequence[int]):
        self.horizons = tuple(horizons)

    def reset(self) -> None:
        pass

    def update(self, t: int, y: Optional[float], mode: str, declared_w: float) -> None:
        pass

    def forecast(self, t: int, declared_w: float) -> Dict[int, float]:
        return {h: declared_w for h in self.horizons}


class BaselineOLS30:
    """B2 ols30: ordinary least squares linear regression over last 30s."""

    def __init__(self, horizons: Sequence[int], window_s: int = 30):
        self.horizons = tuple(horizons)
        self.window_s = window_s
        self.history: List[Tuple[int, float]] = []

    def reset(self) -> None:
        self.history.clear()

    def update(self, t: int, y: Optional[float], mode: str, declared_w: float) -> None:
        if y is not None and math.isfinite(y) and y >= 0:
            self.history.append((t, float(y)))
        # Prune to window [t - window_s + 1, t]
        cutoff = t - self.window_s + 1
        self.history = [(st, sy) for st, sy in self.history if st >= cutoff]

    def forecast(self, t: int, declared_w: float) -> Dict[int, float]:
        if len(self.history) < 2:
            fallback = self.history[-1][1] if self.history else declared_w
            return {h: fallback for h in self.horizons}

        n = len(self.history)
        t_mean = sum(st for st, _ in self.history) / n
        y_mean = sum(sy for _, sy in self.history) / n
        s_tt = sum((st - t_mean) ** 2 for st, _ in self.history)
        s_ty = sum((st - t_mean) * (sy - y_mean) for st, sy in self.history)

        if s_tt < 1e-9:
            slope = 0.0
            intercept = y_mean
        else:
            slope = s_ty / s_tt
            intercept = y_mean - slope * t_mean

        return {h: max(0.0, intercept + slope * (t + h)) for h in self.horizons}


class ModelHoltDamped:
    """M1 holt_damped: normalized damped Holt with conformal prediction bands."""

    def __init__(self, params: HoltParams, horizons: Sequence[int]):
        self.params = params
        self.horizons = tuple(horizons)
        self.state: Optional[HoltState] = None

    def reset(self) -> None:
        self.state = None

    def update(self, t: int, y: Optional[float], mode: str, declared_w: float) -> None:
        z = (float(y) / declared_w) if (y is not None and declared_w > 0) else None
        self.state = holt_update(self.state, t=t, z=z, mode=mode, params=self.params)

    def forecast(self, t: int, declared_w: float) -> Optional[Dict[int, float]]:
        if self.state is None:
            return None
        return holt_forecast(self.state, horizons=self.horizons, params=self.params, declared_w=declared_w)

    def forecast_bands(self, t: int, declared_w: float) -> Optional[Dict[int, Tuple[float, float]]]:
        if self.state is None:
            return None
        fc = holt_forecast(self.state, horizons=self.horizons, params=self.params, declared_w=declared_w)
        if fc is None:
            return None
        bands: Dict[int, Tuple[float, float]] = {}
        for h, y_hat in fc.items():
            q_lo = self.params.conformal_q_lo.get(h, -self.params.conformal_q.get(h, 0.05))
            q_hi = self.params.conformal_q_hi.get(h, self.params.conformal_q.get(h, 0.05))
            bands[h] = compute_conformal_band(
                y_hat=y_hat,
                declared_w=declared_w,
                q_lo=q_lo,
                q_hi=q_hi,
                status=self.state.status,
            )
        return bands


# ---------------------------------------------------------------------------
# Alarm Simulation & Evaluator per §6.7 & §10.2
# ---------------------------------------------------------------------------

class ProductionObserverHarness:
    """Drives the production ForecastObserver (Holt + conformal + physical projector + advisory
    state machine) exactly as replay.py does: on_sample(readings) then on_tick(state.clone(), budgets).

    No benchmark-only alarm logic: WARN entries are read from the production AdvisoryStateMachine.
    The reactive governor input is not fed (the ground-truth crossing is the event), so lead time is
    truth_cross_s - t(WARN). When an episode declares a power that differs from the card (card_modified,
    mode_change), the episode runs against a catalog copy with that declared value, i.e. the card the
    operator would have reloaded.
    """

    def __init__(self, ship_profile: Dict[str, Any], catalog: Dict[str, Any], base_state: Any, params: HoltParams):
        self.ship_profile = ship_profile
        self.catalog = catalog
        self.base_state = base_state
        self.params = params

    def _episode_catalog(self, guest_id: str, mode: str, declared_w: float, cache: Dict[Any, Any]) -> Dict[str, Any]:
        key = (guest_id, mode, int(round(declared_w)))
        if key in cache:
            return cache[key]
        cat = dict(self.catalog)
        manifest = cat[guest_id]
        spec = manifest.modes[mode]
        if abs(float(spec.nominal_power_w) - declared_w) > 0.5:
            modes = dict(manifest.modes)
            modes[mode] = dataclasses.replace(spec, nominal_power_w=int(round(declared_w)))
            cat[guest_id] = dataclasses.replace(manifest, modes=modes)
        cache[key] = cat
        return cat

    def run_episode(self, ep: Dict[str, Any]) -> Dict[str, Any]:
        from sdv_governor.budgets import evaluate_eight_class_budgets
        from sdv_governor.forecast.observer import ForecastObserver

        meta = ep.get("meta", {})
        gid = ep.get("guest_id") or meta.get("guest_id", "G5")
        state = self.base_state.clone()
        if gid not in state.installed_guests:
            raise ValueError(f"guest {gid} not installed in base state")
        state.installed_guests[gid].powered_on = True

        observer = ForecastObserver(ship_profile=self.ship_profile, catalog=self.catalog, params=self.params)
        cat_cache: Dict[Any, Any] = {}
        budget_cache: Dict[Any, Dict[str, Any]] = {}
        state_by_t: List[str] = []
        tick_ms: List[float] = []
        evals: List[int] = []

        for idx, t in enumerate(ep["t"]):
            mode = ep["mode"][idx]
            declared_w = float(ep["declared_w"][idx])
            cat = self._episode_catalog(gid, mode, declared_w, cat_cache)
            observer.catalog = cat
            ginst = state.installed_guests[gid]
            ginst.active_mode = mode
            state.sim_time_s = int(t)

            readings: Dict[str, Any] = {}
            for ogid, oinst in state.installed_guests.items():
                if oinst.powered_on and ogid in cat and oinst.active_mode in cat[ogid].modes:
                    readings[ogid] = float(cat[ogid].modes[oinst.active_mode].nominal_power_w)
            y = ep["y_obs"][idx]
            readings[gid] = float(y) if y is not None else None

            bkey = (mode, int(round(declared_w)))
            if bkey not in budget_cache:
                budget_cache[bkey] = evaluate_eight_class_budgets(self.ship_profile, cat, state)
            snap = budget_cache[bkey]

            t0 = time.perf_counter()
            observer.on_sample(int(t), readings, state_view=state)
            observer.on_tick(int(t), state.clone(), snap)
            tick_ms.append((time.perf_counter() - t0) * 1000.0)
            evals.append(observer.perf_records[-1]["evals_used"])
            m = observer.advisory_machines.get(gid)
            state_by_t.append(m.state if m else "QUIET")

        machine = observer.advisory_machines.get(gid)
        warn_entries = [tr.t for tr in (machine.transitions if machine else []) if tr.to_state == "WARN"]
        return {"warn_entries": warn_entries, "state_by_t": state_by_t, "tick_ms": tick_ms, "evals": evals,
                "alerts": list(observer.alerts_history)}


# ---------------------------------------------------------------------------
# Benchmark Engine
# ---------------------------------------------------------------------------

def percentile(data: Sequence[float], p: float) -> float:
    """Calculate p-th percentile (0 <= p <= 100) using nearest rank."""
    if not data:
        return 0.0
    s = sorted(data)
    n = len(s)
    k = (n - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(s[int(k)])
    d0 = s[f] * (c - k)
    d1 = s[c] * (k - f)
    return float(d0 + d1)


def load_params_with_fallback(params_file: Path) -> HoltParams:
    """Load HoltParams from YAML, with robust default fallback."""
    if params_file.is_file() and yaml is not None:
        try:
            with open(params_file, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return HoltParams.from_dict(data)
        except Exception as e:
            print(f"[WARN] Failed to load {params_file}: {e}. Using defaults.", file=sys.stderr)

    # Defaults per §6.4 and §10.3
    return HoltParams(
        alpha=0.30,
        beta=0.05,
        phi=0.90,
        horizons_s=(10, 20, 30, 45, 60),
        warmup_s=20,
        regime_k=4.0,
        conformal_q={10: 0.021, 20: 0.034, 30: 0.047, 45: 0.066, 60: 0.085},
        conformal_q_hi={10: 0.025, 20: 0.040, 30: 0.055, 45: 0.075, 60: 0.095},
        conformal_q_lo={10: -0.020, 20: -0.030, 30: -0.040, 45: -0.055, 60: -0.070},
    )


def evaluate_split(
    split_name: str,
    episodes: List[Dict[str, Any]],
    params: HoltParams,
    harness: "ProductionObserverHarness",
    horizons: Sequence[int] = (10, 20, 30, 45, 60),
) -> Dict[str, Any]:
    """Execute complete benchmark over a dataset split.
    
    Computes:
    - MAE@h for B0, B1, B2, M1
    - ratio_MAE@30 (overall, ramp_lag, steady_spike)
    - coverage@h for M1
    - recall_alarm, lead_time_s (median, p10), late_alarm_rate
    - false_alarms_per_h
    - p99_on_tick_ms latency
    """
    models = {
        "B0": BaselinePersistence(horizons),
        "B1": BaselineDeclared(horizons),
        "B2": BaselineOLS30(horizons),
        "M1": ModelHoltDamped(params, horizons),
    }

    # Collectors for sample errors: model -> h -> list of normalized errors |y_true - y_hat| / declared
    errors_by_model: Dict[str, Dict[int, List[float]]] = {
        m: {h: [] for h in horizons} for m in models
    }
    # Errors partitioned by profile subgroup for ratio_MAE@30
    ramp_lag_errors_30: Dict[str, List[float]] = {m: [] for m in models}
    steady_spike_errors_30: Dict[str, List[float]] = {m: [] for m in models}

    # Coverage collectors: h -> list of 1.0 (inside) or 0.0 (outside)
    coverage_by_h: Dict[int, List[float]] = {h: [] for h in horizons}

    # Alarm and lead time tracking
    episodes_with_cross = 0
    alarm_hits = 0
    alarm_lates = 0
    lead_times_s: List[float] = []

    breach_episodes_with_cross = 0
    breach_alarm_hits = 0
    breach_alarm_lates = 0
    breach_lead_times_s: List[float] = []

    non_cross_episodes = 0
    total_non_cross_hours = 0.0
    total_false_alarms = 0

    # Latency tracking: full production observer tick (on_sample + on_tick incl. projector), ms
    m1_tick_latencies_ms: List[float] = []
    evals_per_tick: List[int] = []
    warn_cleared_before_cross = 0

    for ep in episodes:
        meta = ep.get("meta", {})
        guest_id = ep.get("guest_id") or meta.get("guest_id", "G5")
        t_list = ep["t"]
        y_obs_list = ep["y_obs"]
        y_true_list = ep["y_true"]
        declared_list = ep["declared_w"]
        mode_list = ep["mode"]
        truth_cross_s = ep.get("truth_cross_s")
        profile_kind = meta.get("profile_kind", "unknown")
        duration_s = len(t_list)

        # Reset models; alarms come from the production observer run of this episode
        for m in models.values():
            m.reset()
        prod = harness.run_episode(ep)
        m1_tick_latencies_ms.extend(prod["tick_ms"])
        evals_per_tick.extend(prod["evals"])

        for t_idx, t in enumerate(t_list):
            y_obs = y_obs_list[t_idx]
            y_true = y_true_list[t_idx]
            declared_w = declared_list[t_idx]
            mode = mode_list[t_idx]

            models["M1"].update(t, y_obs, mode, declared_w)
            fc_m1 = models["M1"].forecast(t, declared_w)
            bands_m1 = models["M1"].forecast_bands(t, declared_w)
            m1_status = models["M1"].state.status if models["M1"].state else "UNAVAILABLE"

            # Update baselines
            models["B0"].update(t, y_obs, mode, declared_w)
            models["B1"].update(t, y_obs, mode, declared_w)
            models["B2"].update(t, y_obs, mode, declared_w)

            # Evaluate forecasts on valid OK samples per §10.2
            if m1_status == "OK" and fc_m1 is not None and bands_m1 is not None:
                fc_b0 = models["B0"].forecast(t, declared_w)
                fc_b1 = models["B1"].forecast(t, declared_w)
                fc_b2 = models["B2"].forecast(t, declared_w)

                all_fc = {"B0": fc_b0, "B1": fc_b1, "B2": fc_b2, "M1": fc_m1}

                for h in horizons:
                    target_t = t + h
                    if target_t < duration_s:
                        actual_y = y_true_list[target_t]
                        for m_name, fc_dict in all_fc.items():
                            pred = fc_dict[h]
                            norm_err = abs(actual_y - pred) / declared_w if declared_w > 0 else 0.0
                            errors_by_model[m_name][h].append(norm_err)

                            if h == 30:
                                if profile_kind in ("ramp", "lag"):
                                    ramp_lag_errors_30[m_name].append(norm_err)
                                elif profile_kind in ("steady", "spike"):
                                    steady_spike_errors_30[m_name].append(norm_err)

                        # Check conformal coverage for M1
                        lo, hi = bands_m1[h]
                        inside = 1.0 if (lo <= actual_y <= hi) else 0.0
                        coverage_by_h[h].append(inside)

        # Post-episode alarm analysis
        is_breach_case = profile_kind in ("ramp", "lag") or (
            truth_cross_s is not None and profile_kind != "card_modified"
        )
        warn_entries = prod["warn_entries"]
        if truth_cross_s is not None:
            episodes_with_cross += 1
            has_hit = False
            # The alarm that counts is the WARN episode still active at the crossing; WARNs that
            # cleared before the crossing are reported separately (not as hits).
            pre = [w for w in warn_entries if w <= truth_cross_s]
            active_at_cross = (
                truth_cross_s < len(prod["state_by_t"]) and prod["state_by_t"][truth_cross_s] == "WARN"
            )
            first_warn_time: Optional[int] = None
            if pre and active_at_cross:
                first_warn_time = pre[-1]
            else:
                post = [w for w in warn_entries if w > truth_cross_s]
                first_warn_time = post[0] if post else None
            warn_cleared_before_cross += len(pre) - (1 if (pre and active_at_cross) else 0)
            if first_warn_time is not None:
                lead = truth_cross_s - first_warn_time
                lead_times_s.append(float(lead))
                if lead >= 10.0:
                    alarm_hits += 1
                    has_hit = True
                else:
                    alarm_lates += 1
            else:
                alarm_lates += 1  # Missed alarm entirely

            if is_breach_case:
                breach_episodes_with_cross += 1
                if has_hit:
                    breach_alarm_hits += 1
                else:
                    breach_alarm_lates += 1
                if first_warn_time is not None:
                    breach_lead_times_s.append(float(truth_cross_s - first_warn_time))
        else:
            non_cross_episodes += 1
            total_non_cross_hours += duration_s / 3600.0
            total_false_alarms += len(warn_entries)

    # -----------------------------------------------------------------------
    # Aggregate Metrics Computation
    # -----------------------------------------------------------------------
    mae_by_model_h: Dict[str, Dict[str, float]] = {}
    for m_name in models:
        mae_by_model_h[m_name] = {}
        for h in horizons:
            errs = errors_by_model[m_name][h]
            mae_by_model_h[m_name][f"MAE@{h}"] = statistics.mean(errs) if errs else 0.0

    mae30_m1 = mae_by_model_h["M1"]["MAE@30"]
    mae30_b0 = mae_by_model_h["B0"]["MAE@30"]
    ratio_mae30 = (mae30_m1 / mae30_b0) if mae30_b0 > 0 else 1.0

    # Subgroup ratio_MAE@30
    m1_ramp = statistics.mean(ramp_lag_errors_30["M1"]) if ramp_lag_errors_30["M1"] else 0.0
    b0_ramp = statistics.mean(ramp_lag_errors_30["B0"]) if ramp_lag_errors_30["B0"] else 0.0
    ratio_mae30_ramp_lag = (m1_ramp / b0_ramp) if b0_ramp > 0 else 1.0

    m1_steady = statistics.mean(steady_spike_errors_30["M1"]) if steady_spike_errors_30["M1"] else 0.0
    b0_steady = statistics.mean(steady_spike_errors_30["B0"]) if steady_spike_errors_30["B0"] else 0.0
    ratio_mae30_steady_spike = (m1_steady / b0_steady) if b0_steady > 0 else 1.0

    coverage_metrics: Dict[str, float] = {}
    for h in horizons:
        cov_list = coverage_by_h[h]
        coverage_metrics[f"coverage@{h}"] = statistics.mean(cov_list) if cov_list else 0.0

    # Breach-specific metrics (§10.3 target requirement: Test (breach cases))
    recall_alarm_breach = (
        (breach_alarm_hits / breach_episodes_with_cross) if breach_episodes_with_cross > 0 else 1.0
    )
    late_alarm_breach_rate = (
        (breach_alarm_lates / breach_episodes_with_cross) if breach_episodes_with_cross > 0 else 0.0
    )
    median_lead_breach_s = statistics.median(breach_lead_times_s) if breach_lead_times_s else 0.0
    p10_lead_breach_s = percentile(breach_lead_times_s, 10.0) if breach_lead_times_s else 0.0

    false_alarms_per_h = (
        (total_false_alarms / total_non_cross_hours) if total_non_cross_hours > 0 else 0.0
    )

    p99_lat_ms = percentile(m1_tick_latencies_ms, 99.0) if m1_tick_latencies_ms else 0.0
    p50_lat_ms = percentile(m1_tick_latencies_ms, 50.0) if m1_tick_latencies_ms else 0.0

    return {
        "split": split_name,
        "episodes_total": len(episodes),
        "episodes_with_cross": episodes_with_cross,
        "episodes_breach_cross": breach_episodes_with_cross,
        "episodes_non_cross": non_cross_episodes,
        "total_non_cross_hours": round(total_non_cross_hours, 2),
        "mae_by_model": mae_by_model_h,
        "ratio_mae30": round(ratio_mae30, 4),
        "ratio_mae30_ramp_lag": round(ratio_mae30_ramp_lag, 4),
        "ratio_mae30_steady_spike": round(ratio_mae30_steady_spike, 4),
        "coverage": {k: round(v, 4) for k, v in coverage_metrics.items()},
        "recall_alarm": round(recall_alarm_breach, 4),
        "late_alarm_rate": round(late_alarm_breach_rate, 4),
        "lead_time_s": {
            "median": round(median_lead_breach_s, 1),
            "p10": round(p10_lead_breach_s, 1),
            "count": len(breach_lead_times_s),
        },
        "all_crosses": {
            "episodes_count": episodes_with_cross,
            "alarm_hits": alarm_hits,
            "recall_alarm": round((alarm_hits / episodes_with_cross) if episodes_with_cross > 0 else 1.0, 4),
            "median_lead_s": round(statistics.median(lead_times_s) if lead_times_s else 0.0, 1),
            "p10_lead_s": round(percentile(lead_times_s, 10.0) if lead_times_s else 0.0, 1),
        },
        "false_alarms_per_h": round(false_alarms_per_h, 3),
        "total_false_alarms": total_false_alarms,
        "latency_ms": {
            "scope": "production ForecastObserver.on_sample + on_tick (Holt, bands, projector bisection, advisory)",
            "p50": round(p50_lat_ms, 3),
            "p99": round(p99_lat_ms, 3),
            "max": round(max(m1_tick_latencies_ms), 3) if m1_tick_latencies_ms else 0.0,
            "ticks_measured": len(m1_tick_latencies_ms),
        },
        "evals_per_tick_max": max(evals_per_tick) if evals_per_tick else 0,
        "warn_cleared_before_cross": warn_cleared_before_cross,
    }


# ---------------------------------------------------------------------------
# Acceptance Gates Verification per §10.3
# ---------------------------------------------------------------------------

def evaluate_acceptance_gates(
    test_metrics: Optional[Dict[str, Any]],
    ood_metrics: Optional[Dict[str, Any]],
    control_metrics: Optional[Dict[str, Any]] = None,
    extra_gates: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Evaluate pre-registered acceptance gates from §10.3.
    
    Gates:
    - G-ACC-RAMP: ratio_MAE@30 <= 0.85 in ramp and lag (test)
    - G-ACC-STEADY: ratio_MAE@30 <= 1.05 in steady and spike (test)
    - G-COV: 0.85 <= coverage@30 <= 0.95 (test)
    - G-RECALL: recall_alarm >= 0.90 (test)
    - G-LEAD: median lead_time_s >= 20s and p10 >= 10s (test)
    - G-FA: false_alarms_per_h <= 1.0 (test non-crossing)
    - G-OOD: recall_alarm >= 0.75 and false_alarms_per_h <= 3.0 (ood)
    - G-LAT: p99_on_tick_ms <= 50.0 ms
    """
    gates: List[Dict[str, Any]] = []

    # 1. G-ACC-RAMP
    if test_metrics:
        val = test_metrics["ratio_mae30_ramp_lag"]
        passed = (val <= 0.85)
        gates.append({
            "gate_id": "G-ACC-RAMP",
            "description": "Accuracy improvement on ramp and lag profiles",
            "split": "test",
            "threshold": "ratio_MAE@30 <= 0.85",
            "measured": f"{val:.4f}",
            "status": "PASS" if passed else "FAIL",
        })

        # 2. G-ACC-STEADY
        val = test_metrics["ratio_mae30_steady_spike"]
        passed = (val <= 1.05)
        gates.append({
            "gate_id": "G-ACC-STEADY",
            "description": "Accuracy non-degradation on steady and spike profiles",
            "split": "test",
            "threshold": "ratio_MAE@30 <= 1.05",
            "measured": f"{val:.4f}",
            "status": "PASS" if passed else "FAIL",
        })

        # 3. G-COV
        val = test_metrics["coverage"]["coverage@30"]
        passed = (0.85 <= val <= 0.95)
        gates.append({
            "gate_id": "G-COV",
            "description": "Conformal coverage within target 90% band (at h=30s)",
            "split": "test",
            "threshold": "0.85 <= coverage@30 <= 0.95",
            "measured": f"{val:.4f}",
            "status": "PASS" if passed else "FAIL",
        })

        # 4. G-RECALL
        val = test_metrics["recall_alarm"]
        passed = (val >= 0.90)
        gates.append({
            "gate_id": "G-RECALL",
            "description": "Alarm recall with anticipation >= 10s on breaches",
            "split": "test",
            "threshold": "recall_alarm >= 0.90",
            "measured": f"{val:.4f}",
            "status": "PASS" if passed else "FAIL",
        })

        # 5. G-LEAD
        med = test_metrics["lead_time_s"]["median"]
        p10 = test_metrics["lead_time_s"]["p10"]
        passed = (med >= 20.0 and p10 >= 10.0)
        gates.append({
            "gate_id": "G-LEAD",
            "description": "Alarm lead time before physical breach",
            "split": "test",
            "threshold": "median >= 20.0s and p10 >= 10.0s",
            "measured": f"median={med:.1f}s, p10={p10:.1f}s",
            "status": "PASS" if passed else "FAIL",
        })

        # 6. G-FA (§10.3: control_steady_1h + test non-crossing episodes, pooled)
        fa_n = test_metrics["total_false_alarms"]
        fa_h = test_metrics["total_non_cross_hours"]
        fa_split = "test"
        if control_metrics:
            fa_n += control_metrics["total_false_alarms"]
            fa_h += control_metrics["total_non_cross_hours"]
            fa_split = "test+control_steady_1h"
        val = (fa_n / fa_h) if fa_h > 0 else 0.0
        passed = (val <= 1.0)
        gates.append({
            "gate_id": "G-FA",
            "description": "False alarm rate on non-breaching operational series",
            "split": fa_split,
            "threshold": "false_alarms_per_h <= 1.0",
            "measured": f"{val:.3f} / h ({fa_n} WARN in {fa_h:.2f} h)",
            "status": "PASS" if passed else "FAIL",
        })

    # 7. G-OOD
    if ood_metrics:
        rec = ood_metrics["recall_alarm"]
        fa = ood_metrics["false_alarms_per_h"]
        passed = (rec >= 0.75 and fa <= 3.0)
        gates.append({
            "gate_id": "G-OOD",
            "description": "Robustness on out-of-distribution episodes (2x faster, 3x noise)",
            "split": "ood",
            "threshold": "recall >= 0.75 and false_alarms_per_h <= 3.0",
            "measured": f"recall={rec:.4f}, FA={fa:.3f}/h",
            "status": "PASS" if passed else "FAIL",
        })

    # 8. G-LAT
    lat_source = test_metrics or ood_metrics
    if lat_source:
        p99 = lat_source["latency_ms"]["p99"]
        passed = (p99 <= 50.0)
        gates.append({
            "gate_id": "G-LAT",
            "description": "Observer execution latency p99 per tick",
            "split": lat_source["split"],
            "threshold": "p99_on_tick_ms <= 50.0 ms",
            "measured": f"{p99:.3f} ms (full observer tick, max evals/tick {lat_source.get('evals_per_tick_max')})",
            "status": "PASS" if passed else "FAIL",
        })

    gates.extend(extra_gates or [])
    return gates


def run_pytest_gate(gate_id: str, description: str, test_ids: List[str]) -> Dict[str, Any]:
    """Run the pinned pytest selectors for a gate and report the real outcome."""
    import subprocess
    cmd = [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider", *test_ids]
    proc = subprocess.run(cmd, cwd=str(REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()][-1:] or ["no output"]
    return {
        "gate_id": gate_id,
        "description": description,
        "split": "all",
        "threshold": "selected tests green",
        "measured": tail[0][:160],
        "status": "PASS" if proc.returncode == 0 else "FAIL",
    }


def env_gate() -> Dict[str, Any]:
    """G-ENV from the active snapshot's guard-band backtest (§5.10)."""
    from sdv_governor.envdata import load_active_snapshot
    try:
        gb = load_active_snapshot(REPO_ROOT)["guard_bands"]
    except Exception as exc:  # report, never fake
        return {"gate_id": "G-ENV", "description": "Guarded false-open windows in 7-day backtest", "split": "backtest",
                "threshold": "false_open = 0 with guard", "measured": f"snapshot unavailable: {exc}", "status": "FAIL"}
    wb = gb.get("window_backtest") or {}
    fo = {k: ((v.get("guarded") or {}).get("false_open") if isinstance(v, dict) else None) for k, v in wb.items()}
    ok = (gb.get("status") == "DERIVED" and gb.get("gate_g_env_status") == "PASS"
          and bool(fo) and all(x == 0 for x in fo.values()))
    return {"gate_id": "G-ENV", "description": "Guarded false-open windows in 7-day backtest", "split": "backtest",
            "threshold": "false_open = 0 with guard (DERIVED)",
            "measured": f"guard {gb.get('status')}, backtest {gb.get('gate_g_env_status')}, false_open_guarded={fo}",
            "status": "PASS" if ok else "FAIL"}


# ---------------------------------------------------------------------------
# Report Formatting (JSON & Markdown)
# ---------------------------------------------------------------------------

def generate_markdown_report(report_data: Dict[str, Any]) -> str:
    """Generate Markdown report per §10.1."""
    md = []
    md.append(f"# Benchmark Report: Predictive Consumption Observer (E2)")
    md.append(f"**Date:** {report_data.get('timestamp_utc')}")
    md.append(f"**Host / Device:** {report_data.get('device')}")
    md.append(f"**Code Revision:** `{report_data.get('git_commit', 'unknown')}`")
    md.append(f"**Evaluated Splits:** {', '.join(report_data.get('splits_evaluated', []))}")
    md.append("")
    md.append("---")
    md.append("")
    md.append("## 1. Acceptance Gates Summary (§10.3)")
    md.append("")
    md.append("| Gate ID | Description | Split | Threshold | Measured Value | Result |")
    md.append("|---|---|---|---|---|---|")

    all_passed = True
    for g in report_data.get("acceptance_gates", []):
        status_badge = f"**{g['status']}**" if g['status'] == "PASS" else f"❌ **{g['status']}**"
        if g['status'] != "PASS":
            all_passed = False
        md.append(
            f"| `{g['gate_id']}` | {g['description']} | `{g['split']}` | `{g['threshold']}` | {g['measured']} | {status_badge} |"
        )

    md.append("")
    verdict_text = "**ALL GATES PASSED**" if all_passed else "**SOME GATES FAILED (See baseline check)**"
    md.append(f"**Overall Verdict:** {verdict_text}")
    md.append("")
    md.append("---")
    md.append("")

    # Section for each split
    for split_name, res in report_data.get("results_by_split", {}).items():
        md.append(f"## 2. Split: `{split_name}` ({res.get('episodes_total')} episodes)")
        md.append("")
        md.append(r"### Baseline MAE@h Comparison (Normalized Error $|y_{true} - \hat{y}| / declared$)")
        md.append("")
        md.append("| Model | MAE@10s | MAE@20s | MAE@30s | MAE@45s | MAE@60s | ratio_MAE@30 (vs B0) |")
        md.append("|---|---|---|---|---|---|---|")

        mae_dict = res.get("mae_by_model", {})
        for m in ("B0", "B1", "B2", "M1"):
            row = mae_dict.get(m, {})
            m30 = row.get("MAE@30", 0.0)
            b0_30 = mae_dict.get("B0", {}).get("MAE@30", 1.0)
            r30 = (m30 / b0_30) if b0_30 > 0 else 1.0
            md.append(
                f"| **`{m}`** | {row.get('MAE@10', 0):.4f} | {row.get('MAE@20', 0):.4f} | "
                f"{m30:.4f} | {row.get('MAE@45', 0):.4f} | {row.get('MAE@60', 0):.4f} | "
                f"**{r30:.4f}** |"
            )

        md.append("")
        md.append(f"- **ratio_MAE@30 (Overall):** `{res.get('ratio_mae30')}`")
        md.append(f"- **ratio_MAE@30 (Ramp & Lag):** `{res.get('ratio_mae30_ramp_lag')}`")
        md.append(f"- **ratio_MAE@30 (Steady & Spike):** `{res.get('ratio_mae30_steady_spike')}`")
        md.append("")

        md.append("### Conformal Coverage by Horizon (Target: 90% Nominal)")
        md.append("")
        cov = res.get("coverage", {})
        cov_parts = [f"`{k}`: **{v * 100:.2f}%**" for k, v in cov.items()]
        md.append("- " + " | ".join(cov_parts))
        md.append("")

        md.append("### Advisory & Alarm Metrics")
        md.append("")
        leads = res.get("lead_time_s", {})
        lat = res.get("latency_ms", {})
        recall_pct = res.get('recall_alarm', 0.0) * 100
        md.append(f"- **Alarm Recall (Anticipation $\\ge 10$s):** `{recall_pct:.2f}%` ({res.get('episodes_with_cross')} breach episodes)")
        md.append(f"- **Lead Time:** Median = `{leads.get('median')} s`, 10th percentile = `{leads.get('p10')} s`")
        md.append(f"- **Late Alarm Rate:** `{res.get('late_alarm_rate') * 100:.2f}%`")
        md.append(f"- **False Alarms Rate:** `{res.get('false_alarms_per_h')} / hour` ({res.get('total_false_alarms')} false triggers across {res.get('total_non_cross_hours')} h)")
        md.append(f"- **Observer Latency (on_tick):** p50 = `{lat.get('p50')} ms`, p99 = `{lat.get('p99')} ms`")
        md.append("")
        md.append("---")
        md.append("")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark and evaluate forecast models for E2 resource observer."
    )
    parser.add_argument(
        "--split",
        type=str,
        default="test",
        choices=["test", "ood", "train", "calib", "control_steady_1h", "both"],
        help="Dataset split to evaluate (default: test). Use 'both' to evaluate test and ood sequentially.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "docs" / "evidence",
        help="Output directory for reports (default: docs/evidence)",
    )
    parser.add_argument(
        "--params",
        type=Path,
        default=REPO_ROOT / "data" / "forecast" / "holt_params.yaml",
        help="Path to holt_params.yaml (default: data/forecast/holt_params.yaml)",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=REPO_ROOT / "data" / "guests",
        help="Path to guest catalog directory (default: data/guests)",
    )
    parser.add_argument(
        "--horizons",
        type=str,
        default="10,20,30,45,60",
        help="Comma-separated forecast horizons in seconds (default: 10,20,30,45,60)",
    )
    parser.add_argument(
        "--report-name",
        type=str,
        default=None,
        help="Override base report filename (without extension)",
    )

    parser.add_argument("--skip-pytest-gates", action="store_true",
                        help="Do not run the G-NI/G-OFF pytest selectors (they are then omitted, not passed)")

    args = parser.parse_args()
    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]

    # Load guest catalog
    from sdv_governor.manifest import validate_ship_profile
    from sdv_governor.replay import build_initial_state_from_scenario, load_yaml_file
    data_dir = REPO_ROOT / "data"
    catalog = load_guest_catalog(args.catalog, errata_path=data_dir / "overrides" / "brief_errata.yaml")
    ship_profile = validate_ship_profile(load_yaml_file(data_dir / "ship" / "vessel_baseline.yaml"))
    base_state = build_initial_state_from_scenario(
        load_yaml_file(data_dir / "scenarios" / "scenario_B_arbitration_degradation.yaml"), catalog
    )

    # Load HoltParams
    params = load_params_with_fallback(args.params)
    harness = ProductionObserverHarness(ship_profile, catalog, base_state, params)

    # Determine splits to run
    if args.split == "both":
        splits_to_run = ["test", "ood", "control_steady_1h"]
    else:
        splits_to_run = [args.split]

    results_by_split: Dict[str, Any] = {}
    test_metrics: Optional[Dict[str, Any]] = None
    ood_metrics: Optional[Dict[str, Any]] = None
    control_metrics: Optional[Dict[str, Any]] = None

    for sp in splits_to_run:
        print(f"[*] Generating dataset split: '{sp}'...")
        episodes = get_dataset_split(sp, catalog=catalog)
        print(f"[*] Evaluating {len(episodes)} episodes for split '{sp}'...")
        res = evaluate_split(sp, episodes, params=params, harness=harness, horizons=horizons)
        results_by_split[sp] = res
        if sp == "test":
            test_metrics = res
        elif sp == "ood":
            ood_metrics = res
        elif sp == "control_steady_1h":
            control_metrics = res

    extra: List[Dict[str, Any]] = []
    if not args.skip_pytest_gates:
        extra.append(run_pytest_gate("G-NI", "Non-interference NI-01..03 (campaign hash, payload, state)",
                                     ["tests/integration/test_forecast_non_interference.py"]))
        extra.append(run_pytest_gate("G-OFF", "Offline runtime ENV-INT-05 + FC-INT-06 (sockets blocked)",
                                     ["tests/integration/test_public_env_integration.py", "-k", "offline",
                                      "tests/integration/test_forecast_non_interference.py"]))
    extra.append(env_gate())
    gates = evaluate_acceptance_gates(test_metrics, ood_metrics, control_metrics, extra)

    # Get git commit hash if available
    git_commit = "unknown"
    try:
        import subprocess
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if proc.returncode == 0:
            git_commit = proc.stdout.strip()
            dirty = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"], cwd=str(REPO_ROOT),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
            ).stdout.strip()
            if dirty:
                git_commit += "-dirty"
    except Exception:
        pass

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    date_str = now_utc.strftime("%Y%m%d")

    report_payload: Dict[str, Any] = {
        "schema_version": "sdv-forecast-benchmark/1.0",
        "timestamp_utc": now_utc.isoformat(),
        "git_commit": git_commit,
        "device": "Mac" if sys.platform == "darwin" else "Linux/Arduino",
        "splits_evaluated": splits_to_run,
        "params": params.to_dict(),
        "results_by_split": results_by_split,
        "acceptance_gates": gates,
    }

    # Ensure output directory exists
    args.out.mkdir(parents=True, exist_ok=True)

    base_name = args.report_name or f"forecast_report_{date_str}"
    json_path = args.out / f"{base_name}.json"
    md_path = args.out / f"{base_name}.md"

    # Write JSON report
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report_payload, f, indent=2)
    print(f"[+] Written JSON report: {json_path}")

    # Write Markdown report
    md_content = generate_markdown_report(report_payload)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md_content)
    print(f"[+] Written Markdown report: {md_path}")

    # If run with a specific split like ood, also output a split-specific file if base_name didn't have it
    if args.split in ("test", "ood"):
        split_base = f"forecast_report_{args.split}_{date_str}"
        split_json = args.out / f"{split_base}.json"
        split_md = args.out / f"{split_base}.md"
        with open(split_json, "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2)
        with open(split_md, "w", encoding="utf-8") as f:
            f.write(md_content)
        print(f"[+] Written Split report: {split_json} and {split_md}")

    # Print summary table to stdout
    print("\n" + "=" * 78)
    print(f"FORECAST BENCHMARK GATES SUMMARY ({', '.join(splits_to_run)})")
    print("=" * 78)
    for g in gates:
        print(f"[{g['status']:4s}] {g['gate_id']:14s} | {g['split']:5s} | Measured: {g['measured']} (Req: {g['threshold']})")
    print("=" * 78)

    all_passed = all(g["status"] == "PASS" for g in gates)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())

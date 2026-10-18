"""Forecast observer orchestrating telemetry samples, Holt predictions, and physics projection.

Implements §6.8 of MEGAPLAN:
- on_sample(sim_time_s, telemetry_readings, state_view): updates Holt states without mutating state.
- on_tick(sim_time_s, state_clone, budgets_snapshot): runs physical projector and advisory machine.
- export(): cleanly separates deterministic `semantic` payload from platform-dependent `perf` timings.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import yaml

from sdv_governor.models import GuestManifest
from sdv_governor.ship_state import ShipOperationalState

from .advisory import AdvisoryStateMachine, advisory_sentence
from .conformal import compute_conformal_band
from .holt import HoltParams, HoltState, holt_forecast, holt_update, interp_quantile
from .projector import project_guest_physics


def _load_params(path: Optional[str] = None) -> Tuple[HoltParams, str]:
    """Load Holt parameters from YAML file or return defaults with sha256."""
    candidate_paths: List[Path] = []
    if path:
        candidate_paths.append(Path(path))
    else:
        # Standard location in data/forecast/holt_params.yaml
        repo_root = Path(__file__).resolve().parents[3]
        candidate_paths.append(repo_root / "data" / "forecast" / "holt_params.yaml")
        candidate_paths.append(repo_root / "src" / "sdv_governor" / "_bundled" / "data" / "forecast" / "holt_params.yaml")

    for p in candidate_paths:
        if p.is_file():
            try:
                raw_bytes = p.read_bytes()
                h = hashlib.sha256(raw_bytes).hexdigest()
                data = yaml.safe_load(raw_bytes.decode("utf-8"))
                if isinstance(data, dict):
                    return HoltParams.from_dict(data), h
            except Exception:
                pass

    # Default parameters if no file exists
    defaults = HoltParams()
    default_bytes = json.dumps(defaults.to_dict(), sort_keys=True).encode("utf-8")
    return defaults, hashlib.sha256(default_bytes).hexdigest()


class ForecastObserver:
    """Orchestrator for 1 Hz telemetry tracking, Holt forecasting, and physical advisory."""

    def __init__(
        self,
        ship_profile: Optional[Dict[str, Any]] = None,
        catalog: Optional[Dict[str, GuestManifest]] = None,
        params: Optional[HoltParams] = None,
        params_path: Optional[str] = None,
    ):
        self.ship_profile: Dict[str, Any] = ship_profile or {}
        self.catalog: Dict[str, GuestManifest] = catalog or {}

        if params is not None:
            self.params = params
            p_bytes = json.dumps(params.to_dict(), sort_keys=True).encode("utf-8")
            self.params_sha256 = hashlib.sha256(p_bytes).hexdigest()
        else:
            self.params, self.params_sha256 = _load_params(params_path)

        self.holt_states: Dict[str, HoltState] = {}
        self.advisory_machines: Dict[str, AdvisoryStateMachine] = {}
        self.telemetry_history: Dict[str, List[Tuple[int, float]]] = {}
        self.last_forecasts: Dict[str, Optional[Dict[int, float]]] = {}
        self.last_projections: Dict[str, Optional[Dict[str, Any]]] = {}
        self.alerts_history: List[Dict[str, Any]] = []
        self.perf_records: List[Dict[str, Any]] = []

    def on_sample(
        self,
        sim_time_s: int,
        telemetry_readings: Any,
        state_view: ShipOperationalState,
    ) -> None:
        """Process 1 Hz guest telemetry sample without mutating state_view (§6.8).
        
        Args:
            sim_time_s: Integer simulation time in seconds.
            telemetry_readings: Dict or object containing current telemetry readings per guest.
            state_view: Current ShipOperationalState (read-only; NOT mutated).
        """
        for guest_id, ginst in state_view.installed_guests.items():
            if not getattr(ginst, "powered_on", True):
                continue

            # Determine nominal power for normalized z series
            nominal_w = 1000.0
            manifest = self.catalog.get(guest_id)
            if manifest and ginst.active_mode in manifest.modes:
                nominal_w = float(manifest.modes[ginst.active_mode].nominal_power_w)

            # Extract power measurement for this guest
            measured_w: Optional[float] = None
            if isinstance(telemetry_readings, dict):
                raw = telemetry_readings.get(guest_id)
                if isinstance(raw, (int, float)):
                    measured_w = float(raw)
                elif isinstance(raw, dict):
                    measured_w = raw.get("power_w") or raw.get("actual_w") or raw.get("value")
                elif hasattr(raw, "power_w"):
                    measured_w = getattr(raw, "power_w")
            elif hasattr(telemetry_readings, "read"):
                # If telemetry_source object was passed
                try:
                    val = telemetry_readings.read(sim_time_s, guest_id)
                    if isinstance(val, (int, float)):
                        measured_w = float(val)
                except Exception:
                    measured_w = None

            z: Optional[float] = None
            if measured_w is not None and isinstance(measured_w, (int, float)) and math.isfinite(measured_w):
                z = measured_w / max(1.0, nominal_w)
                # Track in history
                hist = self.telemetry_history.setdefault(guest_id, [])
                hist.append((sim_time_s, float(measured_w)))
                # Retain only last 120s
                if len(hist) > 150:
                    cutoff = sim_time_s - 120
                    self.telemetry_history[guest_id] = [pt for pt in hist if pt[0] >= cutoff]

            # Update Holt state
            current_st = self.holt_states.get(guest_id)
            new_st = holt_update(
                st=current_st,
                t=sim_time_s,
                z=z,
                mode=ginst.active_mode,
                params=self.params,
            )
            self.holt_states[guest_id] = new_st

    def on_tick(
        self,
        sim_time_s: int,
        state_clone: ShipOperationalState,
        budgets_snapshot: Dict[str, Any],
        reactive_breach_guests: Optional[Sequence[str]] = None,
    ) -> None:
        """Run physical projector and advisory machine on tick completion (§6.8).
        
        Args:
            sim_time_s: Current simulation tick timestamp.
            state_clone: Cloned state instance (isolated for projection).
            budgets_snapshot: Evaluated 8-class budget snapshot from this tick.
            reactive_breach_guests: Guests on which the reactive governor detector acted this tick.
        """
        reactive = set(reactive_breach_guests or ())
        t0 = time.perf_counter()
        tick_evals = 0

        for guest_id, ginst in state_clone.installed_guests.items():
            if not getattr(ginst, "powered_on", True):
                continue

            machine = self.advisory_machines.setdefault(guest_id, AdvisoryStateMachine(guest_id))
            st = self.holt_states.get(guest_id)
            if st is None:
                continue

            status = st.status
            if status != "OK":
                machine.update(
                    sim_time_s, h_star_hi=None, status=status,
                    reactive_breach_active=guest_id in reactive,
                )
                self.last_projections[guest_id] = None
                self.last_forecasts[guest_id] = None
                continue

            manifest = self.catalog.get(guest_id)
            nominal_w = 1000.0
            if manifest and ginst.active_mode in manifest.modes:
                nominal_w = float(manifest.modes[ginst.active_mode].nominal_power_w)

            # Generate Holt forecast
            fc = holt_forecast(st, horizons=self.params.horizons_s, params=self.params, declared_w=nominal_w)
            self.last_forecasts[guest_id] = fc
            slope_w_per_s = st.trend * nominal_w

            # Define projection trajectories with conformal bands
            phi = self.params.phi

            def get_hi(h: int) -> float:
                scale = float(h) if abs(1.0 - phi) < 1e-9 else phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)
                y_hat = (st.level + st.trend * scale) * nominal_w
                q_hi = interp_quantile(self.params.conformal_q_hi or self.params.conformal_q, h)
                _, hi_w = compute_conformal_band(y_hat, nominal_w, q_lo=0.0, q_hi=q_hi, status=status)
                return hi_w

            def get_mid(h: int) -> float:
                scale = float(h) if abs(1.0 - phi) < 1e-9 else phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)
                return (st.level + st.trend * scale) * nominal_w

            def get_lo(h: int) -> float:
                scale = float(h) if abs(1.0 - phi) < 1e-9 else phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)
                y_hat = (st.level + st.trend * scale) * nominal_w
                q_lo = (interp_quantile(self.params.conformal_q_lo, h) if self.params.conformal_q_lo
                        else -interp_quantile(self.params.conformal_q, h))
                lo_w, _ = compute_conformal_band(y_hat, nominal_w, q_lo=q_lo, q_hi=0.0, status=status)
                return lo_w

            # Band at 30s
            y_30 = get_mid(30)
            q_lo_30 = (interp_quantile(self.params.conformal_q_lo, 30) if self.params.conformal_q_lo
                       else -interp_quantile(self.params.conformal_q, 30))
            q_hi_30 = interp_quantile(self.params.conformal_q_hi or self.params.conformal_q, 30)
            band_30 = compute_conformal_band(y_30, nominal_w, q_lo=q_lo_30, q_hi=q_hi_30, status=status)

            # Project physics
            p = project_guest_physics(
                ship_profile=self.ship_profile,
                catalog=self.catalog,
                state=state_clone,
                guest_id=guest_id,
                declared_w=nominal_w,
                power_trajectory_hi=get_hi,
                power_trajectory_mid=get_mid,
                power_trajectory_lo=get_lo,
                budgets_snapshot=budgets_snapshot,
                slope_w_per_s=slope_w_per_s,
                forecast_points=fc,
                conformal_band_at_30=band_30,
            )
            self.last_projections[guest_id] = p

            h_star_hi = None
            if p is not None:
                tick_evals += p.get("evals_used", 0)
                band = p.get("h_star_band_s")
                if band and len(band) > 0:
                    h_star_hi = float(band[0])
                elif p.get("h_star_s") is not None:
                    h_star_hi = float(p["h_star_s"])

            prev_state = machine.state
            new_state = machine.update(
                t=sim_time_s,
                h_star_hi=h_star_hi,
                status=status,
                reactive_breach_active=guest_id in reactive,
                p=p,
            )

            # One alert per WARN episode (on entry), not one per tick while in WARN.
            if new_state == "WARN" and prev_state != "WARN" and p is not None:
                guest_name = manifest.name if manifest else guest_id
                sent = advisory_sentence(p, guest_name=guest_name, src=f"telemetry:{guest_id}")
                self.alerts_history.append({
                    "guest_id": guest_id,
                    "state": "WARN",
                    "sim_time_s": sim_time_s,
                    "h_star_s": p["h_star_s"],
                    "band_s": p["h_star_band_s"],
                    "sentence": sent,
                    "witness": p["witness"],
                })

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self.perf_records.append({
            "sim_time_s": sim_time_s,
            "calc_ms": round(elapsed_ms, 3),
            "evals_used": tick_evals,
        })

    def export(self) -> Dict[str, Any]:
        """Export separated semantic and perf dictionaries (§6.8).
        
        Returns:
            Dict containing:
                "semantic": Platform-independent, reproducible fields (rounded to 1W).
                "perf": Execution timings in milliseconds.
        """
        status_by_guest = {
            gid: self.advisory_machines[gid].state
            for gid in sorted(self.advisory_machines.keys())
        }

        # Format forecasts rounded to 1W
        forecast_by_guest = {}
        for gid in sorted(self.last_forecasts.keys()):
            fcs = self.last_forecasts[gid]
            if fcs:
                forecast_by_guest[gid] = {
                    str(h): int(round(w)) for h, w in sorted(fcs.items())
                }
            else:
                forecast_by_guest[gid] = None

        # Collect transitions
        all_transitions = []
        for gid in sorted(self.advisory_machines.keys()):
            for tr in self.advisory_machines[gid].transitions:
                all_transitions.append(tr.to_dict())

        # Alerts with rounded figures
        semantic_alerts = []
        for a in self.alerts_history:
            semantic_alerts.append({
                "guest_id": a["guest_id"],
                "state": a["state"],
                "sim_time_s": a["sim_time_s"],
                "h_star_s": int(a["h_star_s"]) if a.get("h_star_s") is not None else None,
                "band_s": [int(x) for x in a["band_s"]] if a.get("band_s") else None,
                "sentence": a["sentence"],
                "witness": a["witness"],
            })

        calc_times = [p["calc_ms"] for p in self.perf_records] if self.perf_records else [0.0]
        sorted_times = sorted(calc_times)
        p50 = sorted_times[len(sorted_times) // 2]
        p99 = sorted_times[min(len(sorted_times) - 1, int(len(sorted_times) * 0.99))]

        return {
            "semantic": {
                "enabled": True,
                "params_sha256": self.params_sha256,
                "status_by_guest": status_by_guest,
                "alerts": semantic_alerts,
                "transitions": all_transitions,
                "forecast_by_guest": forecast_by_guest,
            },
            "perf": {
                "calc_ms_samples": calc_times,
                "p50_ms": round(p50, 2),
                "p99_ms": round(p99, 2),
                "total_calc_ms": round(sum(calc_times), 2),
            },
        }

    def forecast_view(self) -> Dict[str, Any]:
        """View dictionary for GET /api/forecast endpoint (§7.1)."""
        exp = self.export()
        sem = exp["semantic"]
        perf = exp["perf"]

        series = {}
        for gid in sorted(self.telemetry_history.keys()):
            hist = self.telemetry_history[gid]
            series[gid] = {
                "t": [pt[0] for pt in hist],
                "y": [round(pt[1], 1) for pt in hist],
                "fc": sem["forecast_by_guest"].get(gid) or {},
            }

        return {
            "enabled": sem["enabled"],
            "params_sha256": sem["params_sha256"],
            "status_by_guest": sem["status_by_guest"],
            "alerts": sem["alerts"],
            "series": series,
            "perf": {
                "p50_ms": perf["p50_ms"],
                "p99_ms": perf["p99_ms"],
            },
        }

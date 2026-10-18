"""Physics projector for SDV forecast advisory (standard library + sdv_governor only).

Implements physical projection per §6.6 of MEGAPLAN:
- Step 1 (Scalar screen): Fast O(1) screening of overconsumption and capacity headroom.
- Step 2 (Exact verification): Monotonic bisection on h in (0, 60] using state.clone()
  and evaluate_eight_class_budgets in <= 8 evaluations.
- Step 3 (Thermal inertia): deepcopy state.thermal_inertia and simulate step_tick.
- Never mutates the original operational state.
"""

from __future__ import annotations

import copy
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from sdv_governor.budgets import evaluate_eight_class_budgets
from sdv_governor.models import GuestManifest
from sdv_governor.ship_state import ShipOperationalState, ThermalInertiaState

# Declared advisory overrun threshold: 110% of declared power (ASM-FCST-OVERRUN-THRESHOLD)
ASM_FCST_OVERRUN_THRESHOLD: float = 1.10


def screen_scalar_risk(
    y_hat_hi_w: float,
    declared_w: float,
    threshold_ratio: float = ASM_FCST_OVERRUN_THRESHOLD,
    power_free_w: Optional[float] = None,
    feeder_free_w: Optional[float] = None,
    cooling_water_free_w: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Step 1 fast scalar screening for a single power projection or upper bound.
    
    Checks whether upper bound y_hat_hi exceeds:
    1. Overrun: y_hat_hi > 1.10 * declared_w (ASM-FCST-OVERRUN-THRESHOLD).
    2. Vessel power reserve headroom.
    3. Feeder line headroom.
    4. Cooling water headroom.
    
    Returns:
        (risk_detected: bool, reason: Optional[str])
    """
    threshold_w = threshold_ratio * declared_w
    if y_hat_hi_w > threshold_w:
        return True, f"OVERRUN ({y_hat_hi_w:.0f} W > {threshold_w:.0f} W)"

    excess_w = max(0.0, y_hat_hi_w - declared_w)
    if power_free_w is not None and excess_w > power_free_w:
        return True, f"SHIP_POWER_EXCEEDED (excess {excess_w:.0f} W > free {power_free_w:.0f} W)"

    if feeder_free_w is not None and excess_w > feeder_free_w:
        return True, f"FEEDER_LINE_EXCEEDED (excess {excess_w:.0f} W > free {feeder_free_w:.0f} W)"

    if cooling_water_free_w is not None and excess_w > cooling_water_free_w:
        return True, f"COOLING_WATER_EXCEEDED (excess {excess_w:.0f} W > free {cooling_water_free_w:.0f} W)"

    return False, None


def bisection_first_infeasible(
    eval_fn: Callable[[int], bool],
    min_h: int = 1,
    max_h: int = 60,
) -> Tuple[Optional[int], int]:
    """Find first infeasible horizon h* in [min_h, max_h] using monotonic bisection.
    
    Monotonic property: if feasible at h, then feasible at all h' < h.
    Evaluation budget: <= 6 bisection iterations + 2 extremes = <= 8 evaluations for [1, 60].
    
    Args:
        eval_fn: Callable(h: int) -> bool, returning True if feasible, False if infeasible.
        min_h: Lower horizon boundary in seconds (default 1).
        max_h: Upper horizon boundary in seconds (default 60).
        
    Returns:
        (h_star, evals_used):
            h_star: First horizon where eval_fn(h) is False, or None if feasible for all h in [min_h, max_h].
            evals_used: Total number of times eval_fn was evaluated.
    """
    evals_used = 0

    # 1. Check upper extreme
    evals_used += 1
    if eval_fn(max_h):
        # Feasible at max_h implies feasible everywhere in [min_h, max_h]
        return None, evals_used

    # 2. Check lower extreme
    evals_used += 1
    if not eval_fn(min_h):
        # Infeasible already at the very first step
        return min_h, evals_used

    # Invariant: eval_fn(low) is True, eval_fn(high) is False
    low = min_h
    high = max_h

    while low + 1 < high:
        mid = (low + high) // 2
        evals_used += 1
        if eval_fn(mid):
            low = mid
        else:
            high = mid

    return high, evals_used


def simulate_thermal_exhaustion(
    thermal_inertia: ThermalInertiaState,
    excess_heat_w: int,
    dt_s: int = 1,
    max_steps_s: int = 120,
) -> Dict[str, Any]:
    """Step 3 thermal inertia projection using existing ThermalInertiaState rules.
    
    Simulates step_tick(excess_heat_w, dt_s) on a deepcopy up to max_steps_s or 1.8 MJ.
    Does NOT mutate original thermal_inertia state.
    
    Returns:
        Dict with:
            exhaust_in_s: Second at which buffer exhausts (or 0 if already exhausted/in cooldown/exceeds max boost)
            remaining_j: Remaining buffer energy in Joules
            status: Thermal status string
    """
    ti = copy.deepcopy(thermal_inertia)

    if ti.exhausted or ti.cooldown_remaining_s > 0:
        return {
            "remaining_j": max(0, ti.max_energy_j - ti.accumulated_boost_energy_j),
            "exhaust_in_s": 0,
            "status": "THERMAL_INERTIA_IN_COOLDOWN",
        }

    if excess_heat_w > ti.max_boost_power_w:
        return {
            "remaining_j": max(0, ti.max_energy_j - ti.accumulated_boost_energy_j),
            "exhaust_in_s": 0,
            "status": f"EXCEEDS_MAX_THERMAL_BOOST ({excess_heat_w} W > {ti.max_boost_power_w} W)",
        }

    if excess_heat_w <= 0:
        return {
            "remaining_j": max(0, ti.max_energy_j - ti.accumulated_boost_energy_j),
            "exhaust_in_s": max_steps_s,
            "status": "THERMAL_NOMINAL",
        }

    exhaust_in_s = 0
    status = "THERMAL_INERTIA_ABSORBING"
    for s in range(1, max_steps_s + 1):
        ok, status = ti.step_tick(excess_heat_w, dt_s=dt_s)
        if ti.exhausted or not ok:
            exhaust_in_s = s
            break

    rem_j = max(0, ti.max_energy_j - ti.accumulated_boost_energy_j)
    return {
        "remaining_j": rem_j,
        "exhaust_in_s": exhaust_in_s,
        "status": status,
    }


def project_guest_physics(
    ship_profile: Dict[str, Any],
    catalog: Dict[str, GuestManifest],
    state: ShipOperationalState,
    guest_id: str,
    declared_w: float,
    power_trajectory_hi: Callable[[int], float],
    power_trajectory_mid: Optional[Callable[[int], float]] = None,
    power_trajectory_lo: Optional[Callable[[int], float]] = None,
    budgets_snapshot: Optional[Dict[str, Any]] = None,
    slope_w_per_s: float = 0.0,
    forecast_points: Optional[Dict[int, float]] = None,
    conformal_band_at_30: Optional[Tuple[float, float]] = None,
    evaluator_override: Optional[Callable[[int], bool]] = None,
) -> Optional[Dict[str, Any]]:
    """Execute complete 3-step physical projection for a guest without mutating state.
    
    Args:
        ship_profile: Vessel specifications dictionary.
        catalog: Loaded guest manifests catalog.
        state: Current ship operational state (NOT mutated).
        guest_id: Identifier of the guest being evaluated.
        declared_w: Declared nominal power in Watts.
        power_trajectory_hi: Function mapping horizon h (s) -> projected upper power (W).
        power_trajectory_mid: Optional function mapping h -> point forecast power (W).
        power_trajectory_lo: Optional function mapping h -> projected lower power (W).
        budgets_snapshot: Latest 8-class budget snapshot from current tick.
        slope_w_per_s: Estimated power trend slope in Watts/second.
        forecast_points: Dict of {horizon_s: forecast_w} for output reporting.
        conformal_band_at_30: Optional (lo_w, hi_w) at h=30s for reporting.
        evaluator_override: Optional mock feasibility evaluator for testing bisection in isolation.
        
    Returns:
        Structured projection dict per §6.6, or None if no risk detected.
    """
    t0 = time.perf_counter()

    # Step 1: Scalar screen across horizons [10, 20, 30, 45, 60]
    horizons_to_screen = [10, 20, 30, 45, 60]
    peak_hi_w = max(power_trajectory_hi(h) for h in horizons_to_screen)

    power_free_w = None
    cooling_water_free_w = None
    feeder_free_w = None
    if budgets_snapshot and "classes" in budgets_snapshot:
        p_class = budgets_snapshot["classes"].get("power", {})
        power_free_w = p_class.get("free")
        c_class = budgets_snapshot["classes"].get("cooling", {})
        cooling_water_free_w = c_class.get("free")

    has_risk, _ = screen_scalar_risk(
        y_hat_hi_w=peak_hi_w,
        declared_w=declared_w,
        threshold_ratio=ASM_FCST_OVERRUN_THRESHOLD,
        power_free_w=power_free_w,
        feeder_free_w=feeder_free_w,
        cooling_water_free_w=cooling_water_free_w,
    )

    if not has_risk:
        return None

    # Step 2: Exact verification via monotonic bisection on h in [1, 60]
    infeasible_cache: Dict[int, Dict[str, Any]] = {}

    if evaluator_override is not None:
        eval_fn = evaluator_override
    else:
        def eval_fn(h: int) -> bool:
            p_override_w = int(round(power_trajectory_hi(h)))
            threshold_w = int(round(declared_w * ASM_FCST_OVERRUN_THRESHOLD))
            if p_override_w > threshold_w:
                return False
            cloned = state.clone()
            if guest_id in cloned.installed_guests:
                cloned.installed_guests[guest_id].actual_power_override_w = p_override_w
            res = evaluate_eight_class_budgets(ship_profile, catalog, cloned)
            is_feas = bool(res.get("feasible", False))
            if not is_feas:
                infeasible_cache[h] = res
            return is_feas

    h_star_hi, evals_used = bisection_first_infeasible(eval_fn, min_h=1, max_h=60)

    if h_star_hi is None:
        # Feasible throughout 60s
        return None

    # Extract binding witness at h_star_hi
    witness_snap = infeasible_cache.get(h_star_hi)
    if witness_snap is None and evaluator_override is None:
        cloned = state.clone()
        p_override_w = int(round(power_trajectory_hi(h_star_hi)))
        if guest_id in cloned.installed_guests:
            cloned.installed_guests[guest_id].actual_power_override_w = p_override_w
        witness_snap = evaluate_eight_class_budgets(ship_profile, catalog, cloned)

    witness_dict: Dict[str, Any] = {
        "class": "power",
        "component": f"{guest_id} feeder",
        "capacity_w": int(declared_w * ASM_FCST_OVERRUN_THRESHOLD),
        "projected_w": int(round(power_trajectory_hi(h_star_hi))),
    }

    if witness_snap is not None:
        witnesses = witness_snap.get("binding_witnesses", [])
        if witnesses:
            bw = witnesses[0]
            w_class = bw.get("resource_class", "power")
            c_id = bw.get("constraint_id", "")
            if "water" in c_id or w_class == "cooling":
                w_comp = "water"
            elif "feeder" in c_id or "line" in c_id:
                w_comp = f"{guest_id} feeder"
            elif "air" in c_id:
                w_comp = "air"
            else:
                w_comp = c_id
            witness_dict = {
                "class": w_class,
                "component": w_comp,
                "capacity_w": int(bw.get("capacity", 0)),
                "projected_w": int(bw.get("demand", 0)),
            }

    # Step 3: Thermal inertia if binding witness is cooling water
    if witness_dict["class"] == "cooling" or "water" in witness_dict["component"]:
        excess_w = max(0, witness_dict["projected_w"] - witness_dict["capacity_w"])
        thermal_res = simulate_thermal_exhaustion(state.thermal_inertia, excess_w)
    else:
        thermal_res = {
            "remaining_j": max(0, state.thermal_inertia.max_energy_j - state.thermal_inertia.accumulated_boost_energy_j),
            "exhaust_in_s": 0,
            "status": "NOT_APPLICABLE",
        }

    # Derive h_star_s and h_star_band_s [earliest_breach, latest_breach]
    if power_trajectory_mid is not None and power_trajectory_lo is not None and slope_w_per_s > 0.1:
        # Time shift from upper bound to point estimate and lower bound
        delta_hi_w = power_trajectory_hi(h_star_hi) - power_trajectory_mid(h_star_hi)
        shift_hi_s = int(round(delta_hi_w / slope_w_per_s))
        h_star_s = h_star_hi + shift_hi_s

        delta_lo_w = power_trajectory_mid(h_star_hi) - power_trajectory_lo(h_star_hi)
        shift_lo_s = int(round(delta_lo_w / slope_w_per_s))
        h_star_lo = h_star_s + shift_lo_s
        h_star_band_s = [h_star_hi, max(h_star_hi, h_star_lo)]
    else:
        h_star_s = h_star_hi
        h_star_band_s = [h_star_hi, h_star_hi]

    threshold_w = int(round(ASM_FCST_OVERRUN_THRESHOLD * declared_w))
    calc_ms = round((time.perf_counter() - t0) * 1000.0, 2)

    fc_out = forecast_points or {
        10: int(round(power_trajectory_hi(10))),
        30: int(round(power_trajectory_hi(30))),
        60: int(round(power_trajectory_hi(60))),
    }

    band_out = {}
    if conformal_band_at_30:
        band_out["30"] = [int(round(conformal_band_at_30[0])), int(round(conformal_band_at_30[1]))]

    return {
        "guest_id": guest_id,
        "h_star_s": h_star_s,
        "h_star_band_s": h_star_band_s,
        "witness": witness_dict,
        "declared_w": int(round(declared_w)),
        "threshold_w": threshold_w,
        "forecast_w": fc_out,
        "band_w": band_out,
        "slope_w_per_s": round(slope_w_per_s, 1),
        "thermal": {
            "remaining_j": thermal_res["remaining_j"],
            "exhaust_in_s": thermal_res["exhaust_in_s"],
        },
        "evals_used": evals_used,
        "calc_ms": calc_ms,
    }

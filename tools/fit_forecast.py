#!/usr/bin/env python3
"""Fit Holt damped forecast model and calibrate conformal quantiles.

Grid search on train split (seeds 1000-1239), calibrate on calib split (seeds 2000-2079).
Writes data/forecast/holt_params.yaml with canonical sha256 per §6.4 and §10.1.
Standard library ONLY (+ yaml, sdv_governor).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import yaml

# Add src to sys.path if not installed
repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo_root / "src"))

from sdv_governor.forecast.conformal import calibrate_conformal_quantiles
from sdv_governor.forecast.holt import HoltParams, holt_forecast, holt_update, save_forecast_params
from sdv_governor.forecast.telemetry import get_dataset_split
from sdv_governor.models import canonical_json_hash


def compute_generator_sha256(repo_root: Path) -> str:
    """Compute sha256 of telemetry generator source file."""
    gen_path = repo_root / "src" / "sdv_governor" / "forecast" / "telemetry.py"
    if not gen_path.is_file():
        raise FileNotFoundError(f"Telemetry generator not found at {gen_path}")
    return hashlib.sha256(gen_path.read_bytes()).hexdigest()


def pre_process_episodes(episodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pre-compute normalized observations and targets for speed."""
    processed = []
    for ep in episodes:
        t_arr = ep["t"]
        y_obs = ep["y_obs"]
        y_true = ep["y_true"]
        dw = ep["declared_w"]
        modes = ep["mode"]
        n = len(t_arr)

        z_obs = [
            (y_obs[i] / dw[i]) if (y_obs[i] is not None and dw[i] > 0) else None
            for i in range(n)
        ]
        z_true = [
            (y_true[i] / dw[i]) if dw[i] > 0 else 1.0
            for i in range(n)
        ]
        processed.append({
            "t": t_arr,
            "z_obs": z_obs,
            "z_true": z_true,
            "mode": modes,
            "n": n,
        })
    return processed


def evaluate_grid_point(
    alpha: float,
    beta: float,
    phi: float,
    train_data: List[Dict[str, Any]],
    horizons: Sequence[int],
    warmup_s: int = 20,
) -> float:
    """Evaluate mean MAE(z_{t+h}) on train dataset across all horizons."""
    params = HoltParams(alpha=alpha, beta=beta, phi=phi, warmup_s=warmup_s)

    # Precompute horizon scales for damped trend
    scales: Dict[int, float] = {}
    for h in horizons:
        if abs(1.0 - phi) < 1e-9:
            scales[h] = float(h)
        else:
            scales[h] = phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)

    total_mae = 0.0
    horizon_counts = {h: 0 for h in horizons}
    horizon_errors = {h: 0.0 for h in horizons}

    for ep in train_data:
        t_arr = ep["t"]
        z_obs_arr = ep["z_obs"]
        z_true_arr = ep["z_true"]
        modes = ep["mode"]
        n = ep["n"]

        st = None
        for i in range(n):
            st = holt_update(
                st,
                t=t_arr[i],
                z=z_obs_arr[i],
                mode=modes[i],
                params=params,
            )

            # Exclude warmup, stale, unavailable, regime break from training loss
            if st.status == "OK":
                for h in horizons:
                    target_idx = i + h
                    if target_idx < n:
                        z_hat = st.level + st.trend * scales[h]
                        err = abs(z_true_arr[target_idx] - z_hat)
                        horizon_errors[h] += err
                        horizon_counts[h] += 1

    # Mean over horizons of MAE per horizon
    horizon_maes = [
        (horizon_errors[h] / horizon_counts[h]) if horizon_counts[h] > 0 else 0.0
        for h in horizons
    ]
    return sum(horizon_maes) / len(horizon_maes)


def fit_grid(
    train_data: List[Dict[str, Any]],
    horizons: Sequence[int],
    alphas: Sequence[float] = (0.1, 0.2, 0.3, 0.5),
    betas: Sequence[float] = (0.01, 0.05, 0.1, 0.2),
    phis: Sequence[float] = (0.80, 0.90, 0.95, 0.98),
) -> Tuple[float, float, float, float]:
    """Run grid search over alpha, beta, phi (64 combinations).
    
    Tie-breaker: smaller phi (more conservative with trends), then higher alpha.
    """
    best_loss = float("inf")
    best_params = (0.3, 0.05, 0.90)

    results = []

    print(f"Starting grid search over {len(alphas) * len(betas) * len(phis)} combinations...")
    for phi in phis:
        for beta in betas:
            for alpha in alphas:
                loss = evaluate_grid_point(alpha, beta, phi, train_data, horizons)
                results.append((loss, phi, alpha, beta))

    # Sort by: loss ascending, phi ascending (more conservative), alpha descending (more responsive)
    results.sort(key=lambda r: (round(r[0], 6), r[1], -r[2], r[3]))
    best_loss, best_phi, best_alpha, best_beta = results[0]

    print(
        f"Optimal parameters found: alpha={best_alpha}, beta={best_beta}, phi={best_phi} "
        f"with mean_MAE_z={best_loss:.6f}"
    )
    return best_alpha, best_beta, best_phi, best_loss


def calibrate_on_calib_split(
    calib_data: List[Dict[str, Any]],
    best_params: HoltParams,
    horizons: Sequence[int],
) -> Tuple[Dict[int, float], Dict[int, float], Dict[int, float]]:
    """Compute calibration residuals r = z_{t+h} - z_hat and conformal quantiles."""
    print("Calibrating conformal quantiles on calib split...")
    phi = best_params.phi
    scales: Dict[int, float] = {}
    for h in horizons:
        if abs(1.0 - phi) < 1e-9:
            scales[h] = float(h)
        else:
            scales[h] = phi * (1.0 - math.pow(phi, h)) / (1.0 - phi)

    residuals_by_h: Dict[int, List[float]] = {h: [] for h in horizons}

    for ep in calib_data:
        t_arr = ep["t"]
        z_obs_arr = ep["z_obs"]
        z_true_arr = ep["z_true"]
        modes = ep["mode"]
        n = ep["n"]

        st = None
        for i in range(n):
            st = holt_update(
                st,
                t=t_arr[i],
                z=z_obs_arr[i],
                mode=modes[i],
                params=best_params,
            )

            if st.status == "OK":
                for h in horizons:
                    target_idx = i + h
                    if target_idx < n:
                        z_hat = st.level + st.trend * scales[h]
                        res = z_true_arr[target_idx] - z_hat
                        residuals_by_h[h].append(res)

    q_lo, q_hi = calibrate_conformal_quantiles(residuals_by_h, alpha_hi=0.05, alpha_lo=0.05)
    # Symmetric envelope for backward compatibility
    q_sym = {h: max(abs(q_lo[h]), abs(q_hi[h])) for h in horizons}

    for h in horizons:
        print(
            f"  h={h:2d}s: n={len(residuals_by_h[h])}, "
            f"q_lo={q_lo[h]:+.5f}, q_hi={q_hi[h]:+.5f}, q_sym={q_sym[h]:.5f}"
        )

    return q_sym, q_hi, q_lo


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fit Holt damped forecast model and calibrate conformal quantiles."
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "data" / "forecast" / "holt_params.yaml",
        help="Target output YAML path",
    )
    args = parser.parse_args()

    horizons = [10, 20, 30, 45, 60]

    # Load and pre-process datasets
    print("Loading train dataset split (seeds 1000-1239)...")
    train_raw = get_dataset_split("train")
    train_processed = pre_process_episodes(train_raw)

    print("Loading calib dataset split (seeds 2000-2079)...")
    calib_raw = get_dataset_split("calib")
    calib_processed = pre_process_episodes(calib_raw)

    # 1. Grid search
    best_alpha, best_beta, best_phi, best_loss = fit_grid(
        train_processed,
        horizons=horizons,
    )

    opt_params = HoltParams(
        alpha=best_alpha,
        beta=best_beta,
        phi=best_phi,
        horizons_s=horizons,
        warmup_s=20,
        regime_k=4.0,
    )

    # 2. Conformal calibration
    q_sym, q_hi, q_lo = calibrate_on_calib_split(
        calib_processed,
        best_params=opt_params,
        horizons=horizons,
    )

    gen_sha = compute_generator_sha256(repo_root)

    # 3. Assemble parameter structure
    params_dict: Dict[str, Any] = {
        "schema_version": "sdv-forecast-params/1.0",
        "model": "holt_damped",
        "alpha": best_alpha,
        "beta": best_beta,
        "phi": best_phi,
        "horizons_s": horizons,
        "warmup_s": 20,
        "regime_k": 4.0,
        "conformal_q": {str(h): round(q_sym[h], 5) for h in horizons},
        "conformal_q_hi": {str(h): round(q_hi[h], 5) for h in horizons},
        "conformal_q_lo": {str(h): round(q_lo[h], 5) for h in horizons},
        "fit": {
            "train_seeds": "1000-1239",
            "calib_seeds": "2000-2079",
            "loss": "mean_MAE_z",
            "grid_size": 64,
        },
        "generator_sha256": gen_sha,
    }

    # 4. Save and compute canonical sha256
    out_file = args.out
    params_sha = save_forecast_params(params_dict, out_file)
    print(f"Frozen parameters written to: {out_file}")
    print(f"  generator_sha256: {gen_sha}")
    print(f"  params_sha256:    {params_sha}")


if __name__ == "__main__":
    main()

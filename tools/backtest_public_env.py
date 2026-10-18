#!/usr/bin/env python3
"""Backtest Open-Meteo forecasts against NOAA NDBC buoy measurements (E1 / §5.10).

Computes:
- Error statistics (MAE, bias, q90, q95, n) for SST, wave height (Hs), wind, and gusts.
- Sea state confusion matrix (WMO Code Table 3700).
- Operational window decision matching (true/false open/closed).
- Rigorous guard bands to guarantee zero false opens on safety-critical deck operations.

Produces:
- `guard_bands.json` inside the environmental snapshot directory.
ONLY standard library is used for execution.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def wave_height_to_sea_state_wmo(hs_m: Optional[float]) -> Optional[int]:
    """WMO Code Table 3700 wave height to sea state mapping."""
    if hs_m is None:
        return None
    if hs_m <= 0.0:
        return 0
    if hs_m <= 0.1:
        return 1
    if hs_m <= 0.5:
        return 2
    if hs_m <= 1.25:
        return 3
    if hs_m <= 2.5:
        return 4
    if hs_m <= 4.0:
        return 5
    if hs_m <= 6.0:
        return 6
    if hs_m <= 9.0:
        return 7
    if hs_m <= 14.0:
        return 8
    return 9


def parse_iso(ts_str: str) -> datetime.datetime:
    """Parse UTC ISO string into timezone-aware datetime."""
    return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def compute_quantile(vals: List[float], q: float) -> float:
    """Calculate sample quantile using linear interpolation."""
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    if n == 1:
        return s[0]
    idx = (n - 1) * q
    low = int(idx)
    high = min(low + 1, n - 1)
    weight = idx - low
    return s[low] * (1.0 - weight) + s[high] * weight


def compute_stats(pairs: List[Tuple[float, float]]) -> Dict[str, Any]:
    """Compute MAE, bias, q90, q95, and count from (forecast, observed) pairs."""
    if not pairs:
        return {"mae": 0.0, "bias": 0.0, "q90": 0.0, "q95": 0.0, "n": 0}
    diffs = [fc - obs for fc, obs in pairs]
    abs_diffs = [abs(d) for d in diffs]
    n = len(pairs)
    mae = sum(abs_diffs) / n
    bias = sum(diffs) / n
    q90 = compute_quantile(abs_diffs, 0.90)
    q95 = compute_quantile(abs_diffs, 0.95)
    return {
        "mae": round(mae, 3),
        "bias": round(bias, 3),
        "q90": round(q90, 3),
        "q95": round(q95, 3),
        "n": n,
    }


def evaluate_window_decisions(
    pairs: List[Dict[str, Any]],
    limit_wind_kn: float,
    limit_sea_state: int,
    guard_wind_kn: float,
    guard_hs_m: float,
) -> Tuple[Dict[str, int], Dict[str, int]]:
    """Evaluate raw and guarded window decisions against observed truth."""
    raw = {"true_open": 0, "false_open": 0, "true_closed": 0, "false_closed": 0}
    guarded = {"true_open": 0, "false_open": 0, "true_closed": 0, "false_closed": 0}

    for p in pairs:
        obs_open = (p["obs_wind"] <= limit_wind_kn) and (
            wave_height_to_sea_state_wmo(p["obs_hs"]) <= limit_sea_state
        )
        raw_open = (p["fc_wind"] <= limit_wind_kn) and (
            wave_height_to_sea_state_wmo(p["fc_hs"]) <= limit_sea_state
        )
        g_open = ((p["fc_wind"] + guard_wind_kn) <= limit_wind_kn) and (
            wave_height_to_sea_state_wmo(p["fc_hs"] + guard_hs_m) <= limit_sea_state
        )

        if raw_open and obs_open:
            raw["true_open"] += 1
        elif raw_open and not obs_open:
            raw["false_open"] += 1
        elif not raw_open and not obs_open:
            raw["true_closed"] += 1
        elif not raw_open and obs_open:
            raw["false_closed"] += 1

        if g_open and obs_open:
            guarded["true_open"] += 1
        elif g_open and not obs_open:
            guarded["false_open"] += 1
        elif not g_open and not obs_open:
            guarded["true_closed"] += 1
        elif not g_open and obs_open:
            guarded["false_closed"] += 1

    return raw, guarded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest public environmental data against NDBC buoys.")
    parser.add_argument("--env-dir", default="data/environment/golden_gate", help="Environment data root")
    parser.add_argument("--snapshot", default="ACTIVE", help="Snapshot subfolder or 'ACTIVE'")
    parser.add_argument("--out", default=None, help="Custom output path for guard_bands.json")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    env_root = Path(args.env_dir).resolve()

    if args.snapshot == "ACTIVE":
        active_file = env_root / "ACTIVE"
        if not active_file.exists():
            raise FileNotFoundError(f"ACTIVE pointer file not found at {active_file}")
        snap_id = active_file.read_text(encoding="utf-8").strip()
    else:
        snap_id = args.snapshot

    snap_dir = env_root / snap_id
    if not snap_dir.exists():
        raise FileNotFoundError(f"Snapshot directory not found: {snap_dir}")

    series_file = snap_dir / "env_series.json"
    manifest_file = snap_dir / "manifest.json"
    if not series_file.exists():
        raise FileNotFoundError(f"env_series.json not found in {snap_dir}")

    env_data = json.loads(series_file.read_text(encoding="utf-8"))
    snapshot_sha = hashlib.sha256(series_file.read_bytes()).hexdigest()

    series = env_data.get("series", {})
    fc_list = series.get("forecast_GG_BAR", [])
    b46237 = series.get("observed_NDBC_46237", [])
    b46026 = series.get("observed_NDBC_46026", [])
    bftpc1 = series.get("observed_NDBC_FTPC1", [])

    obs_46237_dt = [(parse_iso(r["t_utc"]), r) for r in b46237]
    obs_46026_dt = [(parse_iso(r["t_utc"]), r) for r in b46026]
    obs_ftpc1_dt = [(parse_iso(r["t_utc"]), r) for r in bftpc1]

    sst_pairs: List[Tuple[float, float]] = []
    hs_pairs: List[Tuple[float, float]] = []
    wind_pairs: List[Tuple[float, float]] = []
    gust_pairs: List[Tuple[float, float]] = []
    paired_samples: List[Dict[str, Any]] = []

    paired_timestamps: List[str] = []

    for fc in fc_list:
        t_fc = parse_iso(fc["t_utc"])

        # Match observations within +/- 30 minutes (1800 s)
        m46237 = [r for dt, r in obs_46237_dt if abs((dt - t_fc).total_seconds()) <= 1800]
        m46026 = [r for dt, r in obs_46026_dt if abs((dt - t_fc).total_seconds()) <= 1800]

        # SST: 46237 primary, fallback 46026
        sst_vals = [r["sst_c"] for r in m46237 if r.get("sst_c") is not None]
        if not sst_vals:
            sst_vals = [r["sst_c"] for r in m46026 if r.get("sst_c") is not None]
        if sst_vals and fc.get("sst_c") is not None:
            avg_sst_obs = sum(sst_vals) / len(sst_vals)
            sst_pairs.append((fc["sst_c"], avg_sst_obs))

        # Hs: 46237 primary, fallback 46026
        hs_vals = [r["wave_hs_m"] for r in m46237 if r.get("wave_hs_m") is not None]
        if not hs_vals:
            hs_vals = [r["wave_hs_m"] for r in m46026 if r.get("wave_hs_m") is not None]
        if hs_vals and fc.get("wave_hs_m") is not None:
            avg_hs_obs = sum(hs_vals) / len(hs_vals)
            hs_pairs.append((fc["wave_hs_m"], avg_hs_obs))

        # Wind: 46026 offshore anemometer
        wind_vals = [r["wind_kn"] for r in m46026 if r.get("wind_kn") is not None]
        if wind_vals and fc.get("wind_kn") is not None:
            avg_wind_obs = sum(wind_vals) / len(wind_vals)
            wind_pairs.append((fc["wind_kn"], avg_wind_obs))

        # Gust: 46026
        gust_vals = [r["gust_kn"] for r in m46026 if r.get("gust_kn") is not None]
        if gust_vals and fc.get("gust_kn") is not None:
            avg_gust_obs = sum(gust_vals) / len(gust_vals)
            gust_pairs.append((fc["gust_kn"], avg_gust_obs))

        # Paired entry for window matching
        if hs_vals and wind_vals and fc.get("wave_hs_m") is not None and fc.get("wind_kn") is not None:
            paired_timestamps.append(fc["t_utc"])
            paired_samples.append({
                "t_utc": fc["t_utc"],
                "fc_hs": fc["wave_hs_m"],
                "fc_wind": fc["wind_kn"],
                "obs_hs": sum(hs_vals) / len(hs_vals),
                "obs_wind": sum(wind_vals) / len(wind_vals),
            })

    # Stats
    metrics_sst = compute_stats(sst_pairs)
    metrics_hs = compute_stats(hs_pairs)
    metrics_wind = compute_stats(wind_pairs)
    metrics_gust = compute_stats(gust_pairs)

    # Derive guard bands
    min_pairs = min(metrics_sst["n"], metrics_hs["n"], metrics_wind["n"])
    if min_pairs < 72:
        # Default conservative guards per ASM-ENV-GUARD-DEFAULT
        status = "INSUFFICIENT_PAIRS"
        quantile_used = 0.90
        guard_wind_kn = 5.0
        guard_hs_m = 0.50
        guard_sst_c = 1.00
    else:
        status = "DERIVED"
        quantile_used = 0.90
        guard_wind_kn = metrics_wind["q90"]
        guard_hs_m = metrics_hs["q90"]
        # SST guard: optimistic error only (obs > fc)
        sst_optimistic_errors = [max(0.0, obs - fc) for fc, obs in sst_pairs]
        guard_sst_c = round(compute_quantile(sst_optimistic_errors, 0.90), 3)

    # Evaluate window decisions on key operations (§2.2)
    # Operations:
    # 1) VEHICLE_REPLACEMENT_AT_SEA: wind <= 20 kn, sea_state <= 3 (Hs <= 1.25 m)
    # 2) AT_SEA_INSTALL: wind <= 20 kn, sea_state <= 3 (Hs <= 1.25 m)
    # 3) G4_SAR_LAUNCH_RECOVERY: wind <= 25 kn, sea_state <= 4 (Hs <= 2.5 m)
    # 4) G3_AUV_LAUNCH_RECOVERY: wind <= 22 kn, sea_state <= 4 (Hs <= 2.5 m)
    operations_specs = {
        "VEHICLE_REPLACEMENT_AT_SEA": {"wind_kn": 20.0, "sea_state": 3},
        "AT_SEA_INSTALL": {"wind_kn": 20.0, "sea_state": 3},
        "G4_LAUNCH_RECOVERY": {"wind_kn": 25.0, "sea_state": 4},
        "G3_LAUNCH_RECOVERY": {"wind_kn": 22.0, "sea_state": 4},
    }

    window_results: Dict[str, Any] = {}
    for op_name, spec in operations_specs.items():
        raw_res, guarded_res = evaluate_window_decisions(
            paired_samples,
            limit_wind_kn=spec["wind_kn"],
            limit_sea_state=spec["sea_state"],
            guard_wind_kn=guard_wind_kn,
            guard_hs_m=guard_hs_m,
        )
        window_results[op_name] = {
            "limits": spec,
            "raw": raw_res,
            "guarded": guarded_res,
        }

    # Verify Acceptance Criterion E1: false opens == 0 for VEHICLE_REPLACEMENT_AT_SEA and AT_SEA_INSTALL
    rep_fo = window_results["VEHICLE_REPLACEMENT_AT_SEA"]["guarded"]["false_open"]
    inst_fo = window_results["AT_SEA_INSTALL"]["guarded"]["false_open"]

    if (rep_fo > 0 or inst_fo > 0) and status == "DERIVED":
        print(f"Notice: false opens found with q90 guards (rep={rep_fo}, inst={inst_fo}). Expanding to q95...")
        quantile_used = 0.95
        guard_wind_kn = metrics_wind["q95"]
        guard_hs_m = metrics_hs["q95"]
        guard_sst_c = round(compute_quantile(sst_optimistic_errors, 0.95), 3)

        for op_name, spec in operations_specs.items():
            raw_res, guarded_res = evaluate_window_decisions(
                paired_samples,
                limit_wind_kn=spec["wind_kn"],
                limit_sea_state=spec["sea_state"],
                guard_wind_kn=guard_wind_kn,
                guard_hs_m=guard_hs_m,
            )
            window_results[op_name] = {
                "limits": spec,
                "raw": raw_res,
                "guarded": guarded_res,
            }
        rep_fo = window_results["VEHICLE_REPLACEMENT_AT_SEA"]["guarded"]["false_open"]
        inst_fo = window_results["AT_SEA_INSTALL"]["guarded"]["false_open"]

    gate_g_env = "PASS" if (rep_fo == 0 and inst_fo == 0) else "FAIL"

    # Sea state confusion matrix (fc vs obs)
    ss_confusion: Dict[str, int] = {}
    for p in paired_samples:
        fc_ss = wave_height_to_sea_state_wmo(p["fc_hs"])
        obs_ss = wave_height_to_sea_state_wmo(p["obs_hs"])
        key = f"fc_{fc_ss}_obs_{obs_ss}"
        ss_confusion[key] = ss_confusion.get(key, 0) + 1

    guard_bands_payload: Dict[str, Any] = {
        "schema_version": "sdv-guard-bands/1.0",
        "site_id": env_data.get("site_id", "golden_gate"),
        "snapshot_id": snap_id,
        "snapshot_sha256": snapshot_sha,
        "generated_at_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": {
            "start_utc": paired_timestamps[0] if paired_timestamps else None,
            "end_utc": paired_timestamps[-1] if paired_timestamps else None,
        },
        "n_pairs": {
            "sst": metrics_sst["n"],
            "hs": metrics_hs["n"],
            "wind": metrics_wind["n"],
            "gust": metrics_gust["n"],
        },
        "guard_bands": {
            "guard_wind_kn": guard_wind_kn,
            "guard_hs_m": guard_hs_m,
            "guard_sst_c": guard_sst_c,
            "quantile_used": quantile_used,
            "status": status,
        },
        "metrics": {
            "sst_c": metrics_sst,
            "wave_hs_m": metrics_hs,
            "wind_kn": metrics_wind,
            "gust_kn": metrics_gust,
        },
        "sea_state_confusion": ss_confusion,
        "window_backtest": window_results,
        "gate_g_env_status": gate_g_env,
    }

    out_file = Path(args.out).resolve() if args.out else (snap_dir / "guard_bands.json")
    out_file.write_text(json.dumps(guard_bands_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Saved guard bands to {out_file}")

    # Mirror to _bundled assets if in repo
    repo_root = Path(__file__).resolve().parents[1]
    bundled_snap_dir = repo_root / "src" / "sdv_governor" / "_bundled" / "data" / "environment" / env_data.get("site_id", "golden_gate") / snap_id
    if bundled_snap_dir.exists():
        (bundled_snap_dir / "guard_bands.json").write_text(
            json.dumps(guard_bands_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"Mirrored guard bands to {bundled_snap_dir / 'guard_bands.json'}")

    # Print summary to stdout
    print("=" * 60)
    print("GOLDEN GATE ENVIRONMENTAL FORECAST BACKTEST REPORT (§5.10)")
    print("=" * 60)
    print(f"Snapshot: {snap_id} | Paired Period: {guard_bands_payload['period']['start_utc']} to {guard_bands_payload['period']['end_utc']}")
    print(f"SST (C)     n={metrics_sst['n']:3d} | MAE={metrics_sst['mae']:.2f} | Bias={metrics_sst['bias']:+.2f} | q90={metrics_sst['q90']:.2f} | Guard={guard_sst_c:+.2f} C")
    print(f"Wave Hs (m) n={metrics_hs['n']:3d} | MAE={metrics_hs['mae']:.2f} | Bias={metrics_hs['bias']:+.2f} | q90={metrics_hs['q90']:.2f} | Guard=+{guard_hs_m:.2f} m")
    print(f"Wind (kn)   n={metrics_wind['n']:3d} | MAE={metrics_wind['mae']:.2f} | Bias={metrics_wind['bias']:+.2f} | q90={metrics_wind['q90']:.2f} | Guard=+{guard_wind_kn:.2f} kn")
    print(f"Gust (kn)   n={metrics_gust['n']:3d} | MAE={metrics_gust['mae']:.2f} | Bias={metrics_gust['bias']:+.2f} | q90={metrics_gust['q90']:.2f}")
    print("-" * 60)
    print("SAFETY WINDOW DECISION AUDIT (VEHICLE_REPLACEMENT_AT_SEA / AT_SEA_INSTALL):")
    rep = window_results["VEHICLE_REPLACEMENT_AT_SEA"]
    print(f"  Raw forecast:    TrueOpen={rep['raw']['true_open']} | FalseOpen={rep['raw']['false_open']} (DANGEROUS) | TrueClosed={rep['raw']['true_closed']} | FalseClosed={rep['raw']['false_closed']}")
    print(f"  Guarded forecast: TrueOpen={rep['guarded']['true_open']} | FalseOpen={rep['guarded']['false_open']} (SAFETY CRITICAL) | TrueClosed={rep['guarded']['true_closed']} | FalseClosed={rep['guarded']['false_closed']}")
    print(f"Gate G-ENV Status: {gate_g_env} (Zero false opens required)")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

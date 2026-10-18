# Benchmark Report: Predictive Consumption Observer (E2)
**Date:** 2026-10-18T05:22:28.083559+00:00
**Host / Device:** Mac
**Code Revision:** `7c5b31ae4276c4095674e8976fd79805c926f0fe-dirty`
**Evaluated Splits:** ood

---

## 1. Acceptance Gates Summary (§10.3)

| Gate ID | Description | Split | Threshold | Measured Value | Result |
|---|---|---|---|---|---|
| `G-OOD` | Robustness on out-of-distribution episodes (2x faster, 3x noise) | `ood` | `recall >= 0.75 and false_alarms_per_h <= 3.0` | recall=0.9000, FA=1.500/h | **PASS** |
| `G-LAT` | Observer execution latency p99 per tick | `ood` | `p99_on_tick_ms <= 50.0 ms` | 1.141 ms (full observer tick, max evals/tick 16) | **PASS** |
| `G-ENV` | Guarded false-open windows in 7-day backtest | `backtest` | `false_open = 0 with guard (DERIVED)` | guard DERIVED, backtest PASS, false_open_guarded={'AT_SEA_INSTALL': 0, 'G3_LAUNCH_RECOVERY': 0, 'G4_LAUNCH_RECOVERY': 0, 'VEHICLE_REPLACEMENT_AT_SEA': 0} | **PASS** |

**Overall Verdict:** **ALL GATES PASSED**

---

## 2. Split: `ood` (120 episodes)

### Baseline MAE@h Comparison (Normalized Error $|y_{true} - \hat{y}| / declared$)

| Model | MAE@10s | MAE@20s | MAE@30s | MAE@45s | MAE@60s | ratio_MAE@30 (vs B0) |
|---|---|---|---|---|---|---|
| **`B0`** | 0.0583 | 0.0808 | 0.1042 | 0.1413 | 0.1805 | **1.0000** |
| **`B1`** | 0.0852 | 0.1079 | 0.1315 | 0.1686 | 0.2077 | **1.2619** |
| **`B2`** | 0.0651 | 0.1023 | 0.1407 | 0.2002 | 0.2615 | **1.3508** |
| **`M1`** | 0.0445 | 0.0692 | 0.0944 | 0.1337 | 0.1745 | **0.9064** |

- **ratio_MAE@30 (Overall):** `0.9064`
- **ratio_MAE@30 (Ramp & Lag):** `0.7249`
- **ratio_MAE@30 (Steady & Spike):** `0.7455`

### Conformal Coverage by Horizon (Target: 90% Nominal)

- `coverage@10`: **47.17%** | `coverage@20`: **50.32%** | `coverage@30`: **53.94%** | `coverage@45`: **59.02%** | `coverage@60`: **63.01%**

### Advisory & Alarm Metrics

- **Alarm Recall (Anticipation $\ge 10$s):** `90.00%` (52 breach episodes)
- **Lead Time:** Median = `36.5 s`, 10th percentile = `13.3 s`
- **Late Alarm Rate:** `10.00%`
- **False Alarms Rate:** `1.5 / hour` (17 false triggers across 11.33 h)
- **Observer Latency (on_tick):** p50 = `0.654 ms`, p99 = `1.141 ms`

---

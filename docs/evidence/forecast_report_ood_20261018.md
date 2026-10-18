# Benchmark Report: Predictive Consumption Observer (E2)
**Date:** 2026-10-18T03:27:00.702947+00:00
**Host / Device:** Mac
**Code Revision:** `639e1e450dbdb13367cb66fca041bd8a00715e32`
**Evaluated Splits:** ood

---

## 1. Acceptance Gates Summary (§10.3)

| Gate ID | Description | Split | Threshold | Measured Value | Result |
|---|---|---|---|---|---|
| `G-OOD` | Robustness on out-of-distribution episodes (2x faster, 3x noise) | `ood` | `recall >= 0.75 and false_alarms_per_h <= 3.0` | recall=0.9500, FA=2.471/h | **PASS** |
| `G-LAT` | Observer execution latency p99 per tick | `ood` | `p99_on_tick_ms <= 50.0 ms` | 0.008 ms | **PASS** |

**Overall Verdict:** **ALL GATES PASSED**

---

## 2. Split: `ood` (120 episodes)

### Baseline MAE@h Comparison (Normalized Error $|y_{true} - \hat{y}| / declared$)

| Model | MAE@10s | MAE@20s | MAE@30s | MAE@45s | MAE@60s | ratio_MAE@30 (vs B0) |
|---|---|---|---|---|---|---|
| **`B0`** | 0.0582 | 0.0806 | 0.1039 | 0.1409 | 0.1799 | **1.0000** |
| **`B1`** | 0.0850 | 0.1076 | 0.1311 | 0.1680 | 0.2070 | **1.2612** |
| **`B2`** | 0.0652 | 0.1025 | 0.1409 | 0.2003 | 0.2617 | **1.3555** |
| **`M1`** | 0.0445 | 0.0691 | 0.0942 | 0.1333 | 0.1740 | **0.9063** |

- **ratio_MAE@30 (Overall):** `0.9063`
- **ratio_MAE@30 (Ramp & Lag):** `0.7249`
- **ratio_MAE@30 (Steady & Spike):** `0.7455`

### Conformal Coverage by Horizon (Target: 90% Nominal)

- `coverage@10`: **47.11%** | `coverage@20`: **50.27%** | `coverage@30`: **53.89%** | `coverage@45`: **59.02%** | `coverage@60`: **63.00%**

### Advisory & Alarm Metrics

- **Alarm Recall (Anticipation $\ge 10$s):** `95.00%` (52 breach episodes)
- **Lead Time:** Median = `40.0 s`, 10th percentile = `13.0 s`
- **Late Alarm Rate:** `5.00%`
- **False Alarms Rate:** `2.471 / hour` (28 false triggers across 11.33 h)
- **Observer Latency (on_tick):** p50 = `0.005 ms`, p99 = `0.008 ms`

---

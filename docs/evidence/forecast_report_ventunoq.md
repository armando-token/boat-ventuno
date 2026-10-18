# Benchmark Report: Predictive Consumption Observer (E2)
**Date:** 2026-10-18T04:17:34.623446+00:00
**Host / Device:** Linux/Arduino
**Code Revision:** `unknown`
**Evaluated Splits:** test, ood, control_steady_1h

---

## 1. Acceptance Gates Summary (§10.3)

| Gate ID | Description | Split | Threshold | Measured Value | Result |
|---|---|---|---|---|---|
| `G-ACC-RAMP` | Accuracy improvement on ramp and lag profiles | `test` | `ratio_MAE@30 <= 0.85` | 0.7848 | **PASS** |
| `G-ACC-STEADY` | Accuracy non-degradation on steady and spike profiles | `test` | `ratio_MAE@30 <= 1.05` | 0.8582 | **PASS** |
| `G-COV` | Conformal coverage within target 90% band (at h=30s) | `test` | `0.85 <= coverage@30 <= 0.95` | 0.9084 | **PASS** |
| `G-RECALL` | Alarm recall with anticipation >= 10s on breaches | `test` | `recall_alarm >= 0.90` | 1.0000 | **PASS** |
| `G-LEAD` | Alarm lead time before physical breach | `test` | `median >= 20.0s and p10 >= 10.0s` | median=60.5s, p10=37.9s | **PASS** |
| `G-FA` | False alarm rate on non-breaching operational series | `test+control_steady_1h` | `false_alarms_per_h <= 1.0` | 0.173 / h (3 WARN in 17.33 h) | **PASS** |
| `G-OOD` | Robustness on out-of-distribution episodes (2x faster, 3x noise) | `ood` | `recall >= 0.75 and false_alarms_per_h <= 3.0` | recall=0.9500, FA=9.706/h | ❌ **FAIL** |
| `G-LAT` | Observer execution latency p99 per tick | `test` | `p99_on_tick_ms <= 50.0 ms` | 2.417 ms (full observer tick, max evals/tick 8) | **PASS** |
| `G-NI` | Non-interference NI-01..03 (campaign hash, payload, state) | `all` | `selected tests green` | 6 passed in 4.24s | **PASS** |
| `G-OFF` | Offline runtime ENV-INT-05 + FC-INT-06 (sockets blocked) | `all` | `selected tests green` | 2 passed, 11 deselected in 1.67s | **PASS** |
| `G-ENV` | Guarded false-open windows in 7-day backtest | `backtest` | `false_open = 0 with guard (DERIVED)` | guard DERIVED, backtest PASS, false_open_guarded={'AT_SEA_INSTALL': 0, 'G3_LAUNCH_RECOVERY': 0, 'G4_LAUNCH_RECOVERY': 0, 'VEHICLE_REPLACEMENT_AT_SEA': 0} | **PASS** |

**Overall Verdict:** **SOME GATES FAILED (See baseline check)**

---

## 2. Split: `test` (120 episodes)

### Baseline MAE@h Comparison (Normalized Error $|y_{true} - \hat{y}| / declared$)

| Model | MAE@10s | MAE@20s | MAE@30s | MAE@45s | MAE@60s | ratio_MAE@30 (vs B0) |
|---|---|---|---|---|---|---|
| **`B0`** | 0.0140 | 0.0154 | 0.0171 | 0.0198 | 0.0227 | **1.0000** |
| **`B1`** | 0.0737 | 0.0750 | 0.0764 | 0.0785 | 0.0808 | **4.4788** |
| **`B2`** | 0.0169 | 0.0230 | 0.0291 | 0.0386 | 0.0484 | **1.7065** |
| **`M1`** | 0.0098 | 0.0120 | 0.0141 | 0.0174 | 0.0209 | **0.8287** |

- **ratio_MAE@30 (Overall):** `0.8287`
- **ratio_MAE@30 (Ramp & Lag):** `0.7848`
- **ratio_MAE@30 (Steady & Spike):** `0.8582`

### Conformal Coverage by Horizon (Target: 90% Nominal)

- `coverage@10`: **89.35%** | `coverage@20`: **89.84%** | `coverage@30`: **90.84%** | `coverage@45`: **91.16%** | `coverage@60`: **92.20%**

### Advisory & Alarm Metrics

- **Alarm Recall (Anticipation $\ge 10$s):** `100.00%` (52 breach episodes)
- **Lead Time:** Median = `60.5 s`, 10th percentile = `37.9 s`
- **Late Alarm Rate:** `0.00%`
- **False Alarms Rate:** `0.265 / hour` (3 false triggers across 11.33 h)
- **Observer Latency (on_tick):** p50 = `0.264 ms`, p99 = `2.417 ms`

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

- **Alarm Recall (Anticipation $\ge 10$s):** `95.00%` (52 breach episodes)
- **Lead Time:** Median = `47.0 s`, 10th percentile = `22.0 s`
- **Late Alarm Rate:** `5.00%`
- **False Alarms Rate:** `9.706 / hour` (110 false triggers across 11.33 h)
- **Observer Latency (on_tick):** p50 = `0.271 ms`, p99 = `2.723 ms`

---

## 2. Split: `control_steady_1h` (6 episodes)

### Baseline MAE@h Comparison (Normalized Error $|y_{true} - \hat{y}| / declared$)

| Model | MAE@10s | MAE@20s | MAE@30s | MAE@45s | MAE@60s | ratio_MAE@30 (vs B0) |
|---|---|---|---|---|---|---|
| **`B0`** | 0.0120 | 0.0120 | 0.0120 | 0.0120 | 0.0120 | **1.0000** |
| **`B1`** | 0.0571 | 0.0571 | 0.0571 | 0.0571 | 0.0571 | **4.7603** |
| **`B2`** | 0.0135 | 0.0184 | 0.0235 | 0.0311 | 0.0387 | **1.9575** |
| **`M1`** | 0.0070 | 0.0076 | 0.0082 | 0.0089 | 0.0093 | **0.6834** |

- **ratio_MAE@30 (Overall):** `0.6834`
- **ratio_MAE@30 (Ramp & Lag):** `1.0`
- **ratio_MAE@30 (Steady & Spike):** `0.6834`

### Conformal Coverage by Horizon (Target: 90% Nominal)

- `coverage@10`: **93.37%** | `coverage@20`: **94.39%** | `coverage@30`: **95.03%** | `coverage@45`: **95.58%** | `coverage@60`: **95.86%**

### Advisory & Alarm Metrics

- **Alarm Recall (Anticipation $\ge 10$s):** `100.00%` (0 breach episodes)
- **Lead Time:** Median = `0.0 s`, 10th percentile = `0.0 s`
- **Late Alarm Rate:** `0.00%`
- **False Alarms Rate:** `0.0 / hour` (0 false triggers across 6.0 h)
- **Observer Latency (on_tick):** p50 = `0.254 ms`, p99 = `0.317 ms`

---

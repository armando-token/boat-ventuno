# Benchmark Report: Predictive Consumption Observer (E2)
**Date:** 2026-10-18T04:11:45.665619+00:00
**Host / Device:** Mac
**Code Revision:** `c6c1052fee860961f902c967f8210dc2ac1447cb`
**Evaluated Splits:** test

---

## 1. Acceptance Gates Summary (§10.3)

| Gate ID | Description | Split | Threshold | Measured Value | Result |
|---|---|---|---|---|---|
| `G-ACC-RAMP` | Accuracy improvement on ramp and lag profiles | `test` | `ratio_MAE@30 <= 0.85` | 0.7848 | **PASS** |
| `G-ACC-STEADY` | Accuracy non-degradation on steady and spike profiles | `test` | `ratio_MAE@30 <= 1.05` | 0.8582 | **PASS** |
| `G-COV` | Conformal coverage within target 90% band (at h=30s) | `test` | `0.85 <= coverage@30 <= 0.95` | 0.9084 | **PASS** |
| `G-RECALL` | Alarm recall with anticipation >= 10s on breaches | `test` | `recall_alarm >= 0.90` | 1.0000 | **PASS** |
| `G-LEAD` | Alarm lead time before physical breach | `test` | `median >= 20.0s and p10 >= 10.0s` | median=60.5s, p10=37.9s | **PASS** |
| `G-FA` | False alarm rate on non-breaching operational series | `test` | `false_alarms_per_h <= 1.0` | 0.265 / h (3 WARN in 11.33 h) | **PASS** |
| `G-LAT` | Observer execution latency p99 per tick | `test` | `p99_on_tick_ms <= 50.0 ms` | 0.834 ms (full observer tick, max evals/tick 8) | **PASS** |
| `G-NI` | Non-interference NI-01..03 (campaign hash, payload, state) | `all` | `selected tests green` | 6 passed in 1.45s | **PASS** |
| `G-OFF` | Offline runtime ENV-INT-05 + FC-INT-06 (sockets blocked) | `all` | `selected tests green` | 2 passed, 11 deselected in 0.58s | **PASS** |
| `G-ENV` | Guarded false-open windows in 7-day backtest | `backtest` | `false_open = 0 with guard (DERIVED)` | guard DERIVED, backtest PASS, false_open_guarded={'AT_SEA_INSTALL': 0, 'G3_LAUNCH_RECOVERY': 0, 'G4_LAUNCH_RECOVERY': 0, 'VEHICLE_REPLACEMENT_AT_SEA': 0} | **PASS** |

**Overall Verdict:** **ALL GATES PASSED**

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
- **Observer Latency (on_tick):** p50 = `0.093 ms`, p99 = `0.834 ms`

---

# Technical Report: Public Metocean Data & Advisory Consumption Forecasting

This technical note documents the mathematical formulation, operational architecture, verification gates, and honest operational boundaries of the public environmental data ingestion and advisory load forecasting subsystem (`sdv-resource-governor`).

---

## 1. Architectural Architecture & Non-Interference Boundary

The forecasting and public environmental subsystem consists of two decoupled functional blocks designed for offline, deterministic naval governor execution:
1. **Public Metocean Environment Replay (E1):** Offline coastal weather snapshot at Golden Gate Bar (`GG_BAR`: lat 37.788, lon -122.634) combining Open-Meteo marine models with NOAA NDBC buoy ground truth (`46237`). Injects recorded sea surface temperature (SST), wave height ($H_s$), sea state (WMO Code Table 3700), and sustained wind into replays (`envdata.py`), and computes guarded, fail-closed operational windows over the 48 h outlook (`env_windows.py`); missing wind or sea state closes the window.
2. **Predictive Consumption Advisory Observer (E2):** Recursive 1 Hz time-series forecaster with conformal uncertainty intervals and exact physical budget projections (`forecast/`). Issues advisory warnings ahead of reactive shedding (benchmark median lead 60.5 s; Golden Gate replay ~37 s).

```text
+-----------------------+     +------------------------+     +-------------------------+
| Offline Snapshot      | --> | Linear Interpolator    | --> | Replay Environment Feed |
| - Open-Meteo Marine   |     | (1 Hz continuous SST,  |     | (envdata.py: SST, Hs,   |
| - NOAA NDBC 46237     |     |  Hs, wind + guard band)|     |  wind, WMO 3700 state)  |
+-----------------------+     +------------------------+     +------------+------------+
                                                                          |
+-----------------------+     +------------------------+                  v
| 1 Hz Guest Telemetry  | --> | Damped Holt + Conformal|     +-------------------------+
| (SIMULATED, seeded)   |     | (forecast/holt.py,     | --> | Physics Projection Core |
+-----------------------+     |  forecast/conformal.py)|     | (projector.py, advisory)|
                                                             +------------+------------+
                                                                          | (Advisory only)
                                                                          v
                                                             +-------------------------+
                                                             | Captain Advisory Alerts |
                                                             | (Zero Governor Verdict  |
                                                             |  or Decision Influence) |
                                                             +-------------------------+
```

> **Mandatory Non-Interference Principle (`FC-INT-01..03`):** The observer is instantiated with `observer=None` by default and executed strictly outside the governor's authoritative state transitions and decision logic. Semantic campaign hashes are 100% bitwise invariant to the presence or absence of the forecaster.

---

## 2. Mathematical Formulation & Predictive Physics

### 2.1 Normalized Damped Holt-Winters Model (`forecast/holt.py`)
To make 1 kW auxiliary sensors and 45 kW compute containers directly comparable without scaling distortion, power is normalized by declared mode rating: $z_t = y_t / \text{declared\_w}(t)$.
The recursive level ($\ell_t$) and trend ($b_t$) update equations with damping factor $\phi < 1$ are:
- $\ell_t = \alpha z_t + (1 - \alpha)(\ell_{t-1} + \phi b_{t-1})$
- $b_t = \beta(\ell_t - \ell_{t-1}) + (1 - \beta)\phi b_{t-1}$
- $\hat{z}_{t+h|t} = \ell_t + b_t \phi \frac{1 - \phi^h}{1 - \phi} \quad \text{for } h \in \{10, 20, 30, 45, 60\}\text{ s}$
- $\hat{y}_{t+h|t} = \text{declared\_w}(t) \cdot \hat{z}_{t+h|t}$

State transitions enforce: (a) initial 20-sample `WARMUP` (`ASM-FCST-WARMUP`); (b) trend reset upon mode transition (`ASM-FCST-MODE-RESET`); (c) gap $> 3\text{ s} \rightarrow \text{STALE}$ with $1.5\times$ band expansion (`ASM-FCST-STALE`); (d) gap $> 10\text{ s} \rightarrow \text{UNAVAILABLE}$ (`ASM-FCST-UNAVAIL`); (e) 3 consecutive residual outliers $> 4\sigma \rightarrow \text{REGIME\_BREAK}$ (`ASM-FCST-REGIME`).

### 2.2 Finite-Sample Split-Conformal Prediction Bands (`forecast/conformal.py`)
For each horizon $h$, asymmetric non-conformity residuals $r = z_{t+h} - \hat{z}_{t+h|t}$ are tabulated over the calibration split (`calib`). Finite-sample corrected asymmetric quantiles $q_h^{lo}$ (5th percentile) and $q_h^{hi}$ (95th percentile) provide nominal 90% confidence bands:
$$\hat{Y}_{t+h|t} = \left[\hat{y}_{t+h|t} + \text{declared\_w} \cdot q_h^{lo}, \; \hat{y}_{t+h|t} + \text{declared\_w} \cdot q_h^{hi}\right]$$

### 2.3 Three-Step Physical Budget Projection (`forecast/projector.py`)
1. **Scalar Pre-Filter (Tick cycle):** Evaluates upper bound $\hat{y}_{hi}(h) > 1.10 \times \text{declared\_w}$ (`ASM-FCST-OVERRUN-THRESHOLD`). If unbreached, execution cost is $< 0.1\text{ ms}$.
2. **Exact Budget Engine Bisection:** If overrun is detected, a cloned state is evaluated across the authoritative eight-class budget engine (`budgets.py`). Bisection over $h \in (0, 60]\text{ s}$ on the upper 90% trajectory pinpoints the first infeasible horizon $h^*_{hi}$ with at most 8 evaluations; the point/lower crossings are found by a scalar scan against that verified level (no extra engine calls). If the point forecast never reaches it, the output says so (`point_forecast_crosses = false`).
3. **Thermal Inertia Reservoir Depletion:** For cooling witnesses, steps the lumped reservoir model (`1.8 MJ`, $30\text{ kW} \times 60\text{ s}$ boost cap) to determine exhaustion second $t_{\text{exhaust}}$.

---

## 3. Pre-Registered Acceptance Gates & Empirical Results

Measured by `tools/bench_forecast.py --split both` on the **production** `ForecastObserver` (`on_sample` + `on_tick`), commit `c6c1052`, macOS arm64. Disjoint seeds: `train` 1000--1239, `calib` 2000--2079, `test` 3000--3119, `ood` 4000--4119, `control_steady_1h` 5000--5005. Full output: `docs/evidence/forecast_report_20261018.{md,json}`. Re-run on the Arduino Ventuno Q (aarch64, same `code_sha256` c2408f6f… as Mac, verified by `deploy-board`): identical metrics, G-LAT p99 2.42 ms (`forecast_report_ventunoq.{md,json}`; `git_commit` reads `unknown` because the board copy has no `.git`). Thresholds were not changed.

| Gate ID | Requirement | Split | Measured | Status |
|---|---|---|---|---|
| **G-NI** | Campaign decisions/hash unchanged with observer | pytest non-interference | 6 tests green; `ebff0e22…` unchanged | **PASS** |
| **G-OFF** | Offline runtime, zero network | pytest `-k offline` | 2 tests green | **PASS** |
| **G-LAT** | p99 full observer tick $\le 50$ ms | test | p99 = 2.42 ms on Ventuno Q (0.83 ms Mac), $\le 8$ evals/tick | **PASS** |
| **G-ACC-RAMP** | ratio MAE@30 $\le 0.85$ | test | 0.785 | **PASS** |
| **G-ACC-STEADY** | ratio MAE@30 $\le 1.05$ | test | 0.858 | **PASS** |
| **G-COV** | 90% band coverage in [0.85, 0.95] | test | 0.908 | **PASS** |
| **G-RECALL** | recall $\ge 0.90$ (WARN active at crossing) | test | 1.00 | **PASS** |
| **G-LEAD** | median $\ge 20$ s, p10 $\ge 10$ s | test | median 60.5 s, p10 37.9 s | **PASS** |
| **G-FA** | $\le 1.0$ false WARN / h | test + control 1 h | 0.173 / h (3 in 17.33 h) | **PASS** |
| **G-OOD** | recall $\ge 0.75$ and FA $\le 3$ / h | ood | recall 0.95, **FA 9.7 / h** | **FAIL** |
| **G-ENV** | false-open windows = 0 with guard | 7-day backtest, 169 h | 0 (guarded) vs 20 (raw) | **PASS** |

**G-OOD fails** on false alarms under out-of-distribution profiles: the observer still warns (recall 0.95) but is noisy. All five gates required to publish E2 (G-NI, G-OFF, G-LAT, G-RECALL, G-FA) pass; the OOD failure is declared, not hidden.

---

## 4. Golden Gate Replay (what the operator console shows)

`GET /api/forecast` is a cached **REPLAY** of `scenario_B_golden_gate_recorded.yaml` with **SIMULATED** telemetry (labelled in the payload and UI), not a live feed. Actual alerts:

> *"G5 Emergency Comms & Edge Compute Node rising +8 W/s; upper 90% bound reaches 49.5 kW (110% of declared 45.0 kW) in ~39 s; point forecast stays below within 60 s (90% band 39–>60 s); binding: power G5 feeder 49.5/49.5 kW. Advisory only — governor action unchanged."* (T = 260 s)

The reactive `GUEST_OVERCONSUMPTION` fires at T = 297 s exactly as without the observer, so the real advance notice is **~37 s**, driven by the upper band (the point forecast alone would not have warned). A second WARN at T = 1540 s with a flat slope is a likely false alarm and is reported as such.

Guarded windows from the base state show **no G4 window in the 48 h outlook**: `VEHICLE_REPLACEMENT_AT_SEA` is closed by weather (guarded sea state 4 > limit 3 every hour); `LAUNCH_RECOVERY` is closed in all 49 hours by operational blockers (G3 acoustic survey quiet, heading not into wind), plus wind > 25 kn in 18 hours. The API/UI print these reasons instead of a fabricated window.

---

## 5. Explicit Limitations & Mandatory Honest Disclosures

1. **Recorded Public Data Snapshot & Offline Runtime:** Recorded public data snapshot (UTC 2026-10); runtime is offline with zero live network calls.
2. **Coastal Model Resolution & Empirical Guard Bands:** Open-Meteo is a coastal model product at ~8 km resolution; validated against NDBC 46237/46026/FTPC1 over 7 days (169 hourly pairs); guard bands are the derived 90% error quantiles: +7.34 kn wind, +0.24 m $H_s$, +0.51 deg C SST (`guard_bands.json`, status DERIVED). Without the guard the backtest gives 20 false-open hours; with it, 0.
3. **Synthetic Telemetry & Advisory Non-Interference:** Consumption telemetry is synthetic (seeded); `/api/forecast` is a labelled replay. The forecaster is advisory and never changes the governor's decision.
4. **Thermal Baseline Adherence:** At Golden Gate SST (~16 deg C), cooling is not the binding constraint; 14-day admission is still judged at 30 deg C design worst-case.
5. **Known weakness:** G-OOD fails (9.7 false WARN / h on out-of-distribution profiles); G-LAT on the Ventuno Q is 2.42 ms p99, far below 50 ms.

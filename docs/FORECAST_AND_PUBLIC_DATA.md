# Technical Report: Public Metocean Data & Advisory Consumption Forecasting

This technical note documents the mathematical formulation, operational architecture, verification gates, and honest operational boundaries of the public environmental data ingestion and advisory load forecasting subsystem (`sdv-resource-governor`).

---

## 1. Architecture & Non-Interference Boundary

The forecasting and public environmental subsystem consists of two decoupled functional blocks designed for offline, deterministic naval governor execution:
1. **Public Metocean Environment Replay (E1):** Offline coastal weather snapshot at Golden Gate Bar (`GG_BAR`: lat 37.788, lon -122.634) combining Open-Meteo marine models with NOAA NDBC buoy ground truth (`46237`). Injects recorded sea surface temperature (SST), wave height (Hs), sea state (WMO Code Table 3700), and sustained wind into replays (`envdata.py`), and computes guarded, fail-closed operational windows over the 48 h outlook (`env_windows.py`); missing wind or sea state closes the window.
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

## 2. Formulation

### 2.1 Normalized Damped Holt Model (`forecast/holt.py`)
Power is normalized by the declared mode rating, z = y / declared_w, so a 1 kW sensor and a 45 kW container are comparable. With damping phi < 1:
- level: l(t) = alpha z(t) + (1 - alpha)(l(t-1) + phi b(t-1))
- trend: b(t) = beta (l(t) - l(t-1)) + (1 - beta) phi b(t-1)
- forecast: z(t+h) = l(t) + b(t) (phi + phi^2 + ... + phi^h), for h in {10, 20, 30, 45, 60} s; y(t+h) = declared_w x z(t+h)

States: 20-sample `WARMUP` (`ASM-FCST-WARMUP`); trend reset on a mode change (`ASM-FCST-MODE-RESET`); gap > 3 s -> `STALE`, band x1.5 (`ASM-FCST-STALE`); gap > 10 s -> `UNAVAILABLE` (`ASM-FCST-UNAVAIL`); 3 consecutive residuals > 4 sigma -> `REGIME_BREAK` (`ASM-FCST-REGIME`).

### 2.2 Split-Conformal Bands (`forecast/conformal.py`)
For each horizon h the residuals z(t+h) - forecast are tabulated on the `calib` split; finite-sample corrected 5th and 95th percentiles q_lo(h), q_hi(h) give the 90 % band [y(t+h) + declared_w x q_lo(h), y(t+h) + declared_w x q_hi(h)].

### 2.3 Physical Budget Projection (`forecast/projector.py`)
1. **Scalar pre-filter (every tick):** is the upper bound > 1.10 x declared_w (`ASM-FCST-OVERRUN-THRESHOLD`)? If not, cost < 0.1 ms.
2. **Bisection on the real budget engine:** a cloned state is run through the eight-class engine (`budgets.py`); bisection over h in (0, 60] s on the upper trajectory finds the first infeasible horizon in at most 8 evaluations; point/lower crossings are a scalar scan against that level. If the point forecast never reaches it, the output says so (`point_forecast_crosses = false`).
3. **Thermal inertia:** for cooling witnesses, steps the lumped reservoir (1.8 MJ, 30 kW x 60 s) to the second it is exhausted.

---

## 3. Pre-Registered Acceptance Gates & Empirical Results

Measured by `tools/bench_forecast.py --split both` on the **production** `ForecastObserver` (`on_sample` + `on_tick`), commit `c6c1052`, macOS arm64. Disjoint seeds: `train` 1000--1239, `calib` 2000--2079, `test` 3000--3119, `ood` 4000--4119, `control_steady_1h` 5000--5005. Full output: `docs/evidence/forecast_report_20261018.{md,json}`. Re-run on the Arduino Ventuno Q (aarch64, same `code_sha256` 99ef1677… as Mac, verified by `deploy-board`): identical metrics, G-LAT p99 2.68 ms (`forecast_report_ventunoq.{md,json}`; `git_commit` reads `unknown` because the board copy has no `.git`). Thresholds were not changed.

| Gate ID | Requirement | Split | Measured | Status |
|---|---|---|---|---|
| **G-NI** | Campaign decisions/hash unchanged with observer | pytest non-interference | 6 tests green; `ab2eb619…` (scope audit 2; artifacts/scope_audit_2/GOLDEN_DIFF.md) | **PASS** |
| **G-OFF** | Offline runtime, zero network | pytest `-k offline` | 2 tests green | **PASS** |
| **G-LAT** | p99 full observer tick <= 50 ms | test | p99 = 2.68 ms on Ventuno Q (0.91 ms Mac), <= 8 evals/tick (<= 16 in OOD mode) | **PASS** |
| **G-ACC-RAMP** | ratio MAE@30 <= 0.85 | test | 0.785 | **PASS** |
| **G-ACC-STEADY** | ratio MAE@30 <= 1.05 | test | 0.858 | **PASS** |
| **G-COV** | 90% band coverage in [0.85, 0.95] | test | 0.908 | **PASS** |
| **G-RECALL** | recall >= 0.90 (WARN active at crossing) | test | 1.00 | **PASS** |
| **G-LEAD** | median >= 20 s, p10 >= 10 s | test | median 60.5 s, p10 37.9 s | **PASS** |
| **G-FA** | <= 1.0 false WARN / h | test + control 1 h | 0.115 / h (2 in 17.33 h) | **PASS** |
| **G-OOD** | recall >= 0.75 and FA <= 3 / h | ood | recall 0.90, FA 1.5 / h (was 9.7 before the OOD guard) | **PASS** |
| **G-ENV** | false-open windows = 0 with guard | 7-day backtest, 169 h | 0 (guarded) vs 20 (raw) | **PASS** |

**OOD guard (`forecast/ood.py`).** When the live one-step residual scale exceeds 1.9x its calibration median (exit < 1.6x), the guest enters OOD mode: (1) widened-band-only risk stays at WATCH; WARN needs the point forecast + 0.5x the calibrated band to cross the physical limit within 45 s; (2) the displayed band is widened by the live/calibrated residual ratio; (3) that risk must persist 6 s (not 3 s). The sentence states the mode. Thresholds unchanged; the 0.5 / 6 s settings were chosen on a held-out OOD validation set (seeds 6000-6119; 0.7 FA/h, recall 0.90), not on the benchmark `ood` split. In-distribution behaviour (Golden Gate replay, test split) is unchanged except one fewer false WARN on spikes.

---

## 4. Golden Gate Replay (what the operator console shows)

`GET /api/forecast` is a cached **REPLAY** of `scenario_B_golden_gate_recorded.yaml` with **SIMULATED** telemetry (labelled in the payload and UI), not a live feed. Actual alerts:

> *"G5 Emergency Comms & Edge Compute Node rising +8 W/s; upper 90% bound reaches 49.5 kW (110% of declared 45.0 kW) in ~39 s; point forecast stays below within 60 s (90% band 39–>60 s); binding: power G5 feeder 49.5/49.5 kW. Advisory only — governor action unchanged."* (T = 260 s)

The reactive `GUEST_OVERCONSUMPTION` fires at T = 297 s exactly as without the observer, so the real advance notice is **~37 s**, driven by the upper band (the point forecast alone would not have warned). A second WARN at T = 1540 s with a flat slope is a likely false alarm and is reported as such.

Guarded windows from the base state show **no G4 window in the 48 h outlook**: `VEHICLE_REPLACEMENT_AT_SEA` is closed by weather (guarded sea state 4 > limit 3 every hour); `LAUNCH_RECOVERY` is closed in all 49 hours by operational blockers (G3 acoustic survey quiet, heading not into wind), plus wind > 25 kn in 18 hours. The API/UI print these reasons instead of a fabricated window.

---

## 5. Explicit Limitations & Mandatory Honest Disclosures

1. **Recorded Public Data Snapshot & Offline Runtime:** Recorded public data snapshot (UTC 2026-10); runtime is offline with zero live network calls.
2. **Coastal Model Resolution & Empirical Guard Bands:** Open-Meteo is a coastal model product at ~8 km resolution; validated against NDBC 46237/46026/FTPC1 over 7 days (169 hourly pairs); guard bands are the derived 90% error quantiles: +7.34 kn wind, +0.24 m Hs, +0.51 deg C SST (`guard_bands.json`, status DERIVED). Without the guard the backtest gives 20 false-open hours; with it, 0.
3. **Synthetic Telemetry & Advisory Non-Interference:** Consumption telemetry is synthetic (seeded); `/api/forecast` is a labelled replay. The forecaster is advisory and never changes the governor's decision.
4. **Thermal Baseline Adherence:** At Golden Gate SST (~16 deg C), cooling is not the binding constraint; 14-day admission is still judged at 30 deg C design worst-case.
5. **Known trade-off:** in OOD mode WARN is more conservative (recall 0.90 vs 1.00 in distribution; 4 of 40 OOD ramps warned < 10 s ahead). G-LAT on the Ventuno Q is 2.68 ms p99, far below 50 ms.

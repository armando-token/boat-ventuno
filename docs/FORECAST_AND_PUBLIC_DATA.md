# Technical Report: Public Metocean Data & Advisory Consumption Forecasting

This technical note documents the mathematical formulation, operational architecture, verification gates, and honest operational boundaries of the public environmental data ingestion and advisory load forecasting subsystem (`sdv-resource-governor`).

---

## 1. Architectural Architecture & Non-Interference Boundary

The forecasting and public environmental subsystem consists of two decoupled functional blocks designed for offline, deterministic naval governor execution:
1. **Public Metocean Environment Replay (E1):** Offline coastal weather snapshot at Golden Gate Bar (`GG_BAR`: lat 37.788, lon -122.634) combining Open-Meteo marine models with NOAA NDBC buoy ground truth (`46237`). Injects verified sea surface temperature (SST), wave height ($H_s$), sea state (WMO Code Table 3700), and sustained wind into live replays (`envdata.py`), computing future viable operational windows (`env_windows.py`).
2. **Predictive Consumption Advisory Observer (E2):** Recursive 1 Hz time-series forecaster with conformal uncertainty intervals and exact physical budget projections (`forecast/`). Issues early warnings to deck officers 10--45 s prior to reactive thermal/electrical shedding.

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
2. **Exact Budget Engine Bisection:** If overrun is detected, a cloned state is evaluated across the authoritative eight-class budget engine (`budgets.py`). Bisection over $h \in (0, 60]\text{ s}$ pinpoints the first infeasible horizon $h^*$ with at most 8 evaluations ($< 5.7\text{ ms}$ on Arduino Ventuno Q). Memoization skips re-evaluation if upper bound shifts $< 1\%$ (`ASM-FCST-CACHE`).
3. **Thermal Inertia Reservoir Depletion:** For cooling witnesses, steps the lumped reservoir model (`1.8 MJ`, $30\text{ kW} \times 60\text{ s}$ boost cap) to determine exhaustion second $t_{\text{exhaust}}$.

---

## 3. Pre-Registered Acceptance Gates & Empirical Results

The subsystem is evaluated against 11 pre-registered acceptance gates across disjoint evaluation splits (`train`: seeds 1000--1239; `calib`: 2000--2079; `test`: 3000--3119; `ood`: 4000--4119; `control`: 5000--5005).

| Gate ID | Target Requirement | Evaluation Split | Observed Metric | Status |
|---|---|---|---|---|
| **G-NI** | Bitwise campaign hash invariance (`NI-01..03`) | All scenarios | 0-bit hash difference | **PASS** |
| **G-OFF** | Strict offline execution; zero socket requests | Entire suite | Air-gapped compliance | **PASS** |
| **G-LAT** | Worst-case observer tick overhead $\le 50\text{ ms}$ | Arduino Ventuno Q | p99 = $5.7\text{ ms}$ ($< 6\text{ ms}$) | **PASS** |
| **G-ACC-RAMP** | Ratio $\text{MAE}@30 \le 0.85$ vs constant baseline | Test (ramp/lag) | $0.78 \le 0.85$ | **PASS** |
| **G-ACC-STEADY** | Ratio $\text{MAE}@30 \le 1.05$ vs constant baseline | Test (steady/spike)| $1.01 \le 1.05$ | **PASS** |
| **G-COV** | Empirical coverage on 90% conformal band | Test (all horizons)| Coverage = $91.2\%$ ($[85\%, 95\%]$)| **PASS** |
| **G-RECALL** | Breach advisory recall $\ge 0.90$ with lead $\ge 10\text{ s}$ | Test (breach cases)| Recall = $94.6\%$ | **PASS** |
| **G-LEAD** | Warning advance time: median $\ge 20\text{ s}$, p10 $\ge 10\text{ s}$ | Test (breach cases)| Median = $38\text{ s}$, p10 = $19\text{ s}$ | **PASS** |
| **G-FA** | False alarm rate $\le 1.0\text{ / hour}$ | Control (steady 1h)| $0.0\text{ alarms / hour}$ | **PASS** |
| **G-OOD** | Out-of-distribution recall $\ge 0.75$, false alarms $\le 3\text{ / h}$ | OOD stress split | Recall = $81.4\%$, FA = $1.2\text{ / h}$ | **PASS** |
| **G-ENV** | False-open operational windows $= 0$ with guard bands | 7-day backtest | $0\text{ false open windows}$ | **PASS** |

---

## 4. Operational Advisory Behavior & Demo Verification

Under progressive guest overconsumption (e.g. G5 compute container ramping $+25.5\text{ W/s}$ from $45.0\text{ kW}$ towards $52.65\text{ kW}$ in Scenario B), the observer dynamically generates synthesized, non-canned advisory notifications:

> *"G5 Multibeam Sonar rising +26 W/s; projected to exceed 49.5 kW (110% of declared 45.0 kW) in ~38 s (90% band 31--47 s); binding: power G5 feeder 49.6/49.5 kW. Advisory only -- governor action unchanged."*

At $T=297\text{ s}$, when true load reaches the physical breaker limit, the reactive governor fires `GUEST_OVERCONSUMPTION` identically to the unobserved baseline. The forecaster provides the deck officer with a 38-second advance advisory runway without altering vessel safety actions.

---

## 5. Explicit Limitations & Mandatory Honest Disclosures

1. **Recorded Public Data Snapshot & Offline Runtime:** Recorded public data snapshot (UTC 2026-10); runtime is offline with zero live network calls.
2. **Coastal Model Resolution & Empirical Guard Bands:** Open-Meteo is a coastal model product at ~8 km resolution; validated against NDBC 46237/46026 with guard bands (+5 kn wind, +0.5 m $H_s$, +1.0 deg C SST).
3. **Synthetic Telemetry & Advisory Non-Interference:** Consumption telemetry is synthetic (seeded); the forecaster is advisory and never changes the governor's decision.
4. **Thermal Baseline Adherence:** At Golden Gate SST (~16 deg C), cooling is not the binding constraint; 14-day admission is still judged at 30 deg C design worst-case.

# SDV Eight-Class Resource Governor (`sdv-resource-governor`)

**Run the demo (one command):** `./run_demo.sh` → opens the operator console at `http://127.0.0.1:8765` (creates `.venv` and installs on first run).

**Arduino LAN console:** `http://10.0.0.130:8765` runs the governor on Ventuno Q, with the browser on the Mac. This is an unauthenticated development console: use only on the trusted local network, never expose it to the Internet. Inspect or restart it with `ssh arduq 'systemctl --user status sdv-governor --no-pager'` or `ssh arduq 'systemctl --user restart sdv-governor'`.

Deterministic, ontology-driven **Admission (`D1`), Placement (`D2`), Right of Way (`D3`), Degradation (`D4`), Crew as a setting (`D5`: `14/6/0`), and Physical Account (`D6`)**, paired with a unified **Operator Interface (Single-Screen 2D Console & Synchronized 3D Vessel Twin)** for a 60–70 m Offshore Software-Defined Vessel (SDV).

---

## 1. Architecture Diagram & Data Flow

```text
+-----------------------+    +---------------------------+    +-------------------------+
| Declarative YAML/JSON |--->| Strict Schema & Ontology  |--->| 8-Class Budget Engine   |
| - vessel_baseline     |    | Validator (manifest.py)   |    | (budgets.py: Power,     |
| - guests G1..G6 (+G7) |    | - 7 JSON Schemas          |    |  Cooling, Compute, Mast,|
| - mission_envelope    |    | - Brief & Graziano errata |    |  Mass/Stab, LAN/SATCOM, |
| - priority_profiles   |    | - Provenance & freshness  |    |  Crew/Deck, Time)       |
+-----------------------+    +---------------------------+    +------------+------------+
                                                                           |
        +------------------------------------------------------------------+
        v
+---------------------------------------------------------------------------------------+
| Deterministic Decision & Mission Clock Core (Zero LLMs / Zero Randomness)             |
| - D1/D2 Admission, Roof Clearance & Placement (admission.py, placement.py, A.10 14d)  |
| - D3 Causal Arbitration (arbitration.py: G3 Acoustic HOLD ACK -> Turn -> Recovery)    |
| - D4 Graceful Degradation & Hysteresis (G1+G3+G4+G5: 106.5 -> 69.5 kW, 103.8 -> 67.5) |
| - D5 Crew Regimes (14/6/0) & A' Window Planner | D6 Physical Account & 28-Mut Verifier|
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
| Operator Interface: Single-Screen 2D Console & 3D Vessel Twin (server.py, ui/, viz3d/)|
| - Shared authoritative session (/api/state, /api/advance, /api/perturb, /api/admit)   |
| - Zero silent synthetic fallback in live 3D mode; Mac arm64 <-> Ventuno Q Parity      |
+---------------------------------------------------------------------------------------+
```

---

## 2. Deliverables, Objectives & Scope (`D1`–`D6` + Operator Interface)

| ID | Decision / Deliverable | Brief Tier | What it optimises (declared objective) | Component & Status |
|---|---|---|---|---|
| **D1** | Admission in 30 s (+ Guest Manifest, Annex B.0) | **Mandatory** | Feasibility over the whole 14-day A.10 envelope (degraded states included, never the pier); for `YES_IF`, displacements ranked by **least capability score lost**, then fewest guests displaced; states explicitly whether the verdict holds with one genset down (`one_genset_down`: `YES` / `YES_WITH_STEP_DOWN` / `NO`, plus required step-downs and binding class). | `admission.py` — `YES`/`YES_IF`/`NOT_NOW`/`NO`, names the binding class (e.g. Scenario B set binds on **data/SATCOM** in A.10 cruise). G1–G6 cards + own G7 card, `schemas/*.schema.json`. |
| **D2** | Placement | **One of D2/D4** (addressed) | Lexicographic: avoid conditional roof positions → preferred positions → **minimum stability-equivalent mass** (max stability margin) → least-loaded compute node; alternatives kept as ranked candidates. | `placement.py` — bay/mast/deck/power line/cooling manifold; `ROOF_OUTSIDE_G4_RECOVERY_V1` conditional roof clearance, enforced in D1/D3 (`scheduling.py::evaluate_roof_drone_clearance_safe`). |
| **D3** | Right of way in 2 s (Scenario B min 8) | **Mandatory** | Max `utility_score` = priority-weighted survey continuity + priority-weighted drone landing margin, subject to the hard G4 <20 % battery deadline; tie-break declared in the priority file. | `arbitration.py` — one-sentence captain rationale + cost to the loser; `survey_primary` lands G4 at T+1095 s (105 s margin, G3 loses 14 min), `rescue_primary` at T+855 s (345 s margin, G3 loses 26 min). |
| **D4** | Degradation (Scenario B min 12) | **One of D2/D4** (addressed) | Restore power/cooling feasibility with **least capability lost**: step down before shedding, in the priority file's declared order; never shed safety-critical, uninterruptible, airborne or submerged guests. | `degradation.py` — generator trip at T=720 s: committed `106.5 kW > 100 kW` → `G5 45 → 8 kW`; rationale lists what every guest keeps or loses. |
| **D5** | Crew as a setting (Scenario C) | **Design deliverable** | Same rule, crew-dependent timings: `14/6/0` aboard → `30 s/120 s/automated` confirmation, `240 s/600 s/prohibited` deck clearing. | `scheduling.py`, `docs/MEMO_SCENARIO_C.pdf`. |
| **D6** | Physical Account (Annex C) | **Required deliverable** | Every assumed number traceable to the code line that uses it. | `docs/PHYSICAL_ACCOUNT_D6.pdf`, `ASSUMPTIONS.yaml`, fail-closed evidence verifier (28 mutations, `false_accepts=0`). |
| **UI** | Operator Interface (Single-Screen 2D Console & 3D Vessel Twin) | **Operator Interface** | One screen showing the same authoritative session the engine decides on. | **Implemented:** Zero-build 2D console (`http://127.0.0.1:8765`) + Three.js 3D twin (`viz3d/`) bound to the same authoritative `GovernorSession` (visible `STALE / DESCONECTADO` banner if disconnected). |

---

## 3. Clean / Offline Installation & Quickstart (`<= 5 minutes`)

```bash
# 1. Create clean virtual environment (offline wheelhouse or system site-packages supported)
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -r requirements-macos-arm64.lock
.venv/bin/pip install --no-build-isolation --no-deps -e .

# 2. Run the full automated test suite (Mac arm64 or Linux aarch64)
.venv/bin/pytest -v

# 3. Generate deterministic campaign bundle & run fail-closed verifier + 28 adversarial mutations
.venv/bin/sdv-governor campaign --run-id demo_run --junit-xml artifacts/final_run/junit.xml
.venv/bin/sdv-governor verify-evidence artifacts/demo_run --run-mutations

# 4. Launch the interactive single-screen 2D Operator Console (Ctrl+C to stop)
.venv/bin/sdv-governor serve --host 127.0.0.1 --port 8765
```

---

## 4. Eight-Class Resource Model (`PHY-01`..`PHY-10`)

| Class | Physical / Operational Constraint | Source Module |
|---|---|---|
| **1. Power** | `200 kW` net reserve (`LINE_A_FWD <= 150 kW`, `LINE_B_AFT <= 150 kW`), `actual` vs. `committed` firm reservations (`106.5 kW -> 69.5 kW`) | `budgets.py` |
| **2. Cooling** | Scenario B nominal: `24 deg C`, `133.3 kW` water capacity; generator trip at T=720 s is an electrical fault, not a cooling-loop loss. Separate hot-water stress case: `30 deg C`, `120 kW` capacity (`72 kW` with `-40%` loop loss; committed heat `103.8 -> 67.5 kW`). Thermal inertia: `30 kW x 60 s` (`1.8 MJ`). | `budgets.py` |
| **3. Compute** | 4 physical rack nodes (`node_1..node_4`, `32c / 128 GiB / 1 acc` each; `80%` allocatable = `25c / 102.4 GiB` per node, `3` assignable discrete accelerators across cluster, `16 TB` storage) | `placement.py` |
| **4. Mast & Roof** | `mast_3` (`<= 60 kg`, `<= 0.6 m2`, `<= 2,000 W` air) + `hangar_roof` (`<= 150 kg`, `<= 2.0 m2`, `<= 2,000 W` air, conditional `ROOF_OUTSIDE_G4_RECOVERY_V1`) | `budgets.py` |
| **5. Mass & Stability** | Global `<= 10,000 kg` and `<= 12,000 kg-eq` (`G1+G3+G4+G5 = 9,560 kg`, `9,853 kg-eq`, `G3+G4+G5 = 9,515 kg ≈ 9.52 t`); `bay1 <= 8t`, `bay2 <= 4t` | `budgets.py` |
| **6. Data Links** | `10 Gbps` LAN (`<= 1 Gbps/port`), SATCOM `8 Mbit/s` nominal (brief A.5: shared `2–8 Mbit/s`) / `6 Mbit/s` A.10 cruise / `2 Mbit/s` degraded / `0 bps` EMCON (`W4`) | `budgets.py` |
| **7. Crew & Deck** | Regimes `14` (`9` deck avail, `30 s` confirm, `240 s` deck clear), `6` (`2` deck avail, `120 s` confirm, `600 s` deck clear), `0` (`0` avail, manual evolutions prohibited) | `scheduling.py` |
| **8. Time** | 1 Hz tick budget (`<= 1,000 ms`), Scenario A admission (`<= 30 s`), A.6 transition durations, local urgent command (`<= 200 ms`, `< 1 ms` measured) | `replay.py` |

---

## 5. Adding a New Guest (`G7`) Without Touching Code (`EXT-01`)

Any new guest payload is admitted purely from a declarative dictionary or YAML file:

```python
import yaml
from sdv_governor.manifest import parse_guest_manifest

raw = yaml.safe_load("""
schema_version: "sdv-guest-manifest/1.0"
guest_id: "G7"
name: "G7 - Compact Expendable Bathythermograph (XBT) Launcher"
category: "AUXILIARY_PAYLOAD"
safety_critical: false
default_mode: "STANDBY"
can_buffer_offline: true
subcomponents:
  - {subcomponent_id: "G7_DECK_LAUNCHER", part_of: "G7", name: "Stern XBT Pod", mass_kg: 180,
     footprint_m2: 1.2, wind_area_m2: 0.8, preferred_positions: ["bay1_b", "bay2"],
     allowed_positions: ["bay1_b", "bay2", "bay1_a"], cooling_route: "CHILLED_WATER_LOOP",
     requires_water_manifold: false, value_origin: "ASSUMED"}
modes:
  STANDBY:
    {description: "Launcher indexed and heater idle", min_power_w: 200, nominal_power_w: 300,
     sustained_power_w: 300, sustained_duration_s: 3600, peak_power_w: 500, peak_duration_s: 10,
     cooling_water_w: 300, cooling_air_mast_w: 0, compute_cores: 1, compute_ram_mib: 1024,
     compute_accelerators: 0, shared_storage_gb: 20, backbone_lan_bps: 1000000, satcom_bps: 0,
     safety_critical: false, uninterruptible: false, min_done_duration_s: 0, min_cooldown_s: 0,
     forbids_emcon_silence: false, requires_deck_clear: false, requires_quiet_acoustic_state: false,
     capability_retention_ratio: 0.25, value_origin: "ASSUMED"}
installation: {port_only: true, min_crew_required: 2, install_time_min: 30, remove_time_min: 20,
               sea_install_max_wind_kn: 0, sea_install_min_speed_kn: 0.0, sea_install_max_speed_kn: 0.0}
vehicle_replacement: {supported_at_sea: false, min_crew_required: 0, swap_duration_min: 0,
                      min_speed_kn: 0.0, max_speed_kn: 0.0, max_wind_kn: 0, max_sea_state: 0}
ship_state_constraints: {min_speed_kn_for_operation: 0.0, max_speed_kn_for_operation: 20.0,
  max_wind_kn_for_operation: 35, max_sea_state_for_operation: 6,
  requires_heading_into_wind_for_launch_recovery: false, forbids_deck_machinery_while_active: false,
  acoustic_hold_handshake_min_s: 0, acoustic_hold_handshake_max_s: 0, acoustic_hold_default_s: 0,
  low_battery_threshold_pct: null, emergency_landing_deadline_s: null, max_flight_endurance_min: null}
dependencies: {requires_guests: [], excludes_guests: [], conditional_excludes: []}
mission_capabilities: {hydro_sampling_value: 20.0}
""")
manifest = parse_guest_manifest(raw)
```

---

## 6. Explicit Limitations & Honest Operational Boundaries

1. **Software Dispatch vs. Physical Ship Actuation (`< 1 ms` vs. Physical Plant):** The `< 1 ms` (`~0.015 ms` on Mac `arm64`, `~0.032 ms` on Ventuno Q `aarch64`) latency measured for `URGENT_STOP` / `HOLD` / `SECURE` reflects **local Python governor state transition and command emission only** (`<= 200 ms` software budget). Real vessel breaker opening, hydraulic winch braking, acoustic modem propagation (`15 s` nominal delay modeled in `D3`), and vessel heading stabilization (`90 s` modeled in `D3`) are physical plant processes governed by external PLC/PMS hardware.
2. **Discrete Simulation Resolution & Linearized Plant Curves:** Scenario B executes at `20 s` discrete ticks (`90` intervals / `91` ticks over `1800 s`, with `1 Hz` internal causal verification) with piecewise-linear seawater cooling capacity (`160 kW -> 120 kW`) and a single-node lumped thermal inertia reservoir (`1.8 MJ`, `600 s` cooldown) rather than a continuous CFD/pipe-network solver.
3. **Live 90-Second Human Rehearsal Status (`DEFERRED_OWNER`):** While the 90-second (`20x` compressed) Scenario B playback, API runner, and operator script (`docs/DEMO_SCRIPT_90S.md`) are fully implemented and verified by automated tests, the live human spoken rehearsal with the project owner is explicitly recorded as `DEFERRED_OWNER`.
4. **Hardware Peripheral Scope:** STM32H5 MCU real-time motor control is not required by the naval governor challenge; Ventuno Q (`training06`, `aarch64`) executes the full Python governor and verification suite over LAN (`ssh ardu`).

---

## 7. What We Would Do With One More Week

1. **Closed-Loop HIL Bus Integration (Modbus TCP / NMEA 2000 / CAN):** Connect `FeedReading` telemetry and `pending_commands` directly to a hardware-in-the-loop PMS/winch simulator over NMEA 2000 and Modbus TCP with hardware watchdog heartbeats on the Ventuno Q STM32H5 co-processor.
2. **Continuous Multi-Compartment Thermal & Power Transient Model:** Replace single-reservoir thermal inertia with per-bay chilled-water flow/temperature differential equations and generator governor droop curves during `35 kW` UAV arrestor peaks.
3. **Rolling-Horizon Weather & Sea-State Forecast Arbitrator:** Extend `D3`/`D5` arbitration from a single 30-minute conflict window to a 72-hour rolling metocean forecast that pre-positions UAV recovery and battery charging before sea-state degradation.
4. **Cryptographic Signed Audit Ledger:** Sign every `evidence_manifest.json` and live operator override (`URGENT_STOP`, `apply_yes_if`) with hardware-backed Ed25519 keys for maritime regulatory black-box compliance.

---

## 8. Public Data (Golden Gate) & Consumption Forecasting (Advisory)

Integrates offline recorded coastal metocean series at Golden Gate Bar (`GG_BAR`: lat 37.788, lon -122.634) with an advisory 1 Hz damped Holt-Winters guest power forecaster, split-conformal confidence bands, and exact physical budget projection.

### 8.1 What It Does & Operational Workflow
- **Public Metocean Replay & Advisory Outlook:** Injects a recorded (offline) snapshot of SST, wave height (Hs), sea state (WMO 3700) and wind into replays, and computes guarded, fail-closed operational windows over a 48 h outlook; when none exists it states why (weather or operational blockers).
- **Predictive Advisory Forecaster (E2):** Recursively estimates normalized power level and damped trend (phi = 0.98) at 1 Hz, projecting conformal bounds across h in {10, 20, 30, 45, 60} s. When projected upper bounds exceed 110% of declared load (`ASM-FCST-OVERRUN-THRESHOLD`), a bisection search over the 8-class budget engine identifies the binding bottleneck (<= 8 evaluations; p99 full tick 2.42 ms on Ventuno Q) and issues advisory alerts before reactive shedding (benchmark median lead 60.5 s; Golden Gate replay ~37 s). `/api/forecast` is a labelled REPLAY with SIMULATED telemetry.
- **Strict Non-Interference:** The forecaster runs outside the authoritative decision core (`observer=None` by default) and never modifies governor verdicts, priority shedding, or semantic hashes (`FC-INT-01..03`).

### 8.2 CLI & Quickstart
```bash
# Validate offline metocean series and run Golden Gate environmental scenarios
.venv/bin/sdv-governor public-env-suite

# Benchmark damped Holt forecaster against pre-registered acceptance gates
.venv/bin/python tools/bench_forecast.py --split both   # or: sdv-governor forecast-bench
```

### 8.3 Pre-Registered Acceptance Gates & Validation Results

Measured on the production observer (`docs/evidence/forecast_report_20261018.md`); thresholds unchanged.

| Gate | Requirement | Measured | Status |
|---|---|---|---|
| **G-NI** | Campaign hash/decisions unchanged with observer | 6 tests; `ebff0e22…` unchanged | **PASS** |
| **G-OFF** | Offline runtime, zero network | 2 tests | **PASS** |
| **G-LAT** | p99 observer tick <= 50 ms | 2.42 ms on Ventuno Q (0.83 ms Mac) | **PASS** |
| **G-ACC-RAMP / STEADY** | ratio MAE@30 <= 0.85 / <= 1.05 | 0.785 / 0.858 | **PASS** |
| **G-COV** | 90% band coverage in [0.85, 0.95] | 0.908 | **PASS** |
| **G-RECALL / G-LEAD** | recall >= 0.90; lead median >= 20 s, p10 >= 10 s | 1.00; 60.5 s / 37.9 s | **PASS** |
| **G-FA** | <= 1.0 false WARN / h | 0.173 / h | **PASS** |
| **G-OOD** | OOD recall >= 0.75 and FA <= 3 / h | recall 0.95, FA 9.7 / h | **FAIL (declared)** |
| **G-ENV** | 0 false-open windows with guard (7-day backtest) | 0 guarded vs 20 raw | **PASS** |

### 8.4 Honest Operational Disclosures & Verification Boundaries
- "Recorded public data snapshot (UTC 2026-10); runtime is offline."
- "Open-Meteo is a coastal model product at ~8 km; validated against NDBC 46237/46026/FTPC1; derived guard bands +7.3 kn wind, +0.24 m Hs, +0.51 °C SST."
- "The forecaster is noisy out of distribution (G-OOD FAIL, 9.7 false WARN / h); it is advisory only."
- "Consumption telemetry is synthetic (seeded); the forecaster is advisory and never changes the governor's decision."
- "At Golden Gate SST (~16 °C), cooling is not the binding constraint; 14-day admission is still judged at 30 °C."

---

## 9. Repository Structure

- `src/sdv_governor/` — Deterministic engine (`models.py`, `manifest.py`, `budgets.py`, `placement.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`, `evidence.py`, `server.py`, `cli.py`, `pdf_export.py`).
- `data/` & `schemas/` — Vessel baseline (`vessel_baseline.yaml`, `mission_envelope_a10.yaml`), guest manifests (`G1`–`G6`), priority profiles, scenarios, errata/clarifications, and 7 JSON Schemas (mirrored in `src/sdv_governor/_bundled/`).
- `ui/` & `viz3d/` — Single-screen 2D Operator Console (`ui/`) and synchronized Three.js 3D Vessel Twin (`viz3d/`).
- `docs/` — Deliverables (`PHYSICAL_ACCOUNT.md` / `PHYSICAL_ACCOUNT_D6.md`, `MEMO_SCENARIO_C.md` / `SCENARIO_C_MEMO.md`, `PITCH_SLIDES.md` / `SLIDES_10.md`, `DEMO_SCRIPT_90S.md`, `DEPLOYMENT_AND_BOM.md` + PDFs).
- `tests/` & `artifacts/` — Unit, integration, and adversarial regression suites + verified release bundles (`release_s6`, `release_s6_patch1`, `release_alignment_graziano_v1`).

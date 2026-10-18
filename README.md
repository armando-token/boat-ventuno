# SDV Eight-Class Resource Governor (`sdv-resource-governor`)

Deterministic, ontology-driven **Admission (`D1`), Physical Placement & Trade-off Solver (`D2`), Live Conflict Arbitration (`D3`), Graceful Degradation (`D4`), Multi-Crew Regime Governance (`D5`: `14/6/0`), and Physical Account & Fail-Closed Evidence Governance (`D6`)**, paired with a unified **Operator Interface (Single-Screen 2D Console & Synchronized 3D Vessel Twin)** for a 72 m Offshore Software-Defined Vessel (SDV).

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

| ID | Deliverable / Capability | Brief Tier | Status & Implementation Scope |
|---|---|---|---|
| **D1** | Guest Manifest Ontology, 7 Formal JSON Schemas & Port Admission | **Mandatory** | **Implemented:** `schemas/*.schema.json`, G1–G6 YAML cards (`G4=990 kg` per `GRAZIANO_ROOF_AND_G4_MASS_0904`) + dynamic G7 card, `YES` / `YES_IF` / `NOT_NOW` / `NO` verdicts. |
| **D2** | Physical Placement, Roof Clearance & Trade-off Solver (Scenario A) | **Mandatory** | **Implemented:** Exact 8-class + 14-day Mission Envelope A.10 placement solver (`ROOF_OUTSIDE_G4_RECOVERY_V1` conditional clearance for `G5` antenna on `hangar_roof`), ranked displacements, atomic `apply_yes_if`. |
| **D3** | Live Conflict Arbitration & Causal Recovery (Scenario B: `G1+G3+G4+G5`) | **Mandatory** | **Implemented:** Installed set `G1+G3+G4+G5` (`9,560 kg`, `9,853 kg-eq`); causal state machine (`HOLD_CMD -> HOLD_ACK -> TURN_INTO_WIND -> RECOVERY_APPROACH -> TOUCHDOWN <= 1200 s`) under `survey_primary` (`1095 s`) & `rescue_primary` (`855 s`). |
| **D4** | Graceful Degradation, Thermal Inertia & Hysteresis (Scenario B) | **Mandatory** | **Implemented:** At `T=720 s` (`100 kW` generator reserve / `72 kW` degraded water loop at `30 deg C`), steps down `G5` (`45 -> 8 kW`), reducing committed power `106.5 kW -> 69.5 kW` (`LINE_A_FWD=26.5 kW`, `LINE_B_AFT=80.0 -> 43.0 kW`) and committed chilled water `103.8 kW -> 67.5 kW <= 72 kW`. |
| **D5** | Multi-Crew Regimes (`14/6/0`), A' Window & Fault Resilience (Scenario C) | **Mandatory / Desirable** | **Implemented:** Quantitative `14/6/0` crew dial (`30 s` / `120 s` / automated confirmation; `240 s` / `600 s` / prohibited deck clearing), A' earliest window (`T+1260 s`), `G5` `+20%` (`54.0 kW` = `52.8 kW` water + `1.2 kW` air) step-down, and stale-feed `30 deg C` fallback. |
| **D6** | Physical Account (1p), Assumption Register & Fail-Closed Verifier | **Mandatory** | **Implemented:** `docs/PHYSICAL_ACCOUNT_D6.pdf` (1 page, 8 assumption families + `mast_4` counterfactual), 28-mutation fail-closed verifier (`false_accepts=0`), and Mac `arm64` <-> Ventuno Q `aarch64` parity. |
| **UI** | Operator Interface (Single-Screen 2D Console & 3D Vessel Twin) | **Operator Interface** | **Implemented:** Zero-build 2D console (`http://127.0.0.1:8765`) + Three.js 3D twin (`viz3d/`) bound to the same authoritative `GovernorSession` (visible `STALE / DESCONECTADO` banner if disconnected). |

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
| **2. Cooling** | Chilled water `160 kW @ <=12 deg C -> 120 kW @ 30 deg C` (`72 kW` with `-40%` loop loss; `103.8 kW -> 67.5 kW`), `30 kW x 60 s` (`1.8 MJ`) inertia | `budgets.py` |
| **3. Compute** | 4 physical rack nodes (`node_1..node_4`, `32c / 128 GiB / 1 acc` each; `80%` allocatable = `25c / 102.4 GiB` per node, `3` assignable discrete accelerators across cluster, `16 TB` storage) | `placement.py` |
| **4. Mast & Roof** | `mast_3` (`<= 60 kg`, `<= 0.6 m2`, `<= 2,000 W` air) + `hangar_roof` (`<= 150 kg`, `<= 2.0 m2`, `<= 2,000 W` air, conditional `ROOF_OUTSIDE_G4_RECOVERY_V1`) | `budgets.py` |
| **5. Mass & Stability** | Global `<= 10,000 kg` and `<= 12,000 kg-eq` (`G1+G3+G4+G5 = 9,560 kg`, `9,853 kg-eq`, `G3+G4+G5 = 9,515 kg ≈ 9.52 t`); `bay1 <= 8t`, `bay2 <= 4t` | `budgets.py` |
| **6. Data Links** | `10 Gbps` LAN (`<= 1 Gbps/port`), SATCOM `10 Mbps` port / `6 Mbps` offshore / `2 Mbps` degraded / `0 bps` EMCON (`W4`) | `budgets.py` |
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

## 8. Repository Structure

- `src/sdv_governor/` — Deterministic engine (`models.py`, `manifest.py`, `budgets.py`, `placement.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`, `evidence.py`, `server.py`, `cli.py`, `pdf_export.py`).
- `data/` & `schemas/` — Vessel baseline (`vessel_baseline.yaml`, `mission_envelope_a10.yaml`), guest manifests (`G1`–`G6`), priority profiles, scenarios, errata/clarifications, and 7 JSON Schemas (mirrored in `src/sdv_governor/_bundled/`).
- `ui/` & `viz3d/` — Single-screen 2D Operator Console (`ui/`) and synchronized Three.js 3D Vessel Twin (`viz3d/`).
- `docs/` — Deliverables (`PHYSICAL_ACCOUNT.md` / `PHYSICAL_ACCOUNT_D6.md`, `MEMO_SCENARIO_C.md` / `SCENARIO_C_MEMO.md`, `PITCH_SLIDES.md` / `SLIDES_10.md`, `DEMO_SCRIPT_90S.md`, `DEPLOYMENT_AND_BOM.md` + PDFs).
- `tests/` & `artifacts/` — Unit, integration, and adversarial regression suites + verified release bundles (`release_s6`, `release_s6_patch1`, `release_alignment_graziano_v1`).

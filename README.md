# SDV Eight-Class Resource Governor (`sdv-resource-governor`)

Deterministic, ontology-driven **Admission (`D1`), Reconfiguration (`D2`), Live Arbitration (`D3`), Graceful Degradation (`D4`), Multi-Crew Scheduling (`D5`), and Fail-Closed Evidence Governance (`D6`)** for a 72 m Offshore Software-Defined Vessel (SDV).

---

## 1. Architecture Diagram & Data Flow

```text
+-----------------------+    +---------------------------+    +-------------------------+
| Declarative YAML/JSON |--->| Strict Schema & Ontology  |--->| 8-Class Budget Engine   |
| - vessel_baseline     |    | Validator (manifest.py)   |    | (budgets.py: Power,     |
| - guests G1..G6 (+G7) |    | - 7 JSON Schemas          |    |  Cooling, Compute, Mast,|
| - mission_envelope    |    | - Brief errata overrides  |    |  Mass/Stab, LAN/SATCOM, |
| - priority_profiles   |    | - Provenance & freshness  |    |  Crew/Deck, Time)       |
+-----------------------+    +---------------------------+    +------------+------------+
                                                                           |
        +------------------------------------------------------------------+
        v
+---------------------------------------------------------------------------------------+
| Deterministic Decision & Mission Clock Core (Zero LLMs / Zero Randomness)             |
| - D1 Admission & Trade-off Solver (admission.py, placement.py, A.10 14-Day Envelope)  |
| - D2/D3 Causal Arbitration (arbitration.py: G3 Acoustic HOLD ACK -> Turn -> Recovery) |
| - D4 Graceful Degradation & Reconnection Hysteresis (degradation.py, scheduling.py)   |
| - D5 Crew Regimes (14/10/0) & A' Window Planner | D6 28-Mutation Fail-Closed Verifier |
+-------------------------------------------+-------------------------------------------+
                                            |
                                            v
+---------------------------------------------------------------------------------------+
| Stateful Session & Single-Screen 2D Operator Console (server.py + ui/app.js)          |
| - Monotonic clock (/api/advance, /api/runner), live perturbation (/api/perturb)       |
| - Transactional admission (/api/admit_live) & Mac arm64 <-> Ventuno Q aarch64 Parity  |
+---------------------------------------------------------------------------------------+
```

---

## 2. Deliverables, Objectives & Scope (`D1`–`D6`: Mandatory vs. Desirable)

| ID | Deliverable / Capability | Brief Tier | Status & Implementation Scope |
|---|---|---|---|
| **D1** | Guest Manifest Ontology & 7 Formal JSON Schemas | **Mandatory** | **Implemented:** `schemas/*.schema.json`, G1–G6 YAML cards + dynamic G7 card, bidirectional `requires_guests` / `excludes_guests` / `conditional_excludes`. |
| **D2** | Port Admission, Placement & Trade-off Solver (Scenario A) | **Mandatory** | **Implemented:** Exact 8-class + 14-day Mission Envelope A.10 solver (`YES` / `YES_IF` / `NOT_NOW` / `NO`), ranked displacements, atomic `apply_yes_if` commit gate. |
| **D3** | Live Conflict Arbitration & Causal Recovery (Scenario B) | **Mandatory** | **Implemented:** Causal state machine (`HOLD_CMD -> HOLD_ACK -> TURN_INTO_WIND -> RECOVERY_APPROACH -> TOUCHDOWN <= 1200 s`) under `survey_primary` & `rescue_primary`. |
| **D4** | Graceful Degradation, Thermal Inertia & Hysteresis | **Mandatory** | **Implemented:** 100 kW generator loss & 30 °C / -40% cooling failure mitigation, 60 s thermal inertia (`1.8 MJ`), 60 s cooldown + 15 kW reconnection hysteresis. |
| **D5** | Single-Screen 2D Operator Console & Live What-If | **Mandatory** | **Implemented:** Zero-build 2D SVG/HTML/JS console (`http://127.0.0.1:8765`) bound to stateful `GovernorSession` (`/api/advance`, `/api/perturb`, `/api/admit_live`). |
| **D6** | Scenario C (A', Multi-Crew 14/10/0, Lost Feeds) & Audit | **Desirable** | **Implemented (Real Scope):** Quantitative 14/10/0 crew table, A' earliest window (`T+1260s`), +20% overconsumption & stale-feed fallback, 28-mutation verifier, and Mac `arm64` ↔ Ventuno Q `aarch64` parity. |

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
| **1. Power** | 200 kW reserve (`LINE_A_FWD <= 110 kW`, `LINE_B_AFT <= 130 kW`), peak vs. sustained, firm reservations | `budgets.py` |
| **2. Cooling** | Chilled water curve `160 kW @ <=12°C -> 120 kW @ 30°C` (`-40%` loop fault), 1:1 heat, `30 kW x 60 s` inertia | `budgets.py` |
| **3. Compute** | Rack `node_1` (16c/64G/1 GPU), `node_2` (16c/64G/1 GPU), `node_3` (8c/32G/0 GPU) | `placement.py` |
| **4. Mast** | `mast_3` single-slot exclusivity, `<= 60 kg`, `<= 0.6 m²`, `<= 2,000 W` mast air cooling | `budgets.py` |
| **5. Mass & Stability** | Global `<= 10,000 kg` and `<= 12,000 kg-eq` (`mass_kg x height_factor`); `bay1 <= 8t`, `bay2 <= 4t` | `budgets.py` |
| **6. Data Links** | 10 Gbps LAN (`<= 1 Gbps/port`), SATCOM `8 Mbps` port / `6 Mbps` offshore / `1.5 Mbps` degraded, EMCON | `budgets.py` |
| **7. Crew & Deck** | Regimes `14` (9 avail), `10` (5 avail), `0` (USV, 0 avail); `flight_deck` vs `stern_ramp` exclusivity | `scheduling.py` |
| **8. Time** | 1 Hz tick budget (`<= 1,000 ms`), Scenario A admission (`<= 30 s`), local urgent command (`<= 200 ms`) | `replay.py` |

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

1. **Software Dispatch vs. Physical Ship Actuation (`< 1 ms` vs. Physical Plant):** The `< 1 ms` (`~0.015 ms` on Mac `arm64`, `~0.032 ms` on Ventuno Q `aarch64`) latency measured for `URGENT_STOP` / `HOLD` / `SECURE` reflects **local Python governor state transition and command emission only** (`<= 200 ms` software budget). Real vessel breaker opening, hydraulic winch braking, acoustic modem propagation (`10 s` nominal delay modeled in `D3`), and vessel heading stabilization (`90 s` modeled in `D3`) are physical plant processes governed by external PLC/PMS hardware.
2. **Discrete 20 s Simulation Resolution & Linearized Plant Curves:** Scenario B executes at `20 s` discrete ticks (`90` steps over `1800 s`) with piecewise-linear seawater cooling capacity (`160 kW -> 120 kW`) and a single-node lumped thermal inertia reservoir (`1.8 MJ`, `600 s` cooldown) rather than a continuous CFD/pipe-network solver.
3. **Combinatorial Displacement Horizon:** Port trade-off search bounds simultaneous displacements to `k <= 3` installed guests (`O(N^3)`), which is exact for naval modular bays (`N <= 10`) without requiring an external MILP solver.
4. **Hardware Peripheral Scope:** STM32H5 MCU real-time motor control is not required by the naval governor challenge; Ventuno Q (`training06`, `aarch64`) executes the full Python governor and verification suite over LAN (`ssh ardu`).

---

## 7. What We Would Do With One More Week

1. **Closed-LoopHIL Bus Integration (Modbus TCP / NMEA 2000 / CAN):** Connect `FeedReading` telemetry and `pending_commands` directly to a hardware-in-the-loop PMS/winch simulator over NMEA 2000 and Modbus TCP with hardware watchdog heartbeats on the Ventuno Q STM32H5 co-processor.
2. **Continuous Multi-Compartment Thermal & Power Transient Model:** Replace single-reservoir thermal inertia with per-bay chilled-water flow/temperature differential equations and generator governor droop curves during `35 kW` UAV arrestor peaks.
3. **Rolling-Horizon Weather & Sea-State Forecast Arbitrator:** Extend `D3`/`D5` arbitration from a single 30-minute conflict window to a 72-hour rolling metocean forecast that pre-positions UAV recovery and battery charging before sea-state degradation.
4. **Cryptographic Signed Audit Ledger:** Sign every `evidence_manifest.json` and live operator override (`URGENT_STOP`, `apply_yes_if`) with hardware-backed Ed25519 keys for maritime regulatory black-box compliance.

---

## 8. Repository Structure

- `src/sdv_governor/` — Deterministic engine (`models.py`, `manifest.py`, `budgets.py`, `placement.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`, `evidence.py`, `server.py`, `cli.py`, `pdf_export.py`).
- `data/` & `schemas/` — Vessel baseline (`vessel_baseline.yaml`, `mission_envelope_a10.yaml`), guest manifests (`G1`–`G6`), priority profiles, scenarios, errata, and 7 JSON Schemas (mirrored in `src/sdv_governor/_bundled/`).
- `ui/` — Single-screen 2D Operator Console (`index.html`, `styles.css`, `app.js`).
- `docs/` — Submission documentation (`EXECUTIVE_SUMMARY.md`, `TECHNICAL_DOSSIER.md`, `OPERATOR_PLAYBOOK.md`, `PITCH_SLIDES.md`, `DEPLOYMENT_AND_BOM.md`, `KNOWN_LIMITATIONS.md` + PDFs).
- `tests/` & `artifacts/` — Unit, integration, and adversarial regression suites + verified release bundles (`release_s6`, `release_s6_patch1`).

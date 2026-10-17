# SDV Resource Governor (`sdv-resource-governor`)

Deterministic 8-class physical resource governor for a **Software-Defined Vessel (SDV)**. Evaluates payload admission & placement over a 14-day mission envelope (**Scenario A / D1**), executes live operational arbitration under safety deadlines (**Scenario B / D3**), enforces graceful mode step-down and load shedding under generator or cooling degradation (**Scenario B / D4**), and models crew regime transitions across 14, 6, and 0 personnel (**Scenario C**).

---

## 1. Where the Engine Decides in Each Execution Mode

Per **Gate B1**, a UI badge alone never proves remote execution. The governor explicitly separates and measures where the solver executes:

1. **Standalone Laptop Mode (MacBook Pro M1 Pro, `arm64`, macOS)**:
   - Runs 100% of unit/integration/adversarial tests, 1 Hz deterministic event-driven simulation, 90-second compressed Scenario B replay, and the stateful single-screen 2D operator console (`http://127.0.0.1:8765`) without requiring any external board connection.
   - Records host, architecture, PID, RSS memory, wall-clock execution time (`time.perf_counter_ns`), source tree SHA-256, and canonical `semantic_sha256`.
2. **Remote Edge Linux SBC Mode (`ssh ardu` -> `Arduino Ventuno Q` / `training06`, Qualcomm QCS8300 Monza 8-core `aarch64`, 14 GiB RAM, Ubuntu 24.04.4 LTS)**:
   - Deploys the exact same pure-Python governor package (`src/sdv_governor/`) and declarative YAML manifests (`data/`) into `/home/arduino/sdv-resource-governor/.venv`.
   - Executes the solver natively on the `aarch64` Linux processor (`10.0.0.130`), capturing remote `hostname`, `PID`, `machine`, `rss_kib`, `wall_elapsed_ms`, `code_sha256`, and verifying bit-for-bit identical `semantic_sha256` against the MacBook Pro (`DEP-02`, `DEP-03`).
   - If the board is physically unplugged or unpowered on USB-C/LAN, `sdv-governor doctor` and `deploy-board` report `BLOCKED_CONNECTION` cleanly without halting local execution.
3. **STM32H5 MCU Scope**:
   - Explicitly excluded from the admission/arbitration decision loop; no motor-control sketch is flashed or required for the challenge.

---

## 2. Quickstart (Clean Laptop or Linux Board)

```bash
# 0. Install in editable or standard wheel mode (offline-safe)
pip install --no-build-isolation --no-deps -e .

# 1. Validate all 6 guest manifests (G1..G6), ship profile, mission envelope, scenarios, priority profiles, and errata
python -m sdv_governor.cli validate

# 2. Check local & remote hardware readiness (never fails if board is disconnected)
python -m sdv_governor.cli doctor

# 3. Evaluate all 5 combinations of 4 official guests (Scenario A mass & placement regression)
python -m sdv_governor.cli combinations

# 4. Run 3 consecutive Scenario B replays and verify identical semantic SHA-256
python -m sdv_governor.cli replay --runs 3

# 5. Run the complete acceptance & regression test suite (98 tests: unit + integration + adversarial) and emit JUnit XML
pytest -v --junitxml=artifacts/final_run/junit.xml

# 6. Generate and fail-closed verify an evidence bundle (plus all 28 adversarial mutation checks)
python -m sdv_governor.cli campaign --run-id release_s6 --junit-xml artifacts/final_run/junit.xml --with-remote-parity
python -m sdv_governor.cli verify-evidence artifacts/release_s6 --run-mutations

# 7. Launch the Stateful Single-Screen 2D Operator Console (http://127.0.0.1:8765)
python -m sdv_governor.cli serve-ui --port 8765
```

---

## 3. Eight Physical Resource Classes & Key Physical Invariants

| Resource Class | Nominal / Degraded Envelope | Enforced Physical Invariants |
|---|---|---|
| **1. Power** | `200 kW` cruise reserve (`100 kW` gen loss); `LINE_A_FWD <= 150 kW`, `LINE_B_AFT <= 150 kW` | Net of hotel load (never double-counted). Distinguishes instantaneous `actual` load vs firm `committed` load (`G3` `25 kW` recharge reservation + `G4` `35 kW` D3 recovery reservation). |
| **2. Cooling** | `160 kW` @ `12 deg C` linear to `120 kW` @ `30 deg C`; `-40%` loop loss -> `72 kW` @ `30 deg C` | 1:1 electrical-to-thermal coupling. Thermal inertia (`+30 kW / 60 s = 1.8 MJ`, `600 s` cooldown) is stepped at 1 Hz and never used for sustained loads (`>60 s`). |
| **3. Positions** | `Bay1` (`<= 8,000 kg`), `Bay2` (`<= 4,000 kg`), `Hangar` (`<= 1,000 kg`, airframe `<= 150 kg`), `Hangar Roof`, `Flight Deck`, `Stern Ramp` | Powered-off / shed guests retain their physical bay, mast slot, mass, and stability footprint (`PHY-08`). `hangar_roof` enforces `G4` VTOL drone clearance. |
| **4. Mast** | `1` guest-allocatable slot (`mast_3`; `mast_1`, `mast_2` fixed ship); `<=60 kg`, `<=0.60 m2`, air `<=2 kW` | No chilled-water manifold on mast. `G1` (`45 kg`, `1.5 kW` air) and `G5` antenna (`25 kg`, `1.2 kW` air) compete for the single free guest mast slot (`1/1`). |
| **5. Mass & Stability** | `10,000 kg` mass margin; `12,000 kg-eq` stability (`x1.0` deck, `x1.2` hangar/roof, `x3.0` mast) | Computed per subcomponent & position (`G5` = `3000x1.0 + 25x3.0 = 3,075 kg-eq` on `mast_3` or `3,030 kg-eq` on `hangar_roof`). Errata: `G4 = 140 + 850 = 990 kg`; `3 of 5` 4-guest sets exceed `10,000 kg`. |
| **6. Data Links** | Dual `10 Gbps` LAN (`10 Gbps` single-ring guaranteed), `2-8 Mbps` SATCOM (`700 ms`), RF LOS, Acoustic (`15 s` hold) | `200 ms` urgent commands never routed solely over `700 ms` SATCOM. EMCON silence suppresses RF/SATCOM transmissions (`G4` standby `0 bps`). |
| **7. Compute Rack** | `4` nodes (`32 cores / 128 GiB / 1 accel` each), `20 TB` storage; `80%` allocatable | `25 cores`, `102,400 MiB RAM`, `1` whole indivisible accelerator per node (`max 3` cluster-wide), `16,000 GB` storage. Separate from Mac/Ventuno host RAM. |
| **8. Time** | `D1 <= 30 s`, `D3 <= 2 s`, `D4 first shed <= 5 s / full <= 60 s`, `STOP/HOLD/SECURE <= 200 ms` | Measured via `time.perf_counter_ns` after full 8-class budget evaluation; separated from canonical `semantic_sha256`. |

---

## 4. Adding a New Guest Card (`G7` Executable Example)

Any new payload card must conform to `schemas/guest_manifest.schema.json` and pass `parse_guest_manifest` without modifying solver code. Save a YAML manifest (or submit via `POST /api/admit_live`) with `schema_version: "sdv-guest-manifest/1.0"`, 1:1 thermal coupling (`cooling_water_w + cooling_air_mast_w == sustained_power_w`), monotonic power hierarchy (`min <= nominal <= sustained <= peak`), and explicit `value_origin` provenance:

```python
import json, yaml, jsonschema
from pathlib import Path
from sdv_governor.manifest import parse_guest_manifest

g7_yaml = """
schema_version: "sdv-guest-manifest/1.0"
guest_id: "G7"
name: "G7 — Compact Expendable Bathythermograph (XBT) Launcher"
category: "AUXILIARY_PAYLOAD"
safety_critical: false
default_mode: "STANDBY"
can_buffer_offline: true
subcomponents:
  - subcomponent_id: "G7_DECK_LAUNCHER"
    part_of: "G7"
    name: "Stern Deck XBT Autolauncher Pod"
    mass_kg: 180
    footprint_m2: 1.2
    wind_area_m2: 0.8
    preferred_positions: ["bay1_b", "bay2"]
    allowed_positions: ["bay1_b", "bay2", "bay1_a"]
    cooling_route: "CHILLED_WATER_LOOP"
    requires_water_manifold: false
    value_origin: "ASSUMED"
modes:
  STANDBY:
    description: "Launcher indexed and heater idle"
    min_power_w: 200
    nominal_power_w: 300
    sustained_power_w: 300
    sustained_duration_s: 3600
    peak_power_w: 500
    peak_duration_s: 10
    cooling_water_w: 300
    cooling_air_mast_w: 0
    compute_cores: 1
    compute_ram_mib: 1024
    compute_accelerators: 0
    shared_storage_gb: 20
    backbone_lan_bps: 1000000
    satcom_bps: 0
    safety_critical: false
    uninterruptible: false
    min_done_duration_s: 0
    min_cooldown_s: 0
    forbids_emcon_silence: false
    requires_deck_clear: false
    requires_quiet_acoustic_state: false
    capability_retention_ratio: 0.25
    value_origin: "ASSUMED"
installation:
  port_only: true
  min_crew_required: 2
  install_time_min: 30
  remove_time_min: 20
  sea_install_max_wind_kn: 0
  sea_install_min_speed_kn: 0.0
  sea_install_max_speed_kn: 0.0
vehicle_replacement:
  supported_at_sea: false
  min_crew_required: 0
  swap_duration_min: 0
  min_speed_kn: 0.0
  max_speed_kn: 0.0
  max_wind_kn: 0
  max_sea_state: 0
ship_state_constraints:
  min_speed_kn_for_operation: 0.0
  max_speed_kn_for_operation: 20.0
  max_wind_kn_for_operation: 35
  max_sea_state_for_operation: 6
  requires_heading_into_wind_for_launch_recovery: false
  forbids_deck_machinery_while_active: false
  acoustic_hold_handshake_min_s: 0
  acoustic_hold_handshake_max_s: 0
  acoustic_hold_default_s: 0
  low_battery_threshold_pct: null
  emergency_landing_deadline_s: null
  max_flight_endurance_min: null
dependencies:
  requires_guests: []
  excludes_guests: []
  conditional_excludes: []
mission_capabilities:
  hydro_sampling_value: 20.0
"""
raw = yaml.safe_load(g7_yaml)
schema = json.loads(Path("schemas/guest_manifest.schema.json").read_text())
jsonschema.validate(raw, schema)
manifest = parse_guest_manifest(raw)
```

---

## 5. Repository Structure & Governance Traceability

- `src/sdv_governor/`: Pure Python deterministic engine (`models.py`, `manifest.py`, `ship_state.py`, `budgets.py`, `placement.py`, `explanations.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`, `evidence.py`, `deployment.py`, `server.py`, `cli.py`, `pdf_export.py`).
- `schemas/` & `data/`: Strict JSON Schemas and declarative YAML manifests (`vessel_baseline.yaml`, `mission_envelope_a10.yaml`, `G1..G6`, `survey_primary.yaml`, `rescue_primary.yaml`, scenarios, and `brief_errata.yaml`).
- `BACKGROUND_IP.md`, `AI_USE_DISCLOSURE.md`, `THIRD_PARTY_COMPONENTS.csv`, `ASSUMPTIONS.yaml`, `requirements_matrix.csv`, `hardware_inventory.json`: Full provenance and compliance records.

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

# 5. Run the complete acceptance & corrective test suite (unit + integration + adversarial) and emit JUnit XML
pytest -v --junitxml=artifacts/release_c9/junit.xml

# 6. Generate and fail-closed verify an evidence bundle (plus all 20 adversarial mutation checks)
python -m sdv_governor.cli campaign --run-id release_c9 --junit-xml artifacts/release_c9/junit.xml --with-remote-parity
python -m sdv_governor.cli verify-evidence artifacts/release_c9 --run-mutations

# 7. Launch the Stateful Single-Screen 2D Operator Console (http://127.0.0.1:8765)
python -m sdv_governor.cli serve-ui --port 8765
```

---

## 3. Eight Physical Resource Classes & Key Physical Invariants

| Resource Class | Nominal / Degraded Envelope | Enforced Physical Invariants |
|---|---|---|
| **1. Power** | `200 kW` cruise reserve (`100 kW` gen loss); `LINE_A_FWD <= 70 kW`, `LINE_B_AFT <= 140 kW` | Net of hotel load (never double-counted). Distinguishes instantaneous `actual` load (`81 kW`) vs firm `committed` load (`105 kW` with G3 `25 kW` recharge reservation). |
| **2. Cooling** | `160 kW` @ `12 °C` linear to `120 kW` @ `30 °C`; `-40%` loop loss -> `72 kW` @ `30 °C` | 1:1 electrical-to-thermal coupling. Thermal inertia (`+30 kW / 60 s = 1.8 MJ`, `600 s` cooldown) is stepped per tick and never used for sustained loads (`>60 s`). |
| **3. Positions** | `Bay1` (`<= 8,000 kg`), `Bay2` (`<= 4,000 kg`), `Hangar` (`<= 1,000 kg`, airframe `<= 150 kg`), `Hangar Roof`, `Flight Deck`, `Stern Ramp` | Powered-off / shed guests retain their physical bay, mast slot, mass, and stability footprint (`PHY-08`). `hangar_roof` enforces `G4` VTOL drone clearance. |
| **4. Mast** | `3` positions (`mast_1`, `mast_2` fixed ship; `mast_3` free); `≤60 kg`, `≤0.60 m²`, air `≤2 kW` | No chilled-water manifold on mast. `G1` (`45 kg`, `1.5 kW` air) and `G5` antenna (`25 kg`, `1.2 kW` air) compete for `mast_3`. |
| **5. Mass & Stability** | `10,000 kg` mass margin; `12,000 kg-eq` stability (`×1.0` deck, `×1.2` hangar/roof, `×3.0` mast) | Computed per subcomponent & position (`G5` = `3000×1.0 + 25×3.0 = 3,075 kg-eq` on `mast_3` or `3,030 kg-eq` on `hangar_roof`). Errata: `G4 = 140 + 850 = 990 kg`; `3 of 5` 4-guest sets exceed `10,000 kg`. |
| **6. Data Links** | Dual `10 Gbps` LAN (`10 Gbps` single-ring guaranteed), `2–8 Mbps` SATCOM (`700 ms`), RF LOS, Acoustic (`15 s` hold) | `200 ms` urgent commands never routed solely over `700 ms` SATCOM. EMCON silence suppresses RF/SATCOM transmissions (`G4` standby `0 bps`). |
| **7. Compute Rack** | `4` nodes (`32 cores / 128 GiB / 1 accel` each), `20 TB` storage; `80%` allocatable | `25 cores`, `102,400 MiB RAM`, `1` whole indivisible accelerator per node (`max 3` cluster-wide), `16,000 GB` storage. Separate from Mac/Ventuno host RAM. |
| **8. Time** | `D1 ≤ 30 s`, `D3 ≤ 2 s`, `D4 first shed ≤ 5 s / full ≤ 60 s`, `STOP/HOLD/SECURE ≤ 200 ms` | Measured via `time.perf_counter_ns` at the solver boundary; separated from canonical `semantic_sha256`. |

---

## 4. Repository Structure & Governance Traceability

- `src/sdv_governor/`: Pure Python deterministic engine (`models.py`, `manifest.py`, `ship_state.py`, `budgets.py`, `placement.py`, `explanations.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`, `evidence.py`, `deployment.py`, `server.py`, `cli.py`, `pdf_export.py`).
- `schemas/` & `data/`: Strict JSON Schemas and declarative YAML manifests (`vessel_baseline.yaml`, `mission_envelope_a10.yaml`, `G1..G6`, `survey_primary.yaml`, `rescue_primary.yaml`, scenarios, and `brief_errata.yaml`).
- `BACKGROUND_IP.md`, `AI_USE_DISCLOSURE.md`, `THIRD_PARTY_COMPONENTS.csv`, `ASSUMPTIONS.yaml`, `requirements_matrix.csv`, `hardware_inventory.json`, `docs/CORRECTION_LOG.md`, `CORRECTIVE_STATUS.json`: Full provenance and compliance records.

# SDV Resource Governor -- Technical Pitch Deck (10 Slides)

## Slide 1: Executive Overview -- Deterministic Physics First
- **Mission**: Govern admission (`D1`), physical placement (`D2`), live conflict arbitration (`D3`), graceful degradation (`D4`), multi-crew scheduling (`D5`: `14/6/0`), and fail-closed physical accounting (`D6`) across **8 coupled physical resource classes** (`power`, `cooling`, `positions`, `mast`, `mass_stability`, `data`, `compute`, `time`) on a 72 m Software-Defined Vessel.
- **Core Principle**: Hard physical and life/vehicle safety limits are satisfied **first**; mission capability score, free margin, and displacement cost are optimized **second**. No priority weight can ever override a hard physical limit or drone ditching deadline.
- **Zero Opaque Runtime AI**: 100% deterministic, inspectable math (exact cooling curve, component-level power/water/air splits, parameterized witness explanations, 1 Hz internal simulation stepping).

---

## Slide 2: Fail-Closed Validation & Declarative Guest Ontology (D1)
- **Strict Schema & Type Discipline (`B2`, `F3`, `H06`, `H13`, `R2`, `R4`)**:
  - Rejects `NaN`, `Infinity`, negative or zero budgets, booleans used as numbers (`True != 1`), string booleans (`"false"`), unknown YAML/HTTP JSON fields (`UNKNOWN_FIELD`), dependency cycles, and invalid cooling coupling (`cooling_water_w + cooling_air_mast_w == sustained_power_w`).
  - Reproduces and defeats all **10 adversarial input flaws** (`resource_engine_repro.json`) and all **28 verifier mutation classes** (`false_accepts = 0`).
- **Six Declarative Guest Cards (`G1`-`G6`) with Field-Level Provenance**:
  - Official `G1` (`45 kg`, `1.5 kW` air on `mast_3`), `G2` (`3,500 kg`, `20/6 kW`), `G3` (`5,500 kg`, `25/1 kW`), `G4` (`140+850 = 990 kg` per `GRAZIANO_ROOF_AND_G4_MASS_0904`, `35/10/2 kW`), `G5` (`3,000+25 = 3,025 kg`, `45/8 kW`), plus custom civil card `G6` (`420 kg`, `port_only=true`, all fields `ASSUMED`) and zero-code dynamic `G7`.

---

## Slide 3: Scenario A (D1/D2) -- 8-Class Admission, Roof Clearance & Errata Proof
- **Organizer Clarifications Verified (`GRAZIANO_B_COMPOSITION_0855`, `GRAZIANO_ROOF_AND_G4_MASS_0904`)**:
  - With `G4 = 990 kg`, `G3+G4+G5 = 9,515 kg ≈ 9.52 t` and `G1+G3+G4+G5 = 9,560 kg` (`440 kg` mass margin, `9,853.0 kg-eq` stability with `G5` antenna on `hangar_roof`, `2,147.0 kg-eq` margin).
  - Across C(5,4)=5 sets, **3 exceed `10,000 kg`** (`{G1,G2,G3,G4}=10,035 kg`, `{G1,G2,G3,G5}=12,070 kg`, `{G2,G3,G4,G5}=13,015 kg`), while `{G1,G2,G4,G5}=7,560 kg` and `{G1,G3,G4,G5}=9,560 kg` require `G5_MAST_ANTENNA` on `hangar_roof` under conditional policy `ROOF_OUTSIDE_G4_RECOVERY_V1` (`YES_IF` until verified; `NO` under `CONFLICT` policy).
- **Single Platform Modification Counterfactual (`mast_4`)**:
  - Adding `mast_4` (`<= 60 kg, <= 0.6 m2, <= 2,000 W` air) separates `G1` (`1,500 W` on `mast_3`) and `G5` antenna (`1,200 W` on `mast_4`, `9,898.0 kg-eq`), removing the roof-clearance condition while mass/bays/stability still block the other three 4-guest sets.

---

## Slide 4: Scenario B (D3) -- Installed `G1+G3+G4+G5` & Causal Acoustic Handshake
- **Active 4-Guest Set (`G1+G3+G4+G5`)**:
  - `G1` on `mast_3` (`45 kg`, `1.5 kW` air, `LINE_A_FWD`), `G3` in `bay1` (`5,500 kg`, `1.0 kW` survey / `25.0 kW` firm charge, `LINE_A_FWD`), `G4` in `hangar`/`flight_deck` (`990 kg`, `10.0 kW` patrol / `35.0 kW` recovery, `LINE_B_AFT`), `G5` container in `bay2` + antenna on `hangar_roof` (`3,025 kg`, `45.0 kW` = `43.8 kW` water + `1.2 kW` roof air, `LINE_B_AFT`). `G2` is not installed in Scenario B.
- **Causal Command-ACK & A.6 Transition Timing**:
  - At `T=480 s`, `G4` reports **`18%` battery (`720 s` deadline -> `T <= 1,200 s`)** and D3 reserves `35.0 kW` (`82.5 / 106.5 kW` actual/committed).
  - Turning into `22 kn` wind requires verified `ACOUSTIC_HOLD_CONFIRMED` ACK from `G3` (`15 s` nominal, `[10, 60] s` window), `90 s` turn/stabilization into wind, and `240 s` approach/landing: **`rescue_primary` lands at `T=855 s`** (`345 s` margin); **`survey_primary` lands at `T=1,095 s`** (`105 s` margin).

---

## Slide 5: Scenario B (D4) -- Graceful Degradation, Feeders & Water/Air Split
- **Component-Level Power, Feeder & Thermal Ledger (`G1+G3+G4+G5`)**:
  - **Electrical Power (`actual / committed`)**: `57.5 / 81.5 kW` initial -> `82.5 / 106.5 kW` with `G4` `35 kW` reservation -> when generator reserve drops `200 kW -> 100 kW` at `T=720 s`, `106.5 kW > 100 kW` (`6.5 kW` committed deficit) triggers `G5` step-down (`45.0 -> 8.0 kW`), yielding **`45.5 / 69.5 kW <= 100 kW`** (`30.5 kW` committed margin) and **0 infeasible ticks (`91/91`)**!
  - **Chilled-Water & Air Domains**: Water load goes `54.8 / 78.8 kW` -> `79.8 / 103.8 kW` -> **`43.5 / 67.5 kW <= 72 kW`** after `G5` step-down (`43.8 -> 7.5 kW` water), surviving `30 deg C` + `-40%` loop loss (`72 kW`). Air cooling is `1,500 W` (`mast_3`) + `1,200 W` (`hangar_roof`) = `2,700 W` (`2,000 W` degraded).
  - **Feeder Routing (`<= 150 kW/line`)**: `LINE_A_FWD = 2.5 / 26.5 kW`; `LINE_B_AFT = 80.0 -> 43.0 kW`.

---

## Slide 6: Scenario C (D5) -- Crew Regimes (`14 / 6 / 0`) & Implicit Timing Rules
- **Concrete Implicit Rule (`Trigger -> Effect -> Confirmation`)**:
  - Urgent safety actions (`STOP`, `HOLD`, `SECURE <= 200 ms`, `< 1 ms` measured) execute autonomously across `14 / 6 / 0`.
  - Non-urgent confirmation & flight-deck clearing scale with crew dial: **`14` crew** (`9` deck avail, `30 s` confirm, `240 s` deck clear; A' replacement permitted at `T+1260 s`), **`6` crew** (`2` deck avail, `120 s` confirm, `600 s` deck clear; A' replacement blocked), **`0` crew** (`0` avail; software mode changes automated, manual evolutions and occupied-deck recovery **PROHIBITED**).
- **Component-Split Overconsumption (`+20%` G5, §6.5)**:
  - If `G5` surges `+20%` to **`54.0 kW` electrical** (`52.8 kW` water + `1.2 kW` roof air), D4 immediately throttles `G5` to `8.0 kW` (`7.5 kW` water + `0.5 kW` air).

---

## Slide 7: Physical Account (D6) -- 8 Assumption Families & Code Traceability
- **Complete 1-Page Physical Account (`docs/PHYSICAL_ACCOUNT_D6.pdf`)**:
  - Maps all **8 assumption families** (`Provenance/mass`, `Roof & drone clearance`, `Air & component split`, `Power & feeders/reservations`, `Chilled water & thermal inertia`, `State & A.6 transition timing`, `Compute rack & data links`, `Priorities & degraded capability`) to exact YAML files and `file.py::symbol_name` functions.
  - Enforces 4 physical compute nodes (`node_1..node_4`, `32c / 128 GiB / 1 acc` each; `80%` allocatable = `25c / 102.4 GiB` per node, `3` assignable discrete accelerators across cluster).

---

## Slide 8: Operator Interface -- Synchronized 2D Console & 3D Vessel Twin
- **Authoritative Shared Session (`server.py` + `ui/` + `viz3d/`)**:
  - Both the single-screen 2D Operator Console (`http://127.0.0.1:8765`) and the Three.js 3D Vessel Twin (`viz3d/`) bind to the same `GovernorSession` (`/api/state`, `/api/advance`, `/api/perturb`, `/api/admit_live`).
  - **Zero Silent Synthetic Fallback**: If the backend disconnects in live mode, the 3D Twin displays an explicit `STALE / DESCONECTADO` banner rather than fabricating synthetic telemetry.

---

## Slide 9: Dual-Platform Architecture & Live Ventuno Q Parity
- **Decoupled Laptop + Edge Board Execution (`B1`, `DEP-01`..`03`)**:
  - **MacBook Pro (`arm64`, Python 3.13.15)**: Full test suite, 1 Hz event-driven simulator, 90 s (`20x`) compressed demo, and 2D/3D Operator Interface.
  - **Arduino Ventuno Q (`training06`, Qualcomm QCS8300 Monza `aarch64`, 14 GiB RAM, Ubuntu 24.04.4)**: Live SSH deployment at `/home/arduino/sdv-resource-governor`.
  - **Bit-for-Bit Semantic Parity**: Identical `campaign_semantic_sha256` verified across `arm64` macOS and live `aarch64` Ventuno Q, with **28 / 28** adversarial mutations rejected (`false_accepts = 0`).

---

## Slide 10: 90-Second Demo Script & Honest Operational Boundaries
- **90-Second (`20x`) Compressed Mission Walkthrough (`docs/DEMO_SCRIPT_90S.md`)**:
  - `0-20 s`: Port Admission (`G1+G3+G4+G5 = 9,560 kg`, `9,853 kg-eq`, `ROOF_OUTSIDE_G4_RECOVERY_V1` verified) + zero-code `G7`.
  - `20-55 s`: Live Scenario B D3 arbitration (`T=480 s` `G4` `18%` battery -> `G3` `ACOUSTIC_HOLD` ACK -> turn into wind -> `855 s` / `1,095 s` touchdown).
  - `55-90 s`: `T=720 s` generator loss (`106.5 -> 69.5 kW` power, `103.8 -> 67.5 kW <= 72 kW` water via `G5` step-down) + `14/6/0` crew dial.
- **Explicit Rehearsal & Boundary Disclosure**:
  - Software command dispatch is `< 1 ms` (`<= 200 ms` budget); physical breaker/winch/turning durations follow Annex A.6; live human spoken 90-second rehearsal status is explicitly recorded as **`DEFERRED_OWNER`**.

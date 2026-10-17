# SDV Resource Governor -- Technical Pitch Deck (8 Slides)

## Slide 1: Executive Overview -- Deterministic Physics First
- **Mission**: Govern admission, placement, live arbitration, and graceful degradation across **8 coupled physical resource classes** (`power`, `cooling`, `positions`, `mast`, `mass_stability`, `data`, `compute`, `time`) on a Software-Defined Vessel.
- **Core Principle**: Hard physical and life/vehicle safety limits are satisfied **first**; mission capability score, free margin, and displacement cost are optimized **second**. No priority weight can ever override a hard physical limit or drone ditching deadline.
- **Zero Opaque Runtime AI**: 100% deterministic, inspectable math (exact cooling curve, integer Watts/kg/bps, parameterized witness explanations, 1 Hz internal simulation stepping).

---

## Slide 2: Fail-Closed Validation & Defeating Legacy Defects
- **Strict Schema & Type Discipline (`B2`, `F3`, `H06`, `H13`, `R2`, `R4`)**:
  - Rejects `NaN`, `Infinity`, negative or zero budgets, booleans used as numbers (`True != 1`), string booleans (`"false"`), unknown YAML/HTTP JSON fields (`UNKNOWN_FIELD`), dependency cycles, and invalid cooling coupling (`cooling_water_w + cooling_air_mast_w == sustained_power_w`).
  - Reproduces and defeats all **10 adversarial input flaws** (`resource_engine_repro.json`) and all **28 verifier mutation classes** (`false_accepts = 0`).
- **Six Declarative Guest Cards (`G1`-`G6`) with Field-Level Provenance**:
  - Official `G1` (`45 kg`, `1.5 kW` air), `G2` (`3,500 kg`, `20/6 kW`), `G3` (`5,500 kg`, `25/1 kW`), `G4` (`140+850 = 990 kg` per errata, `35/10/2 kW`), `G5` (`3,000+25 = 3,025 kg`, `45/8 kW`), plus custom civil card `G6` (`420 kg`, `port_only=true`, all fields `ASSUMED`).

---

## Slide 3: Scenario A (D1) -- 8-Class Admission & Errata Proof
- **Organizer Errata Verified (`DAT-02`, `DAT-03`)**:
  - With `G4 = 990 kg`, exactly **3 of 5** four-guest combinations exceed `10,000 kg`: `{G1,G2,G3,G4}=10,035 kg`, `{G1,G2,G3,G5}=12,070 kg`, `{G2,G3,G4,G5}=13,015 kg`.
  - The 2 mass-feasible sets `{G1,G2,G4,G5}=7,560 kg` and `{G1,G3,G4,G5}=9,560 kg` prove that **passing mass does not imply passing placement**: `G5`'s antenna collides with `G1` on `mast_3` (`1.5+1.2=2.7 kW > 2.0 kW` air) and with `G4` drone clearance on `hangar_roof`!
- **Single Platform Modification Counterfactual (`R6.1`)**:
  - Adding a second guest mast slot `mast_4` (`<= 60 kg`, `<= 2.0 kW` air) eliminates the `G1`/`G5` mast collision and `G4` roof clearance conflict for `{G1,G2,G4,G5}` and `{G1,G3,G4,G5}`, leaving only standard Mission Envelope A.10 operational mode scheduling (`G5` offline buffering in `W4` EMCON and `G2`/`G3`/`G4` time-multiplexed deck/acoustic windows).

---

## Slide 4: Scenario B (D3) -- Live Arbitration & Causal Acoustic Handshake
- **The Conflict**: `G3` AUV is executing a `40 min` quiet acoustic survey (`SURVEY_QUIET`, `1 kW`, `3 kn`) when `G4` UAV reports **`18%` battery (`< 20%` emergency -> `12 min / 720 s` max endurance)** requiring a turn into `22 kn` wind and `35 kW` recovery/charging power.
- **Causal Command-ACK & Precondition Verification (`R1.1`, `R1.2`)**:
  - D3 approval at `T=480 s` immediately registers a firm `35 kW` reservation (`D3_PLAN_V1_G4_RECOVERY`) for `G4` recovery/charging.
  - Turning into the wind requires a verified `ACOUSTIC_HOLD_CONFIRMED` ACK from `guest_id='G3'` matching the active `command_id` and `plan_version`, plus `power_reserve_w >= 35 kW` and `deck_clear=True`.
  - **`rescue_primary`**: Issues `ACOUSTIC_HOLD` at `T=480 s`, receives ACK at `T=495 s`, turns into wind, and lands `G4` at `T=855 s` (`345 s` margin before `T=1,200 s` deadline).
  - **`survey_primary`**: Completes `240 s` of survey pass, issues `ACOUSTIC_HOLD` at `T=720 s`, receives ACK at `T=735 s`, turns into wind, and lands `G4` at `T=1,095 s` (`105 s` margin before `T=1,200 s` deadline).

---

## Slide 5: Scenario B (D4) -- Graceful Degradation & Zero Infeasible Ticks
- **Honest Power vs. Thermal Accounting (`DEG-01`..`05`, `R1.1`)**:
  - When generator reserve drops from `200 kW` to `100 kW` at `T=720 s`, firm committed power including `G3`'s `25 kW` turnaround recharge reservation and `G4`'s `35 kW` D3 recovery reservation is `25 + 35 + 45 = 105 kW > 100 kW` (`5 kW` firm deficit).
  - Under both `survey_primary` and `rescue_primary`, D4 immediately steps down **`G5` (`45 kW -> 8 kW` `DEGRADED_70PCT_LOSS`)** at `T=720 s`, reducing committed power to `68 kW <= 100 kW` (`32 kW` free margin) and guaranteeing **0 infeasible ticks (`91/91` feasible)** across the entire mission!
  - At `30 deg C` seawater with `-40%` cooling loop loss (`72 kW` thermal capacity vs `100 kW` power), **chilled-water cooling binds first** (`79.8 kW > 72 kW`) and steps down `G5` (`79.8 kW -> 43.5 kW`).

---

## Slide 6: Scenario C -- Crew Regimes (14 / 6 / 0) & Resilience
- **Crew Regime & Deck Clearance Enforcement (`CRW-01`..`03`, `R3`, `R6.2`)**:
  - **14 Crew**: `9` deck crew -> `G3` (`4` crew) and `G4` (`3` crew) at-sea vehicle replacement permitted in A' window (`4-8 kn`, `wind <= 20 kn`, `SS <= 3`). Simultaneous `G2` winch (`0-5 kn`) + `G4` flight ops (`5-12 kn` into wind, touching only at `5.0 kn`) remain forbidden by Annex A.5 `deck_clear` and conflicting heading/station-keeping rules.
  - **6 Crew**: `2` deck crew -> serialized manual launch/recovery (`G2` or `G4`) permitted with `60-180 s` watch handover; at-sea vehicle replacement (`min_crew >= 3`) blocked.
  - **0 Crew**: Zero physical installations or manual deck evolutions at sea (`CRW-02`); `G6` (`420 kg > 250 kg`, bay install) is `port_only=True` across all regimes.

---

## Slide 7: Dual-Platform Architecture & Live Ventuno Q Parity
- **Decoupled Laptop + Edge Board Execution (`B1`, `DEP-01`..`03`)**:
  - **MacBook Pro (`arm64`, Python 3.13.15)**: Full test suite, 1 Hz event-driven simulator, 90 s (20x) compressed demo, and stateful 2D operator console (`http://127.0.0.1:8765`).
  - **Arduino Ventuno Q (`training06`, Qualcomm Dragonwing IQ8 `aarch64`, 16 GB RAM, Ubuntu 24.04.4)**: Live SSH deployment at `/home/arduino/sdv-resource-governor`.
  - **Bit-for-Bit Semantic Parity**: Identical `campaign_semantic_sha256` verified across `arm64` macOS and live `aarch64` Ventuno Q (`training06`).

---

## Slide 8: Single-Screen 2D Operator Console & Fail-Closed Evidence
- **Stateful Single-Screen 2D Operator Console (`B10`, `H12`, `R2`, `R5`)**:
  - Persistent `GovernorSession` (`session_catalog`, `manifest_registry`, `snapshot_id`, `state_version`), per-tick 8-class budget and 2D vessel topology synchronization, 90 s (`1000 ms/tick = 20x`) Scenario B playback, perturbation injector, and live Scenario A guest card editor.
- **Fail-Closed Evidence Verifier (`B8`, `H05`, `R4`)**:
  - Recomputes all summary metrics and semantic hashes from raw JSON logs, verifies all acceptance criteria `<testcase>` nodes in `tests.*` with portable `requirements_matrix.csv`, cross-checks `git_commit` and `environment`, enforces `<= 200 ms` tick deadlines, and rejects **28 / 28** adversarial mutations (`false_accepts = 0`).

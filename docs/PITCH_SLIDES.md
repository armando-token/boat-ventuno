# SDV Resource Governor — Technical Pitch Deck (8 Slides)

## Slide 1: Executive Overview — Deterministic Physics First
- **Mission**: Govern admission, placement, live arbitration, and graceful degradation across **8 coupled physical resource classes** (`power`, `cooling`, `positions`, `mast`, `mass_stability`, `data`, `compute`, `time`) on a Software-Defined Vessel.
- **Core Principle**: Hard physical and life/vehicle safety limits are satisfied **first**; mission capability score, free margin, and displacement cost are optimized **second**. No priority weight can ever override a hard physical limit or drone ditching deadline.
- **Zero Opaque Runtime AI**: 100% deterministic, inspectable math (`Fraction` rational cooling curve, integer Watts/kg/bps/ms, parameterized witness explanations).

---

## Slide 2: Fail-Closed Validation & Defeating Legacy Defects
- **Strict Schema & Type Discipline (`B2`, `F3`, `H06`, `H13`)**:
  - Rejects `NaN`, `Infinity`, negative or zero budgets, booleans used as numbers (`True != 1`), string booleans (`"false"`), unknown fields, dependency cycles, and invalid cooling coupling (`cooling_water_w + cooling_air_mast_w == sustained_power_w`).
  - Reproduces and defeats all **10 adversarial input flaws** (`resource_engine_repro.json`) and all **20 verifier mutation classes** (`false_accepts = 0`).
- **Six Declarative Guest Cards (`G1`–`G6`) with Field-Level Provenance**:
  - Official `G1` (`45 kg`, `1.5 kW` air), `G2` (`3,500 kg`, `20/6 kW`), `G3` (`5,500 kg`, `25/1 kW`), `G4` (`140+850 = 990 kg` per errata, `35/10/2 kW`), `G5` (`3,000+25 = 3,025 kg`, `45/8 kW`), plus custom civil card `G6` (`420 kg`, all fields `ASSUMED`).

---

## Slide 3: Scenario A (D1) — 8-Class Admission & Errata Proof
- **Organizer Errata Verified (`DAT-02`, `DAT-03`)**:
  - With `G4 = 990 kg`, exactly **3 of 5** four-guest combinations exceed `10,000 kg`: `{G1,G2,G3,G4}=10,035 kg`, `{G1,G2,G3,G5}=12,070 kg`, `{G2,G3,G4,G5}=13,015 kg`.
  - The 2 mass-feasible sets `{G1,G2,G4,G5}=7,560 kg` and `{G1,G3,G4,G5}=9,560 kg` prove that **passing mass does not imply passing placement**: `G5`'s antenna collides with `G1` on `mast_3` (`1.5+1.2=2.7 kW > 2.0 kW` air) and with `G4` drone clearance on `hangar_roof`!
- **14-Day Mission Envelope (`A.10`) & Conditioned Availability**:
  - Rejects pier-only comfort (`16 °C`); propagates `A.10` offshore thermal/sea-state restrictions directly into the final `YES` / `YES_IF` / `NO` verdict.

---

## Slide 4: Scenario B (D3) — Live Arbitration Under Safety Deadlines
- **The Conflict**: `G3` AUV is executing a `40 min` quiet acoustic survey (`SURVEY_QUIET`, `1 kW`, `3 kn`) when `G4` UAV reports **`18%` battery (`<20%` emergency -> `12 min / 720 s` max endurance)** requiring a turn into `22 kn` wind and `35 kW` recovery/charging power.
- **Non-Negotiable Safety Invariant (`ARB-01`..`04`)**:
  - Candidate plans are generated and filtered against hard physical/weather/deadline constraints before scoring by `priority_profile` weights; touchdown (`<= T+720 s`) is separated from the 20-min (`1,200 s`) `35 kW` hangar recharge.
  - **`rescue_primary`**: Issues `ACOUSTIC_HOLD` immediately at $T=480\text{ s}$, receives ACK at $T=495\text{ s}$, turns into wind, and lands `G4` at $T=615\text{ s}$ (`585 s` safety margin before $T=1,200\text{ s}$ deadline).
  - **`survey_primary`**: Completes `240 s` of survey pass, issues `ACOUSTIC_HOLD` at $T=720\text{ s}$, receives ACK at $T=735\text{ s}$, turns into wind, and lands `G4` at $T=855\text{ s}$ (`345 s` safety margin).

---

## Slide 5: Scenario B (D4) — Graceful Degradation & Thermal Binding
- **Honest Power vs. Thermal Accounting (`DEG-01`..`05`)**:
  - When generator reserve drops from `200 kW` to `100 kW`, `G3 (1 kW survey) + G4 (35 kW) + G5 (45 kW) = 81 kW` actual draw ($\le 100\text{ kW}$, **zero fabricated instantaneous deficit**).
  - With G3's firm `25 kW` turnaround recharge reservation (`25 + 35 + 45 = 105 kW > 100 kW`), the `5 kW` firm deficit triggers **`G5` mode step-down (`45 kW` $\rightarrow$ `8 kW` `DEGRADED_70PCT_LOSS`)**, saving `37 kW` power and reducing committed load to `68 kW` (`32 kW` free margin) while retaining `30%` capability.
  - At $30\,^\circ\text{C}$ seawater with $-40\%$ cooling loop loss (`72 kW` thermal capacity vs `100 kW` power), **chilled-water cooling binds first** (`79.8 kW > 72 kW`) and steps down `G5` (`79.8 kW` $\rightarrow$ `43.5 kW`).

---

## Slide 6: Scenario C — Crew Regimes (14 / 6 / 0) & Resilience
- **Crew Regime Enforcement (`CRW-01`..`03`)**:
  - **14 Crew**: `9` deck/payload crew $\rightarrow$ `2` concurrent deck operations (`G2` + `G4`) and at-sea vehicle replacement ($A'$ window: `G4` `3` crew / `30 min`; `G3` `4` crew / `45 min`) permitted.
  - **6 Crew**: `2` deck crew $\rightarrow$ single serialized manual launch/recovery permitted; at-sea vehicle replacement (`min_crew >= 3`) blocked.
  - **0 Crew**: Zero physical installations or manual deck evolutions at sea (`CRW-02`); autonomous software re-tasking, mode step-down, and load shedding only.
- **Fault Resilience (`SAF-01`, `RUN-03`, `RUN-04`)**:
  - `STOP`/`HOLD`/`SECURE` measured in `< 1 ms` ($\le 200\text{ ms}$ budget); stale feeds clamp to $30\,^\circ\text{C}$ cooling; `+20%` overconsumption auto-throttled.

---

## Slide 7: Dual-Platform Architecture & Live Ventuno Q Parity
- **Decoupled Laptop + Edge Board Execution (`B1`, `DEP-01`..`03`)**:
  - **MacBook Pro (`arm64`, Python 3.13.15)**: Full test suite, event-driven simulator, 90 s compressed demo, and stateful 2D operator console (`http://127.0.0.1:8765`).
  - **Arduino Ventuno Q (`training06`, Qualcomm Dragonwing IQ8 `aarch64`, 16 GB RAM, Ubuntu 24.04.4)**: Live SSH deployment at `/home/arduino/sdv-resource-governor`.
  - **Bit-for-Bit Semantic Parity**: Identical `campaign_semantic_sha256` verified across `arm64` macOS and live `aarch64` Ventuno Q (`training06`).

---

## Slide 8: Single-Screen 2D Operator Console & Fail-Closed Evidence
- **Stateful Single-Screen 2D Operator Console (`B10`, `H12`)**:
  - Persistent `GovernorSession` (`snapshot_id`, `state_version`, `POST /api/reset`), live 8-class budget gauges, 2D vessel bay/mast/hangar/roof/rack map, 90 s compressed Scenario B timeline scrubber, instant `survey_primary` $\leftrightarrow$ `rescue_primary` switcher, perturbation injector, and live Scenario A guest card editor.
- **Fail-Closed Evidence Verifier (`B8`, `H05`)**:
  - Recomputes all summary metrics and semantic hashes from raw JSON logs, verifies all 49 acceptance criteria `<testcase>` nodes in `junit.xml`, and rejects **20 / 20** adversarial mutations (`false_accepts = 0`).

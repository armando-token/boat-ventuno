# Operational Memo -- Scenario C: Governance Across 14, 6, and 0 Crew Regimes

**To:** SDV Program Office & Vessel Operations Command
**From:** Principal Systems Architect, `sdv-resource-governor`
**Subject:** Deterministic Resource, Deck-Safety, and Autonomy Rules Under Crew Regimes **14**, **6**, and **0**

---

## 1. Executive Summary
A Software-Defined Vessel (SDV) cannot treat human crew availability as a mere scheduling annotation: crew count directly governs **which physical payload transitions are admissible**, **which manual deck evolutions can be staffed**, **whether at-sea vehicle replacement (A' window) is physically safe**, and **how fault recovery escalates when telemetry degrades or a payload overconsumes**. In `sdv-resource-governor`, human crew availability, flight-deck clearance (`deck_clear`), and port-only installation constraints are enforced as hard operational guards inside `scheduling.py`, `admission.py`, and `replay.py`.

---

## 2. Quantitative Regime Comparison (`FULL_CREW_14` vs. `REDUCED_CREW_6` vs. `UNCREWED_0`)

| Operational Dimension | Regime 1: 14 Crew (FULL_CREW_14) | Regime 2: 6 Crew (REDUCED_CREW_6) | Regime 3: 0 Crew (UNCREWED_0) |
|---|---|---|---|
| Bridge & Engine Watch | 5 personnel | 4 personnel | 0 (Remote Shore Control + Onboard Autonomy) |
| Available Deck/Payload Crew | 9 personnel | 2 personnel | 0 personnel |
| Max Concurrent Manual Deck Teams | 2 teams (for non-conflicting manual tasks) | 1 team (strictly serialized) | 0 manual deck evolutions |
| G4 UAV Launch / Recovery (5-12 kn into wind, wind <= 25 kn, SS <= 4, deck_clear=true) | PERMITTED (2 deck crew; 15-60 s local bridge confirmation) | PERMITTED (2 deck crew; 60-180 s watch handover confirmation) | FORBIDDEN (requires manual flight-deck handling; only stowed/standby modes) |
| G2 Winch / Probe Deployment (0-5 kn, wind <= 20 kn, SS <= 4, deck_clear=true) | PERMITTED (2 deck crew; cannot overlap G4 flight ops) | PERMITTED (2 deck crew; serialized) | FORBIDDEN (requires umbilical/winch deck handling) |
| Simultaneous G2 Winch + G4 Flight Ops | FORBIDDEN (Annex A.5 requires flight deck clear during G4 ops; speed windows 0-5 kn vs 5-12 kn only touch at the 5.0 kn boundary and require conflicting station-keeping vs into-wind heading) | FORBIDDEN (insufficient crew + Annex A.5 deck clearance + conflicting heading/station-keeping profiles) | FORBIDDEN (0 crew) |
| At-Sea Vehicle Replacement A' (4-8 kn, wind <= 20 kn, SS <= 3; G4: 3 crew/30m, G3: 4 crew/45m) | PERMITTED (available_crew=9 >= 4 for G3 and >= 3 for G4) | BLOCKED (available_crew=2 < 3 for G4 and < 4 for G3 safe replacement at sea) | FORBIDDEN (0 crew; all vehicle replacements deferred to port) |
| At-Sea Bay/Mast or >250 kg Module Install (G5, G6 420 kg in bay2/bay1_a) | FORBIDDEN AT SEA (Annex A.5 bay/mast installs are port-only; Annex A.4 limits at-sea packages to <= 250 kg) | FORBIDDEN AT SEA (port-only bay/mast + >250 kg + available_crew=2 < 4) | FORBIDDEN AT SEA (CRW-02: zero physical installations at sea) |
| Autonomous Sensor & Compute Modes (G1, G3 already in-water or in-cradle charge, G5, G6 port-installed) | 100% Operational | 100% Operational | Operational for software-governed modes (mode step-down, throttle, power shed); physical stern-ramp launch/recovery of G3 requires 4 deck crew and is FORBIDDEN under 0 crew |

---

## 3. Concrete Operational Rules & Physical Interlocks Enforced by the Solver
1. **Why G2 and G4 Cannot Execute Simultaneous Deck Operations Even With 14 Crew (`Annex A.5`)**:
   - While **14 crew** provides `9` deck/payload personnel (enough to staff two manual handling teams for non-conflicting tasks), **G2 ROV/winch deployment and G4 UAV launch/recovery are physically incompatible**: (a) Annex A.5 mandates that *"Launch or recovery of a drone requires the flight deck to be clear of other ongoing deck operations"* (`state.deck_clear == True`), and (b) **G2** requires low-speed station-keeping (`0-5 kn`) whereas **G4** requires `5-12 kn` with vessel heading aligned within `+/- 15 deg` of the wind (their speed ranges only touch at the single `5.0 kn` boundary, where `deck_clear` and heading/winch-handling rules still strictly forbid overlap).
2. **Why G6 (`420 kg`, `bay2`/`bay1_a`) Is Strictly Port-Only Across All Crew Regimes (`Annex A.4 & A.5`)**:
   - Annex A.5 specifies that payload installation in **Bays 1-2** and **Mast Positions 1-3** is a **port-only operation**, and Annex A.4 restricts at-sea A' package installation to light deck packages **`<= 250 kg`** in `Sea State <= 3`, `wind <= 20 kn`, and `4-8 kn`. Because **G6** weighs `420 kg > 250 kg` and installs into `bay2` / `bay1_a`, `G6` has `port_only: true` and is rejected (`NO` / `NOT_NOW`) for at-sea installation under **14**, **6**, and **0** crew alike.
3. **Acoustic Survey Deck-Machinery Lockout (`G3` `forbids_deck_machinery_while_active=True`)**:
   - Across both **14** and **6** crew regimes, while **G3** is in `SURVEY_QUIET` (`1 kW`, `2-6 kn`), hydraulic A-frames, winches (**G2**), and flight-deck handling gear (**G4**) are locked out to prevent acoustic corruption of the seabed survey sonar.
   - Before turning into the wind or operating deck machinery for **G4** recovery, the governor must issue `ACOUSTIC_HOLD` to **G3**, verify a causal `ACOUSTIC_HOLD_CONFIRMED` ACK from `guest_id='G3'` matching the active `command_id` and `plan_version` (`15 s` nominal acoustic handshake, `60 s` timeout), and confirm **G3** has transitioned to `HOLD_ACOUSTIC`.
4. **Separation of Normal Flight Window vs. A' At-Sea Replacement Window (`CRW-03`)**:
   - **Normal G4 Flight Launch/Recovery**: vessel speed **`5-12 kn`**, wind **`<= 25 kn`**, Sea State **`<= 4`**, heading aligned within `+/- 15 deg` of wind, `deck_clear=True`, **`2` deck crew** (permitted under **14** and **6** crew).
   - **A' At-Sea Vehicle Replacement**: vessel speed **`4-8 kn`**, wind **`<= 20 kn`**, Sea State **`<= 3`**, `deck_clear=True`, **`3` deck crew / `30 min`** for **G4** or **`4` deck crew / `45 min`** for **G3** (permitted only under **14** crew; blocked under **6** and **0** crew).

---

## 4. Fault Governance & Decision Escalation Under Reduced (6) and Uncrewed (0) Regimes
When operating with **6** or **0** crew, manual intervention on tripped breakers, overconsuming payloads, or degraded cooling loops is delayed (**6 crew**: `60-180 s` watch handover) or impossible (**0 crew**). The governor enforces three automated fail-safe barriers:
- **Urgent Safety Commands (`STOP`, `HOLD`, `SECURE <= 200 ms`, `SAF-01`)**: Routed strictly over the dual `10 Gbps` local shipboard LAN (`< 5 ms` transport latency), never over `600-800 ms` SATCOM. Measured execution latency on both MacBook Pro (`arm64`) and the live Arduino Ventuno Q (`training06`, `aarch64`) is **`< 1 ms`** (`<< 200 ms` deadline).
- **Guest Overconsumption (`+10%` to `+20%`, `RUN-04`)**: Because none of `G1`-`G5` is safety-critical by design, if a payload (e.g., **G5** declared `45 kW` drawing `54 kW` = `+20%`) exceeds its manifest envelope, 1:1 thermal coupling immediately adds `+9 kW` to the chilled-water ledger and the governor autonomously throttles **G5** to `DEGRADED_70PCT_LOSS` (`8 kW`) while protecting active uninterruptible/recovery tasks (**G3** recharge, **G4** flight/recovery).
- **Lost or Stale Telemetry Feeds (`RUN-03`)**: If `seawater_temp_c` exceeds its TTL (`STALE` / `UNAVAILABLE`), the governor never extrapolates optimistic cooling; it clamps to the conservative **`30 deg C` (`120 kW` nominal / `72 kW` degraded)** thermal bound until sensor freshness is restored.

# Operational Memo -- Scenario C (D5): Governance Across 14 / 6 / 0 Crew Regimes

**To:** SDV Program Office & Vessel Operations Command
**From:** Principal Systems Architect, `sdv-resource-governor`
**Subject:** Deterministic Resource, Deck-Safety, Implicit Timing & Autonomy Rules Under Crew Regimes **14 / 6 / 0**

---

## 1. Executive Summary
A Software-Defined Vessel (SDV) cannot treat human crew availability (`14 / 6 / 0`) as a mere scheduling annotation: crew regime directly governs **which physical payload transitions are admissible**, **how long manual confirmation and flight-deck clearing take (`30 s` vs. `120 s` confirmation; `240 s` vs. `600 s` deck clearing; prohibited if occupied under `0`)**, **whether at-sea vehicle replacement (A' window) is physically safe**, and **how fault recovery escalates when telemetry degrades or a payload overconsumes**. In `sdv-resource-governor`, the `14 / 6 / 0` crew dial, flight-deck clearance (`deck_clear`), and port-only installation constraints are enforced as hard operational guards inside `scheduling.py`, `admission.py`, `degradation.py`, and `replay.py`.

---

## 2. Quantitative Regime Comparison (`14 / 6 / 0` Crew Dial: `FULL_CREW_14` vs. `REDUCED_CREW_6` vs. `UNCREWED_0`)

| Operational Dimension | Regime 1: 14 Crew (`FULL_CREW_14`) | Regime 2: 6 Crew (`REDUCED_CREW_6`) | Regime 3: 0 Crew (`UNCREWED_0`) |
|---|---|---|---|
| Bridge & Engine Watch | 5 personnel | 4 personnel | 0 (Remote Shore Control + Onboard Autonomy) |
| Available Deck/Payload Crew | 9 personnel | 2 personnel | 0 personnel |
| Max Concurrent Manual Deck Teams | 2 teams (for non-conflicting manual tasks) | 1 team (strictly serialized) | 0 manual deck evolutions |
| Non-Urgent Operator Confirmation (`Trigger -> Effect -> Confirmation`) | `30 s` nominal (`15-60 s` local bridge watch confirmation) | `120 s` nominal (`60-180 s` watch handover confirmation) | `0 s` human confirmation: automated software mode transitions permitted; physical/manual deck evolutions strictly PROHIBITED |
| Flight-Deck Clearing Latency (when `deck_clear=false`) | `240 s` (`4 min` with dedicated deck crew) | `600 s` (`10 min` serialized single-team deck clearing) | PROHIBITED if deck is occupied (`0` crew cannot manually clear deck equipment at sea) |
| G4 UAV Launch / Recovery (`5-12 kn` into wind, `wind <= 25 kn`, `SS <= 4`, `deck_clear=true`) | PERMITTED (`2` deck crew; `30 s` confirmation; `240 s` deck clear if needed) | PERMITTED (`2` deck crew; `120 s` confirmation; `600 s` deck clear if needed) | FORBIDDEN for manual deck handling; if deck occupied, recovery is PROHIBITED (`0` crew) |
| G2 Winch / Probe Deployment (`0-5 kn`, `wind <= 20 kn`, `SS <= 4`, `deck_clear=true`) | PERMITTED (`2` deck crew; cannot overlap G4 flight ops) | PERMITTED (`2` deck crew; serialized) | FORBIDDEN (requires umbilical/winch deck handling) |
| Simultaneous G2 Winch + G4 Flight Ops | FORBIDDEN (Annex A.5 requires flight deck clear during G4 ops; `0-5 kn` vs `5-12 kn` only touch at `5.0 kn` with conflicting heading vs station-keeping) | FORBIDDEN (insufficient crew + Annex A.5 deck clearance + conflicting heading/station-keeping profiles) | FORBIDDEN (`0` crew) |
| At-Sea Vehicle Replacement A' (`4-8 kn`, `wind <= 20 kn`, `SS <= 3`; G4: `3` crew/`30m`, G3: `4` crew/`45m`) | PERMITTED (`available_crew=9 >= 4` for G3 and `>= 3` for G4; earliest window `T+1260 s`) | BLOCKED (`available_crew=2 < 3` for G4 and `< 4` for G3 safe replacement at sea) | FORBIDDEN (`0` crew; all vehicle replacements deferred to port) |
| At-Sea Bay/Mast or `>250 kg` Module Install (G5, G6 `420 kg` in `bay2`/`bay1_a`) | FORBIDDEN AT SEA (Annex A.5 bay/mast installs are port-only; Annex A.4 limits at-sea packages to `<= 250 kg`) | FORBIDDEN AT SEA (port-only bay/mast + `>250 kg` + `available_crew=2 < 4`) | FORBIDDEN AT SEA (`CRW-02`: zero physical installations at sea) |

---

## 3. Concrete Implicit Rule (`Trigger -> Effect -> Confirmation`) & Physical Interlocks Enforced by the Solver
1. **Concrete Implicit Rule (`Trigger -> Effect -> Confirmation` Across `14 / 6 / 0`)**:
   - **Trigger**: Any operational event requiring deck state change, mode escalation, or recovery window preparation (e.g., `G4` `18%` battery recovery alert or `deck_clear=false` prior to flight operations).
   - **Effect**: Urgent safety/protection actions (`STOP`, `HOLD`, `SECURE <= 200 ms`, `G3` `ACOUSTIC_HOLD`, or `G5` overload step-down) execute **autonomously without waiting for human confirmation** across all `14 / 6 / 0` regimes. For non-urgent manual evolutions and physical deck preparation, crew regime dictates both **confirmation latency** (`30 s` under `14` crew vs. `120 s` under `6` crew vs. **automated software-only / physical evolution prohibited** under `0` crew) and **flight-deck clearing time** (`240 s` under `14` crew vs. `600 s` under `6` crew vs. **strictly prohibited if occupied** under `0` crew).
   - **Confirmation & Precondition Gate**: In Scenario B (`14` crew default), `G4` recovery adds confirmation + acoustic hold handshake (`15 s` nominal, `[10, 60] s` valid window) + vessel turn/stabilization (`90 s`) + approach/landing (`240 s`). If switched to `6` crew with an occupied deck (`600 s` deck clearing + `120 s` confirmation), total preparation exceeds the `720 s` (`12 min`) battery emergency window (`480 + 600 + 120 + 90 + 240 = 1,530 s > 1,200 s`), causing the solver to flag the plan as infeasible unless the deck is pre-cleared! Under `0` crew, any evolution requiring manual deck clearing (`deck_clear=false`) or manual vehicle handling is immediately rejected (`PROHIBITED`).
2. **Why G2 and G4 Cannot Execute Simultaneous Deck Operations Even With 14 Crew (`Annex A.5`)**:
   - While **14 crew** provides `9` deck/payload personnel (enough to staff two manual handling teams for non-conflicting tasks), **G2 ROV/winch deployment and G4 UAV launch/recovery are physically incompatible**: (a) Annex A.5 mandates that *"Launch or recovery of a drone requires the flight deck to be clear of other ongoing deck operations"* (`state.deck_clear == True`), and (b) **G2** requires low-speed station-keeping (`0-5 kn`) whereas **G4** requires `5-12 kn` with vessel heading aligned within `+/- 15 deg` of the wind (their speed ranges only touch at the single `5.0 kn` boundary, where `deck_clear` and heading/winch-handling rules still strictly forbid overlap).
3. **Why G6 (`420 kg`, `bay2`/`bay1_a`) Is Strictly Port-Only Across All `14 / 6 / 0` Crew Regimes (`Annex A.4 & A.5`)**:
   - Annex A.5 specifies that payload installation in **Bays 1-2** and **Mast Positions 1-3** is a **port-only operation**, and Annex A.4 restricts at-sea A' package installation to light deck packages **`<= 250 kg`** in `Sea State <= 3`, `wind <= 20 kn`, and `4-8 kn`. Because **G6** weighs `420 kg > 250 kg` and installs into `bay2` / `bay1_a`, `G6` has `port_only: true` and is rejected (`NO` / `NOT_NOW`) for at-sea installation under **14**, **6**, and **0** crew alike.
4. **Acoustic Survey Deck-Machinery Lockout (`G3` `forbids_deck_machinery_while_active=True`)**:
   - Across `14 / 6 / 0` crew regimes, while **G3** is in `SURVEY_QUIET` (`1 kW`, `2-6 kn`), hydraulic A-frames, winches (**G2**), and flight-deck handling gear (**G4**) are locked out to prevent acoustic corruption of the seabed survey sonar until `ACOUSTIC_HOLD_CONFIRMED` ACK is verified.

---

## 4. Fault Governance & Component-Split Overconsumption Under `14 / 6 / 0` Regimes
- **Urgent Safety Commands (`STOP`, `HOLD`, `SECURE <= 200 ms`, `SAF-01`)**: Routed strictly over the dual `10 Gbps` local shipboard LAN (`< 5 ms` transport latency), never over `600-800 ms` SATCOM. Measured software dispatch latency on both MacBook Pro (`arm64`) and the live Arduino Ventuno Q (`training06`, `aarch64`) is **`< 1 ms`** (`<< 200 ms` deadline).
- **Guest Overconsumption (`+20%` Component-Split Accounting, `RUN-04`, §6.5)**: Because none of `G1`-`G5` is safety-critical by design, if **G5** (`45.0 kW` nominal = `43.8 kW` container chilled-water + `1.2 kW` roof-antenna air) overconsumes by `+20%` to **`54.0 kW` electrical** (`+9.0 kW` surge absorbed by the `3,000 kg` compute container -> **`52.8 kW` chilled-water + `1.2 kW` roof air**), the chilled-water ledger immediately reflects the `+9.0 kW` thermal spike and the governor autonomously throttles **G5** to `DEGRADED_70PCT_LOSS` (`8.0 kW` electrical = `7.5 kW` water + `0.5 kW` roof air, retaining `30%` capability) while protecting active uninterruptible/recovery tasks (**G3** recharge, **G4** flight/recovery).
- **Lost or Stale Telemetry Feeds (`RUN-03`)**: If `seawater_temp_c` exceeds its TTL (`STALE` / `UNAVAILABLE`), the governor never extrapolates optimistic cooling; it clamps to the conservative **`30 deg C` (`120 kW` nominal / `72 kW` degraded)** thermal bound until sensor freshness is restored.

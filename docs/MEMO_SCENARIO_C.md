# Operational Memo — Scenario C: Governance Across 14, 6, and 0 Crew Regimes

**To:** SDV Program Office & Vessel Operations Command  
**From:** Principal Systems Architect, `sdv-resource-governor`  
**Subject:** Deterministic Resource, Deck-Safety, and Autonomy Rules Under Crew Regimes **14**, **6**, and **0**

---

## 1. Executive Summary
A Software-Defined Vessel (SDV) cannot treat human crew availability as a mere scheduling annotation: crew count directly governs **which physical payload transitions are admissible**, **how many deck evolutions can execute concurrently**, **whether at-sea vehicle replacement ($A'$) is physically safe**, and **how fault recovery escalates when telemetry degrades or a payload overconsumes**. In `sdv-resource-governor`, human crew availability is enforced as a hard operational guard inside `scheduling.py` and `admission.py`.

---

## 2. Quantitative Regime Comparison (`FULL_CREW_14` vs. `REDUCED_CREW_6` vs. `UNCREWED_0`)

| Operational Dimension | Regime 1: **14 Crew** (`FULL_CREW_14`) | Regime 2: **6 Crew** (`REDUCED_CREW_6`) | Regime 3: **0 Crew** (`UNCREWED_0`) |
|---|---|---|---|
| **Bridge & Engine Watch** | `5` personnel | `4` personnel | `0` (Remote Shore Control + Onboard Autonomy) |
| **Available Deck/Payload Crew** | **`9` personnel** | **`2` personnel** | **`0` personnel** |
| **Max Concurrent Manual Deck Ops** | **`2` simultaneous** (e.g., G2 ROV + G4 UAV) | **`1` serialized** (G2 *or* G4, never simultaneous) | **`0` manual deck evolutions** |
| **G4 UAV Launch / Recovery** (`5–12 kn`, wind $\le 25\text{ kn}`, `SS <= 4`) | **PERMITTED** (`15–60 s` local bridge confirmation) | **PERMITTED** (`1–3 min` watch handover confirmation; blocks G2 deck ops) | **FORBIDDEN** if manual deck arrest/handling required; only pre-stowed or automated modes |
| **G2 Winch / Probe Deployment** (`0–5 kn`, wind $\le 20\text{ kn}`, `SS <= 4`) | **PERMITTED** | **PERMITTED** (serialized; cannot overlap G4 flight deck ops) | **FORBIDDEN** (requires umbilical/winch deck handling) |
| **At-Sea Vehicle Replacement ($A'$)** (`G4`: `3` crew / `30 min`; `G3`: `4` crew / `45 min`) | **PERMITTED** (`available_crew=9 >= 4` required for crane/hangar replacement at sea) | **BLOCKED** (`available_crew=2 < 3` for G4 and `< 4` for G3 safe replacement at sea) | **FORBIDDEN** (`0` crew; all physical replacements deferred to port) |
| **At-Sea Physical Module Install** (`G6` `10 ft` lab, `min_crew=4`) | **PERMITTED** in calm $A'$ window (`0–4 kn`, wind $\le 12\text{ kn}$) | **BLOCKED** (`available_crew=2 < 4`) | **FORBIDDEN** (`CRW-02`: zero physical installation changes at sea) |
| **Autonomous Sensor & Compute Guests** (`G1`, `G3` in-water survey, `G5`, `G6` auto-sampler) | **100% Operational** | **100% Operational** | **100% Operational** (governed via software mode switching, throttle, and power shed) |

---

## 3. Acoustic Survey Deck-Machinery Lockout & $A'$ Window Separation
1. **Acoustic Survey Lockout (`G3` `forbids_deck_machinery_while_active=True`)**:
   - Regardless of whether **14** or **6** crew are aboard, while **G3** is in `SURVEY_QUIET` (`1 kW`, `2–6 kn`), hydraulic A-frames, winches (`G2`), and flight-deck handling gear (`G4`) are **locked out** to prevent acoustic corruption of the seabed survey sonar.
   - To launch or recover **G4** (`UAV_SAR`) or deploy **G2** (`ROV_INSPECTION`), the governor must first issue `ACOUSTIC_HOLD` to **G3**, wait for the mandatory **`15 s` acoustic modem handshake** (`10–60 s` spec), and confirm G3 is in `HOLD_ACOUSTIC` before unlocking deck machinery or turning into the wind.
2. **Separation of Normal Flight Window vs. $A'$ At-Sea Replacement Window (`CRW-03`)**:
   - **Normal G4 Flight Launch/Recovery**: vessel speed **`5–12 kn`**, wind **$\le 25\text{ kn}$**, Sea State **$\le 4$**, heading aligned within $\pm 15^\circ$ of wind, **`2` deck crew**.
   - **$A'$ At-Sea Vehicle Replacement**: vessel speed **`4–8 kn`**, wind **$\le 20\text{ kn}$**, Sea State **$\le 3$** (`G4`, `3` crew / `30 min`) or **$\le 4$** (`G3`, `4` crew / `45 min`). At `10 kn` and `22 kn` wind, normal UAV recovery is admitted (`YES`), whereas at-sea vehicle replacement is rejected (`NOT_NOW` / blocked by $A'$ envelope).

---

## 4. Fault Governance Under Reduced and Uncrewed Regimes (`RUN-03`, `RUN-04`, `SAF-01`)
When operating with **6** or **0** crew, physical manual intervention on tripped breakers or overheated racks is slow (**6 crew**) or impossible (**0 crew**). The governor enforces three automated fail-safe barriers:
- **Urgent Safety Commands (`STOP`, `HOLD`, `SECURE` $\le 200\text{ ms}$, `SAF-01`)**: Routed strictly over the dual `10 Gbps` local shipboard LAN (`<5 ms` transport latency), never over `600–800 ms` SATCOM. Measured execution latency on both MacBook Pro and the `aarch64` Linux board (`training06`) is **`< 1 ms`** ($\ll 200\text{ ms}$ budget).
- **Guest Overconsumption (`+10%` to `+20%`, `RUN-04`)**: Because none of `G1`–`G5` is safety-critical by design, if a payload (e.g., `G5` declared `45 kW` drawing `54 kW` = `+20%`) exceeds its manifest envelope, 1:1 thermal coupling immediately adds `+9 kW` to the chilled-water ledger and the governor autonomously throttles `G5` to `DEGRADED_70PCT_LOSS` (`8 kW`) while protecting active uninterruptible/recovery tasks (`G3` recharge, `G4` flight/recovery).
- **Lost or Stale Telemetry Feeds (`RUN-03`)**: If `seawater_temp_c` exceeds its `5,000 ms` TTL (`STALE` / `UNAVAILABLE`), the governor never extrapolates optimistic cooling; it clamps to the conservative **$30\,^\circ\text{C}$ (`120 kW` nominal / `72 kW` degraded)** thermal bound until sensor freshness is restored.

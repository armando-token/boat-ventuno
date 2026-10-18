# 90-Second Live Operator Demo Script (`DEMO_SCRIPT_90S.md`)

**Subject:** 90-Second (`20x` Time-Compressed) Live Demonstration of `sdv-resource-governor` (`D1`–`D6` + 2D/3D Operator Interface)
**Rehearsal Status:** `DEFERRED_OWNER` *(Automated 90-step API playback, 2D console, and 3D vessel twin synchronization are 100% implemented and test-verified; live spoken human rehearsal by the project owner is explicitly marked `DEFERRED_OWNER`.)*

---

## 1. Pre-Demo Launch Checklist (`< 30 s` Setup)
- Start the authoritative governor backend and 2D Operator Console:
  `PYTHONPATH=src .venv/bin/python -m sdv_governor.cli serve --host 127.0.0.1 --port 8765`
- (Optional) Open the synchronized Three.js 3D Vessel Twin (`viz3d/serve_viz3d.py`) connected to `http://127.0.0.1:8765/api/state`.
- Confirm initial baseline on screen: **Scenario B official 4-guest configuration (`G1 + G3 + G4 + G5`)**, `9,560 kg` total mass (`440 kg` margin vs. `10,000 kg`), `9,853.0 kg-eq` stability (`2,147.0 kg-eq` margin vs. `12,000 kg-eq`), `G3 + G4 + G5 = 9,515 kg ≈ 9.52 t`, `G1` on `mast_3` (`1,500 W` air), `G3` in `bay1`, `G4` in `hangar`/`flight_deck`, `G5` container in `bay2`, and `G5` antenna on `hangar_roof` (`1,200 W` air under `ROOF_OUTSIDE_G4_RECOVERY_V1`).

---

## 2. Second-by-Second Operator Walkthrough (`00:00` to `01:30`, `20x` Compression: `1 s` Wall Clock = `20 s` Sim Time)

| Wall Clock Window | Sim Time (`sim_time_s`) | Operator Action in 2D Console / 3D Twin | Deterministic Governor Output & Physical Numbers to Highlight |
|---|---|---|---|
| **`00:00 - 00:20`** (`20 s`) | `T = 0 s` (Port / Baseline) | Show **Scenario A Admission (`D1`/`D2`)** panel: test C(5,4) combinations and toggle `hangar_roof_clearance_verified`. Admit custom civil card `G6` (`420 kg`) or declarative `G7` without code changes. | Exact 3 of 5 four-guest sets exceed `10,000 kg` (`10,035`, `12,070`, `13,015 kg`). `G1+G3+G4+G5` (`9,560 kg`, `9,853.0 kg-eq`) returns `YES_IF` when `ROOF_OUTSIDE_G4_RECOVERY_V1` is unverified (blocking commit) and commits atomically once verified. Initial Scenario B draw is `57.5 kW` actual / `81.5 kW` committed (`54.8 / 78.8 kW` water + `2.7 kW` air). |
| **`00:20 - 00:36`** (`16 s`) | `T = 0 -> 480 s` (Min 0 to Min 8) | Click **Play 90s (`20x`)** under `survey_primary` (or `rescue_primary`). Watch monotonic mission clock advance to `T = 480 s`. | At `T = 480 s`, `G4` reports **`18%` battery (`< 20%` emergency -> `720 s` deadline, must land by `T <= 1,200 s`)** in `22 kn` wind while `G3` is in `SURVEY_QUIET` (`1.0 kW`, `forbids_deck_machinery_while_active=true`). **D3** immediately registers `G4`'s `35.0 kW` recovery/charging reservation (`actual / committed` rises to `82.5 / 106.5 kW`, water `79.8 / 103.8 kW`). |
| **`00:36 - 00:54`** (`18 s`) | `T = 480 -> 720 s` (Min 8 to Min 12) | Observe causal **D3 Acoustic Handshake** and simultaneous **D4 Generator Loss (`200 kW -> 100 kW`)** at `T = 720 s`. Inject `COOLING_LOSS_30C_40PCT` (`72 kW` water limit). | At `T = 720 s`, generator reserve drops to `100.0 kW`. Committed power `106.5 kW > 100.0 kW` (`LINE_A_FWD = 2.5 / 26.5 kW`, `LINE_B_AFT = 80.0 kW`) and water `103.8 kW > 72.0 kW` trigger **D4** to step down **`G5` (`45.0 -> 8.0 kW`, retaining `30%` capability)**. Committed power drops to **`69.5 kW <= 100 kW`** (`45.5 kW` actual; `LINE_B_AFT = 43.0 kW`) and water drops to **`67.5 kW <= 72.0 kW`** (`43.5 kW` actual) with **0 infeasible ticks**! |
| **`00:54 - 01:12`** (`18 s`) | `T = 720 -> 1,095 s` (Min 12 to Min 18.25) | Watch A.6 transition phases complete: `G3` `ACOUSTIC_HOLD_CONFIRMED` ACK (`15 s`) -> vessel turn/stabilize into wind (`90 s`) -> `G4` approach/landing (`240 s`). Switch profile to compare `rescue_primary` vs. `survey_primary`. | Under `rescue_primary`, `G4` lands at **`T = 855 s`** (`345 s` margin before `1,200 s`). Under `survey_primary`, `G3` completes `240 s` of survey pass before `ACOUSTIC_HOLD` at `T = 720 s`, ACK at `T = 735 s`, turn complete at `T = 825 s`, and `G4` touchdown at **`T = 1,095 s`** (`105 s` margin before `1,200 s`). |
| **`01:12 - 01:30`** (`18 s`) | `T = 1,095 -> 1,800 s` (Min 18.25 to Min 30) | Toggle **Scenario C (`D5`) Crew Dial (`14 / 6 / 0`)** and trigger `G5` `+20%` overconsumption (`54.0 kW` = `52.8 kW` water + `1.2 kW` air). Show `URGENT_STOP` (`< 1 ms`). | Crew dial shows `14` crew (`30 s` confirm, `240 s` deck clear, A' swap at `T+1260 s`), `6` crew (`120 s` confirm, `600 s` deck clear, A' blocked), and `0` crew (manual evolutions prohibited). Closing summary: `91/91` feasible ticks, `28/28` mutations rejected, Mac `arm64` <-> Ventuno Q `aarch64` parity verified. |

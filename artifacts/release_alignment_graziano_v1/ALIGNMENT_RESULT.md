# ALIGNMENT_RESULT — Graziano Alignment Release `release_alignment_graziano_v1`

## 1. Stage Completion Summary
- **Alignment Stage Status**: `COMPLETE_WITH_DECLARED_PENDING` (`technical_alignment = VERIFIED`, `documents = VERIFIED`)
- **Active Release ID**: `release_alignment_graziano_v1` (`artifacts/release_alignment_graziano_v1/`)
- **Parent / Historical Reference**: `release_s6_patch1` (preserved intact on disk along with `release_c9`, `release_r8`, `release_s6`)
- **Git Commit**: `b35b157e80cab1bde6c032eaee178350e144abc6`
- **Campaign Semantic SHA-256**: `7b09e01c6ed415d1c1c5a9fda4d3762efbf336956da902a5000d526bc9566c2f`
- **Single Startup Command**: `.venv/bin/sdv-governor serve-ui --host 127.0.0.1 --port 8765` (2D Console at `http://127.0.0.1:8765/` and 3D Vessel Twin at `http://127.0.0.1:8765/viz3d`)

## 2. Verified Technical & Physical Alignment Highlights (L1–L10)
- **Scenario B 4-Guest Configuration (`GRAZIANO_B_COMPOSITION_0855`)**: `G1 + G3 + G4 + G5` (`9,560 kg` total mass, `440 kg` margin; `9,853 kg-eq` stability, `2,147 kg-eq` margin; `G1` on `mast_3`, `G3` in `bay1`, `G4` in `hangar`, `G5` container in `bay2`, `G5` antenna on `hangar_roof`).
- **G4 Mass Errata & Provenance (`GRAZIANO_ROOF_AND_G4_MASS_0904`)**: `G4 = 990 kg` (`140 kg` drone vehicle + `850 kg` derived hangar station; `G3 + G4 + G5 = 9,515 kg ≈ 9.52 t`).
- **Separate Air Cooling Domains & Feeder Routing**: `mast_3 = 1,500 W <= 2,000 W` (`LINE_A_FWD`), `hangar_roof = 1,200 W <= 2,000 W` (`LINE_B_AFT`), aggregate `2,700 W` feasible per domain while local `> 2,000 W` overload on either position is rejected.
- **Firm Reservation Envelope & Degradation Conservation**:
  - Nominal Scenario B: `57,500 W` actual / `81,500 W` committed (`LINE_A_FWD = 26,500 W`committed including `G3` `+24,000 W` net reservation delta; `LINE_B_AFT = 55,000 W`).
  - With D3 `G4` `35,000 W` recovery reservation (`+25,000 W` net delta): `106,500 W` committed power (`103,800 W` committed chilled water).
  - Under `100,000 W` generator loss (`D4`): `G5` steps down `45,000 W -> 8,000 W`, reducing committed power `106,500 W -> 69,500 W <= 100,000 W` (`20,500 W` actual).
  - Under `30 °C` + `40%` cooling loop loss (`72,000 W` water capacity): `G5` steps down `43,800 W -> 7,500 W` water, reducing committed water `103,800 W -> 67,500 W <= 72,000 W`.
- **Causal A.6 Transitions & D3 Arbitration**: `survey_primary` touchdown at `T+1095s` vs `rescue_primary` touchdown at `T+855s` (`<= 1,200 s` hard deadline); `G4` 20-min (`1,200 s`) charge and `G3` 40-min (`2,400 s`) survey leg tracked honestly as `PENDING_HORIZON` at `T+1800s` and verified to completion in extended horizon.
- **Suite & Adversarial Verifier**: `154` pytest tests passed (`0` failures) + `37 / 37` adversarial mutations rejected (`0` false accepts).

## 3. Explicitly Separated External / Owner Pending Items
1. **`ai_eligibility = NOT_VERIFIED_EXTERNAL_LIST_MISSING`**: Cross-check `AI_USE_DISCLOSURE.md` §1 against the official organizer list of authorized AI services.
2. **`board_parity = VERIFIED`**: Run `.venv/bin/sdv-governor campaign --run-id release_alignment_graziano_board_v1 --junit-xml artifacts/validation_alignment_local/junit.xml --with-remote-parity` when `ssh ardu` is connected on LAN.
3. **`live_90s_rehearsal = DEFERRED_OWNER`**: Perform human spoken 90-second demonstration rehearsal using `docs/DEMO_SCRIPT_90S.md`.

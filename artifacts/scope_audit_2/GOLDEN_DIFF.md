# GOLDEN_DIFF — scope audit 2

| Hash | Before | After |
|---|---|---|
| campaign_semantic_sha256 | 3a0aa7f6… | ab2eb619… |
| scenario_B_survey_primary_sha256 | bbafe549… | d2e75589… |
| scenario_B_rescue_primary_sha256 | 8a63209c… | 951aaee2… |
| scenario_B_cooling_limited_sha256 | 1cd63884… | 302b02ac… |
| scenario_C crew_regimes | 7ed9a261… | 4c032b17… |
| official placements (ch_c4) | 7e2bd188… (105 calls) | 908cc2c3… (229 calls) |

## Why

1. **G4 SATCOM 1.5 / 1.0 Mbit/s → 0** (`data/guests/G4_uav_sar.yaml`). Annex B gives G4 only LOS radio
   (5–20 Mbit/s video, zero in silence); the SATCOM figure was an undocumented invention. Budget
   snapshots in every Scenario B tick change (data class), so the B hashes change. **The B decisions are
   identical**: minute 8 (survey: hold G3 at T+720, land G4 at T+1095; rescue: hold at T+480, land at
   T+855) and minute 12 (106.5 kW > 100 kW, G5 → DEGRADED_70PCT_LOSS) were diffed text-for-text.
2. **YES_IF now ranks throttles** (`STEP_DOWN` of installed guests to a declared lower mode) next to
   removals, as the brief asks ("ranked list of what is displaced or throttled"). The extra
   `find_feasible_placement` calls (105 → 229) are those throttle trials.
3. **Shared-SATCOM squeeze is not blamed on the candidate**: in nominal envelope windows the engine tries
   the cheapest step-down of installed guests and labels it "pre-existing" when the installed set already
   exceeds the window without the candidate.
4. Seven placement records changed, all `G5` into sets containing `G4`: G5 into {G1,G2,G4} was
   infeasible only because of G4's invented SATCOM (8.2 > 8 Mbit/s); it is now feasible.

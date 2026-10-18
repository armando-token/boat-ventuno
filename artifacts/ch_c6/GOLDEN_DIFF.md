# GOLDEN_DIFF — C6 (own-radar -30% on G1 / Quiet radio)

| Field | Value |
|---|---|
| Justifying change | C6 (`feat/scope-chain`): `capability_effects.py`, `own_radar` in `vessel_baseline.yaml`, `own_radar_policy` in vessel state, `capability_effects` in D4 `guest_impacts` |
| Previous `campaign_semantic_sha256` | `2050a9ddb0d72aef590d931aa212b11b9b24a3a4bc876f1824bc0aec29b12900` (C5; pre-chain baseline was `ebff0e22...15c3c`) |
| New `campaign_semantic_sha256` | `feda2501801265be52e055381e6ffeb57d5fd5c108f4f9c4da8ce47306468316` |
| B survey | `90a2ce11...4a6d` -> `bbafe54983e7870a5f6b763676cdb416e4809478a536d74ab6379ae33c4ea4d0` |
| B rescue | `d5b36570...4c7a` -> `8a63209c8bdb6748644c325f6c04c02dac321c63d18d224801e72c30ebefe92c` |
| B cooling | `d4638ec0...ec5ae` -> `1cd63884e3b05e9b35494dcf4ee6c11c1b6f86f1057fa59c8ad6ecefdf5b74e6` |
| C (events) | unchanged |
| Triple replay | 2 consecutive dumps produce the exact same hash (measured) |

## Semantic Deltas (JSON diff by path; 4 paths only)

| Path | Type | Cause |
|---|---|---|
| `scenario_A_g6_admission/ship_profile_hash` | updated | vessel YAML received `own_radar` section |
| `scenario_B_survey_primary_sha256`, `_rescue_primary_`, `_cooling_limited_` | updated | `guest_impacts` adds `capability_effects`, G1 `capability_retention_after` 1.0 -> 0.70, D4 rationale includes `"70% capability: own radar transmitting nearby"`, and state snapshot includes `own_radar_policy` |

## What Did NOT Change (verified by test CH-C6-05)

- G5 45 -> 8 kW step-down in D4 and power draws of all guests.
- D1 verdicts and placements (Scenario A is in `PORT_OPS`, where the navigation radar does not transmit).

# GOLDEN_DIFF — C5 (Scenario A: "holds with one genset down")

| Field | Value |
|---|---|
| Justifying change | C5 (`feat/scope-chain`): D1 declares `one_genset_down`; fixed sentence in `rationale`; `DEGRADED_GEN_LOSS_100KW_30C` window tagged `represents: SINGLE_GENSET_LOSS` |
| Previous `campaign_semantic_sha256` | `ebff0e22a74e20fc781a3d9af31e8f591e725f993194c2a0ba31d17fdf715c3c` |
| New `campaign_semantic_sha256` | `2050a9ddb0d72aef590d931aa212b11b9b24a3a4bc876f1824bc0aec29b12900` |
| Scenario B (survey / rescue / cooling) and C (events) hashes | **Unchanged** (pinned in `test_forecast_non_interference.py`) |
| Triple replay | 3 consecutive runs of `run_full_campaign_suite` return the identical hash (measured) |
| Verdicts and placements | **None changed** |

## Semantic Deltas (JSON diff, 35 paths)

Only new fields added and text/hash derived from the YAML update:

| Path (collapsed indices) | Type | Count |
|---|---|---|
| `scenario_A_four_guest_combinations/combinations[]/fourth_guest_one_genset_down_holds` | added | 5 |
| `.../fourth_guest_ranked_displacements[]/conditioned_availability/one_genset_down` | added | 26 |
| `scenario_A_g6_admission/one_genset_down` | added | 1 |
| `scenario_A_g6_admission/conditioned_availability/one_genset_down` | added | 1 |
| `scenario_A_g6_admission/rationale` | updated (second sentence appended after verdict) | 1 |
| `scenario_A_g6_admission/mission_envelope_hash` | updated (YAML received `represents` field) | 1 |

Method: dump `semantic_summary` on the base commit and on this commit and compare by JSON path.

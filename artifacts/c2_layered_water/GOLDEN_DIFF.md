# GOLDEN_DIFF — C2 (layered water / acoustic link)

## Campaign hash

| | `campaign_semantic_sha256` |
|---|---|
| Before (commit `dd21439`, C7) | `ebff0e22a74e20fc781a3d9af31e8f591e725f993194c2a0ba31d17fdf715c3c` |
| After (C2) | `fd806cda1c871f8c2c9db047f2b34f5547c70d2b60fe9111d4e35ba292646231` |

## What changed (and what did not)

Unchanged, bit for bit (pinned in `tests/unit/test_c2_acoustic_link.py::test_ch_c2_03_official_b_hashes_identical`):

- Official Scenario B, `survey_primary`: `90a2ce1137b0d5729c466f2c313b143d0d189ab21ae9d13093b5d3e380b94a6d`
- Official Scenario B, `rescue_primary`: `d5b36570f0b6bf0ee068dff610a96e382a6c2192458f1cc93e31cc2fcfaf4c7a`
- Cooling-limited 30 C: `d4638ec023aef2f484f700b24ae314b7f7d0261456ee697b5b35e9ae332ec5ae`
- Scenario A combinations / G6 admission and Scenario C crew regimes (same inputs).

Added to `semantic_summary` (the only reason the campaign hash moves), from the new declared variant
`data/scenarios/scenario_B_layered_water.yaml` (`DECLARED_VARIANT`, `initial_state.water_column_layered: true`):

| Key | Hash |
|---|---|
| `scenario_B_layered_water_survey_primary_sha256` | `614f91110c9101651fe8b96a0787ca97c77b127bd69d29c7eba87cb97db2aacc` |
| `scenario_B_layered_water_rescue_primary_sha256` | `e8e4c76cfc01785c2433e2ed18e5a539ea94178283e13e4ca0b07aa01cef9213` |

## Behavioural difference in the variant (measured)

With the acoustic command link down, G3 cannot ACK; it holds autonomously after the ASSUMED 120 s timeout
(`G3_LOST_LINK_TIMEOUT_S`) and the ship turns only afterwards.

| Profile | HOLD cmd | G3 hold (no ACK) | Turn | G4 touchdown | Deadline | Margin | Official B touchdown |
|---|---|---|---|---|---|---|---|
| rescue_primary | 480 s | 600 s | 600 s | **960 s** | 1200 s | 240 s | 855 s |
| survey_primary | 720 s | 840 s | 840 s | **1200 s** | 1200 s | **0 s** | 1095 s |

Hypothesis in plan section 3.6 (about 960 s and about 1200 s): matches. The survey case is feasible only because the
deadline boundary is inclusive (`deadline_boundary_inclusive: true`); any further delay would invalidate the plan.

## Verification

- Triple campaign replay: three runs give the same hash `fd806cda…6231`.
- Triple layered replay: `test_ch_c2_07_layered_replay_is_deterministic_and_campaign_has_variant`.
- Evidence verifier recomputes the campaign hash including the two variant entries (`evidence.py`, bundle `replay_timeline.json` carries both variant blocks).
- Pinned hashes updated: `tests/integration/test_forecast_non_interference.py`, `tests/integration/test_public_env_integration.py`, `tests/unit/test_c7_area_exclusion.py`; docs `README.md`, `docs/FORECAST_AND_PUBLIC_DATA.md` (the PDF renderings are not regenerated here).

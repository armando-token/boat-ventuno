# GOLDEN_DIFF — C4 (D2 `D2_LEXICOGRAPHIC_V2`: alternatives and rejected placements)

| Field | Value |
|---|---|
| Justifying change | C4 (`feat/scope-chain`): `find_feasible_placement` returns `objective_id`, `sort_key_fields`, `chosen_key`, `evaluated_count`, `placement_alternatives` (<=5), and `rejected_placements` (<=5); D1 propagates them |
| Previous `campaign_semantic_sha256` (C6) | `feda2501801265be52e055381e6ffeb57d5fd5c108f4f9c4da8ce47306468316` |
| New `campaign_semantic_sha256` | `eda98c784d116531ad3e31860a1fe2c5186236839cf6a07883dba2c79b361071` |
| Scenario B (survey / rescue / cooling) and C hashes | **Unchanged** relative to C6 |
| Double dump check | 2 consecutive runs produce the exact same hash (measured) |

## Gate F6-G -> Tie-break order applied

`scarcity_cost` is placed at position 4 of the lexicographic key (after `roof_cond_rank`, `pref_penalty`, and `-retention_milli`), acting strictly as a tie-breaker so official baseline placements remain untouched.

Applied key: `(roof_cond_rank, pref_penalty, -retention_milli, scarcity_cost, -min_margin_bucket, stab_eq, node_used_cores, nid)`.

**Measured result:** All 105 placement searches in the official campaign (`G1..G6`, Scenarios A and B) produce **the exact same positions and compute node** as base commit `c4909d5` (identical sequence, sha256 `7e2bd1887bd8563daf0f4f784d44bb85c864ddfc2811d7f1238c41871f54cc3a`; pinned in `CH-C4-06`).

## Semantic Deltas (JSON diff, 6 paths, all additions)

| Path | Type |
|---|---|
| `scenario_A_g6_admission/placement/{objective_id,sort_key_fields,chosen_key,evaluated_count}` | added |
| `scenario_A_g6_admission/placement_alternatives` | added |
| `scenario_A_g6_admission/rejected_placements` | added |

Verdicts unchanged.

## Design Notes

- Alternatives deduplicated by position set; if only one position set is feasible, compute-node variants are shown.
- Rejected placements exclude position sets that have a feasible node and are sorted by `(witness_count, pref_penalty, positions, node)`.
- Exact tie-breaking preserves evaluation order (preferred positions first).
- `manifest.py::combinations` (first-fit mass check for C(5,4)) is unchanged.

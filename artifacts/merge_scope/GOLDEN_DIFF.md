# GOLDEN_DIFF — merge feat/scope-chain (C5,C6,C4,C3) into feat/scope-compliance-fixes (C10,C7,C2)

| Hash | Before | After | Cause |
|---|---|---|---|
| campaign_semantic_sha256 | fd806cda… (C10+C7+C2) | 3a0aa7f6… | Union of C2 layered-water variants + C5 one_genset_down + C6 own-radar retention + C4 scarcity key |
| B survey_primary | 90a2ce11… | bbafe549… | C6 (G1 capability retention) — see artifacts/ch_c6/GOLDEN_DIFF.md |
| B rescue_primary | d5b36570… | 8a63209c… | C6 |
| B cooling_limited | d4638ec0… | 1cd63884… | C6 |
| C crew_regimes | 7ed9a261… | 7ed9a261… | unchanged |

Invariants checked after merge:
- Campaign deterministic (3/3 identical runs).
- B official with layered=false still equals B official (CH-C2-03 now pinned to post-C6 values).
- Layered variants: touchdown 1200 s (survey) / 960 s (rescue), same as C2 measurement.
- verify-evidence --run-mutations: verified=true, false_accepts=0.
- All pins centralised in tests/fixtures/golden_hashes.json.

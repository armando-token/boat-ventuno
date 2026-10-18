# GOLDEN_DIFF — C3 (logical reconfiguration at 0 crew at sea)

- campaign_semantic_sha256 before: `eda98c784d116531ad3e31860a1fe2c5186236839cf6a07883dba2c79b361071`
- campaign_semantic_sha256 after:  `eda98c784d116531ad3e31860a1fe2c5186236839cf6a07883dba2c79b361071` (unchanged; JSON diff of the full campaign dump is empty)
- B survey / rescue / cooling and C crew hashes: unchanged.

Why: the new fields (`alternative_action`, `logical_reconfiguration`) are added only in the crew-0-at-sea `NO` branch of D1,
and no official campaign case reaches it (Scenario A is PORT_OPS; Scenario C crew checks use other paths).
The `logical_workload` key in G5 is read by the parser but not copied into any campaign output.

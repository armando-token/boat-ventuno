# GOLDEN_DIFF — C6 (radar propio −30 % en G1 / Quiet radio)

| Campo | Valor |
|---|---|
| Cambio que lo justifica | C6 (`feat/scope-chain`): `capability_effects.py`, `own_radar` en `vessel_baseline.yaml`, `own_radar_policy` en el estado, `capability_effects` en `guest_impacts` de D4 |
| `campaign_semantic_sha256` anterior | `2050a9ddb0d72aef590d931aa212b11b9b24a3a4bc876f1824bc0aec29b12900` (C5; el oficial previo a la cadena era `ebff0e22…15c3c`) |
| `campaign_semantic_sha256` nuevo | `feda2501801265be52e055381e6ffeb57d5fd5c108f4f9c4da8ce47306468316` |
| B survey | `90a2ce11…4a6d` → `bbafe54983e7870a5f6b763676cdb416e4809478a536d74ab6379ae33c4ea4d0` |
| B rescue | `d5b36570…4c7a` → `8a63209c8bdb6748644c325f6c04c02dac321c63d18d224801e72c30ebefe92c` |
| B cooling | `d4638ec0…ec5ae` → `1cd63884e3b05e9b35494dcf4ee6c11c1b6f86f1057fa59c8ad6ecefdf5b74e6` |
| C (eventos) | sin cambio |
| Triple replay | 2 volcados consecutivos dan el mismo hash (medido) |

## Deltas semánticos (diff JSON por ruta; solo 4)

| Ruta | Tipo | Causa |
|---|---|---|
| `scenario_A_g6_admission/ship_profile_hash` | cambiado | el YAML del buque recibió `own_radar` |
| `scenario_B_survey_primary_sha256`, `_rescue_primary_`, `_cooling_limited_` | cambiado | `guest_impacts` añade `capability_effects`, G1 `capability_retention_after` 1.0 → 0.70, frase de D4 con «70% capability: own radar transmitting nearby», y el snapshot incluye `own_radar_policy` |

## Lo que NO cambió (verificado por test CH-C6-05)

- G5 45 → 8 kW en D4 y las potencias de todos los invitados.
- Veredictos y colocaciones de D1 (Escenario A está en `PORT_OPS`, el radar no transmite en puerto).

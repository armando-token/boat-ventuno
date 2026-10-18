# GOLDEN_DIFF — C5 (Scenario A: "holds with one genset down")

| Campo | Valor |
|---|---|
| Cambio que lo justifica | C5 (`feat/scope-chain`): D1 declara `one_genset_down`; frase fija en `rationale`; ventana `DEGRADED_GEN_LOSS_100KW_30C` marcada `represents: SINGLE_GENSET_LOSS` |
| `campaign_semantic_sha256` anterior | `ebff0e22a74e20fc781a3d9af31e8f591e725f993194c2a0ba31d17fdf715c3c` |
| `campaign_semantic_sha256` nuevo | `2050a9ddb0d72aef590d931aa212b11b9b24a3a4bc876f1824bc0aec29b12900` |
| Hashes B (survey / rescue / cooling) y C (eventos) | **Sin cambio** (siguen fijados en `test_forecast_non_interference.py`) |
| Triple replay | 3 corridas de `run_full_campaign_suite` devuelven el mismo hash (medido) |
| Veredictos y colocaciones | **Ninguno cambió** |

## Deltas semánticos (diff JSON, 35 rutas)

Solo se añaden campos y cambia texto/hash derivado del YAML:

| Ruta (índices colapsados) | Tipo | Nº |
|---|---|---|
| `scenario_A_four_guest_combinations/combinations[]/fourth_guest_one_genset_down_holds` | añadido | 5 |
| `.../fourth_guest_ranked_displacements[]/conditioned_availability/one_genset_down` | añadido | 26 |
| `scenario_A_g6_admission/one_genset_down` | añadido | 1 |
| `scenario_A_g6_admission/conditioned_availability/one_genset_down` | añadido | 1 |
| `scenario_A_g6_admission/rationale` | cambiado (segunda frase fija tras el veredicto) | 1 |
| `scenario_A_g6_admission/mission_envelope_hash` | cambiado (el YAML recibió `represents`) | 1 |

Método: volcar `semantic_summary` en el commit base y en este commit y comparar por ruta.

# GOLDEN_DIFF — C4 (D2 `D2_LEXICOGRAPHIC_V2`: alternativas y rechazadas)

| Campo | Valor |
|---|---|
| Cambio que lo justifica | C4 (`feat/scope-chain`): `find_feasible_placement` devuelve `objective_id`, `sort_key_fields`, `chosen_key`, `evaluated_count`, `placement_alternatives` (≤5) y `rejected_placements` (≤5); D1 los propaga |
| `campaign_semantic_sha256` anterior (C6) | `feda2501801265be52e055381e6ffeb57d5fd5c108f4f9c4da8ce47306468316` |
| `campaign_semantic_sha256` nuevo | `eda98c784d116531ad3e31860a1fe2c5186236839cf6a07883dba2c79b361071` |
| Hashes B (survey / rescue / cooling) y C | **Sin cambio** respecto a C6 |
| Doble volcado | 2 corridas dan el mismo hash (medido) |

## Puerta F6-G (dueño no disponible) → plan B aplicado

El plan (§5.2) exige que el dueño decida si cambian las colocaciones oficiales. Como no está disponible se implementó el **plan B**:
`scarcity_cost` va en la posición 4 de la clave (tras `roof_cond_rank`, `pref_penalty` y `-retention_milli`), de modo que solo desempata.

Clave aplicada: `(roof_cond_rank, pref_penalty, -retention_milli, scarcity_cost, -min_margin_bucket, stab_eq, node_used_cores, nid)`.

**Resultado medido:** las 105 búsquedas de colocación de la campaña oficial (G1…G6, escenarios A y B) producen **exactamente las mismas posiciones y nodo** que en el commit base `c4909d5` (secuencia idéntica, sha256 `7e2bd1887bd8563daf0f4f784d44bb85c864ddfc2811d7f1238c41871f54cc3a`; fijado en `CH-C4-06`).
Ninguna colocación oficial cambió, por lo que no hay nada que el dueño deba aprobar; la clave con escasez en la posición 2 (V2 completa) **no** se implementó y queda como decisión pendiente del dueño.

## Deltas semánticos (diff JSON, 6 rutas, todas añadidas)

| Ruta | Tipo |
|---|---|
| `scenario_A_g6_admission/placement/{objective_id,sort_key_fields,chosen_key,evaluated_count}` | añadido |
| `scenario_A_g6_admission/placement_alternatives` | añadido |
| `scenario_A_g6_admission/rejected_placements` | añadido |

Veredictos sin cambio.

## Decisiones

- Alternativas deduplicadas por conjunto de posiciones; si solo hay un conjunto factible, se muestran las variantes de otro nodo.
- Las rechazadas excluyen conjuntos con algún nodo factible y se ordenan por (nº de testigos, `pref_penalty`, posiciones, nodo).
- Desempate exacto de clave: orden de evaluación (posiciones preferidas primero), igual que el escaneo original.
- `manifest.py::combinations` (first-fit para la masa de C(5,4)) queda fuera, como indica el plan.

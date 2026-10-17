# AI_USE_DISCLOSURE — Declaración de Uso de Inteligencia Artificial y Autorización de Datos

## 1. Identidad del Asistente de Desarrollo (Observada en el Host)

| Campo | Valor Observado | Fuente de Verificación |
|---|---|---|
| **Herramienta / Harness** | Antigravity CLI (`agy`) versión `1.4.0` | `agy --version` / metadatos del entorno en `/Users/armandosilva/.gemini/antigravity-cli` |
| **Proveedor del Harness** | Google DeepMind (Advanced Agentic Coding) | Identidad del sistema Antigravity |
| **Modelo Activo en esta Sesión (MacBook Pro)** | `Gemini 4 Argon (High)` (cambiado desde `Gemini 3.8 Flash` a las 2026-10-16 23:47 PDT) | Registro de configuración de sesión `<USER_SETTINGS_CHANGE>` |
| **Modelos Usados en el Paquete de Transferencia Previo (Mac mini)** | `Claude Opus 5.5 (High)` (extracción A, 2026-10-16 23:13–23:34 PDT) y `Gemini 4 Argon (High)` (sesiones previas V5/V5.1) | `handoff_sdv_20261016_232300/PROVENANCE.md` §1 |
| **Subagentes Invocados en esta Construcción** | Ninguno (`0` subagentes invocados; revisiones ejecutadas en pasadas secuenciales explícitamente declaradas sin independencia de modelo) | Registro de herramientas de la sesión |

---

## 2. Verificación contra la Restricción de Autorización de Datos (Brief p. 12 / Sección 0.2)

1. **Prohibición de Modelos de Compañías Basadas en China y Derivados (p. ej. Qwen, DeepSeek):**
   - **Cumplimiento en `sdv-resource-governor`:** **VERIFICADO — CERO USO.** No se ha descargado, importado, invocado ni incluido ningún modelo Qwen ni ningún otro modelo de lenguaje o visión en este repositorio ni en su entorno virtual (`.venv`).
   - **Auditoría de archivos en la placa prestada (`/home/arduino`):** Durante el inventario de solo lectura se observó que fuera de este proyecto existen directorios antiguos (`/home/arduino/sdv-change-review-pilot/models/jeff-sdv-q8_0.gguf` y `/home/arduino/aegis-q-uno/assets/models/*.onnx`). Ninguno de esos archivos ha sido leído, ejecutado ni enlazado por `sdv-resource-governor`.
   - **Verificación técnica (`Gate B0 / F1`):** `pip freeze` tanto en MacBook Pro (`requirements-macos-arm64.lock`) como en la placa Linux (`requirements-linux-aarch64.lock`) contiene exclusivamente `PyYAML`, `pytest` y sus 4 dependencias puras de test (`iniconfig`, `packaging`, `pluggy`, `Pygments`). No existe `onnx`, `onnxruntime`, `torch`, `transformers`, `llama-cpp-python` ni `opencv`.
2. **Lista Oficial Autorizada de Servicios IA del Evento:**
   - **Estado Local:** `UNKNOWN_PENDING_ORGANIZER_LIST`. El documento externo con la lista nominal de herramientas de programación autorizadas por los organizadores no se encuentra en el sistema de archivos local de esta MacBook Pro.
   - **Acción de Mitigación:** Se registra con total exactitud la identidad de la herramienta (`Antigravity CLI 1.4.0`) y del modelo (`Gemini 4 Argon (High)` de Google) para que el propietario la coteje directamente con la tabla oficial del evento antes de la entrega final.
3. **Ausencia de IA en Tiempo de Ejecución del Gobernador:**
   - El motor `sdv_governor` es **100% determinista, simbólico y basado en restricciones físicas y aritmética reproducible**.
   - Todas las explicaciones (`rationale`) se construyen a partir de los testigos numéricos reales del solver (recurso, demanda, capacidad disponible, estado del buque y unidad SI), nunca mediante un LLM ni plantillas desconectadas del cálculo.

---

## 3. Archivos Generados con Asistencia de IA y Revisión Humana

| Categoría de Archivos | Archivos Principales | Rol de la IA | Estado de Verificación |
|---|---|---|---|
| **Trazabilidad y Gobernanza** | `BACKGROUND_IP.md`, `AI_USE_DISCLOSURE.md`, `THIRD_PARTY_COMPONENTS.csv`, `DEVELOPMENT_LOG.md`, `requirements_matrix.csv`, `ASSUMPTIONS.yaml`, `research/OPEN_QUESTIONS.md` | Redacción estructurada a partir de mediciones del sistema y del plan maestro | Verificado contra hashes SHA-256 reales e inventario de hardware |
| **Esquemas y Datos Declarativos** | `schemas/*.schema.json`, `data/ship/*.yaml`, `data/guests/G1..G6.yaml`, `data/priorities/*.yaml`, `data/scenarios/*.yaml`, `data/overrides/brief_errata.yaml` | Codificación de tablas físicas y erratas del brief | Validado por tests unitarios y adversariales (`DAT-01`..`DAT-04`, `ONT-01`..`ONT-03`) |
| **Motor Determinista del Gobernador** | `src/sdv_governor/{models,manifest,budgets,placement,admission,arbitration,degradation,ship_state,scheduling,explanations,replay,evidence,deployment,server,cli}.py` | Implementación en Python 3.13 tipado sin dependencias pesadas | Validado por la matriz de aceptación `B9` completa y replay determinista triple |
| **Pruebas y Verificador Fail-Closed** | `tests/unit/*`, `tests/integration/*`, `tests/adversarial/*` | Generación de casos positivos, negativos, mutación y reproducción de fallos históricos | Ejecutado con JUnit XML, hashes de evidencia y rechazo comprobado de `verify_content_repro.json` |
| **Interfaz de Operador y Documentación de Entrega** | `ui/{index.html,app.js,styles.css}`, `README.md`, `docs/{MEMO_SCENARIO_C,PHYSICAL_ACCOUNT,PITCH_SLIDES,DEPLOYMENT_AND_BOM}.{md,pdf}` | Diseño de consola 2D conectada al motor real y generación de documentos técnicos | Comprobado en vivo en laptop y contra ejecución remota en placa |

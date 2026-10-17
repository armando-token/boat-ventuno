# BACKGROUND_IP — Declaración de Propiedad Intelectual Previa y Procedencia

## 1. Propósito y Cumplimiento Normativo (Términos Oficiales Art. 6, Art. 11 y Anexo C)

Este documento registra con transparencia total el **Background IP** (investigación previa, prototipo anterior y paquete de transferencia) y lo distingue del **Foreground IP** desarrollado durante el evento para el prototipo `sdv-resource-governor` (Resource Governor de 8 clases para Software-Defined Vessel).

Ningún código del prototipo previo (`aegis-q-uno`) se ha copiado silenciosamente en este repositorio. El motor `sdv_governor` ha sido escrito desde cero sobre la MacBook Pro a partir de las especificaciones del brief oficial de 12 páginas, las erratas del organizador y las lecciones de auditoría de la receta de transferencia.

---

## 2. Inventario de Fuentes y Antecedentes

| Identificador | Artefacto / Repositorio | Ubicación / Hash Observado | Fechas Reales (PDT / UTC) | Rol e Influencia en `sdv-resource-governor` |
|---|---|---|---|---|
| **BIP-01** | Investigación previa (Documentos A–F y planes de consolidación) | Citados en `PROVENANCE.md` §3; no presentes en esta MacBook Pro (`UNKNOWN` local) | Hasta 2026-10-15 (previo al corte del evento) | Contexto general sobre arquitectura dual Linux + MCU en placas Arduino UNO Q / Ventuno Q y buses MessagePack-RPC. No aporta código ni modelos al gobernador actual. |
| **BIP-02** | Prototipo `aegis-q-uno` (V1 a V5.1) | Origen en Mac mini `/Users/armandosilva/Projects/aegis-q-uno` y copia en placa `/home/arduino/aegis-q-uno` · HEAD `3812309f12bdfa395d7c53be102501048198eaf2` · `src/sdv` SHA-256 `af17e4576eb636eef1a1c6bcae1f484da13560a0eed94e89fc1fe67ed1affb9f` · Tar V5.1 `80ef505d73a5b6ee97057621b0ac75d2da6ac2611ca9f4cb54733902fcbb9bfb` | 2026-10-15 00:57 PDT (`7c87d9a`) a 2026-10-16 15:43 PDT | **Retirado del camino crítico.** Su motor `ResourceAdmissionEngine` usaba unidades abstractas (PATROL 55 / SAR 75/105) y contenía defectos reproducidos (`KF-01`..`KF-20`). Sólo influyó como experiencia negativa (casos adversariales `evidence/resource_engine_repro.json` y `evidence/verify_content_repro.json`) y patrón conceptual de dueño único e invariantes de procedencia/frescura. |
| **BIP-03** | Visor y contratos `bote_3d` (`sdv-demo/1.0`) | Origen en Mac mini `/Users/armandosilva/Documents/Barco_ardu/bote_3d` · HEAD `d077bf70906ea8845ea7703531b84589812300d5` · worktree SHA-256 `1c4215029571d2428b0357e839f52fc6d0653743e80a5a00c90f400c72c81622` | 2026-10-16 00:29–15:26 PDT | Inspiró la disciplina de telemetría con `stream_id`, `seq`, `source_time_ns` y etiquetas de procedencia por campo (`MEASURED`, `SIMULATED`, `INJECTED`, `REPLAYED`, `UNAVAILABLE`). La UI 2D de una pantalla de este repositorio es nueva y específica para los 8 presupuestos, bays/mástil/hangar y escenarios A/B. |
| **BIP-04** | Paquete de receta y auditoría `handoff_sdv_20261016_232300` | `/Users/armandosilva/DATA/Hack-Ardui/handoff_sdv_20261016_232300.zip` · SHA-256 `e817d56c8e0f1053907ff6bf11e61bd3a25f137927be1e191846cb2989e8ce63` | 2026-10-16 23:13–23:34 PDT (2026-10-17 06:13–06:34 UTC) | Receta documental de transferencia y reproducción de defectos (`KF-01` a `KF-21`). Sus vectores adversariales (`resource_engine_repro.json` y `verify_content_repro.json`) se incorporan como suite de regresión obligatoria en B3/B6/B8. |
| **BIP-05** | Plan Maestro de Transferencia y Reconstrucción | `/Users/armandosilva/Downloads/PLAN_TRANSFERENCIA_Y_RECONSTRUCCION_SDV_VENTUNO_Q.md` · SHA-256 `c4b14483bc7212b0314fb749a6f4df83d7ab76cc003594a63cc24f316d369092` | 2026-10-16 23:09 PDT | Especificación operativa de las fases B0–B11, síntesis de tablas del brief de 12 páginas y erratas del organizador (G4 hangar = 850 kg, 3 de 5 combinaciones de 4 superan 10.000 kg). |

---

## 3. Separación entre Background IP y Foreground IP

### 3.1 Qué NO se traslada (Retirado / Aplazado)
- `src/sdv/resource/engine.py` (motor abstracto PATROL/SAR con defectos `KF-05`).
- `harness/v5/verify.py` y `tools/v5_1/emit_v5_1_certificates.py` (verificador permisivo ante `{}` y certificados literales `KF-01`, `KF-04`).
- `tools/v5_1/run_r4_unoq_300s.py` y `run_r5_endurance_unoq.py` (métricas literales `KF-02`, `KF-03`).
- Stack V1–V4 de visión/cámara, assets ONNX (`assets/models/*.onnx`), `onnxruntime` y `opencv`.
- Firmware `safety_core.ino` de UNO Q (STM32U585): no se flashea ni se exige para el Resource Governor.

### 3.2 Qué constituye Foreground IP Nuevo (Desarrollado en esta sesión desde 2026-10-16 23:51 PDT / 2026-10-17 06:51 UTC)
1. **Esquemas y Contratos (`schemas/`, `src/sdv_governor/models.py`, `manifest.py`):** validación estricta de tipos, unidades SI, modos, dependencias (`part_of`, `requires`, `excludes`), estados de buque, tripulación y procedencia.
2. ** Tarjetas de Guests (`data/guests/G1..G6.yaml`) y Erratas (`data/overrides/brief_errata.yaml`):** modelado completo de G1–G5 según brief + errata G4 (140 + 850 = 990 kg) y tarjeta propia civil G6.
3. **Motor Físico de 8 Clases y Simulador a 1 Hz (`budgets.py`, `ship_state.py`, `replay.py`):** potencia por línea/estado, curva lineal de refrigeración `C(T) = 160 - 40*(T-12)/18`, ruta separada de aire de mástil vs loop de agua, inercia térmica con recarga de 10 min, posiciones discretas, masa y estabilidad por subcomponente/posición, datos/latencia/silencio EMCON, cómputo multidimensional por nodo (4 nodos × 32 cores / 128 GB / 1 acelerador indivisible) y presupuestos de tiempo.
4. **D1 Placement y Admisión sobre Mission Envelope (`placement.py`, `admission.py`):** evaluación multi-estado (muelle vs crucero a 30 °C, degradado, viento/oleaje, 14 días), veredictos `YES`, `YES_IF`, `NO`, `NOT_NOW` y oraciones de explicación derivadas de los testigos reales de restricción.
5. **D3 Arbitraje en ≤2 s con Prioridades Intercambiables (`arbitration.py`, `data/priorities/`):** resolución del escenario B (minuto 8 batería G4 18%, viento 22 kn, protocolo `HOLD` acústico G3 con latencia real y máquina de estados de maniobra del buque) bajo `survey_primary.yaml` y `rescue_primary.yaml`.
6. **D4 Degradación y Shedding por Modos (`degradation.py`):** respuesta al minuto 12 (200 kW → 100 kW), protección de cargas no interrumpibles y `safety_critical`, separación entre escenario oficial (81 kW en survey vs reserva de carga futura de 25 kW = 105 kW) y variantes de déficit activo (105 kW con G3 en carga; 72 kW por fallo de loop a 30 °C).
7. **Reglas de Tripulación 14/6/0, Ventanas A′ y Fallos (`scheduling.py`):** confirmaciones humanas fuera del bucle urgente de 2 s, prohibición de instalación física en Crew 0, detección de sobreconsumo 10–20% y pérdida de feeds con transición a `STALE`/`UNAVAILABLE`.
8. **Verificador Fail-Closed (`evidence.py`) y Despliegue Paritario MacBook Pro + Placa Linux (`deployment.py`, `ui/`):** verificación criptográfica y semántica que rechaza todos los vectores de `verify_content_repro.json`, e interfaz web local de una sola pantalla conectada en vivo al motor.

# BACKGROUND_IP — Declaration of Background IP, Provenance & Foreground Separation

## 1. Purpose & Regulatory Compliance (Official Event Terms Art. 6, Art. 11 & Annex C)

This document transparently declares all **Background IP** (prior research, legacy prototypes, 3D vessel visualization assets, and the handoff audit package) and strictly separates it from the **Foreground IP** developed during the event for `sdv-resource-governor` (the 8-Class Physical Resource Governor for a Software-Defined Vessel).

Zero Python governor code from the prior prototype (`aegis-q-uno`) was copied into `src/sdv_governor/`. The deterministic 8-class engine was written from scratch on the MacBook Pro from the 12-page official challenge brief, the organizer errata, and the 2026-10-17 organizer clarifications (`GRAZIANO_B_COMPOSITION_0855` and `GRAZIANO_ROOF_AND_G4_MASS_0904`).

---

## 2. Inventory of Background Sources & Prior Artifacts

| Identifier | Artifact / Repository | Observed Location & SHA-256 / Commit | Real Dates (PDT / UTC) | Role & Influence on `sdv-resource-governor` |
|---|---|---|---|---|
| **BIP-01** | Pre-event architecture research notes (Docs A–F & consolidation plans) | Cited in `PROVENANCE.md`; not present on this MacBook Pro (`UNKNOWN` local path) | Up to 2026-10-15 (pre-event cutoff) | General background context on dual Linux + MCU architecture on Arduino UNO Q / Ventuno Q boards. Contributes zero code or models to `sdv-resource-governor`. |
| **BIP-02** | Legacy prototype `aegis-q-uno` (V1 to V5.1) | Mac mini `/Users/armandosilva/Projects/aegis-q-uno` & board `/home/arduino/aegis-q-uno` · HEAD `3812309f12bdfa395d7c53be102501048198eaf2` · `src/sdv` SHA-256 `af17e4576eb636eef1a1c6bcae1f484da13560a0eed94e89fc1fe67ed1affb9f` | 2026-10-15 00:57 PDT to 2026-10-16 15:43 PDT | **Retired from critical path.** Its `ResourceAdmissionEngine` used abstract PATROL/SAR units and contained audited flaws (`KF-01`..`KF-20`). Used only as negative regression test vectors (`evidence/resource_engine_repro.json` and `evidence/verify_content_repro.json`). |
| **BIP-03** | 3D Vessel Viewer & telemetry concepts (`bote_3d` / `viz3d`) | Mac mini `/Users/armandosilva/Documents/Barco_ardu/bote_3d` (`HEAD d077bf70906ea8845ea7703531b84589812300d5`) & adapted in `viz3d/` | 2026-10-16 00:29–15:26 PDT (re-bound in L7 on 2026-10-17) | Prior Three.js/Blender 3D vessel visualization base. In Stage L7 (`viz3d/`), re-bound strictly to the authoritative `sdv-resource-governor` `/api/state` session (`G1+G3+G4+G5`, `hangar_roof` antenna, no silent synthetic fallback in live mode, explicit `STALE / DESCONECTADO` banner on disconnect). |
| **BIP-04** | Handoff & Audit Recipe `handoff_sdv_20261016_232300` | `/Users/armandosilva/DATA/Hack-Ardui/handoff_sdv_20261016_232300.zip` · SHA-256 `e817d56c8e0f1053907ff6bf11e61bd3a25f137927be1e191846cb2989e8ce63` | 2026-10-16 23:13–23:34 PDT (2026-10-17 06:13–06:34 UTC) | Read-only transfer recipe and failure reproduction suite (`KF-01`..`KF-21`). Its adversarial vectors (`resource_engine_repro.json`, `verify_content_repro.json`) are enforced as regression gates in `tests/adversarial/`. |
| **BIP-05** | Master Transfer & Reconstruction Plan | `/Users/armandosilva/Downloads/PLAN_TRANSFERENCIA_Y_RECONSTRUCCION_SDV_VENTUNO_Q.md` · SHA-256 `c4b14483bc7212b0314fb749a6f4df83d7ab76cc003594a63cc24f316d369092` | 2026-10-16 23:09 PDT | Operational specification for phases B0–B11, synthesis of the 12-page challenge brief tables, and organizer errata (`G4` hangar = `850 kg`, `3 of 5` four-guest sets exceed `10,000 kg`). |

---

## 3. Strict Separation Between Background IP and Foreground IP

### 3.1 What Is NOT Used or Carried Over (Retired)
- `src/sdv/resource/engine.py` (abstract PATROL/SAR engine with `KF-05` defects).
- `harness/v5/verify.py` and `tools/v5_1/emit_v5_1_certificates.py` (permissive verifier accepting `{}` and literal certificates `KF-01`, `KF-04`).
- V1–V4 camera/vision stack, synthetic/YOLOX ONNX assets (`assets/models/*.onnx`), `onnxruntime`, `opencv`, and external board pilot models (`jeff-sdv-q8_0.gguf`).
- UNO Q `safety_core.ino` MCU sketch (not flashed or required for the 8-class Resource Governor).

### 3.2 What Constitutes New Foreground IP (Developed 2026-10-16 23:51 PDT – 2026-10-17 PDT)
1. **Formal JSON Schemas & Contracts (`schemas/`, `src/sdv_governor/models.py`, `manifest.py`):** Strict type validation, SI units, modes, dependencies (`part_of`, `requires_guests`, `excludes_guests`, `conditional_excludes`), and field-level provenance (`OFFICIAL_BRIEF`, `ORGANIZER_ERRATA`, `ASSUMED`).
2. **Declarative Guest Catalog (`data/guests/G1..G6.yaml`) & Organizer Clarifications (`data/overrides/brief_errata.yaml`, `organizer_clarifications.yaml`):** Full modeling of `G1`–`G5` (`G4 = 140 + 850 = 990 kg`, `G3+G4+G5 = 9,515 kg ≈ 9.52 t`, `G1+G3+G4+G5 = 9,560 kg` / `9,853.0 kg-eq`) plus custom civil guest `G6` (`420 kg`) and dynamic `G7`.
3. **8-Class Physical Budget Engine & 1 Hz Simulator (`budgets.py`, `ship_state.py`, `replay.py`):** Per-feeder power (`LINE_A_FWD <= 150 kW`, `LINE_B_AFT <= 150 kW`, `200 kW` net reserve), linear chilled-water curve `C(T) = 160 - 40*(T-12)/18 kW`, separate air cooling domains (`mast_3 <= 2,000 W`, `hangar_roof <= 2,000 W`), thermal inertia (`30 kW x 60 s`, `600 s` recharge), 4-node compute cluster (`32c / 128 GiB / 1 acc` per node, `80%` allocatable = `25c / 102.4 GiB`, `3` discrete accelerators), and 1 Hz causal stepping.
4. **D1/D2 Admission, Placement & Conditional Roof Clearance (`placement.py`, `admission.py`):** Multi-state evaluation across port, cruise, degraded, and 14-day Mission Envelope A.10, enforcing `ROOF_OUTSIDE_G4_RECOVERY_V1` (`YES`, `YES_IF`, `NOT_NOW`, `NO`) and atomic `apply_yes_if`.
5. **D3 Causal Arbitration (`arbitration.py`) & D4 Graceful Degradation (`degradation.py`):** Scenario B with `G1+G3+G4+G5` installed (`57.5 / 81.5 kW` -> `82.5 / 106.5 kW` -> `45.5 / 69.5 kW` power; `54.8 / 78.8 kW` -> `79.8 / 103.8 kW` -> `43.5 / 67.5 kW <= 72 kW` water), causal `G3` `ACOUSTIC_HOLD_CONFIRMED` ACK -> `90 s` turn -> `240 s` landing (`855 s` rescue / `1,095 s` survey), and A.6 `60 s` cooldown + `15 kW` reconnection hysteresis.
6. **D5 Crew Regimes (`14/6/0`, `scheduling.py`), D6 Physical Account & 28-Mutation Verifier (`evidence.py`, `pdf_export.py`), and Live 2D/3D Operator Interface (`server.py`, `ui/`, `viz3d/`):** Complete end-to-end governance verified across MacBook Pro (`arm64`) and live Arduino Ventuno Q (`training06`, `aarch64`).

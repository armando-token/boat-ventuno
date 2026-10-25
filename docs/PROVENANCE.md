# PROVENANCE — End-to-End Project Lineage, Background IP, Organizer Clarifications & Board Audit

> This document records the complete, unedited provenance of `sdv-resource-governor`, its relationship to prior prototypes and handoff packages, the organizer clarifications received on 2026-10-17, the AI tools/models/subagents used across all stages, and the read-only audit of pre-existing files on the loaned Arduino Ventuno Q (`training06` / `ardu`).

---

## 1. AI Tooling, Model Identity & Eligibility Status (§13)

| Field | Observed Value | Verification Source |
|---|---|---|
| **AI_ELIGIBILITY** | `NOT_VERIFIED_EXTERNAL_LIST_MISSING` | External organizer nominal list of authorized AI coding services is not present in the local filesystem; recorded fail-closed for owner verification prior to final submission. |
| **Harness / IDE CLI** | Antigravity CLI (`agy`) version `1.4.0` (Google DeepMind Advanced Agentic Coding) | `agy --version` / `~/.gemini/antigravity-cli` |
| **Models Used Across Stages** | 1. **`Claude Opus 5.5 (High)`** (Anthropic via Antigravity): Mac mini read-only extraction `handoff_sdv_20261016_232300` (2026-10-16 23:13–23:34 PDT) and current Graziano Alignment Stage L0–L10 (2026-10-17 PDT).<br>2. **`Gemini 4 Argon (High)`** (Google DeepMind via Antigravity): Initial `sdv-resource-governor` clean-room build B0–B11, C1–C9, R1–R8, and S1–S6 on MacBook Pro (switched from `Gemini 3.8 Flash` at 2026-10-16 23:47 PDT prior to B0 code generation). | Session transcripts & `handoff_sdv_20261016_232300/PROVENANCE.md` §1 |
| **Subagents Invoked Across History** | - **Mac mini Extraction (`2026-10-16`)**: `3` read-only `research` subagents (`Model: inherit`).<br>- **Initial Build B0–B11 (`2026-10-17`)**: `0` subagents.<br>- **Corrective / Closure Audits C1–C9, R1–R8, S1–S6 (`2026-10-17`)**: Multi-agent research and implementation passes (`Model: inherit`).<br>- **Graziano Alignment Stage L0–L10 (`2026-10-17`)**: **`10` `research` subagents + `5` implementation (`self`) subagents** (`Model: inherit`), all orchestrated under the same parent session. | Session transcript `.system_generated/logs/transcript.jsonl` |
| **Runtime AI / ML in `sdv-resource-governor`** | **ZERO (`0`)**. The governor is 100% deterministic, symbolic, and rule/math-based (`budgets.py`, `placement.py`, `admission.py`, `arbitration.py`, `degradation.py`, `scheduling.py`, `replay.py`). | `requirements-macos-arm64.lock`, `requirements-linux-aarch64.lock` |

---

## 2. Chronological Lineage Across Machines & Stages (PDT / UTC)

| Date / Time (PDT) | Stage / Artifact | Location & SHA-256 / Commit | Role & Relationship to `sdv-resource-governor` |
|---|---|---|---|
| `2026-10-15 00:26` – `2026-10-16 15:43 PDT` | Legacy prototype `aegis-q-uno` (V1–V5.1) & `bote_3d` on Mac mini | Mac mini `~/Projects/aegis-q-uno` (`HEAD 3812309f...`) & `~/Documents/Barco_ardu/bote_3d` (`HEAD d077bf70...`) | **Retired Background IP (`BIP-02`, `BIP-03`).** `aegis-q-uno` used abstract PATROL/SAR units and had 21 audited defects (`KF-01..KF-21`). Zero Python governor code was copied; only negative regression vectors (`resource_engine_repro.json`, `verify_content_repro.json`) and 3D vessel visual concepts were retained. |
| `2026-10-16 23:13–23:34 PDT` (`06:13–06:34 UTC` Oct 17) | Handoff & Audit Package `handoff_sdv_20261016_232300.zip` | `~/DATA/Hack-Ardui/handoff_sdv_20261016_232300.zip` (`SHA-256: e817d56c8e0f1053907ff6bf11e61bd3a25f137927be1e191846cb2989e8ce63`) | Documented legacy failures (`KNOWN_FAILURES.md`) and extracted verbatim 12-page challenge brief tables (`SPEC_DELTA.md`). |
| `2026-10-16 23:51 PDT` – `2026-10-17 00:46 PDT` | Clean-Room Build `B0–B11` on MacBook Pro (`MacBookPro18,1`) | `~/DATA/Hack-Ardui/sdv-resource-governor` | Clean-room implementation of the 8-class SDV Resource Governor (`src/sdv_governor/`), 7 JSON schemas, `G1..G6` YAML manifests, and single-screen 2D Operator Console (`ui/`). |
| `2026-10-17 09:32 PDT` – `14:39 PDT` | Hardware-in-the-Loop Ventuno Q Parity & Closure `C1–C9`, `R1–R8`, `S1–S6` | Commit `b35b157` (`artifacts/release_c9`, `release_r8`, `release_s6`, `release_s6_patch1` preserved intact) | Live deployment to loaned Arduino Ventuno Q (`training06`, Qualcomm QCS8300 Monza `aarch64`, Ubuntu 24.04.4 LTS) over LAN (`ssh ardu`), 28-mutation fail-closed verifier, and causal D3/D4 state machine. |
| `2026-10-17 15:30+ PDT` | Graziano Alignment Stage `L0–L10` (`alignment/graziano-b-four-guests`) | `~/DATA/Hack-Ardui/sdv-resource-governor` (`artifacts/release_alignment_graziano_v1`) | Aligns Scenario B to `G1+G3+G4+G5` (`9,560 kg`, `9,853 kg-eq`, `106.5 -> 69.5 kW` power, `103.8 -> 67.5 kW` water), `G4=990 kg` (`G3+G4+G5=9,515 kg ≈ 9.52 t`), conditional roof clearance `ROOF_OUTSIDE_G4_RECOVERY_V1`, separate `mast_3` / `hangar_roof` air domains, feeder routing (`LINE_A_FWD`, `LINE_B_AFT <= 150 kW`), A.6 transition timings, and live-bound 3D Vessel Twin (`viz3d/`). |

---

## 3. Organizer Clarifications Provenance (`2026-10-17`)

Recorded in `docs/sources/organizer_clarifications_2026-10-17.md` and `data/overrides/organizer_clarifications.yaml`:
1. **`GRAZIANO_B_COMPOSITION_0855` (8:55 AM)**: Confirmed `G1 + G3 + G4 + G5` as the official Scenario B 4-guest configuration (`9.56 t`, `G3` in Bay 1, `G5` container in Bay 2, `G1` on `mast_3`, `G5` antenna on `hangar_roof`), while `G2 + G3 + G4 + G5` fails on both bays and mass (`13.015 t vs 10 t`), and confirmed that the Annexes take precedence over the demo narrative.
2. **`GRAZIANO_ROOF_AND_G4_MASS_0904` (9:04 AM)**: Confirmed that hangar roof drone clearance (`Check drone clearance`) is an explicit, parameterized assumption enforced by the engine and recorded in the Physical Account (`ROOF_OUTSIDE_G4_RECOVERY_V1`), and clarified that with `G4 = 0.99 t` (`990 kg` = `140 kg` vehicle + `850 kg` hangar), `G3 + G4 + G5` weighs `9.515 t ≈ 9.52 t` (`5.5 + 0.99 + 3.025 t`).
3. **Authentication Boundary**: Both clarifications are recorded with `source_kind: ORGANIZER_MESSAGE_TRANSCRIBED_BY_OWNER` and `independent_authentication: NOT_PERFORMED` (no fabricated URL or cryptographic signature is claimed).

---

## 4. Truthful Audit of Historical / Pre-Installed Models on Loaned Board (`ardu`) & Legacy Folders

To guarantee full compliance with the Challenge Data Use Authorization (Brief p. 12, prohibiting Chinese-origin models such as Qwen and their derivatives):
1. **Zero Usage in `sdv-resource-governor`**: Neither the MacBook Pro `.venv` (`requirements-macos-arm64.lock`) nor the board `.venv` (`/home/arduino/sdv-resource-governor/.venv`, `requirements-linux-aarch64.lock`) installs, imports, links, or executes any LLM, VLM, ONNX, GGUF, or Qwen model (`0` occurrences in runtime code, data, or tests).
2. **Pre-Existing Files Observed Outside This Repo on the Loaned Board (`training06` / `ardu`)**:
   - **Factory / Pre-Installed Board System Images & Containers**: Qualcomm/Foundries factory Docker/system caches on `training06` (`/var/lib/docker` / system image layers) and pre-existing user directories outside our repository contain historical model artifacts from prior board users or earlier hackathon experiments, specifically `/home/arduino/sdv-change-review-pilot/models/jeff-sdv-q8_0.gguf` (GGUF weights) and `/home/arduino/aegis-q-uno/assets/models/*.onnx` (synthetic/YOLOX ONNX files from retired V1–V4).
   - **Historical Mentions in Pre-Event Planning Docs**: As recorded in `handoff_sdv_20261016_232300/PROVENANCE.md` §5, pre-event research notes mentioned Qwen, Gemma, and YOLO in comparative tables, and a legacy pilot directory (`sdv-change-review-pilot`) existed on the board prior to `sdv-resource-governor`.
   - **Strict Isolation**: `sdv-resource-governor` operates strictly inside `~/DATA/Hack-Ardui/sdv-resource-governor` (Mac) and `/home/arduino/sdv-resource-governor` (board) and never reads, mounts, or invokes any external `.gguf` or `.onnx` files.

---

## 5. Public Metocean Data & Synthetic Telemetry Provenance

| Source / Component | License & Terms | Endpoint / Station | Purpose & Isolation | Code Dependencies |
|---|---|---|---|---|
| **Open-Meteo Marine Weather API** | CC BY 4.0 (Attribution: *"Weather data by Open-Meteo.com"*); Terms: free non-commercial tier (`https://open-meteo.com/en/terms`) | `https://marine-api.open-meteo.com/v1/marine` and `https://api.open-meteo.com/v1/forecast` (`lat=37.788, lon=-122.634`, Golden Gate Bar `GG_BAR`) | Offline captured snapshot of hourly SST, significant wave height (Hs), wave direction, and 10 m wind. Model resolution is ~0.08 deg (~8 km); provider disclaimer explicitly acknowledged: *"computed at 0.08 deg resolution ... Accuracy at coastal areas is limited. This is not suitable for coastal navigation"*. Positioned at `GG_BAR` seaward of the bridge and validated against physical buoy 46237 with empirical guard bands. ZERO runtime network calls. | **ZERO new code dependencies** (offline fetch uses standard library `urllib.request`). |
| **NOAA NDBC Buoy Network** | Public Domain (U.S. Government Work) | Moored oceanographic buoy stations `46237` (San Francisco Bar), `46026` (18NM West of SF), `FTPC1` (Fort Point) via `https://www.ndbc.noaa.gov/data/realtime2/` | Physical ground-truth oceanographic observations used offline for empirical backtesting and guard-band derivation (`ASM-ENV-GUARD-DEFAULT`). ZERO runtime network calls. | **ZERO new code dependencies** (standard library only). |
| **Seeded Synthetic Telemetry** | Internal Project Asset (`data/forecast/`) | Deterministic PRNG (`random.Random(seed)`) anchored to nominal guest YAML manifests | Synthesizes reproducible 1 Hz guest electrical power traces for evaluating advisory Holt-Winters forecaster and conformal intervals. Labeled `SIMULATED`; purely advisory; zero influence on governor decisions. | **ZERO new code dependencies** (Python standard library `random`, `math`, `typing`). |

### Public Data Capture & Verification Policy
1. **Network Boundary**: Public data acquisition is restricted entirely to `tools/fetch_public_env.py` (executed once offline on developer workstation). Neither `tools/` nor any networking code is deployed to the target board (`training06` / `ardu`).
2. **Schema & Integrity**: All recorded metocean series are validated against `schemas/environment_series.schema.json` and cryptographically tracked via SHA-256 hashes in `manifest.json`.
3. **Fail-Closed Offline Runtime**: If environment data is missing, expired, or invalid, the governor falls back safely to conservative design baselines (30 deg C seawater, `ASM-ENV-TTL`).


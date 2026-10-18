# AI_USE_DISCLOSURE — AI Assistance, Subagent Inventory & Data Use Authorization (§13)

## 1. AI Development Tools, Models, Sessions & Subagents Inventory

| Field | Observed Value | Verification Source |
|---|---|---|
| **AI_ELIGIBILITY** | `NOT_VERIFIED_EXTERNAL_LIST_MISSING` | The external organizer document listing nominally authorized AI coding services is not present on the local filesystem. Recorded fail-closed so the project owner can verify against the official event list before final submission. |
| **Harness / CLI** | Antigravity CLI (`agy`) version `1.4.0` | `agy --version` / `/Users/armandosilva/.gemini/antigravity-cli` |
| **Harness Provider** | Google DeepMind (Advanced Agentic Coding) | Antigravity system identity |
| **Models Used Across All Stages** | 1. **`Claude Opus 5.5 (High)`** (Anthropic via Antigravity):<br>   - Mac mini read-only handoff extraction (`handoff_sdv_20261016_232300`, 2026-10-16 23:13–23:34 PDT).<br>   - MacBook Pro Graziano Alignment Stage `L0–L10` (2026-10-17 PDT).<br>2. **`Gemini 4 Argon (High)`** (Google DeepMind via Antigravity):<br>   - MacBook Pro clean-room build `B0–B11`, corrective closure `C1–C9`, `R1–R8`, and `S1–S6` (switched from `Gemini 3.8 Flash` at 2026-10-16 23:47 PDT before B0 code generation; also used in prior Mac mini V5/V5.1 sessions). | `<USER_SETTINGS_CHANGE>` logs, `.system_generated/logs/transcript.jsonl`, and `PROVENANCE.md` §1 |
| **Subagents Invoked Across Stages** | - **Mac mini Extraction (`2026-10-16`)**: `3` read-only `research` subagents (`Model: inherit`).<br>- **Initial Build `B0–B11` (`2026-10-17`)**: `0` subagents.<br>- **Closure Stages `C1–C9`, `R1–R8`, `S1–S6` (`2026-10-17`)**: Parallel `research` and `self` implementation subagents (`Model: inherit`).<br>- **Graziano Alignment Stage `L0–L10` (`2026-10-17`)**: **`10` `research` subagents + `5` implementation (`self`) subagents** (`Model: inherit` = `Claude Opus 5.5 (High)`), invoked for parallel audit and module-isolated implementation (Agents A, B, C, D, E). | `.system_generated/logs/transcript.jsonl` |

---

## 2. Verification Against Challenge Data Use Authorization (Brief p. 12)

1. **Prohibition of Chinese-Origin Models & Derivatives (e.g., Qwen, DeepSeek):**
   - **Compliance in `sdv-resource-governor`:** **VERIFIED — ZERO USAGE.** Zero Qwen, DeepSeek, or any other LLM/VLM/ML models are downloaded, imported, invoked, or bundled in `sdv-resource-governor` or its virtual environments (`.venv`).
   - **Truthful Read-Only Audit of Loaned Board (`ardu` / `training06`) & Legacy Handoff Files:**
     - During read-only hardware inventory of the loaned Arduino Ventuno Q (`training06`, `10.0.0.130`) and the earlier board (`10.0.0.154`), pre-existing directories outside this repository were observed: factory OS/container image layers, `/home/arduino/sdv-change-review-pilot/models/jeff-sdv-q8_0.gguf` (a GGUF model from an unrelated earlier pilot), and `/home/arduino/aegis-q-uno/assets/models/*.onnx` (retired V1–V4 synthetic/YOLOX ONNX files). In addition, pre-event planning documents cited in `handoff_sdv_20261016_232300/PROVENANCE.md` §5 mentioned Qwen, Gemma, and YOLO in architectural comparison tables.
     - **Strict Non-Use:** None of those factory images, `.gguf` files, `.onnx` files, or legacy folders are read, imported, executed, or linked by `sdv-resource-governor`.
   - **Technical Verification (`requirements-*.lock`):** Both `requirements-macos-arm64.lock` and `requirements-linux-aarch64.lock` contain exclusively `PyYAML`, `pytest`, `jsonschema`, and their pure-Python schema/test transitive packages (`attrs`, `referencing`, `rpds-py`/`pyrsistent`, `iniconfig`, `packaging`, `pluggy`, `Pygments`). Zero `onnx`, `onnxruntime`, `torch`, `transformers`, `llama-cpp-python`, or `opencv` packages exist in `.venv`.
2. **Official Organizer List of Authorized AI Services (`AI_ELIGIBILITY = NOT_VERIFIED_EXTERNAL_LIST_MISSING`):**
   - Because the external organizer document enumerating approved AI coding assistants is not stored locally on this laptop, `AI_ELIGIBILITY` is explicitly set to **`NOT_VERIFIED_EXTERNAL_LIST_MISSING`** rather than claiming unverified compliance. All tool, provider, model, and subagent identities are disclosed in §1 for direct owner verification against the official event list.
3. **Zero Runtime AI in the Resource Governor:**
   - `sdv_governor` is **100% deterministic, symbolic, and governed by exact physical constraint arithmetic**. Every admission, placement, arbitration, degradation, and crew-regime rationale string is synthesized directly from binding numerical constraint witnesses (`explanations.py`).

---

## 3. Artifacts Generated with AI Assistance & Human Engineering Review

| Artifact Category | Files | Role of AI Assistant & Subagents | Verification Method |
|---|---|---|---|
| **Provenance, Governance & Disclosures** | `PROVENANCE.md`, `BACKGROUND_IP.md`, `AI_USE_DISCLOSURE.md`, `THIRD_PARTY_COMPONENTS.csv`, `DEVELOPMENT_LOG.md`, `requirements_matrix.csv`, `ASSUMPTIONS.yaml`, `research/OPEN_QUESTIONS.md`, `docs/sources/organizer_clarifications_2026-10-17.md` | Structured drafting from measured system hashes, git logs, and owner-supplied organizer clarifications | Verified against SHA-256 digests, `pip freeze`, and hardware inventory |
| **Schemas & Declarative Data** | `schemas/*.schema.json`, `data/ship/*.yaml`, `data/guests/G1..G6.yaml`, `data/priorities/*.yaml`, `data/scenarios/*.yaml`, `data/overrides/*.yaml` | Encoding Annex A/B physical tables, Graziano clarifications (`G1+G3+G4+G5`, `G4=990 kg`), and roof/air/feeder rules | Validated by Draft 2020-12 JSON Schema & unit/integration suites |
| **Deterministic Governor Core** | `src/sdv_governor/{models,manifest,budgets,placement,admission,arbitration,degradation,ship_state,scheduling,explanations,replay,evidence,deployment,server,cli,pdf_export}.py` | Clean-room Python implementation and L1–L8 alignment updates | Verified by full `pytest` suite, triple deterministic replay, and 28-mutation verifier |
| **Operator Interface (2D & 3D) & Deliverable Docs** | `ui/{index.html,app.js,styles.css}`, `viz3d/`, `README.md`, `docs/{PHYSICAL_ACCOUNT,PHYSICAL_ACCOUNT_D6,MEMO_SCENARIO_C,SCENARIO_C_MEMO,PITCH_SLIDES,SLIDES_10,DEMO_SCRIPT_90S,DEPLOYMENT_AND_BOM}.{md,pdf}` | Live-bound 2D console & Three.js 3D twin integration, technical deliverables, and pure-Python PDF rendering | Verified by HTTP API integration tests and `generate_all_deliverable_pdfs` page-limit checks |

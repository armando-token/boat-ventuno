# Deployment Guide & Bill of Materials (`DEPLOYMENT_AND_BOM.md`)

## 1. Observed Hardware & Environment Inventory (`hardware_inventory.json`)

| Component | Observed Identity & Specifications | Connection / Role |
|---|---|---|
| **Primary Development & Demo Host** | Apple MacBook Pro (`MacBookPro18,1`, Apple M1 Pro `arm64`, 10 cores, 16 GiB RAM, macOS `27.0.1`, Python `3.13.15`) | Runs full test suite, 1 Hz simulator, fail-closed evidence verifier, PDF generator, single-screen 2D Operator UI (`http://127.0.0.1:8765`), and synchronized Three.js 3D Vessel Twin (`viz3d/`). |
| **Remote Edge Linux SBC (`ssh ardu` / `ventuno`)** | **Arduino Ventuno Q** (`training06`, `arduino,monza` / Qualcomm QCS8300 Monza 8-core `aarch64`: 4x Cortex-A78C + 4x Cortex-A55, 14 GiB RAM, 55 GB eMMC, Ubuntu 24.04.4 LTS `noble`, Kernel `6.8.0-1080-qcom`, Python `3.12.3`) | Connected via Wi-Fi LAN (`10.0.0.130`). Hosts isolated `/home/arduino/sdv-resource-governor/.venv` (`--system-site-packages`) for edge deterministic replay (`G1+G3+G4+G5` Scenario B, `9,560 kg`, `9,853 kg-eq`) and verified semantic parity (`PASS`). |
| **USB Serial Interface** | `/dev/cu.usbmodem1C49DF7200002` (`MEGAHUNT VCPHID`, VID `0x0483`, PID `0x5750`) | Enumerated over USB-C. Note: Ventuno Q ADB USB enumeration requires external `7-24 V` DC barrel/header power; when unpowered, `sdv-governor doctor` reports `adb_usb: BLOCKED_CONNECTION` without affecting standalone or LAN operation. |
| **STM32H5 Real-Time MCU** | Onboard MCU co-processor (`arduino:zephyr:ventunoq`) | Out of scope for the admission/arbitration challenge; no UNO Q motor sketch is flashed. |

---

## 2. Software Bill of Materials (SBOM — Synchronized with `THIRD_PARTY_COMPONENTS.csv`)

| Package / Tool | MacBook Pro (`requirements-macos-arm64.lock`) | Arduino Ventuno Q (`requirements-linux-aarch64.lock`) | License | Purpose |
|---|---|---|---|---|
| `Python` | `3.13.15` | `3.12.3` | PSF-2.0 | Standard library (`fractions`, `dataclasses`, `hashlib`, `http.server`, `xml.etree.ElementTree`) |
| `PyYAML` | `6.0.3` | `6.0.3` | MIT | Declarative YAML loading for ship baseline, guest manifests `G1`-`G6`, priority profiles, scenarios, and errata/clarification overrides |
| `pytest` | `9.1.1` | `9.1.1` | MIT | Automated test execution and JUnit XML generation (`junit.xml`) |
| `jsonschema` | `4.26.0` | `4.10.3` | MIT | Formal JSON Schema Draft 2020-12 validation of all 7 schemas (`schemas/*.schema.json`) |
| `attrs` / `referencing` / `rpds-py` / `pyrsistent` | `26.1.0` / `0.37.0` / `0.30.0` | `23.2.0` / `0.20.0` | MIT | Transitive dependencies of `jsonschema` |
| `three.js` | `0.160.0` (`viz3d`) | N/A (browser UI) | MIT | WebGL 3D rendering library for the synchronized 3D Vessel Twin (`viz3d/`) bound to `/api/state` |
| `vite` | `5.x` (dev-only) | N/A | MIT | Offline frontend bundler used only during development to build `viz3d/dist` |
| `Blender` | `4.x` (offline tool) | N/A | GPL-3.0 | External offline 3D asset authoring tool for static GLB meshes; not linked or invoked at runtime |

*(Zero ML/LLM/vision dependencies (`onnx`, `torch`, `opencv`, `transformers`, `gguf`) are installed or imported in `.venv`.)*

---

## 3. Clean / Offline Setup & Remote Board Commands

```bash
# Clean environment setup with dev + schema validation dependencies
python3 -m venv .venv --system-site-packages
.venv/bin/pip install -r requirements-macos-arm64.lock
.venv/bin/pip install --no-build-isolation --no-deps -e .

# Run local hardware & transport doctor
PYTHONPATH=src .venv/bin/python -m sdv_governor.cli doctor

# Sync codebase to remote Linux board (when reachable on ssh ardu) and verify semantic hash parity
PYTHONPATH=src .venv/bin/python -m sdv_governor.cli deploy-board --ssh-target ardu
```

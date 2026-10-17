# Deployment Guide & Bill of Materials (`DEPLOYMENT_AND_BOM.md`)

## 1. Observed Hardware & Environment Inventory (`hardware_inventory.json`)

| Component | Observed Identity & Specifications | Connection / Role |
|---|---|---|
| **Primary Development & Demo Host** | Apple MacBook Pro (`MacBookPro18,1`, Apple M1 Pro `arm64`, 10 cores, 16 GiB RAM, macOS `27.0.1`, Python `3.13.15`) | Runs full test suite, 1 Hz simulator, fail-closed evidence verifier, PDF generator, and single-screen 2D Operator UI (`http://127.0.0.1:8765`). |
| **Remote Edge Linux SBC (`ssh ardu` / `ventuno`)** | **Arduino Ventuno Q** (`training06`, `arduino,monza` / Qualcomm QCS8300 Monza 8-core `aarch64`: 4× Cortex-A78C + 4× Cortex-A55, 14 GiB RAM, 55 GB eMMC, Ubuntu 24.04.4 LTS `noble`, Kernel `6.8.0-1080-qcom`, Python `3.12.3`) | Connected via Wi-Fi LAN (`10.0.0.130`). Hosts isolated `/home/arduino/sdv-resource-governor/.venv` for edge deterministic replay and verified semantic parity (`PASS`, `2a41dffa...`). |
| **USB Serial Interface** | `/dev/cu.usbmodem1C49DF7200002` (`MEGAHUNT VCPHID`, VID `0x0483`, PID `0x5750`) | Enumerated over USB-C. Note: Ventuno Q ADB USB enumeration requires external `7–24 V` DC barrel/header power; when unpowered, `sdv-governor doctor` reports `adb_usb: BLOCKED_CONNECTION` without affecting standalone or LAN operation. |
| **STM32H5 Real-Time MCU** | Onboard MCU co-processor (`arduino:zephyr:ventunoq`) | Out of scope for the admission/arbitration challenge; no UNO Q motor sketch is flashed. |

---

## 2. Software Bill of Materials (SBOM)

| Package | MacBook Pro (`requirements-macos-arm64.lock`) | Arduino Ventuno Q (`requirements-linux-aarch64.lock`) | License | Purpose |
|---|---|---|---|---|
| `Python` | `3.13.15` | `3.12.3` | PSF-2.0 | Standard library (`fractions`, `dataclasses`, `hashlib`, `http.server`, `xml.etree.ElementTree`) |
| `PyYAML` | `6.0.3` | `6.0.1` / `6.0.3` | MIT | Declarative YAML loading for ship baseline, guest manifests, priority profiles, and scenarios |
| `pytest` | `9.1.1` | `9.1.1` | MIT | Automated test execution and JUnit XML generation (`junit.xml`) |

*(Zero heavy ML/vision dependencies (`onnx`, `torch`, `opencv`) are installed in `.venv`.)*

---

## 3. Standalone & Remote Board Commands

```bash
# Run local hardware & transport doctor
PYTHONPATH=src .venv/bin/python -m sdv_governor.cli doctor

# Sync codebase to remote Linux board (when reachable on ssh ardu) and verify semantic hash parity
PYTHONPATH=src .venv/bin/python -m sdv_governor.cli deploy-board --ssh-target ardu
```

# License Gate & AI Compliance Audit (Phase 0 — `viz3d/`)

**Verification Date:** October 17, 2026  
**Applicable Rules:** Fincantieri Maritime Dual-Use Hackathon — *Data Use Authorization §1–§3* and *Background/Foreground IP*.

---

## 1. AI Restrictions Verification (Data Use Authorization §2)

- **Express Prohibition:** *"AI models developed by companies based in China, or models built on or derived from such models may not be used at any stage of the Hackathon."*
- **Enforcement in `viz3d/`:**
  - **Zero usage** of Chinese-origin 3D generators (e.g., Hunyuan3D / Tencent, Tripo3D / VAST, Rodin / Deemos, Meshy).
  - **100% of the 3D vessel and guest geometry (G1–G6)** is generated via **in-house procedural parametric modeling** (real-time station-lofted geometry in Three.js), constituting team *Foreground IP* under Article 11 of the Challenge Rules.

---

## 2. Third-Party Component License Audit (`viz3d/`)

| Component / Reference | License | Architectural Decision in `viz3d/` |
|---|---|---|
| **Three.js** (`mrdoob/three.js`) | **MIT** | Approved. Used for the 3D WebGL engine, PBR materials, and custom GLSL shaders for the JONSWAP ocean and atmosphere. |
| **OpenBridge Design System** (`Ocean-Industries-Concept-Lab/openbridge-webcomponents`) | **AGPL-3.0 / Commercial JIP** | **Zero AGPL-3.0 code bundled** to avoid copyleft contamination. Instead, we implement clean-room CSS/JS components following the public **IEC 62288:2021** (Day/Dusk/Night palettes), **IEC 62923 / IMO MSC.302(87)** (Bridge Alert Management — BAM), **IMO S-Mode**, and **ISO 14726** standards directly. |
| **FFT / JONSWAP Ocean Algorithm** (Tessendorf 2001) | **Public Mathematical Domain / Own Code** | Clean-room implementation of multi-cascade directional JONSWAP waves with Jacobian crest foam, Kelvin wake (`19.47 deg`), and 8 hydrostatic 6DOF buoyancy probes. |
| **Physical Rayleigh/Mie Atmosphere + San Francisco Bay** | **Own Code** | In-house physical sky shader with Rayleigh/Mie scattering, solar positioning, and 3D topographic silhouette of the Golden Gate Bridge and Marin Headlands. |

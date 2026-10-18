# SDV 3D Digital Twin & IEC 62288 Maritime Bridge Console (`viz3d/`)

Real-time **1:1 scale (`LOA = 60.0 m`, `Beam = 11.0 m`, `Draft = 3.2 m`)** 3D digital twin of the **Software-Defined Vessel** for the **Fincantieri Maritime Dual-Use Hackathon (San Francisco, Oct 16–18, 2026)**, coupled live to the deterministic `sdv-resource-governor` engine without modifying a single line of backend solver logic.

---

## 1. One-Command Launch (Eco-Battery 60 FPS Mode)

From the repository root, run:

```bash
PYTHONPATH=src .venv/bin/python viz3d/serve_viz3d.py --port 5173
```

Or launch the unified operator server directly via `./run_demo.sh` (`http://127.0.0.1:8765/`).

> **Backend Connection Note (`:8765`):**
> - When `PYTHONPATH=src .venv/bin/python -m sdv_governor.cli serve-ui --port 8765` is running, `serve_viz3d.py` acts as a **strict transparent proxy** forwarding all `/api/*` requests to `:8765` (`LIVE_PROXY_8765`).
> - Every tick, D1 admission, D3 arbitration, and D4 degradation verdict comes **100% from the authoritative Python solver**.

---

## 2. Key Features of the 3D Digital Twin (`viz3d/`)

1. **1:1 Lofted Naval Geometry Faithful to the Fincantieri Blueprint (Zero "Boxes"):**
   - Hull lofted across 25 transverse stations with a **hydrodynamic bulbous bow**, `bow thruster` tunnel, chine, anti-fouling red lower hull, waterline boot top, and upper hull in **Fincantieri Naval Grey with Dual-Use diagonal stripe** (live-switchable to **White/Blue Civil Survey livery**).
   - **2x Aft Azimuth Pods (`PROPULSION 2x POD`)** with animated bronze propellers + articulated stern ramp (`POS_stern_ramp`) + `12 x 8 m` flight deck (`POS_flight_deck`) with standard **CAP 437** markings.
   - **Midships RHIB boat recess on davits**, articulated hydraulic deck crane, 2-tier 360-degree bridge (`BRIDGE DECK`) with **COLREG Annex I** navigation lights, and integrated mast (`POS_mast_1` X-band radar, `POS_mast_2` SATCOM radome, `POS_mast_3` free guest slot `<= 60 kg`).
2. **Physical JONSWAP Ocean + Kelvin Wake (`19.47 deg`) + San Francisco Bay:**
   - Ocean surface driven by a **6-cascade directional JONSWAP/Gerstner spectrum**, Jacobian-determinant crest foam, Kelvin wake astern of the transom, and pod prop-wash.
   - **8 hydrostatic 6DOF probes** computing heave, pitch, roll, and physical sinkage (`TPC = 4.94 t/cm -> 1.93 cm` for `9,515 kg`, with a `Hydro x20` toggle labeled `VISUAL ONLY`).
   - Scale 3D silhouette of the **Golden Gate Bridge** (*International Orange*) and Marin Headlands on the horizon.
3. **X-Ray Mode (`X-ray`) and G1–G6 Guest Actors:**
   - **Power**: `DG1 (450 kW)`, `DG2 (450 kW)` (turns red when tripping at minute 12 to `100 kW` net reserve), `HARBOUR (150 kW)`, and feeders `LINE_A_FWD` and `LINE_B_AFT`.
   - **Cooling**: Chilled-water manifold per **ISO 14726** and mast/roof air cooling (`<= 2 kW` per domain).
   - **Compute (`Compute Rack`)**: All 4 nodes (`node_1..node_4`) showing core and accelerator utilization.
   - **Animated Actors**: `G3` (20 ft cradle in `Bay 1` + subsea AUV with sonar cone and acoustic rings shifting from cyan in `SURVEY_QUIET` to amber in `HOLD_ACOUSTIC`), `G4` (4-rotor VTOL SAR drone patrolling, descending on `< 20%` battery alert, and touching down on the flight deck before `T+1200s`), `G5` (10 ft container in `Bay 2` + antenna on `mast_3` or `hangar_roof` with SATCOM beam stepping down from `45 kW` to `8 kW`), and **3D Ghost** placement preview for live **Scenario A (D1)** admission.
4. **Standardized Maritime Bridge Console:**
   - **IEC 62288:2021**: **Day / Night** palette switcher.
   - **IEC 62923 / IMO MSC.302(87) (BAM)**: Alert management and decision log bound directly to solver witnesses.
   - **Integrated Video Recorder (`Rec`)**: Exports the 3D canvas directly to `.webm` at 30/60 FPS with minimal battery draw.

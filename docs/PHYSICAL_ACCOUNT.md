# Physical Account — 8-Class Resource Ledger & Errata Verification (1 Page)

## 1. Subcomponent Mass, Position & Stability Ledger (Organizer Errata Applied)
Stability is computed strictly **per subcomponent and assigned position** ($\times 1.0$ main deck bays `bay1`/`bay2`, $\times 1.2$ `hangar`/`hangar_roof`/`flight_deck`, $\times 3.0$ `mast_3`), never by multiplying an entire multi-part guest by the mast factor. Local structural limits (`Bay 1 <= 8,000 kg`, `Bay 2 <= 4,000 kg`, `Hangar <= 1,000 kg`, `VTOL airframe <= 150 kg`, `mast_3 <= 60 kg / 0.6 m2`, `hangar_roof <= 150 kg / 2.0 m2`) are enforced alongside global limits (`10,000 kg` mass, `12,000 kg-eq` stability). Under the organizer errata, **G4 hangar equipment is 850 kg** (overriding PDF 900 kg) + **140 kg drone = 990 kg**, and **G5 includes both the 3,000 kg container and 25 kg antenna = 3,025 kg**:

| Guest ID & Name | Subcomponents & Assigned Positions | Mass (kg) | Stability Multiplier & Calculation | Stability (kg-eq) | Electrical, Thermal & Data Envelope |
|---|---|---:|---|---:|---|
| **G1** Radar/EO Meteo | `G1_MAST_SENSOR_HEAD` $\rightarrow$ `mast_3` (`0.5 m2`) | `45` | $45 \times 3.0$ (`mast_3`) | `135.0` | `1.5 kW` air on `mast_3` ($\le 2\text{ kW}$ limit; `0 W` water); `50 Mbps` LAN, `200 kbps` SATCOM |
| **G2** Ocean Winch/Probe | `G2_ROV_WINCH_CONTAINER_10FT` $\rightarrow$ `bay2` / `bay1_a` | `3,500` | $3,500 \times 1.0$ (deck bay) | `3,500.0` | `20 kW` sustained `300 s` winch / `6 kW` continuous $\rightarrow$ water; `5 Mbps` LAN, `500 kbps` SATCOM |
| **G3** AUV Survey/Cradle | `G3_AUV_CRADLE_BAY1_20FT` $\rightarrow$ `bay1` (`20 ft` via ramp) | `5,500` | $5,500 \times 1.0$ (deck bay) | `5,500.0` | `1 kW` survey / `25 kW` (`3,600 s`) uninterruptible charge $\rightarrow$ water; `100 Mbps` cradle LAN |
| **G4** VTOL SAR *(Errata)* | `G4_DRONE_VEHICLE` (`140 kg`) + `G4_HANGAR_STATION` (`850 kg`) $\rightarrow$ `hangar` | `990` | $(140 + 850) \times 1.2$ (`hangar` $\le 1,000\text{ kg}$) | `1,188.0` | `35 kW` (`1,200 s`) recovery/charge, `10 kW` patrol, `2 kW` standby (`0 bps` silence); `20 Mbps` LAN |
| **G5** Comms/Compute | `G5_COMPUTE_CONTAINER` (`3,000 kg` $\rightarrow$ `bay2`/`bay1_a`) + `G5_MAST_ANTENNA` (`25 kg` $\rightarrow$ `mast_3`/`hangar_roof`) | `3,025` | $3,000 \times 1.0 + 25 \times 3.0$ (`mast_3`) or $+ 25 \times 1.2$ (`roof`) | `3,075.0` (`3,030.0` roof) | `45 kW` (`43.8 kW` water + `1.2 kW` air) / `8 kW` degraded (`30%` retention); `1 Gbps` LAN, `6 Mbps` SATCOM |
| **G6** Civil Hydro Lab | `G6_HYDRO_LAB_10FT` $\rightarrow$ `bay1_a` / `bay2` *(All fields `ASSUMED`)* | `420` | $420 \times 1.0$ (deck bay) | `420.0` | `4.5 kW` (`300 s`) $\rightarrow$ `4.5 kW` water; `25 Mbps` LAN, `250 kbps` SATCOM |

---

## 2. Five Combinations of Four Official Guests (`{G1..G5}`): Mass vs. Full 8-Class Admission
Evaluating all $\binom{5}{4} = 5$ four-guest combinations confirms the organizer errata (**exact 3 of 5 exceed the 10,000 kg margin**) and proves why **passing mass does not imply passing placement, mast/roof clearance, or 14-day Mission Envelope A.10 constraints**:

| 4-Guest Set | Total Mass (kg) | $>10,000\text{ kg}$? | Total Stability (kg-eq) | Physical Placement, Mast/Roof & Mission Envelope Verdict (D1) |
|---|---:|---|---:|---|
| `{G1, G2, G3, G4}` | **10,035 kg** | **YES** (`+35 kg`) | `10,323.0` ($\le 12,000$) | `YES_IF` (Exceeds `10,000 kg` by `35 kg`; displacing `G2` yields `6,535 kg`) |
| `{G1, G2, G3, G5}` | **12,070 kg** | **YES** (`+2,070 kg`) | `12,210.0` ($> 12,000$) | `YES_IF` (Exceeds mass & stability; `G1` & `G5` collide on `mast_3` and `1.5+1.2=2.7 kW > 2.0 kW` mast air) |
| `{G1, G2, G4, G5}` | **7,560 kg** | **NO** (`2,440 kg` free) | `7,898.0` ($\le 12,000$) | **Blocked on `mast_3` / `hangar_roof`**: `G5` antenna collides with `G1` on `mast_3` (`2.7 kW > 2 kW` air) and with `G4` drone clearance on `hangar_roof` |
| `{G1, G3, G4, G5}` | **9,560 kg** | **NO** (`440 kg` free) | `9,898.0` ($\le 12,000$) | **Blocked on `mast_3` / `hangar_roof`**: passes mass/stability (`G5` container fits in `bay2`), but `G5` antenna collides with `G1` (`mast_3`) or `G4` (`hangar_roof`) |
| `{G2, G3, G4, G5}` | **13,015 kg** | **YES** (`+3,015 kg`) | `13,263.0` ($> 12,000$) | `YES_IF` (Exceeds mass & stability; `G2`+`G5` containers cannot share `bay2` (`6,500 kg > 4,000 kg`) while `G3` occupies `bay1`) |

*(Note: Replacing `G5` with custom card `G6` in `{G1, G2, G4, G6}` yields `4,955 kg`, `5,243.0 kg-eq`, `G1` on `mast_3`, `G2` on `bay2`, `G4` in `hangar`, and `G6` in `bay1_a` $\rightarrow$ **`YES` across all 8 resource classes**.)*

---

## 3. Cooling Curve, Thermal Inertia & Scenario B Power Accounting
- **Chilled-Water Curve**: $C(T) = 160,000 - 40,000 \frac{T-12}{18}\text{ W}$ for $T \in [12, 30]\,^\circ\text{C}$. At $30\,^\circ\text{C}$, $C(30) = 120\text{ kW}$ nominal; with $-40\%$ loop degradation, $C_{\text{deg}}(30) = 72\text{ kW}$. Sustained loads ($>60\text{ s}$) cannot borrow from the $+30\text{ kW}/60\text{ s}$ (`1.8 MJ`) thermal inertia buffer.
- **Scenario B Power vs. Cooling Binding**: During G4 recovery/charge (`35 kW`) + G3 quiet survey (`1 kW`) + G5 full spectrum (`45 kW`), instantaneous electrical draw is **$81\text{ kW} \le 100\text{ kW}$** (no instantaneous electrical deficit on generator loss), whereas **firm committed power** including G3's uninterruptible `25 kW` turnaround recharge reservation is **$25 + 35 + 45 = 105\text{ kW} > 100\text{ kW}$** (`5 kW` firm deficit), and at $30\,^\circ\text{C}$ degraded loop ($72\text{ kW}$), chilled-water load is **$1 + 35 + 43.8 = 79.8\text{ kW} > 72\text{ kW}$** (`7.8 kW` thermal deficit). Stepping down **G5 (`45 kW` $\rightarrow$ `8 kW`)** saves `37 kW` power and `36.3 kW` water cooling, restoring positive margin in both cases while protecting G4 and G3.

---

## 4. What Would Change If the Vessel Platform Were Modified? (Mandatory Closing Analysis)
**Qué cambiaría en el barco / What would change if the vessel had:**
1. **A second free mast position (`mast_4`, $\le 60\text{ kg}$, $\le 2\text{ kW}$ air) or non-interfering roof sponson**: Both mass-feasible 4-guest sets `{G1, G2, G4, G5}` (`7,560 kg`) and `{G1, G3, G4, G5}` (`9,560 kg`) would resolve their mast/roof collision (`G1` on `mast_3` at `1.5 kW`, `G5_MAST_ANTENNA` on `mast_4` at `1.2 kW`) without violating `G4` flight-deck/hangar clearance.
2. **A $+50\text{ kg}$ mass margin increase (`10,050 kg` instead of `10,000 kg`)**: `{G1, G2, G3, G4}` (`10,035 kg`, `10,323 kg-eq`) would transition from `YES_IF` to **`YES`**, as its subcomponents already fit physically (`G1` in `mast_3`, `G2` in `bay2`, `G3` in `bay1`, `G4` in `hangar`).
3. **An upgraded chilled-water heat exchanger (`+20 kW` at $30\,^\circ\text{C}$ degraded $\rightarrow 92\text{ kW}$)**: The vessel could sustain `G3 (1 kW) + G4 (35 kW) + G5 (43.8 kW water) = 79.8 kW` during $30\,^\circ\text{C}$ loop degradation without stepping down G5 until G3 initiates its `25 kW` recharge.

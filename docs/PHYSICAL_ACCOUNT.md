# Physical Account -- 8-Class Resource Ledger & Errata Verification (1 Page)

## 1. Subcomponent Mass, Position & Stability Ledger (Organizer Errata Applied)
Stability is computed strictly per subcomponent and assigned position (x1.0 main deck bays bay1/bay2, x1.2 hangar/hangar_roof/flight_deck, x3.0 mast_3), never by multiplying an entire multi-part guest by the mast factor. Local structural limits (Bay 1 <= 8,000 kg, Bay 2 <= 4,000 kg, Hangar <= 1,000 kg, VTOL airframe <= 150 kg, mast_3 <= 60 kg / 0.6 m2, hangar_roof <= 150 kg / 2.0 m2) are enforced alongside global limits (10,000 kg mass, 12,000 kg-eq stability). Under the organizer errata, G4 hangar station is 850 kg (overriding PDF 900 kg) + 140 kg drone = 990 kg, and G5 includes both the 3,000 kg container and 25 kg antenna = 3,025 kg:

| Guest ID & Official Name | Subcomponents & Assigned Positions | Mass (kg) | Stability Multiplier & Calculation | Stability (kg-eq) | Electrical, Thermal & Data Envelope |
|---|---|---:|---|---:|---|
| G1 -- Passive Radio & Meteo Sensor | G1_MAST_SENSOR_HEAD -> mast_3 (0.5 m2) | 45 | 45 x 3.0 (mast_3) | 135.0 | 1.5 kW air on mast_3 (<= 2.0 kW limit; 0 W water); 50 Mbps LAN, 200 kbps SATCOM |
| G2 -- Ocean & Weather Module | G2_ROV_WINCH_CONTAINER_10FT -> bay2 / bay1_a | 3,500 | 3,500 x 1.0 (deck bay) | 3,500.0 | 20 kW sustained 300 s winch / 6 kW continuous -> water; 5 Mbps LAN, 500 kbps SATCOM |
| G3 -- Seabed Inspection AUV + Cradle | G3_AUV_CRADLE_BAY1_20FT -> bay1 (20 ft via ramp) | 5,500 | 5,500 x 1.0 (deck bay) | 5,500.0 | 1 kW survey / 25 kW (3,600 s) uninterruptible charge -> water; 100 Mbps cradle LAN |
| G4 -- VTOL Rescue Drone + Hangar (Errata) | G4_DRONE_VEHICLE (140 kg) + G4_HANGAR_STATION (850 kg) -> hangar | 990 | (140 + 850) x 1.2 (hangar <= 1,000 kg) | 1,188.0 | 35 kW (1,200 s) recovery/charge, 10 kW patrol, 2 kW standby (0 bps silence); 20 Mbps LAN |
| G5 -- Emergency Comms & Edge Compute | G5_COMPUTE_CONTAINER (3,000 kg -> bay2/bay1_a) + G5_MAST_ANTENNA (25 kg -> mast_3/hangar_roof) | 3,025 | 3,000 x 1.0 + 25 x 3.0 (mast_3) or + 25 x 1.2 (roof) | 3,075.0 (3,030.0 roof) | 45 kW (43.8 kW water + 1.2 kW air) / 8 kW degraded (30% retention); 1 Gbps LAN, 6 Mbps SATCOM |
| G6 -- Civil Hydrographic & eDNA Micro-Sampler | G6_HYDRO_SKID_10FT -> bay2 / bay1_a (All fields ASSUMED, port_only=true) | 420 | 420 x 1.0 (deck bay) | 420.0 | 4.5 kW (1,800 s) / 1.5 kW eco -> water; 30 Mbps LAN, 200 kbps SATCOM |

---

## 2. Five Combinations of Four Official Guests ({G1..G5}): Mass vs. Full 8-Class Admission
Evaluating all C(5,4) = 5 four-guest combinations confirms the organizer errata (exact 3 of 5 exceed the 10,000 kg margin) and proves why passing mass does not imply passing placement, mast/roof clearance, or 14-day Mission Envelope A.10 constraints:

| 4-Guest Set | Total Mass (kg) | > 10,000 kg? | Total Stability (kg-eq) | Physical Placement, Mast/Roof & Mission Envelope Verdict (D1) |
|---|---:|---|---:|---|
| {G1, G2, G3, G4} | 10,035 kg | YES (+35 kg) | 10,323.0 (<= 12,000) | YES_IF (Exceeds 10,000 kg by 35 kg; displacing G2 yields 6,535 kg) |
| {G1, G2, G3, G5} | 12,070 kg | YES (+2,070 kg) | 12,210.0 (> 12,000) | YES_IF (Exceeds mass & stability; G1 & G5 collide on mast_3 and 1.5+1.2=2.7 kW > 2.0 kW mast air) |
| {G1, G2, G4, G5} | 7,560 kg | NO (2,440 kg free) | 7,898.0 (<= 12,000) | YES_IF (Passes mass/stability, but G5 antenna collides with G1 on mast_3 or requires G4 hangar_roof clearance) |
| {G1, G3, G4, G5} | 9,560 kg | NO (440 kg free) | 9,898.0 (<= 12,000) | YES_IF (Passes mass/stability with G5 container in bay2, but G5 antenna collides on mast_3 / hangar_roof) |
| {G2, G3, G4, G5} | 13,015 kg | YES (+3,015 kg) | 13,263.0 (> 12,000) | YES_IF (Exceeds mass & stability; G2+G5 containers cannot share bay2 (6,500 kg > 4,000 kg) while G3 occupies bay1) |

(Note: Replacing G5 with custom card G6 in {G1, G2, G4, G6} yields 4,955 kg, 5,243.0 kg-eq, G1 on mast_3, G2 on bay2, G4 in hangar, and G6 in bay1_a -> YES across all 8 resource classes.)

---

## 3. Cooling Curve, Thermal Inertia & Scenario B Power Accounting
- Chilled-Water Curve: C(T) = 160,000 - 40,000 * (T - 12) / 18 W for T in [12, 30] deg C. At 30 deg C, C(30) = 120 kW nominal; with -40% loop degradation, C_deg(30) = 72 kW. Sustained loads (> 60 s) cannot borrow from the +30 kW / 60 s (1.8 MJ) thermal inertia buffer.
- Scenario B Power vs. Cooling Binding: When D3 approves G4 recovery at T=480 s, the governor reserves G4's committed 35 kW recovery/charging load alongside G3's firm 25 kW post-leg recharge reservation. When generator reserve drops to 100 kW at T=720 s, instantaneous draw is 81 kW <= 100 kW during G4 recovery, whereas firm committed power is 25 + 35 + 45 = 105 kW > 100 kW (5 kW firm deficit), and at 30 deg C degraded loop (72 kW), chilled-water load is 1 + 35 + 43.8 = 79.8 kW > 72 kW (7.8 kW thermal deficit). Stepping down G5 (45 kW -> 8 kW) at T=720 s saves 37 kW power and 36.3 kW water cooling, maintaining 68 kW <= 100 kW (32 kW free margin) and zero infeasible ticks across the entire timeline.

---

## 4. What would change if the vessel had one single platform modification (`mast_4`)?
If a single structural modification were made to the vessel -- adding a second guest mast slot `mast_4` (`<= 60 kg`, `<= 0.6 m2`, `<= 2.0 kW` independent forced-air cooling, `x3.0` stability multiplier) -- the structural placement and mast air-cooling bottleneck blocking both mass-feasible four-guest combinations `{G1, G2, G4, G5}` (`7,560 kg`, `7,898.0 kg-eq`) and `{G1, G3, G4, G5}` (`9,560 kg`, `9,898.0 kg-eq`) is eliminated: `G1_MAST_SENSOR_HEAD` (`45 kg`, `1.5 kW` air on `mast_3`) and `G5_MAST_ANTENNA` (`25 kg`, `1.2 kW` air on `mast_4`) occupy separate mast slots without exceeding the `2.0 kW` per-slot air-cooling limit or encroaching on `G4`'s `hangar_roof` VTOL rotor clearance. Both combinations then achieve feasible static placement (`YES_IF` under Mission Envelope A.10), subject only to operational mode scheduling: (a) during degraded SATCOM (`2.0 Mbps`) or EMCON silence (`W4`, `0 bps`), `G5` (`6.0 Mbps` nominal SATCOM) steps down to `DEGRADED_70PCT_LOSS` / offline buffer (`can_buffer_offline=true`), and (b) during `W2`/`W3`/`W5`, `G2`/`G3`/`G4` deck, acoustic-quiet (`forbids_deck_machinery_while_active`), and speed/heading windows remain time-multiplexed by the D3/D4 governor.

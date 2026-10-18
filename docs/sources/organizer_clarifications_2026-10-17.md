# Organizer Clarifications Register (Received 2026-10-17)

## Provenance & Authentication Status
- **Source Kind**: `ORGANIZER_MESSAGE_TRANSCRIBED_BY_OWNER`
- **Received Date**: `2026-10-17`
- **Published Date / Timezone / Original URL**: Not provided (`null`).
- **Independent Authentication**: `NOT_PERFORMED` (transcribed messages provided directly by the project owner from organizer Graziano; no fabricated screenshot, URL, or external authentication is claimed).

---

## Clarification 1 — Scenario B Official Guest Composition & Source Precedence (`GRAZIANO_B_COMPOSITION_0855`)
- **Posted Time Text**: `8:55 AM`
- **Applies To**: `scenario_B`, `source_precedence`
- **Verbatim Text Supplied by Owner**:

> “Yes. Use G1 + G3 + G4 + G5 for Scenario B. Your figures are correct: G2 + G3 + G4 + G5 fails on both bays and mass (13.0 t vs 10 t), while G1 + G3 + G4 + G5 fits (9.56 t, G3 in Bay 1, G5 in Bay 2, G1 on the mast, G5's antenna on the hangar roof). The Annexes take precedence over the demo narrative.”

---

## Clarification 2 — Hangar Roof Drone Clearance & G4 Total Mass (`GRAZIANO_ROOF_AND_G4_MASS_0904`)
- **Posted Time Text**: `9:04 AM`
- **Applies To**: `G4.total_mass_kg`, `roof_drone_clearance`
- **Verbatim Text Supplied by Owner**:

> “It's your assumption, as long as you declare it and the engine enforces it. The brief deliberately doesn't define the drone-clearance geometry. ‘Check drone clearance’ means your manifest and placement logic must be able to express that constraint, not that the answer is fixed.”

> “Either way, record the assumption in the Physical Account and point to where the code uses it. What we don't accept is ignoring the note. One correction: according to the new mass value provided G3 + G4 + G5 weighs 9.52 t (5.5 + 0.99 + 3.025), not 9.0 t. It still fits the 10 t margin and the bays.”

---

## Adopted Engineering Decisions Derived from Clarifications
1. **Scenario B Active Set**: `G1 + G3 + G4 + G5` (`9,560 kg` total mass, `440 kg` mass margin against `10,000 kg`; `9,853 kg-eq` stability mass, `2,147 kg-eq` margin against `12,000 kg-eq`). `G2` is not installed in Scenario B.
2. **G4 Total Mass (`990 kg`)**:
   - Total mass = `990 kg` (`ORGANIZER_ERRATA`, source `GRAZIANO_ROOF_AND_G4_MASS_0904`, derived from `0.99 t` in `5.5 + 0.99 + 3.025 = 9.515 t ≈ 9.52 t`).
   - Drone vehicle (`G4_DRONE_VEHICLE`) = `140 kg` (`OFFICIAL_BRIEF`, Annex B.4).
   - Hangar station (`G4_HANGAR_STATION`) = `850 kg` (derived via `990 - 140 = 850 kg`, recorded in derivation metadata; previous `450 kg` total / `310 kg` station kept in history as `SUPERSEDED`).
3. **Hangar Roof & G4 Drone Clearance (`ROOF_OUTSIDE_G4_RECOVERY_V1`)**:
   - Compatibility is modeled as `CONDITIONAL` upon verified placement of `G5_MAST_ANTENNA` in `ROOF_ANTENNA_OUTSIDE_RECOVERY_ENVELOPE` outside `["G4_ROTOR_DISC", "G4_RECOVERY_CORRIDOR"]`, scoped to the active layout and invalidated if the antenna relocates, G4 envelope changes, or layout version changes.
   - If the policy is switched to `CONFLICT`, the engine rejects simultaneous `G4` + `G5_MAST_ANTENNA` on `hangar_roof` citing the recovery envelope constraint.

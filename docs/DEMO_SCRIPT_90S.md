# 90-Second Live Demo — One Screen (`DEMO_SCRIPT_90S.md`)

**Order required by the brief:** 90 s of Scenario B with the jury's priority file, then Scenario A with the jury's card. Everything runs on the laptop (`./run_demo.sh`, opens `http://127.0.0.1:8765/` → single screen). No slides. The legacy engineering console stays at `/console/` as backup.

## The screen (one view, no scrolling)
| Zone | What it proves |
|---|---|
| Ship schematic (centre) | Bays 1/2, hangar, hangar roof, flight deck, mast slots M1–M3, stern ramp, labelled with occupant and mode (A.3). 3D is only the drawing; it earns nothing on its own. |
| Eight gauges (right) | A.1 power (Line A/B), A.2 cooling C(T), A.3 positions and mast, A.4 mass/stability, A.5 data links, A.7 compute, A.8 response time. The binding class turns red. Forecast chip `↗ ~39 s · WARN` is advisory only (C′). |
| Decision log (bottom left) | One sentence per decision, from the binding constraint; the forecast advisory line sits on top. |
| Tabs (bottom right) | **Jury test · A** (card, water temperature, generators, wind, sea state, port/sea), **Shocks · D4/C**, **Priority · D6** (same run, two priority files side by side). |
| Header | Live engine chip, mission clock, Golden Gate public data (SST, wind, Hs, sea state, A′ deck window), Priority Survey/Rescue/Custom, Crew 14/6/0, deck clear. |

## Script
| Wall clock | Action | Say / point at |
|---|---|---|
| 00:00–00:10 | Header: pick the jury's priority (Survey or Rescue). | "Scenario B: G1+G3+G4+G5, 9.56 t of 10 t, 3 kn over a cable route, 24 °C; eight gauges steady." |
| 00:10–00:35 | **Play 90 s Scenario B** (20×). At T+08:00 the log shows the D3 decision. | "Drone at 18 %, wind 22 kn: land within 12 min or ditch. Survey primary: G3 finishes 240 s, holds at T+12:00, G4 lands T+18:15, cost 14 min. Decided in milliseconds (budget 2 s)." |
| 00:35–00:55 | T+12:00 genset trip: Power gauge capacity drops 200→100 kW, Gensets pill shows 1. | "Committed 106.5 kW > 100 kW. Shed in declared order: G5 to 8 kW low-power (70 % capability lost); 69.5 kW, zero infeasible ticks. The forecast chip only advises (G5 feeder trend); it never changes a decision." |
| 00:55–01:10 | **Priority · D6 → Run both profiles.** | "Same code, same timeline, two ships: rescue holds G3 at T+08:00, lands G4 at T+14:15, survey loses 26 min." |
| 01:10–01:30 | Header Crew 14 → 6 → 0 (Shocks tab shows effect). | "14 crew: 30 s confirmation. 6: 120 s confirmation, deck occupied is infeasible and escalates. 0: recovery refused — no hands on deck." |
| then | **Jury test · A:** baseline G1+G2+G5, the jury's card (catalog or **Paste card** YAML/JSON), change one number (water 30 °C, generators 1), **Evaluate**. | Verdict badge + one sentence, e.g. "Yes if G2 is throttled to CONTINUOUS_SENSING: otherwise electrical power binds (needs 101.5 kW, 100 kW available)", cheapest ways to say yes, placement, one-genset-down check, time vs 30 s budget. |

**Backup:** recording welcome but does not replace the live run. Physical Account (1 page) and Scenario C memo are handed over before starting.

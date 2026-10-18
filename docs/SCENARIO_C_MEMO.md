# Scenario C Memo (D5) -- One implicit rule across 14 / 6 / 0 crew

**To:** Fincantieri SDV jury
**From:** sdv-resource-governor team
**Subject:** "Nobody on the flight deck during recovery", written down and run by the engine at 14, 6 and 0 aboard

## 1. The rule
Today a deck officer enforces it by common sense: nobody and nothing stays on the flight deck while the drone comes in. The brief lists it as an example rule for Scenario C ("nobody on the flight deck during recovery"); Annex A.3 makes "deck clear" a flight-deck condition and the Glossary says the flight deck "Must be free of people and obstacles". We write it as: **G4 may not start its recovery approach until the flight deck is verified clear, and clearing the deck is a crew task whose duration depends on how many people are aboard.**

- **Trigger:** G4 asks to land (Scenario B min 8: battery 18 %, wind 22 kn). Annex B: "below 20 % battery: land within 12 min or ditch", so the alert at T=480 s sets a hard touchdown deadline of T=1200 s (720 s later).
- **Effect:** the recovery plan cannot be ready before max(ship manoeuvre done, human confirmation done, deck verified clear); touchdown follows after a 270 s approach. If that lands after the deadline, the plan is infeasible: the governor refuses it, names the binding constraint and escalates to the captain. It never plans a landing on an occupied deck.
- **Crew-dependent numbers (A.6 crew response, our mid-range picks):** confirmation 30 s at 14 (brief 15-60 s), 120 s at 6 (1-3 min), none at 0 (impossible uncrewed: automatic or forbidden). Clearing an occupied deck 240 s at 14 (3-5 min), 600 s at 6 (8-12 min), impossible at 0 ("uncrewed only if already clear"). G4 recovery needs 2 deck crew (ASSUMED).

## 2. Where the code encodes it
- `src/sdv_governor/scheduling.py` -- `DEFAULT_TRANSITION_TIMING_POLICY` (`deck_clear_crew_14_s=240`, `deck_clear_crew_6_s=600`, `deck_clear_crew_0_s=None`, `human_confirm_crew_14_s=30`, `human_confirm_crew_6_s=120`, `human_confirm_crew_0_s=0`; same values in `data/ship/transition_timing_policy.yaml`), `compute_deck_clear_duration_s`, `compute_human_confirmation_duration_s`.
- `src/sdv_governor/arbitration.py` -- `_evaluate_candidate_feasibility_and_score`: `t_ready_s = max(maneuver_ready_s, human_confirm_done_s, deck_ready_s)`; witnesses `flight_deck_clear_for_recovery`, `g4_battery_emergency_touchdown_deadline_s` and `g4_recovery_min_crew`. `arbitrate_live_conflict` keeps only feasible candidates, else returns verdict NO with "the governor refuses and escalates to the captain".
- Crew level is a state input (`crew_count`, `crew_regime`, `available_crew`); `replay.py` changes it on `CREW_REGIME_CHANGE` events (Scenario C replay).

## 3. Engine results (Scenario B state, alert T=480 s, deadline T=1200 s; times after the alert)
Measured with `arbitrate_live_conflict` on `scenario_B_arbitration_degradation.yaml`, varying only `crew_count` / `crew_regime` / `available_crew` and `deck_clear`. Every decision took < 2 ms.

| Crew, deck | survey_primary | rescue_primary |
|---|---|---|
| 14, deck clear | YES: confirm 30 s, touchdown +615 s (105 s margin) | YES: touchdown +375 s |
| 14, deck occupied | YES: deck clear 240 s, touchdown +645 s | YES: touchdown +525 s |
| 6, deck clear | YES: confirm 120 s, touchdown +615 s | YES: touchdown +390 s (confirmation is now the slowest step) |
| 6, deck occupied | NO: earliest touchdown +885 s > 720 s; binds time / touchdown deadline and flight_deck_clear_for_recovery; escalates | NO: same, +885 s > 720 s |
| 0, deck clear or occupied | NO: positions / g4_recovery_min_crew binds (2 crew needed, 0 aboard); recovery refused | NO: same |

## 4. What each crew level confirms, automates, and loses

| | 14 aboard | 6 aboard | 0 aboard |
|---|---|---|---|
| Human confirms | Bridge confirms the contested plan (30 s) and the deck team verifies the deck clear (240 s if occupied) | Same two acts, slower: 120 s confirmation, 600 s clearing | Nothing: no one can confirm |
| Automatic | D3 decision in < 2 s; G3 acoustic HOLD; speed/course requests; 35 kW charge reservation; D4 shedding | Same | D3, G3 HOLD and D4 shedding still run (they need no hands) |
| Impossible | Nothing within the deadline: clear or occupied, both priority sets land G4 | Recovering onto an occupied deck: 600 s clearing misses the 12-min battery deadline | Any G4 recovery, and clearing the deck at all |

## 5. What this means for the operator
At 14 the rule costs time but never the drone. At 6 the same rule turns the deck state at the moment of the alert into the deciding fact: keep the deck clear whenever G4 is airborne, or accept that a low-battery G4 will be lost. At 0 the governor does not pretend: it refuses the recovery, names `g4_recovery_min_crew`, and the cost to the loser is the drone. The jury can check this by changing only the crew level and deck state: no other code path or priority changes.

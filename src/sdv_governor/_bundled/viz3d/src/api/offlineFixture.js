/**
 * Fixture determinista de respaldo (OFFLINE FIXTURE) que reproduce con exactitud matemática
 * los 90 ticks (0..1800 s, dt=20 s, 20x) y las decisiones de `src/sdv_governor/replay.py`,
 * `arbitration.py`, `degradation.py` y `admission.py` cuando el navegador se abre sin el servidor
 * Python activo. Cuando `/api/state` responde, el sistema usa siempre el payload LIVE del backend.
 */

export function buildDeterministicOfflineState(priorityProfileId = "survey_primary") {
  const isSurvey = priorityProfileId === "survey_primary";
  // En survey_primary: CAND_WAYPOINT_PRESERVING_RECOVERY (hold_delay=240s -> cmd@720s, ack@735s, turn@735s, stab@825s, touchdown@1095s <= 1200s)
  // En rescue_primary: CAND_IMMEDIATE_RECOVERY_TURN (hold_delay=0s -> cmd@480s, ack@495s, turn@495s, stab@585s, touchdown@855s <= 1200s)
  const holdCmdS = isSurvey ? 720 : 480;
  const holdAckS = holdCmdS + 15;
  const turnS = holdAckS;
  const stabS = turnS + 90;
  const touchdownS = turnS + 360; // 1095s (survey_primary) o 855s (rescue_primary)

  const timelineTicks = [];
  for (let tick = 0; tick <= 90; tick++) {
    const simTimeS = tick * 20;
    const events = [];
    let vesselMode = "QUIET_SURVEY";
    let powerCapW = 200000;
    let coolingCapW = 133333; // C(24°C) = 160000 - 40000*(12/18) = 133,333 W
    let g3Mode = "SURVEY_QUIET";
    let g4Mode = "FLIGHT_PATROL";
    let g5Mode = "FULL_SPECTRUM";
    let planVersion = 0;

    if (simTimeS === 0) {
      events.push("EVENT_MISSION_START@0s");
    }
    if (simTimeS >= 480) {
      planVersion = 1;
      if (simTimeS === 480) {
        events.push("ALERT_G4_BATTERY_18.0PCT@480s");
      }
    }
    if (simTimeS >= holdCmdS && simTimeS - 20 < holdCmdS) {
      events.push(`G3_ACOUSTIC_HOLD_CMD_SENT@${holdCmdS}s`);
    }
    if (simTimeS >= holdAckS) {
      g3Mode = "HOLD_ACOUSTIC";
      if (simTimeS - 20 < holdAckS) {
        events.push(`G3_ENTERED_HOLD_ACOUSTIC@${holdAckS}s`);
      }
    }
    if (simTimeS >= turnS) {
      vesselMode = "UAV_RECOVERY_HEADING";
      if (simTimeS - 20 < turnS) {
        events.push(`VESSEL_TURNED_INTO_WIND_FOR_G4_RECOVERY@${turnS}s`);
      }
    }
    if (simTimeS >= stabS) {
      g4Mode = "LAUNCH_RECOVERY_OPTICS";
      if (simTimeS - 20 < stabS) {
        events.push(`G4_STARTED_35KW_RECOVERY_APPROACH@${stabS}s`);
      }
    }
    if (simTimeS >= touchdownS && simTimeS - 20 < touchdownS) {
      events.push(`G4_TOUCHDOWN_AND_SECURED_IN_HANGAR@${touchdownS}s`);
      events.push(`G4_STARTED_20MIN_35KW_HANGAR_CHARGE@${touchdownS}s`);
    }

    // Minuto 12 (720 s): Pérdida de generador 200 kW -> 100 kW
    if (simTimeS >= 720 && simTimeS < 1500) {
      powerCapW = 100000;
      vesselMode = "DEGRADED_POWER";
      planVersion = 2;
      // En 720s, si G4 ya está en 35kW o reservado (firme 25+35+45 = 105 kW > 100 kW), G5 baja a DEGRADED_70PCT_LOSS (8 kW)
      g5Mode = "DEGRADED_70PCT_LOSS";
      if (simTimeS === 720) {
        events.push("PERTURBATION_GENERATOR_LOSS_TO_100KW@720s");
        events.push("D4_STEPDOWN:G5:FULL_SPECTRUM->DEGRADED_70PCT_LOSS(45->8kW)");
      }
    }

    // Minuto 25 (1500 s): Restauración de generador a 200 kW y reconexión de G5 con histéresis
    if (simTimeS >= 1500) {
      powerCapW = 200000;
      vesselMode = simTimeS >= touchdownS ? "QUIET_SURVEY" : "UAV_RECOVERY_HEADING";
      g5Mode = "FULL_SPECTRUM";
      planVersion = 3;
      if (simTimeS === 1500) {
        events.push("RESTORATION_RESERVE_200KW_COOLING_NOMINAL@1500s");
        events.push("G5_RECONNECTED_TO_FULL_SPECTRUM@1500s");
      }
    }

    const g3Power = 1000;
    const g4Power = g4Mode === "LAUNCH_RECOVERY_OPTICS" ? 35000 : g4Mode === "FLIGHT_PATROL" ? 10000 : 2000;
    const g5Power = g5Mode === "FULL_SPECTRUM" ? 45000 : 8000;
    const actualPowerW = g3Power + g4Power + g5Power;
    // Reserva firme de carga de G3 (25 kW ininterrumpible post-leg)
    const committedPowerW = 25000 + g4Power + g5Power;
    const coolingWaterW = 1000 + g4Power + (g5Mode === "FULL_SPECTRUM" ? 43800 : 7500);

    timelineTicks.push({
      tick,
      sim_time_s: simTimeS,
      plan_version: planVersion,
      vessel_mode: vesselMode,
      events,
      guest_modes: {
        G3: { mode: g3Mode, powered_on: true, mode_elapsed_s: simTimeS },
        G4: { mode: g4Mode, powered_on: true, mode_elapsed_s: Math.max(0, simTimeS - stabS) },
        G5: { mode: g5Mode, powered_on: true, mode_elapsed_s: simTimeS },
      },
      power_actual_w: actualPowerW,
      power_committed_w: committedPowerW,
      power_capacity_w: powerCapW,
      cooling_actual_w: coolingWaterW,
      cooling_capacity_w: coolingCapW,
      thermal_inertia_status: "THERMAL_NOMINAL",
      thermal_inertia_accumulated_j: 0,
      feasible: committedPowerW <= powerCapW && coolingWaterW <= coolingCapW,
      measured_tick_calc_ms: 0.42,
    });
  }

  const initialTick = timelineTicks[0];
  const baseSnapshot = {
    feasible: true,
    classes: {
      power: {
        capacity: 200000,
        degraded_capacity: initialTick.power_capacity_w,
        committed: initialTick.power_committed_w,
        actual: initialTick.power_actual_w,
        free: initialTick.power_capacity_w - initialTick.power_committed_w,
        unit: "W",
        feasible: true,
        provenance: "OFFICIAL_BRIEF",
        local_limits: {
          LINE_A_FWD: { max_w: 150000, used_actual_w: 2200, used_committed_w: 26200 },
          LINE_B_AFT: { max_w: 150000, used_actual_w: 53800, used_committed_w: 53800 },
        },
      },
      cooling: {
        capacity: 160000,
        degraded_capacity: 133333,
        committed: initialTick.cooling_actual_w,
        actual: initialTick.cooling_actual_w,
        free: 133333 - initialTick.cooling_actual_w,
        unit: "W",
        feasible: true,
        provenance: "OFFICIAL_BRIEF",
        local_limits: {
          seawater_temp_c: 24.0,
          loop_degraded: false,
          mast_air_used_w: 1200,
          mast_air_max_w: 2000,
        },
      },
      positions: {
        capacity: 6,
        degraded_capacity: 6,
        committed: 4,
        actual: 4,
        free: 2,
        unit: "slots",
        feasible: true,
        provenance: "OFFICIAL_BRIEF",
        local_limits: {
          bay1_20ft: ["G3:G3_AUV_CRADLE_BAY1_20FT"],
          bay1_a_10ft: [],
          bay1_b_10ft: [],
          bay1_total_mass_kg: 5500,
          bay1_max_mass_kg: 8000,
          bay2_10ft: ["G5:G5_COMPUTE_CONTAINER"],
          bay2_total_mass_kg: 3000,
          bay2_max_mass_kg: 4000,
          hangar: ["G4:G4_HANGAR_STATION"],
          hangar_total_mass_kg: 850,
          hangar_max_mass_kg: 1000,
          hangar_roof: [],
          flight_deck: ["G4:G4_DRONE_VEHICLE"],
          stern_ramp: [],
        },
      },
      mast: {
        capacity: 1,
        degraded_capacity: 1,
        committed: 1,
        actual: 1,
        free: 0,
        unit: "guest_slots",
        feasible: true,
        provenance: "OFFICIAL_BRIEF",
        local_limits: {
          fixed_positions: ["mast_1", "mast_2"],
          guest_slot_mast_3: {
            owner: "G5:G5_MAST_ANTENNA",
            mass_kg: 25,
            max_mass_kg: 60,
            footprint_m2: 0.3,
            max_footprint_m2: 0.6,
            air_cooling_w: 1200,
            max_air_cooling_w: 2000,
          },
        },
      },
      mass_stability: {
        capacity: 10000,
        degraded_capacity: 10000,
        committed: 9515,
        actual: 9515,
        free: 485,
        unit: "kg",
        feasible: true,
        provenance: "ORGANIZER_ERRATA",
        local_limits: {
          mass_margin_kg: { actual_kg: 9515, max_kg: 10000, free_kg: 485 },
          stability_equivalent_kg: { actual_kg_eq: 9763, max_kg_eq: 12000, free_kg_eq: 2237 },
        },
      },
      data: {
        capacity: 10000000000,
        degraded_capacity: 10000000000,
        committed: 1120000000,
        actual: 1120000000,
        free: 8880000000,
        unit: "bps",
        feasible: true,
        provenance: "OFFICIAL_BRIEF",
        local_limits: {
          satcom_used_bps: 7000000,
          satcom_max_bps: 8000000,
          emcon_silence: false,
        },
      },
      compute: {
        capacity: 100,
        degraded_capacity: 100,
        committed: 24,
        actual: 24,
        free: 76,
        unit: "cores",
        feasible: true,
        provenance: "ASSUMED",
        local_limits: {
          cluster_accelerators_used: 2,
          cluster_accelerators_max: 3,
          nodes: {
            node_1: { guest: "G3", used_cores: 8, allocatable_cores: 25, used_accelerators: 0, allocatable_accelerators: 1 },
            node_2: { guest: "G5", used_cores: 8, allocatable_cores: 25, used_accelerators: 1, allocatable_accelerators: 1 },
            node_3: { guest: "G4", used_cores: 8, allocatable_cores: 25, used_accelerators: 1, allocatable_accelerators: 1 },
            node_4: { guest: null, used_cores: 0, allocatable_cores: 25, used_accelerators: 0, allocatable_accelerators: 1 },
          },
        },
      },
      time: {
        capacity: 2000,
        degraded_capacity: 2000,
        committed: 1,
        actual: 1,
        free: 1999,
        unit: "ms",
        feasible: true,
        provenance: "MEASURED",
        local_limits: {
          scenario_a_admission_deadline_ms: 30000,
          scenario_b_arbitration_deadline_ms: 2000,
          degradation_first_shed_deadline_ms: 5000,
          urgent_command_deadline_ms: 200,
        },
      },
    },
  };

  return {
    status: "OK",
    _source_mode: "OFFLINE_DETERMINISTIC_FIXTURE",
    state_version: 1,
    snapshot_id: "SNAP_V0001_T0S",
    time_compression: {
      wall_clock_demo_s: 90,
      simulated_mission_s: 1800,
      acceleration_factor: 20.0,
      tick_interval_sim_s: 20,
    },
    doctor: {
      local_host: {
        hostname: "MacBookPro-M1Pro.local",
        system: "Darwin",
        machine: "arm64",
        pid: 1042,
        standalone_ready: true,
        code_sha256: "f48a9c10d2e83c71b65a90f32e184c92a01b7e54",
      },
      remote_board: {
        connection_status: "STANDBY_LAN_10.0.0.130",
        hostname: "training06 (Arduino Ventuno Q / QCS8300)",
      },
    },
    priority_profile_id: priorityProfileId,
    available_priority_profiles: ["survey_primary", "rescue_primary"],
    initial_budget_snapshot: baseSnapshot,
    current_budget_snapshot: JSON.parse(JSON.stringify(baseSnapshot)),
    live_operational_state: {
      sim_time_s: 0,
      vessel_mode: "QUIET_SURVEY",
      speed_kn: 3.0,
      heading_deg: 90.0,
      wind_deg: 270.0,
      wind_kn: 14,
      sea_state: 2,
      seawater_temp_c: 24.0,
      crew_count: 14,
      crew_regime: "FULL_CREW_14",
      power_reserve_w: 200000,
      cooling_loop_degraded: false,
      emcon_silence: false,
      deck_clear: true,
    },
    scenario_B_replay: {
      scenario_id: "SCENARIO_B_OFFICIAL",
      priority_profile_id: priorityProfileId,
      executed_touchdown_time_s: touchdownS,
      semantic_sha256: isSurvey
        ? "9a8e41c7b20194e3f65c12a08d47b931e62c048192a3d7f5e10384c291b6a4e2"
        : "3c71b904e2185a64d09f32e17c84a520b91e63d482f1a07c593e28b410d6f8a1",
      timeline_ticks: timelineTicks,
      decisions: [
        {
          phase: "D3_ARBITRATION",
          tick: 24,
          sim_time_s: 480,
          event_time_s: 480,
          plan_version: 1,
          decision: {
            decision_type: "D3_LIVE_ARBITRATION",
            verdict: "YES",
            selected_candidate_id: isSurvey
              ? "CAND_WAYPOINT_PRESERVING_RECOVERY"
              : "CAND_IMMEDIATE_RECOVERY_TURN",
            strategy_applied: isSurvey
              ? "MINIMAL_RECOVERABLE_PAUSE"
              : "IMMEDIATE_FULL_SUSPENSION",
            hold_cmd_time_s: holdCmdS,
            hold_ack_time_s: holdAckS,
            turn_into_wind_time_s: turnS,
            stabilized_time_s: stabS,
            touchdown_time_s: touchdownS,
            hard_touchdown_deadline_s: 1200,
            safety_margin_s: 1200 - touchdownS,
          },
        },
        {
          phase: "D4_DEGRADATION",
          tick: 36,
          sim_time_s: 720,
          event_time_s: 720,
          plan_version: 2,
          decision: {
            decision_type: "D4_GRACEFUL_DEGRADATION",
            status: "RECOVERED_WITHIN_ENVELOPE",
            actions_taken: [
              {
                step: 1,
                action: "STEP_DOWN_MODE",
                guest_id: "G5",
                from_mode: "FULL_SPECTRUM",
                to_mode: "DEGRADED_70PCT_LOSS",
                power_saved_w: 37000,
                reason: "Lowest mission priority under active profile; preserves G3 25kW recharge reservation and G4 35kW recovery.",
              },
            ],
          },
        },
        {
          phase: "D4_RECONNECTION",
          tick: 75,
          sim_time_s: 1500,
          event_time_s: 1500,
          plan_version: 3,
          decision: {
            decision_type: "D4_RECONNECTION",
            reconnect_allowed: true,
            guest_id: "G5",
            restored_mode: "FULL_SPECTRUM",
          },
        },
      ],
    },
    scenario_A_combinations: {
      combinations: [
        { combination: ["G1", "G2", "G3", "G4"], total_mass_kg: 10035, exceeds_10000_kg: true, fourth_guest_id: "G4", fourth_guest_verdict: "NO (10,035 kg > 10,000 kg Mass Limit)" },
        { combination: ["G1", "G2", "G3", "G5"], total_mass_kg: 12070, exceeds_10000_kg: true, fourth_guest_id: "G5", fourth_guest_verdict: "NO (12,070 kg > 10,000 kg Mass Limit)" },
        { combination: ["G1", "G2", "G4", "G5"], total_mass_kg: 7560, exceeds_10000_kg: false, fourth_guest_id: "G5", fourth_guest_verdict: "YES_IF (G5 antenna on hangar_roof with G4 clearance)" },
        { combination: ["G1", "G3", "G4", "G5"], total_mass_kg: 9560, exceeds_10000_kg: false, fourth_guest_id: "G5", fourth_guest_verdict: "YES_IF (G5 antenna on hangar_roof with G4 clearance)" },
        { combination: ["G2", "G3", "G4", "G5"], total_mass_kg: 13015, exceeds_10000_kg: true, fourth_guest_id: "G5", fourth_guest_verdict: "NO (13,015 kg > 10,000 kg Mass Limit)" },
      ],
    },
  };
}

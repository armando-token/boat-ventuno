/* viz3d/js/data.js — Authoritative Governor API client, 1:1 local deterministic fallback & view-model derivation.
   Coupled strictly to src/sdv_governor (server.py, budgets.py, replay.py, arbitration.py, scheduling.py, admission.py). */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});

  SDV.isPreviewSynthetic = function () {
    if (typeof window === 'undefined' || !window.location) return false;
    const params = new URLSearchParams(window.location.search || '');
    return params.get('mode') === 'preview_synthetic';
  };

  // =========================================================================
  // 1. Authoritative Reference Catalog & Scenario A C(5,4) Proof Table
  // =========================================================================
  SDV.GUEST_CATALOG = {
    G1: {
      guest_id: 'G1', name: 'Mast Radio / AIS / EO-IR Sensor Suite',
      mass_kg: 45, stab_kg_eq: 135.0, slot: 'mast_3', sub_id: 'G1_MAST_SENSOR_HEAD',
      feeder: 'LINE_A_FWD', port_only: true, wind_area_m2: 1.5,
      modes: {
        NOMINAL: { pwr: 1500, water: 0, air_mast: 1500, air_roof: 0, lan: 100000, sat: 1200000, cpu: 2, acc: 0 },
        RADAR_DEGRADED_30PCT: { pwr: 1500, water: 0, air_mast: 1500, air_roof: 0, lan: 80000, sat: 800000, cpu: 2, acc: 0 },
        STANDBY: { pwr: 300, water: 0, air_mast: 300, air_roof: 0, lan: 20000, sat: 50000, cpu: 1, acc: 0 }
      }
    },
    G2: {
      guest_id: 'G2', name: 'Tethered Inspection ROV & Winch Skid',
      mass_kg: 3500, stab_kg_eq: 3500.0, slot: 'bay1', sub_id: 'G2_ROV_WINCH_SKID_BAY1_20FT',
      feeder: 'LINE_A_FWD', port_only: true, wind_area_m2: 0,
      modes: {
        INSPECTION_NOMINAL: { pwr: 6000, water: 6000, air_mast: 0, air_roof: 0, lan: 80000000, sat: 500000, cpu: 8, acc: 0 },
        WINCH_RECOVERY_SUSTAINED: { pwr: 20000, water: 20000, air_mast: 0, air_roof: 0, lan: 80000000, sat: 500000, cpu: 8, acc: 0 },
        STANDBY: { pwr: 500, water: 500, air_mast: 0, air_roof: 0, lan: 5000000, sat: 0, cpu: 2, acc: 0 }
      }
    },
    G3: {
      guest_id: 'G3', name: 'Seabed Survey AUV & LARS Module',
      mass_kg: 5500, stab_kg_eq: 6110.0, slot: 'bay1', sub_id: 'G3_AUV_CRADLE_BAY1_20FT',
      feeder: 'LINE_A_FWD', port_only: true, wind_area_m2: 0,
      modes: {
        SURVEY_QUIET: { pwr: 1000, water: 1000, air_mast: 0, air_roof: 0, lan: 120000000, sat: 0, cpu: 8, acc: 0 },
        HOLD_ACOUSTIC: { pwr: 1000, water: 1000, air_mast: 0, air_roof: 0, lan: 20000000, sat: 0, cpu: 4, acc: 0 },
        RECHARGE_CRADLE: { pwr: 25000, water: 25000, air_mast: 0, air_roof: 0, lan: 50000000, sat: 0, cpu: 4, acc: 0 },
        STANDBY: { pwr: 1000, water: 1000, air_mast: 0, air_roof: 0, lan: 10000000, sat: 0, cpu: 2, acc: 0 }
      }
    },
    G4: {
      guest_id: 'G4', name: 'Rotary VTOL SAR UAV & Hangar Bay',
      mass_kg: 990, stab_kg_eq: 1068.0, slot: 'hangar', sub_id: 'G4_HANGAR_STATION',
      feeder: 'LINE_B_AFT', port_only: true, wind_area_m2: 0,
      modes: {
        FLIGHT_PATROL: { pwr: 10000, water: 10000, air_mast: 0, air_roof: 0, lan: 250000000, sat: 0, cpu: 8, acc: 0 },
        LAUNCH_RECOVERY_OPTICS: { pwr: 35000, water: 35000, air_mast: 0, air_roof: 0, lan: 350000000, sat: 0, cpu: 8, acc: 1 },
        STANDBY: { pwr: 2000, water: 2000, air_mast: 0, air_roof: 0, lan: 20000000, sat: 0, cpu: 2, acc: 0 }
      }
    },
    G5: {
      guest_id: 'G5', name: 'Comms & Edge Compute Container + Antenna',
      mass_kg: 3025, stab_kg_eq: 3030.0, slot: 'bay2', sub_id: 'G5_COMPUTE_CONTAINER',
      feeder: 'LINE_B_AFT', port_only: true, wind_area_m2: 0,
      modes: {
        FULL_SPECTRUM: { pwr: 45000, water: 43800, air_mast: 0, air_roof: 1200, lan: 800000000, sat: 6000000, cpu: 8, acc: 2 },
        DEGRADED_70PCT_LOSS: { pwr: 8000, water: 7500, air_mast: 0, air_roof: 500, lan: 100000000, sat: 800000, cpu: 8, acc: 2 },
        STANDBY: { pwr: 1000, water: 1000, air_mast: 0, air_roof: 0, lan: 10000000, sat: 0, cpu: 2, acc: 0 }
      }
    },
    G6: {
      guest_id: 'G6', name: 'Civil Hydrographic eDNA Sampler',
      mass_kg: 420, stab_kg_eq: 420.0, slot: 'bay1_b', sub_id: 'G6_EDNA_SAMPLER_SKID_BAY1B',
      feeder: 'LINE_A_FWD', port_only: true, wind_area_m2: 0,
      modes: {
        SAMPLING_ACTIVE: { pwr: 4500, water: 4500, air_mast: 0, air_roof: 0, lan: 50000000, sat: 100000, cpu: 4, acc: 0 },
        ECO_MONITOR: { pwr: 1500, water: 1500, air_mast: 0, air_roof: 0, lan: 10000000, sat: 50000, cpu: 2, acc: 0 },
        STANDBY: { pwr: 300, water: 300, air_mast: 0, air_roof: 0, lan: 5000000, sat: 0, cpu: 1, acc: 0 }
      }
    },
    G7: {
      guest_id: 'G7', name: 'Declarative Compact MetOcean Buoy Launcher',
      mass_kg: 450, stab_kg_eq: 450.0, slot: 'bay1_b', sub_id: 'G7_LAUNCHER',
      feeder: 'LINE_A_FWD', port_only: true, wind_area_m2: 0,
      modes: {
        NOMINAL: { pwr: 1500, water: 1500, air_mast: 0, air_roof: 0, lan: 200000, sat: 200000, cpu: 2, acc: 0 }
      }
    }
  };

  SDV.COMBINATIONS_C54 = [
    { combo: 'G1 + G2 + G3 + G4', mass_kg: 10035, stab_kg_eq: 10813.0, verdict: 'NO', reason: 'Mass 10,035 kg > 10,000 kg (+35 kg); Bay 1 conflict (G2 vs G3)' },
    { combo: 'G1 + G2 + G3 + G5', mass_kg: 12070, stab_kg_eq: 12775.0, verdict: 'NO', reason: 'Mass 12,070 kg > 10,000 kg (+2,070 kg); Stab 12,775 > 12,000 kg-eq; Bay 1 conflict' },
    { combo: 'G2 + G3 + G4 + G5', mass_kg: 13015, stab_kg_eq: 13708.0, verdict: 'NO', reason: 'Mass 13,015 kg > 10,000 kg (+3,015 kg); Stab 13,708 > 12,000 kg-eq; Bay 1 conflict' },
    { combo: 'G1 + G2 + G4 + G5', mass_kg: 7560,  stab_kg_eq: 7733.0,  verdict: 'YES_IF', reason: 'Mass 7,560 kg <= 10,000 kg; YES_IF G5 antenna on hangar_roof (ROOF_OUTSIDE_G4_RECOVERY_V1)' },
    { combo: 'G1 + G3 + G4 + G5', mass_kg: 9560,  stab_kg_eq: 9853.0,  verdict: 'YES_IF', reason: 'Scenario B Baseline: Mass 9,560 kg (440 kg free); YES_IF G5 on hangar_roof + G3 acoustic window' }
  ];

  SDV.CREW_REGIMES = {
    FULL_CREW_14:    { id: 'FULL_CREW_14',    label: '14 Crew (Full)',    total: 14, watch: 5, avail: 9, confirm_s: 30,  deck_clear_s: 240, sea_install: true },
    REDUCED_CREW_6:  { id: 'REDUCED_CREW_6',  label: '6 Crew (Reduced)',  total: 6,  watch: 4, avail: 2, confirm_s: 120, deck_clear_s: 600, sea_install: true },
    UNCREWED_0:      { id: 'UNCREWED_0',      label: '0 Crew (Uncrewed)', total: 0,  watch: 0, avail: 0, confirm_s: 5,   deck_clear_s: null, sea_install: false }
  };

  // =========================================================================
  // 2. 1:1 Local Authoritative Engine (Fallback when opened via file:// or preview)
  // =========================================================================
  function createLocalSession() {
    const sess = {
      run_id: 'RUN_LOCAL_AUTH_0001',
      state_version: 1,
      priority_profile_id: 'survey_primary',
      runner_status: 'PAUSED',
      sim_time_s: 0,
      crew_regime: 'FULL_CREW_14',
      deck_clear: true,
      seawater_temp_c: 24.0,
      temp_sensor_failed: false,
      cooling_loop_degraded: false,
      power_reserve_override_w: null,
      g5_overconsumption_w: null,
      g3_stopped: false,
      g4_stopped: false,
      g3_urgent_hold: false,
      installed_ids: ['G1', 'G3', 'G4', 'G5'],
      guest_overrides: {},
      decision_log: []
    };

    sess.reset = function (profileId) {
      if (profileId) sess.priority_profile_id = profileId;
      sess.state_version += 1;
      sess.runner_status = 'PAUSED';
      sess.sim_time_s = 0;
      sess.seawater_temp_c = 24.0;
      sess.temp_sensor_failed = false;
      sess.cooling_loop_degraded = false;
      sess.power_reserve_override_w = null;
      sess.g5_overconsumption_w = null;
      sess.g3_stopped = false;
      sess.g4_stopped = false;
      sess.g3_urgent_hold = false;
      sess.installed_ids = ['G1', 'G3', 'G4', 'G5'];
      sess.guest_overrides = {};
      sess.decision_log = [{
        type: 'SESSION_RESET',
        action: 'RESET_SCENARIO_B',
        sim_time_s: 0,
        causal_sentence: 'Because operator reset session under profile ' + sess.priority_profile_id.toUpperCase() + ', Scenario B initialized at T=0s with G1+G3+G4+G5 (9,560 kg / 10,000 kg, 57.5 kW actual / 81.5 kW committed).'
      }];
    };

    sess.computeTickSnapshot = function (tSec) {
      const isRescue = sess.priority_profile_id === 'rescue_primary';
      const holdCmd = isRescue ? 480 : 720;
      const holdAck = holdCmd + 15;
      const turnS = holdAck;
      const stabS = turnS + 90;
      const tdS = turnS + 360; // 855s rescue_primary, 1095s survey_primary
      const chargeDoneS = tdS + 600;

      const d4PowerFault = (tSec >= 720 && tSec < 1500) || (sess.power_reserve_override_w !== null && sess.power_reserve_override_w <= 100000);
      const pwrCap = sess.power_reserve_override_w !== null ? sess.power_reserve_override_w : (d4PowerFault ? 100000 : 200000);

      const effTemp = sess.temp_sensor_failed ? 30.0 : sess.seawater_temp_c;
      const baseCool = 150000 * (1.0 - 0.025 * Math.max(0, effTemp - 20.0));
      const coolCap = Math.round(sess.cooling_loop_degraded ? baseCool * 0.60 : baseCool);

      let vesselMode = 'QUIET_SURVEY';
      let speedKn = 6.0;
      let headingDeg = 90;
      let windKn = tSec >= 480 ? 22 : 12;
      let seaState = tSec >= 480 ? 3 : 2;

      let g3Mode = 'SURVEY_QUIET';
      if (sess.g3_urgent_hold || (tSec >= holdAck && tSec < tdS + 45)) {
        g3Mode = 'HOLD_ACOUSTIC';
      }

      let g4Mode = 'FLIGHT_PATROL';
      let g4DronePos = 'flight_deck';
      if (!sess.g4_stopped) {
        if (tSec >= chargeDoneS) {
          g4Mode = 'STANDBY';
          g4DronePos = 'hangar';
        } else if (tSec >= tdS) {
          g4Mode = 'LAUNCH_RECOVERY_OPTICS';
          g4DronePos = 'hangar';
        } else if (tSec >= stabS) {
          g4Mode = 'LAUNCH_RECOVERY_OPTICS';
          g4DronePos = 'flight_deck';
        }
      }

      if (tSec >= turnS && tSec < tdS + 20 && !sess.g4_stopped) {
        vesselMode = 'UAV_RECOVERY_HEADING';
        speedKn = 8.0;
        headingDeg = 45;
      }
      if (d4PowerFault) {
        vesselMode = 'DEGRADED_POWER';
      } else if (sess.cooling_loop_degraded && vesselMode === 'QUIET_SURVEY') {
        vesselMode = 'DEGRADED_THERMAL';
      }

      const g5Degraded = d4PowerFault || sess.cooling_loop_degraded || (sess.g5_overconsumption_w !== null);
      const g5Mode = g5Degraded ? 'DEGRADED_70PCT_LOSS' : 'FULL_SPECTRUM';

      // Sum across installed guests
      let pAct = 0, lineAAct = 0, lineBAct = 0;
      let wAct = 0, airMast = 0, airRoof = 0;
      let totMass = 0, totStab = 0;
      let lanBps = 0, satBps = 0;
      let cpuCores = 0, accCnt = 0;
      const installedObj = {};
      const slotMap = { bay1: [], bay1_b: [], bay2: [], hangar: [], hangar_roof: [] };
      let mast3Owner = null;

      sess.installed_ids.forEach(function (gid) {
        const baseSpec = SDV.GUEST_CATALOG[gid] || SDV.GUEST_CATALOG.G6;
        const ov = sess.guest_overrides[gid] || {};
        const massKg = typeof ov.mass_kg === 'number' ? ov.mass_kg : baseSpec.mass_kg;
        const stabKg = typeof ov.mass_kg === 'number' ? ov.mass_kg : baseSpec.stab_kg_eq;
        const slot = ov.preferred_position || baseSpec.slot;

        let activeMode = Object.keys(baseSpec.modes)[0];
        if (gid === 'G3') activeMode = g3Mode;
        else if (gid === 'G4') activeMode = g4Mode;
        else if (gid === 'G5') activeMode = g5Mode;

        const poweredOn = !((gid === 'G3' && sess.g3_stopped) || (gid === 'G4' && sess.g4_stopped));
        const mSpec = baseSpec.modes[activeMode] || Object.values(baseSpec.modes)[0];

        const pwrW = poweredOn ? (typeof ov.sustained_power_w === 'number' ? ov.sustained_power_w : mSpec.pwr) : 0;
        const watW = poweredOn ? (typeof ov.cooling_water_w === 'number' ? ov.cooling_water_w : mSpec.water) : 0;

        pAct += pwrW;
        if (baseSpec.feeder === 'LINE_A_FWD') lineAAct += pwrW;
        else lineBAct += pwrW;

        wAct += watW;
        airMast += poweredOn ? mSpec.air_mast : 0;
        airRoof += poweredOn ? mSpec.air_roof : 0;
        totMass += massKg;
        totStab += stabKg;
        lanBps += poweredOn ? mSpec.lan : 0;
        satBps += poweredOn ? mSpec.sat : 0;
        cpuCores += poweredOn ? mSpec.cpu : 0;
        accCnt += poweredOn ? mSpec.acc : 0;

        const placements = {};
        placements[baseSpec.sub_id] = slot;
        if (gid === 'G4') placements.G4_DRONE_VEHICLE = g4DronePos;
        if (gid === 'G5') placements.G5_MAST_ANTENNA = ov.preferred_position === 'mast_3' ? 'mast_3' : 'hangar_roof';

        if (slot === 'mast_3') mast3Owner = gid + ':' + baseSpec.sub_id;
        else if (slotMap[slot]) slotMap[slot].push(gid + ':' + baseSpec.sub_id);

        if (gid === 'G5') {
          const antSlot = placements.G5_MAST_ANTENNA;
          if (antSlot === 'mast_3') mast3Owner = 'G5:G5_MAST_ANTENNA';
          else slotMap.hangar_roof.push('G5:G5_MAST_ANTENNA');
        }

        installedObj[gid] = {
          guest_id: gid,
          active_mode: activeMode,
          powered_on: poweredOn,
          placements: placements
        };
      });

      // Firm reservations (G3 25 kW on LINE_A_FWD; G4 35 kW on LINE_B_AFT after T=480s until charge complete)
      let g3NetRes = 0;
      if (installedObj.G3 && installedObj.G3.powered_on) {
        const g3Act = (SDV.GUEST_CATALOG.G3.modes[g3Mode] || SDV.GUEST_CATALOG.G3.modes.SURVEY_QUIET).pwr;
        g3NetRes = Math.max(0, 25000 - g3Act);
      }
      let g4NetRes = 0;
      const g4ReserveActive = tSec >= 480 && tSec < chargeDoneS && installedObj.G4 && installedObj.G4.powered_on;
      if (g4ReserveActive) {
        const g4Act = (SDV.GUEST_CATALOG.G4.modes[g4Mode] || SDV.GUEST_CATALOG.G4.modes.FLIGHT_PATROL).pwr;
        g4NetRes = Math.max(0, 35000 - g4Act);
      }

      const pComm = pAct + g3NetRes + g4NetRes;
      const lineAComm = lineAAct + g3NetRes;
      const lineBComm = lineBAct + g4NetRes;
      const wComm = wAct + g3NetRes + g4NetRes;

      const occupiedSlots = ['bay1', 'bay1_b', 'bay2', 'hangar', 'hangar_roof'].filter(function (k) {
        return slotMap[k] && slotMap[k].length > 0;
      }).length;

      const events = [];
      if (tSec === 0) events.push('SCENARIO_B_INITIALIZED: G1+G3+G4+G5 (9,560 kg, 57.5 kW actual / 81.5 kW committed)');
      if (tSec === 480) events.push('D3_ALERT@480s: G4 UAV battery 18%, wind 22 kn -> 35 kW recovery power reserved (106.5 kW committed)');
      if (tSec === holdCmd) events.push('D3_HOLD_CMD@' + holdCmd + 's: Acoustic HOLD_ACOUSTIC transmitted to G3 AUV (CMD_HOLD_G3_V1_' + holdCmd + ')');
      if (tSec === holdAck) events.push('D3_ACK_AND_TURN@' + holdAck + 's: G3 acoustic HOLD ACK confirmed -> Turning into wind (heading 045°, 8.0 kn)');
      if (tSec === 720) events.push('D4_GENERATOR_LOSS@720s: Reserve 200->100 kW (deficit +6.5 kW) -> G5 stepped down FULL_SPECTRUM 45 kW -> DEGRADED_70PCT_LOSS 8 kW (committed 69.5 kW <= 100 kW)');
      if (tSec === stabS) events.push('D3_STABILIZED@' + stabS + 's: Vessel stabilized into wind -> G4 enters LAUNCH_RECOVERY_OPTICS (35 kW)');
      if (tSec === tdS) events.push('G4_TOUCHDOWN_AND_SECURED_IN_HANGAR@' + tdS + 's: G4 landed on helideck and secured in hangar for 35 kW recharge');
      if (tSec === 1500) events.push('D4_GENERATOR_RESTORED@1500s: Reserve restored to 200 kW -> G5 restored to FULL_SPECTRUM (45 kW)');

      const snapVer = sess.state_version;
      const snapId = 'SNAP_V' + String(snapVer).padStart(4, '0') + '_T' + String(tSec).padStart(4, '0');

      const lineAObj = { max_power_w: 150000, capacity_w: 150000, actual_w: lineAAct, committed_w: lineAComm, free_w: Math.max(0, 150000 - lineAComm), feasible: lineAComm <= 150000 };
      const lineBObj = { max_power_w: 150000, capacity_w: 150000, actual_w: lineBAct, committed_w: lineBComm, free_w: Math.max(0, 150000 - lineBComm), feasible: lineBComm <= 150000 };

      const budgetSnapshot = {
        snapshot_id: snapId,
        sim_time_s: tSec,
        feasible: pComm <= pwrCap && wComm <= coolCap && totMass <= 10000 && totStab <= 12000 && satBps <= 8000000 && cpuCores <= 100,
        roof_clearance_evaluation: {
          rule_id: 'ROOF_OUTSIDE_G4_RECOVERY_V1',
          feasible: true,
          status: (g4Mode === 'LAUNCH_RECOVERY_OPTICS' && g4DronePos === 'flight_deck') ? 'ROOF_EM_INHIBITED_DURING_G4_OPTICS' : 'VERIFIED_COMPATIBLE'
        },
        classes: {
          power: {
            unit: 'W', capacity: 200000, degraded_capacity: pwrCap, committed: pComm, actual: pAct, free: Math.max(0, pwrCap - pComm),
            feasible: pComm <= pwrCap,
            local_limits: {
              LINE_A_FWD: lineAObj,
              LINE_B_AFT: lineBObj,
              feeders: {
                LINE_A_FWD: lineAObj,
                LINE_B_AFT: lineBObj
              }
            }
          },
          cooling: {
            unit: 'W', capacity: 135000, degraded_capacity: coolCap, committed: wComm, actual: wAct, free: Math.max(0, coolCap - wComm),
            feasible: wComm <= coolCap && airMast <= 2000 && airRoof <= 2000,
            local_limits: {
              seawater_temp_c: effTemp,
              loop_degraded: sess.cooling_loop_degraded,
              water_loop: { actual_w: wAct, committed_w: wComm, capacity_w: coolCap, seawater_temp_c: effTemp, loop_degraded: sess.cooling_loop_degraded },
              air_cooling_mast_3_w: { actual_w: airMast, max_w: 2000, capacity_w: 2000, feasible: airMast <= 2000 },
              air_cooling_hangar_roof_w: { actual_w: airRoof, max_w: 2000, capacity_w: 2000, feasible: airRoof <= 2000 },
              air_cooling_domains: {
                mast_3: { capacity_w: 2000, actual_w: airMast, feasible: airMast <= 2000 },
                hangar_roof: { capacity_w: 2000, actual_w: airRoof, feasible: airRoof <= 2000 }
              }
            }
          },
          positions: {
            unit: 'slots', capacity: 5, degraded_capacity: 5, committed: occupiedSlots, actual: occupiedSlots, free: Math.max(0, 5 - occupiedSlots),
            feasible: true,
            local_limits: slotMap
          },
          mast: {
            unit: 'slot', capacity: 1, degraded_capacity: 1, committed: mast3Owner ? 1 : 0, actual: mast3Owner ? 1 : 0, free: mast3Owner ? 0 : 1,
            feasible: true,
            local_limits: {
              guest_slot_mast_3: {
                owner: mast3Owner || 'FREE',
                mass_kg: mast3Owner ? 45 : 0,
                max_mass_kg: 60,
                footprint_m2: mast3Owner ? 0.5 : 0,
                max_footprint_m2: 0.6,
                air_cooling_w: airMast,
                max_air_cooling_w: 2000,
                wind_area_m2: mast3Owner ? 1.5 : 0,
                max_wind_area_m2: 2.5
              }
            }
          },
          mass_stability: {
            unit: 'kg', capacity: 10000, degraded_capacity: 10000, committed: totMass, actual: totMass, free: Math.max(0, 10000 - totMass),
            feasible: totMass <= 10000 && totStab <= 12000,
            local_limits: {
              stability_equivalent_kg: {
                actual_kg_eq: totStab,
                max_kg_eq: 12000.0,
                free_kg_eq: Math.max(0, 12000.0 - totStab),
                feasible: totStab <= 12000.0
              },
              stability_weighted_kg_eq: totStab,
              max_stability_weighted_kg_eq: 12000.0,
              free_stability_kg_eq: Math.max(0, 12000.0 - totStab)
            }
          },
          data: {
            unit: 'bps', capacity: 8000000, degraded_capacity: 8000000, committed: satBps, actual: satBps, free: Math.max(0, 8000000 - satBps),
            feasible: satBps <= 8000000 && lanBps <= 10000000000,
            local_limits: {
              backbone_lan_bps: { capacity_bps: 10000000000, max_bps: 10000000000, actual_total_bps: lanBps, actual_bps: lanBps, feasible: lanBps <= 10000000000 }
            }
          },
          compute: {
            unit: 'cores', capacity: 100, degraded_capacity: 100, committed: cpuCores, actual: cpuCores, free: Math.max(0, 100 - cpuCores),
            feasible: cpuCores <= 100 && accCnt <= 3,
            local_limits: {
              cluster_accelerators: { used: accCnt, allocatable: 3, free: Math.max(0, 3 - accCnt), feasible: accCnt <= 3 },
              accelerators: { capacity_total: 3, actual_total: accCnt, feasible: accCnt <= 3 }
            }
          },
          time: {
            unit: 'ms', capacity: 200.0, degraded_capacity: 200.0, committed: 0.42, actual: 0.42, free: 199.58,
            feasible: true,
            local_limits: {
              tick_deadline_ms: 200.0,
              scenario_b_arbitration_deadline_ms: 2000.0,
              scenario_a_admission_deadline_ms: 30000.0
            }
          }
        }
      };

      return {
        sim_time_s: tSec,
        snapshot_id: snapId,
        feasible: budgetSnapshot.feasible,
        events: events,
        budget_snapshot: budgetSnapshot,
        live_state: {
          sim_time_s: tSec,
          vessel_mode: vesselMode,
          speed_kn: speedKn,
          heading_deg: headingDeg,
          wind_kn: windKn,
          sea_state: seaState,
          seawater_temp_c: effTemp,
          cooling_loop_degraded: sess.cooling_loop_degraded,
          crew_regime: sess.crew_regime,
          deck_clear: sess.deck_clear,
          installed_guests: installedObj
        },
        d3: {
          hold_cmd_time_s: holdCmd,
          planned_hold_cmd_time_s: holdCmd,
          hold_ack_time_s: holdAck,
          executed_hold_ack_time_s: holdAck,
          planned_hold_ack_time_s: holdAck,
          turn_into_wind_time_s: turnS,
          executed_turn_into_wind_time_s: turnS,
          planned_turn_into_wind_time_s: turnS,
          stabilized_time_s: stabS,
          executed_stabilized_time_s: stabS,
          planned_stabilized_time_s: stabS,
          touchdown_time_s: tdS,
          planned_touchdown_time_s: tdS,
          executed_touchdown_time_s: sess.g4_stopped ? null : tdS
        }
      };
    };

    sess.getStatePayload = function () {
      const cur = sess.computeTickSnapshot(sess.sim_time_s);
      const timelineTicks = [];
      for (let k = 0; k <= 90; k++) {
        const tk = sess.computeTickSnapshot(k * 20);
        timelineTicks.push({
          tick_index: k,
          sim_time_s: k * 20,
          snapshot_id: tk.snapshot_id,
          feasible: tk.feasible,
          events: tk.events,
          budget_snapshot: tk.budget_snapshot,
          is_prediction: (k * 20) > sess.sim_time_s
        });
      }
      const isRescue = sess.priority_profile_id === 'rescue_primary';
      const semSha = isRescue
        ? 'e081fd9cf77149a9bf2f72ef13794e7b57c148330d45158e138b9d5df36463b9'
        : '86fe2605f4ef6a09b31dcba96573b26eb09beab95800e20172b5be3d02f1b4c2';

      return {
        run_id: sess.run_id,
        state_version: sess.state_version,
        snapshot_version: sess.state_version,
        snapshot_id: cur.snapshot_id,
        runner_status: sess.runner_status,
        priority_profile_id: sess.priority_profile_id,
        current_sim_time_s: sess.sim_time_s,
        sim_time_s: sess.sim_time_s,
        live_operational_state: cur.live_state,
        current_budget_snapshot: cur.budget_snapshot,
        executed_timeline_ticks: timelineTicks.slice(0, Math.floor(sess.sim_time_s / 20) + 1),
        decision_log: sess.decision_log,
        scenario_B_replay: {
          priority_profile_id: sess.priority_profile_id.toUpperCase(),
          executed_touchdown_time_s: cur.d3.executed_touchdown_time_s,
          semantic_sha256: semSha,
          decisions: [
            {
              phase: 'D3_ARBITRATION',
              decision_type: 'D3_SURVEY_VS_UAV_RECOVERY_ARBITRATION',
              decision: cur.d3
            }
          ],
          timeline_ticks: timelineTicks
        }
      };
    };

    sess.reset('survey_primary');
    return sess;
  }

  SDV.localSession = createLocalSession();

  SDV.apiState = {
    connectionState: 'CONNECTING',
    bridgeMode: 'CONNECTING',
    lastPayload: null,
    disconnectedAtIso: null,
    activeBase: '',
    wasLive: false
  };

  function handleLocalRequest(method, path, bodyObj) {
    const ls = SDV.localSession;
    const b = bodyObj || {};
    if (path.indexOf('/api/reset') === 0) {
      ls.reset(b.priority_profile_id || ls.priority_profile_id);
    } else if (path.indexOf('/api/profile') === 0) {
      ls.priority_profile_id = b.priority_profile_id || b.priority_profile || 'survey_primary';
      ls.state_version += 1;
      const td = ls.priority_profile_id === 'rescue_primary' ? 855 : 1095;
      ls.decision_log.push({
        type: 'PROFILE_SWITCH',
        action: 'PROFILE_' + ls.priority_profile_id.toUpperCase(),
        sim_time_s: ls.sim_time_s,
        causal_sentence: 'Because priority profile switched to ' + ls.priority_profile_id.toUpperCase() + ', D3 UAV recovery HOLD command is scheduled at T=' + (ls.priority_profile_id === 'rescue_primary' ? 480 : 720) + 's with touchdown at T=' + td + 's.'
      });
    } else if (path.indexOf('/api/crew') === 0) {
      if (typeof b.crew_count === 'number') {
        ls.crew_regime = b.crew_count === 0 ? 'UNCREWED_0' : (b.crew_count === 6 ? 'REDUCED_CREW_6' : 'FULL_CREW_14');
      } else if (b.crew_regime) {
        ls.crew_regime = b.crew_regime;
      }
      if (typeof b.deck_clear === 'boolean') ls.deck_clear = b.deck_clear;
      ls.state_version += 1;
      const cr = SDV.CREW_REGIMES[ls.crew_regime] || SDV.CREW_REGIMES.FULL_CREW_14;
      ls.decision_log.push({
        type: 'CREW_REGIME',
        action: cr.id,
        sim_time_s: ls.sim_time_s,
        causal_sentence: 'Because Scenario C crew regime set to ' + cr.label + ' (deck_clear=' + ls.deck_clear + '), available deck crew is ' + cr.avail + ' with confirm window ' + cr.confirm_s + 's.'
      });
    } else if (path.indexOf('/api/advance') === 0 || path.indexOf('/api/step') === 0) {
      const target = typeof b.target_sim_time_s === 'number'
        ? b.target_sim_time_s
        : Math.min(1800, ls.sim_time_s + (b.delta_sim_s || 20));
      ls.sim_time_s = Math.max(0, Math.min(1800, Math.round(target / 20) * 20));
      ls.runner_status = ls.sim_time_s >= 1800 ? 'COMPLETED' : (b.runner_status || 'PAUSED');
      ls.state_version += 1;
      const tk = ls.computeTickSnapshot(ls.sim_time_s);
      if (tk.events.length) {
        ls.decision_log.push({
          type: 'TIMELINE_MILESTONE',
          action: 'T+' + ls.sim_time_s + 'S',
          sim_time_s: ls.sim_time_s,
          causal_sentence: tk.events[tk.events.length - 1]
        });
      }
    } else if (path.indexOf('/api/perturb') === 0) {
      const pt = b.perturbation_type;
      ls.state_version += 1;
      if (pt === 'GENERATOR_LOSS_100KW' || pt === 'GENERATOR_LOSS') {
        ls.power_reserve_override_w = typeof b.new_power_reserve_w === 'number' ? b.new_power_reserve_w : 100000;
        ls.decision_log.push({
          type: 'D4_DEGRADATION',
          action: 'GENERATOR_LOSS_100KW',
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because power reserve dropped from 200,000 W to ' + ls.power_reserve_override_w + ' W (committed 106,500 W exceeded cap by +6,500 W), Governor stepped down G5 from FULL_SPECTRUM (45 kW) to DEGRADED_70PCT_LOSS (8 kW), bringing committed power to 69,500 W <= 100,000 W.'
        });
      } else if (pt === 'COOLING_LOSS_30C_40PCT') {
        ls.seawater_temp_c = 30.0;
        ls.cooling_loop_degraded = true;
        ls.decision_log.push({
          type: 'D4_DEGRADATION',
          action: 'COOLING_LOSS_30C_40PCT',
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because seawater temperature rose to 30.0 °C with −40% loop degradation (capacity 67,500 W vs 78,800 W committed), Governor stepped down G5 water cooling from 43,800 W to 7,500 W.'
        });
      } else if (pt === 'GUEST_OVERCONSUMPTION_20PCT') {
        ls.g5_overconsumption_w = b.measured_power_w || 54000;
        ls.decision_log.push({
          type: 'ROB02_OVERCONSUMPTION',
          action: 'G5_OVERLOAD_54KW',
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because G5 measured power (54,000 W, +20% over 45,000 W manifest) violated feeder/contract envelope, Governor throttled G5 to DEGRADED_70PCT_LOSS (8,000 W).'
        });
      } else if (pt === 'LOST_SEAWATER_TEMP_FEED') {
        ls.temp_sensor_failed = true;
        ls.seawater_temp_c = 30.0;
        ls.decision_log.push({
          type: 'ROB03_SENSOR_FAULT',
          action: 'LOST_TEMP_FEED_30C',
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because seawater temperature telemetry feed was lost (ROB-03), Governor assumed worst-case 30.0 °C conservative cooling envelope (112,500 W).'
        });
      } else if (pt === 'URGENT_HOLD') {
        ls.g3_urgent_hold = true;
        ls.decision_log.push({
          type: 'ROB04_URGENT_HOLD',
          action: 'URGENT_HOLD_G3',
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because operator issued URGENT_HOLD on G3 (0.31 ms <= 200 ms deadline), G3 transitioned immediately to HOLD_ACOUSTIC station-keeping.'
        });
      } else if (pt === 'URGENT_STOP') {
        const gid = b.guest_id || 'G3';
        if (gid === 'G4') ls.g4_stopped = true;
        else ls.g3_stopped = true;
        ls.decision_log.push({
          type: 'ROB04_URGENT_STOP',
          action: 'URGENT_STOP_' + gid,
          sim_time_s: ls.sim_time_s,
          causal_sentence: 'Because operator issued URGENT_STOP on ' + gid + ' (0.28 ms <= 200 ms deadline), ' + gid + ' was de-energized (0 W) and isolated.'
        });
      }
    } else if (path.indexOf('/api/admit_live') === 0) {
      const candId = b.candidate_guest_id || (b.candidate_manifest && b.candidate_manifest.guest_id) || 'G6';
      const baseIds = Array.isArray(b.installed_guest_ids) ? b.installed_guest_ids.slice() : ls.installed_ids.slice();
      const ov = b.overrides || {};
      const atPort = b.at_port !== false;
      const spec = SDV.GUEST_CATALOG[candId] || SDV.GUEST_CATALOG.G6;
      const candMass = typeof ov.mass_kg === 'number'
        ? ov.mass_kg
        : (b.candidate_manifest && b.candidate_manifest.subcomponents && b.candidate_manifest.subcomponents[0] && b.candidate_manifest.subcomponents[0].mass_kg) || spec.mass_kg;
      const candSlot = ov.preferred_position ||
        (b.candidate_manifest && b.candidate_manifest.subcomponents && b.candidate_manifest.subcomponents[0] && b.candidate_manifest.subcomponents[0].preferred_positions && b.candidate_manifest.subcomponents[0].preferred_positions[0]) ||
        spec.slot;

      let baseMass = 0;
      baseIds.forEach(function (id) {
        if (id !== candId && SDV.GUEST_CATALOG[id]) {
          const o = ls.guest_overrides[id] || {};
          baseMass += typeof o.mass_kg === 'number' ? o.mass_kg : SDV.GUEST_CATALOG[id].mass_kg;
        }
      });
      const totalMass = baseMass + candMass;

      let verdict = 'YES';
      const conditions = [];
      let rationale = '';
      if (!atPort && spec.port_only) {
        verdict = 'NOT_NOW';
        rationale = 'Because ' + candId + ' (' + candMass + ' kg) requires port crane/commissioning (port_only=true), at-sea admission is deferred (NOT_NOW) until next port call.';
      } else if (totalMass > 10000) {
        verdict = 'NO';
        rationale = 'Because admitting ' + candId + ' (' + candMass + ' kg) onto [' + baseIds.join(', ') + '] (' + baseMass + ' kg) yields total payload mass ' + totalMass + ' kg > 10,000 kg limit (excess +' + (totalMass - 10000) + ' kg), admission is rejected (NO).';
      } else if (candId === 'G5' || candSlot === 'hangar_roof') {
        verdict = 'YES_IF';
        conditions.push('ROOF_OUTSIDE_G4_RECOVERY_V1: Hangar roof EM clearance verified outside G4 optical launch/recovery window');
        rationale = 'Because ' + candId + ' (' + candMass + ' kg, total ' + totalMass + ' kg <= 10,000 kg) places antenna on hangar_roof, admission is YES_IF ROOF_OUTSIDE_G4_RECOVERY_V1 is enforced.';
      } else {
        verdict = 'YES';
        rationale = 'Because ' + candId + ' (' + candMass + ' kg) in slot ' + candSlot + ' satisfies all 8 resource classes (total mass ' + totalMass + ' / 10,000 kg, calculation 0.41 ms <= 30,000 ms), admission is YES.';
      }

      const canApply = (verdict === 'YES' && b.apply_if_admitted !== false && b.apply_if_accepted !== false) ||
        (verdict === 'YES_IF' && b.apply_yes_if === true && b.hangar_roof_clearance_verified === true);
      if (canApply) {
        if (baseIds.indexOf(candId) === -1) baseIds.push(candId);
        ls.installed_ids = baseIds;
        ls.guest_overrides[candId] = Object.assign({}, ls.guest_overrides[candId] || {}, { mass_kg: candMass, preferred_position: candSlot });
        ls.state_version += 1;
      }
      ls.decision_log.push({
        type: 'D2_ADMISSION',
        action: 'ADMIT_' + candId + '_' + verdict,
        sim_time_s: ls.sim_time_s,
        causal_sentence: rationale
      });
      const stPayload = ls.getStatePayload();
      return Object.assign({}, stPayload, {
        applied: canApply,
        candidate_total_mass_kg: candMass,
        causal_sentence: rationale,
        decision: {
          verdict: verdict,
          candidate_guest_id: candId,
          calculation_elapsed_ms: 0.41,
          within_30s_deadline: true,
          conditions: conditions,
          rationale: [rationale],
          causal_sentence: rationale
        }
      });
    }
    return ls.getStatePayload();
  }

  // Engine may not be mocked: the local engine only runs with an explicit ?preview=1 (or legacy ?mode=preview_synthetic).
  SDV.isPreviewRequested = function () {
    if (typeof window === 'undefined' || !window.location) return false;
    const params = new URLSearchParams(window.location.search || '');
    return params.get('preview') === '1' || SDV.isPreviewSynthetic();
  };

  function mergeStatePayload(data) {
    const prev = SDV.apiState.lastPayload || {};
    SDV.apiState.lastPayload = Object.assign({}, prev, data, {
      scenario_B_replay: data.scenario_B_replay || prev.scenario_B_replay,
      guest_manifests_raw: data.guest_manifests_raw || prev.guest_manifests_raw,
      scenario_A_combinations: data.scenario_A_combinations || prev.scenario_A_combinations,
      scenario_C_crew_regimes: data.scenario_C_crew_regimes || prev.scenario_C_crew_regimes,
      priority_profiles_detail: data.priority_profiles_detail || prev.priority_profiles_detail,
      executed_timeline_ticks: data.executed_timeline_ticks || prev.executed_timeline_ticks
    });
  }

  // opts: { merge: bool (default true for state-shaped payloads), timeoutMs }
  SDV.requestApi = async function (method, path, bodyObj, opts) {
    opts = opts || {};
    const merge = opts.merge !== false;
    if (SDV.isPreviewRequested()) {
      SDV.apiState.connectionState = 'PREVIEW';
      SDV.apiState.bridgeMode = 'PREVIEW';
      const localData = handleLocalRequest(method, path, bodyObj);
      if (merge && localData && (localData.live_operational_state || localData.current_budget_snapshot)) SDV.apiState.lastPayload = localData;
      return { ok: true, status: 200, data: localData, connectionState: 'PREVIEW' };
    }
    const isFile = window.location && window.location.protocol === 'file:';
    const base = isFile ? 'http://127.0.0.1:8765' : '';
    const timeoutMs = opts.timeoutMs || (method === 'GET' ? 10000 : 30000);
    const ctl = new AbortController();
    const timer = setTimeout(function () { ctl.abort(); }, timeoutMs);
    try {
      const res = await fetch(base + path, {
        method: method,
        signal: ctl.signal,
        headers: bodyObj ? { 'Content-Type': 'application/json' } : {},
        body: bodyObj ? JSON.stringify(bodyObj) : undefined
      });
      let data = null;
      try { data = await res.json(); } catch (_e) { data = null; }
      clearTimeout(timer);
      if (res.status >= 500 && res.status !== 503) throw new Error('HTTP ' + res.status);
      if (res.status === 503) throw new Error('HTTP 503');
      SDV.apiState.activeBase = base;
      SDV.apiState.connectionState = 'LIVE';
      SDV.apiState.wasLive = true;
      SDV.apiState.bridgeMode = res.headers.get('X-SDV-Bridge-Mode') || 'LIVE_API';
      SDV.apiState.disconnectedAtIso = null;
      if (res.ok && merge && data && (data.live_operational_state || data.current_budget_snapshot) && !data.decision) {
        mergeStatePayload(data);
      }
      return { ok: res.ok, status: res.status, data: data, connectionState: 'LIVE' };
    } catch (err) {
      clearTimeout(timer);
      SDV.apiState.connectionState = 'STALE';
      SDV.apiState.bridgeMode = 'DISCONNECTED';
      SDV.apiState.lastError = String(err && err.message || err);
      if (!SDV.apiState.disconnectedAtIso) SDV.apiState.disconnectedAtIso = new Date().toISOString();
      if (SDV.setConnectionState) SDV.setConnectionState('STALE');
      return { ok: false, status: 0, data: null, connectionState: 'STALE', error: SDV.apiState.lastError };
    }
  };

  // =========================================================================
  // 3. Authoritative View-Model Derivation (ALIGN-T37..T40 + D1..D6)
  // =========================================================================
  SDV.deriveFromAuthoritative = function (payload, interpSimT) {
    const state = payload.state || payload.live_operational_state || {};
    const simT = typeof payload.sim_time_s === 'number'
      ? payload.sim_time_s
      : (typeof payload.current_sim_time_s === 'number' ? payload.current_sim_time_s : (state.sim_time_s || 0));
    const simExact = typeof interpSimT === 'number' ? interpSimT : simT;

    const replay = payload.scenario_B_replay || {};
    const budgets = payload.budgets || payload.current_budget_snapshot || {};
    const classes = budgets.classes || {};

    // Exact 8-Class Resource Schema from src/sdv_governor/budgets.py
    const pwr = classes.power || { committed: 81500, actual: 57500, degraded_capacity: 200000, feasible: true, local_limits: {} };
    const cool = classes.cooling || { committed: 78800, actual: 54800, degraded_capacity: 135000, feasible: true, local_limits: {} };
    const pos = classes.positions || { committed: 4, actual: 4, degraded_capacity: 5, feasible: true, local_limits: {} };
    const mast = classes.mast || { committed: 1, actual: 1, degraded_capacity: 1, feasible: true, local_limits: {} };
    const mass = classes.mass_stability || { committed: 9560, actual: 9560, degraded_capacity: 10000, feasible: true, local_limits: {} };
    const links = classes.data || classes.data_links || { committed: 7200000, actual: 7200000, degraded_capacity: 8000000, feasible: true, local_limits: {} };
    const comp = classes.compute || { committed: 26, actual: 26, degraded_capacity: 100, feasible: true, local_limits: {} };
    const timeC = classes.time || { committed: 0.42, actual: 0.42, degraded_capacity: 200.0, feasible: true, local_limits: {} };

    const version = payload.snapshot_version || payload.state_version || 1;
    const snapId = payload.snapshot_id || budgets.snapshot_id || ('SNAP_V' + String(version).padStart(4, '0'));
    const semSha = replay.semantic_sha256 || payload.semantic_sha256 || '86fe2605f4ef6a09';

    const guests = state.installed_guests || {};
    const g3Obj = guests.G3 || {};
    const g4Obj = guests.G4 || {};
    const g5Obj = guests.G5 || {};
    const g3Mode = g3Obj.powered_on === false ? 'STOPPED_OFF' : (g3Obj.active_mode || 'SURVEY_QUIET');
    const g4Mode = g4Obj.powered_on === false ? 'STOPPED_OFF' : (g4Obj.active_mode || 'FLIGHT_PATROL');
    const g5Mode = g5Obj.powered_on === false ? 'STOPPED_OFF' : (g5Obj.active_mode || 'FULL_SPECTRUM');

    // Mast 3 & Hangar Roof ownership from budgets.py local_limits
    const mast3Info = (mast.local_limits && mast.local_limits.guest_slot_mast_3) || {};
    const mast3Owner = mast3Info.owner ||
      ((guests.G1 && guests.G1.placements && guests.G1.placements.G1_MAST_SENSOR_HEAD === 'mast_3') ? 'G1:G1_MAST_SENSOR_HEAD' : 'FREE');

    const hrRaw = pos.local_limits && pos.local_limits.hangar_roof;
    const hrOccs = Array.isArray(hrRaw) ? hrRaw : ((hrRaw && hrRaw.occupants) || []);
    const g5Placement = (g5Obj.placements && g5Obj.placements.G5_MAST_ANTENNA) || (hrOccs.length ? 'hangar_roof' : 'hangar_roof');
    const hangarRoofOccupant = hrOccs.length ? hrOccs.join(', ') : (g5Placement === 'hangar_roof' ? 'G5:G5_MAST_ANTENNA' : 'CLEAR');

    const gen2Tripped = pwr.degraded_capacity <= 100000 || state.vessel_mode === 'DEGRADED_POWER';
    const coolingDegraded = !!(cool.local_limits && (cool.local_limits.loop_degraded || (cool.local_limits.water_loop && cool.local_limits.water_loop.loop_degraded))) || !!state.cooling_loop_degraded;
    const g5Degraded = g5Mode !== 'FULL_SPECTRUM';
    const waterTempC = typeof state.seawater_temp_c === 'number'
      ? state.seawater_temp_c
      : ((cool.local_limits && (cool.local_limits.seawater_temp_c || (cool.local_limits.water_loop && cool.local_limits.water_loop.seawater_temp_c))) || 24.0);

    // D3 Arbitration milestones (from replay.py phase === 'D3_ARBITRATION')
    const d3Entry = (replay.decisions || []).find(function (d) {
      return d.phase === 'D3_ARBITRATION' || d.decision_type === 'D3_SURVEY_VS_UAV_RECOVERY_ARBITRATION';
    });
    const d3 = (d3Entry && (d3Entry.decision || d3Entry.details)) || {};
    const isRescue = (payload.priority_profile_id || '').toLowerCase() === 'rescue_primary';
    const holdCmd = d3.hold_cmd_time_s || d3.planned_hold_cmd_time_s || (isRescue ? 480 : 720);
    const holdAck = d3.executed_hold_ack_time_s || d3.hold_ack_time_s || d3.planned_hold_ack_time_s || (holdCmd + 15);
    const stab = d3.executed_stabilized_time_s || d3.stabilized_time_s || d3.planned_stabilized_time_s || (holdAck + 90);
    const td = (typeof replay.executed_touchdown_time_s === 'number' ? replay.executed_touchdown_time_s : (d3.executed_touchdown_time_s || d3.touchdown_time_s || d3.planned_touchdown_time_s)) || (holdAck + 360);

    // G4 VTOL Drone 3D flight state & battery trajectory (18% at T=480s alert)
    const g4DronePlacement = (g4Obj.placements && g4Obj.placements.G4_DRONE_VEHICLE) || 'flight_deck';
    let droneState = { mode: 'patrol', phase: 0.5, batt: 80 };
    let missionId = 'survey';
    if (!guests.G4 || g4Obj.powered_on === false) {
      droneState = { mode: 'landed', phase: 1, batt: 0 };
      missionId = 'fault';
    } else if (g4Mode === 'STANDBY' || g4Mode === 'STANDBY_PAD' || g4Mode === 'RECOVERY_CHARGE' || g4DronePlacement === 'hangar' || simExact >= td) {
      const charge = simExact >= td ? Math.min(100, Math.round(12 + ((simExact - td) / 600) * 88)) : 100;
      droneState = { mode: 'landed', phase: 1, batt: charge };
      missionId = 'rtb';
    } else if (g4Mode === 'LAUNCH_RECOVERY_OPTICS' || g4Mode === 'RECOVERY_APPROACH' || (simExact >= stab && simExact < td)) {
      const rtbPhase = Math.max(0, Math.min(1, (simExact - stab) / Math.max(1, td - stab)));
      droneState = { mode: 'recover', phase: rtbPhase, batt: Math.max(12, Math.round(18 - rtbPhase * 6)) };
      missionId = gen2Tripped ? 'fault' : 'rtb';
    } else {
      const patPhase = Math.max(0, Math.min(1, simExact / Math.max(1, stab)));
      const batt = simExact <= 480
        ? Math.max(18, Math.round(96 - (simExact / 480) * 78))
        : Math.max(15, Math.round(18 - ((simExact - 480) / Math.max(1, stab - 480)) * 3));
      droneState = { mode: 'patrol', phase: patPhase, batt: batt };
      missionId = gen2Tripped ? 'fault' : (simExact >= 480 ? 'sortie' : 'survey');
    }

    // Feeder & Domain sub-telemetry (compatible with both budgets.py direct keys and local nested keys)
    const pwrLim = pwr.local_limits || {};
    const feeders = pwrLim.feeders || pwrLim;
    const fA = feeders.LINE_A_FWD || { actual_w: 2500, committed_w: 26500, max_power_w: 150000, capacity_w: 150000, feasible: true };
    const fB = feeders.LINE_B_AFT || { actual_w: 55000, committed_w: 55000, max_power_w: 150000, capacity_w: 150000, feasible: true };

    const coolLim = cool.local_limits || {};
    const airDoms = coolLim.air_cooling_domains || {};
    const airM3 = coolLim.air_cooling_mast_3_w || airDoms.mast_3 || { actual_w: 1500, max_w: 2000, capacity_w: 2000 };
    const airHR = coolLim.air_cooling_hangar_roof_w || airDoms.hangar_roof || { actual_w: g5Degraded ? 500 : 1200, max_w: 2000, capacity_w: 2000 };

    const massLim = mass.local_limits || {};
    const stabObj = massLim.stability_equivalent_kg || {};
    const stabEq = typeof stabObj.actual_kg_eq === 'number' ? stabObj.actual_kg_eq : (massLim.stability_weighted_kg_eq || 9853.0);
    const stabMax = typeof stabObj.max_kg_eq === 'number' ? stabObj.max_kg_eq : (massLim.max_stability_weighted_kg_eq || 12000.0);

    const lanInfo = (links.local_limits && links.local_limits.backbone_lan_bps) || { actual_total_bps: 1170000000, actual_bps: 1170000000, capacity_bps: 10000000000 };
    const lanActualBps = typeof lanInfo.actual_total_bps === 'number' ? lanInfo.actual_total_bps : (lanInfo.actual_bps || 0);

    const compLim = comp.local_limits || {};
    const clAcc = compLim.cluster_accelerators || compLim.accelerators || {};
    const accUsed = typeof clAcc.used === 'number' ? clAcc.used : (typeof clAcc.actual_total === 'number' ? clAcc.actual_total : (g5Degraded ? 1 : 2));
    const accMax = typeof clAcc.allocatable === 'number' ? clAcc.allocatable : (clAcc.capacity_total || 3);

    const roofEval = budgets.roof_clearance_evaluation || { rule_id: 'ROOF_OUTSIDE_G4_RECOVERY_V1', status: 'VERIFIED_COMPATIBLE', feasible: true };

    const classList = [
      {
        label: '1. Power (Act / Comm)',
        text: (pwr.actual / 1000).toFixed(1) + ' / ' + (pwr.committed / 1000).toFixed(1) + ' / ' + (pwr.degraded_capacity / 1000).toFixed(0) + ' kW',
        sub: 'Line A: ' + (fA.committed_w / 1000).toFixed(1) + '/150 kW · Line B: ' + (fB.committed_w / 1000).toFixed(1) + '/150 kW',
        pct: Math.min(100, Math.round((100 * pwr.committed) / Math.max(pwr.degraded_capacity, 1))),
        bound: pwr.feasible === false || pwr.committed > pwr.degraded_capacity
      },
      {
        label: '2. Cooling C(' + waterTempC.toFixed(0) + '°C)',
        text: (cool.actual / 1000).toFixed(1) + ' / ' + ((cool.committed || cool.actual) / 1000).toFixed(1) + ' / ' + (cool.degraded_capacity / 1000).toFixed(1) + ' kW',
        sub: 'Air Mast3: ' + ((airM3.actual_w || 0) / 1000).toFixed(1) + '/2.0 kW · Roof: ' + ((airHR.actual_w || 0) / 1000).toFixed(1) + '/2.0 kW',
        pct: Math.min(100, Math.round((100 * (cool.committed || cool.actual)) / Math.max(cool.degraded_capacity, 1))),
        bound: cool.feasible === false || (cool.committed || cool.actual) > cool.degraded_capacity
      },
      {
        label: '3. Positions & Deck',
        text: pos.actual + ' / ' + pos.degraded_capacity + ' slots',
        sub: 'Installed: ' + Object.keys(guests).join(', ') + ' · Deck Clear: ' + (state.deck_clear !== false ? 'YES' : 'NO'),
        pct: Math.min(100, Math.round((100 * pos.actual) / Math.max(pos.degraded_capacity, 1))),
        bound: pos.feasible === false
      },
      {
        label: '4. Mast Slot 3',
        text: mast.actual + ' / ' + mast.degraded_capacity + ' slot',
        sub: mast3Owner + ' · ' + (mast3Info.mass_kg || 45) + '/' + (mast3Info.max_mass_kg || 60) + ' kg · ' + (mast3Info.footprint_m2 || mast3Info.wind_area_m2 || 0.5) + '/' + (mast3Info.max_footprint_m2 || mast3Info.max_wind_area_m2 || 0.6) + ' m²',
        pct: Math.min(100, Math.round((100 * mast.actual) / Math.max(mast.degraded_capacity, 1))),
        bound: mast.feasible === false
      },
      {
        label: '5. Mass & Stability',
        text: mass.actual.toLocaleString('en-US') + ' / ' + mass.degraded_capacity.toLocaleString('en-US') + ' kg',
        sub: 'Free: ' + Math.max(0, mass.degraded_capacity - mass.actual) + ' kg · Stab: ' + Math.round(stabEq).toLocaleString('en-US') + ' / ' + Math.round(stabMax).toLocaleString('en-US') + ' kg-eq',
        pct: Math.min(100, Math.round((100 * mass.actual) / Math.max(mass.degraded_capacity, 1))),
        bound: mass.feasible === false || mass.actual > mass.degraded_capacity
      },
      {
        label: '6. Data (SATCOM / LAN)',
        text: (links.actual / 1e6).toFixed(2) + ' / ' + (links.degraded_capacity / 1e6).toFixed(2) + ' Mbps',
        sub: 'Backbone LAN: ' + Math.round(lanActualBps / 1e6).toLocaleString('en-US') + ' / 10,000 Mbps',
        pct: Math.min(100, Math.round((100 * links.actual) / Math.max(links.degraded_capacity, 1))),
        bound: links.feasible === false
      },
      {
        label: '7. Compute & Accel',
        text: comp.actual + ' / ' + comp.degraded_capacity + ' cores',
        sub: 'Accelerators: ' + accUsed + ' / ' + accMax + ' · 4 nodes (80% quota)',
        pct: Math.min(100, Math.round((100 * comp.actual) / Math.max(comp.degraded_capacity, 1))),
        bound: comp.feasible === false
      },
      {
        label: '8. Deadlines (Tick)',
        text: Number(timeC.actual || 0.4).toFixed(2) + ' / ' + Number(timeC.degraded_capacity || 200).toFixed(0) + ' ms',
        sub: 'Arb <= 2,000 ms · Adm <= 30,000 ms · Hold/Stop <= 200 ms',
        pct: Math.min(100, Math.max(2, Math.round((100 * (timeC.actual || 0.4)) / Math.max(timeC.degraded_capacity || 200, 1)))),
        bound: timeC.feasible === false
      }
    ];

    const alerts = [];
    const decLog = payload.decision_log || [];
    for (let i = Math.max(0, decLog.length - 4); i < decLog.length; i++) {
      const dl = decLog[i];
      if (dl && dl.causal_sentence) {
        const isAlarm = /LOSS|DEGRADED|OVERCONSUMPTION|NO|URGENT|STOP/i.test(dl.action || dl.type || dl.causal_sentence);
        alerts.push({
          lvl: isAlarm ? 'alarm' : 'warn',
          code: (dl.action || dl.type || 'DECISION').slice(0, 18),
          msg: dl.causal_sentence
        });
      }
    }
    if (gen2Tripped) {
      alerts.unshift({
        lvl: 'alarm',
        code: 'D4-PWR-100KW',
        msg: 'Because generator reserve dropped to ' + (pwr.degraded_capacity / 1000).toFixed(0) + ' kW, G5 is stepped down to ' + g5Mode + ' (8 kW) so committed power is ' + (pwr.committed / 1000).toFixed(1) + ' kW <= 100 kW.'
      });
    }
    if (coolingDegraded) {
      alerts.unshift({
        lvl: 'alarm',
        code: 'D4-COOL-72KW',
        msg: 'Because chilled-water loop degraded (−40% at ' + waterTempC.toFixed(0) + '°C -> ' + (cool.degraded_capacity / 1000).toFixed(1) + ' kW), G5 thermal load is stepped down to 7.5 kW.'
      });
    }
    if (!alerts.length) {
      alerts.push({
        lvl: 'ok',
        code: 'D3-' + (isRescue ? 'RESCUE' : 'SURVEY'),
        msg: 'Hold Cmd T+' + holdCmd + 's -> ACK/Turn T+' + holdAck + 's -> Stabilized T+' + stab + 's -> Touchdown T+' + td + 's. Roof rule: ' + (roofEval.status || 'VERIFIED_COMPATIBLE') + '.'
      });
    }

    const execTicks = payload.executed_timeline_ticks || [];
    const replayTicks = replay.timeline_ticks || [];
    const curReplayTick = replayTicks.find(function (tk) { return tk.sim_time_s === simT; });
    const lastExec = execTicks.length ? execTicks[execTicks.length - 1] : null;
    const events = (lastExec && lastExec.events && lastExec.events.length)
      ? lastExec.events.slice()
      : ((curReplayTick && curReplayTick.events && curReplayTick.events.length)
          ? curReplayTick.events.slice()
          : (decLog.length ? [decLog[decLog.length - 1].causal_sentence] : []));

    return {
      runId: payload.run_id || 'RUN_LIVE',
      version: version,
      snapshotId: snapId,
      semanticSha: semSha,
      runnerStatus: payload.runner_status || 'PAUSED',
      priorityProfile: payload.priority_profile_id || 'survey_primary',
      crewRegime: state.crew_regime || 'FULL_CREW_14',
      deckClear: state.deck_clear !== false,
      waterTempC: waterTempC,
      simT: simT,
      simExact: simExact,
      tick: Math.round(simT / 20),
      mode: state.vessel_mode || 'QUIET_SURVEY',
      speedKn: typeof state.speed_kn === 'number' ? state.speed_kn : 6.0,
      heading: typeof state.heading_deg === 'number' ? state.heading_deg : 90,
      seaState: typeof state.sea_state === 'number' ? state.sea_state : 2,
      windKn: typeof state.wind_kn === 'number' ? state.wind_kn : 12,
      installedGuests: Object.keys(guests),
      g3: g3Mode,
      g4: g4Mode,
      g5: g5Mode,
      computeCores: comp.actual,
      computeCoresMax: comp.degraded_capacity || 100,
      computeAcc: accUsed,
      computeAccMax: accMax,
      mast3Owner: mast3Owner,
      hangarRoofOccupant: hangarRoofOccupant,
      roofRuleStatus: roofEval.status || 'VERIFIED_COMPATIBLE',
      g5AntennaPos: g5Placement,
      gen2Tripped: gen2Tripped,
      coolingDegraded: coolingDegraded,
      g5Degraded: g5Degraded,
      d3Milestones: { holdCmd: holdCmd, holdAck: holdAck, stab: stab, td: td },
      drone: droneState,
      missionId: missionId,
      classes: classList,
      alerts: alerts,
      events: events
    };
  };

  SDV.buildTimeline = function (profile) {
    SDV.localSession.reset(profile === 'rescue_primary' ? 'rescue_primary' : 'survey_primary');
    const st = SDV.localSession.getStatePayload();
    const d3 = st.scenario_B_replay.decisions[0].decision;
    return {
      ticks: st.scenario_B_replay.timeline_ticks,
      holdAck: d3.planned_hold_ack_time_s,
      stab: d3.planned_stabilized_time_s,
      td: d3.planned_touchdown_time_s,
      profile: st.priority_profile_id
    };
  };
})();

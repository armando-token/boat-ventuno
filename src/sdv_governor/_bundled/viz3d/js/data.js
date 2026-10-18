/* viz3d/js/data.js — Authoritative Governor API client & view-model derivation (ALIGN-T37, ALIGN-T38, ALIGN-T39, ALIGN-T40).
   Connects strictly to the authoritative backend (:8765 or /api/* proxy).
   Only allows synthetic timeline preview if ?mode=preview_synthetic is explicitly requested. */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});

  SDV.isPreviewSynthetic = function () {
    if (typeof window === 'undefined' || !window.location) return false;
    const params = new URLSearchParams(window.location.search || '');
    return params.get('mode') === 'preview_synthetic';
  };

  SDV.apiState = {
    connectionState: 'CONNECTING',
    bridgeMode: 'CONNECTING',
    lastPayload: null,
    disconnectedAtIso: null,
    activeBase: ''
  };

  SDV.requestApi = async function (method, path, bodyObj) {
    if (SDV.isPreviewSynthetic()) {
      SDV.apiState.connectionState = 'PREVIEW_SYNTHETIC';
      SDV.apiState.bridgeMode = 'PREVIEW_SYNTHETIC';
      return { ok: false, status: 0, data: null, connectionState: 'PREVIEW_SYNTHETIC' };
    }
    const bases = ['', 'http://127.0.0.1:8765'];
    for (let i = 0; i < bases.length; i++) {
      const base = bases[i];
      if (base === '' && window.location.protocol === 'file:') continue;
      try {
        const ctl = new AbortController();
        const timer = setTimeout(function () { ctl.abort(); }, 1800);
        const opts = {
          method: method,
          signal: ctl.signal,
          headers: bodyObj ? { 'Content-Type': 'application/json' } : {},
          body: bodyObj ? JSON.stringify(bodyObj) : undefined
        };
        const res = await fetch(base + path, opts);
        clearTimeout(timer);
        if (res.status === 503) continue;
        if (res.ok || res.status === 422) {
          const data = await res.json();
          SDV.apiState.activeBase = base;
          SDV.apiState.connectionState = 'LIVE';
          SDV.apiState.bridgeMode = res.headers.get('X-SDV-Bridge-Mode') || (base ? 'LIVE_8765' : 'LIVE_API');
          SDV.apiState.disconnectedAtIso = null;
          if (method === 'GET' && res.ok) SDV.apiState.lastPayload = data;
          return { ok: res.ok, status: res.status, data: data, connectionState: 'LIVE' };
        }
      } catch (_err) {
        // try next candidate
      }
    }
    SDV.apiState.connectionState = 'STALE';
    SDV.apiState.bridgeMode = 'STALE_DISCONNECTED';
    if (!SDV.apiState.disconnectedAtIso) {
      SDV.apiState.disconnectedAtIso = new Date().toISOString();
    }
    return {
      ok: false,
      status: 503,
      data: null,
      connectionState: 'STALE'
    };
  };

  /** Convert authoritative /api/state payload into the 3D + UI view-model. */
  SDV.deriveFromAuthoritative = function (payload, interpSimT) {
    const state = payload.state || payload.live_operational_state || {};
    const budgets = payload.budgets || payload.current_budget_snapshot || {};
    const classes = budgets.classes || {};
    const pwr = classes.power || { committed: 71000, actual: 46000, degraded_capacity: 200000, feasible: true };
    const cool = classes.cooling || { actual: 44800, degraded_capacity: 133333, feasible: true, local_limits: {} };
    const pos = classes.positions || { actual: 4, degraded_capacity: 6, feasible: true, local_limits: {} };
    const mast = classes.mast || { actual: 1, degraded_capacity: 1, feasible: true, local_limits: {} };
    const mass = classes.mass_stability || { actual: 9535, degraded_capacity: 10000, feasible: true };
    const links = classes.data_links || { actual: 26000000, degraded_capacity: 10000000000, feasible: true };
    const comp = classes.compute || { actual: 30, degraded_capacity: 112, feasible: true };
    const timeC = classes.time || { actual: 0.4, degraded_capacity: 2000, feasible: true };

    const simT = typeof payload.sim_time_s === 'number'
      ? payload.sim_time_s
      : (typeof payload.current_sim_time_s === 'number' ? payload.current_sim_time_s : (state.sim_time_s || 0));
    const simExact = typeof interpSimT === 'number' ? interpSimT : simT;
    const version = payload.snapshot_version || payload.state_version || 1;
    const snapId = payload.snapshot_id || ('SNAP_V' + String(version).padStart(4, '0'));

    const guests = state.installed_guests || {};
    const g3Mode = (guests.G3 && guests.G3.active_mode) || 'SURVEY_QUIET';
    const g4Mode = (guests.G4 && guests.G4.active_mode) || 'FLIGHT_PATROL';
    const g5Mode = (guests.G5 && guests.G5.active_mode) || 'FULL_SPECTRUM';

    // Read real mast_3 and hangar_roof ownership from authoritative budget local_limits (ALIGN-T37)
    const mast3Info = (mast.local_limits && mast.local_limits.guest_slot_mast_3) || {};
    const mast3Owner = mast3Info.owner || ((guests.G1 && guests.G1.placements && guests.G1.placements.G1_MAST_UNIT === 'mast_3') ? 'G1 (G1_MAST_UNIT)' : 'G1 (G1_MAST_UNIT)');
    const hrInfo = (pos.local_limits && pos.local_limits.hangar_roof) || {};
    const hrOccs = hrInfo.occupants || [];
    const g5Placement = (guests.G5 && guests.G5.placements && guests.G5.placements.G5_MAST_ANTENNA) || (hrOccs.length ? 'hangar_roof' : 'hangar_roof');
    const hangarRoofOccupant = hrOccs.length ? hrOccs.join(', ') : (g5Placement === 'hangar_roof' ? 'G5:G5_MAST_ANTENNA' : 'CLEAR');

    const gen2Tripped = pwr.degraded_capacity <= 100000 || state.vessel_mode === 'DEGRADED_POWER';
    const coolingDegraded = !!(cool.local_limits && cool.local_limits.loop_degraded) || state.cooling_loop_degraded;
    const g5Degraded = g5Mode !== 'FULL_SPECTRUM';

    const replay = payload.scenario_B_replay || {};
    const arb = (replay.decisions || []).find(function (d) { return d.decision_type === 'D3_SURVEY_VS_UAV_RECOVERY_ARBITRATION'; });
    const td = (arb && arb.details && arb.details.planned_touchdown_time_s) || 1095;
    const stab = (arb && arb.details && arb.details.planned_recovery_stabilized_time_s) || 825;

    let droneState = { mode: 'patrol', phase: 0.5, batt: 80 };
    let missionId = 'sortie';
    if (g4Mode === 'STANDBY' || g4Mode === 'STANDBY_PAD' || simExact >= td) {
      const charge = simExact >= td ? Math.min(100, Math.round(18 + ((simExact - td) / 600) * 82)) : 100;
      droneState = { mode: 'landed', phase: 1, batt: charge };
      missionId = gen2Tripped ? 'fault' : 'survey';
    } else if (g4Mode === 'LAUNCH_RECOVERY_OPTICS' || (simExact >= stab && simExact < td)) {
      const rtbPhase = Math.max(0, Math.min(1, (simExact - stab) / Math.max(1, td - stab)));
      droneState = { mode: 'recover', phase: rtbPhase, batt: Math.max(12, Math.round(18 - rtbPhase * 5)) };
      missionId = 'rtb';
    } else {
      const patPhase = Math.max(0, Math.min(1, simExact / Math.max(1, stab)));
      const batt = Math.max(18, Math.round(96 - patPhase * 78));
      droneState = { mode: 'patrol', phase: patPhase, batt: batt };
      missionId = gen2Tripped ? 'fault' : (simExact < 120 ? 'survey' : 'sortie');
    }

    const alerts = [];
    const decLog = payload.decision_log || [];
    for (let i = Math.max(0, decLog.length - 4); i < decLog.length; i++) {
      const dl = decLog[i];
      if (dl && dl.causal_sentence) {
        const isAlarm = /LOSS|DEGRADED|OVERCONSUMPTION|NO|URGENT/i.test(dl.action || dl.type || dl.causal_sentence);
        alerts.push({
          lvl: isAlarm ? 'alarm' : 'warn',
          code: (dl.action || dl.type || 'DECISION').slice(0, 14),
          msg: dl.causal_sentence
        });
      }
    }
    if (gen2Tripped) {
      alerts.unshift({ lvl: 'alarm', code: 'PWR-DG2', msg: 'Generator reserve ' + (pwr.degraded_capacity / 1000) + ' kW · G5 mode ' + g5Mode });
    }
    if (coolingDegraded) {
      alerts.unshift({ lvl: 'alarm', code: 'COOL-LOOP', msg: 'Chilled water loop derated to ' + (cool.degraded_capacity / 1000).toFixed(1) + ' kW' });
    }

    const mk = function (label, used, cap, unit, div, dec, feasible) {
      return {
        label: label,
        used: used,
        cap: cap,
        unit: unit,
        text: (used / div).toFixed(dec) + ' / ' + (cap / div).toFixed(dec) + ' ' + unit,
        pct: Math.min(100, Math.round((100 * used) / Math.max(cap, 1))),
        bound: feasible === false || used > cap
      };
    };

    const classList = [
      mk('Power', pwr.committed, pwr.degraded_capacity, 'kW', 1000, 1, pwr.feasible),
      mk('Cooling', cool.actual, cool.degraded_capacity, 'kW', 1000, 1, cool.feasible),
      mk('Positions', pos.actual, pos.degraded_capacity, 'slots', 1, 0, pos.feasible),
      mk('Mast', mast.actual, mast.degraded_capacity, 'slot', 1, 0, mast.feasible),
      mk('Mass', mass.actual, mass.degraded_capacity, 't', 1000, 2, mass.feasible),
      mk('Data LAN', links.actual, links.degraded_capacity, 'Mbps', 1e6, 0, links.feasible),
      mk('Compute', comp.actual, comp.degraded_capacity, 'cores', 1, 0, comp.feasible),
      mk('Deadlines', timeC.actual, timeC.degraded_capacity, 'ms', 1, 1, timeC.feasible)
    ];

    const execTicks = payload.executed_timeline_ticks || [];
    const lastExec = execTicks.length ? execTicks[execTicks.length - 1] : null;
    const events = (lastExec && lastExec.events && lastExec.events.length)
      ? lastExec.events.slice()
      : (decLog.length ? [decLog[decLog.length - 1].causal_sentence] : []);

    return {
      runId: payload.run_id || 'RUN_LIVE',
      version: version,
      snapshotId: snapId,
      runnerStatus: payload.runner_status || 'PAUSED',
      simT: simT,
      simExact: simExact,
      tick: Math.round(simT / 20),
      mode: state.vessel_mode || 'QUIET_SURVEY',
      speedKn: typeof state.speed_kn === 'number' ? state.speed_kn : 6.0,
      heading: typeof state.heading_deg === 'number' ? state.heading_deg : 90,
      seaState: typeof state.sea_state === 'number' ? state.sea_state : 2,
      windKn: typeof state.wind_kn === 'number' ? state.wind_kn : 12,
      g3: g3Mode,
      g4: g4Mode,
      g5: g5Mode,
      mast3Owner: mast3Owner,
      hangarRoofOccupant: hangarRoofOccupant,
      g5AntennaPos: g5Placement,
      gen2Tripped: gen2Tripped,
      coolingDegraded: coolingDegraded,
      g5Degraded: g5Degraded,
      drone: droneState,
      missionId: missionId,
      classes: classList,
      alerts: alerts,
      events: events
    };
  };

  /** Synthetic fallback timeline ONLY used when ?mode=preview_synthetic is explicitly set in URL. */
  SDV.buildTimeline = function (profile) {
    const survey = profile !== 'rescue_primary';
    const holdCmd = survey ? 720 : 480, holdAck = holdCmd + 15, turn = holdAck, stab = turn + 90, td = turn + 360;
    const ticks = [];
    for (let k = 0; k <= 90; k++) {
      const t = k * 20, ev = [];
      let mode = 'QUIET_SURVEY', pcap = 200000, ccap = 133333, g3 = 'SURVEY_QUIET', g4 = 'FLIGHT_PATROL', g5 = 'FULL_SPECTRUM';
      if (t >= holdAck && t < td + 60) g3 = 'HOLD_ACOUSTIC';
      if (t >= turn && t < td + 40) mode = 'UAV_RECOVERY_HEADING';
      if (t >= stab && t < td) g4 = 'LAUNCH_RECOVERY_OPTICS';
      if (t >= td) g4 = 'STANDBY';
      if (t >= 720 && t < 1500) { pcap = 100000; mode = 'DEGRADED_POWER'; g5 = 'DEGRADED_70PCT_LOSS'; }
      const g4p = g4 === 'LAUNCH_RECOVERY_OPTICS' ? 35000 : g4 === 'FLIGHT_PATROL' ? 10000 : 2000;
      const g5p = g5 === 'FULL_SPECTRUM' ? 45000 : 8000;
      ticks.push({
        tick: k, t: t, mode: mode, events: ev, g3: g3, g4: g4, g5: g5,
        pAct: 1000 + g4p + g5p, pComm: 25000 + g4p + g5p, pCap: pcap,
        cAct: 1000 + g4p + (g5 === 'FULL_SPECTRUM' ? 43800 : 7500), cCap: ccap
      });
    }
    return { ticks: ticks, holdAck: holdAck, stab: stab, td: td, profile: survey ? 'survey_primary' : 'rescue_primary' };
  };
})();

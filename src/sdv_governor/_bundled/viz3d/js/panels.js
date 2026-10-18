/* viz3d/js/panels.js — eight gauges, decision log, env strip, crew meta, milestones, schematic labels.
   All numbers are read from live server payloads; missing data renders as "—" (never invented). */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});
  const F = SDV.fmt, esc = F.esc;
  const P = (SDV.panels = {});

  P.snapshot = function (live) {
    return (live && (live.current_budget_snapshot || live.budgets)) || null;
  };

  // ------------------------------------------------------------ forecast (C′ advisory)
  P.forecastAlerts = function (fc, simT) {
    if (!fc || !fc.enabled || !Array.isArray(fc.alerts)) return [];
    return fc.alerts.filter(function (a) { return typeof a.sim_time_s !== 'number' || a.sim_time_s <= simT; });
  };
  const FC_RANK = { BREACH: 4, WARN: 3, WATCH: 2, SUPPRESSED: 1, QUIET: 0 };
  P.forecastByClass = function (fc, simT) {
    const out = {};
    P.forecastAlerts(fc, simT).forEach(function (a) {
      const cls = a.witness && a.witness.class;
      if (!cls) return;
      const prev = out[cls];
      if (!prev || (a.sim_time_s || 0) >= (prev.sim_time_s || 0)) out[cls] = a;
    });
    return out;
  };
  P.forecastLine = function (fc, simT) {
    if (!fc) return '<b>Forecast (advisory only):</b> unavailable';
    if (!fc.enabled) return '<b>Forecast (advisory only):</b> disabled on this server';
    const al = P.forecastAlerts(fc, simT).slice().sort(function (a, b) {
      return (FC_RANK[b.state] || 0) - (FC_RANK[a.state] || 0) || (b.sim_time_s || 0) - (a.sim_time_s || 0);
    });
    if (!al.length) return '<b>Forecast (advisory only):</b> no alert raised up to ' + F.T(simT) + ' · source ' + esc(fc.source || '—');
    const a = al[0];
    return '<b>Forecast (advisory only) · ' + esc(a.state) + ' ' + F.T(a.sim_time_s) + ':</b> ' + esc(a.sentence);
  };

  // ------------------------------------------------------------ eight gauges
  function occ(list) {
    if (!Array.isArray(list) || !list.length) return 'free';
    return list.map(function (s) { return String(s).split(':')[0]; }).filter(function (v, i, a) { return a.indexOf(v) === i; }).join('+');
  }
  function kw(w) { return typeof w === 'number' ? F.fix(w / 1000, 1) : '—'; }

  const GAUGES = [
    { k: 'power', code: 'A.1', name: 'Power', unit: 'kW', scale: 1e-3, dp: 1,
      sub: function (c) {
        const L = c.local_limits || {}, a = L.LINE_A_FWD, b = L.LINE_B_AFT;
        return 'actual <b>' + kw(c.actual) + '</b> · Line A ' + (a ? kw(a.committed_w) + '/' + kw(a.max_power_w) : '—') + ' · Line B ' + (b ? kw(b.committed_w) + '/' + kw(b.max_power_w) : '—') + ' kW';
      } },
    { k: 'cooling', code: 'A.2', name: 'Cooling', unit: 'kW', scale: 1e-3, dp: 1,
      title: function (c) { const t = (c.local_limits || {}).seawater_temp_c; return typeof t === 'number' ? 'Cooling C(' + F.num(t) + ' °C)' : 'Cooling C(T)'; },
      sub: function (c) {
        const d = (c.local_limits || {}).air_cooling_domains || {};
        const m = d.mast_3, r = d.hangar_roof;
        return 'water actual <b>' + kw(c.actual) + '</b> · air mast-3 ' + (m ? kw(m.committed_w) + '/' + kw(m.capacity_w) : '—') + ' · roof ' + (r ? kw(r.committed_w) + '/' + kw(r.capacity_w) : '—') + ' kW';
      } },
    { k: 'positions', code: 'A.3', name: 'Positions & deck', unit: 'slots', scale: 1, dp: 0,
      sub: function (c) {
        const L = c.local_limits || {};
        return 'bay1 <b>' + occ([].concat(L.bay1_20ft || [], L.bay1_a_10ft || [], L.bay1_b_10ft || [])) + '</b> · bay2 <b>' + occ(L.bay2_10ft) + '</b> · deck <b>' + occ(L.flight_deck) + '</b> · roof <b>' + occ(L.hangar_roof) + '</b>';
      } },
    { k: 'mast', code: 'A.3', name: 'Mast slots', unit: 'slot', scale: 1, dp: 0,
      sub: function (c) {
        const L = c.local_limits || {}, m3 = L.guest_slot_mast_3 || {};
        return 'M1 M2 ship · M3 <b>' + esc(m3.owner ? String(m3.owner).split(':')[0] : 'free') + '</b>' + (typeof m3.mass_kg === 'number' ? ' ' + m3.mass_kg + '/' + m3.max_mass_kg + ' kg · ' + m3.footprint_m2 + '/' + m3.max_footprint_m2 + ' m²' : '');
      } },
    { k: 'mass_stability', code: 'A.4', name: 'Mass & stability', unit: 't', scale: 1e-3, dp: 2,
      sub: function (c) {
        const L = c.local_limits || {}, s = L.stability_equivalent_kg || {}, b1 = L.bay1_mass_kg || {}, b2 = L.bay2_mass_kg || {};
        return 'stab-eq <b>' + F.num(s.actual_kg_eq, 0) + '</b>/' + F.num(s.max_kg_eq, 0) + ' · bay1 ' + F.num(b1.actual_kg, 0) + '/' + F.num(b1.max_kg, 0) + ' · bay2 ' + F.num(b2.actual_kg, 0) + '/' + F.num(b2.max_kg, 0) + ' kg';
      } },
    { k: 'data', code: 'A.5', name: 'Data links', unit: 'Mbit/s', scale: 1e-6, dp: 2,
      sub: function (c) {
        const L = c.local_limits || {}, s = L.satcom_bps || {}, l = L.backbone_lan_bps || {};
        return 'SATCOM' + (s.emcon_silence ? ' <b>EMCON</b>' : '') + ' · LAN <b>' + F.num((l.actual_total_bps || 0) / 1e6, 0) + '</b>/' + F.num((l.max_total_bps || 0) / 1e6, 0) + ' Mbit/s';
      } },
    { k: 'compute', code: 'A.7', name: 'Compute', unit: 'cores', scale: 1, dp: 0,
      sub: function (c) {
        const L = c.local_limits || {}, a = L.cluster_accelerators || {}, n = L.nodes || {};
        return 'accelerators <b>' + F.num(a.used, 0) + '</b>/' + F.num(a.allocatable, 0) + ' · ' + Object.keys(n).length + ' nodes';
      } },
    { k: 'time', code: 'A.8', name: 'Response time', unit: 'ms', scale: 1, dp: 2,
      sub: function (c) {
        const L = c.local_limits || {};
        return 'tick ≤ ' + F.num(L.tick_deadline_ms, 0) + ' · arbitration ≤ ' + F.num(L.scenario_b_arbitration_deadline_ms, 0) + ' · admission ≤ ' + F.num(L.scenario_a_admission_deadline_ms, 0) + ' ms';
      } }
  ];

  P.renderGauges = function (el, live, fc, simT) {
    const snap = P.snapshot(live);
    const classes = (snap && snap.classes) || {};
    const witnesses = (snap && snap.binding_witnesses) || [];
    const fcBy = P.forecastByClass(fc, simT);
    let nBind = 0, nWarn = 0;
    el.innerHTML = GAUGES.map(function (g) {
      const c = classes[g.k];
      const title = (g.title && c) ? g.title(c) : g.name;
      if (!c) {
        return '<div class="g"><span class="nm"><small>' + g.code + '</small>' + esc(title) + '</span><div class="bar"></div><span class="v">—</span><div class="sub">no data from engine</div></div>';
      }
      const cap = typeof c.degraded_capacity === 'number' ? c.degraded_capacity : c.capacity;
      const nom = c.capacity;
      const com = typeof c.committed === 'number' ? c.committed : c.actual;
      const act = c.actual;
      const wit = witnesses.filter(function (w) { return w.resource_class === g.k; });
      const binding = c.feasible === false || wit.length > 0;
      const ratio = cap > 0 ? com / cap : (com > 0 ? 2 : 0);
      const state = binding ? 'alarm' : ratio > 0.85 ? 'warn' : 'ok';
      if (binding) nBind++; else if (state === 'warn') nWarn++;
      const full = Math.max(nom || 0, cap || 0, com || 0, 1e-9);
      const pc = function (v) { return Math.max(0, Math.min(100, 100 * (v || 0) / full)).toFixed(2); };
      const fmtv = function (v) { return typeof v === 'number' ? F.fix(v * g.scale, g.dp) : '—'; };
      const degraded = typeof nom === 'number' && cap < nom;
      const a = fcBy[g.k];
      const fcChip = a && a.state !== 'QUIET'
        ? '<span class="fc ' + esc(a.state) + '" title="forecast · advisory only — ' + esc(a.sentence) + '">↗ ~' + esc((a.band_s && a.band_s[0]) || a.h_star_s || '?') + ' s · ' + esc(a.state) + '</span>' : '';
      const w0 = wit[0];
      const witLine = binding ? '<span class="wit">binding: ' + esc(w0 ? (w0.detail || F.plainConstraint(w0)) : 'class infeasible') + '</span>' : '';
      const nomTxt = degraded ? '<span class="deg">capacity derated from ' + fmtv(nom) + ' ' + g.unit + '</span> · ' : '';
      return '<div class="g ' + state + (binding ? ' binding' : '') + '" title="' + esc(w0 ? (w0.detail || '') : '') + '">' +
        '<span class="nm"><small>' + g.code + '</small>' + esc(title) + '</span>' +
        '<div class="bar">' + (degraded ? '<i class="hatch" style="left:' + pc(cap) + '%"></i>' : '') +
        '<i class="com" style="width:' + pc(com) + '%"></i><i class="act" style="width:' + pc(act) + '%"></i><i class="cap" style="left:calc(' + pc(cap) + '% - 1px)"></i></div>' +
        '<span class="v">' + fmtv(com) + '<small>/</small>' + fmtv(cap) + ' <small>' + g.unit + '</small></span>' +
        '<div class="sub">' + fcChip + (witLine || nomTxt + g.sub(c)) + '</div></div>';
    }).join('');
    return { binding: nBind, warn: nWarn, have: !!snap };
  };

  // ------------------------------------------------------------ decision log
  function typeBadge(e) {
    const t = e.type || '';
    const a = String(e.action || '');
    if (t === 'ADMISSION_ATTEMPT' || /ADMIT/.test(a)) return ['d1', 'D2 admit'];
    if (t.indexOf('D3') === 0) return ['d3', 'D3 arbitration'];
    if (t === 'D4_RECONNECTION') return ['d4', 'D4 reconnect'];
    if (t.indexOf('D4') === 0) return ['d4', 'D4 shed'];
    if (/CREW/.test(a)) return ['c', 'C crew'];
    if (/PROFILE/.test(a)) return ['p', 'D6 profile'];
    if (/PERTURB/.test(a)) return ['d4', 'perturb'];
    if (/ADVANCE|RESET|SESSION/.test(a)) return ['', 'session'];
    return ['', t.toLowerCase().replace(/_/g, ' ') || 'event'];
  }
  function impactsTable(list) {
    if (!Array.isArray(list) || !list.length) return '';
    return '<table><tr><th>guest</th><th>mode</th><th>power</th><th>loses</th></tr>' + list.map(function (g) {
      return '<tr><td>' + esc(g.guest_id) + '</td><td>' + esc(g.mode_before) + (g.mode_after !== g.mode_before ? ' → ' + esc(g.mode_after) : '') + '</td><td>' +
        kw(g.power_before_w) + (g.power_after_w !== g.power_before_w ? ' → ' + kw(g.power_after_w) : '') + ' kW</td><td>' + esc(g.loses || '') + (g.protected_reason ? ' (protected)' : '') + '</td></tr>';
    }).join('') + '</table>';
  }
  P.logEntries = function (live, simT) {
    const log = (live && live.decision_log) || [];
    const idx = log.map(function (e, i) { return { e: e, i: i }; }).filter(function (x) {
      if (/^Mission clock advanced/.test(String(x.e.causal_sentence || ''))) return false;
      return typeof x.e.sim_time_s !== 'number' || x.e.sim_time_s <= simT;
    });
    idx.sort(function (a, b) {
      const ta = typeof a.e.sim_time_s === 'number' ? a.e.sim_time_s : -1, tb = typeof b.e.sim_time_s === 'number' ? b.e.sim_time_s : -1;
      if (tb !== ta) return tb - ta;
      if ((b.e.state_version || 0) !== (a.e.state_version || 0)) return (b.e.state_version || 0) - (a.e.state_version || 0);
      return b.i - a.i;
    });
    return idx.map(function (x) { return x.e; });
  };
  P.renderLog = function (el, entries, admitCache, openKeys, seenKeys) {
    if (!entries.length) { el.innerHTML = '<li class="empty">No decisions yet — press Play or run a jury test.</li>'; return; }
    el.innerHTML = entries.slice(0, 80).map(function (e, i) {
      const tb = typeBadge(e);
      const key = (e.type || '') + '|' + (e.action || '') + '|' + e.sim_time_s + '|' + (e.state_version || '') + '|' + (e.candidate_guest_id || '') + '|' + i;
      const stable = key.replace(/\|\d+$/, '');
      let sentence = F.plainEntrySentence(e);
      let extra = '';
      const cached = e.type === 'ADMISSION_ATTEMPT' && admitCache[(e.candidate_guest_id || '') + '|' + (e.verdict || '') + '|' + (e.state_version || '')];
      if (cached) {
        sentence = cached.decision.headline || sentence;
        const bcs = cached.decision.binding_constraints || [];
        if (bcs.length) extra += '<div><b>Binding:</b> ' + bcs.map(function (b) { return esc(F.plainConstraint(b)); }).join(' · ') + '</div>';
      }
      const d = e.details || e.decision || {};
      if (d.rationale && typeof d.rationale === 'string' && d.rationale !== e.causal_sentence) extra += '<div>' + esc(d.rationale) + '</div>';
      if (Array.isArray(d.binding_constraints) && d.binding_constraints.length && !cached) extra += '<div><b>Binding:</b> ' + d.binding_constraints.map(function (b) { return esc(F.plainConstraint(b)); }).join(' · ') + '</div>';
      const imp = d.guest_impacts || (d.result && d.result.guest_impacts);
      if (imp) extra += '<div><b>What every guest lost</b></div>' + impactsTable(imp);
      const isNew = seenKeys && !seenKeys[stable];
      return '<li class="' + (isNew ? 'fresh' : '') + '"><details' + (openKeys[stable] ? ' open' : '') + ' data-k="' + esc(stable) + '"><summary>' +
        '<span class="t">' + F.T(e.sim_time_s) + '</span><span class="ty ' + tb[0] + '">' + esc(tb[1]) + '</span>' +
        (e.verdict ? F.badge(e.verdict) : '<span></span>') + '<span class="s" title="' + esc(e.causal_sentence || '') + '">' + esc(sentence) + '</span></summary>' +
        '<div class="body">' + (e.causal_sentence && e.causal_sentence !== sentence ? '<div>' + esc(e.causal_sentence) + '</div>' : '') + extra + F.rawDetails(e) + '</div></details></li>';
    }).join('');
  };

  // ------------------------------------------------------------ env strip (A′ public data)
  P.renderEnv = function (env, win) {
    const $ = function (id) { return document.getElementById(id); };
    const strip = $('envStrip');
    if (!env || env.available === false || !env.current) {
      $('envSst').innerHTML = 'env n/a'; $('envWind').textContent = ''; $('envHs').textContent = ''; $('envSs').textContent = '';
      $('envWin').textContent = 'A′ n/a'; $('envWin').className = 'chip sm';
      strip.title = 'Environment snapshot unavailable on this server (nothing is invented).';
      return;
    }
    const c = env.current;
    $('envSst').innerHTML = 'SST <b>' + F.num(c.sst_c) + '</b> °C';
    $('envWind').innerHTML = 'Wind <b>' + F.num(c.wind_kn, 0) + '</b>/' + F.num(c.gust_kn, 0) + ' kn';
    $('envHs').innerHTML = 'Hs <b>' + F.num(c.hs_m, 2) + '</b> m';
    $('envSs').innerHTML = 'SS <b>' + esc(c.sea_state_wmo) + '</b>';
    const w = $('envWin');
    if (win && typeof win.permitted_now === 'boolean') {
      w.textContent = 'Deck window ' + (win.permitted_now ? 'OPEN' : 'CLOSED');
      w.className = 'chip sm ' + (win.permitted_now ? 'ok' : 'warn');
      w.title = win.permitted_now ? 'G4 vehicle replacement at sea permitted now.' :
        'Blocked now: ' + (win.blocking_reasons_now || []).join('; ') + '\nNext window: ' + ((win.next_window && (typeof win.next_window.start_s === 'number' ? F.T(win.next_window.start_s) : win.next_window.reason)) || '—');
    } else { w.textContent = 'A′ n/a'; w.className = 'chip sm'; }
    strip.title = 'Golden Gate · NOAA 46026 + Open-Meteo snapshot ' + (env.snapshot_id || '') + ' (' + (c.t_utc || '') + ')\n' +
      'Effective chilled-water cooling at this SST: ' + kw(env.cooling && env.cooling.effective_w) + ' kW\n' + (env.attribution || []).join('\n');
  };

  // ------------------------------------------------------------ crew meta (Scenario C)
  P.crewRegimeId = function (n) { return n === 0 ? 'UNCREWED_0' : n === 6 ? 'REDUCED_CREW_6' : 'FULL_CREW_14'; };
  P.renderCrew = function (el, live) {
    const st = (live && live.state) || {};
    const n = st.crew_count;
    const regs = (live && live.scenario_C_crew_regimes && live.scenario_C_crew_regimes.regimes) || {};
    const r = regs[P.crewRegimeId(n)];
    if (typeof n !== 'number' || !r) { el.innerHTML = 'Crew regime: —'; return; }
    const yn = function (v) { return v ? '<span class="ok-t">yes</span>' : '<span class="bad-t">no</span>'; };
    el.innerHTML = '<b>Crew ' + n + '</b> (' + esc(P.crewRegimeId(n)) + ') · available for guest ops <b>' + esc(r.available_for_guest_ops) + '</b> · human confirm <b>' + esc(r.human_confirmation_s) + ' s</b> · deck clear <b>' +
      (r.deck_clear_duration_s === null || r.deck_clear_duration_s === undefined ? 'n/a' : esc(r.deck_clear_duration_s) + ' s') + '</b> · deck ' + (st.deck_clear ? 'clear' : '<span class="bad-t">occupied</span>') +
      '<br>G4 launch/recovery ' + yn(r.g4_uav_launch_recovery_permitted) + ' · G4 swap at sea ' + yn(r.g4_uav_at_sea_replacement_permitted) + ' · G3 swap at sea ' + yn(r.g3_auv_at_sea_replacement_permitted) + ' · G2 ROV L/R ' + yn(r.g2_rov_launch_recovery_permitted);
  };

  // ------------------------------------------------------------ Scenario A C(5,4) proof (engine data)
  P.renderC54 = function (el, live) {
    const c = live && live.scenario_A_combinations;
    if (!c || !Array.isArray(c.combinations)) return;
    el.innerHTML = '<thead><tr><th>4 guests</th><th>mass</th><th>stab-eq</th><th>4th</th><th>verdict</th></tr></thead><tbody>' + c.combinations.map(function (x) {
      return '<tr><td>' + esc((x.combination || []).join('+')) + '</td><td' + (x.exceeds_10000_kg ? ' class="bad-t"' : '') + '>' + F.num(x.total_mass_kg, 0) + '</td><td>' + F.num(x.total_stability_eq_kg, 0) + '</td><td>' + esc(x.fourth_guest_id) + '</td><td>' + F.badge(x.fourth_guest_verdict) + '</td></tr>';
    }).join('') + '</tbody>';
  };
})();

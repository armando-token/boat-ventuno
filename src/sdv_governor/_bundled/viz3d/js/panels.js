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

  P.forecastBody = function (fc, simT) {
    const al = P.forecastAlerts(fc, simT).slice().sort(function (a, b) { return (b.sim_time_s || 0) - (a.sim_time_s || 0); });
    if (!fc || !fc.enabled) return '<div class="dim">The forecast is advisory only and is not available on this server.</div>';
    const head = '<div class="dim">Advisory only: the governor\'s decisions never depend on it · source ' + esc(fc.source || '—') + (fc.telemetry_profile ? ' · telemetry ' + esc(fc.telemetry_profile) : '') + '</div>';
    if (!al.length) return head + '<div class="dim">No alert raised up to ' + F.T(simT) + '.</div>';
    return head + '<ul class="fc-list">' + al.map(function (a) {
      const w = a.witness || {};
      const un = w.class === 'power' || w.class === 'cooling' ? ' kW' : '';
      const v = function (x) { return typeof x === 'number' ? (un ? F.fix(x / 1000, 1) : F.num(x)) : '—'; };
      return '<li><span class="fc ' + esc(a.state) + '">' + esc(a.state) + '</span> <b class="mono">' + F.T(a.sim_time_s) + '</b> ' + esc(a.guest_id || '') +
        '<div>' + esc(a.sentence) + '</div>' +
        '<div class="kvs"><span>witness</span><b>' + esc(w.class || '—') + ' · ' + esc(w.component || '—') + ' · projected ' + v(w.projected_w) + ' / capacity ' + v(w.capacity_w) + un + '</b>' +
        (a.band_s ? '<span>90% band</span><b>' + esc(a.band_s[0]) + '–' + esc(a.band_s[1]) + ' s</b>' : '') + '</div></li>';
    }).join('') + '</ul>';
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

  const gPrev = {}, gDelta = {};
  let gPrevVer = null;
  function sparkline(live, k, simT, full, scale) {
    const ticks = (live && (live.executed_timeline_ticks || (live.scenario_B_replay || {}).timeline_ticks)) || [];
    const pts = [];
    ticks.forEach(function (t) {
      if (t.is_prediction || typeof t.sim_time_s !== 'number' || t.sim_time_s > simT) return;
      const c = ((t.budget_snapshot || {}).classes || {})[k];
      if (c && typeof c.actual === 'number') pts.push([t.sim_time_s, c.actual]);
    });
    if (pts.length < 2) return '';
    const W = 64, H = 16, t1 = Math.max(simT, pts[pts.length - 1][0], 1);
    const d = pts.map(function (p, i) { return (i ? 'L' : 'M') + (W * p[0] / t1).toFixed(1) + ' ' + (H - 1 - (H - 2) * Math.min(1, Math.max(0, p[1] / full))).toFixed(1); }).join('');
    const last = pts[pts.length - 1][1];
    return '<span class="sp" title="actual over the mission so far (' + pts.length + ' engine ticks) · now ' + esc(F.fix(last * scale, 1)) + '"><svg viewBox="0 0 ' + W + ' ' + H + '" width="' + W + '" height="' + H + '"><path d="' + d + '"/></svg></span>';
  }

  P.renderGauges = function (el, live, fc, simT) {
    const snap = P.snapshot(live);
    const now = performance.now();
    const ver = live ? (live.state_version || 0) + '|' + simT : null;
    const fresh = ver !== gPrevVer; gPrevVer = ver;
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
      if (fresh && typeof com === 'number') {
        const p = gPrev[g.k];
        if (typeof p === 'number' && Math.abs(com - p) >= 0.005 * full) gDelta[g.k] = { d: com - p, t0: now };
        gPrev[g.k] = com;
      }
      const dl = gDelta[g.k] && now - gDelta[g.k].t0 < 4000 ? gDelta[g.k] : null;
      const age = dl ? Math.round(now - dl.t0) : 0;
      const deltaTxt = dl ? '<span class="dlt ' + (dl.d > 0 ? 'up' : 'dn') + '" style="animation-delay:-' + age + 'ms">' + (dl.d > 0 ? '+' : '−') + F.fix(Math.abs(dl.d) * g.scale, g.dp) + ' ' + g.unit + '</span>' : '';
      const witLine = binding ? '<span class="wit">binding: ' + esc(w0 ? (w0.detail || F.plainConstraint(w0)) : 'class infeasible') + '</span>' : '';
      const nomTxt = degraded ? '<span class="deg">capacity derated from ' + fmtv(nom) + ' ' + g.unit + '</span> · ' : '';
      return '<div class="g ' + state + (binding ? ' binding' : '') + (dl ? ' flash' : '') + '"' + (dl ? ' style="--fa:-' + age + 'ms"' : '') + ' title="' + esc(w0 ? (w0.detail || '') : '') + '">' +
        '<span class="nm"><small>' + g.code + '</small>' + esc(title) + '</span>' +
        '<div class="bar">' + (degraded ? '<i class="hatch" style="left:' + pc(cap) + '%"></i>' : '') +
        '<i class="com" style="width:' + pc(com) + '%"></i><i class="act" style="width:' + pc(act) + '%"></i><i class="cap" style="left:calc(' + pc(cap) + '% - 1px)"></i></div>' +
        '<span class="v">' + fmtv(com) + '<small>/</small>' + fmtv(cap) + ' <small>' + g.unit + '</small></span>' +
        '<div class="sub">' + fcChip + (witLine || nomTxt + g.sub(c)) + '</div><span class="spark">' + deltaTxt + sparkline(live, g.k, simT, full, g.scale) + '</span></div>';
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
  function kv(k, v) { return v === undefined || v === null || v === '' ? '' : '<dt>' + esc(k) + '</dt><dd>' + v + '</dd>'; }
  function tS(x) { return typeof x === 'number' ? '<span class="mono">' + F.T(x) + '</span>' : null; }
  function yn(x) { return x === true ? '<span class="ok-t">yes</span>' : x === false ? '<span class="bad-t">no</span>' : null; }
  function bindList(list) { return Array.isArray(list) && list.length ? list.map(function (b) { return esc(F.plainConstraint(b)); }).join('<br>') : null; }
  function human(k) { return String(k).replace(/_/g, ' ').replace(/\bw\b/, '(W)').replace(/\bs\b$/, '(s)'); }
  function scalar(v) { return v === null || ['string', 'number', 'boolean'].indexOf(typeof v) >= 0; }
  function elapsed(d) {
    let out = '';
    Object.keys(d || {}).forEach(function (k) { if (/elapsed|_ms$/.test(k) && typeof d[k] === 'number') out += kv(human(k), '<span class="mono">' + F.num(d[k], 2) + ' ms</span>'); });
    return out;
  }
  P.entryDetail = function (e, cached) {
    const d = e.details || e.decision || {};
    let h = '';
    if (cached) {
      const dec = cached.decision || {};
      h += kv('verdict', F.badge(dec.verdict)) + kv('candidate', esc(dec.candidate_guest_id) + (cached.candidate_total_mass_kg ? ' · ' + F.num(cached.candidate_total_mass_kg, 0) + ' kg' : ''));
      h += kv('binding constraint', bindList(dec.binding_constraints) || 'none — every class feasible');
      if (Array.isArray(dec.ranked_displacements) && dec.ranked_displacements.length) h += kv('displacements (ranked)', dec.ranked_displacements.map(function (r, i) { return (i + 1) + '. ' + esc(F.describeAction(r)); }).join('<br>'));
      if (dec.placement) h += kv('placement', esc(typeof dec.placement === 'string' ? dec.placement : (dec.placement.slot || dec.placement.slot_id || dec.placement.position || JSON.stringify(dec.placement))));
      if (Array.isArray(dec.placement_alternatives) && dec.placement_alternatives.length) h += kv('alternatives considered', dec.placement_alternatives.length + ' placements');
      if (Array.isArray(dec.rejected_placements) && dec.rejected_placements.length) h += kv('rejected placements', dec.rejected_placements.length + '');
      if (dec.one_genset_down) h += kv('one genset down', esc(F.GENSET[dec.one_genset_down.verdict] || dec.one_genset_down.verdict || ''));
      if (dec.jury_ship_overrides && dec.jury_ship_overrides.note) h += kv('jury overrides', esc(dec.jury_ship_overrides.note));
      h += kv('priority profile', dec.priority_profile_sha256 ? '<span class="mono">sha ' + esc(String(dec.priority_profile_sha256).slice(0, 12)) + '</span>' : null);
      h += kv('within 30 s', yn(dec.within_30s_deadline));
      h += kv('elapsed', typeof dec.calculation_elapsed_ms === 'number' ? '<span class="mono">' + F.num(dec.calculation_elapsed_ms, 1) + ' ms</span>' : null);
    } else if (e.type === 'D3_ARBITRATION') {
      h += kv('decision', esc(String(d.decision_type || '').replace(/_/g, ' ')));
      h += kv('binding constraint', bindList(d.binding_constraints));
      h += kv('hold command', tS(d.hold_cmd_time_s)) + kv('planned touchdown', tS(d.planned_touchdown_time_s)) + kv('hard touchdown deadline', tS(d.hard_touchdown_deadline_s));
      h += kv('hard safety satisfied', yn(d.hard_safety_satisfied)) + kv('decided within 2 s', yn(d.within_2s_deadline));
      h += kv('tie-break rule', d.tie_break_rule ? esc(String(d.tie_break_rule)) : null);
      const lm = d.losing_mission_cost || {};
      if (lm.losing_guest_id) h += kv('losing mission cost', esc(lm.losing_guest_id) + ' interrupted <b>' + F.num(lm.total_survey_interruption_min) + ' min</b>' + (lm.g3_mode_during_recovery ? ' · ' + esc(lm.g3_mode_during_recovery) : '') + (typeof lm.survey_segment_preserved_s === 'number' ? ' · ' + lm.survey_segment_preserved_s + ' s preserved' : ''));
      h += kv('crew timing', typeof d.human_confirmation_s === 'number' ? 'confirm ' + d.human_confirmation_s + ' s · deck clear ' + (d.deck_clear_duration_s === null || d.deck_clear_duration_s === undefined ? 'n/a' : d.deck_clear_duration_s + ' s') : null);
      if (Array.isArray(d.evaluated_candidates) && d.evaluated_candidates.length) {
        h += kv('alternatives considered', '<table class="alt"><tr><th></th><th>strategy</th><th>touchdown</th><th>margin</th><th>utility</th></tr>' + d.evaluated_candidates.map(function (c) {
          const chosen = c.candidate_id === d.selected_candidate_id;
          return '<tr class="' + (c.feasible ? '' : 'inf') + '"><td>' + (c.feasible ? '<span class="ok-t">✓</span>' : '<span class="bad-t">✕</span>') + '</td><td>' + esc(String(c.strategy_label || c.candidate_id).replace(/_/g, ' ').toLowerCase()) + (chosen ? ' <b>(chosen)</b>' : '') + '</td><td>' + (typeof c.touchdown_time_s === 'number' ? F.T(c.touchdown_time_s) : '—') + '</td><td>' + (typeof c.safety_margin_s === 'number' ? c.safety_margin_s + ' s' : '—') + '</td><td>' + (typeof c.utility_score === 'number' ? F.num(c.utility_score, 1) : '—') + '</td></tr>';
        }).join('') + '</table>');
      }
      h += kv('priority profile', esc(d.priority_profile_id || '') + (d.priority_profile_sha256 ? ' <span class="mono dim">sha ' + esc(String(d.priority_profile_sha256).slice(0, 8)) + '</span>' : ''));
      h += elapsed(d);
      h += kv('rationale', d.rationale ? esc(d.rationale) : null);
    } else if (e.type === 'D4_DEGRADATION') {
      h += kv('status', esc([d.status, d.verdict].filter(Boolean).join(' · ')));
      const da = d.deficit_analysis || {};
      if (typeof da.power_capacity_w === 'number') h += kv('power deficit', 'committed ' + kw(da.initial_power_committed_w) + ' vs capacity ' + kw(da.power_capacity_w) + ' kW → deficit <b>' + kw(da.power_committed_deficit_w) + ' kW</b>');
      if (typeof da.cooling_capacity_w === 'number') h += kv('cooling deficit', 'committed ' + kw(da.initial_cooling_committed_w) + ' vs capacity ' + kw(da.cooling_capacity_w) + ' kW → deficit <b>' + kw(da.cooling_committed_deficit_w) + ' kW</b>');
      if (Array.isArray(d.actions_taken) && d.actions_taken.length) h += kv('actions', d.actions_taken.map(function (a) { return (a.step_index || '') + '. ' + esc(a.guest_id) + ' ' + esc(a.from_mode || '') + ' → ' + esc(a.to_mode || '') + (typeof a.power_saved_w === 'number' ? ' · saves ' + kw(a.power_saved_w) + ' kW' : ''); }).join('<br>'));
      h += kv('first shed ≤ 5 s', yn(d.within_5s_first_shed_deadline)) + kv('sequence ≤ 60 s', yn(d.within_60s_sequence_deadline));
      h += kv('priority profile', d.priority_profile_id ? esc(d.priority_profile_id) : null);
      h += elapsed(d);
      h += kv('rationale', d.rationale ? esc(d.rationale) : null);
      if (Array.isArray(d.guest_impacts) && d.guest_impacts.length) h += kv('what every guest lost', impactsTable(d.guest_impacts));
    } else if (e.type === 'D4_RECONNECTION') {
      h += kv('guest', esc(d.guest_id)) + kv('reconnect allowed', yn(d.reconnect_allowed)) + kv('target mode', esc(d.target_mode || ''));
      h += kv('power free after', typeof d.post_reconnect_power_free_w === 'number' ? kw(d.post_reconnect_power_free_w) + ' kW' : null);
      h += kv('cooling free after', typeof d.post_reconnect_cooling_free_w === 'number' ? kw(d.post_reconnect_cooling_free_w) + ' kW' : null);
      h += kv('reason', d.reason ? esc(d.reason) : null);
      h += elapsed(d);
    } else {
      if (e.verdict) h += kv('verdict', F.badge(e.verdict));
      if (e.candidate_guest_id) h += kv('candidate', esc(e.candidate_guest_id));
      Object.keys(d).forEach(function (k) { if (scalar(d[k]) && !/sentence/.test(k)) h += kv(human(k), esc(String(d[k]))); });
      const imp = d.guest_impacts || (d.result && d.result.guest_impacts);
      if (Array.isArray(imp) && imp.length) h += kv('what every guest lost', impactsTable(imp));
    }
    if (typeof e.state_version === 'number') h += kv('state version', '<span class="mono">v' + e.state_version + '</span>');
    return '<dl class="kv">' + h + '</dl>';
  };
  P.renderLog = function (el, entries, admitCache, openKeys, seenKeys) {
    el._entries = {};
    if (!entries.length) { el.innerHTML = '<li class="empty">No decisions yet — press Play or run a jury test.</li>'; return; }
    el.innerHTML = entries.slice(0, 80).map(function (e, i) {
      const tb = typeBadge(e);
      const key = (e.type || '') + '|' + (e.action || '') + '|' + e.sim_time_s + '|' + (e.state_version || '') + '|' + (e.candidate_guest_id || '') + '|' + i;
      const stable = key.replace(/\|\d+$/, '');
      let sentence = F.plainEntrySentence(e);
      const cached = e.type === 'ADMISSION_ATTEMPT' && admitCache[(e.candidate_guest_id || '') + '|' + (e.verdict || '') + '|' + (e.state_version || '')];
      if (cached) sentence = cached.decision.headline || sentence;
      const isNew = seenKeys && !seenKeys[stable];
      const open = !!openKeys[stable];
      el._entries[stable] = [e, cached, sentence];
      return '<li class="' + (isNew ? 'fresh' : '') + '"><details' + (open ? ' open' : '') + ' data-k="' + esc(stable) + '"><summary>' +
        '<i class="chev">▸</i><span class="t">' + F.T(e.sim_time_s) + '</span><span class="ty ' + tb[0] + '">' + esc(tb[1]) + '</span>' +
        (e.verdict ? F.badge(e.verdict) : '<span></span>') + '<span class="s" title="' + esc(e.causal_sentence || '') + '">' + esc(sentence) + '</span></summary>' +
        '<div class="body">' + (open ? P.entryBody(e, cached, sentence) : '') + '</div></details></li>';
    }).join('');
  };
  P.entryBody = function (e, cached, sentence) {
    return (e.causal_sentence && e.causal_sentence !== sentence ? '<div class="cs">' + esc(e.causal_sentence) + '</div>' : '') + P.entryDetail(e, cached) +
      '<details class="raw"><summary>raw</summary><pre>' + esc(JSON.stringify(e, null, 2)) + '</pre></details>';
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

  P.renderCrewTable = function (el, live) {
    const regs = (live && live.scenario_C_crew_regimes && live.scenario_C_crew_regimes.regimes) || {};
    const ids = ['FULL_CREW_14', 'REDUCED_CREW_6', 'UNCREWED_0'].filter(function (k) { return regs[k]; });
    if (!ids.length) { el.innerHTML = '<tbody><tr><td class="dim">Crew regimes not available from the engine.</td></tr></tbody>'; return; }
    const cur = P.crewRegimeId((live.state || {}).crew_count);
    const yn2 = function (v) { return v === true ? '<span class="ok-t">yes</span>' : v === false ? '<span class="bad-t">no</span>' : esc(v === null || v === undefined ? 'n/a' : v); };
    const rows = [
      ['available for guest ops', 'available_for_guest_ops', esc], ['human confirmation', 'human_confirmation_s', function (v) { return typeof v === 'number' ? v + ' s' : 'n/a'; }],
      ['deck clear time', 'deck_clear_duration_s', function (v) { return typeof v === 'number' ? v + ' s' : 'n/a'; }],
      ['G4 drone launch / recovery', 'g4_uav_launch_recovery_permitted', yn2], ['G4 swap at sea', 'g4_uav_at_sea_replacement_permitted', yn2],
      ['G3 AUV swap at sea', 'g3_auv_at_sea_replacement_permitted', yn2], ['G2 ROV launch / recovery', 'g2_rov_launch_recovery_permitted', yn2]
    ];
    const lbl = { FULL_CREW_14: '14 · full', REDUCED_CREW_6: '6 · reduced', UNCREWED_0: '0 · uncrewed' };
    el.innerHTML = '<thead><tr><th></th>' + ids.map(function (k) { return '<th class="' + (k === cur ? 'cur' : '') + '">' + lbl[k] + '</th>'; }).join('') + '</tr></thead><tbody>' +
      rows.map(function (r) { return '<tr><td>' + r[0] + '</td>' + ids.map(function (k) { return '<td class="' + (k === cur ? 'cur' : '') + '">' + r[2](regs[k][r[1]]) + '</td>'; }).join('') + '</tr>'; }).join('') + '</tbody>';
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

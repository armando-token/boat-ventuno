/* viz3d/js/tabs.js — Shocks (D4 / Scenario C) and Priority (D6) tabs. Every action is a live server call. */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});
  const F = SDV.fmt, esc = F.esc;
  const $ = function (id) { return document.getElementById(id); };

  SDV.tabs = {};
  SDV.tabs.init = function (ctx) {
    // ---------------------------------------------------------------- tab bar
    document.querySelectorAll('#tabbar button').forEach(function (b) {
      b.addEventListener('click', function () { SDV.tabs.show(b.getAttribute('data-tab')); });
    });
    SDV.tabs.show = function (name) {
      document.querySelectorAll('#tabbar button').forEach(function (x) { x.classList.toggle('active', x.getAttribute('data-tab') === name); });
      ['jury', 'shocks', 'prio'].forEach(function (n) { $('tab-' + n).hidden = n !== name; });
    };

    // ---------------------------------------------------------------- shocks
    const SHOCKS = {
      genLoss: function () { return { perturbation_type: 'GENERATOR_LOSS_100KW', new_power_reserve_w: 100000 }; },
      coolLoss: function () { return { perturbation_type: 'COOLING_LOSS_30C_40PCT', seawater_temp_c: 30 }; },
      overload: function () { return { perturbation_type: 'GUEST_OVERCONSUMPTION_20PCT', guest_id: 'G5', measured_power_w: 54000 }; },
      tempFeed: function () { return { perturbation_type: 'LOST_SEAWATER_TEMP_FEED' }; },
      hold: function () { return { perturbation_type: 'URGENT_HOLD', guest_id: 'G3' }; },
      stop: function () { return { perturbation_type: 'URGENT_STOP', guest_id: 'G3' }; }
    };
    function shockOut(title, resp) {
      const d = resp.data || {};
      const r = d.result || {};
      const ok = resp.ok && d.status !== 'REJECTED';
      const sentence = r.causal_sentence || d.causal_sentence || d.message || (resp.ok ? 'Executed.' : 'HTTP ' + resp.status);
      let imp = '';
      if (Array.isArray(r.guest_impacts) && r.guest_impacts.length) {
        imp = '<div class="imp">' + r.guest_impacts.filter(function (g) { return g.mode_before !== g.mode_after || g.power_before_w !== g.power_after_w; }).map(function (g) {
          return '<span><b>' + esc(g.guest_id) + '</b> ' + esc(g.mode_before) + ' → ' + esc(g.mode_after) + '</span>';
        }).join('') + '</div>';
      }
      $('shockOut').className = 'shock-out ' + (ok ? 'ok' : 'bad');
      $('shockOut').innerHTML = '<b>' + esc(title) + (r.verdict ? ' · ' + esc(r.verdict) : '') + (d.status === 'REJECTED' ? ' · REJECTED' : '') + '</b><br>' + esc(sentence) + imp;
    }
    async function perturb(title, body, btn) {
      if (ctx.isPlaying()) ctx.pause();
      btn && btn.classList.add('busy');
      body.priority_profile_id = ctx.profile();
      const resp = await SDV.requestApi('POST', '/api/perturb', body);
      btn && btn.classList.remove('busy');
      if (resp.status === 0) return resp;
      shockOut(title, resp);
      ctx.afterAction(true);
      return resp;
    }
    document.querySelectorAll('[data-inject]').forEach(function (b) {
      b.addEventListener('click', function () {
        const k = b.getAttribute('data-inject');
        perturb(b.querySelector('b').textContent, SHOCKS[k](), b);
      });
    });
    $('btnLayered').addEventListener('click', async function () {
      const next = !ctx.flags.layered;
      const resp = await perturb('Water column layered ' + (next ? 'ON' : 'OFF'), { perturbation_type: 'WATER_COLUMN_LAYERED', value: next }, $('btnLayered'));
      if (resp.ok) {
        ctx.flags.layered = next;
        const link = ((resp.data || {}).result || {}).acoustic_link_status || {};
        $('layeredState').textContent = 'A.5 acoustic · ' + (next ? 'ON' : 'OFF') + (link.available === false ? ' · G3 link DOWN' : link.available ? ' · G3 link OK' : '');
        $('btnLayered').classList.toggle('on', next);
      }
    });
    $('btnQuietRadio').addEventListener('click', async function () {
      const st = ctx.state() || {};
      const next = st.own_radar_policy === 'QUIET_RADIO' ? 'TRANSMIT' : 'QUIET_RADIO';
      await perturb('Own radar policy → ' + next, { perturbation_type: 'OWN_RADAR_POLICY', value: next }, $('btnQuietRadio'));
    });
    $('btnClear').addEventListener('click', function () { ctx.reset(); });

    // ---------------------------------------------------------------- priority mini (live profile detail)
    SDV.tabs.renderPrioMini = function (live) {
      const det = (live && live.priority_profiles_detail) || {};
      const cur = String((live && live.priority_profile_id) || '').toLowerCase();
      const ids = Object.keys(det);
      if (!ids.length) { $('prioMini').innerHTML = '<div class="row dim">waiting for engine…</div>'; return; }
      $('prioMini').innerHTML = ids.map(function (pid) {
        const p = det[pid] || {};
        const shed = p.shed_order_lowest_priority_first || [];
        return '<div class="row' + (pid === cur ? ' cur' : '') + '"><b>' + esc(p.name || pid) + '</b>' + (pid === cur ? ' <span class="chip sm ok">live</span>' : '') +
          '<span>D3 strategy <span class="mono">' + esc(p.survey_vs_recovery_strategy || '—') + '</span></span>' +
          '<span>D4 shed first → last <span class="mono">' + esc(shed.join(' › ') || '—') + '</span></span></div>';
      }).join('');
    };

    // ---------------------------------------------------------------- D6 side-by-side
    function col(st, pid) {
      const r = (st && st.scenario_B_replay) || {};
      const ds = r.decisions || [];
      const d3 = (ds.find(function (x) { return x.phase === 'D3_ARBITRATION'; }) || {}).decision || {};
      const d4 = (ds.find(function (x) { return x.phase === 'D4_DEGRADATION'; }) || {}).decision || {};
      const det = (st.priority_profiles_detail || {})[pid] || {};
      const lc = d3.losing_mission_cost || {};
      const impacts = (d4.guest_impacts || []).map(function (g) {
        return g.guest_id + ' ' + (g.mode_before === g.mode_after ? g.mode_after : g.mode_before + '→' + g.mode_after) + (g.loses && g.loses !== 'nothing' ? ' (loses ' + g.loses + ')' : '');
      });
      return {
        name: det.name || pid,
        strategy: det.survey_vs_recovery_strategy || d3.selected_candidate_id || '—',
        d3: typeof d3.rationale === 'string' ? d3.rationale : '—',
        hold: typeof d3.hold_cmd_time_s === 'number' ? F.T(d3.hold_cmd_time_s) : '—',
        td: typeof r.executed_touchdown_time_s === 'number' ? F.T(r.executed_touchdown_time_s) : '—',
        g3: typeof lc.total_survey_interruption_min === 'number' ? F.num(lc.total_survey_interruption_min) + ' min lost · ' + F.num(lc.survey_segment_aborted_s, 0) + ' s leg re-run' : '—',
        shed: (det.shed_order_lowest_priority_first || []).join(' › ') || '—',
        d4: typeof d4.rationale === 'string' ? d4.rationale : (Array.isArray(d4.rationale) ? d4.rationale.join(' ') : '—'),
        impacts: impacts,
        sha: r.semantic_sha256 ? String(r.semantic_sha256).slice(0, 12) + '…' : '—'
      };
    }
    $('btnRunBoth').addEventListener('click', async function () {
      const btn = $('btnRunBoth');
      btn.disabled = true; btn.textContent = 'Running both…';
      const t0 = performance.now();
      const res = await Promise.all(['survey_primary', 'rescue_primary'].map(function (p) {
        return SDV.requestApi('GET', '/api/state?priority_profile=' + p, null, { merge: false });
      }));
      btn.disabled = false; btn.textContent = 'Run both profiles';
      ctx.refreshDisabled();
      if (!res[0].ok || !res[1].ok) { ctx.openOverlay('<div class="res-headline">Could not fetch both profiles from the engine (HTTP ' + res[0].status + '/' + res[1].status + ').</div>'); return; }
      const a = col(res[0].data, 'survey_primary'), b = col(res[1].data, 'rescue_primary');
      const rows = [
        ['D3 strategy', 'strategy'], ['D3 decision', 'd3'], ['G3 hold command', 'hold'], ['G4 touchdown', 'td'], ['G3 survey cost', 'g3'],
        ['D4 shed order', 'shed'], ['D4 decision', 'd4'], ['Semantic SHA-256', 'sha']
      ];
      let html = '<div class="cmp-h">Same code, same timeline, two different ships.</div>' +
        '<div class="cmp-sub">Scenario B replayed by the engine under each priority file (GET /api/state?priority_profile=…, read-only, ' + Math.round(performance.now() - t0) + ' ms). Differences highlighted.</div>' +
        '<div class="cmp"><div class="h"></div><div class="h">' + esc(a.name) + '</div><div class="h">' + esc(b.name) + '</div>';
      rows.forEach(function (r) {
        const va = a[r[1]], vb = b[r[1]], diff = va !== vb ? ' diff' : '';
        html += '<div class="k">' + esc(r[0]) + '</div><div class="' + diff.trim() + '">' + esc(va) + '</div><div class="' + diff.trim() + '">' + esc(vb) + '</div>';
      });
      const n = Math.max(a.impacts.length, b.impacts.length);
      for (let i = 0; i < n; i++) {
        const va = a.impacts[i] || '', vb = b.impacts[i] || '', diff = va !== vb ? 'diff' : '';
        html += '<div class="k">' + (i === 0 ? 'D4 · what each guest ends with' : '') + '</div><div class="' + diff + ' mono">' + esc(va) + '</div><div class="' + diff + ' mono">' + esc(vb) + '</div>';
      }
      html += '</div>';
      ctx.openOverlay(html, true);
    });

    // ---------------------------------------------------------------- custom profile (ALIGN-T27, posts YAML to /api/profile)
    const SAMPLE = [
      'schema_version: "sdv-priority-profile/1.0"',
      'priority_profile_id: "CUSTOM_SAR_OVERRIDE"',
      'name: "Custom SAR Override Priority Profile"',
      'description: "Custom SAR-first priority profile with immediate G4 recovery turn and strict D4 shed order."',
      'capability_weights:',
      '  maritime_sar_rescue: 100.0',
      '  hydrographic_survey: 85.0',
      '  seabed_anomaly_detection: 80.0',
      '  environmental_sensing: 70.0',
      '  water_quality_monitoring: 65.0',
      '  subsea_intervention: 60.0',
      '  hull_and_cable_inspection: 55.0',
      '  surface_surveillance: 45.0',
      '  flight_weather_support: 50.0',
      '  wideband_spectrum_monitoring: 30.0',
      '  salvage_assistance: 25.0',
      'guest_priority_rank:',
      '  G4: 1', '  G3: 2', '  G1: 3', '  G6: 4', '  G2: 5', '  G5: 6',
      'arbitration_policy:',
      '  survey_vs_recovery_strategy: "IMMEDIATE_FULL_SUSPENSION"',
      '  pre_reserve_recovery_power: true',
      '  max_acceptable_survey_pause_min: 30',
      '  tie_break_order: "HIGHER_CAPABILITY_SCORE_THEN_LEXICOGRAPHIC"',
      'degradation_policy:',
      '  prefer_mode_downgrade_before_shed: true',
      '  protect_uninterruptible_tasks: true',
      '  shed_order_lowest_priority_first: ["G5", "G2", "G6", "G1", "G3", "G4"]'
    ].join('\n');
    $('btnSampleProfile').addEventListener('click', function () { $('customProfile').value = SAMPLE; });
    $('btnApplyProfile').addEventListener('click', async function () {
      const raw = $('customProfile').value.trim();
      if (!raw) { $('customStatus').textContent = 'Paste or load a profile first.'; return; }
      $('customStatus').textContent = 'validating on the server…'; $('customStatus').className = 'hint grow';
      const resp = await SDV.requestApi('POST', '/api/profile', { priority_profile_yaml: raw });
      if (resp.status === 0) return;
      const d = resp.data || {};
      if (!resp.ok) {
        $('customStatus').textContent = 'Rejected: ' + (d.message || d.error_code || ('HTTP ' + resp.status)) + (d.field_path ? ' (' + d.field_path + ')' : '');
        $('customStatus').className = 'hint grow bad-t';
        return;
      }
      $('customStatus').textContent = 'Live profile now ' + (d.priority_profile_id || '?') + ' — press Play to run Scenario B under it.';
      $('customStatus').className = 'hint grow ok-t';
      ctx.afterAction(true);
    });
  };
})();

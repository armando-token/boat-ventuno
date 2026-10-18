/* viz3d/js/jury.js — Scenario A jury test tab: builds the /api/admit_live body from the controls and renders the
   engine verdict (never computed client-side). */
(function () {
  'use strict';
  const SDV = (window.SDV = window.SDV || {});
  const F = SDV.fmt, esc = F.esc;
  const $ = function (id) { return document.getElementById(id); };

  SDV.jury = {};
  SDV.jury.init = function (ctx) {
    const knobs = { sstTouched: false, gen: 2, mode: 'catalog' };

    // ---------------------------------------------------------------- controls
    $('admBase').addEventListener('change', function () { $('admBaseCustomWrap').hidden = $('admBase').value !== 'CUSTOM'; });
    function setMode(m) {
      knobs.mode = m;
      document.querySelectorAll('#segCandMode button').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-mode') === m); });
      $('candCatalog').hidden = m !== 'catalog';
      $('candPaste').hidden = m !== 'paste';
      $('ovrDetails').hidden = m !== 'catalog';
      if (m === 'paste' && !$('admPaste').value.trim() && SDV.EXAMPLE_CARDS && SDV.EXAMPLE_CARDS.G7) {
        $('admPaste').value = JSON.stringify(SDV.EXAMPLE_CARDS.G7, null, 2);
      }
      pasteHint();
    }
    document.querySelectorAll('#segCandMode button').forEach(function (b) {
      b.addEventListener('click', function () { setMode(b.getAttribute('data-mode')); });
    });
    $('admCand').addEventListener('change', function () {
      if ($('admCand').value === 'G7') {
        $('admPaste').value = SDV.EXAMPLE_CARDS && SDV.EXAMPLE_CARDS.G7 ? JSON.stringify(SDV.EXAMPLE_CARDS.G7, null, 2) : '';
        $('admCand').value = 'G4';
        setMode('paste');
      }
    });
    function parsePaste() {
      const raw = $('admPaste').value.trim();
      if (!raw) return { kind: 'empty' };
      if (raw.charAt(0) === '{') {
        try {
          const obj = JSON.parse(raw);
          if (obj && typeof obj === 'object' && !Array.isArray(obj)) return { kind: 'json', obj: obj };
          return { kind: 'error', msg: 'JSON must be an object (Annex B card).' };
        } catch (e) { return { kind: 'error', msg: 'Invalid JSON: ' + e.message }; }
      }
      return { kind: 'yaml', text: raw };
    }
    function pasteHint() {
      const p = parsePaste();
      const h = $('admPasteHint');
      h.className = 'hint';
      if (p.kind === 'json') h.textContent = 'JSON card ' + (p.obj.guest_id || '?') + ' · ' + (p.obj.name || '') + ' → sent as candidate_manifest.';
      else if (p.kind === 'yaml') h.textContent = 'YAML text → sent as candidate_manifest_yaml (parsed by the server).';
      else if (p.kind === 'error') { h.textContent = p.msg; h.className = 'hint bad-t'; }
      else h.textContent = 'Paste an Annex B card (JSON or YAML).';
    }
    $('admPaste').addEventListener('input', pasteHint);

    function sstShow() {
      const v = Number($('admSst').value);
      $('sstVal').textContent = v + ' °C';
      $('sstReset').hidden = !knobs.sstTouched;
      $('sstNote').textContent = knobs.sstTouched ? 'sent: seawater_temp_c = ' + v + ' °C' : 'untouched → not sent';
      $('sstNote').classList.toggle('on', knobs.sstTouched);
    }
    $('admSst').addEventListener('input', function () { knobs.sstTouched = true; sstShow(); });
    $('sstReset').addEventListener('click', function () { knobs.sstTouched = false; $('admSst').value = 24; sstShow(); });
    document.querySelectorAll('#segGen button').forEach(function (b) {
      b.addEventListener('click', function () {
        knobs.gen = Number(b.getAttribute('data-gen'));
        document.querySelectorAll('#segGen button').forEach(function (x) { x.classList.toggle('active', x === b); });
      });
    });
    sstShow();

    // ---------------------------------------------------------------- request body
    function intField(id) {
      const v = $(id).value.trim();
      if (v === '') return undefined;
      const n = Number(v);
      return isFinite(n) ? Math.round(n) : undefined;
    }
    function buildBody(commit) {
      const body = { apply_if_admitted: !!commit, at_port: $('admAtPort').value === '1' };
      const base = $('admBase').value;
      if (base === 'CUSTOM') {
        body.installed_guest_ids = $('admBaseCustom').value.split(/[\s,+]+/).map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);
      } else if (base !== 'LIVE') {
        body.installed_guest_ids = base.split(',');
      }
      let label;
      if (knobs.mode === 'paste') {
        const p = parsePaste();
        if (p.kind === 'json') { body.candidate_manifest = p.obj; label = p.obj.guest_id || 'card'; }
        else if (p.kind === 'yaml') {
          body.candidate_manifest_yaml = p.text;
          const m = p.text.match(/guest_id:\s*"?([A-Za-z0-9_-]+)/); label = m ? m[1] : 'card';
        } else return { error: p.kind === 'empty' ? 'Paste a card first.' : p.msg };
      } else {
        body.candidate_guest_id = $('admCand').value;
        label = body.candidate_guest_id;
        const ov = {};
        [['ovMass', 'mass_kg'], ['ovNom', 'nominal_power_w'], ['ovSus', 'sustained_power_w'], ['ovPeak', 'peak_power_w'], ['ovCool', 'cooling_water_w']].forEach(function (p) {
          const n = intField(p[0]); if (n !== undefined) ov[p[1]] = n;
        });
        if ($('ovPos').value) ov.preferred_position = $('ovPos').value;
        if (Object.keys(ov).length) body.overrides = ov;
      }
      if (knobs.sstTouched) body.seawater_temp_c = Number($('admSst').value);
      if (knobs.gen === 1) body.power_reserve_w = 100000;
      const w = intField('admWind'); if (w !== undefined) body.wind_kn = w;
      const s = intField('admSea'); if (s !== undefined) body.sea_state = s;
      if ($('admRoofOk').value === '1') body.hangar_roof_clearance_verified = true;
      return { body: body, label: label };
    }
    SDV.jury.buildBody = buildBody;

    // ---------------------------------------------------------------- result rendering
    const GHOST_ORDER = ['flight_deck', 'mast_3', 'hangar_roof', 'bay1_a', 'bay1_b', 'bay2', 'bay1', 'hangar', 'stern_ramp'];
    function ghostSlot(dec) {
      const pos = (dec.placement && dec.placement.subcomponent_positions) || {};
      const vals = Object.keys(pos).map(function (k) { return pos[k]; });
      for (let i = 0; i < GHOST_ORDER.length; i++) if (vals.indexOf(GHOST_ORDER[i]) >= 0) return GHOST_ORDER[i];
      return vals[0] || null;
    }
    function optionsHtml(dec) {
      const opts = (dec.ranked_displacements || []).slice(0, 3);
      if (!opts.length) return '';
      return '<div class="res-sec"><div class="res-sec-t">Cheapest ways to say yes (ranked by the engine)</div><ol class="res-list">' + opts.map(function (o) {
        const lost = (o.capability_lost_breakdown || []).map(function (c) { return c.capability_lost_description; }).join('; ');
        const act = o.action_type === 'STEP_DOWN' ? 'Throttle / step-down' : F.describeAction(o);
        return '<li><b>' + esc(act.charAt(0).toUpperCase() + act.slice(1)) + '</b>' + (lost ? ' — loses ' + esc(lost) : '') +
          (typeof o.net_score_delta === 'number' ? ' <span class="mono dim">(net Δ ' + F.num(o.net_score_delta, 2) + ')</span>' : '') + '</li>';
      }).join('') + '</ol></div>';
    }
    function render(resp, label, body) {
      const data = resp.data || {};
      if (!resp.ok || !data.decision) {
        const err = data.error_code || data.status || ('HTTP ' + resp.status);
        ctx.openOverlay('<div class="res-head">' + F.badge('ERROR', true) + '<div class="res-meta"><b>' + esc(label) + '</b> · request rejected by the server</div></div>' +
          '<div class="res-headline">' + esc(data.message || data.detail || err) + '</div>' +
          (data.field_path ? '<div class="res-note">field: <span class="mono">' + esc(data.field_path) + '</span> · ' + esc(err) + '</div>' : '') +
          (body.candidate_manifest_yaml && /candidate_manifest_yaml/.test(JSON.stringify(data)) ? '<div class="res-note">This server build does not accept YAML yet — paste the card as JSON.</div>' : '') +
          F.rawDetails(data, 'Server response'));
        return;
      }
      const dec = data.decision;
      const v = dec.verdict;
      const ms = typeof dec.calculation_elapsed_ms === 'number' ? F.num(dec.calculation_elapsed_ms, 1) + ' ms' : '—';
      const mass = typeof data.candidate_total_mass_kg === 'number' ? F.num(data.candidate_total_mass_kg, 0) + ' kg' : '';
      const base = data.installed_before || body.installed_guest_ids || [];
      const ov = dec.jury_ship_overrides;
      const knobsTxt = [];
      if (body.seawater_temp_c !== undefined) knobsTxt.push(body.seawater_temp_c + ' °C water');
      if (body.power_reserve_w !== undefined) knobsTxt.push('1 genset (100 kW)');
      if (body.wind_kn !== undefined) knobsTxt.push(body.wind_kn + ' kn wind');
      if (body.sea_state !== undefined) knobsTxt.push('SS ' + body.sea_state);
      knobsTxt.push(body.at_port ? 'at port' : 'at sea');
      let html = '<div class="res-head">' + F.badge(v, true) + '<div class="res-meta"><b>' + esc(dec.candidate_guest_id || label) + '</b> ' + esc(mass) +
        ' into <b>' + esc(base.join('+') || 'live ship') + '</b><br><span class="mono">' + esc(knobsTxt.join(' · ')) + '</span><br>' +
        '<span class="mono">' + ms + ' / 30 s budget ' + (dec.within_30s_deadline ? '✓' : '✗') + ' · ' + (data.applied ? '<b class="ok-t">COMMITTED</b>' : 'dry-run') + '</span></div></div>';
      html += '<div class="res-headline">' + esc(F.admissionHeadline(data)) + '</div>';
      if (ov && ov.note) html += '<div class="res-note">⚑ ' + esc(ov.note) + '</div>';
      const bcs = dec.binding_constraints || [];
      if (bcs.length) {
        html += '<div class="res-sec"><div class="res-sec-t">Binding constraint' + (bcs.length > 1 ? 's' : '') + '</div><ul class="res-list">' + bcs.slice(0, 4).map(function (b) {
          return '<li class="bind">' + esc(F.plainConstraint(b)) + (b.detail ? '<br><span class="mono dim">' + esc(b.constraint_id) + ' · ' + esc(b.detail) + '</span>' : '') + '</li>';
        }).join('') + '</ul></div>';
      }
      html += optionsHtml(dec);
      const pl = dec.placement;
      if (pl && pl.subcomponent_positions) {
        html += '<div class="res-sec"><div class="res-sec-t">Placement chosen</div><div>' + Object.keys(pl.subcomponent_positions).map(function (k) {
          return '<span class="mono">' + esc(k) + '</span> → <b>' + esc(pl.subcomponent_positions[k]) + '</b>';
        }).join(' · ') + (pl.compute_node ? ' · compute <b>' + esc(pl.compute_node) + '</b>' : '') + (pl.initial_mode ? ' · mode <b>' + esc(pl.initial_mode) + '</b>' : '') + '</div></div>';
      }
      const og = dec.one_genset_down;
      if (og) {
        html += '<div class="res-sec"><div class="res-sec-t">With one genset down</div><div><b class="' + (og.holds === 'YES' ? 'ok-t' : og.holds === 'NO' ? 'bad-t' : 'warn-t') + '">' +
          esc(F.GENSET[og.holds] || og.holds) + '</b> — ' + esc(og.sentence || '') + '</div></div>';
      }
      if (v === 'NOT_NOW' || dec.next_window) {
        const nw = dec.next_window || {};
        html += '<div class="res-sec"><div class="res-sec-t">A′ next window</div><div>' + (typeof nw.start_s === 'number' ? 'opens at <b>' + F.T(nw.start_s) + '</b> ' : '') + esc(nw.reason || nw.sentence || (dec.next_window ? '' : 'none returned')) + '</div></div>';
      }
      const rc = dec.required_conditions || [];
      if (rc.length) html += '<div class="res-sec"><div class="res-sec-t">Required conditions</div><ul class="res-list">' + rc.map(function (c) { return '<li>' + esc(typeof c === 'string' ? c : JSON.stringify(c)) + '</li>'; }).join('') + '</ul></div>';
      html += F.rawDetails(data.decision, 'Full engine decision (JSON)');
      ctx.openOverlay(html);
      const slot = ghostSlot(dec);
      ctx.setGhost({ slot: slot, verdict: v, cand: dec.candidate_guest_id || label, binding: bcs.map(function (b) { return b.resource_class; }) });
      // the trial snapshot carries C(T) at the evaluated seawater temperature
      const cool = data.current_budget_snapshot && data.current_budget_snapshot.classes && data.current_budget_snapshot.classes.cooling;
      if (cool && body.seawater_temp_c !== undefined) {
        $('sstNote').textContent = 'engine C(' + F.num(body.seawater_temp_c) + ' °C) = ' + F.fix((cool.degraded_capacity || cool.capacity) / 1000, 1) + ' kW chilled water';
      }
    }

    async function run(commit) {
      const b = buildBody(commit);
      if (b.error) { $('admStatus').textContent = b.error; $('admStatus').className = 'hint bad-t'; return; }
      $('admStatus').textContent = 'engine evaluating…'; $('admStatus').className = 'hint';
      $('admRun').disabled = $('admCommit').disabled = true;
      ctx.setGhost({ slot: null });
      const t0 = performance.now();
      const resp = await SDV.requestApi('POST', '/api/admit_live', b.body, { merge: false });
      $('admRun').disabled = $('admCommit').disabled = false;
      ctx.refreshDisabled();
      if (!resp.ok && resp.status === 0) { $('admStatus').textContent = 'server unreachable'; $('admStatus').className = 'hint bad-t'; return; }
      $('admStatus').textContent = resp.ok ? 'verdict in ' + Math.round(performance.now() - t0) + ' ms round-trip' : 'rejected (' + resp.status + ')';
      $('admStatus').className = 'hint' + (resp.ok ? '' : ' bad-t');
      if (resp.ok && resp.data && resp.data.decision) ctx.rememberAdmission(resp.data);
      render(resp, b.label, b.body);
      ctx.afterAction(resp.ok && resp.data && resp.data.applied);
    }
    $('admRun').addEventListener('click', function () { run(false); });
    $('admCommit').addEventListener('click', function () { run(true); });
    setMode('catalog');
  };
})();

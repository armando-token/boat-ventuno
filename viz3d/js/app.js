/* viz3d/js/app.js — one-screen jury console: live state polling, header controls, gauges, decision log,
   schematic labels, transport. Every value shown is read from the live governor API. */
(function () {
  'use strict';
  const SDV = window.SDV;
  const F = SDV.fmt, P = SDV.panels, esc = F.esc;
  const $ = function (id) { return document.getElementById(id); };
  const END_T = 1800, STEP_T = 20;

  SDV.start = function () {
    const S = SDV.createScene();
    const app = {
      live: null, env: null, win: null, fc: null, conn: 'CONNECTING', simT: 0,
      playing: false, busy: false, polling: false, lastEnvMs: 0,
      admitCache: {}, openKeys: {}, seenKeys: null, ghost: null, flags: { layered: false }
    };

    // ---------------------------------------------------------------- connection state
    SDV.setConnectionState = function (st) {
      app.conn = st;
      const chip = $('chipLink');
      chip.className = 'chip ' + (st === 'LIVE' ? 'ok' : st === 'PREVIEW' ? 'cyan' : st === 'STALE' ? 'alarm' : '');
      chip.textContent = st === 'LIVE' ? '● LIVE engine' : st === 'PREVIEW' ? '◆ PREVIEW (not the engine)' : st === 'STALE' ? '● STALE' : '● Connecting…';
      const ban = $('staleBanner');
      ban.hidden = !(st === 'STALE' || st === 'PREVIEW');
      ban.classList.toggle('preview', st === 'PREVIEW');
      if (st === 'PREVIEW') $('staleBannerText').textContent = '◆ PREVIEW (not the engine) — ?preview=1 runs a local replay for layout checks only. Verdicts here are NOT the governor.';
      else $('staleBannerText').textContent = '⚠ STALE / DESCONECTADO — governor API unreachable. Controls disabled; showing the last verified snapshot (no local engine).';
      $('staleBannerMeta').textContent = 'v' + ((app.live && app.live.state_version) || 0) + ' · ' + F.T(app.simT);
      refreshDisabled();
    };
    function refreshDisabled() {
      const off = app.conn === 'STALE' || app.conn === 'CONNECTING';
      document.querySelectorAll('.right button, .right select, .right input, .right textarea, .transport button, .transport input, #segProfile button, #segCrew button, #btnDeck').forEach(function (el) {
        if (el.closest('#tabbar') || el.closest('details.raw')) return;
        el.disabled = off;
      });
    }
    function track(resp) {
      if (!resp) return resp;
      if (resp.connectionState === 'PREVIEW') SDV.setConnectionState('PREVIEW');
      else if (resp.status > 0 && app.conn !== 'LIVE') SDV.setConnectionState('LIVE');
      return resp;
    }
    const api = function (method, path, body, opts) { return SDV.requestApi(method, path, body, opts).then(track); };

    // ---------------------------------------------------------------- overlay (result card inside the stage)
    function openOverlay(html, wide) {
      $('overlayBody').innerHTML = html;
      const ov = $('overlay');
      ov.classList.toggle('wide', !!wide);
      ov.hidden = false;
      requestAnimationFrame(function () { S.setOverlayWidth(wide ? 0 : ov.offsetWidth); });
    }
    function closeOverlay() { $('overlay').hidden = true; S.setOverlayWidth(0); }
    $('ovClose').addEventListener('click', function () { closeOverlay(); setGhost({ slot: null }); });
    window.addEventListener('keydown', function (e) { if (e.key === 'Escape' && !$('overlay').hidden) { closeOverlay(); setGhost({ slot: null }); } });

    // ---------------------------------------------------------------- ghost preview of the candidate
    function setGhost(g) {
      app.ghost = g && g.slot ? g : null;
      S.vessel.showGhost(app.ghost ? app.ghost.slot : null, app.ghost ? app.ghost.verdict : null);
      renderLabels();
    }

    // ---------------------------------------------------------------- schematic labels (occupant + state from the engine)
    const SLOT_LABEL = { flight_deck: 'flightDeck', mast_3: 'mast', hangar_roof: 'hangarRoof', bay1: 'bay1', bay1_a: 'bay1', bay1_b: 'bay1', bay2: 'bay2', hangar: 'hangar', stern_ramp: 'sternRamp' };
    function guestState(gid) {
      const g = ((app.live && app.live.state && app.live.state.installed_guests) || {})[gid];
      if (!g) return null;
      const m = String(g.active_mode || '');
      const cls = (!g.powered_on || /STOP|OFF|SHED/.test(m)) ? 'alarm' : /DEGRADED|STANDBY|HOLD|LOW|REDUCED|CONTINUOUS/.test(m) ? 'warn' : '';
      return { mode: m, cls: cls, on: g.powered_on };
    }
    function occRows(list) {
      const ids = [];
      (list || []).forEach(function (s) { const id = String(s).split(':')[0]; if (ids.indexOf(id) < 0) ids.push(id); });
      let worst = '';
      const rows = ids.map(function (id) {
        const st = guestState(id);
        if (st && st.cls === 'alarm') worst = 'alarm'; else if (st && st.cls === 'warn' && worst !== 'alarm') worst = 'warn';
        return '<em>' + esc(id) + (st ? ' · ' + esc(st.mode.replace(/_/g, ' ').toLowerCase()) : '') + '</em>';
      });
      return { rows: rows.join('') || '<em class="free">free</em>', cls: ids.length ? worst : 'dim', ids: ids };
    }
    function renderLabels() {
      const live = app.live, snap = P.snapshot(live);
      const L = (snap && snap.classes && snap.classes.positions && snap.classes.positions.local_limits) || {};
      const M = (snap && snap.classes && snap.classes.mast && snap.classes.mast.local_limits) || {};
      const st = (live && live.state) || {};
      const binds = ((snap && snap.binding_witnesses) || []).map(function (w) { return w.resource_class; });
      const g = app.ghost, gl = g ? SLOT_LABEL[g.slot] : null;
      const kg = function (a, b) { return typeof a === 'number' ? ' <small>' + F.num(a, 0) + '/' + F.num(b, 0) + ' kg</small>' : ''; };
      const defs = [];
      function push(id, anchor, title, o, extra) {
        const d = Object.assign({ id: id, anchor: anchor, title: title, rows: o.rows, cls: o.cls }, extra || {});
        if (gl === id) {
          d.cls = 'ghost' + ((g.binding || []).some(function (c) { return c === 'positions' || c === 'mast'; }) ? ' binding' : '');
          d.rows = (d.rows || '') + '<em class="gh">+ ' + esc(g.cand) + ' · ' + esc(String(g.verdict).replace(/_/g, ' ')) + '</em>';
        }
        defs.push(d);
      }
      const b1 = occRows([].concat(L.bay1_20ft || [], L.bay1_a_10ft || [], L.bay1_b_10ft || []));
      push('bay1', 'bay1', 'Bay 1' + kg(L.bay1_total_mass_kg, L.bay1_max_mass_kg), b1, { dy: -6 });
      push('bay2', 'bay2', 'Bay 2' + kg(L.bay2_total_mass_kg, L.bay2_max_mass_kg), occRows(L.bay2_10ft), { dy: -6 });
      push('sternRamp', 'sternRamp', 'Stern ramp', occRows(L.stern_ramp), { side: 'down', dy: 70 });
      push('hangar', 'hangar', 'Hangar' + kg(L.hangar_total_mass_kg, L.hangar_max_mass_kg), occRows(L.hangar), { side: 'down', dy: 64 });
      const roof = occRows(L.hangar_roof);
      const rc = L.hangar_roof_drone_clearance;
      if (rc && rc.feasible === false) roof.cls = 'alarm';
      push('hangarRoof', 'hangarRoof', 'Hangar roof', roof);
      push('flightDeck', 'flightDeck', 'Flight deck' + (st.deck_clear === false ? ' <small class="bad-t">occupied</small>' : ''), occRows(L.flight_deck), { side: 'down', dy: 60 });
      const m3 = M.guest_slot_mast_3 || {};
      const mo = occRows(m3.owner ? [m3.owner] : []);
      mo.rows = '<em>M1 · M2 ship sensors</em>' + mo.rows.replace('<em>', '<em>M3 · ').replace('<em class="free">', '<em class="free">M3 · ');
      if (binds.indexOf('mast') >= 0) mo.cls = 'alarm binding';
      push('mast', 'mast', 'Mast slots', mo);
      const g3 = guestState('G3');
      if (g3) push('auv', 'auv', 'G3 AUV', { rows: '<em>' + esc(g3.mode.replace(/_/g, ' ').toLowerCase()) + '</em>', cls: g3.cls });
      const g4 = guestState('G4');
      if (g4) {
        const batt = ((st.installed_guests || {}).G4 || {}).battery_pct;
        push('drone', 'pad', 'G4 drone', { rows: '<em>' + esc(g4.mode.replace(/_/g, ' ').toLowerCase()) + (typeof batt === 'number' ? ' · ' + F.num(batt, 0) + '%' : '') + '</em>', cls: g4.cls });
      }
      if (typeof st.power_reserve_w === 'number') {
        push('gens', 'dg2', 'Gensets', { rows: '<em>' + (st.power_reserve_w < 200000 ? '1 online · ' : '2 online · ') + F.num(st.power_reserve_w / 1000, 0) + ' kW reserve</em>', cls: st.power_reserve_w < 200000 ? 'alarm' : '' }, { xray: true });
      }
      S.setLabels(defs);
    }

    // ---------------------------------------------------------------- render everything from the live payload
    let viewFrom = 0, viewTo = 0, viewWall = 0;
    function renderAll() {
      const live = app.live;
      if (!live) return;
      const st = live.state || {};
      app.simT = typeof live.sim_time_s === 'number' ? live.sim_time_s : (st.sim_time_s || 0);
      $('chipClock').textContent = F.T(app.simT);
      $('tlLabel').textContent = F.T(app.simT) + ' / 30:00 (20×)';
      if (!app.scrubbing) $('scrub').value = Math.round(app.simT / STEP_T);
      $('kSpeed').innerHTML = typeof st.ship_speed_kn === 'number' ? F.num(st.ship_speed_kn) + '<small> kn</small>' : '—';
      $('kHdg').innerHTML = typeof st.heading_deg === 'number' ? F.num(st.heading_deg, 0) + '<small>°</small>' : '—';
      $('kSst').innerHTML = typeof st.seawater_temp_c === 'number' ? F.num(st.seawater_temp_c) + '<small> °C</small>' : '—';
      $('kSst').title = 'provenance: ' + (st.seawater_temp_provenance || '—');
      if (typeof st.power_reserve_w === 'number') {
        const one = st.power_reserve_w < 200000;
        $('kGen').innerHTML = (one ? '1' : '2') + '<small> · ' + F.num(st.power_reserve_w / 1000, 0) + ' kW</small>';
        $('kGen').parentNode.classList.toggle('alarm', one);
      }
      $('kMode').textContent = String(st.vessel_mode || '—').replace(/_/g, ' ');
      $('kMode').parentNode.classList.toggle('warn', !!st.cooling_loop_degraded);

      const g = P.renderGauges($('gauges'), live, app.fc, app.simT);
      $('govState').textContent = !g.have ? '—' : g.binding ? g.binding + ' binding' : g.warn ? g.warn + ' near limit' : 'all feasible';
      $('govState').className = 'chip sm ' + (g.binding ? 'alarm' : g.warn ? 'warn' : 'ok');

      const entries = P.logEntries(live, app.simT);
      const seen = app.seenKeys;
      P.renderLog($('log'), entries, app.admitCache, app.openKeys, seen);
      app.seenKeys = {};
      $('log').querySelectorAll('details').forEach(function (d) { app.seenKeys[d.getAttribute('data-k')] = 1; });
      $('logCount').textContent = entries.length + ' entries';
      $('fcLine').innerHTML = P.forecastLine(app.fc, app.simT);

      P.renderCrew($('crewBox'), live);
      P.renderC54($('c54Table'), live);
      SDV.tabs.renderPrioMini(live);
      document.querySelectorAll('#segCrew button').forEach(function (b) { b.classList.toggle('active', Number(b.getAttribute('data-crew')) === st.crew_count); });
      $('btnDeck').classList.toggle('on', st.deck_clear !== false);
      $('btnDeck').textContent = st.deck_clear !== false ? 'Deck clear' : 'Deck occupied';
      const pid = String(live.priority_profile_id || '').toLowerCase();
      let known = false;
      document.querySelectorAll('#segProfile button').forEach(function (b) {
        const on = b.getAttribute('data-profile') === pid; known = known || on; b.classList.toggle('active', on);
      });
      if (!known && pid) { $('btnProfileCustom').classList.add('active'); $('btnProfileCustom').title = 'Live custom profile: ' + pid; }
      $('radarState').textContent = st.own_radar_policy || '—';
      $('btnQuietRadio').classList.toggle('on', st.own_radar_policy === 'QUIET_RADIO');
      renderMilestones(live);
      renderLabels();
      if (app.conn === 'STALE' || app.conn === 'PREVIEW') $('staleBannerMeta').textContent = 'v' + (live.state_version || 0) + ' · ' + F.T(app.simT);

      viewFrom = viewTo; viewTo = app.simT; viewWall = performance.now();
      if (!app.playing) viewFrom = viewTo;
      try { S.view = SDV.deriveFromAuthoritative(live, app.simT); } catch (e) { /* 3D animation only */ }
    }
    $('log').addEventListener('toggle', function (e) {
      const d = e.target; if (!d || d.tagName !== 'DETAILS' || !d.getAttribute('data-k')) return;
      if (d.open) app.openKeys[d.getAttribute('data-k')] = 1; else delete app.openKeys[d.getAttribute('data-k')];
    }, true);

    function renderMilestones(live) {
      const r = live.scenario_B_replay || {};
      $('msFill').style.width = (100 * Math.min(1, app.simT / END_T)).toFixed(2) + '%';
      const raw = [];
      (r.decisions || []).forEach(function (d) {
        const lab = d.phase === 'D3_ARBITRATION' ? 'D3' : d.phase === 'D4_DEGRADATION' ? 'D4 trip' : d.phase === 'D4_RECONNECTION' ? 'reconnect' : d.phase;
        raw.push({ t: d.sim_time_s, l: lab, c: d.phase === 'D4_DEGRADATION' ? 'alarm' : d.phase === 'D3_ARBITRATION' ? 'warn' : 'ok' });
        const dd = d.decision || {};
        if (d.phase === 'D3_ARBITRATION' && typeof dd.hold_cmd_time_s === 'number') raw.push({ t: dd.hold_cmd_time_s, l: 'hold', c: 'warn' });
      });
      if (typeof r.executed_touchdown_time_s === 'number') raw.push({ t: r.executed_touchdown_time_s, l: 'touchdown', c: 'ok' });
      const byT = {};
      raw.forEach(function (m) {
        if (typeof m.t !== 'number' || m.t > END_T) return;
        const k = String(m.t);
        if (!byT[k]) byT[k] = { t: m.t, l: [], c: m.c };
        byT[k].l.push(m.l);
        if (m.c === 'alarm') byT[k].c = 'alarm';
      });
      const html = Object.keys(byT).map(function (k) { return byT[k]; }).sort(function (a, b) { return a.t - b.t; }).map(function (m) {
        return '<span class="ms-mark ' + m.c + (m.t <= app.simT ? ' past' : '') + '" style="left:' + (100 * m.t / END_T).toFixed(2) + '%" title="' + esc(m.l.join(' + ')) + ' at ' + F.T(m.t) + '"><i></i><span>' + esc(m.l.join(' + ')) + ' ' + F.T(m.t).slice(2) + '</span></span>';
      }).join('');
      const track = $('msTrack');
      track.querySelectorAll('.ms-mark').forEach(function (n) { n.remove(); });
      track.insertAdjacentHTML('beforeend', html);
      $('msSha').textContent = (r.priority_profile_id ? String(r.priority_profile_id).toLowerCase().replace('_primary', '') + ' · ' : '') + (r.semantic_sha256 ? 'sha ' + String(r.semantic_sha256).slice(0, 8) : '');
    }

    // ---------------------------------------------------------------- polling (one in flight at a time)
    async function poll(force) {
      if (app.polling || (!force && (app.playing || app.busy))) return;
      app.polling = true;
      try {
        const now = performance.now();
        const reqs = [api('GET', '/api/state'), api('GET', '/api/forecast', null, { merge: false })];
        const envDue = force || now - app.lastEnvMs > 15000;
        if (envDue) {
          reqs.push(api('GET', '/api/environment', null, { merge: false }));
          reqs.push(api('GET', '/api/environment/windows?guest=G4&op=VEHICLE_REPLACEMENT_AT_SEA', null, { merge: false }));
        }
        const res = await Promise.all(reqs);
        if (res[0].ok && res[0].data) app.live = SDV.apiState.lastPayload || res[0].data;
        if (res[1].ok && res[1].data) app.fc = res[1].data;
        if (envDue) {
          app.lastEnvMs = now;
          app.env = res[2].ok ? res[2].data : null;
          app.win = res[3].ok ? res[3].data : null;
          P.renderEnv(app.env, app.win);
        }
        renderAll();
      } finally { app.polling = false; }
    }

    async function afterAction(full) {
      if (full !== false) await poll(true);
    }
    function rememberAdmission(data) {
      const log = data.decision_log || [];
      for (let i = log.length - 1; i >= 0; i--) {
        const e = log[i];
        if (e.type === 'ADMISSION_ATTEMPT') { app.admitCache[(e.candidate_guest_id || '') + '|' + (e.verdict || '') + '|' + (e.state_version || '')] = data; break; }
      }
      const d = data.decision;
      app.admitCache[(d.candidate_guest_id || '') + '|' + (d.verdict || '') + '|' + (data.state_version || '')] = data;
    }

    // ---------------------------------------------------------------- header controls
    document.querySelectorAll('#segProfile button').forEach(function (b) {
      b.addEventListener('click', async function () {
        const pid = b.getAttribute('data-profile');
        if (pid === 'custom') { SDV.tabs.show('prio'); $('customDetails').open = true; return; }
        pause();
        app.busy = true;
        await api('POST', '/api/profile', { priority_profile_id: pid });
        app.busy = false;
        await poll(true);
      });
    });
    document.querySelectorAll('#segCrew button').forEach(function (b) {
      b.addEventListener('click', async function () {
        app.busy = true;
        const st = (app.live && app.live.state) || {};
        await api('POST', '/api/crew', { crew_count: Number(b.getAttribute('data-crew')), deck_clear: st.deck_clear !== false });
        app.busy = false;
        await poll(true);
      });
    });
    $('btnDeck').addEventListener('click', async function () {
      const st = (app.live && app.live.state) || {};
      app.busy = true;
      await api('POST', '/api/crew', { crew_count: typeof st.crew_count === 'number' ? st.crew_count : 14, deck_clear: st.deck_clear === false });
      app.busy = false;
      await poll(true);
    });
    document.querySelectorAll('#segTime button').forEach(function (b) {
      b.addEventListener('click', function () {
        S.setTime(b.getAttribute('data-time'));
        document.querySelectorAll('#segTime button').forEach(function (x) { x.classList.toggle('active', x === b); });
      });
    });

    // ---------------------------------------------------------------- views menu
    $('btnViews').addEventListener('click', function (e) {
      e.stopPropagation();
      const m = $('viewsMenu'); m.hidden = !m.hidden; $('btnViews').setAttribute('aria-expanded', String(!m.hidden));
    });
    document.addEventListener('click', function (e) { if (!e.target.closest('#viewsMenu')) $('viewsMenu').hidden = true; });
    document.querySelectorAll('#segCam button').forEach(function (b) {
      b.addEventListener('click', function () { S.setCam(b.getAttribute('data-cam')); });
    });
    $('tXray').addEventListener('click', function () { S.setXray(!S.vessel.isXray()); });
    $('tLabels').addEventListener('click', function () { S.labelsOn = !S.labelsOn; $('tLabels').classList.toggle('on', S.labelsOn); });
    $('tEco').addEventListener('click', function () { S.setEco(!S.eco); });
    $('tLivery').addEventListener('click', function () { S.setLivery(!S.vessel.root.userData.civil); });

    // ---------------------------------------------------------------- transport (Scenario B, 20 s per tick)
    function setPlayBtn() { $('btnPlay').textContent = app.playing ? '❚❚ Pause' : '▶ Play 90 s Scenario B'; $('btnPlay').classList.toggle('on', app.playing); }
    function pause() { app.playing = false; setPlayBtn(); }
    async function advanceTo(target) {
      const live = app.live || {};
      const resp = await api('POST', '/api/advance', {
        target_sim_time_s: target, from_sim_time_s: app.simT, expected_state_version: live.state_version,
        priority_profile_id: profile(), runner_status: app.playing ? 'RUNNING' : 'PAUSED'
      });
      if (resp.ok && SDV.apiState.lastPayload) app.live = SDV.apiState.lastPayload;
      else if (resp.status === 409 || resp.status === 422) await poll(true);
      if (resp.status === 0) pause();
      renderAll();
      return resp;
    }
    async function reset() {
      pause();
      app.busy = true;
      await api('POST', '/api/reset', { priority_profile_id: profile() });
      app.busy = false;
      app.flags.layered = false; $('layeredState').textContent = 'A.5 acoustic · OFF'; $('btnLayered').classList.remove('on');
      app.seenKeys = null;
      closeOverlay(); setGhost({ slot: null });
      await poll(true);
    }
    function profile() {
      const pid = app.live && app.live.priority_profile_id;
      return pid ? String(pid) : 'survey_primary';
    }
    let lastStepWall = 0, stepping = false;
    S.onFrame = function (now) {
      if (!app.playing || stepping || now - lastStepWall < 950) return;
      if (app.simT >= END_T) { pause(); return; }
      lastStepWall = now; stepping = true;
      advanceTo(Math.min(END_T, app.simT + STEP_T)).finally(function () { stepping = false; });
    };
    $('btnPlay').addEventListener('click', async function () {
      if (app.playing) { pause(); return; }
      if (app.simT >= END_T) await reset();
      closeOverlay(); setGhost({ slot: null });
      app.playing = true; setPlayBtn();
    });
    $('btnStep').addEventListener('click', function () { pause(); if (app.simT < END_T) advanceTo(Math.min(END_T, app.simT + STEP_T)); });
    $('btnReset').addEventListener('click', reset);
    $('scrub').addEventListener('input', function () { app.scrubbing = true; $('tlLabel').textContent = F.T(Number($('scrub').value) * STEP_T) + ' / 30:00 (20×)'; });
    $('scrub').addEventListener('change', async function () {
      pause();
      const target = Number($('scrub').value) * STEP_T;
      app.scrubbing = false;
      if (target < app.simT) {
        await reset();
        if (target > 0) await advanceTo(target);
      } else if (target > app.simT) await advanceTo(target);
    });

    // ---------------------------------------------------------------- recording (secondary)
    let rec = null, chunks = [];
    $('btnRec').addEventListener('click', function () {
      const b = $('btnRec');
      if (rec && rec.state === 'recording') { rec.stop(); b.textContent = '● Rec'; b.classList.remove('danger'); return; }
      if (!S.canvas.captureStream || typeof MediaRecorder === 'undefined') return;
      chunks = []; rec = new MediaRecorder(S.canvas.captureStream(30));
      rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
      rec.onstop = function () {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
        a.download = 'sdv-schematic.webm'; a.click();
      };
      rec.start(); b.textContent = '■ Stop'; b.classList.add('danger');
    });
    window.addEventListener('keydown', function (e) {
      if (e.target && /SELECT|INPUT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === ' ') { e.preventDefault(); $('btnPlay').click(); }
      else if (e.key === 'x' || e.key === 'X') $('tXray').click();
    });

    // ---------------------------------------------------------------- wire tabs and start
    const ctx = {
      openOverlay: openOverlay, closeOverlay: closeOverlay, setGhost: setGhost, afterAction: afterAction,
      rememberAdmission: rememberAdmission, refreshDisabled: refreshDisabled, reset: reset, pause: pause,
      isPlaying: function () { return app.playing; }, profile: profile, flags: app.flags,
      state: function () { return app.live && app.live.state; }
    };
    SDV.tabs.init(ctx);
    SDV.jury.init(ctx);
    SDV.app = app;
    SDV.setConnectionState(SDV.isPreviewRequested && SDV.isPreviewRequested() ? 'PREVIEW' : 'CONNECTING');
    setPlayBtn();
    S.run();
    poll(true);
    setInterval(function () { poll(false); }, 2500);
  };
})();

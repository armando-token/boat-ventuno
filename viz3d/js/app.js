/* viz3d/js/app.js — Authoritative 3D scene wiring, camera rig, synchronized clock & STALE banner (ALIGN-T37..T41). */
(function () {
  'use strict';
  const THREE = window.THREE, SDV = window.SDV;
  const $ = function (id) { return document.getElementById(id); };
  const clamp = SDV.clamp;

  const PRESETS = {
    hero:      { th: 0.42,  ph: 1.25, r: 70, tx: 1,   ty: 3.2,  tz: 2 },
    blueprint: { th: 0.0,   ph: 1.49, r: 72, tx: 0,   ty: 4.0,  tz: 0 },
    subsea:    { th: -0.35, ph: 1.43, r: 46, tx: -6,  ty: -1.2, tz: 4 },
    bow:       { th: 1.05,  ph: 1.36, r: 34, tx: 22,  ty: 3.5,  tz: 0 },
    bridge:    { th: 0.78,  ph: 1.26, r: 30, tx: 8,   ty: 11.5, tz: 0 },
    deck:      { th: -0.75, ph: 1.15, r: 30, tx: -20, ty: 4.2,  tz: 0 },
    stern:     { th: -2.15, ph: 1.44, r: 32, tx: -24, ty: -0.5, tz: 0 },
    plan:      { th: 0.0,   ph: 0.12, r: 92, tx: 0,   ty: 0,    tz: 0 },
    pad:       { th: 0.65,  ph: 1.18, r: 24, tx: 2,   ty: 8.0,  tz: 0 },
    cutaway:   { th: 0.32,  ph: 1.08, r: 52, tx: -2,  ty: 1.5,  tz: 0 }
  };
  const MODE_TEXT = {
    QUIET_SURVEY: 'Mission 1: Quiet Seabed Survey — G3 AUV scanning seafloor · G4 Drone on base',
    UAV_RECOVERY_HEADING: 'Mission 3: Turning into wind — G4 Drone returning to land on helideck',
    DEGRADED_POWER: 'Mission 4: DG2 Power Degraded — Governor stepping down G5 to protect G3',
    DEGRADED_THERMAL: 'Thermal Derating Active — Chilled water loop at 30 °C (−40 %)'
  };

  SDV.start = function () {
    const stage = $('stage'); let canvas = $('gl');
    let renderer = null, lastErr = null;
    const attempts = [
      { antialias: true, powerPreference: 'high-performance' },
      { antialias: true },
      { antialias: false },
      { antialias: false, failIfMajorPerformanceCaveat: false, alpha: true, stencil: false, depth: true }
    ];
    for (let i = 0; i < attempts.length && !renderer; i++) {
      try {
        let cv = canvas;
        if (i > 0) { cv = canvas.cloneNode(false); canvas.parentNode.replaceChild(cv, canvas); }
        renderer = new THREE.WebGLRenderer(Object.assign({ canvas: cv }, attempts[i]));
      } catch (e) { lastErr = e; renderer = null; }
    }
    if (!renderer) {
      throw new Error('This browser refused to create a WebGL context (' + (lastErr && lastErr.message) + ').');
    }
    canvas = renderer.domElement;
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(36, 1, 1.4, 6500);
    const env = SDV.createEnvironment(scene, renderer);
    const vessel = SDV.createVessel();
    scene.add(vessel.root);

    let eco = false;
    function resize() {
      const w = Math.max(320, stage.clientWidth), h = Math.max(240, stage.clientHeight);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, eco ? 1 : 2.0));
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    if (window.ResizeObserver) new ResizeObserver(resize).observe(stage); else window.addEventListener('resize', resize);
    resize();

    // ------------------------------------------------------------ camera rig
    const rig = { cur: Object.assign({}, PRESETS.hero), des: Object.assign({}, PRESETS.hero), drag: false, pan: false, lx: 0, ly: 0, orbit: false, director: false };
    function setCam(name, keepDirector) {
      const p = PRESETS[name]; if (!p) return;
      Object.assign(rig.des, p);
      if (env.setUnderwaterView) env.setUnderwaterView(name === 'subsea');
      if (!keepDirector) { rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on'); }
      document.querySelectorAll('#segCam button').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-cam') === name); });
    }
    canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    canvas.addEventListener('pointerdown', function (e) {
      rig.drag = true; rig.pan = e.button === 2 || e.shiftKey; rig.lx = e.clientX; rig.ly = e.clientY;
      rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on');
      canvas.classList.add('drag');
      try { canvas.setPointerCapture(e.pointerId); } catch (_err) { /* ignore */ }
    });
    window.addEventListener('pointerup', function () { rig.drag = false; canvas.classList.remove('drag'); });
    window.addEventListener('pointermove', function (e) {
      if (!rig.drag) return;
      const dx = e.clientX - rig.lx, dy = e.clientY - rig.ly;
      rig.lx = e.clientX; rig.ly = e.clientY;
      const d = rig.des;
      if (rig.pan) {
        const k = d.r * 0.0016;
        d.tx = clamp(d.tx - dx * k * Math.cos(d.th), -70, 70);
        d.tz = clamp(d.tz + dx * k * Math.sin(d.th), -70, 70);
        d.ty = clamp(d.ty + dy * k, -4, 30);
      } else {
        d.th -= dx * 0.006;
        d.ph = clamp(d.ph - dy * 0.005, 0.08, 1.51);
      }
    });
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      rig.des.r = clamp(rig.des.r * (1 + e.deltaY * 0.0012), 14, 190);
    }, { passive: false });

    // ------------------------------------------------------------ Authoritative State & Clock Interpolation
    let activeProfile = 'survey_primary';
    let playing = false;
    let advanceInFlight = false;
    let lastAdvanceWallMs = 0;
    let currentRunId = '';
    let interpFromSimT = 0;
    let interpToSimT = 0;
    let interpStartWallMs = performance.now();

    let view = SDV.deriveFromAuthoritative({
      run_id: 'RUN_INIT',
      snapshot_version: 1,
      sim_time_s: 0,
      runner_status: 'PAUSED'
    }, 0);
    let seaS = view.seaState, labelsOn = true;

    function applyAuthoritativePayload(payload, forceSnapClock) {
      if (!payload) return;
      const newRunId = payload.run_id || 'RUN_LIVE';
      const newSimT = typeof payload.sim_time_s === 'number'
        ? payload.sim_time_s
        : (typeof payload.current_sim_time_s === 'number' ? payload.current_sim_time_s : 0);

      // Invalidate interpolation buffers on run_id change or reset or backward jump (ALIGN-T40)
      if (forceSnapClock || newRunId !== currentRunId || newSimT < interpToSimT) {
        currentRunId = newRunId;
        interpFromSimT = newSimT;
        interpToSimT = newSimT;
        interpStartWallMs = performance.now();
      } else if (newSimT > interpToSimT) {
        interpFromSimT = interpToSimT;
        interpToSimT = newSimT;
        interpStartWallMs = performance.now();
      }
      if (payload.priority_profile_id && $('profile')) {
        activeProfile = payload.priority_profile_id;
        $('profile').value = activeProfile;
      }
      if (payload.runner_status === 'COMPLETED' && playing) {
        playing = false;
        $('btnPlay').textContent = '▶ Play';
      }
      view = SDV.deriveFromAuthoritative(payload, interpToSimT);
      renderUI();
      directorShot();
    }

    // ------------------------------------------------------------ 3D HUD Labels (ALIGN-T37: separate mast_3 G1 vs hangar_roof G5)
    const labelDefs = [
      { k: 'auv', t: function () { return 'G3 Seabed AUV'; }, e: function () { return view.g3 === 'HOLD_ACOUSTIC' ? 'HOLD STATION' : 'SONAR SURVEY'; }, s: function () { return view.g3 === 'HOLD_ACOUSTIC' ? 'warn' : ''; } },
      { k: 'pad', t: function () { return 'G4 VTOL Drone'; }, e: function () {
        if (view.drone.mode === 'landed') return 'LANDED · ' + view.drone.batt + '%';
        if (view.drone.mode === 'recover') return 'RTB LANDING · ' + view.drone.batt + '%';
        return 'PATROL · BATT ' + view.drone.batt + '%';
      }, s: function () { return view.drone.mode === 'recover' ? 'warn' : ''; } },
      { k: 'target', t: function () { return 'SAR Distress Buoy'; }, e: function () { return view.drone.mode === 'patrol' ? 'SCANNING' : 'BEACON'; }, s: function () { return 'warn'; } },
      { k: 'pods', t: function () { return '2× Pod + Shaft + Rudder'; }, e: function () { return view.speedKn.toFixed(1) + ' kn'; }, s: function () { return ''; } },
      { k: 'bay1', t: function () { return 'Bay 1 · G3 LARS'; }, e: function () { return '5.5 t · ' + view.g3; }, s: function () { return view.g3 === 'HOLD_ACOUSTIC' ? 'warn' : ''; } },
      { k: 'bay2', t: function () { return 'Bay 2 · G5 Edge'; }, e: function () { return view.g5 === 'FULL_SPECTRUM' ? '45 kW' : '8 kW'; }, s: function () { return view.g5Degraded ? 'warn' : ''; } },
      { k: 'mast', t: function () { return 'Mast 3 · ' + (view.mast3Owner || 'G1 (G1_MAST_UNIT)'); }, e: function () { return '45 kg (×3.0)'; }, s: function () { return ''; } },
      { k: 'hangarRoof', t: function () { return 'Hangar Roof · ' + (view.hangarRoofOccupant || 'G5:G5_MAST_ANTENNA'); }, e: function () { return '25 kg (×1.2)'; }, s: function () { return view.g5Degraded ? 'warn' : ''; } },
      { k: 'gun', t: function () { return vessel.root.userData.civil ? 'Fire Monitor' : '76mm Bow Mount'; }, e: function () { return ''; }, s: function () { return ''; } },
      { k: 'dg2', t: function () { return 'DG2 · Reserve'; }, e: function () { return view.gen2Tripped ? 'TRIPPED (100 kW)' : '200 kW nominal'; }, s: function () { return view.gen2Tripped ? 'alarm' : ''; }, x: true },
      { k: 'rack', t: function () { return 'Compute Rack'; }, e: function () { return '4 nodes'; }, s: function () { return view.coolingDegraded ? 'warn' : ''; }, x: true }
    ];
    labelDefs.forEach(function (d) {
      d.el = document.createElement('div'); d.el.className = 'tag'; d.el.style.opacity = 0;
      $('labels').appendChild(d.el);
    });
    function refreshLabelText() {
      labelDefs.forEach(function (d) {
        d.el.className = 'tag ' + d.s();
        const em = d.e();
        d.el.innerHTML = '<i></i>' + d.t() + (em ? ' <em>' + em + '</em>' : '');
      });
    }
    const tmpV = new THREE.Vector3();
    function updateLabels() {
      const w = stage.clientWidth, h = stage.clientHeight;
      vessel.root.updateMatrixWorld();
      labelDefs.forEach(function (d) {
        const a = vessel.anchors[d.k];
        if (!a || !labelsOn || (d.x && !vessel.isXray())) { d.el.style.opacity = 0; return; }
        tmpV.set(a[0], a[1], a[2]); vessel.root.localToWorld(tmpV); tmpV.project(camera);
        const vis = tmpV.z > -1 && tmpV.z < 1 && Math.abs(tmpV.x) < 1.02 && Math.abs(tmpV.y) < 1.02;
        d.el.style.opacity = vis ? 1 : 0;
        if (vis) d.el.style.transform = 'translate(' + ((tmpV.x * 0.5 + 0.5) * w).toFixed(1) + 'px,' + ((-tmpV.y * 0.5 + 0.5) * h).toFixed(1) + 'px) translate(-50%,-135%)';
      });
    }

    // ------------------------------------------------------------ UI & STALE Banner Rendering
    const fmtT = function (s) { return String(Math.floor(s / 60)).padStart(2, '0') + ':' + String(Math.floor(s % 60)).padStart(2, '0'); };
    const nice = function (m) { const s = String(m || '').replace(/_/g, ' ').toLowerCase(); return s.charAt(0).toUpperCase() + s.slice(1); };

    function updateConnectionBanner() {
      const conn = SDV.apiState.connectionState;
      const chip = $('chipLink');
      const banner = $('staleBanner');
      const isStale = conn === 'STALE';
      if (chip) {
        if (conn === 'LIVE') {
          chip.textContent = '● LIVE GOVERNOR (' + SDV.apiState.bridgeMode + ')';
          chip.className = 'chip ok';
        } else if (conn === 'PREVIEW_SYNTHETIC') {
          chip.textContent = '● PREVIEW SYNTHETIC';
          chip.className = 'chip warn';
        } else if (isStale) {
          chip.textContent = '● STALE / DESCONECTADO';
          chip.className = 'chip alarm';
        }
      }
      if (banner) {
        banner.hidden = !isStale;
        if (isStale && $('staleBannerMeta')) {
          $('staleBannerMeta').textContent = 'v' + view.version + ' · ' + view.snapshotId + ' · T+' + fmtT(view.simT);
        }
      }
      const controls = document.querySelectorAll('[data-inject], #admRun, #btnPlay, #btnReset, #scrub, #profile, #segMission button');
      controls.forEach(function (el) { el.disabled = isStale; });
    }

    function renderUI() {
      updateConnectionBanner();
      const exactSec = Math.min(1800, Math.round(view.simExact));
      $('chipClock').textContent = 'T+' + fmtT(exactSec);
      if ($('chipVersion')) $('chipVersion').textContent = 'v' + view.version + ' · ' + view.snapshotId;
      $('chipSea').textContent = 'Sea state ' + view.seaState;
      $('chipWind').textContent = 'Wind ' + view.windKn + ' kn';
      $('kSpeed').innerHTML = view.speedKn.toFixed(1) + '<small>kn</small>';
      $('kHdg').innerHTML = String(Math.round(view.heading)).padStart(3, '0') + '<small>°</small>';
      $('kMode').textContent = nice(view.mode);
      $('tlLabel').textContent = fmtT(exactSec) + ' / ' + fmtT(1800);
      $('scrub').value = String(view.tick);

      document.querySelectorAll('#segMission button').forEach(function (b) {
        b.classList.toggle('active', b.getAttribute('data-mission') === view.missionId);
      });

      $('classes').innerHTML = view.classes.map(function (c) {
        const cls = c.bound ? 'a' : c.pct > 80 ? 'w' : '';
        return '<div class="cls"><div class="row"><span>' + c.label + '</span><span>' + c.text + '</span></div><div class="meter"><i class="' + cls + '" style="width:' + c.pct + '%"></i></div></div>';
      }).join('');

      const hasAlarm = view.alerts.some(function (a) { return a.lvl === 'alarm'; });
      const hasWarn = view.alerts.length > 0;
      const gs = $('govState');
      gs.className = 'chip ' + (hasAlarm ? 'alarm' : hasWarn ? 'warn' : 'ok');
      gs.textContent = hasAlarm ? 'Constrained' : hasWarn ? 'Adapting' : 'Nominal';
      $('alerts').innerHTML = view.alerts.length
        ? view.alerts.map(function (a) { return '<div class="alert ' + a.lvl + '"><b>' + a.code + '</b><span>' + a.msg + '</span></div>'; }).join('')
        : '<div class="alert ok"><b>NOMINAL</b><span>All eight resource classes are within their envelope.</span></div>';

      const ev = view.events.length ? view.events.join(' · ') : (MODE_TEXT[view.mode] || nice(view.mode));
      $('eventMsg').textContent = ev;
      refreshLabelText();
    }

    function directorShot() {
      if (!rig.director) return;
      const t = view.simT;
      let shot = 'hero', x = false;
      if (t >= 120 && t < 360) shot = 'hero';
      else if (t >= 360 && t < 560) shot = 'subsea';
      else if (t >= 560 && t < 720) shot = 'blueprint';
      else if (t >= 720 && t < 840) { shot = 'cutaway'; x = true; }
      else if (t >= 840 && t < 1150) shot = 'pad';
      setCam(shot, true);
      vessel.setXray(x); $('tXray').classList.toggle('on', x);
    }

    async function syncFromBackend(forceSnap) {
      const resp = await SDV.requestApi('GET', '/api/state?priority_profile=' + encodeURIComponent(activeProfile));
      if (resp.ok && resp.data) {
        applyAuthoritativePayload(resp.data, !!forceSnap);
      } else {
        updateConnectionBanner();
      }
    }

    async function advanceTargetSimTime(targetSimS) {
      if (advanceInFlight) return;
      advanceInFlight = true;
      try {
        if (targetSimS < view.simT) {
          await SDV.requestApi('POST', '/api/reset', { priority_profile_id: activeProfile });
        }
        const r = await SDV.requestApi('POST', '/api/advance', {
          target_sim_time_s: targetSimS,
          from_sim_time_s: view.simT,
          expected_state_version: view.version,
          priority_profile_id: activeProfile,
          runner_status: playing ? 'RUNNING' : 'PAUSED'
        });
        if (r.ok) await syncFromBackend(targetSimS <= view.simT);
        else updateConnectionBanner();
      } finally {
        advanceInFlight = false;
      }
    }

    // ------------------------------------------------------------ Interactive Mission Mode Selector (#segMission)
    async function selectMission(mName) {
      rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on');
      vessel.setXray(false); $('tXray').classList.remove('on');
      if (mName === 'survey') {
        setCam('subsea', true);
        await advanceTargetSimTime(0);
      } else if (mName === 'sortie') {
        setCam('hero', true);
        await advanceTargetSimTime(140);
      } else if (mName === 'rtb') {
        setCam('pad', true);
        await advanceTargetSimTime(840);
      } else if (mName === 'fault') {
        vessel.setXray(true); $('tXray').classList.add('on');
        setCam('cutaway', true);
        await advanceTargetSimTime(720);
      }
    }
    document.querySelectorAll('#segMission button').forEach(function (b) {
      b.addEventListener('click', function () { selectMission(b.getAttribute('data-mission')); });
    });

    // ------------------------------------------------------------ Camera & Environment Controls
    document.querySelectorAll('#segCam button').forEach(function (b) {
      b.addEventListener('click', function () {
        setCam(b.getAttribute('data-cam'));
        vessel.setXray(false); $('tXray').classList.remove('on');
      });
    });
    document.querySelectorAll('#segLivery button').forEach(function (b) {
      b.addEventListener('click', function () {
        const n = b.getAttribute('data-livery'); vessel.setLivery(n); vessel.root.userData.civil = n === 'civil';
        document.querySelectorAll('#segLivery button').forEach(function (x) { x.classList.toggle('active', x === b); });
        refreshLabelText();
      });
    });
    document.querySelectorAll('#segTime button').forEach(function (b) {
      b.addEventListener('click', function () {
        const n = b.getAttribute('data-time'); env.setMode(n); vessel.setNight(n === 'night');
        document.querySelectorAll('#segTime button').forEach(function (x) { x.classList.toggle('active', x === b); });
      });
    });
    $('tXray').addEventListener('click', function () {
      rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on');
      const on = !vessel.isXray(); vessel.setXray(on); $('tXray').classList.toggle('on', on);
      if (on) setCam('cutaway', true);
    });
    $('tLabels').addEventListener('click', function () { labelsOn = !labelsOn; $('tLabels').classList.toggle('on', labelsOn); });
    $('tOrbit').addEventListener('click', function () { rig.orbit = !rig.orbit; rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on'); $('tOrbit').classList.toggle('on', rig.orbit); });
    $('tEco').addEventListener('click', function () { eco = !eco; $('tEco').classList.toggle('on', eco); resize(); });
    if ($('tDirector')) {
      $('tDirector').addEventListener('click', function () { rig.director = !rig.director; $('tDirector').classList.toggle('on', rig.director); if (rig.director) directorShot(); });
    }

    $('btnPlay').addEventListener('click', async function () {
      playing = !playing;
      $('btnPlay').textContent = playing ? '❚❚ Pause' : '▶ Play';
      lastAdvanceWallMs = performance.now();
      if (!playing) {
        interpFromSimT = view.simT;
        interpToSimT = view.simT;
      }
    });
    $('btnReset').addEventListener('click', async function () {
      playing = false; $('btnPlay').textContent = '▶ Play';
      document.querySelectorAll('[data-inject]').forEach(function (b) { b.classList.remove('on'); });
      vessel.setXray(false); $('tXray').classList.remove('on'); setCam('hero', true);
      const r = await SDV.requestApi('POST', '/api/reset', { priority_profile_id: activeProfile });
      if (r.ok) await syncFromBackend(true);
      else updateConnectionBanner();
    });
    $('scrub').addEventListener('change', function (e) {
      const targetTick = parseInt(e.target.value, 10) || 0;
      advanceTargetSimTime(targetTick * 20);
    });
    $('profile').addEventListener('change', async function (e) {
      activeProfile = e.target.value;
      const r = await SDV.requestApi('POST', '/api/profile', { priority_profile_id: activeProfile });
      if (r.ok) await syncFromBackend(false);
      else updateConnectionBanner();
    });
    document.querySelectorAll('[data-inject]').forEach(function (b) {
      b.addEventListener('click', async function () {
        const k = b.getAttribute('data-inject');
        const map = {
          genLoss: { perturbation_type: 'GENERATOR_LOSS_100KW' },
          coolLoss: { perturbation_type: 'COOLING_LOSS_30C_40PCT' },
          overload: { perturbation_type: 'GUEST_OVERCONSUMPTION_20PCT', guest_id: 'G5', measured_power_w: 54000 },
          hold: { perturbation_type: 'URGENT_HOLD', guest_id: 'G3' }
        };
        const body = Object.assign({ priority_profile_id: activeProfile }, map[k] || {});
        if (k === 'genLoss' || k === 'coolLoss') { vessel.setXray(true); $('tXray').classList.add('on'); setCam('cutaway', true); }
        if (k === 'hold') setCam('subsea', true);
        const r = await SDV.requestApi('POST', '/api/perturb', body);
        if (r.ok) {
          b.classList.add('on');
          await syncFromBackend(false);
        } else {
          updateConnectionBanner();
        }
      });
    });
    $('btnClear').addEventListener('click', async function () {
      document.querySelectorAll('[data-inject]').forEach(function (b) { b.classList.remove('on'); });
      const r = await SDV.requestApi('POST', '/api/reset', { priority_profile_id: activeProfile });
      if (r.ok) await syncFromBackend(true);
      else updateConnectionBanner();
    });
    $('admRun').addEventListener('click', async function () {
      const cand = $('admCand').value, slot = $('admSlot').value;
      const resp = await SDV.requestApi('POST', '/api/admit_live', {
        candidate_guest_id: cand,
        priority_profile_id: activeProfile,
        at_port: true,
        apply_if_admitted: false,
        overrides: { preferred_position: slot }
      });
      if (!resp.ok || !resp.data) {
        updateConnectionBanner();
        return;
      }
      const d = resp.data.decision || {};
      const verdict = d.verdict || 'NO';
      const totalMass = resp.data.candidate_total_mass_kg || 0;
      const note = resp.data.causal_sentence || d.causal_sentence || (d.conditions && d.conditions.join(' ')) || 'Evaluated by authoritative governor.';
      vessel.showGhost(slot, verdict);
      const col = verdict === 'YES' ? 'var(--ok)' : verdict === 'YES_IF' ? 'var(--warn)' : 'var(--alarm)';
      $('admOut').style.borderLeftColor = col;
      $('admOut').innerHTML = '<b>' + verdict.replace('_', ' ') + ' · ' + totalMass.toLocaleString('en-US') + ' kg (' + cand + ')</b>' + note;
      const cam = { bay1: 'deck', bay2: 'deck', hangar: 'deck', hangar_roof: 'pad', mast_3: 'bridge' }[slot] || 'hero';
      rig.director = false; if ($('tDirector')) $('tDirector').classList.remove('on'); setCam(cam, true);
      await syncFromBackend(false);
    });

    // recording
    let rec = null, chunks = [];
    $('btnRec').addEventListener('click', function () {
      const b = $('btnRec');
      if (rec && rec.state === 'recording') { rec.stop(); b.textContent = '● Record'; b.classList.remove('danger'); return; }
      if (!canvas.captureStream || typeof MediaRecorder === 'undefined') return;
      chunks = []; rec = new MediaRecorder(canvas.captureStream(30));
      rec.ondataavailable = function (ev) { if (ev.data && ev.data.size) chunks.push(ev.data); };
      rec.onstop = function () {
        const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob(chunks, { type: 'video/webm' }));
        a.download = 'sdv-digital-twin.webm'; a.click();
      };
      rec.start(); b.textContent = '■ Stop'; b.classList.add('danger');
    });
    window.addEventListener('keydown', function (e) {
      if (e.target && (e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT')) return;
      const map = { '1': 'hero', '2': 'blueprint', '3': 'subsea', '4': 'bow', '5': 'bridge', '6': 'deck', '7': 'stern' };
      if (map[e.key]) { setCam(map[e.key]); vessel.setXray(false); $('tXray').classList.remove('on'); }
      else if (e.key === 'x' || e.key === 'X') $('tXray').click();
      else if (e.key === ' ') { e.preventDefault(); $('btnPlay').click(); }
    });

    // Initial authoritative sync + periodic background state sync (1.5 s)
    syncFromBackend(true);
    setInterval(function () {
      if (!advanceInFlight) syncFromBackend(false);
    }, 1500);

    // ------------------------------------------------------------ main loop (smooth 60 FPS ocean + vessel + synchronized clock)
    function showFatal(msg) {
      $('fatalMsg').textContent = msg; $('fatalHelp').textContent = 'Reload the page.';
      $('fatal').hidden = false;
    }
    let tPrev = performance.now(), t = 0, failed = false, firstFrame = true;
    function frame(now) {
      if (failed) return;
      requestAnimationFrame(frame);
      try {
        const dt = Math.min(0.05, (now - tPrev) / 1000); tPrev = now; t += dt;

        // Authoritative clock stepping when playing (1 tick = 20s sim per 1.0s wall clock, ALIGN-T39/T40)
        if (playing && SDV.apiState.connectionState === 'LIVE') {
          if (!advanceInFlight && now - lastAdvanceWallMs >= 950) {
            lastAdvanceWallMs = now;
            const nextSimT = Math.min(1800, view.simT + 20);
            advanceTargetSimTime(nextSimT);
          }
          const alpha = clamp((now - interpStartWallMs) / 950, 0, 1);
          const smoothSimT = interpFromSimT + (interpToSimT - interpFromSimT) * alpha;
          if (SDV.apiState.lastPayload) {
            view = SDV.deriveFromAuthoritative(SDV.apiState.lastPayload, smoothSimT);
          }
        }

        // camera
        const c = rig.cur, d = rig.des, k = 1 - Math.exp(-dt * (rig.drag ? 14 : 4.5));
        if (rig.orbit && !rig.drag) d.th += dt * 0.09;
        ['th', 'ph', 'r', 'tx', 'ty', 'tz'].forEach(function (key) { c[key] += (d[key] - c[key]) * k; });
        camera.position.set(
          c.tx + c.r * Math.sin(c.ph) * Math.sin(c.th),
          Math.max(1.2, c.ty + c.r * Math.cos(c.ph)),
          c.tz + c.r * Math.sin(c.ph) * Math.cos(c.th)
        );
        camera.lookAt(c.tx, c.ty, c.tz);

        // sea + ship motion
        seaS += (view.seaState - seaS) * Math.min(1, dt * 0.6);
        env.update({ dt: dt, t: t, camPos: camera.position, speedKn: view.speedKn, seaState: seaS });
        const amp = env.amp(), dx = env.drift.x, dz = env.drift.y;
        const hc = SDV.waveH(0, 0, t, amp, dx, dz), hb = SDV.waveH(24, 0, t, amp, dx, dz), hs = SDV.waveH(-24, 0, t, amp, dx, dz);
        const hp = SDV.waveH(0, -5, t, amp, dx, dz), hst = SDV.waveH(0, 5, t, amp, dx, dz);
        const r = vessel.root;
        r.position.y = hc * 0.55 - 0.04;
        r.rotation.z = Math.atan2(hb - hs, 48) * 0.65;
        r.rotation.x = -Math.atan2(hst - hp, 10) * 0.65;
        r.rotation.y = Math.sin(t * 0.21) * 0.005;
        vessel.update(t, dt, {
          speedKn: view.speedKn,
          gen2Tripped: view.gen2Tripped,
          g5Degraded: view.g5Degraded,
          coolingDegraded: view.coolingDegraded,
          g3: view.g3,
          drone: view.drone,
          g5AntennaPos: view.g5AntennaPos
        });

        renderer.render(scene, camera);
        updateLabels();
        if (firstFrame) { firstFrame = false; $('boot').classList.add('done'); SDV.running = true; }
      } catch (err) {
        failed = true;
        console.error(err);
        showFatal(String(err && err.stack || err));
      }
    }
    setCam('hero', true);
    Object.assign(rig.cur, PRESETS.hero);
    $('btnPlay').textContent = '▶ Play';
    renderUI();
    requestAnimationFrame(frame);
  };
})();

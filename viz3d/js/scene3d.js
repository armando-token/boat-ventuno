/* viz3d/js/scene3d.js — 3D ship schematic: renderer, engineering camera rig, projected labels, main loop.
   Purely presentational: it renders whatever `view` (derived from the live payload) and `labels` the app hands it. */
(function () {
  'use strict';
  const THREE = window.THREE, SDV = window.SDV;
  const $ = function (id) { return document.getElementById(id); };
  const clamp = SDV.clamp;

  const PRESETS = {
    schematic: { th: 0.30, ph: 1.16, r: 70, tx: -3.5, ty: 6.0, tz: 0 },
    hero:      { th: 0.42, ph: 1.25, r: 70, tx: 1, ty: 3.2, tz: 2 },
    plan:      { th: 0.0, ph: 0.12, r: 92, tx: -4, ty: 0, tz: 0 },
    deck:      { th: -0.75, ph: 1.15, r: 34, tx: -20, ty: 4.2, tz: 0 },
    bridge:    { th: 0.78, ph: 1.20, r: 32, tx: 6, ty: 13.5, tz: 0 },
    subsea:    { th: -0.35, ph: 1.43, r: 46, tx: -6, ty: -1.2, tz: 4 }
  };

  SDV.createScene = function (opts) {
    const stage = $('stage');
    let canvas = $('gl');
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
        renderer = new THREE.WebGLRenderer(Object.assign({ canvas: cv, preserveDrawingBuffer: false }, attempts[i]));
      } catch (e) { lastErr = e; renderer = null; }
    }
    if (!renderer) throw new Error('This browser refused to create a WebGL context (' + (lastErr && lastErr.message) + ').');
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

    const S = {
      renderer: renderer, scene: scene, camera: camera, env: env, vessel: vessel, canvas: canvas,
      eco: true, labelsOn: true, overlayW: 0, view: null, labels: [], onFrame: null
    };

    function resize() {
      const w = Math.max(320, stage.clientWidth), h = Math.max(200, stage.clientHeight);
      renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, S.eco ? 1 : 2.0));
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      applyViewOffset();
      camera.updateProjectionMatrix();
    }
    function applyViewOffset() {
      const w = Math.max(320, stage.clientWidth), h = Math.max(200, stage.clientHeight);
      if (S.overlayW > 0) camera.setViewOffset(w, h, (S.overlayW + 10) / 2, 0, w, h);
      else camera.clearViewOffset();
    }
    S.resize = resize;
    S.setOverlayWidth = function (px) { S.overlayW = px || 0; resize(); };
    if (window.ResizeObserver) new ResizeObserver(resize).observe(stage); else window.addEventListener('resize', resize);
    resize();

    // ------------------------------------------------------------ camera rig
    const rig = { cur: Object.assign({}, PRESETS.schematic), des: Object.assign({}, PRESETS.schematic), drag: false, pan: false, lx: 0, ly: 0 };
    S.setCam = function (name) {
      const p = PRESETS[name]; if (!p) return;
      Object.assign(rig.des, p);
      if (env.setUnderwaterView) env.setUnderwaterView(name === 'subsea');
      document.querySelectorAll('#segCam button').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-cam') === name); });
    };
    canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
    canvas.addEventListener('pointerdown', function (e) {
      rig.drag = true; rig.pan = e.button === 2 || e.shiftKey; rig.lx = e.clientX; rig.ly = e.clientY;
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
    canvas.addEventListener('dblclick', function () { S.setCam('schematic'); });

    // ------------------------------------------------------------ labels (projected HTML tags)
    const pool = {};
    S.setLabels = function (defs) {
      S.labels = defs;
      const seen = {};
      defs.forEach(function (d) {
        seen[d.id] = 1;
        let el = pool[d.id];
        if (!el) { el = pool[d.id] = document.createElement('div'); el.style.opacity = 0; $('labels').appendChild(el); }
        el.className = 'tag ' + (d.cls || '') + (d.side ? ' side-' + d.side : '');
        const html = '<i></i><span class="tt">' + d.title + '</span>' + (d.rows ? '<span class="rows">' + d.rows + '</span>' : '');
        if (el._html !== html) { el.innerHTML = html; el._html = html; }
        if (d.tip !== undefined) el.title = d.tip;
      });
      Object.keys(pool).forEach(function (k) { if (!seen[k]) { pool[k].remove(); delete pool[k]; } });
    };
    const tmpV = new THREE.Vector3();
    function updateLabels() {
      const w = stage.clientWidth, h = stage.clientHeight;
      vessel.root.updateMatrixWorld();
      const placed = [];
      S.labels.forEach(function (d) {
        const el = pool[d.id]; if (!el) return;
        const a = d.pos || vessel.anchors[d.anchor];
        if (!a || !S.labelsOn || (d.xray && !vessel.isXray()) || (S.overlayW > 0 && !/ghost/.test(d.cls || ''))) { el.style.opacity = 0; return; }
        tmpV.set(a[0], a[1], a[2]); vessel.root.localToWorld(tmpV); tmpV.project(camera);
        const vis = tmpV.z > -1 && tmpV.z < 1 && Math.abs(tmpV.x) < 1.02 && Math.abs(tmpV.y) < 1.02;
        el.style.opacity = vis ? 1 : 0;
        if (!vis) return;
        let x = (tmpV.x * 0.5 + 0.5) * w, y = (-tmpV.y * 0.5 + 0.5) * h;
        const ox = d.dx || 0, oy = d.dy || 0;
        const ew = el.offsetWidth || 120, eh = el.offsetHeight || 34;
        // keep tags inside the stage and away from the KPI strip / milestone bar
        let lx = clamp(x + ox - ew / 2, 6 + S.overlayW, w - ew - 6);
        let ly = clamp(y + oy - eh - 10, 56, h - eh - 52);
        const lo = 56, hi = h - eh - 52;
        function hits(yy) {
          for (let k = 0; k < placed.length; k++) {
            const p = placed[k];
            if (lx < p.x + p.w + 4 && lx + ew + 4 > p.x && yy < p.y + p.h + 3 && yy + eh + 3 > p.y) return p;
          }
          return null;
        }
        // walk away from collisions (preferred direction first, then the other) until a free slot is found
        if (hits(ly)) {
          const dirs = d.side === 'down' ? [1, -1] : [-1, 1];
          let best = ly;
          for (let di = 0; di < 2; di++) {
            let yy = ly, p, n = 0, ok = false;
            while ((p = hits(yy)) && n++ < 12) {
              yy = dirs[di] > 0 ? p.y + p.h + 3 : p.y - eh - 3;
              if (yy < lo || yy > hi) break;
            }
            if (yy >= lo && yy <= hi && !hits(yy)) { best = yy; ok = true; }
            if (ok) break;
          }
          ly = clamp(best, lo, hi);
        }
        placed.push({ x: lx, y: ly, w: ew, h: eh });
        el.style.transform = 'translate(' + lx.toFixed(1) + 'px,' + ly.toFixed(1) + 'px)';
        el.style.setProperty('--ax', (x - lx).toFixed(1) + 'px');
        el.style.setProperty('--ay', (y - ly).toFixed(1) + 'px');
      });
    }

    // ------------------------------------------------------------ main loop
    let seaS = 2, tPrev = performance.now(), t = 0, failed = false, firstFrame = true;
    function frame(now) {
      if (failed) return;
      requestAnimationFrame(frame);
      try {
        const dt = Math.min(0.05, (now - tPrev) / 1000); tPrev = now; t += dt;
        if (S.onFrame) S.onFrame(now, dt);
        const view = S.view || { speedKn: 6, seaState: 2, gen2Tripped: false, g5Degraded: false, coolingDegraded: false, g3: 'SURVEY_QUIET', drone: { mode: 'patrol', batt: 100 }, g5AntennaPos: 'hangar_roof' };
        const c = rig.cur, d = rig.des, k = 1 - Math.exp(-dt * (rig.drag ? 14 : 4.5));
        ['th', 'ph', 'r', 'tx', 'ty', 'tz'].forEach(function (key) { c[key] += (d[key] - c[key]) * k; });
        camera.position.set(
          c.tx + c.r * Math.sin(c.ph) * Math.sin(c.th),
          Math.max(1.2, c.ty + c.r * Math.cos(c.ph)),
          c.tz + c.r * Math.sin(c.ph) * Math.cos(c.th)
        );
        camera.lookAt(c.tx, c.ty, c.tz);

        seaS += ((view.seaState || 2) - seaS) * Math.min(1, dt * 0.6);
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
          speedKn: view.speedKn, gen2Tripped: view.gen2Tripped, g5Degraded: view.g5Degraded,
          coolingDegraded: view.coolingDegraded, g3: view.g3, drone: view.drone, g5AntennaPos: view.g5AntennaPos
        });
        renderer.render(scene, camera);
        updateLabels();
        if (firstFrame) { firstFrame = false; $('boot').classList.add('done'); SDV.running = true; }
      } catch (err) {
        failed = true;
        console.error(err);
        $('fatalMsg').textContent = String(err && err.stack || err); $('fatalHelp').textContent = 'Reload the page.';
        $('fatal').hidden = false;
      }
    }
    S.run = function () { requestAnimationFrame(frame); };

    // ------------------------------------------------------------ display toggles
    S.setXray = function (on) { vessel.setXray(on); $('tXray').classList.toggle('on', !!on); };
    S.setEco = function (on) { S.eco = !!on; $('tEco').classList.toggle('on', S.eco); resize(); };
    S.setTime = function (n) { env.setMode(n); vessel.setNight(n === 'night'); };
    S.setLivery = function (civil) { vessel.setLivery(civil ? 'civil' : 'naval'); vessel.root.userData.civil = !!civil; $('tLivery').classList.toggle('on', !!civil); };
    return S;
  };
})();

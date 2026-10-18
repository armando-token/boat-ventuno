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

    // ------------------------------------------------------------ camera rig
    const rig = { cur: Object.assign({}, PRESETS.schematic), des: Object.assign({}, PRESETS.schematic), drag: false, pan: false, lx: 0, ly: 0 };
    S.setCam = function (name) {
      const p = PRESETS[name]; if (!p) return;
      S.autoFit = name === 'schematic';
      if (S.autoFit) fitSchematic(false); else Object.assign(rig.des, p);
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
      S.autoFit = false;
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
      S.autoFit = false;
      rig.des.r = clamp(rig.des.r * (1 + e.deltaY * 0.0012), 14, 190);
    }, { passive: false });
    canvas.addEventListener('dblclick', function () { S.setCam('schematic'); });

    // ------------------------------------------------------------ auto-framing (schematic preset adapts to the stage aspect)
    const HULL = [[-30, 0, 5.5], [-30, 0, -5.5], [-30, 6.5, 5.5], [-30, 6.5, -5.5], [30, 0, 3.5], [30, 0, -3.5], [30, 8.5, 0],
      [5.85, 21, 0], [-2, 9.5, 5], [-2, 9.5, -5], [12, 12, 4], [12, 12, -4]];
    const fitCam = new THREE.PerspectiveCamera(36, 1, 1.4, 6500);
    const fv = new THREE.Vector3(), fR = new THREE.Vector3(), fU = new THREE.Vector3();
    S.autoFit = true;
    S.fitInsets = function (w, h) {
      // room for the KPI strip on top and a callout column on each side
      const cw = S.colW || { L: 150, R: 150 };
      const f = function (c) { return clamp(c * 0.8 + 10, 60, w * 0.24); };
      return { l: f(cw.L), r: f(cw.R), t: h < 420 ? 64 : 70, b: 14 };
    };
    function projectHull(cam, w, h) {
      vessel.root.updateMatrixWorld();
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      HULL.forEach(function (p) {
        fv.set(p[0], p[1], p[2]); vessel.root.localToWorld(fv); fv.project(cam);
        const x = (fv.x * 0.5 + 0.5) * w, y = (-fv.y * 0.5 + 0.5) * h;
        x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y);
      });
      return { x0: x0, x1: x1, y0: y0, y1: y1 };
    }
    function placeCam(cam, c) {
      cam.position.set(c.tx + c.r * Math.sin(c.ph) * Math.sin(c.th), Math.max(1.2, c.ty + c.r * Math.cos(c.ph)), c.tz + c.r * Math.sin(c.ph) * Math.cos(c.th));
      cam.lookAt(c.tx, c.ty, c.tz); cam.updateMatrixWorld(); cam.updateProjectionMatrix();
    }
    function fitSchematic(snap) {
      if (!S.autoFit) return;
      const w = Math.max(320, stage.clientWidth), h = Math.max(200, stage.clientHeight);
      const ins = S.fitInsets(w, h);
      const rw = Math.max(120, w - ins.l - ins.r), rh = Math.max(100, h - ins.t - ins.b);
      const d = Object.assign({}, PRESETS.schematic);
      fitCam.aspect = w / h;
      for (let i = 0; i < 10; i++) {
        placeCam(fitCam, d);
        const bb = projectHull(fitCam, w, h);
        const s = Math.max((bb.x1 - bb.x0) / (rw * 0.94), (bb.y1 - bb.y0) / (rh * 0.94));
        if (isFinite(s) && s > 0) d.r = clamp(d.r * s, 24, 220);
        placeCam(fitCam, d);
        const b2 = projectHull(fitCam, w, h);
        const dx = (b2.x0 + b2.x1) / 2 - (ins.l + rw / 2), dy = (b2.y0 + b2.y1) / 2 - (ins.t + rh / 2);
        const wpp = 2 * d.r * Math.tan(THREE.MathUtils.degToRad(fitCam.fov / 2)) / h;
        fR.setFromMatrixColumn(fitCam.matrixWorld, 0); fU.setFromMatrixColumn(fitCam.matrixWorld, 1);
        if (!isFinite(dx) || !isFinite(dy)) break;
        d.tx += (fR.x * dx - fU.x * dy) * wpp; d.ty += (fR.y * dx - fU.y * dy) * wpp; d.tz += (fR.z * dx - fU.z * dy) * wpp;
      }
      Object.assign(rig.des, d);
      if (snap) Object.assign(rig.cur, d);
    }
    S.refit = fitSchematic;
    let fitFirst = true;
    function onResize() { resize(); if (S.autoFit) { fitSchematic(fitFirst); fitFirst = false; } }
    if (window.ResizeObserver) new ResizeObserver(onResize).observe(stage); else window.addEventListener('resize', onResize);
    onResize();

    // ------------------------------------------------------------ labels: side callouts with leader lines
    const pool = {};
    const svgNS = 'http://www.w3.org/2000/svg';
    const leaders = document.createElementNS(svgNS, 'svg');
    leaders.setAttribute('class', 'leaders');
    $('labels').appendChild(leaders);
    S.setLabels = function (defs) {
      S.labels = defs;
      const seen = {};
      const now = performance.now();
      defs.forEach(function (d) {
        seen[d.id] = 1;
        let el = pool[d.id];
        if (!el) {
          el = pool[d.id] = document.createElement('div'); el.style.opacity = 0; $('labels').appendChild(el);
          el._line = document.createElementNS(svgNS, 'path'); el._dot = document.createElementNS(svgNS, 'circle'); el._dot.setAttribute('r', '3');
          leaders.appendChild(el._line); leaders.appendChild(el._dot);
          el._sig = d.sig; el._hl = 0;
        } else if (d.sig !== undefined && el._sig !== d.sig) { el._sig = d.sig; el._hl = now + 4500; el._pulse = true; }
        el._cls = d.cls || '';
        const html = '<span class="tt">' + d.title + '</span>' + (d.short ? '<span class="ts">' + d.short + '</span>' : '') + (d.rows ? '<span class="rows">' + d.rows + '</span>' : '');
        if (el._html !== html) { el.innerHTML = html; el._html = html; }
        if (d.tip !== undefined) el.title = d.tip;
      });
      Object.keys(pool).forEach(function (k) { if (!seen[k]) { pool[k]._line.remove(); pool[k]._dot.remove(); pool[k].remove(); delete pool[k]; } });
    };
    S.labelRects = function () {
      return Object.keys(pool).map(function (k) { const el = pool[k]; return el.style.opacity === '1' ? Object.assign({ id: k }, el.getBoundingClientRect().toJSON()) : null; }).filter(Boolean);
    };
    const tmpV = new THREE.Vector3();
    function layoutColumn(items, lo, hi, gap) {
      items.sort(function (a, b) { return a.ay - b.ay; });
      items.forEach(function (it) { it.y = it.ay - it.h / 2; });
      for (let i = 0; i < items.length; i++) { if (i) items[i].y = Math.max(items[i].y, items[i - 1].y + items[i - 1].h + gap); }
      for (let i = items.length - 1; i >= 0; i--) {
        const lim = i === items.length - 1 ? hi - items[i].h : items[i + 1].y - gap - items[i].h;
        items[i].y = Math.min(items[i].y, lim);
      }
      for (let i = 0; i < items.length; i++) { if (i) items[i].y = Math.max(items[i].y, items[i - 1].y + items[i - 1].h + gap); else items[i].y = Math.max(items[i].y, lo); }
    }
    function updateLabels(now) {
      const w = stage.clientWidth, h = stage.clientHeight;
      const lab = $('labels');
      if (S._cmpKey !== w + 'x' + h) { S._cmpKey = w + 'x' + h; S._needCompact = false; }
      const compact = h < 440 || w < 700 || S._needCompact;
      lab.classList.toggle('compact', compact);
      lab.classList.toggle('narrow', compact && w < 900);
      leaders.setAttribute('width', w); leaders.setAttribute('height', h);
      vessel.root.updateMatrixWorld();
      const bb = projectHull(camera, w, h);
      const ov = S.overlayW > 0;
      const cols = { L: [], R: [] };
      S.labels.forEach(function (d) {
        const el = pool[d.id]; if (!el) return;
        const a = d.pos || vessel.anchors[d.anchor];
        const hide = function () { el.style.opacity = 0; el._line.style.opacity = 0; el._dot.style.opacity = 0; };
        if (!a || !S.labelsOn || (d.xray && !vessel.isXray()) || (ov && !/ghost/.test(d.cls || ''))) { hide(); return; }
        tmpV.set(a[0], a[1], a[2]); vessel.root.localToWorld(tmpV); tmpV.project(camera);
        if (!(tmpV.z > -1 && tmpV.z < 1 && Math.abs(tmpV.x) < 1.02 && Math.abs(tmpV.y) < 1.02)) { hide(); return; }
        const hl = el._hl > now;
        el.className = 'tag ' + el._cls + (hl ? ' hl' : '') + (el._pulse ? ' pulse' : '');
        if (el._pulse) { el._pulse = false; void el.offsetWidth; }
        const it = { el: el, ax: (tmpV.x * 0.5 + 0.5) * w, ay: (-tmpV.y * 0.5 + 0.5) * h, w: el.offsetWidth || 120, h: el.offsetHeight || 24 };
        (ov || a[0] < -5 ? cols.L : cols.R).push(it);
      });
      const top = h < 420 ? 52 : 58, bot = h - 8, gap = 5, mg = 8;
      const colW = { L: 0, R: 0 };
      [['L', cols.L], ['R', cols.R]].forEach(function (pair) {
        const left = pair[0] === 'L', items = pair[1];
        if (!items.length) return;
        if (!compact && items.reduce(function (sum, i) { return sum + i.h + gap; }, 0) > bot - top) S._needCompact = true;
        layoutColumn(items, top, bot, gap);
        const cw = Math.max.apply(null, items.map(function (i) { return i.w; }));
        colW[pair[0]] = Math.max.apply(null, items.map(function (i) { return /\bhl\b/.test(i.el.className) ? 0 : i.w; }));
        let edge = left ? Math.max(mg + cw, bb.x0 - 14) : Math.min(w - mg - cw - (ov ? S.overlayW + 10 : 0), bb.x1 + 14);
        if (left && ov) edge = Math.min(edge, Math.max(mg + cw, w - S.overlayW - 24));
        items.forEach(function (it) {
          const x = left ? edge - it.w : edge;
          const ex = left ? x + it.w : x, ey = it.y + it.h / 2;
          const kx = left ? Math.min(ex + 16, it.ax) : Math.max(ex - 16, it.ax);
          it.el.style.opacity = 1;
          it.el.style.transform = 'translate(' + x.toFixed(1) + 'px,' + it.y.toFixed(1) + 'px)';
          it.el.classList.toggle('side-l', left); it.el.classList.toggle('side-r', !left);
          it.el._line.setAttribute('d', 'M' + ex.toFixed(1) + ' ' + ey.toFixed(1) + 'H' + kx.toFixed(1) + 'L' + it.ax.toFixed(1) + ' ' + it.ay.toFixed(1));
          it.el._line.setAttribute('class', 'ld ' + it.el._cls);
          it.el._dot.setAttribute('cx', it.ax.toFixed(1)); it.el._dot.setAttribute('cy', it.ay.toFixed(1));
          it.el._dot.setAttribute('class', 'ad ' + it.el._cls);
          it.el._line.style.opacity = 1; it.el._dot.style.opacity = 1;
        });
      });
      // re-frame the ship once the callout columns have a stable width
      if (!ov && S.autoFit && (colW.L || colW.R)) {
        const prev = S.colW || { L: 0, R: 0 };
        if (Math.abs(prev.L - colW.L) > 24 || Math.abs(prev.R - colW.R) > 24) { const first = !S.colW; S.colW = colW; fitSchematic(first); }
      }
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
        updateLabels(now);
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

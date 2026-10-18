/* viz3d/js/vessel.js — 60 m Fincantieri Software-Defined Vessel (SDV), built 1:1 from the side-profile blueprint.
   Authentic Fincantieri Marina Militare (MMI) Grigio Navale palette, zero Z-fighting monolithic upper deck + raised 3D helideck,
   and industrial-grade 3D subsystems (twisted 5-blade skewed propellers + cavitation from Thruster.ts, EO/IR gimbal from Mast.ts,
   detailed diesel-generators, battery SOC racks, copper busbars & cooling fans from InternalSystems.ts, multi-part hydraulic
   knuckle-boom marine crane, lathe-sculpted G4 VTOL UAV, and HUGIN-class G3 Seabed AUV).
   Ship space: +X = bow (x = +30m), -X = stern (x = -30m), +Y = up, Z = beam (half-beam = 5.5m). */
(function () {
  'use strict';
  const THREE = window.THREE, SDV = window.SDV;
  const col = SDV.col, smooth = SDV.smooth, clamp = SDV.clamp, interp = SDV.interp, rboxGeo = SDV.rboxGeo;
  const V3 = THREE.Vector3, V2 = THREE.Vector2;

  // ================================================================== 1. Exact 1:1 Blueprint Hull Lines (metres)
  // Hull sheer stays at y = 2.80m from stern (-30m) through midships (+4.4m) so the weather deck NEVER coplanar-fights
  // with the y = 6.80m hangar/waist roof, and leaves the midships RHIB/AUV side pockets cleanly recessed!
  const SHEER_PTS = [
    [-30.0, 2.80], [-14.0, 2.80], [-1.6, 2.80], [4.35, 2.80],
    [4.65, 5.15],  [16.2, 5.15],  [16.6, 4.85], [23.0, 4.72],
    [30.0, 4.62]
  ];
  function sheerY(x) { return interp(SHEER_PTS, x); }

  const KEEL_PTS = [
    [-30.0, -0.15], [-29.5, -0.28], [-26.0, -0.92], [-21.5, -1.62],
    [-16.5, -2.32], [-12.0, -2.96], [-9.2, -3.20],  [25.2, -3.20],
    [27.0, -3.05],  [28.4, -2.55],  [29.35, -1.55], [30.0, -1.55]
  ];
  function keelY(x) { return interp(KEEL_PTS, x); }

  function chineY(x) {
    if (x <= 4.5) return 1.65;
    const t = clamp((x - 4.5) / 25.5, 0, 1);
    return 1.65 + (4.62 - 1.65) * Math.pow(t, 0.86);
  }

  const STEM_X_BY_Y = [
    [-3.20, 25.40], [-2.95, 27.60], [-2.35, 28.95], [-1.55, 29.38],
    [-0.85, 28.75], [-0.35, 27.15], [-0.05, 26.35], [0.45, 26.68],
    [1.20, 27.25],  [2.20, 28.05],  [3.20, 28.88],  [4.10, 29.60],
    [4.62, 30.00]
  ];
  function stemXAtY(y) { return interp(STEM_X_BY_Y, y); }

  function sternXAtY(y) {
    if (y <= 0) return -29.55;
    return -29.55 - 0.45 * clamp(y / 2.80, 0, 1);
  }

  function hbS(x) {
    if (x < -20) return interp([[-30, 4.45], [-26, 5.10], [-20, 5.50]], x);
    if (x < 9.5) return 5.50;
    const t = clamp((x - 9.5) / 20.5, 0, 1);
    return 5.50 * Math.pow(Math.max(1 - Math.pow(t, 1.75), 0), 0.68);
  }

  function hbW(x) {
    if (x < -20) return interp([[-30, 3.60], [-25, 4.55], [-20, 5.20]], x);
    if (x < 7.5) return 5.20;
    const t = clamp((x - 7.5) / 18.85, 0, 1);
    return 5.20 * Math.pow(Math.max(1 - Math.pow(t, 1.65), 0), 0.72);
  }

  SDV.hull = { hbS: hbS, hbW: hbW, sheerY: sheerY, keelY: keelY, chineY: chineY };

  function bez2(p0, p1, p2, t) {
    const a = 1 - t;
    return [a * a * p0[0] + 2 * a * t * p1[0] + t * t * p2[0], a * a * p0[1] + 2 * a * t * p1[1] + t * t * p2[1]];
  }

  function halfSection(x) {
    const yk = keelY(x), ys = sheerY(x);
    const hS = Math.max(hbS(x), 0.02), hW = Math.max(hbW(x), 0.02);
    let hk = Math.max(0.42 * hW, 0.02), hc = Math.max(hW * 0.96, 0.02);
    if (x > 21.0) {
      const bt = clamp((x - 25.8) / 3.6, -1.2, 1.0);
      const bulbR = bt > -1 && bt < 1 ? 1.32 * Math.sqrt(Math.max(0, 1 - bt * bt)) : 0;
      hk = Math.max(hk, bulbR * 0.55);
      hc = Math.max(hc, bulbR);
    }
    const yc = yk + (0 - yk) * 0.48;
    const yKnuckle = x > 4.5 ? chineY(x) : ys * 0.55;
    const pts = [[0, yk], [hk * 0.6, yk]];
    for (let i = 1; i <= 6; i++) pts.push(bez2([hk * 0.6, yk], [hc, yk], [hc, yc], i / 6));
    for (let i = 1; i <= 5; i++) {
      const t = i / 5;
      pts.push([hc + (hW - hc) * t, yc + (0 - yc) * t]);
    }
    for (let i = 1; i <= 5; i++) {
      const t = i / 5;
      const hK = hW + (hS - hW) * 0.72;
      pts.push(bez2([hW, 0], [hW + (hK - hW) * 0.45, yKnuckle * 0.5], [hK, yKnuckle], t));
    }
    for (let i = 1; i <= 4; i++) {
      const t = i / 4;
      const hK = hW + (hS - hW) * 0.72;
      pts.push([hK + (hS - hK) * t, yKnuckle + (ys - yKnuckle) * t]);
    }
    return pts;
  }

  function hullZAt(x, y) {
    const pts = halfSection(x);
    for (let j = pts.length - 1; j >= 1; j--) {
      const a = pts[j - 1], b = pts[j];
      if (a[1] <= y && y <= b[1] && b[1] > a[1]) return a[0] + (b[0] - a[0]) * ((y - a[1]) / (b[1] - a[1]));
    }
    return pts[pts.length - 1][0];
  }
  SDV.hull.zAt = hullZAt;

  function stationXs(n) {
    const xs = [];
    for (let i = 0; i < n; i++) xs.push(-30 + 60.0 * (1 - Math.cos(Math.PI * i / (n - 1))) / 2);
    return xs;
  }

  function buildHullGeo() {
    const xs = stationXs(92);
    const pos = [], uv = [], idx = [];
    let M = 0, N = 0;
    const rings = xs.map(function (x) {
      const half = halfSection(x);
      N = half.length;
      const bowMorph = smooth((x - 22.5) / 7.5);
      const sternMorph = smooth((-27.0 - x) / 3.0);
      const ring = [];
      for (let j = 0; j < N; j++) {
        const y = half[j][1];
        const z = half[j][0] * (1 - 0.995 * Math.pow(bowMorph, 3.2));
        let vx = x;
        if (bowMorph > 0) vx = x * (1 - bowMorph) + stemXAtY(y) * bowMorph;
        if (sternMorph > 0) vx = vx * (1 - sternMorph) + sternXAtY(y) * sternMorph;
        ring.push([vx, y, z]);
      }
      for (let j = N - 1; j >= 1; j--) {
        const p = ring[j];
        ring.push([p[0], p[1], -p[2]]);
      }
      return ring;
    });
    M = rings[0].length;
    rings.forEach(function (ring) {
      let s = 0;
      for (let j = 0; j < M; j++) {
        if (j > 0) s += Math.hypot(ring[j][1] - ring[j - 1][1], ring[j][2] - ring[j - 1][2]);
        pos.push(ring[j][0], ring[j][1], ring[j][2]);
        uv.push((ring[j][0] + 30) / 3.0, s / 1.5);
      }
    });
    for (let i = 0; i < xs.length - 1; i++) {
      for (let j = 0; j < M; j++) {
        if (j === N - 1) continue;
        const a = i * M + j, b = i * M + ((j + 1) % M), c = (i + 1) * M + j, d = (i + 1) * M + ((j + 1) % M);
        idx.push(a, b, c, b, d, c);
      }
    }
    const capStart = pos.length / 3;
    let cy = 0, cz = 0;
    for (let j = 0; j < M; j++) {
      pos.push(rings[0][j][0], rings[0][j][1], rings[0][j][2]);
      uv.push(rings[0][j][2] / 3.0, rings[0][j][1] / 1.5);
      cy += rings[0][j][1]; cz += rings[0][j][2];
    }
    pos.push(sternXAtY(cy / M), cy / M, cz / M); uv.push(0, 0);
    for (let j = 0; j < M; j++) idx.push(capStart + M, capStart + j, capStart + ((j + 1) % M));

    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  function buildDeckGeo() {
    const xs = stationXs(92), zs = [-1, -0.5, 0, 0.5, 1];
    const pos = [], uv = [], idx = [];
    xs.forEach(function (x) {
      const hs = Math.max(hbS(x), 0.02), y = sheerY(x);
      const bowMorph = smooth((x - 22.5) / 7.5);
      const vx = bowMorph > 0 ? x * (1 - bowMorph) + stemXAtY(y) * bowMorph : x;
      zs.forEach(function (k) {
        pos.push(vx, y, hs * k * (1 - 0.98 * Math.pow(bowMorph, 3.2)));
        uv.push(vx / 1.6, (hs * k) / 1.6);
      });
    });
    const W = zs.length;
    for (let i = 0; i < xs.length - 1; i++) for (let j = 0; j < W - 1; j++) {
      const a = i * W + j, b = a + 1, c = (i + 1) * W + j, d = c + 1;
      idx.push(a, c, b, b, c, d);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute('uv', new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  // ================================================================== 2. Geometry & Industrial Part Helpers
  function barGeo(a, b, r, seg) {
    const A = new V3(a[0], a[1], a[2]), B = new V3(b[0], b[1], b[2]);
    const d = new V3().subVectors(B, A), len = d.length();
    const g = new THREE.CylinderGeometry(r, r, Math.max(len, 0.001), seg || 8, 1);
    const q = new THREE.Quaternion().setFromUnitVectors(new V3(0, 1, 0), d.clone().normalize());
    g.applyMatrix4(new THREE.Matrix4().compose(new V3().addVectors(A, B).multiplyScalar(0.5), q, new V3(1, 1, 1)));
    return g;
  }
  function boxGeo(cx, cy, cz, sx, sy, sz) {
    const g = new THREE.BoxGeometry(sx, sy, sz);
    g.translate(cx, cy, cz);
    return g;
  }
  function railGeos(pts, closed, hRail) {
    const H = hRail || 0.95;
    const out = [], n = pts.length, lim = closed ? n : n - 1;
    for (let i = 0; i < lim; i++) {
      const a = pts[i], b = pts[(i + 1) % n];
      out.push(barGeo([a[0], a[1] + H, a[2]], [b[0], b[1] + H, b[2]], 0.024, 6));
      out.push(barGeo([a[0], a[1] + H * 0.52, a[2]], [b[0], b[1] + H * 0.52, b[2]], 0.016, 6));
      const len = Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]), m = Math.max(1, Math.round(len / 1.45));
      for (let k = 0; k < m; k++) {
        const t = k / m;
        const c = new THREE.CylinderGeometry(0.018, 0.018, H, 6);
        c.translate(a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t + H * 0.5, a[2] + (b[2] - a[2]) * t);
        out.push(c);
      }
    }
    return out;
  }
  function extrude(profile, width, mat, bevel) {
    const b = bevel === undefined ? 0.05 : bevel;
    const s = new THREE.Shape();
    profile.forEach(function (p, i) { if (i) s.lineTo(p[0], p[1]); else s.moveTo(p[0], p[1]); });
    const depth = Math.max(width - 2 * b, 0.05);
    const g = new THREE.ExtrudeGeometry(s, { depth: depth, bevelEnabled: b > 0, bevelThickness: b, bevelSize: b, bevelSegments: 2, steps: 1 });
    g.translate(0, 0, -depth / 2);
    const m = new THREE.Mesh(g, mat);
    m.castShadow = true; m.receiveShadow = true;
    return m;
  }
  function mk(g, mat, cast, recv) {
    const m = new THREE.Mesh(g, mat);
    m.castShadow = cast !== false; m.receiveShadow = recv !== false;
    return m;
  }

  /**
   * Helically twisted, skewed 5-blade marine propeller geometry ported directly from
   * simulacin legacy/packages/vessel-model/src/parts/Thruster.ts (createBladeGeometry),
   * oriented for +X/-X shaft axis.
   */
  function createSkewedBladeGeo(R, rootR) {
    const ur = 12, vc = 9;
    const pos = [], idx = [];
    for (let i = 0; i <= ur; i++) {
      const u = i / ur;
      const r = rootR + (R - rootR) * u;
      const chord = R * (0.28 + 0.34 * Math.sin(Math.PI * Math.pow(u, 0.72))) * (1 - 0.15 * u);
      const pitch = 0.85 - 0.38 * u;
      const skew = 0.22 * R * u * u;
      const rake = -0.06 * R * u;
      const maxT = R * 0.045 * (1 - 0.65 * u);
      for (let j = 0; j <= vc; j++) {
        const v = j / vc - 0.5;
        const s = (v + 0.5) * Math.PI;
        const thick = maxT * Math.sin(s) * (1 - 0.25 * (v + 0.5));
        const along = v * chord;
        const dx = along * Math.sin(pitch) + rake;
        const dTan = along * Math.cos(pitch) + skew;
        const nx = Math.cos(pitch), nTan = -Math.sin(pitch);
        // Upper (+thick) and lower (-thick) surfaces
        pos.push(dx + nx * thick, r, dTan + nTan * thick);
        pos.push(dx - nx * thick * 0.6, r, dTan - nTan * thick * 0.6);
      }
    }
    const row = (vc + 1) * 2;
    for (let i = 0; i < ur; i++) {
      for (let j = 0; j < vc; j++) {
        const a = i * row + j * 2, b = a + 2, c = (i + 1) * row + j * 2, d = c + 2;
        idx.push(a, c, b, b, c, d);
        idx.push(a + 1, b + 1, c + 1, b + 1, d + 1, c + 1);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    return g;
  }

  /**
   * Multi-part EO/IR Optical & Thermal Gimbal Turret ported directly from
   * simulacin legacy/packages/vessel-model/src/parts/Mast.ts (buildEoIrGimbal).
   */
  function buildEoIrGimbal(M, scale) {
    const sc = scale || 1.0;
    const g = new THREE.Group();
    const ped = mk(new THREE.CylinderGeometry(0.18 * sc, 0.22 * sc, 0.14 * sc, 20), M.supDark);
    ped.position.y = 0.07 * sc;
    g.add(ped);

    const yoke = new THREE.Group();
    yoke.position.y = 0.14 * sc;
    [-1, 1].forEach(function (s) {
      const arm = mk(rboxGeo(0.08 * sc, 0.30 * sc, 0.10 * sc, 0.02 * sc, 2), M.radome);
      arm.position.set(0, 0.15 * sc, s * 0.17 * sc);
      yoke.add(arm);
    });
    const base = mk(rboxGeo(0.18 * sc, 0.06 * sc, 0.42 * sc, 0.02 * sc, 2), M.radome);
    base.position.y = 0.02 * sc;
    yoke.add(base);

    const ball = new THREE.Group();
    ball.position.y = 0.20 * sc;
    ball.add(mk(new THREE.SphereGeometry(0.155 * sc, 24, 18), M.radome));

    const lensMat = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0x164c6b), roughness: 0.05, metalness: 0.2, clearcoat: 1.0, clearcoatRoughness: 0.04
    }), 2.2);
    const irMat = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0x281a0e), roughness: 0.08, metalness: 0.65, clearcoat: 1.0
    }), 2.0);

    const mainLens = mk(new THREE.CylinderGeometry(0.062 * sc, 0.062 * sc, 0.04 * sc, 20), lensMat);
    mainLens.rotation.z = Math.PI / 2;
    mainLens.position.set(0.145 * sc, 0.02 * sc, -0.04 * sc);
    ball.add(mainLens);

    const bezel = mk(new THREE.TorusGeometry(0.064 * sc, 0.010 * sc, 8, 20), M.bronze);
    bezel.rotation.y = Math.PI / 2;
    bezel.position.set(0.158 * sc, 0.02 * sc, -0.04 * sc);
    ball.add(bezel);

    const irLens = mk(new THREE.CylinderGeometry(0.040 * sc, 0.040 * sc, 0.04 * sc, 16), irMat);
    irLens.rotation.z = Math.PI / 2;
    irLens.position.set(0.145 * sc, -0.02 * sc, 0.055 * sc);
    ball.add(irLens);

    yoke.add(ball);
    g.add(yoke);
    g.userData.yoke = yoke;
    return g;
  }

  // ================================================================== 3. High-Res Naval Textures
  function makeTextures() {
    const T = {};
    T.hullBrushed = SDV.canvasTexture(512, 512, function (g, w, h) {
      g.fillStyle = '#808080'; g.fillRect(0, 0, w, h);
      g.fillStyle = '#767676';
      for (let y = 0; y < h; y += 64) g.fillRect(0, y, w, 2);
      g.fillStyle = '#888888';
      for (let y = 2; y < h; y += 64) g.fillRect(0, y, w, 1);
      for (let i = 0; i < 900; i++) {
        const v = 125 + Math.random() * 6;
        g.fillStyle = 'rgb(' + v + ',' + v + ',' + v + ')';
        g.fillRect(Math.random() * w, Math.random() * h, 8, 1);
      }
    });
    T.deckBump = SDV.canvasTexture(128, 128, function (g, w, h) {
      g.fillStyle = '#787878'; g.fillRect(0, 0, w, h);
      g.fillStyle = '#929292';
      for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++) {
        g.fillRect(x * 16 + 5, y * 16 + 5, 6, 6);
      }
    });
    T.rib = SDV.canvasTexture(128, 16, function (g, w, h) {
      for (let x = 0; x < w; x++) {
        const v = Math.round(128 + 70 * Math.sin((x / w) * Math.PI * 2 * 6));
        g.fillStyle = 'rgb(' + v + ',' + v + ',' + v + ')';
        g.fillRect(x, 0, 1, h);
      }
    });
    T.bayMarks = SDV.canvasTexture(1024, 560, function (g, w, h) {
      g.fillStyle = '#2d353c'; g.fillRect(0, 0, w, h);
      g.strokeStyle = 'rgba(255,255,255,0.06)'; g.lineWidth = 2;
      for (let x = 0; x < w; x += 64) { g.beginPath(); g.moveTo(x, 0); g.lineTo(x, h); g.stroke(); }
      for (let y = 0; y < h; y += 64) { g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
      const s = w / 15.5, X0 = -29.5;
      function bracket(x0, x1, z0, z1) {
        const px0 = (x0 - X0) * s, px1 = (x1 - X0) * s, py0 = (z0 + 4.2) * s, py1 = (z1 + 4.2) * s, L = 26;
        g.strokeStyle = 'rgba(248,250,252,0.92)'; g.lineWidth = 4;
        [[px0, py0, 1, 1], [px1, py0, -1, 1], [px0, py1, 1, -1], [px1, py1, -1, -1]].forEach(function (c) {
          g.beginPath(); g.moveTo(c[0] + c[2] * L, c[1]); g.lineTo(c[0], c[1]); g.lineTo(c[0], c[1] + c[3] * L); g.stroke();
        });
      }
      bracket(-28.03, -21.97, -1.22, 1.22);
      bracket(-19.9, -16.9, -1.22, 1.22);
      g.strokeStyle = 'rgba(250,204,21,0.90)'; g.lineWidth = 6; g.setLineDash([22, 16]);
      g.strokeRect(12, 12, w - 24, h - 24);
      g.setLineDash([]);
      g.fillStyle = 'rgba(248,250,252,0.78)'; g.font = 'bold 32px sans-serif'; g.textAlign = 'center';
      g.fillText('MISSION BAY 1 · 20 FT ISO', (-25 - X0) * s, h - 36);
      g.fillText('BAY 2 · 10 FT', (-18.4 - X0) * s, h - 36);
    }, { srgb: true, clamp: true });

    T.pad = SDV.canvasTexture(512, 512, function (g, w, h) {
      const cx = w / 2, cy = h / 2;
      g.fillStyle = '#252c33'; g.fillRect(0, 0, w, h);
      g.strokeStyle = '#facc15'; g.lineWidth = 14; g.setLineDash([28, 16]);
      g.beginPath(); g.arc(cx, cy, 224, 0, Math.PI * 2); g.stroke();
      g.setLineDash([]);
      g.strokeStyle = '#facc15'; g.lineWidth = 12;
      g.beginPath(); g.arc(cx, cy, 164, 0, Math.PI * 2); g.stroke();
      g.fillStyle = '#f8fafc';
      g.fillRect(cx - 68, cy - 88, 28, 176);
      g.fillRect(cx + 40, cy - 88, 28, 176);
      g.fillRect(cx - 42, cy - 14, 84, 28);
      g.fillStyle = '#94a3b8'; g.font = 'bold 24px monospace'; g.textAlign = 'center';
      g.fillText('MAX 1.5t · G4 VTOL', cx, cy + 132);
    }, { srgb: true, clamp: true });

    T.pennant = SDV.canvasTexture(512, 256, function (g, w, h) {
      g.clearRect(0, 0, w, h);
      g.font = '900 138px sans-serif';
      g.textAlign = 'center'; g.textBaseline = 'middle';
      g.fillStyle = 'rgba(15, 20, 26, 0.85)';
      g.fillText('590', w / 2 + 6, h / 2 + 7);
      g.fillStyle = '#f8fafc';
      g.fillText('590', w / 2, h / 2);
      g.font = 'bold 28px sans-serif';
      g.fillStyle = '#e2e8f0';
      g.fillText('FINCANTIERI · SDV', w / 2, h - 26);
    }, { srgb: true, clamp: true });

    return T;
  }

  // ================================================================== 4. Vessel Factory
  SDV.createVessel = function () {
    const T = makeTextures();

    const M = {};
    M.hull = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0x9ea8b2), roughness: 0.36, metalness: 0.14, clearcoat: 0.32, clearcoatRoughness: 0.24,
      bumpMap: T.hullBrushed, bumpScale: 0.06, side: THREE.DoubleSide
    }), 1.15);
    M.hull.onBeforeCompile = function (shader) {
      shader.vertexShader = shader.vertexShader
        .replace('#include <common>', '#include <common>\nvarying float vLocalY;')
        .replace('#include <begin_vertex>', '#include <begin_vertex>\nvLocalY = position.y;');
      shader.fragmentShader = shader.fragmentShader
        .replace('#include <common>', '#include <common>\nvarying float vLocalY;')
        .replace('#include <color_fragment>', [
          '#include <color_fragment>',
          'vec3 aqAnti = vec3(0.22, 0.028, 0.022);',
          'vec3 aqBoot = vec3(0.012, 0.015, 0.018);',
          'float aqA = smoothstep(-0.20, -0.14, vLocalY);',
          'float aqB = smoothstep(0.26, 0.32, vLocalY);',
          'vec3 aqC = mix(aqAnti, aqBoot, aqA);',
          'diffuseColor.rgb = mix(aqC, diffuseColor.rgb, aqB);'
        ].join('\n'));
    };
    M.hull.customProgramCacheKey = function () { return 'sdv-fincantieri-paint-v4'; };

    M.underbody = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0x82241e), roughness: 0.42, metalness: 0.16, clearcoat: 0.28
    }), 0.95);
    M.deck = SDV.std(new THREE.MeshStandardMaterial({
      color: col(0x363e45), roughness: 0.82, metalness: 0.12, bumpMap: T.deckBump, bumpScale: 0.30, side: THREE.DoubleSide
    }), 0.55);
    M.sup = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0xa8b2bc), roughness: 0.38, metalness: 0.12, clearcoat: 0.30, clearcoatRoughness: 0.28
    }), 1.1);
    M.supDark = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x21272d), roughness: 0.62, metalness: 0.32 }), 0.75);
    M.recessBay = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x14191f), roughness: 0.82, metalness: 0.18, side: THREE.DoubleSide }), 0.4);
    M.glass = SDV.std(new THREE.MeshPhysicalMaterial({
      color: col(0x071520), roughness: 0.04, metalness: 0.30, clearcoat: 1.0, clearcoatRoughness: 0.02,
      emissive: col(0xffb768), emissiveIntensity: 0.0
    }), 2.8);
    M.frame = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x181f26), roughness: 0.50, metalness: 0.45 }), 0.7);
    M.rail = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xc8d2dc), roughness: 0.24, metalness: 0.82 }), 1.25);
    M.mast = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x9ca6b0), roughness: 0.38, metalness: 0.35, clearcoat: 0.25 }), 1.05);
    M.radome = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0xf4f7fa), roughness: 0.26, metalness: 0.05, clearcoat: 0.50 }), 1.1);
    M.crane = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0xe5a41c), roughness: 0.34, metalness: 0.32, clearcoat: 0.38 }), 1.0);
    M.orange = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0xe8531e), roughness: 0.34, metalness: 0.16, clearcoat: 0.35 }), 0.95);
    M.auvYellow = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0xf5b81c), roughness: 0.24, metalness: 0.24, clearcoat: 0.62 }), 1.15);
    M.rubber = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x181d22), roughness: 0.78, metalness: 0.10 }), 0.5);
    M.bronze = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xd4a44e), roughness: 0.20, metalness: 0.94 }), 1.45);
    M.copper = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xc8783c), roughness: 0.24, metalness: 0.90 }), 1.3);
    M.pod = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x6e201c), roughness: 0.42, metalness: 0.38 }), 0.9);
    M.stripe = SDV.std(new THREE.MeshStandardMaterial({
      color: col(0xe05224), roughness: 0.42, metalness: 0.08, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, side: THREE.DoubleSide
    }), 0.85);
    M.stripe2 = SDV.std(new THREE.MeshStandardMaterial({
      color: col(0xf8fafc), roughness: 0.42, metalness: 0.08, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2, side: THREE.DoubleSide
    }), 0.85);
    M.cont1 = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xc7752a), roughness: 0.54, metalness: 0.34, bumpMap: T.rib, bumpScale: 1.2 }), 0.85);
    M.cont2 = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x2848a0), roughness: 0.54, metalness: 0.34, bumpMap: T.rib, bumpScale: 1.2 }), 0.85);
    M.casting = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x15191d), roughness: 0.58, metalness: 0.58 }), 0.65);
    M.gun = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x8c96a0), roughness: 0.34, metalness: 0.36, clearcoat: 0.35 }), 1.1);
    M.navRed = new THREE.MeshStandardMaterial({ color: col(0x300606), emissive: col(0xff2a2a), emissiveIntensity: 0.7 });
    M.navGreen = new THREE.MeshStandardMaterial({ color: col(0x063010), emissive: col(0x2aff6a), emissiveIntensity: 0.7 });
    M.navWhite = new THREE.MeshStandardMaterial({ color: col(0x303030), emissive: col(0xffffff), emissiveIntensity: 0.9 });
    M.strobe = new THREE.MeshStandardMaterial({ color: col(0x303030), emissive: col(0xffffff), emissiveIntensity: 0.5 });
    M.dg1 = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x1f8f63), roughness: 0.36, metalness: 0.45, clearcoat: 0.25, emissive: col(0x0b3a28), emissiveIntensity: 0.55 }), 0.85);
    M.dg2 = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x1f8f63), roughness: 0.36, metalness: 0.45, clearcoat: 0.25, emissive: col(0x0b3a28), emissiveIntensity: 0.55 }), 0.85);
    M.batt = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x162a3e), roughness: 0.38, metalness: 0.48, clearcoat: 0.3 }), 0.9);
    M.rack = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x182028), roughness: 0.42, metalness: 0.65 }), 0.9);
    M.led = new THREE.MeshStandardMaterial({ color: col(0x08130f), emissive: col(0x18e0a0), emissiveIntensity: 2.2 });
    M.ledAmber = new THREE.MeshStandardMaterial({ color: col(0x1a1204), emissive: col(0xf5b83d), emissiveIntensity: 2.0 });
    M.pipeCold = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x1d9bd6), roughness: 0.34, metalness: 0.58, emissive: col(0x0a4a73), emissiveIntensity: 0.7 }), 0.9);
    M.pipeHot = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xd7603a), roughness: 0.34, metalness: 0.58, emissive: col(0x5a1d0c), emissiveIntensity: 0.7 }), 0.9);
    M.tank = SDV.std(new THREE.MeshPhysicalMaterial({ color: col(0x6c7a86), roughness: 0.32, metalness: 0.62, clearcoat: 0.3 }), 0.95);
    M.flow = new THREE.MeshBasicMaterial({ color: 0x9be7ff });
    M.ghost = new THREE.MeshBasicMaterial({ color: 0x22c55e, transparent: true, opacity: 0.45, depthWrite: false });
    M.sonarCone = new THREE.MeshBasicMaterial({ color: 0x22d3ee, transparent: true, opacity: 0.24, side: THREE.DoubleSide, depthWrite: false });
    M.uavCone = new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.22, side: THREE.DoubleSide, depthWrite: false });
    M.rfRing = new THREE.MeshBasicMaterial({ color: 0x34d399, transparent: true, opacity: 0.35, side: THREE.DoubleSide, depthWrite: false });
    M.xray = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
      uniforms: { uColor: { value: new THREE.Color(0x7dd3fc) }, uA0: { value: 0.04 }, uA1: { value: 0.52 } },
      vertexShader: 'varying vec3 vN; varying vec3 vV; void main(){ vec4 mv = modelViewMatrix * vec4(position, 1.0); vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); gl_Position = projectionMatrix * mv; }',
      fragmentShader: 'uniform vec3 uColor; uniform float uA0; uniform float uA1; varying vec3 vN; varying vec3 vV; void main(){ float f = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 2.4); gl_FragColor = vec4(uColor, mix(uA0, uA1, f)); }'
    });

    const root = new THREE.Group();
    root.name = 'vessel';
    const shell = [];
    const hideInXray = [];
    const addShell = function (m) { m.userData.mat0 = m.material; shell.push(m); root.add(m); return m; };
    const dummy = new THREE.Object3D();

    // ---------------------------------------------------------------- 1. Hull, Deck, Bulbous Bow Fairing & Diagonal Chine Strake
    const hull = mk(buildHullGeo(), M.hull); hull.name = 'hull'; addShell(hull);
    const deck = mk(buildDeckGeo(), M.deck); deck.name = 'deck'; addShell(deck);

    const bulbGeo = new THREE.SphereGeometry(1.12, 32, 24);
    bulbGeo.scale(3.45, 1.18, 1.05);
    const bulbMesh = mk(bulbGeo, M.underbody);
    bulbMesh.position.set(26.15, -1.78, 0);
    addShell(bulbMesh);

    const chineGeos = [];
    [1, -1].forEach(function (s) {
      const pts = [];
      for (let i = 0; i <= 28; i++) {
        const x = 4.5 + (29.9 - 4.5) * (i / 28);
        const y = chineY(x);
        const bowMorph = smooth((x - 22.5) / 7.5);
        const vx = bowMorph > 0 ? x * (1 - bowMorph) + stemXAtY(y) * bowMorph : x;
        const z = s * (hullZAt(x, y) * (1 - 0.98 * Math.pow(bowMorph, 3.2)) + 0.03);
        pts.push(new V3(vx, y, z));
      }
      chineGeos.push(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(pts), 40, 0.065, 6, false));

      const sPts = [];
      for (let i = 0; i <= 24; i++) {
        const x = 4.5 + (29.9 - 4.5) * (i / 24);
        const y = sheerY(x) - 0.05;
        const bowMorph = smooth((x - 22.5) / 7.5);
        const vx = bowMorph > 0 ? x * (1 - bowMorph) + stemXAtY(y) * bowMorph : x;
        const z = s * (hbS(x) * (1 - 0.98 * Math.pow(bowMorph, 3.2)) + 0.03);
        sPts.push(new V3(vx, y, z));
      }
      chineGeos.push(new THREE.TubeGeometry(new THREE.CatmullRomCurve3(sPts), 32, 0.06, 6, false));
    });
    const chineMesh = mk(SDV.mergeGeos(chineGeos), M.supDark);
    root.add(chineMesh); hideInXray.push(chineMesh);

    // Bilge Keels
    const bilgeGeos = [];
    [1, -1].forEach(function (s) {
      bilgeGeos.push(boxGeo(-0.5, -2.82, s * 4.65, 18.0, 0.12, 0.65));
      bilgeGeos.push(barGeo([-9.5, -2.82, s * 4.65], [-10.8, -2.45, s * 4.55], 0.07, 6));
      bilgeGeos.push(barGeo([8.5, -2.82, s * 4.65], [9.8, -2.45, s * 4.45], 0.07, 6));
    });
    root.add(mk(SDV.mergeGeos(bilgeGeos), M.underbody));

    // Bow Thruster Tunnel + 4-Blade Impeller Emblem (⊕ at x = +21.8m, y = -1.85m)
    const btGrp = new THREE.Group();
    [1, -1].forEach(function (s) {
      const zPos = s * (hullZAt(21.8, -1.85) + 0.035);
      const ring = new THREE.Mesh(new THREE.RingGeometry(0.62, 0.80, 28), M.supDark);
      ring.position.set(21.8, -1.85, zPos);
      if (s < 0) ring.rotation.y = Math.PI;
      btGrp.add(ring);
      const hole = new THREE.Mesh(new THREE.CircleGeometry(0.62, 24), M.recessBay);
      hole.position.set(21.8, -1.85, zPos - s * 0.01);
      if (s < 0) hole.rotation.y = Math.PI;
      btGrp.add(hole);
      const c1 = mk(boxGeo(21.8, -1.85, zPos + s * 0.015, 1.24, 0.11, 0.04), M.bronze);
      const c2 = mk(boxGeo(21.8, -1.85, zPos + s * 0.015, 0.11, 1.24, 0.04), M.bronze);
      const hub = mk(new THREE.SphereGeometry(0.18, 12, 10), M.bronze);
      hub.position.set(21.8, -1.85, zPos + s * 0.015);
      btGrp.add(c1, c2, hub);
    });
    root.add(btGrp); hideInXray.push(btGrp);

    // Hull Portholes, Windows, Anchor Pocket & Aft Bulwark Bays
    const hullDetGeos = [], hullGlassGeos = [];
    [1, -1].forEach(function (s) {
      [5.2, 6.5, 7.8, 9.4, 10.7, 12.0, 13.5].forEach(function (px) {
        const pz = s * (hullZAt(px, 2.45) + 0.025);
        const g = new THREE.CylinderGeometry(0.22, 0.22, 0.06, 16);
        g.rotateX(Math.PI / 2); g.translate(px, 2.45, pz);
        hullGlassGeos.push(g);
      });
      [5.5, 7.2, 8.0, 9.8, 11.4, 12.8, 14.8, 15.5].forEach(function (wx) {
        const wz = s * (hullZAt(wx, 4.05) + 0.025);
        hullGlassGeos.push(boxGeo(wx, 4.05, wz, 0.55, 0.42, 0.05));
        hullDetGeos.push(boxGeo(wx, 4.05, wz - s * 0.01, 0.65, 0.52, 0.04));
      });
      const az = s * (hullZAt(24.8, 2.45) * 0.82 + 0.025);
      hullDetGeos.push(boxGeo(24.8, 2.45, az, 1.35, 0.95, 0.12));
      hullDetGeos.push(barGeo([24.5, 2.25, az + s * 0.06], [25.2, 2.65, az + s * 0.06], 0.12, 6));
      const hz = s * (hullZAt(26.8, 3.65) * 0.65 + 0.025);
      hullDetGeos.push(boxGeo(26.8, 3.65, hz, 0.55, 0.28, 0.08));
      [20.8, 22.8].forEach(function (dx) {
        const dz = s * (hullZAt(dx, 0.55) + 0.025);
        const dg = new THREE.CylinderGeometry(0.18, 0.18, 0.06, 12);
        dg.rotateX(Math.PI / 2); dg.translate(dx, 0.55, dz);
        hullDetGeos.push(dg);
      });
      [[-28.4, 1.6], [-26.0, 2.2], [-23.4, 1.8], [-20.6, 2.2]].forEach(function (b) {
        const bz = s * (hullZAt(b[0], 1.75) + 0.025);
        hullDetGeos.push(boxGeo(b[0], 1.75, bz, b[1], 1.05, 0.08));
      });
    });
    root.add(mk(SDV.mergeGeos(hullDetGeos), M.recessBay));
    root.add(mk(SDV.mergeGeos(hullGlassGeos), M.glass));

    // Hull Pennant "590" + Diagonal Stripe
    const penMat = new THREE.MeshBasicMaterial({ map: T.pennant, transparent: true, depthWrite: false });
    [1, -1].forEach(function (s) {
      const xP = 20.2, yP = 3.25;
      const zP = s * (hullZAt(xP, yP) + 0.06);
      const pMesh = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 1.7), penMat);
      pMesh.position.set(xP, yP, zP);
      const dzdx = (hullZAt(xP + 1, yP) - hullZAt(xP - 1, yP)) / 2;
      pMesh.rotation.y = (s < 0 ? Math.PI : 0) - s * Math.atan(dzdx);
      pMesh.rotation.x = -s * 0.12;
      root.add(pMesh); hideInXray.push(pMesh);
    });

    function stripeGeo(side, x0, x1, shift) {
      const rows = 8, pos = [], idx = [];
      for (let r = 0; r <= rows; r++) {
        const t = r / rows;
        [x0, x1].forEach(function (xb) {
          const x = xb + shift * t, y = 0.8 + (4.55 - 0.8) * t;
          pos.push(x, y, side * (hullZAt(x, y) + 0.03));
        });
      }
      for (let r = 0; r < rows; r++) { const a = r * 2, b = a + 1, c = a + 2, d = a + 3; idx.push(a, b, c, b, d, c); }
      const g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setIndex(idx); g.computeVertexNormals();
      return g;
    }
    const stripes = new THREE.Group();
    [1, -1].forEach(function (s) {
      stripes.add(mk(stripeGeo(s, 14.5, 16.5, 3.2), M.stripe, false, false));
      stripes.add(mk(stripeGeo(s, 16.75, 17.2, 3.2), M.stripe2, false, false));
    });
    root.add(stripes); hideInXray.push(stripes);

    // ---------------------------------------------------------------- Raised 3D Mission Bay Plate & Raised 3D Helideck
    const bayPlateMats = [
      M.supDark, M.supDark,
      new THREE.MeshStandardMaterial({ map: T.bayMarks, roughness: 0.75, metalness: 0.1 }),
      M.supDark, M.supDark, M.supDark
    ];
    const bayPlate = new THREE.Mesh(new THREE.BoxGeometry(15.5, 0.08, 8.4), bayPlateMats);
    bayPlate.position.set(-21.75, 2.86, 0);
    bayPlate.receiveShadow = true;
    root.add(bayPlate); hideInXray.push(bayPlate);

    const heliPadGrp = new THREE.Group();
    heliPadGrp.position.set(0.9, 6.94, 0);
    const padCapMat = new THREE.MeshStandardMaterial({ map: T.pad, roughness: 0.65, metalness: 0.12 });
    const padCyl = new THREE.Mesh(new THREE.CylinderGeometry(2.35, 2.45, 0.16, 36), [M.supDark, padCapMat, M.supDark]);
    padCyl.castShadow = true; padCyl.receiveShadow = true;
    heliPadGrp.add(padCyl);
    const padRim = mk(new THREE.TorusGeometry(2.40, 0.045, 8, 36), M.rail);
    padRim.rotation.x = Math.PI / 2; padRim.position.y = 0.08;
    heliPadGrp.add(padRim);
    const padLedMat = new THREE.MeshBasicMaterial({ color: 0x22c55e });
    for (let i = 0; i < 8; i++) {
      const ang = (i / 8) * Math.PI * 2;
      const l = new THREE.Mesh(new THREE.CylinderGeometry(0.06, 0.06, 0.04, 10), padLedMat);
      l.position.set(Math.cos(ang) * 2.30, 0.09, Math.sin(ang) * 2.30);
      heliPadGrp.add(l);
    }
    root.add(heliPadGrp); hideInXray.push(heliPadGrp);

    // ---------------------------------------------------------------- 2. Superstructure & Monolithic 0-Flicker Upper Deck Slab
    function sup(profile, width, mat, bevel) {
      const m = extrude(profile, width, mat || M.sup, bevel);
      m.userData.mat0 = m.material; shell.push(m); root.add(m); return m;
    }

    // Aft Hangar Lower Block (x = -14.0 to -1.62, y = 2.82 to 6.36 — stops cleanly BELOW the monolithic roof slab!)
    sup([[-14.0, 2.82], [-13.55, 6.36], [-1.62, 6.36], [-1.62, 2.82]], 9.6, M.sup, 0.03);
    // Waist Core between Port/Starboard RHIB & AUV Recesses (x = -1.56 to +4.42, y = 2.82 to 6.36)
    sup([[-1.56, 2.82], [-1.56, 6.36], [4.42, 6.36], [4.42, 2.82]], 6.2, M.recessBay, 0.02);
    // Divider Shelf between Upper RHIB Bay and Lower AUV Bay (x = -1.56 to +4.42, y = 3.65 to 3.92)
    sup([[-1.56, 3.65], [-1.56, 3.92], [4.42, 3.92], [4.42, 3.65]], 10.2, M.sup, 0.02);
    // Forward Tier 1 Lower Deckhouse + 45° Glacis (x = +4.48 to +16.2, y = 5.17 to 6.36)
    sup([[4.48, 5.17], [4.48, 6.36], [14.2, 6.36], [16.2, 5.17]], 9.6, M.sup, 0.03);

    // MONOLITHIC CONTINUOUS UPPER DECK / FLIGHT ROOF SLAB (x = -13.6m to +13.8m, y = 6.39m to 6.76m + 0.04m bevel = 6.80m)
    // Because this is a single continuous extrusion and deckGeo is at y = 2.80m, Z-fighting on the helipad roof is 100% impossible!
    sup([[-13.6, 6.39], [-13.5, 6.76], [13.8, 6.76], [14.2, 6.39]], 10.2, M.sup, 0.04);

    // Tier 2 Intermediate Bridge Deckhouse + Sloped Front (starts at y = 6.82m, strictly above 6.80m!)
    sup([[3.8, 6.82], [3.8, 8.48], [11.2, 8.48], [12.8, 6.82]], 8.8, M.sup, 0.04);

    // Tier 3 Reverse-Raked Panoramic Bridge Wheelhouse
    sup([[3.6, 8.52], [3.6, 9.14], [12.6, 9.14], [12.3, 8.52]], 8.4, M.sup, 0.03);
    sup([[3.8, 9.16], [3.8, 10.14], [13.35, 10.14], [12.6, 9.16]], 8.1, M.glass, 0.02);
    sup([[3.4, 10.16], [3.4, 10.40], [13.5, 10.40], [13.85, 10.16]], 8.8, M.radome, 0.03);
    sup([[6.5, 8.48], [12.2, 8.48], [12.2, 8.70], [6.5, 8.70]], 11.4, M.sup, 0.03);

    // Tier 4 Upper Conning / Radome Deckhouse above Bridge (starts at y = 10.44m)
    sup([[4.4, 10.44], [4.4, 11.45], [10.4, 11.45], [11.6, 10.44]], 6.4, M.sup, 0.04);

    // Trapezoidal Funnel / Stack (starts at y = 6.82m, strictly above 6.80m!)
    sup([[-5.5, 6.82], [-5.5, 9.45], [-6.8, 9.45], [-6.8, 10.04], [-1.55, 10.04], [-0.4, 6.82]], 3.2, M.sup, 0.04);
    sup([[-6.8, 10.06], [-6.8, 10.30], [-1.60, 10.30], [-1.55, 10.06]], 3.16, M.supDark, 0.02);

    const exhaustGeos = [];
    [-3.4, -2.8, -2.2].forEach(function (ex) {
      exhaustGeos.push(barGeo([ex, 10.15, -0.5], [ex - 0.35, 10.98, -0.5], 0.16, 8));
      exhaustGeos.push(barGeo([ex, 10.15, 0.5], [ex - 0.35, 10.98, 0.5], 0.16, 8));
    });
    exhaustGeos.push(barGeo([-2.0, 10.25, 0], [-4.2, 14.8, 0], 0.035, 6));
    root.add(mk(SDV.mergeGeos(exhaustGeos), M.supDark));

    // Liferaft Canisters, Bridge Wing Searchlights, Foredeck Breakwater, Capstans & Bollards
    const lifeGeos = [], cradleGeos = [];
    [1, -1].forEach(function (s) {
      [5.2, 6.6, 8.0].forEach(function (lx) {
        const g = new THREE.CapsuleGeometry(0.28, 0.65, 6, 12);
        g.rotateZ(Math.PI / 2); g.translate(lx, 7.18, s * 4.65);
        lifeGeos.push(g);
        cradleGeos.push(boxGeo(lx, 6.90, s * 4.65, 0.9, 0.14, 0.5));
      });
      cradleGeos.push(barGeo([11.6, 8.70, s * 5.3], [11.6, 9.55, s * 5.3], 0.08, 8));
      const sl = new THREE.CylinderGeometry(0.18, 0.18, 0.32, 12);
      sl.rotateZ(Math.PI / 2); sl.translate(11.6, 9.65, s * 5.3);
      lifeGeos.push(sl);
    });
    cradleGeos.push(barGeo([22.8, 5.05, 0], [21.6, 5.05, 3.2], 0.22, 6));
    cradleGeos.push(barGeo([22.8, 5.05, 0], [21.6, 5.05, -3.2], 0.22, 6));
    [-0.9, 0.9].forEach(function (z) {
      const cap = new THREE.CylinderGeometry(0.32, 0.38, 0.55, 12);
      cap.translate(24.5, 5.05, z); cradleGeos.push(cap);
      cradleGeos.push(barGeo([24.5, 4.88, z], [27.2, 4.78, z * 0.65], 0.06, 6));
    });
    [-28.5, -23.0, -16.5, 17.0, 23.5, 27.5].forEach(function (bx) {
      [1, -1].forEach(function (s) {
        const bz = s * (hbS(bx) - 0.45), by = sheerY(bx);
        cradleGeos.push(barGeo([bx - 0.15, by, bz], [bx - 0.15, by + 0.38, bz], 0.08, 8));
        cradleGeos.push(barGeo([bx + 0.15, by, bz], [bx + 0.15, by + 0.38, bz], 0.08, 8));
      });
    });
    root.add(mk(SDV.mergeGeos(lifeGeos), M.radome));
    root.add(mk(SDV.mergeGeos(cradleGeos), M.supDark));

    // Side & Aft Corrugated Hangar Roll-Up Shutter Doors
    const shutterGeos = [], shutterLines = [];
    [1, -1].forEach(function (s) {
      const sz = s * 4.82;
      shutterGeos.push(boxGeo(-9.8, 4.65, sz, 4.8, 3.0, 0.06));
      for (let k = 0; k <= 6; k++) {
        shutterLines.push(boxGeo(-9.8, 3.18 + k * 0.49, sz + s * 0.02, 4.84, 0.04, 0.04));
      }
      shutterLines.push(boxGeo(-12.2, 4.65, sz + s * 0.02, 0.08, 3.05, 0.05));
      shutterLines.push(boxGeo(-7.4, 4.65, sz + s * 0.02, 0.08, 3.05, 0.05));
    });
    shutterGeos.push(boxGeo(-14.02, 4.65, 0, 0.06, 3.0, 5.8));
    for (let k = 0; k <= 6; k++) shutterLines.push(boxGeo(-14.05, 3.18 + k * 0.49, 0, 0.05, 0.04, 5.8));
    root.add(mk(SDV.mergeGeos(shutterGeos), M.supDark));
    root.add(mk(SDV.mergeGeos(shutterLines), M.frame));

    // Bridge Window Mullions + Pantograph Wipers
    const mullionGeos = [];
    [1, -1].forEach(function (s) {
      for (let i = 0; i <= 11; i++) {
        const wx = 4.0 + i * 0.80;
        mullionGeos.push(boxGeo(wx, 9.65, s * 4.06, 0.08, 1.04, 0.06));
      }
      [8.2, 9.6, 11.2, 12.4].forEach(function (wx) {
        mullionGeos.push(boxGeo(wx, 5.80, s * 4.82, 0.75, 0.50, 0.05));
      });
      [5.5, 7.2, 8.8, 10.2].forEach(function (wx) {
        mullionGeos.push(boxGeo(wx, 7.65, s * 4.42, 0.65, 0.55, 0.05));
      });
    });
    for (let i = -3; i <= 3; i++) {
      mullionGeos.push(barGeo([12.62, 9.15, i * 1.15], [13.37, 10.15, i * 1.15], 0.045, 5));
      if (i < 3) {
        mullionGeos.push(barGeo([13.28, 10.05, (i + 0.5) * 1.15], [12.90, 9.52, (i + 0.5) * 1.15], 0.018, 4));
      }
    }
    root.add(mk(SDV.mergeGeos(mullionGeos), M.frame));

    // ---------------------------------------------------------------- 3. Integrated Raked Mast + EO/IR Gimbal (from Mast.ts)
    const mastParts = [];
    const mastCol = extrude([[4.5, 10.44], [6.1, 10.44], [5.4, 16.4], [4.3, 16.4]], 1.5, M.mast, 0.04);
    addShell(mastCol);
    [-0.55, 0.55].forEach(function (z) {
      mastParts.push(barGeo([2.9, 10.44, z], [3.6, 16.4, z], 0.055, 6));
      [11.6, 12.8, 14.0, 15.2, 16.3].forEach(function (ly) {
        const t = (ly - 10.44) / 6.0;
        const rx = 2.9 + 0.7 * t, fx = 4.5 - 0.2 * t;
        mastParts.push(barGeo([rx, ly, z], [fx, ly, z], 0.035, 5));
      });
    });
    [12.6, 13.8, 15.0].forEach(function (ry, idx) {
      const len = 2.8 - idx * 0.45;
      mastParts.push(rboxGeo(len, 0.14, 0.9 - idx * 0.15, 0.03, 2, 5.6 + len * 0.45, ry, 0));
      mastParts.push(barGeo([5.2, ry - 0.55, 0], [5.6 + len * 0.7, ry, 0], 0.035, 5));
    });
    mastParts.push(rboxGeo(4.1, 0.18, 2.4, 0.04, 2, 5.25, 16.45, 0));
    mastParts.push(barGeo([4.45, 16.45, 0], [4.45, 23.40, 0], 0.075, 8));
    mastParts.push(barGeo([3.6, 16.45, 0], [4.45, 19.2, 0], 0.035, 5));
    [18.2, 19.8, 21.2, 22.4].forEach(function (ty, i) {
      const span = 2.4 - i * 0.42;
      mastParts.push(barGeo([4.45, ty, -span / 2], [4.45, ty, span / 2], 0.03, 5));
      mastParts.push(barGeo([4.45 - 0.5, ty, 0], [4.45 + 0.6, ty, 0], 0.03, 5));
    });
    mastParts.push(barGeo([10.3, 10.44, 0], [10.3, 15.6, 0], 0.045, 6));
    mastParts.push(barGeo([8.3, 11.45, 0], [8.3, 16.2, 0], 0.025, 5));
    mastParts.push(barGeo([29.5, 4.62, 0], [29.5, 6.85, 0], 0.035, 6));
    mastParts.push(barGeo([-29.8, 2.80, 0], [-30.45, 5.45, 0], 0.04, 6));
    root.add(mk(SDV.mergeGeos(mastParts), M.mast));

    const domeUp = mk(new THREE.SphereGeometry(0.86, 28, 20), M.radome);
    domeUp.position.set(5.85, 17.38, 0); root.add(domeUp);
    const pedUp = mk(new THREE.CylinderGeometry(0.38, 0.52, 0.35, 16), M.mast);
    pedUp.position.set(5.85, 16.65, 0); root.add(pedUp);

    // EO/IR Gimbal Turret on Forward Mast Platform (from Mast.ts)
    const mastGimbal = buildEoIrGimbal(M, 1.35);
    mastGimbal.position.set(7.05, 16.54, 0);
    root.add(mastGimbal);

    const domeLo = mk(new THREE.SphereGeometry(0.88, 28, 20), M.radome);
    domeLo.position.set(6.0, 11.95, 0); root.add(domeLo);
    const pedLo = mk(new THREE.CylinderGeometry(0.42, 0.55, 0.35, 16), M.mast);
    pedLo.position.set(6.0, 11.25, 0); root.add(pedLo);

    const radar = new THREE.Group(); radar.position.set(7.4, 14.05, 0);
    radar.add(mk(rboxGeo(0.42, 0.18, 2.75, 0.05, 2, 0, 0.22, 0), M.radome), mk(new THREE.CylinderGeometry(0.28, 0.34, 0.32, 16), M.mast));
    root.add(radar);

    const radar2 = new THREE.Group(); radar2.position.set(6.9, 15.25, 0);
    radar2.add(mk(rboxGeo(0.32, 0.15, 1.95, 0.04, 2, 0, 0.18, 0), M.radome), mk(new THREE.CylinderGeometry(0.22, 0.26, 0.26, 16), M.mast));
    root.add(radar2);

    const rfRing = new THREE.Mesh(new THREE.RingGeometry(0.8, 1.15, 32), M.rfRing);
    rfRing.rotation.x = -Math.PI / 2; rfRing.position.set(5.85, 17.4, 0);
    root.add(rfRing);

    // Hangar roof auxiliary mount for G5_MAST_ANTENNA when mast_3 is occupied by G1_MAST_UNIT (ALIGN-T37)
    const hangarRoofAntenna = new THREE.Group();
    hangarRoofAntenna.position.set(-2.2, 6.80, -2.1);
    hangarRoofAntenna.add(mk(new THREE.CylinderGeometry(0.26, 0.34, 0.45, 16), M.mast));
    const hrDome = mk(new THREE.SphereGeometry(0.46, 20, 14), M.radome);
    hrDome.position.set(0, 0.52, 0);
    hangarRoofAntenna.add(hrDome);
    root.add(hangarRoofAntenna);

    const nl = function (mat, x, y, z, r) {
      const m = new THREE.Mesh(new THREE.SphereGeometry(r || 0.12, 10, 8), mat);
      m.position.set(x, y, z); root.add(m); return m;
    };
    nl(M.navRed, 11.5, 8.85, -5.65); nl(M.navGreen, 11.5, 8.85, 5.65);
    nl(M.navWhite, 4.45, 23.5, 0, 0.14); nl(M.navWhite, -30.4, 5.45, 0, 0.11);
    nl(M.strobe, 4.45, 20.0, 0, 0.11);

    // ---------------------------------------------------------------- 4. Bow Stealth Naval Gun (Oto Melara 76/62 Super Rapido Sovraponte)
    const bowY = sheerY(18.2);
    const gunGrp = new THREE.Group();
    gunGrp.position.set(18.2, bowY, 0);

    // 4a. Raised Cylindrical Deck Barbette & Bolted Slewing Ring (matches blueprint pedestal height)
    const barbFlange = mk(new THREE.CylinderGeometry(1.38, 1.44, 0.14, 28), M.supDark);
    barbFlange.position.y = 0.07; gunGrp.add(barbFlange);
    const barbCyl = mk(new THREE.CylinderGeometry(1.26, 1.34, 0.48, 28), M.gun);
    barbCyl.position.y = 0.31; gunGrp.add(barbCyl);
    const slewRing = mk(new THREE.CylinderGeometry(1.30, 1.30, 0.10, 28), M.casting);
    slewRing.position.y = 0.56; gunGrp.add(slewRing);

    // 4b. Rotating Multi-Faceted Stealth Turret Cupola (Chined Oto Melara Super Rapido Shield)
    const gunTurret = new THREE.Group();
    gunTurret.position.y = 0.60;
    gunGrp.add(gunTurret);

    // Central Stealth Cupola Core + Sloped Side Stealth Cheeks
    const turretCore = extrude([[-1.55, 0.0], [1.45, 0.0], [0.75, 1.35], [-0.95, 1.35]], 1.70, M.gun, 0.06);
    gunTurret.add(turretCore);
    const turretCheeks = extrude([[-1.35, 0.02], [1.20, 0.02], [0.60, 1.05], [-0.82, 1.05]], 2.38, M.sup, 0.05);
    gunTurret.add(turretCheeks);

    // Dark Vertical Barrel Elevation Slot on Front Glacis + Rear Maintenance Hatch + Top Fire-Control Director Pod
    gunTurret.add(mk(rboxGeo(0.55, 0.92, 0.36, 0.04, 2, 0.98, 0.68, 0), M.recessBay));
    gunTurret.add(mk(rboxGeo(0.12, 0.88, 0.68, 0.03, 2, -1.28, 0.55, 0), M.supDark));
    const fcPed = mk(new THREE.CylinderGeometry(0.28, 0.36, 0.22, 18), M.supDark);
    fcPed.position.set(-0.15, 1.44, 0); gunTurret.add(fcPed);
    const fcDome = mk(new THREE.SphereGeometry(0.28, 20, 14), M.radome);
    fcDome.scale.set(1.35, 0.85, 1.0);
    fcDome.position.set(-0.10, 1.62, 0); gunTurret.add(fcDome);

    // 4c. Elevating 76mm L/62 Rifled Cannon Barrel with Recoil Cradle, Cooling Jacket, Bore Evacuator & Muzzle Brake
    const gunElev = new THREE.Group();
    gunElev.position.set(0.85, 0.68, 0);
    gunElev.rotation.z = 0.07; // Slight upward naval elevation as shown in blueprint
    // Armored Recoil Cradle Shroud
    gunElev.add(mk(rboxGeo(0.95, 0.36, 0.34, 0.05, 2, 0.35, 0, 0), M.supDark));
    // Rear Water-Cooling Barrel Jacket
    gunElev.add(mk(barGeo([0.65, 0, 0], [2.15, 0, 0], 0.14, 16), M.casting));
    // Mid-Barrel Cylindrical Bore Evacuator (Fume Extractor)
    gunElev.add(mk(barGeo([2.05, 0, 0], [2.62, 0, 0], 0.18, 18), M.supDark));
    // Forward Rifled Barrel Tube
    gunElev.add(mk(barGeo([2.55, 0, 0], [4.25, 0, 0], 0.105, 16), M.casting));
    // Multi-Baffle Slotted Muzzle Brake + Bronze Muzzle Crown
    gunElev.add(mk(barGeo([4.18, 0, 0], [4.58, 0, 0], 0.155, 18), M.supDark));
    gunElev.add(mk(barGeo([4.56, 0, 0], [4.64, 0, 0], 0.135, 16), M.bronze));
    gunTurret.add(gunElev);
    root.add(gunGrp);

    const monGrp = new THREE.Group(); monGrp.position.set(18.2, bowY, 0);
    monGrp.add(mk(new THREE.CylinderGeometry(0.35, 0.45, 1.0, 14), M.orange));
    const monArm = mk(new THREE.CylinderGeometry(0.1, 0.12, 1.8, 10), M.orange);
    monArm.rotation.z = Math.PI / 2 + 0.4; monArm.position.set(0.7, 1.3, 0); monGrp.add(monArm);
    monGrp.visible = false; root.add(monGrp);

    // ---------------------------------------------------------------- 5. Two-Level Midships Recess: Upper RHIB + Lower AUV Pod
    [1, -1].forEach(function (s) {
      const rhib = new THREE.Group(); rhib.position.set(1.4, 4.32, s * 4.25);
      [-1, 1].forEach(function (k) {
        const tube = mk(new THREE.CapsuleGeometry(0.32, 3.6, 8, 16), M.supDark);
        tube.rotation.z = Math.PI / 2; tube.position.set(-0.1, 0.25, k * 0.68);
        rhib.add(tube);
      });
      const rBow = extrude([[1.6, -0.05], [2.7, 0.42], [2.5, 0.68], [1.5, 0.50]], 1.38, M.supDark, 0.05);
      rhib.add(rBow);
      const rHull = extrude([[-2.1, -0.22], [1.7, -0.22], [2.3, 0.15], [-2.1, 0.15]], 1.15, M.sup, 0.03);
      rhib.add(rHull);
      rhib.add(mk(rboxGeo(0.65, 0.72, 0.75, 0.06, 2, 0.35, 0.62, 0), M.sup));
      rhib.add(mk(rboxGeo(0.45, 0.60, 0.70, 0.05, 2, 0.45, 1.05, 0), M.glass));
      rhib.add(mk(rboxGeo(0.48, 0.82, 0.42, 0.06, 2, -2.25, 0.35, 0), M.rubber));
      root.add(rhib);

      const podStow = new THREE.Group(); podStow.position.set(1.4, 3.22, s * 4.25);
      const pBody = mk(new THREE.CapsuleGeometry(0.30, 3.1, 8, 18), s > 0 ? M.auvYellow : M.orange);
      pBody.rotation.z = Math.PI / 2; podStow.add(pBody);
      [-1.1, 1.1].forEach(function (cx) {
        podStow.add(mk(rboxGeo(0.22, 0.28, 0.85, 0.03, 2, cx, -0.28, 0), M.frame));
      });
      root.add(podStow);
    });

    const davGeos = [];
    [1, -1].forEach(function (s) {
      [-0.4, 3.1].forEach(function (dx) {
        davGeos.push(barGeo([dx, 3.92, s * 3.5], [dx, 6.25, s * 3.5], 0.09, 8));
        davGeos.push(barGeo([dx, 6.25, s * 3.5], [dx, 5.95, s * 4.4], 0.08, 8));
        davGeos.push(barGeo([dx, 5.95, s * 4.25], [dx, 4.65, s * 4.25], 0.022, 6));
      });
    });
    root.add(mk(SDV.mergeGeos(davGeos), M.rail));

    // ---------------------------------------------------------------- 6. Detailed Hydraulic Knuckle-Boom Marine Crane (Heila / Palfinger Style)
    const cr = new THREE.Group();
    cr.position.set(-12.0, 6.80, 2.35);

    // 6a. Bolted Deck Pedestal & Slewing Ring
    const crFlange = mk(new THREE.CylinderGeometry(0.76, 0.82, 0.14, 28), M.supDark);
    crFlange.position.y = 0.07; cr.add(crFlange);
    const boltGeos = [];
    for (let i = 0; i < 16; i++) {
      const a = (i / 16) * Math.PI * 2;
      const b = new THREE.CylinderGeometry(0.035, 0.035, 0.08, 6);
      b.translate(Math.cos(a) * 0.68, 0.15, Math.sin(a) * 0.68);
      boltGeos.push(b);
    }
    cr.add(mk(SDV.mergeGeos(boltGeos), M.rail));

    const crPed = mk(new THREE.CylinderGeometry(0.50, 0.56, 1.15, 24), M.crane);
    crPed.position.y = 0.70; cr.add(crPed);
    const crSlew = mk(new THREE.CylinderGeometry(0.58, 0.58, 0.18, 24), M.casting);
    crSlew.position.y = 1.32; cr.add(crSlew);

    // Slewing Upper Assembly (rotates slowly during operations)
    const crTurret = new THREE.Group();
    crTurret.position.y = 1.40;
    cr.add(crTurret);

    // Kingpost Housing & Cheek Plates
    crTurret.add(mk(rboxGeo(1.12, 1.15, 0.92, 0.10, 3, 0, 0.55, 0), M.crane));
    [-0.38, 0.38].forEach(function (z) {
      crTurret.add(mk(rboxGeo(0.75, 0.65, 0.10, 0.04, 2, 0.22, 1.05, z), M.crane));
    });

    // Operator Cabin with Tinted Panoramic Glass + HPU Power Pack
    crTurret.add(mk(rboxGeo(1.15, 1.22, 0.76, 0.10, 3, -0.05, 0.62, 0.84), M.crane));
    crTurret.add(mk(rboxGeo(0.84, 0.78, 0.78, 0.04, 2, 0.08, 0.74, 0.84), M.glass));
    crTurret.add(mk(rboxGeo(0.88, 0.82, 0.58, 0.08, 2, -0.22, 0.48, -0.74), M.supDark));
    const hpuFan = mk(new THREE.CylinderGeometry(0.24, 0.24, 0.06, 16), M.casting);
    hpuFan.rotation.x = Math.PI / 2; hpuFan.position.set(-0.22, 0.48, -1.04);
    crTurret.add(hpuFan);

    // Main Box-Girder Luffing Boom (from [0.22, 1.15, 0] to [3.35, 2.45, 0])
    const mainBoom = new THREE.Group();
    mainBoom.position.set(0.22, 1.15, 0);
    mainBoom.rotation.z = 0.38;
    mainBoom.add(mk(rboxGeo(3.45, 0.42, 0.38, 0.05, 3, 1.65, 0, 0), M.crane));
    // Side Gusset Plates & Pivot Pins
    mainBoom.add(mk(rboxGeo(0.75, 0.52, 0.44, 0.04, 2, 0.15, 0, 0), M.supDark));
    mainBoom.add(mk(rboxGeo(0.68, 0.48, 0.44, 0.04, 2, 3.25, -0.04, 0), M.supDark));
    const pinGeo = new THREE.CylinderGeometry(0.08, 0.08, 0.56, 12);
    pinGeo.rotateX(Math.PI / 2);
    mainBoom.add(mk(pinGeo, M.rail));
    // Hydraulic Hoist Winch Drum on Boom Back
    const winchDrum = mk(new THREE.CylinderGeometry(0.26, 0.26, 0.46, 20), M.supDark);
    winchDrum.rotation.x = Math.PI / 2; winchDrum.position.set(0.95, 0.36, 0);
    mainBoom.add(winchDrum);
    crTurret.add(mainBoom);

    // Dual Hydraulic Luffing Cylinders (Dark Barrels + Gleaming Chrome Piston Rods)
    [-0.26, 0.26].forEach(function (z) {
      crTurret.add(mk(barGeo([0.42, 0.32, z], [1.35, 1.08, z], 0.105, 12), M.casting));
      crTurret.add(mk(barGeo([1.25, 1.00, z], [2.10, 1.68, z], 0.068, 12), M.rail));
    });

    // Knuckle Jib Boom + Telescopic Stinger + Knuckle Hydraulic Ram
    const jibBoom = new THREE.Group();
    jibBoom.position.set(3.25, 0, 0);
    jibBoom.rotation.z = -0.68;
    jibBoom.add(mk(rboxGeo(2.65, 0.32, 0.28, 0.04, 3, 1.28, 0, 0), M.crane));
    jibBoom.add(mk(rboxGeo(1.35, 0.22, 0.19, 0.03, 2, 2.95, 0, 0), M.supDark));
    jibBoom.add(mk(rboxGeo(0.42, 0.36, 0.28, 0.03, 2, 3.58, -0.04, 0), M.crane));
    mainBoom.add(jibBoom);

    // Underslung Knuckle Hydraulic Cylinder (Barrel + Chrome Rod)
    mainBoom.add(mk(barGeo([1.35, -0.28, 0], [2.45, -0.34, 0], 0.095, 12), M.casting));
    mainBoom.add(mk(barGeo([2.35, -0.34, 0], [3.35, -0.38, 0], 0.062, 12), M.rail));

    // Flexible Hydraulic Hose Loops across the Knuckle Joint
    [-0.18, 0.18].forEach(function (z) {
      const hCurve = new THREE.CatmullRomCurve3([
        new V3(2.65, 0.18, z), new V3(3.25, 0.42, z * 1.15), new V3(3.85, -0.15, z)
      ]);
      mainBoom.add(mk(new THREE.TubeGeometry(hCurve, 16, 0.024, 6, false), M.rubber));
    });

    // Hoist Wire Rope, Weighted Sheave Pulley Block & Forged Steel Crane Hook
    const hookAssy = new THREE.Group();
    hookAssy.position.set(5.48, 1.72, 0);
    hookAssy.add(mk(barGeo([0, 0, -0.06], [0, -1.35, -0.06], 0.016, 6), M.rail));
    hookAssy.add(mk(barGeo([0, 0, 0.06], [0, -1.35, 0.06], 0.016, 6), M.rail));
    hookAssy.add(mk(rboxGeo(0.24, 0.38, 0.20, 0.04, 2, 0, -1.45, 0), M.crane));
    const sheaveWheel = mk(new THREE.CylinderGeometry(0.11, 0.11, 0.12, 16), M.supDark);
    sheaveWheel.rotation.x = Math.PI / 2; sheaveWheel.position.set(0, -1.45, 0);
    hookAssy.add(sheaveWheel);
    const swivel = mk(new THREE.CylinderGeometry(0.05, 0.06, 0.14, 10), M.casting);
    swivel.position.set(0, -1.70, 0); hookAssy.add(swivel);
    const forgedHook = mk(new THREE.TorusGeometry(0.14, 0.042, 10, 20, Math.PI * 1.42), M.casting);
    forgedHook.rotation.z = Math.PI * 0.65;
    forgedHook.position.set(0.02, -1.88, 0);
    hookAssy.add(forgedHook);
    crTurret.add(hookAssy);

    // Auxiliary Deck Winch Drum next to Crane
    const deckWinch = new THREE.Group();
    deckWinch.position.set(3.2, 0.42, -1.35);
    const wDrum = mk(new THREE.CylinderGeometry(0.38, 0.38, 0.85, 20), M.supDark);
    wDrum.rotation.x = Math.PI / 2; deckWinch.add(wDrum);
    [-0.46, 0.46].forEach(function (z) {
      deckWinch.add(mk(rboxGeo(0.92, 0.85, 0.10, 0.04, 2, 0, 0, z), M.crane));
    });
    cr.add(deckWinch);
    root.add(cr);

    // Guardrails
    const railParts = [];
    railGeos([[-29.9, 2.8, -4.4], [-29.9, 2.8, 4.4], [-14.0, 2.8, 5.4], [-14.0, 2.8, -5.4]], false, 0.85).forEach(function (g) { railParts.push(g); });
    railGeos([[-13.4, 6.80, -4.8], [-13.4, 6.80, 4.8], [3.6, 6.80, 4.8], [3.6, 6.80, -4.8]], true, 0.80).forEach(function (g) { railParts.push(g); });
    railGeos([[4.0, 10.44, -4.1], [4.0, 10.44, 4.1], [12.8, 10.44, 4.1], [12.8, 10.44, -4.1]], true, 0.75).forEach(function (g) { railParts.push(g); });
    root.add(mk(SDV.mergeGeos(railParts), M.rail, true, false));

    // Beveled ISO Mission Containers with Locking Bars, Doors & Corner Castings
    function container(len, mat, x) {
      const g = new THREE.Group(); g.position.set(x, 4.18, 0);
      g.add(mk(rboxGeo(len, 2.50, 2.44, 0.06, 3), mat));
      const cg = [];
      [-1, 1].forEach(function (a) { [-1, 1].forEach(function (b) { [-1, 1].forEach(function (c) {
        cg.push(boxGeo(a * (len / 2 - 0.08), c * 1.22, b * 1.18, 0.22, 0.18, 0.22));
      }); }); });
      // Vertical door locking bars & HVAC unit on front/back
      [-0.75, -0.25, 0.25, 0.75].forEach(function (z) {
        cg.push(barGeo([-len / 2 - 0.02, -1.15, z], [-len / 2 - 0.02, 1.15, z], 0.022, 6));
      });
      cg.push(rboxGeo(0.28, 0.85, 1.10, 0.04, 2, len / 2 + 0.12, 0.35, 0));
      g.add(mk(SDV.mergeGeos(cg), M.casting));
      return g;
    }
    const bay1 = container(6.06, M.cont1, -24.8), bay2 = container(2.99, M.cont2, -18.2);
    root.add(bay1, bay2);

    // ---------------------------------------------------------------- 7. Exact Blueprint Stern Propulsion: 2x Pods + Twisted 5-Blade Skewed Props + Cavitation (Thruster.ts)
    const pods = [];
    const skewBladeGeo = createSkewedBladeGeo(0.92, 0.18);
    [1, -1].forEach(function (s) {
      const pGrp = new THREE.Group();
      const pz = s * 1.65;
      // Lathe-sculpted Azimuth/Fixed Drive Pod Nacelle
      const podPts = [
        new V2(0.01, -1.85), new V2(0.24, -1.65), new V2(0.46, -1.10),
        new V2(0.52, 0.45),  new V2(0.48, 1.25),  new V2(0.28, 1.75), new V2(0.01, 1.95)
      ];
      const podGeo = new THREE.LatheGeometry(podPts, 24);
      podGeo.rotateZ(-Math.PI / 2);
      const drivePod = mk(podGeo, M.pod);
      drivePod.position.set(-14.6, -1.92, pz);
      pGrp.add(drivePod);

      // Hydrodynamic Strut & Exposed Propeller Shaft + A-Bracket V-Struts
      pGrp.add(mk(barGeo([-14.4, -1.92, pz], [-14.0, -0.95, pz], 0.28, 12), M.pod));
      pGrp.add(mk(barGeo([-15.8, -1.92, pz], [-23.75, -2.28, pz], 0.15, 14), M.rail));
      pGrp.add(mk(barGeo([-22.9, -2.25, pz], [-22.4, -0.75, pz - s * 0.48], 0.095, 10), M.pod));
      pGrp.add(mk(barGeo([-22.9, -2.25, pz], [-22.4, -0.75, pz + s * 0.48], 0.095, 10), M.pod));
      pGrp.add(mk(barGeo([-19.0, -2.08, pz], [-18.8, -1.18, pz], 0.11, 10), M.pod));

      // Twisted 5-Blade Skewed Bronze Propeller + Ogee Hub (from Thruster.ts)
      const prop = new THREE.Group(); prop.position.set(-23.85, -2.28, pz);
      const hubPts = [
        new V2(0.01, -0.42), new V2(0.14, -0.32), new V2(0.22, -0.10),
        new V2(0.24, 0.22),  new V2(0.01, 0.26)
      ];
      const hubGeo = new THREE.LatheGeometry(hubPts, 20);
      hubGeo.rotateZ(-Math.PI / 2);
      prop.add(mk(hubGeo, M.bronze));
      for (let b = 0; b < 5; b++) {
        const bg = new THREE.Group();
        bg.rotation.x = (b / 5) * Math.PI * 2;
        bg.add(mk(skewBladeGeo, M.bronze));
        prop.add(bg);
      }
      pGrp.add(prop);

      // NACA Spade Rudder
      const rudProf = [[-27.1, -0.85], [-25.1, -0.85], [-25.4, -3.10], [-26.9, -3.10]];
      const rudder = extrude(rudProf, 0.24, M.underbody, 0.04);
      rudder.position.z = pz;
      pGrp.add(rudder);

      root.add(pGrp);
      pods.push({ g: pGrp, prop: prop, z: pz });
    });

    // Underwater Propeller Cavitation Bubble Stream (from Thruster.ts)
    const CAV_N = 56;
    const cavMat = new THREE.MeshBasicMaterial({ color: 0xdff6ff, transparent: true, opacity: 0.45, depthWrite: false });
    const cavMesh = new THREE.InstancedMesh(new THREE.SphereGeometry(0.10, 6, 5), cavMat, CAV_N);
    const cavSeeds = [];
    for (let i = 0; i < CAV_N; i++) cavSeeds.push({ phase: Math.random(), ang: Math.random() * Math.PI * 2, rad: 0.2 + Math.random() * 0.75, side: i % 2 ? 1 : -1 });
    root.add(cavMesh);

    // ---------------------------------------------------------------- 8. Active Mission Actors: Sculpted G4 VTOL UAV + HUGIN G3 Seabed AUV + SAR Buoy
    // 8a. G4 Maritime Hybrid VTOL UAV (Lathe Fuselage + Swept Wing + Winglets + Twin Tail Booms + V-Tail + Chin EO/IR Gimbal + Skids)
    const drone = new THREE.Group();
    const uavHullPts = [
      new V2(0.005, -1.25), new V2(0.09, -0.95), new V2(0.18, -0.50),
      new V2(0.26, 0.05),   new V2(0.28, 0.45),  new V2(0.24, 0.82),
      new V2(0.14, 1.08),   new V2(0.01, 1.18)
    ];
    const uavBodyGeo = new THREE.LatheGeometry(uavHullPts, 24);
    uavBodyGeo.rotateZ(-Math.PI / 2);
    uavBodyGeo.scale(1.0, 0.88, 1.05);
    drone.add(mk(uavBodyGeo, M.radome));

    // Tinted Avionics Canopy Bubble + High-Vis Nose Cone
    const canopyGeo = new THREE.SphereGeometry(0.22, 20, 14);
    canopyGeo.scale(2.1, 0.72, 0.92);
    const canopy = mk(canopyGeo, M.glass);
    canopy.position.set(0.38, 0.14, 0);
    drone.add(canopy);

    // Nose EO/IR Gimbal Turret under UAV Chin
    const uavGimbal = buildEoIrGimbal(M, 0.72);
    uavGimbal.rotation.x = Math.PI;
    uavGimbal.position.set(0.68, -0.14, 0);
    drone.add(uavGimbal);

    // Swept Tapered Main Wing + Upturned Orange Winglets
    drone.add(mk(rboxGeo(0.56, 0.06, 3.75, 0.025, 2, -0.05, 0.06, 0), M.radome));
    drone.add(mk(rboxGeo(0.18, 0.065, 3.76, 0.02, 2, 0.16, 0.06, 0), M.orange));
    [-1.88, 1.88].forEach(function (wz, idx) {
      const wlet = mk(rboxGeo(0.42, 0.36, 0.05, 0.02, 2, -0.12, 0.22, wz), M.orange);
      wlet.rotation.x = wz > 0 ? -0.22 : 0.22;
      drone.add(wlet);
      const navL = new THREE.Mesh(new THREE.SphereGeometry(0.045, 8, 6), idx === 0 ? M.navRed : M.navGreen);
      navL.position.set(-0.12, 0.38, wz);
      drone.add(navL);
    });

    // Twin Carbon Tail Booms + Inverted V-Tail Stabilizers + Pusher Prop
    const rotors = [];
    [-0.95, 0.95].forEach(function (bz) {
      drone.add(mk(barGeo([0.92, 0.06, bz], [-1.42, 0.06, bz], 0.042, 10), M.supDark));
      // V-Tail stabilizer fin meeting at top center
      drone.add(mk(barGeo([-1.35, 0.06, bz], [-1.52, 0.58, 0], 0.038, 8), M.orange));
      // Landing Gear Skids
      const sz = bz * 0.52;
      drone.add(mk(barGeo([0.45, -0.32, sz], [-0.55, -0.32, sz], 0.026, 8), M.supDark));
      drone.add(mk(barGeo([0.28, -0.08, sz * 0.7], [0.28, -0.32, sz], 0.022, 6), M.supDark));
      drone.add(mk(barGeo([-0.35, -0.08, sz * 0.7], [-0.35, -0.32, sz], 0.022, 6), M.supDark));

      // Front & Rear Lift-Motor Pods on Each Boom
      [0.85, -0.85].forEach(function (mx) {
        const pod = mk(new THREE.CylinderGeometry(0.085, 0.075, 0.16, 14), M.supDark);
        pod.position.set(mx, 0.12, bz);
        drone.add(pod);
        const r = new THREE.Group();
        r.position.set(mx, 0.21, bz);
        r.add(mk(new THREE.ConeGeometry(0.06, 0.09, 12), M.rail));
        r.add(mk(rboxGeo(1.18, 0.018, 0.095, 0.008, 1, 0, 0, 0), M.rubber));
        const disc = new THREE.Mesh(new THREE.CylinderGeometry(0.59, 0.59, 0.005, 24), new THREE.MeshBasicMaterial({
          color: 0xcfe9ff, transparent: true, opacity: 0.25, depthWrite: false
        }));
        r.add(disc);
        drone.add(r);
        rotors.push(r);
      });
    });

    const uavConeGeo = new THREE.ConeGeometry(6.5, 22.0, 28, 1, true);
    uavConeGeo.translate(0, -11.0, 0);
    const uavCone = new THREE.Mesh(uavConeGeo, M.uavCone);
    drone.add(uavCone);
    drone.position.set(0.9, 7.34, 0);
    root.add(drone);

    // 8b. G3 Deep-Survey Seabed AUV (Kongsberg HUGIN / REMUS 6000 Torpedo Architecture)
    const auv = new THREE.Group();
    const auvHullPts = [
      new V2(0.01, -2.25), new V2(0.16, -1.85), new V2(0.32, -1.25),
      new V2(0.39, -0.60), new V2(0.39, 1.45),  new V2(0.34, 1.85),
      new V2(0.20, 2.12),  new V2(0.01, 2.22)
    ];
    const auvBodyGeo = new THREE.LatheGeometry(auvHullPts, 28);
    auvBodyGeo.rotateZ(-Math.PI / 2);
    auv.add(mk(auvBodyGeo, M.auvYellow));

    // Black Rubber Sonar Nose Cap + Modular Payload Section Rings
    const auvNoseGeo = new THREE.SphereGeometry(0.392, 24, 18, 0, Math.PI * 2, 0, Math.PI * 0.52);
    auvNoseGeo.rotateZ(-Math.PI / 2);
    const auvNose = mk(auvNoseGeo, M.rubber);
    auvNose.position.set(1.82, 0, 0);
    auv.add(auvNose);

    [-1.15, -0.25, 0.65, 1.45].forEach(function (rx) {
      const band = mk(new THREE.CylinderGeometry(0.396, 0.396, 0.08, 24), M.supDark);
      band.rotation.z = Math.PI / 2; band.position.set(rx, 0, 0);
      auv.add(band);
    });

    // Port & Starboard Side-Scan Sonar Array Blisters + Dorsal Acoustic Transducer & LED Beacon
    [-0.38, 0.38].forEach(function (sz) {
      auv.add(mk(rboxGeo(1.95, 0.18, 0.07, 0.025, 2, 0.20, -0.08, sz), M.rubber));
    });
    const auvModem = mk(new THREE.CylinderGeometry(0.11, 0.13, 0.24, 16), M.supDark);
    auvModem.position.set(0.55, 0.44, 0); auv.add(auvModem);
    const auvBeacon = new THREE.Mesh(new THREE.SphereGeometry(0.075, 12, 10), M.strobe);
    auvBeacon.position.set(-0.35, 0.43, 0); auv.add(auvBeacon);

    // Cruciform Hydrofoil Fins + Shrouded Kort Nozzle Ring + Spinning 5-Blade Bronze Propeller
    const finProf = [[-2.05, 0.15], [-1.35, 0.15], [-1.60, 0.68], [-2.02, 0.68]];
    for (let f = 0; f < 4; f++) {
      const fg = new THREE.Group();
      fg.rotation.x = (f / 4) * Math.PI * 2;
      fg.add(extrude(finProf, 0.05, M.orange, 0.01));
      auv.add(fg);
    }
    const auvKort = mk(new THREE.TorusGeometry(0.30, 0.045, 10, 24), M.supDark);
    auvKort.rotation.y = Math.PI / 2; auvKort.position.set(-2.18, 0, 0);
    auv.add(auvKort);

    const auvProp = new THREE.Group();
    auvProp.position.set(-2.18, 0, 0);
    auvProp.scale.setScalar(0.30);
    for (let b = 0; b < 5; b++) {
      const bg = new THREE.Group();
      bg.rotation.x = (b / 5) * Math.PI * 2;
      bg.add(mk(skewBladeGeo, M.bronze));
      auvProp.add(bg);
    }
    auv.add(auvProp);

    // Volumetric Multibeam Sonar Swath Cone + Scan Ring
    const sonarGeo = new THREE.ConeGeometry(5.2, 5.2, 28, 1, true);
    sonarGeo.scale(0.45, 1.0, 1.35);
    sonarGeo.translate(0, -2.6, 0);
    const sonarCone = new THREE.Mesh(sonarGeo, M.sonarCone);
    auv.add(sonarCone);
    auv.position.set(4.0, -5.2, 8.5);
    root.add(auv);

    // 8c. SAR Distress Target Buoy
    const targetBuoy = new THREE.Group();
    const buoyFloat = mk(new THREE.TorusGeometry(1.15, 0.38, 14, 28), M.orange);
    buoyFloat.rotation.x = Math.PI / 2; targetBuoy.add(buoyFloat);
    const buoyTower = mk(new THREE.CylinderGeometry(0.12, 0.35, 2.2, 12), M.crane);
    buoyTower.position.y = 1.1; targetBuoy.add(buoyTower);
    const buoyBeacon = new THREE.Mesh(new THREE.SphereGeometry(0.22, 12, 10), M.strobe);
    buoyBeacon.position.y = 2.35; targetBuoy.add(buoyBeacon);
    const buoyRing = new THREE.Mesh(new THREE.RingGeometry(1.8, 2.2, 32), new THREE.MeshBasicMaterial({
      color: 0xf97316, transparent: true, opacity: 0.55, side: THREE.DoubleSide, depthWrite: false
    }));
    buoyRing.rotation.x = -Math.PI / 2; buoyRing.position.y = 0.08;
    targetBuoy.add(buoyRing);
    targetBuoy.position.set(32.0, 0.0, 18.0);
    root.add(targetBuoy);

    // ---------------------------------------------------------------- 9. Detailed Industrial Engine Room & Internals (Ported from InternalSystems.ts)
    const internals = new THREE.Group(); internals.visible = false; root.add(internals);

    // 9a. Structural Transverse Hull Bulkheads & Engine Room Bilge Floor Plate
    const structGeos = [];
    structGeos.push(rboxGeo(38.0, 0.12, 8.2, 0.03, 2, -2.0, -2.35, 0));
    [-14.0, -1.5, 4.5, 16.0].forEach(function (bx) {
      const bw = Math.max(2.5, hbW(bx) * 1.82);
      structGeos.push(boxGeo(bx, -0.85, -bw * 0.32, 0.14, 2.9, bw * 0.32));
      structGeos.push(boxGeo(bx, -0.85, bw * 0.32, 0.14, 2.9, bw * 0.32));
      structGeos.push(boxGeo(bx, 0.45, 0, 0.14, 0.45, bw));
    });
    internals.add(mk(SDV.mergeGeos(structGeos), M.frame));

    // 9b. Twin 16V Marine Diesel-Generator Sets (DG1 Port & DG2 Starboard, 450 kW each)
    function buildDieselGenSet(matBlock, zPos) {
      const g = new THREE.Group();
      g.position.set(-6.5, -1.45, zPos);
      // Heavy steel base skid rails + vibration mounts
      g.add(mk(rboxGeo(4.4, 0.18, 1.55, 0.04, 2, 0, -0.72, 0), M.supDark));
      // Main Engine Crankcase Block
      g.add(mk(rboxGeo(2.65, 1.18, 1.12, 0.12, 3, -0.55, 0, 0), matBlock));
      // 16 Angled V-Bank Cylinder Head Covers +Twin Turbochargers + Flywheel + Alternator Barrel
      const detGeos = [];
      for (let c = 0; c < 8; c++) {
        const cx = -1.55 + c * 0.28;
        [-1, 1].forEach(function (side) {
          const cyl = rboxGeo(0.22, 0.34, 0.36, 0.04, 2);
          cyl.rotateX(side * 0.48);
          cyl.translate(cx, 0.62, side * 0.38);
          detGeos.push(cyl);
        });
      }
      // Twin Turbocharger Snail Volutes + Intercooler Box
      [-0.38, 0.38].forEach(function (tz) {
        const tc = new THREE.TorusGeometry(0.22, 0.09, 10, 18);
        tc.rotateY(Math.PI / 2); tc.translate(0.75, 0.65, tz);
        detGeos.push(tc);
      });
      detGeos.push(rboxGeo(0.52, 0.46, 0.92, 0.06, 2, 0.82, 0.25, 0));
      // Exhaust Uptake Pipe rising to Funnel
      detGeos.push(barGeo([0.65, 0.72, 0], [3.5, 11.2, -zPos * 0.5], 0.14, 10));
      g.add(mk(SDV.mergeGeos(detGeos), M.rail));

      // Coupled Brushless Marine Alternator Barrel + Junction Box
      const altBarrel = mk(new THREE.CylinderGeometry(0.56, 0.56, 1.28, 24), matBlock);
      altBarrel.rotation.z = Math.PI / 2; altBarrel.position.set(1.42, -0.05, 0);
      g.add(altBarrel);
      g.add(mk(rboxGeo(0.48, 0.42, 0.52, 0.04, 2, 1.42, 0.62, 0), M.supDark));
      return g;
    }
    internals.add(buildDieselGenSet(M.dg1, -2.15));
    internals.add(buildDieselGenSet(M.dg2, 2.15));

    // 9c. Anodized Lithium-Ion ESS Battery Banks with Live Glowing SOC Bar Gauges (from InternalSystems.ts)
    const battGeos = [], ribGeos = [], socGreenGeos = [], socAmberGeos = [];
    [-2.35, 2.35].forEach(function (bz) {
      const sign = bz > 0 ? 1 : -1;
      for (let r = 0; r < 2; r++) {
        const bx = 0.4 + r * 1.65;
        battGeos.push(rboxGeo(1.45, 1.35, 1.05, 0.08, 3, bx, -1.35, bz));
        for (let k = -4; k <= 4; k++) {
          ribGeos.push(boxGeo(bx + k * 0.14, -1.35, bz, 0.03, 1.38, 1.08));
        }
        // 7-Segment Glowing LED SOC Bars on Outboard Face
        for (let s = 0; s < 7; s++) {
          const bar = boxGeo(bx, -1.82 + s * 0.15, bz + sign * 0.54, 0.85, 0.07, 0.02);
          if (s === 6) socAmberGeos.push(bar); else socGreenGeos.push(bar);
        }
      }
    });
    internals.add(mk(SDV.mergeGeos(battGeos), M.batt));
    internals.add(mk(SDV.mergeGeos(ribGeos), M.supDark));
    internals.add(mk(SDV.mergeGeos(socGreenGeos), M.led));
    internals.add(mk(SDV.mergeGeos(socAmberGeos), M.ledAmber));

    // 9d. Main HVDC Switchboard & Cross-Hull Solid Copper Busbars on Cable Trays (from InternalSystems.ts)
    internals.add(mk(rboxGeo(1.25, 1.85, 3.6, 0.08, 3, -2.4, -1.1, 0), M.crane));
    const busGeos = [];
    [-0.22, 0.0, 0.22].forEach(function (z) {
      busGeos.push(boxGeo(2.0, -0.45, z, 32.0, 0.06, 0.09));
    });
    internals.add(mk(SDV.mergeGeos(busGeos), M.copper));
    internals.add(mk(rboxGeo(32.4, 0.06, 0.85, 0.02, 2, 2.0, -0.52, 0), M.supDark));

    // 9e. G5 Edge Compute Server Racks with Louvers, Pull Handles & Dual LED Columns (from InternalSystems.ts)
    const rackGeos = [], rackTrimGeos = [], rackLedGeos = [];
    for (let i = 0; i < 4; i++) {
      const rz = -2.4 + i * 1.6;
      rackGeos.push(rboxGeo(1.05, 2.15, 0.88, 0.06, 3, -9.6, 4.05, rz));
      for (let s = -3; s <= 3; s++) {
        rackTrimGeos.push(boxGeo(-9.6, 4.05 + s * 0.24, rz, 1.08, 0.03, 0.72));
      }
      [-0.28, 0.28].forEach(function (hz) {
        rackTrimGeos.push(barGeo([-9.05, 3.35, rz + hz], [-9.05, 4.75, rz + hz], 0.022, 6));
      });
      for (let k = 0; k < 8; k++) {
        rackLedGeos.push(boxGeo(-9.06, 3.25 + k * 0.22, rz - 0.18, 0.03, 0.05, 0.16));
        rackLedGeos.push(boxGeo(-9.06, 3.25 + k * 0.22, rz + 0.18, 0.03, 0.05, 0.16));
      }
    }
    internals.add(mk(SDV.mergeGeos(rackGeos), M.rack));
    internals.add(mk(SDV.mergeGeos(rackTrimGeos), M.rail));
    internals.add(mk(SDV.mergeGeos(rackLedGeos), M.led));

    // 9f. Finned Seawater Heat Exchangers with Spinning 7-Blade Fans + Cylindrical Saddled Tanks (from InternalSystems.ts)
    const coolFans = [];
    [-1.85, 1.85].forEach(function (cz) {
      const hxGrp = new THREE.Group();
      hxGrp.position.set(-11.6, -1.25, cz);
      hxGrp.add(mk(rboxGeo(1.35, 1.10, 0.95, 0.07, 3), M.supDark));
      const finG = [];
      for (let f = -4; f <= 4; f++) finG.push(boxGeo(f * 0.13, 0, 0, 0.025, 1.14, 0.98));
      hxGrp.add(mk(SDV.mergeGeos(finG), M.rail));
      const shroud = mk(new THREE.TorusGeometry(0.38, 0.045, 10, 24), M.casting);
      shroud.position.set(0, 0, cz > 0 ? 0.50 : -0.50);
      hxGrp.add(shroud);

      const fan = new THREE.Group();
      fan.position.set(0, 0, cz > 0 ? 0.49 : -0.49);
      const hub = mk(new THREE.CylinderGeometry(0.09, 0.09, 0.08, 12), M.bronze);
      hub.rotation.x = Math.PI / 2; fan.add(hub);
      for (let b = 0; b < 7; b++) {
        const bg = new THREE.Group();
        bg.rotation.z = (b / 7) * Math.PI * 2;
        const bl = mk(boxGeo(0, 0.20, 0, 0.09, 0.26, 0.018), M.rail);
        bl.rotation.y = 0.45;
        bg.add(bl); fan.add(bg);
      }
      hxGrp.add(fan);
      coolFans.push(fan);
      internals.add(hxGrp);
    });

    // Cylindrical Fuel & Ballast Tanks with Hemispherical Domes + Saddle Cradles + Bow Thruster Motor Drum
    const tankGeos = [], saddleGeos = [];
    [-2.55, 2.55].forEach(function (tz) {
      const cyl = new THREE.CylinderGeometry(0.88, 0.88, 7.6, 24);
      cyl.rotateZ(Math.PI / 2); cyl.translate(8.5, -1.55, tz);
      tankGeos.push(cyl);
      [-3.8, 3.8].forEach(function (dx) {
        const dome = new THREE.SphereGeometry(0.88, 20, 14);
        dome.translate(8.5 + dx, -1.55, tz);
        tankGeos.push(dome);
      });
      [-2.6, -0.9, 0.9, 2.6].forEach(function (sx) {
        saddleGeos.push(rboxGeo(0.28, 0.65, 1.95, 0.04, 2, 8.5 + sx, -2.05, tz));
      });
    });
    const btDrum = new THREE.CylinderGeometry(0.72, 0.72, 3.6, 20);
    btDrum.rotateX(Math.PI / 2); btDrum.translate(21.8, -1.85, 0);
    tankGeos.push(btDrum);
    // Below-deck 76mm Gun Automatic Carousel Magazine Drum & Hoist Trunk (shown in blueprint under the bow gun)
    const magDrum = new THREE.CylinderGeometry(1.15, 1.15, 2.35, 24);
    magDrum.translate(18.2, 3.45, 0);
    tankGeos.push(magDrum);
    internals.add(mk(SDV.mergeGeos(tankGeos), M.tank));
    internals.add(mk(SDV.mergeGeos(saddleGeos), M.supDark));

    // Seawater Cooling Manifolds + Animated Flow Pulses
    internals.add(mk(barGeo([-12.5, 1.3, -0.55], [14.5, 1.3, -0.55], 0.10, 10), M.pipeCold));
    internals.add(mk(barGeo([-12.5, 1.3, 0.55], [14.5, 1.3, 0.55], 0.10, 10), M.pipeHot));
    const FLOW_N = 40;
    const flow = new THREE.InstancedMesh(new THREE.SphereGeometry(0.13, 8, 6), M.flow, FLOW_N);
    internals.add(flow);

    const ghost = new THREE.Mesh(rboxGeo(1, 1, 1, 0.06, 2), M.ghost); ghost.visible = false; root.add(ghost);
    const SLOTS = {
      bay1: [-24.8, 4.18, 0, 6.2, 2.7, 2.6], bay2: [-18.2, 4.18, 0, 3.1, 2.7, 2.6], hangar: [-9.8, 4.75, 0, 4.6, 3.0, 3.2],
      hangar_roof: [0.9, 7.6, 0, 3.2, 1.8, 3.2], mast_3: [5.85, 17.4, 0, 1.8, 1.8, 1.8]
    };

    const anchors = {
      bay1: [-24.8, 6.0, 0],
      bay2: [-18.2, 6.0, 0],
      mast: [5.85, 19.2, 0],
      hangarRoof: [-2.2, 8.2, -2.1],
      pad: [0.9, 8.5, 0],
      auv: [4.0, -4.2, 8.5],
      target: [32.0, 3.2, 18.0],
      bridge: [10.5, 11.2, 0],
      gun: [18.2, bowY + 2.5, 0],
      dg2: [-6.5, 0.2, 2.15],
      rack: [-9.6, 6.4, 0],
      pods: [-23.8, -2.8, 1.65]
    };

    // ================================================================== Public API
    const api = { root: root, anchors: anchors, M: M, drone: drone, auv: auv, targetBuoy: targetBuoy };
    let xray = false, livery = 'naval', night = false;

    api.setXray = function (on) {
      xray = !!on;
      shell.forEach(function (m) { m.material = xray ? M.xray : m.userData.mat0; m.castShadow = !xray; });
      hideInXray.forEach(function (o) { o.visible = !xray; });
      internals.visible = xray;
    };
    api.isXray = function () { return xray; };
    api.setLivery = function (name) {
      livery = name;
      const naval = name === 'naval';
      M.hull.color.copy(col(naval ? 0x9ea8b2 : 0xf2f5f8));
      M.sup.color.copy(col(naval ? 0xa8b2bc : 0xf8fafc));
      M.stripe.color.copy(col(naval ? 0xe05224 : 0x0f5ea8));
      gunGrp.visible = naval; monGrp.visible = !naval;
    };
    api.setNight = function (n) {
      night = !!n;
      M.glass.emissiveIntensity = night ? 0.9 : 0.0;
    };
    api.showGhost = function (slot, verdict) {
      if (!slot || !SLOTS[slot]) { ghost.visible = false; return; }
      const s = SLOTS[slot];
      ghost.position.set(s[0], s[1], s[2]); ghost.scale.set(s[3], s[4], s[5]);
      M.ghost.color.set(verdict === 'YES' ? 0x22c55e : verdict === 'YES_IF' ? 0xf59e0b : 0xef4444);
      ghost.visible = true;
    };

    const flowPos = [];
    for (let i = 0; i < FLOW_N; i++) flowPos.push(Math.random());

    api.update = function (t, dt, S) {
      radar.rotation.y += dt * 2.4;
      radar2.rotation.y -= dt * 3.1;
      if (mastGimbal.userData.yoke) mastGimbal.userData.yoke.rotation.y = Math.sin(t * 0.7) * 0.65;
      if (uavGimbal.userData.yoke) uavGimbal.userData.yoke.rotation.y = Math.sin(t * 1.2) * 0.80;
      crTurret.rotation.y = Math.sin(t * 0.25) * 0.14;
      gunTurret.rotation.y = Math.sin(t * 0.32) * 0.16;

      const spin = 9 + S.speedKn * 1.2;
      pods.forEach(function (p) { p.prop.rotation.x += dt * spin; });
      auvProp.rotation.x += dt * 18.0;

      // Update Propeller Cavitation Bubble Stream
      for (let i = 0; i < CAV_N; i++) {
        const cs = cavSeeds[i];
        cs.phase = (cs.phase + dt * (0.65 + S.speedKn * 0.08)) % 1;
        const p = cs.phase;
        const swirl = cs.ang + p * 8.0 + t * 5.0;
        const r = cs.rad * (0.25 + p * 1.35);
        dummy.position.set(
          -24.1 - p * 6.5,
          -2.28 + Math.sin(swirl) * r * 0.45 + p * 0.35,
          cs.side * 1.65 + Math.cos(swirl) * r
        );
        const sc = (1 - p) * (0.55 + 0.45 * Math.sin(p * Math.PI));
        dummy.scale.setScalar(Math.max(0.01, sc));
        dummy.updateMatrix();
        cavMesh.setMatrixAt(i, dummy.matrix);
      }
      cavMesh.instanceMatrix.needsUpdate = true;

      M.strobe.emissiveIntensity = (Math.sin(t * 9) > 0.8 ? 3.5 : 0.2);
      const nI = night ? 1.8 : 0.6;
      M.navRed.emissiveIntensity = nI; M.navGreen.emissiveIntensity = nI; M.navWhite.emissiveIntensity = night ? 2.2 : 0.7;

      const g5OnHangarRoof = S.g5AntennaPos === 'hangar_roof';
      hangarRoofAntenna.visible = g5OnHangarRoof;
      if (g5OnHangarRoof) {
        rfRing.position.set(-2.2, 7.55, -2.1);
      } else {
        rfRing.position.set(5.85, 17.4, 0);
      }
      const rfPulse = (t * 0.8) % 1;
      rfRing.scale.setScalar(1 + rfPulse * 4.5);
      M.rfRing.opacity = S.g5Degraded ? 0.08 : (1 - rfPulse) * 0.42;

      const bPulse = (t * 0.65) % 1;
      buoyRing.scale.setScalar(1 + bPulse * 2.8);
      buoyRing.material.opacity = (1 - bPulse) * 0.65;
      targetBuoy.position.y = Math.sin(t * 1.9) * 0.25 - root.position.y * 0.6;

      // Underwater G3 Seabed AUV animation
      const holding = S.g3 === 'HOLD_ACOUSTIC';
      if (holding) {
        auv.position.set(1.5 + Math.sin(t * 0.8) * 0.6, -5.0 + Math.cos(t * 1.1) * 0.2, 7.8);
        auv.rotation.y = Math.sin(t * 0.5) * 0.08;
        M.sonarCone.color.setHex(0xf59e0b);
        M.sonarCone.opacity = 0.16 + 0.12 * Math.sin(t * 5.0);
      } else {
        const sx = 6.0 + Math.sin(t * 0.35) * 14.0;
        const sz = 9.0 + Math.cos(t * 0.70) * 2.2;
        const vx = Math.cos(t * 0.35) * 14.0 * 0.35;
        const vz = -Math.sin(t * 0.70) * 2.2 * 0.70;
        auv.position.set(sx, -5.2 + Math.sin(t * 1.4) * 0.18, sz);
        auv.rotation.y = Math.atan2(-vz, vx) * 0.35;
        M.sonarCone.color.setHex(0x22d3ee);
        M.sonarCone.opacity = 0.24 + 0.06 * Math.sin(t * 3.5);
      }
      anchors.auv[0] = auv.position.x; anchors.auv[1] = auv.position.y + 1.1; anchors.auv[2] = auv.position.z;

      // G4 VTOL Drone Flight State Machine (Skids sit flush at y = 7.34m on top of the y = 7.02m helideck!)
      const dm = S.drone || { mode: 'landed', phase: 0 };
      const padX = 0.9, padY = 7.34, padZ = 0.0;
      let px = padX, py = padY, pz = padZ, spinR = 0;
      if (dm.mode === 'patrol') {
        const a = t * 0.55;
        const tkx = 24.0 + Math.cos(a) * 14.0;
        const tkz = 14.0 + Math.sin(a) * 11.0;
        const climb = smooth(clamp(dm.phase * 4.0, 0, 1));
        px = padX * (1 - climb) + tkx * climb;
        pz = padZ * (1 - climb) + tkz * climb;
        py = padY * (1 - climb) + (21.5 + Math.sin(t * 1.1) * 1.2) * climb;
        spinR = 42;
        uavCone.visible = climb > 0.4;
        padLedMat.color.setHex(0x38bdf8);
      } else if (dm.mode === 'recover') {
        const startX = 24.0, startZ = 14.0, startY = 21.5;
        const horizK = smooth(clamp(dm.phase * 1.45, 0, 1));
        px = startX * (1 - horizK) + padX * horizK;
        pz = startZ * (1 - horizK) + padZ * horizK;
        const descK = smooth(clamp((dm.phase - 0.35) / 0.65, 0, 1));
        py = startY * (1 - descK) + padY * descK;
        spinR = 38 * (1 - 0.7 * descK);
        uavCone.visible = dm.phase < 0.5;
        padLedMat.color.setHex(Math.sin(t * 10) > 0 ? 0xfacc15 : 0x22c55e);
      } else {
        px = padX; py = padY; pz = padZ;
        spinR = 0;
        uavCone.visible = false;
        padLedMat.color.setHex(0x22c55e);
      }
      const ox = drone.position.x, oz = drone.position.z;
      drone.position.set(px, py, pz);
      const dvx = px - ox, dvz = pz - oz;
      if (Math.hypot(dvx, dvz) > 0.002 && dm.mode !== 'landed') {
        drone.rotation.y = Math.atan2(-dvz, dvx);
      } else if (dm.mode === 'landed') {
        drone.rotation.y = 0;
      }
      rotors.forEach(function (r) { r.rotation.y += dt * spinR; });
      anchors.pad[0] = px; anchors.pad[1] = py + 1.6; anchors.pad[2] = pz;

      // DG2, Cooling Fans & Coolant Loop Status
      const trip = !!S.gen2Tripped, pulse = 0.5 + 0.5 * Math.sin(t * 8);
      M.dg2.color.copy(col(trip ? 0xc0392b : 0x1f8f63));
      M.dg2.emissive.copy(col(trip ? 0xff3b30 : 0x0b3a28));
      M.dg2.emissiveIntensity = trip ? 0.6 + 0.9 * pulse : 0.55;
      const ledCol = S.coolingDegraded || S.g5Degraded ? 0xf5b83d : 0x18e0a0;
      M.led.emissive.copy(col(ledCol));
      M.pipeCold.emissive.copy(col(S.coolingDegraded ? 0xff5a36 : 0x0a4a73));

      const fanSpin = S.coolingDegraded ? 3.0 : 14.0;
      coolFans.forEach(function (f) { f.rotation.z += dt * fanSpin; });

      if (xray) {
        const speed = S.coolingDegraded ? 0.07 : 0.16;
        for (let i = 0; i < FLOW_N; i++) {
          flowPos[i] = (flowPos[i] + dt * speed) % 1;
          const x = 14.5 - flowPos[i] * 27, z = (i % 2 ? 0.55 : -0.55);
          dummy.position.set(i % 2 ? 14.5 - (1 - flowPos[i]) * 27 : x, 1.3, z);
          dummy.scale.set(1, 1, 1); dummy.rotation.set(0, 0, 0); dummy.updateMatrix();
          flow.setMatrixAt(i, dummy.matrix);
        }
        flow.instanceMatrix.needsUpdate = true;
      }
    };

    api.setLivery('naval');
    api.setNight(false);
    return api;
  };
})();

/* viz3d/js/util.js — shared helpers. Classic script (no modules) so it works from file:// and http://. */
(function () {
  'use strict';
  var THREE = window.THREE;
  var SDV = (window.SDV = window.SDV || {});

  SDV.clamp = function (x, a, b) { return Math.min(b, Math.max(a, x)); };
  SDV.lerp = function (a, b, t) { return a + (b - a) * t; };
  SDV.smooth = function (t) { t = SDV.clamp(t, 0, 1); return t * t * (3 - 2 * t); };

  /** sRGB hex -> linear THREE.Color (scene lighting is linear, output is sRGB). */
  SDV.col = function (hex) {
    var c = new THREE.Color();
    var r = ((hex >> 16) & 255) / 255, g = ((hex >> 8) & 255) / 255, b = (hex & 255) / 255;
    c.setRGB(Math.pow(r, 2.2), Math.pow(g, 2.2), Math.pow(b, 2.2));
    return c;
  };

  /** Piecewise-linear interpolation over [[x,y],...]. */
  SDV.interp = function (P, x) {
    if (x <= P[0][0]) return P[0][1];
    for (var i = 1; i < P.length; i++) {
      if (x <= P[i][0]) {
        var t = (x - P[i - 1][0]) / (P[i][0] - P[i - 1][0]);
        return P[i - 1][1] + (P[i][1] - P[i - 1][1]) * t;
      }
    }
    return P[P.length - 1][1];
  };

  /** Registry so the environment can scale reflection strength per time of day. */
  SDV.stdMats = [];
  SDV.std = function (mat, envBase) {
    mat.userData.envBase = envBase === undefined ? 1 : envBase;
    SDV.stdMats.push(mat);
    return mat;
  };

  SDV.canvasTexture = function (w, h, draw, opts) {
    opts = opts || {};
    var c = document.createElement('canvas');
    c.width = w; c.height = h;
    var g = c.getContext('2d');
    draw(g, w, h);
    var t = new THREE.CanvasTexture(c);
    t.wrapS = t.wrapT = opts.clamp ? THREE.ClampToEdgeWrapping : THREE.RepeatWrapping;
    if (opts.srgb) t.encoding = THREE.sRGBEncoding;
    t.anisotropy = 8;
    t.needsUpdate = true;
    return t;
  };

  /**
   * Exact procedural RoundedBoxGeometry (chamfered/filleted edges + smooth analytical normals)
   * matching three/examples/jsm/geometries/RoundedBoxGeometry from the legacy simulation.
   */
  SDV.rboxGeo = function (sx, sy, sz, rad, seg, cx, cy, cz) {
    var s = Math.max(1, seg || 3);
    var N = 2 * s + 1;
    var r = Math.min(rad !== undefined ? rad : Math.min(sx, sy, sz) * 0.12, sx * 0.48, sy * 0.48, sz * 0.48);
    var hx = sx * 0.5 - r, hy = sy * 0.5 - r, hz = sz * 0.5 - r;
    var g = new THREE.BoxGeometry(1, 1, 1, N, N, N);
    var pos = g.attributes.position;
    var nor = g.attributes.normal;
    function mapAxis(u, H, r0) {
      var idx = Math.round((u + 0.5) * N);
      if (idx <= s) return -H + (idx / s) * r0;
      return H - ((N - idx) / s) * r0;
    }
    for (var i = 0; i < pos.count; i++) {
      var px = mapAxis(pos.getX(i), sx * 0.5, r);
      var py = mapAxis(pos.getY(i), sy * 0.5, r);
      var pz = mapAxis(pos.getZ(i), sz * 0.5, r);
      var cx0 = SDV.clamp(px, -hx, hx);
      var cy0 = SDV.clamp(py, -hy, hy);
      var cz0 = SDV.clamp(pz, -hz, hz);
      var dx = px - cx0, dy = py - cy0, dz = pz - cz0;
      var len = Math.hypot(dx, dy, dz) || 1;
      var nx = dx / len, ny = dy / len, nz = dz / len;
      pos.setXYZ(i, cx0 + nx * r + (cx || 0), cy0 + ny * r + (cy || 0), cz0 + nz * r + (cz || 0));
      nor.setXYZ(i, nx, ny, nz);
    }
    pos.needsUpdate = true;
    nor.needsUpdate = true;
    return g;
  };

  /** Merge indexed/non-indexed geometries that all have position+normal(+uv) into one. */
  SDV.mergeGeos = function (geos) {
    var vCount = 0, iCount = 0, i;
    for (i = 0; i < geos.length; i++) {
      vCount += geos[i].attributes.position.count;
      iCount += geos[i].index ? geos[i].index.count : geos[i].attributes.position.count;
    }
    var pos = new Float32Array(vCount * 3), nor = new Float32Array(vCount * 3), uv = new Float32Array(vCount * 2);
    var idx = new Uint32Array(iCount);
    var vo = 0, io = 0;
    for (i = 0; i < geos.length; i++) {
      var g = geos[i], p = g.attributes.position, n = g.attributes.normal, u = g.attributes.uv;
      pos.set(p.array, vo * 3);
      if (n) nor.set(n.array, vo * 3);
      if (u) uv.set(u.array, vo * 2);
      var k;
      if (g.index) { for (k = 0; k < g.index.count; k++) idx[io + k] = g.index.array[k] + vo; io += g.index.count; }
      else { for (k = 0; k < p.count; k++) idx[io + k] = vo + k; io += p.count; }
      vo += p.count;
      g.dispose();
    }
    var out = new THREE.BufferGeometry();
    out.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    out.setAttribute('normal', new THREE.BufferAttribute(nor, 3));
    out.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    out.setIndex(new THREE.BufferAttribute(idx, 1));
    return out;
  };
})();

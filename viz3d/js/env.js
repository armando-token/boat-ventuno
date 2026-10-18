/* viz3d/js/env.js — Exact port of Legacy SkySystem.ts + OceanSurface.ts + WakeTrail.ts,
   plus atmospheric San Francisco Bay horizon (Golden Gate Bridge + Marin Headlands).
   Compatible with Three.js r147 (WebGL 1 / GLSL ES 1.00 & WebGL 2). */
(function () {
  'use strict';
  var THREE = window.THREE, SDV = window.SDV, col = SDV.col;

  // ================================================================== 1. Shared Gerstner Wave Set (from OceanSurface.ts)
  var WAVES = [
    [0.80, 0.60, 38.0, 0.20],
    [0.55, 0.83, 21.0, 0.22],
    [0.93, 0.36, 12.5, 0.18],
    [0.30, 0.95, 7.5,  0.14],
    [0.99, 0.10, 4.6,  0.10]
  ];
  SDV.WAVES = WAVES;

  /** CPU mirror of the vertex Gerstner wave sum for vessel heave/pitch/roll and floating buoy. */
  SDV.waveH = function (x, z, t, amp, dx, dz) {
    var px = x + (dx || 0), pz = z + (dz || 0), y = 0;
    for (var i = 0; i < WAVES.length; i++) {
      var w = WAVES[i], len = Math.hypot(w[0], w[1]);
      var k = (Math.PI * 2) / w[2], c = Math.sqrt(9.81 / k);
      var f = k * ((w[0] / len) * px + (w[1] / len) * pz - c * t);
      y += w[2] * 0.011 * (w[3] / 0.2) * amp * Math.sin(f);
    }
    return y;
  };

  // ================================================================== 2. Sky System Palettes (from SkySystem.ts, linear RGB)
  var PALETTES = {
    day: {
      horizon: [0.52, 0.64, 0.76],
      zenith: [0.10, 0.27, 0.56],
      sunColor: [1.00, 0.84, 0.62],
      sunTint: [1.00, 0.55, 0.28],
      fog: [0.46, 0.58, 0.70],
      waterDeep: [0.008, 0.065, 0.098],
      waterShallow: [0.032, 0.25, 0.29],
      hemiSky: [0.58, 0.72, 0.90],
      hemiGround: [0.08, 0.16, 0.22],
      sunIntensity: 3.4,
      fillIntensity: 0.45,
      envIntensity: 0.98,
      clouds: 0.55,
      disc: 22,
      elevDeg: 14,
      night: 0,
      exposure: 1.0,
      fogDensity: 0.00045
    },
    golden: {
      horizon: [0.76, 0.44, 0.28],
      zenith: [0.11, 0.22, 0.44],
      sunColor: [1.00, 0.66, 0.36],
      sunTint: [1.00, 0.42, 0.18],
      fog: [0.64, 0.42, 0.30],
      waterDeep: [0.010, 0.055, 0.088],
      waterShallow: [0.040, 0.21, 0.25],
      hemiSky: [0.68, 0.54, 0.48],
      hemiGround: [0.12, 0.14, 0.18],
      sunIntensity: 3.0,
      fillIntensity: 0.40,
      envIntensity: 0.92,
      clouds: 0.60,
      disc: 24,
      elevDeg: 8.5,
      night: 0,
      exposure: 0.98,
      fogDensity: 0.00050
    },
    night: {
      horizon: [0.018, 0.032, 0.060],
      zenith: [0.003, 0.007, 0.020],
      sunColor: [0.42, 0.52, 0.78],
      sunTint: [0.10, 0.14, 0.26],
      fog: [0.012, 0.022, 0.045],
      waterDeep: [0.003, 0.016, 0.028],
      waterShallow: [0.010, 0.060, 0.088],
      hemiSky: [0.05, 0.09, 0.18],
      hemiGround: [0.01, 0.018, 0.03],
      sunIntensity: 0.55,
      fillIntensity: 0.18,
      envIntensity: 0.32,
      clouds: 0.20,
      disc: 1.6,
      elevDeg: 18,
      night: 1,
      exposure: 1.12,
      fogDensity: 0.00055
    }
  };
  var SUN_AZIMUTH = new THREE.Vector2(0.88, -0.46).normalize();

  var SKY_GLSL = [
    'uniform vec3 uHorizon; uniform vec3 uZenith; uniform vec3 uSunColor; uniform vec3 uSunTint; uniform vec3 uSunDir; uniform float uDisc;',
    'vec3 skyColor(vec3 d) {',
    '  float h = clamp(d.y, 0.0, 1.0);',
    '  vec3 col = mix(uHorizon, uZenith, pow(h, 0.45));',
    '  float s = max(dot(d, uSunDir), 0.0);',
    '  col = mix(col, uSunTint, pow(s, 3.0) * pow(1.0 - h, 3.0) * 0.55);',
    '  col += uSunColor * (pow(s, 8.0) * 0.16 + pow(s, 80.0) * 0.45 + pow(s, 3000.0) * uDisc);',
    '  return col;',
    '}'
  ].join('\n');

  var NOISE_GLSL = [
    'float hash(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }',
    'float noise(vec2 p){ vec2 i = floor(p); vec2 f = fract(p); f = f * f * (3.0 - 2.0 * f);',
    '  return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), f.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), f.x), f.y); }',
    'float fbm(vec2 p){ float a = 0.5; float s = 0.0; for (int i = 0; i < 4; i++) { s += a * noise(p); p *= 2.03; a *= 0.5; } return s; }'
  ].join('\n');

  var DOME_VERT = 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }';

  var DOME_FRAG = [
    'varying vec3 vDir; uniform float uTime; uniform float uClouds; uniform float uNight;',
    SKY_GLSL,
    NOISE_GLSL,
    'void main(){',
    '  vec3 d = normalize(vDir);',
    '  vec3 col = skyColor(d);',
    '  if (d.y > 0.0) {',
    '    vec2 cp = d.xz / (d.y + 0.18) * 1.6 + vec2(uTime * 0.004, 0.0);',
    '    float c = smoothstep(0.52, 0.86, fbm(cp) * (0.55 + uClouds));',
    '    float lit = 0.55 + 0.45 * max(dot(d, uSunDir), 0.0);',
    '    vec3 cloudCol = mix(uHorizon * 1.1, uSunColor * 0.9 + vec3(0.25), lit * 0.6 * (1.0 - uNight));',
    '    col = mix(col, cloudCol, c * 0.55 * smoothstep(0.0, 0.25, d.y));',
    '    if (uNight > 0.01) {',
    '      vec2 sp = d.xz / (d.y + 0.35) * 70.0;',
    '      float st = step(0.9965, hash(floor(sp))) * (0.6 + 0.4 * hash(floor(sp) + 7.0));',
    '      col += vec3(0.8, 0.85, 1.0) * st * uNight * smoothstep(0.08, 0.3, d.y) * (1.0 - c);',
    '    }',
    '  } else {',
    '    col = mix(uHorizon * 0.75, vec3(0.01, 0.06, 0.09), clamp(-d.y * 6.0, 0.0, 1.0));',
    '  }',
    '  gl_FragColor = vec4(col, 1.0);',
    '  #include <tonemapping_fragment>',
    '  #include <encodings_fragment>',
    '}'
  ].join('\n');

  // ================================================================== 3. Ocean Surface (Exact Port of OceanSurface.ts)
  // Radial grid dense near the vessel (r = R * (i/rings)^2.25) so 3D waves are crisp right around the hull
  function createOceanGeometry(rings, segments, radius) {
    var pos = new Float32Array((rings + 1) * (segments + 1) * 3);
    var o = 0, i, j;
    for (i = 0; i <= rings; i++) {
      var r = radius * Math.pow(i / rings, 2.25);
      for (j = 0; j <= segments; j++) {
        var a = (j / segments) * Math.PI * 2;
        pos[o++] = Math.cos(a) * r;
        pos[o++] = 0;
        pos[o++] = Math.sin(a) * r;
      }
    }
    var idx = [];
    for (i = 0; i < rings; i++) {
      for (j = 0; j < segments; j++) {
        var a0 = i * (segments + 1) + j;
        var b0 = a0 + 1;
        var c0 = a0 + segments + 1;
        var d0 = c0 + 1;
        idx.push(a0, c0, b0, b0, c0, d0);
      }
    }
    var g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setIndex(idx);
    return g;
  }

  var OCEAN_VERT = [
    'uniform float uTime; uniform float uAmp; uniform vec2 uDrift; uniform vec4 uWave[5]; uniform float uFoamGain;',
    'varying vec3 vWorld; varying vec3 vNormalW; varying float vFoam; varying float vHeight;',
    'void main(){',
    '  vec4 wp = modelMatrix * vec4(position, 1.0);',
    '  vec3 p = wp.xyz;',
    '  vec2 xz = p.xz + uDrift;',
    '  float dist0 = length(p.xz);',
    '  float fadeFar = 1.0 - smoothstep(450.0, 2200.0, dist0);',
    '  float mask = smoothstep(3.0, 14.0, length(p.xz * vec2(0.28, 1.0)));',
    '  float ampLocal = uAmp * (0.40 + 0.60 * mask) * fadeFar;',
    '  vec3 disp = vec3(0.0); vec2 slope = vec2(0.0); float jac = 1.0;',
    '  for (int i = 0; i < 5; i++) {',
    '    vec2 d = normalize(uWave[i].xy); float wl = uWave[i].z; float k = 6.28318 / wl;',
    '    float c = sqrt(9.81 / k); float f = k * (dot(d, xz) - c * uTime);',
    '    float a = wl * 0.0125 * (uWave[i].w / 0.2) * ampLocal;',
    '    disp.xz += d * (a * cos(f)); disp.y += a * sin(f);',
    '    slope += d * (k * a * cos(f));',
    '    jac -= k * a * sin(f) * uFoamGain;',
    '  }',
    '  p += disp;',
    '  vWorld = p; vHeight = disp.y;',
    '  vNormalW = normalize(vec3(-slope.x, 1.0, -slope.y));',
    '  vFoam = 1.0 - smoothstep(0.04, 0.36, jac);',
    '  gl_Position = projectionMatrix * viewMatrix * vec4(p, 1.0);',
    '}'
  ].join('\n');

  var OCEAN_FRAG = [
    'precision highp float;',
    'uniform float uTime; uniform vec3 uCamPos; uniform float uOpacity;',
    'uniform vec3 uFogColor; uniform float uFogDensity; uniform vec3 uDeep; uniform vec3 uShallow; uniform vec2 uDrift;',
    'uniform float uRough; uniform float uNight; uniform float uWake;',
    'varying vec3 vWorld; varying vec3 vNormalW; varying float vFoam; varying float vHeight;',
    SKY_GLSL,
    NOISE_GLSL,
    'float hgt(vec2 p){',
    '  return noise(p * 0.85 + vec2(uTime * 0.35, uTime * 0.22)) * 0.60',
    '       + noise(p * 2.25 - vec2(uTime * 0.52, uTime * 0.31)) * 0.30',
    '       + noise(p * 5.60 + vec2(uTime * 0.90, -uTime * 0.48)) * 0.10;',
    '}',
    'float hullHalf(float x){',
    '  float aft = smoothstep(-30.0, -20.0, x);',
    '  float base = mix(4.1, 5.4, aft);',
    '  float t = clamp((x - 7.5) / 20.5, 0.0, 0.999);',
    '  float bowTap = max(1.0 - t * t, 0.001);',
    '  return base * pow(bowTap, 0.65);',
    '}',
    'void main(){',
    '  vec3 toCam = uCamPos - vWorld; float dist = length(toCam); vec3 V = toCam / max(dist, 0.001);',
    '  vec3 N = normalize(vNormalW);',
    '  vec2 q = vWorld.xz + uDrift;',
    '  float fadeD = 1.0 - smoothstep(25.0, 260.0, dist);',
    '  float e = 0.07;',
    '  vec2 g = vec2(hgt(q + vec2(e, 0.0)) - hgt(q - vec2(e, 0.0)), hgt(q + vec2(0.0, e)) - hgt(q - vec2(0.0, e)));',
    '  N = normalize(vec3(N.x - g.x * uRough * 2.4 * fadeD, N.y, N.z - g.y * uRough * 2.4 * fadeD));',
    '',
    '  float cosT = clamp(dot(N, V), 0.0, 1.0);',
    '  // Keep rich deep ocean body colour visible even at shallow grazing angles so sea and sky are unmistakably distinct',
    '  float F = 0.02 + 0.72 * pow(1.0 - cosT, 4.2);',
    '  vec3 R = reflect(-V, N); R.y = abs(R.y);',
    '  vec3 sky = skyColor(normalize(R));',
    '  float sunLit = clamp(dot(N, uSunDir) * 0.5 + 0.5, 0.0, 1.0);',
    '  vec3 scatter = mix(uDeep, uShallow, smoothstep(-0.35, 0.45, vHeight)) * (0.42 + 0.95 * sunLit) * (1.0 - 0.8 * uNight);',
    '  vec3 col = mix(scatter, sky * 0.82, F);',
    '  vec3 Hh = normalize(V + uSunDir);',
    '  float spec = pow(max(dot(N, Hh), 0.0), 420.0) * 3.0 + pow(max(dot(N, Hh), 0.0), 36.0) * 0.18;',
    '  col += uSunColor * spec * (1.0 - 0.6 * uNight) * (0.25 + 0.75 * F) * 5.5;',
    '',
    '  // Wave whitecaps + 60m hull contact foam ring + bow wave + Kelvin V-wake arms',
    '  vec2 s = vWorld.xz; float az = abs(s.z);',
    '  float hw = hullHalf(s.x);',
    '  float inHull = step(-30.0, s.x) * step(s.x, 29.5);',
    '  float wlRing = inHull * smoothstep(hw - 0.40, hw + 0.12, az) * (1.0 - smoothstep(hw + 0.12, hw + 1.95, az));',
    '  float bx = s.x - 27.6; float bz = az * 0.85;',
    '  float bowSp = exp(-(bx * bx + bz * bz) / 14.0);',
    '  float dB = max(27.5 - s.x, 0.0);',
    '  float armZ = 1.2 + 0.21 * dB; float tA = (az - armZ) / (0.85 + 0.03 * dB);',
    '  float kelvin = step(s.x, 28.5) * exp(-tA * tA) * exp(-dB * 0.014);',
    '  float fn = noise(q * 2.8 + uTime * 0.6) * 0.65 + noise(q * 7.2 - uTime * 0.9) * 0.35;',
    '  float contact = clamp((wlRing * 1.15 + bowSp * 1.10 + kelvin * 0.85) * uWake, 0.0, 1.0);',
    '  float foam = clamp(vFoam * smoothstep(0.32, 0.74, fn) * 0.95 + contact * smoothstep(0.22, 0.68, fn + 0.15), 0.0, 1.0);',
    '  vec3 foamCol = vec3(0.88, 0.94, 0.98) * (0.48 + 0.52 * sunLit) * (1.0 - 0.7 * uNight);',
    '  col = mix(col, foamCol, foam * 0.88);',
    '',
    '  float ff = 1.0 - exp(-(dist * uFogDensity) * (dist * uFogDensity));',
    '  col = mix(col, uFogColor, clamp(ff, 0.0, 1.0));',
    '  gl_FragColor = vec4(col, uOpacity);',
    '  #include <tonemapping_fragment>',
    '  #include <encodings_fragment>',
    '}'
  ].join('\n');

  // ================================================================== 4. Dynamic Stern Wake Ribbon (from WakeTrail.ts)
  function createWakeTrail(skyU) {
    var ROWS = 90;
    var pos = new Float32Array(ROWS * 2 * 3);
    var uv = new Float32Array(ROWS * 2 * 2);
    var idx = [];
    for (var i = 0; i < ROWS; i++) {
      var v = i / (ROWS - 1);
      var x = -24.5 - v * 165.0;
      var halfW = 4.5 + v * 21.0;
      pos[(i * 2) * 3 + 0] = x;     pos[(i * 2) * 3 + 1] = 0.06; pos[(i * 2) * 3 + 2] = -halfW;
      pos[(i * 2 + 1) * 3 + 0] = x; pos[(i * 2 + 1) * 3 + 1] = 0.06; pos[(i * 2 + 1) * 3 + 2] = halfW;
      uv[(i * 2) * 2 + 0] = 0; uv[(i * 2) * 2 + 1] = v;
      uv[(i * 2 + 1) * 2 + 0] = 1; uv[(i * 2 + 1) * 2 + 1] = v;
      if (i < ROWS - 1) {
        var a = i * 2, b = a + 1, c = a + 2, d = a + 3;
        idx.push(a, b, c, b, d, c);
      }
    }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geo.setAttribute('uv', new THREE.BufferAttribute(uv, 2));
    geo.setIndex(idx);

    var WAKE_VERT = [
      'varying vec2 vUv; varying vec3 vWorld;',
      'uniform float uTime; uniform float uAmp; uniform vec2 uDrift; uniform vec4 uWave[5];',
      'void main(){',
      '  vUv = uv;',
      '  vec3 p = position;',
      '  vec2 xz = p.xz + uDrift;',
      '  float y = 0.07;',
      '  for (int i = 0; i < 5; i++) {',
      '    vec2 d = normalize(uWave[i].xy); float wl = uWave[i].z; float k = 6.28318 / wl;',
      '    float c = sqrt(9.81 / k); float f = k * (dot(d, xz) - c * uTime);',
      '    y += wl * 0.0125 * (uWave[i].w / 0.2) * uAmp * sin(f);',
      '  }',
      '  p.y = y;',
      '  vWorld = (modelMatrix * vec4(p, 1.0)).xyz;',
      '  gl_Position = projectionMatrix * viewMatrix * vec4(vWorld, 1.0);',
      '}'
    ].join('\n');

    var WAKE_FRAG = [
      NOISE_GLSL,
      'varying vec2 vUv; varying vec3 vWorld;',
      'uniform float uTime; uniform float uNight; uniform float uWake;',
      'void main(){',
      '  float u = vUv.x * 2.0 - 1.0; float v = vUv.y;',
      '  float edge1 = exp(-pow((abs(u) - 0.68) * 6.5, 2.0));',
      '  float propJet = exp(-pow((abs(u) - 0.24) * 5.2, 2.0));',
      '  float center = exp(-pow(u * 2.8, 2.0)) * 0.72;',
      '  float n = fbm(vWorld.xz * 0.42 + vec2(uTime * 1.4, 0.0));',
      '  float turb = smoothstep(0.28, 0.74, n);',
      '  float fade = (1.0 - smoothstep(0.0, 0.95, v)) * smoothstep(0.0, 0.04, v);',
      '  float a = (edge1 * 0.85 + propJet * 0.95 + center * turb) * fade * (0.45 + 0.55 * turb) * uWake;',
      '  vec3 c = mix(vec3(0.16, 0.52, 0.60), vec3(0.90, 0.96, 0.99), clamp(a * 1.35, 0.0, 1.0)) * (1.0 - 0.7 * uNight);',
      '  gl_FragColor = vec4(c, clamp(a * 0.75, 0.0, 0.85));',
      '  #include <tonemapping_fragment>',
      '  #include <encodings_fragment>',
      '}'
    ].join('\n');

    var mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, side: THREE.DoubleSide,
      uniforms: {
        uTime: { value: 0 }, uAmp: { value: 1 }, uDrift: { value: new THREE.Vector2() },
        uWave: { value: WAVES.map(function (w) { return new THREE.Vector4(w[0], w[1], w[2], w[3]); }) },
        uNight: skyU.uNight, uWake: { value: 1.0 }
      },
      vertexShader: WAKE_VERT,
      fragmentShader: WAKE_FRAG
    });
    var mesh = new THREE.Mesh(geo, mat);
    mesh.frustumCulled = false;
    mesh.renderOrder = 3;
    return { mesh: mesh, mat: mat };
  }

  // ================================================================== 5. Architectural Golden Gate Bridge & Sculpted Marin Headlands
  function buildSanFranciscoHorizon() {
    var grp = new THREE.Group();
    grp.name = 'sf-horizon';
    var rbox = SDV.rboxGeo;
    var intlOrange = SDV.std(new THREE.MeshStandardMaterial({ color: col(0xc03e28), roughness: 0.50, metalness: 0.28 }), 0.6);
    var concrete = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x8c887e), roughness: 0.85, metalness: 0.05 }), 0.3);
    var roadMat = SDV.std(new THREE.MeshStandardMaterial({ color: col(0x32363b), roughness: 0.85, metalness: 0.1 }), 0.3);
    var beaconMat = new THREE.MeshBasicMaterial({ color: 0xff3b30 });

    // 5a. Golden Gate Bridge with Stepped Art-Deco Towers, Concrete Pylon Fenders, Truss Deck & Catenary Cables
    var br = new THREE.Group();
    var towerGeos = [], fenderGeos = [];
    [-580, 580].forEach(function (tx) {
      // Elliptical Concrete Pylon Fender at Waterline
      var fnd = rbox(34, 16, 52, 6, 3, tx, 4, 0);
      fenderGeos.push(fnd);
      // 4 Stepped Art-Deco Tower Tiers per Leg (tapering upward from y = 12 to y = 215)
      [-12.5, 12.5].forEach(function (lz) {
        towerGeos.push(rbox(7.8, 62, 7.2, 0.5, 2, tx, 43, lz));
        towerGeos.push(rbox(6.6, 54, 6.2, 0.5, 2, tx, 100, lz));
        towerGeos.push(rbox(5.4, 52, 5.2, 0.4, 2, tx, 152, lz));
        towerGeos.push(rbox(4.2, 42, 4.2, 0.4, 2, tx, 198, lz));
        // Aviation red warning beacons on tower peaks
        var bcn = new THREE.Mesh(new THREE.SphereGeometry(1.4, 8, 6), beaconMat);
        bcn.position.set(tx, 221, lz);
        br.add(bcn);
      });
      // Art-Deco Horizontal Portal Struts with vertical fluting above & below roadway
      [28, 48, 86, 122, 158, 192, 212].forEach(function (py, idx) {
        var h = idx >= 2 ? 8.5 : 5.5;
        towerGeos.push(rbox(5.2, h, 26.0, 0.4, 2, tx, py, 0));
      });
    });
    // Stiffening Truss Roadway Deck + Approach Spans
    towerGeos.push(rbox(3600, 6.8, 26.5, 0.4, 2, 0, 62.0, 0));
    br.add(new THREE.Mesh(SDV.mergeGeos(fenderGeos), concrete));
    br.add(new THREE.Mesh(SDV.mergeGeos(towerGeos), intlOrange));
    var rd = new THREE.Mesh(new THREE.BoxGeometry(3600, 0.8, 22.0), roadMat);
    rd.position.set(0, 65.8, 0);
    br.add(rd);

    function cableY(x) {
      var ax = Math.abs(x);
      if (ax <= 580) return 68 + (216 - 68) * Math.pow(x / 580, 2);
      var u = Math.min(1, (ax - 580) / 360);
      return 216 + (68 - 216) * u - 14 * Math.sin(Math.PI * u);
    }
    var supLines = [];
    [-12.5, 12.5].forEach(function (cz) {
      var pts = [];
      for (var x = -940; x <= 940; x += 18) pts.push(new THREE.Vector3(x, cableY(x), cz));
      var curve = new THREE.CatmullRomCurve3(pts);
      br.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 200, 1.15, 8, false), intlOrange));
      for (var sx = -920; sx <= 920; sx += 16) {
        supLines.push(sx, cableY(sx), cz, sx, 65.4, cz);
      }
    });
    var lg = new THREE.BufferGeometry();
    lg.setAttribute('position', new THREE.Float32BufferAttribute(supLines, 3));
    br.add(new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ color: col(0xc84830), transparent: true, opacity: 0.55 })));
    br.position.set(60, 0, -1450);
    grp.add(br);

    // 5b. Smooth Sculpted Marin Headlands & Angel Island Silhouette
    function buildHeadland(xMin, xMax, zFront, depth, maxH, seed) {
      var NX = 84, NZ = 14;
      var pos = [], cols = [], idx = [];
      var cLow = col(0x283428), cMid = col(0x525a3c), cHigh = col(0x7a7652), tmp = new THREE.Color();
      for (var i = 0; i <= NX; i++) {
        var u = i / NX;
        var x = xMin + (xMax - xMin) * u;
        var edgeEnv = Math.sin(Math.PI * u);
        edgeEnv = Math.pow(Math.max(0, edgeEnv), 0.65);
        for (var j = 0; j <= NZ; j++) {
          var v = j / NZ;
          var z = zFront - v * depth;
          var ridgeProfile = Math.sin(Math.PI * Math.pow(v, 0.72));
          var n1 = Math.sin(u * 9.5 + seed) * 0.45 + Math.cos(u * 21.0 - seed * 1.7 + v * 3.2) * 0.25 + Math.sin(u * 43.0 + v * 7.5) * 0.10;
          var h = Math.max(-4.0, edgeEnv * ridgeProfile * maxH * (0.62 + 0.38 * n1) - 3.0);
          pos.push(x, h, z);
          var ht = SDV.clamp((h + 3) / Math.max(maxH * 0.85, 1), 0, 1);
          if (ht < 0.5) tmp.copy(cLow).lerp(cMid, ht * 2);
          else tmp.copy(cMid).lerp(cHigh, (ht - 0.5) * 2);
          cols.push(tmp.r, tmp.g, tmp.b);
        }
      }
      for (var i0 = 0; i0 < NX; i0++) {
        for (var j0 = 0; j0 < NZ; j0++) {
          var a = i0 * (NZ + 1) + j0, b = a + 1, c = a + (NZ + 1), d = c + 1;
          idx.push(a, b, d, a, d, c);
        }
      }
      var g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
      g.setIndex(idx);
      g.computeVertexNormals();
      return g;
    }
    var hillMat = SDV.std(new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.96, metalness: 0.0, side: THREE.DoubleSide }), 0.25);
    grp.add(new THREE.Mesh(buildHeadland(-2600, -480, -1380, 520, 210, 1.4), hillMat));
    grp.add(new THREE.Mesh(buildHeadland(540, 2500, -1420, 480, 145, 4.2), hillMat));
    grp.add(new THREE.Mesh(buildHeadland(-1800, 1800, -1950, 680, 290, 7.8), hillMat));
    return grp;
  }

  // ================================================================== 6. Environment Factory
  SDV.createEnvironment = function (scene, renderer) {
    var env = {};
    var skyU = {
      uHorizon: { value: new THREE.Color() },
      uZenith: { value: new THREE.Color() },
      uSunColor: { value: new THREE.Color() },
      uSunTint: { value: new THREE.Color() },
      uSunDir: { value: new THREE.Vector3(0.8, 0.24, -0.42) },
      uDisc: { value: 22 },
      uNight: { value: 0 }
    };
    env.skyU = skyU;

    function makePal(p) {
      return {
        horizon: new THREE.Color().fromArray(p.horizon),
        zenith: new THREE.Color().fromArray(p.zenith),
        sunColor: new THREE.Color().fromArray(p.sunColor),
        sunTint: new THREE.Color().fromArray(p.sunTint),
        fog: new THREE.Color().fromArray(p.fog),
        waterDeep: new THREE.Color().fromArray(p.waterDeep),
        waterShallow: new THREE.Color().fromArray(p.waterShallow),
        hemiSky: new THREE.Color().fromArray(p.hemiSky),
        hemiGround: new THREE.Color().fromArray(p.hemiGround),
        sunIntensity: p.sunIntensity,
        fillIntensity: p.fillIntensity,
        envIntensity: p.envIntensity,
        clouds: p.clouds,
        disc: p.disc,
        elevDeg: p.elevDeg,
        night: p.night,
        exposure: p.exposure,
        fogDensity: p.fogDensity
      };
    }

    var tgt = {
      day: makePal(PALETTES.day),
      golden: makePal(PALETTES.golden),
      night: makePal(PALETTES.night)
    };
    var cur = makePal(PALETTES.day);
    var target = tgt.day;
    env.mode = 'day';

    function sunDirFor(elevDeg) {
      var e = THREE.MathUtils.degToRad(elevDeg);
      return new THREE.Vector3(Math.cos(e) * SUN_AZIMUTH.x, Math.sin(e), Math.cos(e) * SUN_AZIMUTH.y).normalize();
    }

    // 1. Sky Dome
    var domeMat = new THREE.ShaderMaterial({
      side: THREE.BackSide,
      depthWrite: false,
      depthTest: false,
      fog: false,
      uniforms: {
        uTime: { value: 0 },
        uClouds: { value: 0.55 },
        uHorizon: skyU.uHorizon,
        uZenith: skyU.uZenith,
        uSunColor: skyU.uSunColor,
        uSunTint: skyU.uSunTint,
        uSunDir: skyU.uSunDir,
        uDisc: skyU.uDisc,
        uNight: skyU.uNight
      },
      vertexShader: DOME_VERT,
      fragmentShader: DOME_FRAG
    });
    var dome = new THREE.Mesh(new THREE.SphereGeometry(1, 48, 24), domeMat);
    dome.scale.setScalar(5000);
    dome.renderOrder = -10;
    dome.frustumCulled = false;
    scene.add(dome);
    env.dome = dome;

    // 2. Lights & 4K Sun Shadow Camera
    var sun = new THREE.DirectionalLight(0xffffff, 3.4);
    sun.castShadow = true;
    sun.shadow.mapSize.set(4096, 4096);
    var sc = sun.shadow.camera;
    sc.left = -36; sc.right = 36; sc.top = 36; sc.bottom = -36; sc.near = 10; sc.far = 260;
    sc.updateProjectionMatrix();
    sun.shadow.bias = -0.0003;
    sun.shadow.normalBias = 0.025;
    scene.add(sun, sun.target);

    var hemi = new THREE.HemisphereLight(0xcfe3ff, 0x0b2230, 0.45);
    scene.add(hemi);
    env.sun = sun; env.hemi = hemi;

    scene.fog = new THREE.FogExp2(0x7594b2, 0.00045);

    // 3. Ocean Surface (Exact Port of OceanSurface.ts with dense radial wave mesh)
    var uWave = WAVES.map(function (w) { return new THREE.Vector4(w[0], w[1], w[2], w[3]); });
    var oceanMat = new THREE.ShaderMaterial({
      transparent: true,
      depthWrite: true,
      side: THREE.DoubleSide,
      uniforms: {
        uTime: { value: 0 },
        uAmp: { value: 1.0 },
        uDrift: { value: new THREE.Vector2() },
        uWave: { value: uWave },
        uFoamGain: { value: 1.85 },
        uCamPos: { value: new THREE.Vector3() },
        uOpacity: { value: 0.94 },
        uFogColor: { value: cur.fog.clone() },
        uFogDensity: { value: 0.00045 },
        uDeep: { value: cur.waterDeep.clone() },
        uShallow: { value: cur.waterShallow.clone() },
        uRough: { value: 0.85 },
        uWake: { value: 1.0 },
        uHorizon: skyU.uHorizon,
        uZenith: skyU.uZenith,
        uSunColor: skyU.uSunColor,
        uSunTint: skyU.uSunTint,
        uSunDir: skyU.uSunDir,
        uDisc: skyU.uDisc,
        uNight: skyU.uNight
      },
      vertexShader: OCEAN_VERT,
      fragmentShader: OCEAN_FRAG
    });
    var ocean = new THREE.Mesh(createOceanGeometry(128, 168, 2600), oceanMat);
    ocean.frustumCulled = false;
    ocean.renderOrder = 1;
    scene.add(ocean);
    env.ocean = ocean;
    env.oceanMat = oceanMat;
    env.drift = oceanMat.uniforms.uDrift.value;

    // 4. Dynamic Stern Wake Ribbon
    var wake = createWakeTrail(skyU);
    scene.add(wake.mesh);
    env.wake = wake;

    // 5. San Francisco Bay Horizon (Golden Gate Bridge + Marin Headlands)
    try {
      env.horizon = buildSanFranciscoHorizon();
      scene.add(env.horizon);
    } catch (e) { console.warn('horizon failed', e); }

    // 6. PMREM Environment Reflection Maps
    var envTex = {};
    try {
      var pm = new THREE.PMREMGenerator(renderer);
      Object.keys(tgt).forEach(function (k) {
        var p = tgt[k];
        var u = {
          uHorizon: { value: p.horizon.clone() },
          uZenith: { value: p.zenith.clone() },
          uSunColor: { value: p.sunColor.clone() },
          uSunTint: { value: p.sunTint.clone() },
          uSunDir: { value: sunDirFor(p.elevDeg) },
          uDisc: { value: p.disc }
        };
        var m = new THREE.ShaderMaterial({
          side: THREE.BackSide,
          depthWrite: false,
          uniforms: u,
          vertexShader: DOME_VERT,
          fragmentShader: 'varying vec3 vDir;\n' + SKY_GLSL + '\nvoid main(){ vec3 d = normalize(vDir); vec3 c = d.y < 0.0 ? mix(uHorizon * 0.7, uHorizon * 0.25, clamp(-d.y * 3.0, 0.0, 1.0)) : skyColor(d); gl_FragColor = vec4(c, 1.0); }'
        });
        var mesh = new THREE.Mesh(new THREE.SphereGeometry(50, 32, 16), m);
        var tmpScene = new THREE.Scene();
        tmpScene.add(mesh);
        envTex[k] = pm.fromScene(tmpScene, 0.02, 0.1, 200).texture;
        mesh.geometry.dispose();
        m.dispose();
      });
      pm.dispose();
    } catch (e) { console.warn('PMREM failed', e); }
    scene.environment = envTex.day || null;

    env.setMode = function (name, instant) {
      if (!tgt[name]) return;
      env.mode = name;
      target = tgt[name];
      if (envTex[name]) scene.environment = envTex[name];
      if (instant) apply(1);
    };

    var targetOpacity = 0.94;
    env.setUnderwaterView = function (under) {
      targetOpacity = under ? 0.58 : 0.94;
      oceanMat.depthWrite = !under;
    };

    function apply(k) {
      cur.horizon.lerp(target.horizon, k);
      cur.zenith.lerp(target.zenith, k);
      cur.sunColor.lerp(target.sunColor, k);
      cur.sunTint.lerp(target.sunTint, k);
      cur.fog.lerp(target.fog, k);
      cur.waterDeep.lerp(target.waterDeep, k);
      cur.waterShallow.lerp(target.waterShallow, k);
      cur.hemiSky.lerp(target.hemiSky, k);
      cur.hemiGround.lerp(target.hemiGround, k);
      ['sunIntensity', 'fillIntensity', 'envIntensity', 'clouds', 'disc', 'elevDeg', 'night', 'exposure', 'fogDensity'].forEach(function (key) {
        cur[key] += (target[key] - cur[key]) * k;
      });
      skyU.uHorizon.value.copy(cur.horizon);
      skyU.uZenith.value.copy(cur.zenith);
      skyU.uSunColor.value.copy(cur.sunColor);
      skyU.uSunTint.value.copy(cur.sunTint);
      skyU.uDisc.value = cur.disc;
      skyU.uNight.value = cur.night;
      domeMat.uniforms.uClouds.value = cur.clouds;

      var dir = sunDirFor(cur.elevDeg);
      skyU.uSunDir.value.copy(dir);
      sun.color.copy(cur.sunColor);
      sun.intensity = cur.sunIntensity;
      sun.position.copy(dir).multiplyScalar(110);
      sun.target.position.set(0, 0, 0);

      hemi.color.copy(cur.hemiSky);
      hemi.groundColor.copy(cur.hemiGround);
      hemi.intensity = cur.fillIntensity;

      scene.fog.color.copy(cur.fog);
      scene.fog.density = cur.fogDensity;

      var u = oceanMat.uniforms;
      u.uFogColor.value.copy(cur.fog);
      u.uFogDensity.value = cur.fogDensity;
      u.uDeep.value.copy(cur.waterDeep);
      u.uShallow.value.copy(cur.waterShallow);
      u.uOpacity.value += (targetOpacity - u.uOpacity.value) * k;

      renderer.toneMappingExposure = cur.exposure;
      for (var i = 0; i < SDV.stdMats.length; i++) {
        var mm = SDV.stdMats[i];
        mm.envMapIntensity = cur.envIntensity * (mm.userData.envBase === undefined ? 1 : mm.userData.envBase);
      }
    }
    apply(1);

    /** p: {dt, t, camPos, speedKn, seaState} */
    env.update = function (p) {
      apply(1 - Math.exp(-p.dt * 2.5));
      dome.position.copy(p.camPos);
      domeMat.uniforms.uTime.value = p.t;

      var u = oceanMat.uniforms;
      u.uTime.value = p.t;
      u.uCamPos.value.copy(p.camPos);
      u.uAmp.value = 0.70 + p.seaState * 0.34;
      u.uFoamGain.value = 1.65 + p.seaState * 0.25;
      u.uRough.value = 0.65 + p.seaState * 0.18;
      u.uWake.value = SDV.clamp(p.speedKn / 8.0, 0.45, 1.45);
      env.drift.x += p.dt * p.speedKn * 0.514;

      wake.mat.uniforms.uTime.value = p.t;
      wake.mat.uniforms.uAmp.value = u.uAmp.value;
      wake.mat.uniforms.uDrift.value.copy(env.drift);
      wake.mat.uniforms.uWake.value = u.uWake.value;
    };
    env.amp = function () { return oceanMat.uniforms.uAmp.value; };
    env.night = function () { return cur.night; };
    return env;
  };
})();

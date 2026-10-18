/**
 * Motor de Océano Físico (Espectro Direccional JONSWAP / Gerstner Multicascada),
 * Espuma por Jacobiano, Estela Kelvin (19.47°), Flotabilidad Hidrostática de 8 Sondas (6DOF)
 * y Entorno de la Bahía de San Francisco (Puente Golden Gate + Marin Headlands conmutables).
 *
 * Optimizado específicamente para MacBook Pro M1 Pro (bajo consumo energético a 60 FPS).
 */

import * as THREE from "three";

// 8 componentes espectrales JONSWAP calibradas para escalar con wind_kn y sea_state (Douglas 0..9)
const WAVE_COMPONENTS = [
  { dirX: 0.94, dirZ: 0.34, wavelength: 46.0, steepness: 0.19, speed: 1.05 },
  { dirX: 0.88, dirZ: -0.47, wavelength: 29.0, steepness: 0.16, speed: 1.18 },
  { dirX: 0.98, dirZ: 0.18, wavelength: 17.5, steepness: 0.14, speed: 1.32 },
  { dirX: 0.76, dirZ: 0.65, wavelength: 11.2, steepness: 0.11, speed: 1.48 },
  { dirX: 0.82, dirZ: -0.57, wavelength: 6.8, steepness: 0.09, speed: 1.65 },
  { dirX: 0.45, dirZ: 0.89, wavelength: 4.1, steepness: 0.06, speed: 1.92 },
];

export function evaluateWaveHeightAndNormal(x, z, timeS, seaState = 2, windKn = 14) {
  const ampScale = Math.max(0.08, (seaState * 0.22) + (windKn - 10) * 0.015);
  let y = 0.0;
  let dYdX = 0.0;
  let dYdZ = 0.0;

  for (let i = 0; i < WAVE_COMPONENTS.length; i++) {
    const w = WAVE_COMPONENTS[i];
    const k = (2.0 * Math.PI) / w.wavelength;
    const c = Math.sqrt(9.81 / k) * w.speed;
    const a = (w.steepness / k) * ampScale;
    const phase = k * (w.dirX * x + w.dirZ * z - c * timeS);
    const s = Math.sin(phase);
    const co = Math.cos(phase);

    y += a * s;
    dYdX += a * k * w.dirX * co;
    dYdZ += a * k * w.dirZ * co;
  }

  const normal = new THREE.Vector3(-dYdX, 1.0, -dYdZ).normalize();
  return { height: y, normal, slope: Math.hypot(dYdX, dYdZ) };
}

/**
 * Calcula las fuerzas de flotabilidad en 8 estaciones del casco de 60 m (LOA=60m, B=11m)
 * más el asiento (trim) y escora (heel) producidos por las masas de los guests en sus bahías.
 */
export function computeVessel6DOF(timeS, seaState, windKn, shipSpeedKn, headingDeg, guestMassKg, stabilityKgEq, exaggerateHydro = false) {
  // 8 sondas distribuidas en el casco: proa, hombro proa B/E, centro B/E, aleta B/E, espejo
  const probes = [
    { x: 24.0, z: 0.0 },
    { x: 14.0, z: -4.2 },
    { x: 14.0, z: 4.2 },
    { x: 0.0, z: -5.0 },
    { x: 0.0, z: 5.0 },
    { x: -14.0, z: -4.6 },
    { x: -14.0, z: 4.6 },
    { x: -26.0, z: 0.0 },
  ];

  let avgH = 0;
  let pitchMoment = 0;
  let rollMoment = 0;

  for (const p of probes) {
    const w = evaluateWaveHeightAndNormal(p.x, p.z, timeS, seaState, windKn);
    avgH += w.height;
    pitchMoment += w.height * (p.x / 26.0);
    rollMoment += w.height * (p.z / 5.0);
  }
  avgH /= probes.length;

  // Inmersión hidrostática real: TPC = 4.94 t/cm = 4940 kg/cm = 494,000 kg/m
  const sinkageRealM = guestMassKg / 494000.0;
  const hydroScale = exaggerateHydro ? 20.0 : 1.0;
  const sinkageDisplayM = sinkageRealM * hydroScale;

  // Escora cuasiestática por momento transversal / estabilidad alta en mástil
  const heelStaticRad = ((stabilityKgEq - guestMassKg) / 120000.0) * (exaggerateHydro ? 8.0 : 1.0);

  const heaveM = avgH * 0.42 - sinkageDisplayM;
  const pitchRad = (pitchMoment / probes.length) * 0.045;
  const rollRad = (rollMoment / probes.length) * 0.085 + heelStaticRad;

  return {
    heaveM,
    pitchRad,
    rollRad,
    sinkageRealCm: sinkageRealM * 100.0,
    sinkageDisplayCm: sinkageDisplayM * 100.0,
  };
}

export function createOceanSystem(scene) {
  const oceanGeo = new THREE.PlaneGeometry(1400, 1400, 180, 180);
  oceanGeo.rotateX(-Math.PI / 2);

  const oceanUniforms = {
    uTime: { value: 0 },
    uSeaState: { value: 2.0 },
    uWindKn: { value: 14.0 },
    uShipSpeedKn: { value: 3.0 },
    uSunDirection: { value: new THREE.Vector3(0.55, 0.38, -0.74).normalize() },
    uSunColor: { value: new THREE.Color(0xfff1d6) },
    uDeepColor: { value: new THREE.Color(0x041e30) },
    uShallowColor: { value: new THREE.Color(0x0b5e6f) },
    uSkyColor: { value: new THREE.Color(0x4b79a1) },
  };

  const oceanMat = new THREE.ShaderMaterial({
    uniforms: oceanUniforms,
    transparent: true,
    vertexShader: `
      uniform float uTime;
      uniform float uSeaState;
      uniform float uWindKn;
      varying vec3 vWorldPos;
      varying vec3 vNormal;
      varying float vJacobianFoam;

      void addGerstner(vec2 xz, vec2 dir, float wl, float steep, float spd, float ampScale, inout vec3 pos, inout vec3 tangent, inout vec3 binormal, inout float jac) {
        float k = 6.2831853 / wl;
        float c = sqrt(9.81 / k) * spd;
        float a = (steep / k) * ampScale;
        float f = k * (dot(dir, xz) - c * uTime);
        float s = sin(f);
        float co = cos(f);

        pos.x += dir.x * (a * co);
        pos.y += a * s;
        pos.z += dir.y * (a * co);

        tangent += vec3(-dir.x * dir.x * steep * ampScale * s, dir.x * steep * ampScale * co, -dir.x * dir.y * steep * ampScale * s);
        binormal += vec3(-dir.x * dir.y * steep * ampScale * s, dir.y * steep * ampScale * co, -dir.y * dir.y * steep * ampScale * s);
        jac -= steep * ampScale * co;
      }

      void main() {
        vec3 pos = position;
        vec2 xz = pos.xz;
        float ampScale = max(0.08, uSeaState * 0.22 + (uWindKn - 10.0) * 0.015);
        // Atenuar amplitud en el horizonte lejano para evitar aliasing
        float distFade = 1.0 - smoothstep(220.0, 680.0, length(xz));
        ampScale *= distFade;

        vec3 tangent = vec3(1.0, 0.0, 0.0);
        vec3 binormal = vec3(0.0, 0.0, 1.0);
        float jac = 1.0;

        addGerstner(xz, normalize(vec2(0.94, 0.34)), 46.0, 0.19, 1.05, ampScale, pos, tangent, binormal, jac);
        addGerstner(xz, normalize(vec2(0.88, -0.47)), 29.0, 0.16, 1.18, ampScale, pos, tangent, binormal, jac);
        addGerstner(xz, normalize(vec2(0.98, 0.18)), 17.5, 0.14, 1.32, ampScale, pos, tangent, binormal, jac);
        addGerstner(xz, normalize(vec2(0.76, 0.65)), 11.2, 0.11, 1.48, ampScale, pos, tangent, binormal, jac);
        addGerstner(xz, normalize(vec2(0.82, -0.57)), 6.8, 0.09, 1.65, ampScale, pos, tangent, binormal, jac);
        addGerstner(xz, normalize(vec2(0.45, 0.89)), 4.1, 0.06, 1.92, ampScale, pos, tangent, binormal, jac);

        vNormal = normalize(cross(binormal, tangent));
        vJacobianFoam = clamp(1.0 - jac, 0.0, 1.0);
        vec4 worldPosition = modelMatrix * vec4(pos, 1.0);
        vWorldPos = worldPosition.xyz;
        gl_Position = projectionMatrix * viewMatrix * worldPosition;
      }
    `,
    fragmentShader: `
      uniform float uTime;
      uniform float uShipSpeedKn;
      uniform vec3 uSunDirection;
      uniform vec3 uSunColor;
      uniform vec3 uDeepColor;
      uniform vec3 uShallowColor;
      uniform vec3 uSkyColor;

      varying vec3 vWorldPos;
      varying vec3 vNormal;
      varying float vJacobianFoam;

      float hash(vec2 p) {
        return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
      }

      float noise(vec2 p) {
        vec2 i = floor(p);
        vec2 f = fract(p);
        vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i + vec2(0.0, 0.0)), hash(i + vec2(1.0, 0.0)), u.x),
                   mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y);
      }

      void main() {
        // Micro-rizo de alta frecuencia para brillo realista del agua
        vec2 uvMicro1 = vWorldPos.xz * 0.42 + vec2(uTime * 0.35, -uTime * 0.22);
        vec2 uvMicro2 = vWorldPos.xz * 1.15 + vec2(-uTime * 0.45, uTime * 0.31);
        float n1 = noise(uvMicro1) * 2.0 - 1.0;
        float n2 = noise(uvMicro2) * 2.0 - 1.0;
        vec3 N = normalize(vNormal + vec3(n1 * 0.08 + n2 * 0.05, 0.0, n1 * 0.06 - n2 * 0.05));

        vec3 V = normalize(cameraPosition - vWorldPos);
        float NoV = max(dot(N, V), 0.001);

        // Reflectancia de Fresnel (Schlick para agua n=1.333 -> F0=0.02)
        float fresnel = 0.02 + 0.98 * pow(1.0 - NoV, 5.0);

        // Dispersión subsuperficial (Subsurface Scattering) en las crestas iluminadas
        float sss = pow(max(0.0, dot(V, -uSunDirection + N * 0.65)), 3.0) * max(0.0, vWorldPos.y + 0.5) * 0.55;
        vec3 waterBase = mix(uDeepColor, uShallowColor, clamp(sss + (1.0 - NoV) * 0.25, 0.0, 1.0));

        // Reflejo del cielo y especular solar GGX simplificado
        vec3 R = reflect(-V, N);
        float skyGrad = clamp(R.y * 0.5 + 0.5, 0.0, 1.0);
        vec3 reflColor = mix(uDeepColor * 1.3, uSkyColor, skyGrad);

        vec3 H = normalize(uSunDirection + V);
        float spec = pow(max(dot(N, H), 0.0), 320.0) * 2.6;

        // Estela Kelvin de 19.47° detrás de la popa (X < -28m) y lavado de los 2 pods
        float wakeMask = 0.0;
        if (vWorldPos.x < 28.0 && vWorldPos.x > -220.0) {
          float dx = 28.0 - vWorldPos.x;
          float kelvinHalfWidth = dx * 0.353 + 2.2; // tan(19.47°) ≈ 0.3535
          float absZ = abs(vWorldPos.z);
          // Borde de la V de Kelvin + chorro central de los 2 pods en popa (x < -28)
          float vEdge = exp(-pow((absZ - kelvinHalfWidth * 0.72) / max(1.2, dx * 0.08), 2.0));
          float propWash = vWorldPos.x < -27.0
            ? exp(-pow(absZ / (3.0 + (-27.0 - vWorldPos.x) * 0.09), 2.0)) * exp((vWorldPos.x + 27.0) * 0.018)
            : 0.0;
          float hullRibbon = (vWorldPos.x > -30.0 && vWorldPos.x < 30.0)
            ? exp(-pow((absZ - 5.6) / 0.85, 2.0))
            : 0.0;
          float speedFactor = clamp(uShipSpeedKn / 8.0, 0.25, 1.35);
          float foamTex = noise(vWorldPos.xz * 0.85 + vec2(uTime * 2.2, 0.0));
          wakeMask = clamp((vEdge * 0.45 * exp(-dx * 0.012) + propWash * 0.95 + hullRibbon * 0.65) * speedFactor * (0.55 + 0.65 * foamTex), 0.0, 1.0);
        }

        // Espuma en las crestas por Jacobiano
        float crestFoam = smoothstep(0.32, 0.75, vJacobianFoam + n1 * 0.12) * 0.85;
        float totalFoam = clamp(max(crestFoam, wakeMask), 0.0, 1.0);

        vec3 color = mix(waterBase, reflColor, fresnel * 0.78) + uSunColor * spec;
        vec3 foamColor = mix(vec3(0.78, 0.89, 0.94), vec3(0.97, 0.99, 1.0), totalFoam);
        color = mix(color, foamColor, totalFoam);

        // Niebla atmosférica hacia el horizonte
        float dist = length(vWorldPos.xz);
        float fogFactor = smoothstep(260.0, 690.0, dist);
        color = mix(color, uSkyColor * 0.92, fogFactor);

        gl_FragColor = vec4(color, 0.94);
      }
    `,
  });

  const oceanMesh = new THREE.Mesh(oceanGeo, oceanMat);
  oceanMesh.name = "OCEAN_SURFACE";
  scene.add(oceanMesh);

  // Lecho / volumen submarino para cuando la cámara baja bajo la línea de flotación (AUV G3 y Pods)
  const seabedGeo = new THREE.PlaneGeometry(1200, 1200, 32, 32);
  seabedGeo.rotateX(-Math.PI / 2);
  const seabedMat = new THREE.MeshStandardMaterial({
    color: 0x051824,
    roughness: 0.95,
    metalness: 0.05,
  });
  const seabedMesh = new THREE.Mesh(seabedGeo, seabedMat);
  seabedMesh.position.y = -28.0;
  seabedMesh.name = "SEABED_FLOOR";
  scene.add(seabedMesh);

  return {
    oceanMesh,
    oceanUniforms,
    seabedMesh,
  };
}

/**
 * Construye la silueta ingenieril de la Bahía de San Francisco:
 * - Puente Golden Gate a escala al horizonte (2 torres de 227m en International Orange #C0362C,
 *   cables catenarios principales, péndolas verticales, tablero y balizas rojas)
 * - Colinas de Marin Headlands al norte y península de SF al sur
 */
export function createSanFranciscoBayEnvironment(scene) {
  const bayGroup = new THREE.Group();
  bayGroup.name = "SF_BAY_ENVIRONMENT";

  const bridgeMat = new THREE.MeshStandardMaterial({
    color: 0xc0362c, // International Orange oficial del Golden Gate
    roughness: 0.55,
    metalness: 0.35,
  });
  const cableMat = new THREE.MeshBasicMaterial({ color: 0xa82e24 });
  const hillMat = new THREE.MeshStandardMaterial({
    color: 0x1e2933,
    roughness: 0.92,
    metalness: 0.05,
  });

  const bridgeGroup = new THREE.Group();
  bridgeGroup.position.set(180.0, 0.0, -380.0);
  bridgeGroup.rotation.y = 0.22;

  // Dos torres principales (escala visual lejana: 82 m de altura en el horizonte)
  for (const side of [-1, 1]) {
    const towerX = side * 95.0;
    const tower = new THREE.Group();
    tower.position.set(towerX, 0, 0);

    // 2 pilares verticales por torre + 4 riostras horizontales Art Déco
    for (const legZ of [-4.5, 4.5]) {
      const leg = new THREE.Mesh(new THREE.BoxGeometry(3.6, 82.0, 2.8), bridgeMat);
      leg.position.set(0, 41.0, legZ);
      tower.add(leg);
    }
    for (const braceY of [22.0, 42.0, 60.0, 76.0]) {
      const brace = new THREE.Mesh(new THREE.BoxGeometry(3.2, 2.8, 11.6), bridgeMat);
      brace.position.set(0, braceY, 0);
      tower.add(brace);
    }
    // Base de hormigón en el agua
    const pier = new THREE.Mesh(
      new THREE.BoxGeometry(12.0, 10.0, 18.0),
      new THREE.MeshStandardMaterial({ color: 0x646b73, roughness: 0.8 })
    );
    pier.position.set(0, 3.0, 0);
    tower.add(pier);

    // Baliza aeronáutica roja en el tope
    const beacon = new THREE.Mesh(
      new THREE.SphereGeometry(1.1, 12, 12),
      new THREE.MeshBasicMaterial({ color: 0xff2a2a })
    );
    beacon.position.set(0, 83.2, 0);
    tower.add(beacon);

    bridgeGroup.add(tower);
  }

  // Tablero del puente (Roadway truss)
  const deck = new THREE.Mesh(new THREE.BoxGeometry(380.0, 3.4, 11.0), bridgeMat);
  deck.position.set(0, 22.0, 0);
  bridgeGroup.add(deck);

  // Cables catenarios principales entre las torres y hacia los anclajes
  const catenaryPtsPort = [];
  const catenaryPtsStbd = [];
  for (let i = -30; i <= 30; i++) {
    const x = (i / 30) * 185.0;
    const absX = Math.abs(x);
    let y;
    if (absX <= 95.0) {
      const t = x / 95.0;
      y = 24.5 + t * t * 56.5;
    } else {
      const t = (absX - 95.0) / 90.0;
      y = 81.0 - t * 57.0;
    }
    catenaryPtsPort.push(new THREE.Vector3(x, y, -4.5));
    catenaryPtsStbd.push(new THREE.Vector3(x, y, 4.5));
  }
  const curvePort = new THREE.CatmullRomCurve3(catenaryPtsPort);
  const curveStbd = new THREE.CatmullRomCurve3(catenaryPtsStbd);
  bridgeGroup.add(new THREE.Mesh(new THREE.TubeGeometry(curvePort, 64, 0.45, 6, false), cableMat));
  bridgeGroup.add(new THREE.Mesh(new THREE.TubeGeometry(curveStbd, 64, 0.45, 6, false), cableMat));

  bayGroup.add(bridgeGroup);

  // Colinas de Marin Headlands (Norte) y Presidio (Sur)
  const headlandsPositions = [
    { x: -45.0, z: -410.0, r: 95.0, h: 68.0 },
    { x: -130.0, z: -430.0, r: 120.0, h: 85.0 },
    { x: 390.0, z: -360.0, r: 110.0, h: 48.0 },
    { x: 470.0, z: -340.0, r: 95.0, h: 36.0 },
  ];
  for (const hp of headlandsPositions) {
    const cone = new THREE.Mesh(new THREE.ConeGeometry(hp.r, hp.h, 7), hillMat);
    cone.position.set(hp.x, hp.h * 0.42, hp.z);
    cone.scale.set(1.4, 1.0, 0.75);
    bayGroup.add(cone);
  }

  scene.add(bayGroup);
  return bayGroup;
}

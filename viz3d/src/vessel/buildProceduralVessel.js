/**
 * Constructor Procedural de Alta Fidelidad a Escala 1:1 (LOA = 60.0 m, Manga = 11.0 m, Calado = 3.2 m)
 * del Software Defined Vessel (Fincantieri Maritime Dual-Use Hackathon).
 *
 * Reemplaza por completo las primitivas rectangulares por:
 * - Casco lofted multicapa (obra viva antiincrustante, bota de flotación, obra muerta con codillo,
 *   abanico de proa, bulbo hidrodinámico con bow-thruster, y espejo con rampa articulada)
 * - 2x Pods azimutales en popa (conforme al plano "PROPULSION 2x POD") con hélices giratorias
 * - Superestructura escalonada con puente de mando 360°, ventanas inclinadas, alerones y luces COLREG
 * - Nicho central babor/estribor con RHIB en pescantes + módulo inferior (idéntico al plano)
 * - Grúa articulada en popa de la superestructura, chimenea doble con penachos de escape individuales
 * - Mástil integrado con mast_1 (Radar X-band), mast_2 (Radomo SATCOM) y mast_3 (Slot libre guest)
 * - Modo Rayos X (X-Ray Cutaway): DG1/DG2/Harbour gensets, LINE_A_FWD, LINE_B_AFT, Chilled-Water Loop
 *   ISO 14726, ROV Hangar de proa y Compute Rack de 4 nodos
 * - Actores 3D detallados para G1, G2, G3 (Cradle + AUV submarino), G4 (Hangar + VTOL SAR en vuelo/toma),
 *   G5 (Contenedor Edge + Antena en mast_3 o hangar_roof + Haz SATCOM) y G6 (Skid eDNA + Ghost Admission).
 */

import * as THREE from "three";

/**
 * Genera una textura procedural Canvas de alta resolución para las marcas normativas
 * de la cubierta de vuelo de 12x8m (CAP 437 / NATO STANAG: círculo de toma, H, líneas límite y antideslizante).
 */
function createFlightDeckTexture() {
  const canvas = document.createElement("canvas");
  canvas.width = 1024;
  canvas.height = 682;
  const ctx = canvas.getContext("2d");

  // Base antideslizante gris oscuro marino
  ctx.fillStyle = "#2b3137";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Grano fino antideslizante
  ctx.fillStyle = "rgba(255,255,255,0.025)";
  for (let i = 0; i < 6000; i++) {
    ctx.fillRect(Math.random() * canvas.width, Math.random() * canvas.height, 2, 2);
  }

  // Borde perimetral de seguridad blanco discontinuo
  ctx.strokeStyle = "#e2e8f0";
  ctx.lineWidth = 10;
  ctx.setLineDash([28, 18]);
  ctx.strokeRect(24, 24, canvas.width - 48, canvas.height - 48);
  ctx.setLineDash([]);

  // Franja de exclusión de rampa de popa (amarillo/negro)
  ctx.strokeStyle = "#f59e0b";
  ctx.lineWidth = 8;
  ctx.beginPath();
  ctx.moveTo(90, 24);
  ctx.lineTo(90, canvas.height - 24);
  ctx.stroke();

  // Círculo de toma TD/PM amarillo (CAP 437)
  const cx = canvas.width * 0.54;
  const cy = canvas.height * 0.5;
  ctx.strokeStyle = "#eab308";
  ctx.lineWidth = 14;
  ctx.beginPath();
  ctx.arc(cx, cy, 195, 0, Math.PI * 2);
  ctx.stroke();

  // "H" blanca central para VTOL G4
  ctx.fillStyle = "#f8fafc";
  const hw = 90;
  const hh = 130;
  const bar = 22;
  ctx.fillRect(cx - hw / 2, cy - hh / 2, bar, hh);
  ctx.fillRect(cx + hw / 2 - bar, cy - hh / 2, bar, hh);
  ctx.fillRect(cx - hw / 2, cy - bar / 2, hw, bar);

  // Rótulo de masa máxima de cubierta (VTOL 150 kg / FLIGHT DECK)
  ctx.font = "bold 26px monospace";
  ctx.fillStyle = "#94a3b8";
  ctx.fillText("VTOL MAX 0.15t · G4 SAR", 125, canvas.height - 44);
  ctx.fillText("MISSION BAY 1 →", canvas.width - 310, 62);

  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 4;
  return tex;
}

/**
 * Genera textura de contenedor ISO corrugado con código BIC y advertencias técnicas.
 */
function createContainerTexture(baseHex, labelText, subText) {
  const canvas = document.createElement("canvas");
  canvas.width = 512;
  canvas.height = 256;
  const ctx = canvas.getContext("2d");

  ctx.fillStyle = baseHex;
  ctx.fillRect(0, 0, 512, 256);

  // Corrugado vertical realista
  for (let x = 16; x < 496; x += 20) {
    ctx.fillStyle = "rgba(0,0,0,0.18)";
    ctx.fillRect(x, 18, 6, 220);
    ctx.fillStyle = "rgba(255,255,255,0.09)";
    ctx.fillRect(x + 6, 18, 4, 220);
  }

  // Marco perimetral ISO y cantoneras
  ctx.strokeStyle = "rgba(255,255,255,0.28)";
  ctx.lineWidth = 8;
  ctx.strokeRect(6, 6, 500, 244);

  // Placa de identificación
  ctx.fillStyle = "rgba(10, 18, 28, 0.86)";
  ctx.fillRect(28, 42, 310, 86);
  ctx.strokeStyle = "#38bdf8";
  ctx.lineWidth = 2;
  ctx.strokeRect(28, 42, 310, 86);

  ctx.fillStyle = "#f8fafc";
  ctx.font = "bold 26px monospace";
  ctx.fillText(labelText, 42, 78);
  ctx.fillStyle = "#7dd3fc";
  ctx.font = "18px monospace";
  ctx.fillText(subText, 42, 108);

  const tex = new THREE.CanvasTexture(canvas);
  tex.anisotropy = 4;
  return tex;
}

/**
 * Construye una malla lofted cerrada entre estaciones longitudinales X (en metros, de -30 a +30).
 */
function buildLoftedHullSection(yMinNorm, yMaxNorm, material) {
  // 25 estaciones de popa (-30m) a proa (+30m) medidas del plano Fincantieri
  const stationsX = [
    -30.0, -28.8, -27.0, -24.5, -21.0, -17.5, -14.0, -10.5, -7.0, -3.5,
    0.0, 3.5, 7.0, 10.5, 14.0, 17.5, 20.5, 23.0, 25.2, 27.0, 28.3, 29.2, 29.7, 30.0,
  ];

  // Función que devuelve el perfil transversal (z, y) en función de x y de la fracción vertical v in [0..1]
  function sampleHullPoint(x, sideSign, vNorm) {
    const u = (x + 30.0) / 60.0; // 0 en popa, 1 en proa

    // Semimanga en cubierta/flotación según estación longitudinal
    let halfBeamWl = 5.5;
    if (u < 0.14) {
      halfBeamWl = 4.55 + (u / 0.14) * 0.95;
    } else if (u > 0.64) {
      const tb = (u - 0.64) / 0.36;
      halfBeamWl = 5.5 * Math.pow(Math.max(0.0, Math.cos(tb * Math.PI * 0.495)), 0.82);
    }

    // Altura de borda/cubierta: popa a Y=+2.60m sobre WL (5.8m sobre quilla), castillo de proa sube a +4.80m sobre WL (8.0m sobre quilla)
    let sheerY = 2.60;
    if (u > 0.56) {
      const tf = Math.min(1.0, (u - 0.56) / 0.18);
      sheerY = 2.60 + (1.0 - Math.cos(tf * Math.PI)) * 0.5 * 2.15 + Math.max(0, u - 0.74) * 1.4;
    }

    // Quilla: rises slightly at stern transom for the 2x pods tunnel, drops to -3.20m midships/forward
    let keelY = -3.20;
    if (u < 0.22) {
      keelY = -3.20 + (1.0 - u / 0.22) * 1.45;
    }

    const y = keelY + vNorm * (sheerY - keelY);

    // Forma de la cuaderna transversal: pantoque redondeado + codillo + abanico de proa (bow flare)
    let widthFactor = 1.0;
    if (vNorm < 0.08) {
      widthFactor = (vNorm / 0.08) * 0.32;
    } else if (vNorm < 0.45) {
      const tv = (vNorm - 0.08) / 0.37;
      widthFactor = 0.32 + Math.pow(tv, 0.58) * 0.66;
    } else {
      const tv = (vNorm - 0.45) / 0.55;
      const bowFlare = u > 0.60 ? ((u - 0.60) / 0.40) * 0.22 * tv : 0.03 * tv;
      widthFactor = 0.98 + bowFlare;
    }

    // Bulbo de proa sumergido (u > 0.86 y vNorm < 0.40) tal como se ve en el plano
    if (u > 0.86 && vNorm > 0.05 && vNorm < 0.40) {
      const bulbU = (u - 0.86) / 0.14;
      const bulbV = 1.0 - Math.abs(vNorm - 0.22) / 0.18;
      if (bulbV > 0) {
        const bulbRadius = 1.15 * Math.sin(bulbU * Math.PI * 0.88) * bulbV;
        return new THREE.Vector3(x + bulbU * 0.85, y, sideSign * Math.max(halfBeamWl * widthFactor, bulbRadius));
      }
    }

    return new THREE.Vector3(x, y, sideSign * halfBeamWl * widthFactor);
  }

  const vSteps = 10;
  const positions = [];
  const uvs = [];
  const indices = [];

  // Construimos ambos costados (estribor sideSign=+1 y babor sideSign=-1)
  for (const sideSign of [1, -1]) {
    const baseVertexOffset = positions.length / 3;
    for (let i = 0; i < stationsX.length; i++) {
      const x = stationsX[i];
      const uCoord = (x + 30.0) / 60.0;
      for (let j = 0; j <= vSteps; j++) {
        const vLocal = j / vSteps;
        const vNorm = yMinNorm + vLocal * (yMaxNorm - yMinNorm);
        const pt = sampleHullPoint(x, sideSign, vNorm);
        positions.push(pt.x, pt.y, pt.z);
        uvs.push(uCoord, vLocal);
      }
    }

    const stride = vSteps + 1;
    for (let i = 0; i < stationsX.length - 1; i++) {
      for (let j = 0; j < vSteps; j++) {
        const a = baseVertexOffset + i * stride + j;
        const b = baseVertexOffset + (i + 1) * stride + j;
        const c = baseVertexOffset + (i + 1) * stride + (j + 1);
        const d = baseVertexOffset + i * stride + (j + 1);
        if (sideSign > 0) {
          indices.push(a, b, c, a, c, d);
        } else {
          indices.push(a, c, b, a, d, c);
        }
      }
    }
  }

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geo.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geo.setIndex(indices);
  geo.computeVertexNormals();

  return new THREE.Mesh(geo, material);
}

export function buildProceduralVessel() {
  const root = new THREE.Group();
  root.name = "SHIP_ROOT";

  // Materiales PBR navales de alta calidad
  const matHullUpper = new THREE.MeshStandardMaterial({
    color: 0x46525e, // Fincantieri Naval / Offshore Slate Grey
    roughness: 0.42,
    metalness: 0.32,
    side: THREE.DoubleSide,
  });
  const matHullLower = new THREE.MeshStandardMaterial({
    color: 0x6e221f, // Anti-fouling Oxide Red
    roughness: 0.55,
    metalness: 0.22,
    side: THREE.DoubleSide,
  });
  const matBootTop = new THREE.MeshStandardMaterial({
    color: 0x182026, // Boot-topping waterline black band
    roughness: 0.35,
    metalness: 0.25,
    side: THREE.DoubleSide,
  });
  const matSuperstructure = new THREE.MeshStandardMaterial({
    color: 0x566370,
    roughness: 0.38,
    metalness: 0.28,
  });
  const matDeckSteel = new THREE.MeshStandardMaterial({
    color: 0x2c353d,
    roughness: 0.72,
    metalness: 0.18,
  });
  const matBridgeGlass = new THREE.MeshPhysicalMaterial({
    color: 0x0c2438,
    roughness: 0.12,
    metalness: 0.1,
    transmission: 0.45,
    transparent: true,
    opacity: 0.88,
    emissive: new THREE.Color(0x0d3b5c),
    emissiveIntensity: 0.35,
  });
  const matDarkMetal = new THREE.MeshStandardMaterial({
    color: 0x1e252b,
    roughness: 0.45,
    metalness: 0.65,
  });
  const matBrassProp = new THREE.MeshStandardMaterial({
    color: 0xc89d3c,
    roughness: 0.25,
    metalness: 0.85,
  });
  const matStripeOrange = new THREE.MeshStandardMaterial({
    color: 0xea580c,
    roughness: 0.35,
    metalness: 0.15,
    emissive: new THREE.Color(0xea580c),
    emissiveIntensity: 0.12,
  });
  const matStripeWhite = new THREE.MeshStandardMaterial({
    color: 0xf8fafc,
    roughness: 0.3,
    metalness: 0.1,
  });

  // ============================================================================
  // 1. CASCO LOFTED MULTICAPA (Obra viva, Bota de flotación, Obra muerta)
  // ============================================================================
  const hullGroup = new THREE.Group();
  hullGroup.name = "HULL";

  const lowerHull = buildLoftedHullSection(0.0, 0.43, matHullLower);
  const bootStripe = buildLoftedHullSection(0.43, 0.48, matBootTop);
  const upperHull = buildLoftedHullSection(0.48, 1.0, matHullUpper);
  hullGroup.add(lowerHull, bootStripe, upperHull);

  // Franjas diagonales de alta visibilidad en las amuras (estilo Fincantieri Dual-Use / Coast Guard)
  const stripeGroup = new THREE.Group();
  stripeGroup.name = "DUAL_USE_HULL_STRIPES";
  for (const side of [-1, 1]) {
    const s1 = new THREE.Mesh(new THREE.BoxGeometry(2.2, 3.6, 0.08), matStripeOrange);
    s1.position.set(15.2, 2.0, side * 5.22);
    s1.rotation.z = -0.42;
    s1.rotation.y = side * 0.16;
    const s2 = new THREE.Mesh(new THREE.BoxGeometry(1.1, 3.6, 0.08), matStripeWhite);
    s2.position.set(13.2, 2.0, side * 5.34);
    s2.rotation.z = -0.42;
    s2.rotation.y = side * 0.14;
    stripeGroup.add(s1, s2);
  }
  hullGroup.add(stripeGroup);

  // Espejo de popa (Transom) y Rampa de Popa Articulada (POS_stern_ramp)
  const transomLeft = new THREE.Mesh(new THREE.BoxGeometry(0.45, 4.2, 3.2), matHullUpper);
  transomLeft.position.set(-29.8, 0.5, -3.1);
  const transomRight = new THREE.Mesh(new THREE.BoxGeometry(0.45, 4.2, 3.2), matHullUpper);
  transomRight.position.set(-29.8, 0.5, 3.1);
  hullGroup.add(transomLeft, transomRight);

  const sternRampGroup = new THREE.Group();
  sternRampGroup.name = "POS_stern_ramp";
  sternRampGroup.position.set(-28.2, 0.4, 0.0);
  const rampBed = new THREE.Mesh(new THREE.BoxGeometry(6.8, 0.35, 3.1), matDeckSteel);
  rampBed.rotation.z = 0.22; // Rampa inclinada hacia el mar
  sternRampGroup.add(rampBed);
  hullGroup.add(sternRampGroup);

  // Túnel de hélice de proa (Bow Thruster, visible en el plano bajo la proa)
  const thrusterRing = new THREE.Mesh(
    new THREE.CylinderGeometry(0.65, 0.65, 3.8, 20, 1, true),
    matDarkMetal
  );
  thrusterRing.rotation.x = Math.PI / 2;
  thrusterRing.position.set(23.5, -1.85, 0);
  thrusterRing.name = "PROP_BOW_THRUSTER";
  hullGroup.add(thrusterRing);

  // ============================================================================
  // 2. CUBIERTAS Y MARCAS NORMATIVAS DE CUBIERTA DE VUELO (12x8 m)
  // ============================================================================
  // Cubierta de trabajo y vuelo de popa (de X=-30m a X=-12m, Y=+2.60m)
  const aftDeckGeo = new THREE.BoxGeometry(20.5, 0.25, 10.6);
  const aftDeckMesh = new THREE.Mesh(aftDeckGeo, matDeckSteel);
  aftDeckMesh.position.set(-19.8, 2.52, 0);
  hullGroup.add(aftDeckMesh);

  // Superficie de cubierta de vuelo 12x8m con textura CAP 437
  const flightDeckTex = createFlightDeckTexture();
  const flightDeckMat = new THREE.MeshStandardMaterial({
    map: flightDeckTex,
    roughness: 0.65,
    metalness: 0.15,
  });
  const flightDeckMesh = new THREE.Mesh(new THREE.PlaneGeometry(12.2, 8.2), flightDeckMat);
  flightDeckMesh.rotation.x = -Math.PI / 2;
  flightDeckMesh.position.set(-22.8, 2.66, 0);
  flightDeckMesh.name = "POS_flight_deck";
  hullGroup.add(flightDeckMesh);

  // Cubierta del castillo de proa (Forecastle deck, Y=+4.75m)
  const foreDeckMesh = new THREE.Mesh(new THREE.BoxGeometry(19.5, 0.28, 9.6), matDeckSteel);
  foreDeckMesh.position.set(18.5, 4.62, 0);
  hullGroup.add(foreDeckMesh);

  // Rompeolas en proa y bolardos de amarre
  const breakwater = new THREE.Mesh(new THREE.BoxGeometry(0.35, 0.95, 6.2), matHullUpper);
  breakwater.position.set(24.2, 5.2, 0);
  breakwater.rotation.z = -0.25;
  hullGroup.add(breakwater);

  root.add(hullGroup);

  // ============================================================================
  // 3. PROPULSIÓN SUBMARINA: 2x PODS AZIMUTALES + TIMÓN DE POPA (Plano oficial)
  // ============================================================================
  const podsGroup = new THREE.Group();
  podsGroup.name = "PROPULSION_PODS";
  const podSpinners = [];

  // El plano rotula dos estaciones "PROPULSION 2x POD" bajo la popa con árboles inclinados y aletas
  const podPositions = [
    { name: "PROP_POD_P", x: -23.5, y: -3.35, z: -2.35 },
    { name: "PROP_POD_S", x: -23.5, y: -3.35, z: 2.35 },
    { name: "PROP_POD_FWD_P", x: -16.8, y: -3.45, z: -2.15 },
    { name: "PROP_POD_FWD_S", x: -16.8, y: -3.45, z: 2.15 },
  ];

  for (const pp of podPositions) {
    const podUnit = new THREE.Group();
    podUnit.name = pp.name;
    podUnit.position.set(pp.x, pp.y, pp.z);

    const strut = new THREE.Mesh(new THREE.BoxGeometry(1.4, 1.8, 0.32), matHullLower);
    strut.position.set(0, 0.85, 0);
    podUnit.add(strut);

    const nacelle = new THREE.Mesh(new THREE.CapsuleGeometry(0.48, 2.1, 8, 16), matDarkMetal);
    nacelle.rotation.z = Math.PI / 2;
    podUnit.add(nacelle);

    // Hélice de 5 palas de bronce naval
    const propHub = new THREE.Group();
    propHub.position.set(-1.35, 0, 0);
    const hubCone = new THREE.Mesh(new THREE.ConeGeometry(0.32, 0.65, 12), matBrassProp);
    hubCone.rotation.z = Math.PI / 2;
    propHub.add(hubCone);

    for (let b = 0; b < 5; b++) {
      const angle = (b / 5) * Math.PI * 2;
      const blade = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.95, 0.28), matBrassProp);
      blade.position.set(0, Math.cos(angle) * 0.52, Math.sin(angle) * 0.52);
      blade.rotation.x = angle;
      blade.rotation.y = 0.38;
      propHub.add(blade);
    }
    podUnit.add(propHub);
    podSpinners.push(propHub);
    podsGroup.add(podUnit);
  }

  // Aleta estabilizadora / skeg de popa como en el extremo izquierdo del plano
  for (const sideZ of [-2.35, 2.35]) {
    const rudder = new THREE.Mesh(new THREE.BoxGeometry(1.8, 2.1, 0.24), matHullLower);
    rudder.position.set(-27.8, -2.9, sideZ);
    podsGroup.add(rudder);
  }
  root.add(podsGroup);

  // ============================================================================
  // 4. SUPERESTRUCTURA, HANGAR, PUENTE 360°, GRÚA Y NICHO DE RHIB (Fiel al plano)
  // ============================================================================
  const superGroup = new THREE.Group();
  superGroup.name = "SUPERSTRUCTURE_GROUP";

  // Bloque de popa de la superestructura: Hangar de Dron VTOL (X=-10.2 a -2.5m)
  const hangarBlock = new THREE.Mesh(new THREE.BoxGeometry(8.2, 3.4, 7.6), matSuperstructure);
  hangarBlock.position.set(-6.2, 4.3, 0);
  hangarBlock.name = "POS_hangar";
  superGroup.add(hangarBlock);

  // Puerta enrollable del hangar mirando hacia la cubierta de vuelo de popa
  const hangarDoor = new THREE.Mesh(
    new THREE.BoxGeometry(0.18, 2.6, 4.4),
    new THREE.MeshStandardMaterial({ color: 0x1e293b, roughness: 0.5, metalness: 0.6 })
  );
  hangarDoor.position.set(-10.31, 4.0, 0);
  superGroup.add(hangarDoor);

  // Plataforma en el techo del hangar (POS_hangar_roof: x1.2 stability, alternativa para antena G5)
  const hangarRoofPad = new THREE.Mesh(
    new THREE.CylinderGeometry(1.1, 1.25, 0.22, 16),
    new THREE.MeshStandardMaterial({ color: 0x334155, roughness: 0.4, metalness: 0.5 })
  );
  hangarRoofPad.position.set(-7.2, 6.12, 0);
  hangarRoofPad.name = "POS_hangar_roof";
  superGroup.add(hangarRoofPad);

  // Bloque central y nicho lateral para el bote RHIB + Bay 2 (X=-2.5 a +12.5m)
  const midCore = new THREE.Mesh(new THREE.BoxGeometry(14.8, 4.2, 6.2), matSuperstructure);
  midCore.position.set(4.8, 4.7, 0);
  superGroup.add(midCore);

  // Embarcación RHIB en pescantes en el nicho central (exactamente como se ve en el plano)
  for (const side of [-1, 1]) {
    const rhibGroup = new THREE.Group();
    rhibGroup.position.set(0.8, 5.15, side * 4.15);

    const sponson = new THREE.Mesh(
      new THREE.CapsuleGeometry(0.45, 4.2, 8, 16),
      new THREE.MeshStandardMaterial({ color: 0xd97706, roughness: 0.45 })
    );
    sponson.rotation.z = Math.PI / 2;
    rhibGroup.add(sponson);

    const rhibConsole = new THREE.Mesh(new THREE.BoxGeometry(1.2, 0.85, 0.9), matDarkMetal);
    rhibConsole.position.set(0.3, 0.55, 0);
    rhibGroup.add(rhibConsole);

    // Pescantes (Davits)
    for (const dx of [-1.8, 1.8]) {
      const davit = new THREE.Mesh(new THREE.BoxGeometry(0.25, 2.2, 0.9), matSuperstructure);
      davit.position.set(dx, 0.4, -side * 0.45);
      rhibGroup.add(davit);
    }
    superGroup.add(rhibGroup);
  }

  // Chimenea trapezoidal inclinada (Funnel casing con 2 tubos de escape DG1 y DG2)
  const funnelMesh = new THREE.Mesh(new THREE.BoxGeometry(4.6, 3.6, 3.4), matSuperstructure);
  funnelMesh.position.set(-2.8, 7.4, 0);
  funnelMesh.rotation.z = -0.14;
  superGroup.add(funnelMesh);

  const exhaustDG1 = new THREE.Mesh(new THREE.CylinderGeometry(0.38, 0.38, 1.2, 16), matDarkMetal);
  exhaustDG1.position.set(-3.1, 9.3, -0.75);
  exhaustDG1.name = "EXHAUST_DG1";
  const exhaustDG2 = new THREE.Mesh(new THREE.CylinderGeometry(0.38, 0.38, 1.2, 16), matDarkMetal);
  exhaustDG2.position.set(-3.1, 9.3, 0.75);
  exhaustDG2.name = "EXHAUST_DG2";
  superGroup.add(exhaustDG1, exhaustDG2);

  // Grúa articulada de cubierta a popa de la chimenea (visible en el plano sobre el hangar)
  const craneGroup = new THREE.Group();
  craneGroup.name = "DECK_CRANE";
  craneGroup.position.set(-8.2, 6.0, 2.4);
  const cranePedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.42, 0.52, 1.8, 16), matStripeOrange);
  cranePedestal.position.y = 0.9;
  const craneBoom1 = new THREE.Mesh(new THREE.BoxGeometry(4.2, 0.38, 0.38), matStripeOrange);
  craneBoom1.position.set(-1.6, 2.1, 0);
  craneBoom1.rotation.z = -0.22;
  const craneBoom2 = new THREE.Mesh(new THREE.BoxGeometry(3.2, 0.28, 0.28), matStripeOrange);
  craneBoom2.position.set(-4.4, 2.2, 0);
  craneBoom2.rotation.z = 0.15;
  craneGroup.add(cranePedestal, craneBoom1, craneBoom2);
  superGroup.add(craneGroup);

  // Puente de mando en 2 niveles ("BRIDGE DECK" inferior y superior en el plano)
  const lowerBridge = new THREE.Mesh(new THREE.BoxGeometry(9.6, 2.4, 8.4), matSuperstructure);
  lowerBridge.position.set(8.2, 6.2, 0);
  superGroup.add(lowerBridge);

  const upperBridge = new THREE.Group();
  upperBridge.name = "BRIDGE_WHEELHOUSE";
  upperBridge.position.set(7.6, 8.6, 0);

  const wheelhouseBase = new THREE.Mesh(new THREE.BoxGeometry(7.4, 1.2, 9.4), matSuperstructure);
  wheelhouseBase.position.y = -0.5;
  upperBridge.add(wheelhouseBase);

  // Banda acristalada 360° inclinada hacia adelante (típica de buques Fincantieri / Vard)
  const glassBand = new THREE.Mesh(new THREE.BoxGeometry(7.1, 1.15, 9.1), matBridgeGlass);
  glassBand.position.y = 0.55;
  upperBridge.add(glassBand);

  // Marcos verticales de las ventanas del puente (Mullions)
  for (let z = -4.2; z <= 4.2; z += 1.05) {
    const mullion = new THREE.Mesh(new THREE.BoxGeometry(0.12, 1.22, 0.12), matDarkMetal);
    mullion.position.set(3.58, 0.55, z);
    mullion.rotation.z = 0.14;
    upperBridge.add(mullion);
  }

  const bridgeRoof = new THREE.Mesh(new THREE.BoxGeometry(7.8, 0.35, 9.6), matSuperstructure);
  bridgeRoof.position.y = 1.28;
  upperBridge.add(bridgeRoof);

  // Luces de navegación COLREG (Anexo I: Babor Roja, Estribor Verde) en los alerones del puente
  const portLight = new THREE.Mesh(
    new THREE.SphereGeometry(0.22, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xff1e1e })
  );
  portLight.position.set(2.2, 0.2, -4.85);
  const stbdLight = new THREE.Mesh(
    new THREE.SphereGeometry(0.22, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0x10b981 })
  );
  stbdLight.position.set(2.2, 0.2, 4.85);
  upperBridge.add(portLight, stbdLight);

  superGroup.add(upperBridge);

  // ============================================================================
  // 5. MÁSTIL INTEGRADO CON LAS 3 POSICIONES OFICIALES (mast_1, mast_2, mast_3)
  // ============================================================================
  const mastGroup = new THREE.Group();
  mastGroup.name = "INTEGRATED_MAST";
  mastGroup.position.set(6.5, 9.9, 0);

  // Torre piramidal principal del mástil + vergas horizontales (yardarms)
  const mastTower = new THREE.Mesh(new THREE.CylinderGeometry(0.45, 1.35, 8.2, 8), matSuperstructure);
  mastTower.position.y = 4.1;
  mastGroup.add(mastTower);

  for (const yBar of [3.2, 5.4, 7.6]) {
    const yard = new THREE.Mesh(new THREE.BoxGeometry(2.2, 0.18, 4.4 - yBar * 0.32), matSuperstructure);
    yard.position.set(0.3, yBar, 0);
    mastGroup.add(yard);
  }

  // mast_1 (Fijo): Radar de navegación X-Band giratorio
  const mast1Radar = new THREE.Group();
  mast1Radar.name = "POS_mast_1";
  mast1Radar.position.set(1.15, 5.65, 0);
  const radarPedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.28, 0.45, 12), matDarkMetal);
  const radarScanner = new THREE.Mesh(
    new THREE.BoxGeometry(0.28, 0.24, 2.3),
    new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.25 })
  );
  radarScanner.position.y = 0.32;
  mast1Radar.add(radarPedestal, radarScanner);
  mastGroup.add(mast1Radar);

  // mast_2 (Fijo): Radomo SATCOM del buque (esfera blanca en el plano)
  const mast2Satcom = new THREE.Group();
  mast2Satcom.name = "POS_mast_2";
  mast2Satcom.position.set(1.05, 3.55, 0);
  const satDome = new THREE.Mesh(
    new THREE.SphereGeometry(0.78, 24, 24),
    new THREE.MeshStandardMaterial({ color: 0xf1f5f9, roughness: 0.2, metalness: 0.05 })
  );
  satDome.position.y = 0.65;
  mast2Satcom.add(satDome);
  mastGroup.add(mast2Satcom);

  // mast_3 (Slot Libre para Guest G1 o Antena de G5: <=60 kg, <=0.6 m2, <=2 kW aire)
  const mast3Slot = new THREE.Group();
  mast3Slot.name = "POS_mast_3";
  mast3Slot.position.set(0.0, 8.35, 0);
  const mast3Pad = new THREE.Mesh(
    new THREE.CylinderGeometry(0.55, 0.55, 0.16, 16),
    new THREE.MeshStandardMaterial({
      color: 0x0ea5e9,
      emissive: new THREE.Color(0x0284c7),
      emissiveIntensity: 0.45,
    })
  );
  mast3Slot.add(mast3Pad);
  mastGroup.add(mast3Slot);

  // Luz blanca de tope COLREG (225°)
  const mastheadLight = new THREE.Mesh(
    new THREE.SphereGeometry(0.2, 12, 12),
    new THREE.MeshBasicMaterial({ color: 0xffffff })
  );
  mastheadLight.position.set(0.0, 9.8, 0);
  mastGroup.add(mastheadLight);

  superGroup.add(mastGroup);

  // ============================================================================
  // 6. MONTAJE DE PROA CONMUTABLE (Dual-Use RWS Naval ↔ Cañón de Agua / SAR Civil)
  // ============================================================================
  const bowMountNaval = new THREE.Group();
  bowMountNaval.name = "BOW_MOUNT_NAVAL";
  bowMountNaval.position.set(17.2, 4.78, 0);
  const turretBase = new THREE.Mesh(new THREE.CylinderGeometry(1.15, 1.35, 0.65, 16), matSuperstructure);
  turretBase.position.y = 0.32;
  const turretCupola = new THREE.Mesh(new THREE.ConeGeometry(1.15, 1.05, 6), matSuperstructure);
  turretCupola.position.y = 1.05;
  const turretBarrel = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.12, 2.8, 12), matDarkMetal);
  turretBarrel.rotation.z = Math.PI / 2 - 0.12;
  turretBarrel.position.set(1.55, 1.05, 0);
  bowMountNaval.add(turretBase, turretCupola, turretBarrel);
  superGroup.add(bowMountNaval);

  const bowMountCivil = new THREE.Group();
  bowMountCivil.name = "BOW_MOUNT_CIVIL";
  bowMountCivil.position.set(17.2, 4.78, 0);
  bowMountCivil.visible = false;
  const sarPedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.65, 0.85, 1.2, 16), matStripeWhite);
  sarPedestal.position.y = 0.6;
  const waterMonitor = new THREE.Mesh(new THREE.CylinderGeometry(0.28, 0.35, 1.4, 16), matStripeOrange);
  waterMonitor.rotation.z = Math.PI / 2 - 0.25;
  waterMonitor.position.set(0.55, 1.35, 0);
  bowMountCivil.add(sarPedestal, waterMonitor);
  superGroup.add(bowMountCivil);

  root.add(superGroup);

  // ============================================================================
  // 7. COMPARTIMENTOS INTERNOS PARA MODO RAYOS X (Power, Cooling, Rack, ROV Hangar)
  // ============================================================================
  const xrayGroup = new THREE.Group();
  xrayGroup.name = "XRAY_INTERNAL_SYSTEMS";
  xrayGroup.visible = false;

  const matGenHealthy = new THREE.MeshStandardMaterial({
    color: 0x10b981,
    emissive: new THREE.Color(0x059669),
    emissiveIntensity: 0.75,
    roughness: 0.3,
  });
  const matGen2 = matGenHealthy.clone();
  const matLineA = new THREE.MeshStandardMaterial({
    color: 0xf59e0b,
    emissive: new THREE.Color(0xd97706),
    emissiveIntensity: 0.85,
  });
  const matLineB = new THREE.MeshStandardMaterial({
    color: 0x3b82f6,
    emissive: new THREE.Color(0x2563eb),
    emissiveIntensity: 0.85,
  });
  const matCoolingLoop = new THREE.MeshStandardMaterial({
    color: 0x06b6d4,
    emissive: new THREE.Color(0x0891b2),
    emissiveIntensity: 0.85,
  });

  // 2x Generadores principales de 450 kW (DG1 babor, DG2 estribor) + 1x Generador de puerto 150 kW
  const dg1Mesh = new THREE.Mesh(new THREE.BoxGeometry(3.8, 1.8, 1.7), matGenHealthy);
  dg1Mesh.position.set(-3.5, -1.2, -2.1);
  dg1Mesh.name = "PWR_GEN_1";
  const dg2Mesh = new THREE.Mesh(new THREE.BoxGeometry(3.8, 1.8, 1.7), matGen2);
  dg2Mesh.position.set(-3.5, -1.2, 2.1);
  dg2Mesh.name = "PWR_GEN_2";
  const harbourGen = new THREE.Mesh(
    new THREE.BoxGeometry(2.2, 1.4, 1.3),
    new THREE.MeshStandardMaterial({ color: 0x64748b, roughness: 0.5 })
  );
  harbourGen.position.set(1.2, -1.3, 0.0);
  harbourGen.name = "PWR_HARBOUR";
  xrayGroup.add(dg1Mesh, dg2Mesh, harbourGen);

  // Líneas de potencia LINE_A_FWD (150 kW -> Bay1, Mast) y LINE_B_AFT (150 kW -> Bay2, Hangar, Rack)
  const lineAPts = [
    new THREE.Vector3(-3.5, -0.4, -1.8),
    new THREE.Vector3(-15.5, 2.2, -1.8),
    new THREE.Vector3(6.5, 2.2, -1.8),
    new THREE.Vector3(6.5, 17.5, 0.0),
  ];
  const lineAMesh = new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3(lineAPts), 32, 0.14, 8, false),
    matLineA
  );
  lineAMesh.name = "PWR_LINE_A_FWD";

  const lineBPts = [
    new THREE.Vector3(-3.5, -0.4, 1.8),
    new THREE.Vector3(-22.0, 2.2, 1.8),
    new THREE.Vector3(-6.2, 3.8, 1.8),
    new THREE.Vector3(4.5, 2.2, 1.8),
  ];
  const lineBMesh = new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3(lineBPts), 32, 0.14, 8, false),
    matLineB
  );
  lineBMesh.name = "PWR_LINE_B_AFT";
  xrayGroup.add(lineAMesh, lineBMesh);

  // Lazo de agua helada (CHILLED_WATER_LOOP ISO 14726)
  const coolPts = [
    new THREE.Vector3(-16.5, 1.2, 0.0),
    new THREE.Vector3(-6.2, 1.2, 0.0),
    new THREE.Vector3(0.5, 1.2, -2.8),
    new THREE.Vector3(5.5, 1.2, 0.0),
  ];
  const coolLoopMesh = new THREE.Mesh(
    new THREE.TubeGeometry(new THREE.CatmullRomCurve3(coolPts), 32, 0.18, 10, false),
    matCoolingLoop
  );
  coolLoopMesh.name = "COOL_LOOP_SUPPLY";
  xrayGroup.add(coolLoopMesh);

  // Rack de cómputo de 4 nodos (32 cores / 128 GiB / 1 acelerador cada uno)
  const rackNodes = [];
  for (let n = 0; n < 4; n++) {
    const nodeMat = new THREE.MeshStandardMaterial({
      color: 0x1e293b,
      emissive: new THREE.Color(0x38bdf8),
      emissiveIntensity: 0.55,
    });
    const nodeMesh = new THREE.Mesh(new THREE.BoxGeometry(0.95, 2.1, 0.72), nodeMat);
    nodeMesh.position.set(5.2, 1.8, -1.35 + n * 0.9);
    nodeMesh.name = `RACK_node_${n + 1}`;
    xrayGroup.add(nodeMesh);
    rackNodes.push(nodeMesh);
  }

  // ROV Hangar de proa baja (rotulado en el plano como "ROV HANGAR")
  const rovHangarBox = new THREE.Mesh(
    new THREE.BoxGeometry(5.6, 2.2, 4.2),
    new THREE.MeshBasicMaterial({ color: 0x38bdf8, wireframe: true })
  );
  rovHangarBox.position.set(15.5, -0.8, 0.0);
  rovHangarBox.name = "ROV_HANGAR_BOW";
  xrayGroup.add(rovHangarBox);

  root.add(xrayGroup);

  // ============================================================================
  // 8. ACTORES 3D DE LOS 6 GUESTS (G1..G6) Y SISTEMA ACÚSTICO / SATCOM
  // ============================================================================
  const guestsGroup = new THREE.Group();
  guestsGroup.name = "GUEST_PAYLOAD_ACTORS";

  // --- G1: Passive Radio & Meteo Mast Sensor (45 kg en mast_3) ---
  const g1Actor = new THREE.Group();
  g1Actor.name = "ACTOR_G1";
  g1Actor.position.set(6.5, 18.4, 0.0);
  g1Actor.visible = true;
  const g1Pole = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.16, 1.4, 12), matStripeWhite);
  g1Pole.position.y = 0.7;
  g1Actor.add(g1Pole);
  for (let a = 0; a < 4; a++) {
    const dipole = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.9, 8), matStripeOrange);
    dipole.position.set(Math.cos((a * Math.PI) / 2) * 0.35, 1.15, Math.sin((a * Math.PI) / 2) * 0.35);
    g1Actor.add(dipole);
  }
  guestsGroup.add(g1Actor);

  // --- G2: Ocean & Weather Module 10-ft + Winch + CTD Probe (3,500 kg) ---
  const g2Actor = new THREE.Group();
  g2Actor.name = "ACTOR_G2";
  g2Actor.position.set(-14.2, 3.95, 2.4); // Bay1_b o Bay2
  g2Actor.visible = false;
  const g2Tex = createContainerTexture("#0369a1", "G2 · OCEAN & METEO", "3,500 kg · 6/20 kW WINCH");
  const g2Box = new THREE.Mesh(
    new THREE.BoxGeometry(2.99, 2.59, 2.44),
    new THREE.MeshStandardMaterial({ map: g2Tex, roughness: 0.45, metalness: 0.25 })
  );
  const g2WinchDrum = new THREE.Mesh(new THREE.CylinderGeometry(0.55, 0.55, 0.9, 16), matStripeOrange);
  g2WinchDrum.position.set(0, 1.65, 0.6);
  g2Actor.add(g2Box, g2WinchDrum);
  guestsGroup.add(g2Actor);

  // --- G3: Seabed Inspection AUV + 20-ft Cradle en Bay 1 (5,500 kg) ---
  const g3Actor = new THREE.Group();
  g3Actor.name = "ACTOR_G3";
  const g3Tex = createContainerTexture("#b45309", "G3 · AUV CRADLE 20FT", "5,500 kg · 1 kW SURVEY / 25 kW CHG");
  const g3Cradle = new THREE.Mesh(
    new THREE.BoxGeometry(6.06, 2.59, 2.44),
    new THREE.MeshStandardMaterial({ map: g3Tex, roughness: 0.42, metalness: 0.3 })
  );
  g3Cradle.position.set(-13.8, 3.95, 0.0); // Bay 1 aft
  g3Actor.add(g3Cradle);

  // Vehículo submarino AUV G3 navegando bajo el casco con anillos acústicos
  const g3AuvSubmerged = new THREE.Group();
  g3AuvSubmerged.name = "G3_AUV_SUBMERGED";
  g3AuvSubmerged.position.set(-18.0, -9.5, 8.5);
  const auvBody = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.45, 3.6, 12, 24),
    new THREE.MeshStandardMaterial({
      color: 0xfacc15,
      roughness: 0.28,
      metalness: 0.35,
      emissive: new THREE.Color(0xca8a04),
      emissiveIntensity: 0.25,
    })
  );
  auvBody.rotation.z = Math.PI / 2;
  g3AuvSubmerged.add(auvBody);

  // Cono de sonar multihaz hacia el lecho marino
  const sonarConeMat = new THREE.MeshBasicMaterial({
    color: 0x22d3ee,
    transparent: true,
    opacity: 0.22,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const sonarCone = new THREE.Mesh(new THREE.ConeGeometry(5.5, 14.0, 24, 1, true), sonarConeMat);
  sonarCone.position.set(0, -7.2, 0);
  g3AuvSubmerged.add(sonarCone);

  // Anillos de enlace acústico (Acoustic Modem Link 10-60s handshake)
  const acousticRings = [];
  for (let r = 0; r < 3; r++) {
    const ringMat = new THREE.MeshBasicMaterial({
      color: 0x22d3ee,
      transparent: true,
      opacity: 0.55,
      side: THREE.DoubleSide,
    });
    const ring = new THREE.Mesh(new THREE.RingGeometry(0.8 + r * 0.6, 1.05 + r * 0.6, 32), ringMat);
    ring.rotation.x = Math.PI / 2;
    g3AuvSubmerged.add(ring);
    acousticRings.push(ring);
  }
  g3Actor.add(g3AuvSubmerged);
  guestsGroup.add(g3Actor);

  // --- G4: VTOL Rescue Drone (140 kg) + Hangar Station (850 kg = 990 kg Errata) ---
  const g4Actor = new THREE.Group();
  g4Actor.name = "ACTOR_G4";

  const g4Drone = new THREE.Group();
  g4Drone.name = "G4_VTOL_DRONE";
  g4Drone.position.set(-22.8, 12.0, 0.0);

  const fuselage = new THREE.Mesh(
    new THREE.CapsuleGeometry(0.42, 2.2, 10, 16),
    new THREE.MeshStandardMaterial({ color: 0xf8fafc, roughness: 0.25, metalness: 0.2 })
  );
  fuselage.rotation.z = Math.PI / 2;
  const tailStripe = new THREE.Mesh(new THREE.BoxGeometry(0.9, 0.55, 0.14), matStripeOrange);
  tailStripe.position.set(-1.35, 0.35, 0);
  const mainWing = new THREE.Mesh(new THREE.BoxGeometry(0.65, 0.1, 4.6), matStripeWhite);
  mainWing.position.set(0.1, 0.25, 0);
  g4Drone.add(fuselage, tailStripe, mainWing);

  // 4 Rotores VTOL en H-frame
  const g4Rotors = [];
  const rotorCoords = [
    [-1.1, 0.35, -1.85],
    [1.2, 0.35, -1.85],
    [-1.1, 0.35, 1.85],
    [1.2, 0.35, 1.85],
  ];
  for (const [rx, ry, rz] of rotorCoords) {
    const disc = new THREE.Mesh(
      new THREE.CylinderGeometry(0.72, 0.72, 0.04, 16),
      new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.42 })
    );
    disc.position.set(rx, ry, rz);
    g4Drone.add(disc);
    g4Rotors.push(disc);
  }

  // Anillo holográfico sobre el dron para mostrar alerta <20% batería / 720s deadline
  const droneAlertRingMat = new THREE.MeshBasicMaterial({
    color: 0x22c55e,
    transparent: true,
    opacity: 0.75,
    side: THREE.DoubleSide,
  });
  const droneAlertRing = new THREE.Mesh(new THREE.RingGeometry(2.4, 2.75, 36), droneAlertRingMat);
  droneAlertRing.rotation.x = Math.PI / 2;
  droneAlertRing.position.y = -0.35;
  g4Drone.add(droneAlertRing);

  g4Actor.add(g4Drone);
  guestsGroup.add(g4Actor);

  // --- G5: Emergency Comms & Edge Compute Node (3,000 kg en Bay 2 + 25 kg Antena en mast_3 o hangar_roof) ---
  const g5Actor = new THREE.Group();
  g5Actor.name = "ACTOR_G5";
  const g5Tex = createContainerTexture("#4338ca", "G5 · EDGE COMPUTE 10FT", "3,000 kg · 45 kW FULL / 8 kW LOW");
  const g5Container = new THREE.Mesh(
    new THREE.BoxGeometry(2.99, 2.59, 2.44),
    new THREE.MeshStandardMaterial({ map: g5Tex, roughness: 0.38, metalness: 0.35 })
  );
  g5Container.position.set(-0.8, 3.95, -3.55); // Bay 2 midships port recess
  g5Actor.add(g5Container);

  // Antena de 25 kg de G5 + Haz SATCOM hacia el satélite (6 Mbps)
  const g5Antenna = new THREE.Group();
  g5Antenna.name = "G5_ANTENNA_UNIT";
  g5Antenna.position.set(-7.2, 6.3, 0.0); // En hangar_roof cuando G1 ocupa mast_3 (o (6.5, 18.35, 0.0) si G5 está en mast_3)
  const g5Dish = new THREE.Mesh(
    new THREE.CylinderGeometry(0.52, 0.25, 0.55, 20),
    new THREE.MeshStandardMaterial({ color: 0xe2e8f0, roughness: 0.25, metalness: 0.4 })
  );
  g5Dish.position.y = 0.35;
  g5Antenna.add(g5Dish);

  const satBeamMat = new THREE.MeshBasicMaterial({
    color: 0x818cf8,
    transparent: true,
    opacity: 0.28,
    side: THREE.DoubleSide,
    depthWrite: false,
  });
  const satBeam = new THREE.Mesh(new THREE.CylinderGeometry(2.6, 0.35, 42.0, 20, 1, true), satBeamMat);
  satBeam.position.set(4.5, 20.5, -4.0);
  satBeam.rotation.z = -0.22;
  satBeam.rotation.x = -0.18;
  g5Antenna.add(satBeam);

  g5Actor.add(g5Antenna);
  guestsGroup.add(g5Actor);

  // --- G6: Civil Hydrographic & eDNA Micro-Sampler (420 kg) + Ghost Admission Box ---
  const g6Actor = new THREE.Group();
  g6Actor.name = "ACTOR_G6";
  g6Actor.visible = false;
  const g6Tex = createContainerTexture("#0d9488", "G6 · CIVIL eDNA SAMPLER", "420 kg · 4.5 kW WATER-COOLED");
  const g6Skid = new THREE.Mesh(
    new THREE.BoxGeometry(2.4, 1.85, 1.9),
    new THREE.MeshStandardMaterial({ map: g6Tex, roughness: 0.35, metalness: 0.3 })
  );
  g6Skid.position.set(-16.8, 3.55, 2.4);
  g6Actor.add(g6Skid);
  guestsGroup.add(g6Actor);

  // Caja Holográfica de Previsualización de Admisión D1 (Ghost Placement Preview)
  const ghostMat = new THREE.MeshBasicMaterial({
    color: 0x22c55e,
    transparent: true,
    opacity: 0.38,
    wireframe: false,
  });
  const ghostBox = new THREE.Mesh(new THREE.BoxGeometry(3.1, 2.7, 2.55), ghostMat);
  ghostBox.position.set(-16.8, 4.0, 2.4);
  ghostBox.visible = false;
  guestsGroup.add(ghostBox);

  root.add(guestsGroup);

  return {
    root,
    hullGroup,
    superGroup,
    xrayGroup,
    materials: {
      matHullUpper,
      matHullLower,
      matSuperstructure,
      matGen2,
      matLineA,
      matLineB,
      matCoolingLoop,
      sonarConeMat,
      satBeamMat,
      droneAlertRingMat,
      ghostMat,
    },
    animatedParts: {
      podSpinners,
      mast1Radar,
      bowMountNaval,
      bowMountCivil,
      stripeGroup,
      dg2Mesh,
      rackNodes,
      g1Actor,
      g2Actor,
      g3Actor,
      g3AuvSubmerged,
      acousticRings,
      sonarCone,
      g4Actor,
      g4Drone,
      g4Rotors,
      droneAlertRing,
      g5Actor,
      g5Antenna,
      satBeam,
      g6Actor,
      ghostBox,
    },
  };
}

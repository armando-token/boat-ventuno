/**
 * Director de Cámara Cinematográfico y Grabador de Vídeo de Respaldo (1080p Eco-Battery)
 * para el Gemelo Digital 3D del Software Defined Vessel.
 */

import * as THREE from "three";

export const CAMERA_SHOTS = {
  ORBIT_HERO: {
    label: "1 · Plano General Fincantieri (Starboard Bow)",
    pos: new THREE.Vector3(42.0, 15.5, 44.0),
    target: new THREE.Vector3(-2.0, 4.5, 0.0),
  },
  SF_GOLDEN_GATE: {
    label: "2 · Bahía de San Francisco & Golden Gate",
    pos: new THREE.Vector3(-48.0, 14.0, 52.0),
    target: new THREE.Vector3(22.0, 8.0, -45.0),
  },
  FLIGHT_DECK_G4: {
    label: "3 · Cubierta de Vuelo 12×8m & Dron G4 VTOL",
    pos: new THREE.Vector3(-38.0, 11.5, 18.0),
    target: new THREE.Vector3(-18.0, 5.2, 0.0),
  },
  SUBSEA_AUV_G3: {
    label: "4 · Submarino: AUV G3 + Enlace Acústico & 2x Pods",
    pos: new THREE.Vector3(-32.0, -6.2, 24.0),
    target: new THREE.Vector3(-16.0, -6.5, 3.0),
  },
  MAST_SLOT_3: {
    label: "5 · Mástil Integrado (mast_3: G1 vs Antena G5)",
    pos: new THREE.Vector3(18.0, 21.5, 16.0),
    target: new THREE.Vector3(6.2, 16.5, 0.0),
  },
  XRAY_ENGINE_ROOM: {
    label: "6 · Rayos X: DG1/DG2, Lazo Térmico y Rack 4 Nodos",
    pos: new THREE.Vector3(6.0, 9.5, 28.0),
    target: new THREE.Vector3(-3.0, 1.5, 0.0),
  },
  TOP_GA_BLUEPRINT: {
    label: "7 · Vista Lateral / Plano Oficial Fincantieri 60m",
    pos: new THREE.Vector3(0.0, 6.5, 68.0),
    target: new THREE.Vector3(0.0, 5.0, 0.0),
  },
};

export class CameraDirector {
  constructor(camera, controls) {
    this.camera = camera;
    this.controls = controls;
    this.activeShotKey = "ORBIT_HERO";
    this.autoDirectorEnabled = true;
    this.desiredPos = CAMERA_SHOTS.ORBIT_HERO.pos.clone();
    this.desiredTarget = CAMERA_SHOTS.ORBIT_HERO.target.clone();
    this.transitionAlpha = 1.0;
  }

  setShot(shotKey, smooth = true) {
    const shot = CAMERA_SHOTS[shotKey] || CAMERA_SHOTS.ORBIT_HERO;
    this.activeShotKey = shotKey;
    this.desiredPos.copy(shot.pos);
    this.desiredTarget.copy(shot.target);
    this.transitionAlpha = smooth ? 0.0 : 1.0;
    if (!smooth) {
      this.camera.position.copy(this.desiredPos);
      this.controls.target.copy(this.desiredTarget);
      this.controls.update();
    }
  }

  /**
   * Coreografía automática sincronizada con los eventos reales de `scenario_B_replay`
   */
  evaluateScenarioBAutoShot(simTimeS, priorityProfileId = "survey_primary") {
    if (!this.autoDirectorEnabled) return null;
    const isSurvey = priorityProfileId === "survey_primary";
    const holdAckS = isSurvey ? 735 : 495;
    const touchdownS = isSurvey ? 1095 : 855;

    let targetShot = "ORBIT_HERO";
    if (simTimeS >= 140 && simTimeS < 460) {
      targetShot = "SF_GOLDEN_GATE";
    } else if (simTimeS >= 460 && simTimeS < holdAckS) {
      targetShot = "FLIGHT_DECK_G4";
    } else if (simTimeS >= holdAckS && simTimeS < 700) {
      targetShot = "SUBSEA_AUV_G3";
    } else if (simTimeS >= 700 && simTimeS < 840) {
      targetShot = "XRAY_ENGINE_ROOM";
    } else if (simTimeS >= 840 && simTimeS < touchdownS + 80) {
      targetShot = "FLIGHT_DECK_G4";
    } else if (simTimeS >= 1460 && simTimeS < 1640) {
      targetShot = "MAST_SLOT_3";
    } else {
      targetShot = "ORBIT_HERO";
    }

    if (targetShot !== this.activeShotKey) {
      this.setShot(targetShot, true);
      return targetShot;
    }
    return null;
  }

  update(dt) {
    if (this.transitionAlpha < 1.0) {
      this.transitionAlpha = Math.min(1.0, this.transitionAlpha + dt * 1.45);
      const lerpFactor = Math.min(1.0, dt * 3.8);
      this.camera.position.lerp(this.desiredPos, lerpFactor);
      this.controls.target.lerp(this.desiredTarget, lerpFactor);
    }
    this.controls.update();
  }
}

/**
 * Grabador de vídeo WebM/MP4 directo desde el canvas 3D (1080p Eco-Battery para MacBook Pro M1 Pro)
 */
export class CanvasVideoRecorder {
  constructor(canvas) {
    this.canvas = canvas;
    this.recorder = null;
    this.chunks = [];
    this.isRecording = false;
  }

  startRecording(fps = 30) {
    if (this.isRecording) return;
    const stream = this.canvas.captureStream(fps);
    const mimeCandidates = [
      "video/webm;codecs=vp9",
      "video/webm;codecs=vp8",
      "video/webm",
      "video/mp4",
    ];
    const mimeType = mimeCandidates.find((m) => MediaRecorder.isTypeSupported(m)) || "";
    this.chunks = [];
    this.recorder = new MediaRecorder(stream, mimeType ? { mimeType, videoBitsPerSecond: 8_000_000 } : undefined);
    this.recorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) this.chunks.push(e.data);
    };
    this.recorder.onstop = () => {
      const blob = new Blob(this.chunks, { type: mimeType || "video/webm" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `SDV_Digital_Twin_ScenarioB_${new Date().toISOString().slice(0, 19).replace(/:/g, "-")}.webm`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    };
    this.recorder.start(250);
    this.isRecording = true;
  }

  stopRecording() {
    if (!this.isRecording || !this.recorder) return;
    this.recorder.stop();
    this.isRecording = false;
  }
}

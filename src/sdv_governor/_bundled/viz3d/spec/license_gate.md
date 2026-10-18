# Puerta de Licencias y Cumplimiento de IA (Fase 0 — `viz3d/`)

**Fecha de verificación:** 17 de octubre de 2026  
**Normativa aplicable:** Fincantieri Maritime Dual-Use Hackathon — *Data Use Authorization §1–§3* y *Background/Foreground IP*.

---

## 1. Verificación de Restricciones de IA (Data Use Authorization §2)

- **Prohibición expresa:** *"AI models developed by companies based in China, or models built on or derived from such models may not be used at any stage of the Hackathon."*
- **Aplicación en `viz3d/`:**
  - ❌ **Cero uso** de generadores 3D de origen chino (Hunyuan3D / Tencent, Tripo3D / VAST, Rodin / Deemos, Meshy).
  - ✅ **100% de la geometría 3D del buque y de los guests (G1–G6)** se genera mediante **modelado paramétrico procedural propio** (en tiempo real con geometría NURBS/loft por estaciones en Three.js y en el script de exportación `viz3d/tools/blender/build_vessel.py`), constituyendo *Foreground IP* del equipo conforme al Artículo 11 de las Bases.

---

## 2. Auditoría de Licencias de Componentes de Terceros (`viz3d/`)

| Componente / Referencia | Licencia | Decisión de arquitectura en `viz3d/` |
|---|---|---|
| **Three.js** (`mrdoob/three.js`) | **MIT** | ✅ Aprobado. Se utiliza para el motor 3D, materiales físicos PBR, post-procesado y shaders GLSL de océano FFT/JONSWAP y atmósfera. |
| **OpenBridge Design System** (`Ocean-Industries-Concept-Lab/openbridge-webcomponents`) | **AGPL-3.0 / JIP Comercial** | ⚠️ **No se empaqueta código AGPL-3.0** para evitar contaminación copyleft del repositorio MIT. En su lugar, implementamos nuestros propios componentes limpios (`viz3d/src/console/`) siguiendo directamente las normas públicas **IEC 62288:2021** (paletas Day/Dusk/Night), **IEC 62923 / IMO MSC.302(87)** (Bridge Alert Management — BAM), **IMO S-Mode** e **ISO 14726**. |
| **Algoritmo de Océano FFT / JONSWAP** (Tessendorf 2001) | **Dominio público matemático / Código propio MIT** | ✅ Implementación limpia propia del espectro direccional JONSWAP multicascada con espuma por determinante Jacobiano, estela Kelvin ($19.47^\circ$) y 8 sondas de flotabilidad 6DOF. |
| **Atmósfera Física Rayleigh/Mie + Bahía de San Francisco** | **Código propio MIT** | ✅ Shader físico propio con dispersión Rayleigh/Mie, sol astronómico y silueta topográfica 3D del puente Golden Gate y Marin Headlands. |
| **Blender 4.5 LTS (`bpy`)** | **GPL-3.0 (herramienta externa)** | ✅ El script `viz3d/tools/blender/build_vessel.py` es código propio MIT; los archivos `.glb` generados por Blender son propiedad exclusiva del autor (no cubiertos por la GPL de Blender). |

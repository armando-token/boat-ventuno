# SDV 3D Digital Twin & IEC 62288 Maritime Bridge Console (`viz3d/`)

Gemelo digital 3D en tiempo real a **escala 1:1 (`LOA = 60.0 m`, `Manga = 11.0 m`, `Calado = 3.2 m`)** del **Software Defined Vessel** para el **Fincantieri Maritime Dual-Use Hackathon (San Francisco, 16–18 Oct 2026)**, acoplado en vivo al motor determinista `sdv-resource-governor` sin modificar ni una sola línea del backend.

---

## 1. Arranque en 1 Comando (MacBook Pro M1 Pro — Modo Eco-Battery 60 FPS)

Desde la raíz del proyecto (`/Users/armandosilva/DATA/Hack-Ardui/sdv-resource-governor`), ejecuta en tu terminal:

```bash
PYTHONPATH=src .venv/bin/python viz3d/serve_viz3d.py --port 5173
```

Luego abre en **Google Chrome**:
👉 **`http://127.0.0.1:5173`**

> **Nota sobre la conexión con el Backend (`:8765`):**
> - Si ya tienes corriendo `PYTHONPATH=src .venv/bin/python -m sdv_governor.cli serve-ui --port 8765`, `serve_viz3d.py` hace **proxy transparente** de todas las llamadas `/api/*` a `:8765` (`LIVE_PROXY_8765`).
> - Si `:8765` no está corriendo, `serve_viz3d.py` invoca **directamente en memoria** `dispatch_api_request()` del motor Python (`LIVE_DIRECT_ENGINE`), garantizando que cada tick, admisión D1, arbitraje D3 y degradación D4 provienen **100% del solver real**.

---

## 2. Qué Incluye el Gemelo Digital 3D (`viz3d/`)

1. **Geometría Naval Lofted 1:1 Fiel al Plano de Fincantieri (Cero "Cajas"):**
   - Casco construido mediante 25 estaciones transversales con **proa de bulbo hidrodinámica**, túnel de `bow thruster`, codillo, obra viva en rojo antiincrustante, línea de flotación y obra muerta en **Gris Naval Fincantieri con franja diagonal Dual-Use** (conmutable en vivo a **Blanco/Azul Civil Survey**).
   - **2× Pods Azimutales en popa (`PROPULSION 2x POD`)** con hélices de bronce animadas + rampa de popa articulada (`POS_stern_ramp`) + cubierta de vuelo de `12 × 8 m` (`POS_flight_deck`) con marcas normativas **CAP 437**.
   - **Nicho central con RHIB en pescantes**, grúa hidráulica articulada de cubierta, puente de mando 360° en 2 niveles (`BRIDGE DECK`) con luces de navegación **COLREG Anexo I** y mástil integrado (`POS_mast_1` radar X-band, `POS_mast_2` radomo SATCOM, `POS_mast_3` slot libre guest $\le 60\text{ kg}$).
2. **Océano Físico JONSWAP + Estela Kelvin ($19.47^\circ$) + Bahía de San Francisco:**
   - Superficie oceánica animada por espectro direccional **JONSWAP/Gerstner de 6 cascadas**, espuma por determinante Jacobiano en las crestas, estela Kelvin detrás del espejo y lavado de los pods.
   - **8 sondas hidrostáticas 6DOF** que calculan arfada (*heave*), cabeceo (*pitch*), balance (*roll*) y la inmersión física real ($\text{TPC} = 4.94\text{ t/cm} \rightarrow 1.93\text{ cm}$ para $9,515\text{ kg}$, con botón `Hydro ×20` rotulado como `VISUAL ONLY`).
   - Silueta 3D a escala del **puente Golden Gate** (*International Orange*) y Marin Headlands al horizonte.
3. **Modo Rayos X (`🔍 Rayos X`) y Actores G1–G6:**
   - **Energía (`Power`)**: `DG1 (450 kW)`, `DG2 (450 kW)` (cambia a rojo cuando cae en el minuto 12 a `100 kW`), `HARBOUR (150 kW)` y los conductos `LINE_A_FWD` y `LINE_B_AFT`.
   - **Refrigeración (`Cooling`)**: Colector de agua helada conforme a **ISO 14726** y enfriamiento por aire en mástil ($\le 2\text{ kW}$).
   - **Cómputo (`Compute Rack`)**: Los 4 nodos (`node_1..node_4`) con uso de cores y aceleradores.
   - **Actores animados**: `G3` (Cuna 20ft en `Bay 1` + AUV submarino con cono sonar y anillos acústicos que pasan de cian en `SURVEY_QUIET` a ámbar en `HOLD_ACOUSTIC`), `G4` (Dron VTOL SAR de 4 rotores patrullando, descendiendo con alarma $<20\%$ y aterrizando en cubierta antes de `T+1200s`), `G5` (Contenedor 10ft en `Bay 2` + antena en `mast_3` o `hangar_roof` con haz SATCOM que se atenúa de `45 kW` a `8 kW`), y previsualización **Ghost 3D** para la admisión en vivo del **Escenario A (D1)**.
4. **Consola de Puente Marítimo Normalizada:**
   - **IEC 62288:2021**: Conmutador de paletas **Dusk / Day / Night** (`◐ IEC Dusk`).
   - **IEC 62923 / IMO MSC.302(87) (BAM)**: Lista central de alertas con prioridades `▲ ALARM`, `◆ WARNING`, `● CAUTION` y botón de reconocimiento `ACK ALL`.
   - **Grabador de Vídeo Integrado (`⏺ Rec Video`)**: Exporta directamente el canvas 3D en `.webm`/`.mp4` a 30/60 FPS con consumo mínimo de batería.

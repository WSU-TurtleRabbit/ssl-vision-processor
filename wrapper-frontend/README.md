# wrapper-frontend

Browser UI for the vision-processor wrapper. Svelte 5 + TypeScript + Vite.

Operator dashboard: connects to `wrapper_backend`'s WebSocket at `/ws`,
lists the debug images on disk via `GET /snapshots` and shows them
(refreshed once per second), and polls `GET /api/health` and
`GET /api/config` for service status and the active `vision_processor`
config.

The UI always talks to **its own origin** (`/ws`, `/api/*`, `/snapshot*`).
In development the Vite dev server proxies those paths to the backend on
`localhost:8765`; in production the backend serves the built UI itself.
Either way the browser only needs to reach one port, and it keeps working
behind an HTTPS reverse proxy such as `tailscale serve` (`wss://` is
picked automatically).

## Requirements

Node.js >= 22.13 (see `engines` in `package.json`). On the Jetson, Node is
a user-local install under `~/.local/node` (no sudo needed); put it on
`PATH` first:

```
export PATH=$HOME/.local/node/bin:$PATH
node --version
```

## Run (development)

Two terminals from the repo root:

```
# terminal 1 (the wrapper backend on :8765)
./start_wrapper.sh

# terminal 2 (the Vite dev server on :5173 with HMR)
cd wrapper-frontend
npm install
npm run dev
```

`npm run dev` listens on all interfaces (`server.host: true`), so open
`http://<jetson-ip>:5173` from another machine (or
<http://localhost:5173> locally). Only port 5173 has to be reachable:
`/ws`, `/api`, `/snapshots` and `/snapshot/` are proxied to the backend
(see `vite.config.ts`). To proxy to a backend on another port, set
`WRAPPER_BACKEND`, e.g. `WRAPPER_BACKEND=http://localhost:8795 npm run dev`.

## Architecture

One page that fits 1280×720 without scrolling: a 44 px status strip (camera
name with inline rename, ● live-data / vision + fps / Pi / calibrated /
game-controller chips, fps · ms/frame · pkt/s with tooltips, Refresh, log
console, Help, 🚨 Panic, Receipt, theme), the camera view on the left (icon
mode buttons on the image, calibration chip, view strip raw · flat · gradient
· blob beneath) and task tabs on the right (remembered in `localStorage`):

- **Live** — performance numbers + sparkline, then detections (Team/ID · X ·
  Y · Angle° · Conf, balls beneath, image px as tooltip; keyed rows).
- **Cameras** — one block per camera: name, Pi status and the latest Pi
  error line (current camera run only), Start capture / Stop capture (two-step)
  / Restart camera / Show log, "Raw stream ↗" (the direct Pi feed in a new tab,
  only while vision_processor is stopped — the Pi allows one viewer), camera
  input text, calibration state with the corners labelled 1·−x −y … 4·+x −y
  and the lens k2·pp, Set corners / Lens / Recalibrate, solved camera model.
- **Calibrate** — field geometry one-liner + "Edit…" (modal; Save & exit
  publishes and recalibrates), orientation of the saved corners (Rotate 180°,
  another corner → origin), colours (Auto-calibrate | manual: colour-picker
  swatch or "r, g, b"/#rrggbb text in normal RGB, converted to dRGB on save,
  stored dRGB shown as a hint, ↺ reset to the upstream default, Pick from the
  image).
- **System** — cameras summary with "Scan network…" (scan results, Use this
  camera, token forms), processing: vision_processor (state, status line,
  current-run warning, Start/Stop/Restart, log), wrapper backend (uptime, logs),
  game controller (receiving on the referee multicast: stage, command, teams,
  age; process running/pid).

Full views: Help (`#/help/<doc>`, lazy chunk), Logs (`#/logs/<file>`), Receipt
(`#/receipt`, printable black-on-white, "Download JSON" = `/api/receipt?download=1`,
lazy chunk). The log console is a right-edge drawer (` key). Static config
details (thresholds, network addresses) only appear in the receipt.

Lightweight by design: one WebSocket; detection frames are applied at most once
per animation frame; polling is 2 s for health/calibration/metrics, 3 s for
the config, 5 s for the snapshot list, 1 s for the image and colours; keyed
table rows; marked/DOMPurify/mermaid stay in lazy chunks.

### Files

- `src/App.svelte` — routing, theme, polling, derived state, status strip,
  layout, modals, toast.
- `src/lib/CameraView.svelte` — image + overlay canvas (sized to 16:9 inside
  the stage), icon mode buttons, view strip; hosts the corners / lens modes
  (their banner overlays the frozen snapshot; "help ?" expands the long text).
- `src/lib/LiveTab.svelte`, `CamerasTab.svelte`, `CalibrateTab.svelte`,
  `SystemTab.svelte` — the tabs described above.
- `src/lib/overlay.ts` — the camera overlay (field outline/markings through
  the published calibration incl. lens distortion, +x/+y axes, goal −x/+x,
  origin corner, labelled corners, lens lines, live robots/balls).
- `src/lib/FieldCorners.svelte` / `LensCorrection.svelte` + `editHistory.ts`
  / `ShortcutLegend.svelte` — the point-editing modes with undo/redo and the
  shortcuts (Ctrl+Z / Ctrl+Y, Enter, Esc, Backspace, Shift+click, Ctrl+click,
  drag, arrows; ignored while typing). Corners mode has the orientation bar.
- `src/lib/GeometryEditor.svelte` — field dimensions (modal) with live
  preview and the "refine with field lines" switch.
- `src/lib/ColorPanel.svelte` — colours (see Calibrate above).
- `src/lib/CameraScan.svelte`, `CameraName.svelte`, `PerformancePanel.svelte`,
  `LogDrawer.svelte`, `LogsView.svelte`, `HelpView.svelte`, `ReceiptView.svelte`,
  `Icon.svelte` (inline SVG icons), `health.ts` (API types + helpers),
  `wrapper-bus.ts` (the WebSocket client: `connectionState`, `topic()`,
  `reconnect()`, `messageCount()`).

The WS wire format mirrors `wrapper_backend/websocket.py`'s envelope:

```jsonc
// client -> server
{ "action": "subscribe",   "topic": "wrapper_packet.out" }
{ "action": "unsubscribe", "topic": "wrapper_packet.out" }
// server -> client
{ "topic": "wrapper_packet.out", "data": { ... } }
{ "error": "unknown topic", "topic": "..." }
```

Snapshot endpoints are plain HTTP: `GET /snapshots` returns the list of
available `{cam_id, view}` entries as JSON; `GET /snapshot/<cam_id>/<view>`
returns exactly `img/<cam_id>.<view>.{jpg,jpeg,png}` (or 404 if missing).

## Scripts

```
npm run dev           # Vite dev server with HMR
npm run build         # production build to dist/
npm run preview       # serve the production build locally
npm run check         # svelte-check + tsc (type-check everything)
npm run lint          # eslint over src/
npm run format        # prettier --write .
npm run format:check  # prettier --check . (CI-style)
```

`npm run check` is the rough analogue of `mypy` on the Python side.
`npm run lint` + `npm run format` together cover what `ruff` does for
Python. TypeScript is configured strict (`strict`,
`noUncheckedIndexedAccess`, `noImplicitOverride`,
`noPropertyAccessFromIndexSignature`, `noImplicitReturns`,
`noFallthroughCasesInSwitch`).

## Production serving

`npm run build` writes `dist/`. The wrapper backend already serves it on
the same port as the API: `GET /` returns `dist/index.html` and
`/assets/*` is resolved per request, so a rebuild is picked up without
restarting the backend. Open `http://<jetson-ip>:8765/` — no dev server
needed. (Override the directory with `--frontend-dir`.)

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

- `src/lib/wrapper-bus.ts` — single `WebSocket` client. Exposes
  `connectionState` (Svelte store) and `topic<T>(name)` (returns a
  store of the latest message). Subscribes to a topic lazily on first
  reader, unsubscribes when the last reader goes away. Reconnects on
  close with exponential backoff (1s → 30s).
- `src/App.svelte` — operator UI: connection badge, snapshot viewer
  (list from `GET /snapshots` every 5 s, image refreshed once per second
  via a cache-busting `?t=<ms>` query) with a projected field-geometry
  overlay (field from the saved corners, numbered corner markers, live
  robots/balls at their image pixels, redrawn on every detection frame),
  detection table, service status and the active config
  (`GET /api/config`). Detections older than 1 s are dropped, and a state
  line explains an empty table ("vision_processor not running" /
  "running, not calibrated → set field corners" / "recalibrating..." /
  "no detection frames" / "N robots, M balls (live)"). The calibration
  state is polled from `GET /api/calibration` every second.
- `src/lib/ServicesPanel.svelte` — services from `GET /api/health`: Pi
  camera (only for an http(s) `camera.path`; online/offline, streaming,
  client, size, fps), `vision_processor` (running/stopped, pid, managed vs
  started by hand, uptime, restarts, last detection age; Start / Stop /
  Restart via `POST /api/vision/*`, "Show log" polls `GET /api/vision/log`
  every second), backend uptime, field calibration state. Response types
  live in `src/lib/health.ts`.
- `src/lib/FieldCorners.svelte` — "Set field corners" mode (field
  calibration without field lines). Freezes one raw snapshot, collects 4
  clicks (numbered markers, polygon preview, drag a marker to adjust,
  Undo / Reset / Cancel, Esc cancels), maps CSS px to image px, renumbers
  2..4 clockwise from corner 1 and checks convexity. "I click: outer edge
  incl. boundary" (default; the derived field corners are drawn dashed,
  same homography as the backend, lens distortion ignored) or "the field
  corners directly". Sizes come from `GET /api/calibration` (the published
  geometry). "Save corners" posts to `POST /api/calibration/corners`; the
  viewer then shows "recalibrating..." until the backend has a calibration
  again. Corners saved in the config are pre-filled.
- `src/lib/LensCorrection.svelte` — "Lens correction" mode: click points
  along straight seams/edges (Next line / Enter, Undo point, Delete line,
  Clear all, Esc), >= 3 lines of >= 4 points, posts to
  `/api/calibration/lens`; "Remove lens correction" sends DELETE. The overlay
  draws the saved lines (purple) and the calibrated model's straight-line
  reprojection (dashed); the viewer chip shows k2 and the principal point.
- `src/lib/GeometryEditor.svelte` — field geometry editor with a live
  preview (`/api/geometry`), "Recalibrate now" after a size change, and the
  "Refine with field lines" switch (`/api/calibration/refinement`).
- `src/lib/PerformancePanel.svelte` — per-camera rate / processing / network
  latency / robots / balls from `/api/metrics` (sparkline of the rate) plus
  this page's detection updates/s and WebSocket messages/s.
- `src/lib/HelpView.svelte` — Help (`#/help/<doc>`), loaded lazily: renders
  `/api/docs/<name>` with marked + DOMPurify, GitHub-style heading ids,
  in-app doc links, copy buttons on code blocks, mermaid diagrams (mermaid is
  a separate lazy chunk, themed light/dark). Navigation mirrors
  `docs/README.md`. The red "🚨 Panic" header button opens `panic.md`.
- `src/lib/editHistory.ts` + `ShortcutLegend.svelte` — undo/redo stack and
  the shortcuts shared by the corners and lens modes: Ctrl+Z / Ctrl+Y
  (Ctrl+Shift+Z), Enter (lens: next line; save when complete), Esc,
  Backspace/Delete (remove last point), Shift+click (new line, lens),
  Ctrl+click (delete nearest point), drag to move, arrow keys nudge the
  selected point (Shift: 5 px). Shortcuts are ignored while typing in a
  field; the legend is shown in both mode banners and in Help → Shortcuts.
  Corners mode has an Orientation bar (Rotate 180°, choose the origin
  corner, warning when field_length would map onto the short side) and the
  overlay draws the +x/+y axes, "goal −x/+x" and the origin corner.
- `src/lib/CameraScan.svelte` — "Scan for cameras..." (Services): lists
  camstream Pis from `GET /api/cameras/scan`, "Use this camera" (with or
  without a vision_processor restart), Restart/Open/Close for cameras with a
  saved token, inline "Add token" (`POST /api/cameras/token`), and a range
  override (`?subnet=`). The Pi camera row has Start capture (open + start
  vision_processor), Stop capture (two-step: stop vision_processor + close)
  and Restart camera. Help → Cameras explains it.
- Field dimensions: "Edit field dimensions" opens `GeometryEditor` as a modal
  (Esc cancels); "Save & exit" writes + publishes the geometry, triggers
  `POST /api/calibration/recalibrate` and shows a toast.
- Colours: the Reference column is editable (three numbers, Enter saves via
  `POST /api/colors/save`, Esc reverts) next to Pick and Auto-calibrate.
- `src/lib/overlay.ts` — the camera-image overlay (field outline/markings
  through the published calibration incl. lens distortion, +x/+y axes,
  "goal −x/+x", origin corner, saved corners labelled 1·−x −y … 4·+x −y,
  lens lines, live robots/balls), shared by the operator page and the
  pop-out window.
- `src/lib/PopoutView.svelte` — `/popout?cam=0&view=raw` (header "Pop out"
  opens it in its own window): only the camera image on a dark background
  with view selector, overlay on/off, snapshot rate 1/2/5/10 Hz
  (`POST /api/config/debug-interval`) and a "Raw Pi stream" link that is only
  offered while vision_processor is stopped (the Pi has a single viewer).
- `src/lib/LogDrawer.svelte` + `LogsView.svelte` — the log console: a
  right-edge drawer (header "Logs" or the `key, 380 px, state remembered)
with a merged live tail of`vision_processor.log`, `pi-camera-<host>.log`and`wrapper_backend.log`(source checkboxes, WARN amber / error red,
pause, clear, copy, "open file");`#/logs/<file>`is the single-file tail
view (auto-refresh 2 s, pause, copy, download via`?raw=1`) and shows the
  folder on the Jetson. The Services Pi camera row has "Show log" and shows
  the latest failed/error line of the Pi (current camera run only).
- Header: the mode buttons (corners, lens, field dimensions) are icon-only
  with tooltips (inline SVG in `Icon.svelte`); the connection badge reads
  "Live data: connected / reconnecting… / disconnected"; FPS and Frame chips
  explain what they measure in their tooltips.
- `src/lib/CameraName.svelte` — camera name ("Camera <id>" when unset) with
  inline rename (✎, Enter saves via `POST /api/camera/name`, Esc cancels);
  shown in the header, the Services Pi row, Performance and the
  corners/lens/colour modes.
- Header: ⟳ Refresh (re-fetches everything, reconnects the WebSocket, shows
  the time), theme Light/Dark/System (CSS variables in `App.svelte`, choice
  remembered in `localStorage`), ? Help, 🚨 Panic. The Services Pi camera row
  has a _Restart camera_ button (`POST /api/camera/restart`; disabled with a
  tooltip when no token is configured).
- Colours: "Auto-calibrate colours" (`/api/colors/auto`) with progress and the
  saved/skipped result. Detections and Colours sit side by side on wide
  screens.
- `src/lib/ColorPanel.svelte` — colour calibration panel. Polls
  `GET /api/colors` every second and shows learned vs reference colour
  per blob class (values are brightness-free dRGB; the swatches are hue
  previews, `clamp(128 + 2 * (d - 127.5))` per channel). "Save learned
  colours as reference" (two-step confirm) posts `{"from": "learned"}` to
  `POST /api/colors/save`. "Pick" samples the raw snapshot instead: click
  the colour in the image, the browser averages RGB over a 3 px radius
  (image pixels), converts it to dRGB with `kernel/resampling.cl`'s
  integer formula and posts it on "Apply". Esc cancels.
- `src/main.ts` — mounts `App` into `#app`.

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

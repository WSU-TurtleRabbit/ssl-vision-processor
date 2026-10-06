# wrapper_backend

Python application that owns the field geometry and (eventually) the browser-based calibration UI for `vision_processor`. Replaces `python/geom_publisher.py` with a modular asyncio base that other modules — web server, WebSocket bridge, `vision_processor` supervisor — can plug into without touching existing code.

## Run

```
./start_wrapper.sh                          # uses geometry-wrapper-lab-divB.yml
./start_wrapper.sh geometry-divA.yml        # different config
./start_wrapper.sh geometry-divB.yml --vision-port 10100
./start_wrapper.sh geometry-divB.yml --port 9000
./start_wrapper.sh geometry-wrapper-lab-divB.yml --vision-config config-pi-cam.yml
./start_wrapper.sh geometry-wrapper-lab-divB.yml --vision-config config-pi-cam.yml --start-vision
```

`vision_processor` supervision flags:

- `--start-vision` — start `vision_processor` together with the backend
  (otherwise use *Start* in the UI / `POST /api/vision/start`). The managed
  child is stopped when the backend exits (SIGTERM/Ctrl+C; it also gets
  SIGTERM via `PR_SET_PDEATHSIG` if the backend is killed hard).
- `--vision-binary PATH` — default `<repo>/build/vision_processor`.
- `--camera-token-file PATH` — Pi camera remote-control token (default:
  `<repo>/.camera-token` if it exists, git-ignored; whitespace stripped;
  fallback: env `PI_CAMERA_TOKEN`). Never logged. Set it up as in
  `pi_camera/README.md` section 6.
- `--vision-cwd DIR` — default: the directory of `--vision-config`.
  `vision_processor` resolves `img/` and `bot_heights_file` relative to its
  cwd, and the backend reads snapshots from `<vision-config dir>/img`, so both
  stay consistent (for the repo's configs that is the repo root, as before).
  It is started as `<binary> <absolute vision-config path>`.

Everything after the geometry file is passed through to `python -m wrapper_backend`.
`--vision-config` names the `vision_processor` config shown in the UI; its directory's
`img/` is where snapshots are read from. The geometry file must be in the wrapper format
(`optional_field_lines:`); the legacy `geom_publisher.py` files such as
`geometry-lab-divB.yml` (`default_lines:`) fail with `KeyError: 'optional_field_lines'`.

The wrapper loads the given `geometry-*.yml`, broadcasts an `SSL_WrapperPacket` at 1 Hz on the multicast bus (default `224.5.23.2:10006`), and absorbs incoming per-camera calibrations into its in-memory state. Logs go to stderr.

## Architecture

The wrapper is a single asyncio process listening on `:8765`. Inside,
modules don't call each other directly; they talk through an in-process
pub/sub bus. From the outside, this is what it does:

- Listens for SSL vision multicast traffic on UDP and parses it.
- Folds incoming per-camera calibrations into one merged geometry state.
- Re-broadcasts that merged state back onto the multicast group at 1 Hz.
- Exposes the same internal topics to the browser frontend over WebSocket.
- Serves the debug images that the C++ side writes to disk.

### Files

Each file is one module. In rough "outside-in" order:

- **`multicast.py`** — the UDP I/O layer. Receives `SSL_WrapperPacket`s
  from the multicast group, splits them into geometry / detection topics,
  and sends our own merged packets back out.
- **`geometry.py`** — the brains. Holds one in-memory `SSL_WrapperPacket`,
  replaces or appends per-camera calibrations as they arrive, and emits
  the current state once a second.
- **`websocket.py`** — the WebSocket endpoint at `/ws`. Lets browser
  clients subscribe to any bus topic and receive frames as JSON. Client
  side lives in `wrapper-frontend/src/lib/wrapper-bus.ts`.
- **`snapshot.py`** — the debug-image endpoints. `GET /snapshots`
  returns the list of `{cam_id, view}` entries currently on disk;
  `GET /snapshot/<cam_id>/<view>` serves the JPEG/PNG itself. Files are
  written by the C++ `SnapshotWriter` into `img/`, which is hardcoded on
  both sides (relative to each process's cwd).
- **`colors.py`** — colour calibration. `GET /api/colors?cam_id=N`
  returns the learned/reference colours `vision_processor` publishes to
  `img/<N>.colors.json` (plus `age_s`). `POST /api/colors/save` with
  `{"colors": {"pink": [r, g, b]}}` or `{"from": "learned"}` rewrites
  those keys in the `color:` section of `--vision-config` (comments and
  everything else untouched, atomic write); `vision_processor` picks the
  change up within ~0.5 s.
- **`supervisor.py`** — runs `vision_processor`. `POST /api/vision/start`
  (409 while an instance with the same config runs), `POST /api/vision/stop`
  (SIGTERM, SIGKILL after 5 s), `POST /api/vision/restart`, `GET
  /api/vision/log` (last 200 lines of its stdout+stderr plus supervisor
  notes). An instance started by hand with the same config (found via
  `/proc`, same user only) can still be stopped/restarted. If the managed
  child dies while it should run it is retried forever with back-off (2 s,
  5 s, then every 10 s). For an http(s) camera the Pi's `/status` is probed
  (1 s timeout) before each retry and vision_processor is only started once
  the Pi answers; `/api/health` shows `state` = "waiting for camera (Pi not
  reachable)" or "restarting (N attempts)" meanwhile (`rapid_failures` is
  informational only). *Stop* ends the retrying.
- **`calibration.py`** — field calibration from 4 clicked corners (no field
  lines needed). `GET /api/calibration` returns the state
  (`calibrated` / `recalibrating` / `not_calibrated`), the saved corners and
  the field size/boundary from the published geometry. `POST
  /api/calibration/corners` with `{"cam_id": 0, "corners": [[x, y] x 4],
  "mode": "outer"}` validates the points (inside the raw image, no
  duplicates, convex), orders them clockwise from point 1 (the field's
  (-x, -y) side), and in `outer` mode (the default: the clicks are the outer
  edge incl. boundary, e.g. the foam mats) maps the field corners through a
  homography. It writes `geometry.line_corners` (field corners),
  `geometry.outer_line_corners` (the clicks) and `geometry.refinement:
  false` into `--vision-config`, then stops `vision_processor`, clears the
  calibrations in the in-memory geometry, broadcasts, and starts it again so
  it recalibrates.
- **`calibration.py` (lens, refinement, recalibrate)** — `POST
  /api/calibration/lens {"cam_id", "lines": [[[x, y], ...], ...]}` (>= 3 lines
  of >= 4 in-image points) writes `geometry.distortion_lines` (one flow list
  per line, 0.1 px); with stored outer corners it then writes the clicks
  directly as `line_corners` + `line_corners_include_boundary: true` (the
  lens model handles them), otherwise nothing else. `DELETE
  /api/calibration/lens` removes them and returns to the homography rule.
  `POST /api/calibration/recalibrate` (clear calibs + restart),
  `POST /api/calibration/refinement {"enabled", "restart"}` sets
  `geometry.refinement`. All use the same clear-calibs + restart flow.
- **`fieldgeometry.py`** — `GET/POST /api/geometry`: edit the geometry file
  the backend runs with (field sizes, boundaries, goal, penalty area, centre
  circle, line thickness, `optional_field_lines`). Validated, written
  comment-preserving, parsed strictly and swapped into the in-memory geometry,
  which is broadcast at once (calibs kept, no restart). `size_changed` in the
  reply means the camera should be recalibrated.
- **`colors.py` (auto)** — `POST /api/colors/auto[?cam_id=N]` samples
  `colors.json` 10x every 0.5 s while watching `detection.in`; fails unless
  robots were seen in >= 50 % of the windows; saves the per-colour median for
  colours with evidence (blue/yellow: that team's robots, green/pink: any
  robot, orange: a ball, field: any frame) in >= 50 % of windows and a
  per-channel spread <= 12, lists the others as skipped. `GET
  /api/colors/auto` polls the job.
- **`metrics.py`** — `GET /api/metrics`: per `camera_id`, rolling 5 s:
  detection rate, `t_sent - t_capture` (processing), receive time - `t_sent`
  (only with synchronised clocks, else null), robots/balls per frame.
  Note: for video-file input the C++ uses the file's clock, so processing is
  0 and receive latency null.
- **`docs.py`** — `GET /api/docs` and `GET /api/docs/<name>`: markdown of a
  fixed whitelist (`docs/*.md` basenames, `pi-camera` = `pi_camera/README.md`);
  names are looked up, never joined into paths.
- **`camera.py`** — `POST /api/camera/{restart,close,open,shutdown}` proxies
  to the Pi's `/control/<command>` (header `X-Camstream-Token`, 3 s timeout)
  and returns the Pi's JSON + status; 503 when the camera isn't http(s) or no
  token is configured. `close` stops the supervised vision_processor first;
  `open` does not restart it. The UI only offers *Restart camera*; close/open/
  shutdown are API-only. `POST /api/camera/name {"name"}` sets `camera.name`
  in the vision config (1-40 of `A-Za-z0-9 _-`, `""` removes it; the C++
  ignores the key). `/api/health` reports `camera_name`, and the `pi_camera`
  entry includes `closed`/`control` from the Pi's `/status`.
- **`yamledit.py`** — the comment-preserving, line-based YAML section editor
  (with atomic write) used by `colors.py` and `calibration.py`.
- **`operator.py`** — `GET /api/config`, `GET /api/health` (services: Pi
  camera `/status` when `camera.path` is an http(s) URL, `vision_processor`,
  backend, field calibration) and the built frontend.
- **`bus.py`** — the pub/sub bus everything else talks through. Each
  subscriber gets its own size-1 queue, so slow readers see only the
  latest message and never block publishers.
- **`__main__.py`** — entry point. Parses CLI args, sets up logging, and
  wires all the modules above onto one aiohttp app.

### Topics on the bus

| Topic | Payload | Written by | Read by |
|---|---|---|---|
| `geometry.in` | `SSL_GeometryData` | `multicast.py` (inbound) | `geometry.py` |
| `detection.in` | `SSL_DetectionFrame` | `multicast.py` (inbound) | (none yet) |
| `wrapper_packet.out` | serialised `SSL_WrapperPacket` bytes | `geometry.py` | `multicast.py` (outbound), `websocket.py` |

## Development

Project is managed by [uv](https://astral.sh/uv). On a fresh clone:

```
uv sync                       # install deps + dev deps
uv run pre-commit install     # enable git hooks
```

Day-to-day:

```
uv run mypy wrapper_backend/
uv run ruff check wrapper_backend/
uv run ruff format wrapper_backend/
```

The pre-commit hook runs ruff (`--fix` + format) scoped to `wrapper_backend/` and mypy on every commit. Existing `python/` scripts are not subject to the new tooling and keep running against the system Python.

Type stubs for `protobuf` and `pyyaml` are dev deps. The two `# type: ignore[assignment]` on `SSL_FieldShapeType.Value(...)` calls in `geometry.py` work around an upstream `types-protobuf` stub mismatch (`Value()` is typed as returning `int` while proto enum fields are typed as the enum).

## Field calibration without field lines

1. Run with the field size and boundary of the clicked rectangle in the
   geometry file (the lab: mats 3030 x 1830 mm = field 2730 x 1530 mm + 150 mm
   boundary).
2. In the UI: *Set field corners*, click the four outer mat corners
   (corner 1 on the (-x, -y) side, then clockwise), *Save corners*.
3. The UI shows "recalibrating..." until `vision_processor` sends the new
   calibration, then the overlay shows the field outline from the corners.

Limitations: the corners are fitted without lens distortion (the corner
homography is flat and the camera model's distortion is only estimated from
whatever line pixels `vision_processor` happens to find, which can be
off); `error_rate` in `img/*.calib.json` is meaningless without field lines.
If the clicked rectangle does not really have the published size, the
mm-per-pixel scale is wrong and robots are not recognised at all.

## Behavioural deltas vs `python/geom_publisher.py`

- **Strict YAML parsing.** `ParseDict` runs without `ignore_unknown_fields`, so a typo in `geometry.yml` raises at startup instead of being silently dropped.
- **`default_lines:` renamed to `optional_field_lines:`** with all four toggles required. See `wrapper_backend/CLAUDE.md` for details.
- **Dash-form CLI flags only** (`--vision-ip`, `--vision-port`, `--host`, `--port`). The underscore form is dropped.

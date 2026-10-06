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
- `--camera-tokens-file PATH` — YAML `host: token` lines for *other* Pi
  cameras found by the scan (default `<repo>/.camera-tokens`, git-ignored,
  written with mode 600 by "Add token"). Never logged or returned.
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
  Several cameras: `GET /api/cameras/scan[?subnet=CIDR]` probes the LAN /24
  of the interface routing to the configured camera host (fallback: all
  non-loopback IPv4 /24s, point-to-point /32s skipped, at most 1024 hosts)
  for camstream `/status` on port 8080 and the configured camera's port
  (0.5 s timeout, 64 in flight) and returns `{host, port, streaming, client,
  closed, control, size, fps, name_if_known, has_token, is_current}`;
  `subnet` (/24 .. /32) overrides the range, e.g. `127.0.0.1/32` to find a
  fake camera on this machine. `POST /api/cameras/token {host, token}` stores
  a token in the tokens file; `POST /api/cameras/use {host, port, restart}`
  writes `camera.path` (`http://host:port/stream`) and optionally restarts
  vision_processor; `POST /api/cameras/control {host, port, command}` sends
  restart/open/close/shutdown to any camera with a known token (the single
  `.camera-token` applies to the configured camera only).
- **`logs.py`** — `<repo>/logs/` (git-ignored): `wrapper_backend.log` (the
  backend's own logging, also on stderr), `vision_processor.log` (stdout +
  stderr of the supervised vision_processor, `---- started <time> ----` per
  run) and `pi-camera-<host>.log` (the Pi service's `GET /log?since=N`,
  polled every 3 s while `camera.path` is http(s); lines are
  `<ISO time> <text>`). Each file rotates to `.1` above 5 MB. `GET /api/logs`
  lists them, `GET /api/logs/<name>?tail=200` returns the last lines,
  `?raw=1` the file as text/plain; only basenames present in the directory
  are served. `/api/health` carries `logs_dir`, the `pi_camera` entry
  `log_file` / `last_error` (newest failed/error line, only if newer than
  the last "camera ON") and `vision_processor.last_warning` is only reported
  for the current run and while newer than its last status line.
- `POST /api/config/debug-interval {"interval_ms": N}` — sets
  `debug.debug_stream_interval_ms` (snapshot refresh rate; 0 = off) in the
  vision config, comment-preserving; vision_processor reloads it live.
- **`gamecontroller.py`** — joins the referee multicast (`gc_ip:gc_port` from
  the vision config, default 224.5.23.1:10003), parses `Referee` packets and
  reports stage, command, team names/scores and the packet age in
  `/api/health` → `services.game_controller` (`running` = a packet in the last
  5 s; `process_running`/`pid` if an `ssl-game-controller` process exists).
- `GET /api/receipt[?download=1]` — one JSON with everything (generated_at,
  git commit, vision build device line, geometry, cameras with Pi status /
  calibration / lens / solved model, colours, services incl. game controller,
  last errors, performance, 20-line log tails); `download=1` serves it as an
  attachment. The UI's Receipt view prints it.
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

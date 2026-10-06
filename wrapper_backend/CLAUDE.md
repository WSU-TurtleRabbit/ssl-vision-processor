# CLAUDE.md — wrapper_backend

Async Python application replacing `python/geom_publisher.py`. Owned by uv; managed via `pyproject.toml` at repo root.

## Commands

All run from repo root.

- Run: `./start_wrapper.sh [geometry-X.yml]` (or `uv run python -m wrapper_backend geometry.yml`)
- Type check: `uv run mypy wrapper_backend/`
- Lint: `uv run ruff check wrapper_backend/`
- Format: `uv run ruff format wrapper_backend/`
- Pre-commit (manual run on wrapper files): `uv run pre-commit run --files wrapper_backend/*.py`

## Architecture

An `aiohttp.web.Application` owns the listener (default `:8765`). Each feature module exposes `register(http_app, ...)` which adds its routes and any cleanup hooks. `__main__.py` builds the app, calls each `register`, runs an `AppRunner` + `TCPSite`, and then drives the geometry publisher loop. Modules:

- `bus.py` — every `subscribe(topic)` returns its own size-1 `asyncio.Queue`. `publish` drains-then-puts; slow subscribers see only the latest value.
- `multicast.py` — `asyncio.DatagramProtocol` UDP. Inbound parses `SSL_WrapperPacket` and demuxes into `geometry.in` / `detection.in`. Outbound subscribes to `wrapper_packet.out` (bytes) and `sendto`s.
- `geometry.py` — owns `geometry.yml` + in-memory `SSL_WrapperPacket`. Two tasks: `_absorb_loop` (replace-or-append calibs) and `_publish_loop` (1 Hz emit). Both share `self._wrapper`; no locks because asyncio doesn't preempt between non-`await` statements. Publisher serialises to bytes before publishing so the snapshot is locked-in before the multicast adapter awaits.
- `websocket.py` — aiohttp WS at `/ws`. Lazy per-topic bus subscription: a channel's bus-reader task starts when the first client joins and stops when the last leaves. Each connected client owns its own size-1 outbound queue (slow clients drop intermediate frames). Read-only for now; envelope (`{"topic": ..., "data": ...}` and `{"action": ..., "topic": ...}`) is symmetric so inbound commands can be added later without breaking the wire format. Topic-to-JSON encoders live in `_TOPIC_ENCODERS`.
- `snapshot.py` — two aiohttp routes. `GET /snapshots` walks `img/` and returns matching `<cam_id>.<view>.{jpg,png}` filenames as a JSON list of `{cam_id, view}` (legacy / non-matching files are filtered by a regex). `GET /snapshot/{cam_id}/{view}` validates both params against the same regex, looks up exactly `img/{cam_id}.{view}.{jpg,jpeg,png}` (no globbing, so `pixels` never matches `pixels.refined`) and streams the most-recently-modified match via `web.FileResponse` (sendfile-backed, 404 when nothing matches). The C++ `SnapshotWriter` writes those files via `tmp → rename`, so reads are torn-frame-free without coordination.
- `yamledit.py` — `update_section(text, section, values, drop_commented=, remove=, require_flow_list=)` line-based editor for one top-level mapping (inline values keep trailing comments; block sequences are written as `  key:` + `    - item`; a commented `#key:` placeholder block is replaced in place) and `atomic_write`. Callers re-parse with `yaml.safe_load` and refuse to write unless the values round-trip.
- `supervisor.py` — `VisionSupervisor`: spawns `<--vision-binary> <abs vision-config>` with cwd `--vision-cwd` (default: the config's dir), own session + `PR_SET_PDEATHSIG`, stdout+stderr into a 200-line deque. `want_running` drives crash restarts: `_retry_loop` backs off 2 s / 5 s / 10 s forever, probing the camera's `/status` first (`camera_status_url`, injected by `__main__`; `None` for non-http cameras = just start) so a crash-looping vision_processor never hammers an absent Pi; `retry_state`/`retry_attempts` feed `status()["state"]`. Never "gives up": only *Stop* clears `want_running`. `/proc` scan (`scan_vision_processors`) resolves argv[1] against `/proc/<pid>/cwd`; instances with the same resolved config not started by us are "external": start refuses (409), stop/restart SIGTERM them (same uid only). `restart(between=fn)` runs `fn` after the old process is gone. Shutdown (on_cleanup) stops only the managed child.
- `calibration.py` — `FieldCalibration`: `/api/calibration` + `/api/calibration/corners` (see README). Order of a save: write config -> supervisor.restart(between=clear calibs + broadcast). `vision_processor` reuses any calib for its camera it receives and only recalibrates when the received geometry has **no calib at all** (`calib_size() == 0` in `Perspective::geometryCheck`), so *all* cameras' calibs are cleared (others recalibrate on their own; the response lists them). `Geometry.clear_calibs` ignores incoming calibs for those cameras for 1.5 s, because our own multicast echo of the last packet would otherwise re-add the old calib. `calib.json` is named after the camera *path* (`img/<path with / -> _>.calib.json`), so it is found by globbing and matching `camera_id`.
- `fieldgeometry.py` — geometry editor; `Geometry.replace(wrapper)` swaps the packet keeping absorbed calibs and publishes; `geometry_from_config` is the strict parser shared with `load_geometry`.
- `metrics.py`, `docs.py`, `camera.py` — see README. `camera.py` registers `/api/camera/name` before the `/api/camera/{command}` proxy route (order matters). The token is never logged or returned.
- `colors.py` — colour calibration (plus `/api/colors/auto`, see README). `GET /api/colors?cam_id=N` returns `img/<N>.colors.json` (written by `vision_processor` every 0.5 s via tmp → rename) plus `age_s`; 404 JSON when missing. `POST /api/colors/save[?cam_id=N]` takes exactly `{"colors": {name: [r, g, b]}}` (names ⊆ orange/field/yellow/blue/green/pink, ints 0..255) or `{"from": "learned"}` (all six from `colors.json`); anything else is 400. It edits the top-level `color:` mapping of `--vision-config` **line by line** (replace `  <name>: [...]` keeping a trailing comment, else append at the end of the section, else append a `color:` section), re-parses the result with `yaml.safe_load` and refuses to write unless the values round-trip, then writes atomically (tmp in same dir + `os.replace`, mode preserved). `vision_processor` live-reloads the section on mtime change. Values are dRGB (brightness-free, see `kernel/resampling.cl`), not RGB.

Topics: `geometry.in`, `detection.in` (inbound demuxed), `wrapper_packet.out` (outbound bytes).

## Gotchas

- `ParseDict` runs strict (no `ignore_unknown_fields`). A typo in `geometry.yml` raises at startup. Don't add forgiveness.
- `optional_field_lines:` controls the SSL markings that may be absent on lab/exhibition carpets: `goal2goal` (CenterLine), `halfway` (HalfwayLine), `centercircle` (CenterCircle arc), `penalty` (the six penalty-area stretches). Touchlines and goal lines are always emitted. The block and all four keys are required — `load_geometry` pops the block before `ParseDict` so strict parse still rejects typos elsewhere, and missing keys raise `KeyError` rather than silently defaulting.
- Two `# type: ignore[assignment]` on `SSL_FieldShapeType.Value(...)` calls are unavoidable: `types-protobuf` types `Value()` as `int` while proto enum fields are typed as the enum.
- Generated proto bindings are NOT committed. `wrapper_backend/__init__.py` runs `protoc` on first import if the bindings are missing, older than `proto/*.proto`, or fail to import (checked in a subprocess). Compiler preference: `<repo>/.protoc/bin/protoc` (3.20, emits `.pyi`), then `grpc_tools`, then `protoc` on PATH. System protoc 3.12 output does NOT import under protobuf 7.x; if the fresh bindings still fail to import, `wrapper_backend/proto/` is deleted and startup aborts asking for protoc >= 3.19. It then always prepends `wrapper_backend/` to `sys.path` so `from proto.* import ...` resolves to `wrapper_backend/proto/`. mypy uses `mypy_path = "wrapper_backend"` and excludes `wrapper_backend/proto/` to mirror this.
- `python/` scripts (`geom_publisher.py`, `cam_viewer.py`, benchmarks) are NOT covered by the wrapper's tooling. They keep running on system Python; never modify them as part of wrapper work unless explicitly asked.
- Never round-trip the vision config through PyYAML to write it: the files are hand-commented and `yaml.safe_dump` would drop every comment. Extend `yamledit.update_section` instead.
- `line_corners` order matters twice in the C++: `cornerCalibration` keeps the first point and tries the clockwise-convex order of the rest, but `DetectionCorrector` uses the config order as-is ((-x,-y), (-x,+y), (+x,+y), (+x,-y)). The backend therefore always writes them clockwise on screen starting at corner 1.
- The vision config's `geometry:` section is read only at `vision_processor` startup (`reloadConfigIfChanged` only re-applies tunables), hence the restart after saving corners.
- Pre-commit hooks are scoped to `^wrapper_backend/` for ruff. Don't widen the scope without reason — would reformat all the legacy `python/` files.

## CLI flags

Dash form only: `--vision-ip`, `--vision-port`, `--host`, `--port`, `--vision-config`, `--frontend-dir`, `--vision-binary`, `--vision-cwd`, `--start-vision`. `geom_publisher.py`'s underscore form is dropped.

## Out-of-scope (planned but not yet built)

Calib persistence to `geometry.yml`. (Static serving of the built frontend already exists in `operator.py`: `/` -> `dist/index.html`, `/assets/*` resolved per request.) Each will be an additive module subscribing to the bus or registering routes — don't restructure existing modules to anticipate them.

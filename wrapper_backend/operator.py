"""Read-only operator API and static frontend hosting."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from aiohttp import web
from yarl import URL

from wrapper_backend.bus import Bus
from wrapper_backend.camera import camera_name
from wrapper_backend.logs import CameraLogPoller
from wrapper_backend.yamledit import YamlEditError, atomic_write, update_section
from wrapper_backend.calibration import FieldCalibration, config_cam_id, read_config
from wrapper_backend.supervisor import VisionSupervisor, scan_vision_processors

PI_STATUS_TIMEOUT_S = 1.0
PI_STATUS_CACHE_S = 1.5


def pi_status_url(camera_path: Any) -> str | None:
    """``<scheme>://<host>:<port>/status`` for an http(s) camera path."""
    if not isinstance(camera_path, str):
        return None
    url = URL(camera_path)
    if url.scheme not in ("http", "https") or not url.host:
        return None
    return str(url.with_path("/status").with_query(None).with_fragment(None))


async def fetch_pi_status(session: aiohttp.ClientSession, url: str) -> dict[str, Any]:
    """Query the Pi camera service (pi_camera/camstream.py) ``/status``."""
    base: dict[str, Any] = {"url": url}
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=PI_STATUS_TIMEOUT_S)
        ) as response:
            if response.status != 200:
                return {**base, "running": False, "error": f"HTTP {response.status}"}
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        return {**base, "running": False, "error": str(exc) or type(exc).__name__}
    if not isinstance(data, dict):
        return {**base, "running": False, "error": "unexpected /status reply"}
    return {
        **base,
        "running": True,
        "streaming": bool(data.get("streaming")),
        "client": data.get("client"),
        "device": data.get("device"),
        "size": data.get("size"),
        "fps": data.get("fps"),
        "closed": bool(data.get("closed")),
        "control": bool(data.get("control")),
    }


def register(
    http_app: web.Application,
    bus: Bus,
    vision_config: Path,
    geometry_config: Path,
    frontend_dir: Path,
    img_dir: Path,
    supervisor: VisionSupervisor,
    calibration: FieldCalibration,
    logs_dir: Path | None = None,
    camera_log: CameraLogPoller | None = None,
) -> None:
    started_at = time.monotonic()
    pi_cache: dict[str, Any] = {"at": 0.0, "url": None, "status": None}
    session_key = web.AppKey("operator_http_session", aiohttp.ClientSession)
    last_detection_at: float | None = None

    async def watch_detections() -> None:
        nonlocal last_detection_at
        queue = bus.subscribe("detection.in")
        try:
            while True:
                await queue.get()
                last_detection_at = time.monotonic()
        finally:
            bus.unsubscribe("detection.in", queue)

    async def start_watcher(app: web.Application) -> None:
        app[session_key] = aiohttp.ClientSession()
        app[watcher_key] = asyncio.create_task(
            watch_detections(), name="operator-detections"
        )

    async def stop_watcher(app: web.Application) -> None:
        task = app[watcher_key]
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await app[session_key].close()

    watcher_key = web.AppKey("operator_detection_watcher", asyncio.Task[None])

    async def config_handler(_: web.Request) -> web.Response:
        try:
            config = yaml.safe_load(vision_config.read_text(encoding="utf-8")) or {}
            stat = vision_config.stat()
        except (OSError, yaml.YAMLError) as exc:
            return web.json_response({"error": str(exc)}, status=500)

        return web.json_response(
            {
                "path": str(vision_config),
                "modified_at": stat.st_mtime,
                "config": config,
            }
        )

    async def pi_camera_status(app: web.Application) -> dict[str, Any] | None:
        camera = read_config(vision_config).get("camera")
        url = pi_status_url(camera.get("path") if isinstance(camera, dict) else None)
        if url is None:
            return None
        now = time.monotonic()
        if pi_cache["url"] != url or now - pi_cache["at"] > PI_STATUS_CACHE_S:
            pi_cache["status"] = await fetch_pi_status(app[session_key], url)
            pi_cache["url"] = url
            pi_cache["at"] = time.monotonic()
        status: dict[str, Any] = pi_cache["status"]
        return {**status, "pid": None}

    def vision_status() -> dict[str, Any]:
        instances = scan_vision_processors()
        status = supervisor.status()
        # A warning is only shown while it is newer than the last status line
        # of the same run (the supervisor resets it on every start).
        warning = status.get("last_warning")
        last_status = status.get("last_status")
        if warning and last_status and last_status["at"] > warning["at"]:
            status["last_warning"] = None
        return {
            **status,
            "config": status["config_path"],
            # Every vision_processor on this host, whatever its config.
            "instances": [{"pid": i["pid"], "config": i["config"]} for i in instances],
            # Detections may also come from a vision_processor on another host.
            "last_detection_age_s": (
                round(time.monotonic() - last_detection_at, 1)
                if last_detection_at is not None
                else None
            ),
        }

    async def health_handler(request: web.Request) -> web.Response:
        images = [
            path
            for path in img_dir.glob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]
        latest_image = max((path.stat().st_mtime for path in images), default=0.0)
        uptime = round(time.monotonic() - started_at, 1)
        cam_id = config_cam_id(vision_config)
        calib = calibration.status(cam_id)
        services: dict[str, Any] = {}
        pi_camera = await pi_camera_status(request.app)
        if pi_camera is not None:
            if camera_log is not None:
                err = camera_log.last_error
                pi_camera["last_error"] = (
                    err if err and err["at"] >= camera_log.last_on_at else None
                )
                pi_camera["last_line"] = camera_log.last_line
                pi_camera["log_file"] = (
                    camera_log.file.name if camera_log.file is not None else None
                )
            services["pi_camera"] = pi_camera
        services["vision_processor"] = vision_status()
        name = camera_name(vision_config)
        for entry in ("pi_camera", "vision_processor"):
            if entry in services:
                services[entry]["camera_name"] = name
        services["wrapper_backend"] = {
            "running": True,
            "pid": os.getpid(),
            "uptime_s": uptime,
        }
        services["field_calibration"] = {
            "running": calib["calibrated"],
            "pid": None,
            "cam_id": cam_id,
            "state": calib["state"],
            "calibrated_cameras": calib["calibrated_cameras"],
            "has_corners": isinstance(calib["corners"], list)
            and len(calib["corners"]) == 4,
        }
        return web.json_response(
            {
                "status": "ok",
                "uptime_s": uptime,
                "vision_config": str(vision_config),
                "geometry_config": str(geometry_config),
                "cam_id": cam_id,
                "camera_name": camera_name(vision_config),
                "logs_dir": str(logs_dir) if logs_dir is not None else None,
                "snapshot_count": len(images),
                "latest_snapshot_age_s": (
                    round(time.time() - latest_image, 1) if latest_image else None
                ),
                "services": services,
            }
        )

    async def index_handler(_: web.Request) -> web.StreamResponse:
        index = frontend_dir / "index.html"
        if not index.is_file():
            return web.json_response(
                {
                    "error": "operator frontend is not built",
                    "expected": str(index),
                },
                status=503,
            )
        return web.FileResponse(index)

    http_app.router.add_get("/api/config", config_handler)
    http_app.router.add_get("/api/health", health_handler)

    async def asset_handler(request: web.Request) -> web.FileResponse:
        # Resolved per request (not add_static at startup) so a frontend
        # built after the backend started is served without a restart.
        assets = (frontend_dir / "assets").resolve()
        path = (assets / request.match_info["path"]).resolve()
        if not path.is_relative_to(assets) or not path.is_file():
            raise web.HTTPNotFound
        return web.FileResponse(path)

    http_app.router.add_get("/", index_handler)
    http_app.router.add_get("/popout", index_handler)

    debug_lock = asyncio.Lock()

    async def debug_interval_handler(request: web.Request) -> web.Response:
        """``POST /api/config/debug-interval {"interval_ms": N}`` (0 = off).

        Sets ``debug.debug_stream_interval_ms`` in the vision config
        (comment-preserving); vision_processor reloads tunables live.
        """
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        value = body.get("interval_ms") if isinstance(body, dict) else None
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= 60000
        ):
            return web.json_response(
                {"error": "interval_ms must be an integer 0..60000"}, status=400
            )
        async with debug_lock:
            try:
                original = vision_config.read_text(encoding="utf-8")
                updated = update_section(
                    original, "debug", {"debug_stream_interval_ms": str(value)}
                )
                parsed = yaml.safe_load(updated) or {}
                debug = parsed.get("debug") if isinstance(parsed, dict) else None
                if (
                    not isinstance(debug, dict)
                    or debug.get("debug_stream_interval_ms") != value
                ):
                    raise YamlEditError("edited config does not round-trip")
                if updated != original:
                    atomic_write(vision_config, updated)
            except (OSError, yaml.YAMLError, YamlEditError) as exc:
                return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"interval_ms": value})

    http_app.router.add_post("/api/config/debug-interval", debug_interval_handler)
    http_app.router.add_get("/assets/{path:.+}", asset_handler)
    http_app.on_startup.append(start_watcher)
    http_app.on_cleanup.append(stop_watcher)

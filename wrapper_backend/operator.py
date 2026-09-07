"""Read-only operator API and static frontend hosting."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web


def _process_status(pid_file: Path) -> dict[str, Any]:
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        os.kill(pid, 0)
        return {"running": True, "pid": pid}
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return {"running": False, "pid": None}


def register(
    http_app: web.Application,
    vision_config: Path,
    geometry_config: Path,
    frontend_dir: Path,
    img_dir: Path,
) -> None:
    started_at = time.monotonic()

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

    async def geometry_handler(_: web.Request) -> web.Response:
        """The geometry yaml as loaded, so field dimensions are readable
        without waiting for a multicast round trip through the wrapper."""
        try:
            config = yaml.safe_load(geometry_config.read_text(encoding="utf-8")) or {}
            stat = geometry_config.stat()
        except (OSError, yaml.YAMLError) as exc:
            return web.json_response({"error": str(exc)}, status=500)

        return web.json_response(
            {
                "path": str(geometry_config),
                "modified_at": stat.st_mtime,
                "geometry": config,
            }
        )

    async def version_handler(_: web.Request) -> web.Response:
        """Identify the build currently on disk.

        Vite fingerprints the bundle filename, so the name index.html points at
        IS the build identity. A page can compare it against the bundle it is
        running and notice a rebuild, instead of someone having to know to
        force-reload.
        """
        index = frontend_dir / "index.html"
        try:
            markup = index.read_text(encoding="utf-8")
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=503)

        match = re.search(r'assets/([^"\']+\.js)', markup)
        response = web.json_response({"bundle": match.group(1) if match else None})
        response.headers["Cache-Control"] = "no-store"
        return response

    async def health_handler(_: web.Request) -> web.Response:
        images = [
            path
            for path in img_dir.glob("*")
            if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        ]
        latest_image = max((path.stat().st_mtime for path in images), default=0.0)
        return web.json_response(
            {
                "status": "ok",
                "uptime_s": round(time.monotonic() - started_at, 1),
                "vision_config": str(vision_config),
                "geometry_config": str(geometry_config),
                "snapshot_count": len(images),
                "latest_snapshot_age_s": (
                    round(time.time() - latest_image, 1) if latest_image else None
                ),
                "services": {
                    "vision_processor": _process_status(
                        Path("/tmp/vision-processor.pid")
                    ),
                    "wrapper_backend": {
                        "running": True,
                        "pid": os.getpid(),
                    },
                    "wrapper_ui": _process_status(Path("/tmp/vision-ui.pid")),
                    "game_controller": _process_status(
                        Path("/tmp/ssl-game-controller.pid")
                    ),
                },
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
        response = web.FileResponse(index)
        # index.html names the fingerprinted bundles, so caching it strands
        # browsers on a previous build - the exact failure where a rebuilt UI
        # appears not to deploy until someone force-reloads. "no-cache" still
        # stores the file but revalidates every time, so the ETag already on
        # the response turns the usual case into a cheap 304.
        response.headers["Cache-Control"] = "no-cache"
        return response

    http_app.router.add_get("/api/config", config_handler)
    http_app.router.add_get("/api/version", version_handler)
    http_app.router.add_get("/api/geometry", geometry_handler)
    http_app.router.add_get("/api/health", health_handler)
    http_app.router.add_get("/", index_handler)

    assets = frontend_dir / "assets"

    async def asset_handler(request: web.Request) -> web.FileResponse:
        root = assets.resolve()
        path = (root / request.match_info["filename"]).resolve()
        # Resolve first, then confirm the result is still inside the asset
        # directory, so "..", symlinks and absolute names cannot escape it.
        if root not in path.parents or not path.is_file():
            raise web.HTTPNotFound
        response = web.FileResponse(path)
        # Vite fingerprints every asset filename, so a given URL's bytes never
        # change and this can be cached indefinitely.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    http_app.router.add_get("/assets/{filename}", asset_handler)

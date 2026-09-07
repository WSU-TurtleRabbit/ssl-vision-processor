"""Read-only operator API and static frontend hosting."""

from __future__ import annotations

import os
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
        return web.FileResponse(index)

    http_app.router.add_get("/api/config", config_handler)
    http_app.router.add_get("/api/health", health_handler)
    http_app.router.add_get("/", index_handler)

    assets = frontend_dir / "assets"
    if assets.is_dir():
        http_app.router.add_static("/assets/", assets, show_index=False)

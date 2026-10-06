"""Pi camera remote control (proxy to pi_camera/camstream.py).

``POST /api/camera/{restart,close,open,shutdown}`` forwards to
``<scheme>://<host>:<port>/control/<command>`` of the camera in the vision
config's ``camera.path`` with the header ``X-Camstream-Token`` (3 s timeout)
and returns the Pi's JSON and status code. 503 when the camera is not an
http(s) camera or no token is configured.

The token comes from ``--camera-token-file`` (default ``<repo>/.camera-token``
when it exists; whitespace stripped) or, as a fallback, the
``PI_CAMERA_TOKEN`` environment variable. It is never logged or returned.

``close`` first stops the supervised vision_processor (``want_running``
false), because it would otherwise reconnect-loop against the Pi's 503.
``open`` does not start it again; the response says so.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from aiohttp import web
from yarl import URL

from wrapper_backend.calibration import read_config
from wrapper_backend.yamledit import YamlEditError, atomic_write, update_section
from wrapper_backend.supervisor import SupervisorError, VisionSupervisor

log = logging.getLogger("wrapper_backend.camera")

COMMANDS = ("restart", "close", "open", "shutdown")
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")


def camera_name(vision_config: Path) -> str | None:
    """``camera.name`` from the vision config (ignored by the C++)."""
    camera = read_config(vision_config).get("camera")
    name = camera.get("name") if isinstance(camera, dict) else None
    return name if isinstance(name, str) and NAME_RE.match(name) else None


TIMEOUT_S = 3.0
TOKEN_HELP = "set up the token: see pi_camera/README.md section 6"


def load_token(token_file: Path | None) -> str | None:
    if token_file is not None:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning("camera token file %s not readable: %s", token_file, exc)
        else:
            if token:
                return token
    token = os.environ.get("PI_CAMERA_TOKEN", "").strip()
    return token or None


def camera_base_url(vision_config: Path) -> URL | None:
    camera = read_config(vision_config).get("camera")
    path = camera.get("path") if isinstance(camera, dict) else None
    if not isinstance(path, str):
        return None
    url = URL(path)
    if url.scheme not in ("http", "https") or not url.host:
        return None
    return url.with_path("/").with_query(None).with_fragment(None)


def register(
    http_app: web.Application,
    vision_config: Path,
    supervisor: VisionSupervisor,
    token: str | None,
) -> None:
    if token:
        log.info("Pi camera remote control enabled (token configured)")
    name_lock = asyncio.Lock()

    async def name_handler(request: web.Request) -> web.Response:
        """``POST /api/camera/name {"name": "..."}``; "" removes the name."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str):
            return web.json_response({"error": '"name" must be a string'}, status=400)
        name = name.strip()
        if name and not NAME_RE.match(name):
            return web.json_response(
                {"error": "name: 1-40 letters, digits, spaces, - or _"}, status=400
            )
        async with name_lock:
            try:
                original = vision_config.read_text(encoding="utf-8")
                updated = update_section(
                    original,
                    "camera",
                    {"name": f'"{name}"'} if name else {},
                    remove=[] if name else ["name"],
                )
                parsed = read_config_text(updated)
                if parsed.get("name") != (name or None):
                    raise YamlEditError("edited config does not round-trip")
                if updated != original:
                    atomic_write(vision_config, updated)
            except (OSError, YamlEditError, yaml.YAMLError) as exc:
                return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"name": name or None})

    async def handler(request: web.Request) -> web.Response:
        command = request.match_info["command"]
        if command not in COMMANDS:
            raise web.HTTPNotFound
        base = camera_base_url(vision_config)
        if base is None:
            return web.json_response(
                {
                    "ok": False,
                    "error": "the camera in the vision config is not an "
                    "http(s) network camera",
                },
                status=503,
            )
        if not token:
            return web.json_response(
                {"ok": False, "error": f"no camera token configured; {TOKEN_HELP}"},
                status=503,
            )
        extra: dict[str, Any] = {}
        if command == "close":
            try:
                stopped = await supervisor.stop()
            except SupervisorError as exc:
                return web.json_response(
                    {"ok": False, "error": f"could not stop vision_processor: {exc}"},
                    status=exc.status,
                )
            extra["vision_processor_stopped"] = stopped["stopped"]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    str(base.with_path(f"/control/{command}")),
                    headers={"X-Camstream-Token": token},
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
                ) as response:
                    status = response.status
                    try:
                        data = await response.json(content_type=None)
                    except ValueError:
                        data = {"ok": False, "error": (await response.text()).strip()}
        except (aiohttp.ClientError, TimeoutError) as exc:
            return web.json_response(
                {
                    "ok": False,
                    "error": f"Pi camera not reachable: "
                    f"{str(exc) or type(exc).__name__}",
                    **extra,
                },
                status=502,
            )
        if not isinstance(data, dict):
            data = {"ok": False, "error": "unexpected reply from the Pi"}
        if command == "open" and status == 200:
            extra["note"] = (
                "camera open again; vision_processor was not started — press Start"
            )
        log.info("camera %s -> HTTP %d", command, status)
        return web.json_response({**data, **extra}, status=status)

    # The fixed route first, so "name" never reaches the command proxy.
    http_app.router.add_post("/api/camera/name", name_handler)
    http_app.router.add_post("/api/camera/{command}", handler)


def read_config_text(text: str) -> dict[str, Any]:
    """The ``camera:`` mapping of a config text (for round-trip checks)."""
    parsed = yaml.safe_load(text) or {}
    camera = parsed.get("camera") if isinstance(parsed, dict) else None
    return camera if isinstance(camera, dict) else {}

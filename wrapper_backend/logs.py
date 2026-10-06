"""Log files in ``<repo>/logs`` and the routes that serve them.

Files (each rotated to ``<name>.1`` once it exceeds 5 MB, one old copy kept):

- ``wrapper_backend.log`` — the backend's own logging (also on stderr).
- ``vision_processor.log`` — stdout+stderr of the supervised vision_processor,
  with a ``---- started <time> ----`` separator per run.
- ``pi-camera-<host>.log`` — the Pi camera service's log, polled from
  ``<camera>/log?since=N`` every 3 s while ``camera.path`` is an http(s) URL
  (skipped quietly while the Pi is unreachable). Lines are
  ``<ISO time> <text>`` with the Pi's own timestamp.

Routes: ``GET /api/logs`` -> ``[{name, size, modified, path}]``;
``GET /api/logs/<name>?tail=200`` -> ``{"name", "lines", "total"}``, or the
raw file as ``text/plain`` with ``?raw=1``. Only basenames that exist in the
directory are served (no path traversal).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

log = logging.getLogger("wrapper_backend.logs")

MAX_BYTES = 5 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.log(\.1)?$")
CAMERA_POLL_S = 3.0
CAMERA_TIMEOUT_S = 2.0
ERROR_WORDS = re.compile(r"fail|error", re.IGNORECASE)


def rotate(path: Path) -> None:
    """Rename ``path`` to ``path.1`` when it exceeds MAX_BYTES."""
    try:
        if path.stat().st_size > MAX_BYTES:
            os.replace(path, path.with_name(path.name + ".1"))
    except OSError:
        pass


def append_lines(path: Path, lines: list[str]) -> None:
    if not lines:
        return
    rotate(path)
    try:
        with path.open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(line.rstrip("\n") + "\n")
    except OSError as exc:
        log.warning("cannot write %s: %s", path, exc)


def iso(unix_time: float) -> str:
    return (
        datetime.fromtimestamp(unix_time, timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )


def tail(path: Path, count: int) -> tuple[list[str], int]:
    """Last ``count`` lines of ``path`` and the total line count."""
    try:
        data = path.read_bytes()
    except OSError:
        return [], 0
    lines = data.decode("utf-8", errors="replace").splitlines()
    return lines[-count:] if count > 0 else [], len(lines)


class CameraLogPoller:
    """Appends new lines of the Pi camera's ``/log`` to a file."""

    def __init__(self, logs_dir: Path, base_url_getter: Any) -> None:
        self._logs_dir = logs_dir
        self._base_url = base_url_getter  # () -> yarl.URL | None
        self._since: dict[str, int] = {}
        self.last_error: dict[str, Any] | None = None
        self.last_on_at: float = 0.0
        self.last_line: dict[str, Any] | None = None
        self.file: Path | None = None

    async def poll_once(self, session: aiohttp.ClientSession) -> None:
        base = self._base_url()
        if base is None or not base.host:
            return
        host = base.host
        self.file = self._logs_dir / f"pi-camera-{host}.log"
        since = self._since.get(host, 0)
        try:
            async with session.get(
                str(base.with_path("/log").with_query(since=since)),
                timeout=aiohttp.ClientTimeout(total=CAMERA_TIMEOUT_S),
            ) as response:
                if response.status != 200:
                    return
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return  # Pi away: try again next time
        if not isinstance(data, dict) or not isinstance(data.get("lines"), list):
            return
        out: list[str] = []
        for item in data["lines"]:
            if not isinstance(item, list) or len(item) < 3:
                continue
            seq, when, text = item[0], item[1], item[2]
            if not isinstance(when, (int, float)) or not isinstance(text, str):
                continue
            out.append(f"{iso(float(when))} {text}")
            self.last_line = {"text": text, "at": float(when), "seq": seq}
            if "camera ON" in text:
                self.last_on_at = float(when)
                self.last_error = None  # a fresh start clears old errors
            elif ERROR_WORDS.search(text):
                self.last_error = {"text": text, "at": float(when)}
        next_seq = data.get("next")
        if isinstance(next_seq, int):
            if next_seq < since:
                # The Pi restarted: its sequence starts over.
                out.insert(
                    0, f"{iso(time.time())} ---- pi camera service restarted ----"
                )
            self._since[host] = next_seq
        append_lines(self.file, out)

    async def run(self) -> None:
        async with aiohttp.ClientSession() as session:
            while True:
                await self.poll_once(session)
                await asyncio.sleep(CAMERA_POLL_S)


def register(
    http_app: web.Application, logs_dir: Path, poller: CameraLogPoller
) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    task_key = web.AppKey("camera_log_poller", asyncio.Task[None])

    async def start(app: web.Application) -> None:
        app[task_key] = asyncio.create_task(poller.run(), name="camera-log-poller")

    async def stop(app: web.Application) -> None:
        app[task_key].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app[task_key]

    def files() -> dict[str, Path]:
        return {
            p.name: p
            for p in sorted(logs_dir.iterdir())
            if p.is_file() and NAME_RE.match(p.name)
        }

    async def list_handler(_: web.Request) -> web.Response:
        out = []
        for name, path in files().items():
            try:
                stat = path.stat()
            except OSError:
                continue
            out.append(
                {
                    "name": name,
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "path": str(path),
                }
            )
        return web.json_response({"dir": str(logs_dir), "files": out})

    async def file_handler(request: web.Request) -> web.StreamResponse:
        path = files().get(request.match_info["name"])
        if path is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": "no such log"}),
                content_type="application/json",
            )
        if request.query.get("raw") == "1":
            return web.FileResponse(
                path,
                headers={
                    "Content-Type": "text/plain; charset=utf-8",
                    "Content-Disposition": f'attachment; filename="{path.name}"',
                },
            )
        try:
            count = max(1, min(5000, int(request.query.get("tail", "200"))))
        except ValueError:
            count = 200
        lines, total = tail(path, count)
        return web.json_response({"name": path.name, "lines": lines, "total": total})

    http_app.router.add_get("/api/logs", list_handler)
    http_app.router.add_get("/api/logs/{name}", file_handler)
    http_app.on_startup.append(start)
    http_app.on_cleanup.append(stop)

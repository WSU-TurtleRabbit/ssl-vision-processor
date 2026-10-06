"""Colour calibration endpoints.

``vision_processor`` publishes its learned and reference blob colours to
``img/<cam_id>.colors.json`` every 0.5 s (``tmp -> rename``) and live-reloads
the ``color:`` section of its YAML config whenever the file's mtime changes.
These routes expose the former and write the latter:

- ``GET /api/colors?cam_id=N`` returns the JSON plus ``age_s``.
- ``POST /api/colors/save[?cam_id=N]`` with ``{"colors": {name: [r, g, b]}}``
  or ``{"from": "learned"}`` rewrites those keys in the config's ``color:``
  section.
- ``POST /api/colors/auto[?cam_id=N]`` starts a ~5 s auto-calibration:
  ``colors.json`` is sampled every 0.5 s while ``detection.in`` frames are
  watched. It fails unless robots were detected in >= 50 % of the windows;
  a colour is saved (median of the learned values) only with evidence in
  >= 50 % of the windows (blue/yellow: robots of that team, green/pink: any
  robot, orange: a ball, field: any frame) and a per-channel spread
  <= 12; the others are listed as skipped. ``GET /api/colors/auto`` polls.

Values are vision_processor's brightness-free "dRGB" (see
``kernel/resampling.cl``), not plain RGB; they are stored verbatim.

The config is hand-written and commented, so it is edited line by line
rather than round-tripped through PyYAML (which would drop every comment).
"""

from __future__ import annotations

import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from wrapper_backend.bus import Bus
from wrapper_backend.yamledit import YamlEditError, atomic_write, update_section

COLOR_NAMES = ("orange", "field", "yellow", "blue", "green", "pink")

# Auto-calibration: 10 samples x 0.5 s; a colour needs evidence in >= 50 % of
# the windows and a per-channel spread (max - min) <= 12 to be saved.
AUTO_SAMPLES = 10
AUTO_INTERVAL_S = 0.5
AUTO_MIN_SHARE = 0.5
AUTO_MAX_SPREAD = 12

_SECTION = "color"

Rgb = list[int]

# Kept as a name for callers/tests; the editor itself lives in yamledit.py.
ColorEditError = YamlEditError


def _cam_id(request: web.Request) -> int:
    raw = request.query.get("cam_id", "0")
    try:
        cam_id = int(raw)
    except ValueError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "cam_id must be an integer"}),
            content_type="application/json",
        ) from None
    if cam_id < 0:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "cam_id must be non-negative"}),
            content_type="application/json",
        )
    return cam_id


def _bad_request(message: str) -> web.Response:
    return web.json_response({"error": message}, status=400)


def _validate_rgb(value: Any) -> Rgb | None:
    if (
        isinstance(value, list)
        and len(value) == 3
        and all(
            isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255
            for c in value
        )
    ):
        return [int(c) for c in value]
    return None


def _format_rgb(rgb: Rgb) -> str:
    return f"[{rgb[0]}, {rgb[1]}, {rgb[2]}]"


def update_color_section(text: str, colors: dict[str, Rgb]) -> str:
    """Set ``colors`` inside the top-level ``color:`` mapping of ``text``.

    Thin wrapper over :func:`yamledit.update_section`: existing ``<name>:``
    lines are replaced in place (keeping a trailing comment), missing names
    are appended to the section, everything else stays byte-identical.
    """
    return update_section(
        text,
        _SECTION,
        {name: _format_rgb(rgb) for name, rgb in colors.items()},
        require_flow_list=True,
    )


def _read_colors_json(
    img_dir: Path, cam_id: int
) -> tuple[dict[str, Any], float] | None:
    path = img_dir / f"{cam_id}.colors.json"
    try:
        mtime = path.stat().st_mtime
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        # A rename can't tear the file, but treat a bad parse like "missing".
        return None
    if not isinstance(data, dict):
        return None
    return data, mtime


def register(
    http_app: web.Application, vision_config: Path, img_dir: Path, bus: Bus
) -> None:
    write_lock = asyncio.Lock()
    auto: dict[str, Any] = {"state": "idle"}

    async def write_colors(colors: dict[str, Rgb]) -> dict[str, Any]:
        """Write ``colors`` into the config's ``color:`` section; returns it."""
        async with write_lock:
            original = vision_config.read_text(encoding="utf-8")
            updated = update_color_section(original, colors)
            # Refuse to write anything PyYAML (and so yaml-cpp) can't
            # read back with exactly the requested values.
            parsed = yaml.safe_load(updated) or {}
            section = parsed.get(_SECTION) if isinstance(parsed, dict) else None
            if not isinstance(section, dict) or any(
                section.get(name) != rgb for name, rgb in colors.items()
            ):
                raise ColorEditError("edited config does not round-trip")
            if updated != original:
                atomic_write(vision_config, updated)
            return section

    async def run_auto(cam_id: int) -> None:
        """Sample learned colours + detections, save stable ones (see module doc)."""
        queue = bus.subscribe("detection.in")
        samples: list[dict[str, Rgb]] = []
        # Per 0.5 s window: did detections show robots / blue / yellow / balls?
        windows: list[dict[str, bool]] = []
        try:
            for index in range(AUTO_SAMPLES):
                auto["progress"] = round(index / AUTO_SAMPLES, 2)
                seen = {
                    "frames": False,
                    "robot": False,
                    "blue": False,
                    "yellow": False,
                    "ball": False,
                }
                end = asyncio.get_running_loop().time() + AUTO_INTERVAL_S
                while (left := end - asyncio.get_running_loop().time()) > 0:
                    try:
                        frame = await asyncio.wait_for(queue.get(), left)
                    except TimeoutError:
                        break
                    if frame.camera_id != cam_id:
                        continue
                    seen["frames"] = True
                    seen["blue"] |= len(frame.robots_blue) > 0
                    seen["yellow"] |= len(frame.robots_yellow) > 0
                    seen["robot"] |= seen["blue"] or seen["yellow"]
                    seen["ball"] |= len(frame.balls) > 0
                windows.append(seen)
                result = _read_colors_json(img_dir, cam_id)
                if result is not None and time.time() - result[1] < 2.0:
                    learned = result[0].get("learned")
                    if isinstance(learned, dict):
                        sample = {
                            name: rgb
                            for name in COLOR_NAMES
                            if (rgb := _validate_rgb(learned.get(name))) is not None
                        }
                        samples.append(sample)
        finally:
            bus.unsubscribe("detection.in", queue)

        def share(key: str) -> float:
            return sum(w[key] for w in windows) / max(1, len(windows))

        evidence_key = {
            "blue": "blue",
            "yellow": "yellow",
            "green": "robot",
            "pink": "robot",
            "orange": "ball",
            "field": "frames",
        }
        evidence = {name: share(key) for name, key in evidence_key.items()}
        if len(samples) < AUTO_SAMPLES // 2:
            auto.update(
                state="failed",
                progress=1.0,
                error="vision_processor is not publishing colours (colors.json "
                "missing or stale)",
            )
            return
        if share("robot") < AUTO_MIN_SHARE:
            auto.update(
                state="failed",
                progress=1.0,
                error=f"robots were seen in only {share('robot'):.0%} of the samples "
                f"(need {AUTO_MIN_SHARE:.0%}); put robots on the field and retry",
                evidence=evidence,
            )
            return
        save: dict[str, Rgb] = {}
        skipped: dict[str, str] = {}
        spread: dict[str, list[int]] = {}
        for name in COLOR_NAMES:
            if evidence[name] < AUTO_MIN_SHARE:
                skipped[name] = f"not seen enough ({evidence[name]:.0%} of samples)"
                continue
            values = [sample[name] for sample in samples if name in sample]
            if len(values) < len(samples) // 2:
                skipped[name] = "no learned value"
                continue
            channels = list(zip(*values, strict=True))
            spread[name] = [max(c) - min(c) for c in channels]
            if max(spread[name]) > AUTO_MAX_SPREAD:
                skipped[name] = f"unstable (spread {spread[name]})"
                continue
            save[name] = [int(statistics.median(c)) for c in channels]
        saved_section: dict[str, Any] = {}
        if save:
            try:
                saved_section = await write_colors(save)
            except (OSError, yaml.YAMLError, ColorEditError) as exc:
                auto.update(state="failed", progress=1.0, error=str(exc))
                return
        auto.update(
            state="done",
            progress=1.0,
            saved=save,
            skipped=skipped,
            spread=spread,
            evidence=evidence,
            samples=len(samples),
            reference={n: saved_section.get(n) for n in save},
        )

    async def auto_start_handler(request: web.Request) -> web.Response:
        cam_id = _cam_id(request)
        if auto.get("state") == "running":
            return web.json_response({"error": "already running", **auto}, status=409)
        auto.clear()
        auto.update(state="running", progress=0.0, cam_id=cam_id, started=time.time())
        task = asyncio.create_task(run_auto(cam_id), name="colors-auto")
        auto_tasks.add(task)

        def done(t: asyncio.Task[None]) -> None:
            auto_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                auto.update(state="failed", error=repr(t.exception()))

        task.add_done_callback(done)
        return web.json_response(auto)

    async def auto_status_handler(_: web.Request) -> web.Response:
        return web.json_response(auto)

    auto_tasks: set[asyncio.Task[None]] = set()

    async def get_handler(request: web.Request) -> web.Response:
        cam_id = _cam_id(request)
        result = _read_colors_json(img_dir, cam_id)
        if result is None:
            return web.json_response(
                {
                    "error": "vision_processor is not publishing colours",
                    "expected": str(img_dir / f"{cam_id}.colors.json"),
                },
                status=404,
            )
        data, mtime = result
        data["age_s"] = round(max(0.0, time.time() - mtime), 2)
        return web.json_response(data)

    async def save_handler(request: web.Request) -> web.Response:
        cam_id = _cam_id(request)
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _bad_request("body must be JSON")
        if not isinstance(body, dict):
            return _bad_request("body must be a JSON object")

        colors: dict[str, Rgb] = {}
        if set(body) == {"from"} and body["from"] == "learned":
            result = _read_colors_json(img_dir, cam_id)
            if result is None:
                return web.json_response(
                    {"error": "no learned colours: vision_processor not publishing"},
                    status=404,
                )
            learned = result[0].get("learned")
            if not isinstance(learned, dict):
                return web.json_response(
                    {"error": "colors.json has no 'learned' mapping"}, status=500
                )
            for name in COLOR_NAMES:
                rgb = _validate_rgb(learned.get(name))
                if rgb is None:
                    return web.json_response(
                        {"error": f"learned colour {name!r} is missing or invalid"},
                        status=500,
                    )
                colors[name] = rgb
        elif set(body) == {"colors"} and isinstance(body["colors"], dict):
            if not body["colors"]:
                return _bad_request("'colors' is empty")
            for name, value in body["colors"].items():
                if name not in COLOR_NAMES:
                    return _bad_request(
                        f"unknown colour {name!r}; expected one of {list(COLOR_NAMES)}"
                    )
                rgb = _validate_rgb(value)
                if rgb is None:
                    return _bad_request(f"{name!r} must be 3 integers in 0..255")
                colors[name] = rgb
        else:
            return _bad_request(
                'expected {"colors": {name: [r, g, b], ...}} or {"from": "learned"}'
            )

        try:
            section = await write_colors(colors)
        except (OSError, yaml.YAMLError, ColorEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)

        return web.json_response(
            {
                "path": str(vision_config),
                "updated": list(colors),
                "reference": {
                    name: section.get(name) for name in COLOR_NAMES if name in section
                },
            }
        )

    http_app.router.add_get("/api/colors", get_handler)
    http_app.router.add_post("/api/colors/save", save_handler)
    http_app.router.add_post("/api/colors/auto", auto_start_handler)
    http_app.router.add_get("/api/colors/auto", auto_status_handler)

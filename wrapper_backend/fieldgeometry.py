"""Field geometry editor: read/write the geometry.yml the backend runs with.

- ``GET  /api/geometry`` — the editable ``field:`` values and the
  ``optional_field_lines:`` toggles of the geometry file, plus its path.
- ``POST /api/geometry`` with ``{"field": {...}, "optional_field_lines":
  {...}}`` (any subset of the editable keys) — validated, written with the
  comment-preserving editor (:mod:`yamledit`), re-parsed strictly and swapped
  into the in-memory geometry, which is broadcast at once (no restart; the
  absorbed camera calibrations are kept). The response says whether the
  field size / boundary changed, because the camera calibration was fitted
  to the old size and should be redone (``POST /api/calibration/recalibrate``).
"""

from __future__ import annotations

import asyncio
import json
import math
from typing import Any

import yaml
from aiohttp import web

from wrapper_backend.geometry import Geometry, geometry_from_config
from wrapper_backend.yamledit import YamlEditError, atomic_write, update_section

FIELD_KEYS = (
    "field_length",
    "field_width",
    "boundary_width",
    "boundary_width_goal_line",
    "goal_width",
    "goal_depth",
    "penalty_area_depth",
    "penalty_area_width",
    "center_circle_radius",
    "line_thickness",
)
LINE_KEYS = ("goal2goal", "halfway", "centercircle", "penalty")
# Changing these invalidates a camera calibration fitted to field corners.
SIZE_KEYS = (
    "field_length",
    "field_width",
    "boundary_width",
    "boundary_width_goal_line",
)


class GeometryError(ValueError):
    pass


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def validate(field: dict[str, float]) -> None:
    """Raise GeometryError if the field values are inconsistent."""
    for key in ("field_length", "field_width", "line_thickness"):
        if field[key] <= 0:
            raise GeometryError(f"{key} must be > 0")
    for key in FIELD_KEYS:
        if field[key] < 0:
            raise GeometryError(f"{key} must not be negative")
    length, width = field["field_length"], field["field_width"]
    if field["field_width"] > field["field_length"]:
        raise GeometryError("field_width must not exceed field_length")
    if field["goal_width"] >= width:
        raise GeometryError("goal_width must be smaller than field_width")
    if field["penalty_area_width"] >= width:
        raise GeometryError("penalty_area_width must be smaller than field_width")
    if (
        field["penalty_area_width"]
        and field["penalty_area_width"] < field["goal_width"]
    ):
        raise GeometryError("penalty_area_width must be at least goal_width")
    if field["penalty_area_depth"] >= length / 2:
        raise GeometryError("penalty_area_depth must be less than half field_length")
    if field["center_circle_radius"] * 2 >= width:
        raise GeometryError("the centre circle must fit into the field width")
    if field["line_thickness"] > 100:
        raise GeometryError("line_thickness looks wrong (> 100 mm)")


def _format(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:g}"


def _current(config: dict[str, Any]) -> tuple[dict[str, float], dict[str, bool]]:
    raw_field = config.get("field")
    field_cfg: dict[str, Any] = raw_field if isinstance(raw_field, dict) else {}
    lines_cfg = config.get("optional_field_lines")
    lines_cfg = lines_cfg if isinstance(lines_cfg, dict) else {}
    field: dict[str, float] = {}
    for key in FIELD_KEYS:
        value = _number(field_cfg.get(key))
        if value is None and key == "boundary_width_goal_line":
            value = _number(field_cfg.get("boundary_width"))
        field[key] = value if value is not None else 0.0
    lines = {key: bool(lines_cfg.get(key, False)) for key in LINE_KEYS}
    return field, lines


def register(http_app: web.Application, geometry: Geometry) -> None:
    lock = asyncio.Lock()

    def read() -> dict[str, Any]:
        parsed = yaml.safe_load(geometry.path.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            raise GeometryError("geometry file is not a mapping")
        return parsed

    async def get_handler(_: web.Request) -> web.Response:
        try:
            field, lines = _current(read())
        except (OSError, yaml.YAMLError, GeometryError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(
            {"path": str(geometry.path), "field": field, "optional_field_lines": lines}
        )

    async def post_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        if not isinstance(body, dict) or not set(body) <= {
            "field",
            "optional_field_lines",
        }:
            return web.json_response(
                {"error": 'expected {"field": {...}, "optional_field_lines": {...}}'},
                status=400,
            )
        async with lock:
            try:
                original = geometry.path.read_text(encoding="utf-8")
                old_field, old_lines = _current(yaml.safe_load(original) or {})
                field = dict(old_field)
                lines = dict(old_lines)
                new_field = body.get("field", {})
                new_lines = body.get("optional_field_lines", {})
                if not isinstance(new_field, dict) or not isinstance(new_lines, dict):
                    raise GeometryError(
                        "'field' and 'optional_field_lines' are objects"
                    )
                for key, value in new_field.items():
                    if key not in FIELD_KEYS:
                        raise GeometryError(f"unknown field key {key!r}")
                    number = _number(value)
                    if number is None:
                        raise GeometryError(f"{key} must be a number")
                    field[key] = number
                for key, value in new_lines.items():
                    if key not in LINE_KEYS or not isinstance(value, bool):
                        raise GeometryError(f"{key!r} must be one of {LINE_KEYS}: bool")
                    lines[key] = value
                validate(field)
                updated = update_section(
                    original,
                    "field",
                    {
                        key: _format(field[key])
                        for key in FIELD_KEYS
                        if field[key] != old_field[key] or key in new_field
                    },
                )
                updated = update_section(
                    updated,
                    "optional_field_lines",
                    {key: "true" if lines[key] else "false" for key in LINE_KEYS},
                )
                parsed = yaml.safe_load(updated)
                got_field, got_lines = _current(parsed)
                if got_field != field or got_lines != lines:
                    raise YamlEditError("edited geometry does not round-trip")
                wrapper = geometry_from_config(parsed)  # strict: raises on typos
                if updated != original:
                    atomic_write(geometry.path, updated)
                geometry.replace(wrapper)
            except GeometryError as exc:
                return web.json_response({"error": str(exc)}, status=400)
            except (OSError, yaml.YAMLError, YamlEditError, ValueError) as exc:
                return web.json_response({"error": str(exc)}, status=500)
        size_changed = any(field[key] != old_field[key] for key in SIZE_KEYS)
        return web.json_response(
            {
                "path": str(geometry.path),
                "field": field,
                "optional_field_lines": lines,
                "size_changed": size_changed,
                "message": "Saved and published."
                + (
                    " The field size changed: recalibrate the camera so the "
                    "corners match the new size."
                    if size_changed
                    else ""
                ),
            }
        )

    http_app.router.add_get("/api/geometry", get_handler)
    http_app.router.add_post("/api/geometry", post_handler)

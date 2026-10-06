"""Field calibration from four manually clicked corners.

The lab has no white field lines, so ``vision_processor`` is calibrated from
``geometry.line_corners`` alone (``cornerCalibration`` in
``src/calib/GeomModel.cpp`` fits the camera model to the four corners of the
field rectangle published in the geometry, *without* boundary) with line
refinement disabled (``geometry.refinement: false``).

- ``GET /api/calibration[?cam_id=N]`` — state (``calibrated`` /
  ``recalibrating`` / ``not_calibrated``), the corners saved in the vision
  config (``line_corners`` and the clicked ``outer_line_corners``), the field
  size and boundary widths from the published geometry, the snapshot size
  and the newest ``img/*.calib.json`` for the camera.
- ``POST /api/calibration/corners`` with ``{"cam_id": 0, "corners":
  [[x, y] x 4], "mode": "outer" | "field"}`` (image pixels of
  ``img/<cam>.raw.jpg``; the first point is on the field's (-x, -y) side).
  Validated (bounds, duplicates, convexity) and reordered clockwise on screen
  starting at the first point (the order ``cornerCalibration`` and
  ``DetectionCorrector`` expect: (-x,-y), (-x,+y), (+x,+y), (+x,-y)).

  ``mode: "outer"`` (default): the clicks are the outer edge incl. boundary
  (``field_length + 2 * boundary_width_goal_line`` x ``field_width + 2 *
  boundary_width``, e.g. the lab's foam-mat area); a plane homography from
  that rectangle (field mm) to the clicks maps the field corners to pixels,
  which become ``line_corners`` (lens distortion ignored). The clicks are
  kept in ``geometry.outer_line_corners`` (yaml-cpp ignores unknown keys) so
  the UI can show them again. ``mode: "field"``: the clicks are the field
  corners themselves; ``outer_line_corners`` is removed.

  Written with the comment-preserving editor, then: stop vision_processor ->
  clear the in-memory calibs and broadcast -> start it again.
  vision_processor reads the geometry section only at startup and reuses any
  calib for its camera it receives, so all three steps are needed for the
  new corners to take effect.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import time
from pathlib import Path
from typing import Any

import yaml
from aiohttp import web

from wrapper_backend.geometry import Geometry
from wrapper_backend.supervisor import SupervisorError, VisionSupervisor
from wrapper_backend.yamledit import (
    Value,
    YamlEditError,
    atomic_write,
    update_section,
)

DEFAULT_IMAGE_SIZE = (768, 432)
MIN_CORNER_DISTANCE_PX = 3.0
MIN_AREA_PX2 = 100.0

Point = tuple[float, float]


class CornerError(ValueError):
    pass


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """(width, height) from a JPEG's SOF header, without decoding it."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if data[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker == 0xFF:
            pos += 1
            continue
        length = struct.unpack(">H", data[pos + 2 : pos + 4])[0]
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if pos + 9 > len(data):
                return None
            height, width = struct.unpack(">HH", data[pos + 5 : pos + 9])
            return width, height
        pos += 2 + length
    return None


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def order_corners(points: list[Point]) -> list[Point]:
    """Keep ``points[0]`` first, the rest clockwise as seen on screen.

    Image y points down, so ascending ``atan2(dy, dx)`` around the centroid
    is clockwise on screen — the orientation ``isClockwiseConvexQuadrilateral``
    accepts.
    """
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    ordered = sorted(points, key=lambda p: math.atan2(p[1] - cy, p[0] - cx))
    first = ordered.index(points[0])
    return ordered[first:] + ordered[:first]


def validate_corners(raw: Any, width: int, height: int) -> list[Point]:
    if not isinstance(raw, list) or len(raw) != 4:
        raise CornerError("'corners' must be a list of exactly 4 [x, y] points")
    points: list[Point] = []
    for index, item in enumerate(raw, start=1):
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in item
            )
            or not all(math.isfinite(v) for v in item)
        ):
            raise CornerError(f"corner {index} must be [x, y] with two numbers")
        x, y = float(item[0]), float(item[1])
        if not (0 <= x <= width and 0 <= y <= height):
            raise CornerError(
                f"corner {index} ({x:g}, {y:g}) is outside the {width}x{height} image"
            )
        points.append((round(x, 1), round(y, 1)))
    for i in range(4):
        for j in range(i + 1, 4):
            if math.dist(points[i], points[j]) < MIN_CORNER_DISTANCE_PX:
                raise CornerError(f"corners {i + 1} and {j + 1} are the same point")
    ordered = order_corners(points)
    crosses = [
        _cross(ordered[i], ordered[(i + 1) % 4], ordered[(i + 2) % 4]) for i in range(4)
    ]
    # Screen-clockwise in y-down image coordinates => every cross > 0.
    if not all(c > 0 for c in crosses):
        raise CornerError(
            "the 4 corners do not form a convex quadrilateral "
            "(one point lies inside the others or three are on one line)"
        )
    area = 0.5 * abs(
        sum(
            ordered[i][0] * ordered[(i + 1) % 4][1]
            - ordered[(i + 1) % 4][0] * ordered[i][1]
            for i in range(4)
        )
    )
    if area < MIN_AREA_PX2:
        raise CornerError("the 4 corners enclose (almost) no area")
    return ordered


def _format_point(point: Point) -> str:
    # 3 decimals: corners are rescaled when the quality preset changes, 0.1 px would drift
    return f"[{round(point[0], 3)}, {round(point[1], 3)}]"


def solve_linear(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting (n is 8 here)."""
    n = len(rhs)
    rows = [row[:] + [value] for row, value in zip(matrix, rhs, strict=True)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-12:
            raise CornerError("corners are degenerate (no homography)")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(n):
            if r != col:
                factor = rows[r][col] / rows[col][col]
                if factor:
                    rows[r] = [a - factor * b for a, b in zip(rows[r], rows[col])]
    return [rows[i][n] / rows[i][i] for i in range(n)]


Homography = list[float]  # h11 h12 h13 h21 h22 h23 h31 h32 (h33 = 1)


def homography(src: list[Point], dst: list[Point]) -> Homography:
    """Plane homography mapping the 4 ``src`` points onto the 4 ``dst``."""
    matrix: list[list[float]] = []
    rhs: list[float] = []
    for (x, y), (u, v) in zip(src, dst, strict=True):
        matrix.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        rhs.append(u)
        matrix.append([0, 0, 0, x, y, 1, -v * x, -v * y])
        rhs.append(v)
    return solve_linear(matrix, rhs)


def apply_homography(h: Homography, point: Point) -> Point:
    x, y = point
    w = h[6] * x + h[7] * y + 1.0
    return ((h[0] * x + h[1] * y + h[2]) / w, (h[3] * x + h[4] * y + h[5]) / w)


def field_rectangle(half_x: float, half_y: float) -> list[Point]:
    """Field-mm corners in line_corners order: (-x,-y), (-x,+y), (+x,+y), (+x,-y)."""
    return [(-half_x, -half_y), (-half_x, half_y), (half_x, half_y), (half_x, -half_y)]


def inner_from_outer(
    outer_px: list[Point],
    field_length: float,
    field_width: float,
    boundary_width: float,
    boundary_goal_line: float,
) -> list[Point]:
    """Field corners (pixels) from the clicked outer corners (incl. boundary).

    A homography of the plane; lens distortion is ignored.
    """
    half_x = field_length / 2
    half_y = field_width / 2
    h = homography(
        field_rectangle(half_x + boundary_goal_line, half_y + boundary_width),
        outer_px,
    )
    return [
        (round(px, 1), round(py, 1))
        for px, py in (apply_homography(h, p) for p in field_rectangle(half_x, half_y))
    ]


MIN_LENS_LINES = 3
MIN_LENS_POINTS = 4
MAX_LENS_LINES = 30
MAX_LENS_POINTS = 100

Lines = list[list[Point]]


def validate_lens_lines(raw: Any, width: int, height: int) -> Lines:
    """>= 3 lines of >= 4 finite in-image points each, rounded to 0.1 px."""
    if not isinstance(raw, list) or not (MIN_LENS_LINES <= len(raw) <= MAX_LENS_LINES):
        raise CornerError(
            f"'lines' must be a list of {MIN_LENS_LINES}..{MAX_LENS_LINES} lines"
        )
    lines: Lines = []
    for li, line in enumerate(raw, start=1):
        if not isinstance(line, list) or not (
            MIN_LENS_POINTS <= len(line) <= MAX_LENS_POINTS
        ):
            raise CornerError(
                f"line {li} needs {MIN_LENS_POINTS}..{MAX_LENS_POINTS} points"
            )
        points: list[Point] = []
        for pi, item in enumerate(line, start=1):
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not all(
                    isinstance(v, (int, float)) and not isinstance(v, bool)
                    for v in item
                )
                or not all(math.isfinite(v) for v in item)
            ):
                raise CornerError(f"line {li} point {pi} must be [x, y] numbers")
            x, y = float(item[0]), float(item[1])
            if not (0 <= x <= width and 0 <= y <= height):
                raise CornerError(
                    f"line {li} point {pi} ({x:g}, {y:g}) is outside the "
                    f"{width}x{height} image"
                )
            points.append((round(x, 1), round(y, 1)))
        lines.append(points)
    return lines


def _format_line(line: list[Point]) -> str:
    return "[" + ", ".join(_format_point(p) for p in line) + "]"


def _points(value: Any, count: int | None = None) -> list[Point] | None:
    if not isinstance(value, list) or (count is not None and len(value) != count):
        return None
    out: list[Point] = []
    for item in value:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in item
            )
        ):
            return None
        out.append((float(item[0]), float(item[1])))
    return out


def _lines(value: Any) -> Lines | None:
    if not isinstance(value, list) or not value:
        return None
    out: Lines = []
    for line in value:
        points = _points(line)
        if points is None:
            return None
        out.append(points)
    return out


def write_geometry(
    config_path: Path, values: dict[str, Any], remove: list[str]
) -> bool:
    """Set/remove keys of the config's ``geometry:`` section; True if changed.

    ``values``: point lists (``line_corners``, ``outer_line_corners``) are
    written as block sequences of ``[x, y]``, ``distortion_lines`` as one
    flow list per line, bools as ``true``/``false``. The result must parse
    back to exactly ``values`` with the ``remove`` keys gone.
    """
    rendered: dict[str, Value] = {}
    expected: dict[str, Any] = {}
    for key, value in values.items():
        if isinstance(value, bool):
            rendered[key] = "true" if value else "false"
            expected[key] = value
        elif key == "distortion_lines":
            rendered[key] = [_format_line(line) for line in value]
            expected[key] = [[[p[0], p[1]] for p in line] for line in value]
        else:
            rendered[key] = [_format_point(p) for p in value]
            expected[key] = [[p[0], p[1]] for p in value]
    original = config_path.read_text(encoding="utf-8")
    updated = update_section(
        original,
        "geometry",
        rendered,
        drop_commented=["line_corners"],
        remove=remove,
    )
    parsed = yaml.safe_load(updated) or {}
    geometry = parsed.get("geometry") if isinstance(parsed, dict) else None
    if (
        not isinstance(geometry, dict)
        or any(geometry.get(k) != v for k, v in expected.items())
        or any(k in geometry for k in remove)
    ):
        raise YamlEditError("edited config does not round-trip")
    if updated == original:
        return False
    atomic_write(config_path, updated)
    return True


def write_camera(config_path: Path, values: dict[str, str]) -> bool:
    """Set scalar keys of the config's ``camera:`` section (values as YAML text); True if changed."""
    original = config_path.read_text(encoding="utf-8")
    updated = update_section(original, "camera", values)
    parsed = yaml.safe_load(updated) or {}
    camera = parsed.get("camera") if isinstance(parsed, dict) else None
    if not isinstance(camera, dict) or any(
        camera.get(k) != yaml.safe_load(v) for k, v in values.items()
    ):
        raise YamlEditError("edited config does not round-trip")
    if updated == original:
        return False
    atomic_write(config_path, updated)
    return True


def read_config(config_path: Path) -> dict[str, Any]:
    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def config_cam_id(config_path: Path) -> int:
    value = read_config(config_path).get("cam_id", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


QUALITY_PIXEL_KEYS = ("line_corners", "outer_line_corners")


class QualityError(ValueError):
    pass


def quality_presets(camera: Any) -> dict[str, tuple[int, int]]:
    """``camera.quality_presets`` ({name: [width, height]}) of a vision config, {} if absent/invalid."""
    raw = camera.get("quality_presets") if isinstance(camera, dict) else None
    if not isinstance(raw, dict):
        return {}
    presets: dict[str, tuple[int, int]] = {}
    for name, size in raw.items():
        if (
            isinstance(name, str)
            and isinstance(size, list)
            and len(size) == 2
            and all(
                isinstance(v, int) and not isinstance(v, bool) and v > 0 for v in size
            )
        ):
            presets[name] = (size[0], size[1])
    return presets


def _scale(points: list[Point], factor: float) -> list[Point]:
    # Rounded like _format_point writes them, so write_geometry's round-trip check holds
    return [(round(p[0] * factor, 3), round(p[1] * factor, 3)) for p in points]


class FieldCalibration:
    def __init__(
        self,
        geometry: Geometry,
        supervisor: VisionSupervisor,
        vision_config: Path,
        img_dir: Path,
    ) -> None:
        self._geometry = geometry
        self._supervisor = supervisor
        self._config = vision_config
        self._img_dir = img_dir
        self._lock = asyncio.Lock()
        # cam_id -> (wall time, monotonic time) of the last corner save.
        self._saved: dict[int, tuple[float, float]] = {}

    def image_size(self, cam_id: int) -> tuple[int, int]:
        return jpeg_size(self._img_dir / f"{cam_id}.raw.jpg") or DEFAULT_IMAGE_SIZE

    def calib_json(self, cam_id: int) -> dict[str, Any] | None:
        """Newest ``img/*.calib.json`` for ``cam_id``.

        vision_processor names it after the camera *path*
        (``img/<camera path with / -> _>.calib.json``), not the id.
        """
        best: tuple[float, Path, dict[str, Any]] | None = None
        if not self._img_dir.is_dir():
            return None
        for path in self._img_dir.glob("*.calib.json"):
            try:
                mtime = path.stat().st_mtime
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("camera_id") != cam_id:
                continue
            if best is None or mtime > best[0]:
                best = (mtime, path, data)
        if best is None:
            return None
        mtime, path, data = best
        error_rate = data.get("error_rate")
        return {
            "path": str(path),
            "modified_at": mtime,
            "age_s": round(max(0.0, time.time() - mtime), 1),
            "line_corners": data.get("line_corners"),
            "refinement_enabled": data.get("refinement_enabled"),
            "focal_length": data.get("focal_length"),
            "position": data.get("position"),
            "euler": data.get("euler"),
            "distortion_k2": data.get("distortion_k2"),
            "principal_point": data.get("principal_point"),
            # Meaningless without field lines (no line pixels to score).
            "error_rate": error_rate if isinstance(error_rate, (int, float)) else None,
        }

    def state(self, cam_id: int) -> str:
        has_calib = cam_id in self._geometry.calibrated_cameras()
        saved = self._saved.get(cam_id)
        if saved is not None:
            received = self._geometry.calib_received_at.get(cam_id)
            calib = self.calib_json(cam_id)
            fresh_json = calib is not None and calib["modified_at"] > saved[0]
            if (has_calib and received is not None and received > saved[1]) or (
                fresh_json and has_calib
            ):
                del self._saved[cam_id]
            else:
                return "recalibrating"
        return "calibrated" if has_calib else "not_calibrated"

    def status(self, cam_id: int) -> dict[str, Any]:
        config = read_config(self._config)
        geometry_cfg = config.get("geometry")
        geometry_cfg = geometry_cfg if isinstance(geometry_cfg, dict) else {}
        field = self._geometry.geometry_data.field
        width, height = self.image_size(cam_id)
        saved = self._saved.get(cam_id)
        state = self.state(cam_id)
        return {
            "cam_id": cam_id,
            "state": state,
            "calibrated": cam_id in self._geometry.calibrated_cameras(),
            "calibrated_cameras": self._geometry.calibrated_cameras(),
            "saved_at": saved[0] if saved and state == "recalibrating" else None,
            "corners": geometry_cfg.get("line_corners"),
            "outer_corners": geometry_cfg.get("outer_line_corners"),
            "include_boundary": geometry_cfg.get(
                "line_corners_include_boundary", False
            ),
            "distortion_lines": geometry_cfg.get("distortion_lines"),
            "boundary_width": field.boundary_width,
            "boundary_width_goal_line": (
                field.boundary_width_goal_line
                if field.HasField("boundary_width_goal_line")
                else field.boundary_width
            ),
            "refinement": geometry_cfg.get("refinement", True),
            "field_length": field.field_length,
            "field_width": field.field_width,
            "image_width": width,
            "image_height": height,
            "calib_json": self.calib_json(cam_id),
            "config_path": str(self._config),
        }

    def _boundaries(self) -> tuple[float, float, float, float]:
        field = self._geometry.geometry_data.field
        if field.field_length <= 0 or field.field_width <= 0:
            raise CornerError("the published geometry has no field size")
        boundary = float(field.boundary_width)
        goal_line = (
            float(field.boundary_width_goal_line)
            if field.HasField("boundary_width_goal_line")
            else boundary
        )
        return float(field.field_length), float(field.field_width), boundary, goal_line

    def _corner_values(
        self, outer: list[Point] | None, field_corners: list[Point] | None, lens: bool
    ) -> tuple[dict[str, Any], list[str]]:
        """Config keys for the corners, following the lens rule.

        Outer clicks + lens correction: written directly as ``line_corners``
        with ``line_corners_include_boundary: true`` (vision_processor fits
        them through its lens model). Outer clicks without lens correction:
        field corners from the flat homography. Field clicks: as they are.
        """
        values: dict[str, Any] = {}
        remove: list[str] = []
        if outer is not None:
            values["outer_line_corners"] = outer
            if lens:
                values["line_corners"] = outer
                values["line_corners_include_boundary"] = True
            else:
                values["line_corners"] = inner_from_outer(outer, *self._boundaries())
                remove.append("line_corners_include_boundary")
        elif field_corners is not None:
            values["line_corners"] = field_corners
            remove += ["outer_line_corners", "line_corners_include_boundary"]
        values["refinement"] = False
        return values, remove

    async def _apply(
        self,
        cam_id: int,
        values: dict[str, Any],
        remove: list[str],
        what: str,
        camera_values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Write the config, then stop -> clear calibs + broadcast -> start."""
        async with self._lock:
            original = self._config.read_text(encoding="utf-8")
            try:
                changed = write_geometry(self._config, values, remove)
                if camera_values:
                    changed = write_camera(self._config, camera_values) or changed
            except Exception:
                # All or nothing: a size change without its rescaled corners would break calibration
                atomic_write(self._config, original)
                raise
            sup = self._supervisor.status()
            restart = sup["running"] or sup["want_running"]
            camera_ids = set(self._geometry.calibrated_cameras()) | {cam_id}
            cleared: list[int] = []

            def clear() -> None:
                # vision_processor only recalibrates when the geometry it
                # receives carries no calib at all, so clear every camera.
                cleared.extend(self._geometry.clear_calibs(camera_ids))
                self._saved[cam_id] = (time.time(), time.monotonic())

            restarted = False
            if restart:
                await self._supervisor.restart(between=clear)
                restarted = True
                message = "Saved. vision_processor restarted and is recalibrating."
            else:
                clear()
                message = (
                    "Saved. vision_processor is not running: start it to "
                    f"calibrate with the new {what}."
                )
            others = sorted(set(cleared) - {cam_id})
            if others:
                message += (
                    f" Calibrations of camera(s) {others} were cleared too; "
                    "their vision_processors recalibrate on their own."
                )
        corners = values.get("line_corners")
        return {
            "cam_id": cam_id,
            "corners": (
                [[p[0], p[1]] for p in corners] if corners is not None else None
            ),
            "include_boundary": values.get("line_corners_include_boundary", False),
            "config_changed": changed,
            "config_path": str(self._config),
            "cleared_cameras": cleared,
            "restarted": restarted,
            "message": message,
        }

    def _geometry_cfg(self) -> dict[str, Any]:
        geometry_cfg = read_config(self._config).get("geometry")
        return geometry_cfg if isinstance(geometry_cfg, dict) else {}

    async def save_corners(
        self, cam_id: int, raw: Any, mode: str = "outer"
    ) -> dict[str, Any]:
        if mode not in ("outer", "field"):
            raise CornerError("'mode' must be 'outer' or 'field'")
        width, height = self.image_size(cam_id)
        clicked = validate_corners(raw, width, height)
        given = [(round(float(p[0]), 1), round(float(p[1]), 1)) for p in raw]
        lens = _lines(self._geometry_cfg().get("distortion_lines")) is not None
        if mode == "outer":
            values, remove = self._corner_values(clicked, None, lens)
        else:
            values, remove = self._corner_values(None, clicked, lens)
        result = await self._apply(cam_id, values, remove, "corners")
        return {
            **result,
            "mode": mode,
            "outer_corners": (
                [[p[0], p[1]] for p in clicked] if mode == "outer" else None
            ),
            "reordered": clicked != given,
        }

    def quality(self) -> dict[str, Any]:
        camera = read_config(self._config).get("camera")
        camera = camera if isinstance(camera, dict) else {}
        presets = quality_presets(camera)
        current = camera.get("quality")
        return {
            "presets": {name: [w, h] for name, (w, h) in presets.items()},
            "current": current if current in presets else None,
            "size": [camera.get("output_width"), camera.get("output_height")],
        }

    async def set_quality(self, cam_id: int, name: Any) -> dict[str, Any]:
        """Switch ``camera.output_width/height`` to a preset, rescaling the pixel geometry
        (corners, lens lines) so the calibration stays valid, then clear calibs + restart."""
        camera = read_config(self._config).get("camera")
        camera = camera if isinstance(camera, dict) else {}
        presets = quality_presets(camera)
        if not presets:
            raise QualityError("this camera config has no camera.quality_presets")
        if not isinstance(name, str) or name not in presets:
            raise QualityError(f"'quality' must be one of {sorted(presets)}")
        width, height = presets[name]
        old_width, old_height = camera.get("output_width"), camera.get("output_height")
        if (
            not isinstance(old_width, int)
            or not isinstance(old_height, int)
            or old_width <= 0
            or old_height <= 0
        ):
            raise QualityError(
                "camera.output_width/output_height must be set to scale the corners"
            )
        factor = width / old_width
        if abs(height / old_height - factor) > 0.01:
            raise QualityError(
                f"{width}x{height} has a different aspect ratio from {old_width}x{old_height}; "
                "corners can't be rescaled, click them again instead"
            )
        geometry_cfg = self._geometry_cfg()
        values: dict[str, Any] = {}
        for key in QUALITY_PIXEL_KEYS:
            points = _points(geometry_cfg.get(key), 4)
            if points is not None:
                values[key] = _scale(points, factor)
        lines = _lines(geometry_cfg.get("distortion_lines"))
        if lines is not None:
            values["distortion_lines"] = [_scale(line, factor) for line in lines]
        result = await self._apply(
            cam_id,
            values,
            [],
            "quality",
            {"quality": name, "output_width": str(width), "output_height": str(height)},
        )
        return {**result, "quality": name, "size": [width, height]}

    async def recalibrate(self, cam_id: int) -> dict[str, Any]:
        """Clear the calibs and restart vision_processor (config unchanged)."""
        return await self._apply(cam_id, {}, [], "settings")

    async def set_refinement(
        self, cam_id: int, enabled: bool, restart: bool
    ) -> dict[str, Any]:
        """Set ``geometry.refinement`` (field-line refinement of the fit)."""
        if restart:
            result = await self._apply(cam_id, {"refinement": enabled}, [], "settings")
        else:
            async with self._lock:
                changed = write_geometry(self._config, {"refinement": enabled}, [])
            result = {
                "config_changed": changed,
                "restarted": False,
                "message": "Saved. Takes effect after a vision_processor restart.",
            }
        return {**result, "refinement": enabled}

    async def save_lens(self, cam_id: int, raw: Any | None) -> dict[str, Any]:
        """Set (``raw`` = lines) or remove (``raw`` None) lens correction."""
        cfg = self._geometry_cfg()
        outer = _points(cfg.get("outer_line_corners"), 4)
        if raw is None:
            values: dict[str, Any] = {}
            remove = ["distortion_lines"]
            if outer is not None:
                corner_values, corner_remove = self._corner_values(outer, None, False)
                values.update(corner_values)
                remove += corner_remove
            else:
                remove.append("line_corners_include_boundary")
                values["refinement"] = False
            return await self._apply(cam_id, values, remove, "settings")
        width, height = self.image_size(cam_id)
        lines = validate_lens_lines(raw, width, height)
        values = {"distortion_lines": lines}
        remove = []
        if outer is not None:
            corner_values, corner_remove = self._corner_values(outer, None, True)
            values.update(corner_values)
            remove += corner_remove
        else:
            values["refinement"] = False
        result = await self._apply(cam_id, values, remove, "lens correction")
        return {**result, "lines": len(lines)}


def _cam_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CornerError("'cam_id' must be a non-negative integer")
    return value


def register(http_app: web.Application, calibration: FieldCalibration) -> None:
    async def status_handler(request: web.Request) -> web.Response:
        try:
            cam_id = int(request.query.get("cam_id", "0"))
        except ValueError:
            return web.json_response({"error": "cam_id must be an integer"}, status=400)
        return web.json_response(calibration.status(cam_id))

    async def corners_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        if not isinstance(body, dict) or not set(body) <= {"cam_id", "corners", "mode"}:
            return web.json_response(
                {
                    "error": 'expected {"cam_id": N, "corners": [[x, y] x 4], '
                    '"mode": "outer" | "field"}'
                },
                status=400,
            )
        try:
            cam_id = _cam_id(body.get("cam_id", 0))
            mode = body.get("mode", "outer")
            if not isinstance(mode, str):
                raise CornerError("'mode' must be 'outer' or 'field'")
            result = await calibration.save_corners(cam_id, body.get("corners"), mode)
        except CornerError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except SupervisorError as exc:
            return web.json_response(
                {"error": f"corners saved, but restarting failed: {exc}"},
                status=exc.status,
            )
        except (OSError, yaml.YAMLError, YamlEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    async def lens_handler(request: web.Request) -> web.Response:
        try:
            text = await request.text()
            body = json.loads(text) if text.strip() else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        if not isinstance(body, dict) or not set(body) <= {"cam_id", "lines"}:
            return web.json_response(
                {"error": 'expected {"cam_id": N, "lines": [[[x, y], ...], ...]}'},
                status=400,
            )
        remove = request.method == "DELETE"
        if not remove and "lines" not in body:
            return web.json_response({"error": "'lines' is required"}, status=400)
        try:
            cam_id = _cam_id(body.get("cam_id", 0))
            result = await calibration.save_lens(
                cam_id, None if remove else body["lines"]
            )
        except CornerError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except SupervisorError as exc:
            return web.json_response(
                {"error": f"saved, but restarting failed: {exc}"}, status=exc.status
            )
        except (OSError, yaml.YAMLError, YamlEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    async def recalibrate_handler(request: web.Request) -> web.Response:
        try:
            text = await request.text()
            body = json.loads(text) if text.strip() else {}
            if not isinstance(body, dict):
                raise CornerError("body must be a JSON object")
            result = await calibration.recalibrate(_cam_id(body.get("cam_id", 0)))
        except (json.JSONDecodeError, UnicodeDecodeError, CornerError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except SupervisorError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        except (OSError, yaml.YAMLError, YamlEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    async def refinement_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("enabled"), bool)
                or not isinstance(body.get("restart", False), bool)
            ):
                raise CornerError(
                    'expected {"enabled": bool, "restart": bool, "cam_id": N}'
                )
            result = await calibration.set_refinement(
                _cam_id(body.get("cam_id", 0)),
                body["enabled"],
                body.get("restart", False),
            )
        except (json.JSONDecodeError, UnicodeDecodeError, CornerError) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except SupervisorError as exc:
            return web.json_response({"error": str(exc)}, status=exc.status)
        except (OSError, yaml.YAMLError, YamlEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    async def quality_get_handler(_: web.Request) -> web.Response:
        return web.json_response(calibration.quality())

    async def quality_post_handler(request: web.Request) -> web.Response:
        try:
            body = await request.json()
            if not isinstance(body, dict) or not set(body) <= {"cam_id", "quality"}:
                raise QualityError('expected {"quality": "<preset name>", "cam_id": N}')
            result = await calibration.set_quality(
                _cam_id(body.get("cam_id", 0)), body.get("quality")
            )
        except (
            json.JSONDecodeError,
            UnicodeDecodeError,
            QualityError,
            CornerError,
        ) as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except SupervisorError as exc:
            return web.json_response(
                {"error": f"saved, but restarting failed: {exc}"}, status=exc.status
            )
        except (OSError, yaml.YAMLError, YamlEditError) as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    http_app.router.add_get("/api/camera/quality", quality_get_handler)
    http_app.router.add_post("/api/camera/quality", quality_post_handler)
    http_app.router.add_post("/api/calibration/recalibrate", recalibrate_handler)
    http_app.router.add_post("/api/calibration/refinement", refinement_handler)
    http_app.router.add_get("/api/calibration", status_handler)
    http_app.router.add_post("/api/calibration/lens", lens_handler)
    http_app.router.add_delete("/api/calibration/lens", lens_handler)
    http_app.router.add_post("/api/calibration/corners", corners_handler)

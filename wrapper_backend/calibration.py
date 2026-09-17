"""Sample colour references from the newest debug frame and write them back.

The C++ side re-reads the `color:` and `thresholds:` blocks of its config
within about half a second, so writing the file is enough to apply a
calibration - nothing has to be restarted.

The sampling mirrors what an operator would otherwise do by eye: find the
carpet, find the saturated marks sitting on it, and take each mark's core
colour. Cores rather than means, because a marker is only a handful of
pixels across and its edge blends into the carpet, which drags a plain
average toward green.
"""

from __future__ import annotations

import io
import logging
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
from aiohttp import web
from PIL import Image

from wrapper_backend.bus import Bus

log = logging.getLogger("wrapper_backend.calibration")

# A marker or the ball covers roughly this many pixels in the debug frame.
# Anything larger is carpet that happens to share a hue.
MIN_MARK_PX = 5
MAX_MARK_PX = 250

# How far from a reported object position to look for its marks, in pixels.
# A robot's side markers sit well inside this of its centre.
ANCHOR_PX = 14

# Hue bands in real degrees (0-360), not OpenCV's halved 0-179 scale, plus the
# test that separates a real mark from carpet that happens to fall in the same
# band. Green markers overlap the carpet's hue outright, so for them the
# brightness test is doing all the work. Colours are 0-255 RGB.
_BANDS: dict[str, tuple[float, float, Any]] = {
    "orange": (8, 40, lambda c: c[0] > 110 and c[0] > c[2] * 2),
    # Yellow needs a low blue channel too: a bright highlight on green
    # carpet lands in this hue band and would otherwise be accepted as a
    # yellow marker when no yellow robot is on the field at all.
    "yellow": (40, 70, lambda c: c[0] > 140 and c[1] > 130 and c[2] < c[0] * 0.6),
    "green": (120, 175, lambda c: c[1] > 125 and c[1] > c[0] * 1.8),
    "blue": (190, 265, lambda c: c[2] > 110 and c[2] > c[0] * 2),
    "pink": (300, 355, lambda c: c[0] > 120 and c[0] > c[1] * 1.4),
}


def _rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hue in degrees (0-360), saturation and value in 0-1."""
    arr = rgb.astype(np.float32) / 255.0
    mx = arr.max(axis=2)
    mn = arr.min(axis=2)
    diff = mx - mn
    hue = np.zeros_like(mx)
    safe = diff > 1e-6
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    hue = np.where(safe & (mx == r), (60 * (g - b) / np.where(safe, diff, 1)) % 360, hue)
    hue = np.where(safe & (mx == g), 60 * (b - r) / np.where(safe, diff, 1) + 120, hue)
    hue = np.where(safe & (mx == b), 60 * (r - g) / np.where(safe, diff, 1) + 240, hue)
    sat = np.where(mx > 1e-6, diff / np.where(mx > 1e-6, mx, 1), 0)
    return hue, sat, mx



def _blobs(mask: np.ndarray, min_area: int, max_area: int) -> list[np.ndarray]:
    """Group a boolean mask into connected regions within a size range.

    Written out rather than pulled from scipy or OpenCV: the mask is a few
    thousand pixels after thresholding, so a plain flood fill is fast enough
    for a button press and saves a large dependency.

    Size is the discriminator that matters here. A marker is a few dozen
    pixels; a stretch of carpet that happens to match a hue is thousands, and
    pooling both together produced a "green" reference that was mostly carpet.
    """
    height, width = mask.shape
    seen = np.zeros_like(mask)
    out: list[np.ndarray] = []
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if seen[sy, sx]:
            continue
        stack = [(sy, sx)]
        seen[sy, sx] = True
        region: list[tuple[int, int]] = []
        while stack:
            y, x = stack.pop()
            region.append((y, x))
            if len(region) > max_area:
                break
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < height and 0 <= nx < width and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        # Mark the whole region seen even when abandoned for being oversized,
        # so it is not revisited from another starting pixel.
        for y, x in region:
            seen[y, x] = True
        if min_area <= len(region) <= max_area:
            out.append(np.array(region))
    return out


def sample_colours(
    image_path: Path, anchors: dict[str, list[tuple[float, float]]] | None = None
) -> dict[str, Any]:
    """Read one frame and return the colour references it implies.

    `anchors` maps a colour name to image positions the detector reported
    for objects of that colour. Only those neighbourhoods are sampled.
    """
    rgb = np.asarray(Image.open(io.BytesIO(image_path.read_bytes())).convert("RGB"))
    hue, sat, val = _rgb_to_hsv(rgb)

    # The carpet is the green everything else sits on, and it bounds where a
    # mark can legitimately be: the barrier and the floor beyond it are not
    # part of the field and must not contribute.
    carpet = (hue > 90) & (hue < 180) & (sat > 0.25) & (val > 0.08)
    if carpet.sum() < 0.05 * carpet.size:
        raise ValueError("no carpet found in the frame - is the camera pointed at the field?")

    rows = np.where(carpet.any(axis=1))[0]
    cols = np.where(carpet.any(axis=0))[0]
    inset = 8
    box = np.zeros_like(carpet)
    box[rows[0] + inset : rows[-1] - inset, cols[0] + inset : cols[-1] - inset] = True

    # Tight enough that carpet - including the bright streak a low sun puts
    # across it - does not read as a marker.
    marks = box & (sat > 0.45) & (val > 0.40)

    out: dict[str, Any] = {"colors": {}, "skipped": {}, "pixels": {}, "blobs": {}}

    # Anchor on what the detector actually found. Searching the frame blind for
    # each colour cannot tell "this colour is not on the field" from "I did not
    # find it": with no orange ball and no yellow robot present, a bright streak
    # of sunlight on green carpet was accepted as both. Sampling only around
    # reported objects means a colour is calibrated when there is genuinely one
    # to calibrate against, and skipped otherwise.
    for name, points in (anchors or {}).items():
        band_lo, band_hi, test = _BANDS[name]
        accepted = []
        for px_x, px_y in points:
            x, y = int(round(px_x)), int(round(px_y))
            if not (0 <= x < rgb.shape[1] and 0 <= y < rgb.shape[0]):
                continue
            window = np.zeros_like(marks)
            window[max(0, y - ANCHOR_PX) : y + ANCHOR_PX + 1,
                   max(0, x - ANCHOR_PX) : x + ANCHOR_PX + 1] = True
            band = marks & window & (hue >= band_lo) & (hue <= band_hi)
            for region in _blobs(band, MIN_MARK_PX, MAX_MARK_PX):
                sample = rgb[region[:, 0], region[:, 1]]
                brightness = sample.astype(np.int32).sum(axis=1)
                core = sample[brightness >= np.percentile(brightness, 67)]
                mean = core.mean(axis=0)
                if test(mean):
                    accepted.append((len(core), mean))
        if not accepted:
            out["skipped"][name] = "no matching blob near any reported object"
            continue
        weights = [float(n) for n, _ in accepted]
        out["colors"][name] = [
            int(round(v)) for v in np.average([m for _, m in accepted], axis=0, weights=weights)
        ]
        out["pixels"][name] = int(sum(weights))
        out["blobs"][name] = len(accepted)

    for name in _BANDS:
        if name not in out["colors"] and name not in out["skipped"]:
            out["skipped"][name] = "nothing of this colour was detected on the field"

    # Carpet reference: the median of the carpet itself, away from the marks.
    field_px = rgb[box & carpet & ~marks]
    if len(field_px) > 100:
        out["colors"]["field"] = [int(round(v)) for v in np.median(field_px, axis=0)]
        out["pixels"]["field"] = int(len(field_px))

    return out


def apply_colours(config_path: Path, colours: dict[str, list[int]]) -> list[str]:
    """Rewrite just the colour values, leaving the rest of the file alone.

    Deliberately a regex over the text rather than a YAML round-trip: loading
    and dumping would silently discard every comment in the file, and this
    config is mostly comments explaining why the numbers are what they are.
    """
    text = config_path.read_text(encoding="utf-8")
    changed: list[str] = []
    for name, value in colours.items():
        pattern = re.compile(rf"(\n  {name}: )\[[^\]]*\]")
        if not pattern.search(text):
            continue
        replacement = r"\g<1>[%d, %d, %d]" % tuple(value)
        text, count = pattern.subn(replacement, text, count=1)
        if count:
            changed.append(name)
    if changed:
        shutil.copy2(config_path, config_path.with_suffix(config_path.suffix + ".precalib"))
        config_path.write_text(text, encoding="utf-8")
    return changed


class Calibration:
    """Keeps the newest detection frame so calibration has somewhere to look."""

    def __init__(self, bus: Bus) -> None:
        self._bus = bus
        self._latest: dict[int, Any] = {}

    async def run(self) -> None:
        queue = self._bus.subscribe("detection.in")
        while True:
            frame = await queue.get()
            self._latest[frame.camera_id] = frame

    def anchors(self, cam_id: int) -> dict[str, list[tuple[float, float]]]:
        """Where to look for each colour, from the detector's own output.

        A robot contributes its centre (team colour) and the surrounding area
        (the green and pink side markers); a ball contributes its own position.
        """
        frame = self._latest.get(cam_id)
        if frame is None:
            return {}
        found: dict[str, list[tuple[float, float]]] = {}
        for ball in frame.balls:
            found.setdefault("orange", []).append((ball.pixel_x, ball.pixel_y))
        for robot in frame.robots_blue:
            found.setdefault("blue", []).append((robot.pixel_x, robot.pixel_y))
        for robot in frame.robots_yellow:
            found.setdefault("yellow", []).append((robot.pixel_x, robot.pixel_y))
        # Side markers are green and pink on both teams, so every robot centre
        # is an anchor for both.
        for robot in list(frame.robots_blue) + list(frame.robots_yellow):
            found.setdefault("green", []).append((robot.pixel_x, robot.pixel_y))
            found.setdefault("pink", []).append((robot.pixel_x, robot.pixel_y))
        return found


def register(
    http_app: web.Application, vision_config: Path, img_dir: Path, state: Calibration
) -> None:
    async def handler(request: web.Request) -> web.Response:
        cam_id = request.match_info.get("cam_id", "0")
        frames = sorted(img_dir.glob(f"{cam_id}.raw.*"))
        if not frames:
            return web.json_response(
                {"error": f"no raw frame for camera {cam_id} in {img_dir}"}, status=404
            )
        frame = max(frames, key=lambda p: p.stat().st_mtime)

        try:
            sampled = sample_colours(frame, state.anchors(int(cam_id)))
        except (OSError, ValueError) as exc:
            return web.json_response({"error": str(exc)}, status=422)

        dry_run = request.query.get("apply") == "false"
        applied: list[str] = []
        if not dry_run and sampled["colors"]:
            applied = apply_colours(vision_config, sampled["colors"])
            log.info("colour calibration wrote %s to %s", applied, vision_config)

        return web.json_response(
            {
                "frame": str(frame),
                "sampled": sampled["colors"],
                "skipped": sampled["skipped"],
                "pixels": sampled["pixels"],
                "applied": applied,
                "config": str(vision_config),
                "dry_run": dry_run,
            }
        )

    # POST only: this writes a file, so it must not be reachable by a link,
    # a prefetch or a crawler.
    http_app.router.add_post("/api/calibrate/colors", handler)
    http_app.router.add_post("/api/calibrate/colors/{cam_id}", handler)

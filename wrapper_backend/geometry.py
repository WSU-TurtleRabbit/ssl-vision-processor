"""Owns geometry.yml + the in-memory SSL_WrapperPacket.

Two concurrent tasks:
- _absorb_loop: subscribe to geometry.in, replace-or-append per-camera
  calibrations into the SSL_WrapperPacket.
- _publish_loop: every second, the SSL_WrapperPacket on wrapper_packet.out.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from pathlib import Path

import yaml
from google.protobuf.json_format import ParseDict

from proto.ssl_vision_geometry_pb2 import (
    SSL_FieldShapeType,
    SSL_GeometryData,
)
from proto.ssl_vision_wrapper_pb2 import (
    SSL_SOURCE_VISION_PROCESSOR,
    SSL_WrapperPacket,
)
from wrapper_backend.bus import Bus

log = logging.getLogger("wrapper_backend.geometry")

PUBLISH_INTERVAL_S = 1.0
# After clearing a calibration, incoming calibs for that camera are ignored
# this long: the bus may still hold our own (multicast-looped) last packet
# carrying the old calib, which would otherwise be re-absorbed at once. A
# restarted vision_processor needs several seconds before it calibrates.
CLEAR_HOLD_S = 1.5


def _generate_field_markings(
    wrapper: SSL_WrapperPacket, field: dict, optional_lines: dict
) -> None:
    lines = wrapper.geometry.field.field_lines

    thickness = field["line_thickness"]
    half_length = field["field_length"] / 2
    half_width = field["field_width"] / 2
    penalty_length = half_length - field["penalty_area_depth"]
    half_penalty = field["penalty_area_width"] / 2

    def add_line(name: str, x1: float, y1: float, x2: float, y2: float) -> None:
        line = lines.add()
        line.name = name
        line.p1.x = x1
        line.p1.y = y1
        line.p2.x = x2
        line.p2.y = y2
        line.thickness = thickness
        line.type = SSL_FieldShapeType.Value(name)  # type: ignore[assignment]

    add_line("TopTouchLine", -half_length, half_width, half_length, half_width)
    add_line("BottomTouchLine", -half_length, -half_width, half_length, -half_width)
    add_line("LeftGoalLine", -half_length, -half_width, -half_length, half_width)
    add_line("RightGoalLine", half_length, -half_width, half_length, half_width)
    if optional_lines["halfway"]:
        add_line("HalfwayLine", 0, -half_width, 0, half_width)
    if optional_lines["goal2goal"]:
        add_line("CenterLine", -half_length, 0, half_length, 0)
    if optional_lines["penalty"]:
        add_line(
            "LeftPenaltyStretch",
            -penalty_length,
            -half_penalty,
            -penalty_length,
            half_penalty,
        )
        add_line(
            "RightPenaltyStretch",
            penalty_length,
            -half_penalty,
            penalty_length,
            half_penalty,
        )
        add_line(
            "LeftFieldLeftPenaltyStretch",
            -half_length,
            -half_penalty,
            -penalty_length,
            -half_penalty,
        )
        add_line(
            "LeftFieldRightPenaltyStretch",
            -half_length,
            half_penalty,
            -penalty_length,
            half_penalty,
        )
        add_line(
            "RightFieldLeftPenaltyStretch",
            penalty_length,
            half_penalty,
            half_length,
            half_penalty,
        )
        add_line(
            "RightFieldRightPenaltyStretch",
            penalty_length,
            -half_penalty,
            half_length,
            -half_penalty,
        )

    if optional_lines["centercircle"]:
        arc = wrapper.geometry.field.field_arcs.add()
        arc.name = "CenterCircle"
        arc.type = SSL_FieldShapeType.Value(arc.name)  # type: ignore[assignment]
        arc.center.x = 0.0
        arc.center.y = 0.0
        arc.radius = field["center_circle_radius"]
        arc.a1 = 0
        arc.a2 = math.tau
        arc.thickness = thickness


def load_geometry(path: Path) -> SSL_WrapperPacket:
    with path.open("r") as f:
        config = yaml.safe_load(f)
    return geometry_from_config(config)


def geometry_from_config(config: dict) -> SSL_WrapperPacket:
    """Build the packet from a parsed geometry.yml (strict, raises on typos)."""
    config = dict(config)
    optional_lines = config.pop("optional_field_lines")
    wrapper = SSL_WrapperPacket()
    ParseDict(config, wrapper.geometry)
    _generate_field_markings(wrapper, config["field"], optional_lines)
    wrapper.source = SSL_SOURCE_VISION_PROCESSOR
    return wrapper


class Geometry:
    def __init__(self, bus: Bus, geometry_yml_path: Path) -> None:
        self._bus = bus
        self.path = geometry_yml_path
        self._wrapper = load_geometry(geometry_yml_path)
        log.info(
            "loaded %s with %d initial calib(s)",
            geometry_yml_path,
            len(self._wrapper.geometry.calib),
        )
        # camera_id -> monotonic deadline until which its calibs are ignored.
        self._hold_until: dict[int, float] = {}
        # camera_id -> monotonic time its calib was last absorbed.
        self.calib_received_at: dict[int, float] = {}

    async def run(self) -> None:
        await asyncio.gather(
            self._absorb_loop(),
            self._publish_loop(),
        )

    async def _absorb_loop(self) -> None:
        queue = self._bus.subscribe("geometry.in")
        while True:
            incoming: SSL_GeometryData = await queue.get()
            self._merge_calibs(incoming)

    async def _publish_loop(self) -> None:
        while True:
            self._bus.publish("wrapper_packet.out", self._wrapper.SerializeToString())
            await asyncio.sleep(PUBLISH_INTERVAL_S)

    def replace(self, wrapper: SSL_WrapperPacket) -> None:
        """Swap in a newly loaded geometry, keeping the absorbed calibs."""
        calibs = [c.SerializeToString() for c in self._wrapper.geometry.calib]
        del wrapper.geometry.calib[:]
        for data in calibs:
            wrapper.geometry.calib.add().ParseFromString(data)
        self._wrapper = wrapper
        log.info("reloaded field geometry from %s", self.path)
        self.publish_now()

    def publish_now(self) -> None:
        """Emit the current state immediately (outside the 1 Hz tick)."""
        self._bus.publish("wrapper_packet.out", self._wrapper.SerializeToString())

    @property
    def geometry_data(self) -> SSL_GeometryData:
        return self._wrapper.geometry

    def calibrated_cameras(self) -> list[int]:
        return sorted(c.camera_id for c in self._wrapper.geometry.calib)

    def clear_calibs(self, camera_ids: set[int]) -> list[int]:
        """Drop the calibs of ``camera_ids`` and broadcast the result.

        vision_processor only recalibrates when the geometry it receives has
        no calib at all (``calib_size() == 0`` in ``Perspective::
        geometryCheck``), so callers clear *every* camera when they want one
        to recalibrate; other cameras' running processors then recalibrate
        too and re-send theirs. Returns the ids that were actually removed.
        """
        calib = self._wrapper.geometry.calib
        removed = [c.camera_id for c in calib if c.camera_id in camera_ids]
        keep = [c.SerializeToString() for c in calib if c.camera_id not in camera_ids]
        del calib[:]
        for data in keep:
            calib.add().ParseFromString(data)
        deadline = time.monotonic() + CLEAR_HOLD_S
        for camera_id in camera_ids:
            self._hold_until[camera_id] = deadline
            self.calib_received_at.pop(camera_id, None)
        if removed:
            log.info("cleared calibration of camera(s) %s", removed)
        self.publish_now()
        return removed

    def _merge_calibs(self, incoming: SSL_GeometryData) -> None:
        calib = self._wrapper.geometry.calib
        by_id = {c.camera_id: c for c in calib}
        now = time.monotonic()
        for camera in incoming.calib:
            if now < self._hold_until.get(camera.camera_id, 0.0):
                continue
            existing = by_id.get(camera.camera_id)

            # Initial calibration received for camera. Store it
            if existing is None:
                calib.append(camera)
                self.calib_received_at[camera.camera_id] = now
                log.info("Added camera %d calibration", camera.camera_id)
                continue
            incoming_bytes = camera.SerializeToString(deterministic=True)

            # Don't update if nothing changed
            if existing.SerializeToString(deterministic=True) == incoming_bytes:
                continue
            existing.CopyFrom(camera)
            self.calib_received_at[camera.camera_id] = now
            log.info("Updated camera %d calibration", camera.camera_id)

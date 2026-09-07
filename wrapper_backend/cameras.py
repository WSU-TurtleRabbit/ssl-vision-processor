"""Per-camera liveness, rate and latency, aggregated from inbound detections.

`multicast.py` publishes one small dict per detection datagram on
`camera_frame.in`; this module folds those into per-camera state and
re-publishes the whole roster on `cameras.out` once a second.

Rates are derived from `frame_number` deltas rather than by counting
arrivals: bus subscribers have size-1 queues and drop intermediate
messages under load, so counting what we happen to read would undercount.
Frame numbers are monotonic per camera, so the delta stays correct even
when reads are missed - and the gap between the delta and the number of
messages actually seen is itself worth reporting.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from typing import Any

from aiohttp import web

from wrapper_backend.bus import Bus

log = logging.getLogger("wrapper_backend.cameras")

PUBLISH_INTERVAL_S = 1.0

# A camera is "online" while a frame has arrived within this window. Two
# seconds is long enough not to flicker at low frame rates and short enough
# that an operator notices a dead feed quickly.
ONLINE_TIMEOUT_S = 2.0

# Rates are reported over a trailing window rather than since start, so the
# number tracks reality after a camera stalls and recovers.
RATE_WINDOW_S = 5.0


class _Camera:
    def __init__(self, camera_id: int, address: str) -> None:
        self.camera_id = camera_id
        self.address = address
        self.hostname: str | None = None
        self.last_seen = 0.0
        self.last_frame_number = 0
        self.latency_s = 0.0
        # (local monotonic, sender t_capture, frame_number, cumulative
        # arrivals) trimmed to RATE_WINDOW_S.
        self.samples: list[tuple[float, float, int, int]] = []

    def observe(self, frame: dict[str, Any], now: float) -> None:
        self.address = frame["address"]
        self.last_seen = now
        self.last_frame_number = frame["frame_number"]
        # Both timestamps are set by the sender, so this needs no clock
        # agreement between machines: it is the detector's own capture-to-send
        # time, not an end-to-end network latency.
        self.latency_s = frame["t_sent"] - frame["t_capture"]
        self.samples.append(
            (now, frame["t_capture"], frame["frame_number"], frame["received"])
        )
        cutoff = now - RATE_WINDOW_S
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.pop(0)

    def capture_fps(self) -> float:
        """The rate the camera is actually capturing at.

        Frame numbers over t_capture, both stamped by the sender, so this is
        measured entirely on the detector's own clock: no agreement with our
        clock is needed and network jitter cannot affect it. Frame numbers are
        monotonic, so dropped datagrams do not bias it - a gap enlarges the
        numerator and denominator together.
        """
        if len(self.samples) < 2:
            return 0.0
        first, last = self.samples[0], self.samples[-1]
        elapsed = last[1] - first[1]
        return (last[2] - first[2]) / elapsed if elapsed > 0 else 0.0

    def received_fps(self) -> float:
        """The rate datagrams are actually reaching us, on our clock."""
        if len(self.samples) < 2:
            return 0.0
        first, last = self.samples[0], self.samples[-1]
        elapsed = last[0] - first[0]
        return (last[3] - first[3]) / elapsed if elapsed > 0 else 0.0

    def loss_percent(self) -> float:
        """How much of what the detector produced never arrived."""
        if len(self.samples) < 2:
            return 0.0
        first, last = self.samples[0], self.samples[-1]
        produced = last[2] - first[2]
        arrived = last[3] - first[3]
        if produced <= 0:
            return 0.0
        return max(0.0, round(100.0 * (produced - arrived) / produced, 1))

    def to_dict(self, now: float) -> dict[str, Any]:
        online = (now - self.last_seen) <= ONLINE_TIMEOUT_S
        return {
            "camera_id": self.camera_id,
            "address": self.address,
            "name": self.hostname or self.address,
            "online": online,
            "fps": round(self.capture_fps(), 1),
            "received_fps": round(self.received_fps(), 1),
            "loss_percent": self.loss_percent(),
            "latency_ms": round(self.latency_s * 1000.0, 1),
            "frame_number": self.last_frame_number,
            "age_s": round(now - self.last_seen, 2) if self.last_seen else None,
        }


class Cameras:
    def __init__(self, bus: Bus) -> None:
        self._bus = bus
        self._cameras: dict[int, _Camera] = {}
        self._resolving: set[str] = set()

    async def _resolve_hostname(self, camera: _Camera, address: str) -> None:
        """Best-effort reverse lookup for a friendlier default name.

        Lab networks frequently have no PTR records, so failure is the normal
        case and simply leaves the address showing.
        """
        loop = asyncio.get_running_loop()
        try:
            host, _ = await loop.getnameinfo((address, 0), 0)
            if host and host != address:
                camera.hostname = host
        except (OSError, socket.gaierror):
            pass
        finally:
            self._resolving.discard(address)

    async def _absorb_loop(self) -> None:
        queue = self._bus.subscribe("camera_frame.in")
        while True:
            frame: dict[str, Any] = await queue.get()
            now = time.monotonic()
            camera_id = frame["camera_id"]
            camera = self._cameras.get(camera_id)
            if camera is None:
                camera = _Camera(camera_id, frame["address"])
                self._cameras[camera_id] = camera
                log.info("camera %d first seen at %s", camera_id, frame["address"])
            camera.observe(frame, now)

            address = camera.address
            if camera.hostname is None and address not in self._resolving:
                self._resolving.add(address)
                asyncio.create_task(self._resolve_hostname(camera, address))

    def roster(self) -> dict[str, Any]:
        """Current camera state, for the bus and the HTTP endpoint alike."""
        now = time.monotonic()
        cameras = [c.to_dict(now) for c in self._cameras.values()]
        cameras.sort(key=lambda c: c["camera_id"])
        online = [c for c in cameras if c["online"]]
        return {
            "cameras": cameras,
            "combined": {
                "count": len(cameras),
                "online": len(online),
                "fps": round(sum(c["fps"] for c in online), 1),
                "latency_ms": (
                    round(max(c["latency_ms"] for c in online), 1) if online else 0.0
                ),
            },
        }

    def register(self, http_app: web.Application) -> None:
        async def cameras_handler(_: web.Request) -> web.Response:
            return web.json_response(self.roster())

        http_app.router.add_get("/api/cameras", cameras_handler)

    async def _publish_loop(self) -> None:
        while True:
            self._bus.publish("cameras.out", self.roster())
            await asyncio.sleep(PUBLISH_INTERVAL_S)

    async def run(self) -> None:
        await asyncio.gather(self._absorb_loop(), self._publish_loop())

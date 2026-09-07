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
        # (monotonic, frame_number) samples trimmed to RATE_WINDOW_S.
        self.samples: list[tuple[float, int]] = []
        self.messages_seen = 0

    def observe(self, frame: dict[str, Any], now: float) -> None:
        self.address = frame["address"]
        self.last_seen = now
        self.last_frame_number = frame["frame_number"]
        # Both timestamps are set by the sender, so this needs no clock
        # agreement between machines: it is the detector's own capture-to-send
        # time, not an end-to-end network latency.
        self.latency_s = frame["t_sent"] - frame["t_capture"]
        self.messages_seen += 1
        self.samples.append((now, frame["frame_number"]))
        cutoff = now - RATE_WINDOW_S
        while len(self.samples) > 2 and self.samples[0][0] < cutoff:
            self.samples.pop(0)

    def fps(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        (t0, f0), (t1, f1) = self.samples[0], self.samples[-1]
        elapsed = t1 - t0
        return (f1 - f0) / elapsed if elapsed > 0 else 0.0

    def to_dict(self, now: float) -> dict[str, Any]:
        online = (now - self.last_seen) <= ONLINE_TIMEOUT_S
        return {
            "camera_id": self.camera_id,
            "address": self.address,
            "name": self.hostname or self.address,
            "online": online,
            "fps": round(self.fps(), 1),
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

    async def _publish_loop(self) -> None:
        while True:
            now = time.monotonic()
            roster = [c.to_dict(now) for c in self._cameras.values()]
            roster.sort(key=lambda c: c["camera_id"])
            online = [c for c in roster if c["online"]]
            self._bus.publish(
                "cameras.out",
                {
                    "cameras": roster,
                    "combined": {
                        "count": len(roster),
                        "online": len(online),
                        "fps": round(sum(c["fps"] for c in online), 1),
                        "latency_ms": (
                            round(max(c["latency_ms"] for c in online), 1)
                            if online
                            else 0.0
                        ),
                    },
                },
            )
            await asyncio.sleep(PUBLISH_INTERVAL_S)

    async def run(self) -> None:
        await asyncio.gather(self._absorb_loop(), self._publish_loop())

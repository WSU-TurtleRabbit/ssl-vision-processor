"""Per-camera detection metrics over a rolling 5 s window.

``GET /api/metrics`` -> ``{"window_s": 5, "cameras": {"<id>": {...}}}`` with,
per ``camera_id`` seen on ``detection.in``:

- ``rate_hz`` — detection frames per second received by the backend,
- ``processing_ms`` — ``t_sent - t_capture`` (vision_processor's own time
  stamps: capture -> packet sent),
- ``receive_ms`` — backend receive time - ``t_sent``; only meaningful when
  both clocks agree (same machine or NTP-synced; ``None`` beyond +-10 s),
- ``robots_per_frame`` / ``balls_per_frame`` averages and ``frames``.

Receive time is taken when this module's bus subscriber wakes up; the bus is
size-1 per subscriber, so under heavy load frames can be missed (the rate is
then what the backend actually processed).
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import statistics
import time
from collections.abc import Callable
from typing import Any

from aiohttp import web

from proto.ssl_vision_detection_pb2 import SSL_DetectionFrame
from wrapper_backend.bus import Bus

WINDOW_S = 5.0
MAX_CLOCK_SKEW_S = 10.0

# (receive wall time, t_capture, t_sent, robots, balls)
Sample = tuple[float, float, float, int, int]


def summarize(samples: collections.deque[Sample], now: float) -> dict[str, Any]:
    while samples and now - samples[0][0] > WINDOW_S:
        samples.popleft()
    if not samples:
        return {"frames": 0, "rate_hz": 0.0}
    span = max(1e-3, min(WINDOW_S, now - samples[0][0]))
    processing = [(s[2] - s[1]) * 1000 for s in samples if s[1] > 0 and s[2] > 0]
    receive = [
        (s[0] - s[2]) * 1000
        for s in samples
        if s[2] > 0 and abs(s[0] - s[2]) < MAX_CLOCK_SKEW_S
    ]
    return {
        "frames": len(samples),
        "rate_hz": round(len(samples) / span, 1),
        "processing_ms": round(statistics.median(processing), 1)
        if processing
        else None,
        "receive_ms": round(statistics.median(receive), 1) if receive else None,
        "robots_per_frame": round(sum(s[3] for s in samples) / len(samples), 2),
        "balls_per_frame": round(sum(s[4] for s in samples) / len(samples), 2),
        "last_age_s": round(now - samples[-1][0], 2),
    }


def register(http_app: web.Application, bus: Bus) -> Callable[[], dict[str, Any]]:
    """Registers the route; returns a snapshot function (used by the receipt)."""
    cameras: dict[int, collections.deque[Sample]] = {}
    task_key = web.AppKey("metrics_task", asyncio.Task[None])

    async def watch() -> None:
        queue = bus.subscribe("detection.in")
        try:
            while True:
                frame: SSL_DetectionFrame = await queue.get()
                cameras.setdefault(frame.camera_id, collections.deque()).append(
                    (
                        time.time(),
                        frame.t_capture,
                        frame.t_sent,
                        len(frame.robots_blue) + len(frame.robots_yellow),
                        len(frame.balls),
                    )
                )
        finally:
            bus.unsubscribe("detection.in", queue)

    async def start(app: web.Application) -> None:
        app[task_key] = asyncio.create_task(watch(), name="metrics")

    async def stop(app: web.Application) -> None:
        app[task_key].cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app[task_key]

    async def handler(_: web.Request) -> web.Response:
        now = time.time()
        return web.json_response(
            {
                "window_s": WINDOW_S,
                "cameras": {
                    str(cam): summarize(samples, now)
                    for cam, samples in sorted(cameras.items())
                },
            }
        )

    http_app.router.add_get("/api/metrics", handler)
    http_app.on_startup.append(start)
    http_app.on_cleanup.append(stop)

    def snapshot() -> dict[str, Any]:
        now = time.time()
        return {
            str(cam): summarize(samples, now)
            for cam, samples in sorted(cameras.items())
        }

    return snapshot

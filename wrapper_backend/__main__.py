"""CLI entrypoint for the wrapper.

Run with:

    uv run python -m wrapper_backend geometry.yml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from collections.abc import Awaitable, Callable

from aiohttp import web

from wrapper_backend import operator, snapshot, websocket
from wrapper_backend.cameras import Cameras
from wrapper_backend.bus import Bus
from wrapper_backend.geometry import Geometry
from wrapper_backend.multicast import Multicast

log = logging.getLogger("wrapper_backend")


@web.middleware
async def _cors_middleware(
    request: web.Request,
    handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
) -> web.StreamResponse:
    # Dev-mode wide-open CORS so the Vite dev server on :5173 can call
    # /snapshots and /ws from a different origin. Tighten before prod.
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


async def _main() -> None:
    parser = argparse.ArgumentParser(prog="wrapper")
    parser.add_argument("geometry", type=Path, help="geometry.yml path")
    parser.add_argument("--vision-ip", default="224.5.23.2")
    parser.add_argument("--vision-port", type=int, default=10006)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--vision-config", type=Path, default=Path("config.yml"))
    parser.add_argument(
        "--frontend-dir", type=Path, default=Path("wrapper-frontend/dist")
    )
    args = parser.parse_args()

    bus = Bus()
    multicast = Multicast(bus, args.vision_ip, args.vision_port)
    geometry = Geometry(bus, args.geometry)
    cameras = Cameras(bus)

    http_app = web.Application(middlewares=[_cors_middleware])
    websocket.register(http_app, bus)
    img_dir = args.vision_config.parent / "img"
    snapshot.register(http_app, img_dir)
    operator.register(
        http_app,
        args.vision_config,
        args.geometry,
        args.frontend_dir,
        img_dir,
    )

    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http_site = web.TCPSite(http_runner, args.host, args.port)
    await http_site.start()
    log.info("http+ws listening on %s:%d", args.host, args.port)

    cameras_task = asyncio.create_task(cameras.run(), name="cameras")
    try:
        await multicast.start()
        await geometry.run()
    finally:
        cameras_task.cancel()
        await http_runner.cleanup()
        await multicast.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(_main())


if __name__ == "__main__":
    main()

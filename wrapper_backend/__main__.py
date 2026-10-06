"""CLI entrypoint for the wrapper.

Run with:

    uv run python -m wrapper_backend geometry.yml
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

from collections.abc import Awaitable, Callable

from aiohttp import web

from wrapper_backend import (
    calibration,
    camera,
    colors,
    docs,
    fieldgeometry,
    gamecontroller,
    logs,
    metrics,
    operator,
    snapshot,
    supervisor,
    websocket,
)
from wrapper_backend.bus import Bus
from wrapper_backend.geometry import Geometry
from wrapper_backend.multicast import Multicast

log = logging.getLogger("wrapper_backend")

REPO_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = REPO_ROOT / "logs"


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
    parser.add_argument(
        "--vision-binary",
        type=Path,
        default=REPO_ROOT / "build" / "vision_processor",
        help="vision_processor executable (default: <repo>/build/vision_processor)",
    )
    parser.add_argument(
        "--vision-cwd",
        type=Path,
        default=None,
        help="working directory for vision_processor (default: the "
        "--vision-config file's directory, i.e. where img/ is read from)",
    )
    parser.add_argument(
        "--camera-token-file",
        type=Path,
        default=None,
        help="file holding the Pi camera remote-control token (default: "
        "<repo>/.camera-token if it exists; fallback: $PI_CAMERA_TOKEN)",
    )
    parser.add_argument(
        "--camera-tokens-file",
        type=Path,
        default=REPO_ROOT / ".camera-tokens",
        help="YAML `host: token` file for other Pi cameras found by the scan "
        "(default: <repo>/.camera-tokens; created on first 'Add token')",
    )
    parser.add_argument(
        "--start-vision",
        action="store_true",
        help="start vision_processor together with the backend",
    )
    args = parser.parse_args()

    # SIGTERM (systemd, kill) cancels the main task like Ctrl+C does, so the
    # cleanup below runs and the managed vision_processor is stopped too.
    main_task = asyncio.current_task()
    if main_task is not None:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, main_task.cancel)

    bus = Bus()
    multicast = Multicast(bus, args.vision_ip, args.vision_port)
    geometry = Geometry(bus, args.geometry)

    http_app = web.Application(middlewares=[_cors_middleware])
    websocket.register(http_app, bus)
    img_dir = args.vision_config.parent / "img"
    snapshot.register(http_app, img_dir)
    colors.register(http_app, args.vision_config, img_dir, bus)
    vision = supervisor.VisionSupervisor(
        args.vision_binary,
        args.vision_config,
        args.vision_cwd or args.vision_config.parent,
    )

    def camera_status_url() -> str | None:
        camera = calibration.read_config(args.vision_config).get("camera")
        return operator.pi_status_url(
            camera.get("path") if isinstance(camera, dict) else None
        )

    vision.camera_status_url = camera_status_url
    supervisor.register(http_app, vision, args.start_vision)
    field_calibration = calibration.FieldCalibration(
        geometry, vision, args.vision_config, img_dir
    )
    calibration.register(http_app, field_calibration)
    fieldgeometry.register(http_app, geometry)
    metrics_snapshot = metrics.register(http_app, bus)
    network = calibration.read_config(args.vision_config).get("network")
    network = network if isinstance(network, dict) else {}
    gc = gamecontroller.GameController(
        str(network.get("gc_ip", "224.5.23.1")), int(network.get("gc_port", 10003))
    )
    docs.register(
        http_app,
        REPO_ROOT,
        docs.setup_of(calibration.read_config(args.vision_config).get("camera")),
    )
    token_file = args.camera_token_file
    if token_file is None and (REPO_ROOT / ".camera-token").is_file():
        token_file = REPO_ROOT / ".camera-token"
    camera.register(
        http_app,
        args.vision_config,
        vision,
        camera.load_token(token_file),
        args.camera_tokens_file,
    )
    vision.log_file = LOGS_DIR / "vision_processor.log"
    camera_log = logs.CameraLogPoller(
        LOGS_DIR, lambda: camera.camera_base_url(args.vision_config)
    )
    logs.register(http_app, LOGS_DIR, camera_log)
    operator.register(
        http_app,
        bus,
        args.vision_config,
        args.geometry,
        args.frontend_dir,
        img_dir,
        vision,
        field_calibration,
        LOGS_DIR,
        camera_log,
        gc,
        metrics_snapshot,
        REPO_ROOT,
    )

    http_runner = web.AppRunner(http_app)
    await http_runner.setup()
    http_site = web.TCPSite(http_runner, args.host, args.port)
    await http_site.start()
    log.info("http+ws listening on %s:%d", args.host, args.port)

    try:
        await multicast.start()
        await gc.start()
        await geometry.run()
    finally:
        gc.close()
        await http_runner.cleanup()
        await multicast.close()


def main() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    with contextlib.suppress(OSError):
        handlers.append(
            RotatingFileHandler(
                LOGS_DIR / "wrapper_backend.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=1,
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )
    with contextlib.suppress(KeyboardInterrupt, asyncio.CancelledError):
        asyncio.run(_main())


if __name__ == "__main__":
    main()

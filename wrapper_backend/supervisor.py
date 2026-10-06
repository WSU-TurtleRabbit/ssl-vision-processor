"""``vision_processor`` subprocess supervisor.

Runs ``<binary> <vision-config>`` with ``cwd`` = the vision config's
directory (so ``img/`` and the relative ``bot_heights_file`` resolve next to
the config, the same ``img/`` the snapshot/colour routes read), keeps the
last lines of its combined stdout/stderr in memory and restarts it when it
dies while it is meant to run.

Routes:

- ``POST /api/vision/start``   start (409 if one with the same config runs)
- ``POST /api/vision/stop``    SIGTERM, SIGKILL after 5 s (managed child or a
  hand-started instance with the same config owned by the same user)
- ``POST /api/vision/restart`` stop + start
- ``GET  /api/vision/log``     ``{"lines": [...]}`` (last ~200 lines)

A ``vision_processor`` started by hand (found by scanning ``/proc``) is
"external": stop/restart still work on it, start refuses while it runs.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import ctypes
import logging
import os
import signal
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any

import aiohttp
from aiohttp import web

from wrapper_backend.logs import append_lines

log = logging.getLogger("wrapper_backend.supervisor")

VISION_PROCESSOR = "vision_processor"
LOG_LINES = 200
STOP_TIMEOUT_S = 5.0
# Crash restart back-off: 2 s, 5 s, then every 10 s - forever, while
# want_running is set (the camera may just be unplugged for a while).
RETRY_DELAYS_S = (2.0, 5.0, 10.0)
# A run shorter than this counts as a "rapid" failure (informational only).
RAPID_RUN_S = 10.0
# Before a retry the camera's /status is probed (http(s) cameras only).
CAMERA_PROBE_TIMEOUT_S = 1.0


class SupervisorError(Exception):
    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _resolve_arg(cwd: str | None, arg: str) -> str:
    path = Path(arg)
    if not path.is_absolute() and cwd is not None:
        path = Path(cwd) / path
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def scan_vision_processors() -> list[dict[str, Any]]:
    """Find running ``vision_processor`` instances by scanning ``/proc``.

    Nothing writes a pid file for the C++ binary, so match on argv[0]'s
    basename instead. ``config`` mirrors main.cpp's ``argv[1]`` default;
    ``config_path`` is it resolved against the process's cwd (``None`` when
    the process isn't ours to inspect).
    """
    instances: list[dict[str, Any]] = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
            uid = proc.stat().st_uid
        except OSError:
            continue  # exited mid-scan, or not ours to read
        if not argv[0] or os.path.basename(argv[0]).decode() != VISION_PROCESSOR:
            continue
        config = argv[1].decode() if len(argv) > 1 and argv[1] else "config.yml"
        try:
            cwd: str | None = os.readlink(proc / "cwd")
        except OSError:
            cwd = None
        instances.append(
            {
                "pid": int(proc.name),
                "config": config,
                "config_path": _resolve_arg(cwd, config) if cwd else None,
                "uid": uid,
            }
        )
    instances.sort(key=lambda i: i["pid"])
    return instances


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers kill(0); treat it as gone.
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False


def _set_parent_death_signal() -> None:
    """Child-side: get SIGTERM if the backend dies without cleaning up."""
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
    except OSError:
        pass


class VisionSupervisor:
    def __init__(self, binary: Path, config: Path, cwd: Path) -> None:
        self.binary = binary.resolve()
        self.config = config.resolve()
        self.cwd = cwd.resolve()
        self._proc: asyncio.subprocess.Process | None = None
        self._monitor: asyncio.Task[None] | None = None
        self._restart_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._log: collections.deque[str] = collections.deque(maxlen=LOG_LINES)
        self.want_running = False
        self._stopping = False
        self._started_at: float | None = None
        self._rapid_failures = 0
        self.restarts = 0
        # Crash-retry bookkeeping: "idle" | "waiting_for_camera" | "restarting".
        self.retry_state = "idle"
        self.retry_attempts = 0
        self._retry_round = 0
        self._retry_next_at: float | None = None
        self.camera_wait_reason = "Pi not reachable"
        # Optional file that receives the child's stdout+stderr (logs/).
        self.log_file: Path | None = None
        # Returns the camera's /status URL (http(s) cameras) or None; set by
        # __main__ so the supervisor can wait for the Pi before retrying.
        self.camera_status_url: Callable[[], str | None] | None = None
        self._last_exit: dict[str, Any] | None = None
        # Last "status: ..." stdout line and last stderr (WARN) line.
        self.last_status: dict[str, Any] | None = None
        self.last_warning: dict[str, Any] | None = None

    # -- helpers ---------------------------------------------------------

    def note(self, message: str) -> None:
        log.info("%s", message)
        self._log.append(f"[supervisor {time.strftime('%H:%M:%S')}] {message}")

    def _managed_pid(self) -> int | None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return None
        return proc.pid

    def external_instances(self) -> list[dict[str, Any]]:
        """Instances with our config that this backend did not start."""
        managed = self._managed_pid()
        return [
            inst
            for inst in scan_vision_processors()
            if inst["pid"] != managed and inst["config_path"] == str(self.config)
        ]

    def log_lines(self) -> list[str]:
        return list(self._log)

    def state_text(self, managed: int | None, external: list[dict[str, Any]]) -> str:
        if managed is not None:
            return "running"
        if external:
            return "running (started by hand)"
        if self.want_running:
            if self.retry_state == "waiting_for_camera":
                return f"waiting for camera ({self.camera_wait_reason})"
            n = self.retry_attempts
            return f"restarting ({n} attempt{'' if n == 1 else 's'})"
        return "stopped"

    def status(self) -> dict[str, Any]:
        managed = self._managed_pid()
        external = self.external_instances()
        pid = (
            managed
            if managed is not None
            else (external[0]["pid"] if external else None)
        )
        return {
            "running": pid is not None,
            "pid": pid,
            "managed": managed is not None,
            "external_pids": [inst["pid"] for inst in external],
            "uptime_s": (
                round(time.monotonic() - self._started_at, 1)
                if managed is not None and self._started_at is not None
                else None
            ),
            "want_running": self.want_running,
            "restarts": self.restarts,
            "rapid_failures": self._rapid_failures,
            "state": self.state_text(managed, external),
            "retry": {
                "state": self.retry_state if self.want_running else "idle",
                "attempts": self.retry_attempts,
                "next_in_s": (
                    round(max(0.0, self._retry_next_at - time.monotonic()), 1)
                    if self._retry_next_at is not None and self.want_running
                    else None
                ),
            },
            "last_exit": self._last_exit,
            "last_status": self.last_status,
            "last_warning": self.last_warning,
            "binary": str(self.binary),
            "binary_exists": os.access(self.binary, os.X_OK),
            "config_path": str(self.config),
            "cwd": str(self.cwd),
        }

    # -- lifecycle -------------------------------------------------------

    async def _spawn(self) -> None:
        if not os.access(self.binary, os.X_OK):
            raise SupervisorError(
                f"{self.binary} is not an executable (build vision_processor first "
                "or pass --vision-binary)",
                500,
            )
        external = self.external_instances()
        if external:
            pids = ", ".join(str(inst["pid"]) for inst in external)
            raise SupervisorError(
                f"vision_processor is already running with {self.config.name} "
                f"(pid {pids}, started outside the backend); stop or restart it "
                "instead"
            )
        proc = await asyncio.create_subprocess_exec(
            str(self.binary),
            str(self.config),
            cwd=self.cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            # Separate pipe: stderr carries WARN(...) lines, shown as the
            # last warning in /api/health.
            stderr=asyncio.subprocess.PIPE,
            # Own session: a Ctrl+C in the backend's terminal must not reach
            # the child directly; the backend stops it on shutdown instead.
            start_new_session=True,
            preexec_fn=_set_parent_death_signal,
        )
        self._proc = proc
        self._started_at = time.monotonic()
        self.last_status = None
        self.last_warning = None  # warnings belong to one run only
        if self.log_file is not None:
            append_lines(
                self.log_file,
                [
                    f"---- started {time.strftime('%Y-%m-%dT%H:%M:%S%z')} pid {proc.pid} ----"
                ],
            )
        self.note(f"started {self.binary.name} {self.config} (pid {proc.pid})")
        self._monitor = asyncio.create_task(
            self._watch(proc), name="vision-supervisor-watch"
        )

    async def _pump(self, stream: asyncio.StreamReader, is_stderr: bool) -> None:
        while True:
            raw = await stream.readline()
            if not raw:
                return
            line = raw.decode(errors="replace").rstrip("\n")
            self._log.append(line)
            if self.log_file is not None:
                append_lines(self.log_file, [line])
            if is_stderr:
                self.last_warning = {"line": line, "at": time.time()}
            elif "] status: " in line:
                self.last_status = {
                    "line": line.split("] status: ", 1)[1],
                    "at": time.time(),
                }

    async def _watch(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None and proc.stderr is not None
        await asyncio.gather(
            self._pump(proc.stdout, False), self._pump(proc.stderr, True)
        )
        code = await proc.wait()
        ran_s = time.monotonic() - (self._started_at or time.monotonic())
        self._last_exit = {"code": code, "at": time.time(), "ran_s": round(ran_s, 1)}
        if proc is not self._proc:
            return
        if self._stopping or not self.want_running:
            self.note(f"vision_processor (pid {proc.pid}) exited with {code}")
            return

        if ran_s < RAPID_RUN_S:
            self._rapid_failures += 1
        else:
            # A healthy run: the retry counters start over.
            self._rapid_failures = 0
            self.retry_attempts = 0
            self._retry_round = 0
        self.note(
            f"vision_processor exited unexpectedly with {code} after "
            f"{ran_s:.1f} s; retrying in {self._next_delay():.0f} s"
        )
        if self._restart_task is None or self._restart_task.done():
            self._restart_task = asyncio.create_task(
                self._retry_loop(), name="vision-supervisor-retry"
            )

    def _next_delay(self) -> float:
        return RETRY_DELAYS_S[min(self._retry_round, len(RETRY_DELAYS_S) - 1)]

    async def _camera_reachable(self) -> bool | None:
        """True/False for an http(s) camera's /status, None for other cameras.

        A camera that answers but is ``closed`` by the operator counts as not
        ready too (vision_processor would only get 503 from ``/stream``).
        """
        url = self.camera_status_url() if self.camera_status_url else None
        if url is None:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=CAMERA_PROBE_TIMEOUT_S)
                ) as response:
                    if response.status != 200:
                        self.camera_wait_reason = "Pi not reachable"
                        return False
                    data = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            self.camera_wait_reason = "Pi not reachable"
            return False
        if isinstance(data, dict) and data.get("closed"):
            self.camera_wait_reason = "camera closed by operator"
            return False
        return True

    async def _retry_loop(self) -> None:
        """Retry forever (2 s, 5 s, then every 10 s) while want_running.

        An http(s) camera is probed first; vision_processor is only started
        once the Pi answers, otherwise it would crash-loop against it.
        """
        try:
            while self.want_running:
                delay = self._next_delay()
                self._retry_round += 1
                self._retry_next_at = time.monotonic() + delay
                await asyncio.sleep(delay)
                async with self._lock:
                    if not self.want_running or self._managed_pid() is not None:
                        break
                    if await self._camera_reachable() is False:
                        if self.retry_state != "waiting_for_camera":
                            self.note(
                                f"waiting for camera ({self.camera_wait_reason}); "
                                "retrying while it is away"
                            )
                        self.retry_state = "waiting_for_camera"
                        continue
                    self.retry_state = "restarting"
                    self.retry_attempts += 1
                    try:
                        self.restarts += 1
                        await self._spawn()
                        break
                    except Exception as exc:  # noqa: BLE001 - keep retrying
                        self.note(
                            f"restart attempt {self.retry_attempts} failed: {exc!r}"
                        )
        finally:
            self.retry_state = "idle"
            self._retry_next_at = None

    async def start(self) -> dict[str, Any]:
        async with self._lock:
            if self._managed_pid() is not None:
                raise SupervisorError(
                    f"vision_processor is already running (pid {self._managed_pid()})"
                )
            self._cancel_restart()
            await self._spawn()
            self.want_running = True
            self._reset_retry()
            return self.status()

    def _reset_retry(self) -> None:
        self._rapid_failures = 0
        self.retry_attempts = 0
        self._retry_round = 0
        self.retry_state = "idle"
        self._retry_next_at = None

    def _cancel_restart(self) -> None:
        if self._restart_task is not None and not self._restart_task.done():
            self._restart_task.cancel()
        self._restart_task = None

    async def _terminate_managed(self) -> int | None:
        proc = self._proc
        if proc is None or proc.returncode is not None:
            return None
        self._stopping = True
        try:
            self.note(f"stopping vision_processor (pid {proc.pid}, SIGTERM)")
            with contextlib.suppress(ProcessLookupError):
                proc.send_signal(signal.SIGTERM)
            try:
                await asyncio.wait_for(proc.wait(), STOP_TIMEOUT_S)
            except TimeoutError:
                self.note(f"pid {proc.pid} ignored SIGTERM for 5 s, sending SIGKILL")
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                await proc.wait()
            if self._monitor is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await self._monitor
        finally:
            self._stopping = False
        return proc.pid

    async def _terminate_external(self) -> list[int]:
        own_uid = os.getuid()
        targets: list[int] = []
        for inst in self.external_instances():
            if inst["uid"] != own_uid:
                raise SupervisorError(
                    f"vision_processor pid {inst['pid']} belongs to another user",
                    403,
                )
            targets.append(inst["pid"])
        for pid in targets:
            self.note(f"stopping hand-started vision_processor (pid {pid}, SIGTERM)")
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT_S
        while any(_pid_alive(pid) for pid in targets) and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        for pid in targets:
            if _pid_alive(pid):
                self.note(f"pid {pid} ignored SIGTERM for 5 s, sending SIGKILL")
                with contextlib.suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGKILL)
        return targets

    async def _stop_locked(self) -> dict[str, Any]:
        was_wanted = self.want_running
        self.want_running = False
        self._cancel_restart()
        managed = await self._terminate_managed()
        external = await self._terminate_external()
        stopped = ([managed] if managed is not None else []) + external
        return {"stopped": stopped, "was_running": bool(stopped) or was_wanted}

    async def stop(self) -> dict[str, Any]:
        async with self._lock:
            result = await self._stop_locked()
            if not result["stopped"]:
                self.note("stop: no vision_processor was running")
            return {**result, **self.status()}

    async def restart(
        self, *, between: Callable[[], None] | None = None
    ) -> dict[str, Any]:
        """Stop whatever runs with our config, then start a managed child.

        ``between`` (an optional sync callable) runs after the old process
        is gone and before the new one starts.
        """
        async with self._lock:
            result = await self._stop_locked()
            if between is not None:
                between()
            self._reset_retry()
            await self._spawn()
            self.want_running = True
            return {**result, **self.status()}

    async def shutdown(self) -> None:
        """Stop the managed child (never a hand-started one)."""
        async with self._lock:
            self.want_running = False
            self._cancel_restart()
            await self._terminate_managed()


def register(
    http_app: web.Application, supervisor: VisionSupervisor, start: bool
) -> None:
    async def run(op: str) -> web.Response:
        try:
            if op == "start":
                result = await supervisor.start()
            elif op == "stop":
                result = await supervisor.stop()
            else:
                result = await supervisor.restart()
        except SupervisorError as exc:
            return web.json_response(
                {"error": str(exc), **supervisor.status()}, status=exc.status
            )
        except OSError as exc:
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response(result)

    async def start_handler(_: web.Request) -> web.Response:
        return await run("start")

    async def stop_handler(_: web.Request) -> web.Response:
        return await run("stop")

    async def restart_handler(_: web.Request) -> web.Response:
        return await run("restart")

    async def log_handler(_: web.Request) -> web.Response:
        return web.json_response(
            {"lines": supervisor.log_lines(), **supervisor.status()}
        )

    async def on_startup(_: web.Application) -> None:
        if not start:
            return
        try:
            await supervisor.start()
        except (SupervisorError, OSError) as exc:
            log.warning("--start-vision: %s", exc)
            supervisor.note(f"--start-vision: {exc}")

    async def on_cleanup(_: web.Application) -> None:
        await supervisor.shutdown()

    http_app.router.add_post("/api/vision/start", start_handler)
    http_app.router.add_post("/api/vision/stop", stop_handler)
    http_app.router.add_post("/api/vision/restart", restart_handler)
    http_app.router.add_get("/api/vision/log", log_handler)
    http_app.on_startup.append(on_startup)
    http_app.on_cleanup.append(on_cleanup)

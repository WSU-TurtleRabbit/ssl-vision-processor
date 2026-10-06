"""Pi camera remote control (proxy to pi_camera/camstream.py).

``POST /api/camera/{restart,close,open,shutdown}`` forwards to
``<scheme>://<host>:<port>/control/<command>`` of the camera in the vision
config's ``camera.path`` with the header ``X-Camstream-Token`` (3 s timeout)
and returns the Pi's JSON and status code. 503 when the camera is not an
http(s) camera or no token is configured.

The token comes from ``--camera-token-file`` (default ``<repo>/.camera-token``
when it exists; whitespace stripped) or, as a fallback, the
``PI_CAMERA_TOKEN`` environment variable. It is never logged or returned.

``close`` first stops the supervised vision_processor (``want_running``
false), because it would otherwise reconnect-loop against the Pi's 503.
``open`` does not start it again; the response says so.

Several cameras:

- ``GET /api/cameras/scan[?subnet=CIDR]`` probes the LAN /24 of the interface
  that routes to the configured camera host (fallback: every non-loopback
  IPv4 /24, at most 1024 hosts) for camstream ``GET /status`` on port 8080
  and the configured camera's port (0.5 s timeout, <= 64 in flight) and lists
  what answered. ``subnet`` (CIDR, /24 .. /32) overrides the range, e.g.
  ``127.0.0.1/32`` for a fake camera on this machine.
- ``POST /api/cameras/token {"host", "token"}`` stores a token for another
  camera in ``--camera-tokens-file`` (default ``<repo>/.camera-tokens``, YAML
  ``host: token`` per line, mode 600, git-ignored). ``.camera-token`` (single
  token) applies to the configured camera only.
- ``POST /api/cameras/use {"host", "port", "restart"}`` writes
  ``camera.path`` = ``http://host:port/stream`` into the vision config and
  restarts vision_processor when asked.
- ``POST /api/cameras/control {"host", "port", "command"}`` sends a
  ``/control/<command>`` to any camera with a known token.

Tokens are never logged or returned (only ``has_token``).
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from aiohttp import web
from yarl import URL

from wrapper_backend.calibration import read_config
from wrapper_backend.yamledit import YamlEditError, atomic_write, update_section
from wrapper_backend.supervisor import SupervisorError, VisionSupervisor

log = logging.getLogger("wrapper_backend.camera")

COMMANDS = ("restart", "close", "open", "shutdown")
NAME_RE = re.compile(r"^[A-Za-z0-9 _-]{1,40}$")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9._~:+/=-]{4,128}$")
SCAN_PORT = 8080
SCAN_TIMEOUT_S = 0.5
SCAN_CONCURRENCY = 64
SCAN_MAX_HOSTS = 1024


def camera_name(vision_config: Path) -> str | None:
    """``camera.name`` from the vision config (ignored by the C++)."""
    camera = read_config(vision_config).get("camera")
    name = camera.get("name") if isinstance(camera, dict) else None
    return name if isinstance(name, str) and NAME_RE.match(name) else None


TIMEOUT_S = 3.0
TOKEN_HELP = "set up the token: see pi_camera/README.md section 6"


def load_token(token_file: Path | None) -> str | None:
    if token_file is not None:
        try:
            token = token_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            log.warning("camera token file %s not readable: %s", token_file, exc)
        else:
            if token:
                return token
    token = os.environ.get("PI_CAMERA_TOKEN", "").strip()
    return token or None


def load_tokens(tokens_file: Path | None) -> dict[str, str]:
    """``host: token`` pairs from the tokens file (invalid lines skipped)."""
    if tokens_file is None:
        return {}
    try:
        parsed = yaml.safe_load(tokens_file.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(host): str(token)
        for host, token in parsed.items()
        if HOST_RE.match(str(host)) and TOKEN_RE.match(str(token))
    }


def save_tokens(tokens_file: Path, tokens: dict[str, str]) -> None:
    """Rewrite the tokens file (``host: token`` per line) with mode 600."""
    text = "# Pi camera remote-control tokens, one `host: token` per line.\n" + "".join(
        f"{host}: {token}\n" for host, token in sorted(tokens.items())
    )
    fd, tmp_name = tempfile.mkstemp(
        dir=tokens_file.parent, prefix=f".{tokens_file.name}.", suffix=".tmp"
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, tokens_file)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


async def pi_control(base: URL, token: str, command: str) -> tuple[int, dict[str, Any]]:
    """``POST <base>/control/<command>``; returns the Pi's status + JSON."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                str(base.with_path(f"/control/{command}")),
                headers={"X-Camstream-Token": token},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
            ) as response:
                status = response.status
                try:
                    data = await response.json(content_type=None)
                except ValueError:
                    data = {"ok": False, "error": (await response.text()).strip()}
    except (aiohttp.ClientError, TimeoutError) as exc:
        return 502, {
            "ok": False,
            "error": f"Pi camera not reachable: {str(exc) or type(exc).__name__}",
        }
    if not isinstance(data, dict):
        data = {"ok": False, "error": "unexpected reply from the Pi"}
    return status, data


async def _ip_json(*args: str) -> Any:
    """``ip -j <args>`` parsed, or None (no iproute2 / error)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip",
            "-j",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), 3.0)
    except (OSError, TimeoutError):
        return None
    try:
        return json.loads(out or b"null")
    except ValueError:
        return None


def _slash24(address: str) -> ipaddress.IPv4Network | None:
    try:
        return ipaddress.IPv4Network(f"{address}/24", strict=False)
    except ValueError:
        return None


async def lan_subnets(camera_host: str | None) -> list[ipaddress.IPv4Network]:
    """The /24 of the interface routing to ``camera_host``, else all LAN /24s."""
    if camera_host:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(camera_host, None)
            target = next((i[4][0] for i in infos if i[0].name == "AF_INET"), None)
        except OSError:
            target = None
        if target:
            route = await _ip_json("-4", "route", "get", target)
            if isinstance(route, list) and route and isinstance(route[0], dict):
                prefsrc = route[0].get("prefsrc")
                net = _slash24(prefsrc) if isinstance(prefsrc, str) else None
                if net is not None and not net.is_loopback:
                    return [net]
    addrs = await _ip_json("-4", "addr", "show", "scope", "global")
    found: list[ipaddress.IPv4Network] = []
    if isinstance(addrs, list):
        for link in addrs:
            if not isinstance(link, dict) or link.get("link_type") == "loopback":
                continue
            for info in link.get("addr_info", []):
                if not isinstance(info, dict) or info.get("family") != "inet":
                    continue
                if info.get("prefixlen", 24) >= 32:
                    continue  # point-to-point (VPN) address, no LAN behind it
                net = _slash24(str(info.get("local", "")))
                if net is not None and not net.is_loopback and net not in found:
                    found.append(net)
    return found


async def probe_camstream(host: str, port: int) -> dict[str, Any] | None:
    """camstream ``/status`` of host:port, or None if it is not one."""
    url = f"http://{host}:{port}/status"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=SCAN_TIMEOUT_S)
            ) as response:
                if response.status != 200:
                    return None
                data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return None
    if not isinstance(data, dict) or "streaming" not in data:
        return None
    return {
        "host": host,
        "port": port,
        "streaming": bool(data.get("streaming")),
        "client": data.get("client"),
        "closed": bool(data.get("closed")),
        "control": bool(data.get("control")),
        "device": data.get("device"),
        "size": data.get("size"),
        "fps": data.get("fps"),
    }


async def scan_cameras(hosts: list[str], ports: list[int]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def one(host: str, port: int) -> dict[str, Any] | None:
        async with semaphore:
            return await probe_camstream(host, port)

    results = await asyncio.gather(
        *(one(host, port) for host in hosts for port in ports)
    )
    return [r for r in results if r is not None]


def camera_base_url(vision_config: Path) -> URL | None:
    camera = read_config(vision_config).get("camera")
    path = camera.get("path") if isinstance(camera, dict) else None
    if not isinstance(path, str):
        return None
    url = URL(path)
    if url.scheme not in ("http", "https") or not url.host:
        return None
    return url.with_path("/").with_query(None).with_fragment(None)


def register(
    http_app: web.Application,
    vision_config: Path,
    supervisor: VisionSupervisor,
    token: str | None,
    tokens_file: Path | None = None,
) -> None:
    if token:
        log.info("Pi camera remote control enabled (token configured)")
    name_lock = asyncio.Lock()
    tokens_lock = asyncio.Lock()

    def token_for(host: str | None) -> str | None:
        """Token for ``host``: the tokens file first, then the single token
        (which applies to the configured camera only)."""
        if host is None:
            return None
        base = camera_base_url(vision_config)
        if token and base is not None and base.host == host:
            return token  # the configured camera: .camera-token wins
        return load_tokens(tokens_file).get(host)

    async def name_handler(request: web.Request) -> web.Response:
        """``POST /api/camera/name {"name": "..."}``; "" removes the name."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "body must be JSON"}, status=400)
        name = body.get("name") if isinstance(body, dict) else None
        if not isinstance(name, str):
            return web.json_response({"error": '"name" must be a string'}, status=400)
        name = name.strip()
        if name and not NAME_RE.match(name):
            return web.json_response(
                {"error": "name: 1-40 letters, digits, spaces, - or _"}, status=400
            )
        async with name_lock:
            try:
                original = vision_config.read_text(encoding="utf-8")
                updated = update_section(
                    original,
                    "camera",
                    {"name": f'"{name}"'} if name else {},
                    remove=[] if name else ["name"],
                )
                parsed = read_config_text(updated)
                if parsed.get("name") != (name or None):
                    raise YamlEditError("edited config does not round-trip")
                if updated != original:
                    atomic_write(vision_config, updated)
            except (OSError, YamlEditError, yaml.YAMLError) as exc:
                return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"name": name or None})

    async def handler(request: web.Request) -> web.Response:
        command = request.match_info["command"]
        if command not in COMMANDS:
            raise web.HTTPNotFound
        base = camera_base_url(vision_config)
        if base is None:
            return web.json_response(
                {
                    "ok": False,
                    "error": "the camera in the vision config is not an "
                    "http(s) network camera",
                },
                status=503,
            )
        own_token = token_for(base.host)
        if not own_token:
            return web.json_response(
                {"ok": False, "error": f"no camera token configured; {TOKEN_HELP}"},
                status=503,
            )
        extra: dict[str, Any] = {}
        if command == "close":
            try:
                stopped = await supervisor.stop()
            except SupervisorError as exc:
                return web.json_response(
                    {"ok": False, "error": f"could not stop vision_processor: {exc}"},
                    status=exc.status,
                )
            extra["vision_processor_stopped"] = stopped["stopped"]
        status, data = await pi_control(base, own_token, command)
        if command == "open" and status == 200:
            extra["note"] = (
                "camera open again; vision_processor was not started — press Start"
            )
        log.info("camera %s -> HTTP %d", command, status)
        return web.json_response({**data, **extra}, status=status)

    def bad(message: str, status: int = 400) -> web.Response:
        return web.json_response({"ok": False, "error": message}, status=status)

    async def json_body(request: web.Request) -> dict[str, Any] | None:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return body if isinstance(body, dict) else None

    def host_port(body: dict[str, Any]) -> tuple[str, int] | None:
        host = body.get("host")
        port = body.get("port", SCAN_PORT)
        if not isinstance(host, str) or not HOST_RE.match(host):
            return None
        if (
            isinstance(port, bool)
            or not isinstance(port, int)
            or not 1 <= port <= 65535
        ):
            return None
        return host, port

    async def scan_handler(request: web.Request) -> web.Response:
        base = camera_base_url(vision_config)
        override = request.query.get("subnet")
        if override:
            try:
                network = ipaddress.IPv4Network(override, strict=False)
            except ValueError:
                return bad("subnet must be an IPv4 CIDR like 192.168.1.0/24")
            if network.prefixlen < 24:
                return bad("subnet must be /24 or smaller")
            subnets = [network]
        else:
            subnets = await lan_subnets(base.host if base is not None else None)
        hosts: list[str] = []
        for network in subnets:
            candidates = (
                [network.network_address]
                if network.prefixlen == 32
                else list(network.hosts())
            )
            hosts += [str(h) for h in candidates]
        hosts = hosts[:SCAN_MAX_HOSTS]
        ports = [SCAN_PORT]
        if base is not None and base.port and base.port not in ports:
            ports.append(base.port)
        found = await scan_cameras(hosts, ports)
        tokens = load_tokens(tokens_file)
        name = camera_name(vision_config)
        for cam in found:
            current = (
                base is not None
                and base.host == cam["host"]
                and base.port == cam["port"]
            )
            cam["is_current"] = current
            cam["has_token"] = (
                token_for(cam["host"]) is not None
                if (current or cam["host"] in tokens)
                else False
            )
            cam["name_if_known"] = name if current else None
        found.sort(key=lambda c: (not c["is_current"], c["host"], c["port"]))
        return web.json_response(
            {
                "subnets": [str(n) for n in subnets],
                "hosts_probed": len(hosts),
                "ports": ports,
                "cameras": found,
            }
        )

    async def token_handler(request: web.Request) -> web.Response:
        body = await json_body(request)
        if body is None:
            return bad("body must be a JSON object")
        host = body.get("host")
        new_token = body.get("token")
        if not isinstance(host, str) or not HOST_RE.match(host):
            return bad("host: an IPv4 address or hostname")
        if not isinstance(new_token, str) or not TOKEN_RE.match(new_token.strip()):
            return bad("token: 4-128 characters of A-Z a-z 0-9 . _ ~ : + / = -")
        if tokens_file is None:
            return bad("no --camera-tokens-file configured", 503)
        async with tokens_lock:
            try:
                tokens = load_tokens(tokens_file)
                tokens[host] = new_token.strip()
                save_tokens(tokens_file, tokens)
            except OSError as exc:
                return bad(str(exc), 500)
        log.info("stored a camera token for %s", host)
        return web.json_response({"ok": True, "host": host, "hosts": sorted(tokens)})

    async def use_handler(request: web.Request) -> web.Response:
        body = await json_body(request)
        target = host_port(body) if body is not None else None
        if body is None or target is None:
            return bad('expected {"host": ..., "port": 8080, "restart": bool}')
        host, port = target
        restart = body.get("restart", False)
        if not isinstance(restart, bool):
            return bad("restart must be a boolean")
        path = f"http://{host}:{port}/stream"
        async with name_lock:
            try:
                original = vision_config.read_text(encoding="utf-8")
                updated = update_section(original, "camera", {"path": path})
                if read_config_text(updated).get("path") != path:
                    raise YamlEditError("edited config does not round-trip")
                if updated != original:
                    atomic_write(vision_config, updated)
            except (OSError, YamlEditError, yaml.YAMLError) as exc:
                return bad(str(exc), 500)
        log.info("camera.path set to %s", path)
        result: dict[str, Any] = {"ok": True, "path": path, "restarted": False}
        if restart:
            try:
                await supervisor.restart()
                result["restarted"] = True
            except SupervisorError as exc:
                result["error"] = f"saved, but restarting failed: {exc}"
        return web.json_response(result)

    async def control_handler(request: web.Request) -> web.Response:
        body = await json_body(request)
        target = host_port(body) if body is not None else None
        command = body.get("command") if body is not None else None
        if body is None or target is None or command not in COMMANDS:
            return bad(
                'expected {"host", "port", "command": restart|close|open|shutdown}'
            )
        host, port = target
        cam_token = token_for(host)
        if not cam_token:
            return bad(
                f"no token saved for {host}; add it to .camera-tokens "
                "(Help -> Pi camera -> 6)",
                503,
            )
        base = URL(f"http://{host}:{port}/")
        own = camera_base_url(vision_config)
        extra: dict[str, Any] = {}
        if (
            command == "close"
            and own is not None
            and own.host == host
            and own.port == port
        ):
            stopped = await supervisor.stop()
            extra["vision_processor_stopped"] = stopped["stopped"]
        status, data = await pi_control(base, cam_token, str(command))
        log.info("camera %s:%d %s -> HTTP %d", host, port, command, status)
        return web.json_response({**data, **extra}, status=status)

    # The fixed route first, so "name" never reaches the command proxy.
    http_app.router.add_post("/api/camera/name", name_handler)
    http_app.router.add_post("/api/camera/{command}", handler)
    http_app.router.add_get("/api/cameras/scan", scan_handler)
    http_app.router.add_post("/api/cameras/token", token_handler)
    http_app.router.add_post("/api/cameras/use", use_handler)
    http_app.router.add_post("/api/cameras/control", control_handler)


def read_config_text(text: str) -> dict[str, Any]:
    """The ``camera:`` mapping of a config text (for round-trip checks)."""
    parsed = yaml.safe_load(text) or {}
    camera = parsed.get("camera") if isinstance(parsed, dict) else None
    return camera if isinstance(camera, dict) else {}

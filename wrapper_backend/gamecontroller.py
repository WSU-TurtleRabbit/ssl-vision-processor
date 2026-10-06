"""Game-controller presence: listens on the referee multicast group.

Joins ``gc_ip:gc_port`` from the vision config's ``network:`` section
(default 224.5.23.1:10003), parses ``Referee`` packets
(``proto/ssl_gc_referee_message.proto``) and keeps the latest stage,
command, team names and the receive time. ``status()`` also reports whether
a process named ``ssl-game-controller`` runs on this machine (``/proc``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import time
from pathlib import Path
from typing import Any

from google.protobuf.message import DecodeError

from proto.ssl_gc_referee_message_pb2 import Referee

log = logging.getLogger("wrapper_backend.gamecontroller")

PROCESS_NAME = "ssl-game-controller"


def find_process() -> int | None:
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            argv = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if (
            argv
            and argv[0]
            and os.path.basename(argv[0]).decode().startswith(PROCESS_NAME)
        ):
            return int(proc.name)
    return None


class GameController(asyncio.DatagramProtocol):
    def __init__(self, group: str, port: int) -> None:
        self.group = group
        self.port = port
        self._transport: asyncio.DatagramTransport | None = None
        self.last_at: float | None = None
        self.last: dict[str, Any] = {}
        self.packets = 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        message = Referee()
        try:
            message.ParseFromString(data)
        except DecodeError:
            return
        self.packets += 1
        self.last_at = time.monotonic()
        self.last = {
            "stage": Referee.Stage.Name(message.stage),
            "command": Referee.Command.Name(message.command),
            "yellow": message.yellow.name,
            "blue": message.blue.name,
            "yellow_score": message.yellow.score,
            "blue_score": message.blue.score,
            "from": addr[0],
        }

    async def start(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self.group, self.port))
            sock.setsockopt(
                socket.IPPROTO_IP,
                socket.IP_ADD_MEMBERSHIP,
                struct.pack("4sl", socket.inet_aton(self.group), socket.INADDR_ANY),
            )
        except OSError as exc:
            log.warning(
                "game controller multicast %s:%d: %s", self.group, self.port, exc
            )
            sock.close()
            return
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: self, sock=sock
        )
        log.info("listening for the game controller on %s:%d", self.group, self.port)

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def status(self) -> dict[str, Any]:
        age = round(time.monotonic() - self.last_at, 1) if self.last_at else None
        pid = find_process()
        return {
            "running": age is not None and age < 5.0,
            "pid": pid,
            "process_running": pid is not None,
            "group": f"{self.group}:{self.port}",
            "last_packet_age_s": age,
            "packets": self.packets,
            **self.last,
        }

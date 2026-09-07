"""Measure what the running vision_processor is putting on the wire.

Listens to the SSL vision multicast group and summarises a fixed window.
Run it once against the binary you have now, once against the new one, and
compare the two printouts. Nothing is written and nothing is restarted.

    .venv/bin/python vision_probe.py --seconds 30 --label old
"""

from __future__ import annotations

import argparse
import collections
import socket
import struct
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

import wrapper_backend  # noqa: F401  (generates + path-adds the proto bindings)
from proto.ssl_vision_wrapper_pb2 import SSL_WrapperPacket


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="224.5.23.2")
    ap.add_argument("--port", type=int, default=10006)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.ip, args.port))
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        struct.pack("4sl", socket.inet_aton(args.ip), socket.INADDR_ANY),
    )
    sock.settimeout(2.0)

    frames: collections.Counter[int] = collections.Counter()
    balls_seen: collections.Counter[int] = collections.Counter()
    ball_conf: dict[int, list[float]] = collections.defaultdict(list)
    bots: dict[int, list[int]] = collections.defaultdict(list)
    bot_conf: dict[int, list[float]] = collections.defaultdict(list)
    ids_seen: dict[int, set[tuple[str, int]]] = collections.defaultdict(set)
    first_num: dict[int, int] = {}
    last_num: dict[int, int] = {}
    calib: list[str] = []

    print(f"[{args.label}] listening on {args.ip}:{args.port} for {args.seconds:.0f}s ...")
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        pkt = SSL_WrapperPacket()
        try:
            pkt.ParseFromString(data)
        except Exception:
            continue

        if pkt.HasField("geometry") and not calib:
            for c in pkt.geometry.calib:
                calib.append(
                    f"cam {c.camera_id}: focal={c.focal_length:.2f} "
                    f"pp=({c.principal_point_x:.1f},{c.principal_point_y:.1f}) "
                    f"dist={c.distortion:.4f} "
                    f"t=({c.tx:.0f},{c.ty:.0f},{c.tz:.0f})"
                )

        if not pkt.HasField("detection"):
            continue
        d = pkt.detection
        cam = d.camera_id
        frames[cam] += 1
        first_num.setdefault(cam, d.frame_number)
        last_num[cam] = d.frame_number

        if d.balls:
            balls_seen[cam] += 1
            ball_conf[cam].extend(b.confidence for b in d.balls)

        n = len(d.robots_blue) + len(d.robots_yellow)
        bots[cam].append(n)
        for r in d.robots_blue:
            bot_conf[cam].append(r.confidence)
            ids_seen[cam].add(("blue", r.robot_id))
        for r in d.robots_yellow:
            bot_conf[cam].append(r.confidence)
            ids_seen[cam].add(("yellow", r.robot_id))

    def mean(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 0.0

    print(f"\n===== {args.label} =====")
    if not frames:
        print("NO DETECTION PACKETS SEEN - is the detector running?")
        return

    for cam in sorted(frames):
        got = frames[cam]
        span = last_num[cam] - first_num[cam] + 1
        dropped = max(0, span - got)
        print(f"\ncamera {cam}")
        print(f"  detection frames   {got}  ({got / args.seconds:.1f} /s)")
        print(f"  frame-number span  {span}   gaps: {dropped} "
              f"({100.0 * dropped / span if span else 0:.1f}% missing)")
        print(f"  ball present in    {100.0 * balls_seen[cam] / got:.1f}% of frames")
        print(f"  mean ball conf     {mean(ball_conf[cam]):.3f}")
        print(f"  mean robots/frame  {mean([float(x) for x in bots[cam]]):.2f}")
        print(f"  mean robot conf    {mean(bot_conf[cam]):.3f}")
        print(f"  distinct ids seen  {len(ids_seen[cam])}  "
              f"{sorted(ids_seen[cam])}")

    if calib:
        print("\ncalibration")
        for line in calib:
            print(f"  {line}")


if __name__ == "__main__":
    main()

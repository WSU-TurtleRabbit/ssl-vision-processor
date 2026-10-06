#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record SSL_WrapperPacket detections from a multicast group as JSON lines until killed or idle.

usage: capture_detections.py <python proto dir> <group> <port> <out.jsonl> [idle seconds]
(<python proto dir> contains proto/*_pb2.py, e.g. from `protoc -I <repo> --python_out=<dir> <repo>/proto/*.proto`)
"""
import json, socket, struct, sys, time
sys.path.insert(0, sys.argv[1])
from proto import ssl_vision_wrapper_pb2

group, port, out, idle = sys.argv[2], int(sys.argv[3]), sys.argv[4], float(sys.argv[5]) if len(sys.argv) > 5 else 20.0
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(('', port))
sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack('4sl', socket.inet_aton(group), socket.INADDR_ANY))
sock.settimeout(1.0)

last = time.time()
seen_detection = False
with open(out, 'w') as f:
    while True:
        try:
            data, _ = sock.recvfrom(65536)
        except socket.timeout:
            if seen_detection and time.time() - last > idle:
                break
            continue
        packet = ssl_vision_wrapper_pb2.SSL_WrapperPacket()
        try:
            packet.ParseFromString(data)
        except Exception:
            continue
        if not packet.HasField('detection'):
            continue
        d = packet.detection
        seen_detection = True
        last = time.time()
        def bots(lst):
            return [{'id': r.robot_id, 'x': r.x, 'y': r.y, 'o': r.orientation, 'c': r.confidence, 'px': r.pixel_x, 'py': r.pixel_y} for r in lst]
        f.write(json.dumps({
            'frame': d.frame_number, 't_capture': d.t_capture, 't_sent': d.t_sent,
            'balls': [{'x': b.x, 'y': b.y, 'c': b.confidence, 'px': b.pixel_x, 'py': b.pixel_y} for b in d.balls],
            'yellow': bots(d.robots_yellow), 'blue': bots(d.robots_blue),
        }) + '\n')
        f.flush()

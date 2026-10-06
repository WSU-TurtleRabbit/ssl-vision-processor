#!/usr/bin/env python3
"""On-demand MJPEG camera stream for a networked vision_processor camera.

The camera is only opened while a client is connected to /stream:
client connects -> ffmpeg starts, client disconnects -> ffmpeg stops.
Frames are passed through without re-encoding (camera MJPEG -> multipart HTTP).

Endpoints:
  GET  /stream            multipart/x-mixed-replace MJPEG stream (one client at a time)
  GET  /status            JSON {"streaming": bool, "client": str|null, "closed": bool, "device": ..., "size": ..., "fps": ...,
                                "ctrl": [...], "control": bool}
  POST /control/restart   drop the current stream (camera off, the client reconnects -> camera on again)
  POST /control/close     camera off and refused to all clients (503) until /control/open or a reboot
  POST /control/open      allow streaming again
  POST /control/shutdown  shut down the Pi (needs the sudoers rule from camstream-sudoers)

The /control/* commands need the shared secret: header "X-Camstream-Token: <token>" (or ?token=...).
The token comes from --token or the CAMSTREAM_TOKEN environment variable; without one, /control/* is disabled.

Only depends on python3 (stdlib), ffmpeg and v4l-utils (for --ctrl).
"""
import argparse
import hmac
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BOUNDARY = "ffmpeg"  # ffmpeg's mpjpeg muxer default boundary


class CamStream:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.client: str | None = None
        self.proc: subprocess.Popen | None = None
        self.closed = False

    def ffmpeg_cmd(self) -> list[str]:
        a = self.args
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "warning",
            "-f", "v4l2", "-input_format", "mjpeg", "-video_size", a.size, "-framerate", str(a.fps),
            "-i", a.device,
            "-c:v", "copy", "-f", "mpjpeg", "pipe:1",
        ]

    def apply_controls(self) -> None:
        # UVC cameras may reset controls when reopened, so apply them before every stream start
        for ctrl in self.args.ctrl:
            result = subprocess.run(["v4l2-ctl", "-d", self.args.device, f"--set-ctrl={ctrl}"], capture_output=True, text=True)
            if result.returncode != 0:
                print(f"failed to set {ctrl}: {result.stderr.strip()}", file=sys.stderr, flush=True)

    def drop_stream(self) -> bool:
        """Stop the running ffmpeg (if any); the stream handler then finishes and releases the camera."""
        proc = self.proc
        if proc is None:
            return False
        proc.terminate()
        return True

    def status(self) -> dict:
        a = self.args
        return {
            "streaming": self.client is not None, "client": self.client, "closed": self.closed,
            "device": a.device, "size": a.size, "fps": a.fps, "ctrl": a.ctrl, "control": bool(a.token),
        }


def make_handler(cam: CamStream):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"{self.client_address[0]} {fmt % args}", file=sys.stderr, flush=True)

        def send_text(self, code: int, body: str, ctype: str = "text/plain") -> None:
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def send_json(self, code: int, obj: dict) -> None:
            self.send_text(code, json.dumps(obj) + "\n", "application/json")

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/status":
                self.send_json(200, cam.status())
            elif path in ("/", "/stream"):
                self.stream()
            else:
                self.send_text(404, "not found\n")

        def do_POST(self):
            url = urlsplit(self.path)
            if not url.path.startswith("/control/"):
                self.send_text(404, "not found\n")
                return
            if not self.authorized(url.query):
                return
            command = url.path[len("/control/"):]
            print(f"control '{command}' from {self.client_address[0]}", file=sys.stderr, flush=True)
            if command == "restart":
                dropped = cam.drop_stream()
                self.send_json(200, {"ok": True, "dropped_stream": dropped, "closed": cam.closed})
            elif command == "close":
                cam.closed = True
                dropped = cam.drop_stream()
                self.send_json(200, {"ok": True, "dropped_stream": dropped, "closed": True})
            elif command == "open":
                cam.closed = False
                self.send_json(200, {"ok": True, "closed": False})
            elif command == "shutdown":
                result = subprocess.run(["sudo", "-n", "/sbin/shutdown", "-h", "now"], capture_output=True, text=True)
                if result.returncode == 0:
                    self.send_json(202, {"ok": True, "message": "shutting down"})
                else:
                    self.send_json(500, {"ok": False, "error": result.stderr.strip() or "shutdown failed (sudoers rule missing?)"})
            else:
                self.send_text(404, "unknown control command\n")

        def authorized(self, query: str) -> bool:
            token = cam.args.token
            if not token:
                self.send_json(403, {"ok": False, "error": "remote control disabled: no token configured on the Pi"})
                return False
            given = self.headers.get("X-Camstream-Token") or (parse_qs(query).get("token") or [""])[0]
            if not hmac.compare_digest(given.encode(), token.encode()):
                self.send_json(403, {"ok": False, "error": "forbidden: wrong token"})
                return False
            return True

        def stream(self):
            if cam.closed:
                self.send_text(503, "camera closed by operator (POST /control/open to allow streaming again)\n")
                return
            if not cam.lock.acquire(blocking=False):
                self.send_text(409, f"camera busy, streaming to {cam.client}\n")
                return
            proc = None
            try:
                cam.client = self.client_address[0]
                print(f"camera ON for {cam.client}", file=sys.stderr, flush=True)
                cam.apply_controls()
                proc = subprocess.Popen(cam.ffmpeg_cmd(), stdout=subprocess.PIPE)
                cam.proc = proc
                first = proc.stdout.read1(65536)
                if not first:
                    self.send_text(503, "camera failed to start, see: journalctl -u camstream\n")
                    return
                self.send_response(200)
                self.send_header("Content-Type", f"multipart/x-mixed-replace;boundary={BOUNDARY}")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                chunk = first
                while chunk:
                    self.wfile.write(chunk)
                    chunk = proc.stdout.read1(65536)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Client disconnected
            finally:
                cam.proc = None
                if proc is not None:
                    # Close our end first, otherwise ffmpeg blocks writing its trailer into the unread pipe
                    proc.stdout.close()
                    proc.terminate()
                    try:
                        proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                print(f"camera OFF ({cam.client} disconnected)", file=sys.stderr, flush=True)
                cam.client = None
                cam.lock.release()

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--ctrl", action="append", default=[], metavar="NAME=VALUE",
                        help="v4l2 camera control applied on every stream start, repeatable (see: v4l2-ctl -d /dev/video0 --list-ctrls)")
    parser.add_argument("--token", default=os.environ.get("CAMSTREAM_TOKEN", ""),
                        help="shared secret for POST /control/* (default: $CAMSTREAM_TOKEN; empty = remote control disabled)")
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(CamStream(args)))
    server.daemon_threads = True
    control = "enabled" if args.token else "disabled (no token)"
    print(f"camstream on http://{args.host}:{args.port}/stream ({args.device} {args.size}@{args.fps}), remote control {control}", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

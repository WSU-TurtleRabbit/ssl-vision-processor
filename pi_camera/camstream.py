#!/usr/bin/env python3
"""On-demand MJPEG camera stream for a networked vision_processor camera.

The camera is only opened while a client is connected to /stream:
client connects -> ffmpeg starts, client disconnects -> ffmpeg stops.
Frames are passed through without re-encoding (camera MJPEG -> multipart HTTP).

Endpoints:
  GET /stream  multipart/x-mixed-replace MJPEG stream (one client at a time)
  GET /status  JSON {"streaming": bool, "client": str|null, "device": ..., "size": ..., "fps": ..., "ctrl": [...]}

Only depends on python3 (stdlib), ffmpeg and v4l-utils (for --ctrl).
"""
import argparse
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOUNDARY = "ffmpeg"  # ffmpeg's mpjpeg muxer default boundary


class CamStream:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.lock = threading.Lock()
        self.client: str | None = None

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

    def status(self) -> dict:
        a = self.args
        return {"streaming": self.client is not None, "client": self.client, "device": a.device, "size": a.size, "fps": a.fps, "ctrl": a.ctrl}


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

        def do_GET(self):
            if self.path == "/status":
                self.send_text(200, json.dumps(cam.status()) + "\n", "application/json")
            elif self.path in ("/", "/stream"):
                self.stream()
            else:
                self.send_text(404, "not found\n")

        def stream(self):
            if not cam.lock.acquire(blocking=False):
                self.send_text(409, f"camera busy, streaming to {cam.client}\n")
                return
            proc = None
            try:
                cam.client = self.client_address[0]
                print(f"camera ON for {cam.client}", file=sys.stderr, flush=True)
                cam.apply_controls()
                proc = subprocess.Popen(cam.ffmpeg_cmd(), stdout=subprocess.PIPE)
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
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), make_handler(CamStream(args)))
    server.daemon_threads = True
    print(f"camstream on http://{args.host}:{args.port}/stream ({args.device} {args.size}@{args.fps})", file=sys.stderr, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

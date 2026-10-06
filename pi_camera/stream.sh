#!/bin/bash
# Manual test stream: restarts after each viewer disconnects, a single Ctrl+C stops everything.
# For permanent use install camstream.service instead (see README.md).
DEVICE=${DEVICE:-/dev/video0}
SIZE=${SIZE:-1920x1080}
FPS=${FPS:-60}
PORT=${PORT:-8080}

stop=0; trap 'stop=1' INT
while [ $stop = 0 ]; do
  ffmpeg -hide_banner -loglevel warning -f v4l2 -input_format mjpeg -video_size "$SIZE" -framerate "$FPS" -i "$DEVICE" -c:v copy -f mpjpeg -listen 1 "http://0.0.0.0:$PORT"
  [ $stop = 0 ] && sleep 1
done
echo "stream stopped"

[🏠 Home](README.md) · 🚨 PANIC: [📷 Pi](pi-camera/panic.md) · [🎥 ZED](zed-box/panic.md)

# 🔌 USB camera on the Jetson: TODO

> **TODO, not in use.** This page and `config-usb-cam.yml` are an **untested draft** for a plain USB webcam, kept for later.
> **The ZED 2i does not use this page.** It has its own driver: [zed-box/camera.md](zed-box/camera.md).

The same USB camera could be plugged straight into the Jetson; then the Pi is not needed.

```mermaid
flowchart LR
    cam["USB camera"] -->|"USB 3 port"| vp["vision_processor<br/>config-usb-cam.yml"]
    vp --> be["backend"] --> fe["web page"]
```

| | Pi camera (current) | USB on the Jetson (TODO) |
|---|---|---|
| Camera position | anywhere on the network | within USB cable length (3–5 m) |
| Camera on/off | automatic | on while `vision_processor` runs |
| Camera settings | `--ctrl` on the Pi | `v4l2-ctl` on the Jetson |
| Config | `config-pi-cam.yml` | `config-usb-cam.yml` |

## Draft steps

1. **Find the camera:** `v4l2-ctl --list-devices`. Use the camera's first `/dev/videoN`, and set it as `path:` in `config-usb-cam.yml`.
2. **Start** like the Pi setup ([pi-camera/start-stop.md](pi-camera/start-stop.md)), with `config-usb-cam.yml` in both commands.
3. **Camera settings** are applied *after* `vision_processor` has started:
   ```bash
   v4l2-ctl -d /dev/video0 --set-ctrl=power_line_frequency=1 --set-ctrl=auto_exposure=1 --set-ctrl=exposure_time_absolute=100
   ```

## Known risk (untested)

`vision_processor` sets auto exposure through OpenCV (`src/driver/opencvdriver.cpp:39-44`), and on typical USB cameras those values may be **inverted**: `1` means manual there. So:
- leave `exposure:` out of the config;
- use `v4l2-ctl` instead;
- check the values stuck with `v4l2-ctl -d /dev/video0 --get-ctrl=auto_exposure,exposure_time_absolute`.

| Symptom | Fix |
|---|---|
| `GStreamer warning: ... no source element for URI "/dev/video0"`, then it stops | No camera at that path. Check `v4l2-ctl --list-devices` and that you're in the `video` group (`groups`). |
| `VIDEOIO(V4L2): ... not supported` | You picked the camera's metadata node. Use its first `/dev/videoN`. |
| `Device or resource busy` | Another program has the camera: `fuser /dev/video0` |
| Low fps | Use `fourcc: MJPG`, a USB 3 port, no hub |

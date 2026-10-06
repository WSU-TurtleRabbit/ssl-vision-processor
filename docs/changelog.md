[🏠 Home](README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md)

# 📝 What changed for the Jetson

## 2026-10-06, in 5 lines

1. `src/CameraModel.cpp` compiles with Ubuntu 22.04's Eigen 3.4.0. Everything else built unchanged.
2. Image processing runs on **PoCL** (OpenCL on the CPU), because NVIDIA ships no OpenCL for Jetson. A CUDA port is in progress on branch `cuda-backend`.
3. `src/opencl.cpp` keeps 4-channel images, because PoCL 1.8 rejects 1-channel ones. As a side effect, some debug pictures look wrong.
4. New `pi_camera/`: the Pi streams its USB camera on demand, and the camera is on only while `vision_processor` is connected.
5. New `config-pi-cam.yml` with sunlight-friendly colour learning, plus a colour panel and field-corner calibration on the web page. The website was fixed and tested from another PC.

## Details

- **Files.**
  - New event field file `geometry-event.yml`: foam mats 3030 × 1830 mm, so a 2730 × 1530 mm field plus a 150 mm boundary.
  - Camera height 1555 mm.
  - The lab field file `geometry-wrapper-lab-divB.yml` is unchanged.
- **Backend fixes.**
  - It builds its protobuf files with the repo's own `protoc`, and repairs broken ones.
  - The default field file is correct.
  - Snapshots are looked up by exact name.
  - The `vision_processor` status is detected properly.
- **Web page fixes.**
  - Only one port is needed (5173).
  - It works from other PCs.
  - A rebuilt page is served without restarting the backend.
  - Lint and format checks pass.
- **Colour features.**
  - `vision_processor` writes learned colours to `img/0.colors.json`.
  - The web page has a colour panel: save learned colours, or pick a colour from the image.
- **`python/geom_publisher.py`** only reads `proto/` now, so stray copies of the repo don't break it.
- **USB camera on the Jetson:** a draft only (TODO), see [usb-camera.md](usb-camera.md).

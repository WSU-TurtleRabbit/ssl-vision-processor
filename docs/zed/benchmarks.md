[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 🎥 ZED Box

# 📊 Benchmarks (ZED Box)

Measured results, newest first. Raw numbers are in [benchmark-data/](benchmark-data/). Rerun any of these with `tools/zed-benchmark.py` (stop `vision_processor` first). What we decided from them: [🧭 decisions.md](decisions.md).

## 2026-10-06: processing size sweep at 60 fps

**Question:** which processing size (`output_width` × `output_height`) gives 60 fps with the lowest delay and CPU?

**How:**
- `.venv/bin/python tools/zed-benchmark.py --sweep --seconds 30`
- ZED 2i in its 1280×720 mode at 60 fps, automatic exposure, CUDA build, `MAXN` power mode.
- Static lab field: 2 robots and 1 ball, nothing moving. One 30 s run per size, in the order shown.
- Raw data: [benchmark-data/2026-10-06-output-size-sweep.json](benchmark-data/2026-10-06-output-size-sweep.json).

| size | detections/s | ms per frame | CPU % (of 1 core) | robots per frame | robot confidence | ball % | ball confidence | camera → network delay | preset |
|---|---|---|---|---|---|---|---|---|---|
| 768×432 | 60.0 | 6.1 | 74 | 2.01 | 0.659 | 100 | 0.996 | 4.7 ms | |
| **832×468** | 59.8 | 4.5 | **66** | 2.00 | 0.670 | 100 | 0.988 | **3.4 ms** | `low` (default) |
| **896×504** | 59.8 | **4.4** | 72 | 2.00 | 0.774 | 100 | 0.953 | 3.8 ms | `medium` |
| 960×540 | 60.0 | 4.6 | 73 | 2.00 | 0.599 | 100 | 0.894 | 3.6 ms | |
| 1024×576 | 60.0 | 5.4 | 82 | 2.00 | 0.674 | 100 | 0.906 | 4.5 ms | |
| 1088×612 | 60.0 | 5.8 | 83 | 2.00 | 0.694 | 100 | 0.950 | 5.0 ms | |
| 1152×648 | 60.0 | 5.6 | 85 | 2.00 | 0.758 | 100 | 0.964 | 4.6 ms | |
| **1216×684** | 60.0 | 5.3 | 83 | 2.00 | **0.824** | 100 | 0.968 | 4.5 ms | `max` |
| 1280×720 | **57.1** ❌ | 14.2 | 120 | 2.00 | 0.626 | 100 | 0.944 | 12.6 ms | |

**What it shows:**
- **Every size up to 1216×684 holds 60 fps** and finds both robots and the ball in every frame.
- **1280×720 falls off a cliff:** 14 ms per frame, and it can't hold 60 fps. Why isn't known yet; it isn't a gradual slowdown.
- **832×468 has the lowest delay and CPU,** so it's the default for unattended running.
- **896×504 is almost as quick, with clearly higher robot confidence:** the `medium` preset.
- **1216×684 has the highest robot confidence,** for about 1 ms more delay and ~25 % more CPU: the `max` preset.

**Be careful with:**
- **Small differences.** Each size ran once on a still scene, so differences of ~1 ms or ~0.1 confidence are within run-to-run noise. Robot confidence doesn't follow size.
- **The 768×432 row.** It ran first, probably while the GPU was still warming up. On the same day, a separate run measured it at 2.7 ms delay.
- **The two "ball jitter ~37 mm" values in the raw data.** They are the detector switching between two ball candidates, not movement.
- **Not tested: a moving ball.** That's where size could matter most.

## 2026-10-06: frame rate and full size

Same setup, 30 s each, at 768×432 unless stated. Details: [camera.md → Frame rate and resolution](camera.md#frame-rate-and-resolution).

| | 30 fps | 60 fps | 60 fps at 1280×720 |
|---|---|---|---|
| detections per second | 30 | 60 | 54 |
| robot confidence | 0.654 | 0.656 | 0.605 |
| camera → network delay | 3.9 ms | 2.7 ms | 13.1 ms |

## 2026-10-06: depth (second lens)

NEURAL_LIGHT depth at 1280×720: **26 ms per frame** (median), floor noise ±26 mm, camera ~2.7 m above the floor. Why we don't use it while playing: [decisions.md → One camera, not two](decisions.md#one-camera-not-two).

## 2026-09-09: low resolution (672×376, webcam mode)

23.3 detections per second (vs 17.4), but the ball was never found (0 % vs 100 %) and robots dropped to 0.35 per frame. Rejected: [decisions.md](decisions.md#no-low-resolution-mode).

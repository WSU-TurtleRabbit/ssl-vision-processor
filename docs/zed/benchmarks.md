[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 🎥 ZED Box

# 📊 Benchmarks (ZED Box)

Measured results, newest first. Raw numbers are in [benchmark-data/](benchmark-data/). Rerun any of these with `tools/zed-benchmark.py` (stop `vision_processor` first). What we decided from them: [🧭 decisions.md](decisions.md).

## 2026-10-06: all sizes, 5 rounds (the reference result)

**Question:** with more repeats, which size is best at 60 fps for delay, CPU and confidence?

**How:**
- `.venv/bin/python tools/zed-benchmark.py --sweep --repeat 5 --seconds 20`
- 9 sizes × 5 rounds = 45 runs, one after another. Only one program can use the camera, so runs can't be parallel.
- The order was rotated each round. Static scene: 2 robots, 1 ball.
- Raw data: [benchmark-data/2026-10-06-output-size-sweep-5-rounds.json](benchmark-data/2026-10-06-output-size-sweep-5-rounds.json).

Mean of 5 runs (min–max):

| size | ms per frame | camera → network delay | CPU % (of 1 core) | robot confidence | detections/s | ball % |
|---|---|---|---|---|---|---|
| 768×432 | 4.8 (4.6–4.9) | 3.9 ms (3.9–4.1) | 73 (73–74) | 0.65 (0.64–0.68) | 60.0 | 100 |
| 832×468 | 6.6 (6.3–6.7) | 5.7 ms (5.3–5.9) | 79 (76–81) | 0.68 (0.67–0.68) | 60.0 | 100 |
| 896×504 | 6.1 (5.7–6.4) | 5.1 ms (4.9–5.3) | 80 (79–82) | 0.75 (0.73–0.76) | 60.0 | 100 |
| 960×540 | 5.4 (5.1–5.6) | 4.4 ms (4.1–4.7) | 79 (74–81) | 0.61 (0.61–0.61) | 60.0 | 100 |
| 1024×576 | 5.7 (5.7–5.9) | 4.9 ms (4.8–5.0) | 80 (78–82) | 0.67 (0.67–0.67) | 60.0 | 100 |
| 1088×612 | 6.1 (5.9–6.4) | 5.2 ms (5.1–5.3) | 82 (79–83) | 0.68 (0.67–0.68) | 60.0 | 100 |
| 1152×648 | 6.1 (5.8–6.1) | 5.3 ms (5.1–5.3) | 80 (73–83) | 0.75 (0.74–0.75) | 60.0 | 100 |
| 1216×684 | 5.7 (5.4–6.0) | 4.8 ms (4.6–5.2) | 81 (80–82) | 0.81 (0.80–0.82) | 60.0 | 100 |
| 1280×720 | calibration failed in all 5 rounds, 0 detections | | | | | |

**Presets chosen from this (2026-10-07):** `low` = 768×432, `max` = 1216×684.

**What it shows:**
- **768×432 is the fastest and cheapest** (3.9 ms delay, 73 % CPU). This agrees with the 2-round confirmation.
- **832×468 is the slowest** (5.7 ms), again. It has never been fast once warm-up is out of the way.
- **1216×684 is the most confident** (0.81) and, in this session, not slower than 896×504 (4.8 vs 5.1 ms). In the 2-round session 896 was 0.4 ms faster. **Between 896 and 1216 there's no reliable speed difference;** the confidence difference (0.75 vs 0.81) is reliable.
- **Absolute delays move ~1 ms between sessions** (768×432: 3.1 ms in the confirmation run, 3.9 ms here). Within one session the runs agree closely. **Compare sizes within a session, not across sessions.**
- **1280×720 failed to calibrate** ("Principal point outside of image") in every round, with today's corners and lens lines scaled up from 832×468. In the first sweep (scaled from 768×432) it calibrated. The corner-plus-lens fit can break at full size. It isn't offered as a preset.

## 2026-10-06: confirmation of the sweep (2 rounds, rotated order)

**Question:** do the sweep's best sizes hold up when each is run more than once, in changing order?

**How:** `.venv/bin/python tools/zed-benchmark.py --sweep --only 768x432 832x468 896x504 1216x684 --repeat 2`. Same setup as the sweep below. Round 1 ran 768, 832, 896, 1216; round 2 ran 832, 896, 1216, 768. Raw data: [benchmark-data/2026-10-06-output-size-confirm.json](benchmark-data/2026-10-06-output-size-confirm.json).

Mean of 2 runs (min–max):

| size | ms per frame | camera → network delay | CPU % (of 1 core) | robot confidence | detections/s | ball % |
|---|---|---|---|---|---|---|
| **768×432** | **4.1** (3.9–4.2) | **3.1 ms** (3.1–3.2) | 75 (73–76) | 0.66 | 60.0 | 100 |
| 832×468 | 6.4 (6.3–6.5) | 5.2 ms (4.9–5.5) | 77 (71–84) | 0.66 | 60.0 | 100 |
| 896×504 | 4.8 (4.6–5.1) | 3.9 ms (3.7–4.1) | 78 (77–79) | 0.76 | 60.0 | 100 |
| 1216×684 | 5.1 (4.8–5.3) | 4.3 ms (4.0–4.5) | 84 (82–85) | 0.82 | 60.0 | 100 |

**What it shows:**
- **The sweep's single run misled on 768 vs 832.** 768×432 ran first in the sweep, probably during GPU warm-up. Repeated and rotated, it has the **lowest delay**. 832×468 is consistently ~2 ms slower, at the same confidence.
- **832×468 is slow for its size** in all three of its runs that came after warm-up (4.5 in the sweep, 6.3 and 6.5 here). Why is not known.
- **Robot confidence rises with size in every run:** 0.66 (768 and 832), 0.76 (896), 0.82 (1216).
- **The timings repeat within ±0.3 ms** between rounds, so differences of 1 ms or more here are real.

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
| **832×468** | 59.8 | 4.5 | **66** | 2.00 | 0.670 | 100 | 0.988 | **3.4 ms** | (`low` until 2026-10-07) |
| **896×504** | 59.8 | **4.4** | 72 | 2.00 | 0.774 | 100 | 0.953 | 3.8 ms | (`medium` until 2026-10-07) |
| 960×540 | 60.0 | 4.6 | 73 | 2.00 | 0.599 | 100 | 0.894 | 3.6 ms | |
| 1024×576 | 60.0 | 5.4 | 82 | 2.00 | 0.674 | 100 | 0.906 | 4.5 ms | |
| 1088×612 | 60.0 | 5.8 | 83 | 2.00 | 0.694 | 100 | 0.950 | 5.0 ms | |
| 1152×648 | 60.0 | 5.6 | 85 | 2.00 | 0.758 | 100 | 0.964 | 4.6 ms | |
| **1216×684** | 60.0 | 5.3 | 83 | 2.00 | **0.824** | 100 | 0.968 | 4.5 ms | `max` |
| 1280×720 | **57.1** ❌ | 14.2 | 120 | 2.00 | 0.626 | 100 | 0.944 | 12.6 ms | |

**What it shows** (one run per size: see the confirmation above, which corrects the 768 vs 832 ranking):
- **Every size up to 1216×684 holds 60 fps** and finds both robots and the ball in every frame.
- **1280×720 falls off a cliff:** 14 ms per frame, and it can't hold 60 fps. Why isn't known yet; it isn't a gradual slowdown.
- ~~832×468 has the lowest delay and CPU~~: **not confirmed.** In the repeat runs above it was the slowest of the four.
- **896×504 is almost as quick, with clearly higher robot confidence.** It was briefly the `medium` preset; dropped, because repeat runs showed it no faster than 1216×684.
- **1216×684 has the highest robot confidence,** for about 1 ms more delay and ~25 % more CPU: the `max` preset.

**Be careful with:**
- **Small differences.** Each size ran once on a still scene, so differences of ~1 ms are not reliable here. The repeat runs above show that robot confidence *does* rise with size.
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

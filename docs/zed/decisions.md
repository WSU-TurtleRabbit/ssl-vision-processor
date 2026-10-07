[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 🎥 ZED Box

# 🧭 Decisions (ZED Box)

What we decided, why, and what would make us look again. Newest first. **Before re-trying something listed here, read why it was dropped.**

| Date | Decision | Status |
|---|---|---|
| 2026-10-06 | [One camera: use only the ZED's left lens, not "2 cameras"](#one-camera-not-two) | ✅ decided |
| 2026-10-07 | [Quality presets: low 768×432 (default) and max 1216×684](#quality-presets) | ✅ in use |
| 2026-10-06 | [60 fps](#60-fps-at-768432) | ✅ in use |
| 2026-10-06 | [Read the ZED with the ZED SDK, not as a webcam](#zed-sdk-instead-of-webcam-mode) | ✅ in use |
| 2026-10-06 | [Automatic exposure and white balance, with adapting colours](#automatic-exposure-with-adapting-colours) | ✅ in use |
| 2026-10-06 | [Raw (unrectified) picture](#raw-unrectified-picture) | ✅ in use |
| 2026-09-09 | [No low-resolution mode (672×376)](#no-low-resolution-mode) | ❌ rejected |

## One camera, not two

**Decision:** `vision_processor` uses **only the left lens**. We don't treat the ZED's two lenses as two cameras, and we don't compute depth while playing.

**Why:**

| Reason | Evidence |
|---|---|
| **No extra field coverage.** The two lenses sit 12 cm apart and see the same area. A second camera only helps if it looks at a part of the field the first can't see. | ZED 2i baseline 120 mm |
| **Depth halves the frame rate.** Depth is the only thing the second lens adds, and even the fastest mode (NEURAL_LIGHT) takes ~26 ms per frame. 60 fps leaves 16.7 ms. | Measured 2026-10-06: 26 ms median, 34 ms p90 |
| **Depth can't see the ball on the floor.** The floor's depth noise is about ±26 mm; a ball is 43 mm tall. | Measured 2026-10-06 |
| **Colour detection already finds everything.** Robots stand out in depth (100–180 mm), but the normal detector already sees them in every frame. | 2.00 robots per frame, ball in 100 % of frames |

**Still useful, later (TODO):** depth as a **one-time setup measurement**, not every frame:
- **Camera height.** Depth measured ~2.7 m, but the config says 2000 mm. See [camera.md](camera.md#camera-height).
- **A "something is on the field" check** against a snapshot of the empty field.

**Look again if:** we need the height of chipped balls in the air, or a much faster depth mode appears.

**Not the same as more cameras.** Several **separate** cameras covering different parts of the field (two ZEDs, or ZED + Pi) is **not rejected, only not done yet**:
- `vision_processor` already supports it: one process per camera, `cam_id` + `camera_amount`.
- The backend and web page handle one camera only.
- One ZED covers the whole lab field, so there's no need yet.

## Quality presets

**Decision (2026-10-07):** two processing sizes, switched in the web page:
- `low` 768×432, the default, because the system mostly runs unattended.
- `max` 1216×684.

**Why:** the 5-round sweep and the 2-round confirmation ([benchmarks.md](benchmarks.md)):
- **768×432 had the lowest delay and CPU** (3.9 ms, 73 %) in both sessions.
- **1216×684 had the highest robot confidence** (0.81 vs 0.65).
- **Every size up to 1216×684 holds 60 fps.**

**What changed from the first version (2026-10-06):**
- **`low` was 832×468,** picked from a single-run sweep that turned out misleading. Repeated, 832×468 was always the slowest (~2 ms more).
- **A `medium` 896×504 was dropped.** It was never reliably faster than 1216×684, and less confident.

**How:** one config file, `camera.quality_presets`, not three files: three copies of the corners and colours would drift apart. Switching rescales the pixel corners (exact, because the sensor mode doesn't change), clears the calibration and restarts `vision_processor` (`/api/camera/quality`).

**Look again if:** a moving-ball test reorders the sizes. All tests so far were on a still scene.

## 60 fps at 768×432

**Decision:** `fps: 60`, sensor mode 1280×720. This was first measured at 768×432; the size is now set by the [quality presets](#quality-presets).

**Why:** twice the detections and less delay than 30 fps, with the same quality. A bigger picture (1280×720) couldn't keep 60 fps and was less confident. Full table: [camera.md → Frame rate and resolution](camera.md#frame-rate-and-resolution).

**Look again if:** the GPU steps get faster (the SAT steps in `cuda/NOTES.md`) **and** a moving-ball test shows 768×432 losing the ball.

## ZED SDK instead of webcam mode

**Decision:** `driver: ZED` (ZED SDK), not `driver: OPENCV` on `/dev/video0`.

**Why:**

| | Webcam mode (old, `zed-config-lab-v4l2.yml`) | ZED SDK (now) |
|---|---|---|
| Frames per second | 17.4 | 60 |
| CPU use | ~3.25 cores | ~0.7 core |
| Picture work | CPU | GPU |

## Automatic exposure with adapting colours

**Decision:** exposure, gain and white balance on automatic. Colours adapt with `reference_force: 0.1` and `history_force: 0.7`.

**Why:** with automatic exposure but frozen colours (`reference_force: 0`), only 1 of 2 robots and no ball were found. With adapting colours: 2 robots and the ball in every frame.

**Look again if:** the light is fully fixed. Then manual exposure plus frozen colours is an option.

## Raw (unrectified) picture

**Decision:** `rectify: false`.

**Why:** the raw left picture lines up with the old webcam picture, so the existing field corners carried over unchanged. The lens bending is handled by the normal calibration.

**Not measured yet:** whether `rectify: true` (straightened by the ZED SDK) improves positions near the picture edges. It would need the corners clicked again.

## No low-resolution mode

**Decision:** don't use the ZED's 672×376 mode.

**Why:** measured 2026-09-09, 25 s each. It was 34 % faster (23.3 vs 17.4 detections per second), but the ball disappeared completely (0 % vs 100 %) and robots dropped from 2.00 to 0.35 per frame. The ball is only ~5 px across at 768×432, so less resolution loses it.

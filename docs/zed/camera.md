[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 🎥 ZED Box

# 🎥 ZED camera settings

All settings are in the `camera:` section of **`zed-config-lab.yml`**. After changing them, **Restart** `vision_processor` in the Services panel.

`vision_processor` reads the ZED through the **ZED SDK** (`driver: ZED`). The SDK grabs the left lens's picture and shrinks it on the GPU. Only the **left lens** is used for detection.

## The settings

| Setting | Now | Meaning |
|---|---|---|
| `driver` | `ZED` | Read the camera through the ZED SDK |
| `id` | `0` | First ZED on this computer. Use `serial: <number>` instead to pick a specific camera. |
| `width` / `height` | `1280` / `720` | Sensor mode per lens; `height` picks it. ZED 2i: 376 (up to 100 fps), 720 (60), 1080 (30), 1242 (15). |
| `fps` | `60` | Frames per second, up to the mode's limit above. |
| `quality` | `low` | Which processing-size preset is active. **Switch it in the web page** (next section), not by hand. |
| `quality_presets` | low / medium / max | The three sizes on offer |
| `output_width` / `output_height` | `832` / `468` | The size `vision_processor` actually works on. Set by `quality`. |
| `rectify` | `false` | `false`: the raw left picture. `true`: the SDK straightens the lens bending, but then the field corners must be clicked again. |
| `exposure` | `0.0` | `0` = automatic. Otherwise milliseconds. |
| `gain` | `0.0` | `0` = automatic. Otherwise 0–100. |
| `white_balance` | `OUTDOOR` | `OUTDOOR` or `INDOOR` = automatic. Manual: `white_balance: {temperature: 4500}` (2800–6500 K). |

**Automatic exposure and white balance move the colours a little.** That's why `zed-config-lab.yml` lets the colours adapt (`reference_force: 0.1`, `history_force: 0.7`). Don't set `reference_force: 0` (frozen colours) while exposure is automatic. See [🎨 colours.md](../colours.md).

## Quality presets (processing size)

**In the web page: System tab → Processing → Quality.** Click a preset. The page rescales the field corners to the new size, clears the old calibration and restarts `vision_processor`, about 10 s without detections. No re-clicking of corners is needed.

| Preset | Size | Use it when | Delay | CPU (of 1 core) | Robot confidence |
|---|---|---|---|---|---|
| **`low`** (default) | 832×468 | **unattended running**: lowest delay and CPU | 3.4 ms | 66 % | 0.67 |
| `medium` | 896×504 | someone is watching and wants a bit more certainty | 3.8 ms | 72 % | 0.77 |
| `max` | 1216×684 | tricky light or a match: most confident | 4.5 ms | 83 % | 0.82 |

All three hold 60 fps and found both robots and the ball in every frame ([📊 benchmarks.md](benchmarks.md)). 1280×720 isn't offered: it can't keep 60 fps.

## Frame rate and resolution

Why these settings, and what was tried and dropped: [🧭 decisions.md](decisions.md).


Measured on the lab field on 2026-10-06, 30 s each:

| | 30 fps, 768×432 | 60 fps, 768×432 | 60 fps, 1280×720 |
|---|---|---|---|
| detections per second | 30 | **60** | 54 (can't keep up) |
| robots per frame / ball found | 2.00 / 100 % | 2.00 / 100 % | 2.00 / 100 % |
| robot confidence | 0.654 | 0.656 | 0.605 |
| delay from camera to network | 3.9 ms | **2.7 ms** | 13.1 ms |

**Measure it yourself:** stop `vision_processor`, then run `.venv/bin/python tools/zed-benchmark.py`. It runs each setting for 30 s on the live field and prints a table like the one above (`--only "960x540"` runs just one).

- **These were the first tests, at 768×432.** The full size sweep that led to the presets is in [📊 benchmarks.md](benchmarks.md).
- **A smaller picture is worse.** Tested on 2026-09-09: 672×376 lost the ball completely.
- **fps well under 60?** Use a USB 3 port and check that nothing else is using the GPU heavily (`tegrastats`).

## Camera height

`camera_height` (under `geometry:`) must be measured by hand. The calibration can't work it out for a camera looking straight down. How to measure it: [📐 calibration.md](../calibration.md#measuring-the-field-and-the-camera).

**Check it.** The config says `2000`, but the ZED's depth measured about **2.7 m** from the lens to the floor (2026-10-06). Measure with a tape and fix the config. A wrong height shifts robots a few centimetres near the ends of the field.

## Checking the camera without vision_processor

Stop `vision_processor` first (only one program can use the ZED), then:

| Tool | Shows |
|---|---|
| `/usr/local/zed/tools/ZED_Explorer` | the live picture, and lets you try exposure, gain and so on |
| `/usr/local/zed/tools/ZED_Depth_Viewer` | the depth picture |
| `/usr/local/zed/tools/ZED_Diagnostic` | a USB, camera and GPU health check |

## TODO (future)

- **Image controls: hue, contrast, saturation, brightness, sharpness.** The ZED SDK supports them on the ZED 2i (hue 0–11; contrast, saturation, brightness and sharpness 0–8). They aren't in the config yet; the driver only sets exposure, gain, gamma and white balance. Plan:
  - **Manual:** new `camera:` keys (`hue:`, `contrast:`, …) passed to `setCameraSettings` in `src/driver/zeddriver.cpp`, the same way exposure is.
  - **Automatic:** the SDK has no automatic mode for these. "Auto" would mean our own loop, for example nudging saturation and contrast until the learned marker colours stay well apart. Do manual first, measure, then decide.
- **Use depth to measure the camera height** during setup, instead of typing `camera_height` in by hand.

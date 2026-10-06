[🏠 Home](README.md) · 🚨 PANIC: [📷 Pi](pi/panic.md) · [🎥 ZED](zed/panic.md)

# ⚡ Performance and the GPU (CUDA)

## Today

The image work runs on the **GPU with CUDA** wherever CUDA is installed. The first log line tells you which version you're running:

| First log line | Means |
|---|---|
| `Using device: CUDA … Orin` | GPU (fast) |
| `Using device: Portable Computing Language …` | CPU only (PoCL), about 10× slower |

| | 🎥 ZED Box (Orin NX, measured 2026-10-06) | 📷 Pi camera Jetson (AGX Orin) |
|---|---|---|
| Image work | GPU (CUDA) | GPU if built with CUDA, otherwise CPU (PoCL, 12 cores) |
| Time per frame | about **4 ms** | CUDA: about 3 ms · CPU: about 31–45 ms |
| Frames per second | **60** (the camera's limit at 720p) | camera-limited with CUDA · 25–30 with CPU |
| Camera → network delay | about 2.7 ms | not measured |
| CPU use | about 0.7 of one core (out of 8) | not measured |
| Decoding the camera video | done by the ZED SDK | about 8 ms per 1080p frame (not the bottleneck) |

The AGX numbers come from `cuda/NOTES.md` (one recorded video, 768×432).

## Free speed-up: full-power mode

```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
```

Check with `nvpmodel -q`, which should show `MAXN`. The 🎥 ZED Box already runs in `MAXN`. On the 📷 AGX it gives about 47 % more CPU clock (2.2 GHz instead of 1.5 GHz), which matters most for the CPU build.

## The CUDA port

- **Done and in use.** All image steps run on the GPU. Each one was checked against the original CPU version: same results, byte for byte.
- **Two programs from one codebase.** `vision_processor` uses the GPU. The CPU build (`cmake -B build -DWITH_CUDA=OFF .`) is the original code.
- **Details and measurements:** `cuda/NOTES.md`.
- **Possible later speed-ups**, not needed at 60 fps: faster SAT steps, fewer waits between steps. See "Remaining phases" in `cuda/NOTES.md`.

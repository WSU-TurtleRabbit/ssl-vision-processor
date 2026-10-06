[🏠 Home](README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md)

# ⚡ Performance and the GPU (CUDA)

## Today

| | Value |
|---|---|
| Where the image work runs | the **CPU** (12 cores), via PoCL. NVIDIA has no OpenCL for Jetson. |
| Time per frame | about **31–45 ms**, so 25–30 fps (measured in 50 W mode) |
| Decoding the 1080p camera video | about 8 ms per frame. Not the bottleneck. |

## Free speed-up: full-power mode

```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
```

That gives about 47 % more CPU clock (2.2 GHz instead of 1.5 GHz). Check with `nvpmodel -q`, which should show `MAXN`.

## Big speed-up: the CUDA port (in progress)

The image work is being moved onto the Orin's GPU, on branch `cuda-backend`:

```
✅ 0 probe   ✅ 1 build setup   ✅ 2 GPU layer   ✅ 3 simple steps   ⏳ 4 float steps   ⏳ 5 full test   ⬜ 6–7 polish
```

- **Two programs from one codebase.** `vision_processor` runs on the GPU; `vision_processor_opencl` runs on the CPU and is the original code.
- **Same results.** Each GPU step must give the same output as the CPU version. The 6 steps ported so far match byte for byte.
- **Per-step speed so far:** colour conversion 4.8 → 0.04 ms, gradient 4.6 → 0.09 ms.
- **Not usable for live use yet.** Detailed notes are in `cuda/NOTES.md` on the branch.

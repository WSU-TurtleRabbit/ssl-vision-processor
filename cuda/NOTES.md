# CUDA compute backend – working notes

Goal: a CUDA backend for the vision processor that produces the same results as the
OpenCL backend, while the upstream OpenCL code (`src/opencl.*`, `kernel/*.cl`) stays
untouched and mergeable. PoCL 1.8 (CPU, the only OpenCL implementation on the Jetson
AGX Orin / JetPack 6 image) is the ground truth.

## Phase 0 – PoCL 1.8 semantics (probed on the Jetson, `cuda/probes/`)

All probes run on `Portable Computing Language » pthread` (PoCL 1.8-3, aarch64).

### Image formats
* `CL_R` images (`CL_UNSIGNED_INT8` and `CL_FLOAT`) cannot be created: error `-59`
  (`CL_INVALID_OPERATION`). This is why `src/opencl.cpp` on this branch uses `CL_RGBA`
  for `PixelFormat::U8` and `PixelFormat::F32` (left as is).
  Consequence for comparisons: on the OpenCL side a U8 pixel occupies 4 bytes and an
  F32 pixel 16 bytes; channel 0 holds the value.
* Scalar writes broadcast: `write_imageui(img, pos, 77u)` on `CL_RGBA/UNSIGNED_INT8`
  stores `77 77 77 77`; `write_imagef(img, pos, 1.5f)` on `CL_RGBA/CL_FLOAT` stores
  `1.5 1.5 1.5 1.5`. Channel 0 is therefore always the value the kernel meant.
* The CUDA backend uses true single-channel U8/F32 images (upstream semantics).

### `read_imageui` with `CLK_FILTER_LINEAR` (undefined by the OpenCL spec for integer images)
Used by `quad2rgba.cl`, `quad2nv12.cl`, `resampling.cl`
(`CLK_FILTER_LINEAR | CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_CLAMP_TO_EDGE`).

* **float2 coordinates:** PoCL performs a real bilinear interpolation in single precision
  and **truncates** (round toward zero) to uint. Exact model, 0 mismatches over
  3.1 M random/edge coordinates × 4 channels (offsets ±0.25, arbitrary fractions,
  coordinates outside the image, values next to x.5 boundaries):
  ```
  uu = u - 0.5f;  vv = v - 0.5f;
  i0 = floorf(uu); j0 = floorf(vv);  a = uu - i0;  b = vv - j0;   // no fract() clamp observable
  w00 = (1-a)*(1-b); w10 = a*(1-b); w01 = (1-a)*b; w11 = a*b;     // each rounded separately
  T.. = pixel at clamp_to_edge(i0|i0+1, j0|j0+1)
  result = (uint)(((w00*T00 + w10*T10) + w01*T01) + w11*T11)       // NO fma, left to right, truncate
  ```
  Rejected models: nearest floor(u) / floor(u+0.5) (11.5 M mismatches), bilinear with
  round-half-up / rint (5.9 M), same expression with fma chain (47 k), lerp form (53 k).
  Note: for the demosaicing offsets ±0.25 at integer positions all weights are multiples of
  1/16, so the sum is exact and only truncation matters; for `resampling.cl` (arbitrary
  coordinates) the operation order above is required for bit-exactness.
* `(pos.x + 0.25f, ...)` at integer `pos` therefore really blends neighbouring pixels
  (uu = x - 0.25 → i0 = x-1, a = 0.75): the demosaic is a bilinear interpolation, not a copy.
* **int2 coordinates with a LINEAR sampler** (BGR branches): behaves like NEAREST
  with clamp-to-edge (0 mismatches, incl. out-of-range coordinates).
* NEAREST with float2 coordinates = `floor()` (0 mismatches).

### Conversions / math
* `convert_uchar_sat(float)` / `convert_char_sat(float)`: round toward zero, saturate,
  NaN → 0 (`254.99 → 254`, `-0.99 → 0`, `inf → 255`, `-inf → 0`).
* `native_sqrt` is correctly rounded (0 / 1 M differences vs `sqrtf`).
* Division is correctly rounded (also with `-cl-fast-relaxed-math`).
* **FP contraction: PoCL fuses `a*b + c` into an fma _within one expression_**
  (OpenCL C default `FP_CONTRACT ON`, clang semantics), but not across statements
  (`t = a*b; t + c` is unfused). For `a*b + c*d` the left product is fused:
  `fma(a, b, c*d)`. Same with `-cl-fast-relaxed-math`.
  **Correction to the earlier plan:** compiling CUDA with `-fmad=false` alone does *not*
  reproduce PoCL; nvcc with `-fmad=true` fuses across statements and is wrong too.
  Rule for ports: build with `-fmad=false` and write every contraction clang would do
  explicitly with `__fmaf_rn()` (per expression, left operand first). This only matters for
  float kernels (resampling `field2image`, satBlobCenter, blobList, blobScore, blobCenter);
  the Phase-3 kernels are integer/exact.
* Integer arithmetic in the NV12 kernels: `(66*color.r + ...)` is unsigned (uint4), the UV
  terms are cast to int (signed division, truncation toward zero), then `convert_uchar_sat`.

### Known non-determinism in the OpenCL reference
* `quad2nv12` / `rgb2nv12`: the 4 work items of a 2×2 block all write the same UV byte
  pair (last writer wins). Results are compared race-tolerant (UV must equal the value of
  one of the 4 candidate pixels). See Phase 3 results for what PoCL actually picks.
* `blobList`: `atomic_inc` order → match list order is nondeterministic; compare as
  multisets.
* Struct layouts: `CLCameraModel` (Perspective.h) / `CameraModel` (resampling.cl) are
  72 B packed (not 80 as assumed in the plan); `CLMatch` (main.cpp) / `Match` (blobList.cl) are 22 B packed with
  misaligned floats → byte-wise stores on the CUDA side, `static_assert` the layouts.

## Layout (Phases 1–3)

```
src/compute.h                  backend switch (VP_BACKEND_CUDA → cuda_compute.h, else opencl.h)
cuda/include/cuda_compute.h    façade with the same shape as src/opencl.h + minimal `cl` shim
cuda/include/cuda_kernel_abi.h GlobalSize, ImageView, signature tags, kernel registration (host + nvcc)
cuda/src/cuda_compute.cpp      runtime: pinned mapped memory, pools, maps, events, registry, launch
cuda/kernels/*.cu              one file per kernel/*.cl, cl_emulation.cuh = PoCL-exact built-ins
cuda/tools/kernel_compare.cpp  per-kernel comparison harness (built for both backends)
cuda/probes/*.cpp              Phase-0 PoCL probes
```

Build: `cmake -B build-cuda . && make -j12 -C build-cuda` builds `vision_processor`,
`geometry_benchmark`, `blob_benchmark`, `kernel_compare` (CUDA) and the same with `_opencl`.
`-DWITH_CUDA=OFF` gives the upstream OpenCL-only build with the original target names.
`WITH_CUDA` defaults to ON on aarch64 when nvcc is found (`/usr/local/cuda/bin/nvcc` is picked
up automatically), `CMAKE_CUDA_ARCHITECTURES` defaults to 87.

Runtime design:
* Memory: `cudaHostAlloc(cudaHostAllocMapped)` (zero-copy, I/O coherent on Orin), zero
  initialised. Not managed memory: Orin reports `concurrentManagedAccess=0` and the CPU maps
  buffers from other threads while kernels run (rtpstreamer, snapshotwriter, spinnaker).
  Maps = `cudaStreamSynchronize` + host pointer (same blocking semantics as the OpenCL maps on
  the in-order queue), unmap is a no-op. Images are tightly packed (pitch = width × pixel size).
* `compile(code, options)`: kernel identified by pointer identity with the embedded
  `kernel_*_cl` symbol (CMake generates `cuda_kernel_table.h`), variant from `-D` options.
  Unported kernel/variant → fatal error only when run:
  `[CUDA] Kernel resampling (variant 'BGR') is not ported to the CUDA backend yet`.
* `run()`: images → `ImageView`, buffers → device pointer, values by copy; `GlobalSize` is
  prepended; argument types are checked against the `__global__` signature (pointers and
  structs by size, like `clSetKernelArg`); 16×16 blocks (2D) / 256 (1D) on one non-blocking
  in-order stream; pooled `cudaEvent` pairs, `printRuntimes()` prints the same format.

## Phase 3 results (integer kernels)

Ported: raw2quad, quad2rgba, quad2nv12 (BGR/RGGB/GRBG), rgba2nv12, f2nv12, gradientDot.

```
build-cuda/kernel_compare_opencl dump ref && build-cuda/kernel_compare dump test
build-cuda/kernel_compare compare ref test      # exit code 1 on mismatch
build-cuda/kernel_compare bench                  # also: kernel_compare_opencl bench
```
Inputs: random raw images 1280×720 (BGR), 640×360 quads (RGGB, GRBG) and 34×18 for all
variants, random RGBA (incl. alpha), floats in [-400, 400] with NaN/±inf, gradient offsets 1/2/5.
78 outputs, all bit-exact:
* raw2quad, quad2rgba, gradientDot, f2nv12 (Y and UV), NV12 Y planes: 0 differences.
* NV12 UV planes (quad2nv12, rgba2nv12): 2–10 % of the UV pairs differ, **all** explained by
  the write race; the reference itself is nondeterministic (two PoCL runs differ in up to
  2620 / 230400 UV pairs). PoCL mostly ends with the pixel (1,1) of each 2×2 block (≈96 %),
  sometimes (1,0) (row work-groups finishing out of order). CUDA deterministically writes UV
  from the last pixel of the block, (1,1), i.e. the most frequent PoCL result.
* CUDA results are deterministic (two runs identical).
* `vision_processor` smoke test (OpenCV driver, BGR, no geometry): CUDA and `_opencl` both log
  "Saved sample image"; the two `img/0.raw.jpg` files are byte-identical.

Average time per call incl. launch + synchronisation (`kernel_compare bench`):

| kernel      | PoCL 1280×720 BGR | CUDA  | PoCL 640×360 RGGB | CUDA  |
|-------------|------------------:|------:|------------------:|------:|
| raw2quad    | 1.44 ms | 0.13 ms | 0.66 ms | 0.05 ms |
| quad2rgba   | 2.95 ms | 0.17 ms | 4.80 ms | 0.04 ms |
| quad2nv12   | 3.26 ms | 0.09 ms | 4.97 ms | 0.05 ms |
| rgba2nv12   | 1.56 ms | 0.20 ms | 0.57 ms | 0.03 ms |
| gradientDot | 4.59 ms | 0.09 ms | 1.20 ms | 0.03 ms |
| f2nv12      | 0.87 ms | 0.10 ms | 0.35 ms | 0.03 ms |

## Phase 4 results (float kernels)

Ported: resampling (BGR/RGGB/GRBG), satHorizontal, satVertical, satBlobCenter, blobList,
blobScore (`cuda/kernels/resampling.cu`, `sat.cu`, `blobList.cu`). blobCenter.cl is not used by
any target and stays unported.

* `CLCameraModel` / `CameraModel` is **72 B** packed (2 int + 16 float), not 80 B as assumed
  earlier; `Match` is 22 B. Both are `static_assert`ed (size and offsets); Match floats are
  stored byte-wise.
* Fused expressions written explicitly (`__fmaf_rn`), everything else `__f*_rn`:
  `gid*fieldScale + offset`, `r0*x + r1*y + r2*z` → `fma(r2, z, fma(r0, x, r1*y))`,
  `1 + d*(rx+ry)` → `fma(d, rx+ry, 1)`, `f*rayU + p` → `fma(f, rayU, p)`,
  `neg - 2*center + pos` → `fma(-2, center, neg) + pos`. The stddev term
  `s2 - s1*s1/n` is not fused (the product feeds a division).
  Cross-check: replacing only the inner `fma(r0, x, r1*y)` by an unfused sum makes
  60–165 resampled pixels per image differ by 1 and changes 1–2 blobList matches.
* Harness: synthetic 1280×720 scene (noisy mat + 60 coloured discs) as BGR and as RGGB/GRBG
  mosaic, two camera views ("inner" 600×340 px at 5 mm/px, "wide" 600×333 at 6 mm/px with
  out-of-image coordinates and stronger distortion), full `rgba2blobCenter` call sequence, then
  blobList with the production threshold (≈50–60 matches), a low threshold (3.6–4.5 k matches)
  and an overflow run (maxMatches 16).
* Result: all 132 outputs pass. Images (resampling RGBA, gradientDot, SAT, satBlobCenter,
  blobScore): 0 bitwise differences (max ULP 0). blobList: counters identical, match multisets
  identical; overflow runs store 16 matches that are all in the reference's full set.
  Only difference found: NaN payloads (flat peaks give 0/0 in the sub-pixel interpolation:
  aarch64 0x7fc00000, CUDA 0x7fffffff), canonicalised by the comparison.

## Findings / risks

* On this Jetson the `_opencl` host views of U8/F32 images are wrong because of the
  `CL_RGBA` workaround: `CLImageMap` builds 1-channel `cv::Mat`s / `rowPitch` over 4-channel
  rows (`snapshotwriter` gradient/blob JPEGs, `CLImage::save` for F32, `blob_benchmark`'s
  `circMap`). Kernel results are fine. The CUDA backend has correct host views, so end-to-end
  comparisons must only compare kernel outputs / detections, not these debug images.
* The smoke test only reaches "Saved sample image" if no geometry arrives on the network: on
  the lab network another process publishes geometry on 224.5.23.2:10006, so use isolated ports
  (`network: {vision_port: 10996, gc_port: 10993}`) in the test config.
* FP contraction (see Phase 0) makes the float kernels the delicate part: every
  `a*b + c` / `a - b*c` inside one OpenCL expression must become `__fmaf_rn` in the port.
* `blobList`: besides the atomic order, which matches survive when more than `maxBlobs`
  are found is nondeterministic in the reference.
* Pinned zero-copy memory is fine for the streaming kernels; the neighbourhood-heavy blob
  kernels may want device memory for intermediate images (Phase 7).

## Remaining phases

4. Float kernels: resampling (field2image with explicit fma, 72 B packed CameraModel with
   static_assert), satHorizontal/satVertical (serial prefix sums, same order), satBlobCenter,
   blobList (atomics, packed 22 B Match with byte-wise stores), blobScore/blobCenter (only used
   by benchmarks); extend kernel_compare (match lists as multisets).
5. End-to-end comparison: vision_processor / blob_benchmark / geometry_benchmark on recorded
   videos (detections, blob lists), both backends.
6. Hardening: driver paths not testable here (Spinnaker persistent maps, mvIMPACT copy
   constructor), error paths, thread-safety review of concurrent maps.
7. Performance: per-kernel `await` sync overhead, device memory for intermediates, CUDA
   graphs / fewer syncs, CPU spin vs blocking sync (`cudaDeviceScheduleBlockingSync`).

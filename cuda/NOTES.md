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
  80 B packed; `CLMatch` (main.cpp) / `Match` (blobList.cl) are 22 B packed with
  misaligned floats → byte-wise stores on the CUDA side, `static_assert` the layouts.

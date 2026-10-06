// SPDX-License-Identifier: Apache-2.0
#pragma once

// Compile-time compute backend selection. Both backends expose the same API
// (OpenCL, CLArray, CLMap, RawImage, CLImage, CLImageMap, cl::Kernel, ...).
#ifdef VP_BACKEND_CUDA
#include "cuda_compute.h"
#else
#include "opencl.h"
#endif

// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/f2nv12.cl
#include "cl_emulation.cuh"

using namespace vpcuda;

__global__ void f2nv12(GlobalSize gs, ImageView in, uint8_t* out) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	out[x + y*gs.x] = convertUcharSat(__fadd_rn(readClamped<float>(in, x, y), 127.0f));
	out[in.width*in.height + x + (y/2)*gs.x] = 127;
}

VP_CUDA_REGISTER("f2nv12", "", 2, &f2nv12);

// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/rgba2nv12.cl (kernel function rgb2nv12)
#include "cl_emulation.cuh"

using namespace vpcuda;

__global__ void rgba2nv12(GlobalSize gs, ImageView in, uint8_t* out) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const uchar4 v = readClamped<uchar4>(in, x, y);
	writeNV12(out, gs, in.width, in.height, x, y, Color{v.x, v.y, v.z});
}

VP_CUDA_REGISTER("rgba2nv12", "", 2, &rgba2nv12);

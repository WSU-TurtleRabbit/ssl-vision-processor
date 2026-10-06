// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/quad2nv12.cl
#include "cl_emulation.cuh"

using namespace vpcuda;

template<int V>
__global__ void quad2nv12(GlobalSize gs, ImageView channel0, ImageView channel1, ImageView channel2, ImageView channel3, uint8_t* out) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const Color color = quadColor<V>(channel0, channel1, channel2, channel3, x, y);
	writeNV12(out, gs, channel0.width, channel0.height, x, y, color);
}

VP_CUDA_REGISTER("quad2nv12", "BGR", 2, &quad2nv12<VARIANT_BGR>);
VP_CUDA_REGISTER("quad2nv12", "RGGB", 2, &quad2nv12<VARIANT_RGGB>);
VP_CUDA_REGISTER("quad2nv12", "GRBG", 2, &quad2nv12<VARIANT_GRBG>);

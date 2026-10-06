// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/quad2rgba.cl
#include "cl_emulation.cuh"

using namespace vpcuda;

template<int V>
__global__ void quad2rgba(GlobalSize gs, ImageView channel0, ImageView channel1, ImageView channel2, ImageView channel3, ImageView out) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const Color color = quadColor<V>(channel0, channel1, channel2, channel3, x, y);
	write<uchar4>(out, x, y, make_uchar4((uint8_t)color.r, (uint8_t)color.g, (uint8_t)color.b, 255));
}

VP_CUDA_REGISTER("quad2rgba", "BGR", 2, &quad2rgba<VARIANT_BGR>);
VP_CUDA_REGISTER("quad2rgba", "RGGB", 2, &quad2rgba<VARIANT_RGGB>);
VP_CUDA_REGISTER("quad2rgba", "GRBG", 2, &quad2rgba<VARIANT_GRBG>);

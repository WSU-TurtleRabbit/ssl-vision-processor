// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/raw2quad.cl
#include "cl_emulation.cuh"

using namespace vpcuda;

template<int V>
__global__ void raw2quad(GlobalSize gs, const uint8_t* img, ImageView channel0, ImageView channel1, ImageView channel2, ImageView channel3) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	if constexpr(V == VARIANT_BGR) {
		const int imgpos = 3*(x + y*gs.x);
		write<uint8_t>(channel0, x, y, img[imgpos]);
		write<uint8_t>(channel1, x, y, img[imgpos+1]);
		write<uint8_t>(channel2, x, y, img[imgpos+2]);
	} else {
		const int rowSize = 2*gs.x;
		const int imgpos = 2*x + 2*y*rowSize;
		write<uint8_t>(channel0, x, y, img[imgpos]);
		write<uint8_t>(channel1, x, y, img[imgpos+1]);
		write<uint8_t>(channel2, x, y, img[imgpos+rowSize]);
		write<uint8_t>(channel3, x, y, img[imgpos+1+rowSize]);
	}
}

VP_CUDA_REGISTER("raw2quad", "BGR", 2, &raw2quad<VARIANT_BGR>);
VP_CUDA_REGISTER("raw2quad", "RGGB", 2, &raw2quad<VARIANT_RGGB>);
VP_CUDA_REGISTER("raw2quad", "GRBG", 2, &raw2quad<VARIANT_GRBG>);

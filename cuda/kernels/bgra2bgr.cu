// SPDX-License-Identifier: Apache-2.0
// Camera input conversion for drivers that produce GPU images (ZED SDK). Not a port of a kernel/*.cl.
#include <cstdint>
#include <cuda_runtime.h>

#include "log.h"

namespace vpcuda {

cudaStream_t computeStream();

__global__ void bgra2bgr(const uint8_t* src, size_t srcPitch, uint8_t* dst, int width, int height) {
	const int x = blockIdx.x * blockDim.x + threadIdx.x;
	const int y = blockIdx.y * blockDim.y + threadIdx.y;
	if(x >= width || y >= height)
		return;

	const uchar4 pixel = *(const uchar4*)(src + y*srcPitch + 4*x);
	uint8_t* out = dst + 3*(x + y*width);
	out[0] = pixel.x;
	out[1] = pixel.y;
	out[2] = pixel.z;
}

void bgra2bgr(const void* srcDevice, size_t srcPitch, void* dstDevice, int width, int height) {
	const dim3 block(32, 8);
	const dim3 grid((width + block.x - 1) / block.x, (height + block.y - 1) / block.y);
	bgra2bgr<<<grid, block, 0, computeStream()>>>((const uint8_t*)srcDevice, srcPitch, (uint8_t*)dstDevice, width, height);
	const cudaError_t error = cudaGetLastError();
	if(error != cudaSuccess) {
		FATAL("[CUDA] bgra2bgr launch failed: " << cudaGetErrorName(error) << " (" << cudaGetErrorString(error) << ")");
	}
}

} // namespace vpcuda

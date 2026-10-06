// SPDX-License-Identifier: Apache-2.0
// CUDA ports of kernel/satHorizontal.cl, kernel/satVertical.cl and kernel/satBlobCenter.cl
#include "cl_emulation.cuh"

using namespace vpcuda;

/** One work item per row, sequential prefix sum (same summation order as OpenCL). */
__global__ void satHorizontal(GlobalSize gs, ImageView in, ImageView out) {
	int y;
	if(!globalId1(gs, y))
		return;

	float sum = 0.f;
	for(int x = 0; x < in.width; x++) {
		sum = __fadd_rn(sum, *pixel<float>(in, x, y));
		write<float>(out, x, y, sum);
	}
}

/** One work item per column, sequential prefix sum. */
__global__ void satVertical(GlobalSize gs, ImageView in, ImageView out) {
	int x;
	if(!globalId1(gs, x))
		return;

	float sum = 0.f;
	for(int y = 0; y < in.height; y++) {
		sum = __fadd_rn(sum, *pixel<float>(in, x, y));
		write<float>(out, x, y, sum);
	}
}

__device__ __forceinline__ float satRead(const ImageView& sat, int x, int y, int dx, int dy) {
	return readClamped<float>(sat, x + dx, y + dy);
}

__device__ __forceinline__ float quadrant(const ImageView& sat, int x, int y, int a, int b, int c, int d, int e, int f, int g, int h) {
	// read(a,b) - read(c,d) - read(e,f) + read(g,h), left to right
	return __fadd_rn(__fsub_rn(__fsub_rn(satRead(sat, x, y, a, b), satRead(sat, x, y, c, d)), satRead(sat, x, y, e, f)), satRead(sat, x, y, g, h));
}

__global__ void satBlobCenter(GlobalSize gs, ImageView sat, ImageView out, int maxBlobRadius) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const int r = maxBlobRadius;
	const float ppScore = quadrant(sat, x, y,  r,  r,  r,  1,  1,  r,  1,  1);
	const float pnScore = quadrant(sat, x, y,  r, -r,  r, -1,  1, -r,  1, -1);
	const float npScore = quadrant(sat, x, y, -r,  r, -r,  1, -1,  r, -1,  1);
	const float nnScore = quadrant(sat, x, y, -r, -r, -r, -1, -1, -r, -1, -1);
	write<float>(out, x, y, __fdiv_rn(fminf(fminf(ppScore, nnScore), fminf(pnScore, npScore)), (float)(r*r)));
}

VP_CUDA_REGISTER("satHorizontal", "", 1, &satHorizontal);
VP_CUDA_REGISTER("satVertical", "", 1, &satVertical);
VP_CUDA_REGISTER("satBlobCenter", "", 2, &satBlobCenter);

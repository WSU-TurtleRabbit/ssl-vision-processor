// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/gradientDot.cl (kernel function gradient_dotproduct)
#include "cl_emulation.cuh"

using namespace vpcuda;

__global__ void gradientDot(GlobalSize gs, ImageView in, ImageView out, int offset) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const uchar4 px = readClamped<uchar4>(in, x+offset, y);
	const uchar4 nx = readClamped<uchar4>(in, x-offset, y);
	const uchar4 py = readClamped<uchar4>(in, x, y+offset);
	const uchar4 ny = readClamped<uchar4>(in, x, y-offset);

	// gx *= gy; gx.x + gx.y + gx.z -- all terms are small integers, products and sums are exact in float
	const float gxr = __fsub_rn((float)px.x, (float)nx.x), gyr = __fsub_rn((float)py.x, (float)ny.x);
	const float gxg = __fsub_rn((float)px.y, (float)nx.y), gyg = __fsub_rn((float)py.y, (float)ny.y);
	const float gxb = __fsub_rn((float)px.z, (float)nx.z), gyb = __fsub_rn((float)py.z, (float)ny.z);
	write<float>(out, x, y, __fadd_rn(__fadd_rn(__fmul_rn(gxr, gyr), __fmul_rn(gxg, gyg)), __fmul_rn(gxb, gyb)));
}

VP_CUDA_REGISTER("gradientDot", "", 2, &gradientDot);

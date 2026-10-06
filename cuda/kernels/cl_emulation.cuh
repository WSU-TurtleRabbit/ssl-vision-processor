// SPDX-License-Identifier: Apache-2.0
#pragma once

// Device helpers emulating the OpenCL C built-ins used by kernel/*.cl with the exact semantics of
// the PoCL 1.8 reference (see cuda/NOTES.md, Phase 0). Sampling is done in software, not with
// texture hardware, to stay bit-exact.

#include <cstdint>
#include "cuda_kernel_abi.h"

namespace vpcuda {

enum Variant { VARIANT_NONE = 0, VARIANT_BGR = 1, VARIANT_RGGB = 2, VARIANT_GRBG = 3 };

/** get_global_id(0/1); returns false for padding threads outside the global range. */
__device__ __forceinline__ bool globalId2(const GlobalSize& gs, int& x, int& y) {
	x = (int)(blockIdx.x * blockDim.x + threadIdx.x);
	y = (int)(blockIdx.y * blockDim.y + threadIdx.y);
	return x < gs.x && y < gs.y;
}

__device__ __forceinline__ bool globalId1(const GlobalSize& gs, int& x) {
	x = (int)(blockIdx.x * blockDim.x + threadIdx.x);
	return x < gs.x;
}

__device__ __forceinline__ int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

template<typename T>
__device__ __forceinline__ T* pixel(const ImageView& img, int x, int y) {
	return (T*)(img.data + (size_t)y * img.pitch) + x;
}

/** CLK_ADDRESS_CLAMP_TO_EDGE (also used for CLK_ADDRESS_NONE, where out-of-range reads are undefined anyway). */
template<typename T>
__device__ __forceinline__ T readClamped(const ImageView& img, int x, int y) {
	return *pixel<T>(img, clampi(x, 0, img.width - 1), clampi(y, 0, img.height - 1));
}

template<typename T>
__device__ __forceinline__ void write(const ImageView& img, int x, int y, T value) {
	*pixel<T>(img, x, y) = value;
}

/**
 * read_imageui(U8 image, CLK_FILTER_LINEAR | CLK_NORMALIZED_COORDS_FALSE | CLK_ADDRESS_CLAMP_TO_EDGE, float2)
 * as implemented by PoCL 1.8: single precision bilinear interpolation without fma, summed left to right,
 * truncated to uint.
 */
__device__ __forceinline__ unsigned int readLinearU8(const ImageView& img, float u, float v) {
	const float uu = __fsub_rn(u, 0.5f);
	const float vv = __fsub_rn(v, 0.5f);
	const float fi0 = floorf(uu);
	const float fj0 = floorf(vv);
	const float a = __fsub_rn(uu, fi0);
	const float b = __fsub_rn(vv, fj0);
	const int i0 = (int)fi0;
	const int j0 = (int)fj0;

	const float t00 = readClamped<uint8_t>(img, i0, j0);
	const float t10 = readClamped<uint8_t>(img, i0 + 1, j0);
	const float t01 = readClamped<uint8_t>(img, i0, j0 + 1);
	const float t11 = readClamped<uint8_t>(img, i0 + 1, j0 + 1);

	const float a1 = __fsub_rn(1.0f, a);
	const float b1 = __fsub_rn(1.0f, b);
	const float w00 = __fmul_rn(a1, b1);
	const float w10 = __fmul_rn(a, b1);
	const float w01 = __fmul_rn(a1, b);
	const float w11 = __fmul_rn(a, b);

	float sum = __fmul_rn(w00, t00);
	sum = __fadd_rn(sum, __fmul_rn(w10, t10));
	sum = __fadd_rn(sum, __fmul_rn(w01, t01));
	sum = __fadd_rn(sum, __fmul_rn(w11, t11));
	return (unsigned int)sum; // convert_uint: round toward zero, sum >= 0
}

/** convert_uchar_sat for integers */
__device__ __forceinline__ uint8_t convertUcharSat(int v) { return (uint8_t)clampi(v, 0, 255); }
__device__ __forceinline__ uint8_t convertUcharSat(unsigned int v) { return (uint8_t)(v > 255u ? 255u : v); }

/** convert_uchar_sat(float): round toward zero, saturate, NaN -> 0 (PoCL) */
__device__ __forceinline__ uint8_t convertUcharSat(float v) {
	if(!(v > 0.0f)) // also NaN
		return 0;
	if(v >= 255.0f)
		return 255;
	return (uint8_t)v;
}

struct Color {
	unsigned int r, g, b;
};

/** Demosaic/channel merge shared by quad2rgba.cl, quad2nv12.cl (integer positions) */
template<int V>
__device__ __forceinline__ Color quadColor(const ImageView& c0, const ImageView& c1, const ImageView& c2, const ImageView& c3, int x, int y) {
	Color color{};
	if constexpr(V == VARIANT_BGR) {
		// int2 coordinates with a LINEAR sampler behave like NEAREST + clamp to edge in PoCL
		color.r = readClamped<uint8_t>(c2, x, y);
		color.g = readClamped<uint8_t>(c1, x, y);
		color.b = readClamped<uint8_t>(c0, x, y);
	} else {
		const float fx = (float)x;
		const float fy = (float)y;
		const float xp = __fadd_rn(fx, 0.25f), xn = __fsub_rn(fx, 0.25f);
		const float yp = __fadd_rn(fy, 0.25f), yn = __fsub_rn(fy, 0.25f);
		if constexpr(V == VARIANT_RGGB) {
			color.r = readLinearU8(c0, xp, yp);
			color.g = readLinearU8(c1, xn, yp)/2 + readLinearU8(c2, xp, yn)/2;
			color.b = readLinearU8(c3, xn, yn);
		} else if constexpr(V == VARIANT_GRBG) {
			color.r = readLinearU8(c1, xn, yp);
			color.g = readLinearU8(c0, xp, yp)/2 + readLinearU8(c3, xn, yn)/2;
			color.b = readLinearU8(c2, xp, yn);
		}
	}
	return color;
}

/**
 * NV12 output shared by quad2nv12.cl and rgba2nv12.cl. In OpenCL all 4 work items of a 2x2 block write
 * the UV pair (last writer wins, nondeterministic). Here only the last work item of the block (highest x,
 * then highest y; clamped at odd image sizes) writes, which is deterministic.
 */
__device__ __forceinline__ void writeNV12(uint8_t* out, const GlobalSize& gs, int width, int height, int x, int y, const Color& color) {
	out[x + y*gs.x] = convertUcharSat((66u*color.r + 129u*color.g + 25u*color.b) / 256u + 16u);

	const bool lastX = (x & 1) || x == gs.x - 1;
	const bool lastY = (y & 1) || y == gs.y - 1;
	if(!lastX || !lastY)
		return;

	const int uvout = width*height + (x/2)*2 + (y/2)*gs.x;
	out[uvout] = convertUcharSat((-38*(int)color.r + -74*(int)color.g + 112*(int)color.b) / 256 + 128);
	out[uvout+1] = convertUcharSat((112*(int)color.r + -94*(int)color.g + -18*(int)color.b) / 256 + 128);
}

} // namespace vpcuda

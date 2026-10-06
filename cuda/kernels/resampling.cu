// SPDX-License-Identifier: Apache-2.0
// CUDA port of kernel/resampling.cl
//
// Float semantics follow PoCL (cuda/NOTES.md): every a*b+c inside one OpenCL expression is fused
// (left product first), separate statements are not. The comments show the original expressions.
#include <cstddef>
#include "cl_emulation.cuh"

using namespace vpcuda;

/** Same layout as CameraModel in resampling.cl and CLCameraModel in src/Perspective.h */
struct __attribute__ ((packed)) CameraModel {
	int shape[2];  // raw image shape
	float f;       // focal length
	float p[2];    // principal point
	float d;       // distortion
	float r[9];    // rotation matrix
	float c[3];    // camera position
};
static_assert(sizeof(CameraModel) == 72, "CameraModel must match the packed OpenCL struct (72 bytes)");
static_assert(offsetof(CameraModel, f) == 8 && offsetof(CameraModel, p) == 12 && offsetof(CameraModel, d) == 20 && offsetof(CameraModel, r) == 24 && offsetof(CameraModel, c) == 60, "CameraModel layout");

__device__ __forceinline__ float2 field2image(const CameraModel& m, float fx, float fy, float fz) {
	// fieldpos -= (float3)(m.c[0], m.c[1], m.c[2]);
	fx = __fsub_rn(fx, m.c[0]);
	fy = __fsub_rn(fy, m.c[1]);
	fz = __fsub_rn(fz, m.c[2]);

	// m.r[0] * fieldpos.x + m.r[1] * fieldpos.y + m.r[2] * fieldpos.z  ==  fma(r2, z, fma(r0, x, r1*y))
	const float camX = __fmaf_rn(m.r[2], fz, __fmaf_rn(m.r[0], fx, __fmul_rn(m.r[1], fy)));
	const float camY = __fmaf_rn(m.r[5], fz, __fmaf_rn(m.r[3], fx, __fmul_rn(m.r[4], fy)));
	const float camZ = __fmaf_rn(m.r[8], fz, __fmaf_rn(m.r[6], fx, __fmul_rn(m.r[7], fy)));

	const float ray2x = __fdiv_rn(camX, camZ);
	const float ray2y = __fdiv_rn(camY, camZ);
	float rayUx = ray2x;
	float rayUy = ray2y;
	for(int i = 0; i < 8; i++) {
		// float2 r = camRayU*camRayU;
		const float rx = __fmul_rn(rayUx, rayUx);
		const float ry = __fmul_rn(rayUy, rayUy);
		// float dr = 1 + m.d*(r.x + r.y);
		const float dr = __fmaf_rn(m.d, __fadd_rn(rx, ry), 1.0f);
		rayUx = __fdiv_rn(ray2x, dr);
		rayUy = __fdiv_rn(ray2y, dr);
	}
	// m.f * camRayU + (float2)(m.p[0], m.p[1])
	return make_float2(__fmaf_rn(m.f, rayUx, m.p[0]), __fmaf_rn(m.f, rayUy, m.p[1]));
}

template<int V>
__global__ void resampling(GlobalSize gs, ImageView channel0, ImageView channel1, ImageView channel2, ImageView channel3, ImageView out, CameraModel model, float maxRobotHeight, float fieldScale, float fieldOffsetX, float fieldOffsetY) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	// get_global_id(0)*fieldScale + fieldOffsetX
	const float2 pos = field2image(model, __fmaf_rn((float)x, fieldScale, fieldOffsetX), __fmaf_rn((float)y, fieldScale, fieldOffsetY), maxRobotHeight);

	unsigned int r, g, b;
	if constexpr(V == VARIANT_BGR) {
		r = readLinearU8(channel2, pos.x, pos.y);
		g = readLinearU8(channel1, pos.x, pos.y);
		b = readLinearU8(channel0, pos.x, pos.y);
	} else {
		const float xp = __fadd_rn(pos.x, 0.25f), xn = __fsub_rn(pos.x, 0.25f);
		const float yp = __fadd_rn(pos.y, 0.25f), yn = __fsub_rn(pos.y, 0.25f);
		if constexpr(V == VARIANT_RGGB) {
			r = readLinearU8(channel0, xp, yp);
			g = readLinearU8(channel1, xn, yp)/2 + readLinearU8(channel2, xp, yn)/2;
			b = readLinearU8(channel3, xn, yn);
		} else {
			r = readLinearU8(channel1, xn, yp);
			g = readLinearU8(channel0, xp, yp)/2 + readLinearU8(channel3, xn, yn)/2;
			b = readLinearU8(channel2, xp, yn);
		}
	}

	// dRGB, unsigned arithmetic as in OpenCL (wraps, result is always in [0, 255])
	write<uchar4>(out, x, y, make_uchar4(
			(uint8_t)((2u*r - g - b + 510u) / 4u),
			(uint8_t)((2u*g - b - r + 510u) / 4u),
			(uint8_t)((2u*b - r - g + 510u) / 4u),
			255
	));
}

VP_CUDA_REGISTER("resampling", "BGR", 2, &resampling<VARIANT_BGR>);
VP_CUDA_REGISTER("resampling", "RGGB", 2, &resampling<VARIANT_RGGB>);
VP_CUDA_REGISTER("resampling", "GRBG", 2, &resampling<VARIANT_GRBG>);

// SPDX-License-Identifier: Apache-2.0
// CUDA ports of kernel/blobList.cl (kernel function matches) and kernel/blobScore.cl (replica used by blob_benchmark)
#include <cstddef>
#include "cl_emulation.cuh"

using namespace vpcuda;

/** Same layout as Match in blobList.cl and CLMatch in src/main.cpp (22 bytes, misaligned floats) */
struct __attribute__ ((packed)) Match {
	float x, y;
	uint8_t color[3];
	uint8_t center[3];
	float circ;
	float score;
};
static_assert(sizeof(Match) == 22, "Match must match the packed OpenCL struct");
static_assert(offsetof(Match, color) == 8 && offsetof(Match, center) == 11 && offsetof(Match, circ) == 14 && offsetof(Match, score) == 18, "Match layout");

__device__ __forceinline__ void storeFloat(uint8_t* dst, float value) {
	const unsigned int bits = __float_as_uint(value);
	dst[0] = bits & 0xff;
	dst[1] = (bits >> 8) & 0xff;
	dst[2] = (bits >> 16) & 0xff;
	dst[3] = bits >> 24;
}

struct CircleStats {
	unsigned int s1[3];
	int n;
	float stddevSum; // stddev.x + stddev.y + stddev.z
};

__device__ __forceinline__ CircleStats circleStats(const ImageView& img, int px, int py, int radius) {
	// https://en.wikipedia.org/wiki/Standard_deviation#Rapid_calculation_methods
	CircleStats stats{{0, 0, 0}, 0, 0.0f};
	unsigned int s2[3] = {0, 0, 0};
	const int sqRadius = radius*radius;
	for(int y = -radius; y <= radius; y++) {
		for(int x = -radius; x <= radius; x++) {
			if(x*x + y*y <= sqRadius) {
				const uchar4 v = readClamped<uchar4>(img, px + x, py + y);
				stats.s1[0] += v.x; stats.s1[1] += v.y; stats.s1[2] += v.z;
				s2[0] += (unsigned int)v.x*v.x; s2[1] += (unsigned int)v.y*v.y; s2[2] += (unsigned int)v.z*v.z;
				stats.n++;
			}
		}
	}

	// native_sqrt((convert_float4(s2) - convert_float4(s1)*convert_float4(s1)/n) / n): the product feeds a division, no fusion
	const float n = (float)stats.n;
	float stddev[3];
	for(int c = 0; c < 3; c++) {
		const float s1f = (float)stats.s1[c];
		stddev[c] = __fsqrt_rn(__fdiv_rn(__fsub_rn((float)s2[c], __fdiv_rn(__fmul_rn(s1f, s1f), n)), n));
	}
	stats.stddevSum = __fadd_rn(__fadd_rn(stddev[0], stddev[1]), stddev[2]);
	return stats;
}

/** pos + 0.5f * (neg - pos') / (neg - 2*center + pos')  -- PoCL fuses neg - 2*center into fma(-2, center, neg) */
__device__ __forceinline__ float peakInterpolation(int pos, float neg, float center, float posValue) {
	const float denominator = __fadd_rn(__fmaf_rn(-2.0f, center, neg), posValue);
	return __fadd_rn((float)pos, __fdiv_rn(__fmul_rn(0.5f, __fsub_rn(neg, posValue)), denominator));
}

__global__ void blobList(GlobalSize gs, ImageView img, ImageView circ, uint8_t* matches, int* counter, float circThreshold, float minScore, int radius, int maxMatches) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const float circScore = readClamped<float>(circ, x, y);
	if(circScore < circThreshold)
		return;

	// Filter to only local peaks
	const float circNegX = readClamped<float>(circ, x-1, y);
	const float circPosX = readClamped<float>(circ, x+1, y);
	const float circNegY = readClamped<float>(circ, x, y-1);
	const float circPosY = readClamped<float>(circ, x, y+1);
	if(circNegX > circScore || circPosX > circScore || circNegY > circScore || circPosY > circScore) {
		atomicAdd(counter+2, 1);
		return;
	}

	const CircleStats stats = circleStats(img, x, y, radius);
	const float score = __fdiv_rn(circScore, stats.stddevSum);
	if(score < minScore) {
		atomicAdd(counter+1, 1);
		return;
	}

	const uchar4 center = readClamped<uchar4>(img, x, y);
	const int i = atomicAdd(counter, 1);
	if(i >= maxMatches)
		return;

	uint8_t* match = matches + (size_t)i * sizeof(Match);
	storeFloat(match + offsetof(Match, x), peakInterpolation(x, circNegX, circScore, circPosX));
	storeFloat(match + offsetof(Match, y), peakInterpolation(y, circNegY, circScore, circPosY));
	for(int c = 0; c < 3; c++)
		match[offsetof(Match, color) + c] = (uint8_t)(stats.s1[c] / (unsigned int)stats.n);
	match[offsetof(Match, center) + 0] = center.x;
	match[offsetof(Match, center) + 1] = center.y;
	match[offsetof(Match, center) + 2] = center.z;
	storeFloat(match + offsetof(Match, circ), circScore);
	storeFloat(match + offsetof(Match, score), score);
}

__global__ void blobScore(GlobalSize gs, ImageView img, ImageView circ, ImageView score, float circThreshold, int radius) {
	int x, y;
	if(!globalId2(gs, x, y))
		return;

	const float circScore = readClamped<float>(circ, x, y);
	if(circScore < circThreshold ||
			readClamped<float>(circ, x-1, y) > circScore ||
			readClamped<float>(circ, x+1, y) > circScore ||
			readClamped<float>(circ, x, y-1) > circScore ||
			readClamped<float>(circ, x, y+1) > circScore) {
		write<float>(score, x, y, -INFINITY);
		return;
	}

	const CircleStats stats = circleStats(img, x, y, radius);
	write<float>(score, x, y, __fdiv_rn(circScore, stats.stddevSum));
}

VP_CUDA_REGISTER("blobList", "", 2, &blobList);
VP_CUDA_REGISTER("blobScore", "", 2, &blobScore);

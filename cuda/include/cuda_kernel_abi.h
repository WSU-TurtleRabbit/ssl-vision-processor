// SPDX-License-Identifier: Apache-2.0
#pragma once

// Types shared between the host façade (cuda_compute.h, compiled by the C++ compiler) and the
// CUDA kernels (compiled by nvcc). Keep this header free of OpenCV/Eigen/CUDA includes.

#include <cstddef>
#include <cstdint>
#include <string>
#include <type_traits>
#include <typeindex>
#include <vector>

namespace vpcuda {

/** Equivalent of get_global_size(0/1); always passed as the first kernel parameter. */
struct GlobalSize {
	int x;
	int y;
};

/** Device view of an image (replaces image2d_t). Pixels are tightly packed per row, rows are pitch bytes apart. */
struct ImageView {
	unsigned char* data;
	int pitch; // bytes per row
	int width;
	int height;
};

// Signature tags used for the host<->kernel argument type check
struct DevPtr {};                // any global buffer pointer (cl::Buffer)
template<size_t N> struct PodArg {}; // any struct passed by value, checked by size (like clSetKernelArg)

template<typename T>
struct ArgSignature {
	using type = std::conditional_t<std::is_pointer_v<T>, DevPtr,
	             std::conditional_t<std::is_class_v<T> && !std::is_same_v<T, ImageView>, PodArg<sizeof(T)>, T>>;
};

struct KernelEntry {
	std::string name;    // kernel file stem, e.g. "quad2rgba"
	std::string variant; // compile option variant: "", "RGGB", "GRBG", "BGR"
	const void* function;
	std::vector<std::type_index> signature; // without the leading GlobalSize
	int dimensions; // 1 or 2, used for the default block shape
};

void registerKernel(KernelEntry entry);

template<typename... A>
struct KernelRegistrar {
	KernelRegistrar(const char* name, const char* variant, int dimensions, void (*function)(GlobalSize, A...)) {
		registerKernel(KernelEntry{name, variant, (const void*)function, {std::type_index(typeid(typename ArgSignature<A>::type))...}, dimensions});
	}
};

} // namespace vpcuda

#define VP_CUDA_CONCAT_(a, b) a##b
#define VP_CUDA_CONCAT(a, b) VP_CUDA_CONCAT_(a, b)
/** Register a __global__ function as the CUDA implementation of kernel/<name>.cl built with -D<variant>. */
#define VP_CUDA_REGISTER(name, variant, dimensions, function) \
	static const vpcuda::KernelRegistrar VP_CUDA_CONCAT(vpCudaRegistrar, __LINE__)(name, variant, dimensions, function)

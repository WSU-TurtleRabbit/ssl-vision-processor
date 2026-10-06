// SPDX-License-Identifier: Apache-2.0
#pragma once

// CUDA implementation of the compute API declared by src/opencl.h. The class layout mirrors
// opencl.h so that all call sites compile unchanged; only the subset of the OpenCL C++ API used
// by the call sites is provided in the `cl` namespace. Memory is pinned, mapped host memory
// (zero-copy on the integrated Jetson GPU), maps synchronize the stream and return host pointers.

#include <cstdint>
#include <map>
#include <memory>
#include <string>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include <opencv2/core/mat.hpp>

#include "log.h"
#include "cuda_kernel_abi.h"


typedef int8_t cl_char;
typedef uint8_t cl_uchar;
typedef int16_t cl_short;
typedef uint16_t cl_ushort;
typedef int32_t cl_int;
typedef uint32_t cl_uint;
typedef int64_t cl_long;
typedef uint64_t cl_ulong;
typedef float cl_float;

#ifndef CL_SUCCESS
#define CL_SUCCESS 0
#define CL_MAP_READ (1 << 0)
#define CL_MAP_WRITE (1 << 1)
#define CL_MAP_WRITE_INVALIDATE_REGION (1 << 2)
#endif


namespace vpcuda {

/** Pinned + mapped host allocation (cudaHostAlloc(cudaHostAllocMapped)), zero initialized. */
class Allocation {
public:
	explicit Allocation(size_t size);
	~Allocation();
	Allocation(const Allocation&) = delete;
	Allocation& operator=(const Allocation&) = delete;

	void* host = nullptr;
	void* device = nullptr;
	const size_t size;
};

struct EventPair;

/** Blocks until all work enqueued so far on the compute stream has finished. */
void synchronize();

/** The in-order compute stream all kernels run on (a cudaStream_t), for drivers that enqueue GPU work themselves. */
void* computeStreamHandle();

/** Converts a pitched device BGRA image into a tightly packed BGR image (e.g. a RawImage's device pointer) on the compute stream. */
void bgra2bgr(const void* srcDevice, size_t srcPitch, void* dstDevice, int width, int height);

} // namespace vpcuda


namespace cl {

class Buffer {
public:
	Buffer() = default;
	explicit Buffer(size_t size, const void* data = nullptr);

	[[nodiscard]] void* host() const { return alloc ? alloc->host : nullptr; }
	[[nodiscard]] void* device() const { return alloc ? alloc->device : nullptr; }
	[[nodiscard]] size_t size() const { return alloc ? alloc->size : 0; }

	bool operator==(const Buffer& other) const { return alloc == other.alloc; }
	bool operator!=(const Buffer& other) const { return alloc != other.alloc; }

private:
	std::shared_ptr<vpcuda::Allocation> alloc;
};

class Image2D {
public:
	Image2D() = default;
	Image2D(int width, int height, int pixelBytes);

	[[nodiscard]] void* host() const { return alloc ? alloc->host : nullptr; }
	[[nodiscard]] vpcuda::ImageView view() const { return {alloc ? (unsigned char*)alloc->device : nullptr, pitch, width, height}; }

	bool operator==(const Image2D& other) const { return alloc == other.alloc; }
	bool operator!=(const Image2D& other) const { return alloc != other.alloc; }

	int width = 0;
	int height = 0;
	int pitch = 0; // bytes per row

private:
	std::shared_ptr<vpcuda::Allocation> alloc;
};

class NDRange {
public:
	explicit NDRange(size_t x): dimensions(1), sizes{x, 1} {}
	NDRange(size_t x, size_t y): dimensions(2), sizes{x, y} {}

	int dimensions;
	size_t sizes[2];
};

class Event {
public:
	Event() = default;
	explicit Event(std::shared_ptr<vpcuda::EventPair> pair): pair(std::move(pair)) {}

	/** Blocks until the kernel finished, returns CL_SUCCESS or the CUDA error code. */
	int wait() const;
	/** Kernel runtime in ms (waits for completion). */
	[[nodiscard]] float runtime() const;

	std::shared_ptr<vpcuda::EventPair> pair;
};

/** Event dependencies are implied: all kernels run in order on a single stream. */
class EnqueueArgs {
public:
	explicit EnqueueArgs(NDRange global): global(global) {}
	EnqueueArgs(const Event& /*dependency*/, NDRange global): global(global) {}
	EnqueueArgs(const std::vector<Event>& /*dependencies*/, NDRange global): global(global) {}

	NDRange global;
};

class Kernel {
public:
	Kernel() = default;
	Kernel(const vpcuda::KernelEntry* entry, std::string name, std::string variant): entry(entry), name(std::move(name)), variant(std::move(variant)) {}

	const vpcuda::KernelEntry* entry = nullptr; // nullptr: not ported to CUDA (fatal on run)
	std::string name;
	std::string variant;
};

} // namespace cl


namespace vpcuda {

// Host argument -> kernel parameter conversion
inline ImageView toKernelArg(const cl::Image2D& image) { return image.view(); }
inline void* toKernelArg(const cl::Buffer& buffer) { return buffer.device(); }
template<typename T>
inline T toKernelArg(const T& value) {
	static_assert(std::is_trivially_copyable_v<T>, "Kernel arguments must be images, buffers or trivially copyable values");
	return value;
}
template<typename T>
using KernelArg = decltype(toKernelArg(std::declval<const T&>()));

} // namespace vpcuda


class PixelFormat {
public:
	// CLImage formats
	static const PixelFormat RGBA8;
	static const PixelFormat U8;
	static const PixelFormat F32;
	static const PixelFormat NV12;

	// Raw Bayer formats
	static const PixelFormat RGGB8;
	static const PixelFormat GRBG8;

	static const PixelFormat BGR8;

	[[nodiscard]] int pixelSize() const { return stride*rowStride; }

	const int stride;
	const int rowStride;
	const bool color;
	const int cvType;
	const int imageBytes; // bytes per pixel when used as CLImage, 0 if not usable as image

	const char* kernelOptions;
private:
	PixelFormat(int stride, int rowStride, bool color, int cvType, int imageBytes, const char* kernelOptions): stride(stride), rowStride(rowStride), color(color), cvType(cvType), imageBytes(imageBytes), kernelOptions(kernelOptions) {}
	PixelFormat(int stride, int rowStride, bool color, int cvType, int imageBytes): PixelFormat(stride, rowStride, color, cvType, imageBytes, "") {}
};


typedef struct __attribute__ ((packed)) RGBA {
	cl_uchar r;
	cl_uchar g;
	cl_uchar b;
	cl_uchar a;
} RGBA;

class CLImage;
class RawImage;


class OpenCL {
public:
	OpenCL();

	cl::Kernel compile(const char* code, const std::string& options = "");

	template<typename... Ts>
	cl::Event run(cl::Kernel kernel, const cl::EnqueueArgs& args, Ts... ts) {
		static const std::vector<std::type_index> signature = {std::type_index(typeid(typename vpcuda::ArgSignature<vpcuda::KernelArg<Ts>>::type))...};
		std::tuple<vpcuda::KernelArg<Ts>...> converted(vpcuda::toKernelArg(ts)...);
		vpcuda::GlobalSize globalSize{};
		void* params[sizeof...(Ts) + 1];
		params[0] = &globalSize;
		std::apply([&](auto&... arg) {
			int i = 1;
			((params[i++] = (void*)&arg), ...);
		}, converted);

		cl::Event event = launch(kernel, args, signature, globalSize, params);
		events.push_back(event);
		return event;
	}

	template<typename... Ts>
	void await(cl::Kernel kernel, const cl::EnqueueArgs& args, Ts... ts) {
		wait(run(kernel, args, std::forward<Ts>(ts)...));
	}

	static void wait(const cl::Event& event);

	void printRuntimes();
	void clearEvents();

	std::shared_ptr<CLImage> acquire(const PixelFormat* format, int width, int height, const std::string& name);

	std::shared_ptr<RawImage> acquireNV12(int width, int height);

private:
	cl::Event launch(const cl::Kernel& kernel, const cl::EnqueueArgs& args, const std::vector<std::type_index>& signature, vpcuda::GlobalSize& globalSize, void** params);
	std::shared_ptr<vpcuda::EventPair> acquireEvent();

	std::map<const PixelFormat*, std::vector<std::shared_ptr<CLImage>>> pool;
	std::vector<std::shared_ptr<RawImage>> nv12pool;
	std::vector<std::shared_ptr<vpcuda::EventPair>> eventPool;

	std::vector<cl::Event> events;
};

template<typename T>
class CLMap {
public:
	explicit CLMap(const cl::Buffer& buffer, int /*size*/, int /*clRWType*/): buffer(buffer) {
		vpcuda::synchronize();
		map = (T*) buffer.host();
	}
	~CLMap() = default; // Zero-copy memory: nothing to unmap

	CLMap (CLMap&& other) noexcept: buffer(other.buffer), map(std::move(other.map)) {
		other.unmoved = false;
	}
	CLMap ( const CLMap & ) = delete;
	CLMap& operator= ( const CLMap & ) = delete;
	T*& operator*() { return map; }
	T* operator-> () { return map; }
	T& operator [] (int i) { return map[i]; }
	const T* const& operator*() const { return map; }
	const T* operator-> () const { return map; }
	const T& operator [] (int i) const { return map[i]; }

private:
	const cl::Buffer buffer; // keeps the allocation alive while mapped
	T* map;
	bool unmoved = true;
};

class CLArray {
public:
	explicit CLArray(int size);
	CLArray(void* data, int size);

	template<typename T> CLMap<T> read() const { return CLMap<T>(buffer, size, CL_MAP_READ); }
	template<typename T> CLMap<T> write() { return CLMap<T>(buffer, size, CL_MAP_WRITE_INVALIDATE_REGION); }
	template<typename T> CLMap<T> readWrite() { return CLMap<T>(buffer, size, CL_MAP_WRITE); }

	const cl::Buffer buffer;
	const int size;
};


class RawImage : public CLArray {
public:
	RawImage(const RawImage& other) = default;
	RawImage(const PixelFormat* format, int width, int height): CLArray(width * height * format->pixelSize()), format(format), width(width), height(height), name() {}
	RawImage(CLArray array, const PixelFormat* format, int width, int height, std::string name): CLArray(std::move(array)), format(format), width(width), height(height), name(std::move(name)) {}
	RawImage(const PixelFormat* format, int width, int height, std::string name): CLArray(width * height * format->pixelSize()), format(format), width(width), height(height), name(std::move(name)) {}
	RawImage(const PixelFormat* format, int width, int height, double timestamp): CLArray(width * height * format->pixelSize()), format(format), width(width), height(height), timestamp(timestamp), name() {}

	//Only use these constructors if not possible otherwise due to necessary copy (because of potential alignment mismatch for zero-copy support)
	RawImage(const PixelFormat* format, int width, int height, unsigned char* data): CLArray(data, width * height * format->pixelSize()), format(format), width(width), height(height) {}
	RawImage(const PixelFormat* format, int width, int height, double timestamp, unsigned char* data): CLArray(data, width * height * format->pixelSize()), format(format), width(width), height(height), timestamp(timestamp) {}

	virtual ~RawImage() = default;

	const PixelFormat* format;
	const int width;
	const int height;
	// timestamp of 0 indicates unavailability
	double timestamp = 0;
	const std::string name;
};


template<typename T>
class CLImageMap;


class CLImage {
public:
	explicit CLImage(const PixelFormat* format);
	CLImage(const PixelFormat* format, int width, int height, std::string name);

	template<typename T> CLImageMap<T> read() const { return CLImageMap<T>(*this, CL_MAP_READ); }
	template<typename T> CLImageMap<T> write() { return CLImageMap<T>(*this, CL_MAP_WRITE_INVALIDATE_REGION); }
	template<typename T> CLImageMap<T> readWrite() { return CLImageMap<T>(*this, CL_MAP_WRITE); }

	void save(const std::string& suffix, float factor = 1.0f, float offset = 0.0f) const;

	cl::Image2D image;

	const PixelFormat* format;
	int width;
	int height;
	std::string name;
};


template<typename T>
class CLImageMap {
public:
	explicit CLImageMap(const CLImage& image, int /*clRWType*/): image(image.image) {
		vpcuda::synchronize();
		map = (T*) image.image.host();
		bytePitch = image.image.pitch;
		rowPitch = bytePitch/sizeof(T);
		cv = ::cv::Mat(image.height, image.width, image.format->cvType, map, bytePitch);
	}
	~CLImageMap() = default; // Zero-copy memory: nothing to unmap

	CLImageMap (CLImageMap&& other) noexcept: bytePitch(other.bytePitch), rowPitch(other.rowPitch), cv(other.cv), image(other.image), map(std::move(other.map)) {
		other.unmoved = false;
	}
	CLImageMap ( const CLImageMap & ) = delete;
	CLImageMap& operator= ( const CLImageMap & ) = delete;
	T*& operator*() { return map; }
	T* operator-> () { return map; }
	T& operator [] (int i) { return map[i]; }
	T& operator()(int x, int y) { return map[x + y * rowPitch]; }
	const T& operator()(int x, int y) const { return map[x + y * rowPitch]; }
	const T* const& operator*() const { return map; }
	const T* operator-> () const { return map; }
	const T& operator [] (int i) const { return map[i]; }

	size_t bytePitch;
	size_t rowPitch;
	cv::Mat cv;

private:
	const cl::Image2D image; // keeps the allocation alive while mapped
	T* map;
	bool unmoved = true;
};

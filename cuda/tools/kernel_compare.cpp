// SPDX-License-Identifier: Apache-2.0
//
// Per-kernel comparison harness for the CUDA backend. The same source is built against both backends
// (kernel_compare = CUDA, kernel_compare_opencl = OpenCL) and only uses the common compute API.
//
//   kernel_compare_opencl dump <dir>        run all ported kernels on deterministic inputs, dump outputs
//   kernel_compare dump <dir>
//   kernel_compare compare <refDir> <testDir>   compare dumps (exit code 1 on mismatch)
//   kernel_compare bench                    average runtime per kernel (1280x720 BGR / 640x360 quads)
//
// Dumps are backend independent: images are stored tightly packed with their logical channel count
// (the OpenCL backend on PoCL stores U8/F32 images as CL_RGBA, see cuda/NOTES.md).

#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <vector>
#include <filesystem>

#include "compute.h"
#include "cl_kernels.h"

namespace fs = std::filesystem;

enum DumpType : uint32_t { DUMP_U8 = 0, DUMP_RGBA = 1, DUMP_F32 = 2, DUMP_NV12 = 3 };

struct Dump {
	DumpType type;
	int width;
	int height;
	std::vector<uint8_t> data;
};

static const char MAGIC[4] = {'V', 'P', 'D', '1'};

static void writeDump(const fs::path& path, const Dump& dump) {
	std::ofstream out(path, std::ios::binary);
	uint32_t header[3] = {dump.type, (uint32_t)dump.width, (uint32_t)dump.height};
	out.write(MAGIC, 4);
	out.write((const char*)header, sizeof(header));
	out.write((const char*)dump.data.data(), (std::streamsize)dump.data.size());
}

static Dump readDump(const fs::path& path) {
	std::ifstream in(path, std::ios::binary);
	char magic[4];
	uint32_t header[3];
	in.read(magic, 4);
	in.read((char*)header, sizeof(header));
	if(!in || memcmp(magic, MAGIC, 4) != 0) {
		FATAL("Invalid dump " << path);
	}
	Dump dump{(DumpType)header[0], (int)header[1], (int)header[2], {}};
	dump.data.assign(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
	return dump;
}

/** Number of map elements per pixel of an image as stored by the active backend. */
static int elementsPerPixel(const PixelFormat* format) {
#ifdef VP_BACKEND_CUDA
	return format == &PixelFormat::RGBA8 ? 4 : 1;
#else
	return format->clFormat.image_channel_order == CL_RGBA ? 4 : 1;
#endif
}

static int logicalChannels(const PixelFormat* format) {
	return format == &PixelFormat::RGBA8 ? 4 : 1;
}

template<typename T>
static Dump dumpImage(const CLImage& image, DumpType type) {
	const int channels = logicalChannels(image.format);
	const int elements = elementsPerPixel(image.format);
	Dump dump{type, image.width, image.height, std::vector<uint8_t>((size_t)image.width * image.height * channels * sizeof(T))};
	CLImageMap<T> map = image.read<T>();
	T* out = (T*)dump.data.data();
	for(int y = 0; y < image.height; y++)
		for(int x = 0; x < image.width; x++)
			for(int c = 0; c < channels; c++)
				out[(x + y*image.width)*channels + c] = (*map)[y*map.rowPitch + x*elements + c];
	return dump;
}

/** Fill an image; single channel values are broadcast to all stored channels (like scalar write_image*). */
template<typename T>
static void fillImage(CLImage& image, const std::function<T(int x, int y, int c)>& value) {
	const int channels = logicalChannels(image.format);
	const int elements = elementsPerPixel(image.format);
	CLImageMap<T> map = image.write<T>();
	for(int y = 0; y < image.height; y++)
		for(int x = 0; x < image.width; x++)
			for(int e = 0; e < elements; e++)
				(*map)[y*map.rowPitch + x*elements + e] = value(x, y, channels == 1 ? 0 : e);
}

static Dump dumpNV12(const RawImage& nv12) {
	// Y plane + interleaved UV plane (only rows written by the kernels)
	const size_t size = (size_t)nv12.width*nv12.height + (size_t)nv12.width*((nv12.height + 1)/2);
	Dump dump{DUMP_NV12, nv12.width, nv12.height, std::vector<uint8_t>(size)};
	CLMap<uint8_t> map = nv12.read<uint8_t>();
	memcpy(dump.data.data(), *map, size);
	return dump;
}

struct Case {
	const char* variant;
	const PixelFormat* format;
	int width; // RawImage width (quad width)
	int height;
};

static const Case CASES[] = {
		{"BGR", &PixelFormat::BGR8, 1280, 720},
		{"RGGB", &PixelFormat::RGGB8, 640, 360},
		{"GRBG", &PixelFormat::GRBG8, 640, 360},
		{"BGR", &PixelFormat::BGR8, 34, 18},
		{"RGGB", &PixelFormat::RGGB8, 34, 18},
		{"GRBG", &PixelFormat::GRBG8, 34, 18},
};

static int dumpAll(const fs::path& dir) {
	fs::create_directories(dir);
	OpenCL openCl;
	std::mt19937 rng(42);
	auto byte = [&]() { return (uint8_t)(rng() & 0xff); };
	int files = 0;
	auto save = [&](const std::string& name, const Dump& dump) { writeDump(dir / name, dump); files++; };

	cl::Kernel rgba2nv12 = openCl.compile(kernel_rgba2nv12_cl);
	cl::Kernel f2nv12 = openCl.compile(kernel_f2nv12_cl);
	cl::Kernel gradientDot = openCl.compile(kernel_gradientDot_cl);

	for(const Case& c : CASES) {
		const std::string prefix = std::string(c.variant) + "_" + std::to_string(c.width) + "x" + std::to_string(c.height) + "_";
		cl::Kernel raw2quad = openCl.compile(kernel_raw2quad_cl, c.format->kernelOptions);
		cl::Kernel quad2rgba = openCl.compile(kernel_quad2rgba_cl, c.format->kernelOptions);
		cl::Kernel quad2nv12 = openCl.compile(kernel_quad2nv12_cl, c.format->kernelOptions);

		RawImage raw(c.format, c.width, c.height);
		{
			CLMap<uint8_t> map = raw.write<uint8_t>();
			for(int i = 0; i < raw.size; i++)
				map[i] = byte();
		}

		std::shared_ptr<CLImage> channels[4];
		for(int i = 0; i < 4; i++) {
			channels[i] = openCl.acquire(&PixelFormat::U8, c.width, c.height, "quad");
			fillImage<uint8_t>(*channels[i], [](int, int, int) { return (uint8_t)0; });
		}
		openCl.await(raw2quad, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), raw.buffer, channels[0]->image, channels[1]->image, channels[2]->image, channels[3]->image);
		for(int i = 0; i < 4; i++)
			save(prefix + "raw2quad_c" + std::to_string(i), dumpImage<uint8_t>(*channels[i], DUMP_U8));

		std::shared_ptr<CLImage> rgba = openCl.acquire(&PixelFormat::RGBA8, c.width, c.height, "rgba");
		openCl.await(quad2rgba, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), channels[0]->image, channels[1]->image, channels[2]->image, channels[3]->image, rgba->image);
		Dump rgbaDump = dumpImage<uint8_t>(*rgba, DUMP_RGBA);
		save(prefix + "quad2rgba", rgbaDump);

		std::shared_ptr<RawImage> nv12 = openCl.acquireNV12(c.width, c.height);
		openCl.await(quad2nv12, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), channels[0]->image, channels[1]->image, channels[2]->image, channels[3]->image, nv12->buffer);
		save(prefix + "quad2nv12", dumpNV12(*nv12));
		save(prefix + "quad2nv12.src", rgbaDump);

		// rgba2nv12 and gradientDot on a random RGBA image (random alpha too)
		std::shared_ptr<CLImage> randomRgba = openCl.acquire(&PixelFormat::RGBA8, c.width, c.height, "random");
		fillImage<uint8_t>(*randomRgba, [&](int, int, int) { return byte(); });
		Dump randomRgbaDump = dumpImage<uint8_t>(*randomRgba, DUMP_RGBA);
		std::shared_ptr<RawImage> nv12b = openCl.acquireNV12(c.width, c.height);
		openCl.await(rgba2nv12, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), randomRgba->image, nv12b->buffer);
		save(prefix + "rgba2nv12", dumpNV12(*nv12b));
		save(prefix + "rgba2nv12.src", randomRgbaDump);

		for(int offset : {1, 2, 5}) {
			std::shared_ptr<CLImage> grad = openCl.acquire(&PixelFormat::F32, c.width, c.height, "grad");
			openCl.await(gradientDot, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), randomRgba->image, grad->image, offset);
			save(prefix + "gradientDot_o" + std::to_string(offset), dumpImage<float>(*grad, DUMP_F32));
		}

		// f2nv12 on random floats incl. fractions, out of range values and specials
		std::shared_ptr<CLImage> floats = openCl.acquire(&PixelFormat::F32, c.width, c.height, "float");
		std::uniform_int_distribution<int> fraction(-40000, 40000);
		std::vector<float> values((size_t)c.width*c.height);
		for(float& v : values)
			v = (float)fraction(rng) / 100.0f;
		values[0] = NAN; values[1] = INFINITY; values[2] = -INFINITY; values[3] = 127.99f; values[4] = -127.5f; values[5] = 128.0f;
		fillImage<float>(*floats, [&](int x, int y, int) { return values[x + y*c.width]; });
		std::shared_ptr<RawImage> nv12c = openCl.acquireNV12(c.width, c.height);
		openCl.await(f2nv12, cl::EnqueueArgs(cl::NDRange(c.width, c.height)), floats->image, nv12c->buffer);
		save(prefix + "f2nv12", dumpNV12(*nv12c));
	}

	std::cout << "Dumped " << files << " files to " << dir << std::endl;
	return 0;
}

static int compareAll(const fs::path& refDir, const fs::path& testDir) {
	int failures = 0;
	std::vector<fs::path> files;
	for(const auto& entry : fs::directory_iterator(refDir))
		files.push_back(entry.path());
	std::sort(files.begin(), files.end());

	for(const fs::path& refPath : files) {
		const std::string name = refPath.filename().string();
		if(name.size() > 4 && name.substr(name.size() - 4) == ".src")
			continue;
		if(!fs::exists(testDir / name)) {
			std::cout << "MISSING  " << name << std::endl;
			failures++;
			continue;
		}

		Dump ref = readDump(refPath);
		Dump test = readDump(testDir / name);
		if(ref.type != test.type || ref.width != test.width || ref.height != test.height || ref.data.size() != test.data.size()) {
			std::cout << "SHAPE    " << name << std::endl;
			failures++;
			continue;
		}

		std::stringstream result;
		bool ok;
		if(ref.type == DUMP_F32) {
			const float* r = (const float*)ref.data.data();
			const float* t = (const float*)test.data.data();
			size_t n = ref.data.size() / 4, bitDiff = 0;
			double maxDiff = 0.0;
			for(size_t i = 0; i < n; i++) {
				if(memcmp(&r[i], &t[i], 4) != 0) {
					bitDiff++;
					maxDiff = std::max(maxDiff, (double)std::fabs(r[i] - t[i]));
				}
			}
			ok = bitDiff == 0;
			result << n << " floats, " << bitDiff << " bitwise differences, max |diff| " << maxDiff;
		} else if(ref.type == DUMP_NV12) {
			const size_t ySize = (size_t)ref.width * ref.height;
			size_t yDiff = 0, uvDiff = 0, uvRaceFail = 0, uvPairs = 0;
			for(size_t i = 0; i < ySize; i++)
				yDiff += ref.data[i] != test.data[i];

			// UV: the OpenCL kernels let 4 work items race for each UV pair. Accept a reference value if it
			// equals the value derived from one of the 4 candidate pixels, and record which one PoCL picked.
			const fs::path srcPath = refDir / (name + ".src");
			const bool hasSrc = fs::exists(srcPath);
			Dump src = hasSrc ? readDump(srcPath) : Dump{};
			std::map<int, size_t> winner;
			auto uv = [](const uint8_t* p) {
				int r = p[0], g = p[1], b = p[2];
				auto sat = [](int v) { return (uint8_t)std::min(255, std::max(0, v)); };
				return std::make_pair(sat((-38*r + -74*g + 112*b) / 256 + 128), sat((112*r + -94*g + -18*b) / 256 + 128));
			};
			for(int by = 0; by < (ref.height + 1)/2; by++) {
				for(int bx = 0; bx < (ref.width + 1)/2; bx++) {
					const size_t i = ySize + bx*2 + (size_t)by*ref.width;
					uvPairs++;
					bool same = ref.data[i] == test.data[i] && ref.data[i+1] == test.data[i+1];
					uvDiff += !same;
					if(!hasSrc)
						continue;
					int testMatch = -1, refMatches = 0, refMatch = -1;
					for(int k = 0; k < 4; k++) {
						int x = std::min(2*bx + (k & 1), ref.width - 1), y = std::min(2*by + (k >> 1), ref.height - 1);
						auto [u, v] = uv(&src.data[(x + (size_t)y*ref.width)*4]);
						if(u == test.data[i] && v == test.data[i+1])
							testMatch = k;
						if(u == ref.data[i] && v == ref.data[i+1]) {
							refMatches++;
							refMatch = k;
						}
					}
					if(refMatches == 1) // unambiguous
						winner[refMatch]++;
					uvRaceFail += testMatch < 0 || refMatches == 0;
				}
			}
			ok = yDiff == 0 && (hasSrc ? uvRaceFail == 0 : uvDiff == 0);
			result << "Y " << yDiff << "/" << ySize << " diff, UV " << uvDiff << "/" << uvPairs << " exact diff";
			if(hasSrc) {
				result << ", " << uvRaceFail << " not explained by race; reference UV unambiguously from pixel (dx,dy):";
				for(auto [k, count] : winner)
					result << " (" << (k & 1) << "," << (k >> 1) << ")=" << count;
			}
		} else {
			size_t diff = 0;
			int maxDiff = 0;
			for(size_t i = 0; i < ref.data.size(); i++) {
				if(ref.data[i] != test.data[i]) {
					diff++;
					maxDiff = std::max(maxDiff, std::abs((int)ref.data[i] - (int)test.data[i]));
				}
			}
			ok = diff == 0;
			result << ref.data.size() << " bytes, " << diff << " differ, max |diff| " << maxDiff;
		}

		std::cout << (ok ? "OK       " : "MISMATCH ") << name << ": " << result.str() << std::endl;
		failures += !ok;
	}

	std::cout << (failures ? "FAILED: " : "PASSED: ") << failures << " mismatching outputs" << std::endl;
	return failures ? 1 : 0;
}

static int bench() {
	OpenCL openCl;
	const int iterations = 50;
	auto time = [&](const std::string& name, const std::function<void()>& run) {
		run(); // warm up
		auto start = std::chrono::steady_clock::now();
		for(int i = 0; i < iterations; i++)
			run();
		double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count() / iterations;
		std::cout << "  " << name << ": " << ms << " ms" << std::endl;
	};

	for(int caseId = 0; caseId < 2; caseId++) {
		const Case& c = CASES[caseId];
		std::cout << c.variant << " " << c.width << "x" << c.height << std::endl;
		cl::Kernel raw2quad = openCl.compile(kernel_raw2quad_cl, c.format->kernelOptions);
		cl::Kernel quad2rgba = openCl.compile(kernel_quad2rgba_cl, c.format->kernelOptions);
		cl::Kernel quad2nv12 = openCl.compile(kernel_quad2nv12_cl, c.format->kernelOptions);
		cl::Kernel rgba2nv12 = openCl.compile(kernel_rgba2nv12_cl);
		cl::Kernel f2nv12 = openCl.compile(kernel_f2nv12_cl);
		cl::Kernel gradientDot = openCl.compile(kernel_gradientDot_cl);

		RawImage raw(c.format, c.width, c.height);
		std::shared_ptr<CLImage> ch[4];
		for(auto& i : ch)
			i = openCl.acquire(&PixelFormat::U8, c.width, c.height, "quad");
		std::shared_ptr<CLImage> rgba = openCl.acquire(&PixelFormat::RGBA8, c.width, c.height, "rgba");
		std::shared_ptr<CLImage> f = openCl.acquire(&PixelFormat::F32, c.width, c.height, "f");
		std::shared_ptr<RawImage> nv12 = openCl.acquireNV12(c.width, c.height);
		cl::EnqueueArgs range(cl::NDRange(c.width, c.height));

		time("raw2quad", [&] { openCl.await(raw2quad, range, raw.buffer, ch[0]->image, ch[1]->image, ch[2]->image, ch[3]->image); });
		time("quad2rgba", [&] { openCl.await(quad2rgba, range, ch[0]->image, ch[1]->image, ch[2]->image, ch[3]->image, rgba->image); });
		time("quad2nv12", [&] { openCl.await(quad2nv12, range, ch[0]->image, ch[1]->image, ch[2]->image, ch[3]->image, nv12->buffer); });
		time("rgba2nv12", [&] { openCl.await(rgba2nv12, range, rgba->image, nv12->buffer); });
		time("gradientDot", [&] { openCl.await(gradientDot, range, rgba->image, f->image, 3); });
		time("f2nv12", [&] { openCl.await(f2nv12, range, f->image, nv12->buffer); });
		openCl.clearEvents();
	}
	return 0;
}

int main(int argc, char* argv[]) {
	const std::string mode = argc > 1 ? argv[1] : "";
	if(mode == "dump" && argc == 3)
		return dumpAll(argv[2]);
	if(mode == "compare" && argc == 4)
		return compareAll(argv[2], argv[3]);
	if(mode == "bench")
		return bench();

	std::cerr << "Usage: " << argv[0] << " dump <dir> | compare <refDir> <testDir> | bench" << std::endl;
	return 2;
}

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

enum DumpType : uint32_t { DUMP_U8 = 0, DUMP_RGBA = 1, DUMP_F32 = 2, DUMP_NV12 = 3, DUMP_MATCHES = 4 };

/** Same layout as CLCameraModel (src/Perspective.h) */
struct __attribute__ ((packed)) HarnessCameraModel {
	int shape[2];
	float f;
	float p[2];
	float d;
	float r[9];
	float c[3];
};
static_assert(sizeof(HarnessCameraModel) == 72);

/** Same layout as CLMatch (src/main.cpp) */
struct __attribute__ ((packed)) HarnessMatch {
	float x, y;
	uint8_t color[3];
	uint8_t center[3];
	float circ;
	float score;
};
static_assert(sizeof(HarnessMatch) == 22);

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

	// Phase 4: blob detection pipeline on a synthetic scene (noisy mat with coloured discs), from the same
	// 1280x720 RGB scene as BGR (1280x720) and as RGGB/GRBG mosaic (640x360 quads)
	std::vector<uint8_t> scene(1280*720*3);
	{
		std::mt19937 sceneRng(7);
		for(size_t i = 0; i < scene.size(); i += 3) {
			scene[i] = 100 + sceneRng() % 21; scene[i+1] = 120 + sceneRng() % 21; scene[i+2] = 95 + sceneRng() % 21; // BGR
		}
		const uint8_t colors[][3] = {{30, 120, 250}, {20, 220, 240}, {230, 120, 20}, {160, 40, 240}, {60, 230, 60}, {250, 250, 250}}; // BGR
		for(int disc = 0; disc < 60; disc++) {
			const int cx = 20 + sceneRng() % 1240, cy = 20 + sceneRng() % 680, radius = 7 + sceneRng() % 5;
			const uint8_t* color = colors[sceneRng() % 6];
			for(int y = cy - radius; y <= cy + radius; y++)
				for(int x = cx - radius; x <= cx + radius; x++)
					if((x-cx)*(x-cx) + (y-cy)*(y-cy) <= radius*radius)
						for(int ch = 0; ch < 3; ch++)
							scene[(x + y*1280)*3 + ch] = std::min(255, color[ch] + (int)(sceneRng() % 9));
		}
	}

	cl::Kernel satHorizontal = openCl.compile(kernel_satHorizontal_cl);
	cl::Kernel satVertical = openCl.compile(kernel_satVertical_cl);
	cl::Kernel satBlobCenter = openCl.compile(kernel_satBlobCenter_cl);
	cl::Kernel blobList = openCl.compile(kernel_blobList_cl);
	cl::Kernel blobScore = openCl.compile(kernel_blobScore_cl);

	struct View { const char* name; float extentX, extentY, scale, distortion; };
	const View views[] = {{"inner", 1500.f, 850.f, 5.f, -0.05f}, {"wide", 1800.f, 1000.f, 6.f, 0.08f}};
	for(int caseId = 0; caseId < 3; caseId++) {
		const Case& c = CASES[caseId];
		RawImage raw(c.format, c.width, c.height);
		{
			CLMap<uint8_t> map = raw.write<uint8_t>();
			for(int y = 0; y < 720; y++) {
				for(int x = 0; x < 1280; x++) {
					const uint8_t* bgr = &scene[(x + y*1280)*3];
					if(c.format == &PixelFormat::BGR8) {
						memcpy(&map[(x + y*1280)*3], bgr, 3);
					} else {
						// RGGB: R G / G B, GRBG: G R / B G
						const bool rggb = c.format == &PixelFormat::RGGB8;
						const int phase = (x & 1) + 2*(y & 1);
						const int channel = rggb ? (phase == 0 ? 2 : phase == 3 ? 0 : 1) : (phase == 1 ? 2 : phase == 2 ? 0 : 1);
						map[x + y*1280] = bgr[channel];
					}
				}
			}
		}

		std::shared_ptr<CLImage> channels[4];
		for(auto& channel : channels) {
			channel = openCl.acquire(&PixelFormat::U8, c.width, c.height, "quad");
			fillImage<uint8_t>(*channel, [](int, int, int) { return (uint8_t)0; });
		}
		openCl.await(openCl.compile(kernel_raw2quad_cl, c.format->kernelOptions), cl::EnqueueArgs(cl::NDRange(c.width, c.height)), raw.buffer, channels[0]->image, channels[1]->image, channels[2]->image, channels[3]->image);
		cl::Kernel resampling = openCl.compile(kernel_resampling_cl, c.format->kernelOptions);

		for(const View& view : views) {
			const std::string prefix = std::string("pipeline_") + c.variant + "_" + view.name + "_";
			// Camera 3 m above the field looking down, slightly rotated (R = Rz*Ry*Rx * diag(1,-1,-1))
			const float ax = 0.03f, ay = -0.02f, az = 0.01f;
			const double rx[9] = {1, 0, 0, 0, cos(ax), -sin(ax), 0, sin(ax), cos(ax)};
			const double ry[9] = {cos(ay), 0, sin(ay), 0, 1, 0, -sin(ay), 0, cos(ay)};
			const double rz[9] = {cos(az), -sin(az), 0, sin(az), cos(az), 0, 0, 0, 1};
			auto mul = [](const double* a, const double* b, double* out) {
				for(int i = 0; i < 3; i++) for(int j = 0; j < 3; j++) { out[i*3+j] = 0; for(int k = 0; k < 3; k++) out[i*3+j] += a[i*3+k]*b[k*3+j]; }
			};
			const double flip[9] = {1, 0, 0, 0, -1, 0, 0, 0, -1};
			double t1[9], t2[9], rot[9];
			mul(ry, rx, t1); mul(rz, t1, t2); mul(t2, flip, rot);
			HarnessCameraModel model{{c.width, c.height}, 0.9f*(float)c.width, {c.width/2.0f, c.height/2.0f}, view.distortion, {}, {37.f, -21.f, 3000.f}};
			for(int i = 0; i < 9; i++)
				model.r[i] = (float)rot[i];

			const int fw = (int)(2*view.extentX/view.scale), fh = (int)(2*view.extentY/view.scale);
			const int maxBlobRadius = 25, minBlobRadius = 20;
			std::shared_ptr<CLImage> flat = openCl.acquire(&PixelFormat::RGBA8, fw, fh, "flat");
			std::shared_ptr<CLImage> gradDot = openCl.acquire(&PixelFormat::F32, fw, fh, "gradDot");
			std::shared_ptr<CLImage> gradDotHor = openCl.acquire(&PixelFormat::F32, fw, fh, "gradDotHor");
			std::shared_ptr<CLImage> gradDotSat = openCl.acquire(&PixelFormat::F32, fw, fh, "gradDotSat");
			std::shared_ptr<CLImage> blobCenter = openCl.acquire(&PixelFormat::F32, fw, fh, "blobCenter");
			std::shared_ptr<CLImage> score = openCl.acquire(&PixelFormat::F32, fw, fh, "score");
			cl::NDRange range(fw, fh);

			// Same call sequence as Resources::rgba2blobCenter / main.cpp
			openCl.await(resampling, cl::EnqueueArgs(range), channels[0]->image, channels[1]->image, channels[2]->image, channels[3]->image, flat->image, model, 150.0f, view.scale, -view.extentX, -view.extentY);
			openCl.await(gradientDot, cl::EnqueueArgs(range), flat->image, gradDot->image, (int)ceilf(maxBlobRadius / view.scale) / 3);
			openCl.await(satHorizontal, cl::EnqueueArgs(cl::NDRange(fh)), gradDot->image, gradDotHor->image);
			openCl.await(satVertical, cl::EnqueueArgs(cl::NDRange(fw)), gradDotHor->image, gradDotSat->image);
			openCl.await(satBlobCenter, cl::EnqueueArgs(range), gradDotSat->image, blobCenter->image, (int)ceilf(minBlobRadius / view.scale));
			const int radius = (int)floorf(minBlobRadius / view.scale);
			openCl.await(blobScore, cl::EnqueueArgs(range), flat->image, blobCenter->image, score->image, 15.0f, radius);

			save(prefix + "resampling", dumpImage<uint8_t>(*flat, DUMP_RGBA));
			save(prefix + "gradientDot", dumpImage<float>(*gradDot, DUMP_F32));
			save(prefix + "satHorizontal", dumpImage<float>(*gradDotHor, DUMP_F32));
			save(prefix + "satVertical", dumpImage<float>(*gradDotSat, DUMP_F32));
			save(prefix + "satBlobCenter", dumpImage<float>(*blobCenter, DUMP_F32));
			save(prefix + "blobScore", dumpImage<float>(*score, DUMP_F32));

			// blobList: realistic threshold, low threshold (many matches) and overflow of maxMatches
			struct BlobRun { const char* name; float threshold; int maxMatches; };
			for(const BlobRun& run : {BlobRun{"blobList", 15.0f, 4096}, BlobRun{"blobList_low", 0.5f, 65536}, BlobRun{"blobList_overflow", 0.5f, 16}}) {
				CLArray matchArray((int)sizeof(HarnessMatch) * run.maxMatches);
				CLArray counter(sizeof(cl_int)*3);
				{
					CLMap<int> counterMap = counter.write<int>();
					counterMap[0] = counterMap[1] = counterMap[2] = 0;
				}
				openCl.await(blobList, cl::EnqueueArgs(range), flat->image, blobCenter->image, matchArray.buffer, counter.buffer, run.threshold, 0.0f, radius, run.maxMatches);

				CLMap<int> counterMap = counter.read<int>();
				CLMap<HarnessMatch> matchMap = matchArray.read<HarnessMatch>();
				const int stored = std::min(counterMap[0], run.maxMatches);
				Dump dump{DUMP_MATCHES, stored, run.maxMatches, std::vector<uint8_t>(12 + stored*sizeof(HarnessMatch))};
				memcpy(dump.data.data(), &counterMap[0], 12);
				memcpy(dump.data.data() + 12, &matchMap[0], stored*sizeof(HarnessMatch));
				save(prefix + run.name, dump);
			}
		}
	}

	std::cout << "Dumped " << files << " files to " << dir << std::endl;
	return 0;
}

/** Distance in units in the last place (monotonic integer mapping of floats), NaN/inf compared by bits */
static int64_t ulpDistance(float a, float b) {
	auto ordered = [](float f) {
		int32_t i;
		memcpy(&i, &f, 4);
		return i < 0 ? (int64_t)INT32_MIN - i : (int64_t)i;
	};
	return std::llabs(ordered(a) - ordered(b));
}

static std::vector<std::vector<uint8_t>> matchRecords(const Dump& dump) {
	std::vector<std::vector<uint8_t>> records;
	for(int i = 0; i < dump.width; i++) {
		const uint8_t* record = dump.data.data() + 12 + i*sizeof(HarnessMatch);
		records.emplace_back(record, record + sizeof(HarnessMatch));
		// NaN payloads differ between CPUs/GPUs (0/0 at flat peaks: aarch64 0x7fc00000, CUDA 0x7fffffff), canonicalize
		for(size_t offset : {offsetof(HarnessMatch, x), offsetof(HarnessMatch, y), offsetof(HarnessMatch, circ), offsetof(HarnessMatch, score)}) {
			float value;
			memcpy(&value, records.back().data() + offset, 4);
			if(std::isnan(value)) {
				const uint32_t canonical = 0x7fc00000;
				memcpy(records.back().data() + offset, &canonical, 4);
			}
		}
	}
	std::sort(records.begin(), records.end());
	return records;
}

/**
 * blobList outputs: counters must be equal, matches are compared as multisets (atomic order is
 * nondeterministic). If more matches than maxMatches were found, which ones are stored is
 * nondeterministic too: then every stored match must be part of the reference's "_low" run
 * (same threshold, no overflow).
 */
static bool compareMatches(const std::string& name, const Dump& ref, const Dump& test, const fs::path& refDir) {
	const int* refCounter = (const int*)ref.data.data();
	const int* testCounter = (const int*)test.data.data();
	std::stringstream result;
	bool ok = memcmp(refCounter, testCounter, 12) == 0;
	result << "counters (matches, low score, no peak) ref " << refCounter[0] << "/" << refCounter[1] << "/" << refCounter[2]
	       << " test " << testCounter[0] << "/" << testCounter[1] << "/" << testCounter[2];

	std::vector<std::vector<uint8_t>> refRecords = matchRecords(ref), testRecords = matchRecords(test);
	if(refCounter[0] <= ref.height) {
		std::vector<std::vector<uint8_t>> missing, extra;
		std::set_difference(refRecords.begin(), refRecords.end(), testRecords.begin(), testRecords.end(), std::back_inserter(missing));
		std::set_difference(testRecords.begin(), testRecords.end(), refRecords.begin(), refRecords.end(), std::back_inserter(extra));
		ok &= missing.empty() && extra.empty();
		result << ", " << refRecords.size() << " matches as multiset: " << missing.size() << " only in ref, " << extra.size() << " only in test";
		auto print = [&](const char* side, const std::vector<std::vector<uint8_t>>& records) {
			for(size_t i = 0; i < std::min<size_t>(records.size(), 5); i++) {
				HarnessMatch m;
				memcpy(&m, records[i].data(), sizeof(m));
				result << "\n           " << side << " x " << std::hexfloat << m.x << " y " << m.y << std::defaultfloat << " (" << m.x << ", " << m.y << ") color " << (int)m.color[0] << "," << (int)m.color[1] << "," << (int)m.color[2]
				       << " center " << (int)m.center[0] << "," << (int)m.center[1] << "," << (int)m.center[2] << " circ " << m.circ << " score " << std::hexfloat << m.score << std::defaultfloat;
			}
		};
		print("ref ", missing);
		print("test", extra);
	} else {
		std::string fullName = name;
		fullName.replace(fullName.find("_overflow"), 9, "_low");
		std::vector<std::vector<uint8_t>> full = matchRecords(readDump(refDir / fullName));
		size_t notInFull = 0;
		for(const auto& record : testRecords)
			notInFull += !std::binary_search(full.begin(), full.end(), record);
		ok &= notInFull == 0 && testRecords.size() == refRecords.size();
		result << ", overflow: " << testRecords.size() << " stored, " << notInFull << " not in the reference's full match set";
	}

	std::cout << (ok ? "OK       " : "MISMATCH ") << name << ": " << result.str() << std::endl;
	return ok;
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
		if(ref.type == DUMP_MATCHES && test.type == DUMP_MATCHES) {
			const bool ok = compareMatches(name, ref, test, refDir);
			failures += !ok;
			continue;
		}

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
			int64_t maxUlp = 0;
			for(size_t i = 0; i < n; i++) {
				if(memcmp(&r[i], &t[i], 4) != 0) {
					bitDiff++;
					maxDiff = std::max(maxDiff, (double)std::fabs(r[i] - t[i]));
					maxUlp = std::max(maxUlp, ulpDistance(r[i], t[i]));
				}
			}
			ok = bitDiff == 0;
			result << n << " floats, " << bitDiff << " bitwise differences, max |diff| " << maxDiff << ", max ULP " << maxUlp;
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

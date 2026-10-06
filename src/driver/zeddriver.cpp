// SPDX-License-Identifier: Apache-2.0
#ifdef ZED_SDK
#include "zeddriver.h"

#include <cuda.h>
#include <cuda_runtime_api.h>

static sl::RESOLUTION resolutionFromHeight(int height) {
	// Per-eye heights are unique per mode; 0 lets the SDK pick its default
	switch(height) {
		case 0: return sl::RESOLUTION::AUTO;
		case 376: return sl::RESOLUTION::VGA;
		case 600: return sl::RESOLUTION::SVGA;
		case 720: return sl::RESOLUTION::HD720;
		case 1080: return sl::RESOLUTION::HD1080;
		case 1200: return sl::RESOLUTION::HD1200;
		case 1242: return sl::RESOLUTION::HD2K;
		default: FATAL("[ZED] Unsupported camera height " << height << " (use 376, 600, 720, 1080, 1200, 1242 or 0 for auto)");
	}
}

static bool endsWith(const std::string& s, const std::string& suffix) {
	return s.size() >= suffix.size() && s.compare(s.size() - suffix.size(), suffix.size(), suffix) == 0;
}

ZEDDriver::ZEDDriver(const CameraConfig& config): view(config.rectify ? sl::VIEW::LEFT : sl::VIEW::LEFT_UNRECTIFIED), outputSize(std::max(config.outputWidth, 0), std::max(config.outputHeight, 0)), name("zed"), svo(endsWith(config.path, ".svo") || endsWith(config.path, ".svo2")) {
	// Run the SDK in the compute backend's CUDA context so its GPU images can be used by our kernels directly
	if(cudaFree(nullptr) != cudaSuccess)
		FATAL("[ZED] Could not initialize CUDA");
	CUcontext context = nullptr;
	cuCtxGetCurrent(&context);
	int device = 0;
	cudaGetDevice(&device);

	sl::InitParameters init;
	init.sdk_cuda_ctx = context;
	init.sdk_gpu_id = device; // required together with sdk_cuda_ctx
	init.depth_mode = sl::DEPTH_MODE::NONE; // Only images are needed, saves most of the SDK's GPU time
	init.camera_resolution = resolutionFromHeight(config.height);
	init.camera_fps = (int)config.fps;
	init.sdk_verbose = 0;
	if(svo) {
		init.input.setFromSVOFile(config.path.c_str());
		init.svo_real_time_mode = true;
		name = config.path;
		std::replace(name.begin(), name.end(), '/', '_');
	} else if(config.serial != 0) {
		init.input.setFromSerialNumber(config.serial);
	} else {
		init.input.setFromCameraID(config.hardwareId);
	}

	while(true) {
		sl::ERROR_CODE error = camera.open(init);
		if(error == sl::ERROR_CODE::SUCCESS)
			break;
		const bool notConnected = error == sl::ERROR_CODE::CAMERA_NOT_DETECTED || error == sl::ERROR_CODE::CAMERA_DETECTION_ISSUE || error == sl::ERROR_CODE::CAMERA_NOT_INITIALIZED;
		if(svo || !notConnected)
			FATAL("[ZED] Could not open " << (svo ? config.path : "camera") << ": " << sl::toString(error));
		WARN("[ZED] Waiting for camera: " << sl::toString(error));
		sleep(1);
	}

	const sl::CameraInformation info = camera.getCameraInformation();
	const sl::Resolution native = info.camera_configuration.resolution;
	if(outputSize.width == 0 || outputSize.height == 0)
		outputSize = native;
	LOG("[ZED] Opened " << sl::toString(info.camera_model) << " serial " << info.serial_number << " " << native.width << "x" << native.height << "@" << info.camera_configuration.fps
		<< " -> " << outputSize.width << "x" << outputSize.height << (config.rectify ? " rectified" : " unrectified"));

	if(!svo)
		applySettings(config);

	gpuImage.alloc(outputSize, sl::MAT_TYPE::U8_C4, sl::MEM::GPU);
}

void ZEDDriver::applySettings(const CameraConfig& config) {
	auto set = [&](sl::VIDEO_SETTINGS setting, int value) {
		sl::ERROR_CODE error = camera.setCameraSettings(setting, value);
		if(error != sl::ERROR_CODE::SUCCESS)
			WARN("[ZED] Could not set " << sl::toString(setting) << " to " << value << ": " << sl::toString(error));
	};

	if(config.autoExposure() && config.autoGain()) {
		set(sl::VIDEO_SETTINGS::AEC_AGC, 1);
	} else {
		// exposure is in ms like the other drivers; the ZED 2/2i/Mini take a percentage of the frame time
		const double frameMs = 1000.0 / camera.getCameraInformation().camera_configuration.fps;
		if(!config.autoExposure())
			set(sl::VIDEO_SETTINGS::EXPOSURE, std::clamp((int)std::lround(config.exposure / frameMs * 100.0), 0, 100));
		if(!config.autoGain())
			set(sl::VIDEO_SETTINGS::GAIN, std::clamp((int)std::lround(config.gain), 0, 100));
	}

	if(!config.autoGamma())
		set(sl::VIDEO_SETTINGS::GAMMA, std::clamp((int)std::lround(config.gamma), 1, 9));

	if(config.whiteBalanceType != WhiteBalanceType_Manual) {
		set(sl::VIDEO_SETTINGS::WHITEBALANCE_AUTO, 1);
	} else if(config.whiteBalanceTemperature > 0) {
		set(sl::VIDEO_SETTINGS::WHITEBALANCE_AUTO, 0);
		set(sl::VIDEO_SETTINGS::WHITEBALANCE_TEMPERATURE, (int)std::lround(config.whiteBalanceTemperature / 100.0) * 100);
	} else {
		WARN("[ZED] white_balance blue/red ratios are not supported by the ZED SDK, use white_balance: {temperature: 2800..6500}. Keeping auto white balance.");
		set(sl::VIDEO_SETTINGS::WHITEBALANCE_AUTO, 1);
	}
}

ZEDDriver::~ZEDDriver() {
	camera.close();
}

std::shared_ptr<RawImage> ZEDDriver::readImage() {
	sl::ERROR_CODE error = camera.grab();
	if(error == sl::ERROR_CODE::END_OF_SVOFILE_REACHED)
		return nullptr;
	if(error != sl::ERROR_CODE::SUCCESS) {
		WARN("[ZED] grab failed: " << sl::toString(error));
		return nullptr;
	}

	if(image == nullptr || !image.unique())
		image = std::make_shared<RawImage>(&PixelFormat::BGR8, (int)outputSize.width, (int)outputSize.height, name);

	cudaStream_t stream = (cudaStream_t)vpcuda::computeStreamHandle();
	error = camera.retrieveImage(gpuImage, view, sl::MEM::GPU, outputSize, stream);
	if(error != sl::ERROR_CODE::SUCCESS) {
		WARN("[ZED] retrieveImage failed: " << sl::toString(error));
		return nullptr;
	}

	// Ordered on the compute stream before every kernel that reads the image; CPU maps synchronize the stream
	vpcuda::bgra2bgr(gpuImage.getPtr<sl::uchar1>(sl::MEM::GPU), gpuImage.getStepBytes(sl::MEM::GPU), image->buffer.device(), image->width, image->height);
	image->timestamp = (double)camera.getTimestamp(sl::TIME_REFERENCE::IMAGE).getNanoseconds() / 1e9;
	return image;
}

const PixelFormat ZEDDriver::format() {
	return PixelFormat::BGR8;
}

double ZEDDriver::expectedFrametime() {
	return 1.0 / camera.getCameraInformation().camera_configuration.fps;
}

#endif

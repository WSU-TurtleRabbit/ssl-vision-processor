// SPDX-License-Identifier: Apache-2.0
#pragma once
#ifdef ZED_SDK

#include "cameradriver.h"

// The ZED SDK headers are not valid C++20 (GCC >= 11 rejects them), so they are only included by zeddriver.cpp,
// which is compiled as C++17 (see CMakeLists.txt).
struct ZEDState;

/** StereoLabs ZED cameras through the ZED SDK. Delivers the left image as BGR8, converted and resized on the GPU. */
class ZEDDriver : public CameraDriver {
public:
	explicit ZEDDriver(const CameraConfig& config);
	~ZEDDriver() override;

	std::shared_ptr<RawImage> readImage() override;

	const PixelFormat format() override;

	double expectedFrametime() override;

private:
	void applySettings(const CameraConfig& config);

	std::unique_ptr<ZEDState> zed;
	int outputWidth;
	int outputHeight;
	std::string name;
	bool svo;
	std::shared_ptr<RawImage> image;
};

#endif

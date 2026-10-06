// SPDX-License-Identifier: Apache-2.0
#pragma once
#ifdef ZED_SDK

#include "cameradriver.h"
#include <sl/Camera.hpp>

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

	sl::Camera camera;
	sl::Mat gpuImage;
	sl::VIEW view;
	sl::Resolution outputSize;
	std::string name;
	bool svo;
	std::shared_ptr<RawImage> image;
};

#endif

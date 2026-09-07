/*
     Copyright 2024 Felix Weinmann

     Licensed under the Apache License, Version 2.0 (the "License");
     you may not use this file except in compliance with the License.
     You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

     Unless required by applicable law or agreed to in writing, software
     distributed under the License is distributed on an "AS IS" BASIS,
     WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
     See the License for the specific language governing permissions and
     limitations under the License.
 */
#include "opencvdriver.h"

#include <opencv2/imgproc.hpp>

OpenCVDriver::OpenCVDriver(const CameraConfig& config): capture(config.path, cv::CAP_ANY, {cv::CAP_PROP_HW_ACCELERATION, cv::VIDEO_ACCELERATION_ANY}), name(config.path), cropLeftHalf(config.cropLeftHalf), outputWidth(config.outputWidth), outputHeight(config.outputHeight) {
	std::replace(name.begin(), name.end(), '/', '_');

	if(config.fourcc.size() == 4) {
		capture.set(cv::CAP_PROP_FOURCC, cv::VideoWriter::fourcc(config.fourcc[0], config.fourcc[1], config.fourcc[2], config.fourcc[3]));
	}

	if(config.autoResolution()) {
		capture.set(cv::CAP_PROP_FRAME_WIDTH, INT_MAX);
		capture.set(cv::CAP_PROP_FRAME_HEIGHT, INT_MAX);
	} else {
		capture.set(cv::CAP_PROP_FRAME_WIDTH, config.width);
		capture.set(cv::CAP_PROP_FRAME_HEIGHT, config.height);
	}

	if(config.fps > 0.0) {
		capture.set(cv::CAP_PROP_FPS, config.fps);
	}

	if(config.autoExposure()) {
		capture.set(cv::CAP_PROP_AUTO_EXPOSURE, 1.0);
	} else {
		capture.set(cv::CAP_PROP_AUTO_EXPOSURE, 0.0);
		capture.set(cv::CAP_PROP_EXPOSURE, config.exposure * 1000.0);
	}

	if(!config.autoGain()) {
		capture.set(cv::CAP_PROP_GAIN, config.gain);
	}

	if(config.autoGamma()) {
		capture.set(cv::CAP_PROP_GAMMA, config.gamma);
	}

	if(config.whiteBalanceType != WhiteBalanceType_Manual) {
		capture.set(cv::CAP_PROP_AUTO_WB, 1.0);
	} else {
		capture.set(cv::CAP_PROP_AUTO_WB, 0.0);
		capture.set(cv::CAP_PROP_WHITE_BALANCE_BLUE_U, config.whiteBalanceBlue);
		capture.set(cv::CAP_PROP_WHITE_BALANCE_RED_V, config.whiteBalanceRed);
	}
}

std::shared_ptr<RawImage> OpenCVDriver::readImage() {
	const int captureWidth = (int)capture.get(cv::CAP_PROP_FRAME_WIDTH);
	const int captureHeight = (int)capture.get(cv::CAP_PROP_FRAME_HEIGHT);
	const int croppedWidth = cropLeftHalf ? captureWidth / 2 : captureWidth;
	const int finalWidth = outputWidth > 0 ? outputWidth : croppedWidth;
	const int finalHeight = outputHeight > 0 ? outputHeight : captureHeight;

	if(image == nullptr || !image.unique())
		image = std::make_shared<RawImage>(&PixelFormat::BGR8, finalWidth, finalHeight, name);

	if(cropLeftHalf || finalWidth != captureWidth || finalHeight != captureHeight) {
		cv::Mat full(cv::Size(captureWidth, captureHeight), CV_8UC3);
		if(!capture.read(full))
			return nullptr;

		CLMap<uint8_t> map = image->write<uint8_t>();
		cv::Mat output(cv::Size(image->width, image->height), CV_8UC3, (void*)*map);
		cv::Mat cropped = full(cv::Rect(0, 0, croppedWidth, captureHeight));
		if(cropped.cols == output.cols && cropped.rows == output.rows)
			cropped.copyTo(output);
		else
			cv::resize(cropped, output, output.size(), 0.0, 0.0, cv::INTER_AREA);
		return image;
	}

	CLMap<uint8_t> map = image->write<uint8_t>();
	cv::Mat mat(cv::Size(image->width, image->height), CV_8UC3, (void*)*map);
	if(!capture.read(mat))
		return nullptr;

	return image;
}

const PixelFormat OpenCVDriver::format() {
	return PixelFormat::BGR8;
}

double OpenCVDriver::expectedFrametime() {
	double fps = capture.get(cv::CAP_PROP_FPS);

	if(fps == 0.0) // Unavailable for cameras, estimate 30 FPS
		fps = 30.0;

	return 1 / fps;
}


double OpenCVDriver::getTime() {
	double pos = capture.get(cv::CAP_PROP_POS_FRAMES);

	if(pos == -1) // Not a video file, use real time
		return getRealTime();

	return pos / capture.get(cv::CAP_PROP_FPS);
}

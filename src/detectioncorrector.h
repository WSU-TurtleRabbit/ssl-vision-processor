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
#pragma once

#include <opencv2/core.hpp>
#include <vector>

#include "Perspective.h"
#include "proto/ssl_vision_detection.pb.h"


class DetectionCorrector {
public:
	DetectionCorrector(std::vector<Eigen::Vector2f> lineCorners, float cameraHeight);

	void update(const Perspective& perspective);
	void correct(SSL_DetectionFrame* detection, const Perspective& perspective) const;

	[[nodiscard]] bool enabled() const;
	[[nodiscard]] Eigen::Vector2f imageToField(const Eigen::Vector2f& image, float objectHeight) const;
	[[nodiscard]] Eigen::Vector2f fieldToImage(const Eigen::Vector2f& field, float objectHeight, const Perspective& perspective) const;

private:
	[[nodiscard]] Eigen::Vector2f project(const cv::Mat& transform, const Eigen::Vector2f& point) const;

	const std::vector<Eigen::Vector2f> lineCorners;
	const float configuredCameraHeight;
	cv::Mat imageToFieldTransform;
	cv::Mat fieldToImageTransform;
	Eigen::Vector2f cameraGround = Eigen::Vector2f::Zero();
	float cameraHeight = 0.0f;
	int geometryVersion = -1;
};

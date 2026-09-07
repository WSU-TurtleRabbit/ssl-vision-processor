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
#include "detectioncorrector.h"

#include <algorithm>
#include <cmath>
#include <opencv2/imgproc.hpp>
#include <utility>

#include "log.h"


DetectionCorrector::DetectionCorrector(std::vector<Eigen::Vector2f> lineCorners, const float cameraHeight):
		lineCorners(std::move(lineCorners)),
		configuredCameraHeight(cameraHeight) {}

void DetectionCorrector::update(const Perspective& perspective) {
	if(geometryVersion == perspective.geometryVersion)
		return;
	geometryVersion = perspective.geometryVersion;
	imageToFieldTransform.release();
	fieldToImageTransform.release();

	if(lineCorners.size() != 4 || !perspective.field.has_field_length() || !perspective.field.has_field_width())
		return;

	const float halfLength = perspective.field.field_length() / 2.0f;
	const float halfWidth = perspective.field.field_width() / 2.0f;
	const cv::Point2f imageCorners[] = {
		{lineCorners[0].x(), lineCorners[0].y()},
		{lineCorners[1].x(), lineCorners[1].y()},
		{lineCorners[2].x(), lineCorners[2].y()},
		{lineCorners[3].x(), lineCorners[3].y()}
	};
	const cv::Point2f fieldCorners[] = {
		{-halfLength, -halfWidth},
		{-halfLength, halfWidth},
		{halfLength, halfWidth},
		{halfLength, -halfWidth}
	};

	imageToFieldTransform = cv::getPerspectiveTransform(imageCorners, fieldCorners);
	fieldToImageTransform = imageToFieldTransform.inv();
	cameraHeight = configuredCameraHeight > 0.0f ? configuredCameraHeight : perspective.model.pos.z();
	cameraGround = perspective.model.pos.head<2>();

	if(!std::isfinite(cameraHeight) || cameraHeight <= 0.0f) {
		imageToFieldTransform.release();
		fieldToImageTransform.release();
		return;
	}

	LOG("Detection output correction enabled from four field corners at camera height " << cameraHeight << "mm");
}

bool DetectionCorrector::enabled() const {
	return !imageToFieldTransform.empty() && !fieldToImageTransform.empty();
}

Eigen::Vector2f DetectionCorrector::project(const cv::Mat& transform, const Eigen::Vector2f& point) const {
	const double denominator = transform.at<double>(2, 0) * point.x() + transform.at<double>(2, 1) * point.y() + transform.at<double>(2, 2);
	if(std::abs(denominator) < 1e-9)
		return {NAN, NAN};

	return {
		(float)((transform.at<double>(0, 0) * point.x() + transform.at<double>(0, 1) * point.y() + transform.at<double>(0, 2)) / denominator),
		(float)((transform.at<double>(1, 0) * point.x() + transform.at<double>(1, 1) * point.y() + transform.at<double>(1, 2)) / denominator)
	};
}

Eigen::Vector2f DetectionCorrector::imageToField(const Eigen::Vector2f& image, const float objectHeight) const {
	if(!enabled())
		return {NAN, NAN};

	const Eigen::Vector2f groundIntersection = project(imageToFieldTransform, image);
	const float heightScale = std::clamp((cameraHeight - objectHeight) / cameraHeight, 0.5f, 1.0f);
	return cameraGround + (groundIntersection - cameraGround) * heightScale;
}

Eigen::Vector2f DetectionCorrector::fieldToImage(const Eigen::Vector2f& field, const float objectHeight, const Perspective& perspective) const {
	if(!enabled())
		return perspective.model.field2image({field.x(), field.y(), objectHeight});

	const float heightScale = std::clamp((cameraHeight - objectHeight) / cameraHeight, 0.5f, 1.0f);
	const Eigen::Vector2f groundIntersection = cameraGround + (field - cameraGround) / heightScale;
	return project(fieldToImageTransform, groundIntersection);
}

void DetectionCorrector::correct(SSL_DetectionFrame* detection, const Perspective& perspective) const {
	if(!enabled())
		return;

	const float ballHeight = perspective.field.has_ball_radius() ? perspective.field.ball_radius() : 21.5f;
	for(SSL_DetectionBall& ball : *detection->mutable_balls()) {
		if(!ball.has_pixel_x() || !ball.has_pixel_y())
			continue;
		const Eigen::Vector2f corrected = imageToField({ball.pixel_x(), ball.pixel_y()}, ballHeight);
		if(corrected.allFinite()) {
			ball.set_x(corrected.x());
			ball.set_y(corrected.y());
		}
	}

	auto correctRobots = [this, &perspective](google::protobuf::RepeatedPtrField<SSL_DetectionRobot>* robots) {
		for(SSL_DetectionRobot& robot : *robots) {
			if(!robot.has_pixel_x() || !robot.has_pixel_y())
				continue;

			const float height = robot.has_height() ? robot.height() : 0.0f;
			const Eigen::Vector2f oldPosition(robot.x(), robot.y());
			const Eigen::Vector2f corrected = imageToField({robot.pixel_x(), robot.pixel_y()}, height);
			if(!corrected.allFinite())
				continue;

			if(robot.has_orientation()) {
				const Eigen::Vector2f oldDirection = oldPosition + Eigen::Vector2f(std::cos(robot.orientation()), std::sin(robot.orientation())) * 100.0f;
				const Eigen::Vector2f directionPixel = perspective.model.field2image({oldDirection.x(), oldDirection.y(), height});
				const Eigen::Vector2f correctedDirection = imageToField(directionPixel, height);
				const Eigen::Vector2f delta = correctedDirection - corrected;
				if(delta.allFinite() && delta.squaredNorm() > 1.0f)
					robot.set_orientation(std::atan2(delta.y(), delta.x()));
			}

			robot.set_x(corrected.x());
			robot.set_y(corrected.y());
		}
	};

	correctRobots(detection->mutable_robots_blue());
	correctRobots(detection->mutable_robots_yellow());
}

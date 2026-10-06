// Camera-image overlay: field outline/markings projected through the
// published camera calibration (same distortion model as the C++
// CameraModel::field2image), axes and goal labels, saved corners and lens
// lines, and the live robots/balls. Shared by the operator page and the
// pop-out view.

export type DataMap = Record<string, unknown>;
export type Point3 = [number, number, number];

export interface OverlayRobot {
  robot_id?: number;
  orientation?: number;
  pixel_x?: number;
  pixel_y?: number;
}

export interface OverlayBall {
  pixel_x?: number;
  pixel_y?: number;
}

export interface OverlayState {
  /** The camera's SSL_GeometryCameraCalibration (JSON), or {}. */
  calib: DataMap;
  /** SSL_GeometryFieldSize (JSON), or {}. */
  field: DataMap;
  /** The vision config's geometry: section (line_corners, ...). */
  geometryConfig: DataMap;
  robotsBlue: OverlayRobot[];
  robotsYellow: OverlayRobot[];
  balls: OverlayBall[];
}

export function valueNumber(value: unknown, fallback = 0): number {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function drawOverlay(
  canvas: HTMLCanvasElement,
  state: OverlayState,
): void {
  const cameraCalibration = state.calib;
  const field = state.field;
  const geometryConfig = state.geometryConfig;
  const blueRobots = state.robotsBlue;
  const yellowRobots = state.robotsYellow;
  const balls = state.balls;
  type RobotDetection = OverlayRobot;

  function projectCameraPoint(point: Point3): [number, number] | null {
    const focalLength = valueNumber(cameraCalibration["focal_length"]);
    const principalX = valueNumber(cameraCalibration["principal_point_x"]);
    const principalY = valueNumber(cameraCalibration["principal_point_y"]);
    const distortion = valueNumber(cameraCalibration["distortion"]);
    let qx = valueNumber(cameraCalibration["q0"]);
    let qy = valueNumber(cameraCalibration["q1"]);
    let qz = valueNumber(cameraCalibration["q2"]);
    let qw = valueNumber(cameraCalibration["q3"], 1);
    const quaternionLength = Math.hypot(qx, qy, qz, qw);
    if (focalLength <= 0 || quaternionLength <= 0) return null;

    qx /= quaternionLength;
    qy /= quaternionLength;
    qz /= quaternionLength;
    qw /= quaternionLength;

    const [x, y, z] = point;
    const tx = 2 * (qy * z - qz * y);
    const ty = 2 * (qz * x - qx * z);
    const tz = 2 * (qx * y - qy * x);
    const cameraX =
      x + qw * tx + (qy * tz - qz * ty) + valueNumber(cameraCalibration["tx"]);
    const cameraY =
      y + qw * ty + (qz * tx - qx * tz) + valueNumber(cameraCalibration["ty"]);
    const cameraZ =
      z + qw * tz + (qx * ty - qy * tx) + valueNumber(cameraCalibration["tz"]);
    if (cameraZ <= 0.001) return null;

    const originalX = cameraX / cameraZ;
    const originalY = cameraY / cameraZ;
    let normalizedX = originalX;
    let normalizedY = originalY;
    for (let iteration = 0; iteration < 10; iteration += 1) {
      const scale =
        1 +
        distortion * (normalizedX * normalizedX + normalizedY * normalizedY);
      normalizedX = originalX / scale;
      normalizedY = originalY / scale;
    }
    return [
      focalLength * normalizedX + principalX,
      focalLength * normalizedY + principalY,
    ];
  }

  // Distorted normalized image coords -> undistorted (inverse of the
  // fixed-point iteration in CameraModel::field2image: d = o / (1 + k2 d·d)).
  function undistortPixel(pixel: [number, number]): [number, number] | null {
    const focalLength = valueNumber(cameraCalibration["focal_length"]);
    if (focalLength <= 0) return null;
    const k2 = valueNumber(cameraCalibration["distortion"]);
    const dx =
      (pixel[0] - valueNumber(cameraCalibration["principal_point_x"])) /
      focalLength;
    const dy =
      (pixel[1] - valueNumber(cameraCalibration["principal_point_y"])) /
      focalLength;
    const scale = 1 + k2 * (dx * dx + dy * dy);
    return [dx * scale, dy * scale];
  }

  function distortNormalized(point: [number, number]): [number, number] {
    const focalLength = valueNumber(cameraCalibration["focal_length"]);
    const k2 = valueNumber(cameraCalibration["distortion"]);
    let [nx, ny] = point;
    for (let iteration = 0; iteration < 10; iteration += 1) {
      const scale = 1 + k2 * (nx * nx + ny * ny);
      nx = point[0] / scale;
      ny = point[1] / scale;
    }
    return [
      focalLength * nx + valueNumber(cameraCalibration["principal_point_x"]),
      focalLength * ny + valueNumber(cameraCalibration["principal_point_y"]),
    ];
  }

  function projectFieldPoint(point: Point3): [number, number] | null {
    // The published calibration is what vision_processor uses (incl. lens
    // distortion); the corner homography is only a fallback before it exists.
    if (valueNumber(cameraCalibration["focal_length"]) > 0)
      return projectCameraPoint(point);
    const includeBoundary =
      geometryConfig["line_corners_include_boundary"] === true;
    const boundary = valueNumber(field["boundary_width"]);
    const goalBoundary = valueNumber(
      field["boundary_width_goal_line"] ?? field["boundary_width"],
    );
    const fieldLength =
      valueNumber(field["field_length"]) +
      (includeBoundary ? 2 * goalBoundary : 0);
    const fieldWidth =
      valueNumber(field["field_width"]) + (includeBoundary ? 2 * boundary : 0);
    const corners = geometryConfig["line_corners"];
    if (
      fieldLength <= 0 ||
      fieldWidth <= 0 ||
      !Array.isArray(corners) ||
      corners.length !== 4
    ) {
      return projectCameraPoint(point);
    }

    const parsed = corners.map((corner) =>
      Array.isArray(corner) && corner.length >= 2
        ? ([
            valueNumber(corner[0], Number.NaN),
            valueNumber(corner[1], Number.NaN),
          ] as [number, number])
        : null,
    );
    // Config order is bottom-left, top-left, top-right, bottom-right.
    const [p0, p3, p2, p1] = parsed;
    if (
      !p0 ||
      !p1 ||
      !p2 ||
      !p3 ||
      [p0, p1, p2, p3].some(
        (corner) => !Number.isFinite(corner[0]) || !Number.isFinite(corner[1]),
      )
    ) {
      return projectCameraPoint(point);
    }
    const dx1 = p1[0] - p2[0];
    const dx2 = p3[0] - p2[0];
    const dx3 = p0[0] - p1[0] + p2[0] - p3[0];
    const dy1 = p1[1] - p2[1];
    const dy2 = p3[1] - p2[1];
    const dy3 = p0[1] - p1[1] + p2[1] - p3[1];
    const determinant = dx1 * dy2 - dx2 * dy1;
    let g = 0;
    let h = 0;
    if (Math.abs(determinant) > 1e-9) {
      g = (dx3 * dy2 - dx2 * dy3) / determinant;
      h = (dx1 * dy3 - dx3 * dy1) / determinant;
    }
    const a = p1[0] - p0[0] + g * p1[0];
    const b = p3[0] - p0[0] + h * p3[0];
    const d = p1[1] - p0[1] + g * p1[1];
    const e = p3[1] - p0[1] + h * p3[1];
    const u = point[0] / fieldLength + 0.5;
    const v = point[1] / fieldWidth + 0.5;
    const scale = g * u + h * v + 1;
    if (Math.abs(scale) <= 1e-9) return null;
    return [(a * u + b * v + p0[0]) / scale, (d * u + e * v + p0[1]) / scale];
  }

  function drawFieldOverlay(): void {
    const width = valueNumber(cameraCalibration["pixel_image_width"], 768);
    const height = valueNumber(cameraCalibration["pixel_image_height"], 432);
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, width, height);
    context.lineJoin = "round";
    context.lineCap = "round";

    const drawPath = (
      points: Point3[],
      stroke: string,
      lineWidth = 2,
      dash: number[] = [],
      close = false,
    ): void => {
      // Subdivide so straight field lines curve with the lens distortion.
      const dense: Point3[] = [];
      const closedPoints = close && points[0] ? [...points, points[0]] : points;
      closedPoints.forEach((current, index) => {
        const next = closedPoints[index + 1];
        dense.push(current);
        if (!next) return;
        const steps = 16;
        for (let step = 1; step < steps; step += 1) {
          const t = step / steps;
          dense.push([
            current[0] + (next[0] - current[0]) * t,
            current[1] + (next[1] - current[1]) * t,
            current[2] + (next[2] - current[2]) * t,
          ]);
        }
      });
      const projected = dense
        .map(projectFieldPoint)
        .filter((point): point is [number, number] => point !== null);
      if (projected.length < 2) return;
      const firstPoint = projected[0];
      if (!firstPoint) return;
      context.beginPath();
      context.moveTo(firstPoint[0], firstPoint[1]);
      for (const point of projected.slice(1))
        context.lineTo(point[0], point[1]);
      if (close) context.closePath();
      context.setLineDash(dash);
      context.strokeStyle = "rgba(0, 0, 0, 0.78)";
      context.lineWidth = lineWidth + 3;
      context.stroke();
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.stroke();
      context.setLineDash([]);
    };

    const fieldLength = valueNumber(field["field_length"]);
    const fieldWidth = valueNumber(field["field_width"]);
    if (fieldLength <= 0 || fieldWidth <= 0) {
      drawDetections(context);
      return;
    }
    const halfLength = fieldLength / 2;
    const halfWidth = fieldWidth / 2;
    const boundary = valueNumber(field["boundary_width"]);
    const goalWidth = valueNumber(field["goal_width"]);
    const goalDepth = valueNumber(field["goal_depth"]);
    const penaltyDepth = valueNumber(field["penalty_area_depth"]);
    const penaltyWidth = valueNumber(field["penalty_area_width"]);
    const centerRadius = valueNumber(field["center_circle_radius"]);

    drawPath(
      [
        [-halfLength - boundary, -halfWidth - boundary, 0],
        [halfLength + boundary, -halfWidth - boundary, 0],
        [halfLength + boundary, halfWidth + boundary, 0],
        [-halfLength - boundary, halfWidth + boundary, 0],
      ],
      "#4dd8a0",
      1.5,
      [8, 6],
      true,
    );

    drawPath(
      [
        [-halfLength, -halfWidth, 0],
        [halfLength, -halfWidth, 0],
        [halfLength, halfWidth, 0],
        [-halfLength, halfWidth, 0],
      ],
      "#f4f7f5",
      2.2,
      [],
      true,
    );
    drawPath(
      [
        [0, -halfWidth, 0],
        [0, halfWidth, 0],
      ],
      "#ffd451",
      1.7,
    );

    if (centerRadius > 0) {
      const circle: Point3[] = [];
      for (let step = 0; step <= 64; step += 1) {
        const angle = (step / 64) * Math.PI * 2;
        circle.push([
          Math.cos(angle) * centerRadius,
          Math.sin(angle) * centerRadius,
          0,
        ]);
      }
      drawPath(circle, "#ffd451", 1.7, [], true);
    }

    if (penaltyDepth > 0 && penaltyWidth > 0) {
      const halfPenaltyWidth = penaltyWidth / 2;
      drawPath(
        [
          [-halfLength, -halfPenaltyWidth, 0],
          [-halfLength + penaltyDepth, -halfPenaltyWidth, 0],
          [-halfLength + penaltyDepth, halfPenaltyWidth, 0],
          [-halfLength, halfPenaltyWidth, 0],
        ],
        "#ff78ae",
        1.7,
      );
      drawPath(
        [
          [halfLength, -halfPenaltyWidth, 0],
          [halfLength - penaltyDepth, -halfPenaltyWidth, 0],
          [halfLength - penaltyDepth, halfPenaltyWidth, 0],
          [halfLength, halfPenaltyWidth, 0],
        ],
        "#ff78ae",
        1.7,
      );
    }

    if (goalWidth > 0 && goalDepth > 0) {
      const halfGoalWidth = goalWidth / 2;
      for (const side of [-1, 1]) {
        const goalLineX = side * halfLength;
        const backX = side * (halfLength + goalDepth);
        const base: Point3[] = [
          [goalLineX, -halfGoalWidth, 0],
          [backX, -halfGoalWidth, 0],
          [backX, halfGoalWidth, 0],
          [goalLineX, halfGoalWidth, 0],
        ];
        drawPath(base, "#40cfff", 2.1, [], true);
      }
    }

    // Field axes and goal ends: the frame is fixed by the corner order
    // (corner 1 = (-x, -y)), so show where +x / +y and the goals ended up.
    const drawArrow = (
      from: Point3,
      to: Point3,
      color: string,
      label: string,
    ): void => {
      const a = projectFieldPoint(from);
      const b = projectFieldPoint(to);
      if (!a || !b) return;
      const angle = Math.atan2(b[1] - a[1], b[0] - a[0]);
      context.setLineDash([]);
      context.beginPath();
      context.moveTo(a[0], a[1]);
      context.lineTo(b[0], b[1]);
      context.lineWidth = 2.5;
      context.strokeStyle = color;
      context.stroke();
      context.beginPath();
      context.moveTo(b[0], b[1]);
      context.lineTo(
        b[0] - 9 * Math.cos(angle - 0.5),
        b[1] - 9 * Math.sin(angle - 0.5),
      );
      context.lineTo(
        b[0] - 9 * Math.cos(angle + 0.5),
        b[1] - 9 * Math.sin(angle + 0.5),
      );
      context.closePath();
      context.fillStyle = color;
      context.fill();
      drawLabel(label, b[0] + 6, b[1] - 6, color);
    };
    const drawLabel = (
      label: string,
      x: number,
      y: number,
      color = "#ffffff",
    ): void => {
      context.font = "bold 12px sans-serif";
      context.lineWidth = 3;
      context.strokeStyle = "#111713";
      context.strokeText(label, x, y);
      context.fillStyle = color;
      context.fillText(label, x, y);
    };
    drawArrow([0, 0, 0], [halfLength / 2, 0, 0], "#ff7a59", "+x");
    drawArrow([0, 0, 0], [0, halfWidth / 2, 0], "#7ed957", "+y");
    const goalMinus = projectFieldPoint([-halfLength, 0, 0]);
    const goalPlus = projectFieldPoint([halfLength, 0, 0]);
    if (goalMinus) drawLabel("goal −x", goalMinus[0] + 6, goalMinus[1] - 8);
    if (goalPlus) drawLabel("goal +x", goalPlus[0] + 6, goalPlus[1] - 8);
    const origin = projectFieldPoint([-halfLength, -halfWidth, 0]);
    if (origin)
      drawLabel(
        "origin corner (−x,−y)",
        origin[0] + 8,
        origin[1] + 16,
        "#ffd451",
      );

    const center = projectFieldPoint([0, 0, 0]);
    if (center) {
      context.beginPath();
      context.arc(center[0], center[1], 4, 0, Math.PI * 2);
      context.fillStyle = "#ffd451";
      context.fill();
      context.strokeStyle = "#111713";
      context.lineWidth = 1.5;
      context.stroke();
    }

    // Corners saved in the config: the clicked outer edge (if any) and the
    // field corners vision_processor calibrates from, numbered 1..4.
    const quadrant = ["−x −y", "−x +y", "+x +y", "+x −y"];
    const drawCorners = (
      value: unknown,
      fill: string,
      radius: number,
      labels = false,
    ): void => {
      if (!Array.isArray(value)) return;
      value.forEach((corner: unknown, index) => {
        if (!Array.isArray(corner) || corner.length < 2) return;
        const x = valueNumber(corner[0], Number.NaN);
        const y = valueNumber(corner[1], Number.NaN);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return;
        context.beginPath();
        context.arc(x, y, radius, 0, Math.PI * 2);
        context.fillStyle = fill;
        context.fill();
        context.strokeStyle = "#ffffff";
        context.lineWidth = 1.5;
        context.stroke();
        context.font = "bold 12px sans-serif";
        context.lineWidth = 3;
        context.strokeStyle = "#111713";
        const label = labels
          ? `${String(index + 1)} · ${quadrant[index] ?? ""}`
          : String(index + 1);
        // Keep labels inside the image near the right edge.
        const right = x > width * 0.8;
        context.textAlign = right ? "right" : "left";
        const lx = right ? x - radius - 2 : x + radius + 2;
        context.strokeText(label, lx, y - radius - 2);
        context.fillStyle = "#ffffff";
        context.fillText(label, lx, y - radius - 2);
        context.textAlign = "left";
      });
    };
    drawLensLines(context);
    drawCorners(geometryConfig["outer_line_corners"], "#ff5b55", 5);
    drawCorners(geometryConfig["line_corners"], "#f4f7f5", 3.5, true);

    drawDetections(context);
  }

  // The clicked lens-correction lines (solid) and the straight line the
  // calibrated lens model makes of them (dashed): least-squares line through
  // the undistorted points, re-distorted. They overlap when the fit is good.
  function drawLensLines(context: CanvasRenderingContext2D): void {
    const lines = geometryConfig["distortion_lines"];
    if (!Array.isArray(lines)) return;
    for (const line of lines) {
      if (!Array.isArray(line)) continue;
      const points = line
        .map((point: unknown): [number, number] | null =>
          Array.isArray(point) && point.length >= 2
            ? [valueNumber(point[0]), valueNumber(point[1])]
            : null,
        )
        .filter((point): point is [number, number] => point !== null);
      const first = points[0];
      if (!first || points.length < 2) continue;
      context.setLineDash([]);
      context.beginPath();
      context.moveTo(first[0], first[1]);
      for (const point of points.slice(1)) context.lineTo(point[0], point[1]);
      context.lineWidth = 1.5;
      context.strokeStyle = "#b99cff";
      context.stroke();

      const undistorted = points
        .map(undistortPixel)
        .filter((point): point is [number, number] => point !== null);
      if (undistorted.length < 2) continue;
      const n = undistorted.length;
      const mx = undistorted.reduce((sum, p) => sum + p[0], 0) / n;
      const my = undistorted.reduce((sum, p) => sum + p[1], 0) / n;
      let sxx = 0;
      let sxy = 0;
      let syy = 0;
      for (const [x, y] of undistorted) {
        sxx += (x - mx) ** 2;
        sxy += (x - mx) * (y - my);
        syy += (y - my) ** 2;
      }
      const angle = 0.5 * Math.atan2(2 * sxy, sxx - syy);
      const ux = Math.cos(angle);
      const uy = Math.sin(angle);
      const along = undistorted.map((p) => (p[0] - mx) * ux + (p[1] - my) * uy);
      const t0 = Math.min(...along);
      const t1 = Math.max(...along);
      context.beginPath();
      for (let step = 0; step <= 32; step += 1) {
        const t = t0 + ((t1 - t0) * step) / 32;
        const [px, py] = distortNormalized([mx + ux * t, my + uy * t]);
        if (step === 0) context.moveTo(px, py);
        else context.lineTo(px, py);
      }
      context.setLineDash([5, 4]);
      context.lineWidth = 1.5;
      context.strokeStyle = "#ffffff";
      context.stroke();
      context.setLineDash([]);
    }
  }

  // Robots and balls of the live detection frame, at their image pixels.
  function drawDetections(context: CanvasRenderingContext2D): void {
    const robots: [RobotDetection, string][] = [
      ...blueRobots.map((robot): [RobotDetection, string] => [
        robot,
        "#2a80c8",
      ]),
      ...yellowRobots.map((robot): [RobotDetection, string] => [
        robot,
        "#e5be22",
      ]),
    ];
    for (const [robot, color] of robots) {
      const x = valueNumber(robot.pixel_x, Number.NaN);
      const y = valueNumber(robot.pixel_y, Number.NaN);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      context.beginPath();
      context.arc(x, y, 14, 0, Math.PI * 2);
      context.lineWidth = 3;
      context.strokeStyle = color;
      context.stroke();
      context.font = "bold 12px sans-serif";
      context.lineWidth = 3;
      context.strokeStyle = "#111713";
      const label = String(robot.robot_id ?? "?");
      context.strokeText(label, x + 15, y - 12);
      context.fillStyle = color;
      context.fillText(label, x + 15, y - 12);
    }
    for (const ball of balls) {
      const x = valueNumber(ball.pixel_x, Number.NaN);
      const y = valueNumber(ball.pixel_y, Number.NaN);
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      context.beginPath();
      context.arc(x, y, 7, 0, Math.PI * 2);
      context.lineWidth = 2.5;
      context.strokeStyle = "#ff8a2a";
      context.stroke();
    }
  }

  drawFieldOverlay();
}

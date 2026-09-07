<script lang="ts">
  import { onMount } from "svelte";
  import { connectionState, topic } from "./lib/wrapper-bus";

  type DataMap = Record<string, unknown>;

  interface Snapshot {
    cam_id: string;
    view: string;
  }

  interface RobotDetection {
    robot_id?: number;
    confidence?: number;
    x?: number;
    y?: number;
    orientation?: number;
    pixel_x?: number;
    pixel_y?: number;
    height?: number;
  }

  interface BallDetection {
    confidence?: number;
    x?: number;
    y?: number;
    z?: number;
    pixel_x?: number;
    pixel_y?: number;
  }

  interface DetectionFrame {
    frame_number?: number;
    t_capture?: number;
    t_sent?: number;
    camera_id?: number;
    balls?: BallDetection[];
    robots_blue?: RobotDetection[];
    robots_yellow?: RobotDetection[];
  }

  type Point3 = [number, number, number];

  interface FieldLine {
    name?: string;
    p1?: { x?: number; y?: number };
    p2?: { x?: number; y?: number };
    thickness?: number;
  }

  interface FieldArc {
    name?: string;
    center?: { x?: number; y?: number };
    radius?: number;
    a1?: number;
    a2?: number;
  }

  interface FieldData extends DataMap {
    field_lines?: FieldLine[];
    field_arcs?: FieldArc[];
  }

  interface GeometryData {
    field?: FieldData;
    calib?: DataMap[];
    models?: DataMap;
  }

  interface WrapperPacket {
    detection?: DetectionFrame;
    geometry?: GeometryData;
    source?: string;
  }

  interface CameraStatus {
    camera_id: number;
    address: string;
    name: string;
    online: boolean;
    fps: number;
    latency_ms: number;
    frame_number: number;
    age_s: number | null;
  }

  interface CamerasPayload {
    cameras: CameraStatus[];
    combined: { count: number; online: number; fps: number; latency_ms: number };
  }

  interface ConfigResponse {
    path: string;
    modified_at: number;
    config: DataMap;
  }

  interface GeometryResponse {
    path: string;
    modified_at: number;
    geometry: DataMap;
  }

  interface ServiceHealth {
    running: boolean;
    pid: number | null;
  }

  interface HealthResponse {
    status: string;
    uptime_s: number;
    vision_config?: string;
    geometry_config?: string;
    snapshot_count: number;
    latest_snapshot_age_s: number | null;
    services: Record<string, ServiceHealth>;
  }

  /** A robot we have seen, kept briefly after it stops being detected. */
  interface TrackedRobot {
    key: string;
    team: "blue" | "yellow";
    id: number;
    x: number;
    y: number;
    orientation: number;
    confidence: number;
    height: number;
    camera: number;
    lastSeen: number;
  }

  // A robot greys out this long after its last detection, and is dropped
  // after DROP_MS. Greying at a few frames' worth of silence rather than one
  // keeps the display from flickering at ~20 fps.
  const STALE_MS = 250;
  const DROP_MS = 1000;

  const wrapperPacket = topic<WrapperPacket>("wrapper_packet.out");
  const detectionPacket = topic<DetectionFrame>("detection.in");
  const camerasPacket = topic<CamerasPayload>("cameras.out");

  const apiBase = location.port === "8765" ? "" : `http://${location.hostname}:8765`;
  const preferredViews = [
    "raw",
    "flat",
    "gradient",
    "blob",
    "pixels.corner",
    "pixels.refined",
    "lines",
  ];
  const colorNames = ["orange", "field", "yellow", "blue", "green", "pink"];

  let snapshots = $state<Snapshot[]>([]);
  let selectedCamera = $state<string>("combine");
  let selectedView = $state("overlay");
  let cacheBuster = $state(0);
  // The C++ side rewrites the debug images every debug_stream_interval_ms
  // (100 ms on this setup). Refreshing them once a second left the picture
  // visibly stale under an overlay that moves at the detection rate, so pull
  // them several times a second instead.
  const IMAGE_REFRESH_MS = 200;
  // Staleness is judged on this tick, so it also sets how promptly a robot
  // greys out when its camera stops reporting.
  const AGE_TICK_MS = 50;
  let now = $state(Date.now());
  let configPayload = $state<ConfigResponse | null>(null);
  let health = $state<HealthResponse | null>(null);
  let geometry = $state<GeometryData | null>(null);
  let cameras = $state<CamerasPayload | null>(null);
  // HTTP is the floor for both of these. The bus carries them too, but a
  // browser that falls behind the ~20/s detection stream would otherwise
  // leave the field dimensions and the camera roster blank indefinitely -
  // and both should be readable whenever the backend is up at all.
  let geometryFile = $state<GeometryResponse | null>(null);
  let fieldHttp = $state<GeometryData | null>(null);
  let camerasHttp = $state<CamerasPayload | null>(null);
  let fieldCanvas = $state<HTMLCanvasElement>();
  let overlayCanvas = $state<HTMLCanvasElement>();

  // Detections arrive per camera and each camera has its own frame counter,
  // so keep the newest frame from each rather than one shared slot. The
  // combined view is the union; a single-camera view reads one entry.
  let framesByCamera = $state<Record<number, DetectionFrame>>({});
  // When each camera's frame arrived, by our clock. t_sent is stamped on the
  // sending machine, so comparing it to Date.now() silently misbehaves the
  // moment the detector runs anywhere else - which is the whole point of the
  // multi-camera setup. Robots already age off a local stamp; balls now do too.
  let frameArrivedAt = $state<Record<number, number>>({});
  let tracked = $state<Record<string, TrackedRobot>>({});
  let lastUpdate = $state<number | null>(null);

  let activeConfig = $derived(asRecord(configPayload?.config));
  let cameraConfig = $derived(asRecord(activeConfig["camera"]));
  let geometryConfig = $derived(asRecord(activeConfig["geometry"]));
  let thresholdConfig = $derived(asRecord(activeConfig["thresholds"]));
  let colorConfig = $derived(asRecord(activeConfig["color"]));
  let networkConfig = $derived(asRecord(activeConfig["network"]));
  let streamConfig = $derived(asRecord(activeConfig["stream"]));
  // The panel and the drawing want different things, so keep them apart.
  //
  // fieldFile is the geometry yaml as written: what this setup is configured
  // to be. That is what the Field geometry panel reports, so the numbers on
  // screen always match the file an operator can open and edit.
  //
  // fieldDrawn is the wrapper's broadcast geometry, which is the only source
  // of the generated lines and arcs, and so the only thing that can be drawn.
  // It falls back to the file for dimensions before the first packet lands.
  // The broadcast geometry, however it reached us. It carries the generated
  // field lines and the absorbed calibrations, so without it there is nothing
  // to draw and no solved camera model to report.
  let liveGeometry = $derived(geometry ?? fieldHttp);

  let fieldFile = $derived(asRecord(geometryFile?.geometry["field"]));
  // Which markings are actually painted on this carpet. The wrapper only
  // generates the enabled ones, so the overlay has to honour them too -
  // drawing a halfway line that is not on the floor makes the calibration
  // check read as wrong when it is right.
  let optionalLines = $derived(
    asRecord(geometryFile?.geometry["optional_field_lines"]),
  );
  let fieldDrawn = $derived<FieldData>({ ...fieldFile, ...(liveGeometry?.field ?? {}) });

  // Prefer whichever source actually has cameras, not merely whichever is
  // non-null: cameras.out publishes an empty roster before the first
  // detection arrives, and that empty payload would otherwise mask the
  // polled copy for the rest of the session.
  let cameraSource = $derived(
    (cameras?.cameras.length ?? 0) > 0
      ? cameras
      : ((camerasHttp?.cameras.length ?? 0) > 0 ? camerasHttp : cameras ?? camerasHttp),
  );

  // A camera that has sent a detection exists, full stop. The roster is only
  // published once a second and carries the extras (name, address, measured
  // rate), so seed the list from the frames themselves and let the roster
  // fill in detail as it arrives - otherwise a camera that just came up is
  // missing from the table for up to a second while its data is on screen.
  let cameraList = $derived.by<CameraStatus[]>(() => {
    const roster = cameraSource?.cameras ?? [];
    const known = roster.map((camera) => camera.camera_id);
    const seeded: CameraStatus[] = [];
    for (const [key, frame] of Object.entries(framesByCamera)) {
      const id = Number(key);
      if (known.includes(id)) continue;
      seeded.push({
        camera_id: id,
        address: "--",
        name: `camera ${String(id)}`,
        online: true,
        fps: 0,
        latency_ms: ((frame.t_sent ?? 0) - (frame.t_capture ?? 0)) * 1000,
        frame_number: frame.frame_number ?? 0,
        age_s: 0,
      });
    }
    return [...roster, ...seeded].sort((a, b) => a.camera_id - b.camera_id);
  });
  let combined = $derived.by(() => {
    const roster = cameraSource?.combined;
    const online = cameraList.filter((camera) => camera.online).length;
    return {
      count: cameraList.length,
      online,
      fps: roster?.fps ?? 0,
      latency_ms: roster?.latency_ms ?? 0,
    };
  });

  // Calibration is published per camera; match it to the selection rather
  // than always reading calib[0], which is only correct for one camera.
  let cameraCalibration = $derived.by<DataMap>(() => {
    const calibs = liveGeometry?.calib ?? [];
    if (selectedCameraId === null) return calibs[0] ?? {};
    return (
      calibs.find((calib) => Number(calib["camera_id"] ?? -1) === selectedCameraId) ?? {}
    );
  });

  let isCombined = $derived(selectedCamera === "combine");
  let selectedCameraId = $derived(isCombined ? null : Number(selectedCamera));
  let selectedStatus = $derived(
    cameraList.find((camera) => camera.camera_id === selectedCameraId),
  );

  // The frame driving the header: the selected camera's, or whichever
  // camera reported most recently when combining.
  let activeFrame = $derived.by<DetectionFrame | undefined>(() => {
    const frames = Object.values(framesByCamera);
    if (frames.length === 0) return undefined;
    if (selectedCameraId !== null) return framesByCamera[selectedCameraId];
    return frames.reduce((newest, frame) =>
      (frame.t_sent ?? 0) > (newest.t_sent ?? 0) ? frame : newest,
    );
  });

  let visibleRobots = $derived(
    Object.values(tracked)
      .filter((robot) => now - robot.lastSeen <= DROP_MS)
      .filter((robot) => selectedCameraId === null || robot.camera === selectedCameraId)
      .sort((a, b) => a.id - b.id),
  );
  let blueRobots = $derived(visibleRobots.filter((robot) => robot.team === "blue"));
  let yellowRobots = $derived(visibleRobots.filter((robot) => robot.team === "yellow"));

  let balls = $derived.by<(BallDetection & { camera: number })[]>(() => {
    const out: (BallDetection & { camera: number })[] = [];
    for (const [camId, frame] of Object.entries(framesByCamera)) {
      const id = Number(camId);
      if (selectedCameraId !== null && id !== selectedCameraId) continue;
      if (now - (frameArrivedAt[id] ?? 0) > DROP_MS) continue;
      for (const ball of frame.balls ?? []) out.push({ ...ball, camera: id });
    }
    return out;
  });

  let processingHealthy = $derived(
    $connectionState === "open" && combined.online > 0 && combined.online === combined.count,
  );

  $effect(() => {
    const packet = $wrapperPacket;
    if (packet?.geometry) geometry = packet.geometry;
  });

  $effect(() => {
    const payload = $camerasPacket;
    if (payload) cameras = payload;
  });

  $effect(() => {
    const frame = $detectionPacket;
    if (frame?.camera_id === undefined) return;
    const stamp = Date.now();
    framesByCamera = { ...framesByCamera, [frame.camera_id]: frame };
    frameArrivedAt = { ...frameArrivedAt, [frame.camera_id]: stamp };
    lastUpdate = stamp;
    absorbRobots(frame);
  });

  // Re-evaluate ages on a timer so entries grey out and expire even when no
  // new frames arrive - which is exactly the case a dead camera produces.
  $effect(() => {
    const timer = setInterval(() => {
      now = Date.now();
    }, AGE_TICK_MS);
    return () => {
      clearInterval(timer);
    };
  });

  $effect(() => {
    void [fieldCanvas, liveGeometry, visibleRobots, balls, isCombined];
    drawField();
  });

  $effect(() => {
    void [overlayCanvas, selectedView, liveGeometry, cameraCalibration, visibleRobots, balls];
    drawFieldOverlay();
  });

  // Views are per camera and not every camera writes every view, so a stale
  // selection would leave the stage blank after switching. Fall back to the
  // first view this camera actually has.
  $effect(() => {
    if (isCombined) return;
    const available = cameraViews();
    if (!available.includes(selectedView)) {
      selectedView = available[0] ?? "overlay";
    }
  });

  function absorbRobots(frame: DetectionFrame): void {
    const stamp = Date.now();
    const camera = frame.camera_id ?? 0;
    const next = { ...tracked };
    const record = (team: "blue" | "yellow", robots: RobotDetection[]) => {
      for (const robot of robots) {
        const id = robot.robot_id ?? -1;
        const key = `${team}-${String(id)}`;
        next[key] = {
          key,
          team,
          id,
          x: robot.x ?? 0,
          y: robot.y ?? 0,
          orientation: robot.orientation ?? 0,
          confidence: robot.confidence ?? 0,
          height: robot.height ?? 0,
          camera,
          lastSeen: stamp,
        };
      }
    };
    record("blue", frame.robots_blue ?? []);
    record("yellow", frame.robots_yellow ?? []);
    tracked = Object.fromEntries(
      Object.entries(next).filter(([, robot]) => stamp - robot.lastSeen <= DROP_MS),
    );
  }

  function asRecord(value: unknown): DataMap {
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as DataMap)
      : {};
  }

  function api(path: string): string {
    return `${apiBase}${path}`;
  }

  function number(value: unknown, digits = 0): string {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "--";
  }

  function text(value: unknown): string {
    if (value === null || value === undefined || value === "") return "--";
    if (typeof value === "string") return value;
    if (typeof value === "number" || typeof value === "boolean") return String(value);
    return JSON.stringify(value);
  }

  function colorCss(value: unknown): string {
    if (!Array.isArray(value) || value.length < 3) return "#808080";
    return `rgb(${number(value[0])} ${number(value[1])} ${number(value[2])})`;
  }

  /** Robot height is per team, resolved by the detector from the Game
   * Controller's team names, and reported per robot on the wire. Show the
   * distinct values actually being used rather than a config constant. */
  function robotHeightText(): string {
    const heights = new Set(
      visibleRobots.map((robot) => robot.height).filter((height) => height > 0),
    );
    if (heights.size === 0) return "--";
    return [...heights].map((height) => height.toFixed(0)).join(" / ") + " mm";
  }

  function valueNumber(value: unknown, fallback = 0): number {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  /** Project a field point through the solved camera model. */
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
    const cameraX = x + qw * tx + (qy * tz - qz * ty) + valueNumber(cameraCalibration["tx"]);
    const cameraY = y + qw * ty + (qz * tx - qx * tz) + valueNumber(cameraCalibration["ty"]);
    const cameraZ = z + qw * tz + (qx * ty - qy * tx) + valueNumber(cameraCalibration["tz"]);
    if (cameraZ <= 0.001) return null;

    const originalX = cameraX / cameraZ;
    const originalY = cameraY / cameraZ;
    let normalizedX = originalX;
    let normalizedY = originalY;
    for (let iteration = 0; iteration < 10; iteration += 1) {
      const scale = 1 + distortion * (normalizedX * normalizedX + normalizedY * normalizedY);
      normalizedX = originalX / scale;
      normalizedY = originalY / scale;
    }
    return [focalLength * normalizedX + principalX, focalLength * normalizedY + principalY];
  }

  /** Prefer the homography implied by the four configured line corners: it is
   * what the operator actually tuned, so the drawn field matches the image
   * even before the solver has converged. Falls back to the camera model. */
  function projectFieldPoint(point: Point3): [number, number] | null {
    const fieldLength = valueNumber(fieldDrawn["field_length"]);
    const fieldWidth = valueNumber(fieldDrawn["field_width"]);
    const corners = geometryConfig["line_corners"];
    if (
      fieldLength <= 0 ||
      fieldWidth <= 0 ||
      !Array.isArray(corners) ||
      corners.length !== 4
    ) {
      return projectCameraPoint(point);
    }

    const parsed: [number, number][] = [];
    for (const corner of corners) {
      if (!Array.isArray(corner) || corner.length < 2) break;
      const cornerX = valueNumber(corner[0], Number.NaN);
      const cornerY = valueNumber(corner[1], Number.NaN);
      if (!Number.isFinite(cornerX) || !Number.isFinite(cornerY)) break;
      parsed.push([cornerX, cornerY]);
    }

    // Config order is bottom-left, top-left, top-right, bottom-right.
    const [bottomLeft, topLeft, topRight, bottomRight] = parsed;
    if (!bottomLeft || !topLeft || !topRight || !bottomRight) {
      return projectCameraPoint(point);
    }
    const p0 = bottomLeft;
    const p1 = bottomRight;
    const p2 = topRight;
    const p3 = topLeft;
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

  function isStale(robot: TrackedRobot): boolean {
    return now - robot.lastSeen > STALE_MS;
  }

  function baseName(path: unknown): string {
    if (typeof path !== "string" || path === "") return "--";
    return path.split("/").pop() ?? path;
  }

  function clockText(stamp: number | null): string {
    if (!stamp) return "--";
    return new Date(stamp).toLocaleTimeString();
  }

  function viewLabel(view: string): string {
    const labels: Record<string, string> = {
      overlay: "Field overlay",
      raw: "Raw",
      flat: "Color delta",
      gradient: "Gradient",
      blob: "Blob score",
      "pixels.corner": "Corner model",
      "pixels.refined": "Refined model",
      lines: "Detected lines",
      pixels: "Pixels",
      geomcalib_input: "Calib input",
    };
    return labels[view] ?? view;
  }

  /** Views offered for the selected camera: the synthesised field overlay
   * first, then whatever this camera has actually written to disk. */
  function cameraViews(): string[] {
    if (isCombined) {
      // Nothing is selected, so show the usual set disabled rather than an
      // empty bar - the tabs should read as available-once-you-pick-a-camera.
      const seen = snapshots.map((snapshot) => snapshot.view);
      const known = preferredViews.filter((view) => seen.includes(view));
      return ["overlay", ...(known.length > 0 ? known : preferredViews)];
    }
    return ["overlay", ...cameraSnapshots().map((snapshot) => snapshot.view)];
  }

  function cameraSnapshots(): Snapshot[] {
    const id = String(selectedCameraId ?? "");
    const mine = snapshots.filter((snapshot) => snapshot.cam_id === id);
    const ranked = (view: string) => {
      const index = preferredViews.indexOf(view);
      return index === -1 ? preferredViews.length : index;
    };
    return mine.sort((a, b) => ranked(a.view) - ranked(b.view));
  }

  function currentSnapshot(): Snapshot | undefined {
    return cameraSnapshots().find((snapshot) => snapshot.view === selectedView);
  }

  /** Draw field geometry and the live detections over this camera's image,
   * in image pixels. This is the calibration check: if the drawn lines do not
   * sit on the painted ones, the geometry is wrong. */
  function drawFieldOverlay(): void {
    const canvas = overlayCanvas;
    if (!canvas || isCombined || selectedView !== "overlay") return;
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
      const projected = points
        .map(projectFieldPoint)
        .filter((point): point is [number, number] => point !== null);
      const first = projected[0];
      if (projected.length < 2 || !first) return;
      context.beginPath();
      context.moveTo(first[0], first[1]);
      for (const point of projected.slice(1)) context.lineTo(point[0], point[1]);
      if (close) context.closePath();
      context.setLineDash(dash);
      // Dark halo first, so the line stays readable over pale carpet.
      context.strokeStyle = "rgba(0, 0, 0, 0.78)";
      context.lineWidth = lineWidth + 3;
      context.stroke();
      context.strokeStyle = stroke;
      context.lineWidth = lineWidth;
      context.stroke();
      context.setLineDash([]);
    };

    const fieldLength = valueNumber(fieldDrawn["field_length"]);
    const fieldWidth = valueNumber(fieldDrawn["field_width"]);
    if (fieldLength <= 0 || fieldWidth <= 0) return;
    const halfLength = fieldLength / 2;
    const halfWidth = fieldWidth / 2;
    const boundary = valueNumber(fieldDrawn["boundary_width"]);
    const goalWidth = valueNumber(fieldDrawn["goal_width"]);
    const goalDepth = valueNumber(fieldDrawn["goal_depth"]);
    const penaltyDepth = valueNumber(fieldDrawn["penalty_area_depth"]);
    const penaltyWidth = valueNumber(fieldDrawn["penalty_area_width"]);
    const centerRadius = valueNumber(fieldDrawn["center_circle_radius"]);

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
    // Optional markings are drawn either way: solid when the carpet actually
    // has them, dashed when it does not, so the regulation field is there for
    // orientation without implying the camera should be seeing a line.
    const optionalDash = (name: string): number[] =>
      optionalLines[name] === true ? [] : [5, 5];

    drawPath(
      [
        [0, -halfWidth, 0],
        [0, halfWidth, 0],
      ],
      "#ffd451",
      1.7,
      optionalDash("halfway"),
    );

    drawPath(
      [
        [-halfLength, 0, 0],
        [halfLength, 0, 0],
      ],
      "#ffd451",
      1.7,
      optionalDash("goal2goal"),
    );

    if (centerRadius > 0) {
      const circle: Point3[] = [];
      for (let step = 0; step <= 64; step += 1) {
        const angle = (step / 64) * Math.PI * 2;
        circle.push([Math.cos(angle) * centerRadius, Math.sin(angle) * centerRadius, 0]);
      }
      drawPath(circle, "#ffd451", 1.7, optionalDash("centercircle"), true);
    }

    if (penaltyDepth > 0 && penaltyWidth > 0) {
      const halfPenaltyWidth = penaltyWidth / 2;
      for (const side of [-1, 1]) {
        drawPath(
          [
            [side * halfLength, -halfPenaltyWidth, 0],
            [side * (halfLength - penaltyDepth), -halfPenaltyWidth, 0],
            [side * (halfLength - penaltyDepth), halfPenaltyWidth, 0],
            [side * halfLength, halfPenaltyWidth, 0],
          ],
          "#ff78ae",
          1.7,
          optionalDash("penalty"),
        );
      }
    }

    if (goalWidth > 0 && goalDepth > 0) {
      const halfGoalWidth = goalWidth / 2;
      for (const side of [-1, 1]) {
        drawPath(
          [
            [side * halfLength, -halfGoalWidth, 0],
            [side * (halfLength + goalDepth), -halfGoalWidth, 0],
            [side * (halfLength + goalDepth), halfGoalWidth, 0],
            [side * halfLength, halfGoalWidth, 0],
          ],
          "#40cfff",
          2.1,
          [],
          true,
        );
      }
    }

    // Live detections, projected at their own reported height so a robot
    // marker sits on the robot rather than on the carpet beneath it.
    const robotRadius = valueNumber(fieldDrawn["max_robot_radius"], 90);
    for (const robot of visibleRobots) {
      const centre = projectFieldPoint([robot.x, robot.y, robot.height]);
      if (!centre) continue;
      const edge = projectFieldPoint([robot.x + robotRadius, robot.y, robot.height]);
      const drawn = edge ? Math.max(4, Math.abs(edge[0] - centre[0])) : 8;
      const stale = isStale(robot);
      context.globalAlpha = stale ? 0.4 : 1;

      context.beginPath();
      context.arc(centre[0], centre[1], drawn, 0, Math.PI * 2);
      context.fillStyle = robot.team === "blue" ? "#4aa3e8" : "#e5be22";
      context.fill();
      context.lineWidth = 2;
      context.strokeStyle = "rgba(0, 0, 0, 0.8)";
      context.stroke();

      const nose = projectFieldPoint([
        robot.x + Math.cos(robot.orientation) * robotRadius * 1.4,
        robot.y + Math.sin(robot.orientation) * robotRadius * 1.4,
        robot.height,
      ]);
      if (nose) {
        context.beginPath();
        context.moveTo(centre[0], centre[1]);
        context.lineTo(nose[0], nose[1]);
        context.strokeStyle = "rgba(0, 0, 0, 0.8)";
        context.lineWidth = 3;
        context.stroke();
      }

      context.fillStyle = "#0d1a12";
      context.font = `700 ${String(Math.max(9, drawn))}px Inter, system-ui, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(robot.id), centre[0], centre[1]);
      context.globalAlpha = 1;
    }

    for (const ball of balls) {
      const centre = projectFieldPoint([ball.x ?? 0, ball.y ?? 0, ball.z ?? 0]);
      if (!centre) continue;
      context.beginPath();
      context.arc(centre[0], centre[1], 5, 0, Math.PI * 2);
      context.fillStyle = "#e3732f";
      context.fill();
      context.lineWidth = 1.5;
      context.strokeStyle = "#ffffff";
      context.stroke();
    }

    // The tuned corner pixels themselves, so a bad calibration is obvious.
    const inputCorners =
      geometryConfig["outer_line_corners"] ?? geometryConfig["line_corners"];
    if (Array.isArray(inputCorners)) {
      for (const corner of inputCorners) {
        if (!Array.isArray(corner) || corner.length < 2) continue;
        const x = valueNumber(corner[0], Number.NaN);
        const y = valueNumber(corner[1], Number.NaN);
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        context.beginPath();
        context.arc(x, y, 5, 0, Math.PI * 2);
        context.fillStyle = "#ff5b55";
        context.fill();
        context.strokeStyle = "#ffffff";
        context.lineWidth = 1.5;
        context.stroke();
      }
    }
  }

  /** Draw the field and everything on it, top-down, in field coordinates. */
  function drawField(): void {
    const canvas = fieldCanvas;
    if (!canvas || !isCombined) return;
    const context = canvas.getContext("2d");
    if (!context) return;

    const ratio = window.devicePixelRatio || 1;
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (width === 0 || height === 0) return;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);

    const styles = getComputedStyle(canvas);
    const carpet = styles.getPropertyValue("--field-carpet").trim() || "#17612f";
    const paint = styles.getPropertyValue("--field-paint").trim() || "#ffffff";

    context.fillStyle = carpet;
    context.fillRect(0, 0, width, height);

    const length = Number(fieldDrawn["field_length"] ?? 0);
    const fieldWidth = Number(fieldDrawn["field_width"] ?? 0);
    if (!length || !fieldWidth) {
      context.fillStyle = styles.getPropertyValue("--text-muted").trim() || "#7d9188";
      context.font = "13px Inter, system-ui, sans-serif";
      context.textAlign = "center";
      context.fillText("Waiting for field geometry", width / 2, height / 2);
      return;
    }

    const boundary = Number(fieldDrawn["boundary_width"] ?? 0);
    const totalLength = length + boundary * 2;
    const totalWidth = fieldWidth + boundary * 2;
    const scale = Math.min(width / totalLength, height / totalWidth) * 0.97;
    const toX = (x: number) => width / 2 + x * scale;
    const toY = (y: number) => height / 2 - y * scale;

    // The playing surface inside the boundary, a touch brighter than the
    // surround so the run-off area is legible without an extra outline.
    context.fillStyle = styles.getPropertyValue("--field-inner").trim() || "#1c7a3e";
    context.fillRect(
      toX(-length / 2),
      toY(fieldWidth / 2),
      length * scale,
      fieldWidth * scale,
    );

    context.strokeStyle = paint;
    context.lineWidth = Math.max(1, Number(fieldDrawn["line_thickness"] ?? 10) * scale);
    context.lineCap = "round";

    for (const line of fieldDrawn.field_lines ?? []) {
      context.beginPath();
      context.moveTo(toX(line.p1?.x ?? 0), toY(line.p1?.y ?? 0));
      context.lineTo(toX(line.p2?.x ?? 0), toY(line.p2?.y ?? 0));
      context.stroke();
    }

    for (const arc of fieldDrawn.field_arcs ?? []) {
      context.beginPath();
      context.arc(
        toX(arc.center?.x ?? 0),
        toY(arc.center?.y ?? 0),
        (arc.radius ?? 0) * scale,
        -(arc.a2 ?? 0),
        -(arc.a1 ?? 0),
      );
      context.stroke();
    }

    // The wrapper only generates markings the carpet actually has, so anything
    // switched off in optional_field_lines is absent from field_lines. Draw
    // those dashed from the dimensions instead: the regulation field is useful
    // for orientation, and dashing keeps it honest about what is painted.
    const halfLength = length / 2;
    const halfFieldWidth = fieldWidth / 2;
    const painted = new Set(
      (fieldDrawn.field_lines ?? []).map((line) => line.name ?? ""),
    );
    context.save();
    context.setLineDash([6, 5]);
    context.lineWidth = Math.max(1, Number(fieldDrawn["line_thickness"] ?? 10) * scale);
    context.strokeStyle = paint;
    context.globalAlpha = 0.5;

    const dashedLine = (x1: number, y1: number, x2: number, y2: number): void => {
      context.beginPath();
      context.moveTo(toX(x1), toY(y1));
      context.lineTo(toX(x2), toY(y2));
      context.stroke();
    };

    if (!painted.has("HalfwayLine")) dashedLine(0, -halfFieldWidth, 0, halfFieldWidth);
    if (!painted.has("CenterLine")) dashedLine(-halfLength, 0, halfLength, 0);

    const centreRadius = Number(fieldDrawn["center_circle_radius"] ?? 0);
    const hasCircle = (fieldDrawn.field_arcs ?? []).some(
      (arc) => arc.name === "CenterCircle",
    );
    if (!hasCircle && centreRadius > 0) {
      context.beginPath();
      context.arc(toX(0), toY(0), centreRadius * scale, 0, Math.PI * 2);
      context.stroke();
    }

    const penaltyDepth = Number(fieldDrawn["penalty_area_depth"] ?? 0);
    const penaltyWidth = Number(fieldDrawn["penalty_area_width"] ?? 0);
    if (!painted.has("LeftPenaltyStretch") && penaltyDepth > 0 && penaltyWidth > 0) {
      const halfPenalty = penaltyWidth / 2;
      for (const side of [-1, 1]) {
        const outer = side * halfLength;
        const inner = side * (halfLength - penaltyDepth);
        dashedLine(outer, -halfPenalty, inner, -halfPenalty);
        dashedLine(inner, -halfPenalty, inner, halfPenalty);
        dashedLine(inner, halfPenalty, outer, halfPenalty);
      }
    }
    context.restore();

    // Goals, which the geometry describes by size rather than as lines.
    const goalWidth = Number(fieldDrawn["goal_width"] ?? 0);
    const goalDepth = Number(fieldDrawn["goal_depth"] ?? 0);
    if (goalWidth && goalDepth) {
      context.strokeStyle = styles.getPropertyValue("--goal").trim() || "#40cfff";
      context.lineWidth = Math.max(1.5, 20 * scale);
      for (const side of [-1, 1]) {
        const x = (side * length) / 2;
        context.beginPath();
        context.moveTo(toX(x), toY(-goalWidth / 2));
        context.lineTo(toX(x + side * goalDepth), toY(-goalWidth / 2));
        context.lineTo(toX(x + side * goalDepth), toY(goalWidth / 2));
        context.lineTo(toX(x), toY(goalWidth / 2));
        context.stroke();
      }
    }

    const robotRadius = Number(fieldDrawn["max_robot_radius"] ?? 90);
    // An SSL robot is a cylinder with the front flattened for the dribbler,
    // so draw the same shape rather than a plain disc: the flat edge shows
    // heading without needing a separate marker.
    const dribblerOffset = Math.min(73, robotRadius * 0.81);
    const halfFront = Math.acos(dribblerOffset / robotRadius);

    for (const robot of visibleRobots) {
      const stale = isStale(robot);
      const base =
        robot.team === "blue"
          ? styles.getPropertyValue("--blue").trim() || "#4aa3e8"
          : styles.getPropertyValue("--yellow").trim() || "#e5be22";
      const radius = robotRadius * scale;
      context.globalAlpha = stale ? 0.4 : 1;

      // Canvas y grows downwards while field y grows up, so angles are
      // negated to keep the drawn heading matching the reported one.
      const heading = -robot.orientation;
      context.beginPath();
      context.arc(
        toX(robot.x),
        toY(robot.y),
        radius,
        heading + halfFront,
        heading - halfFront + Math.PI * 2,
      );
      context.closePath();
      context.fillStyle = base;
      context.fill();
      context.lineWidth = Math.max(1, radius * 0.12);
      context.strokeStyle = stale ? "#8fa39a" : "#0d1a12";
      context.stroke();

      context.fillStyle = "#0d1a12";
      context.font = `700 ${String(Math.max(8, radius * 1.1))}px Inter, system-ui, sans-serif`;
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(robot.id), toX(robot.x), toY(robot.y));
      context.globalAlpha = 1;
    }

    const ballRadius = Number(fieldDrawn["ball_radius"] ?? 21.5);
    for (const ball of balls) {
      // A real ball is 21.5 mm, only a few pixels at field scale, so enforce a
      // floor: it has to stay findable on a full-field view.
      const drawn = Math.max(3.5, ballRadius * scale);
      context.beginPath();
      context.arc(toX(ball.x ?? 0), toY(ball.y ?? 0), drawn, 0, Math.PI * 2);
      context.fillStyle = styles.getPropertyValue("--orange").trim() || "#e3732f";
      context.fill();
      context.lineWidth = Math.max(1, drawn * 0.3);
      context.strokeStyle = "#0d1a12";
      context.stroke();
    }
  }

  function loadSnapshots(): void {
    fetch(api("/snapshots"))
      .then((response) => response.json())
      .then((data: Snapshot[]) => (snapshots = data))
      .catch(() => undefined);
  }

  function loadConfig(): void {
    fetch(api("/api/config"))
      .then((response) => response.json())
      .then((data: ConfigResponse) => (configPayload = data))
      .catch(() => undefined);
  }

  function loadHealth(): void {
    fetch(api("/api/health"))
      .then((response) => response.json())
      .then((data: HealthResponse) => (health = data))
      .catch(() => undefined);
  }

  function loadGeometry(): void {
    fetch(api("/api/geometry"))
      .then((response) => response.json())
      .then((data: GeometryResponse) => (geometryFile = data))
      .catch(() => undefined);
  }

  function loadField(): void {
    fetch(api("/api/field"))
      .then((response) => response.json())
      .then((data: GeometryData) => (fieldHttp = data))
      .catch(() => undefined);
  }

  function loadCameras(): void {
    fetch(api("/api/cameras"))
      .then((response) => response.json())
      .then((data: CamerasPayload) => (camerasHttp = data))
      .catch(() => undefined);
  }

  /** Pull everything again right now.
   *
   * Switching camera or view changes which image URL is on screen, and that
   * URL may already be in the browser cache from the last time it was shown.
   * Moving the cache-busting stamp forward on the interaction itself means the
   * first frame after a click is fetched fresh rather than being whatever was
   * cached, and the surrounding panels update with it instead of lagging by a
   * poll interval.
   */
  function refreshNow(): void {
    cacheBuster = Date.now();
    loadSnapshots();
    loadConfig();
    loadHealth();
    loadGeometry();
    loadField();
    loadCameras();
  }

  /** Reload the page without letting the browser reuse what it has cached.
   *
   * location.reload() revalidates but will still happily reuse a cached
   * index.html, and index.html is what names the fingerprinted bundle - which
   * is exactly how an old build survives an ordinary refresh. Clearing the
   * Cache API and then navigating to a URL the browser cannot have seen
   * before forces the whole thing to come from the server again.
   */
  async function hardReload(): Promise<void> {
    try {
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch {
      // Cache API blocked or unavailable; the cache-busting navigation below
      // is the part that actually matters.
    }
    const url = new URL(location.href);
    url.searchParams.set("_", String(Date.now()));
    location.replace(url.toString());
  }

  onMount(() => {
    loadSnapshots();
    loadConfig();
    loadHealth();
    loadGeometry();
    loadField();
    loadCameras();
    const snapshotTimer = setInterval(loadSnapshots, 3000);
    const configTimer = setInterval(loadConfig, 5000);
    const healthTimer = setInterval(loadHealth, 1000);
    const geometryTimer = setInterval(loadGeometry, 5000);
    const camerasTimer = setInterval(loadCameras, 1000);
    const fieldTimer = setInterval(loadField, 1000);
    const imageTimer = setInterval(() => {
      cacheBuster = Date.now();
    }, IMAGE_REFRESH_MS);
    const resize = () => {
      drawField();
    };
    window.addEventListener("resize", resize);

    return () => {
      clearInterval(snapshotTimer);
      clearInterval(configTimer);
      clearInterval(healthTimer);
      clearInterval(geometryTimer);
      clearInterval(camerasTimer);
      clearInterval(fieldTimer);
      clearInterval(imageTimer);
      window.removeEventListener("resize", resize);
    };
  });
</script>

<svelte:head>
  <title>SSL Processor UI</title>
</svelte:head>

<main>
  <header class="topbar">
    <div class="identity">
      <span class="product-mark">VP</span>
      <h1>SSL Processor UI</h1>
    </div>
    <div class="header-status">
      <span class="metric">Last update <strong>{clockText(lastUpdate)}</strong></span>
      <span class="metric">Frame <strong>{number(activeFrame?.frame_number)}</strong></span>
      <span class:healthy={$connectionState === "open"} class="status-badge">
        <span class="status-dot"></span>
        Bus {$connectionState}
      </span>
    </div>
  </header>

  <div class="toolbar">
    <label class="camera-picker">
      <span>Camera</span>
      <select bind:value={selectedCamera} onchange={refreshNow}>
        <option value="combine">Combined ({combined.online}/{combined.count})</option>
        {#each cameraList as camera (camera.camera_id)}
          <option value={String(camera.camera_id)}>
            {camera.camera_id} &middot; {camera.name}
          </option>
        {/each}
      </select>
    </label>

    <span class="metric">
      {isCombined ? "Combined latency" : "Latency"}
      <strong>{number(isCombined ? combined.latency_ms : selectedStatus?.latency_ms, 1)} ms</strong>
    </span>
    <span class="metric">
      {isCombined ? "Total rate" : "Camera rate"}
      <strong>{number(isCombined ? combined.fps : selectedStatus?.fps, 1)} fps</strong>
    </span>
    <span class:healthy={processingHealthy} class="status-badge">
      <span class="status-dot"></span>
      Processing {processingHealthy ? "healthy" : "degraded"}
    </span>

    <button class="action" title="Refetch everything now" onclick={refreshNow}>
      Refresh
    </button>
    <button
      class="action"
      title="Clear cached files and reload the page from the server"
      onclick={() => {
        void hardReload();
      }}
    >
      Clear cache
    </button>

    <span class="config-chip" title={text(health?.vision_config)}>
      <span class="chip-label">config</span>
      <strong>{baseName(health?.vision_config ?? configPayload?.path)}</strong>
    </span>
    <span class="config-chip" title={text(health?.geometry_config)}>
      <span class="chip-label">geometry</span>
      <strong>{baseName(health?.geometry_config)}</strong>
    </span>
  </div>

  <div class="workspace">
    <div class="primary-column">
      <section class="viewer-panel">
        <div class="section-heading">
          <div>
            <h2>{isCombined ? "Field" : `Camera ${String(selectedCameraId)} diagnostics`}</h2>
            <p>
              {isCombined
                ? "Robots, ball and geometry as published by the wrapper"
                : "Live diagnostic snapshots from this processor"}
            </p>
          </div>
          {#if isCombined}
            <span class="small-state">{visibleRobots.length} robots &middot; {balls.length} balls</span>
          {:else}
            <span class="small-state">{health?.latest_snapshot_age_s ?? "--"} s ago</span>
          {/if}
        </div>

        <nav class="view-tabs" aria-label="Diagnostic view">
          {#each cameraViews() as view (view)}
            <button
              class:active={!isCombined && view === selectedView}
              disabled={isCombined}
              title={isCombined ? "Select a camera to view diagnostics" : viewLabel(view)}
              onclick={() => {
                selectedView = view;
                refreshNow();
              }}
            >
              {viewLabel(view)}
            </button>
          {/each}
        </nav>

        <div class="image-stage" class:field-stage={isCombined}>
          {#if isCombined}
            <canvas bind:this={fieldCanvas} aria-label="Top-down field view"></canvas>
          {:else if selectedView === "overlay"}
            <div class="overlay-stage">
              <img
                src={api(`/snapshot/${String(selectedCameraId)}/raw?t=${String(cacheBuster)}`)}
                alt={`Camera ${String(selectedCameraId)} field overlay`}
                onload={drawFieldOverlay}
              />
              <canvas bind:this={overlayCanvas} aria-label="Projected field geometry and detections"></canvas>
              <div class="overlay-legend">
                <span class="field-key">Field</span>
                <span class="goal-key">Goals</span>
                <span class="marking-key">Markings</span>
                <span class="boundary-key">Boundary</span>
                <span class="corner-key">Corners</span>
              </div>
            </div>
          {:else if currentSnapshot()}
            <img
              src={api(`/snapshot/${String(selectedCameraId)}/${selectedView}?t=${String(cacheBuster)}`)}
              alt={`Camera ${String(selectedCameraId)} ${viewLabel(selectedView)}`}
            />
          {:else}
            <p>No snapshots available for this camera.</p>
          {/if}
        </div>
      </section>

      <section class="panel">
        <div class="section-heading compact">
          <h2>Camera input</h2>
          <span class="section-tag">{baseName(health?.vision_config ?? configPayload?.path)}</span>
        </div>
        <dl class="property-grid wide">
          <div><dt>Driver</dt><dd>{text(cameraConfig["driver"])}</dd></div>
          <div><dt>Device</dt><dd>{text(cameraConfig["path"])}</dd></div>
          <div><dt>Capture</dt><dd>{number(cameraConfig["width"])} x {number(cameraConfig["height"])}</dd></div>
          <div><dt>Processing</dt><dd>{number(cameraConfig["output_width"])} x {number(cameraConfig["output_height"])}</dd></div>
          <div><dt>Configured rate</dt><dd>{number(cameraConfig["fps"])} fps</dd></div>
          <div><dt>Format</dt><dd>{text(cameraConfig["fourcc"])}</dd></div>
          <div><dt>Exposure</dt><dd>{number(cameraConfig["exposure"], 1)}</dd></div>
          <div><dt>Gain</dt><dd>{number(cameraConfig["gain"], 1)}</dd></div>
          <div><dt>Gamma</dt><dd>{number(cameraConfig["gamma"], 1)}</dd></div>
          <div><dt>White balance</dt><dd>{text(cameraConfig["white_balance"])}</dd></div>
          <div><dt>Left crop</dt><dd>{text(cameraConfig["crop_left_half"])}</dd></div>
          <div><dt>Cameras on field</dt><dd>{number(geometryConfig["camera_amount"])}</dd></div>
        </dl>
      </section>

      <section class="panel">
        <div class="section-heading compact"><h2>Solved camera model</h2></div>
        <dl class="property-grid wide">
          <div><dt>Focal length</dt><dd>{number(cameraCalibration["focal_length"], 2)}</dd></div>
          <div><dt>Principal point</dt><dd>{number(cameraCalibration["principal_point_x"], 1)}, {number(cameraCalibration["principal_point_y"], 1)}</dd></div>
          <div><dt>Image size</dt><dd>{number(cameraCalibration["pixel_image_width"])} x {number(cameraCalibration["pixel_image_height"])}</dd></div>
          <div><dt>Distortion</dt><dd>{number(cameraCalibration["distortion"], 4)}</dd></div>
          <div><dt>Camera height</dt><dd>{number(geometryConfig["camera_height"], 0)} mm</dd></div>
          <div><dt>Translation</dt><dd>{number(cameraCalibration["tx"], 0)}, {number(cameraCalibration["ty"], 0)}, {number(cameraCalibration["tz"], 0)}</dd></div>
        </dl>
        <div class="corner-list">
          <span>Field corners px</span>
          <code>{JSON.stringify(geometryConfig["line_corners"] ?? [])}</code>
        </div>
      </section>

      <section class="panel">
        <div class="section-heading compact"><h2>Detection thresholds</h2></div>
        <dl class="property-grid wide">
          <div><dt>Circularity</dt><dd>{number(thresholdConfig["circularity"], 1)}</dd></div>
          <div><dt>Score</dt><dd>{number(thresholdConfig["score"], 1)}</dd></div>
          <div><dt>Confidence</dt><dd>{number(thresholdConfig["min_confidence"], 2)}</dd></div>
          <div><dt>Blob limit</dt><dd>{number(thresholdConfig["blobs"])}</dd></div>
          <div><dt>Edge distance</dt><dd>{number(thresholdConfig["min_cam_edge_distance"])}</dd></div>
          <div><dt>Clipping</dt><dd>{number(thresholdConfig["clipping_tolerance"], 1)}</dd></div>
        </dl>
      </section>

      <section class="panel">
        <div class="section-heading compact"><h2>Color references</h2></div>
        <div class="color-list">
          {#each colorNames as name (name)}
            <div class="color-row">
              <span class="swatch" style={`background: ${colorCss(colorConfig[name])}`}></span>
              <span>{name}</span>
              <code>{JSON.stringify(colorConfig[name] ?? [])}</code>
            </div>
          {/each}
        </div>
      </section>
    </div>

    <aside class="inspector">
      <section class="panel">
        <div class="section-heading compact">
          <h2>Cameras active</h2>
          <span class="section-tag">{combined.online}/{combined.count} online</span>
        </div>
        <div class="table-wrap">
          <table class="camera-table">
            <thead>
              <tr><th></th><th>Name</th><th>Address</th><th>Latency</th><th>FPS</th></tr>
            </thead>
            <tbody>
              {#each cameraList as camera (camera.camera_id)}
                <tr class:offline={!camera.online}>
                  <td><span class:online={camera.online} class="service-indicator"></span>{camera.camera_id}</td>
                  <td class="name-cell" title={camera.name}>{camera.name}</td>
                  <td class="mono">{camera.address}</td>
                  <td class="mono">{number(camera.latency_ms, 1)} ms</td>
                  <td class="mono">{number(camera.fps, 1)}</td>
                </tr>
              {:else}
                <tr><td colspan="5" class="empty-row">No cameras have reported yet</td></tr>
              {/each}
            </tbody>
            {#if cameraList.length > 0}
              <tfoot>
                <tr>
                  <td colspan="3">Combined</td>
                  <td class="mono">{number(combined.latency_ms, 1)} ms</td>
                  <td class="mono">{number(combined.fps, 1)}</td>
                </tr>
              </tfoot>
            {/if}
          </table>
        </div>
        <div class="service-list">
          {#each Object.entries(health?.services ?? {}) as [name, service] (name)}
            <div class="service-row">
              <span class:online={service.running} class="service-indicator"></span>
              <span>{name.replaceAll("_", " ")}</span>
              <code>{service.pid ?? "stopped"}</code>
            </div>
          {/each}
        </div>
      </section>

      <section class="panel">
        <div class="section-heading compact"><h2>Field geometry</h2></div>
        <dl class="property-grid">
          <div><dt>Length</dt><dd>{number(fieldFile["field_length"])} mm</dd></div>
          <div><dt>Width</dt><dd>{number(fieldFile["field_width"])} mm</dd></div>
          <div><dt>Center circle</dt><dd>{number(fieldFile["center_circle_radius"])} mm</dd></div>
          <div><dt>Penalty area</dt><dd>{number(fieldFile["penalty_area_depth"])} x {number(fieldFile["penalty_area_width"])}</dd></div>
          <div><dt>Goal</dt><dd>{number(fieldFile["goal_width"])} x {number(fieldFile["goal_depth"])} mm</dd></div>
          <div><dt>Boundary</dt><dd>{number(fieldFile["boundary_width"])} mm</dd></div>
          <div><dt>Line thickness</dt><dd>{number(fieldFile["line_thickness"])} mm</dd></div>
          <div><dt>Robot radius</dt><dd>{number(fieldFile["max_robot_radius"], 0)} mm</dd></div>
          <div><dt>Ball radius</dt><dd>{number(fieldFile["ball_radius"], 1)} mm</dd></div>
          <div><dt>Robot height</dt><dd>{robotHeightText()}</dd></div>
        </dl>
      </section>

      <section class="panel">
        <div class="section-heading compact">
          <h2>Detected objects</h2>
          <span class="section-tag">{visibleRobots.length + balls.length} tracked</span>
        </div>

        <div class="object-block">
          <div class="object-head"><span>Ball</span><span class="qty">{balls.length}</span></div>
          {#if balls.length > 0}
            <table class="object-table">
              <thead><tr><th>Cam</th><th>x</th><th>y</th><th>z</th><th>conf</th></tr></thead>
              <tbody>
                {#each balls as ball, index (`ball-${String(ball.camera)}-${String(index)}`)}
                  <tr>
                    <td class="mono">{ball.camera}</td>
                    <td class="mono">{number(ball.x, 0)}</td>
                    <td class="mono">{number(ball.y, 0)}</td>
                    <td class="mono">{number(ball.z, 0)}</td>
                    <td class="mono">{number(ball.confidence, 2)}</td>
                  </tr>
                {/each}
              </tbody>
            </table>
          {:else}
            <p class="none">No ball detected</p>
          {/if}
        </div>

        {#each [{ team: "yellow", robots: yellowRobots }, { team: "blue", robots: blueRobots }] as group (group.team)}
          <div class="object-block">
            <div class="object-head">
              <span class="team-dot {group.team}"></span>
              <span class="team-name">{group.team}</span>
              <span class="qty">{group.robots.length}</span>
            </div>
            {#if group.robots.length > 0}
              <div class="robot-grid">
                {#each group.robots as robot (robot.key)}
                  <div class="robot-chip {group.team}" class:stale={isStale(robot)}>
                    <span class="robot-id">{robot.id}</span>
                    <span class="robot-pos">{number(robot.x, 0)}, {number(robot.y, 0)}</span>
                    <span class="robot-meta">{number(robot.confidence, 2)} &middot; {number(robot.height, 0)}mm</span>
                  </div>
                {/each}
              </div>
            {:else}
              <p class="none">None detected</p>
            {/if}
          </div>
        {/each}
      </section>

      <section class="panel">
        <div class="section-heading compact"><h2>Network output</h2></div>
        <dl class="property-grid">
          <div><dt>Vision multicast</dt><dd>{text(networkConfig["vision_ip"])}:{number(networkConfig["vision_port"])}</dd></div>
          <div><dt>Game Controller</dt><dd>{text(networkConfig["gc_ip"])}:{number(networkConfig["gc_port"])}</dd></div>
          <div><dt>Debug stream</dt><dd>{text(streamConfig["ip_base_prefix"])}{number(streamConfig["ip_base_end"])}:{number(streamConfig["port"])}</dd></div>
          <div><dt>Stream active</dt><dd>{text(streamConfig["active"])}</dd></div>
        </dl>
      </section>
    </aside>
  </div>

  <footer>
    <span>camera config <code>{text(health?.vision_config ?? configPayload?.path)}</code></span>
    <span>geometry <code>{text(health?.geometry_config)}</code></span>
    <span>backend uptime {number(health?.uptime_s)} s</span>
  </footer>
</main>

<style>
  /* Dark operator theme. Every colour resolves through a token below, so
     re-theming means editing this block only. */
  :global(:root) {
    color-scheme: dark;

    --bg: #0e1512;
    --surface: #161f1b;
    --surface-raised: #1b2621;
    --surface-sunken: #0b100e;
    --surface-hover: #223029;

    --border: #2a3a33;
    --border-strong: #384a42;
    --border-subtle: #202c27;

    --text: #e6efea;
    --text-secondary: #9aada4;
    --text-muted: #74877e;

    --accent: #3fbb7d;
    --accent-strong: #2f8f5e;
    --accent-soft: #bfe8cf;

    --ok: #4ecb8a;
    --error: #e0645e;

    --blue: #4aa3e8;
    --blue-soft: #123049;
    --yellow: #e5be22;
    --yellow-soft: #3a2f08;
    --orange: #e3732f;
    --goal: #40cfff;

    /* Field render follows ssl-vision-client: green carpet, white markings.
       The surround is a shade darker so the playing area reads as distinct
       without drawing an extra border. */
    --field-carpet: #17612f;
    --field-inner: #1c7a3e;
    --field-paint: #ffffff;
  }

  :global(*) {
    box-sizing: border-box;
  }

  :global(html) {
    background: var(--bg);
  }

  :global(body) {
    margin: 0;
    min-width: 320px;
    color: var(--text);
    background: var(--bg);
    font-family: Inter, "Segoe UI", system-ui, sans-serif;
  }

  :global(button),
  :global(select) {
    font: inherit;
  }

  main {
    min-height: 100vh;
  }

  .topbar {
    min-height: 54px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 20px;
    padding: 8px 18px;
    color: var(--text);
    background: var(--surface-sunken);
    border-bottom: 2px solid var(--accent-strong);
  }

  .identity,
  .header-status,
  .section-heading,
  .service-row,
  .color-row {
    display: flex;
    align-items: center;
  }

  .identity {
    gap: 11px;
    min-width: 0;
  }

  .product-mark {
    width: 32px;
    height: 32px;
    display: grid;
    place-items: center;
    flex: 0 0 32px;
    border: 1px solid var(--accent-strong);
    border-radius: 4px;
    color: var(--accent-soft);
    font-weight: 750;
    font-size: 13px;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  h1 {
    font-size: 15px;
    line-height: 1.25;
    font-weight: 700;
    letter-spacing: 0.01em;
  }

  .section-heading p {
    margin-top: 2px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .header-status {
    justify-content: flex-end;
    gap: 8px;
    flex-wrap: wrap;
  }

  /* ---------- toolbar ---------- */

  .toolbar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 9px;
    padding: 9px 18px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }

  .camera-picker {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .camera-picker select {
    height: 30px;
    padding: 0 8px;
    color: var(--text);
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }

  .action {
    min-height: 28px;
    padding: 4px 11px;
    border-radius: 4px;
    color: var(--text);
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    font-size: 12px;
    cursor: pointer;
  }

  .action:hover {
    border-color: var(--accent-strong);
    background: var(--surface-hover);
  }

  .action:focus-visible,
  .camera-picker select:focus-visible,
  .view-tabs button:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 1px;
  }

  .status-badge,
  .metric,
  .small-state,
  .section-tag,
  .config-chip {
    min-height: 28px;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 9px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
  }

  .status-badge,
  .metric,
  .config-chip {
    color: var(--text-secondary);
    border: 1px solid var(--border-strong);
    background: var(--surface-raised);
  }

  .metric strong,
  .config-chip strong {
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .chip-label {
    color: var(--text-muted);
    font-size: 10px;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .config-chip strong {
    max-width: 18ch;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .status-dot,
  .service-indicator {
    width: 8px;
    height: 8px;
    flex: 0 0 8px;
    border-radius: 50%;
    background: var(--error);
  }

  .status-badge.healthy .status-dot,
  .service-indicator.online {
    background: var(--ok);
  }

  /* ---------- layout ---------- */

  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1.75fr) minmax(340px, 1fr);
    gap: 11px;
    padding: 11px;
    align-items: start;
  }

  .primary-column,
  .inspector {
    min-width: 0;
    display: grid;
    gap: 11px;
  }

  .viewer-panel,
  .panel {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
    overflow: hidden;
  }

  .section-heading {
    min-height: 46px;
    justify-content: space-between;
    gap: 12px;
    padding: 9px 12px;
    border-bottom: 1px solid var(--border);
  }

  .section-heading.compact {
    min-height: 40px;
    padding-block: 8px;
  }

  h2 {
    font-size: 13px;
    line-height: 1.25;
    font-weight: 700;
  }

  .small-state,
  .section-tag {
    min-height: 24px;
    color: var(--text-secondary);
    background: var(--surface-raised);
    border: 1px solid var(--border);
    max-width: 22ch;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* ---------- viewer ---------- */

  .view-tabs {
    min-height: 38px;
    display: flex;
    gap: 2px;
    padding: 4px 6px;
    overflow-x: auto;
    background: var(--surface-raised);
    border-bottom: 1px solid var(--border);
  }

  .view-tabs button {
    height: 30px;
    flex: 0 0 auto;
    padding: 0 10px;
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--text-secondary);
    background: transparent;
    cursor: pointer;
    font-size: 12px;
  }

  .view-tabs button:hover {
    color: var(--text);
    border-color: var(--border-strong);
    background: var(--surface-hover);
  }

  .view-tabs button.active {
    color: #ffffff;
    background: var(--accent-strong);
    border-color: var(--accent-strong);
  }

  .view-tabs button:disabled {
    color: var(--text-muted);
    background: transparent;
    border-color: transparent;
    cursor: not-allowed;
    opacity: 0.55;
  }

  .view-tabs button:disabled:hover {
    color: var(--text-muted);
    background: transparent;
    border-color: transparent;
  }

  .overlay-stage {
    position: relative;
    width: 100%;
    height: 100%;
  }

  .overlay-stage img,
  .overlay-stage canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: contain;
  }

  .overlay-stage canvas {
    pointer-events: none;
  }

  .overlay-legend {
    position: absolute;
    top: 8px;
    left: 8px;
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    padding: 5px 7px;
    color: var(--text);
    background: rgba(8, 12, 10, 0.82);
    border: 1px solid rgba(220, 232, 224, 0.28);
    border-radius: 3px;
    font-size: 10px;
  }

  .overlay-legend span::before {
    content: "";
    width: 12px;
    height: 2px;
    display: inline-block;
    margin-right: 4px;
    vertical-align: 3px;
    background: #f4f7f5;
  }

  .overlay-legend .goal-key::before {
    background: #40cfff;
  }

  .overlay-legend .marking-key::before {
    background: #ffd451;
  }

  .overlay-legend .boundary-key::before {
    background: #4dd8a0;
  }

  .overlay-legend .corner-key::before {
    height: 6px;
    width: 6px;
    border-radius: 50%;
    background: #ff5b55;
    vertical-align: 1px;
  }

  .image-stage {
    width: 100%;
    aspect-ratio: 16 / 9;
    display: grid;
    place-items: center;
    background: var(--surface-sunken);
    overflow: hidden;
  }

  .image-stage.field-stage {
    aspect-ratio: 2 / 1;
  }

  .image-stage img,
  .image-stage canvas {
    width: 100%;
    height: 100%;
    display: block;
    object-fit: contain;
  }

  .image-stage p {
    color: var(--text-muted);
    font-size: 13px;
  }

  /* ---------- tables ---------- */

  .table-wrap {
    width: 100%;
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
  }

  th,
  td {
    padding: 7px 9px;
    text-align: left;
    border-bottom: 1px solid var(--border-subtle);
    white-space: nowrap;
  }

  th {
    color: var(--text-secondary);
    background: var(--surface-raised);
    font-weight: 600;
    font-size: 10px;
    letter-spacing: 0.07em;
    text-transform: uppercase;
  }

  tbody tr:last-child td {
    border-bottom: 0;
  }

  .mono {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-variant-numeric: tabular-nums;
    font-size: 11px;
  }

  .camera-table td:first-child {
    display: flex;
    align-items: center;
    gap: 7px;
    font-variant-numeric: tabular-nums;
  }

  .camera-table .name-cell {
    max-width: 14ch;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .camera-table tr.offline td {
    color: var(--text-muted);
  }

  .camera-table tfoot td {
    border-top: 1px solid var(--border);
    color: var(--text-secondary);
    font-weight: 600;
  }

  .empty-row {
    height: 46px;
    text-align: center;
    color: var(--text-muted);
  }

  /* ---------- detected objects ---------- */

  .object-block {
    padding: 9px 12px;
    border-bottom: 1px solid var(--border-subtle);
  }

  .object-block:last-child {
    border-bottom: 0;
  }

  .object-head {
    display: flex;
    align-items: center;
    gap: 7px;
    margin-bottom: 7px;
    font-size: 12px;
    font-weight: 600;
  }

  .team-name {
    text-transform: capitalize;
  }

  .qty {
    margin-left: auto;
    padding: 1px 7px;
    border-radius: 999px;
    background: var(--surface-raised);
    border: 1px solid var(--border);
    color: var(--text-secondary);
    font-size: 11px;
    font-variant-numeric: tabular-nums;
  }

  .object-table th,
  .object-table td {
    padding: 5px 7px;
  }

  .none {
    color: var(--text-muted);
    font-size: 11px;
  }

  /* Fixed-width chips rather than a scrolling list: the point is to see at a
     glance which robots exist, so the block grows with the count instead of
     hiding entries behind a scrollbar. */
  .robot-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(84px, 1fr));
    gap: 5px;
  }

  .robot-chip {
    display: grid;
    gap: 1px;
    padding: 5px 7px;
    border-radius: 4px;
    border: 1px solid var(--border-strong);
    background: var(--surface-raised);
    transition: opacity 120ms linear;
  }

  .robot-chip.blue {
    border-color: var(--blue);
    background: var(--blue-soft);
  }

  .robot-chip.yellow {
    border-color: var(--yellow);
    background: var(--yellow-soft);
  }

  /* Lost, but not yet dropped: grey it in place so the slot stays put. */
  .robot-chip.stale {
    border-color: var(--border-strong);
    background: var(--surface-raised);
    opacity: 0.45;
  }

  .robot-id {
    font-size: 13px;
    font-weight: 700;
    font-variant-numeric: tabular-nums;
  }

  .robot-pos,
  .robot-meta {
    font-family: ui-monospace, "SF Mono", Menlo, monospace;
    font-size: 9.5px;
    color: var(--text-secondary);
  }

  .robot-meta {
    color: var(--text-muted);
  }

  .team-dot {
    width: 9px;
    height: 9px;
    display: inline-block;
    border-radius: 50%;
  }

  .team-dot.blue {
    background: var(--blue);
  }

  .team-dot.yellow {
    background: var(--yellow);
  }

  /* ---------- property grids ---------- */

  .service-list,
  .color-list {
    padding: 4px 12px 8px;
  }

  .service-row,
  .color-row {
    min-height: 30px;
    gap: 9px;
    border-bottom: 1px solid var(--border-subtle);
    font-size: 12px;
  }

  .service-row:last-child,
  .color-row:last-child {
    border-bottom: 0;
  }

  .service-row span:nth-child(2),
  .color-row span:nth-child(2) {
    text-transform: capitalize;
  }

  .service-row code,
  .color-row code {
    margin-left: auto;
    color: var(--text-muted);
    font-size: 11px;
  }

  .property-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    margin: 0;
    padding: 6px 12px 10px;
  }

  .property-grid.wide {
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  }

  .property-grid div {
    min-width: 0;
    min-height: 41px;
    padding: 6px 8px 6px 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  dt {
    margin-bottom: 3px;
    color: var(--text-muted);
    font-size: 10px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  dd {
    margin: 0;
    min-width: 0;
    color: var(--text);
    font-size: 12px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  .corner-list {
    display: grid;
    gap: 5px;
    padding: 0 12px 11px;
    color: var(--text-muted);
    font-size: 10px;
    text-transform: uppercase;
  }

  .corner-list code {
    color: var(--text-secondary);
    font-size: 10px;
    line-height: 1.45;
    text-transform: none;
    overflow-wrap: anywhere;
  }

  .swatch {
    width: 18px;
    height: 18px;
    flex: 0 0 18px;
    border: 1px solid var(--border-strong);
    border-radius: 3px;
  }

  footer {
    min-height: 34px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 16px;
    padding: 8px 18px;
    color: var(--text-muted);
    font-size: 11px;
    border-top: 1px solid var(--border);
  }

  footer code {
    color: var(--text-secondary);
    font-size: 11px;
  }

  @media (max-width: 1100px) {
    .workspace {
      grid-template-columns: 1fr;
    }
  }

  @media (max-width: 680px) {
    .topbar,
    .toolbar {
      align-items: flex-start;
      flex-direction: column;
    }

    .workspace {
      padding: 8px;
    }

    .section-heading {
      align-items: flex-start;
      flex-direction: column;
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .robot-chip {
      transition: none;
    }
  }
</style>

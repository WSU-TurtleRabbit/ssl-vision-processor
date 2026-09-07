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
  // Seed from the URL so a particular camera and view can be linked to, and
  // so a reload lands back where you were rather than on the combined field.
  const initialParams = new URLSearchParams(location.search);
  // "" means no camera chosen yet, which only happens before the first one
  // reports. There is no combined mode any more: this page shows one camera's
  // processing at a time, which is what the diagnostic views describe.
  let selectedCamera = $state<string>(initialParams.get("camera") ?? "");
  let selectedView = $state(initialParams.get("view") ?? "overlay");
  // The C++ side rewrites the debug images every debug_stream_interval_ms
  // (100 ms on this setup). Refreshing them once a second left the picture
  // visibly stale under an overlay that moves at the detection rate, so pull
  // them several times a second instead.
  const IMAGE_REFRESH_MS = 200;
  // Two clocks on purpose. drawClock advances every animation frame and is
  // read only inside the draw functions, so trail fades stay smooth. `now` is
  // reactive and every derived list depends on it, so it ticks at a human
  // rate: driving it at display rate recomputed the robot and ball lists 60
  // times a second to produce the same answer.
  let drawClock = Date.now();
  let now = $state(Date.now());
  const REACTIVE_TICK_MS = 100;
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
  // Which bundle the server is serving, versus the one this page is running.
  // A rebuild changes the fingerprinted filename, so a difference means the
  // page is stale - which is otherwise invisible, since a browser will reuse
  // a cached index.html happily and nothing on screen says so.
  let servedBundle = $state<string | null>(null);
  let runningBundle: string | null = null;
  let updateAvailable = $derived(
    servedBundle !== null && runningBundle !== null && servedBundle !== runningBundle,
  );
  let camerasHttp = $state<CamerasPayload | null>(null);
  let overlayCanvas = $state<HTMLCanvasElement>();

  // Last frame that finished loading, per camera and view. Deliberately not
  // reactive: it is a render cache, read by the draw loop, and making it
  // reactive would re-run every derived value on each arriving frame.
  //
  // Without it the <img> went blank between fetches - the source changes
  // several times a second and each change restarts loading - and a failed
  // or 404 fetch cleared the picture entirely. Holding the last good frame
  // means the view degrades to "slightly stale" instead of "empty", and
  // switching back to a view you have already seen paints instantly.
  const frameCache: Record<string, HTMLImageElement> = {};

  /** Offscreen layers for the parts of a drawing that do not change per frame.
   *
   * The carpet, field lines and goals depend only on geometry and canvas size;
   * the projected overlay depends only on geometry and calibration. Redrawing
   * them every animation frame meant re-running the projection for every point
   * of every line 60 times a second to produce an identical picture. They are
   * rendered once into their own canvas and blitted afterwards, leaving only
   * the moving objects to draw per frame.
   */
  interface StaticLayer {
    canvas: HTMLCanvasElement;
    key: string;
  }

  const layers: Record<string, StaticLayer> = {};

  function staticLayer(
    name: string,
    key: string,
    width: number,
    height: number,
    render: (context: CanvasRenderingContext2D) => void,
  ): HTMLCanvasElement | null {
    if (width <= 0 || height <= 0) return null;
    const full = `${key}|${String(width)}x${String(height)}`;
    const existing = layers[name];
    if (existing?.key === full) return existing.canvas;

    const canvas = existing?.canvas ?? document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    context.clearRect(0, 0, width, height);
    render(context);
    layers[name] = { canvas, key: full };
    return canvas;
  }
  let inFlight = "";
  let lastPump = 0;

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

  // Every camera we could show something for. The roster is the good source
  // but it depends on one endpoint; the snapshots on disk name their camera
  // too, so the views stay reachable even when the roster is slow, empty or
  // failing. Gating the buttons on the roster alone left them dead with a
  // perfectly good image sitting on disk.
  let selectableCameraIds = $derived.by<number[]>(() => {
    const ids = cameraList.map((camera) => camera.camera_id);
    for (const snapshot of snapshots) {
      const id = Number(snapshot.cam_id);
      if (Number.isFinite(id) && !ids.includes(id)) ids.push(id);
    }
    return ids.sort((a, b) => a - b);
  });

  let isCombined = $derived(selectedCamera === "");
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
    // Tie the picture to the data: pull a new frame when a detection lands,
    // not on an independent timer. Throttled to the rate the C++ side
    // actually rewrites the files, so packets do not cause redundant fetches.
    if (stamp - lastPump >= IMAGE_REFRESH_MS) pumpFrame();
    framesByCamera = { ...framesByCamera, [frame.camera_id]: frame };
    frameArrivedAt = { ...frameArrivedAt, [frame.camera_id]: stamp };
    lastUpdate = stamp;
    absorbRobots(frame);
  });

  // Re-evaluate ages on a timer so entries grey out and expire even when no
  // new frames arrive - which is exactly the case a dead camera produces.
  // One animation loop owns both canvases. Drawing from here rather than from
  // an effect keeps rendering off Svelte's dependency graph: the draw reads
  // whatever the current state is when the frame comes round, instead of a
  // state change scheduling a redraw.
  $effect(() => {
    let frame = 0;
    let lastReactive = 0;
    const tick = () => {
      drawClock = Date.now();
      if (drawClock - lastReactive >= REACTIVE_TICK_MS) {
        lastReactive = drawClock;
        now = drawClock;
      }
      drawFieldOverlay();
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(frame);
    };
  });

  // Views are per camera and not every camera writes every view, so a stale
  // selection would leave the stage blank after switching. Fall back to the
  // first view this camera actually has.
  // Select the first camera as soon as one is known, so the page lands on
  // something rather than waiting for a click.
  $effect(() => {
    if (!isCombined) return;
    const first = selectableCameraIds[0];
    if (first !== undefined) selectedCamera = String(first);
  });

  $effect(() => {
    if (isCombined) return;
    // Until the snapshot list has loaded we do not know which views this
    // camera has, and "not in the list" is indistinguishable from "list is
    // empty". Resetting on that guess discarded a view chosen from the URL
    // before the page had the information to judge it.
    if (snapshots.length === 0) return;
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

  /** Gap between detection packets, which is what actually paces this page. */
  function packetIntervalText(): string {
    const rate = isCombined ? combined.fps : (selectedStatus?.fps ?? 0);
    if (!Number.isFinite(rate) || rate <= 0) return "--";
    return `${(1000 / rate).toFixed(1)} ms`;
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


  /** Draw field geometry and the live detections over this camera's image,
   * in image pixels. This is the calibration check: if the drawn lines do not
   * sit on the painted ones, the geometry is wrong. */
  function drawFieldOverlay(): void {
    const canvas = overlayCanvas;
    if (!canvas || isCombined) return;
    const cached = frameCache[frameKey()];
    // Size the canvas to the image so the overlay can stay in image pixels,
    // which is the space the projection produces. CSS scales the result.
    // naturalWidth is 0 for an image that has not decoded, so fall back on
    // the calibrated size rather than sizing the canvas to nothing.
    const cachedWidth = cached?.naturalWidth ?? 0;
    const cachedHeight = cached?.naturalHeight ?? 0;
    const width =
      cachedWidth > 0 ? cachedWidth : valueNumber(cameraCalibration["pixel_image_width"], 768);
    const height =
      cachedHeight > 0 ? cachedHeight : valueNumber(cameraCalibration["pixel_image_height"], 432);
    // Assigning width or height reallocates the backing store and clears it,
    // so only do it when the size really changed - otherwise every frame paid
    // for a full reallocation.
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;
    const context = canvas.getContext("2d");
    if (!context) return;
    context.clearRect(0, 0, width, height);
    if (cached) context.drawImage(cached, 0, 0, width, height);
    if (selectedView !== "overlay") return;
    context.lineJoin = "round";
    context.lineCap = "round";

    // The projected geometry depends only on the field, the calibration and
    // the tuned corners - none of which change between frames - but drawing it
    // re-runs the projection for every point of every line. Cache it as a
    // transparent layer over the photo.
    const geometryKey = [
      JSON.stringify(cameraCalibration),
      JSON.stringify(geometryConfig["line_corners"] ?? []),
      JSON.stringify(geometryConfig["outer_line_corners"] ?? []),
      JSON.stringify(optionalLines),
      valueNumber(fieldDrawn["field_length"]),
      valueNumber(fieldDrawn["field_width"]),
      valueNumber(fieldDrawn["boundary_width"]),
      valueNumber(fieldDrawn["goal_width"]),
      valueNumber(fieldDrawn["goal_depth"]),
      valueNumber(fieldDrawn["penalty_area_depth"]),
      valueNumber(fieldDrawn["penalty_area_width"]),
      valueNumber(fieldDrawn["center_circle_radius"]),
    ].join("|");

    const geometryLayer = staticLayer("overlay", geometryKey, width, height, (context) => {
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
    });
    if (geometryLayer) context.drawImage(geometryLayer, 0, 0);

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

  /** Skip periodic work while the tab is in the background.
   *
   * This page is meant to sit open all day, and a hidden tab was still
   * pulling six endpoints and a JPEG every second for nobody. Interactions
   * still fetch: only the timers defer.
   */
  function idle(): boolean {
    return document.hidden;
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

  function loadVersion(): void {
    fetch(api("/api/version"), { cache: "no-store" })
      .then((response) => response.json())
      .then((data: { bundle: string | null }) => {
        if (data.bundle === null) return;
        // The first answer defines what this page is running; later answers
        // are compared against it.
        runningBundle ??= data.bundle;
        servedBundle = data.bundle;
      })
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
  /** Show a diagnostic view, switching off the combined field to do it.
   *
   * The views belong to a single processor, so they need a camera selected.
   * Making the buttons inert until one is picked reads as broken - the
   * obvious thing to do with a view button is click it - so clicking one
   * selects the first available camera instead of doing nothing.
   */
  function chooseView(view: string): void {
    if (isCombined) {
      const first = selectableCameraIds[0];
      if (first === undefined) return;
      selectedCamera = String(first);
    }
    selectedView = view;
    refreshNow();
  }

  /** Keep the address bar in step, without adding history entries. */
  function syncUrl(): void {
    const url = new URL(location.href);
    if (isCombined) {
      url.searchParams.delete("camera");
      url.searchParams.delete("view");
    } else {
      url.searchParams.set("camera", selectedCamera);
      url.searchParams.set("view", selectedView);
    }
    history.replaceState(null, "", url.toString());
  }

  function frameKey(): string {
    const view = selectedView === "overlay" ? "raw" : selectedView;
    return `${String(selectedCameraId)}/${view}`;
  }

  /** Fetch the next snapshot into the cache, off-screen.
   *
   * Loading into a detached Image and swapping only on success is what keeps
   * the canvas from flickering: the visible frame is never the one currently
   * being downloaded.
   */
  function pumpFrame(): void {
    if (isCombined || selectedCameraId === null) return;
    if (document.hidden) return;
    const key = frameKey();
    if (inFlight === key) return;
    inFlight = key;
    lastPump = Date.now();
    const image = new Image();
    image.onload = () => {
      frameCache[key] = image;
      inFlight = "";
    };
    image.onerror = () => {
      // Keep whatever we already had; a missed frame is not a reason to
      // clear the view.
      inFlight = "";
    };
    image.src = api(`/snapshot/${key}?t=${String(Date.now())}`);
  }

  function refreshNow(): void {
    syncUrl();
    // Pull the new view's frame immediately. Waiting for the refresh timer
    // meant a switch showed nothing for up to IMAGE_REFRESH_MS, which reads
    // as the switch itself being slow.
    pumpFrame();
    loadSnapshots();
    loadConfig();
    loadHealth();
    loadGeometry();
    loadField();
    loadCameras();
    loadVersion();
    // Start the first frame now rather than waiting a refresh interval, so a
    // page opened straight onto a camera is not blank for its first moment.
    pumpFrame();
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
    const snapshotTimer = setInterval(() => {
      if (!idle()) loadSnapshots();
    }, 3000);
    const configTimer = setInterval(() => {
      if (!idle()) loadConfig();
    }, 5000);
    const healthTimer = setInterval(() => {
      if (!idle()) loadHealth();
    }, 1000);
    const geometryTimer = setInterval(() => {
      if (!idle()) loadGeometry();
    }, 5000);
    const camerasTimer = setInterval(() => {
      if (!idle()) loadCameras();
    }, 1000);
    const fieldTimer = setInterval(() => {
      if (!idle()) loadField();
    }, 1000);
    const versionTimer = setInterval(() => {
      if (!idle()) loadVersion();
    }, 10000);
    const imageTimer = setInterval(pumpFrame, IMAGE_REFRESH_MS);

    // Coming back to a backgrounded tab should show current data at once,
    // not whatever was on screen when it was hidden.
    const onVisibility = () => {
      if (!document.hidden) refreshNow();
    };
    document.addEventListener("visibilitychange", onVisibility);
    const resize = () => {
      drawFieldOverlay();
    };
    window.addEventListener("resize", resize);

    return () => {
      clearInterval(snapshotTimer);
      clearInterval(configTimer);
      clearInterval(healthTimer);
      clearInterval(geometryTimer);
      clearInterval(camerasTimer);
      clearInterval(fieldTimer);
      clearInterval(versionTimer);
      clearInterval(imageTimer);
      window.removeEventListener("resize", resize);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  });
</script>

<svelte:head>
  <title>SSL Processor UI</title>
</svelte:head>

<main>
  {#if updateAvailable}
    <div class="update-bar" role="status">
      <span
        >A newer build is on the server. This page is still running the
        previous one.</span
      >
      <button
        onclick={() => {
          void hardReload();
        }}
      >
        Load it
      </button>
    </div>
  {/if}

  <header class="topbar">
    <div class="identity">
      <span class="product-mark">VP</span>
      <h1>SSL Processor UI</h1>
    </div>
    <div class="header-status">
      <span class="metric">Last update <strong>{clockText(lastUpdate)}</strong></span>
      <span class="metric">Frame <strong>{number(activeFrame?.frame_number)}</strong></span>
      <button class="action" title="Refetch everything now" onclick={refreshNow}>
        Refresh
      </button>
      <span class:healthy={$connectionState === "open"} class="status-badge">
        <span class="status-dot"></span>
        Bus {$connectionState}
      </span>
    </div>
  </header>

  <div class="toolbar">
    <div class="picker" role="group" aria-label="Camera">
      <span class="picker-label">Camera</span>
      {#each selectableCameraIds as id (id)}
        <button
          class:active={!isCombined && selectedCameraId === id}
          aria-pressed={!isCombined && selectedCameraId === id}
          onclick={() => {
            selectedCamera = String(id);
            refreshNow();
          }}
        >
          {id} &middot; {cameraList.find((camera) => camera.camera_id === id)?.name ??
            `camera ${String(id)}`}
        </button>
      {/each}
    </div>

    <span class="metric">
      {isCombined ? "Combined latency" : "Latency"}
      <strong>{number(isCombined ? combined.latency_ms : selectedStatus?.latency_ms, 1)} ms</strong>
    </span>
    <span class="metric">
      {isCombined ? "Total rate" : "Camera rate"}
      <strong>{number(isCombined ? combined.fps : selectedStatus?.fps, 1)} fps</strong>
    </span>
    <span class="metric" title="Time between detection packets">
      Packet interval <strong>{packetIntervalText()}</strong>
    </span>
    <span class:healthy={processingHealthy} class="status-badge">
      <span class="status-dot"></span>
      Processing {processingHealthy ? "healthy" : "degraded"}
    </span>

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
              aria-pressed={!isCombined && view === selectedView}
              disabled={selectableCameraIds.length === 0}
              title={isCombined
                ? `Show ${viewLabel(view)} for camera ${String(selectableCameraIds[0] ?? 0)}`
                : viewLabel(view)}
              onclick={() => {
                chooseView(view);
              }}
            >
              {viewLabel(view)}
            </button>
          {/each}
        </nav>

        <div class="image-stage">
          {#if isCombined}
            <p>Waiting for a camera to report.</p>
          {:else}
            <div class="overlay-stage">
              <canvas
                bind:this={overlayCanvas}
                aria-label={`Camera ${String(selectedCameraId)} ${viewLabel(selectedView)}`}
              ></canvas>
              {#if selectedView === "overlay"}
                <div class="overlay-legend">
                  <span class="field-key">Field</span>
                  <span class="goal-key">Goals</span>
                  <span class="marking-key">Markings</span>
                  <span class="boundary-key">Boundary</span>
                  <span class="corner-key">Corners</span>
                </div>
              {/if}
            </div>
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

  /* Sits above everything, because it explains why anything below it might
     be wrong. Only ever appears when the server genuinely has a newer build. */
  .update-bar {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 18px;
    color: #0d1a12;
    background: var(--accent);
    font-size: 12.5px;
    font-weight: 500;
  }

  .update-bar button {
    margin-left: auto;
    height: 26px;
    padding: 0 12px;
    border-radius: 4px;
    border: 1px solid rgba(13, 26, 18, 0.45);
    background: rgba(13, 26, 18, 0.12);
    color: #0d1a12;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .update-bar button:hover {
    background: rgba(13, 26, 18, 0.22);
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

  /* Buttons rather than a <select>: the option list is rebuilt on every poll,
     and rebuilding options under a bound select lets the control's displayed
     value drift away from the state it is bound to. */
  .picker {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    flex-wrap: wrap;
  }

  .picker-label {
    margin-right: 4px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .picker button {
    height: 30px;
    padding: 0 10px;
    color: var(--text-secondary);
    background: var(--surface-raised);
    border: 1px solid var(--border-strong);
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
    white-space: nowrap;
  }

  .picker button:hover {
    color: var(--text);
    border-color: var(--accent-strong);
    background: var(--surface-hover);
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
  .picker button:focus-visible,
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

  /* The selected view has to be obvious at a glance: an operator glancing
     back at the page should not have to work out which one is showing. */
  .view-tabs button.active,
  .picker button.active {
    position: relative;
    color: #ffffff;
    background: var(--accent-strong);
    border-color: var(--accent);
    font-weight: 600;
    box-shadow: 0 0 0 1px var(--accent);
  }

  .view-tabs button.active::after,
  .picker button.active::after {
    content: "";
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: -1px;
    height: 2px;
    border-radius: 2px;
    background: #ffffff;
  }

  .view-tabs button.active:hover,
  .picker button.active:hover {
    color: #ffffff;
    background: var(--accent-strong);
    border-color: var(--accent);
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

  .image-stage p {
    color: var(--text-muted);
    font-size: 13px;
  }

  .image-stage canvas {
    width: 100%;
    height: 100%;
    display: block;
    object-fit: contain;
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

  /* ---------- liveness ---------- */

  /* Controls respond to the pointer. Small movements, but they make the page
     feel like something running rather than a screenshot of one. */
  .picker button,
  .view-tabs button,
  .action {
    transition:
      transform 90ms ease,
      background-color 120ms ease,
      border-color 120ms ease,
      color 120ms ease;
  }

  .picker button:hover:not(:disabled),
  .view-tabs button:hover:not(:disabled),
  .action:hover {
    transform: translateY(-1px);
  }

  .picker button:active:not(:disabled),
  .view-tabs button:active:not(:disabled),
  .action:active {
    transform: translateY(1px);
  }

  /* A live feed pulses; a dead one sits still. The animation is the signal,
     so it is easy to see from across a room that data is still arriving. */
  .status-badge.healthy .status-dot,
  .service-indicator.online {
    animation: pulse 2s ease-in-out infinite;
  }

  @keyframes pulse {
    0%,
    100% {
      box-shadow: 0 0 0 0 rgba(78, 203, 138, 0.55);
    }
    50% {
      box-shadow: 0 0 0 4px rgba(78, 203, 138, 0);
    }
  }

  /* Values that change on their own get a brief tint as they land, so a
     number moving is noticeable without staring at it. */
  .metric strong,
  .camera-table .mono {
    transition: color 400ms ease;
  }

  .robot-chip {
    animation: chip-in 160ms ease-out;
  }

  @keyframes chip-in {
    from {
      opacity: 0;
      transform: scale(0.94);
    }
    to {
      opacity: 1;
      transform: scale(1);
    }
  }

  @media (prefers-reduced-motion: reduce) {
    .robot-chip,
    .picker button,
    .view-tabs button,
    .action,
    .status-badge.healthy .status-dot,
    .service-indicator.online {
      animation: none;
      transition: none;
    }

    .picker button:hover:not(:disabled),
    .view-tabs button:hover:not(:disabled),
    .action:hover,
    .picker button:active:not(:disabled),
    .view-tabs button:active:not(:disabled),
    .action:active {
      transform: none;
    }
  }
</style>

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

  interface GeometryData {
    field?: DataMap;
    calib?: DataMap[];
    models?: DataMap;
  }

  type Point3 = [number, number, number];

  interface WrapperPacket {
    detection?: DetectionFrame;
    geometry?: GeometryData;
    source?: string;
  }

  interface ConfigResponse {
    path: string;
    modified_at: number;
    config: DataMap;
  }

  interface ServiceHealth {
    running: boolean;
    pid: number | null;
  }

  interface HealthResponse {
    status: string;
    uptime_s: number;
    snapshot_count: number;
    latest_snapshot_age_s: number | null;
    services: Record<string, ServiceHealth>;
  }

  const wrapperPacket = topic<WrapperPacket>("wrapper_packet.out");
  const detectionPacket = topic<DetectionFrame>("detection.in");
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
  let selectedView = $state("overlay");
  let cacheBuster = $state(0);
  let configPayload = $state<ConfigResponse | null>(null);
  let health = $state<HealthResponse | null>(null);
  let detection = $state<DetectionFrame | null>(null);
  let geometry = $state<GeometryData | null>(null);
  let fps = $state(0);
  let previousFrame = 0;
  let previousFrameAt = 0;
  let overlayCanvas = $state<HTMLCanvasElement>();

  let activeConfig = $derived(asRecord(configPayload?.config));
  let cameraConfig = $derived(asRecord(activeConfig["camera"]));
  let geometryConfig = $derived(asRecord(activeConfig["geometry"]));
  let thresholdConfig = $derived(asRecord(activeConfig["thresholds"]));
  let colorConfig = $derived(asRecord(activeConfig["color"]));
  let networkConfig = $derived(asRecord(activeConfig["network"]));
  let streamConfig = $derived(asRecord(activeConfig["stream"]));
  let field = $derived(asRecord(geometry?.field));
  let cameraCalibration = $derived(geometry?.calib?.[0] ?? {});
  let blueRobots = $derived(detection?.robots_blue ?? []);
  let yellowRobots = $derived(detection?.robots_yellow ?? []);
  let balls = $derived(detection?.balls ?? []);

  $effect(() => {
    const packet = $wrapperPacket;
    if (packet?.geometry) geometry = packet.geometry;
  });

  $effect(() => {
    const frame = $detectionPacket;
    if (frame) detection = frame;
  });

  $effect(() => {
    const frame = detection?.frame_number ?? 0;
    if (!frame || frame === previousFrame) return;
    const now = performance.now();
    if (previousFrameAt > 0) {
      const elapsed = now - previousFrameAt;
      const frames = Math.max(1, frame - previousFrame);
      const instant = (frames * 1000) / elapsed;
      fps = fps === 0 ? instant : fps * 0.8 + instant * 0.2;
    }
    previousFrame = frame;
    previousFrameAt = now;
  });

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
    return String(value);
  }

  function colorCss(value: unknown): string {
    if (!Array.isArray(value) || value.length < 3) return "#808080";
    return `rgb(${number(value[0])} ${number(value[1])} ${number(value[2])})`;
  }

  function valueNumber(value: unknown, fallback = 0): number {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

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

  function projectFieldPoint(point: Point3): [number, number] | null {
    const fieldLength = valueNumber(field["field_length"]);
    const fieldWidth = valueNumber(field["field_width"]);
    const corners = geometryConfig["line_corners"];
    if (fieldLength <= 0 || fieldWidth <= 0 || !Array.isArray(corners) || corners.length !== 4) {
      return projectCameraPoint(point);
    }

    const parsed = corners.map((corner) =>
      Array.isArray(corner) && corner.length >= 2
        ? [valueNumber(corner[0], Number.NaN), valueNumber(corner[1], Number.NaN)] as [number, number]
        : null,
    );
    if (parsed.some((corner) => corner === null || !Number.isFinite(corner[0]) || !Number.isFinite(corner[1]))) {
      return projectCameraPoint(point);
    }

    // Config order is bottom-left, top-left, top-right, bottom-right.
    const p0 = parsed[0] as [number, number];
    const p1 = parsed[3] as [number, number];
    const p2 = parsed[2] as [number, number];
    const p3 = parsed[1] as [number, number];
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
    return [
      (a * u + b * v + p0[0]) / scale,
      (d * u + e * v + p0[1]) / scale,
    ];
  }

  function drawFieldOverlay(): void {
    if (!overlayCanvas || selectedView !== "overlay") return;
    const width = valueNumber(cameraCalibration["pixel_image_width"], 768);
    const height = valueNumber(cameraCalibration["pixel_image_height"], 432);
    overlayCanvas.width = width;
    overlayCanvas.height = height;
    const context = overlayCanvas.getContext("2d");
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
      const projected = points.map(projectFieldPoint).filter((point): point is [number, number] => point !== null);
      if (projected.length < 2) return;
      const firstPoint = projected[0];
      if (!firstPoint) return;
      context.beginPath();
      context.moveTo(firstPoint[0], firstPoint[1]);
      for (const point of projected.slice(1)) context.lineTo(point[0], point[1]);
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
    if (fieldLength <= 0 || fieldWidth <= 0) return;
    const halfLength = fieldLength / 2;
    const halfWidth = fieldWidth / 2;
    const boundary = valueNumber(field["boundary_width"]);
    const goalWidth = valueNumber(field["goal_width"]);
    const goalDepth = valueNumber(field["goal_depth"]);
    const penaltyDepth = valueNumber(field["penalty_area_depth"]);
    const penaltyWidth = valueNumber(field["penalty_area_width"]);
    const centerRadius = valueNumber(field["center_circle_radius"]);

    drawPath([
      [-halfLength - boundary, -halfWidth - boundary, 0],
      [halfLength + boundary, -halfWidth - boundary, 0],
      [halfLength + boundary, halfWidth + boundary, 0],
      [-halfLength - boundary, halfWidth + boundary, 0],
    ], "#4dd8a0", 1.5, [8, 6], true);

    drawPath([
      [-halfLength, -halfWidth, 0],
      [halfLength, -halfWidth, 0],
      [halfLength, halfWidth, 0],
      [-halfLength, halfWidth, 0],
    ], "#f4f7f5", 2.2, [], true);
    drawPath([[0, -halfWidth, 0], [0, halfWidth, 0]], "#ffd451", 1.7);

    if (centerRadius > 0) {
      const circle: Point3[] = [];
      for (let step = 0; step <= 64; step += 1) {
        const angle = (step / 64) * Math.PI * 2;
        circle.push([Math.cos(angle) * centerRadius, Math.sin(angle) * centerRadius, 0]);
      }
      drawPath(circle, "#ffd451", 1.7, [], true);
    }

    if (penaltyDepth > 0 && penaltyWidth > 0) {
      const halfPenaltyWidth = penaltyWidth / 2;
      drawPath([
        [-halfLength, -halfPenaltyWidth, 0],
        [-halfLength + penaltyDepth, -halfPenaltyWidth, 0],
        [-halfLength + penaltyDepth, halfPenaltyWidth, 0],
        [-halfLength, halfPenaltyWidth, 0],
      ], "#ff78ae", 1.7);
      drawPath([
        [halfLength, -halfPenaltyWidth, 0],
        [halfLength - penaltyDepth, -halfPenaltyWidth, 0],
        [halfLength - penaltyDepth, halfPenaltyWidth, 0],
        [halfLength, halfPenaltyWidth, 0],
      ], "#ff78ae", 1.7);
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

    const inputCorners = geometryConfig["outer_line_corners"] ?? geometryConfig["line_corners"];
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

  function viewLabel(view: string): string {
    const labels: Record<string, string> = {
      raw: "Raw",
      flat: "Color delta",
      gradient: "Gradient",
      blob: "Blob score",
      "pixels.corner": "Corner model",
      "pixels.refined": "Refined model",
      lines: "Detected lines",
    };
    return labels[view] ?? view.replaceAll(".", " ");
  }

  function orderedSnapshots(): Snapshot[] {
    return [...snapshots].sort((left, right) => {
      const leftIndex = preferredViews.indexOf(left.view);
      const rightIndex = preferredViews.indexOf(right.view);
      const a = leftIndex === -1 ? preferredViews.length : leftIndex;
      const b = rightIndex === -1 ? preferredViews.length : rightIndex;
      return a - b || left.view.localeCompare(right.view);
    });
  }

  function selectedSnapshot(): Snapshot | undefined {
    return snapshots.find((snapshot) => snapshot.view === selectedView);
  }

  async function refreshSnapshots(): Promise<void> {
    try {
      const response = await fetch(api("/snapshots"));
      if (!response.ok) return;
      snapshots = (await response.json()) as Snapshot[];
      if (selectedView !== "overlay" && !selectedSnapshot() && snapshots.length > 0) {
        selectedView = snapshots.find((item) => item.view === "raw")?.view ?? snapshots[0]?.view ?? "raw";
      }
    } catch {
      // The health indicator communicates backend availability.
    }
  }

  async function refreshConfig(): Promise<void> {
    try {
      const response = await fetch(api("/api/config"));
      if (response.ok) configPayload = (await response.json()) as ConfigResponse;
    } catch {
      configPayload = null;
    }
  }

  async function refreshHealth(): Promise<void> {
    try {
      const response = await fetch(api("/api/health"));
      if (response.ok) health = (await response.json()) as HealthResponse;
    } catch {
      health = null;
    }
  }

  onMount(() => {
    void Promise.all([refreshSnapshots(), refreshConfig(), refreshHealth()]);
    const snapshotListTimer = setInterval(() => void refreshSnapshots(), 5000);
    const configTimer = setInterval(() => void refreshConfig(), 3000);
    const healthTimer = setInterval(() => void refreshHealth(), 2000);
    const imageTimer = setInterval(() => {
      cacheBuster = Date.now();
    }, 100);
    return () => {
      clearInterval(snapshotListTimer);
      clearInterval(configTimer);
      clearInterval(healthTimer);
      clearInterval(imageTimer);
    };
  });

  $effect(() => {
    cacheBuster;
    geometry;
    if (selectedView === "overlay") requestAnimationFrame(drawFieldOverlay);
  });
</script>

<svelte:head>
  <title>SSL Vision Operator</title>
</svelte:head>

<main>
  <header class="topbar">
    <div class="identity">
      <span class="product-mark">VP</span>
      <div>
        <h1>SSL Vision Operator</h1>
        <p>Camera {number(detection?.camera_id)} / Division B lab field</p>
      </div>
    </div>
    <div class="header-status">
      <span class:healthy={$connectionState === "open"} class="status-badge">
        <span class="status-dot"></span>
        Bus {$connectionState}
      </span>
      <span class="metric"><strong>{number(fps, 1)}</strong> FPS</span>
      <span class="metric"><strong>{number(detection?.frame_number)}</strong> Frame</span>
    </div>
  </header>

  <div class="workspace">
    <div class="primary-column">
      <section class="viewer-panel">
        <div class="section-heading">
          <div>
            <h2>Processing view</h2>
            <p>Live diagnostic snapshots from the active processor</p>
          </div>
          <span class="small-state">{health?.latest_snapshot_age_s ?? "--"} s ago</span>
        </div>

        <nav class="view-tabs" aria-label="Diagnostic view">
          <button class:active={selectedView === "overlay"} onclick={() => (selectedView = "overlay")}>
            Field overlay
          </button>
          {#each orderedSnapshots() as snapshot (`${snapshot.cam_id}.${snapshot.view}`)}
            <button
              class:active={snapshot.view === selectedView}
              onclick={() => (selectedView = snapshot.view)}
            >
              {viewLabel(snapshot.view)}
            </button>
          {/each}
        </nav>

        <div class="image-stage">
          {#if selectedView === "overlay" && snapshots.find((snapshot) => snapshot.view === "raw")}
            <div class="overlay-stage">
              <img
                src={api(`/snapshot/${snapshots.find((snapshot) => snapshot.view === "raw")?.cam_id}/raw?t=${String(cacheBuster)}`)}
                alt="Camera field geometry overlay"
                onload={drawFieldOverlay}
              />
              <canvas bind:this={overlayCanvas} aria-label="Projected field and goal geometry"></canvas>
              <div class="overlay-legend">
                <span class="field-key">Field</span>
                <span class="goal-key">Goals</span>
                <span class="marking-key">Markings</span>
                <span class="boundary-key">Boundary</span>
                <span class="corner-key">Outer points</span>
              </div>
            </div>
          {:else if selectedSnapshot()}
            <img
              src={api(`/snapshot/${selectedSnapshot()?.cam_id}/${selectedView}?t=${String(cacheBuster)}`)}
              alt={`Camera ${selectedSnapshot()?.cam_id} ${viewLabel(selectedView)}`}
            />
          {:else}
            <p>No processor snapshots are available.</p>
          {/if}
        </div>
      </section>

      <section class="detections-panel">
        <div class="section-heading compact">
          <div>
            <h2>Detections</h2>
            <p>Latest SSL-Vision multicast frame</p>
          </div>
          <div class="detection-counts">
            <span class="blue-count">{blueRobots.length} blue</span>
            <span class="yellow-count">{yellowRobots.length} yellow</span>
            <span>{balls.length} balls</span>
          </div>
        </div>

        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Object</th>
                <th>ID</th>
                <th>Confidence</th>
                <th>X mm</th>
                <th>Y mm</th>
                <th>Angle rad</th>
                <th>Image px</th>
              </tr>
            </thead>
            <tbody>
              {#each blueRobots as robot, index (`blue-${robot.robot_id ?? index}`)}
                <tr>
                  <td><span class="team-dot blue"></span>Blue robot</td>
                  <td>{text(robot.robot_id)}</td>
                  <td>{number(robot.confidence, 2)}</td>
                  <td>{number(robot.x, 1)}</td>
                  <td>{number(robot.y, 1)}</td>
                  <td>{number(robot.orientation, 3)}</td>
                  <td>{number(robot.pixel_x, 1)}, {number(robot.pixel_y, 1)}</td>
                </tr>
              {/each}
              {#each yellowRobots as robot, index (`yellow-${robot.robot_id ?? index}`)}
                <tr>
                  <td><span class="team-dot yellow"></span>Yellow robot</td>
                  <td>{text(robot.robot_id)}</td>
                  <td>{number(robot.confidence, 2)}</td>
                  <td>{number(robot.x, 1)}</td>
                  <td>{number(robot.y, 1)}</td>
                  <td>{number(robot.orientation, 3)}</td>
                  <td>{number(robot.pixel_x, 1)}, {number(robot.pixel_y, 1)}</td>
                </tr>
              {/each}
              {#each balls as ball, index (`ball-${index}`)}
                <tr>
                  <td><span class="team-dot orange"></span>Ball</td>
                  <td>--</td>
                  <td>{number(ball.confidence, 2)}</td>
                  <td>{number(ball.x, 1)}</td>
                  <td>{number(ball.y, 1)}</td>
                  <td>--</td>
                  <td>{number(ball.pixel_x, 1)}, {number(ball.pixel_y, 1)}</td>
                </tr>
              {/each}
              {#if blueRobots.length + yellowRobots.length + balls.length === 0}
                <tr><td colspan="7" class="empty-row">No objects in the latest frame</td></tr>
              {/if}
            </tbody>
          </table>
        </div>
      </section>
    </div>

    <aside class="inspector">
      <section>
        <div class="section-heading compact"><h2>Services</h2></div>
        <div class="service-list">
          {#each Object.entries(health?.services ?? {}) as [name, service]}
            <div class="service-row">
              <span class:online={service.running} class="service-indicator"></span>
              <span>{name.replaceAll("_", " ")}</span>
              <code>{service.pid ?? "stopped"}</code>
            </div>
          {/each}
        </div>
      </section>

      <section>
        <div class="section-heading compact">
          <h2>Camera input</h2>
          <span class="section-tag">Active config</span>
        </div>
        <dl class="property-grid">
          <div><dt>Device</dt><dd>{text(cameraConfig["path"])}</dd></div>
          <div><dt>Capture</dt><dd>{number(cameraConfig["width"])} x {number(cameraConfig["height"])}</dd></div>
          <div><dt>Processing</dt><dd>{number(cameraConfig["output_width"])} x {number(cameraConfig["output_height"])}</dd></div>
          <div><dt>Capture rate</dt><dd>{number(cameraConfig["fps"])} FPS</dd></div>
          <div><dt>Gain</dt><dd>{number(cameraConfig["gain"], 1)}</dd></div>
          <div><dt>Gamma</dt><dd>{number(cameraConfig["gamma"], 1)}</dd></div>
          <div><dt>Format</dt><dd>{text(cameraConfig["fourcc"])}</dd></div>
          <div><dt>Left crop</dt><dd>{text(cameraConfig["crop_left_half"])}</dd></div>
        </dl>
      </section>

      <section>
        <div class="section-heading compact"><h2>Solved camera model</h2></div>
        <dl class="property-grid">
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

      <section>
        <div class="section-heading compact"><h2>Field geometry</h2></div>
        <dl class="property-grid">
          <div><dt>Field</dt><dd>{number(field["field_length"])} x {number(field["field_width"])} mm</dd></div>
          <div><dt>Boundary</dt><dd>{number(field["boundary_width"])} mm</dd></div>
          <div><dt>Goal</dt><dd>{number(field["goal_width"])} x {number(field["goal_depth"])} mm</dd></div>
          <div><dt>Robot radius</dt><dd>{number(field["max_robot_radius"], 0)} mm</dd></div>
          <div><dt>Ball radius</dt><dd>{number(field["ball_radius"], 1)} mm</dd></div>
          <div><dt>Line thickness</dt><dd>{number(field["line_thickness"])} mm</dd></div>
        </dl>
      </section>

      <section>
        <div class="section-heading compact"><h2>Detection thresholds</h2></div>
        <dl class="property-grid thresholds">
          <div><dt>Circularity</dt><dd>{number(thresholdConfig["circularity"], 1)}</dd></div>
          <div><dt>Score</dt><dd>{number(thresholdConfig["score"], 1)}</dd></div>
          <div><dt>Confidence</dt><dd>{number(thresholdConfig["min_confidence"], 2)}</dd></div>
          <div><dt>Blob limit</dt><dd>{number(thresholdConfig["blobs"])}</dd></div>
          <div><dt>Edge distance</dt><dd>{number(thresholdConfig["min_cam_edge_distance"])}</dd></div>
          <div><dt>Clipping</dt><dd>{number(thresholdConfig["clipping_tolerance"], 1)}</dd></div>
        </dl>
      </section>

      <section>
        <div class="section-heading compact"><h2>Color references</h2></div>
        <div class="color-list">
          {#each colorNames as name}
            <div class="color-row">
              <span class="swatch" style={`background: ${colorCss(colorConfig[name])}`}></span>
              <span>{name}</span>
              <code>{JSON.stringify(colorConfig[name] ?? [])}</code>
            </div>
          {/each}
        </div>
      </section>

      <section>
        <div class="section-heading compact"><h2>Network output</h2></div>
        <dl class="property-grid">
          <div><dt>Vision multicast</dt><dd>{text(networkConfig["vision_ip"])}:{number(networkConfig["vision_port"])}</dd></div>
          <div><dt>Game Controller</dt><dd>{text(networkConfig["gc_ip"])}:{number(networkConfig["gc_port"])}</dd></div>
          <div><dt>Debug stream</dt><dd>{text(streamConfig["ip_base_prefix"])}{number(streamConfig["ip_base_end"])}:{number(streamConfig["port"])}</dd></div>
        </dl>
      </section>
    </aside>
  </div>

  <footer>
    <span>{configPayload?.path ?? "Waiting for active configuration"}</span>
    <span>Backend uptime {number(health?.uptime_s)} s</span>
  </footer>
</main>

<style>
  /* Dark operator theme. Every colour in this component resolves through a
     token below, so re-theming means editing this block only. */
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

    --overlay-field: #f4f7f5;
    --overlay-goal: #40cfff;
    --overlay-marking: #ffd451;
    --overlay-boundary: #4dd8a0;
    --overlay-corner: #ff5b55;
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

  :global(button) {
    font: inherit;
  }

  main {
    min-height: 100vh;
  }

  .topbar {
    min-height: 62px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 24px;
    padding: 10px 20px;
    color: var(--text);
    background: var(--surface-sunken);
    border-bottom: 3px solid var(--accent-strong);
  }

  .identity,
  .header-status,
  .section-heading,
  .detection-counts,
  .service-row,
  .color-row {
    display: flex;
    align-items: center;
  }

  .identity {
    gap: 12px;
    min-width: 0;
  }

  .product-mark {
    width: 38px;
    height: 38px;
    display: grid;
    place-items: center;
    flex: 0 0 38px;
    border: 1px solid var(--accent-strong);
    border-radius: 4px;
    color: var(--accent-soft);
    font-weight: 750;
    font-size: 14px;
  }

  h1,
  h2,
  p {
    margin: 0;
  }

  h1 {
    font-size: 16px;
    line-height: 1.25;
    font-weight: 700;
  }

  .identity p,
  .section-heading p {
    margin-top: 2px;
    color: var(--text-secondary);
    font-size: 12px;
  }

  .header-status {
    justify-content: flex-end;
    gap: 10px;
    flex-wrap: wrap;
  }

  .status-badge,
  .metric,
  .small-state,
  .section-tag,
  .detection-counts span {
    min-height: 28px;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 9px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
  }

  .status-badge,
  .metric {
    color: var(--text);
    border: 1px solid var(--border-strong);
    background: var(--surface-raised);
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

  .workspace {
    display: grid;
    grid-template-columns: minmax(0, 1.8fr) minmax(360px, 1fr);
    gap: 12px;
    padding: 12px;
    align-items: start;
  }

  .primary-column,
  .inspector {
    min-width: 0;
    display: grid;
    gap: 12px;
  }

  .viewer-panel,
  .detections-panel,
  .inspector section {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
    overflow: hidden;
  }

  .section-heading {
    min-height: 54px;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }

  .section-heading.compact {
    min-height: 42px;
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
  }

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

  .image-stage {
    width: 100%;
    aspect-ratio: 16 / 9;
    display: grid;
    place-items: center;
    background: var(--surface-sunken);
    overflow: hidden;
  }

  .image-stage img {
    width: 100%;
    height: 100%;
    display: block;
    object-fit: contain;
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
    pointer-events: none;
  }

  .overlay-legend {
    position: absolute;
    top: 8px;
    left: 8px;
    display: flex;
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
    background: var(--overlay-field);
  }

  .overlay-legend .goal-key::before {
    background: var(--overlay-goal);
  }

  .overlay-legend .marking-key::before {
    background: var(--overlay-marking);
  }

  .overlay-legend .boundary-key::before {
    background: var(--overlay-boundary);
  }

  .overlay-legend .corner-key::before {
    height: 6px;
    width: 6px;
    border-radius: 50%;
    background: var(--overlay-corner);
    vertical-align: 1px;
  }

  .image-stage p {
    color: var(--text-muted);
    font-size: 13px;
  }

  .detection-counts {
    gap: 6px;
  }

  .detection-counts span {
    min-height: 24px;
    color: var(--text-secondary);
    background: var(--surface-raised);
  }

  .detection-counts .blue-count {
    color: var(--blue);
    background: var(--blue-soft);
  }

  .detection-counts .yellow-count {
    color: var(--yellow);
    background: var(--yellow-soft);
  }

  .table-wrap {
    width: 100%;
    overflow-x: auto;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    font-size: 12px;
  }

  th,
  td {
    height: 36px;
    padding: 7px 9px;
    text-align: right;
    border-bottom: 1px solid var(--border-subtle);
    white-space: nowrap;
  }

  th {
    color: var(--text-secondary);
    background: var(--surface-raised);
    font-weight: 600;
  }

  th:first-child,
  td:first-child {
    width: 140px;
    text-align: left;
  }

  tr:last-child td {
    border-bottom: 0;
  }

  .team-dot {
    width: 9px;
    height: 9px;
    display: inline-block;
    margin-right: 7px;
    border-radius: 50%;
    vertical-align: -1px;
  }

  .team-dot.blue {
    background: var(--blue);
  }

  .team-dot.yellow {
    background: var(--yellow);
  }

  .team-dot.orange {
    background: var(--orange);
  }

  .empty-row {
    height: 58px;
    text-align: center;
    color: var(--text-muted);
  }

  .inspector section {
    overflow: visible;
  }

  .service-list,
  .color-list {
    padding: 4px 12px 8px;
  }

  .service-row,
  .color-row {
    min-height: 31px;
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

  .property-grid div {
    min-width: 0;
    min-height: 43px;
    padding: 7px 8px 6px 0;
    border-bottom: 1px solid var(--border-subtle);
  }

  .property-grid div:nth-last-child(-n + 2) {
    border-bottom: 0;
  }

  dt {
    margin-bottom: 3px;
    color: var(--text-muted);
    font-size: 10px;
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
    min-height: 38px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 16px;
    color: var(--text-muted);
    font-size: 11px;
    border-top: 1px solid var(--border);
  }

  @media (max-width: 1050px) {
    .workspace {
      grid-template-columns: 1fr;
    }

    .inspector {
      grid-template-columns: repeat(2, minmax(0, 1fr));
    }
  }

  @media (max-width: 680px) {
    .topbar {
      align-items: flex-start;
      flex-direction: column;
    }

    .header-status {
      justify-content: flex-start;
    }

    .workspace {
      padding: 8px;
    }

    .inspector {
      grid-template-columns: 1fr;
    }

    .section-heading {
      align-items: flex-start;
      flex-direction: column;
    }

    footer {
      align-items: flex-start;
      flex-direction: column;
    }
  }
</style>

// Shapes of GET /api/health (wrapper_backend/operator.py).

export interface PiCameraHealth {
  running: boolean;
  camera_name?: string | null;
  url?: string;
  streaming?: boolean;
  client?: string | null;
  size?: string | null;
  fps?: number | string | null;
  closed?: boolean;
  control?: boolean;
  device?: string | null;
  log_file?: string | null;
  last_error?: { text: string; at: number } | null;
  last_line?: { text: string; at: number } | null;
  error?: string;
}

export interface VisionHealth {
  running: boolean;
  pid: number | null;
  managed?: boolean;
  external_pids?: number[];
  uptime_s?: number | null;
  want_running?: boolean;
  restarts?: number;
  rapid_failures?: number;
  /** "running" | "running (started by hand)" | "waiting for camera (Pi not reachable)" | "restarting (N attempts)" | "stopped" */
  state?: string;
  retry?: {
    state: "idle" | "waiting_for_camera" | "restarting";
    attempts: number;
    next_in_s: number | null;
  };
  last_exit?: { code: number; at: number; ran_s: number } | null;
  binary_exists?: boolean;
  last_status?: { line: string; at: number } | null;
  last_warning?: { line: string; at: number } | null;
  last_detection_age_s?: number | null;
}

export interface BackendHealth {
  running: boolean;
  pid: number | null;
  uptime_s?: number;
  logs_dir?: string | null;
}

export interface GameControllerHealth {
  running: boolean;
  pid: number | null;
  process_running?: boolean;
  group?: string;
  last_packet_age_s?: number | null;
  packets?: number;
  stage?: string;
  command?: string;
  yellow?: string;
  blue?: string;
  yellow_score?: number;
  blue_score?: number;
}

export interface CalibrationHealth {
  running: boolean;
  cam_id?: number;
  state?: "calibrated" | "recalibrating" | "not_calibrated";
  calibrated_cameras?: number[];
  has_corners?: boolean;
}

export interface HealthServices {
  pi_camera?: PiCameraHealth;
  vision_processor?: VisionHealth;
  wrapper_backend?: BackendHealth;
  field_calibration?: CalibrationHealth;
  game_controller?: GameControllerHealth;
}

export interface HealthResponse {
  status: string;
  camera_name?: string | null;
  uptime_s: number;
  cam_id?: number;
  snapshot_count: number;
  latest_snapshot_age_s: number | null;
  services: HealthServices;
}

// GET /api/calibration
export interface CalibrationStatus {
  cam_id: number;
  state: "calibrated" | "recalibrating" | "not_calibrated";
  calibrated: boolean;
  saved_at: number | null;
  corners: unknown;
  outer_corners: unknown;
  include_boundary: unknown;
  distortion_lines: unknown;
  calib_json: {
    distortion_k2?: number;
    principal_point?: number[];
    focal_length?: number;
    age_s?: number;
  } | null;
  refinement: unknown;
  field_length: number;
  field_width: number;
  boundary_width: number;
  boundary_width_goal_line: number;
  image_width: number;
  image_height: number;
}

// GET /api/metrics
export interface CameraMetrics {
  frames: number;
  rate_hz: number;
  processing_ms?: number | null;
  receive_ms?: number | null;
  robots_per_frame?: number;
  balls_per_frame?: number;
  last_age_s?: number;
}

export type DataMap = Record<string, unknown>;

export interface RobotDetection {
  robot_id?: number;
  confidence?: number;
  x?: number;
  y?: number;
  orientation?: number;
  pixel_x?: number;
  pixel_y?: number;
  height?: number;
}

export interface BallDetection {
  confidence?: number;
  x?: number;
  y?: number;
  z?: number;
  pixel_x?: number;
  pixel_y?: number;
}

export interface DetectionFrame {
  frame_number?: number;
  t_capture?: number;
  t_sent?: number;
  camera_id?: number;
  balls?: BallDetection[];
  robots_blue?: RobotDetection[];
  robots_yellow?: RobotDetection[];
}

export interface GeometryData {
  field?: DataMap;
  calib?: DataMap[];
  models?: DataMap;
}

export interface WrapperPacket {
  detection?: DetectionFrame;
  geometry?: GeometryData;
  source?: string;
}

export interface ConfigResponse {
  path: string;
  modified_at: number;
  config: DataMap;
}

export interface Snapshot {
  cam_id: string;
  view: string;
}

export function asRecord(value: unknown): DataMap {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as DataMap)
    : {};
}

export function num(value: unknown, digits = 0): string {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed.toFixed(digits) : "--";
}

export function duration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "--";
  if (seconds < 60) return `${seconds.toFixed(0)} s`;
  if (seconds < 3600) return `${(seconds / 60).toFixed(0)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

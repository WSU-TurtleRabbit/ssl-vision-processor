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

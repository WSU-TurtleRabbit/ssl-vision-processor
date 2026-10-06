<script lang="ts">
  import { onMount, untrack } from "svelte";
  import CameraName from "./lib/CameraName.svelte";
  import ColorPanel from "./lib/ColorPanel.svelte";
  import FieldCorners from "./lib/FieldCorners.svelte";
  import LensCorrection from "./lib/LensCorrection.svelte";
  import GeometryEditor from "./lib/GeometryEditor.svelte";
  import PerformancePanel from "./lib/PerformancePanel.svelte";
  import ServicesPanel from "./lib/ServicesPanel.svelte";
  import type { CalibrationStatus, HealthResponse } from "./lib/health";
  import { drawOverlay } from "./lib/overlay";
  import Icon from "./lib/Icon.svelte";
  import LogDrawer from "./lib/LogDrawer.svelte";
  import LogsView from "./lib/LogsView.svelte";
  import PopoutView from "./lib/PopoutView.svelte";
  import { connectionState, reconnect, topic } from "./lib/wrapper-bus";

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

  const wrapperPacket = topic<WrapperPacket>("wrapper_packet.out");
  const detectionPacket = topic<DetectionFrame>("detection.in");
  const preferredViews = [
    "raw",
    "flat",
    "gradient",
    "blob",
    "pixels.corner",
    "pixels.refined",
    "lines",
  ];

  let snapshots = $state<Snapshot[]>([]);
  let selectedView = $state("overlay");
  let cacheBuster = $state(0);
  let configPayload = $state<ConfigResponse | null>(null);
  let health = $state<HealthResponse | null>(null);
  let healthReachable = $state(false);
  let calibration = $state<CalibrationStatus | null>(null);
  // --- view routing: "#/help/<doc>[#anchor]" shows the Help view ---------
  function parseHash(): {
    view: "main" | "help" | "logs";
    doc: string;
    file: string | null;
  } {
    const help = /^#\/help(?:\/([\w-]+))?/.exec(location.hash);
    if (help) return { view: "help", doc: help[1] ?? "README", file: null };
    const logs = /^#\/logs(?:\/([\w.-]+))?/.exec(location.hash);
    if (logs) return { view: "logs", doc: "README", file: logs[1] ?? null };
    return { view: "main", doc: "README", file: null };
  }
  // /popout?cam=0&view=raw: the minimal pop-out camera window.
  const isPopout =
    location.pathname.endsWith("/popout") ||
    location.hash.startsWith("#/popout");
  function openLogs(file: string | null = null): void {
    location.hash = `#/logs${file ? `/${file}` : ""}`;
    route = parseHash();
    window.scrollTo(0, 0);
  }
  function popOut(): void {
    window.open(
      `/popout?cam=${String(camId)}&view=raw`,
      "vp-popout",
      "width=960,height=600,menubar=no,toolbar=no",
    );
  }
  // Log console drawer (right edge), remembered per browser.
  const DRAWER_KEY = "vp-log-drawer";
  function readDrawer(): boolean {
    try {
      return localStorage.getItem(DRAWER_KEY) === "1";
    } catch {
      return false;
    }
  }
  let drawerOpen = $state(readDrawer());
  function toggleDrawer(): void {
    drawerOpen = !drawerOpen;
    try {
      localStorage.setItem(DRAWER_KEY, drawerOpen ? "1" : "0");
    } catch {
      // Not remembered.
    }
  }
  function onGlobalKey(event: KeyboardEvent): void {
    if (event.key !== "`" || event.ctrlKey || event.metaKey || event.altKey)
      return;
    const target = event.target;
    if (
      target instanceof HTMLElement &&
      (target.isContentEditable ||
        ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
    )
      return;
    event.preventDefault();
    toggleDrawer();
  }
  let route = $state(parseHash());
  function openHelp(doc = "README", anchor?: string): void {
    location.hash = `#/help/${doc}${anchor ? `#${anchor}` : ""}`;
    route = parseHash();
    if (!anchor) window.scrollTo(0, 0);
  }
  function closeHelp(): void {
    history.pushState(null, "", location.pathname + location.search);
    route = parseHash();
  }
  let helpModule: Promise<typeof import("./lib/HelpView.svelte")> | null = null;
  function loadHelp(): Promise<typeof import("./lib/HelpView.svelte")> {
    helpModule ??= import("./lib/HelpView.svelte");
    return helpModule;
  }

  // --- theme: light (default) / dark / system, remembered per browser ----
  type ThemePref = "light" | "dark" | "system";
  const THEME_KEY = "vp-theme";
  function readThemePref(): ThemePref {
    try {
      const stored = localStorage.getItem(THEME_KEY);
      if (stored === "dark" || stored === "system") return stored;
    } catch {
      // Storage blocked: fall back to the default.
    }
    return "light";
  }
  let themePref = $state<ThemePref>(readThemePref());
  let systemDark = $state(
    window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
  let dark = $derived(
    themePref === "dark" || (themePref === "system" && systemDark),
  );
  $effect(() => {
    document.documentElement.dataset["theme"] = dark ? "dark" : "light";
    try {
      localStorage.setItem(THEME_KEY, themePref);
    } catch {
      // Not remembered; still applied for this page.
    }
  });

  // --- manual refresh -----------------------------------------------------
  let refreshToken = $state(0);
  let lastUpdated = $state<Date | null>(null);
  let refreshing = $state(false);
  async function refreshAll(): Promise<void> {
    refreshing = true;
    reconnect();
    refreshToken += 1;
    await Promise.all([
      refreshSnapshots(),
      refreshConfig(),
      refreshHealth(),
      refreshCalibration(),
    ]);
    cacheBuster = Date.now();
    lastUpdated = new Date();
    refreshing = false;
  }
  // Detection frames applied to the page (Performance panel).
  let uiFrames = $state(0);

  // Field dimensions modal + toast.
  let geometryModal = $state(false);
  let toast = $state<{ kind: "ok" | "error"; text: string } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;
  function showToast(
    next: { kind: "ok" | "error"; text: string } | null,
  ): void {
    if (toastTimer) clearTimeout(toastTimer);
    toast = next;
    if (next)
      toastTimer = setTimeout(() => {
        toast = null;
      }, 8000);
  }
  let cornersMode = $state(false);
  let lensMode = $state(false);
  let cornersMessage = $state<string | null>(null);
  let detection = $state<DetectionFrame | null>(null);
  // performance.now() of the last detection frame, and a 250 ms clock so
  // "live" turns false by itself when frames stop.
  let lastFrameAt = $state(0);
  let clock = $state(performance.now());
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
  let camId = $derived(health?.cam_id ?? 0);
  let cameraName = $derived(health?.camera_name ?? null);
  let cameraLabel = $derived(cameraName ?? `Camera ${String(camId)}`);
  let cameraCalibration = $derived(
    geometry?.calib?.find(
      (calib) => valueNumber(calib["camera_id"]) === camId,
    ) ?? {},
  );
  // Detections older than this are dropped: nothing stays on screen when
  // vision_processor stops sending.
  const DETECTION_STALE_MS = 1000;
  let detectionLive = $derived(
    lastFrameAt > 0 && clock - lastFrameAt <= DETECTION_STALE_MS,
  );
  let liveDetection = $derived(detectionLive ? detection : null);
  let blueRobots = $derived(liveDetection?.robots_blue ?? []);
  let yellowRobots = $derived(liveDetection?.robots_yellow ?? []);
  let balls = $derived(liveDetection?.balls ?? []);
  let visionRunning = $derived(
    health?.services.vision_processor?.running ?? false,
  );
  let calibrationState = $derived(
    calibration?.state ?? health?.services.field_calibration?.state,
  );
  // "k2 0.23 · pp 380, 214" from the newest calib.json.
  let lensInfo = $derived.by(() => {
    const calib = calibration?.calib_json;
    if (!calib || calibrationState !== "calibrated") return null;
    const k2 = valueNumber(calib.distortion_k2, Number.NaN);
    const pp = Array.isArray(calib.principal_point)
      ? calib.principal_point
      : [];
    if (!Number.isFinite(k2)) return null;
    return `k2 ${k2.toFixed(3)} · pp ${number(pp[0])}, ${number(pp[1])}${
      Array.isArray(calibration?.distortion_lines)
        ? ` · ${String(calibration.distortion_lines.length)} lens lines`
        : ""
    }`;
  });

  let detectionState = $derived.by(() => {
    if (detectionLive) {
      const robots = blueRobots.length + yellowRobots.length;
      return {
        kind: "live",
        text: `${String(robots)} robot${robots === 1 ? "" : "s"}, ${String(balls.length)} ball${balls.length === 1 ? "" : "s"} (live)`,
      };
    }
    if (!visionRunning)
      return { kind: "down", text: "vision_processor not running" };
    if (calibrationState === "recalibrating")
      return { kind: "wait", text: "running, recalibrating..." };
    if (calibrationState === "not_calibrated")
      return {
        kind: "wait",
        text: "running, not calibrated → set field corners",
      };
    return { kind: "down", text: "running, but no detection frames" };
  });

  $effect(() => {
    const packet = $wrapperPacket;
    if (packet?.geometry) geometry = packet.geometry;
  });

  $effect(() => {
    const frame = $detectionPacket;
    if (frame) {
      detection = frame;
      lastFrameAt = performance.now();
      // untrack: reading uiFrames here must not re-trigger this effect.
      untrack(() => (uiFrames += 1));
    }
  });

  $effect(() => {
    if (!detectionLive) {
      fps = 0;
      previousFrameAt = 0;
    }
  });

  $effect(() => {
    const frame = detection?.frame_number ?? 0;
    if (!frame || frame === previousFrame) return;
    const now = performance.now();
    // Frame-number rate over >= 1 s windows (robust against bursts of
    // WebSocket messages); restart the window when numbering restarts.
    if (frame < previousFrame || previousFrameAt === 0) {
      previousFrame = frame;
      previousFrameAt = now;
      return;
    }
    const elapsed = now - previousFrameAt;
    if (elapsed < 1000) return;
    fps = ((frame - previousFrame) * 1000) / elapsed;
    previousFrame = frame;
    previousFrameAt = now;
  });

  function asRecord(value: unknown): DataMap {
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as DataMap)
      : {};
  }

  function number(value: unknown, digits = 0): string {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed.toFixed(digits) : "--";
  }

  function text(value: unknown): string {
    if (value === null || value === undefined || value === "") return "--";
    if (typeof value === "string") return value;
    if (
      typeof value === "number" ||
      typeof value === "boolean" ||
      typeof value === "bigint"
    ) {
      return String(value);
    }
    // Objects/arrays: show their contents rather than "[object Object]".
    return typeof value === "object" ? JSON.stringify(value) : "--";
  }

  function valueNumber(value: unknown, fallback = 0): number {
    const parsed = typeof value === "number" ? value : Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }

  function drawFieldOverlay(): void {
    if (!overlayCanvas || selectedView !== "overlay") return;
    drawOverlay(overlayCanvas, {
      calib: cameraCalibration,
      field,
      geometryConfig,
      robotsBlue: blueRobots,
      robotsYellow: yellowRobots,
      balls,
    });
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

  // Template-side views of the snapshot list, so `{#if}` can narrow them.
  let rawSnapshot = $derived(
    snapshots.find((snapshot) => snapshot.view === "raw"),
  );
  let currentSnapshot = $derived(
    snapshots.find((snapshot) => snapshot.view === selectedView),
  );

  async function refreshSnapshots(): Promise<void> {
    try {
      const response = await fetch("/snapshots");
      if (!response.ok) return;
      snapshots = (await response.json()) as Snapshot[];
      if (
        selectedView !== "overlay" &&
        !selectedSnapshot() &&
        snapshots.length > 0
      ) {
        selectedView =
          snapshots.find((item) => item.view === "raw")?.view ??
          snapshots[0]?.view ??
          "raw";
      }
    } catch {
      // The health indicator communicates backend availability.
    }
  }

  async function refreshConfig(): Promise<void> {
    try {
      const response = await fetch("/api/config");
      if (response.ok)
        configPayload = (await response.json()) as ConfigResponse;
    } catch {
      configPayload = null;
    }
  }

  async function refreshHealth(): Promise<void> {
    try {
      const response = await fetch("/api/health");
      healthReachable = response.ok;
      if (response.ok) health = (await response.json()) as HealthResponse;
    } catch {
      healthReachable = false;
    }
  }

  async function refreshCalibration(): Promise<void> {
    try {
      const response = await fetch(`/api/calibration?cam_id=${String(camId)}`);
      if (response.ok)
        calibration = (await response.json()) as CalibrationStatus;
    } catch {
      // Shown through the services panel.
    }
  }

  function cornersSaved(message: string): void {
    cornersMode = false;
    lensMode = false;
    cornersMessage = message;
    void refreshCalibration();
    void refreshConfig();
    void refreshHealth();
  }

  onMount(() => {
    void Promise.all([
      refreshSnapshots(),
      refreshConfig(),
      refreshHealth(),
      refreshCalibration(),
    ]).then(() => {
      lastUpdated = new Date();
    });
    const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");
    const onSystemTheme = (event: MediaQueryListEvent): void => {
      systemDark = event.matches;
    };
    darkQuery.addEventListener("change", onSystemTheme);
    const onHash = (): void => {
      route = parseHash();
    };
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    const calibrationTimer = setInterval(() => void refreshCalibration(), 1000);
    const clockTimer = setInterval(() => {
      clock = performance.now();
    }, 250);
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
      clearInterval(calibrationTimer);
      clearInterval(clockTimer);
      darkQuery.removeEventListener("change", onSystemTheme);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  });

  $effect(() => {
    // Redraw on every new frame or geometry update.
    void cacheBuster;
    void geometry;
    void liveDetection;
    if (selectedView === "overlay") requestAnimationFrame(drawFieldOverlay);
  });
</script>

<svelte:window onkeydown={onGlobalKey} />

<svelte:head>
  <title>SSL Vision Operator</title>
</svelte:head>

{#if isPopout}
  <PopoutView />
{:else}
  <main>
    <header class="topbar">
      <div class="identity">
        <span class="product-mark">VP</span>
        <div>
          <h1>SSL Vision Operator</h1>
          <p>
            <CameraName
              {camId}
              name={cameraName}
              showId
              onrenamed={() => void refreshHealth()}
            />
            · {number(field["field_length"])} × {number(field["field_width"])} mm
            field
          </p>
        </div>
      </div>
      <div class="header-status">
        <span
          class:healthy={$connectionState === "open"}
          class="status-badge"
          title="WebSocket to the backend on :8765 (detections, geometry, colours)"
        >
          <span class="status-dot"></span>
          Live data: {$connectionState === "open"
            ? "connected"
            : $connectionState === "connecting"
              ? "reconnecting…"
              : "disconnected"}
        </span>
        <span
          class="metric"
          title="Detection frames per second reaching this page from vision_processor (camera fps is shown in Performance)"
          ><strong>{number(fps, 1)}</strong> FPS</span
        >
        <span
          class="metric"
          title="Frame number of the latest detection packet from vision_processor"
          ><strong>{number(detection?.frame_number)}</strong> Frame</span
        >
        <button
          class="header-button"
          title="Pop out the camera view into its own window"
          aria-label="Pop out the camera view"
          onclick={popOut}><Icon name="popout" size={14} /> Pop out</button
        >
        <button
          class="header-button"
          class:active={drawerOpen}
          title="Log console (` key)"
          aria-label="Toggle the log console"
          onclick={toggleDrawer}><Icon name="logs" size={14} /> Logs</button
        >
        <button
          class="header-button"
          disabled={refreshing}
          title="Re-fetch everything and reconnect the WebSocket"
          onclick={refreshAll}
          >⟳ Refresh{lastUpdated
            ? ` · ${lastUpdated.toLocaleTimeString()}`
            : ""}</button
        >
        <select
          class="header-button"
          bind:value={themePref}
          aria-label="Colour theme"
          title="Colour theme"
        >
          <option value="light">☀ Light</option>
          <option value="dark">☾ Dark</option>
          <option value="system">◐ System</option>
        </select>
        {#if route.view !== "main"}
          <button class="header-button" onclick={closeHelp}>← Operator</button>
        {:else}
          <button
            class="header-button"
            onclick={() => {
              openHelp();
            }}>? Help</button
          >
        {/if}
        <button
          class="panic-button"
          onclick={() => {
            openHelp("panic");
          }}>🚨 Panic</button
        >
      </div>
    </header>

    {#if route.view === "logs"}
      <LogsView file={route.file} onopen={openLogs} />
    {:else if route.view === "help"}
      {#await loadHelp()}
        <p class="help-loading">Loading help...</p>
      {:then module}
        <module.default name={route.doc} {dark} onnavigate={openHelp} />
      {:catch error}
        <p class="help-loading">Help failed to load: {String(error)}</p>
      {/await}
    {:else}
      <div class="workspace">
        <div class="primary-column">
          <section class="viewer-panel">
            <div class="section-heading">
              <div>
                <h2>Processing view</h2>
                <p>Live diagnostic snapshots from the active processor</p>
              </div>
              <div class="viewer-actions">
                <span
                  class="small-state"
                  class:ok={calibrationState === "calibrated"}
                  class:warn={calibrationState !== "calibrated"}
                >
                  {calibrationState === "calibrated"
                    ? "calibrated"
                    : calibrationState === "recalibrating"
                      ? "recalibrating..."
                      : "not calibrated"}
                </span>
                <span class="small-state"
                  >{health?.latest_snapshot_age_s ?? "--"} s ago</span
                >
                {#if lensInfo}
                  <span
                    class="small-state"
                    title="Lens distortion k2 and principal point from the last calibration (calib.json)"
                    >{lensInfo}</span
                  >
                {/if}
                <button
                  class="corners-button icon"
                  class:active={lensMode}
                  title={lensMode
                    ? "Cancel lens correction (Esc)"
                    : "Lens correction: click points along straight edges"}
                  aria-label={lensMode
                    ? "Cancel lens correction"
                    : "Lens correction"}
                  onclick={() => {
                    lensMode = !lensMode;
                    cornersMode = false;
                    cornersMessage = null;
                  }}><Icon name="lens" /></button
                >
                <button
                  class="corners-button icon"
                  class:active={cornersMode}
                  title={cornersMode
                    ? "Cancel field corners (Esc)"
                    : "Set field corners: click the 4 corners (Enter saves, Esc cancels)"}
                  aria-label={cornersMode
                    ? "Cancel field corners"
                    : "Set field corners"}
                  onclick={() => {
                    cornersMode = !cornersMode;
                    lensMode = false;
                    cornersMessage = null;
                  }}><Icon name="corners" /></button
                >
                <button
                  class="corners-button icon"
                  title="Edit field dimensions (modal; Esc cancels)"
                  aria-label="Edit field dimensions"
                  onclick={() => (geometryModal = true)}
                  ><Icon name="field" /></button
                >
              </div>
            </div>

            {#if cornersMessage}
              <p class="corners-message">
                {cornersMessage}
                {#if calibrationState === "recalibrating"}
                  Waiting for the new calibration...
                {:else if calibrationState === "calibrated"}
                  Calibrated — the overlay shows the field from the new
                  calibration{lensInfo ? ` (${lensInfo})` : ""}.
                {/if}
                <button onclick={() => (cornersMessage = null)}>Dismiss</button>
              </p>
            {/if}

            {#if lensMode}
              <LensCorrection
                {cameraLabel}
                camId={String(camId)}
                savedLines={calibration?.distortion_lines ??
                  geometryConfig["distortion_lines"]}
                oncancel={() => (lensMode = false)}
                onsaved={cornersSaved}
              />
            {:else if cornersMode}
              <FieldCorners
                {cameraLabel}
                camId={String(camId)}
                savedCorners={calibration?.corners ??
                  geometryConfig["line_corners"]}
                savedOuter={calibration?.outer_corners ??
                  geometryConfig["outer_line_corners"]}
                fieldLength={calibration?.field_length ??
                  valueNumber(field["field_length"])}
                fieldWidth={calibration?.field_width ??
                  valueNumber(field["field_width"])}
                boundaryWidth={calibration?.boundary_width ??
                  valueNumber(field["boundary_width"])}
                boundaryGoalLine={calibration?.boundary_width_goal_line ??
                  valueNumber(
                    field["boundary_width_goal_line"] ??
                      field["boundary_width"],
                  )}
                oncancel={() => (cornersMode = false)}
                onsaved={cornersSaved}
              />
            {:else}
              <nav class="view-tabs" aria-label="Diagnostic view">
                <button
                  class:active={selectedView === "overlay"}
                  onclick={() => (selectedView = "overlay")}
                >
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
                {#if selectedView === "overlay" && rawSnapshot}
                  <div class="overlay-stage">
                    <img
                      src={`/snapshot/${rawSnapshot.cam_id}/raw?t=${String(cacheBuster)}`}
                      alt="Camera field geometry overlay"
                      onload={drawFieldOverlay}
                    />
                    <canvas
                      bind:this={overlayCanvas}
                      aria-label="Projected field and goal geometry"
                    ></canvas>
                    <div class="overlay-legend">
                      <span class="field-key">Field</span>
                      <span class="goal-key">Goals</span>
                      <span class="marking-key">Markings</span>
                      <span class="boundary-key">Boundary</span>
                      <span class="corner-key">Outer points</span>
                    </div>
                  </div>
                {:else if currentSnapshot}
                  <img
                    src={`/snapshot/${currentSnapshot.cam_id}/${selectedView}?t=${String(cacheBuster)}`}
                    alt={`Camera ${currentSnapshot.cam_id} ${viewLabel(selectedView)}`}
                  />
                {:else}
                  <p>No processor snapshots are available.</p>
                {/if}
              </div>
            {/if}
          </section>

          <div class="panel-row">
            <section class="detections-panel">
              <div class="section-heading compact">
                <div>
                  <h2>Detections</h2>
                  <p class={`detection-state ${detectionState.kind}`}>
                    {detectionState.text}
                  </p>
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
                    {#each blueRobots as robot, index (`blue-${String(index)}-${String(robot.robot_id)}`)}
                      <tr>
                        <td><span class="team-dot blue"></span>Blue robot</td>
                        <td>{text(robot.robot_id)}</td>
                        <td>{number(robot.confidence, 2)}</td>
                        <td>{number(robot.x, 1)}</td>
                        <td>{number(robot.y, 1)}</td>
                        <td>{number(robot.orientation, 3)}</td>
                        <td
                          >{number(robot.pixel_x, 1)}, {number(
                            robot.pixel_y,
                            1,
                          )}</td
                        >
                      </tr>
                    {/each}
                    {#each yellowRobots as robot, index (`yellow-${String(index)}-${String(robot.robot_id)}`)}
                      <tr>
                        <td
                          ><span class="team-dot yellow"></span>Yellow robot</td
                        >
                        <td>{text(robot.robot_id)}</td>
                        <td>{number(robot.confidence, 2)}</td>
                        <td>{number(robot.x, 1)}</td>
                        <td>{number(robot.y, 1)}</td>
                        <td>{number(robot.orientation, 3)}</td>
                        <td
                          >{number(robot.pixel_x, 1)}, {number(
                            robot.pixel_y,
                            1,
                          )}</td
                        >
                      </tr>
                    {/each}
                    {#each balls as ball, index (`ball-${String(index)}`)}
                      <tr>
                        <td><span class="team-dot orange"></span>Ball</td>
                        <td>--</td>
                        <td>{number(ball.confidence, 2)}</td>
                        <td>{number(ball.x, 1)}</td>
                        <td>{number(ball.y, 1)}</td>
                        <td>--</td>
                        <td
                          >{number(ball.pixel_x, 1)}, {number(
                            ball.pixel_y,
                            1,
                          )}</td
                        >
                      </tr>
                    {/each}
                    {#if blueRobots.length + yellowRobots.length + balls.length === 0}
                      <tr
                        ><td colspan="7" class="empty-row"
                          >{detectionLive
                            ? "No objects in the latest frame"
                            : detectionState.text}</td
                        ></tr
                      >
                    {/if}
                  </tbody>
                </table>
              </div>
            </section>

            <ColorPanel
              {cameraLabel}
              camId={rawSnapshot?.cam_id ?? "0"}
              configColors={colorConfig}
              {refreshToken}
            />
          </div>
        </div>

        <aside class="inspector">
          <ServicesPanel
            services={health?.services ?? null}
            reachable={healthReachable}
            onchange={() => void refreshHealth()}
          />

          <section>
            <div class="section-heading compact">
              <h2>Camera input</h2>
              <span class="section-tag">Active config</span>
            </div>
            <dl class="property-grid">
              <div>
                <dt>Device</dt>
                <dd>{text(cameraConfig["path"])}</dd>
              </div>
              <div>
                <dt>Capture</dt>
                <dd>
                  {number(cameraConfig["width"])} x {number(
                    cameraConfig["height"],
                  )}
                </dd>
              </div>
              <div>
                <dt>Processing</dt>
                <dd>
                  {number(cameraConfig["output_width"])} x {number(
                    cameraConfig["output_height"],
                  )}
                </dd>
              </div>
              <div>
                <dt>Capture rate</dt>
                <dd>{number(cameraConfig["fps"])} FPS</dd>
              </div>
              <div>
                <dt>Gain</dt>
                <dd>{number(cameraConfig["gain"], 1)}</dd>
              </div>
              <div>
                <dt>Gamma</dt>
                <dd>{number(cameraConfig["gamma"], 1)}</dd>
              </div>
              <div>
                <dt>Format</dt>
                <dd>{text(cameraConfig["fourcc"])}</dd>
              </div>
              <div>
                <dt>Left crop</dt>
                <dd>{text(cameraConfig["crop_left_half"])}</dd>
              </div>
            </dl>
          </section>

          <section>
            <div class="section-heading compact">
              <h2>Solved camera model</h2>
            </div>
            <dl class="property-grid">
              <div>
                <dt>Focal length</dt>
                <dd>{number(cameraCalibration["focal_length"], 2)}</dd>
              </div>
              <div>
                <dt>Principal point</dt>
                <dd>
                  {number(cameraCalibration["principal_point_x"], 1)}, {number(
                    cameraCalibration["principal_point_y"],
                    1,
                  )}
                </dd>
              </div>
              <div>
                <dt>Image size</dt>
                <dd>
                  {number(cameraCalibration["pixel_image_width"])} x {number(
                    cameraCalibration["pixel_image_height"],
                  )}
                </dd>
              </div>
              <div>
                <dt>Distortion</dt>
                <dd>{number(cameraCalibration["distortion"], 4)}</dd>
              </div>
              <div>
                <dt>Camera height</dt>
                <dd>{number(geometryConfig["camera_height"], 0)} mm</dd>
              </div>
              <div>
                <dt>Translation</dt>
                <dd>
                  {number(cameraCalibration["tx"], 0)}, {number(
                    cameraCalibration["ty"],
                    0,
                  )}, {number(cameraCalibration["tz"], 0)}
                </dd>
              </div>
            </dl>
            <div class="corner-list">
              <span>Field corners px</span>
              <code>{JSON.stringify(geometryConfig["line_corners"] ?? [])}</code
              >
            </div>
          </section>

          <GeometryEditor
            {camId}
            refinement={calibration?.refinement === true}
            {refreshToken}
            onedit={() => (geometryModal = true)}
            onchange={() => {
              void refreshCalibration();
              void refreshHealth();
            }}
          />

          <PerformancePanel
            {uiFrames}
            names={{ [String(camId)]: cameraLabel }}
          />

          <section>
            <div class="section-heading compact">
              <h2>Detection thresholds</h2>
            </div>
            <dl class="property-grid thresholds">
              <div>
                <dt>Circularity</dt>
                <dd>{number(thresholdConfig["circularity"], 1)}</dd>
              </div>
              <div>
                <dt>Score</dt>
                <dd>{number(thresholdConfig["score"], 1)}</dd>
              </div>
              <div>
                <dt>Confidence</dt>
                <dd>{number(thresholdConfig["min_confidence"], 2)}</dd>
              </div>
              <div>
                <dt>Blob limit</dt>
                <dd>{number(thresholdConfig["blobs"])}</dd>
              </div>
              <div>
                <dt>Edge distance</dt>
                <dd>{number(thresholdConfig["min_cam_edge_distance"])}</dd>
              </div>
              <div>
                <dt>Clipping</dt>
                <dd>{number(thresholdConfig["clipping_tolerance"], 1)}</dd>
              </div>
            </dl>
          </section>

          <section>
            <div class="section-heading compact"><h2>Network output</h2></div>
            <dl class="property-grid">
              <div>
                <dt>Vision multicast</dt>
                <dd>
                  {text(networkConfig["vision_ip"])}:{number(
                    networkConfig["vision_port"],
                  )}
                </dd>
              </div>
              <div>
                <dt>Game Controller</dt>
                <dd>
                  {text(networkConfig["gc_ip"])}:{number(
                    networkConfig["gc_port"],
                  )}
                </dd>
              </div>
              <div>
                <dt>Debug stream</dt>
                <dd>
                  {text(streamConfig["ip_base_prefix"])}{number(
                    streamConfig["ip_base_end"],
                  )}:{number(streamConfig["port"])}
                </dd>
              </div>
            </dl>
          </section>
        </aside>
      </div>
    {/if}

    {#if geometryModal}
      <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
      <div
        class="modal-backdrop"
        onclick={(event) => {
          if (event.target === event.currentTarget) geometryModal = false;
        }}
      >
        <div class="modal-box" role="dialog" aria-label="Edit field dimensions">
          <GeometryEditor
            modal
            {camId}
            refinement={calibration?.refinement === true}
            {refreshToken}
            onchange={() => {
              void refreshCalibration();
              void refreshHealth();
              void refreshConfig();
              refreshToken += 1;
            }}
            onclose={(
              result: { kind: "ok" | "error"; text: string } | null,
            ) => {
              geometryModal = false;
              showToast(result);
            }}
          />
        </div>
      </div>
    {/if}

    {#if toast}
      <div class={`toast ${toast.kind}`} role="status">
        {toast.text}
        <button
          onclick={() => {
            showToast(null);
          }}
          aria-label="Dismiss">×</button
        >
      </div>
    {/if}

    <footer>
      <span>{configPayload?.path ?? "Waiting for active configuration"}</span>
      <span>Backend uptime {number(health?.uptime_s)} s</span>
    </footer>
  </main>
  <LogDrawer
    open={drawerOpen}
    piLogFile={health?.services.pi_camera?.log_file ?? null}
    ontoggle={toggleDrawer}
    onopenfile={openLogs}
  />
{/if}

<style>
  :global(*) {
    box-sizing: border-box;
  }

  /* Theme tokens: light is the default; App sets data-theme="dark" on
     <html> (explicitly or from the system preference). */
  :global(:root) {
    color-scheme: light;
    --page: #e9eeeb;
    --surface: #ffffff;
    --surface-2: #f5f7f6;
    --text: #17201b;
    --text-muted: #5b665f;
    --text-faint: #98aaa0;
    --border: #c8d0cb;
    --border-soft: #e7ece9;
    --ok: #1f5e3d;
    --ok-bg: #e8f5ed;
    --ok-border: #bfe0cc;
    --bad: #8a2d29;
    --bad-bg: #fbeceb;
    --bad-border: #efc9c6;
    --warn: #8a5a00;
    --warn-bg: #fff4dc;
    --warn-border: #f0dca8;
    --yellow-bg: #fff4c7;
    --blue-bg: #e7f1fb;
    --blue-text: #1e5c9e;
    --code-bg: #eef2ef;
  }

  :global(:root[data-theme="dark"]) {
    color-scheme: dark;
    --page: #121815;
    --surface: #1b2420;
    --surface-2: #222d28;
    --text: #e3ebe6;
    --text-muted: #a5b3ab;
    --text-faint: #7d8c84;
    --border: #34423b;
    --border-soft: #29342f;
    --ok: #8fdcae;
    --ok-bg: #173527;
    --ok-border: #2b5a40;
    --bad: #ff9d97;
    --bad-bg: #3a1d1b;
    --bad-border: #6b2f2b;
    --warn: #f2c66b;
    --warn-bg: #352a12;
    --warn-border: #5e4a1c;
    --yellow-bg: #3a3311;
    --blue-bg: #14283b;
    --blue-text: #8cc4ff;
    --code-bg: #26312c;
  }

  :global(html) {
    background: var(--page);
  }

  :global(body) {
    margin: 0;
    min-width: 320px;
    color: var(--text);
    background: var(--page);
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
    color: #f7faf8;
    background: #202a25;
    border-bottom: 3px solid #39a56c;
  }

  .identity,
  .header-status,
  .section-heading,
  .detection-counts,
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
    border: 1px solid #7fc49c;
    border-radius: 4px;
    color: #bfe8cf;
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
    color: var(--text-faint);
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
    color: #dce5e0;
    border: 1px solid #46544d;
    background: #29352f;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    flex: 0 0 8px;
    border-radius: 50%;
    background: #c5524d;
  }

  .status-badge.healthy .status-dot {
    background: #48b477;
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
    color: var(--text-muted);
    background: var(--surface-2);
    border: 1px solid var(--border);
  }

  .view-tabs {
    min-height: 38px;
    display: flex;
    gap: 2px;
    padding: 4px 6px;
    overflow-x: auto;
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
  }

  .view-tabs button {
    height: 30px;
    flex: 0 0 auto;
    padding: 0 10px;
    border: 1px solid transparent;
    border-radius: 3px;
    color: var(--text-muted);
    background: transparent;
    cursor: pointer;
    font-size: 12px;
  }

  .view-tabs button:hover {
    color: var(--text);
    border-color: var(--border);
    background: var(--surface);
  }

  .view-tabs button.active {
    color: #ffffff;
    background: #276f4b;
    border-color: #276f4b;
  }

  .image-stage {
    width: 100%;
    aspect-ratio: 16 / 9;
    display: grid;
    place-items: center;
    background: #111713;
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
    color: #edf3ef;
    background: rgba(17, 23, 19, 0.78);
    border: 1px solid rgba(220, 232, 224, 0.38);
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

  /* Detections and Colours side by side on wide screens, stacked when
     narrow. */
  .panel-row {
    min-width: 0;
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 360px), 1fr));
    gap: 12px;
    align-items: start;
  }

  .header-button,
  .panic-button {
    height: 28px;
    padding: 0 10px;
    color: #dce5e0;
    background: #29352f;
    border: 1px solid #46544d;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
    white-space: nowrap;
  }

  .header-button:hover:not(:disabled) {
    border-color: #7fc49c;
  }

  .panic-button {
    color: #ffffff;
    background: #c0322b;
    border-color: #e0574f;
    font-weight: 700;
  }

  .panic-button:hover {
    background: #a8261f;
  }

  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    padding: 16px;
    background: rgba(0, 0, 0, 0.55);
  }

  .modal-box {
    width: min(720px, 100%);
    max-height: calc(100vh - 32px);
    overflow: auto;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 6px;
    box-shadow: 0 12px 40px rgba(0, 0, 0, 0.4);
  }

  .toast {
    position: fixed;
    right: 16px;
    bottom: 16px;
    z-index: 60;
    max-width: min(480px, calc(100vw - 32px));
    padding: 10px 14px;
    border-radius: 5px;
    font-size: 13px;
    box-shadow: 0 6px 24px rgba(0, 0, 0, 0.3);
  }

  .toast.ok {
    color: var(--ok);
    background: var(--ok-bg);
    border: 1px solid var(--ok-border);
  }

  .toast.error {
    color: var(--bad);
    background: var(--bad-bg);
    border: 1px solid var(--bad-border);
  }

  .toast button {
    margin-left: 10px;
    color: inherit;
    background: none;
    border: 0;
    cursor: pointer;
    font-size: 15px;
  }

  .help-loading {
    padding: 24px;
    color: var(--text-muted);
  }

  .viewer-actions {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px;
  }

  .small-state.ok {
    color: var(--ok);
    background: var(--ok-bg);
    border-color: var(--ok-border);
  }

  .small-state.warn {
    color: var(--warn);
    background: var(--warn-bg);
    border-color: var(--warn-border);
  }

  .corners-button {
    height: 28px;
    padding: 0 10px;
    border: 1px solid #276f4b;
    border-radius: 3px;
    color: #ffffff;
    background: #276f4b;
    cursor: pointer;
    font-size: 12px;
  }

  .corners-button.icon {
    display: inline-grid;
    place-items: center;
    width: 32px;
    padding: 0;
  }

  .header-button.active {
    border-color: #7fc49c;
  }

  .corners-button.active {
    color: #276f4b;
    background: var(--surface);
  }

  .corners-message {
    margin: 0;
    padding: 7px 12px;
    color: var(--ok);
    background: var(--ok-bg);
    border-bottom: 1px solid var(--ok-border);
    font-size: 12px;
  }

  .corners-message button {
    margin-left: 8px;
    border: 1px solid var(--ok-border);
    border-radius: 3px;
    background: var(--surface);
    cursor: pointer;
    font-size: 11px;
  }

  .detection-state {
    font-weight: 600;
  }

  .section-heading p.detection-state.live {
    color: var(--ok);
  }

  .section-heading p.detection-state.wait {
    color: var(--warn);
  }

  .section-heading p.detection-state.down {
    color: var(--bad);
  }

  .image-stage p {
    color: #aab6af;
    font-size: 13px;
  }

  .detection-counts {
    gap: 6px;
  }

  .detection-counts span {
    min-height: 24px;
    color: var(--text-muted);
    background: var(--surface-2);
  }

  .detection-counts .blue-count {
    color: var(--blue-text);
    background: var(--blue-bg);
  }

  .detection-counts .yellow-count {
    color: var(--warn);
    background: var(--yellow-bg);
  }

  .detections-panel table {
    min-width: 600px;
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
    border-bottom: 1px solid var(--border-soft);
    white-space: nowrap;
  }

  th {
    color: var(--text-muted);
    background: var(--surface-2);
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
    background: #2a80c8;
  }

  .team-dot.yellow {
    background: #e5be22;
  }

  .team-dot.orange {
    background: #e3732f;
  }

  .empty-row {
    height: 58px;
    text-align: center;
    color: var(--text-muted);
  }

  .inspector section {
    overflow: visible;
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
    border-bottom: 1px solid var(--border-soft);
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
    color: var(--text);
    font-size: 10px;
    line-height: 1.45;
    text-transform: none;
    overflow-wrap: anywhere;
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

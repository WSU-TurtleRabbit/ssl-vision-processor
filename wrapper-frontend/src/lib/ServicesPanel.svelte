<script lang="ts">
  import { onMount } from "svelte";
  import CameraName from "./CameraName.svelte";
  import type {
    CalibrationHealth,
    HealthServices,
    VisionHealth,
  } from "./health";

  // Services panel: Pi camera (only for http(s) camera paths), the
  // vision_processor supervisor (start/stop/restart + log), the backend
  // itself and the field calibration state — all from GET /api/health.

  let {
    services,
    reachable,
    onchange,
  }: {
    services: HealthServices | null;
    reachable: boolean;
    onchange: () => void;
  } = $props();

  let busy = $state<string | null>(null);
  let message = $state<{ kind: "ok" | "error"; text: string } | null>(null);
  let showLog = $state(false);
  let logLines = $state<string[]>([]);
  let logElement = $state<HTMLPreElement>();

  let vision = $derived(services?.vision_processor);
  let pi = $derived(services?.pi_camera);
  let backend = $derived(services?.wrapper_backend);
  let calibration = $derived(services?.field_calibration);

  function duration(seconds: number | null | undefined): string {
    if (seconds === null || seconds === undefined) return "--";
    if (seconds < 60) return `${seconds.toFixed(0)} s`;
    if (seconds < 3600) return `${(seconds / 60).toFixed(0)} min`;
    return `${(seconds / 3600).toFixed(1)} h`;
  }

  function visionState(v: VisionHealth | undefined): string {
    if (!v) return "unknown";
    if (v.state) return v.state;
    if (v.running) return v.managed ? "running" : "running (started by hand)";
    if (v.want_running) return "restarting...";
    return "stopped";
  }

  function calibrationState(c: CalibrationHealth | undefined): string {
    if (!c) return "unknown";
    const cam = `camera ${String(c.cam_id ?? 0)}`;
    if (c.state === "recalibrating") return `recalibrating ${cam}...`;
    if (c.state === "calibrated") return `${cam} calibrated`;
    return `${cam} not calibrated`;
  }

  async function action(name: "start" | "stop" | "restart"): Promise<void> {
    busy = name;
    message = null;
    try {
      const response = await fetch(`/api/vision/${name}`, { method: "POST" });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        pid?: number | null;
      };
      message = response.ok
        ? {
            kind: "ok",
            text:
              name === "stop"
                ? "vision_processor stopped."
                : `vision_processor ${name === "start" ? "started" : "restarted"} (pid ${String(result.pid ?? "--")}).`,
          }
        : {
            kind: "error",
            text: result.error ?? `HTTP ${String(response.status)}`,
          };
    } catch (error) {
      message = { kind: "error", text: String(error) };
    } finally {
      busy = null;
      onchange();
      if (showLog) void refreshLog();
    }
  }

  // Pi camera remote control (POST /api/camera/<command>, proxied to the Pi).
  let cameraBusy = $state(false);
  let cameraMessage = $state<{ kind: "ok" | "error"; text: string } | null>(
    null,
  );

  async function cameraAction(command: "restart"): Promise<void> {
    cameraBusy = true;
    cameraMessage = null;
    try {
      const response = await fetch(`/api/camera/${command}`, {
        method: "POST",
      });
      const result = (await response.json().catch(() => ({}))) as {
        ok?: boolean;
        error?: string;
        note?: string;
        dropped_stream?: boolean;
      };
      if (response.ok && result.ok !== false) {
        cameraMessage = {
          kind: "ok",
          text: result.dropped_stream
            ? "Camera stream dropped; vision_processor reconnects."
            : "Camera restarted (no stream was running).",
        };
      } else {
        cameraMessage = {
          kind: "error",
          text: result.error ?? `HTTP ${String(response.status)}`,
        };
      }
    } catch (error) {
      cameraMessage = { kind: "error", text: String(error) };
    } finally {
      cameraBusy = false;
      onchange();
    }
  }

  async function refreshLog(): Promise<void> {
    try {
      const response = await fetch("/api/vision/log");
      if (!response.ok) return;
      const result = (await response.json()) as { lines?: string[] };
      const stick =
        !logElement ||
        logElement.scrollTop + logElement.clientHeight >=
          logElement.scrollHeight - 8;
      logLines = result.lines ?? [];
      if (stick)
        requestAnimationFrame(() => {
          if (logElement) logElement.scrollTop = logElement.scrollHeight;
        });
    } catch {
      // Backend reachability is shown by the rows above.
    }
  }

  onMount(() => {
    const timer = setInterval(() => {
      if (showLog) void refreshLog();
    }, 1000);
    return () => {
      clearInterval(timer);
    };
  });
</script>

<section class="services">
  <div class="section-heading compact"><h2>Services</h2></div>
  <div class="service-list">
    {#if pi}
      <div class="service-row">
        <span class:online={pi.running} class="service-indicator"></span>
        <span class="name"
          >Pi camera ·
          <CameraName
            camId={calibration?.cam_id ?? 0}
            name={pi.camera_name}
            onrenamed={onchange}
          /></span
        >
        <span class="detail">
          {#if pi.running}
            online · {pi.streaming
              ? `streaming to ${pi.client ?? "?"}`
              : "idle (not streaming)"}
            {#if pi.size}· {pi.size}{/if}
            {#if pi.fps}· {pi.fps} fps{/if}
            {#if pi.closed}· <strong class="closed">closed by operator</strong
              >{/if}
          {:else}
            offline{pi.error ? ` (${pi.error})` : ""}
          {/if}
        </span>
        {#if pi.running}
          <span
            class="buttons"
            title={pi.control
              ? ""
              : "No token configured on the Pi/backend, see Help → Pi camera → 6"}
          >
            <!-- Close / Open / Shut down exist as API only (POST
                 /api/camera/*); the UI offers just Restart. -->
            <button
              class="ghost"
              disabled={!pi.control || cameraBusy}
              onclick={() => cameraAction("restart")}>Restart camera</button
            >
          </span>
        {/if}
      </div>
      {#if cameraMessage}
        <p class={`notice ${cameraMessage.kind}`}>{cameraMessage.text}</p>
      {/if}
    {/if}

    <div class="service-row vision">
      <span class:online={vision?.running} class="service-indicator"></span>
      <span class="name">vision_processor</span>
      <span class="detail">
        {visionState(vision)}
        {#if vision?.pid}· pid {vision.pid}{/if}
        {#if vision?.managed && vision.uptime_s !== null && vision.uptime_s !== undefined}
          · up {duration(vision.uptime_s)}
        {/if}
        {#if (vision?.restarts ?? 0) > 0}· {vision?.restarts} restarts{/if}
        · last detection {vision?.last_detection_age_s !== null &&
        vision?.last_detection_age_s !== undefined
          ? `${duration(vision.last_detection_age_s)} ago`
          : "never"}
      </span>
      <span class="buttons">
        <button
          class="ghost"
          disabled={!reachable || busy !== null || vision?.running === true}
          onclick={() => action("start")}>Start</button
        >
        <button
          class="ghost"
          disabled={!reachable || busy !== null || vision?.running !== true}
          onclick={() => action("stop")}>Stop</button
        >
        <button
          class="ghost"
          disabled={!reachable || busy !== null}
          onclick={() => action("restart")}>Restart</button
        >
        <button
          class="ghost"
          class:active={showLog}
          onclick={() => {
            showLog = !showLog;
            if (showLog) void refreshLog();
          }}>{showLog ? "Hide log" : "Show log"}</button
        >
      </span>
    </div>
    {#if vision?.last_status && vision.running}
      <p class="status-line" title="vision_processor's periodic status line">
        {vision.last_status.line}
      </p>
    {/if}
    {#if vision?.last_warning}
      <p
        class="status-line warning"
        title="Last line vision_processor wrote to stderr (WARN)"
      >
        last warning ({duration(Date.now() / 1000 - vision.last_warning.at)} ago):
        {vision.last_warning.line.replace(/^\[[^\]]*\]\s*/, "")}
      </p>
    {/if}
    {#if vision?.binary_exists === false}
      <p class="notice error">
        vision_processor binary not found — build it or pass --vision-binary.
      </p>
    {/if}
    {#if message}
      <p class={`notice ${message.kind}`}>{message.text}</p>
    {/if}
    {#if showLog}
      <pre bind:this={logElement} class="log">{logLines.length
          ? logLines.join("\n")
          : "(no output yet)"}</pre>
    {/if}

    <div class="service-row">
      <span
        class:online={reachable && backend?.running}
        class="service-indicator"
      ></span>
      <span class="name">wrapper backend</span>
      <span class="detail">
        {reachable ? `ok · up ${duration(backend?.uptime_s)}` : "unreachable"}
      </span>
    </div>

    <div class="service-row">
      <span
        class:online={calibration?.state === "calibrated"}
        class:pending={calibration?.state === "recalibrating"}
        class="service-indicator"
      ></span>
      <span class="name">field calibration</span>
      <span class="detail">{calibrationState(calibration)}</span>
    </div>
  </div>
</section>

<style>
  .services {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
  }

  .section-heading {
    min-height: 42px;
    display: flex;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    margin: 0;
    font-size: 13px;
    line-height: 1.25;
    font-weight: 700;
  }

  .service-list {
    padding: 4px 12px 8px;
  }

  .service-row {
    min-height: 31px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 9px;
    padding: 5px 0;
    border-bottom: 1px solid var(--border-soft);
    font-size: 12px;
  }

  .service-row:last-child {
    border-bottom: 0;
  }

  .service-indicator {
    width: 8px;
    height: 8px;
    flex: 0 0 8px;
    border-radius: 50%;
    background: #c5524d;
  }

  .service-indicator.online {
    background: #48b477;
  }

  .service-indicator.pending {
    background: #e5be22;
  }

  .name {
    font-weight: 600;
  }

  .detail {
    color: var(--text-muted);
    flex: 1 1 160px;
  }

  .buttons {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 4px;
    margin-left: auto;
  }

  button {
    height: 24px;
    padding: 0 8px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
  }

  button:disabled {
    cursor: default;
    opacity: 0.5;
  }

  button.ghost {
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
  }

  button.ghost:hover:not(:disabled),
  button.ghost.active {
    color: var(--text);
    border-color: #276f4b;
  }

  .status-line {
    margin: 2px 0 4px 17px;
    color: var(--text-muted);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .status-line.warning {
    color: var(--bad);
  }

  .closed {
    color: var(--bad);
  }

  .log {
    max-height: 260px;
    margin: 4px 0 6px;
    padding: 6px 8px;
    overflow: auto;
    color: #dce5e0;
    background: #111713;
    border-radius: 3px;
    font-size: 10px;
    line-height: 1.4;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .notice {
    margin: 4px 0;
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 12px;
  }

  .notice.error {
    color: var(--bad);
    background: var(--bad-bg);
    border: 1px solid var(--bad-border);
  }

  .notice.ok {
    color: var(--ok);
    background: var(--ok-bg);
    border: 1px solid var(--ok-border);
  }
</style>

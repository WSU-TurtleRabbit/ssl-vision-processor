<script lang="ts">
  import { onMount } from "svelte";
  import CameraName from "./CameraName.svelte";
  import type {
    CalibrationStatus,
    DataMap,
    HealthResponse,
    PiCameraHealth,
    VisionHealth,
  } from "./health";
  import { asRecord, num } from "./health";

  // Cameras tab: one block per camera (ready for several): name, Pi status
  // and capture buttons, raw-stream pop-out, camera input, calibration,
  // solved model; scan + tokens at the bottom.

  let {
    health,
    calibration,
    config,
    calib,
    onchange,
    oncorners,
    onlens,
    ontoast,
  }: {
    health: HealthResponse | null;
    calibration: CalibrationStatus | null;
    config: DataMap;
    calib: DataMap;
    onchange: () => void;
    oncorners: () => void;
    onlens: () => void;
    ontoast: (toast: { kind: "ok" | "error"; text: string }) => void;
  } = $props();

  const QUADRANT = ["−x −y", "−x +y", "+x +y", "+x −y"];

  let camId = $derived(health?.cam_id ?? 0);
  let pi = $derived<PiCameraHealth | undefined>(health?.services.pi_camera);
  let vision = $derived<VisionHealth | undefined>(
    health?.services.vision_processor,
  );
  let cameraConfig = $derived(asRecord(config["camera"]));
  let busy = $state(false);
  let confirmStop = $state(false);
  let showLog = $state(false);
  let logLines = $state<string[]>([]);

  async function post(
    url: string,
    body?: unknown,
  ): Promise<Record<string, unknown>> {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    if (!response.ok || data["ok"] === false)
      throw new Error(
        typeof data["error"] === "string"
          ? data["error"]
          : `HTTP ${String(response.status)}`,
      );
    return data;
  }

  async function run(
    label: string,
    work: () => Promise<string>,
  ): Promise<void> {
    busy = true;
    confirmStop = false;
    try {
      ontoast({ kind: "ok", text: await work() });
    } catch (error) {
      ontoast({ kind: "error", text: `${label}: ${String(error)}` });
    } finally {
      busy = false;
      onchange();
    }
  }

  const startCapture = (): Promise<void> =>
    run("Start capture", async () => {
      await post("/api/camera/open");
      if (vision?.running)
        return "Camera open; vision_processor already running.";
      const started = await post("/api/vision/start");
      const pid = started["pid"];
      return `Camera open; vision_processor started (pid ${typeof pid === "number" ? String(pid) : "--"}).`;
    });
  const stopCapture = (): Promise<void> =>
    run("Stop capture", async () => {
      await post("/api/camera/close");
      return "Capture stopped: vision_processor stopped, camera closed.";
    });
  const restartCamera = (): Promise<void> =>
    run("Restart camera", async () => {
      const r = await post("/api/camera/restart");
      return r["dropped_stream"]
        ? "Camera stream dropped; vision_processor reconnects."
        : "Camera restarted (no stream was running).";
    });
  const recalibrate = (): Promise<void> =>
    run("Recalibrate", async () => {
      const r = await post("/api/calibration/recalibrate", { cam_id: camId });
      return typeof r["message"] === "string" ? r["message"] : "Recalibrating.";
    });

  // Direct Pi feed (MJPEG): the Pi allows one viewer, so only while
  // vision_processor is stopped.
  let streamUrl = $derived.by(() => {
    const path = cameraConfig["path"];
    if (typeof path !== "string" || !/^https?:\/\//.test(path)) return null;
    try {
      const url = new URL(path);
      return `${url.protocol}//${url.host}/stream`;
    } catch {
      return null;
    }
  });

  async function refreshLog(): Promise<void> {
    if (!pi?.log_file) return;
    try {
      const response = await fetch(
        `/api/logs/${encodeURIComponent(pi.log_file)}?tail=60`,
      );
      if (response.ok)
        logLines = ((await response.json()) as { lines: string[] }).lines;
    } catch {
      // reachability shown elsewhere
    }
  }

  onMount(() => {
    const timer = setInterval(() => {
      if (showLog) void refreshLog();
    }, 2000);
    return () => {
      clearInterval(timer);
    };
  });

  // Read-only text lines, unset values omitted.
  function lines(pairs: [string, unknown][]): string {
    return pairs
      .filter(
        ([, v]) => v !== undefined && v !== null && v !== "" && v !== "--",
      )
      .map(([k, v]) => `${k} ${String(v)}`)
      .join(" · ");
  }

  let inputText = $derived(
    lines([
      ["path", cameraConfig["path"]],
      ["capture", pi?.size],
      ["fps", pi?.fps],
      ["device", pi?.device],
      [
        "processing",
        typeof cameraConfig["output_width"] === "number"
          ? `${num(cameraConfig["output_width"])}×${num(cameraConfig["output_height"])}`
          : undefined,
      ],
      ["driver", cameraConfig["driver"]],
    ]),
  );
  let modelText = $derived(
    calib["focal_length"] === undefined
      ? ""
      : lines([
          ["focal", num(calib["focal_length"], 1)],
          [
            "pp",
            `${num(calib["principal_point_x"], 0)}, ${num(calib["principal_point_y"], 0)}`,
          ],
          ["distortion", num(calib["distortion"], 4)],
          [
            "position",
            `${num(calib["derived_camera_world_tx"], 0)}, ${num(calib["derived_camera_world_ty"], 0)}, ${num(calib["derived_camera_world_tz"], 0)} mm`,
          ],
        ]),
  );
  let corners = $derived(
    Array.isArray(calibration?.corners)
      ? calibration.corners.map((c, i) =>
          Array.isArray(c)
            ? `${String(i + 1)}·${QUADRANT[i] ?? ""} (${num(c[0], 0)}, ${num(c[1], 0)})`
            : "",
        )
      : [],
  );
  let lensText = $derived.by(() => {
    const cj = calibration?.calib_json;
    if (!cj || typeof cj.distortion_k2 !== "number") return "";
    const pp = cj.principal_point ?? [];
    const n = Array.isArray(calibration.distortion_lines)
      ? calibration.distortion_lines.length
      : 0;
    return `k2 ${cj.distortion_k2.toFixed(3)} · pp ${num(pp[0])}, ${num(pp[1])}${n ? ` · ${String(n)} lines` : " · no lens lines"}`;
  });
</script>

<section class="camera-block">
  <h3>
    <CameraName
      {camId}
      name={health?.camera_name ?? null}
      showId
      onrenamed={onchange}
    />
  </h3>

  {#if pi}
    <p class="row">
      <span
        class="dot"
        class:ok={pi.running && !pi.closed}
        class:warn={pi.closed}
      ></span>
      Pi {pi.running
        ? pi.closed
          ? "closed by operator"
          : pi.streaming
            ? `streaming to ${pi.client ?? "?"}`
            : "idle"
        : `offline${pi.error ? ` (${pi.error})` : ""}`}
      {#if pi.running}· control {pi.control ? "on" : "off"}{/if}
    </p>
    {#if pi.last_error}
      <p
        class="row warn-text"
        title="latest failed/error line of the Pi service"
      >
        {pi.last_error.text}
      </p>
    {/if}
    <p class="buttons">
      {#if confirmStop}
        <span class="warn-text"
          >Stop vision_processor and close the camera?</span
        >
        <button class="danger" disabled={busy} onclick={stopCapture}
          >Yes, stop</button
        >
        <button onclick={() => (confirmStop = false)}>Cancel</button>
      {:else}
        <button
          disabled={!pi.control ||
            busy ||
            (vision?.running === true && !pi.closed)}
          title="Open the camera on the Pi and start vision_processor"
          onclick={startCapture}>Start capture</button
        >
        <button
          disabled={!pi.control ||
            busy ||
            (vision?.running !== true && !pi.streaming)}
          title="Stop vision_processor and close the camera"
          onclick={() => (confirmStop = true)}>Stop capture</button
        >
        <button
          disabled={!pi.control || busy}
          title="Drop the Pi's stream; vision_processor reconnects"
          onclick={restartCamera}>Restart camera</button
        >
        <button
          disabled={!pi.log_file}
          class:active={showLog}
          onclick={() => {
            showLog = !showLog;
            if (showLog) void refreshLog();
          }}>{showLog ? "Hide log" : "Show log"}</button
        >
        {#if streamUrl}
          {#if vision?.running}
            <button
              disabled
              title="Stop capture first — the Pi allows one viewer"
              >Raw stream ↗</button
            >
          {:else}
            <a
              class="button"
              href={streamUrl}
              target="_blank"
              rel="noopener noreferrer"
              title="Direct Pi feed in a new tab">Raw stream ↗</a
            >
          {/if}
        {/if}
      {/if}
    </p>
    {#if showLog}
      <pre class="log">{logLines.length
          ? logLines.join("\n")
          : "(no Pi log lines yet)"}</pre>
    {/if}
  {:else}
    <p class="row muted">
      not a network camera — Start/Stop vision_processor in System
    </p>
  {/if}

  {#if inputText}
    <p class="text"><span class="label">input</span> {inputText}</p>
  {/if}

  <p class="text">
    <span class="label">calibration</span>
    <span
      class="dot"
      class:ok={calibration?.state === "calibrated"}
      class:warn={calibration?.state === "recalibrating"}
    ></span>
    {calibration?.state.replaceAll("_", " ") ?? "unknown"}
    {#if corners.length}· {corners.join(" ")}{/if}
    {#if lensText}· {lensText}{/if}
  </p>
  <p class="buttons">
    <button onclick={oncorners}>Set corners</button>
    <button onclick={onlens}>Lens</button>
    <button disabled={busy} onclick={recalibrate}>Recalibrate</button>
  </p>
  {#if modelText}
    <p class="text"><span class="label">model</span> {modelText}</p>
  {/if}
</section>

<style>
  section {
    margin-bottom: 12px;
  }

  h3 {
    margin: 0 0 4px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }

  .row,
  .text {
    margin: 2px 0;
    font-size: 12px;
    overflow-wrap: anywhere;
  }

  .text {
    color: var(--text);
  }

  .label {
    display: inline-block;
    min-width: 72px;
    color: var(--text-muted);
  }

  .muted {
    color: var(--text-muted);
  }

  .warn-text {
    color: var(--warn);
  }

  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-right: 5px;
    border-radius: 50%;
    background: var(--bad);
    vertical-align: -1px;
  }

  .dot.ok {
    background: var(--ok);
  }

  .dot.warn {
    background: var(--warn);
  }

  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 4px 0 6px;
  }

  button {
    height: 26px;
    padding: 0 9px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    font: inherit;
    font-size: 12px;
  }

  button:disabled {
    cursor: default;
    opacity: 0.45;
  }

  a.button {
    display: inline-flex;
    align-items: center;
    height: 26px;
    padding: 0 9px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    font-size: 12px;
    text-decoration: none;
  }

  button.active {
    border-color: var(--accent);
  }

  button.danger {
    color: #fff;
    background: #a8423d;
    border-color: #a8423d;
  }

  .log {
    max-height: 180px;
    margin: 0 0 6px;
    padding: 6px 8px;
    overflow: auto;
    color: #dce5e0;
    background: #111713;
    border-radius: 4px;
    font-size: 10px;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
</style>

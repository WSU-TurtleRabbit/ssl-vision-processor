<script lang="ts">
  import { onMount } from "svelte";
  import CameraScan from "./CameraScan.svelte";
  import type { HealthResponse } from "./health";
  import { duration } from "./health";

  // System tab: cameras summary (→ Cameras tab) and processing:
  // vision_processor, wrapper backend, game controller.

  let {
    health,
    reachable,
    onchange,
    oncameras,
    onlogs,
    ontoast,
  }: {
    health: HealthResponse | null;
    reachable: boolean;
    onchange: () => void;
    oncameras: () => void;
    onlogs: () => void;
    ontoast: (toast: { kind: "ok" | "error"; text: string }) => void;
  } = $props();

  let vision = $derived(health?.services.vision_processor);
  let pi = $derived(health?.services.pi_camera);
  let backend = $derived(health?.services.wrapper_backend);
  let gc = $derived(health?.services.game_controller);
  let busy = $state<string | null>(null);
  let showScan = $state(false);
  let showLog = $state(false);
  let logLines = $state<string[]>([]);
  let logElement = $state<HTMLPreElement>();

  async function action(name: "start" | "stop" | "restart"): Promise<void> {
    busy = name;
    try {
      const response = await fetch(`/api/vision/${name}`, { method: "POST" });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        pid?: number | null;
      };
      if (!response.ok)
        throw new Error(result.error ?? `HTTP ${String(response.status)}`);
      ontoast({
        kind: "ok",
        text:
          name === "stop"
            ? "vision_processor stopped."
            : `vision_processor ${name === "start" ? "started" : "restarted"} (pid ${String(result.pid ?? "--")}).`,
      });
    } catch (error) {
      ontoast({ kind: "error", text: String(error) });
    } finally {
      busy = null;
      onchange();
      if (showLog) void refreshLog();
    }
  }

  // Processing-size presets (camera.quality_presets in the vision config). Switching
  // rescales the field corners and restarts vision_processor (POST /api/camera/quality).
  const QUALITY_HINTS: Record<string, string> = {
    low: "lowest delay and CPU, for unattended running",
    medium: "a bit more robot confidence",
    max: "most confident, ~25 % more CPU",
  };
  let quality = $state<{
    presets: Record<string, [number, number]>;
    current: string | null;
  } | null>(null);

  async function refreshQuality(): Promise<void> {
    try {
      const response = await fetch("/api/camera/quality");
      if (response.ok) quality = (await response.json()) as typeof quality;
    } catch {
      // Optional: the row stays hidden without presets.
    }
  }

  async function setQuality(name: string): Promise<void> {
    busy = `quality-${name}`;
    try {
      const response = await fetch("/api/camera/quality", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quality: name }),
      });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        message?: string;
      };
      if (!response.ok)
        throw new Error(result.error ?? `HTTP ${String(response.status)}`);
      ontoast({
        kind: "ok",
        text: `Quality: ${name}. ${result.message ?? ""}`,
      });
    } catch (error) {
      ontoast({ kind: "error", text: String(error) });
    } finally {
      busy = null;
      await refreshQuality();
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
      // reachability shown by the backend row
    }
  }

  onMount(() => {
    void refreshQuality();
    const timer = setInterval(() => {
      if (showLog) void refreshLog();
    }, 2000);
    return () => {
      clearInterval(timer);
    };
  });

  function piText(): string {
    if (!pi) return "not a network camera";
    if (!pi.running) return `offline${pi.error ? ` (${pi.error})` : ""}`;
    if (pi.closed) return "closed by operator";
    return pi.streaming ? `streaming to ${pi.client ?? "?"}` : "idle";
  }
</script>

<section>
  <h3>
    Cameras
    <button class="link" onclick={oncameras}>open Cameras tab →</button>
    <button
      class="link"
      onclick={() => {
        showScan = !showScan;
      }}>{showScan ? "hide scan" : "Scan network…"}</button
    >
  </h3>
  <p class="row">
    <span
      class="dot"
      class:ok={pi?.running && !pi.closed}
      class:warn={pi?.closed}
    ></span>
    {health?.camera_name ?? `Camera ${String(health?.cam_id ?? 0)}`} · {piText()}
  </p>
  {#if showScan}
    <CameraScan {onchange} />
  {/if}
</section>

<section>
  <h3>Processing</h3>

  <div class="service">
    <p class="row">
      <span
        class="dot"
        class:ok={vision?.running}
        class:warn={vision?.want_running && !vision.running}
      ></span>
      <strong>vision_processor</strong>
      {vision?.state ?? "unknown"}{#if vision?.pid}· pid {vision.pid}{/if}{#if vision?.managed && vision.uptime_s != null}·
        up {duration(vision.uptime_s)}{/if}{#if (vision?.restarts ?? 0) > 0}· {vision?.restarts}
        restarts{/if}
      <span class="buttons">
        <button
          disabled={!reachable || busy !== null || vision?.running === true}
          onclick={() => action("start")}>Start</button
        >
        <button
          disabled={!reachable || busy !== null || vision?.running !== true}
          onclick={() => action("stop")}>Stop</button
        >
        <button
          disabled={!reachable || busy !== null}
          onclick={() => action("restart")}>Restart</button
        >
        <button
          class:active={showLog}
          onclick={() => {
            showLog = !showLog;
            if (showLog) void refreshLog();
          }}>{showLog ? "Hide log" : "Log"}</button
        >
      </span>
    </p>
    {#if quality && Object.keys(quality.presets).length > 0}
      <p class="row">
        <strong>Quality</strong>
        <span class="buttons">
          {#each Object.entries(quality.presets) as [name, [w, h]] (name)}
            <button
              class:active={quality.current === name}
              title={`${String(w)}×${String(h)}: ${QUALITY_HINTS[name] ?? ""}. Switching restarts vision_processor.`}
              disabled={!reachable || busy !== null || quality.current === name}
              onclick={() => setQuality(name)}>{name} · {w}×{h}</button
            >
          {/each}
        </span>
      </p>
    {/if}
    {#if vision?.last_status && vision.running}
      <p class="sub">{vision.last_status.line}</p>
    {/if}
    {#if vision?.last_warning}
      <p class="sub warn-text" title="Last warning of the current run">
        {vision.last_warning.line.replace(/^\[[^\]]*\]\s*/, "")}
      </p>
    {/if}
    {#if vision?.binary_exists === false}
      <p class="sub warn-text">
        vision_processor binary not found — build it or pass --vision-binary.
      </p>
    {/if}
    {#if showLog}
      <pre bind:this={logElement} class="log">{logLines.length
          ? logLines.join("\n")
          : "(no output yet)"}</pre>
    {/if}
  </div>

  <div class="service">
    <p class="row">
      <span class="dot" class:ok={reachable && backend?.running}></span>
      <strong>wrapper backend</strong>
      {reachable ? `up ${duration(backend?.uptime_s)}` : "unreachable"}
      {#if backend?.logs_dir}
        · <button class="link" onclick={onlogs} title={backend.logs_dir}
          >logs</button
        >
      {/if}
    </p>
  </div>

  <div class="service">
    <p class="row">
      <span
        class="dot"
        class:ok={gc?.running}
        class:warn={!gc?.running && gc?.process_running}
      ></span>
      <strong>game controller</strong>
      {#if gc?.running}
        receiving on {gc.group}: {gc.stage} · {gc.command}
        {#if gc.yellow ?? gc.blue}· {gc.yellow ?? "?"}
          {gc.yellow_score ?? ""} : {gc.blue_score ?? ""}
          {gc.blue ?? "?"}{/if}
        · {gc.last_packet_age_s} s ago
      {:else}
        not seen on {gc?.group ??
          "the referee multicast"}{#if gc?.last_packet_age_s != null}
          · last packet {duration(gc.last_packet_age_s)} ago{/if}
      {/if}
      {#if gc?.process_running}· process running (pid {gc.pid}){/if}
    </p>
  </div>
</section>

<style>
  section {
    margin-bottom: 12px;
  }

  h3 {
    display: flex;
    align-items: baseline;
    gap: 10px;
    margin: 0 0 4px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--text-muted);
  }

  .link {
    padding: 0;
    color: var(--accent);
    background: none;
    border: 0;
    cursor: pointer;
    font: inherit;
    text-transform: none;
    letter-spacing: 0;
  }

  .service {
    padding: 4px 0;
    border-bottom: 1px solid var(--border-soft);
  }

  .row {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 6px;
    margin: 0;
    font-size: 12px;
  }

  .sub {
    margin: 2px 0 0 14px;
    color: var(--text-muted);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .warn-text {
    color: var(--warn);
  }

  .dot {
    width: 8px;
    height: 8px;
    flex: 0 0 8px;
    border-radius: 50%;
    background: var(--bad);
  }

  .dot.ok {
    background: var(--ok);
  }

  .dot.warn {
    background: var(--warn);
  }

  .buttons {
    display: inline-flex;
    gap: 4px;
    margin-left: auto;
  }

  .buttons button {
    height: 24px;
    padding: 0 8px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 4px;
    cursor: pointer;
    font: inherit;
    font-size: 11px;
  }

  .buttons button:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .buttons button.active {
    border-color: var(--accent);
  }

  .log {
    max-height: 220px;
    margin: 4px 0 2px;
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

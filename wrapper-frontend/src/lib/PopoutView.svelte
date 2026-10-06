<script lang="ts">
  import { onMount } from "svelte";
  import { drawOverlay, valueNumber, type DataMap } from "./overlay";
  import { topic } from "./wrapper-bus";

  // Pop-out camera view (/popout?cam=0&view=raw): only the camera image,
  // full-size on a dark background, with a view selector, overlay on/off,
  // a refresh-rate selector (sets debug.debug_stream_interval_ms in the
  // vision config via POST /api/config/debug-interval; vision_processor
  // reloads it live) and a link to the raw Pi stream when vision_processor
  // is stopped (the Pi allows one viewer).

  interface DetectionFrame {
    camera_id?: number;
    balls?: { pixel_x?: number; pixel_y?: number }[];
    robots_blue?: { robot_id?: number; pixel_x?: number; pixel_y?: number }[];
    robots_yellow?: { robot_id?: number; pixel_x?: number; pixel_y?: number }[];
  }
  interface WrapperPacket {
    geometry?: { field?: DataMap; calib?: DataMap[] };
  }

  const params = new URLSearchParams(location.search);
  let camId = $state(Number(params.get("cam") ?? "0") || 0);
  let view = $state(params.get("view") ?? "raw");
  let overlay = $state(params.get("overlay") !== "0");
  const views = ["raw", "flat", "gradient", "blob"];
  const rates = [1, 2, 5, 10];

  const wrapperPacket = topic<WrapperPacket>("wrapper_packet.out");
  const detectionPacket = topic<DetectionFrame>("detection.in");

  let cacheBuster = $state(Date.now());
  let rateHz = $state(1);
  let configInterval = $state<number | null>(null);
  let cameraPath = $state<string | null>(null);
  let geometryConfig = $state<DataMap>({});
  let visionRunning = $state(false);
  let detection = $state<DetectionFrame | null>(null);
  let lastFrameAt = $state(0);
  let geometry = $state<WrapperPacket["geometry"] | null>(null);
  let canvas = $state<HTMLCanvasElement>();
  let message = $state<string | null>(null);
  let loadFailed = $state(false);

  let calib = $derived(
    geometry?.calib?.find((c) => valueNumber(c["camera_id"]) === camId) ?? {},
  );
  let piBase = $derived.by(() => {
    if (!cameraPath || !/^https?:\/\//.test(cameraPath)) return null;
    try {
      const url = new URL(cameraPath);
      return `${url.protocol}//${url.host}`;
    } catch {
      return null;
    }
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
    }
  });

  function draw(): void {
    if (!canvas) return;
    if (!overlay) {
      canvas.width = 1;
      canvas.height = 1;
      return;
    }
    const live = performance.now() - lastFrameAt < 1000 ? detection : null;
    drawOverlay(canvas, {
      calib,
      field: geometry?.field ?? {},
      geometryConfig,
      robotsBlue: live?.robots_blue ?? [],
      robotsYellow: live?.robots_yellow ?? [],
      balls: live?.balls ?? [],
    });
  }

  $effect(() => {
    void cacheBuster;
    void geometry;
    void detection;
    void overlay;
    requestAnimationFrame(draw);
  });

  async function poll(): Promise<void> {
    try {
      const [health, config] = await Promise.all([
        fetch("/api/health"),
        fetch("/api/config"),
      ]);
      if (health.ok) {
        const h = (await health.json()) as {
          services?: { vision_processor?: { running?: boolean } };
        };
        visionRunning = h.services?.vision_processor?.running === true;
      }
      if (config.ok) {
        const c = (await config.json()) as { config?: DataMap };
        const cfg = c.config ?? {};
        const camera = cfg["camera"] as DataMap | undefined;
        cameraPath =
          typeof camera?.["path"] === "string" ? camera["path"] : null;
        geometryConfig = (cfg["geometry"] as DataMap | undefined) ?? {};
        const debug = cfg["debug"] as DataMap | undefined;
        const interval = valueNumber(debug?.["debug_stream_interval_ms"], NaN);
        configInterval = Number.isFinite(interval) ? interval : null;
      }
    } catch {
      // Keep the last known state.
    }
  }

  async function setRate(hz: number): Promise<void> {
    rateHz = hz;
    message = null;
    try {
      const response = await fetch("/api/config/debug-interval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ interval_ms: Math.round(1000 / hz) }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        error?: string;
      };
      message = response.ok
        ? `debug_stream_interval_ms = ${String(Math.round(1000 / hz))} saved; vision_processor applies it within a second.`
        : (data.error ?? `HTTP ${String(response.status)}`);
      void poll();
    } catch (caught) {
      message = String(caught);
    }
  }

  function updateUrl(): void {
    const next = new URLSearchParams({
      cam: String(camId),
      view,
      overlay: overlay ? "1" : "0",
    });
    history.replaceState(null, "", `${location.pathname}?${next.toString()}`);
  }

  onMount(() => {
    void poll();
    const pollTimer = setInterval(() => void poll(), 2000);
    let imageTimer = setInterval(() => {
      cacheBuster = Date.now();
    }, 1000 / rateHz);
    const rateEffect = setInterval(() => {
      // Re-arm the image timer when the rate changes.
      clearInterval(imageTimer);
      imageTimer = setInterval(() => {
        cacheBuster = Date.now();
      }, 1000 / rateHz);
    }, 5000);
    return () => {
      clearInterval(pollTimer);
      clearInterval(imageTimer);
      clearInterval(rateEffect);
    };
  });

  $effect(() => {
    void camId;
    void view;
    void overlay;
    updateUrl();
  });
</script>

<svelte:head>
  <title>Camera {camId} · {view}</title>
</svelte:head>

<div class="popout">
  <div class="stage">
    <div class="frame">
      <img
        src={`/snapshot/${String(camId)}/${view}?t=${String(cacheBuster)}`}
        alt={`Camera ${String(camId)} ${view}`}
        onerror={() => (loadFailed = true)}
        onload={() => (loadFailed = false)}
      />
      {#if overlay && view === "raw"}
        <canvas bind:this={canvas}></canvas>
      {/if}
    </div>
    {#if loadFailed}
      <p class="missing">
        No {view} snapshot for camera {camId} (is vision_processor running?)
      </p>
    {/if}
  </div>
  <div class="bar">
    <label
      >cam <input type="number" min="0" bind:value={camId} class="cam" /></label
    >
    <span class="group" role="radiogroup" aria-label="View">
      {#each views as name (name)}
        <button class:active={view === name} onclick={() => (view = name)}
          >{name}</button
        >
      {/each}
    </span>
    <label><input type="checkbox" bind:checked={overlay} /> overlay</label>
    <span
      class="group"
      title="Refresh rate of the snapshots written by vision_processor (debug.debug_stream_interval_ms)"
    >
      rate
      {#each rates as hz (hz)}
        <button
          class:active={configInterval !== null &&
            Math.round(1000 / hz) === configInterval}
          onclick={() => void setRate(hz)}>{hz} Hz</button
        >
      {/each}
      <span class="note"
        >higher rates cost vision_processor time on the CPU build</span
      >
    </span>
    {#if piBase}
      {#if visionRunning}
        <span class="note" title="The Pi camera has a single viewer">
          Raw Pi stream: stop capture first — the Pi camera has a single viewer
        </span>
      {:else}
        <a href={`${piBase}/stream`} target="_blank" rel="noopener noreferrer"
          >Raw Pi stream</a
        >
      {/if}
    {/if}
    {#if message}<span class="note">{message}</span>{/if}
  </div>
</div>

<style>
  :global(html),
  :global(body) {
    height: 100%;
    margin: 0;
    background: #0b0f0d !important;
  }

  .popout {
    display: flex;
    flex-direction: column;
    height: 100vh;
    color: #dce5e0;
    background: #0b0f0d;
    font-family: system-ui, sans-serif;
    font-size: 12px;
  }

  .stage {
    position: relative;
    flex: 1;
    min-height: 0;
    display: grid;
    place-items: center;
  }

  .frame {
    position: relative;
    max-width: 100%;
    max-height: 100%;
    line-height: 0;
  }

  img {
    max-width: 100%;
    max-height: calc(100vh - 44px);
    width: auto;
    height: auto;
  }

  canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }

  .missing {
    position: absolute;
    color: #aab6af;
  }

  .bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 12px;
    padding: 6px 10px;
    background: #141b17;
    border-top: 1px solid #2a3430;
  }

  .group {
    display: inline-flex;
    align-items: center;
    gap: 4px;
  }

  button {
    height: 24px;
    padding: 0 8px;
    color: #dce5e0;
    background: #29352f;
    border: 1px solid #46544d;
    border-radius: 3px;
    cursor: pointer;
    font: inherit;
  }

  button.active {
    background: #276f4b;
    border-color: #276f4b;
  }

  .cam {
    width: 3em;
    padding: 2px 4px;
    color: inherit;
    background: #29352f;
    border: 1px solid #46544d;
    border-radius: 3px;
    font: inherit;
  }

  .note {
    color: #98aaa0;
    font-size: 11px;
  }

  a {
    color: #8cc4ff;
  }
</style>

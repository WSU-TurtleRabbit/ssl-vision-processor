<script lang="ts">
  import { onMount, untrack } from "svelte";
  import CalibrateTab from "./lib/CalibrateTab.svelte";
  import CameraName from "./lib/CameraName.svelte";
  import CameraView from "./lib/CameraView.svelte";
  import CamerasTab from "./lib/CamerasTab.svelte";
  import GeometryEditor from "./lib/GeometryEditor.svelte";
  import Icon from "./lib/Icon.svelte";
  import LiveTab from "./lib/LiveTab.svelte";
  import LogDrawer from "./lib/LogDrawer.svelte";
  import LogsView from "./lib/LogsView.svelte";
  import SystemTab from "./lib/SystemTab.svelte";
  import type {
    CalibrationStatus,
    CameraMetrics,
    ConfigResponse,
    DetectionFrame,
    GeometryData,
    HealthResponse,
    Snapshot,
    WrapperPacket,
  } from "./lib/health";
  import { asRecord, num } from "./lib/health";
  import type { OverlayState } from "./lib/overlay";
  import {
    connectionState,
    messageCount,
    reconnect,
    topic,
  } from "./lib/wrapper-bus";

  // Operator page: status strip, camera view (left), task tabs (right),
  // side log console, modals. Help / Logs / Receipt are full views
  // (#/help, #/logs, #/receipt); Help and Receipt are lazy chunks.

  type Toast = { kind: "ok" | "error"; text: string } | null;
  type Tab = "live" | "cameras" | "calibrate" | "system";

  // --- routing ------------------------------------------------------------
  function parseHash(): {
    view: "main" | "help" | "logs" | "receipt";
    doc: string;
    file: string | null;
  } {
    const help = /^#\/help(?:\/([\w-]+))?/.exec(location.hash);
    if (help) return { view: "help", doc: help[1] ?? "README", file: null };
    const logs = /^#\/logs(?:\/([\w.-]+))?/.exec(location.hash);
    if (logs) return { view: "logs", doc: "README", file: logs[1] ?? null };
    if (location.hash.startsWith("#/receipt"))
      return { view: "receipt", doc: "README", file: null };
    return { view: "main", doc: "README", file: null };
  }
  let route = $state(parseHash());
  function go(hash: string): void {
    location.hash = hash;
    route = parseHash();
    window.scrollTo(0, 0);
  }
  function openHelp(doc = "README", anchor?: string): void {
    go(`#/help/${doc}${anchor ? `#${anchor}` : ""}`);
  }
  function openLogs(file: string | null = null): void {
    go(`#/logs${file ? `/${file}` : ""}`);
  }
  function closeView(): void {
    history.pushState(null, "", location.pathname + location.search);
    route = parseHash();
  }
  let helpModule: Promise<typeof import("./lib/HelpView.svelte")> | null = null;
  function loadHelp(): Promise<typeof import("./lib/HelpView.svelte")> {
    helpModule ??= import("./lib/HelpView.svelte");
    return helpModule;
  }
  let receiptModule: Promise<typeof import("./lib/ReceiptView.svelte")> | null =
    null;
  function loadReceipt(): Promise<typeof import("./lib/ReceiptView.svelte")> {
    receiptModule ??= import("./lib/ReceiptView.svelte");
    return receiptModule;
  }

  // --- remembered UI state (per browser, failures ignored) -----------------
  function remember(key: string, value: string): void {
    try {
      localStorage.setItem(key, value);
    } catch {
      // not remembered
    }
  }
  function recall(key: string): string | null {
    try {
      return localStorage.getItem(key);
    } catch {
      return null;
    }
  }
  type ThemePref = "light" | "dark" | "system";
  const stored = recall("vp-theme");
  let themePref = $state<ThemePref>(
    stored === "dark" || stored === "system" ? stored : "light",
  );
  let systemDark = $state(
    window.matchMedia("(prefers-color-scheme: dark)").matches,
  );
  let dark = $derived(
    themePref === "dark" || (themePref === "system" && systemDark),
  );
  $effect(() => {
    document.documentElement.dataset["theme"] = dark ? "dark" : "light";
    remember("vp-theme", themePref);
  });
  const storedTab = recall("vp-tab");
  let tab = $state<Tab>(
    storedTab === "cameras" ||
      storedTab === "calibrate" ||
      storedTab === "system"
      ? storedTab
      : "live",
  );
  function setTab(next: Tab): void {
    tab = next;
    remember("vp-tab", next);
  }
  let drawerOpen = $state(recall("vp-log-drawer") === "1");
  function toggleDrawer(): void {
    drawerOpen = !drawerOpen;
    remember("vp-log-drawer", drawerOpen ? "1" : "0");
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

  // --- data ---------------------------------------------------------------
  const wrapperPacket = topic<WrapperPacket>("wrapper_packet.out");
  const detectionPacket = topic<DetectionFrame>("detection.in");

  let snapshots = $state<Snapshot[]>([]);
  let selectedView = $state("raw");
  let cacheBuster = $state(Date.now());
  let configPayload = $state<ConfigResponse | null>(null);
  let health = $state<HealthResponse | null>(null);
  let healthReachable = $state(false);
  let calibration = $state<CalibrationStatus | null>(null);
  let metrics = $state<Record<string, CameraMetrics>>({});
  let geometry = $state<GeometryData | null>(null);
  let detection = $state<DetectionFrame | null>(null);
  let lastFrameAt = $state(0);
  let clock = $state(performance.now());
  let fps = $state(0);
  let uiFps = $state(0);
  let wsRate = $state(0);
  let uiFrames = 0;
  let refreshToken = $state(0);
  let lastUpdated = $state<Date | null>(null);
  let refreshing = $state(false);
  let cornersMode = $state(false);
  let lensMode = $state(false);
  let geometryModal = $state(false);
  let toast = $state<Toast>(null);
  let toastTimer: ReturnType<typeof setTimeout> | null = null;

  function showToast(next: Toast): void {
    if (toastTimer) clearTimeout(toastTimer);
    toast = next;
    if (next)
      toastTimer = setTimeout(() => {
        toast = null;
      }, 8000);
  }

  let activeConfig = $derived(asRecord(configPayload?.config));
  let geometryConfig = $derived(asRecord(activeConfig["geometry"]));
  let field = $derived(asRecord(geometry?.field));
  let camId = $derived(health?.cam_id ?? 0);
  let cameraName = $derived(health?.camera_name ?? null);
  let cameraLabel = $derived(cameraName ?? `Camera ${String(camId)}`);
  let cameraCalibration = $derived(
    geometry?.calib?.find((c) => Number(c["camera_id"] ?? 0) === camId) ?? {},
  );
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
  let lensInfo = $derived.by(() => {
    const cj = calibration?.calib_json;
    if (!cj || calibrationState !== "calibrated") return null;
    const k2 = cj.distortion_k2;
    if (typeof k2 !== "number") return null;
    const pp = cj.principal_point ?? [];
    return `k2 ${k2.toFixed(3)} · pp ${num(pp[0])}, ${num(pp[1])}`;
  });
  let detectionState = $derived.by(() => {
    if (detectionLive) {
      const robots = blueRobots.length + yellowRobots.length;
      return `${String(robots)} robot${robots === 1 ? "" : "s"}, ${String(balls.length)} ball${balls.length === 1 ? "" : "s"} (live)`;
    }
    if (!visionRunning) return "vision_processor not running";
    if (calibrationState === "recalibrating") return "running, recalibrating…";
    if (calibrationState === "not_calibrated")
      return "running, not calibrated → set field corners";
    return "running, no detection frames";
  });
  let overlay = $derived<OverlayState>({
    calib: cameraCalibration,
    field,
    geometryConfig,
    robotsBlue: blueRobots,
    robotsYellow: yellowRobots,
    balls,
  });
  let camMetrics = $derived(metrics[String(camId)]);
  let pi = $derived(health?.services.pi_camera);
  let gc = $derived(health?.services.game_controller);

  $effect(() => {
    const packet = $wrapperPacket;
    if (packet?.geometry) geometry = packet.geometry;
  });

  // Detection frames are applied at most once per animation frame.
  let pendingFrame: DetectionFrame | null = null;
  let rafId = 0;
  $effect(() => {
    const frame = $detectionPacket;
    if (!frame) return;
    pendingFrame = frame;
    untrack(() => {
      lastFrameAt = performance.now();
      if (rafId) return;
      rafId = requestAnimationFrame(() => {
        rafId = 0;
        if (pendingFrame) {
          detection = pendingFrame;
          uiFrames += 1;
        }
      });
    });
  });

  // vision_processor frame rate from frame numbers over >= 1 s windows.
  let previousFrame = 0;
  let previousFrameAt = 0;
  $effect(() => {
    const frame = detection?.frame_number ?? 0;
    if (!frame || frame === previousFrame) return;
    const now = performance.now();
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
  $effect(() => {
    if (!detectionLive) fps = 0;
  });

  async function getJson<T>(url: string): Promise<T | null> {
    const response = await fetch(url);
    return response.ok ? ((await response.json()) as T) : null;
  }
  async function refreshSnapshots(): Promise<void> {
    try {
      const list = await getJson<Snapshot[]>("/snapshots");
      if (list) snapshots = list;
    } catch {
      // the strip shows reachability
    }
  }
  async function refreshConfig(): Promise<void> {
    try {
      configPayload =
        (await getJson<ConfigResponse>("/api/config")) ?? configPayload;
    } catch {
      // keep the last config
    }
  }
  async function refreshHealth(): Promise<void> {
    try {
      const next = await getJson<HealthResponse>("/api/health");
      healthReachable = next !== null;
      if (next) health = next;
    } catch {
      healthReachable = false;
    }
  }
  async function refreshCalibration(): Promise<void> {
    try {
      calibration =
        (await getJson<CalibrationStatus>(
          `/api/calibration?cam_id=${String(camId)}`,
        )) ?? calibration;
    } catch {
      // shown in the strip
    }
  }
  async function refreshMetrics(): Promise<void> {
    try {
      const data = await getJson<{ cameras?: Record<string, CameraMetrics> }>(
        "/api/metrics",
      );
      if (data) metrics = data.cameras ?? {};
    } catch {
      // shown in the strip
    }
  }
  function refreshEverything(): Promise<unknown> {
    return Promise.all([
      refreshSnapshots(),
      refreshConfig(),
      refreshHealth(),
      refreshCalibration(),
      refreshMetrics(),
    ]);
  }
  async function refreshAll(): Promise<void> {
    refreshing = true;
    reconnect();
    refreshToken += 1;
    await refreshEverything();
    cacheBuster = Date.now();
    lastUpdated = new Date();
    refreshing = false;
  }
  function onChange(): void {
    void refreshHealth();
    void refreshCalibration();
    void refreshConfig();
    refreshToken += 1;
  }
  function modeSaved(message: string): void {
    cornersMode = false;
    lensMode = false;
    showToast({ kind: "ok", text: message });
    onChange();
  }

  onMount(() => {
    void refreshEverything().then(() => {
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
    let lastUi = 0;
    let lastWs = messageCount();
    const timers = [
      setInterval(() => void refreshSnapshots(), 5000),
      setInterval(() => void refreshConfig(), 3000),
      setInterval(() => void refreshHealth(), 2000),
      setInterval(() => void refreshCalibration(), 2000),
      setInterval(() => void refreshMetrics(), 2000),
      setInterval(() => {
        cacheBuster = Date.now();
      }, 1000),
      setInterval(() => {
        clock = performance.now();
      }, 250),
      setInterval(() => {
        uiFps = uiFrames - lastUi;
        lastUi = uiFrames;
        const ws = messageCount();
        wsRate = ws - lastWs;
        lastWs = ws;
      }, 1000),
    ];
    return () => {
      for (const timer of timers) clearInterval(timer);
      darkQuery.removeEventListener("change", onSystemTheme);
      window.removeEventListener("hashchange", onHash);
      window.removeEventListener("popstate", onHash);
    };
  });

  function piText(): string {
    if (!pi) return "no network camera";
    if (!pi.running) return "Pi offline";
    if (pi.closed) return "Pi closed";
    return pi.streaming ? "Pi streaming" : "Pi idle";
  }
</script>

<svelte:window onkeydown={onGlobalKey} />

<svelte:head>
  <title>{cameraLabel} · SSL Vision</title>
</svelte:head>

<main class:full={route.view === "main"}>
  <header class="strip">
    <span class="name">
      <CameraName
        {camId}
        name={cameraName}
        showId
        onrenamed={() => void refreshHealth()}
      />
    </span>
    <span class="chips">
      <span
        class="chip"
        title={`WebSocket to the backend (detections, geometry, colours): ${$connectionState}`}
        ><i
          class="dot"
          class:ok={$connectionState === "open"}
          class:warn={$connectionState === "connecting"}
        ></i>live</span
      >
      <span
        class="chip"
        title={`vision_processor: ${health?.services.vision_processor?.state ?? "unknown"} · ${detectionState}`}
        ><i
          class="dot"
          class:ok={visionRunning}
          class:warn={!visionRunning &&
            health?.services.vision_processor?.want_running}
        ></i>vision {num(fps, 1)} fps</span
      >
      <span class="chip" title={pi?.error ?? "Pi camera service /status"}
        ><i
          class="dot"
          class:ok={pi?.running && !pi.closed}
          class:warn={pi?.closed}
        ></i>{piText()}</span
      >
      <span class="chip" title="Field calibration of this camera"
        ><i
          class="dot"
          class:ok={calibrationState === "calibrated"}
          class:warn={calibrationState === "recalibrating"}
        ></i>{calibrationState === "calibrated"
          ? "calibrated"
          : calibrationState === "recalibrating"
            ? "recalibrating"
            : "not calibrated"}</span
      >
      <span
        class="chip"
        title={gc?.running
          ? `game controller on ${gc.group ?? ""}: ${gc.stage ?? ""} · ${gc.command ?? ""}`
          : `game controller not seen on ${gc?.group ?? "the referee multicast"}`}
        ><i
          class="dot"
          class:ok={gc?.running}
          class:warn={!gc?.running && gc?.process_running}
        ></i>GC</span
      >
      <span class="chip metrics">
        <span
          title="Detection frames per second reaching this page from vision_processor"
          >{num(fps, 1)} fps</span
        >
        ·
        <span
          title="vision_processor processing time per frame (t_sent − t_capture)"
          >{num(camMetrics?.processing_ms, 1)} ms</span
        >
        ·
        <span
          title="Detection packets per second on the multicast bus (backend, last 5 s)"
          >{num(camMetrics?.rate_hz, 1)} pkt/s</span
        >
      </span>
    </span>
    <span class="actions">
      <button
        disabled={refreshing}
        title={`Re-fetch everything and reconnect${lastUpdated ? ` · last ${lastUpdated.toLocaleTimeString()}` : ""}`}
        onclick={refreshAll}><Icon name="refresh" size={14} /></button
      >
      <button
        class:active={drawerOpen}
        title="Log console (` key)"
        aria-label="Log console"
        onclick={toggleDrawer}><Icon name="logs" size={14} /></button
      >
      {#if route.view !== "main"}
        <button onclick={closeView}>← back</button>
      {:else}
        <button
          title="Help"
          onclick={() => {
            openHelp();
          }}><Icon name="help" size={14} /> Help</button
        >
      {/if}
      <button
        class="panic"
        onclick={() => {
          openHelp("panic");
        }}>🚨 Panic</button
      >
      <button
        title="Printable receipt of the whole setup"
        onclick={() => {
          go("#/receipt");
        }}>Receipt</button
      >
      <select bind:value={themePref} aria-label="Theme" title="Theme">
        <option value="light">☀</option>
        <option value="dark">☾</option>
        <option value="system">◐</option>
      </select>
    </span>
  </header>

  {#if route.view === "help"}
    {#await loadHelp()}
      <p class="loading">Loading help…</p>
    {:then module}
      <module.default name={route.doc} {dark} onnavigate={openHelp} />
    {:catch error}
      <p class="loading">Help failed to load: {String(error)}</p>
    {/await}
  {:else if route.view === "logs"}
    <LogsView file={route.file} onopen={openLogs} />
  {:else if route.view === "receipt"}
    {#await loadReceipt()}
      <p class="loading">Loading…</p>
    {:then module}
      <module.default />
    {/await}
  {:else}
    <div class="body">
      <section class="camera">
        <CameraView
          {camId}
          {cameraLabel}
          {snapshots}
          bind:selectedView
          {cacheBuster}
          {overlay}
          {calibration}
          {calibrationState}
          {lensInfo}
          {geometryConfig}
          {field}
          bind:cornersMode
          bind:lensMode
          onfield={() => (geometryModal = true)}
          onsaved={modeSaved}
        />
      </section>
      <aside class="side">
        <nav class="tabs" aria-label="Panels">
          {#each [["live", "Live"], ["cameras", "Cameras"], ["calibrate", "Calibrate"], ["system", "System"]] as [key, label] (key)}
            <button
              class:active={tab === key}
              onclick={() => {
                setTab(key as Tab);
              }}>{label}</button
            >
          {/each}
        </nav>
        <div class="tab-body">
          {#if tab === "live"}
            <LiveTab
              blue={blueRobots}
              yellow={yellowRobots}
              {balls}
              live={detectionLive}
              stateText={detectionState}
              {metrics}
              names={{ [String(camId)]: cameraLabel }}
              {uiFps}
              {wsRate}
            />
          {:else if tab === "cameras"}
            <CamerasTab
              {health}
              {calibration}
              config={activeConfig}
              calib={cameraCalibration}
              onchange={onChange}
              oncorners={() => {
                cornersMode = true;
                lensMode = false;
              }}
              onlens={() => {
                lensMode = true;
                cornersMode = false;
              }}
              ontoast={showToast}
            />
          {:else if tab === "calibrate"}
            <CalibrateTab
              {calibration}
              config={activeConfig}
              {camId}
              {cameraLabel}
              {refreshToken}
              onedit={() => (geometryModal = true)}
              oncorners={() => {
                cornersMode = true;
                lensMode = false;
              }}
              onchange={onChange}
              ontoast={showToast}
            />
          {:else}
            <SystemTab
              {health}
              reachable={healthReachable}
              onchange={onChange}
              oncameras={() => {
                setTab("cameras");
              }}
              onlogs={() => {
                openLogs();
              }}
              ontoast={showToast}
            />
          {/if}
        </div>
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
          onchange={onChange}
          onclose={(result: Toast) => {
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
</main>

<LogDrawer
  open={drawerOpen}
  piLogFile={pi?.log_file ?? null}
  ontoggle={toggleDrawer}
  onopenfile={openLogs}
/>

<style>
  :global(:root) {
    color-scheme: light;
    --accent: #276f4b;
    --page: #f3f5f4;
    --surface: #ffffff;
    --surface-2: #eceff0;
    --text: #17201b;
    --text-muted: #5f6b65;
    --text-faint: #98a39d;
    --border: #cfd6d2;
    --border-soft: #e4e9e6;
    --ok: #1f7a46;
    --ok-bg: #e8f5ed;
    --ok-border: #bfe0cc;
    --bad: #b3261e;
    --bad-bg: #fbeceb;
    --bad-border: #efc9c6;
    --warn: #9a6400;
    --warn-bg: #fff4dc;
    --warn-border: #f0dca8;
    --yellow-bg: #fff4c7;
    --blue-bg: #e7f1fb;
    --blue-text: #1e5c9e;
    --code-bg: #eceff0;
  }

  :global(:root[data-theme="dark"]) {
    color-scheme: dark;
    --accent: #4fbf82;
    --page: #121815;
    --surface: #182019;
    --surface-2: #222c26;
    --text: #e3ebe6;
    --text-muted: #a5b3ab;
    --text-faint: #7d8c84;
    --border: #34423b;
    --border-soft: #26302b;
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

  :global(*) {
    box-sizing: border-box;
  }

  :global(html),
  :global(body) {
    margin: 0;
    color: var(--text);
    background: var(--page);
    font:
      12.5px/1.4 system-ui,
      -apple-system,
      "Segoe UI",
      sans-serif;
  }

  :global(button),
  :global(select),
  :global(input) {
    font: inherit;
  }

  main {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }

  main.full {
    height: 100vh;
    overflow: hidden;
  }

  .strip {
    display: flex;
    align-items: center;
    gap: 12px;
    height: 44px;
    padding: 0 12px;
    background: var(--surface);
    border-bottom: 1px solid var(--border-soft);
  }

  .name {
    font-weight: 600;
    white-space: nowrap;
  }

  .chips {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 10px;
    min-width: 0;
    flex: 1;
    font-size: 12px;
    color: var(--text-muted);
  }

  .chip {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    white-space: nowrap;
  }

  .chip.metrics {
    color: var(--text);
    font-variant-numeric: tabular-nums;
  }

  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--bad);
  }

  .dot.ok {
    background: var(--ok);
  }

  .dot.warn {
    background: var(--warn);
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 4px;
  }

  .actions button,
  .actions select {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    height: 28px;
    padding: 0 8px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid transparent;
    border-radius: 4px;
    cursor: pointer;
    font-size: 12px;
  }

  .actions button.active {
    border-color: var(--accent);
  }

  .actions .panic {
    color: #fff;
    background: #c0322b;
    font-weight: 700;
  }

  .body {
    display: grid;
    grid-template-columns: minmax(0, 65fr) minmax(320px, 35fr);
    gap: 8px;
    flex: 1;
    min-height: 0;
    padding: 8px;
  }

  .camera {
    min-height: 0;
    min-width: 0;
  }

  .side {
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
    background: var(--surface);
    border-radius: 4px;
  }

  .tabs {
    display: flex;
    flex: 0 0 auto;
    border-bottom: 1px solid var(--border-soft);
  }

  .tabs button {
    flex: 1;
    height: 32px;
    color: var(--text-muted);
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    font-size: 12.5px;
  }

  .tabs button.active {
    color: var(--text);
    border-bottom-color: var(--accent);
  }

  .tab-body {
    flex: 1;
    min-height: 0;
    padding: 8px 10px;
    overflow: hidden auto;
  }

  .loading {
    padding: 24px;
    color: var(--text-muted);
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
    border-radius: 6px;
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

  @media (max-width: 1100px) {
    main.full {
      height: auto;
      overflow: visible;
    }

    .strip {
      height: auto;
      flex-wrap: wrap;
      padding: 6px 12px;
    }

    .body {
      grid-template-columns: 1fr;
    }

    .camera {
      height: 56vw;
      max-height: 60vh;
    }
  }

  @media (max-width: 700px) {
    .chip.metrics {
      display: none;
    }

    .actions button,
    .tabs button {
      min-height: 36px;
    }
  }
</style>

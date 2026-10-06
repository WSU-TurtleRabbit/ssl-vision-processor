<script lang="ts">
  import { onMount } from "svelte";

  // Colour calibration: learned vs reference blob colours from
  // GET /api/colors, "save learned as reference", and a fallback picker
  // that samples the raw camera snapshot. All values are vision_processor's
  // brightness-free dRGB (kernel/resampling.cl), not plain RGB.

  type Rgb = [number, number, number];

  interface ColorsResponse {
    cam_id?: number;
    learned?: Record<string, unknown>;
    reference?: Record<string, unknown>;
    reference_force?: number;
    history_force?: number;
    age_s?: number;
  }

  interface Message {
    kind: "ok" | "error";
    text: string;
  }

  interface Pick {
    name: string;
    rgb: Rgb;
    drgb: Rgb;
    // Marker position as a fraction of the displayed image.
    fx: number;
    fy: number;
  }

  interface AutoStatus {
    state: "idle" | "running" | "done" | "failed";
    progress?: number;
    error?: string;
    saved?: Record<string, number[]>;
    skipped?: Record<string, string>;
    evidence?: Record<string, number>;
  }

  let {
    camId,
    configColors = {},
    refreshToken = 0,
    cameraLabel = "",
  }: {
    camId: string;
    configColors?: Record<string, unknown>;
    refreshToken?: number;
    cameraLabel?: string;
  } = $props();

  let auto = $state<AutoStatus | null>(null);

  const names = ["orange", "field", "yellow", "blue", "green", "pink"];
  const staleAfterS = 5;
  const pickRadius = 3;

  let colors = $state<ColorsResponse | null>(null);
  let missing = $state(false);
  let confirmSave = $state(false);
  let saving = $state(false);
  let message = $state<Message | null>(null);
  let pickName = $state<string | null>(null);
  let pick = $state<Pick | null>(null);
  let pickSrc = $state<string | null>(null);
  let pickImage = $state<HTMLImageElement>();
  // The fully decoded frame shown in the picker; pixels are read from this
  // object, so a refresh in flight never leaves us sampling an empty image.
  let pickFrame: HTMLImageElement | null = null;
  let frameLoading = false;

  let live = $derived(
    !missing && colors !== null && (colors.age_s ?? Infinity) <= staleAfterS,
  );

  let staleText = $derived(
    `vision_processor not publishing colours for camera ${camId}` +
      (colors && !missing
        ? ` (last update ${(colors.age_s ?? 0).toFixed(0)} s ago)`
        : ""),
  );
  let forces = $derived(
    live
      ? { reference: colors?.reference_force, history: colors?.history_force }
      : {
          reference: configColors["reference_force"],
          history: configColors["history_force"],
        },
  );

  function asRgb(value: unknown): Rgb | null {
    if (!Array.isArray(value) || value.length < 3) return null;
    const rgb = value.slice(0, 3).map(Number);
    return rgb.every((c) => Number.isFinite(c)) ? (rgb as Rgb) : null;
  }

  function clampByte(value: number): number {
    return Math.min(255, Math.max(0, Math.round(value)));
  }

  // dRGB keeps only the difference from grey (127.5); stretch it x2 around
  // mid-grey so the hue is recognisable. Brightness is not recoverable.
  function previewCss(value: unknown): string {
    const d = asRgb(value);
    if (!d) return "transparent";
    const [r, g, b] = d.map((c) => clampByte(128 + 2 * (c - 127.5)));
    return `rgb(${String(r)} ${String(g)} ${String(b)})`;
  }

  function formatRgb(value: unknown): string {
    const rgb = asRgb(value);
    return rgb ? rgb.map((c) => String(Math.round(c))).join(", ") : "--";
  }

  // Exactly kernel/resampling.cl's integer formula (all terms >= 0).
  function toDrgb([r, g, b]: Rgb): Rgb {
    return [
      Math.floor((2 * r - g - b + 510) / 4),
      Math.floor((2 * g - b - r + 510) / 4),
      Math.floor((2 * b - r - g + 510) / 4),
    ];
  }

  async function refresh(): Promise<void> {
    try {
      const response = await fetch(
        `/api/colors?cam_id=${encodeURIComponent(camId)}`,
      );
      if (response.status === 404) {
        missing = true;
        return;
      }
      if (!response.ok) return;
      colors = (await response.json()) as ColorsResponse;
      missing = false;
    } catch {
      missing = true;
    }
  }

  async function save(body: unknown, success: string): Promise<void> {
    saving = true;
    message = null;
    try {
      const response = await fetch(
        `/api/colors/save?cam_id=${encodeURIComponent(camId)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        path?: string;
      };
      if (response.ok) {
        message = {
          kind: "ok",
          text: `${success} in ${result.path ?? "config"}; vision_processor reloads it within a second.`,
        };
        void refresh();
      } else {
        message = {
          kind: "error",
          text: result.error ?? `HTTP ${String(response.status)}`,
        };
      }
    } catch (error) {
      message = { kind: "error", text: String(error) };
    } finally {
      saving = false;
    }
  }

  async function saveLearned(): Promise<void> {
    confirmSave = false;
    await save({ from: "learned" }, "Saved learned colours as reference");
  }

  // Auto-calibration: POST /api/colors/auto, then poll until done. The
  // backend samples ~5 s, needs robots in view and saves stable colours.
  async function startAuto(): Promise<void> {
    message = null;
    try {
      const response = await fetch(
        `/api/colors/auto?cam_id=${encodeURIComponent(camId)}`,
        { method: "POST" },
      );
      auto = (await response.json()) as AutoStatus;
      if (!response.ok && auto.state !== "running") return;
      while (auto.state === "running") {
        await new Promise((resolve) => setTimeout(resolve, 500));
        const poll = await fetch("/api/colors/auto");
        auto = (await poll.json()) as AutoStatus;
      }
      void refresh();
    } catch (error) {
      auto = { state: "failed", error: String(error) };
    }
  }

  function startPick(name: string): void {
    pickName = name;
    pick = null;
    message = null;
    pickFrame = null;
    pickSrc = null;
    loadFrame();
  }

  // Preload the next raw snapshot off-screen and swap it in only once it has
  // decoded; at most one request in flight.
  function loadFrame(): void {
    if (frameLoading) return;
    frameLoading = true;
    const url = `/snapshot/${camId}/raw?t=${String(Date.now())}`;
    const frame = new Image();
    frame.onload = () => {
      frameLoading = false;
      if (pickName && !pick) {
        pickFrame = frame;
        pickSrc = url;
      }
    };
    frame.onerror = () => {
      frameLoading = false;
    };
    frame.src = url;
  }

  function cancelPick(): void {
    pickName = null;
    pick = null;
  }

  async function applyPick(): Promise<void> {
    if (!pick) return;
    const { name, drgb } = pick;
    await save({ colors: { [name]: drgb } }, `Saved ${name} reference`);
    if (message?.kind === "ok") cancelPick();
  }

  function samplePick(event: MouseEvent): void {
    const image = pickFrame;
    if (!pickName || !pickImage || !image?.naturalWidth) return;
    const rect = pickImage.getBoundingClientRect();
    // The image is scaled uniformly to its box; map CSS px to image px.
    const fx = (event.clientX - rect.left) / rect.width;
    const fy = (event.clientY - rect.top) / rect.height;
    const width = image.naturalWidth;
    const height = image.naturalHeight;
    const cx = Math.min(width - 1, Math.max(0, Math.floor(fx * width)));
    const cy = Math.min(height - 1, Math.max(0, Math.floor(fy * height)));

    const x0 = Math.max(0, cx - pickRadius);
    const y0 = Math.max(0, cy - pickRadius);
    const x1 = Math.min(width - 1, cx + pickRadius);
    const y1 = Math.min(height - 1, cy + pickRadius);
    const canvas = document.createElement("canvas");
    canvas.width = x1 - x0 + 1;
    canvas.height = y1 - y0 + 1;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return;
    context.drawImage(
      image,
      x0,
      y0,
      canvas.width,
      canvas.height,
      0,
      0,
      canvas.width,
      canvas.height,
    );
    const { data } = context.getImageData(0, 0, canvas.width, canvas.height);

    const sum = [0, 0, 0];
    let count = 0;
    for (let y = y0; y <= y1; y += 1) {
      for (let x = x0; x <= x1; x += 1) {
        if ((x - cx) ** 2 + (y - cy) ** 2 > pickRadius ** 2) continue;
        const offset = ((y - y0) * canvas.width + (x - x0)) * 4;
        sum[0] = (sum[0] ?? 0) + (data[offset] ?? 0);
        sum[1] = (sum[1] ?? 0) + (data[offset + 1] ?? 0);
        sum[2] = (sum[2] ?? 0) + (data[offset + 2] ?? 0);
        count += 1;
      }
    }
    if (count === 0) return;
    const rgb = sum.map((c) => Math.round(c / count)) as Rgb;
    pick = { name: pickName, rgb, drgb: toDrgb(rgb), fx, fy };
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      if (pickName) cancelPick();
      confirmSave = false;
    }
  }

  $effect(() => {
    void refreshToken;
    void refresh();
  });

  onMount(() => {
    const colorTimer = setInterval(() => void refresh(), 1000);
    // Keep the pick image fresh until a sample is taken, then freeze it so
    // the marker matches what was sampled.
    const imageTimer = setInterval(() => {
      if (pickName && !pick) loadFrame();
    }, 1000);
    return () => {
      clearInterval(colorTimer);
      clearInterval(imageTimer);
    };
  });
</script>

<svelte:window onkeydown={onKeydown} />

<section class="color-panel">
  <div class="section-heading">
    <div>
      <h2>Colours</h2>
      <p>
        {cameraLabel || `Camera ${camId}`} · learned vs reference blob colours (dRGB,
        brightness removed)
      </p>
    </div>
    <span class:stale={!live} class="small-state">
      {#if live}
        updated {(colors?.age_s ?? 0).toFixed(1)} s ago
      {:else if missing || colors === null}
        not publishing
      {:else}
        stale {(colors.age_s ?? 0).toFixed(0)} s
      {/if}
    </span>
  </div>

  {#if !live}
    <p class="notice warn">
      {staleText}. Reference values and forces below come from the config file.
    </p>
  {/if}

  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Colour</th>
          <th>Learned</th>
          <th>Reference</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        {#each names as name (name)}
          {@const learned = live ? colors?.learned?.[name] : undefined}
          {@const reference = live
            ? colors?.reference?.[name]
            : configColors[name]}
          <tr class:picking={pickName === name}>
            <td class="name">{name}</td>
            <td>
              <span class="swatch-cell">
                <span
                  class="swatch"
                  class:empty={!asRgb(learned)}
                  style={`background: ${previewCss(learned)}`}
                  title="Hue preview (brightness removed)"
                ></span>
                <code>{formatRgb(learned)}</code>
              </span>
            </td>
            <td>
              <span class="swatch-cell">
                <span
                  class="swatch"
                  class:empty={!asRgb(reference)}
                  style={`background: ${previewCss(reference)}`}
                  title="Hue preview (brightness removed)"
                ></span>
                <code>{formatRgb(reference)}</code>
              </span>
            </td>
            <td class="actions">
              <button
                class="ghost"
                class:active={pickName === name}
                onclick={() => {
                  if (pickName === name) cancelPick();
                  else startPick(name);
                }}>{pickName === name ? "Picking..." : "Pick"}</button
              >
            </td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>

  <div class="footer-row">
    <span class="forces">
      reference_force <strong>{forces.reference ?? "--"}</strong>
      &middot; history_force <strong>{forces.history ?? "--"}</strong>
      &middot; swatches are hue previews
    </span>
    <button
      class="ghost"
      disabled={!live || auto?.state === "running"}
      title="Sample the learned colours for ~5 s while robots are on the field and save the stable ones"
      onclick={startAuto}
      >{auto?.state === "running"
        ? `Sampling... ${String(Math.round((auto.progress ?? 0) * 100))}%`
        : "Auto-calibrate colours"}</button
    >
    {#if confirmSave}
      <span class="confirm">
        Overwrite all six reference colours in the config?
        <button class="primary" disabled={saving} onclick={saveLearned}
          >Yes, save</button
        >
        <button class="ghost" onclick={() => (confirmSave = false)}
          >Cancel</button
        >
      </span>
    {:else}
      <button
        class="primary"
        disabled={!live || saving}
        onclick={() => (confirmSave = true)}
        >Save learned colours as reference</button
      >
    {/if}
  </div>

  {#if auto?.state === "running"}
    <div class="progress">
      <span style={`width: ${String((auto.progress ?? 0) * 100)}%`}></span>
    </div>
  {:else if auto?.state === "failed"}
    <p class="notice error">Auto-calibration failed: {auto.error}</p>
  {:else if auto?.state === "done"}
    <p class="notice ok">
      Auto-calibration saved:
      {Object.keys(auto.saved ?? {}).join(", ") || "nothing"}.
      {#each Object.entries(auto.skipped ?? {}) as [name, reason] (name)}
        <br />Skipped <strong>{name}</strong>: {reason}
      {/each}
    </p>
  {/if}

  {#if message}
    <p class={`notice ${message.kind}`}>{message.text}</p>
  {/if}

  {#if pickName}
    <div class="picker">
      <div class="picker-bar">
        <span
          >Click the <strong>{pickName}</strong> colour in the camera image
          (averages a {pickRadius} px radius). Esc cancels.</span
        >
        <button class="ghost" onclick={cancelPick}>Cancel</button>
      </div>
      <div class="pick-stage">
        {#if pickSrc}
          <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
          <img
            bind:this={pickImage}
            src={pickSrc}
            alt={`Camera ${camId} raw image for colour picking`}
            onclick={samplePick}
          />
        {:else}
          <p class="loading">Loading camera {camId} raw image...</p>
        {/if}
        {#if pick}
          <span
            class="marker"
            style={`left: ${String(pick.fx * 100)}%; top: ${String(pick.fy * 100)}%`}
          ></span>
        {/if}
      </div>
      {#if pick}
        <div class="pick-result">
          <span class="swatch-cell">
            <span
              class="swatch"
              style={`background: rgb(${pick.rgb.join(" ")})`}
            ></span>
            <span>RGB <code>{pick.rgb.join(", ")}</code></span>
          </span>
          <span class="swatch-cell">
            <span class="swatch" style={`background: ${previewCss(pick.drgb)}`}
            ></span>
            <span>dRGB <code>{pick.drgb.join(", ")}</code></span>
          </span>
          <button class="primary" disabled={saving} onclick={applyPick}
            >Apply as {pick.name} reference</button
          >
          <button class="ghost" onclick={() => (pick = null)}>Re-pick</button>
        </div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .color-panel {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
    overflow: hidden;
  }

  .section-heading {
    min-height: 54px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
  }

  h2,
  p {
    margin: 0;
  }

  h2 {
    font-size: 13px;
    line-height: 1.25;
    font-weight: 700;
  }

  .section-heading p {
    margin-top: 2px;
    color: var(--text-faint);
    font-size: 12px;
  }

  .small-state {
    min-height: 24px;
    display: inline-flex;
    align-items: center;
    padding: 4px 9px;
    border-radius: 4px;
    font-size: 12px;
    white-space: nowrap;
    color: var(--text-muted);
    background: var(--surface-2);
    border: 1px solid var(--border);
  }

  .small-state.stale {
    color: var(--bad);
    background: var(--bad-bg);
    border-color: var(--bad-border);
  }

  .notice {
    margin: 8px 12px 0;
    padding: 7px 9px;
    border-radius: 4px;
    font-size: 12px;
  }

  .notice.warn,
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

  .notice:last-child {
    margin-bottom: 10px;
  }

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
    height: 34px;
    padding: 5px 9px;
    text-align: left;
    border-bottom: 1px solid var(--border-soft);
    white-space: nowrap;
  }

  th {
    color: var(--text-muted);
    background: var(--surface-2);
    font-weight: 600;
  }

  td.name {
    text-transform: capitalize;
  }

  td.actions {
    text-align: right;
  }

  tr.picking td {
    background: var(--surface-2);
  }

  .swatch-cell {
    display: inline-flex;
    align-items: center;
    gap: 8px;
  }

  .swatch {
    width: 30px;
    height: 18px;
    flex: 0 0 30px;
    border: 1px solid #8b958f;
    border-radius: 3px;
  }

  .swatch.empty {
    background: repeating-linear-gradient(
      45deg,
      var(--surface-2) 0 4px,
      var(--surface) 4px 8px
    ) !important;
  }

  code {
    color: var(--text);
    font-size: 11px;
  }

  button {
    height: 28px;
    padding: 0 10px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 12px;
  }

  button:disabled {
    cursor: default;
    opacity: 0.5;
  }

  button.primary {
    color: #ffffff;
    background: #276f4b;
    border: 1px solid #276f4b;
  }

  button.primary:hover:not(:disabled) {
    background: #1f5e3d;
  }

  button.ghost {
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
  }

  button.ghost:hover,
  button.ghost.active {
    color: var(--text);
    border-color: #276f4b;
  }

  .footer-row,
  .picker-bar,
  .pick-result {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px 12px;
    padding: 8px 12px;
    font-size: 12px;
  }

  .footer-row {
    justify-content: space-between;
  }

  .forces {
    color: var(--text-muted);
  }

  .forces strong {
    color: var(--text);
  }

  .confirm {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    color: var(--bad);
    font-weight: 600;
  }

  .progress {
    height: 6px;
    margin: 0 12px 8px;
    background: var(--surface-2);
    border-radius: 3px;
    overflow: hidden;
  }

  .progress span {
    display: block;
    height: 100%;
    background: #39a56c;
    transition: width 0.4s;
  }

  .picker {
    border-top: 1px solid var(--border);
  }

  .picker-bar {
    justify-content: space-between;
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
  }

  .pick-stage {
    position: relative;
    background: #111713;
  }

  .pick-stage img {
    width: 100%;
    height: auto;
    display: block;
    cursor: crosshair;
  }

  .loading {
    padding: 40px 12px;
    color: #aab6af;
    font-size: 13px;
    text-align: center;
  }

  .marker {
    position: absolute;
    width: 14px;
    height: 14px;
    margin: -7px 0 0 -7px;
    border: 2px solid #ffffff;
    border-radius: 50%;
    box-shadow: 0 0 0 1px #111713;
    pointer-events: none;
  }
</style>

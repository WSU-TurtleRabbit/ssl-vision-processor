<script lang="ts">
  import FieldCorners from "./FieldCorners.svelte";
  import Icon from "./Icon.svelte";
  import LensCorrection from "./LensCorrection.svelte";
  import type { CalibrationStatus, DataMap, Snapshot } from "./health";
  import { drawOverlay, type OverlayState } from "./overlay";

  // Camera view: the snapshot with the overlay canvas sized to the image's
  // aspect ratio, icon mode buttons on the image, a view strip beneath.
  // The corners / lens modes replace the image (they carry their own
  // frozen snapshot) and show their banner as an overlay.

  let {
    camId,
    cameraLabel,
    snapshots,
    selectedView = $bindable("raw"),
    cacheBuster,
    overlay,
    calibration,
    calibrationState,
    lensInfo,
    geometryConfig,
    field,
    cornersMode = $bindable(false),
    lensMode = $bindable(false),
    onfield,
    onsaved,
  }: {
    camId: number;
    cameraLabel: string;
    snapshots: Snapshot[];
    selectedView?: string;
    cacheBuster: number;
    overlay: OverlayState;
    calibration: CalibrationStatus | null;
    calibrationState: string | undefined;
    lensInfo: string | null;
    geometryConfig: DataMap;
    field: DataMap;
    cornersMode?: boolean;
    lensMode?: boolean;
    onfield: () => void;
    onsaved: (message: string) => void;
  } = $props();

  const views = ["raw", "flat", "gradient", "blob"];
  let stage = $state<HTMLDivElement>();
  let canvas = $state<HTMLCanvasElement>();
  let frame = $state({ width: 0, height: 0 });
  let showOverlay = $state(true);

  // Fit a 16:9 frame into the stage so the canvas lines up with the image.
  $effect(() => {
    const element = stage;
    if (!element) return;
    const fit = (): void => {
      const w = element.clientWidth;
      const h = element.clientHeight;
      const ratio = 768 / 432;
      const width = Math.min(w, h * ratio);
      frame = { width, height: width / ratio };
    };
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  });

  $effect(() => {
    void cacheBuster;
    const target = canvas;
    const state = overlay;
    if (!target || !showOverlay || selectedView !== "raw") return;
    requestAnimationFrame(() => {
      drawOverlay(target, state);
    });
  });

  let available = $derived(new Set(snapshots.map((s) => s.view)));
  let hasRaw = $derived(available.has("raw"));
  let fieldValue = $derived((key: string): number => {
    const v = field[key];
    return typeof v === "number" ? v : Number(v) || 0;
  });
</script>

<div class="camera-view">
  <div class="stage" bind:this={stage}>
    {#if lensMode}
      <LensCorrection
        camId={String(camId)}
        {cameraLabel}
        savedLines={calibration?.distortion_lines ??
          geometryConfig["distortion_lines"]}
        oncancel={() => (lensMode = false)}
        {onsaved}
      />
    {:else if cornersMode}
      <FieldCorners
        camId={String(camId)}
        {cameraLabel}
        savedCorners={calibration?.corners ?? geometryConfig["line_corners"]}
        savedOuter={calibration?.outer_corners ??
          geometryConfig["outer_line_corners"]}
        fieldLength={calibration?.field_length ?? fieldValue("field_length")}
        fieldWidth={calibration?.field_width ?? fieldValue("field_width")}
        boundaryWidth={calibration?.boundary_width ??
          fieldValue("boundary_width")}
        boundaryGoalLine={calibration?.boundary_width_goal_line ??
          fieldValue("boundary_width_goal_line")}
        oncancel={() => (cornersMode = false)}
        {onsaved}
      />
    {:else if hasRaw || available.size > 0}
      <div
        class="frame"
        style={`width:${String(frame.width)}px;height:${String(frame.height)}px`}
      >
        <img
          src={`/snapshot/${String(camId)}/${selectedView}?t=${String(cacheBuster)}`}
          alt={`${cameraLabel} ${selectedView}`}
        />
        {#if selectedView === "raw" && showOverlay}
          <canvas bind:this={canvas} aria-label="Field overlay"></canvas>
        {/if}
        <div class="modes">
          <button
            class="icon"
            title="Set field corners: click the 4 corners (Enter saves, Esc cancels)"
            aria-label="Set field corners"
            onclick={() => {
              cornersMode = true;
              lensMode = false;
            }}><Icon name="corners" /></button
          >
          <button
            class="icon"
            title="Lens correction: click points along straight edges"
            aria-label="Lens correction"
            onclick={() => {
              lensMode = true;
              cornersMode = false;
            }}><Icon name="lens" /></button
          >
          <button
            class="icon"
            title="Edit field dimensions (Esc cancels)"
            aria-label="Edit field dimensions"
            onclick={onfield}><Icon name="field" /></button
          >
          <button
            class="icon"
            class:active={showOverlay}
            title="Field overlay on/off"
            aria-label="Toggle the field overlay"
            onclick={() => (showOverlay = !showOverlay)}
            ><Icon name="pick" /></button
          >
        </div>
        <div
          class="chip"
          class:ok={calibrationState === "calibrated"}
          class:warn={calibrationState !== "calibrated"}
        >
          {calibrationState === "calibrated"
            ? "calibrated"
            : calibrationState === "recalibrating"
              ? "recalibrating…"
              : "not calibrated"}{lensInfo ? ` · ${lensInfo}` : ""}
        </div>
      </div>
    {:else}
      <p class="empty">No snapshots yet — start capture / vision_processor.</p>
    {/if}
  </div>
  {#if !cornersMode && !lensMode}
    <nav class="views" aria-label="View">
      {#each views as view (view)}
        <button
          class:active={selectedView === view}
          disabled={!available.has(view)}
          onclick={() => (selectedView = view)}>{view}</button
        >
      {/each}
      {#each snapshots.filter((s) => !views.includes(s.view)) as s (s.view)}
        <button
          class:active={selectedView === s.view}
          onclick={() => (selectedView = s.view)}
          >{s.view.replaceAll(".", " ")}</button
        >
      {/each}
    </nav>
  {/if}
</div>

<style>
  .camera-view {
    display: flex;
    flex-direction: column;
    min-height: 0;
    height: 100%;
  }

  .stage {
    position: relative;
    flex: 1;
    min-height: 0;
    display: grid;
    place-items: center;
    background: #0b0f0d;
    border-radius: 4px;
    overflow: hidden;
  }

  .frame {
    position: relative;
  }

  .frame img {
    display: block;
    width: 100%;
    height: 100%;
  }

  .frame canvas {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }

  .modes {
    position: absolute;
    top: 8px;
    right: 8px;
    display: flex;
    gap: 4px;
  }

  .icon {
    display: inline-grid;
    place-items: center;
    width: 32px;
    height: 32px;
    padding: 0;
    color: #fff;
    background: rgba(17, 23, 19, 0.7);
    border: 1px solid rgba(255, 255, 255, 0.25);
    border-radius: 4px;
    cursor: pointer;
  }

  .icon.active {
    border-color: var(--accent);
  }

  .chip {
    position: absolute;
    top: 8px;
    left: 8px;
    padding: 2px 8px;
    border-radius: 3px;
    font-size: 11px;
    color: #fff;
    background: rgba(17, 23, 19, 0.7);
  }

  .chip.ok {
    color: #8fdcae;
  }

  .chip.warn {
    color: #f2c66b;
  }

  .empty {
    color: var(--text-faint);
    font-size: 12px;
  }

  .views {
    display: flex;
    gap: 2px;
    padding: 4px 0 0;
  }

  .views button {
    height: 24px;
    padding: 0 10px;
    color: var(--text-muted);
    background: none;
    border: 0;
    border-bottom: 2px solid transparent;
    cursor: pointer;
    font: inherit;
    font-size: 12px;
  }

  .views button.active {
    color: var(--text);
    border-bottom-color: var(--accent);
  }

  .views button:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>

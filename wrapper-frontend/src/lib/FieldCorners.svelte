<script lang="ts">
  import { onMount } from "svelte";
  import ShortcutLegend from "./ShortcutLegend.svelte";
  import {
    History,
    isTypingTarget,
    movePoint,
    nearestIndex,
    nudgeDelta,
    shortcutOf,
    toImagePoint,
  } from "./editHistory";

  // Field corners mode: calibrate the camera from four clicked corners
  // instead of white field lines. Shows a frozen raw snapshot, collects 4
  // points (image pixels) and posts them to POST /api/calibration/corners.
  // Default: the OUTER edge incl. boundary (e.g. the foam-mat area) is
  // clicked and the backend derives the field corners through a plane
  // homography (lens distortion ignored); alternatively the field corners
  // are clicked directly. The backend writes geometry.line_corners (+ the
  // clicked outer_line_corners) and refinement: false into the vision
  // config and restarts vision_processor so it recalibrates.

  import type { Point } from "./editHistory";

  type Mode = "outer" | "field";

  let {
    camId,
    cameraLabel = "",
    savedCorners,
    savedOuter,
    fieldLength,
    fieldWidth,
    boundaryWidth,
    boundaryGoalLine,
    oncancel,
    onsaved,
  }: {
    camId: string;
    cameraLabel?: string;
    savedCorners: unknown;
    savedOuter: unknown;
    fieldLength: number;
    fieldWidth: number;
    boundaryWidth: number;
    boundaryGoalLine: number;
    oncancel: () => void;
    onsaved: (message: string) => void;
  } = $props();

  let imageUrl = $state<string | null>(null);
  let loadError = $state<string | null>(null);
  let width = $state(768);
  let height = $state(432);
  let points = $state<Point[]>([]);
  let mode = $state<Mode>("outer");
  let fromConfig = $state(false);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let dragIndex: number | null = null;
  let svg = $state<SVGSVGElement>();

  let ordered = $derived(points.length === 4 ? orderCorners(points) : null);
  let convex = $derived(ordered !== null && isConvex(ordered));
  let clickedLength = $derived(
    mode === "outer" ? fieldLength + 2 * boundaryGoalLine : fieldLength,
  );
  let clickedWidth = $derived(
    mode === "outer" ? fieldWidth + 2 * boundaryWidth : fieldWidth,
  );
  // Field corners derived from the clicked outer corners (preview of what
  // the backend writes as line_corners).
  let inner = $derived(
    mode === "outer" && ordered && convex
      ? innerFromOuter(ordered, fieldLength, fieldWidth)
      : null,
  );
  let markerRadius = $derived(Math.max(5, width / 110));
  // Orientation checks: side 1→2 should be the short side (field_width),
  // side 2→3 the long side (field_length), as the C++ maps them.
  let orientationWarning = $derived.by(() => {
    if (!ordered || !convex || fieldLength <= fieldWidth) return null;
    const side = (a: Point | undefined, b: Point | undefined): number =>
      a && b ? Math.hypot(a[0] - b[0], a[1] - b[1]) : 0;
    const short =
      (side(ordered[0], ordered[1]) + side(ordered[2], ordered[3])) / 2;
    const long =
      (side(ordered[1], ordered[2]) + side(ordered[3], ordered[0])) / 2;
    return short > long * 1.15
      ? "field_length would run along the short side of the rectangle — check field_length/field_width, or make the next corner the origin."
      : null;
  });
  let reorderedNotice = $state<string | null>(null);
  // Field quadrant of each corner in the saved (clockwise) order.
  const QUADRANT = ["−x −y", "−x +y", "+x +y", "+x −y"];
  let fontSize = $derived(Math.max(11, width / 55));

  function parseCorners(value: unknown): Point[] | null {
    if (!Array.isArray(value) || value.length !== 4) return null;
    const parsed: Point[] = [];
    for (const corner of value) {
      if (!Array.isArray(corner) || corner.length < 2) return null;
      const x = Number(corner[0]);
      const y = Number(corner[1]);
      if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
      parsed.push([x, y]);
    }
    return parsed;
  }

  // Keep corner 1 first, the rest clockwise as seen on screen (image y
  // points down, so ascending atan2 around the centroid is clockwise).
  // Same rule as the backend's calibration.order_corners.
  function orderCorners(input: Point[]): Point[] {
    const cx = input.reduce((sum, p) => sum + p[0], 0) / input.length;
    const cy = input.reduce((sum, p) => sum + p[1], 0) / input.length;
    const sorted = [...input].sort(
      (a, b) =>
        Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx),
    );
    const first = input[0];
    const start = first ? sorted.indexOf(first) : 0;
    return [...sorted.slice(start), ...sorted.slice(0, start)];
  }

  function cross(o: Point, a: Point, b: Point): number {
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  }

  function isConvex(quad: Point[]): boolean {
    for (let i = 0; i < 4; i += 1) {
      const a = quad[i];
      const b = quad[(i + 1) % 4];
      const c = quad[(i + 2) % 4];
      if (!a || !b || !c || cross(a, b, c) <= 0) return false;
    }
    for (let i = 0; i < 4; i += 1) {
      for (let j = i + 1; j < 4; j += 1) {
        const a = quad[i];
        const b = quad[j];
        if (a && b && Math.hypot(a[0] - b[0], a[1] - b[1]) < 3) return false;
      }
    }
    return true;
  }

  // Gaussian elimination with partial pivoting.
  function solve(matrix: number[][], rhs: number[]): number[] | null {
    const n = rhs.length;
    const rows = matrix.map((row, i) => [...row, rhs[i] ?? 0]);
    for (let col = 0; col < n; col += 1) {
      let pivot = col;
      for (let r = col + 1; r < n; r += 1) {
        if (Math.abs(rows[r]?.[col] ?? 0) > Math.abs(rows[pivot]?.[col] ?? 0))
          pivot = r;
      }
      const pivotRow = rows[pivot];
      const colRow = rows[col];
      if (!pivotRow || !colRow || Math.abs(pivotRow[col] ?? 0) < 1e-12)
        return null;
      rows[col] = pivotRow;
      rows[pivot] = colRow;
      const lead = pivotRow;
      for (let r = 0; r < n; r += 1) {
        const row = rows[r];
        if (r === col || !row) continue;
        const factor = (row[col] ?? 0) / (lead[col] ?? 1);
        rows[r] = row.map((value, k) => value - factor * (lead[k] ?? 0));
      }
    }
    return rows.map((row, i) => (row[n] ?? 0) / (row[i] ?? 1));
  }

  function fieldRectangle(halfX: number, halfY: number): Point[] {
    return [
      [-halfX, -halfY],
      [-halfX, halfY],
      [halfX, halfY],
      [halfX, -halfY],
    ];
  }

  // Same as the backend's calibration.inner_from_outer.
  function innerFromOuter(
    outer: Point[],
    length: number,
    width: number,
  ): Point[] | null {
    const src = fieldRectangle(
      length / 2 + boundaryGoalLine,
      width / 2 + boundaryWidth,
    );
    const matrix: number[][] = [];
    const rhs: number[] = [];
    src.forEach(([x, y], i) => {
      const [u, v] = outer[i] ?? [0, 0];
      matrix.push([x, y, 1, 0, 0, 0, -u * x, -u * y]);
      rhs.push(u);
      matrix.push([0, 0, 0, x, y, 1, -v * x, -v * y]);
      rhs.push(v);
    });
    const h = solve(matrix, rhs);
    if (!h) return null;
    const [a = 0, b = 0, c = 0, d = 0, e = 0, f = 0, g = 0, k = 0] = h;
    return fieldRectangle(length / 2, width / 2).map(([x, y]) => {
      const w = g * x + k * y + 1;
      return [(a * x + b * y + c) / w, (d * x + e * y + f) / w];
    });
  }

  // Undo/redo over whole point lists; the selected point takes arrow keys.
  const history = new History<Point[]>();
  let canUndo = $state(false);
  let canRedo = $state(false);
  let selected = $state<number | null>(null);
  let dragStart: Point[] | null = null;
  const HIT_PX = 20;

  function syncHistory(): void {
    canUndo = history.canUndo;
    canRedo = history.canRedo;
  }

  function commit(next: Point[]): void {
    history.push(points);
    points = next;
    fromConfig = false;
    error = null;
    syncHistory();
  }

  function normalise(): void {
    // Renumber 2..4 clockwise once all four are placed, so the labels match
    // what will be saved (the C++ only accepts clockwise orders).
    if (points.length === 4) {
      const next = orderCorners(points);
      if (isConvex(next)) {
        const changed = next.some((p, i) => p !== points[i]);
        if (changed)
          reorderedNotice =
            "Your click order was counter-clockwise; corners 2–4 were renumbered clockwise as the calibration requires.";
        points = next;
      }
    }
  }

  // Orientation: corner 1 is the origin; rotate which clicked corner is #1.
  function rotateOrigin(steps: number): void {
    if (points.length !== 4) return;
    const shift = ((steps % 4) + 4) % 4;
    commit([...points.slice(shift), ...points.slice(0, shift)]);
    selected = null;
    reorderedNotice = null;
  }

  function onPointerDown(event: PointerEvent): void {
    if (event.button !== 0) return;
    error = null;
    const point = toImagePoint(svg, event, width, height);
    if (!point) return;
    if (event.ctrlKey || event.metaKey) {
      const index = nearestIndex(points, point, HIT_PX);
      if (index >= 0) {
        commit(points.filter((_, i) => i !== index));
        selected = null;
      }
      event.preventDefault();
      return;
    }
    const target = event.target as Element;
    const index = target.getAttribute("data-index");
    if (index !== null) {
      dragIndex = Number(index);
      selected = dragIndex;
      dragStart = points;
      svg?.setPointerCapture(event.pointerId);
      event.preventDefault();
      return;
    }
    if (points.length >= 4) return;
    commit([...points, point]);
    selected = points.length - 1;
    normalise();
  }

  function onPointerMove(event: PointerEvent): void {
    if (dragIndex === null) return;
    const point = toImagePoint(svg, event, width, height);
    if (!point) return;
    points = points.map((p, i) => (i === dragIndex ? point : p));
    fromConfig = false;
  }

  function onPointerUp(): void {
    if (dragIndex === null) return;
    if (dragStart && dragStart !== points) {
      history.push(dragStart);
      syncHistory();
    }
    dragIndex = null;
    dragStart = null;
    normalise();
  }

  function undo(): void {
    const previous = history.undo(points);
    if (previous) points = previous;
    selected = null;
    syncHistory();
  }

  function redo(): void {
    const next = history.redo(points);
    if (next) points = next;
    selected = null;
    syncHistory();
  }

  function removeLast(): void {
    if (points.length === 0) return;
    commit(points.slice(0, -1));
    selected = null;
  }

  function reset(): void {
    if (points.length === 0) return;
    commit([]);
    selected = null;
  }

  function nudge(event: KeyboardEvent): void {
    if (selected === null || !points[selected]) return;
    const index = selected;
    const delta = nudgeDelta(event);
    commit(
      points.map((p, i) =>
        i === index ? movePoint(p, delta, width, height) : p,
      ),
    );
  }

  async function save(): Promise<void> {
    if (!ordered || !convex) return;
    saving = true;
    error = null;
    try {
      const response = await fetch("/api/calibration/corners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cam_id: Number(camId),
          corners: ordered,
          mode,
        }),
      });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        message?: string;
      };
      if (response.ok) onsaved(result.message ?? "Saved.");
      else error = result.error ?? `HTTP ${String(response.status)}`;
    } catch (caught) {
      error = String(caught);
    } finally {
      saving = false;
    }
  }

  function onKeydown(event: KeyboardEvent): void {
    if (isTypingTarget(event)) return;
    const shortcut = shortcutOf(event);
    if (shortcut === null) return;
    event.preventDefault();
    switch (shortcut) {
      case "undo":
        undo();
        break;
      case "redo":
        redo();
        break;
      case "escape":
        oncancel();
        break;
      case "removeLast":
        removeLast();
        break;
      case "nudge":
        nudge(event);
        break;
      case "enter":
        if (convex && !saving && imageUrl) void save();
        break;
    }
  }

  function midpoint(a: Point | undefined, b: Point | undefined): Point | null {
    return a && b ? [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] : null;
  }

  function mm(value: number): string {
    return `${String(Math.round(value))} mm`;
  }

  let edgeLabels = $derived.by(() => {
    if (!ordered || !convex) return [];
    const labels = [clickedWidth, clickedLength, clickedWidth, clickedLength];
    return labels.flatMap((length, i) => {
      const mid = midpoint(ordered[i], ordered[(i + 1) % 4]);
      return mid ? [{ mid, text: mm(length), key: i }] : [];
    });
  });

  onMount(() => {
    const outer = parseCorners(savedOuter);
    const saved = parseCorners(savedCorners);
    if (outer) {
      points = outer;
      mode = "outer";
      fromConfig = true;
    } else if (saved) {
      points = saved;
      mode = "field";
      fromConfig = true;
    }
    let url: string | null = null;
    const session = { cancelled: false };
    // Freeze one raw snapshot for the whole session.
    void (async () => {
      try {
        const response = await fetch(
          `/snapshot/${camId}/raw?t=${String(Date.now())}`,
        );
        if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
        const blob = await response.blob();
        if (session.cancelled) return;
        url = URL.createObjectURL(blob);
        imageUrl = url;
      } catch (caught) {
        loadError = `No raw snapshot for camera ${camId} (${String(caught)}). Is vision_processor running?`;
      }
    })();
    return () => {
      session.cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  });
</script>

<svelte:window onkeydown={onKeydown} />

<div class="corners">
  <div class="instructions">
    <p class="camera-label">
      <strong>{cameraLabel || `Camera ${camId}`}</strong> (cam {camId})
    </p>
    <div class="mode" role="radiogroup" aria-label="What you click">
      <span>I click:</span>
      <label
        ><input type="radio" bind:group={mode} value="outer" /> outer edge incl. boundary
        (e.g. the mat corners)</label
      >
      <label
        ><input type="radio" bind:group={mode} value="field" /> the field corners
        directly</label
      >
    </div>
    <p>
      <strong
        >Corner 1 is the origin (−x,−y); x runs along the long side toward the
        +x goal. Rotate 180° to swap the goals.</strong
      >
      The camera angle does not matter — the calibration solves tilt and rotation
      itself; only the corner order fixes the field's coordinate frame.
    </p>
    <ol>
      <li>
        Click <strong>corner 1</strong>: the corner on the origin side, i.e.
        towards field position (−x, −y). With the long side running left–right
        on screen that is usually the <em>bottom-left</em> corner.
      </li>
      <li>
        Click the other three corners <strong>going clockwise</strong> (as seen
        on screen). Side 1→2 must be a short side ({mm(clickedWidth)}), side 2→3
        a long side ({mm(clickedLength)}).
      </li>
    </ol>
    <p>
      {#if mode === "outer"}
        You click the outer edge <strong
          >{mm(clickedLength)} × {mm(clickedWidth)}</strong
        >; the field is <strong>{mm(fieldLength)} × {mm(fieldWidth)}</strong>
        with a {mm(boundaryWidth)} boundary{boundaryGoalLine !== boundaryWidth
          ? ` (${mm(boundaryGoalLine)} behind the goal lines)`
          : ""}. The field corners (dashed) are derived from your clicks with a
        flat homography, so lens distortion is ignored.
      {:else}
        You click the field corners of the published field <strong
          >{mm(fieldLength)} × {mm(fieldWidth)}</strong
        >.
      {/if}
      Robot and ball positions are reported relative to the field ((0, 0) is its centre).
      The clicked rectangle must really have that size, otherwise the scale is wrong
      and robots are not recognised. Drag a marker to adjust it; Esc cancels.
    </p>
    <ShortcutLegend />
  </div>

  <div class="stage">
    {#if imageUrl}
      <img
        src={imageUrl}
        alt={`${cameraLabel || `Camera ${camId}`} raw snapshot for field corners`}
        onload={(event) => {
          const image = event.currentTarget as HTMLImageElement;
          width = image.naturalWidth || width;
          height = image.naturalHeight || height;
        }}
      />
      <svg
        bind:this={svg}
        viewBox={`0 0 ${String(width)} ${String(height)}`}
        preserveAspectRatio="none"
        role="application"
        aria-label="Click the four field corners"
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}
        onpointercancel={onPointerUp}
      >
        {#if points.length >= 2}
          <polygon
            class:closed={points.length === 4}
            class:invalid={points.length === 4 && !convex}
            points={points.map((p) => p.join(",")).join(" ")}
          />
        {/if}
        {#if inner}
          <polygon
            class="inner"
            points={inner.map((p) => p.join(",")).join(" ")}
          />
          {#each inner as corner, index (index)}
            <circle
              class="inner-corner"
              cx={corner[0]}
              cy={corner[1]}
              r={markerRadius * 0.6}
            />
          {/each}
        {/if}
        {#each edgeLabels as label (label.key)}
          <text
            class="edge"
            x={label.mid[0]}
            y={label.mid[1]}
            font-size={fontSize * 0.85}>{label.text}</text
          >
        {/each}
        {#each points as point, index (index)}
          <circle
            data-index={index}
            class:origin={index === 0}
            class:selected={index === selected}
            cx={point[0]}
            cy={point[1]}
            r={markerRadius}
          />
          <text
            class="number"
            text-anchor={point[0] > width * 0.8 ? "end" : "start"}
            x={point[0] + (point[0] > width * 0.8 ? -1.4 : 1.4) * markerRadius}
            y={point[1] < height * 0.1
              ? point[1] + markerRadius * 3
              : point[1] - markerRadius * 1.4}
            font-size={fontSize}
            >{index + 1} · {QUADRANT[index] ?? ""}{index === 0
              ? " (origin)"
              : ""}</text
          >
        {/each}
      </svg>
    {:else if loadError}
      <p class="message">{loadError}</p>
    {:else}
      <p class="message">Loading camera {camId} raw snapshot...</p>
    {/if}
  </div>

  {#if points.length === 4}
    <div class="bar orientation">
      <span class="hint"><strong>Orientation:</strong></span>
      <button
        class="ghost"
        onclick={() => {
          rotateOrigin(2);
        }}
        title="Swap the goal ends">Rotate 180°</button
      >
      <span class="hint"
        >Origin corner (−x −y) is #1 · make another corner the origin:</span
      >
      {#each [1, 2, 3] as step (step)}
        <button
          class="ghost"
          onclick={() => {
            rotateOrigin(step);
          }}>corner {step + 1} ({QUADRANT[step]}) → 1</button
        >
      {/each}
      <span class="hint">
        {#each points as point, index (index)}
          {index + 1}·{QUADRANT[index]} ({point[0]}, {point[1]}){index < 3
            ? " · "
            : ""}
        {/each}
      </span>
      {#if orientationWarning}
        <span class="hint warn">{orientationWarning}</span>
      {/if}
      {#if reorderedNotice}
        <span class="hint warn">{reorderedNotice}</span>
      {/if}
    </div>
  {/if}

  <div class="bar">
    <span class="hint">
      {#if fromConfig}
        Showing the corners saved in the config — drag to adjust or Reset.
      {:else if points.length < 4}
        Click corner {points.length + 1} of 4.
      {:else if !convex}
        These 4 points do not form a convex shape — move or undo one.
      {:else}
        Ready: corner 1 at ({ordered?.[0]?.join(", ")}) px.
      {/if}
    </span>
    <span class="buttons">
      <button class="ghost" disabled={!canUndo} onclick={undo} title="Ctrl+Z"
        >Undo</button
      >
      <button class="ghost" disabled={!canRedo} onclick={redo} title="Ctrl+Y"
        >Redo</button
      >
      <button class="ghost" disabled={points.length === 0} onclick={reset}
        >Reset</button
      >
      <button class="ghost" onclick={oncancel} title="Esc">Cancel</button>
      <button
        class="primary"
        disabled={!convex || saving || !imageUrl}
        title="Enter"
        onclick={save}>{saving ? "Saving..." : "Save corners"}</button
      >
    </span>
  </div>
  {#if error}
    <p class="notice error">{error}</p>
  {/if}
</div>

<style>
  .corners {
    display: grid;
  }

  .instructions {
    padding: 8px 12px;
    color: var(--text);
    background: var(--surface-2);
    border-bottom: 1px solid var(--border);
    font-size: 12px;
    line-height: 1.45;
  }

  .instructions ol {
    margin: 0 0 4px;
    padding-left: 18px;
  }

  .instructions p {
    margin: 0;
    color: var(--text-muted);
  }

  .stage {
    position: relative;
    background: #111713;
    user-select: none;
  }

  .stage img {
    width: 100%;
    height: auto;
    display: block;
  }

  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    cursor: crosshair;
    touch-action: none;
  }

  polygon {
    fill: rgba(77, 216, 160, 0.12);
    stroke: #4dd8a0;
    stroke-width: 2;
    stroke-dasharray: 6 4;
    vector-effect: non-scaling-stroke;
  }

  polygon.inner {
    fill: none;
    stroke: #ffffff;
    stroke-dasharray: 5 4;
    stroke-width: 1.5;
  }

  circle.inner-corner {
    fill: #ffffff;
    stroke-width: 1;
    cursor: default;
    pointer-events: none;
  }

  .mode {
    display: flex;
    flex-wrap: wrap;
    gap: 4px 12px;
    margin-bottom: 4px;
    font-weight: 600;
  }

  .mode label {
    font-weight: 400;
    cursor: pointer;
  }

  polygon.closed {
    stroke-dasharray: none;
  }

  polygon.invalid {
    fill: rgba(255, 91, 85, 0.15);
    stroke: #ff5b55;
  }

  circle {
    fill: #ffd451;
    stroke: #111713;
    stroke-width: 2;
    vector-effect: non-scaling-stroke;
    cursor: grab;
  }

  circle.selected {
    stroke: #ffffff;
    stroke-width: 3;
  }

  circle.origin {
    fill: #ff5b55;
  }

  text {
    fill: #ffffff;
    stroke: #111713;
    stroke-width: 3px;
    paint-order: stroke;
    font-weight: 700;
    pointer-events: none;
  }

  text.edge {
    fill: #bfe8cf;
    font-weight: 600;
    text-anchor: middle;
    dominant-baseline: middle;
  }

  .message {
    margin: 0;
    padding: 60px 12px;
    color: #aab6af;
    font-size: 13px;
    text-align: center;
  }

  .bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 8px 12px;
    padding: 8px 12px;
    font-size: 12px;
    border-top: 1px solid var(--border);
  }

  .hint {
    color: var(--text-muted);
  }

  .hint.warn {
    color: var(--warn);
  }

  .bar.orientation {
    background: var(--surface-2);
  }

  .buttons {
    display: inline-flex;
    gap: 6px;
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

  button.ghost {
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
  }

  .notice.error {
    margin: 0 12px 10px;
    padding: 7px 9px;
    border-radius: 4px;
    font-size: 12px;
    color: var(--bad);
    background: var(--bad-bg);
    border: 1px solid var(--bad-border);
  }
</style>

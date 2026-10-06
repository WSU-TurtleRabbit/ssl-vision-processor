<script lang="ts">
  import { onMount } from "svelte";

  // Lens correction mode: the user clicks points along physically straight
  // edges (mat seams) in a frozen raw snapshot. vision_processor fits its
  // lens distortion (k2 + principal point) so these become straight again.
  // POST /api/calibration/lens {cam_id, lines}; DELETE removes it.

  type Point = [number, number];

  const MIN_LINES = 3;
  const MIN_POINTS = 4;
  const COLORS = [
    "#ffd451",
    "#4dd8a0",
    "#40cfff",
    "#ff78ae",
    "#ff8a2a",
    "#b99cff",
    "#f4f7f5",
  ];

  let {
    camId,
    cameraLabel = "",
    savedLines,
    oncancel,
    onsaved,
  }: {
    camId: string;
    cameraLabel?: string;
    savedLines: unknown;
    oncancel: () => void;
    onsaved: (message: string) => void;
  } = $props();

  let imageUrl = $state<string | null>(null);
  let loadError = $state<string | null>(null);
  let width = $state(768);
  let height = $state(432);
  // The last line is the one being drawn.
  let lines = $state<Point[][]>([[]]);
  let fromConfig = $state(false);
  let saving = $state(false);
  let error = $state<string | null>(null);
  let confirmRemove = $state(false);
  let svg = $state<SVGSVGElement>();

  let complete = $derived(lines.filter((line) => line.length >= MIN_POINTS));
  let tooShort = $derived(
    lines.filter((line) => line.length > 0 && line.length < MIN_POINTS).length,
  );
  let ready = $derived(complete.length >= MIN_LINES && tooShort === 0);
  let current = $derived(lines[lines.length - 1] ?? []);
  let hasSaved = $derived(parseLines(savedLines) !== null);
  let radius = $derived(Math.max(3, width / 200));
  let fontSize = $derived(Math.max(11, width / 60));

  function parseLines(value: unknown): Point[][] | null {
    if (!Array.isArray(value) || value.length === 0) return null;
    const parsed: Point[][] = [];
    for (const line of value) {
      if (!Array.isArray(line)) return null;
      const points: Point[] = [];
      for (const point of line) {
        if (!Array.isArray(point) || point.length < 2) return null;
        const x = Number(point[0]);
        const y = Number(point[1]);
        if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
        points.push([x, y]);
      }
      parsed.push(points);
    }
    return parsed;
  }

  function onPointerDown(event: PointerEvent): void {
    if (event.button !== 0 || !svg) return;
    error = null;
    confirmRemove = false;
    const rect = svg.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return;
    // The SVG covers the image exactly; map CSS px to image px.
    const x = ((event.clientX - rect.left) / rect.width) * width;
    const y = ((event.clientY - rect.top) / rect.height) * height;
    const point: Point = [
      Math.round(Math.min(width, Math.max(0, x)) * 10) / 10,
      Math.round(Math.min(height, Math.max(0, y)) * 10) / 10,
    ];
    lines = [...lines.slice(0, -1), [...current, point]];
    fromConfig = false;
  }

  function nextLine(): void {
    if (current.length === 0) return;
    lines = [...lines, []];
  }

  function undoPoint(): void {
    if (current.length > 0) {
      lines = [...lines.slice(0, -1), current.slice(0, -1)];
    } else if (lines.length > 1) {
      // Back into the previous line.
      const previous = lines[lines.length - 2] ?? [];
      lines = [...lines.slice(0, -2), previous.slice(0, -1)];
    }
  }

  function deleteLine(): void {
    // Drop the line being drawn, or the previous one if it is empty.
    lines =
      current.length > 0
        ? [...lines.slice(0, -1), []]
        : [...lines.slice(0, -2), []];
  }

  function clearAll(): void {
    lines = [[]];
    fromConfig = false;
    error = null;
  }

  async function send(method: "POST" | "DELETE"): Promise<void> {
    saving = true;
    error = null;
    try {
      const body: Record<string, unknown> = { cam_id: Number(camId) };
      if (method === "POST") body["lines"] = complete;
      const response = await fetch("/api/calibration/lens", {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
        message?: string;
      };
      if (response.ok)
        onsaved(
          (method === "DELETE" ? "Lens correction removed. " : "") +
            (result.message ?? "Saved."),
        );
      else error = result.error ?? `HTTP ${String(response.status)}`;
    } catch (caught) {
      error = String(caught);
    } finally {
      saving = false;
    }
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Escape") oncancel();
    else if (event.key === "Enter") nextLine();
    else if ((event.ctrlKey || event.metaKey) && event.key === "z") undoPoint();
  }

  onMount(() => {
    const saved = parseLines(savedLines);
    if (saved) {
      lines = [...saved, []];
      fromConfig = true;
    }
    let url: string | null = null;
    const session = { cancelled: false };
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

<div class="lens">
  <div class="instructions">
    <p class="camera-label">
      <strong>{cameraLabel || `Camera ${camId}`}</strong> (cam {camId})
    </p>
    <p>
      The camera lens bends straight lines, most of all near the picture edges.
      Show it some lines that are straight in reality:
      <strong>click 6–10 points along a straight mat seam or edge</strong>, then
      press <em>Next line</em> (or Enter). Do 3–5 different seams, spread over
      the picture — include some near the edges, where the bending is strongest.
      Each line needs at least {MIN_POINTS} points; at least {MIN_LINES} lines. Esc
      cancels.
    </p>
  </div>

  <div class="stage">
    {#if imageUrl}
      <img
        src={imageUrl}
        alt={`${cameraLabel || `Camera ${camId}`} raw snapshot for lens correction`}
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
        aria-label="Click points along straight edges"
        onpointerdown={onPointerDown}
      >
        {#each lines as line, index (index)}
          {@const color = COLORS[index % COLORS.length] ?? "#ffd451"}
          {#if line.length >= 2}
            <polyline
              class:short={line.length < MIN_POINTS}
              stroke={color}
              points={line.map((p) => p.join(",")).join(" ")}
            />
          {/if}
          {#each line as point, pointIndex (pointIndex)}
            <circle cx={point[0]} cy={point[1]} r={radius} fill={color} />
          {/each}
          {#if line[0]}
            <text
              x={line[0][0] + radius * 1.5}
              y={line[0][1] - radius * 1.5}
              font-size={fontSize}
              fill={color}>{index + 1}</text
            >
          {/if}
        {/each}
      </svg>
    {:else if loadError}
      <p class="message">{loadError}</p>
    {:else}
      <p class="message">Loading camera {camId} raw snapshot...</p>
    {/if}
  </div>

  <div class="bar">
    <span class="hint">
      {#if fromConfig}
        Showing the saved lines — add more, or Clear all to start over.
      {:else}
        Line {lines.length}: {current.length} point{current.length === 1
          ? ""
          : "s"} · {complete.length} complete line{complete.length === 1
          ? ""
          : "s"}{tooShort > 0
          ? ` · ${String(tooShort)} line(s) need ${String(MIN_POINTS)}+ points`
          : ""}
      {/if}
    </span>
    <span class="buttons">
      <button class="ghost" disabled={current.length === 0} onclick={nextLine}
        >Next line</button
      >
      <button
        class="ghost"
        disabled={current.length === 0 && lines.length < 2}
        onclick={undoPoint}>Undo point</button
      >
      <button
        class="ghost"
        disabled={current.length === 0 && lines.length < 2}
        onclick={deleteLine}>Delete line</button
      >
      <button class="ghost" onclick={clearAll}>Clear all</button>
      <button class="ghost" onclick={oncancel}>Cancel</button>
      <button
        class="primary"
        disabled={!ready || saving || !imageUrl}
        onclick={() => send("POST")}
        >{saving ? "Saving..." : "Save lens correction"}</button
      >
    </span>
  </div>
  {#if hasSaved}
    <div class="bar remove">
      {#if confirmRemove}
        <span>Remove the saved lens correction and recalibrate?</span>
        <span class="buttons">
          <button
            class="danger"
            disabled={saving}
            onclick={() => send("DELETE")}>Yes, remove</button
          >
          <button class="ghost" onclick={() => (confirmRemove = false)}
            >Keep</button
          >
        </span>
      {:else}
        <span class="hint">A lens correction is saved in the config.</span>
        <button class="ghost" onclick={() => (confirmRemove = true)}
          >Remove lens correction</button
        >
      {/if}
    </div>
  {/if}
  {#if error}
    <p class="notice error">{error}</p>
  {/if}
</div>

<style>
  .lens {
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

  .instructions p {
    margin: 0;
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

  polyline {
    fill: none;
    stroke-width: 2;
    vector-effect: non-scaling-stroke;
  }

  polyline.short {
    stroke-dasharray: 5 4;
  }

  circle {
    stroke: #111713;
    stroke-width: 1;
    vector-effect: non-scaling-stroke;
  }

  text {
    stroke: #111713;
    stroke-width: 3px;
    paint-order: stroke;
    font-weight: 700;
    pointer-events: none;
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

  .bar.remove {
    background: var(--warn-bg);
  }

  .hint {
    color: var(--text-muted);
  }

  .buttons {
    display: inline-flex;
    flex-wrap: wrap;
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

  button.danger {
    color: #ffffff;
    background: #a8423d;
    border: 1px solid #a8423d;
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

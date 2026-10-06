<script lang="ts">
  // Field geometry editor: edits the geometry.yml the backend runs with
  // (GET/POST /api/geometry). The backend validates, writes it
  // comment-preserving and publishes the new geometry at once (no restart).
  // Also holds the "refine with field lines" switch (geometry.refinement
  // in the vision config, POST /api/calibration/refinement).

  interface GeometryResponse {
    path: string;
    field: Record<string, number>;
    optional_field_lines: Record<string, boolean>;
  }

  let {
    camId,
    refinement,
    refreshToken,
    onchange,
  }: {
    camId: number;
    refinement: boolean;
    refreshToken: number;
    onchange: () => void;
  } = $props();

  const fieldKeys: [string, string][] = [
    ["field_length", "Field length"],
    ["field_width", "Field width"],
    ["boundary_width", "Boundary (sides)"],
    ["boundary_width_goal_line", "Boundary (behind goals)"],
    ["goal_width", "Goal width"],
    ["goal_depth", "Goal depth"],
    ["penalty_area_depth", "Penalty area depth"],
    ["penalty_area_width", "Penalty area width"],
    ["center_circle_radius", "Centre circle radius"],
    ["line_thickness", "Line thickness"],
  ];
  const lineKeys: [string, string][] = [
    ["halfway", "Halfway line"],
    ["goal2goal", "Goal-to-goal line"],
    ["centercircle", "Centre circle"],
    ["penalty", "Penalty areas"],
  ];

  let loaded = $state<GeometryResponse | null>(null);
  let field = $state<Record<string, number>>({});
  let lines = $state<Record<string, boolean>>({});
  let editing = $state(false);
  let saving = $state(false);
  let message = $state<{ kind: "ok" | "error"; text: string } | null>(null);
  let sizeChanged = $state(false);
  let refinementDraft = $state<boolean | null>(null);

  let problem = $derived(validate(field));
  let dirty = $derived(
    loaded !== null &&
      (fieldKeys.some(([key]) => field[key] !== loaded?.field[key]) ||
        lineKeys.some(
          ([key]) => lines[key] !== loaded?.optional_field_lines[key],
        )),
  );

  function num(key: string): number {
    const value = field[key];
    return typeof value === "number" && Number.isFinite(value) ? value : NaN;
  }

  // Mirrors wrapper_backend/fieldgeometry.validate (server re-checks).
  function validate(values: Record<string, number>): string | null {
    if (Object.keys(values).length === 0) return null;
    for (const [key, label] of fieldKeys) {
      const value = values[key];
      if (typeof value !== "number" || !Number.isFinite(value))
        return `${label} must be a number`;
      if (value < 0) return `${label} must not be negative`;
    }
    const length = num("field_length");
    const width = num("field_width");
    if (length <= 0 || width <= 0) return "Field length and width must be > 0";
    if (num("line_thickness") <= 0) return "Line thickness must be > 0";
    if (width > length) return "Field width must not exceed field length";
    if (num("goal_width") >= width)
      return "Goal must be narrower than the field";
    if (num("penalty_area_width") >= width)
      return "Penalty area must be narrower than the field";
    if (
      num("penalty_area_width") > 0 &&
      num("penalty_area_width") < num("goal_width")
    )
      return "Penalty area must be at least as wide as the goal";
    if (num("penalty_area_depth") >= length / 2)
      return "Penalty area depth must be less than half the field length";
    if (num("center_circle_radius") * 2 >= width)
      return "Centre circle must fit into the field width";
    if (num("line_thickness") > 100)
      return "Line thickness looks wrong (> 100 mm)";
    return null;
  }

  async function load(): Promise<void> {
    try {
      const response = await fetch("/api/geometry");
      if (!response.ok) return;
      loaded = (await response.json()) as GeometryResponse;
      if (!editing) {
        field = { ...loaded.field };
        lines = { ...loaded.optional_field_lines };
      }
    } catch {
      // Backend reachability is shown elsewhere.
    }
  }

  async function post(
    url: string,
    body: unknown,
  ): Promise<Record<string, unknown>> {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    if (!response.ok)
      throw new Error(
        typeof result["error"] === "string"
          ? result["error"]
          : `HTTP ${String(response.status)}`,
      );
    return result;
  }

  function messageOf(
    result: Record<string, unknown>,
    fallback: string,
  ): string {
    const value = result["message"];
    return typeof value === "string" ? value : fallback;
  }

  async function save(): Promise<void> {
    if (problem) return;
    saving = true;
    message = null;
    try {
      const result = await post("/api/geometry", {
        field,
        optional_field_lines: lines,
      });
      sizeChanged = result["size_changed"] === true;
      message = {
        kind: "ok",
        text:
          typeof result["message"] === "string" ? result["message"] : "Saved.",
      };
      editing = false;
      await load();
      onchange();
    } catch (error) {
      message = { kind: "error", text: String(error) };
    } finally {
      saving = false;
    }
  }

  async function recalibrate(): Promise<void> {
    saving = true;
    try {
      const result = await post("/api/calibration/recalibrate", {
        cam_id: camId,
      });
      message = {
        kind: "ok",
        text: messageOf(result, "Recalibrating."),
      };
      sizeChanged = false;
      onchange();
    } catch (error) {
      message = { kind: "error", text: String(error) };
    } finally {
      saving = false;
    }
  }

  async function saveRefinement(restart: boolean): Promise<void> {
    if (refinementDraft === null) return;
    saving = true;
    try {
      const result = await post("/api/calibration/refinement", {
        cam_id: camId,
        enabled: refinementDraft,
        restart,
      });
      message = { kind: "ok", text: messageOf(result, "Saved.") };
      refinementDraft = null;
      onchange();
    } catch (error) {
      message = { kind: "error", text: String(error) };
    } finally {
      saving = false;
    }
  }

  // Preview geometry (mm -> SVG units, y up).
  let preview = $derived.by(() => {
    const length = num("field_length");
    const width = num("field_width");
    if (!(length > 0 && width > 0)) return null;
    const bx = num("boundary_width_goal_line") || 0;
    const by = num("boundary_width") || 0;
    const goalDepth = num("goal_depth") || 0;
    const outerX = length / 2 + Math.max(bx, goalDepth);
    const outerY = width / 2 + by;
    return {
      viewBox: `${String(-outerX)} ${String(-outerY)} ${String(2 * outerX)} ${String(2 * outerY)}`,
      stroke: Math.max(length, width) / 300,
      hl: length / 2,
      hw: width / 2,
      bx,
      by,
      goalWidth: num("goal_width") || 0,
      goalDepth,
      penaltyDepth: num("penalty_area_depth") || 0,
      penaltyWidth: num("penalty_area_width") || 0,
      radius: num("center_circle_radius") || 0,
    };
  });

  $effect(() => {
    void refreshToken;
    void load();
  });
</script>

<section class="geometry">
  <div class="section-heading">
    <h2>Field geometry</h2>
    {#if editing}
      <span class="buttons">
        <button
          class="ghost"
          onclick={() => {
            editing = false;
            if (loaded) {
              field = { ...loaded.field };
              lines = { ...loaded.optional_field_lines };
            }
          }}>Cancel</button
        >
        <button
          class="primary"
          disabled={!dirty || problem !== null || saving}
          onclick={save}>{saving ? "Saving..." : "Save & publish"}</button
        >
      </span>
    {:else}
      <button class="ghost" onclick={() => (editing = true)}>Edit</button>
    {/if}
  </div>

  {#if preview}
    {@const p = preview}
    <svg class="preview" viewBox={p.viewBox} aria-label="Field preview">
      <g transform="scale(1,-1)" stroke-width={p.stroke} fill="none">
        <rect
          class="boundary"
          x={-p.hl - p.bx}
          y={-p.hw - p.by}
          width={2 * (p.hl + p.bx)}
          height={2 * (p.hw + p.by)}
        />
        <rect
          class="line"
          x={-p.hl}
          y={-p.hw}
          width={2 * p.hl}
          height={2 * p.hw}
        />
        <line
          class:line={lines["halfway"]}
          class:off={!lines["halfway"]}
          x1="0"
          y1={-p.hw}
          x2="0"
          y2={p.hw}
        />
        <line
          class:line={lines["goal2goal"]}
          class:off={!lines["goal2goal"]}
          x1={-p.hl}
          y1="0"
          x2={p.hl}
          y2="0"
        />
        {#if p.radius > 0}
          <circle
            class:line={lines["centercircle"]}
            class:off={!lines["centercircle"]}
            r={p.radius}
          />
        {/if}
        {#each [-1, 1] as side (side)}
          {#if p.penaltyDepth > 0 && p.penaltyWidth > 0}
            <polyline
              class:line={lines["penalty"]}
              class:off={!lines["penalty"]}
              points={`${String(side * p.hl)},${String(-p.penaltyWidth / 2)} ${String(side * (p.hl - p.penaltyDepth))},${String(-p.penaltyWidth / 2)} ${String(side * (p.hl - p.penaltyDepth))},${String(p.penaltyWidth / 2)} ${String(side * p.hl)},${String(p.penaltyWidth / 2)}`}
            />
          {/if}
          {#if p.goalWidth > 0}
            <polyline
              class="goal"
              points={`${String(side * p.hl)},${String(-p.goalWidth / 2)} ${String(side * (p.hl + p.goalDepth))},${String(-p.goalWidth / 2)} ${String(side * (p.hl + p.goalDepth))},${String(p.goalWidth / 2)} ${String(side * p.hl)},${String(p.goalWidth / 2)}`}
            />
          {/if}
        {/each}
      </g>
    </svg>
    <p class="caption">
      {num("field_length")} × {num("field_width")} mm field, outer edge {num(
        "field_length",
      ) +
        2 * (num("boundary_width_goal_line") || 0)} × {num("field_width") +
        2 * (num("boundary_width") || 0)} mm. Dashed = marking not on the carpet.
    </p>
  {/if}

  {#if editing}
    <div class="form">
      {#each fieldKeys as [key, label] (key)}
        <label>
          <span>{label}</span>
          <input type="number" min="0" step="10" bind:value={field[key]} /> mm
        </label>
      {/each}
      <fieldset>
        <legend>Markings present on the carpet</legend>
        {#each lineKeys as [key, label] (key)}
          <label class="check">
            <input type="checkbox" bind:checked={lines[key]} />
            {label}
          </label>
        {/each}
      </fieldset>
    </div>
    {#if problem}
      <p class="notice error">{problem}</p>
    {/if}
  {:else}
    <p class="path">{loaded?.path ?? "--"}</p>
  {/if}

  {#if sizeChanged}
    <p class="notice warn">
      The field size changed. The camera calibration still uses the old size.
      <button class="primary" disabled={saving} onclick={recalibrate}
        >Recalibrate now</button
      >
    </p>
  {/if}

  <div class="refine">
    <label class="check">
      <input
        type="checkbox"
        checked={refinementDraft ?? refinement}
        onchange={(event) => (refinementDraft = event.currentTarget.checked)}
      />
      Refine with field lines (needs visible lines/tape)
    </label>
    <p>
      Off (default): the calibration uses only the 4 clicked corners. On:
      vision_processor also fits the camera to white lines it sees — only useful
      when real field lines are visible, otherwise clutter makes it worse.
    </p>
    {#if refinementDraft !== null && refinementDraft !== refinement}
      <span class="buttons">
        <button
          class="primary"
          disabled={saving}
          onclick={() => saveRefinement(true)}
          >Save & restart vision_processor</button
        >
        <button
          class="ghost"
          disabled={saving}
          onclick={() => saveRefinement(false)}>Save only</button
        >
        <button class="ghost" onclick={() => (refinementDraft = null)}
          >Cancel</button
        >
      </span>
    {/if}
  </div>

  {#if message}
    <p class={`notice ${message.kind}`}>{message.text}</p>
  {/if}
</section>

<style>
  .geometry {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
  }

  .section-heading {
    min-height: 42px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
  }

  .preview {
    display: block;
    width: calc(100% - 24px);
    max-height: 200px;
    margin: 10px 12px 2px;
    background: #2f6d4a;
    border-radius: 3px;
  }

  .preview .boundary {
    stroke: rgba(255, 255, 255, 0.35);
    stroke-dasharray: 40 30;
  }

  .preview .line {
    stroke: #ffffff;
  }

  .preview .off {
    stroke: rgba(255, 255, 255, 0.45);
    stroke-dasharray: 30 30;
  }

  .preview .goal {
    stroke: #40cfff;
  }

  .caption,
  .path {
    margin: 2px 12px 8px;
    color: var(--text-muted);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .form {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px 12px;
    padding: 6px 12px;
    font-size: 12px;
  }

  .form label {
    display: grid;
    grid-template-columns: 1fr auto auto;
    align-items: center;
    gap: 4px;
    color: var(--text-muted);
  }

  .form label span {
    grid-column: 1 / -1;
    font-size: 10px;
    text-transform: uppercase;
  }

  input[type="number"] {
    width: 100%;
    min-width: 0;
    padding: 3px 5px;
    color: var(--text);
    background: var(--surface-2);
    border: 1px solid var(--border);
    border-radius: 3px;
    font: inherit;
  }

  fieldset {
    grid-column: 1 / -1;
    display: flex;
    flex-wrap: wrap;
    gap: 4px 14px;
    margin: 4px 0 0;
    padding: 6px 8px;
    border: 1px solid var(--border);
    border-radius: 3px;
  }

  legend {
    padding: 0 4px;
    color: var(--text-muted);
    font-size: 11px;
  }

  .form label.check,
  .check {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: var(--text);
    font-size: 12px;
  }

  .refine {
    padding: 8px 12px;
    border-top: 1px solid var(--border-soft);
  }

  .refine p {
    margin: 4px 0 6px;
    color: var(--text-muted);
    font-size: 11px;
  }

  .buttons {
    display: inline-flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  button {
    height: 26px;
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

  .notice {
    margin: 6px 12px 10px;
    padding: 7px 9px;
    border-radius: 4px;
    font-size: 12px;
  }

  .notice.error {
    color: var(--bad);
    background: var(--bad-bg);
    border: 1px solid var(--bad-border);
  }

  .notice.warn {
    color: var(--warn);
    background: var(--warn-bg);
    border: 1px solid var(--warn-border);
  }

  .notice.ok {
    color: var(--ok);
    background: var(--ok-bg);
    border: 1px solid var(--ok-border);
  }
</style>

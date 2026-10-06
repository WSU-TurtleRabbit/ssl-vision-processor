<script lang="ts">
  import ColorPanel from "./ColorPanel.svelte";
  import type { CalibrationStatus, DataMap } from "./health";
  import { asRecord, num } from "./health";

  // Calibrate tab: field geometry summary (Edit → modal), orientation of
  // the saved corners, colours (auto | manual).

  interface GeometryFile {
    path: string;
    field: Record<string, number>;
    optional_field_lines: Record<string, boolean>;
  }

  let {
    calibration,
    config,
    camId,
    cameraLabel,
    refreshToken,
    onedit,
    oncorners,
    onchange,
    ontoast,
  }: {
    calibration: CalibrationStatus | null;
    config: DataMap;
    camId: number;
    cameraLabel: string;
    refreshToken: number;
    onedit: () => void;
    oncorners: () => void;
    onchange: () => void;
    ontoast: (toast: { kind: "ok" | "error"; text: string }) => void;
  } = $props();

  const QUADRANT = ["−x −y", "−x +y", "+x +y", "+x −y"];
  let geometryFile = $state<GeometryFile | null>(null);
  let busy = $state(false);

  $effect(() => {
    void refreshToken;
    void (async () => {
      try {
        const response = await fetch("/api/geometry");
        if (response.ok) geometryFile = (await response.json()) as GeometryFile;
      } catch {
        // shown as missing
      }
    })();
  });

  let colorConfig = $derived(asRecord(config["color"]));
  let f = $derived(geometryFile?.field ?? {});
  let markings = $derived(
    Object.entries(geometryFile?.optional_field_lines ?? {})
      .filter(([, on]) => on)
      .map(([name]) => name),
  );
  function pointList(value: unknown): [number, number][] | null {
    if (!Array.isArray(value) || value.length !== 4) return null;
    const out: [number, number][] = [];
    for (const item of value) {
      if (!Array.isArray(item) || item.length < 2) return null;
      out.push([Number(item[0]), Number(item[1])]);
    }
    return out;
  }
  let outer = $derived(pointList(calibration?.outer_corners));
  let corners = $derived(pointList(calibration?.corners));
  let editable = $derived(
    outer ??
      (corners && calibration?.include_boundary !== true ? corners : null),
  );

  // Re-save the stored corners with a different origin: the backend
  // re-orders clockwise from the new first point and recalibrates.
  async function rotate(shift: number): Promise<void> {
    if (!editable) return;
    busy = true;
    try {
      const rotated = [...editable.slice(shift), ...editable.slice(0, shift)];
      const response = await fetch("/api/calibration/corners", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          cam_id: camId,
          corners: rotated,
          mode: outer ? "outer" : "field",
        }),
      });
      const data = (await response.json().catch(() => ({}))) as {
        error?: string;
        message?: string;
      };
      if (!response.ok)
        throw new Error(data.error ?? `HTTP ${String(response.status)}`);
      ontoast({ kind: "ok", text: data.message ?? "Saved." });
      onchange();
    } catch (error) {
      ontoast({ kind: "error", text: String(error) });
    } finally {
      busy = false;
    }
  }
</script>

<section>
  <h3>Field geometry <button class="link" onclick={onedit}>Edit…</button></h3>
  {#if geometryFile}
    <p class="text">
      {num(f["field_length"])} × {num(f["field_width"])} mm field · boundary {num(
        f["boundary_width"],
      )} / {num(f["boundary_width_goal_line"])} mm (sides / goal lines) · goal {num(
        f["goal_width"],
      )} × {num(f["goal_depth"])} mm · penalty {num(f["penalty_area_depth"])} × {num(
        f["penalty_area_width"],
      )} mm · centre circle r {num(f["center_circle_radius"])} mm · lines {num(
        f["line_thickness"],
      )} mm
    </p>
    <p class="text muted">
      markings on the carpet: {markings.length ? markings.join(", ") : "none"} · file
      {geometryFile.path.split("/").pop()}
    </p>
  {:else}
    <p class="text muted">geometry file not loaded</p>
  {/if}
</section>

<section>
  <h3>
    Orientation <button class="link" onclick={oncorners}>Set corners…</button>
  </h3>
  {#if corners}
    <p class="text">
      {#each corners as c, i (i)}
        <span class="corner"
          >{i + 1}·{QUADRANT[i]} ({num(c[0])}, {num(c[1])})</span
        >
      {/each}
    </p>
    <p class="text muted">
      Corner 1 is the origin (−x −y); x runs along the long side toward the +x
      goal.
    </p>
    {#if editable}
      <p class="buttons">
        <button
          disabled={busy}
          onclick={() => rotate(2)}
          title="Swap the goal ends">Rotate 180°</button
        >
        <button disabled={busy} onclick={() => rotate(1)}
          >corner 2 → origin</button
        >
        <button disabled={busy} onclick={() => rotate(3)}
          >corner 4 → origin</button
        >
      </p>
    {/if}
  {:else}
    <p class="text muted">no corners saved — use Set corners</p>
  {/if}
</section>

<section>
  <ColorPanel
    camId={String(camId)}
    configColors={colorConfig}
    {refreshToken}
    {cameraLabel}
  />
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

  .text {
    margin: 2px 0;
    font-size: 12px;
    overflow-wrap: anywhere;
  }

  .muted {
    color: var(--text-muted);
  }

  .corner {
    margin-right: 10px;
    white-space: nowrap;
  }

  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 4px 0 0;
  }

  .buttons button {
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

  .buttons button:disabled {
    opacity: 0.45;
  }
</style>

<script lang="ts">
  import { onMount } from "svelte";

  // Receipt: a printable black-on-white summary of GET /api/receipt.

  type Json = Record<string, unknown>;

  let receipt = $state<Json | null>(null);
  let error = $state<string | null>(null);

  function obj(value: unknown): Json {
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Json)
      : {};
  }

  function fmt(value: unknown): string {
    if (value === null || value === undefined || value === "") return "—";
    if (typeof value === "number")
      return Number.isInteger(value) ? String(value) : value.toFixed(3);
    if (typeof value === "string" || typeof value === "boolean")
      return String(value);
    if (Array.isArray(value))
      return value
        .map((v) =>
          Array.isArray(v) ? `(${v.map((n) => fmt(n)).join(", ")})` : fmt(v),
        )
        .join(", ");
    return JSON.stringify(value);
  }

  // [label, value] rows, skipping unset values.
  function rows(value: unknown, keys?: string[]): [string, string][] {
    const source = obj(value);
    return (keys ?? Object.keys(source))
      .filter((key) => source[key] !== null && source[key] !== undefined)
      .map((key) => [key.replaceAll("_", " "), fmt(source[key])]);
  }

  onMount(() => {
    void (async () => {
      try {
        const response = await fetch("/api/receipt");
        if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
        receipt = (await response.json()) as Json;
      } catch (caught) {
        error = String(caught);
      }
    })();
  });
</script>

<svelte:head>
  <title>Vision receipt</title>
</svelte:head>

<div class="receipt">
  <div class="toolbar">
    <button
      onclick={() => {
        window.print();
      }}>Print</button
    >
    <a href="/api/receipt?download=1" download>Download JSON</a>
    <a href="#/" class="back">← back</a>
  </div>
  {#if error}
    <p>{error}</p>
  {:else if !receipt}
    <p>Loading…</p>
  {:else}
    {@const cameras = Array.isArray(receipt["cameras"])
      ? receipt["cameras"]
      : []}
    {@const services = obj(receipt["services"])}
    {@const geometry = obj(receipt["geometry"])}
    {@const colours = obj(receipt["colours"])}
    {@const logs = obj(receipt["log_tail"])}
    <h1>SSL vision receipt</h1>
    <p class="meta">
      {fmt(receipt["generated_at"])} · commit {fmt(receipt["git_commit"]).slice(
        0,
        12,
      )} · build {fmt(receipt["vision_build"])}
    </p>

    <h2>Field geometry — {fmt(geometry["file"])}</h2>
    <table>
      <tbody>
        {#each rows(geometry["field"]) as [k, v] (k)}<tr
            ><th>{k}</th><td>{v}</td></tr
          >{/each}
        <tr
          ><th>markings</th><td
            >{fmt(
              Object.entries(obj(geometry["optional_field_lines"]))
                .filter(([, on]) => on)
                .map(([name]) => name),
            )}</td
          ></tr
        >
      </tbody>
    </table>

    {#each cameras as camera, index (index)}
      {@const cam = obj(camera)}
      <h2>Camera {fmt(cam["cam_id"])} — {fmt(cam["name"])}</h2>
      <table>
        <tbody>
          <tr><th>path</th><td>{fmt(cam["path"])}</td></tr>
          {#each rows( cam["pi_status"], ["running", "streaming", "client", "closed", "control", "size", "fps"], ) as [k, v] (k)}<tr
              ><th>Pi {k}</th><td>{v}</td></tr
            >{/each}
          <tr><th>calibration</th><td>{fmt(cam["calibration_state"])}</td></tr>
          <tr
            ><th>corners (−x−y, −x+y, +x+y, +x−y)</th><td
              >{fmt(cam["corners"])}</td
            ></tr
          >
          {#if cam["outer_corners"]}<tr
              ><th>outer corners</th><td>{fmt(cam["outer_corners"])}</td></tr
            >{/if}
          {#each rows(cam["lens"]) as [k, v] (k)}<tr
              ><th>lens {k}</th><td>{v}</td></tr
            >{/each}
          {#each rows(cam["solved_model"]) as [k, v] (k)}<tr
              ><th>model {k}</th><td>{v}</td></tr
            >{/each}
        </tbody>
      </table>
    {/each}

    <h2>Colours (dRGB)</h2>
    <table>
      <thead><tr><th>colour</th><th>learned</th><th>reference</th></tr></thead>
      <tbody>
        {#each Object.keys(obj(colours["reference"])) as name (name)}
          <tr
            ><th>{name}</th><td>{fmt(obj(colours["learned"])[name])}</td><td
              >{fmt(obj(colours["reference"])[name])}</td
            ></tr
          >
        {/each}
        {#each rows(colours["forces"]) as [k, v] (k)}<tr
            ><th>{k}</th><td colspan="2">{v}</td></tr
          >{/each}
      </tbody>
    </table>

    <h2>Services</h2>
    <table>
      <tbody>
        {#each rows(services["vision_processor"]) as [k, v] (k)}<tr
            ><th>vision {k}</th><td>{v}</td></tr
          >{/each}
        {#each rows(services["wrapper_backend"]) as [k, v] (k)}<tr
            ><th>backend {k}</th><td>{v}</td></tr
          >{/each}
        {#each rows( services["game_controller"], ["running", "process_running", "group", "last_packet_age_s", "stage", "command", "yellow", "blue"], ) as [k, v] (k)}<tr
            ><th>game controller {k}</th><td>{v}</td></tr
          >{/each}
      </tbody>
    </table>

    <h2>Performance (last 5 s)</h2>
    <table>
      <tbody>
        {#each Object.entries(obj(receipt["performance"])) as [cam, m] (cam)}
          {#each rows(m) as [k, v] (k)}<tr
              ><th>cam {cam} {k}</th><td>{v}</td></tr
            >{/each}
        {/each}
      </tbody>
    </table>

    <h2>Last errors</h2>
    <table>
      <tbody>
        {#each rows(receipt["errors"]) as [k, v] (k)}<tr
            ><th>{k}</th><td>{v}</td></tr
          >{/each}
      </tbody>
    </table>

    {#each Object.entries(logs) as [name, lines] (name)}
      <h2>Log tail — {name}</h2>
      <pre>{Array.isArray(lines) ? lines.join("\n") : ""}</pre>
    {/each}
  {/if}
</div>

<style>
  .receipt {
    max-width: 900px;
    margin: 0 auto;
    padding: 16px 24px 48px;
    color: #000;
    background: #fff;
    font:
      12px/1.45 system-ui,
      sans-serif;
  }

  .toolbar {
    display: flex;
    gap: 12px;
    margin-bottom: 12px;
  }

  .toolbar button,
  .toolbar a {
    padding: 4px 10px;
    color: #000;
    background: #f2f2f2;
    border: 1px solid #999;
    border-radius: 3px;
    font: inherit;
    text-decoration: none;
    cursor: pointer;
  }

  h1 {
    margin: 0 0 4px;
    font-size: 20px;
  }

  h2 {
    margin: 18px 0 6px;
    font-size: 14px;
    border-bottom: 1px solid #000;
  }

  .meta {
    margin: 0 0 8px;
    color: #333;
  }

  table {
    width: 100%;
    border-collapse: collapse;
  }

  th,
  td {
    padding: 2px 8px;
    text-align: left;
    vertical-align: top;
    border-bottom: 1px solid #ddd;
  }

  th {
    width: 32%;
    font-weight: 600;
    white-space: nowrap;
  }

  pre {
    padding: 6px 8px;
    overflow-x: auto;
    background: #f6f6f6;
    border: 1px solid #ddd;
    font-size: 10px;
    white-space: pre-wrap;
  }

  @media print {
    .toolbar {
      display: none;
    }

    .receipt {
      max-width: none;
      padding: 0;
    }

    h2 {
      page-break-after: avoid;
    }
  }
</style>

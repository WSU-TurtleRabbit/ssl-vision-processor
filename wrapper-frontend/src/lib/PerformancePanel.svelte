<script lang="ts">
  import { untrack } from "svelte";
  import type { CameraMetrics } from "./health";

  // Performance: per-camera numbers (from App's 2 s /api/metrics poll) with
  // a sparkline of the detection rate, plus this page's own update rate.

  let {
    metrics,
    names = {},
    uiFps,
    wsRate,
  }: {
    metrics: Record<string, CameraMetrics>;
    names?: Record<string, string>;
    uiFps: number;
    wsRate: number;
  } = $props();

  const HISTORY = 40;
  let history = $state<Record<string, number[]>>({});

  $effect(() => {
    const current = metrics;
    untrack(() => {
      const next: Record<string, number[]> = {};
      for (const [cam, m] of Object.entries(current))
        next[cam] = [...(history[cam] ?? []), m.rate_hz].slice(-HISTORY);
      history = next;
    });
  });

  function sparkline(values: number[]): string {
    if (values.length < 2) return "";
    const max = Math.max(1, ...values);
    return values
      .map(
        (v, i) =>
          `${String((i / (HISTORY - 1)) * 100)},${String(20 - (v / max) * 18)}`,
      )
      .join(" ");
  }

  function fmt(value: number | null | undefined, digits = 1): string {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toFixed(digits)
      : "--";
  }
</script>

<table class="perf">
  <thead>
    <tr>
      <th>Camera</th>
      <th title="Detection packets per second received by the backend">pkt/s</th
      >
      <th
        title="vision_processor processing time per frame (t_sent − t_capture)"
        >ms/frame</th
      >
      <th title="Backend receive time − t_sent; needs synchronised clocks"
        >net ms</th
      >
      <th>robots</th>
      <th>balls</th>
      <th></th>
    </tr>
  </thead>
  <tbody>
    {#each Object.entries(metrics) as [cam, m] (cam)}
      <tr class:stale={(m.last_age_s ?? 0) > 1 || m.frames === 0}>
        <td title={`camera_id ${cam}`}>{names[cam] ?? `Camera ${cam}`}</td>
        <td>{fmt(m.rate_hz)}</td>
        <td>{fmt(m.processing_ms)}</td>
        <td>{fmt(m.receive_ms)}</td>
        <td>{fmt(m.robots_per_frame, 2)}</td>
        <td>{fmt(m.balls_per_frame, 2)}</td>
        <td class="spark"
          ><svg viewBox="0 0 100 20" preserveAspectRatio="none"
            ><polyline points={sparkline(history[cam] ?? [])} /></svg
          ></td
        >
      </tr>
    {:else}
      <tr><td colspan="7" class="empty">no detection frames yet</td></tr>
    {/each}
  </tbody>
</table>
<p
  class="page"
  title="Detection frames applied to this page per second · WebSocket messages per second"
>
  page {fmt(uiFps)} updates/s · {fmt(wsRate)} ws msg/s
</p>

<style>
  .perf {
    width: 100%;
    border-collapse: collapse;
    table-layout: fixed;
    font-size: 11.5px;
  }

  th,
  td {
    height: 24px;
    padding: 0 4px;
    overflow: hidden;
    text-overflow: ellipsis;
    text-align: right;
    border-bottom: 1px solid var(--border-soft);
    white-space: nowrap;
  }

  th {
    color: var(--text-muted);
    font-weight: 500;
  }

  th:first-child,
  td:first-child {
    text-align: left;
  }

  tr.stale td {
    color: var(--text-faint);
  }

  .empty {
    text-align: center;
    color: var(--text-muted);
  }

  .spark {
    width: 56px;
  }

  svg {
    width: 52px;
    height: 18px;
    display: block;
  }

  polyline {
    fill: none;
    stroke: var(--accent);
    stroke-width: 1.5;
    vector-effect: non-scaling-stroke;
  }

  .page {
    margin: 4px 6px 0;
    color: var(--text-muted);
    font-size: 11px;
  }
</style>

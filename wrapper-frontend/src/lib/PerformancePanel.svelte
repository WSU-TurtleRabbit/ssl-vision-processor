<script lang="ts">
  import { onMount } from "svelte";
  import { messageCount } from "./wrapper-bus";

  // Performance: per-camera numbers from GET /api/metrics (backend, rolling
  // 5 s window) plus what reaches this page: detection frames applied per
  // second (uiFrames counter from App) and WebSocket messages per second.

  interface CameraMetrics {
    frames: number;
    rate_hz: number;
    processing_ms?: number | null;
    receive_ms?: number | null;
    robots_per_frame?: number;
    balls_per_frame?: number;
    last_age_s?: number;
  }

  let {
    uiFrames,
    names = {},
  }: { uiFrames: number; names?: Record<string, string> } = $props();

  const HISTORY = 60;
  let cameras = $state<Record<string, CameraMetrics>>({});
  let history = $state<Record<string, number[]>>({});
  let uiFps = $state(0);
  let wsRate = $state(0);
  let uiHistory = $state<number[]>([]);
  let lastUiFrames = 0;
  let lastMessages = 0;
  let lastTick = performance.now();

  function push(list: number[] | undefined, value: number): number[] {
    return [...(list ?? []), value].slice(-HISTORY);
  }

  function sparkline(values: number[]): string {
    if (values.length < 2) return "";
    const max = Math.max(1, ...values);
    return values
      .map(
        (value, index) =>
          `${String((index / (HISTORY - 1)) * 100)},${String(20 - (value / max) * 18)}`,
      )
      .join(" ");
  }

  function fmt(value: number | null | undefined, digits = 1): string {
    return typeof value === "number" && Number.isFinite(value)
      ? value.toFixed(digits)
      : "--";
  }

  async function tick(): Promise<void> {
    const now = performance.now();
    const seconds = Math.max(0.001, (now - lastTick) / 1000);
    lastTick = now;
    const messages = messageCount();
    wsRate = (messages - lastMessages) / seconds;
    lastMessages = messages;
    uiFps = (uiFrames - lastUiFrames) / seconds;
    lastUiFrames = uiFrames;
    uiHistory = push(uiHistory, uiFps);
    try {
      const response = await fetch("/api/metrics");
      if (!response.ok) return;
      const result = (await response.json()) as {
        cameras?: Record<string, CameraMetrics>;
      };
      cameras = result.cameras ?? {};
      const next: Record<string, number[]> = {};
      for (const [cam, metrics] of Object.entries(cameras))
        next[cam] = push(history[cam], metrics.rate_hz);
      history = next;
    } catch {
      // Shown as stale numbers; the services panel reports reachability.
    }
  }

  onMount(() => {
    lastUiFrames = uiFrames;
    lastMessages = messageCount();
    const timer = setInterval(() => void tick(), 1000);
    return () => {
      clearInterval(timer);
    };
  });
</script>

<section class="performance">
  <div class="section-heading"><h2>Performance</h2></div>
  <table>
    <thead>
      <tr>
        <th>Camera</th>
        <th title="Detection packets per second received by the backend"
          >Rate</th
        >
        <th title="vision_processor: t_sent − t_capture">Process</th>
        <th
          title="Backend receive time − t_sent (needs synchronised clocks; -- when they differ by > 10 s)"
          >Network</th
        >
        <th>Robots</th>
        <th>Balls</th>
        <th></th>
      </tr>
    </thead>
    <tbody>
      {#each Object.entries(cameras) as [cam, m] (cam)}
        <tr class:stale={(m.last_age_s ?? 0) > 1 || m.frames === 0}>
          <td title={`camera_id ${cam}`}>{names[cam] ?? `Camera ${cam}`}</td>
          <td>{fmt(m.rate_hz)} /s</td>
          <td>{fmt(m.processing_ms)} ms</td>
          <td>{fmt(m.receive_ms)} ms</td>
          <td>{fmt(m.robots_per_frame, 2)}</td>
          <td>{fmt(m.balls_per_frame, 2)}</td>
          <td class="spark">
            <svg viewBox="0 0 100 20" preserveAspectRatio="none">
              <polyline points={sparkline(history[cam] ?? [])} />
            </svg>
          </td>
        </tr>
      {:else}
        <tr><td colspan="7" class="empty">No detection frames yet</td></tr>
      {/each}
    </tbody>
  </table>
  <div class="browser">
    <span>This page: <strong>{fmt(uiFps)}</strong> detection updates/s</span>
    <span><strong>{fmt(wsRate)}</strong> WebSocket msgs/s</span>
    <svg viewBox="0 0 100 20" preserveAspectRatio="none" class="spark-inline">
      <polyline points={sparkline(uiHistory)} />
    </svg>
  </div>
</section>

<style>
  .performance {
    min-width: 0;
    border: 1px solid var(--border);
    border-radius: 5px;
    background: var(--surface);
  }

  .section-heading {
    min-height: 42px;
    display: flex;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    margin: 0;
    font-size: 13px;
    font-weight: 700;
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 11px;
  }

  th,
  td {
    padding: 5px 6px;
    text-align: right;
    border-bottom: 1px solid var(--border-soft);
    white-space: nowrap;
  }

  th {
    color: var(--text-muted);
    background: var(--surface-2);
    font-weight: 600;
  }

  th:first-child,
  td:first-child {
    text-align: left;
  }

  tr.stale td {
    color: var(--text-faint);
  }

  td.empty {
    text-align: center;
    color: var(--text-muted);
  }

  .spark {
    width: 70px;
  }

  svg {
    width: 70px;
    height: 18px;
    display: block;
  }

  polyline {
    fill: none;
    stroke: #39a56c;
    stroke-width: 1.5;
    vector-effect: non-scaling-stroke;
  }

  .browser {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 6px 14px;
    padding: 7px 12px;
    color: var(--text-muted);
    font-size: 11px;
  }

  .browser strong {
    color: var(--text);
  }
</style>

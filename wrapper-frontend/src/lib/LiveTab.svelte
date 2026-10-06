<script lang="ts">
  import PerformancePanel from "./PerformancePanel.svelte";
  import type { BallDetection, CameraMetrics, RobotDetection } from "./health";
  import { num } from "./health";

  // Live tab: detections (Team/ID · X · Y · Angle° · Conf, balls beneath)
  // and the performance numbers.

  let {
    blue,
    yellow,
    balls,
    live,
    stateText,
    metrics,
    names,
    uiFps,
    wsRate,
  }: {
    blue: RobotDetection[];
    yellow: RobotDetection[];
    balls: BallDetection[];
    live: boolean;
    stateText: string;
    metrics: Record<string, CameraMetrics>;
    names: Record<string, string>;
    uiFps: number;
    wsRate: number;
  } = $props();

  interface Row {
    team: "blue" | "yellow";
    robot: RobotDetection;
    key: string;
  }

  // Sorted by team then id; keyed by team+id so unchanged rows are reused.
  let rows = $derived.by((): Row[] => {
    const byId = (a: RobotDetection, b: RobotDetection): number =>
      (a.robot_id ?? 99) - (b.robot_id ?? 99);
    const mk = (team: Row["team"], list: RobotDetection[]): Row[] =>
      [...list].sort(byId).map((robot, index) => ({
        team,
        robot,
        key: `${team}-${String(robot.robot_id ?? `n${String(index)}`)}`,
      }));
    return [...mk("blue", blue), ...mk("yellow", yellow)];
  });

  function deg(radians: unknown): string {
    const r = typeof radians === "number" ? radians : Number(radians);
    return Number.isFinite(r) ? ((r * 180) / Math.PI).toFixed(1) : "--";
  }

  function px(item: { pixel_x?: number; pixel_y?: number }): string {
    return `image px ${num(item.pixel_x, 1)}, ${num(item.pixel_y, 1)}`;
  }
</script>

<section>
  <h3>Detections <span class="state" class:live>{stateText}</span></h3>
  <table>
    <thead>
      <tr>
        <th>Team / ID</th>
        <th>X</th>
        <th>Y</th>
        <th>Angle°</th>
        <th>Conf</th>
      </tr>
    </thead>
    <tbody>
      {#each rows as row (row.key)}
        <tr title={px(row.robot)}>
          <td
            ><span class={`dot ${row.team}`}></span>{row.robot.robot_id ??
              "?"}</td
          >
          <td>{num(row.robot.x)}</td>
          <td>{num(row.robot.y)}</td>
          <td>{deg(row.robot.orientation)}</td>
          <td>{num(row.robot.confidence, 2)}</td>
        </tr>
      {/each}
      {#each balls as ball, index (index)}
        <tr title={px(ball)}>
          <td><span class="dot ball"></span>ball</td>
          <td>{num(ball.x)}</td>
          <td>{num(ball.y)}</td>
          <td>—</td>
          <td>{num(ball.confidence, 2)}</td>
        </tr>
      {/each}
      {#if rows.length + balls.length === 0}
        <tr
          ><td colspan="5" class="empty">{live ? "no objects" : stateText}</td
          ></tr
        >
      {/if}
    </tbody>
  </table>
</section>

<section>
  <h3>Performance</h3>
  <PerformancePanel {metrics} {names} {uiFps} {wsRate} />
</section>

<style>
  section {
    margin-bottom: 10px;
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

  .state {
    text-transform: none;
    letter-spacing: 0;
    font-weight: 500;
    color: var(--bad);
  }

  .state.live {
    color: var(--ok);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  th,
  td {
    height: 24px;
    padding: 0 6px;
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

  .dot {
    display: inline-block;
    width: 8px;
    height: 8px;
    margin-right: 6px;
    border-radius: 50%;
    vertical-align: -1px;
  }

  .dot.blue {
    background: #2a80c8;
  }

  .dot.yellow {
    background: #e5be22;
  }

  .dot.ball {
    background: #e3732f;
  }

  .empty {
    text-align: center;
    color: var(--text-muted);
  }
</style>

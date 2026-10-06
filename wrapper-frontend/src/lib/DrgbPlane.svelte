<script lang="ts">
  // The dRGB chroma plane: dRGB = ((2R−G−B+510)/4, …) always sums to ~382,
  // so every colour lies on one plane — grey (127,127,127) in the centre,
  // saturated red/green/blue toward the corners. Click/drag picks a dRGB;
  // the reference is drawn as a ring, the learned colour as a dot.

  type Rgb = [number, number, number];

  let {
    value,
    learned = null,
    onpick,
    size = 160,
  }: {
    value: Rgb | null;
    learned?: Rgb | null;
    onpick: (drgb: Rgb) => void;
    size?: number;
  } = $props();

  const SUM = 382;
  const CENTRE = SUM / 3;
  // Orthonormal basis of the plane (components sum to 0).
  const E1 = [2 / Math.sqrt(6), -1 / Math.sqrt(6), -1 / Math.sqrt(6)];
  const E2 = [0, 1 / Math.sqrt(2), -1 / Math.sqrt(2)];
  // Pure red (255,64,64) is ~156 from the centre; map that to the radius.
  const RADIUS = 160;

  let canvas = $state<HTMLCanvasElement>();
  let dragging = false;

  function toDrgb(u: number, v: number): Rgb | null {
    const d = [0, 1, 2].map(
      (i) => CENTRE + u * (E1[i] ?? 0) + v * (E2[i] ?? 0),
    );
    if (d.some((c) => c < -0.5 || c > 255.5)) return null;
    return d.map((c) => Math.min(255, Math.max(0, c))) as Rgb;
  }

  function toPlane(d: Rgb): [number, number] {
    const u = d.reduce((s, c, i) => s + (c - CENTRE) * (E1[i] ?? 0), 0);
    const v = d.reduce((s, c, i) => s + (c - CENTRE) * (E2[i] ?? 0), 0);
    return [
      size / 2 + (u / RADIUS) * (size / 2),
      size / 2 + (v / RADIUS) * (size / 2),
    ];
  }

  /** Integer dRGB whose channels sum to ~382 (shift onto the plane). */
  export function project(d: Rgb): Rgb {
    const shift = (SUM - (d[0] + d[1] + d[2])) / 3;
    return d.map((c) =>
      Math.min(255, Math.max(0, Math.round(c + shift))),
    ) as Rgb;
  }

  function preview(d: Rgb): string {
    const [r, g, b] = d.map((c) =>
      Math.min(255, Math.max(0, Math.round(128 + 2 * (c - 127.5)))),
    );
    return `rgb(${String(r)} ${String(g)} ${String(b)})`;
  }

  $effect(() => {
    const target = canvas;
    if (!target) return;
    const ctx = target.getContext("2d");
    if (!ctx) return;
    const image = ctx.createImageData(size, size);
    for (let py = 0; py < size; py += 1) {
      for (let px = 0; px < size; px += 1) {
        const u = ((px - size / 2) / (size / 2)) * RADIUS;
        const v = ((py - size / 2) / (size / 2)) * RADIUS;
        const d = toDrgb(u, v);
        const o = (py * size + px) * 4;
        if (!d) {
          image.data[o] = 24;
          image.data[o + 1] = 28;
          image.data[o + 2] = 26;
        } else {
          for (let i = 0; i < 3; i += 1)
            image.data[o + i] = Math.min(
              255,
              Math.max(0, Math.round(128 + 2 * ((d[i] ?? 0) - 127.5))),
            );
        }
        image.data[o + 3] = 255;
      }
    }
    ctx.putImageData(image, 0, 0);
    const mark = (d: Rgb, ring: boolean): void => {
      const [x, y] = toPlane(d);
      ctx.beginPath();
      ctx.arc(x, y, ring ? 7 : 3.5, 0, Math.PI * 2);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#111";
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, ring ? 6 : 2.5, 0, Math.PI * 2);
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = ring ? 2 : 1;
      if (ring) ctx.stroke();
      else {
        ctx.fillStyle = "#fff";
        ctx.fill();
      }
    };
    if (learned) mark(learned, false);
    if (value) mark(value, true);
  });

  function pick(event: PointerEvent): void {
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const px = ((event.clientX - rect.left) / rect.width) * size;
    const py = ((event.clientY - rect.top) / rect.height) * size;
    const u = ((px - size / 2) / (size / 2)) * RADIUS;
    const v = ((py - size / 2) / (size / 2)) * RADIUS;
    const d = toDrgb(u, v);
    if (d) onpick(project(d));
  }
</script>

<canvas
  bind:this={canvas}
  width={size}
  height={size}
  title="Brightness-free colour plane: centre = grey; drag toward a corner for a stronger colour."
  aria-label="dRGB colour plane"
  style={`width:${String(size)}px;height:${String(size)}px;${value ? `box-shadow: 0 0 0 2px ${preview(value)}` : ""}`}
  onpointerdown={(event) => {
    dragging = true;
    canvas?.setPointerCapture(event.pointerId);
    pick(event);
  }}
  onpointermove={(event) => {
    if (dragging) pick(event);
  }}
  onpointerup={() => (dragging = false)}
  onpointercancel={() => (dragging = false)}
></canvas>

<style>
  canvas {
    display: block;
    border-radius: 4px;
    cursor: crosshair;
    touch-action: none;
  }
</style>

<script lang="ts">
  import { onMount } from "svelte";

  // Log console: a right-edge drawer with a merged live tail of
  // logs/vision_processor.log, logs/pi-camera-<host>.log and
  // logs/wrapper_backend.log (GET /api/logs/<name>?tail=N every 2 s).

  interface Source {
    key: "vp" | "pi" | "be";
    label: string;
    file: string | null;
  }

  interface Line {
    source: Source["key"];
    time: number; // ms, 0 when the line carried no timestamp
    text: string;
    level: "warn" | "error" | "info";
  }

  let {
    open,
    piLogFile,
    ontoggle,
    onopenfile,
  }: {
    open: boolean;
    piLogFile: string | null;
    ontoggle: () => void;
    onopenfile: (name: string) => void;
  } = $props();

  let sources = $derived<Source[]>([
    { key: "vp", label: "vision_processor", file: "vision_processor.log" },
    { key: "pi", label: "Pi camera", file: piLogFile },
    { key: "be", label: "backend", file: "wrapper_backend.log" },
  ]);
  let enabled = $state<Record<Source["key"], boolean>>({
    vp: true,
    pi: true,
    be: false,
  });
  let lines = $state<Line[]>([]);
  let paused = $state(false);
  let clearedAt = $state(0);
  let copied = $state<string | null>(null);
  let pre = $state<HTMLPreElement>();

  const TS_RE =
    /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})(?:[.,]\d+)?([+-]\d{2}:?\d{2}|Z)?/;

  function parseTime(text: string): number {
    const match = TS_RE.exec(text);
    if (!match) return 0;
    const iso = `${match[1] ?? ""}T${match[2] ?? ""}${match[3] ?? ""}`;
    const time = Date.parse(iso);
    return Number.isFinite(time) ? time : 0;
  }

  function level(text: string): Line["level"] {
    if (/error|failed|fatal|exited unexpectedly/i.test(text)) return "error";
    if (/warn/i.test(text)) return "warn";
    return "info";
  }

  async function refresh(): Promise<void> {
    const merged: Line[] = [];
    await Promise.all(
      sources.map(async (source) => {
        if (!source.file || !enabled[source.key]) return;
        try {
          const response = await fetch(
            `/api/logs/${encodeURIComponent(source.file)}?tail=150`,
          );
          if (!response.ok) return;
          const data = (await response.json()) as { lines: string[] };
          let last = 0;
          for (const text of data.lines) {
            const time = parseTime(text) || last;
            last = time;
            merged.push({ source: source.key, time, text, level: level(text) });
          }
        } catch {
          // Shown as a missing source; the backend badge reports reachability.
        }
      }),
    );
    merged.sort((a, b) => a.time - b.time);
    const stick =
      !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
    lines = merged.filter((line) => line.time === 0 || line.time >= clearedAt);
    if (stick)
      requestAnimationFrame(() => {
        if (pre) pre.scrollTop = pre.scrollHeight;
      });
  }

  async function copy(): Promise<void> {
    const text = lines.map((l) => `[${l.source}] ${l.text}`).join("\n");
    try {
      await navigator.clipboard.writeText(text);
      copied = "Copied";
    } catch {
      copied = "Copy failed (needs https)";
    }
    setTimeout(() => {
      copied = null;
    }, 1500);
  }

  onMount(() => {
    const timer = setInterval(() => {
      if (open && !paused) void refresh();
    }, 2000);
    return () => {
      clearInterval(timer);
    };
  });

  $effect(() => {
    if (open) void refresh();
  });
</script>

<button
  class="handle"
  class:open
  onclick={ontoggle}
  title="Log console (` key)"
  aria-label="Toggle the log console">{open ? "›" : "‹ logs"}</button
>

{#if open}
  <aside class="drawer" aria-label="Log console">
    <div class="bar">
      {#each sources as source (source.key)}
        <label class:disabled={!source.file}>
          <input
            type="checkbox"
            bind:checked={enabled[source.key]}
            disabled={!source.file}
            onchange={() => void refresh()}
          />
          {source.label}
          {#if source.file}
            <button
              class="link"
              title={`Open ${source.file}`}
              onclick={() => {
                if (source.file) onopenfile(source.file);
              }}>open file</button
            >
          {/if}
        </label>
      {/each}
    </div>
    <div class="bar">
      <button class="ghost" onclick={() => (paused = !paused)}
        >{paused ? "▶ Resume" : "⏸ Pause"}</button
      >
      <button
        class="ghost"
        onclick={() => {
          clearedAt = Date.now();
          lines = [];
        }}>Clear</button
      >
      <button class="ghost" onclick={copy}>{copied ?? "Copy"}</button>
      <span class="hint">{lines.length} lines</span>
    </div>
    <pre bind:this={pre}>{#each lines as line, index (index)}<span
          class={line.level}
          ><span class="src">{line.source}</span> {line.text}
</span>{/each}</pre>
  </aside>
{/if}

<style>
  .handle {
    position: fixed;
    right: 0;
    top: 50%;
    z-index: 70;
    padding: 10px 4px;
    color: #dce5e0;
    background: #29352f;
    border: 1px solid #46544d;
    border-right: 0;
    border-radius: 4px 0 0 4px;
    cursor: pointer;
    font-size: 11px;
    writing-mode: vertical-rl;
    transform: translateY(-50%);
  }

  .handle.open {
    right: 380px;
  }

  .drawer {
    position: fixed;
    top: 0;
    right: 0;
    bottom: 0;
    z-index: 65;
    width: 380px;
    max-width: 100vw;
    display: flex;
    flex-direction: column;
    background: var(--surface);
    border-left: 1px solid var(--border);
    box-shadow: -6px 0 24px rgba(0, 0, 0, 0.25);
  }

  .bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 10px;
    padding: 6px 8px;
    border-bottom: 1px solid var(--border-soft);
    font-size: 11px;
  }

  label {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--text);
  }

  label.disabled {
    opacity: 0.5;
  }

  .link {
    padding: 0;
    color: var(--text-muted);
    background: none;
    border: 0;
    cursor: pointer;
    font-size: 10px;
    text-decoration: underline;
  }

  .ghost {
    height: 24px;
    padding: 0 8px;
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
  }

  .hint {
    color: var(--text-muted);
  }

  pre {
    flex: 1;
    margin: 0;
    padding: 6px 8px;
    overflow: auto;
    color: #dce5e0;
    background: #111713;
    font-size: 10.5px;
    line-height: 1.4;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  pre .src {
    color: #7fc49c;
  }

  pre .warn {
    color: #f2c66b;
  }

  pre .error {
    color: #ff9d97;
  }
</style>

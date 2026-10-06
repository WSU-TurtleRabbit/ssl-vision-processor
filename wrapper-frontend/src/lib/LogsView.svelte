<script lang="ts">
  import { onMount } from "svelte";

  // Logs view (#/logs[/<file>]): lists <repo>/logs via GET /api/logs and
  // tails one file via GET /api/logs/<name>?tail=N (auto-refresh every 2 s,
  // pause, copy, download = ?raw=1).

  interface LogFile {
    name: string;
    size: number;
    modified: number;
    path: string;
  }

  let {
    file,
    onopen,
  }: {
    file: string | null;
    onopen: (name: string | null) => void;
  } = $props();

  let dir = $state<string | null>(null);
  let files = $state<LogFile[]>([]);
  let lines = $state<string[]>([]);
  let total = $state(0);
  let tailCount = $state(200);
  let paused = $state(false);
  let error = $state<string | null>(null);
  let copied = $state<string | null>(null);
  let pre = $state<HTMLPreElement>();

  function size(bytes: number): string {
    if (bytes < 1024) return `${String(bytes)} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} kB`;
    return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  }

  async function loadList(): Promise<void> {
    try {
      const response = await fetch("/api/logs");
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      const data = (await response.json()) as { dir: string; files: LogFile[] };
      dir = data.dir;
      files = data.files;
      error = null;
    } catch (caught) {
      error = String(caught);
    }
  }

  async function loadTail(): Promise<void> {
    if (!file) return;
    try {
      const response = await fetch(
        `/api/logs/${encodeURIComponent(file)}?tail=${String(tailCount)}`,
      );
      if (!response.ok) throw new Error(`HTTP ${String(response.status)}`);
      const data = (await response.json()) as {
        lines: string[];
        total: number;
      };
      const stick =
        !pre || pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 8;
      lines = data.lines;
      total = data.total;
      error = null;
      if (stick)
        requestAnimationFrame(() => {
          if (pre) pre.scrollTop = pre.scrollHeight;
        });
    } catch (caught) {
      error = String(caught);
    }
  }

  async function copy(): Promise<void> {
    const text = lines.join("\n");
    try {
      await navigator.clipboard.writeText(text);
      copied = "Copied";
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      copied = document.execCommand("copy") ? "Copied" : "Copy failed";
      area.remove();
    }
    setTimeout(() => {
      copied = null;
    }, 1500);
  }

  $effect(() => {
    void file;
    lines = [];
    void loadTail();
  });

  onMount(() => {
    void loadList();
    const listTimer = setInterval(() => void loadList(), 5000);
    const tailTimer = setInterval(() => {
      if (!paused) void loadTail();
    }, 2000);
    return () => {
      clearInterval(listTimer);
      clearInterval(tailTimer);
    };
  });
</script>

<div class="logs">
  <aside>
    <h2>Logs</h2>
    <p class="hint">
      on the Jetson: <code>{dir ?? "…"}</code>
    </p>
    {#each files as entry (entry.name)}
      <button
        class="file"
        class:active={entry.name === file}
        onclick={() => {
          onopen(entry.name);
        }}
      >
        <span class="name">{entry.name}</span>
        <span class="meta"
          >{size(entry.size)} · {new Date(
            entry.modified * 1000,
          ).toLocaleTimeString()}</span
        >
      </button>
    {:else}
      <p class="hint">No log files yet.</p>
    {/each}
  </aside>
  <section>
    {#if file}
      <div class="bar">
        <strong>{file}</strong>
        <span class="hint">last {lines.length} of {total} lines</span>
        <select bind:value={tailCount} onchange={() => void loadTail()}>
          <option value={100}>100</option>
          <option value={200}>200</option>
          <option value={500}>500</option>
          <option value={2000}>2000</option>
        </select>
        <button class="ghost" onclick={() => (paused = !paused)}
          >{paused ? "▶ Resume" : "⏸ Pause"}</button
        >
        <button class="ghost" onclick={copy}>{copied ?? "Copy"}</button>
        <a
          class="ghost"
          href={`/api/logs/${encodeURIComponent(file)}?raw=1`}
          download={file}>Download</a
        >
      </div>
      <pre bind:this={pre}>{lines.length ? lines.join("\n") : "(empty)"}</pre>
    {:else}
      <p class="hint pad">Pick a file on the left.</p>
    {/if}
    {#if error}
      <p class="error">{error}</p>
    {/if}
  </section>
</div>

<style>
  .logs {
    display: grid;
    grid-template-columns: minmax(200px, 280px) minmax(0, 1fr);
    gap: 12px;
    padding: 12px;
    align-items: start;
  }

  aside,
  section {
    min-width: 0;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
  }

  aside {
    padding: 8px;
  }

  h2 {
    margin: 2px 4px 6px;
    font-size: 13px;
  }

  .hint {
    margin: 2px 4px 6px;
    color: var(--text-muted);
    font-size: 11px;
    overflow-wrap: anywhere;
  }

  .hint.pad {
    padding: 24px;
  }

  .file {
    display: grid;
    width: 100%;
    padding: 6px 8px;
    color: var(--text);
    background: none;
    border: 0;
    border-radius: 3px;
    text-align: left;
    cursor: pointer;
    font: inherit;
  }

  .file:hover {
    background: var(--surface-2);
  }

  .file.active {
    color: #ffffff;
    background: #276f4b;
  }

  .file .name {
    font-size: 12px;
    font-weight: 600;
    overflow-wrap: anywhere;
  }

  .file .meta {
    font-size: 10px;
    opacity: 0.8;
  }

  .bar {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }

  select,
  button.ghost,
  a.ghost {
    height: 26px;
    padding: 0 8px;
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    font: inherit;
    font-size: 12px;
    line-height: 24px;
    text-decoration: none;
  }

  pre {
    height: calc(100vh - 170px);
    min-height: 240px;
    margin: 0;
    padding: 8px 12px;
    overflow: auto;
    color: #dce5e0;
    background: #111713;
    font-size: 11px;
    line-height: 1.4;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .error {
    margin: 8px 12px;
    color: var(--bad);
  }

  @media (max-width: 760px) {
    .logs {
      grid-template-columns: 1fr;
    }
  }
</style>

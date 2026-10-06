<script lang="ts">
  // Camera name with inline rename: pencil -> text field, Enter saves
  // (POST /api/camera/name, stored as camera.name in the vision config),
  // Esc cancels. Shows "Camera <id>" when no name is set.

  let {
    camId,
    name,
    editable = true,
    showId = false,
    onrenamed,
  }: {
    camId: number;
    name: string | null | undefined;
    editable?: boolean;
    showId?: boolean;
    onrenamed?: () => void;
  } = $props();

  let editing = $state(false);
  let draft = $state("");
  let error = $state<string | null>(null);
  let saving = $state(false);

  let label = $derived(name ?? `Camera ${String(camId)}`);

  function start(): void {
    draft = name ?? "";
    error = null;
    editing = true;
  }

  async function save(): Promise<void> {
    if (!/^[A-Za-z0-9 _-]{0,40}$/.test(draft.trim())) {
      error = "1–40 letters, digits, spaces, - or _";
      return;
    }
    saving = true;
    try {
      const response = await fetch("/api/camera/name", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: draft.trim() }),
      });
      const result = (await response.json().catch(() => ({}))) as {
        error?: string;
      };
      if (!response.ok) {
        error = result.error ?? `HTTP ${String(response.status)}`;
        return;
      }
      editing = false;
      onrenamed?.();
    } catch (caught) {
      error = String(caught);
    } finally {
      saving = false;
    }
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key === "Enter") void save();
    else if (event.key === "Escape") {
      event.stopPropagation();
      editing = false;
    }
  }

  function focus(node: HTMLInputElement): void {
    node.focus();
    node.select();
  }
</script>

{#if editing}
  <span class="rename">
    <input
      use:focus
      bind:value={draft}
      maxlength="40"
      placeholder={`Camera ${String(camId)}`}
      disabled={saving}
      onkeydown={onKeydown}
      onblur={() => {
        if (!saving && !error) editing = false;
      }}
      aria-label="Camera name (Enter saves, Esc cancels)"
    />
    {#if error}<span class="error">{error}</span>{/if}
  </span>
{:else}
  <span class="name"
    >{label}{#if showId && name}&nbsp;<span class="id">(cam {camId})</span
      >{/if}{#if editable}<button
        class="pencil"
        title="Rename camera"
        aria-label="Rename camera"
        onclick={start}>✎</button
      >{/if}</span
  >
{/if}

<style>
  .name {
    white-space: nowrap;
  }

  .id {
    opacity: 0.75;
  }

  .pencil {
    margin-left: 4px;
    padding: 0 3px;
    color: inherit;
    background: none;
    border: 0;
    opacity: 0.6;
    cursor: pointer;
    font-size: 0.95em;
  }

  .pencil:hover {
    opacity: 1;
  }

  .rename {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }

  input {
    width: 16em;
    padding: 1px 5px;
    color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    font: inherit;
  }

  .error {
    color: var(--bad);
    font-size: 11px;
  }
</style>

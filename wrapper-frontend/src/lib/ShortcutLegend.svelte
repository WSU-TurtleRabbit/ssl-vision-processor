<script lang="ts">
  import { SHORTCUTS } from "./editHistory";

  // Compact keyboard/mouse legend for the point-editing modes.
</script>

<p class="shortcuts">
  {#each SHORTCUTS as [keys, what] (keys)}
    <span><kbd>{keys}</kbd> {what}</span>
  {/each}
</p>

<style>
  .shortcuts {
    display: flex;
    flex-wrap: wrap;
    gap: 2px 12px;
    margin: 6px 0 0;
    color: var(--text-muted);
    font-size: 11px;
  }

  kbd {
    padding: 0 4px;
    color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border);
    border-bottom-width: 2px;
    border-radius: 3px;
    font-family: inherit;
    font-size: 10px;
  }
</style>

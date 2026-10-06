<script lang="ts">
  // Inline SVG icons for the icon-only buttons (no icon font, no CDN).
  let {
    name,
    size = 18,
  }: {
    name:
      | "corners"
      | "lens"
      | "field"
      | "pick"
      | "popout"
      | "logs"
      | "scan"
      | "refresh"
      | "help"
      | "collapse"
      | "expand";
    size?: number;
  } = $props();
</script>

<svg
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width="2"
  stroke-linecap="round"
  stroke-linejoin="round"
  aria-hidden="true"
>
  {#if name === "corners"}
    <path d="M5 7 L19 5 L20 18 L4 19 Z" />
    <circle cx="5" cy="7" r="2" fill="currentColor" />
    <circle cx="19" cy="5" r="2" fill="currentColor" />
    <circle cx="20" cy="18" r="2" fill="currentColor" />
    <circle cx="4" cy="19" r="2" fill="currentColor" />
  {:else if name === "lens"}
    <circle cx="12" cy="12" r="9" />
    <path d="M4 12 Q12 6 20 12" />
    <path d="M4 12 Q12 18 20 12" />
  {:else if name === "field"}
    <rect x="3" y="6" width="18" height="12" rx="1" />
    <path d="M12 6 V18" />
    <circle cx="12" cy="12" r="2.5" />
    <path d="M3 9.5 H6 V14.5 H3 M21 9.5 H18 V14.5 H21" />
  {:else if name === "pick"}
    <path d="M14 4 L20 10 L10 20 L4 20 L4 14 Z" />
    <path d="M12 6 L18 12" />
  {:else if name === "popout"}
    <path d="M14 4 H20 V10" />
    <path d="M20 4 L11 13" />
    <path
      d="M18 14 V19 A1 1 0 0 1 17 20 H5 A1 1 0 0 1 4 19 V7 A1 1 0 0 1 5 6 H10"
    />
  {:else if name === "logs"}
    <path d="M4 6 H20 M4 10 H16 M4 14 H18 M4 18 H12" />
  {:else if name === "scan"}
    <circle cx="12" cy="12" r="9" />
    <circle cx="12" cy="12" r="4" />
    <path d="M12 12 L18 6" />
  {:else if name === "refresh"}
    <path d="M20 12 A8 8 0 1 1 17.5 6.2" />
    <path d="M20 4 V9 H15" />
  {:else if name === "help"}
    <circle cx="12" cy="12" r="9" />
    <path d="M9.5 9.5 A2.5 2.5 0 1 1 12.8 12 C12 12.4 12 13 12 14" />
    <circle cx="12" cy="17.5" r="0.8" fill="currentColor" />
  {:else if name === "collapse"}
    <path d="M6 9 L12 15 L18 9" />
  {:else}
    <path d="M6 15 L12 9 L18 15" />
  {/if}
</svg>

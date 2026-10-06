<script lang="ts">
  import DOMPurify from "dompurify";
  import { Marked, type Tokens } from "marked";
  import { SHORTCUTS } from "./editHistory";

  // Help: renders the repo's docs (GET /api/docs/<name>, whitelisted by the
  // backend) as sanitised HTML. Links between docs navigate in-app, ```
  // blocks get a copy button, ```mermaid blocks are rendered by mermaid,
  // which is imported lazily (only when a page contains a diagram). This
  // whole component is itself loaded lazily by App.

  let {
    name,
    dark,
    onnavigate,
  }: {
    name: string;
    dark: boolean;
    onnavigate: (name: string, anchor?: string) => void;
  } = $props();

  interface NavItem {
    name: string;
    label: string;
  }

  // Pages that live in the UI itself (not in docs/), appended to the nav.
  const BUILTIN: Record<string, { label: string; markdown: string }> = {
    shortcuts: {
      label: "⌨ Shortcuts",
      markdown: `# ⌨ Keyboard & mouse shortcuts

In **Set field corners** and **Lens correction** mode (they do not fire while you type in a text field):

| Key / mouse | What it does |
|---|---|
${SHORTCUTS.map(([keys, what]) => `| \`${keys}\` | ${what} |`).join("\n")}

Corners mode also has an **Orientation** bar once 4 corners are placed: *Rotate 180°* swaps the goal ends, *corner N → 1* makes another clicked corner the origin (−x, −y). Corner 1 is the origin; x runs along the long side toward the +x goal. A counter-clockwise click order is renumbered clockwise automatically (the calibration only accepts clockwise orders).
`,
    },
    cameras: {
      label: "📷 Cameras (scan)",
      markdown: `# 📷 Several Pi cameras

**Services → Scan for cameras...** probes the Jetson's LAN (the /24 of the interface that routes to the configured camera; otherwise every LAN /24, at most 1024 hosts) for the camstream service on port 8080 and on the configured camera's port, and lists what answered: name, host:port, streaming / idle / closed, size, fps.

- **Use this camera** writes \`camera.path\` (\`http://host:port/stream\`) into the vision config, and offers to restart vision_processor right away.
- **Restart / Open / Close** need the camera's token. The configured camera uses \`.camera-token\`; any other camera needs a line \`host: token\` in \`.camera-tokens\` (both in the repo folder, git-ignored, never shown in the UI). **Add token** writes that line for you (file mode 600).
- "No token saved for this camera" means exactly that — set it up as described in *Pi camera → 6*.
- *range...* lets you scan another range (CIDR, /24 .. /32), e.g. \`192.168.1.0/24\` or \`127.0.0.1/32\` for a fake camera on this machine.

**Start capture** (Services → Pi camera) opens the camera on the Pi and starts vision_processor; **Stop capture** stops vision_processor and closes the camera (two-step confirm); **Restart camera** just drops the stream so vision_processor reconnects. Shut down of the Pi is API-only (\`POST /api/camera/shutdown\`).
`,
    },
  };

  let html = $state("");
  let error = $state<string | null>(null);
  let loading = $state(true);
  let nav = $state<NavItem[]>([]);
  let content = $state<HTMLElement>();
  let renderId = 0;

  function slug(text: string): string {
    // GitHub-style heading ids: lowercase, drop punctuation/emoji, spaces -> -.
    return text
      .toLowerCase()
      .replace(/<[^>]*>/g, "")
      .replace(/[^\p{L}\p{N}_\s-]/gu, "")
      .trim()
      .replace(/\s/g, "-");
  }

  function escapeHtml(text: string): string {
    return text
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  const marked = new Marked({
    gfm: true,
    renderer: {
      heading({ tokens, depth, text }: Tokens.Heading): string {
        const inner = this.parser.parseInline(tokens);
        return `<h${String(depth)} id="${slug(text)}">${inner}</h${String(depth)}>\n`;
      },
      code({ text, lang }: Tokens.Code): string | false {
        if (lang === "mermaid")
          return `<pre class="mermaid-source">${escapeHtml(text)}</pre>\n`;
        return false;
      },
    },
  });

  // Doc link -> in-app doc name, resolved relative to the current doc. Per-setup
  // pages live in docs/<setup>/ and are served as "<setup>-<name>" ("zed/panic.md"
  // -> "zed-panic"); the running setup's pages also answer to their bare name.
  // "calibration.md", "zed/panic.md", "../calibration.md", "../pi_camera/README.md"
  function docTarget(
    href: string,
    current: string,
  ): { name: string; anchor?: string } | null {
    const match =
      /^(?:\.\/)?((?:\.\.\/){0,2})((?:[\w-]+\/)?[\w-]+)\.md(#.*)?$/.exec(href);
    if (!match?.[2]) return null;
    const ups = (match[1] ?? "").length / 3;
    const path = match[2];
    const setup = /^(zed|pi)-/.exec(current)?.[1];
    let docName: string | null;
    if (path === "pi_camera/README" && ups >= 1) docName = "pi-camera";
    else if (ups > 1) docName = null;
    else if (path.includes("/")) {
      const [dir = "", stem = ""] = path.split("/");
      docName = dir === "zed" || dir === "pi" ? `${dir}-${stem}` : null;
    } else if (ups === 1) docName = path;
    else docName = setup && current !== "pi-camera" ? `${setup}-${path}` : path;
    if (!docName) return null;
    return match[3]
      ? { name: docName, anchor: match[3].slice(1) }
      : { name: docName };
  }

  async function loadNav(): Promise<void> {
    // Navigation mirrors docs/README.md: its doc links, in order.
    try {
      const response = await fetch("/api/docs/README");
      if (!response.ok) return;
      const text = await response.text();
      const items: NavItem[] = [{ name: "README", label: "🏠 Home" }];
      for (const match of text.matchAll(/\[([^\]]+)\]\(([^)]+)\)/g)) {
        const target = docTarget(match[2] ?? "", "README");
        if (!target || target.anchor) continue;
        if (items.some((item) => item.name === target.name)) continue;
        const label = (match[1] ?? target.name)
          .replace(/\.\.\/pi_camera\/README\.md/, "Pi camera (Raspberry Pi)")
          .replace(/\.\.\/AGENTS\.md/, "Naming rules")
          .replace(/\.md$/, "");
        items.push({ name: target.name, label });
      }
      for (const [name, page] of Object.entries(BUILTIN))
        items.push({ name, label: page.label });
      nav = items;
    } catch {
      // Navigation is optional; the page itself still renders.
    }
  }

  async function load(docName: string): Promise<void> {
    const id = ++renderId;
    loading = true;
    error = null;
    try {
      let markdown = BUILTIN[docName]?.markdown;
      if (markdown === undefined) {
        const response = await fetch(
          `/api/docs/${encodeURIComponent(docName)}`,
        );
        if (!response.ok)
          throw new Error(
            response.status === 404
              ? `No document called "${docName}"`
              : `HTTP ${String(response.status)}`,
          );
        markdown = await response.text();
      }
      const rendered = await marked.parse(markdown);
      if (id !== renderId) return;
      html = DOMPurify.sanitize(rendered);
    } catch (caught) {
      if (id === renderId) {
        html = "";
        error = String(caught);
      }
    } finally {
      if (id === renderId) loading = false;
    }
  }

  async function copyText(text: string): Promise<boolean> {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // navigator.clipboard needs a secure context (https/localhost).
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.append(area);
      area.select();
      // eslint-disable-next-line @typescript-eslint/no-deprecated
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    }
  }

  function decorate(root: HTMLElement): void {
    for (const pre of root.querySelectorAll("pre:not(.mermaid-source)")) {
      if (pre.querySelector(".copy")) continue;
      const button = document.createElement("button");
      button.className = "copy";
      button.type = "button";
      button.textContent = "Copy";
      button.addEventListener("click", () => {
        const code = pre.querySelector("code")?.textContent ?? "";
        void copyText(code).then((ok) => {
          button.textContent = ok ? "Copied" : "Copy failed";
          setTimeout(() => {
            button.textContent = "Copy";
          }, 1500);
        });
      });
      pre.append(button);
    }
  }

  async function renderMermaid(
    root: HTMLElement,
    isDark: boolean,
  ): Promise<void> {
    const sources = [
      ...root.querySelectorAll<HTMLElement>("pre.mermaid-source"),
    ];
    if (sources.length === 0) return;
    const id = renderId;
    const { default: mermaid } = await import("mermaid");
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: "strict",
      theme: isDark ? "dark" : "default",
    });
    for (const [index, source] of sources.entries()) {
      if (id !== renderId) return;
      const target =
        source.nextElementSibling?.classList.contains("mermaid-diagram") ===
        true
          ? (source.nextElementSibling as HTMLElement)
          : source.insertAdjacentElement(
              "afterend",
              document.createElement("div"),
            );
      if (!(target instanceof HTMLElement)) continue;
      target.className = "mermaid-diagram";
      try {
        const { svg } = await mermaid.render(
          `mermaid-${String(id)}-${String(index)}-${isDark ? "d" : "l"}`,
          source.textContent,
        );
        target.innerHTML = svg;
        source.hidden = true;
      } catch (caught) {
        target.textContent = `Diagram error: ${String(caught)}`;
        source.hidden = false;
      }
    }
  }

  function onClick(event: MouseEvent): void {
    const anchor = (event.target as Element).closest("a");
    if (!anchor) return;
    const href = anchor.getAttribute("href") ?? "";
    if (href.startsWith("#")) {
      event.preventDefault();
      document.getElementById(href.slice(1))?.scrollIntoView();
      return;
    }
    const target = docTarget(href, name);
    if (target) {
      event.preventDefault();
      onnavigate(target.name, target.anchor);
      return;
    }
    if (/^https?:/.test(href)) {
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
    }
  }

  $effect(() => {
    void load(name);
  });

  $effect(() => {
    void loadNav();
  });

  // After each render (and on theme change): copy buttons, diagrams, anchor.
  $effect(() => {
    void html;
    const isDark = dark;
    const root = content;
    if (!root) return;
    queueMicrotask(() => {
      decorate(root);
      void renderMermaid(root, isDark);
      const hash = /#\/help\/[^#]+#(.+)$/.exec(location.hash)?.[1];
      if (hash)
        document.getElementById(decodeURIComponent(hash))?.scrollIntoView();
    });
  });
</script>

<div class="help">
  <nav aria-label="Documentation">
    {#each nav as item (item.name)}
      <a
        href={`#/help/${item.name}`}
        class:active={item.name === name}
        onclick={(event) => {
          event.preventDefault();
          onnavigate(item.name);
        }}>{item.label}</a
      >
    {/each}
  </nav>
  <!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_noninteractive_element_interactions -->
  <article bind:this={content} onclick={onClick}>
    {#if error}
      <p class="error">{error}</p>
    {:else if loading && !html}
      <p>Loading...</p>
    {/if}
    <!-- Sanitised with DOMPurify above. -->
    <!-- eslint-disable-next-line svelte/no-at-html-tags -->
    {@html html}
  </article>
</div>

<style>
  .help {
    display: grid;
    grid-template-columns: minmax(170px, 230px) minmax(0, 1fr);
    gap: 12px;
    padding: 12px;
    align-items: start;
  }

  nav {
    position: sticky;
    top: 12px;
    display: grid;
    gap: 2px;
    padding: 8px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
  }

  nav a {
    padding: 6px 8px;
    color: var(--text);
    border-radius: 3px;
    font-size: 13px;
    text-decoration: none;
  }

  nav a:hover {
    background: var(--surface-2);
  }

  nav a.active {
    color: #ffffff;
    background: #276f4b;
  }

  article {
    min-width: 0;
    padding: 6px 24px 24px;
    color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 5px;
    font-size: 14px;
    line-height: 1.55;
  }

  article :global(h1) {
    font-size: 24px;
    margin: 18px 0 10px;
  }

  article :global(h2) {
    font-size: 19px;
    margin: 24px 0 8px;
    padding-bottom: 4px;
    border-bottom: 1px solid var(--border-soft);
  }

  article :global(h3) {
    font-size: 16px;
    margin: 18px 0 6px;
  }

  article :global(a) {
    color: var(--blue-text);
  }

  article :global(code) {
    padding: 1px 4px;
    background: var(--code-bg);
    border-radius: 3px;
    font-size: 12.5px;
  }

  article :global(pre) {
    position: relative;
    padding: 10px 12px;
    overflow-x: auto;
    background: var(--code-bg);
    border: 1px solid var(--border-soft);
    border-radius: 4px;
  }

  article :global(pre code) {
    padding: 0;
    background: none;
  }

  article :global(pre .copy) {
    position: absolute;
    top: 6px;
    right: 6px;
    padding: 2px 8px;
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
  }

  article :global(table) {
    display: block;
    max-width: 100%;
    overflow-x: auto;
    border-collapse: collapse;
    font-size: 13px;
  }

  article :global(th),
  article :global(td) {
    padding: 5px 9px;
    border: 1px solid var(--border);
    vertical-align: top;
  }

  article :global(th) {
    background: var(--surface-2);
  }

  article :global(blockquote) {
    margin: 10px 0;
    padding: 4px 12px;
    color: var(--text-muted);
    border-left: 3px solid var(--border);
  }

  article :global(.mermaid-diagram) {
    margin: 10px 0;
    overflow-x: auto;
    text-align: center;
  }

  article :global(.mermaid-source) {
    white-space: pre-wrap;
  }

  .error {
    color: var(--bad);
  }

  @media (max-width: 760px) {
    .help {
      grid-template-columns: 1fr;
    }

    nav {
      position: static;
    }
  }
</style>

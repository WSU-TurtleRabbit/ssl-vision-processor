<script lang="ts">
  // Camera scan: GET /api/cameras/scan lists camstream Pis on the LAN.
  // Each result offers "Use this camera" (POST /api/cameras/use) and, with
  // a saved token, the control commands (POST /api/cameras/control); a
  // token can be added inline (POST /api/cameras/token -> .camera-tokens).

  interface ScannedCamera {
    host: string;
    port: number;
    streaming: boolean;
    client: string | null;
    closed: boolean;
    control: boolean;
    device?: string | null;
    size?: string | null;
    fps?: number | string | null;
    name_if_known: string | null;
    has_token: boolean;
    is_current: boolean;
  }

  interface ScanResponse {
    subnets: string[];
    hosts_probed: number;
    ports: number[];
    cameras: ScannedCamera[];
    error?: string;
  }

  let { onchange }: { onchange: () => void } = $props();

  let scanning = $state(false);
  let result = $state<ScanResponse | null>(null);
  let error = $state<string | null>(null);
  let subnet = $state("");
  let showSubnet = $state(false);
  let busy = $state<string | null>(null);
  let notice = $state<{ kind: "ok" | "error"; text: string } | null>(null);
  let confirmUse = $state<string | null>(null);
  let tokenFor = $state<string | null>(null);
  let tokenDraft = $state("");

  function key(cam: ScannedCamera): string {
    return `${cam.host}:${String(cam.port)}`;
  }

  async function scan(): Promise<void> {
    scanning = true;
    error = null;
    notice = null;
    try {
      const query = subnet.trim()
        ? `?subnet=${encodeURIComponent(subnet.trim())}`
        : "";
      const response = await fetch(`/api/cameras/scan${query}`);
      const data = (await response.json()) as ScanResponse;
      if (!response.ok)
        throw new Error(data.error ?? `HTTP ${String(response.status)}`);
      result = data;
    } catch (caught) {
      error = String(caught);
    } finally {
      scanning = false;
    }
  }

  async function post(
    url: string,
    body: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const response = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const data = (await response.json().catch(() => ({}))) as Record<
      string,
      unknown
    >;
    if (!response.ok || data["ok"] === false)
      throw new Error(
        typeof data["error"] === "string"
          ? data["error"]
          : `HTTP ${String(response.status)}`,
      );
    return data;
  }

  async function use(cam: ScannedCamera, restart: boolean): Promise<void> {
    confirmUse = null;
    busy = key(cam);
    notice = null;
    try {
      const data = await post("/api/cameras/use", {
        host: cam.host,
        port: cam.port,
        restart,
      });
      notice = {
        kind: "ok",
        text: `camera.path = ${String(data["path"])}${
          restart
            ? data["restarted"] === true
              ? "; vision_processor restarted."
              : `; ${typeof data["error"] === "string" ? data["error"] : "not restarted"}`
            : " (restart vision_processor to use it)."
        }`,
      };
      onchange();
      void scan();
    } catch (caught) {
      notice = { kind: "error", text: String(caught) };
    } finally {
      busy = null;
    }
  }

  async function control(
    cam: ScannedCamera,
    command: "restart" | "open" | "close",
  ): Promise<void> {
    busy = key(cam);
    notice = null;
    try {
      await post("/api/cameras/control", {
        host: cam.host,
        port: cam.port,
        command,
      });
      notice = { kind: "ok", text: `${key(cam)}: ${command} ok.` };
      onchange();
      void scan();
    } catch (caught) {
      notice = { kind: "error", text: `${key(cam)}: ${String(caught)}` };
    } finally {
      busy = null;
    }
  }

  async function addToken(host: string): Promise<void> {
    busy = host;
    notice = null;
    try {
      await post("/api/cameras/token", { host, token: tokenDraft.trim() });
      notice = {
        kind: "ok",
        text: `Token for ${host} saved to .camera-tokens.`,
      };
      tokenFor = null;
      tokenDraft = "";
      void scan();
    } catch (caught) {
      notice = { kind: "error", text: String(caught) };
    } finally {
      busy = null;
    }
  }
</script>

<div class="scan">
  <div class="row">
    <button class="ghost" disabled={scanning} onclick={scan}
      >{scanning ? "Scanning..." : "Scan for cameras"}</button
    >
    <button
      class="link"
      onclick={() => (showSubnet = !showSubnet)}
      title="Scan a different range (CIDR, /24 .. /32)"
      >{showSubnet ? "hide range" : "range..."}</button
    >
    {#if showSubnet}
      <input
        class="subnet"
        bind:value={subnet}
        placeholder="auto (LAN /24), e.g. 192.168.1.0/24"
        onkeydown={(event) => {
          if (event.key === "Enter") void scan();
        }}
      />
    {/if}
    {#if result}
      <span class="hint">
        {result.cameras.length} found · {result.hosts_probed} hosts in {result.subnets.join(
          ", ",
        )} · ports {result.ports.join(", ")}
      </span>
    {/if}
  </div>
  {#if error}
    <p class="notice error">{error}</p>
  {/if}
  {#if notice}
    <p class={`notice ${notice.kind}`}>{notice.text}</p>
  {/if}
  {#if result}
    {#each result.cameras as cam (key(cam))}
      <div class="camera" class:current={cam.is_current}>
        <div class="line">
          <strong>{cam.name_if_known ?? key(cam)}</strong>
          {#if cam.name_if_known}<code>{key(cam)}</code>{/if}
          {#if cam.is_current}<span class="tag">configured</span>{/if}
          <span class="hint">
            {cam.closed
              ? "closed by operator"
              : cam.streaming
                ? `streaming to ${cam.client ?? "?"}`
                : "idle"}
            {#if cam.size}· {cam.size}{/if}
            {#if cam.fps}· {cam.fps} fps{/if}
            {#if !cam.control}· remote control disabled on the Pi{/if}
          </span>
        </div>
        <div class="line">
          {#if !cam.is_current}
            {#if confirmUse === key(cam)}
              <span class="hint">Use this camera and</span>
              <button
                class="primary"
                disabled={busy !== null}
                onclick={() => use(cam, true)}>restart vision_processor</button
              >
              <button
                class="ghost"
                disabled={busy !== null}
                onclick={() => use(cam, false)}>just save</button
              >
              <button class="ghost" onclick={() => (confirmUse = null)}
                >Cancel</button
              >
            {:else}
              <button
                class="ghost"
                disabled={busy !== null}
                onclick={() => (confirmUse = key(cam))}>Use this camera</button
              >
            {/if}
          {/if}
          {#if cam.has_token && cam.control}
            <button
              class="ghost"
              disabled={busy !== null}
              onclick={() => control(cam, "restart")}>Restart</button
            >
            {#if cam.closed}
              <button
                class="ghost"
                disabled={busy !== null}
                onclick={() => control(cam, "open")}>Open</button
              >
            {:else}
              <button
                class="ghost"
                disabled={busy !== null}
                onclick={() => control(cam, "close")}>Close</button
              >
            {/if}
          {:else if cam.control}
            {#if tokenFor === cam.host}
              <input
                class="token"
                type="password"
                bind:value={tokenDraft}
                placeholder="token from the Pi"
                onkeydown={(event) => {
                  if (event.key === "Enter") void addToken(cam.host);
                  if (event.key === "Escape") tokenFor = null;
                }}
              />
              <button
                class="primary"
                disabled={busy !== null || tokenDraft.trim().length < 4}
                onclick={() => addToken(cam.host)}>Save token</button
              >
              <button class="ghost" onclick={() => (tokenFor = null)}
                >Cancel</button
              >
            {:else}
              <span class="hint warn">
                No token saved for this camera → cannot control it (add it to
                .camera-tokens, see Help → Pi camera → 6).
              </span>
              <button
                class="ghost"
                onclick={() => {
                  tokenFor = cam.host;
                  tokenDraft = "";
                }}>Add token</button
              >
            {/if}
          {/if}
        </div>
      </div>
    {:else}
      <p class="hint">No camstream camera answered.</p>
    {/each}
  {/if}
</div>

<style>
  .scan {
    padding: 4px 0 6px 17px;
    font-size: 12px;
  }

  .row,
  .line {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 8px;
  }

  .camera {
    margin: 6px 0;
    padding: 6px 8px;
    border: 1px solid var(--border-soft);
    border-radius: 4px;
  }

  .camera.current {
    border-color: #39a56c;
  }

  .tag {
    padding: 0 6px;
    color: var(--ok);
    background: var(--ok-bg);
    border-radius: 3px;
    font-size: 11px;
  }

  .hint {
    color: var(--text-muted);
    font-size: 11px;
  }

  .hint.warn {
    color: var(--warn);
  }

  code {
    font-size: 11px;
  }

  input {
    padding: 2px 6px;
    color: var(--text);
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 3px;
    font: inherit;
    font-size: 11px;
  }

  input.subnet {
    width: 18em;
  }

  input.token {
    width: 14em;
  }

  button {
    height: 24px;
    padding: 0 8px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
  }

  button:disabled {
    cursor: default;
    opacity: 0.5;
  }

  button.ghost {
    color: var(--text-muted);
    background: var(--surface);
    border: 1px solid var(--border);
  }

  button.primary {
    color: #ffffff;
    background: #276f4b;
    border: 1px solid #276f4b;
  }

  button.link {
    padding: 0;
    color: var(--text-muted);
    background: none;
    border: 0;
    text-decoration: underline;
  }

  .notice {
    margin: 4px 0;
    padding: 6px 8px;
    border-radius: 4px;
    font-size: 12px;
  }

  .notice.error {
    color: var(--bad);
    background: var(--bad-bg);
    border: 1px solid var(--bad-border);
  }

  .notice.ok {
    color: var(--ok);
    background: var(--ok-bg);
    border: 1px solid var(--ok-border);
  }
</style>

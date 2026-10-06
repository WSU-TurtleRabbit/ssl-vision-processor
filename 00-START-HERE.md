# ▶️ Start here

Run on the **Jetson**, each command in **its own terminal**, from the repo folder:

```bash
cd ~/ssl-software/test/ssl-vision-processor
```

## 1 · Backend + vision_processor

```bash
PATH=$HOME/.local/bin:$PATH ./start_wrapper.sh geometry-event.yml --vision-config config-pi-cam.yml --start-vision
```

Lab field instead of the foam mats: swap `geometry-event.yml` for `geometry-wrapper-lab-divB.yml`.

vision_processor alone — **only** if the backend was started without `--start-vision` (don't run both):

```bash
build/vision_processor config-pi-cam.yml
```

## 2 · Web page

Open **`http://192.168.210.222:8765`** (Tailscale: `http://100.84.89.60:8765`).
The backend serves the page itself; nothing else to start.

After changing anything in `wrapper-frontend/`, rebuild it once, then reload the browser:

```bash
cd wrapper-frontend && PATH=$HOME/.local/node/bin:$PATH npm run build
```

Build says `failed to resolve import "…"`? Run `npm ci` first, then build again. See [Troubleshooting → Frontend](docs/troubleshooting.md#frontend).

## Stop

**Ctrl+C** in each terminal.

---

The Pi camera starts by itself when the Pi boots (check: `curl http://192.168.210.149:8080/status` — port 8080 is the Pi, not the web page).
The Pi feeds **one** viewer at a time: while vision_processor runs, the Pi's raw stream (`:8080/stream`) answers 409 busy. Use the camera picture on the web page instead.
More detail: [docs/start-stop.md](docs/start-stop.md) · Something broken: [docs/panic.md](docs/panic.md)

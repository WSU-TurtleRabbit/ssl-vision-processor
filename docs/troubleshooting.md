[🏠 Home](README.md) · 🚨 PANIC: [📷 Pi](pi-camera/panic.md) · [🎥 ZED](zed-box/panic.md)

# 🔍 Troubleshooting (detailed)

In a hurry? → the panic page for your setup is shorter: [📷 Pi camera](pi-camera/panic.md) · [🎥 ZED Box](zed-box/panic.md). This page is for when you have an error message to look up.

## Which part is broken?

```mermaid
flowchart TD
    S["Something doesn't work"] --> P{"Camera OK?<br/>📷 curl PI:8080/status<br/>🎥 lsusb -d 2b03:"}
    P -->|no| PI["📷 pi_camera/README.md<br/>🎥 zed-box/panic.md"]
    P -->|yes| V{"vision_processor<br/>keeps running?"}
    V -->|"no / exits"| VP["→ vision_processor"]
    V -->|yes| B{"curl localhost:8765/api/health<br/>answers?"}
    B -->|no| BE["→ backend"]
    B -->|yes| F{"Page loads in<br/>the browser?"}
    F -->|no| FE["→ frontend"]
    F -->|"yes, but no data"| D["Backend sees no data:<br/>vision_processor running?<br/>right --vision-config?"]
```

Camera problems → 📷 [pi_camera/README.md troubleshooting](../pi_camera/README.md#troubleshooting) · 🎥 [ZED Box panic page, Fix A](zed-box/panic.md#fix-a-camera-zed)

## vision_processor

```mermaid
flowchart TD
    A["build/vision_processor YOUR-CONFIG.yml"] --> B{"What happens?"}
    B -->|"📷 No OpenCL platform / device<br/>(CPU build only)"| B1["clinfo -l empty →<br/>sudo apt install pocl-opencl-icd"]
    B -->|"📷 Image creation error: -59<br/>(CPU build only)"| B9["opencl.cpp must use CL_RGBA<br/>(PoCL rejects 1-channel)"]
    B -->|"Unknown camera/image driver: ZED"| B2["🎥 build without the ZED SDK →<br/>zed-box/setup.md"]
    B -->|"bad file: robot-heights.yml"| B3["Run from the repo folder"]
    B -->|"Saved sample image, then stops"| B4["Not calibrated →<br/>calibration.md"]
    B -->|"Hangs, no output"| B5{"wait_for_geometry: true?"}
    B5 -->|yes| B6["Start the backend first"]
    B5 -->|no| B7["Camera busy?<br/>📷 curl PI:8080/status<br/>🎥 pgrep -a ZED"]
    B -->|"Runs, 0 robots"| B8["Not calibrated, robots outside the field,<br/>or colours/exposure → colours.md"]
```

| Symptom | Fix |
|---|---|
| `frame time overrun: 40 ms` lines | Too slow for the camera fps. Use full-power mode ([performance.md](performance.md)) or lower the fps (📷 on the Pi · 🎥 `fps:` in `config-zed-lab.yml`). |
| Debug pictures (gradient/blob views) look striped | Known side effect of the CPU (PoCL) build only. Detection is not affected. |
| `GStreamer warning ... no source element for URI "/dev/video0"` | Wrong config: the USB draft config. Use 📷 `config-pi-cam.yml` or 🎥 `config-zed-lab.yml`. |

## Backend

```mermaid
flowchart TD
    A["./start_wrapper.sh ..."] --> B{"Error"}
    B -->|"protoc >= 3.19 is required"| B1["Restore .protoc/:<br/>git checkout .protoc"]
    B -->|"KeyError: 'optional_field_lines'"| B2["Old-format field file →<br/>📷 geometry-event.yml<br/>🎥 geometry-wrapper-lab-divB.yml"]
    B -->|"uv: command not found"| B3["Prefix: PATH=$HOME/.local/bin:$PATH"]
    B -->|"address already in use :8765"| B4["Already running:<br/>ss -ltnp | grep 8765"]
    B -->|"Runs, no detections in UI"| B5["vision_processor running?<br/>ip route get 224.5.23.2 → eno1"]
```

## Frontend

```mermaid
flowchart TD
    A["Open the web page<br/>📷 192.168.210.222 · 🎥 192.168.210.130"] --> B{"What happens?"}
    B -->|"Connection refused"| B1["npm run dev not running?<br/>ss -ltn | grep 5173"]
    B -->|"npm: not found / engine error"| B2["Prefix: PATH=$HOME/.local/node/bin:$PATH"]
    B -->|"Page says 'disconnected'"| B3["Backend down → start it"]
    B -->|":8765 says 'frontend is not built'"| B4["cd wrapper-frontend && npm run build"]
    B -->|"Colour panel: 'not publishing'"| B5["Rebuild + restart vision_processor"]
```

### `npm run build` fails with `failed to resolve import "…"`

Example: `Rolldown failed to resolve import "dompurify"`.

- **Cause:** `package.json` lists a package that isn't in `node_modules` yet. This happens when the packages were installed before someone added a new one, usually after a pull or a branch switch. The code is fine.
- **Fix:** reinstall exactly what `package-lock.json` lists, then build:

```bash
cd wrapper-frontend && PATH=$HOME/.local/node/bin:$PATH npm ci && npm run build
```

### npm says `N vulnerabilities (… high)`

- **Not an error.** The build still worked. npm checks every installed package against a list of known security issues.
- **Most are build tools** (vite, postcss, …) that run only while building and never end up in the page. To check what actually ships in the page, run `npm audit --omit=dev`.
- **Optional fix:** `npm audit fix` updates them within safe versions. Rebuild afterwards, and commit `package-lock.json` if it still builds.
- **Don't run `npm audit fix --force`.** It jumps to new major versions and can break the build.

Back: [🏠 Home](README.md)

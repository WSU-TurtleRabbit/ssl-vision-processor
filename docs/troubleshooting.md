[🏠 Home](README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md)

# 🔍 Troubleshooting (detailed)

In a hurry? → [🚨 panic.md](panic.md) is shorter. This page is for when you have an error message to look up.

## Which part is broken?

```mermaid
flowchart TD
    S["Something doesn't work"] --> P{"curl PI:8080/status<br/>answers?"}
    P -->|no| PI["Pi / network →<br/>pi_camera/README.md"]
    P -->|yes| V{"vision_processor<br/>keeps running?"}
    V -->|"no / exits"| VP["→ vision_processor"]
    V -->|yes| B{"curl localhost:8765/api/health<br/>answers?"}
    B -->|no| BE["→ backend"]
    B -->|yes| F{"Page loads in<br/>the browser?"}
    F -->|no| FE["→ frontend"]
    F -->|"yes, but no data"| D["Backend sees no data:<br/>vision_processor running?<br/>--vision-config = config-pi-cam.yml?"]
```

Pi camera problems → [pi_camera/README.md troubleshooting](../pi_camera/README.md#troubleshooting)

## vision_processor

```mermaid
flowchart TD
    A["build/vision_processor config-pi-cam.yml"] --> B{"What happens?"}
    B -->|"No OpenCL platform / device"| B1["clinfo -l empty →<br/>sudo apt install pocl-opencl-icd"]
    B -->|"Image creation error: -59"| B2["opencl.cpp must use CL_RGBA<br/>(PoCL rejects 1-channel)"]
    B -->|"bad file: robot-heights.yml"| B3["Run from the repo folder"]
    B -->|"Saved sample image, then stops"| B4["Not calibrated →<br/>calibration.md"]
    B -->|"Hangs, no output"| B5{"wait_for_geometry: true?"}
    B5 -->|yes| B6["Start the backend first"]
    B5 -->|no| B7["Camera busy?<br/>curl PI:8080/status"]
    B -->|"Runs, 0 robots"| B8["Not calibrated, robots outside the field,<br/>or colours/exposure → colours.md"]
```

| Symptom | Fix |
|---|---|
| `frame time overrun: 40 ms` lines | The CPU is too slow for the camera fps. Use full-power mode ([performance.md](performance.md)) or lower the fps on the Pi. |
| Debug pictures (gradient/blob views) look striped | Known side effect of the CPU (PoCL) build. Detection is not affected. |
| `GStreamer warning ... no source element for URI "/dev/video0"` | Wrong config (USB). Use `config-pi-cam.yml`. |

## Backend

```mermaid
flowchart TD
    A["./start_wrapper.sh ..."] --> B{"Error"}
    B -->|"protoc >= 3.19 is required"| B1["Restore .protoc/:<br/>git checkout .protoc"]
    B -->|"KeyError: 'optional_field_lines'"| B2["Old-format field file →<br/>use geometry-event.yml"]
    B -->|"uv: command not found"| B3["Prefix: PATH=$HOME/.local/bin:$PATH"]
    B -->|"address already in use :8765"| B4["Already running:<br/>ss -ltnp | grep 8765"]
    B -->|"Runs, no detections in UI"| B5["vision_processor running?<br/>ip route get 224.5.23.2 → eno1"]
```

## Frontend

```mermaid
flowchart TD
    A["Open http://192.168.210.222:5173"] --> B{"What happens?"}
    B -->|"Connection refused"| B1["npm run dev not running?<br/>ss -ltn | grep 5173"]
    B -->|"npm: not found / engine error"| B2["Prefix: PATH=$HOME/.local/node/bin:$PATH"]
    B -->|"Page says 'disconnected'"| B3["Backend down → start it"]
    B -->|":8765 says 'frontend is not built'"| B4["cd wrapper-frontend && npm run build"]
    B -->|"Colour panel: 'not publishing'"| B5["Rebuild + restart vision_processor"]
```

Back: [🏠 Home](README.md)

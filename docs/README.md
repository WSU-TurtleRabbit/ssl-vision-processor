**🏠 Home** · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md)

# Vision system docs: start here

**Something is broken right now?** → [🚨 panic.md](panic.md). One page, follow the boxes.

## What is this, in one picture?

```
 📷 camera on a Pi  ──video──▶  🧠 Jetson: vision_processor  ──robot & ball positions──▶  🤖 team software
                                        │
                                        ▼
                              📮 backend  ──▶  🖥️ web page (you look here)
```

1. **The camera** films the field. The Pi just sends the video over the cable.
2. **`vision_processor`** (the brain) finds robots and balls in every frame.
3. **The backend** (the post office) passes the results and pictures to the web page.
4. **The web page** shows you everything, and has buttons for calibration.

## Which page do I need?

| I want to… | Open |
|---|---|
| Fix something that broke | [🚨 panic.md](panic.md) |
| Start or stop the system | [▶️ start-stop.md](start-stop.md) |
| Understand how the parts fit together | [🧩 how-it-works.md](how-it-works.md) |
| Calibrate the field (4 corners) or measure the camera | [📐 calibration.md](calibration.md) |
| Fix wrong colours / sunlight problems | [🎨 colours.md](colours.md) |
| Dig into a specific error message | [🔍 troubleshooting.md](troubleshooting.md) |
| Set up the Pi camera from scratch | [📷 ../pi_camera/README.md](../pi_camera/README.md) |
| Set up a fresh Jetson | [🛠️ setup-jetson.md](setup-jetson.md) |
| Use a USB camera on the Jetson (TODO) | [🔌 usb-camera.md](usb-camera.md) |
| Know how fast it is / the GPU (CUDA) status | [⚡ performance.md](performance.md) |
| See what was changed for the Jetson | [📝 changelog.md](changelog.md) |

## Key facts to remember

| Thing | Value |
|---|---|
| Repo folder | `~/ssl-software/test/ssl-vision-processor` |
| Jetson address | `192.168.210.222` (Tailscale `100.84.89.60`) |
| Pi camera address | `192.168.210.149` (Ethernet, use this) · `.150` is Wi-Fi |
| Web page | `http://192.168.210.222:5173` |
| Field file (event, foam mats) | `geometry-event.yml` |
| Camera config | `config-pi-cam.yml` |

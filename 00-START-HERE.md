# ▶️ Start here

> 📖 **Read the full docs on GitHub, where they look much nicer** (diagrams and tables don't show properly in Obsidian):
> **https://github.com/WSU-TurtleRabbit/ssl-vision-processor/blob/zed-orin/docs/README.md**

## What is this?

A camera looks down at the field. A computer (a Jetson) finds the robots and the ball and tells the team software where they are. A web page shows you what it sees.

## 1 · Which computer am I on?

Type `hostname` in a terminal.

| It says | You're on | Camera |
|---|---|---|
| `GTW-ONX-…` | 🎥 **the ZED Box** | ZED 2i, plugged in by USB |
| anything else | 📷 **the Pi-camera Jetson** | USB camera on a Raspberry Pi |

## 2 · Start it

Open a terminal and copy **one** block: the one for your computer.

**🎥 ZED Box**

```bash
cd ~/ssl-software/TIGERS/vision-processor
PATH=$HOME/.local/bin:$PATH ./start_wrapper.sh geometry-wrapper-lab-divB.yml --vision-config config-zed-lab.yml --start-vision
```

Then open **http://192.168.210.130:8765** in a browser.

**📷 Pi camera**

```bash
cd ~/ssl-software/test/ssl-vision-processor
PATH=$HOME/.local/bin:$PATH ./start_wrapper.sh geometry-event.yml --vision-config config-pi-cam.yml --start-vision
```

Then open **http://192.168.210.222:8765** in a browser. The Pi turns its camera on by itself.

✅ **It works when** the web page shows the camera picture and a robot count above 0.

## 3 · Stop it

Press **Ctrl+C** in that terminal. That's it.

## 4 · Something's wrong?

| Problem | Do this |
|---|---|
| Web page won't open | Is the terminal from step 2 still running? If not, start again. |
| Web page says "frontend is not built" | `cd wrapper-frontend && PATH=$HOME/.local/node/bin:$PATH npm ci && npm run build` |
| 0 robots | The field corners need clicking: **Set field corners** on the web page |
| Anything else | The panic page for your setup: [🎥 ZED Box](docs/zed-box/panic.md) · [📷 Pi camera](docs/pi-camera/panic.md) |

## More

- **Start/stop in detail:** [🎥 ZED Box](docs/zed-box/start-stop.md) · [📷 Pi camera](docs/pi-camera/start-stop.md)
- **Everything else** (calibration, colours, setup): [docs/README.md](docs/README.md)

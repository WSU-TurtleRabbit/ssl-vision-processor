[🏠 Docs home](../docs/README.md) · [🚨 PANIC](../docs/panic.md) · [▶️ Start / Stop](../docs/start-stop.md)

# Raspberry Pi network camera

Turns a Raspberry Pi with a USB camera into a network camera for `vision_processor`.
The Pi does **no image processing**: it passes the camera's own MJPEG frames through unchanged,
so even a small Pi can do it. All vision work happens on the Jetson.

```mermaid
flowchart LR
    cam["USB camera<br/>MJPEG 1920x1080"] -->|USB| svc
    subgraph pi["Raspberry Pi (camstream.service, starts at boot)"]
        svc["camstream.py<br/>:8080"] -->|starts on connect<br/>stops on disconnect| ff["ffmpeg<br/>-c:v copy (no re-encode)"]
    end
    ff -->|"http://PI-IP:8080/stream<br/>~120 Mbit/s @ 1080p60"| vp["Jetson<br/>vision_processor"]
    ui["Browser / backend"] -.->|"GET /status"| svc
```

| File | What it is |
|---|---|
| `camstream.py` | The stream server. Camera is **on only while someone watches** `/stream`. One viewer at a time. |
| `camstream.service` | systemd unit: starts `camstream.py` at boot and restarts it if it crashes. |
| `stream.sh` | Manual test loop (no service). One Ctrl+C stops it. |

## Which Pi?

A 1080p MJPEG frame is about 250 kB (2 Mbit), so the stream needs roughly **2 Mbit/s × fps** (≈120 Mbit/s at 60 fps, more for busy scenes).
The bottleneck is the network and USB bus, not the CPU.

| Model | Ethernet | Camera + network share USB 2? | Recommended max |
|---|---|---|---|
| **Pi 4 / Pi 5** (recommended) | Gigabit (native) | No (USB 3) | 1080p @ 120 fps |
| Pi 3B+ | "Gigabit" over USB 2 (~300 Mbit/s real) | Yes | 1080p @ 30–60 fps (test it) |
| Pi 3B | 100 Mbit/s over USB 2 | Yes | 720p @ 60 or 1080p @ 30 |

Also needed: a **wired Ethernet** connection (Wi-Fi is too slow/unstable for 60 fps),
a camera that offers **MJPG** (`v4l2-ctl --list-formats-ext`), and the official power supply
(undervoltage drops USB devices).

### Pi 4 (minimum recommended, what this lab uses)

- Any RAM size works (1 GB is plenty; the Pi only forwards frames, CPU load stays low).
- Plug the camera into a **blue USB 3 port**. The Ethernet port is separate, so camera and network never compete.
- Runs the defaults in `camstream.service` (1920x1080 @ 60 fps) comfortably, and 120 fps with a bright field.

### Pi 3 (works, with limits)

- On a Pi 3 the **Ethernet port and all USB ports share one USB 2 bus** (480 Mbit/s total).
  The video crosses that bus twice: camera → Pi, then Pi → Ethernet.
- **Pi 3B**: 100 Mbit/s Ethernet. Use `--size 1280x720 --fps 60` (≈55 Mbit/s) or `--size 1920x1080 --fps 30` (≈60 Mbit/s).
- **Pi 3B+**: faster Ethernet (~300 Mbit/s in practice), so 1080p @ 60 may work. Test it before relying on it.
- Use the 64-bit OS (the Pi 3 supports it) and the official 2.5 A supply; a Pi 3 under load browns out USB more easily.
- Change the size/fps on the `ExecStart=` line (see [section 5](#5-adjusting-camera-settings)), then check
  the real frame rate from the Jetson:

```bash
timeout 10 ffmpeg -hide_banner -i http://<pi-ip>:8080/stream -f null - 2>&1 | grep -o "fps=[ 0-9.]*" | tail -1
```

If it stays clearly below the configured fps, lower `--size` or `--fps`, or use a Pi 4.

## 1. Prepare the Pi (once)

Flash **Raspberry Pi OS (64-bit) Bookworm** (Lite is enough) with SSH enabled. Then on the Pi:

```bash
sudo apt update && sudo apt install -y ffmpeg v4l-utils python3
```

Find the camera and check it supports MJPG:

```bash
v4l2-ctl --list-devices
```

```bash
v4l2-ctl -d /dev/video0 --list-formats-ext | grep -A3 MJPG
```

A USB camera usually shows up as two nodes (`/dev/video0` = video, `/dev/video1` = metadata). Use the first.
Note the Pi's **Ethernet** address with `ip -br addr show eth0`.

## 2. Install the service

From the Jetson (asks for the Pi password), copy the files:

```bash
scp pi_camera/camstream.py pi_camera/camstream.service pi_camera/stream.sh pi@<pi-ip>:~/
```

On the Pi, install and start it at boot:

```bash
sudo cp ~/camstream.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now camstream
```

> The unit assumes user `pi` and `/home/pi/camstream.py`. For another user, edit `User=` and the path in `camstream.service` first.

Check from the Jetson (should print `"streaming": false` = waiting, camera off):

```bash
curl http://<pi-ip>:8080/status
```

## 3. Start / stop

| What | Command (on the Pi) |
|---|---|
| Start now | `sudo systemctl start camstream` |
| Stop now | `sudo systemctl stop camstream` |
| Restart (after editing settings) | `sudo systemctl restart camstream` |
| Start at boot on / off | `sudo systemctl enable camstream` / `sudo systemctl disable camstream` |
| Is it running? | `systemctl status camstream` |
| Live log (camera ON/OFF, errors) | `journalctl -u camstream -f` |

The camera itself switches on/off automatically: `vision_processor` connecting turns it on, disconnecting turns it off.

**Manual test without the service** (stop the service first, both use port 8080):

```bash
sudo systemctl stop camstream && ~/stream.sh
```

Stop it with **one Ctrl+C**. The ffmpeg lines `Immediate exit requested` / `Bad file descriptor` are harmless.
Other settings: `SIZE=1280x720 FPS=30 ~/stream.sh`.

## 4. Viewing the camera

Only one viewer at a time, so stop `vision_processor` first. Then open `http://<pi-ip>:8080/stream`
in a browser, or on the Jetson:

```bash
ffplay -fflags nobuffer http://<pi-ip>:8080/stream
```

## 5. Adjusting camera settings

Resolution and frame rate are set with `--size` and `--fps`; image settings (exposure, gain, white balance...)
with repeatable `--ctrl name=value`, applied every time the camera switches on.

**Step 1 — see what your camera supports** (names differ between cameras):

```bash
v4l2-ctl -d /dev/video0 --list-ctrls-menus
```

**Step 2 — try values live** while `vision_processor` or a viewer is connected:

```bash
v4l2-ctl -d /dev/video0 --set-ctrl=auto_exposure=1 --set-ctrl=exposure_time_absolute=100
```

**Step 3 — make them permanent:** edit the `ExecStart=` line, then restart:

```bash
sudo systemctl edit --full camstream
```

```ini
ExecStart=/usr/bin/python3 /home/pi/camstream.py --device /dev/video0 --size 1920x1080 --fps 60 --port 8080 \
  --ctrl power_line_frequency=1 --ctrl auto_exposure=1 --ctrl exposure_time_absolute=100 \
  --ctrl white_balance_automatic=0 --ctrl white_balance_temperature=4600 --ctrl gain=0
```

```bash
sudo systemctl restart camstream
```

Good starting points for SSL vision (from [documentation.md](../documentation.md)):

| Setting | Typical control name | Advice |
|---|---|---|
| Light flicker | `power_line_frequency` | `1` = 50 Hz (Australia/Europe), `2` = 60 Hz. Removes banding/flicker. |
| Exposure | `auto_exposure` = `1` (manual), `exposure_time_absolute` (units of 0.1 ms) | Use manual. Must be shorter than one frame: < 166 at 60 fps, < 83 at 120 fps. Shorter = less motion blur, darker. |
| Brightness | `gain` | Raise only if too dark after exposure is maxed. Too bright → robot dots turn white → teams/IDs flicker. |
| White balance | `white_balance_automatic` = `0`, `white_balance_temperature` | Fixed value so colours don't drift; white field lines should look white, not orange. |
| Frame rate | `--fps` | 60 is the SSL norm. 120 needs a bright field and a fast enough Jetson pipeline. |

> Older kernels use `exposure_auto`, `exposure_absolute`, `white_balance_temperature_auto` instead.
> Colours of the robot markers do **not** need tuning on the Pi: `vision_processor` learns them automatically.

## 6. Remote control (no SSH needed)

Four commands can be sent from the Jetson or the web page, protected by a shared secret:

| Command | What happens |
|---|---|
| `restart` | Camera off, the viewer reconnects, camera on again. Use when the picture is frozen or stuck. |
| `close` | Camera off and refused to everyone until `open` or a reboot. Use before moving or unplugging things. |
| `open` | Allow streaming again after `close`. |
| `shutdown` | Shuts the Pi down cleanly (then pull the power). |

**Set up once** (on the Pi, after copying the new `camstream.py`, `camstream.service` and `camstream-sudoers`):

```bash
echo "CAMSTREAM_TOKEN=$(openssl rand -hex 16)" | sudo tee /etc/camstream.env && sudo chmod 600 /etc/camstream.env && sudo install -m 440 ~/camstream-sudoers /etc/sudoers.d/camstream && sudo cp ~/camstream.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart camstream && sudo cat /etc/camstream.env
```

The last line prints the token. Give the same token to the Jetson backend (see the backend README, `--camera-token-file`), so the web page's camera buttons work.
Without a token on the Pi, the commands are switched off and answer `403`.

**From the Jetson by hand** (replace `TOKEN`):

```bash
curl -X POST -H "X-Camstream-Token: TOKEN" http://192.168.210.149:8080/control/close
```

Same for `/control/open`, `/control/restart` and `/control/shutdown`. `GET /status` shows `"closed": true/false` and `"control": true` when a token is set.

**Log without SSH.** The service keeps its last 1000 log lines (camera on/off, commands, ffmpeg and camera errors):

```bash
curl http://192.168.210.149:8080/log
```

The Jetson backend fetches this automatically into `logs/pi-camera-<host>.log` and shows it on the web page (Services → Pi camera → Show log). `journalctl -u camstream` on the Pi still has the full history.

## Troubleshooting

```mermaid
flowchart TD
    A["Jetson: curl http://PI-IP:8080/status"] --> B{Answer?}
    B -->|"Connection refused"| C["Service not running<br/>Pi: systemctl status camstream"]
    C --> C1{"Active?"}
    C1 -->|"inactive"| C2["sudo systemctl start camstream"]
    C1 -->|"failed"| C3["journalctl -u camstream -n 50<br/>Port 8080 in use? stop stream.sh<br/>Wrong path/user in camstream.service?"]
    B -->|"Timeout / no route"| D["Network problem<br/>ping PI-IP · cable · right IP (eth0 not wlan0)?"]
    B -->|"JSON reply"| E{"streaming?"}
    E -->|"true, client = someone else"| E1["Only one viewer allowed:<br/>close browser / ffplay / other vision_processor"]
    E -->|false| F["Open the stream:<br/>ffplay http://PI-IP:8080/stream"]
    F --> G{"Picture?"}
    G -->|"503 camera failed to start"| H["Camera problem<br/>v4l2-ctl --list-devices<br/>replug USB · check --device / --size / --fps<br/>are supported (--list-formats-ext)"]
    G -->|"Stutters / low fps"| I["cat /sys/class/net/eth0/speed = 1000?<br/>vcgencmd get_throttled = 0x0?<br/>Lower --fps or --size; brighter light<br/>(auto exposure lowers fps in the dark)"]
    G -->|"Too dark / bright / coloured"| J["Section 5: adjust --ctrl settings"]
    G -->|"Yes, looks good"| K["Pi is fine → check the Jetson side"]
```

| Symptom | Likely cause | Fix |
|---|---|---|
| `409 camera busy` | Another viewer is connected | Close it; check `client` in `/status` |
| `failed to set X` in the log | Control name/value not supported by this camera | Check `--list-ctrls-menus`, fix the `--ctrl` |
| Frame rate lower than set | Camera auto-exposure lengthens frames in dim light | Manual exposure below the frame time, more light |
| Pi reboots / camera disappears | Undervoltage | Official PSU; `vcgencmd get_throttled` should be `0x0` |
| Works on .150 but not .149 (or the reverse) | One address is Wi-Fi | Use the `eth0` address |

[🏠 Home](README.md) · 🚨 PANIC: [📷 Pi](pi-camera/panic.md) · [🎥 ZED](zed-box/panic.md)

# 🧩 How it works

## The short version

| Part | Think of it as | What it does | Runs on |
|---|---|---|---|
| **the camera** | 📷 the eyes | 📷 **Pi setup:** `camstream` on the Pi sends the camera video over the network, only while someone watches. 🎥 **ZED Box:** the ZED 2i is plugged into the Jetson and read through the ZED SDK. | Pi / Jetson |
| **vision_processor** | 🧠 the brain | Finds robots and balls in every frame and works out where they are on the field | Jetson |
| **backend** (`wrapper_backend`) | 📮 the post office | Holds the field size, passes results and pictures to the web page, saves calibration changes. No image work. | Jetson |
| **web page** (`wrapper-frontend`) | 🖥️ the TV screen | Shows everything, and has buttons for calibration and colours | your browser |

## The full picture

📷 **Pi camera setup:**

```mermaid
flowchart LR
    subgraph pi["Raspberry Pi 4 · 192.168.210.149"]
        cam["USB camera<br/>MJPEG 1080p"] --> cs["camstream<br/>:8080"]
    end
    subgraph jetson["Jetson AGX Orin · 192.168.210.222"]
        vp["vision_processor<br/>config-pi-cam.yml"]
        img[("img/<br/>pictures + colours")]
        be["backend + web page<br/>:8765"]
    end
    browser["Your browser"]
    teams["Team AI / game controller"]

    cs -->|"video"| vp
    vp -->|"robot & ball positions"| teams
    vp -->|"positions + calibration"| be
    be -->|"field size, 1×/s"| vp
    be --> teams
    vp -->|writes| img --> be
    be <-->|"live data + the page"| browser
```

🎥 **ZED Box:**

```mermaid
flowchart LR
    subgraph box["ZED Box (Jetson Orin NX) · 192.168.210.130"]
        cam["ZED 2i<br/>USB 3"] -->|"ZED SDK, GPU"| vp["vision_processor<br/>config-zed-lab.yml"]
        img[("img/<br/>pictures + colours")]
        be["backend + web page<br/>:8765"]
    end
    browser["Your browser"]
    teams["Team AI / game controller"]

    vp -->|"robot & ball positions"| teams
    vp -->|"positions + calibration"| be
    be -->|"field size, 1×/s"| vp
    be --> teams
    vp -->|writes| img --> be
    be <-->|"live data + the page"| browser
```

## Frontend vs backend

- **Backend:** a small Python program on the Jetson (port 8765). It **knows things**: the field size, the latest robot positions, the camera pictures. It **changes things** when you click buttons: saving colours or corners, restarting `vision_processor`.
- **Frontend:** the web page in your browser. It **only shows** what the backend tells it, and sends your button clicks back. If the backend is down, the page says "disconnected".

## How the camera turns on and off

🎥 **ZED Box:** simple. The ZED is on while `vision_processor` runs, and only one program can use it at a time.

📷 **Pi camera setup:**

**Nobody presses a camera button.** Connecting turns it on, disconnecting turns it off:

```mermaid
sequenceDiagram
    participant VP as vision_processor (Jetson)
    participant CS as camstream (Pi, always waiting)
    participant CAM as camera (Pi)
    Note over CS: Pi boots → waits, camera off
    VP->>CS: "send me video"
    CS->>CAM: switch on
    CAM-->>VP: video frames
    VP--xCS: stops / disconnects
    CS->>CAM: switch off (< 0.5 s)
    Note over CS: a 2nd viewer meanwhile gets "busy"
```

Only **one** viewer at a time. That's why opening the stream in a browser while `vision_processor` runs gives "busy".

## Where things are written

| File | Written by | Used for |
|---|---|---|
| camera config: 📷 `config-pi-cam.yml` · 🎥 `config-zed-lab.yml` | you / the web page | camera settings, camera height, field corners, colours |
| field file: 📷 `geometry-event.yml` · 🎥 `geometry-wrapper-lab-divB.yml` | you | field size |
| `img/0.raw.jpg` | vision_processor (every second) | camera picture on the web page, corner clicking |
| `img/0.colors.json` | vision_processor (2×/s) | colour panel on the web page |
| `img/*.calib.json` | vision_processor (after calibration) | calibration result |

Next: [📐 calibration.md](calibration.md) · [🎨 colours.md](colours.md)

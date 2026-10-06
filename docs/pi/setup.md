[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 📷 Pi camera

# 🛠️ One-time setup (Pi camera + AGX Jetson)

**This page is for the Pi camera setup:** the USB camera on a Raspberry Pi, streaming to the AGX Jetson. Using the ZED Box instead? → [ZED Box setup](../zed/setup.md)

**Only needed once per Jetson.** This one is already set up.

## 1. System packages (needs sudo)

```bash
sudo apt install --no-install-recommends build-essential cmake pkg-config libyaml-cpp-dev ocl-icd-opencl-dev opencl-clhpp-headers libeigen3-dev libopencv-dev protobuf-compiler libprotobuf-dev libavcodec-dev libavformat-dev libavutil-dev pocl-opencl-icd clinfo
```

Check it: `clinfo -l` must list `Portable Computing Language`.

## 2. Build vision_processor

```bash
cmake -B build . && make -j12 -C build vision_processor
```

If CUDA is installed (`/usr/local/cuda`), this builds the **CUDA** version, so the GPU does the image work; otherwise the CPU (OpenCL/PoCL) version.
Check it: the first log line of `vision_processor` says `Using device: CUDA …` or `Using device: Portable Computing Language …`.

## 3. Backend tools (Python)

- `uv` lives in `~/.local/bin`, installed with `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- It uses Python 3.12 (`/usr/bin/python3.12`).

```bash
export PATH=$HOME/.local/bin:$PATH && uv sync
```

## 4. Web page tools (Node)

- Node 22 lives in `~/.local/node`. It's the official linux-arm64 download, unpacked there.
- Ubuntu's own Node 12 is too old.

```bash
export PATH=$HOME/.local/node/bin:$PATH && cd wrapper-frontend && npm ci
```

**Tip:** add both `export PATH=...` lines to `~/.bashrc`, so you never have to type them.

## 5. Full speed (recommended)

The Jetson ships in 50 W mode, which caps the CPU at 1.5 GHz instead of 2.2 GHz:

```bash
sudo nvpmodel -m 0 && sudo jetson_clocks
```

Check with `nvpmodel -q`: it should say `MAXN`. Switching modes may ask for a reboot.

## 6. The Pi camera

→ [pi_camera/README.md](../../pi_camera/README.md)

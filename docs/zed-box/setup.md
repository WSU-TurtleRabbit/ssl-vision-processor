[🏠 Home](../README.md) · [🚨 PANIC](panic.md) · [▶️ Start / Stop](start-stop.md) · 🎥 ZED Box

# 🛠️ One-time setup on the ZED Box

**Only needed once per box.** This one (`GTW-ONX-E15GN52P`) is already set up.

The ZED Box is a StereoLabs Jetson **Orin NX** with Ubuntu 20.04 (JetPack 5, CUDA 11.4). The **ZED SDK** comes preinstalled in `/usr/local/zed`. Pi camera setup instead? → [Pi camera setup](../pi-camera/setup.md)

## 1. System packages (needs sudo)

```bash
sudo apt install --no-install-recommends build-essential cmake pkg-config g++-10 libyaml-cpp-dev libeigen3-dev libopencv-dev protobuf-compiler libprotobuf-dev libavcodec-dev libavformat-dev libavutil-dev
```

- **`g++-10`**, because Ubuntu 20.04's default gcc 9 is too old for this code. CMake picks `g++-10` by itself.
- **No OpenCL/PoCL needed.** The image work runs on the GPU with CUDA.
- **The ZED SDK** is already there. If it's missing: install the StereoLabs ZED SDK for **L4T 35 (JetPack 5)** from the StereoLabs website.

## 2. Build vision_processor

```bash
cmake -B build . && make -j6 -C build vision_processor
```

The CMake output must include:
- `CUDA backend enabled (11.4…, sm_87)`
- `ZED SDK found: /usr/local/zed/lib/libsl_zed.so`

Check it: start `build/vision_processor config-zed-lab.yml`. The first lines must say `Using device: CUDA … Orin` and `[ZED] Opened ZED 2i …`.

| Build error | Fix |
|---|---|
| `error: expected unqualified-id before 'const'` in `sl/Camera.hpp` | An old build folder from before the fix. `rm -rf build`, then build again. |
| `No rule to make target 'vision_processor,'` | A comma got copied into the command. Remove it. |

## 3. Backend tools (Python)

- `uv` lives in `~/.local/bin`, installed with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

```bash
export PATH=$HOME/.local/bin:$PATH && uv sync
```

The `.venv/` folder can always be deleted and rebuilt with `uv sync`. `uv.lock` defines exactly what goes in it.

## 4. Web page tools (Node)

- Node 22 lives in `~/.local/node`. It's the official linux-arm64 download, unpacked there.
- Ubuntu's own Node is too old.

```bash
export PATH=$HOME/.local/node/bin:$PATH && cd wrapper-frontend && npm ci && npm run build
```

The backend serves the built page itself on port 8765.

**Tip:** add both `export PATH=...` lines to `~/.bashrc`, so you never have to type them.

## 5. The camera

Plug the ZED 2i into a **USB 3** (blue) port, directly or through a USB 3 hub. Nothing else to install. Settings: [📷 camera.md](camera.md).

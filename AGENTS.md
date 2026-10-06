# Rules for people and AI working in this repo

This fork runs on **two setups**. Keep each setup's things together and named after it.

| Setup id | What it is | Computer |
|---|---|---|
| `zed` | ZED 2i plugged into the Jetson by USB, read with the ZED SDK | ZED Box (Jetson Orin NX), `GTW-ONX-…`, 192.168.210.130 |
| `pi` | USB camera on a Raspberry Pi, video over the network | Jetson AGX Orin, 192.168.210.222 |

## Naming

| Kind of file | Name | Example |
|---|---|---|
| Camera config (one per setup and field) | `<setup>-config-<field>[-<variant>].yml` in the repo root | `zed-config-lab.yml`, `zed-config-lab-v4l2.yml` |
| Field file (shared: any camera can use any field) | `geometry-<field>.yml` | `geometry-event.yml` |
| Docs for one setup only | `docs/<setup>/<page>.md` | `docs/zed/panic.md` |
| Docs for both setups | `docs/<page>.md`; mark setup-only lines 📷 Pi / 🎥 ZED | `docs/calibration.md` |
| Old copies you want to keep visible | `archive/` | `archive/config-robocup-lab.yml.bak-…` |

- **Lower case, words joined by `-`.** No spaces, no dates in active file names.
- **No backup copies next to the real file** (`*.bak`, `*.before-*`, `*-old.yml`). Git keeps history. Commit before experimenting, or put the copy in `archive/`.
- **Experiments and measurements** go in a scratch folder outside the repo. Only the result goes in, as a comment in the config or a line in `docs/<setup>/decisions.md`.
- **Legacy names, not renamed yet:** `config-pi-cam.yml` (would be `pi-config-event.yml`) and `geometry-wrapper-lab-divB.yml` (the lab field). They're used on the AGX, so rename them only together with someone who can test there.
- **`docs/pi/camera.md` must not be created.** The web page's Help already uses the name `pi-camera` for `pi_camera/README.md`.

## Where the docs show up

- Every page is also shown in the web page's **Help** view. `wrapper_backend/docs.py` serves `docs/*.md`, plus `docs/<setup>/*.md` as `<setup>-<page>`. The running setup's pages also answer to their short name: on the ZED Box, `panic` is `zed-panic`.
- **New setup folder?** Add its id to `SETUPS` in `wrapper_backend/docs.py`, and to the `zed|pi` checks in `docTarget()` in `wrapper-frontend/src/lib/HelpView.svelte`.
- **Docs style:** short, plain words (ELI5), tables over paragraphs. `00-START-HERE.md` stays a one-screen start/stop card. Full docs: `docs/README.md` (renders properly on GitHub, not in Obsidian).

## Before calling a change done

- **C++:** `cmake -B build . && make -j6 -C build vision_processor`. The log must say `CUDA backend enabled` (and on the ZED Box `ZED SDK found`).
- **Backend:** `uv run ruff check wrapper_backend/ && uv run ruff format --check wrapper_backend/ && uv run mypy wrapper_backend/`
- **Frontend:** `cd wrapper-frontend && npm run check && npm run lint && npm run build`
- **Docs:** every relative link and `#anchor` must resolve.

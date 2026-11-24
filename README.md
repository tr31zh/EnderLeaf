# EnderLeaf

EnderLeaf turns an **Ender 3 v3 SE 3D printer** into a **leaf-disc phenotyping robot**. The printer's
**extruder kit** is removed and replaced by a 3D-printed **anchor** that mounts a **camera** and an
**LED ring** (the anchor attaches to the printer in place of the extruder, never to it). A
Raspberry Pi drives the camera and the LED ring and talks to the printer over serial (the "stage").
Leaf discs are arranged in a grid (default **9×9**) on a plate placed on top of the
print bed; the robot moves the camera over each disc and **acquires four images per disc, each under
a different lighting configuration** (the four cardinal quarters N/E/S/W of the LED ring).

![Hardware setup](addons/resources/hardware_setup.png)

EnderLeaf's job **stops at acquisition**: it captures and saves the four raw images (with metadata)
for each disc. **It does not merge or process them.** Reflection removal, disc segmentation, and
pathogen-resistance scoring are performed downstream by the separate
[`EnderLeafMl`](#references) project.

EnderLeaf is a fork of
[Enderscope](https://github.com/mutterer/enderscope); new/changed behavior lives in the `enderleaf/`
package, while `enderscope/` is kept as a stable base.

## Contents

- [Overview](#overview)
- [Architecture & data flow](#architecture--data-flow)
- [Hardware requirements](#hardware-requirements)
- [Installation](#installation)
- [Running](#running)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [References](#references)

## Overview

A single EnderLeaf **node** is one Raspberry Pi + printer + camera + LED ring. Each node:

1. Moves the camera over a leaf disc using the printer stage (over serial).
2. For that disc, runs a **lighting cycle** — by default `LightsCycle.ONE_FOURTH`, i.e. four
   configurations lighting N, E, S, and W quarters of the ring separately.
3. **Captures one image per configuration** and records the associated metadata.
4. Saves the raw images and metadata to its own `output/` folder.

![Acquisition pipeline](addons/resources/acquisition_pipeline.png)

The four images of a disc, taken under different lighting, are later merged per color-channel
(always `MIN`) to suppress reflections — but that work happens in `EnderLeafMl`, not here.

## Architecture & data flow

The system is **distributed / client-server**:

- **Node** (one per Raspberry Pi + printer): runs `enderleaf/ws_node.py`, a websocket **server**
  (default port **8765**) that owns an `EnderLeafController` and handles remote commands.
- **Main node / UI** (a dedicated host computer **or one of the worker nodes**): runs
  `enderleaf/ws_ui.py` (Flet desktop/web UI, the production UI) or `enderleaf/enderleaf_ui.py` +
  `serve_enderleaf.py` (Panel UI, used **only to test new features — never in production**). It is the
  **coordinating node** that connects to all worker nodes. When a worker node also acts as the main
  node it must have a large hard drive attached (at least 2 TB) to store the imported images.
- The **Panel UI has no websocket capability**: it can only control the printer / camera / LED ring
  of the **single EnderLeaf it is launched on**, so it cannot coordinate other nodes. That is why it
  is for testing new features only — any multi-node run must use the Flet UI (`ws_ui.py`).
- **Data handoff**: each worker node saves its images and metadata in its **local `output/`
  folder**. The main node **imports them over SFTP into `images/received/`** (raw download target);
  once a file's content is verified (CSV parsed / image decoded) it is **atomically moved to
  `images/validated/` and removed from the source node's `output/`**. The downstream `EnderLeafMl`
  project then consumes `images/validated/` and moves processed results to `images/processed/`.

![System architecture](addons/resources/architecture.png)

The **control message protocol** is defined in `enderleaf/socket_message.py`
(`SocketMessage`, `SocketData`) and enumerated in `enderleaf/enums.py` (`MsgType`,
`ControllerCommands`, `LogLevel`, `LightsCycle`, `CardPoint`, `ELStatus`, `CameraState`,
`CropMode`, `ImageMergeMode`).

## Hardware requirements

- **3D printer** — Ender 3 v3 SE (used as the moving stage; its extruder kit is removed and
  replaced by the anchor).
- **Anchor** — 3D-printed mount that replaces the extruder kit and attaches the camera and LED ring
  to the printer in place of it (see FreeCAD/STEP/G-code files in `addons/freecad/`). It is a core
  part of EnderLeaf itself.
- **Camera** — Raspberry Pi camera, driven via **Picamera2 / libcamera**.
- **LED ring** — ring of addressable (NeoPixel) LEDs driven **directly from the Raspberry Pi
  GPIO** via `neopixel` / `board` (circuitpython) in `enderscope/enderlights_pi.py`. If a ring of a
  different model/size is used, the LED count (`led_count` in `default_leds` and the `Enderlights`
  default) must be set in `enderscope/enderlights_pi.py`. The Arduino approach from the base
  Enderscope project (firmware in `addons/enderlights/enderlights.ino`, driver
  `enderscope/enderlights.py`) is **no longer used** by EnderLeaf.
- **Raspberry Pi 4** — Debian **Bookworm** (2024-07-04), running the node.
- **Leaf disc plate** — holds the disc grid (default 9×9) on top of the print bed.
- **Host computer (optional)** — runs the UI / main node and receives data. It can be replaced by
  one of the worker nodes; when a node also acts as the main node it needs a large hard drive
  attached (at least 2 TB).

![Leaf disc plate](addons/resources/disc_plate.png)

## Installation

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then (same steps on the
Raspberry Pi and on any other system):
```
git clone -b enderleaf https://github.com/tr31zh/EnderLeaf.git
cd EnderLeaf
uv venv --system-site-packages
uv sync
```
- `--system-site-packages` gives the environment access to the system's `picamera2` / `libcamera`
  on the Raspberry Pi; it is harmless elsewhere.
- Dependencies are declared in `pyproject.toml` (uv) and `requirements.txt` (uv-exported).
- Python **≥ 3.14**.
- Off-Pi, `picamera2` / `libcamera` imports fail and the code transparently falls back to
  `enderleaf/dummy_camera.py` (`simulate_camera = True`), so the controller can be developed and
  run without hardware.

## Running

### Node (on the Raspberry Pi)
```
cd enderleaf && python ws_node.py          # websocket server, default port 8765
# or, on the Pi:
scripts/launch_node.sh
```
`ws_node.py` reads an optional port from `sys.argv[1]` (default `8765`).

A node has no inputs to start itself, so on first installation add the following line to cron
(`crontab -e`) to launch the node automatically on reboot:
```
@reboot /home/ender/launch_node.sh
```

### UI / main node (a host computer or one of the worker nodes)
```
# Flet UI (desktop) from the enderleaf/ folder:
cd enderleaf && flet run ws_ui.py
# Flet UI (web):
cd enderleaf && flet run --web ws_ui.py
# or use the launch helpers:
scripts/launch_ui.sh       # Flet desktop
scripts/launch_web_ui.sh   # Flet web

# Panel UI — used only to test new features, never in production. It has no websocket capability,
# so it can only control the printer/camera/LED ring of the single EnderLeaf it is launched on:
python serve_enderleaf.py
```
Node addresses are read from `addons/conf/addresses.json`. When that file is absent/unreadable, the
UI falls back to **local development mode** (test nodes on sequential ports `8760..8763`, the UI
acting as the main node).

## Project layout

| Path | Purpose |
| --- | --- |
| `enderleaf/` | **Main project logic** — controller, websocket server (`ws_node.py`), UI (`ws_ui.py`, `enderleaf_ui.py`), image processing, QR, focus metrics, enums, constants, tools. |
| `enderscope/` | **Inherited Enderscope base** — serial/async stage control, bed geometry, scan patterns, LED ring drivers, legacy Panel UI. Treated as a stable base; prefer fixing the fork layer before changing it. |
| `addons/` | Non-Python assets: `conf/addresses.json` (node config, **per-installation, git-ignored** — copy `addresses.sample.json`), `diagrams/` (drawio), `freecad/` (`.FCStd`, `.step`, `.gcode`), `enderlights/enderlights.ino` (Arduino firmware, **no longer used** — the LED ring is driven from the Pi GPIO via `enderlights_pi.py`), `resources/` (documentation images), `tests/path2.gcode` (G-code sample; no automated test suite). |
| `demos/` | Low-level Jupyter demos (some inherited from the original Enderscope project). They illustrate low-level functionality and remain useful for understanding the code, even where they reflect the original project. |
| `notebooks/` | **Active dev/test area** for new features (also contains a Panel UI notebook for testing new features — never for production). Use for prototyping, not as source of truth. |
| `images/` | **Main-node data handoff**, not a source tree: `received/` (raw SFTP download) → `validated/` (content-verified, what `EnderLeafMl` reads) → `processed/` (after `EnderLeafMl`; not present until images are processed). |
| `output/` | **Per-node generated data** — each node writes its images and metadata here (`output/images/<exp>/<inoc>/`, `output/job_data/<exp>/<inoc>/`); files are imported to the main node and then removed from the source. |
| `scripts/` | Launch helpers: `launch_node.sh`, `launch_ui.sh`, `launch_web_ui.sh`. |
| `logs/` | Generated logs. |

## Configuration

Node addresses live in `addons/conf/addresses.json` — a **per-installation file that is git-ignored
and not shipped in the repository** (it holds the real, private IP addresses of your nodes). Each
lab / installation that deploys EnderLeaf creates its own copy. Start from the template
`addons/conf/addresses.sample.json` and fill in the addresses of your nodes:

```json
{
  "nodes": ["<ip-of-node-1>", "<ip-of-node-2>"],
  "main_node": "<either-true-or-false>"
}
```

The **`main_node`** flag selects which machine is the **main (coordinating) node** — the one that
pulls each worker's images/metadata over SFTP into `images/received/` → `validated/`. Set it to
`true` on the coordinating host and `false` (or leave it out) on the others; when it is not set the
machine is treated as **not main**. It does not depend on the OS or a fixed IP, so the main node can
be any host (a Mac, a Windows PC, or a Raspberry Pi) even when it reaches the network through a VPN.
In local development mode (no `addresses.json`) the UI is the main node.

When this file is missing or unreadable, the UI uses the local development fallback described under
[Running](#running). Key paths and constants come from `enderleaf/const.py`
(`PT_ADDONS`, `PT_CONF`, `PT_OUTPUT`, `PT_IMAGES*`, …).

## References

- **Upstream:** [Enderscope](https://github.com/mutterer/enderscope) — the project EnderLeaf is forked from.
- **Downstream:** `EnderLeafMl` — a separate repository that consumes `images/validated/` for
  reflection removal (per-channel merging), disc segmentation, and pathogen-resistance scoring.
- **LED ring driver:** `enderscope/enderlights_pi.py` — drives the NeoPixel ring directly from the
  Raspberry Pi GPIO (no Arduino). The Arduino firmware in `addons/enderlights/enderlights.ino` is
  inherited from the base Enderscope project and is **no longer used** by EnderLeaf.
- **Mechanical designs:** `addons/freecad/` (`.FCStd`, `.step`, `.gcode`).
- **Focus metrics:** adapted from
  [hoogenboom-group/focus-metrics](https://github.com/hoogenboom-group/focus-metrics)
  (`enderleaf/focus_metrics.py`).

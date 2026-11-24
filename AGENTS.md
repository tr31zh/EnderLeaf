# AGENTS.md

Guidance for AI coding agents working in the **EnderLeaf** repository.

## Project overview

EnderLeaf is a fork of [Enderscope](https://github.com/mutterer/enderscope). It turns an
**Ender 3 v3 SE 3D printer** into a **leaf-disc phenotyping robot**:

- The printer's **extruder kit** is removed and replaced by a 3D-printed **anchor** that mounts a
  **camera** and an **LED ring** (the anchor attaches to the printer in place of the extruder,
  never to it).
- A **Raspberry Pi** (Picamera2 / libcamera) drives the camera and the LED ring, and talks to the
  printer over **serial** (the stage).
- **Leaf discs** sit in a grid (default 9×9) on a plate placed on top of the print bed.
- The robot moves the camera over each disc and **acquires 4 images per disc, each under a different
  lighting configuration** (the four cardinal quarters N/E/S/W of the LED ring).
- EnderLeaf's job **stops at acquisition**: it captures and saves those 4 raw images (with metadata)
  per disc. **It does not merge them.** Reflection removal (per-channel `MIN` merging),
  disc segmentation, and pathogen-resistance scoring are performed downstream by a **separate
  project in another repository, `EnderLeafMl`**.
- A **UI on a main machine** controls one or many EnderLeaf nodes over websockets.

![EnderLeaf](./addons/resources/hardware_setup.png)

## Architecture

The system is **distributed / client-server**:

- **Node** (one per Raspberry Pi + printer): runs `enderleaf/ws_node.py`, a websocket **server**
  (default port **8765**) that owns an `EnderLeafController` and handles remote commands.
- **Main node / UI** (a dedicated host computer **or one of the worker nodes**): runs
  `enderleaf/ws_ui.py` (Flet desktop/web UI, the production UI) or `enderleaf/enderleaf_ui.py` +
  `serve_enderleaf.py` (Panel UI, used **only to test new features — never in production**). It is the
  **coordinating node** that connects to all worker nodes. Each **worker node saves its own images
  and metadata in its local `output/` folder**. The main node then **imports them over SFTP into
  `images/received/`** (raw download target); once a file's content is verified (CSV parsed / image
  decoded) it is **atomically moved to `images/validated/` and removed from the source node's
  `output/`**. The **`EnderLeafMl`** project then consumes `images/validated/` and moves processed
  results to `images/processed/`. When a worker node also acts as the main node it must have a large
  hard drive attached (at least 2 TB); a dedicated host computer is optional.
- The **Panel UI has no websocket capability**: it can only control the printer / camera / LED ring of
  the **single EnderLeaf it is launched on**, so it cannot coordinate other nodes. That is why it is
  for testing new features only — any multi-node run must use the Flet UI (`ws_ui.py`).
- **Configuration**: node addresses live in `addons/conf/addresses.json` (`{"nodes": [...]}`) — a
  **per-installation file that is git-ignored** (it holds real, private node IPs); copy
  `addons/conf/addresses.sample.json` for a new deployment. The `"main_node"` flag selects the
  **coordinating main node** (`MAIN_NODE = addresses.get("main_node", False)`; `True` in local
  `LOCAL_CLIENTS` mode); it does **not** use the OS or a fixed IP, so the main node can be any host
  even on a VPN. When the file is absent/unreadable, the UI falls back to `LOCAL_CLIENTS` mode (local
  test nodes on sequential ports `8760..8763`, UI acting as the main node).

The **control message protocol** is defined in `enderleaf/socket_message.py` (`SocketMessage`,
`SocketData`) and enumerated in `enderleaf/enums.py` (`MsgType`, `ControllerCommands`, `LogLevel`,
`LightsCycle`, `CardPoint`, `ELStatus`, `CameraState`, `CropMode`, `ImageMergeMode`).

## 4-image acquisition pipeline (core logic)

All in `enderleaf/enderleaf_ctrl.py`:

1. `launch_acquisition()` — top-level job: validates printer + free disk (`MIN_HD_SPACE`), iterates
   the plate grid (`parse_positions()`), moves the stage over each disc, and runs the light cycles.
2. For each disc it iterates `self.light_cycles` (default `[LightsCycle.ONE_FOURTH]`). A
   `LightsCycle` expands to a list of lighting configurations (`LIGHTS_CONF` in `enums.py`);
   `ONE_FOURTH` = 4 configs lighting N, E, S, W quarters separately.
3. `acquire_leaf_disc()` — for each lighting config: set lights, `capture_array()`, build a per-image
   metadata dict (experiment/inoculum/plate/row/col/timestamp + cardinal flags + camera metadata),
   and return the list of images + metadata + CSV DataFrame.
4. Images are written to `output/images/<exp>/<inoc>/` (PNG, RGB→BGR via OpenCV) and metadata to
   `output/job_data/<exp>/<inoc>/` (CSV via `tools.write_dataframe`).
5. EnderLeaf **does not merge or process the 4 images** — that downstream work (reflection removal
   via per-channel `MIN` merging, segmentation, and resistance scoring) is done by the
   separate **`EnderLeafMl`** repository. Note that merge helpers exist in this repo
   (`image.merge_images` / `merge_images_channels`, `ImageMergeMode`/`ImageMergeMethod` in
   `enums.py`) but are **not part of the acquisition pipeline** — keep that in mind when editing.

Key state on `EnderLeafController`: crop margins, `plate_x/y/row_count/col_count`,
`focus_start_z`/`focus_delta_z`, `led_ring` (an `Enderlights` instance), and `light_cycles`.

## Layout

| Path | Purpose |
| --- | --- |
| `enderleaf/` | **Main project logic** — controller, websocket server (`ws_node.py`), UI (`ws_ui.py`, `enderleaf_ui.py`), image processing, QR, focus metrics, enums, constants, tools. |
| `enderscope/` | **Inherited Enderscope logic** — serial/async stage control (`serial.py`, `async_serial.py`), bed geometry (`bed.py`), scan patterns (`scan_patterns.py`), LED ring drivers, Panel UI (`ui.py`). EnderLeaf uses **`enderlights_pi.py`** (NeoPixel ring driven from the Pi GPIO); `enderlights.py` (Arduino) is base-only and **not used**. Treated mostly as a stable base; prefer fixing the fork layer (`enderleaf/`) before changing this. |
| `addons/` | Non-Python assets: `conf/addresses.json` (node config, **per-installation, git-ignored** — copy `addresses.sample.json`), `diagrams/` (drawio), `freecad/` (`.FCStd`, `.step`, `.gcode`), `enderlights/enderlights.ino` (Arduino firmware, **no longer used** — the LED ring is driven from the Pi GPIO via `enderlights_pi.py`), `tests/path2.gcode` (G-code sample; no automated test suite). |
| `demos/` | Low-level Jupyter demos (some inherited from the original Enderscope project). They illustrate low-level functionality and remain useful for understanding the code. |
| `notebooks/` | **Active dev/test area** for new features (also contains a Panel UI notebook for testing new features — never for production). Use for prototyping, not as source of truth. |
| `images/` | **Main-node data handoff, not a source tree.** `received/` holds the raw SFTP download from each worker node's `output/`; once a file is content-verified it is atomically moved to `validated/` (and deleted from the source node's `output/`). `validated/` is what the downstream **`EnderLeafMl`** project reads; after processing it moves results to `processed/` (does not exist yet — no images processed so far). |
| `scripts/` | Launch helpers: `launch_node.sh` (node on Pi), `launch_ui.sh` (Flet desktop UI), `launch_web_ui.sh` (Flet web UI). |
| `output/` | **Per-node generated data** — each node writes its acquired images and metadata here (`output/images/<exp>/<inoc>/`, `output/job_data/<exp>/<inoc>/`). Files are imported to the main node and then removed from the source. |
| `logs/` | Generated logs. |

### Key `enderleaf/` modules

- `enderleaf_ctrl.py` — `EnderLeafController`: camera/lights/stage orchestration + the acquisition pipeline.
- `ws_node.py` — node websocket **server**; maps `ControllerCommands` → controller methods; default port `8765` (`main()` reads `sys.argv[1]`).
- `ws_ui.py` — Flet desktop/web UI that coordinates nodes (the distributed client + main node).
- `enderleaf_ui.py` — Panel-based UI (legacy/dev).
- `image.py` — `Rectangle`, cropping, `merge_images`/`merge_images_channels`, circle detection, color-space helpers.
- `acquire.py` — higher-level `capture` / `read_qr_code` helpers.
- `focus_metrics.py` — focus measure-of-merit functions (Brenner, GLVA, …) adapted from `hoogenboom-group/focus-metrics`.
- `qr_reader.py` — QR decoding (disc/plate identification).
- `draw.py`, `tools.py` — plotting and utility helpers (folder/file/move/format helpers).
- `const.py`, `enums.py` — constants (paths, colors, focus methods, HD threshold) and all enums.
- `dummy_camera.py` — **off-Pi simulation**: stubs `Picamera2`/`JpegEncoder`/`FileOutput` so the
  controller runs without hardware.

## Hardware / platform notes

- **On Raspberry Pi**: `picamera2` / `libcamera` are imported; the real camera and the `Enderlights`
  ring (`enderlights_pi.py`, NeoPixel LEDs driven from the Pi GPIO via `neopixel`/`board`) are
  used. EnderLeaf does **not** use an Arduino — the base project's `enderlights.py` + `enderlights.ino`
  Arduino path is **no longer used**. The LED-ring model/size is configured via `led_count` in
  `default_leds` and the `Enderlights` default in `enderscope/enderlights_pi.py`; a different ring
  requires changing those counts.
- **Main node**: the UI/main node can be a dedicated host computer **or one of the worker nodes**;
  a host computer is optional. When a worker node also acts as the main node it must have a large
  hard drive attached (at least 2 TB).
- **Off Pi (dev/desktop)**: those imports fail and the code transparently falls back to
  `dummy_camera` (`simulate_camera = True`); use this to run/develop without hardware.
- `enderleaf_ui.py` and others `try: from libcamera import controls except: pass` — do not assume
  `libcamera`/`picamera2` are importable on the dev host.
- Stage (printer) talks over **serial** at **115200** baud; default port via
  `enderscope.serial.default_printer_port()`.

## Running

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then (same on the Raspberry
Pi and on any other system — uv supports `--system-site-packages`):
```
git clone -b enderleaf https://github.com/tr31zh/EnderLeaf.git
cd EnderLeaf
uv venv --system-site-packages
uv sync
# run the node (default port 8765):
cd enderleaf && python ws_node.py
# or use scripts/launch_node.sh
```

**Autostart (nodes):** a node has no inputs to start itself, so on first installation add the
following line to cron (`crontab -e`) to launch the node automatically on reboot:
```
@reboot /home/ender/launch_node.sh
```

UI / main node (a host computer or one of the worker nodes):
```
cd enderleaf && flet run ws_ui.py          # Flet desktop
cd enderleaf && flet run --web ws_ui.py    # Flet web
# or use scripts/launch_ui.sh / scripts/launch_web_ui.sh

# Panel UI — used only to test new features, never in production. It has no websocket capability,
# so it can only control the printer/camera/LED ring of the single EnderLeaf it is launched on:
python serve_enderleaf.py
```

- Dependencies: `pyproject.toml` (uv) and `requirements.txt` (uv-exported).
- Python **≥ 3.14** (per `pyproject.toml`).
- `--system-site-packages` gives the environment access to the system's `picamera2` / `libcamera`
  on the Pi; it is harmless elsewhere.

## Conventions for agents

- **Edit the fork, not the base**: put new/changed behavior in `enderleaf/`; keep `enderscope/`
  changes minimal and only when strictly necessary.
- **Prefer simulation for dev**: validate logic off-Pi using `dummy_camera`; never require real
  hardware to make a change testable.
- **Async everywhere in the controller**: controller methods are `async`; respect the existing
  callback/socket-send pattern (`socket_send_*`, `maybe_method` decorator for error propagation).
- **Don't treat the `notebooks/*.ipynb` Panel UI as authoritative** — it reflects the old
  Enderscope project and is for testing new features only (never production). `demos/` illustrates
  low-level functionality and is still useful; `notebooks/` is for prototyping new features.
- **Config, don't hardcode**: node addresses belong in `addons/conf/addresses.json` (git-ignored,
  per-installation — start from `addresses.sample.json`); paths come from `enderleaf/const.py`
  (`PT_ADDONS`, `PT_CONF`, `PT_OUTPUT`, `PT_IMAGES*`, etc.).
- **No automated test suite** currently exists — when adding tests, extend `addons/tests/`.

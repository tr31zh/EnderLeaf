## EnderLeaf

### Installation

#### For a Raspberry Pi 4 with Debian Bookworm OS (Released 2024-07-04):
```
git clone -b enderleaf https://github.com/tr31zh/EnderLeaf.git
cd EnderLeaf
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
```
#### For any other system without picamera support
Install uv https://docs.astral.sh/uv/getting-started/installation/
```
git clone -b enderleaf https://github.com/tr31zh/EnderLeaf.git
cd EnderLeaf
uv init
uv sync
```

### Usage

From the demos folder
```
uv run jupyter lab
```
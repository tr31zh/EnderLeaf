## EnderLeaf

### Installation

For a Raspberry Pi 4 with Debian Bookworm OS (Released 2024-07-04):
- Install uv https://docs.astral.sh/uv/getting-started/installation/

```
git clone -b enderleaf https://github.com/tr31zh/EnderLeaf.git
cd EnderLeaf
uv init --python "python==3.13"
uv venv --system-site-packages
uv sync
```

Clone this repo and open the 'demo' notebook in JupyterLab. 


### Usage

From the main folder
```
uv run jupyter lab
```
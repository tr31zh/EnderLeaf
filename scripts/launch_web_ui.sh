#!/bin/bash

cd /home/ender/code/EnderLeaf
source .venv/bin/activate
cd enderleaf
flet run --web ws_ui.py

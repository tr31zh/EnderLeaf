from pathlib import Path
from enum import Enum
from typing import Literal

import numpy as np

import enderleaf.image as ei
import enderleaf.qr_reader as eqr
from enderleaf.const import FocusMode


def capture(
    resulution: tuple,
    focus_mode: Literal[FocusMode.MANUAL, FocusMode.HUNT, FocusMode.AUTO],
    crop_data: ei.Rectangle | None = None,
    **kwargs,
) -> np.array:
    match focus_mode:
        case FocusMode.MANUAL:
            pass
        case FocusMode.HUNT:
            pass
        case FocusMode.AUTO:
            pass
    return f"Captured image with resolution: {resulution} using {focus_mode.value} focus mode with crop {str(crop_data)}"


def read_qr_code(image: np.array, crop_data: ei.Rectangle | None):
    if crop_data is not None:
        image = ei.crop_image(image=image, crop_data=crop_data)
    qr_data = eqr.get_qr_data(image_object=image)
    return qr_data["info"] if qr_data["info"] else None

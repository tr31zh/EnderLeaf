from pathlib import Path

import numpy as np
import cv2
from PIL import Image

def to_pil(image, size: tuple = None) -> Image:
    """Converts image from OpenCV format to Pillow format

    Args:
        image (nd.array): Source image
        size (tuple, optional): Resize values. Defaults to None.

    Returns:
        Image: Pillow image
    """
    if image is None:
        return None
    ret = Image.fromarray(image)
    if size is not None:
        ret = ret.resize(size=size, resample=Image.Resampling.LANCZOS)
    return ret

def crop_image(image, crop_top=400, crop_bottom=400, crop_left=400, crop_right=400):
    height, width, _ = image.shape
    cy, cx = height // 2, width // 2
    return image[cy - crop_top : cy + crop_bottom, cx - crop_left : cx + crop_right]

def load_image(image_path: Path, rgb: bool = True, image_size: int = None) -> np.ndarray:
    try:
        image = cv2.imread(str(image_path))
        if rgb is True:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        if image_size is not None:
            image = cv2.resize(
                image, dsize=(image_size, image_size), interpolation=cv2.INTER_LANCZOS4
            )
        return image
    except Exception as e:
        print(image_path)
        print(f"Failed load image: {str(e)}")
        return None
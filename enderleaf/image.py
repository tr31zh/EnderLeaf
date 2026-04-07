from pathlib import Path
from dataclasses import dataclass

import numpy as np
import cv2
from PIL import Image


@dataclass
class Rectangle:
    top: int | None = None
    bottom: int | None = None
    left: int | None = None
    right: int | None = None

    def __repr__(self):
        return f"[left:[{self.left}]|right:[{self.right}]|top:[{self.top}]|bottom:[{self.bottom}]]"

    def empty(self) -> bool:
        return (
            self.top is None
            or self.bottom is None
            or self.left is None
            or self.right is None
            or self.top >= self.bottom
            or self.left >= self.right
        )

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top


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


def crop_image(image, crop_data: Rectangle = Rectangle()):
    return (
        image[crop_data.top : crop_data.bottom, crop_data.left : crop_data.right]
        if crop_data.empty() is False
        else image
    )


def crop_from_center(image, crop_data: Rectangle = Rectangle(400, 400, 400, 400)):
    height, width, _ = image.shape
    cy, cx = height // 2, width // 2
    return crop_image(
        image=image,
        crop_data=Rectangle(
            top=cy - crop_data.top,
            bottom=cy + crop_data.bottom,
            left=cx - crop_data.left,
            right=cx + crop_data.right,
        ),
    )


def load_image(
    image_path: Path, rgb: bool = True, image_size: int = None
) -> np.ndarray:
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

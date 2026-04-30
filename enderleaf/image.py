from pathlib import Path
from dataclasses import dataclass

import numpy as np
import cv2
from PIL import Image, ImageOps
from skimage import color
from skimage.transform import hough_circle, hough_circle_peaks


@dataclass
class Rectangle:
    top: int | None = None
    bottom: int | None = None
    left: int | None = None
    right: int | None = None

    def __repr__(self):
        return (
            f"left:{self.left}|right:{self.right}|top:{self.top}|bottom:{self.bottom}"
        )

    def empty(self) -> bool:
        return (
            self.top is None
            or self.bottom is None
            or self.left is None
            or self.right is None
            or self.top >= self.bottom
            or self.left >= self.right
        )

    def shrink(self, new_width, new_height):
        return self.__class__(
            top=self.cy - new_height / 2,
            bottom=self.cy + new_height / 2,
            left=self.cx - new_width / 2,
            right=self.cx + new_width / 2,
        )

    def ensure_int(self):
        return self.__class__(
            top=int(round(self.top)),
            bottom=int(round(self.bottom)),
            left=int(round(self.left)),
            right=int(round(self.right)),
        )

    def to_cv(self, image, color, thickness):
        return cv2.rectangle(
            image.copy(),
            (self.left, self.top),
            (self.right, self.bottom),
            color,
            thickness,
        )

    @property
    def width(self):
        return self.right - self.left

    @property
    def height(self):
        return self.bottom - self.top

    @property
    def cx(self):
        return self.left + self.width / 2

    @property
    def cy(self):
        return self.top + self.height / 2


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


def get_channels(image, color_space):
    """Get all channels from a color space

    Args:
        image (np.ndarray): Source RGB image
        color_space (str): color space

    Raises:
        NotImplementedError: Unknown color space

    Returns:
        tuple: channels
    """
    if color_space.lower() == "rgb":
        return cv2.split(image)
    elif color_space.lower() == "hsv":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2HSV))
    elif color_space.lower() == "yiq":
        return [
            ((c - np.min(c)) / (np.max(c) - np.min(c)) * 255).astype(np.uint8)
            for c in cv2.split(np.array(color.rgb2yiq(to_pil(image))))
        ]
    elif color_space.lower() == "lab":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_BGR2LAB))
    else:
        raise NotImplementedError(f"Unknown color space {color_space}")


def get_channel(image: np.ndarray, color_space: str, channel: str) -> np.ndarray:
    """Extract channel from image

    Args:
        image (np.ndarray): Source image
        color_space (str): Color space
        channel (str): channel

    Raises:
        NotImplementedError: Checks that channel and color space are supported

    Returns:
        np.ndarray: Channel
    """
    channels = get_channels(image=image, color_space=color_space)
    if channel.lower() in ["red", "h", "y", "l"]:
        return channels[0]
    if channel.lower() in ["green", "s", "i", "a"]:
        return channels[1]
    if channel.lower() in ["blue", "v", "q", "b"]:
        return channels[2]
    else:
        raise NotImplementedError(
            f"Unknown combination color space {color_space}, channel {channel}"
        )


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
    height, width, _ = image.shape
    return image[
        crop_data.top : (
            crop_data.bottom if crop_data.bottom > 0 else height + crop_data.bottom
        ),
        crop_data.left : (
            crop_data.right if crop_data.right > 0 else width + crop_data.right
        ),
    ]


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


def safe_pil_resize(image: Image, new_width, new_height):
    return ImageOps.contain(image=image, size=(new_width, new_height))


def lap_var(img):
    laplacian = cv2.Laplacian(img, cv2.CV_32F)
    variance = laplacian.var()
    return variance


def canny(
    image, color_space, channel, min_thresholf=100, max_threshold=200, aperture=3
):
    return cv2.Canny(
        cv2.normalize(
            get_channel(image=image, color_space=color_space, channel=channel),
            None,
            alpha=0,
            beta=200,
            norm_type=cv2.NORM_MINMAX,
        ),
        min_thresholf,
        max_threshold,
        None,
        aperture,
    )


def find_circles(edges, radii, max_circles: int = 3):
    accus, xs, ys, raddi = hough_circle_peaks(
        hough_circle(edges, radii),
        radii,
        total_num_peaks=max_circles,
        min_xdistance=round(radii.max() * 1.8),
        min_ydistance=round(radii.max() * 1.8),
    )
    return [[a, x, y, r] for a, x, y, r in zip(accus, xs, ys, raddi)]


def filter_circles(circles, img_width, img_height):
    accepted, discarded_position, discarded_accu = [], [], []
    for accu, cx, cy, r in circles:
        if cx + r > img_width or cx - r < 0 or cy + r > img_height or cy - r < 0:
            discarded_position.append([accu, cx, cy, r])
        elif accu < 0.15:
            discarded_accu.append([accu, cx, cy, r])
        else:
            accepted.append([accu, cx, cy, r])
    return {
        "accepted": accepted,
        "discarded_position": discarded_position,
        "discarded_accu": discarded_accu,
    }

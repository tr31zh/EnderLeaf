from pathlib import Path
from dataclasses import dataclass
from enum import Enum
from typing import Literal
import base64

import numpy as np
import cv2
from PIL import Image, ImageOps

from scipy.spatial.transform import Rotation as R
from skimage import color
from skimage.transform import hough_circle, hough_circle_peaks
from skimage.feature import SIFT, match_descriptors

from enderleaf.const import COLOR_SPACES
from enderleaf.enums import ImageMergeMode


@dataclass
class Rectangle:
    """Represents a rectangular region of an image"""
    top: int | None = None
    bottom: int | None = None
    left: int | None = None
    right: int | None = None

    def __repr__(self):
        return (
            f"left:{self.left}|right:{self.right}|top:{self.top}|bottom:{self.bottom}"
        )

    @classmethod
    def from_circle(cls, circle):
        """Creates a Rectangle that bounds a given circle

        Args:
            circle (tuple): Circle as (cx, cy, r).

        Returns:
            Rectangle: Rectangle bounding the circle.
        """
        cx, cy, r = circle
        return cls(cy - r, cy + r, cx - r, cx + r)

    def empty(self) -> bool:
        """Checks if the rectangle is empty or invalid

        Returns:
            bool: True if the rectangle is empty or invalid.
        """
        return (
            self.top is None
            or self.bottom is None
            or self.left is None
            or self.right is None
            or self.top >= self.bottom
            or self.left >= self.right
        )

    def shrink(self, new_width, new_height):
        """Returns a new rectangle centered at the same position but with specified dimensions

        Args:
            new_width (int): New width of the rectangle.
            new_height (int): New height of the rectangle.

        Returns:
            Rectangle: New rectangle with the specified dimensions.
        """
        return self.__class__(
            top=self.cy - new_height / 2,
            bottom=self.cy + new_height / 2,
            left=self.cx - new_width / 2,
            right=self.cx + new_width / 2,
        )

    def ensure_int(self):
        """Returns a new rectangle with all coordinates rounded to the nearest integer

        Returns:
            Rectangle: New rectangle with integer coordinates.
        """
        return self.__class__(
            top=int(round(self.top)),
            bottom=int(round(self.bottom)),
            left=int(round(self.left)),
            right=int(round(self.right)),
        )

    def to_cv(self, image, color, thickness):
        """Draws the rectangle on a copy of the provided image using OpenCV

        Args:
            image (np.ndarray): Image to draw on.
            color (tuple): Color of the rectangle.
            thickness (int): Thickness of the rectangle border.

        Returns:
            np.ndarray: Image with the rectangle drawn.
        """
        return cv2.rectangle(
            image.copy(),
            (self.left, self.top),
            (self.right, self.bottom),
            color,
            thickness,
        )

    @property
    def width(self):
        """Returns the width of the rectangle

        Returns:
            int: Width of the rectangle.
        """
        return self.right - self.left

    @property
    def height(self):
        """Returns the height of the rectangle

        Returns:
            int: Height of the rectangle.
        """
        return self.bottom - self.top

    @property
    def cx(self):
        """Returns the x-coordinate of the rectangle's center

        Returns:
            float: X-coordinate of the rectangle's center.
        """
        return self.left + self.width / 2

    @property
    def cy(self):
        """Returns the y-coordinate of the rectangle's center

        Returns:
            float: Y-coordinate of the rectangle's center.
        """
        return self.top + self.height / 2


def load_image(
    image_path: Path, rgb: bool = True, image_size: int = None
) -> np.ndarray:
    """Loads an image from a file path, optionally converts to RGB and resizes

    Args:
        image_path (Path): Path to the image file
        rgb (bool, optional): If True, converts BGR to RGB. Defaults to True.
        image_size (int, optional): If provided, resizes image to (image_size, image_size). Defaults to None.

    Returns:
        np.ndarray: Loaded image or None if failure occurs
    """
    image = cv2.imread(str(image_path))
    if rgb is True:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    if image_size is not None:
        # Use LANCZOS4 interpolation for high-quality downsampling
        image = cv2.resize(
            image, dsize=(image_size, image_size), interpolation=cv2.INTER_LANCZOS4
        )
    return image

def encode_image(img, format=".jpg") -> str:
    """Encodes an OpenCV image to a base64 string for transmission

    Args:
        img (np.ndarray): Source image
        format (str, optional): Image format (e.g. ".jpg", ".png"). Defaults to ".jpg".

    Returns:
        str: Base64 encoded image string
    """
    success, encoded = cv2.imencode(format, img)
    if not success:
        raise ValueError("Image encoding failed")
    return base64.b64encode(encoded.tobytes()).decode("utf-8")

def get_channels(image, color_space, rgb_source:bool=True):
    """Get all channels from a color space

    Args:
        image (np.ndarray): Source RGB image
        color_space (str): color space
        rgb_source (bool): True if source is RGB false if BGR, defaults to True

    Raises:
        NotImplementedError: Unknown color space

    Returns:
        tuple: channels
    """
    if color_space.lower() == "rgb":
        return cv2.split(image)
    elif color_space.lower() == "hsv":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_RGB2HSV))
    elif color_space.lower() == "yiq":
        return [
            ((c - np.min(c)) / (np.max(c) - np.min(c)) * 255).astype(np.uint8)
            for c in cv2.split(np.array(color.rgb2yiq(to_pil(image))))
        ]
    elif color_space.lower() == "lab":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_RGB2LAB))
    elif color_space.lower() == "yuv":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_RGB2YUV))
    elif color_space.lower() == "ycrcb":
        return cv2.split(cv2.cvtColor(image, cv2.COLOR_RGB2YCrCb))
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
    if channel.lower() in ["r", "h", "y", "l"]:
        return channels[0]
    if channel.lower() in ["g", "s", "i", "a", "u", "cr"]:
        return channels[1]
    if channel.lower() in ["b", "v", "q", "b", "cb"]:
        return channels[2]
    else:
        raise NotImplementedError(
            f"Unknown combination color space {color_space}, channel {channel}"
        )


def equalize_hist(image, color_space):
    """Equalizes the histogram of the luminance channel in a specified color space

    Args:
        image (np.ndarray): Source image
        color_space (str): Color space for equalization ("hsv", "lab", "yuv", "ycrcb")

    Returns:
        np.ndarray: Image with equalized luminance channel in BGR format
    """
    assert color_space in ["hsv", "lab", "yuv", "ycrcb"]
    if color_space == "hsv":
        # Equalize the Value channel in HSV
        h, s, v = get_channels(image=image, color_space=color_space)
        return cv2.cvtColor(cv2.merge([h, s, cv2.equalizeHist(v)]), cv2.COLOR_HSV2BGR)
    else:
        # Equalize the Luminance channel in other spaces
        l, c1, c2 = get_channels(image=image, color_space=color_space)
        return cv2.cvtColor(
            cv2.merge([cv2.equalizeHist(l), c1, c2]),
            (
                cv2.COLOR_LAB2BGR
                if color_space == "lab"
                else cv2.COLOR_YUV2BGR if color_space == "yuv" else cv2.COLOR_YCrCb2BGR
            ),
        )


def to_pil(image, size: tuple = None) -> Image:
    """Converts image from OpenCV (RGB) format to Pillow format

    Args:
        image (np.ndarray): Source RGB image
        size (tuple, optional): Resize values. Defaults to None.

    Returns:
        PIL.Image: Pillow image
    """
    if image is None:
        return None
    ret = Image.fromarray(image)
    if size is not None:
        ret = ret.resize(size=size, resample=Image.Resampling.LANCZOS)
    return ret


def crop_image(image, crop_data: Rectangle = Rectangle()):
    """Crops an image based on a Rectangle object, supporting negative indices for relative cropping

    Args:
        image (np.ndarray): Source image
        crop_data (Rectangle): Rectangle defining the crop region. Defaults to an empty Rectangle.

    Returns:
        np.ndarray: Cropped image
    """
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
    """Crops an image centered around its middle point using the provided offsets

    Args:
        image (np.ndarray): Source image
        crop_data (Rectangle): Offsets from center. Defaults to a 400x400 center crop.

    Returns:
        np.ndarray: Centered cropped image
    """
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
    """Resizes a PIL image to fit within specified dimensions while maintaining aspect ratio

    Args:
        image (PIL.Image): Source image.
        new_width (int): Target width.
        new_height (int): Target height.

    Returns:
        PIL.Image: Resized image.
    """
    return ImageOps.contain(image=image, size=(new_width, new_height))


def merge_images(
    image_list: list,
    merge_mode: Literal[
        ImageMergeMode.MIN,
        ImageMergeMode.MAX,
        ImageMergeMode.MEDIAN,
        ImageMergeMode.AVG,
    ],
):
    """Merges a list of images using a specified aggregation mode

    Args:
        image_list (list): List of images (np.ndarray) to merge
        merge_mode (Literal): The aggregation mode (MIN, MAX, MEDIAN, AVG)

    Returns:
        np.ndarray: Merged image
    """
    if len(image_list) == 1:
        result = image_list[0]
    else:
        match merge_mode:
            case ImageMergeMode.MIN:
                result = np.minimum(image_list[0], image_list[1])
                if len(image_list) == 2:
                    return result
                for img in image_list[2:]:
                    result = np.minimum(img, result)
            case ImageMergeMode.MAX:
                result = np.maximum(image_list[0], image_list[1])
                if len(image_list) == 2:
                    return result
                for img in image_list[2:]:
                    result = np.maximum(img, result)
            case ImageMergeMode.AVG:
                result = np.mean(image_list, axis=0).astype(np.uint8)
            case ImageMergeMode.MEDIAN:
                result = np.median(image_list, axis=0).astype(np.uint8)
            case _:
                raise NotImplementedError(f"Unknown mode '{merge_mode}")

    return result


def merge_images_channels(image_list: list, color_space: list, merge_modes: list):
    """Merges multiple images by applying specific merge modes to each channel of a chosen color space

    Args:
        image_list (list): List of images to merge
        color_space (str): Target color space
        merge_modes (list): List of merge modes, one for each channel of the color space

    Returns:
        np.ndarray: Merged image converted back to RGB
    """
    result = cv2.merge(
        [
            merge_images(
                image_list=[
                    get_channel(image=image, color_space=color_space, channel=channel)
                    for image in image_list
                ],
                merge_mode=method,
            )
            for channel, method in zip(COLOR_SPACES[color_space], merge_modes)
        ]
    )
    if color_space == "rgb":
        pass
    elif color_space == "hsv":
        result = cv2.cvtColor(result, cv2.COLOR_HSV2RGB)
    elif color_space == "yiq":
        raise NotImplementedError("Non conversion available for YIQ")
    elif color_space == "lab":
        result = cv2.cvtColor(result, cv2.COLOR_LAB2RGB)
    elif color_space == "yuv":
        result = cv2.cvtColor(result, cv2.COLOR_YUV2RGB)
    elif color_space == "ycrcb":
        result = cv2.cvtColor(result, cv2.COLOR_YCrCb2RGB)
        
    return result


def canny(
    image, color_space, channel, min_thresholf=100, max_threshold=200, aperture=3
):
    """Detects edges in a specific channel of an image using the Canny algorithm

    Args:
        image (np.ndarray): Source image
        color_space (str): Color space to analyze
        channel (str): Channel to use for edge detection
        min_thresholf (int, optional): Lower threshold for Canny. Defaults to 100.
        max_threshold (int, optional): Upper threshold for Canny. Defaults to 200.
        aperture (int, optional): Sobel operator aperture. Defaults to 3.

    Returns:
        np.ndarray: Binary edge map
    """
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
    """Finds candidate circles using the Hough Circle Transform on an edge map

    Args:
        edges (np.ndarray): Binary edge image
        radii (np.ndarray): Array of radii to search for
        max_circles (int, optional): Maximum number of circles to detect. Defaults to 3.

    Returns:
        list: List of detected circles as [accumulator, x, y, radius]
    """
    accus, xs, ys, raddi = hough_circle_peaks(
        hough_circle(edges, radii),
        radii,
        total_num_peaks=max_circles,
        min_xdistance=round(radii.max() * 1.8),
        min_ydistance=round(radii.max() * 1.8),
    )
    return [[a, x, y, r] for a, x, y, r in zip(accus, xs, ys, raddi)]


def filter_circles(circles, img_width, img_height):
    """Filters detected circles based on image boundaries and accumulator strength

    Args:
        circles (list): List of candidate circles [accu, x, y, r]
        img_width (int): Width of the image
        img_height (int): Height of the image

    Returns:
        dict: Categories of circles: "accepted", "discarded_position", "discarded_accu"
    """
    accepted, discarded_position, discarded_accu = [], [], []
    for accu, cx, cy, r in circles:
        # Check if circle is outside image boundaries
        if cx + r > img_width or cx - r < 0 or cy + r > img_height or cy - r < 0:
            discarded_position.append([accu, cx, cy, r])
        # Check if accumulator value is too low (not a strong enough circle)
        elif accu < 0.15:
            discarded_accu.append([accu, cx, cy, r])
        else:
            accepted.append([accu, cx, cy, r])
    return {
        "accepted": accepted,
        "discarded_position": discarded_position,
        "discarded_accu": discarded_accu,
    }


def get_circles(
    image,
    color_space: str = "hsv",
    channel: str = "s",
    min_threshold=100,
    max_threshold=200,
    aperture=3,
    max_circles=3,
    resize_factor=4,
    normalize: bool = True,
    median_blur=7,
    radii_data: tuple = (450, 550, 20),
):
    """Full pipeline to detect and filter circles in an image

    Args:
        image (np.ndarray): Source image
        color_space (str, optional): Color space to use. Defaults to "hsv".
        channel (str, optional): Channel to use. Defaults to "s".
        min_threshold (int, optional): Lower threshold for Canny. Defaults to 100.
        max_threshold (int, optional): Upper threshold for Canny. Defaults to 200.
        aperture (int, optional): Sobel operator aperture. Defaults to 3.
        max_circles (int, optional): Maximum number of circles to detect. Defaults to 3.
        resize_factor (int, optional): Downsample factor. Defaults to 4.
        normalize (bool, optional): Whether to normalize the image. Defaults to True.
        median_blur (int, optional): Median blur kernel size. Defaults to 7.
        radii_data (tuple, optional): (min_radius, max_radius, step). Defaults to (450, 550, 20).

    Returns:
        dict: Detected circles (scaled back to original size) and the edge map used
    """
    # Downsample image for faster circle detection
    im_width = image.shape[1] // resize_factor
    im_height = image.shape[0] // resize_factor
    image = cv2.resize(image, (im_width, im_height))
    if normalize is True:
        image = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    if median_blur > 1:
        image = cv2.medianBlur(image, ksize=median_blur)
    edges = canny(
        image=image,
        color_space=color_space,
        channel=channel,
        min_thresholf=min_threshold,
        max_threshold=max_threshold,
        aperture=aperture,
    )
    circles = filter_circles(
        find_circles(
            edges=edges,
            radii=np.arange(
                radii_data[0] // resize_factor,
                radii_data[1] // resize_factor,
                radii_data[2] // resize_factor,
            ),
            max_circles=max_circles,
        ),
        img_width=im_width,
        img_height=im_height,
    )
    # Scale coordinates back to original image size
    return {
        k: [
            [a, x * resize_factor, y * resize_factor, r * resize_factor]
            for a, x, y, r in v
        ]
        for k, v in circles.items()
    } | {"edges": edges}


def crop_best_circle(
    image,
    color_space,
    channel,
    min_threshold=100,
    max_threshold=200,
    aperture=3,
    max_circles=3,
    resize_factor=4,
    normalize: bool = True,
    median_blur=7,
    radii_data: tuple = (450, 550, 20),
):
    """Tries to detect circles and crop the image around the only accepted circle found

    Args:
        image (np.ndarray): Source image
        color_space (str, optional): Color space to use. Defaults to "hsv".
        channel (str, optional): Channel to use. Defaults to "s".
        min_threshold (int, optional): Lower threshold for Canny. Defaults to 100.
        max_threshold (int, optional): Upper threshold for Canny. Defaults to 200.
        aperture (int, optional): Sobel operator aperture. Defaults to 3.
        max_circles (int, optional): Maximum number of circles to detect. Defaults to 3.
        resize_factor (int, optional): Downsample factor. Defaults to 4.
        normalize (bool, optional): Whether to normalize the image. Defaults to True.
        median_blur (int, optional): Median blur kernel size. Defaults to 7.
        radii_data (tuple, optional): (min_radius, max_radius, step). Defaults to (450, 550, 20).

    Returns:
        np.ndarray: Cropped image if exactly one circle is accepted, otherwise original image
    """
    circles = get_circles(**locals())
    if len(circles["accepted"]) == 1:
        accu, cx, cy, r = circles["accepted"][0]

        # Crop with a small padding (16px) around the circle
        return crop_image(image, Rectangle.from_circle((cx, cy, r + 16)))
    else:
        return image


def rotate_image(image, angle):
    """Rotates an image around its center by the given angle (degrees)

    Args:
        image (np.ndarray): Source image
        angle (float | None): Rotation angle. None or 0 results in no rotation.

    Returns:
        np.ndarray: Rotated image
    """
    if angle in [None, 0]:
        return image
    else:
        h, w = image.shape[:2]
        return cv2.warpAffine(
            image, cv2.getRotationMatrix2D((w // 2, h // 2), angle, 1.0), (w, h)
        )


def find_matches(
    previous_image,
    current_image,
    previous_mask=None,
    current_mask=None,
    safe_ratio: float = 0.5,
):
    """Finds corresponding feature matches between two images using SIFT

    Args:
        previous_image (np.ndarray): First image
        current_image (np.ndarray): Second image
        previous_mask (np.ndarray, optional): Mask for first image. Defaults to None.
        current_mask (np.ndarray, optional): Mask for second image. Defaults to None.
        safe_ratio (float, optional): Ratio to filter matches by distance. Defaults to 0.5.

    Returns:
        tuple: (matches_previous, matches_current) as np.ndarray of keypoints
    """
    desc_extractor = SIFT()
    # Process previous image: mask, grayscale, and histogram equalization
    masked_previous_image = cv2.equalizeHist(
        cv2.cvtColor(
            cv2.bitwise_and(previous_image, previous_image, mask=previous_mask),
            cv2.COLOR_RGB2GRAY,
        )
    )
    desc_extractor.detect_and_extract(masked_previous_image)
    kp_previous = desc_extractor.keypoints
    desc_previous = desc_extractor.descriptors
    # Process current image: mask, grayscale, and histogram equalization
    masked_current_image = cv2.equalizeHist(
        cv2.cvtColor(
            cv2.bitwise_and(current_image, current_image, mask=current_mask),
            cv2.COLOR_RGB2GRAY,
        )
    )
    desc_extractor.detect_and_extract(masked_current_image)
    kp_current = desc_extractor.keypoints
    desc_current = desc_extractor.descriptors
    # Find and filter matches based on cross-check and distance ratio
    matches = match_descriptors(
        desc_previous, desc_current, max_ratio=0.8, cross_check=True
    )
    matches_previous = kp_previous[matches[:, 0]]
    matches_current = kp_current[matches[:, 1]]
    distances = np.array(
        [np.linalg.norm(p1 - p2) for p1, p2 in zip(matches_previous, matches_current)]
    )
    median_dist = np.median(distances)
    if median_dist == 0:
        median_dist = np.median(np.unique(distances))
    matches_filter = (distances <= median_dist / safe_ratio) & (
        distances >= median_dist * safe_ratio
    )
    matches_previous = matches_previous[matches_filter]
    matches_current = matches_current[matches_filter]

    return matches_previous, matches_current


def find_rotation_anlge(
    previous_image,
    current_image,
    previous_mask=None,
    current_mask=None,
    safe_ratio: float = 0.5,
):
    """Estimates the rotation angle between two images using feature match alignment

    Args:
        previous_image (np.ndarray): First image
        current_image (np.ndarray): Second image
        previous_mask (np.ndarray, optional): Mask for first image. Defaults to None.
        current_mask (np.ndarray, optional): Mask for second image. Defaults to None.
        safe_ratio (float, optional): Ratio to filter matches by distance. Defaults to 0.5.

    Returns:
        float: Estimated rotation angle in degrees
    """
    matches_previous, matches_current = find_matches(
        previous_image=previous_image,
        previous_mask=previous_mask,
        current_image=current_image,
        current_mask=current_mask,
        safe_ratio=safe_ratio,
    )
    # Align vectors to find the rotation matrix
    rot, *_ = R.align_vectors(
        np.pad(
            matches_previous - matches_previous.mean(axis=0),
            pad_width=[0, 1],
            mode="constant",
        ),
        np.pad(
            matches_current - matches_current.mean(axis=0),
            pad_width=[0, 1],
            mode="constant",
        ),
        return_sensitivity=True,
    )
    return rot.as_euler("zyx", degrees=True)[0]


def match_previous_rotation(
    previous_image,
    current_image,
    previous_mask=None,
    current_mask=None,
    safe_ratio: float = 0.5,
):
    """Aligns the current image rotation to match the previous image's orientation

    Args:
        previous_image (np.ndarray): First image
        current_image (np.ndarray): Second image
        previous_mask (np.ndarray, optional): Mask for first image. Defaults to None.
        current_mask (np.ndarray, optional): Mask for second image. Defaults to None.
        safe_ratio (float, optional): Ratio to filter matches by distance. Defaults to 0.5.

    Returns:
        np.ndarray: Rotated current image aligned to previous image
    """
    angle = find_rotation_anlge(
        previous_image=previous_image,
        previous_mask=previous_mask,
        current_image=current_image,
        current_mask=current_mask,
        safe_ratio=safe_ratio,
    )
    return rotate_image(current_image, angle=angle)

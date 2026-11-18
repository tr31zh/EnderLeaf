import logging
from pathlib import Path

import numpy as np
import cv2

from qreader import QReader

from enderleaf.enums import LogLevel
from enderleaf.image import load_image

logger = logger = logging.getLogger(__name__)


def empty_qr():
    """Returns a default empty QR result structure"""
    return {"retval": False, "info": [], "points": []}


def get_points_extremes(points):
    """Calculates the bounding box coordinates from a set of points

    Args:
        points (np.ndarray): Array of points

    Returns:
        tuple: (min_x, min_y, max_x, max_y) as integers
    """
    min_x, min_y = points.min(axis=0)
    max_x, max_y = points.max(axis=0)
    return int(min_x), int(min_y), int(max_x), int(max_y)


def check_qr_code(qr_data, need_info: bool = True):
    """Validates if a QR detection result is considered successful

    Args:
        qr_data (dict): Result dictionary from QR detection
        need_info (bool, optional): If True, requires that at least one non-empty info string exists. Defaults to True.

    Returns:
        bool: True if the QR code is valid according to criteria
    """
    return (
        (
            qr_data["retval"] is True
            and len([i for i in qr_data["info"] if i]) > 0
            and len(qr_data["points"]) > 0
        )
        if need_info is True
        else qr_data["retval"] is True and len(qr_data["points"]) > 0
    )


def draw_qr_data(image, points, info):
    """Draws the detected QR code bounding box, corners and decoded info on the image

    Args:
        image (np.ndarray): Target image to draw on
        points (tuple): Bounding box extremes (min_x, min_y, max_x, max_y)
        info (str): Decoded QR information to display as text
    """
    min_x, min_y, max_x, max_y = points
    cv2.rectangle(image, (min_x, min_y), (max_x, max_y), (0, 128, 0), 5)
    for point in points:
        cv2.circle(image, [int(c) for c in point], 10, (0, 0, 255), -1)
    tcx = [int(c) for c in [min_x, min_y]]
    return cv2.putText(image, info, tcx, cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 4)


def get_qr_data_qr(image_object: Path | str | np.ndarray, **kwargs):
    """Detects and decodes QR codes using the qreader library

    Args:
        image_object (Path | str | np.ndarray): Image source (path or numpy array)
        **kwargs: Additional arguments passed to qreader

    Returns:
        dict: Result containing retval, decoded info, and bounding box points
    """
    qreader = QReader()
    detection_result = qreader.detect(image_object)
    qr_info = qreader.detect_and_decode(image_object)

    return {
        "retval": qr_info is not None and len(qr_info) > 0,
        "info": [qi if qi is not None else "" for qi in qr_info],
        "points": (
            [detection_result[0]["bbox_xyxy"].astype(int)]
            if len(detection_result) > 0
            else []
        ),
    }


def get_qr_data_cv2(
    image_object: Path | str | np.ndarray, safe_pad=100, sharpen_image: bool = False
):
    """Detects and decodes QR codes using OpenCV's QRCodeDetector

    Args:
        image_object (Path | str | np.ndarray): Image source (path or numpy array)
        safe_pad (int, optional): Padding around the detected QR. Defaults to 100.
        sharpen_image (bool, optional): If True, applies a sharpening filter to the image. Defaults to False.

    Returns:
        dict: Result containing retval, decoded info, and bounding box extremes
    """
    image = (
        image_object
        if isinstance(image_object, np.ndarray) is True
        else load_image(image_path=image_object)
    )
    if sharpen_image is True:
        # Apply a simple Laplacian-like sharpening filter
        image = cv2.filter2D(image, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
    retval, decoded_info, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(image)
    point_data = []
    info_data = []

    if retval:
        for info, qr_points in zip(decoded_info, points):
            point_data.append(qr_points)
            info_data.append(info)
    return {
        "retval": retval,
        "info": info_data,
        "points": [get_points_extremes(p) for p in np.asarray(point_data)],
    }


async def get_qr_data(
    image_object: Path | str | np.ndarray,
    safe_pad=100,
    sharpen_image: bool = False,
    call_back=None,
    need_info: bool = True,
):
    """Asynchronously retrieves QR data, trying OpenCV first and falling back to QReader if needed

    Args:
        image_object (Path | str | np.ndarray): Image source (path or numpy array)
        safe_pad (int, optional): Padding around detected QR. Defaults to 100.
        sharpen_image (bool, optional): If True, applies sharpening. Defaults to False.
        call_back (callable, optional): Async function for logging messages. Defaults to None.
        need_info (bool, optional): If True, requires decoded text info. Defaults to True.

    Returns:
        dict: QR result containing retval, info, and points
    """
    try:
        qr_data = get_qr_data_cv2(
            image_object=image_object, safe_pad=safe_pad, sharpen_image=sharpen_image
        )
    except Exception as e:
        await call_back(
            level=LogLevel.WARNING, message=f"Unable to read QR code: {str(e)}"
        )
        qr_data = empty_qr()
    # If OpenCV failed or didn't meet requirements, try the alternative QReader
    if check_qr_code(qr_data=qr_data, need_info=need_info) is False:
        err_msg = "Unable to detect QR code with OpenCV, trying alternative."
        if call_back is None:
            logging.warning(err_msg)
        else:
            await call_back(level=LogLevel.WARNING, message=err_msg)
        qr_data = get_qr_data_qr(image_object=image_object)
    # Log success or final failure
    if check_qr_code(qr_data=qr_data, need_info=need_info) is True:
        ok_msg = "Successfully detected QR code with QRReader."
        if call_back is None:
            logging.info(ok_msg)
        else:
            await call_back(level=LogLevel.INFO, message=ok_msg)
    else:
        err_msg = "Unable to detect QR code with QRReader, exiting."
        if call_back is None:
            logging.error(err_msg)
        else:
            await call_back(level=LogLevel.WARNING, message=err_msg)
    return qr_data


def get_qr_viz(
    image_object: Path | str | np.ndarray, safe_pad=100, sharpen_image: bool = False
):
    """Generates a visualization image with detected QR codes drawn on it

    Args:
        image_object (Path | str | np.ndarray): Image source (path or numpy array)
        safe_pad (int, optional): Padding around detected QR. Defaults to 100.
        sharpen_image (bool, optional): If True, applies sharpening. Defaults to False.

    Returns:
        np.ndarray: Image with QR bounding boxes and info drawn
    """
    data = get_qr_data(
        image_object=image_object, safe_pad=safe_pad, sharpen_image=sharpen_image
    )

    image = (
        image_object
        if isinstance(image_object, np.ndarray) is True
        else load_image(image_path=image_object)
    )
    if data["retval"]:
        for info, qr_points in zip(data["info"], data["points"]):
            image = draw_qr_data(image=image, points=qr_points, info=info)
    return image

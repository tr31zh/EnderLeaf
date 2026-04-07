from pathlib import Path

import numpy as np
import cv2

from enderleaf.image import load_image


def get_points_extremes(points):
    min_x, min_y = points.min(axis=0)
    max_x, max_y = points.max(axis=0)
    return int(min_x), int(min_y), int(max_x), int(max_y)


def draw_qr_data(image, points, info):
    min_x, min_y, max_x, max_y = get_points_extremes(points)
    cv2.rectangle(image, (min_x, min_y), (max_x, max_y), (0, 128, 0), 5)
    for point in points:
        cv2.circle(image, [int(c) for c in point], 10, (0, 0, 255), -1)
    tcx = [int(c) for c in [min_x, min_y]]
    return cv2.putText(image, info, tcx, cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 0, 0), 4)


def get_qr_data(
    image_object: Path | str | np.ndarray,
    safe_pad=100,
    sharpen_image: bool = False,
    allow_self_code: bool = True,
):
    image = (
        image_object
        if isinstance(image_object, np.ndarray) is True
        else load_image(image_path=image_object)
    )
    if sharpen_image is True:
        image = cv2.filter2D(image, -1, np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]]))
    retval, decoded_info, points, _ = cv2.QRCodeDetector().detectAndDecodeMulti(image)
    point_data = []
    info_data = []

    if retval:
        for info, qr_points in zip(decoded_info, points):
            if not info and allow_self_code is True:
                min_x, min_y, max_x, max_y = get_points_extremes(qr_points)
                min_x, min_y, max_x, max_y = (
                    min_x - safe_pad,
                    min_y - safe_pad,
                    max_x + safe_pad,
                    max_y + safe_pad,
                )
                cropped_ret_data = get_qr_data(
                    image[min_y:max_y, min_x:max_x], allow_self_code=False
                )
                if cropped_ret_data["retval"]:
                    cropped_ret_data["points"][:, :, 0] += min_x
                    cropped_ret_data["points"][:, :, 1] += min_y
                    for cropped_info, cropped_qr_points in zip(
                        cropped_ret_data["info"], cropped_ret_data["points"]
                    ):
                        info_data.append(cropped_info)
                        point_data.append(cropped_qr_points)
            else:
                point_data.append(qr_points)
                info_data.append(info)
                image = draw_qr_data(image=image, points=qr_points, info=info)
    return {
        "retval": retval,
        "info": info_data,
        "points": np.asarray(point_data),
    }


def get_qr_viz(
    image_object: Path | str | np.ndarray, safe_pad=100, sharpen_image: bool = False
):
    data = get_qr_data(
        image_object=image_object,
        safe_pad=safe_pad,
        sharpen_image=sharpen_image,
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

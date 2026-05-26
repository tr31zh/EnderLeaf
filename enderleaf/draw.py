import numpy as np

import pandas as pd
import cv2

import albumentations as A

from matplotlib.figure import Figure
from matplotlib.patches import Circle
import plotly.express as px
import altair as alt
import seaborn as sns

from enderleaf.const import COLOR_SPACES, C_CBF_RED, C_CBF_GREEN, C_CBF_BLUE
from enderleaf.image import get_channels


def plot_image_to_ax(ax, image, title=None, fontsize=18):
    """Plot image to existing ax and set title

    Args:
        ax (Matplotlib ax): target ax
        image (Opencv Image): source image
        title (str, optional): image title. Defaults to None.
        fontsize (int, optional): Font size in matplotlib scale. Defaults to 18.
    """
    if len(image.shape) == 2:
        ax.imshow(image, cmap="gray")
    else:
        ax.imshow(image)
    if title is not None:
        ax.set_title(title, fontsize=fontsize)
    ax.set_axis_off()


def image_grid(
    images: list,
    row_count: int,
    col_count: int | None = None,
    figsize: tuple | int = (20, 20),
) -> Figure:
    """Builds image mosaic in Matplotlib Figure

    Args:
        images (list): List of images
        row_count (int): Row count
        col_count (int | None, optional): Column count. Defaults to None.
        figsize (tuple, optional): Figure size. Defaults to (20, 20).

    Returns:
        Figure: Matplotlib figure with images
    """
    col_count = row_count if col_count is None else col_count
    fig = Figure(
        figsize=(
            figsize
            if isinstance(figsize, tuple)
            else (col_count * figsize, row_count * figsize)
        )
    )
    axii = fig.subplots(nrows=row_count, ncols=col_count)
    for ax, image in zip(axii.flatten(), images):
        try:
            plot_image_to_ax(ax=ax, image=image)
        except:
            ax.set_axis_off()
    for ax in axii.flatten():
        ax.set_axis_off()

    fig.tight_layout()
    return fig


def draw_circles(image, circles, thickness=12):
    out = image.copy()
    for _, cx, cy, r in circles["accepted"]:
        out = cv2.circle(out, (cx, cy), r, (0, 0, 0), thickness=thickness)
    for _, cx, cy, r in circles["discarded_position"]:
        out = cv2.circle(out, (cx, cy), r, (255, 0, 0), thickness=thickness // 2)
    for accu, cx, cy, r in circles["discarded_accu"]:
        _ = cv2.circle(out, (cx, cy), r, (255, 0, 255), thickness=thickness // 2)
    return out


def resize_with_padding(
    image: np.ndarray,
    max_size: int,
    out_width: int | None = None,
    out_height: int | None = None,
    interpolation: int = cv2.INTER_CUBIC,
) -> np.ndarray:
    return A.Compose(
        [
            A.LongestMaxSize(max_size=max_size, p=1, interpolation=interpolation),
            A.PadIfNeeded(
                min_height=max_size if out_height is None else out_height,
                min_width=max_size if out_width is None else out_width,
                p=1,
                border_mode=cv2.BORDER_CONSTANT,
            ),
        ]
    )(image=image)["image"]


def hconcat_resize_min(
    im_list: list, interpolation: int = cv2.INTER_CUBIC, height: int | None = None
) -> np.ndarray:
    """Horizontally concatenate a list of images

    Args:
        im_list (list): List of images in OpenCV format
        interpolation (int, optional): Interpolation method. Defaults to cv2.INTER_CUBIC.
        height (int | None, optional): Output image height. Defaults to None.

    Returns:
        np.ndarray: Concatenated image
    """
    h_min = min(im.shape[0] for im in im_list) if height is None else height
    im_list_resize = [
        resize_with_padding(
            image=im if len(im.shape) == 3 else cv2.merge([im, im, im]),
            max_size=h_min,
            out_width=int(im.shape[1] * h_min / im.shape[0]),
            out_height=h_min,
            interpolation=interpolation,
        )
        for im in im_list
    ]
    return cv2.hconcat(im_list_resize)


def vconcat_resize_min(
    im_list: list, interpolation: int = cv2.INTER_CUBIC, width: int | None = None
) -> np.ndarray:
    """Vertically concatenate a list of images

    Args:
        im_list (list): List of images in OpenCV format
        interpolation (int, optional): Interpolation method. Defaults to cv2.INTER_CUBIC.
        width (int | None, optional): Output image width. Defaults to None.

    Returns:
        np.ndarray: Concatenated image
    """
    w_min = min(im.shape[1] for im in im_list) if width is None else width
    im_list_resize = [
        resize_with_padding(
            image=im if len(im.shape) == 3 else cv2.merge([im, im, im]),
            max_size=w_min,
            out_width=w_min,
            out_height=int(im.shape[0] * w_min / im.shape[1]),
            interpolation=interpolation,
        )
        for im in im_list
    ]
    return cv2.vconcat(im_list_resize)


def concat_tile_resize(
    im_list_2d: list,
    interpolation=cv2.INTER_CUBIC,
    width: int | None = None,
    height: int | None = None,
) -> np.ndarray:
    """Builds mosaic image from list of images

    Args:
        im_list_2d (list): List of images
        interpolation (int, optional): Interpolation mode. Defaults to cv2.INTER_CUBIC.
        width (int | None, optional): Mosaic width. Defaults to None.
        height (int | None, optional): Mosaic height. Defaults to None.

    Returns:
        np.ndarray: Mosaic
    """
    im_list_v = [
        hconcat_resize_min(im_list_h, interpolation=interpolation, height=height)
        for im_list_h in im_list_2d
    ]
    return vconcat_resize_min(im_list_v, interpolation=interpolation, width=width)


def plot_focus_plotly(df, width: int = 400):
    fig = px.line(
        pd.melt(df, id_vars=["z"]),
        x="z",
        y="value",
        color="variable",
        markers=True,
        width=width,
    )
    fig.update_traces(mode="markers+lines", hovertemplate=None)
    fig.update_layout(hovermode="x unified")
    return fig


def plot_focus_altair(df, width: int = 200, height=200):
    df_melted = pd.melt(df, id_vars=["z"])
    base = alt.Chart(df_melted).encode(x="z")
    columns = sorted(df_melted.variable.unique())
    selection = alt.selection_point(
        fields=["z"], nearest=True, on="mouseover", empty="none", clear="mouseout"
    )

    lines = base.mark_line().encode(y="value", color="variable")
    points = lines.mark_circle(size=100)  # .transform_filter(selection)

    rule = (
        base.transform_pivot("variable", value="value", groupby=["z"])
        .mark_rule()
        .encode(
            opacity=alt.condition(selection, alt.value(0.3), alt.value(0)),
            tooltip=[alt.Tooltip(c, type="quantitative") for c in columns],
        )
        .add_params(selection)
        .properties(width=width, height=height)
    )

    return lines + points + rule


def plot_focus_plt(df, width: int = 200):
    df_melted = pd.melt(df, id_vars=["z"])
    fig = Figure(figsize=(4, 4))
    ax = fig.subplots(nrows=1, ncols=1)
    return sns.lineplot(
        data=df_melted,
        x="z",
        y="value",
        hue="variable",
        markers=True,
        dashes=False,
        ax=ax,
    )
    return fig


def plot_images_with_histograms(
    images: list,
    color_spaces: list = ["rgb"],
    fig_height: int = 4,
    titles: list | None = None,
):
    fig = Figure(
        figsize=(len(images) * fig_height, fig_height * (len(color_spaces) + 1))
    )
    axii = fig.subplots(nrows=1 + len(color_spaces), ncols=len(images))
    for idx, image in enumerate(images):
        ax_img = axii[0, idx] if len(images) > 1 else axii[0]
        ax_hists = axii[1:, idx] if len(images) > 1 else axii[1:]

        ax_img.imshow(image)
        if titles is not None and len(titles) > idx:
            ax_img.set_title(titles[idx])
        ax_img.set_axis_off()

        for ax_hist, color_space in zip(ax_hists, color_spaces):
            for color_value, color_label, channel in zip(
                [C_CBF_RED, C_CBF_GREEN, C_CBF_BLUE],
                COLOR_SPACES[color_space],
                get_channels(image=image, color_space=color_space),
            ):
                histr = cv2.calcHist([channel], [0], None, [256], [0, 256])
                ax_hist.plot(
                    histr, color=np.array(color_value) / 255, label=color_label
                )
            ax_hist.set_axis_off()
            if idx == 0:
                ax_hist.legend()

    fig.tight_layout()
    return fig

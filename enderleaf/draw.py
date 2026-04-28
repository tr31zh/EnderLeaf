from matplotlib.figure import Figure

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

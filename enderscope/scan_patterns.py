import numpy as np
import pandas as pd

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle, Circle
from matplotlib.figure import Figure

from enderscope.bed import bed


def raster(cols=4, rows=3):
    return np.array(list((x, y) for y in range(rows) for x in range(cols)))


def snake(cols=4, rows=3):
    return np.array(
        list(
            (x, y)
            for y in range(rows)
            for x in range(
                (cols - 1) * (y % 2),
                cols - (cols + 1) * (y % 2),
                ((y + 1) % 2) - 1 * ((y % 2)),
            )
        )
    )


def rnd_mvt(num_points=10, seed=1):
    x_min, x_max = 0, 180  # Range for x values
    y_min, y_max = 0, 180  # Range for y values
    np.random.seed(seed)
    return np.column_stack(
        (
            np.random.uniform(x_min, x_max, num_points),
            np.random.uniform(y_min, y_max, num_points),
        )
    )


def spiral(num_points=50):
    directions = np.array([[1, 0], [0, 1], [-1, 0], [0, -1]])
    d = 0
    i = 1
    p = np.array([0, 0])
    sp = np.array([p])
    while len(sp) < num_points:
        for j in range(i):
            p = p + directions[d]
            sp = np.append(sp, [p], axis=0)
        d = (d + 1) % 4
        i = i + (d % 2 == 0)
    return np.array(sp[:num_points])


def plot_path(
    path=np.array([[0, 0]]),
    labels=True,
    field=(10, 10),
    title="Path preview",
    selected_rectangles: list | None = None,
    circle_diam: float | None = None,
):
    x = path[:, 0]
    y = path[:, 1]
    field = Rectangle((0, 0), field[0], field[1])
    rectangle = Rectangle(
        (0, 0),
        bed.x_max,
        bed.y_max,
        edgecolor="green",
        facecolor="#00ff0010",
        linewidth=1,
    )
    plt.gca().add_patch(rectangle)
    plt.plot(x, y, marker="x")
    plt.axis("equal")
    ticks = np.arange(-25, bed.x_max - 10, 25)
    plt.xticks(ticks)
    plt.yticks(ticks)
    plt.grid(linestyle="--", linewidth=0.7, alpha=0.7)
    plt.xlim(bed.x_min - 10, bed.x_max + 10)
    plt.ylim(bed.y_min - 10, bed.y_max + 10)
    plt.xlabel("x axis")
    plt.ylabel("y axis")
    plt.title(title)
    if labels:
        for idx, (x_pos, y_pos) in enumerate(zip(x, y)):
            plt.text(
                x_pos,
                y_pos,
                str(idx + 1),
                fontsize=10,
                color="gray",
                ha="right",
                va="bottom",
            )
            if selected_rectangles is None or idx + 1 in selected_rectangles:
                f = Rectangle(
                    (x_pos - field.get_width() / 2, y_pos - field.get_height() / 2),
                    field.get_width(),
                    field.get_height(),
                    edgecolor="red",
                    facecolor="none",
                    linewidth=0.25 if selected_rectangles is None else 2,
                )
                plt.gca().add_patch(f)
            if circle_diam is not None:
                c = Circle(
                    xy=(x_pos, y_pos),
                    radius=circle_diam / 2,
                    edgecolor="green",
                    facecolor="none",
                    linewidth=1,
                )
                plt.gca().add_patch(c)


def plot_path_status(
    path: np.ndarray | None = None,
    title="PAth status",
    circle_diam: float | None = None,
    highlighted_indexes: int | list | None = None,
):
    fig = Figure(figsize=(4, 4))
    ax = fig.subplots(nrows=1, ncols=1)
    ax.add_patch(
        Rectangle(
            (0, 0),
            bed.x_max,
            bed.y_max,
            edgecolor="green",
            facecolor="#00ff0005",
            linewidth=1,
        )
    )
    ax.axis("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(bed.x_min, bed.x_max)
    ax.set_ylim(bed.y_min, bed.y_max)
    ax.set_xlabel("x axis")
    ax.set_ylabel("y axis")
    ax.set_title(title)
    if path is None:
        return fig
    x = path[:, 0]
    y = path[:, 1]
    ax.plot(x, y, marker=".")
    for idx, (x_pos, y_pos) in enumerate(zip(x, y)):
        if highlighted_indexes is None:
            facecolor = "none"
        elif isinstance(highlighted_indexes, list):
            facecolor = "lightgreen" if idx in highlighted_indexes else "none"
        else:
            facecolor = (
                "white"
                if idx < highlighted_indexes
                else "lightgreen" if highlighted_indexes == idx else "lightblue"
            )
        if circle_diam is not None:
            ax.add_patch(
                Circle(
                    xy=(x_pos, y_pos),
                    radius=circle_diam / 2,
                    edgecolor="green",
                    facecolor=facecolor,
                    linewidth=1,
                )
            )

    return fig


def plot_discs_status(
    path: np.ndarray | None = None,
    title="Disc status",
    circle_diam: float = 17,
    good_discs: list = [],
    bad_discs: list = [],
):
    fig = Figure(figsize=(4, 4))
    ax = fig.subplots(nrows=1, ncols=1)
    ax.add_patch(
        Rectangle(
            (0, 0),
            bed.x_max,
            bed.y_max,
            edgecolor="green",
            facecolor="#00ff0005",
            linewidth=1,
        )
    )
    ax.axis("equal")
    ax.set_xticks([])
    ax.set_yticks([])
    ax.set_xlim(bed.x_min, bed.x_max)
    ax.set_ylim(bed.y_min, bed.y_max)
    ax.set_xlabel("x axis")
    ax.set_ylabel("y axis")
    ax.set_title(title)
    if path is None:
        return fig
    x = path[:, 0]
    y = path[:, 1]
    ax.plot(x, y, marker=".")
    for idx, (x_pos, y_pos) in enumerate(zip(x, y)):
        if circle_diam is not None:
            ax.add_patch(
                Circle(
                    xy=(x_pos, y_pos),
                    radius=circle_diam / 2,
                    edgecolor="blue",
                    facecolor=(
                        "green"
                        if idx in good_discs
                        else "red" if idx in bad_discs else "lavender"
                    ),
                    linewidth=1,
                )
            )

    return fig


def get_extremes(positions):
    df_pos = pd.DataFrame(positions, columns=["x", "y"])
    df_min_x = df_pos[df_pos.x == df_pos.x.min()].sort_values("y")
    df_max_x = df_pos[df_pos.x == df_pos.x.max()].sort_values("y")
    return [df_min_x.iloc[0], df_min_x.iloc[-1], df_max_x.iloc[-1], df_max_x.iloc[0]]

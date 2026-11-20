import os
from pathlib import Path
from timeit import default_timer as timer
from datetime import datetime as dt
import psutil
import math
import stat
from collections import defaultdict

import paramiko

import pandas as pd

from enderleaf.const import TIME_FORMAT


def format_time(seconds):
    """Transforms seconds in human readable time string

    Arguments:
        seconds {float} -- seconds to convert

    Returns:
        string -- seconds as human readable string
    """

    mg, sg = divmod(seconds, 60)
    hg, mg = divmod(mg, 60)
    return "{:02.0f}:{:02.0f}:{:02.3f}".format(hg, mg, sg)


def time_method(f):
    """Decorator: prints execution time

    Arguments:
        f {function} -- function to decorate

    Returns:
        function -- created function
    """

    def new_function(*args, **kwargs):
        before = timer()
        x = f(*args, **kwargs)
        after = timer()
        print('"{}" process time = {}'.format(f.__name__, format_time(after - before)))
        return x

    return new_function


def format_datetime(t=None, time_format=TIME_FORMAT):
    return dt.now().strftime(time_format) if t is None else t.strftime(time_format)


def ensure_folder(forced_path: Path | str, return_string: bool = False) -> str | Path:
    """Ensures that forced_path exists

    Args:
        forced_path (Path): Target path
        return_string (bool, optional): If true return path to folder as string if not as Path. Defaults to False.

    Returns:
        str | Path: Path to created folder
    """
    if isinstance(forced_path, str):
        forced_path = Path(forced_path)
    if forced_path.is_dir() is False:
        forced_path.mkdir(parents=True, exist_ok=True)
    return str(forced_path) if return_string is True else forced_path


def read_dataframe(path: Path, sep: str = ";") -> pd.DataFrame:
    """Read dataframe from disc

    Args:
        path (Path): Path to dataframe
        sep (str, optional): Separator. Defaults to ";".

    Returns:
        pd.DataFrame: Read dataframe
    """
    return pd.read_csv(filepath_or_buffer=str(path), sep=sep)


def write_dataframe(df: pd.DataFrame, path: Path, sep: str = ";") -> pd.DataFrame:
    """Write dataframe to disc

    Args:
        df (pd.DataFrame): Dataframe
        path (Path): Path
        sep (str, optional): Separator. Defaults to ";".

    Returns:
        pd.DataFrame: Written dataframe
    """
    ensure_folder(path.parent)
    df.to_csv(path_or_buf=path, sep=sep, index=False)
    return df


def get_available_hd():
    return psutil.disk_usage(".").free / 1024**3


def scaled_rgb(mag, color_min, color_max):
    """Return a tuple of integers, as used in AWT/Java plots."""
    try:
        x = float(mag - color_min) / (color_max - color_min)
    except ZeroDivisionError:
        x = 0.5  # color_max == color_min
    blue = min((max((4 * (0.75 - x), 0.0)), 1.0))
    red = min((max((4 * (x - 0.25), 0.0)), 1.0))
    green = min((max((4 * math.fabs(x - 0.5) - 1.0, 0.0)), 1.0))
    return int(red * 255), int(green * 255), int(blue * 255)


def build_color_steps(step_count: int) -> list:
    """
    Builds a list containing step_count different colors from blue to yellow
    :param step_count:
    :return:
    """
    if step_count <= 1:
        return [(255, 0, 255)]
    else:
        return [
            scaled_rgb(mag=i, color_min=0, color_max=step_count)
            for i in range(step_count)
        ]


def list_remote_files(sftp: paramiko.sftp_client.SFTPClient, path: str):
    result = []
    for item in sftp.listdir_attr(path):
        if stat.S_ISREG(item.st_mode):
            result.append(Path(path).joinpath(item.filename))
        elif stat.S_ISDIR(item.st_mode):
            result.extend(
                list_remote_files(
                    sftp=sftp, path=str(Path(path).joinpath(item.filename))
                )
            )
        else:
            pass
    return result

def split_file_list(files:list[Path])->defaultdict:
    result = defaultdict(list)
    for file in files:
        result[file.suffix].append(file)
    return result


def list_remote_folders(sftp: paramiko.sftp_client.SFTPClient, path: str):
    result = [Path(path)]
    for item in sftp.listdir_attr(path):
        if stat.S_ISDIR(item.st_mode):
            result.extend(
                list_remote_folders(
                    sftp=sftp, path=str(Path(path).joinpath(item.filename))
                )
            )

    return result


def fsync_dir(path: Path | str):
    """Flush a directory entry, making a create/rename inside it durable.

    Directory metadata (a new file or a rename) is not guaranteed on disk until
    the directory itself is synced. This is required so that a validated copy
    survives a power loss that happens between writing it and deleting the
    remote source.

    Args:
        path (Path | str): The directory to flush.
    """
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_file_content(path: Path | str):
    """Validate that a downloaded file holds readable content of its type.

    CSV files are parsed and image files are decoded; an undecodable or
    unparseable file raises so the caller can discard it instead of promoting
    it to the validated folder.

    Args:
        path (Path | str): Path to the downloaded file to validate.

    Raises:
        ValueError: When the file cannot be decoded/parsed or its type is not
            handled.
    """
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        read_dataframe(path)
    elif suffix in (".png", ".jpg", ".jpeg", ".tif", ".tiff"):
        # Imported lazily to avoid a tools -> image import cycle at module load.
        from enderleaf.image import load_image

        image = load_image(path)
        # A failed decode returns None rather than raising, so treat None as a
        # hard error.
        if image is None:
            raise ValueError(f"Failed to decode image '{path}'")
    else:
        raise ValueError(f"Unrecognised file type '{suffix}' for '{path}'")


def atomic_move(from_path: Path | str, to_path: Path | str):
    """Move a file to a new location and make the move durable.

    Performs an atomic rename followed by a directory sync so the destination
    path is guaranteed to exist on stable storage before the caller proceeds
    (for example, before deleting the remote source).

    Args:
        from_path (Path | str): The file to move.
        to_path (Path | str): The destination path.
    """
    os.replace(str(from_path), str(to_path))
    fsync_dir(to_path.parent)

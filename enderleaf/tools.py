from pathlib import Path
from timeit import default_timer as timer
from datetime import datetime as dt

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


def ensure_folder(forced_path: Path, return_string: bool = False) -> str | Path:
    """Ensures that forced_path exists

    Args:
        forced_path (Path): Target path
        return_string (bool, optional): If true return path to folder as string if not as Path. Defaults to False.

    Returns:
        str | Path: Path to created folder
    """
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

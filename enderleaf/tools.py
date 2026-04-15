from pathlib import Path
from timeit import default_timer as timer
from datetime import datetime as dt


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


def format_datetime(t=dt.now()):
    return t.strftime("%Y%m%d%H%M%S")


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

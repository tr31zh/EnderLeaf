from timeit import default_timer as timer


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
"""A collection of useful Git(hub) related functions"""

from collections import namedtuple, OrderedDict
import datetime
from hashlib import sha1
import inspect
import os
import re
import sys
import time


def github_token():
    try:
        return os.environ["GITHUB_TOKEN"]
    except KeyError:
        raise RuntimeError("GITHUB_TOKEN env var not found, please set it")


def utf8(s):
    return s.encode("utf-8")


def generateCacheId(entries):
    h = sha1()
    for k, v in entries:
        h.update(k)
        h.update(str(v))
    return h.hexdigest()


def calculateMessageHash(message):
    # Anything which can resemble a hash or a date is filtered out.
    subbed = re.sub("[0-9a-f-A-F]", "", message)
    sortedSubbed = sorted(subbed.split("\n"))
    sha = sha1("\n".join(sortedSubbed))
    return sha.hexdigest()[0:10]


def parseGithubRef(s):
    # repo#pr_n@commit_ref
    # e.g. foo/bar#100@4787895789324784
    repo_name = re.split("[@#]", s)[0]
    commit_ref = s.split("@")[1] if "@" in s else "master"
    pr_n = re.split("[@#]", s)[1] if "#" in s else None
    return (repo_name, pr_n, commit_ref)


def github_datetime(utc_dt_str):
    def _utc_hours_offset():
        local_now_hour = time.localtime().tm_hour
        utc_now_hour = time.gmtime().tm_hour
        return local_now_hour - utc_now_hour

    def _to_utc_timetuple(utc_dt_str):
        utc_regex = re.compile(("(\d{4})-"  # year
                                "(\d{2})-"  # month
                                "(\d{2})"   # day
                                "T"
                                "(\d{2}):"  # hour
                                "(\d{2}):"  # mins
                                "(\d{2})"   # secs
                                "Z"))
        dt_info = utc_regex.match(utc_dt_str).groups()
        return tuple([int(i) for i in dt_info])

    def _to_datetime(utc_str, as_utc=False):
        if utc_str is None:
            return None

        utc_tt = _to_utc_timetuple(utc_str)
        dt = datetime.datetime(*utc_tt)
        if not as_utc:
            # get as local time
            dt += datetime.timedelta(hours=_utc_hours_offset())
        return dt

    def _to_epoch(dt):
        return int(dt.strftime("%s"))

    GithubDateTime = namedtuple("GithubDateTime", "epoch epoch_utc dt dt_utc")

    if utc_dt_str is None:
        return GithubDateTime(None, None, None, None)

    local_dt = _to_datetime(utc_dt_str, as_utc=False)
    utc_dt = _to_datetime(utc_dt_str, as_utc=True)
    local_epoch = _to_epoch(local_dt)
    utc_epoch = _to_epoch(utc_dt)

    return GithubDateTime(local_epoch, utc_epoch, local_dt, utc_dt)


def trace(func):
    """Simple function to trace enter and exit of a function/method
    and the args/kws that were passed. It is activated by -d/--debug
    being present in the sys.argv list, otherwise just returns the
    un-decorated function.
    """
    sysargs = sys.argv[1:]
    if not ('--debug' in sysargs or '-d' in sysargs):
        return func

    def _(txt=func.__name__, enter=True):
        prefix = "==> " if enter else "<== "
        return '{0} {1}\n'.format(prefix, txt)

    def examine(d):
        m = ''
        for i, (k, v) in enumerate(d.items(), 1):
            m += '  [{0}] {1}({2}), value: {3}\n'.format(i, k, type(v), str(v))
        return m

    def wrapped(*args, **kws):
        argspec = inspect.getargspec(func)
        path = None
        if argspec.args and argspec.args[0] == 'self':
            # this func is actually an instance method
            inst = args[0]
            path = inst.__module__
            path += '.' + inst.__class__.__name__
            path += ':' + func.__name__ + '():'
        else:
            path = func.__module__ + '.' + func.__name__ + '():'

        m = _(path)

        if args:
            m += '{0} *args:\n'.format(len(args))
            m += examine(OrderedDict(zip(argspec.args, args)))

        if kws:
            m += '{0} **kws:\n'.format(len(kws))
            m += examine(kws)

        print(m)
        try:
            retval = func(*args, **kws)
            print(_(path, enter=False))
        except:
            print('Exception caught, re-raising.')
            print(_(path, enter=False))
            raise
        else:
            return retval
    return wrapped

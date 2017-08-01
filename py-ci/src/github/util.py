"""A collection of useful Git(hub) related functions"""

from collections import OrderedDict
from hashlib import sha1
import inspect
import os
import re
import sys


def github_token():
    try:
        return os.environ["GITHUB_TOKEN"]
    except KeyError:
        raise RuntimeError("GITHUB_TOKEN env var not found, please set it")


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

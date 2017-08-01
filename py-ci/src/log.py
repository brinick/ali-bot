import datetime
import functools
import multiprocessing
import os
import sys
import time


# The global queue on which all processes put their logging messages
logging_queue = None
logging_process = None


def start():
    global logging_queue
    global logging_process
    if not logging_queue:
        logging_queue = multiprocessing.Queue()
    if not logging_process:
        logging_process = LogAccumulator()
        logging_process.start()


def stop():
    global logging_queue
    global logging_process

    # flush any last log messages
    waitedFor = 0
    while waitedFor < 30 and not logging_queue.empty():
        time.sleep(1)
        waitedFor += 1

    logging_queue.close()
    logging_queue.join_thread()

    logging_process.terminate()


def ging():
    """Context manager to start and stop the logging background process.
    For nerdy fun, it is recommended to call this with its namespace prefix
    thusly:
        with log.ging():
    """
    start()
    try:
        yield
    finally:
        stop()


class LogAccumulator(multiprocessing.Process):
    """The process which pulls all the logging messages from the
    global logging_queue and writes out to a single file.
    """

    def __init__(self, logfile="ci.log"):
        super(LogAccumulator, self).__init__(name="log_fetcher")
        self.shutdown = False
        self.logfile = "{0}.{1}".format(int(time.time()), logfile)

    def run(self):
        while not self.shutdown:
            lines = []
            while not logging_queue.empty():
                lines.append(logging_queue.get())
            self.dump(lines)
            time.sleep(3)

    def dump(self, lines):
        if not lines:
            return
        with open(self.logfile, 'a') as logfile:
            for line in lines:
                logfile.write(line + "\n")


class NullLog(object):
    """Throw everything away."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, name):
        def devnull(*args, **kws):
            pass
        return devnull


class Log(object):
    """Calls to debug, info etc logging methods of this class will
    write to the global multiprocessing logging queue.
    """

    def __init__(self, name):
        self.name = name

    def write(self, level, msg, exc_info=False):
        level = level.upper()
        when = datetime.datetime.now().isoformat()
        prefix = "::".join([when, self.name, str(os.getpid()), level])
        line = "[{0}] {1}".format(prefix, msg)
        logging_queue.put(line)

        # send to stdout/err as well
        # log level warning and above to sys.stderr, otherwise stdout
        stream = sys.stdout if level in ("debug", "info") else sys.stderr
        stream.write(line + "\n")

    def __getattr__(self, name):
        if name in ("debug", "info", "warning", "error", "fatal"):
            return functools.partial(self.write, name)

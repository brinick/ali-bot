import os
import time

from sh import Command as ShellCommand
from sh import ErrorReturnCode
from sh import SignalException_SIGTERM as SIGTERM
from sh import SignalException_SIGKILL as SIGKILL
import tasks


class ForkedCommand(tasks.Task):
    """Run a given sh.Command in its own process and put the results
    on a multiprocessing.Queue.
    """

    def __init__(self, cmd, results_queue, message_queue_pair=None):
        super(ForkedCommand, self).__init__(cmd.name, message_queue_pair)
        self.cmd = cmd
        self.results_queue = results_queue

    def run(self):
        self.setStart()
        try:
            self.cmd.run()
        except:
            pass

        self.results_queue.put({
            "exitcode": self.cmd.exitcode,
            "ok": (self.cmd.exitcode == 0),
            "sigkill": (self.cmd.exitcode == -9),
            "sigterm": (self.cmd.exitcode == -15),
            "out": self.cmd.out,
            "err": self.cmd.err
        })

        self.waitForQueueToEmpty(self.results_queue)

    def waitForQueueToEmpty(self, queue, maxWait=60):
        """If we don't wait for the other end of the queue
        to grab the data, it will be lost when this process exits,
        so we wait. But we limit the wait (default:1 min) to avoid
        blocking forever, for example if the process on the other end
        of the queue is dead.
        """
        waited = 0
        while waited < maxWait and not queue.empty():
            time.sleep(1)
            waited += 1


class Command(object):
    """Run a sh.Command"""

    def __init__(self, executable, *args, **envs):
        """Pass in the name of a system executable e.g ls,
        or the path to a local executable, and any arguments
        with which it should be invoked. If envs are provided
        they will be exported within the sub-shell prior to
        invoking the command.
        """
        self.name = os.path.basename(executable.__name__)
        self.executable = ShellCommand(executable)
        self.args = args
        self.envs = envs

    def pre_exec(self):
        pass

    def _exec(self):
        envs = os.environ.copy()
        envs.update(self.envs)

        try:
            result = self.executable(*self.args, _env=envs)
            self.exitcode = 0
            self.err = result.stderr
            self.out = result.stdout
        except (ErrorReturnCode, SIGTERM, SIGKILL) as e:
            # non-zero exit code
            self.exitcode = e.exit_code
            self.err = e.stderr
            self.out = e.stdout

        self.ok = (self.exitcode == 0)

    def post_exec(self):
        pass

    def run(self):
        self.pre_exec()
        self._exec()
        self.post_exec()
        return self.ok

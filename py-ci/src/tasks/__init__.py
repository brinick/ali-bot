import heapq
import importlib
import itertools
import multiprocessing
import os
import time

import log
import messages.queue

import psutil


def available(reverse=False):
    """Return the list of available tasks to run.
    If reverse is True, the run-order is reversed.
    Each item of the list is the name of a directory
    in the tasks parent directory.
    """
    return ['pr_builder']


def cycle():
    """Forever get the next task, import it and yield a tuple:
        (task name, task class object)
    """
    all_task_names = itertools.cycle(available(reverse=True))
    for name in all_task_names:
        task_name = "tasks.{0}.main".format(name)
        task_klass = import_task(task_name)
        if task_klass:
            yield (task_name, task_klass)


def import_task(task_name):
    """Import the fully qualified module given in task_name
    e.g. tasks.<task_name_dir>.main
    """
    module = _import_module(task_name)
    klass = _import_main_class(module)
    return klass


def get_all_tasks_docs():
    """Get a brief description of each task from the docstring
    in its __init__.py module
    """
    docs = {}
    for task in available():
        name = "tasks." + task
        doc = _import_module(name).__doc__ or ""
        docs[task] = doc
    return docs


def _import_module(module_name):
    """Try importing the module with the given name."""
    try:
        return importlib.import_module(module_name)
    except ImportError as e:
        print("Error trying to import {0}".format(module_name))
        print(str(e))
        return None


def _import_main_class(module):
    """Try grabbing the Main attribute of the given module object."""
    try:
        return module.Main
    except AttributeError:
        # TODO: log this somewhere
        return None


class Task(multiprocessing.Process):
    """Base from which all tasks should inherit."""

    def __init__(self, name, message_queue_pair=None):
        super(Task, self).__init__(name=name)
        self.log = log.Log(name)
        self.created = int(time.time())
        self.messages = messages.queue.MessageQueueBroker(message_queue_pair)
        self.shutdown = False
        self.shutdown_children = False
        self.child_tasks = []

    @property
    def now(self):
        """Utility function to get current epoch time."""
        return int(time.time())

    @property
    def active_since(self):
        """Get the number of seconds since this task started."""
        try:
            return self.now - self.started
        except:
            return 0

    @property
    def cpu(self):
        return psutil.cpu_percent()

    @property
    def mem(self):
        return 0

    @property
    def pid(self):
        return os.getpid()

    def setStart(self):
        """Call at the beginning of the task run() method to
        mark when the task began running.
        """
        self.started = self.now

    def remove_child_task(self, task):
        """Remove the given child task from the list of child_tasks"""
        index = None
        for index, t in enumerate(self.child_tasks):
            if t.pid == task.pid:
                break

        try:
            self.child_tasks.remove(index)
        except ValueError:
            return False
        return True

    def handle_parent_message(self, timeout=2):
        """Handle a message, if one is waiting, from the parent task.
        Stop waiting, by default, after 3 seconds, and timeout.
        """
        if not self.messages.parent:
            return

        data = self.messages.parent.recv(timeout)
        # log.debug('handle_parent_message received: ' + str(data))
        if data and data["exitcode"] == 0:
            func_name = "message_" + data["message"]
            self.log.debug("This is {0}".format(self.name))
            self.log.debug("Received data: {0}".format(data))
            self.log.debug("Forwarding to handler {0}".format(func_name))
            try:
                func = getattr(self, func_name)
            except AttributeError:
                pass
            else:
                args = data.get("args", {})
                try:
                    func(**args)
                except:
                    pass
        else:
            self.log.debug("No parent messages")

    def waitForChildTasks(self, tasks, **kws):
        """Wait for the given list of tasks to finish.
        Every so-often check if there is a message
        from the parent process.
        """
        alive_kids = [t.name for t in tasks if t.is_alive()]
        while any(alive_kids):
            time.sleep(kws.get("check_every", 5))
            self.log.debug("Waiting on: {0}".format(", ".join(alive_kids)))
            self.handle_parent_message()
            alive_kids = [t.name for t in tasks if t.is_alive()]

    def message_list_processes(self):
        """Get the recursive list of alive processes in this task,
        their pids, cpu and mem usage.
        """
        self.log.debug("Executing message_list_processes")

        self.messages.parent.send({
            "pid": self.pid,
            "cpu": self.cpu,
            "mem": self.mem,
            "name": self.name,
            "child_processes": [
                self.messages.child(child.name).fetch({
                    "message": "list_processes"
                }) for child in self.child_tasks
            ]
        })

    def message_shutdown(self):
        """Shut down this whole task and child processes."""
        self.log.info("Received shutdown, forwarding to children")
        self.shutdown_children = True
        for child in self.child_tasks:
            self.log.info("Sending shutdown to: {0}".format(child.name))
            self.messages.child(child.name).send({
                "message": "shutdown"
            })

        self.waitForChildTasks(*self.child_tasks)
        self.shutdown = True


class PriorityQueue(object):
    def __init__(self):
        self.queue = []

    def push(self, item):
        priority, element = item
        if item not in self.queue:
            # Don't add the same element twice
            heapq.heappush(self.queue, (priority, element))

    def pop(self):
        return heapq.heappop(self.queue)

    def next(self):
        try:
            return self.queue[0]
        except IndexError:
            return None

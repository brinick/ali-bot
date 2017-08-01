import time

import tasks


def run(name="ci.main", message_queue_pair=None):
    proc = CI(name, message_queue_pair)
    proc.start()

    # if we don't wait here we will exit from the main
    # routine and stop the logging (cf. __main__.py)
    proc.join()


class CI(tasks.Task):
    def __init__(self, name, message_queue_pair=None):
        super(CI, self).__init__(name, message_queue_pair)

    def run(self):
        all_tasks = tasks.cycle()

        while not self.shutdown:
            next_task = all_tasks.next()
            self.current_task = self.load_task(*next_task)
            self.current_task.start()
            self.loop2()

        self.log.info("Shutting down")

    def load_task(self, task_name, task_klass):
        """Instantiate the given task from its class object"""
        # Create the message server in this parent process
        # and the child process (when we create it) will create the client
        if task_name.startswith("tasks."):
            task_name = task_name.split(".", 1)[1]

        queue_pair = self.messages.create_queue_pair(name=task_name)

        # Instantiate the task Main class and launch the process
        inst = task_klass(name=task_name, message_queue_pair=queue_pair)
        self.log.info("Created task {0}".format(task_name))
        return inst

    def loop(self):
        while self.current_task.is_alive():
            time.sleep(10)
            self.handle_parent_message()

    def loop2(self):
        loops = 0
        maxloops = 20
        task_name = self.current_task.name
        while self.current_task.is_alive():
            time.sleep(10)
            loops += 1
            if loops == maxloops:
                self.log.debug("Shutting down")
                self.log.debug("Sending shutdown to {0}".format(task_name))
                self.messages.send_child(self.current_task.name, {
                    "message": "shutdown"
                })
            elif loops > maxloops:
                self.log.info("sent shutdown, waiting....")

    def message_available_tasks(self, **kws):
        """Get a list of available tasks and their doc strings."""
        docs = tasks.get_all_tasks_docs()
        tasks_names = docs.keys()
        tasks_docs = docs.values()

        self.messages.parent.send({
            "content": {
                "tasks": tasks_names,
                "tasks_doc": tasks_docs,
                "current_task": self.current_task.name
            }
        })

    def message_current_task(self, **kws):
        """Get the name of the currently running task."""
        self.messages.parent.send({
            "content": {
                "current_task": self.current_task.name
            }
        })

    def message_current_task_processes(self, **kws):
        """Get a list of all of the current task's processes."""
        child_task_name = self.current_task.name
        self.log.info("ci.main: calling {0} procs".format(child_task_name))
        child = self.messages.child(child_task_name)
        self.log.info("Sending to child {0}".format(child_task_name))
        processes = child.fetch({
            "message": "list_processes"
        })
        self.messages.parent.send({
            "content": {
                "processes": processes
            }
        })

    def message_current_task_shutdown(self, **kws):
        """Send a shutdown message to the current task and its
        children processes. Shutting down the current task will
        make the next task start running.
        """
        self.messages.child(self.current_task.name).send({
            "message": "shutdown"
        })
        # wait for response...
        self.waitForChildTasks(self.current_task)

    def message_current_task_kill_proc(self, **kws):
        try:
            pid = int(kws.get("pid"))
        except:
            pid = None

        child_proc_name = kws.get("name")
        self.messages.child(self.current_task.name).fetch({
            "message": "kill_proc",
            "args": {
                "pid": pid,
                "name": child_proc_name
            }
        })

    def message_shutdown(self):
        """Shut down everything and exit."""
        self.messages.child(self.current_task.name).send({
            "message": "shutdown"
        })
        self.waitForChildTasks(self.current_task)
        self.shutdown = True


if __name__ == "__main__":
    import log
    with log.ging():
        run()

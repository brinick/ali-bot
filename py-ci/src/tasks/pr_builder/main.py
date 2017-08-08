"""The entry point into the pull request builder task."""

import multiprocessing
import Queue
import time

from tasks import Task, PriorityQueue
from tasks.pr_builder.fetch import PRFetcherTask
from tasks.pr_builder.handle import PRHandlerTask
import tasks.pr_builder.metrics as metrics


class Main(Task):
    def __init__(self, name, message_queue_pair=None):
        super(Main, self).__init__(name, message_queue_pair)
        self.requests = PriorityQueue()
        self.current_pr = None
        self.task_fetcher = None
        self.task_pr_handler = None
        self.observed_prs = []

    def run(self):
        self.setStart()
        self.launch_metrics_collect_process()
        self.launch_fetcher_process()
        while not self.shutdown:
            self.handle_parent_message()
            self.run_main_loop()
            time.sleep(5)
        self.log.debug("Exiting")

    def run_main_loop(self):
        if not self.task_fetcher.is_alive():
            # Restart the fetcher process if it is not only dead
            # but also that we are not in the process of shutting down
            # (which would explain why it is gone)
            if not self.shutdown_children:
                self.restart_fetcher_process()
        else:
            self.fetch_new_prs()
            if self.higher_priority_pr_pending():
                self.shutdown_ongoing_pr_build()
                self.launch_next_pr_build()

    def restart_fetcher_process(self):
        self.log.warning("Restarting the fetcher process")
        self.prs_queue.close()
        self.prs_queue.join_thread()

        message_queue_pair = self.messages.child(self.task_fetcher.name)
        message_queue_pair.close()

        self.remove_child_task(self.task_fetcher)
        self.launch_fetcher_process()

    def launch_fetcher_process(self):
        """Spawn the pull request fetcher process that will run continually,
        pushing periodically onto a multiprocessing.Queue pull requests that
        are either new, or that have been fetched greater than some time ago.
        """
        # queue on which the fetcher will put PRs
        self.prs_queue = multiprocessing.Queue()

        # create the message server end of the pair
        child_proc_name = "pr_builder.fetcher"
        queue_pair = self.messages.create_queue_pair(name=child_proc_name)
        self.log.info("Spawning task {0}".format(child_proc_name))
        self.task_fetcher = PRFetcherTask(child_proc_name,
                                          self.prs_queue,
                                          message_queue_pair=queue_pair)
        self.task_fetcher.start()
        self.child_tasks.append(self.task_fetcher)

    def launch_metrics_collect_process(self):
        child_proc_name = "pr_builder.metrics"
        queue_pair = self.messages.create_queue_pair(name=child_proc_name)
        metrics.start()
        self.task_metrics = metrics.PRMetricsTask(child_proc_name,
                                                  message_queue_pair=queue_pair)
        self.task_metrics.start()
        self.child_tasks.append(self.task_metrics)

    def launch_next_pr_build(self):
        self.current_priority, self.current_pr = self.fetch_next_pr()
        child_proc_name = "pr_builder.handler"
        queue_pair = self.messages.create_queue_pair(name=child_proc_name)

        self.task_pr_handler = PRHandlerTask(child_proc_name,
                                             self.current_pr,
                                             message_queue_pair=queue_pair)
        no = self.current_pr["number"]
        self.log.info("Starting processing of pull request {0}".format(no))
        try:
            self.task_pr_handler.start()
        except (SystemExit, Exception) as e:
            m = "Task {0} threw an exception and exited".format(child_proc_name)
            self.log.error(m)
            self.log.error(str(e))

        self.child_tasks.append(self.task_pr_handler)

    def higher_priority_pr_pending(self):
        """Is the next pull request on the PriorityQueue of higher
        priority than the current pull request being built?
        """
        next_pr = self.peek_next_pr()
        if not next_pr:
            # There are no new prs so keep on doing
            # what we are currently doing
            m = "Pull request queue is empty, "
            if self.current_pr:
                m += "keep testing #{0}"
                m = m.format(self.current_pr["number"])
            else:
                m += "not currently testing a PR either. Nothing to do."
            self.log.debug(m)
            return False

        if not self.current_pr:
            # No pull request is being currently built, so let
            # us launch the next one
            m = "Not currently testing a PR, grabbing one from queue (#{0})"
            m = m.format(next_pr[1]["number"])
            self.log.debug(m)
            return True

        if self.task_pr_handler and not self.task_pr_handler.is_alive():
            # Current build is over, so let us launch the next one.
            # First however, we pop the previous reference from the
            # child tasks list
            m = "Finished with PR #{0}, launching #{1}"
            m = m.format(self.current_pr["number"], next_pr[1]["number"])
            self.log.debug(m)
            self.child_tasks.remove(self.task_pr_handler)
            self.task_pr_handler = None
            return True

        # Compare the ongoing build priority to the next one
        next_priority, next_pr = next_pr
        higher = (next_priority < self.current_priority)
        return higher

    def shutdown_ongoing_pr_build(self):
        if not (self.current_pr and self.task_pr_handler):
            return

        self.messages.child(self.task_pr_handler.name).send({
            "message": "shutdown"
        })

        # wait for it to be cleaned up
        self.waitForChildTasks(self.task_pr_handler)

        # put back on the priority queue the pr that was shut down
        self.put_back_current_pr()

    def put_back_current_pr(self):
        self.requests.push((self.current_priority, self.current_pr))

    def peek_next_pr(self):
        """Without actually popping it from the priority queue, take
        a peek at the next pull request.
        """
        return self.requests.next()

    def fetch_next_pr(self):
        """Pop the next pull request from the PriorityQueue."""
        return self.requests.pop()

    def fetch_new_prs(self):
        """Get any pull requests from the fetcher multiprocessing.Queue
        and then push them onto the local PriorityQueue.
        """
        try:
            prs = self.prs_queue.get(timeout=10)
        except Queue.Empty:
            # actually this is a timeout
            return
        self.prioritise(prs)

    def prioritise(self, prs):
        """Push the received prs onto the process-local PriorityQueue."""
        for pr in prs:
            self.requests.push(pr)

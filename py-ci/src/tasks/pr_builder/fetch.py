from collections import defaultdict
import log
import multiprocessing
import time

import github.pullrequests.fetch as pullrequests
from tasks import Task
from tasks.pr_builder.sleep_task import SleepTask
import tasks.pr_builder.metrics as metrics
import tasks.pr_builder.config as cfg


class PRFetcherTask(Task):
    def __init__(self, name, pr_queue, message_queue_pair=None):
        super(PRFetcherTask, self).__init__(name, message_queue_pair)
        self.results_queue = pr_queue

    def run(self):
        """Fetches pull requests periodically. If there are new ones,
        put them on the results queue to be processed immediately.
        If not, but there are known pull requests that are older
        than ma_wseconds, put these on the results queue instead.
        Otherwise, go to sleep for 30 seconds, before looping again.
        """
        self.setStart()
        args = get_kw_args()
        self.log.debug(args)
        self.known_prs = PullRequests()

        # How many seconds to wait before sending back existing pull requests?
        self.max_wait_no_new_prs = args["max_wait_no_new_prs"]

        # The time at which the last pull requests were fetched
        # If this time is too long in the past, we shut down the
        # pr builder task
        epoch_last_prs = self.now

        while not self.shutdown:
            prs = self.fetch_prs(args)
            self.handle_parent_message(timeout=1)

            if not prs and time_since(epoch_last_prs) > args["max_wait_no_prs"]:
                # If no pull requests at all have been found in the past
                # N seconds, we shut down the whole pull request building
                # task so as to allow other tasks to run
                m = "No pull requests at all have been found in the past {0}s. "
                m += "Shutting down."
                self.log.warning(m.format(args["max_wait_no_prs"]))
                self.shutdown = True
            else:
                epoch_last_prs = self.now
                self.handle_fetched_prs(prs)
                delay = cfg.pr_fetch["delay_between_fetches"]
                self.log.debug("Sleep {0}s".format(delay))
                self.goto_sleep(delay)

            self.handle_parent_message(timeout=1)

        self.log.info("Exiting")

    def fetch_prs(self, args):
        """Retrieve all open and reviewed pull requests as a list of
        2-tuples of the form: (priority, pr), where priority is an integer,
        and pr is a dict of form:
            {sha:..., number:..., created:.., updated:...}
        Note: if the main branch was also requested there will
        be one dict where updated is None, and number is the
        branch name (master, or whatever).
        """
        try:
            prs = pullrequests.fetch(**args)
        except SystemExit:
            # The argparser in pullrequests can call parser.error
            # So let's catch that, and shutdown cleanly
            # In principle the parent main process will notice
            # this fetcher has died, and will restart it.
            self.shutdown = True
            prs = []

        # This may of course contain previously seen pull requests
        # It is just a brute "total prs" at any one time metric.
        # The new PRs metric is done in self._handle_new_prs()
        metrics.send("number_prs", len(prs))
        return prs

    def handle_fetched_prs(self, prs):
        """Process the fetched pull requests and then remove
        any obsolete ones.
        """
        new_prs = [pr for pr in prs if pr not in self.known_prs]
        if new_prs:
            metrics.send("number_new_prs", len(new_prs))
            new_prs = self._add_fetched_time_info(new_prs)
            self._handle_new_prs(new_prs)
            self._send_fetch_time_metric(new_prs)
        else:
            self.log.debug("No (new) pull requests found")
            old_prs = self._fetch_old_prs()
            if old_prs:
                self._handle_old_prs(old_prs)

        self.remove_obsolete_prs(prs)

    def remove_obsolete_prs(self, prs):
        """Each time we call the pullrequests.fetch() method, it should
        return all open, reviewed pull requests. This list will include
        some mix of unseen and previously seen requests. If there are
        previously seen pull requests that are not in the newly retrieved list,
        this would imply that those missing have been closed, un-reviewed...
        in any case, we no longer need to track them and we remove them
        from our local list.
        """
        closed = [pr for pr in self.known_prs if pr not in prs]
        self.known_prs -= closed

    def _add_fetched_time_info(self, new_prs):
        """Add the time we fetched these for the first time,
        so that we can use this in later metrics calculations.
        """
        now = int(time.time())
        for np in new_prs:
            np["fetched"] = now
        return new_prs

    def _handle_new_prs(self, new_prs):
        """Log the new pull requests found, and then add them to the
        results queue for retrieval by the main pr builder process.
        """
        nprs = len(new_prs)
        ids = ", ".join([str(pr[1]["number"]) for pr in new_prs])
        self.log.info("{0} new pull requests found: {1}".format(nprs,
                                                                ids))

        self.known_prs += new_prs
        self.results_queue.put(new_prs)

    def _send_fetch_time_metric(self, new_prs):
        """Calculate for each new pull request the time between its
        last update time on Github, and the time when it was fetched
        here. This should be as small as possible.
        """
        for pr in new_prs:
            t1 = int(pr["updated"] or pr["created"])
            t2 = int(pr["fetched"])
            metrics.send("time_to_fetch", (t2 - t1))

    def _fetch_old_prs(self):
        """Get the list of pull requests that were fetched
        greater than some time ago.
        """
        return self.known_prs.older_than(self.max_wait_no_new_prs)

    def _handle_old_prs(self, old_prs):
        """Put the old pull requests on the results queue."""
        m = "No new pull requests found in the past"
        m += "{0}s, ".format(self.max_wait_no_new_prs)
        m += "will return old pull requests: "
        m += ",".join([("#" + str(pr[1]["number"])) for pr in old_prs])
        self.log.info(m)
        self.known_prs -= old_prs
        self.results_queue.put(old_prs)

    def goto_sleep(self, duration):
        """Fork a sleep process for the given duration, and wait for it."""
        taskname = "pr_builder.fetcher.sleep"
        queue_pair = self.messages.create_queue_pair(name=taskname)
        self.task_sleeper = SleepTask(name=taskname,
                                      duration=duration,
                                      message_queue_pair=queue_pair)
        self.task_sleeper.start()
        self.child_tasks.append(self.task_sleeper)
        self.waitForChildTasks(self.task_sleeper)
        self.child_tasks.pop()

    def message_shutdown(self):
        """Shut down this task"""
        # Kill the sleep process if it is ongoing
        self.log.info("Received shutdown")
        self.message_kill_sleep()

        # Clean up the PR results queue
        self.results_queue.close()
        self.results_queue.join_thread()
        self.shutdown = True

    def message_kill_sleep(self):
        """Stop sleeping by killing the sleep task."""
        if hasattr(self, "task_sleeper"):
            self.task_sleeper.terminate()
            self.task_sleeper.join()


class PullRequests(object):
    """Handy wrapper around the list of fetched pull requests."""

    def __init__(self):
        self.prs = defaultdict(list)
        self.log = log.Log("pullrequests wrapper")

    def older_than(self, age):
        """Find the list of known prs older than some age in secs."""
        age = int(age)
        now = int(time.time())
        old = []
        for priority in self.prs:
            for added, pr in self.prs[priority]:
                added = added[-1]
                if (now - added) > age:
                    old.append((priority, pr))
        return old

    def __contains__(self, item):
        priority, pr = item
        for added, pullreq in self.prs.get(priority, []):
            if pr == pullreq:
                return True
        return False

    def __iter__(self):
        return iter([
            pr for priority, prs in self.prs.items() for (added, pr) in prs
        ])

    def __iadd__(self, prs):
        # prs is a list of (priority, pr) tuples
        now = int(time.time())
        for priority, pr in prs:
            self.prs[priority].append(([now], pr))
        return self

    def __isub__(self, prs):
        for priority, pr in prs:
            prs_list = self.prs.get(priority, [])[:]  # copy the list
            for item in self.prs.get(priority, []):
                if item[1] == pr:
                    prs_list.remove(item)

            self.prs[priority] = prs_list
        return self

    def reset(self, prs):
        """Reset the given pull requests as if they had just been fetched."""
        now = int(time.time())
        for priority, pr in prs:
            for item in self.prs.get(priority, []):
                if item[1] == pr:
                    item[0].append(now)

        return self

    def __str__(self):
        o = []
        for priority, values in self.prs.items():
            o.append(str(priority))
            for added, pr in values:
                o.append(str(pr))
        return "\n".join(o)


# -------------------------------------------------------------------
# -------------------------------------------------------------------
# -------------------------------------------------------------------

def get_kw_args():
    """Grab a bunch of variables from the environment and
    store their values in a dict.
    """
    return {
        "show_main_branch": cfg.pr_fetch["show_main_branch"],
        "max_wait_no_new_prs": cfg.pr_fetch["max_wait_no_new_prs"],
        "max_wait_no_prs": cfg.pr_fetch["max_wait_no_prs"],
        "branch": "{0}@{1}".format(cfg.pr["repo"], cfg.pr["branch"]),
        "check_name": cfg.check_name,
        "trust_collaborators": cfg.trust["collaborators"],
        "trusted": cfg.trust["users"],
        "workers_pool_size": cfg.workers_pool_size,
        "worker_index": cfg.worker_index,
    }


def time_since(epoch=None):
    return int(time.time()) - (0 if not epoch else epoch)


def main():
    pr_queue = multiprocessing.Queue()
    fetcher = PRFetcherTask("pr_builder.fetcher.main", pr_queue)
    fetcher.start()


if __name__ == "__main__":
    with log.ging():
        main()

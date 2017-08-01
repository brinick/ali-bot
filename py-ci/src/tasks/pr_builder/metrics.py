import multiprocessing
import os

import monalisa
import tasks

from sh import hostname


metrics_queue = None


def start():
    global metrics_queue
    if not metrics_queue:
        metrics_queue = multiprocessing.Queue()


def send(name, value):
    """Put a metric with given name and value on the
    module-level metrics_queue. It will be retrieved
    by the PRMetricsTask process and forwarded to MonaLisa.
    """
    global metrics_queue
    metrics_queue.put({"name": name, "value": value})


class PRMetricsTask(tasks.Task):
    def __init__(self, name, message_queue_pair=None):
        super(PRMetricsTask, self).__init__(name, message_queue_pair)
        self.metric_path = self.construct_metric_path(name)

    def construct_metric_path(self, task_name):
        category = task_name.split(".", 1)[0]
        ci_name = os.environ.get("CI_NAME", "")
        subcategory = ci_name

        hn = hostname("-s").strip()
        wi = os.environ.get("WORKER_INDEX")
        nodename = "-".join([hn, wi])
        if ci_name:
            nodename += "-" + ci_name
        return "{0}.{1}_Nodes/{2}".format(category, subcategory, nodename)

    def run(self):
        self.setStart()
        while not self.shutdown:
            self.handle_parent_message()
            for metric in self.get_metrics():
                metric["path"] = self.metric_path
                monalisa.send(**metric)

    def get_metrics(self):
        """Grab all data on the metrics_queue global
        multiprocessing.Queue and return it.
        """
        metrics_data = []
        while not metrics_queue.empty():
            try:
                m = metrics_queue.get(timeout=5)
                metrics_data.append(m)
            except:
                pass
        return metrics_data

    def message_shutdown(self):
        self.shutdown = True
        metrics_queue.close()
        metrics_queue.join_thread()

import time
from tasks import Task


class SleepTask(Task):
    def __init__(self, name, duration, message_queue_pair=None):
        super(SleepTask, self).__init__(name, message_queue_pair)
        self.duration = duration

    def run(self):
        """Sleep for the given duration and then exit."""
        time.sleep(self.duration)

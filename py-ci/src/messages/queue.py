from log import NullLog as Log
# from log import Log
import multiprocessing
import Queue
import uuid


class MessageQueueBroker(object):
    def __init__(self, parent_queue_pair=None):
        self.id = uuid.uuid4().hex
        self.parent = parent_queue_pair
        self.log = Log("MessageQueueBroker")
        self.children = {}

    def create_queue_pair(self, name):
        """Create two MessageQueue instances."""
        sender = multiprocessing.Queue()
        receiver = multiprocessing.Queue()

        kid = MessageQueuePair(sender, receiver)
        self.children[name] = kid

        # this swap of queues is not a typo...
        retPair = MessageQueuePair(receiver, sender)
        return retPair

    def get_pair(self):
        sender = multiprocessing.Queue()
        receiver = multiprocessing.Queue()
        return (MessageQueuePair(sender, receiver),
                MessageQueuePair(receiver, sender))

    def sign(self, msg):
        """Add our UUID to the message"""
        msg["sender"] = self.id
        return msg

    def child(self, name):
        return self.children.get(name)

    def send_child(self, child_name, message):
        """Send a JSON message to the given child MessageQueuePair"""
        message = self.sign(message)
        child = self.children.get(child_name)
        if child:
            child.send(message)

    def send_parent(self, message):
        """Send a JSON message to the parent MessageQueuePair"""
        message = self.sign(message)
        if self.parent:
            # silently drop messages to inexistant parents
            self.parent.send(message)

    def fetch_child(self, child_name, message, timeout=30):
        """Fetch a response from the given child to the JSON message.
        Timeout, if necessary, waiting for the child's response.
        """
        message = self.sign(message)
        child = self.children.get(child_name)
        if not child:
            return {
                "exitcode": 1,
                "content": "{0}: no such child".format(child_name)
            }

        child.send(message)
        return child.recv(timeout)


class MessageQueuePair(object):
    def __init__(self, send_queue=None, recv_queue=None):
        self.log = Log("MessageQueuePair")
        self.send_queue = send_queue if send_queue else multiprocessing.Queue()
        self.recv_queue = recv_queue if recv_queue else multiprocessing.Queue()

    def send(self, payload):
        self.send_queue.put(payload)

    def recv(self, timeout=1):
        json_data = {}
        try:
            json_data = self.recv_queue.get(timeout=timeout)
            json_data["exitcode"] = 0
        except Queue.Empty:
            # timeout
            # log.warning('recv caught Emtpy exception')
            json_data = {"exitcode": 1, "content": "recv timed out"}
        return json_data

    def close(self):
        self.close_sender()
        self.close_receiver()

    def close_sender(self):
        self.send_queue.close()
        self.send_queue.join_thread()

    def close_receiver(self):
        self.recv_queue.close()
        self.recv_queue.join_thread()

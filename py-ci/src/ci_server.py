import json

from ci import CI
import tasks

import bottle


app = bottle.Bottle()

# API routes -
# Simple structure of HTTP method mapping to a list of 2-tuples
# where each tuple is (route, callback).
# Note: callbacks are methods of the CIServer instance
ROUTES = {
    "GET": [
        ("/", "list_tasks"),
        ("/tasks", "list_tasks"),
        ("/tasks/current", "get_current_task"),
        ("/tasks/current/procs",  "list_task_procs"),
        ("/health", "health"),
        ("/help", "list_routes")
    ],
    "POST": [
        ("/tasks/current/procs/<pid:int>/kill", "kill_task_proc"),
        ("/tasks/current/shutdown", "shutdown_current_task"),
        ("/shutdown", "shutdown")
    ]
}


@app.error(404)
def bad_route(error):
    bottle.response.status = 404
    bottle.response.content_type = "application/json"
    return json.dumps({
        "content": "inexistant URL",
        "status": 404
    })


def run(host="localhost", port=8080):
    proc = CIServer(host, port)
    proc.start()
    proc.join()


class CIServer(tasks.Task):
    def __init__(self, host, port):
        super(CIServer, self).__init__("ciserver.main")
        self.host = host
        self.port = port
        self.launch_ci()

    def launch_ci(self):
        queue_pair = self.messages.create_queue_pair(name="ci.main")
        self.ci = CI(name="ci.main", message_queue_pair=queue_pair)
        self.ci.start()

    def add_server_routes(self):
        """Load up the app with the API routes/callbacks."""
        for method, api_routes in ROUTES.items():
            for path, callback in api_routes:
                callback = getattr(self, callback)
                app.route(path=path, callback=callback, method=method)

    def run(self):
        self.add_server_routes()
        app.run(host=self.host, port=self.port, debug=True, reloader=True)

    def list_routes(self):
        """Get the available API routes"""
        data = {"content": []}
        for method, api_routes in ROUTES.items():
            for path, callback in api_routes:
                doc = getattr(self, callback).__doc__
                data["content"].append(
                    "{0} {1} -- {2}".format(method, path, doc)
                )
        return data

    def list_tasks(self):
        """Get the list of available tasks"""
        data = self.messages.fetch_child(self.ci.name, {
            "message": "available_tasks"
        }, timeout=5)
        return data

    def get_current_task(self):
        """Get the currently running task"""
        data = self.messages.fetch_child(self.ci.name, {
            "message": "current_task"
        }, timeout=5)
        return data

    def list_task_procs(self):
        """Get the list of processes for the current task"""
        data = self.messages.fetch_child(self.ci.name, {
            "message": "current_task_processes"
        }, timeout=None)
        return data

    def kill_task_proc(self, pid):
        """Terminate the given task process"""
        self.messages.send_child(self.ci.name, {
            "message": "current_task_kill_proc",
            "args": {
                "pid": pid
            }
        })

    def shutdown_current_task(self):
        """Shutdown the currently running task"""
        self.messages.send_child(self.ci.name, {
            "message": "current_task_shutdown"
        })

    def shutdown(self):
        """Shut down everything and exit"""
        self.messages.send_child(self.ci.name, {
            "message": "shutdown"
        })
        self.waitForChildTasks(self.ci)

    def health(self):
        """Get a simple ok response"""
        return {"status": "ok"}


def main():
    def get_port():
        def port_in_range(port):
            isOK = isinstance(port, int) and port in range(1024, 65535)
            if not isOK:
                m = "CI Server port must be in range 1024-65535"
                raise argparse.ArgumentTypeError(m)
            return port

        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--port",
                            help=("Port on the localhost on which "
                                  "to run the CI server"),
                            type=port_in_range,
                            dest="port")

        args = parser.parse_args()
        return args.port

    server_port = get_port() or 8080
    import log
    with log.ging():
        run(port=server_port)


if __name__ == "__main__":
    main()

#! /usr/bin/env python

"""Main entry point into the package."""

import argparse
import sys

major, minor, _, _, _ = sys.version_info
if not (major == 2 and minor == 7):
    sys.stderr.write("Python 2.7 required\n")
    sys.exit(1)


def get_ci_server_port():
    """If the --port <N> arg is passed, we will start a CIServer() instance
    on localhost:<N>. If no arg is passed, launch the bare CI() instance.
    """

    def port_in_range(port):
        isOK = isinstance(port, int) and port in range(1024, 65535)
        if not isOK:
            m = "CI Server port must be in range 1024-65535"
            raise argparse.ArgumentTypeError(m)
        return port

    parser = argparse.ArgumentParser()
    parser.add_argument("--port",
                        help=("Port on the localhost on which "
                              "to run the CI server"),
                        type=port_in_range,
                        dest="port")

    args = parser.parse_args()
    return args.port


def run_ci():
    import ci
    ci.run()


def run_ci_server(port, host="localhost"):
    import ci_server
    ci_server.run(host, port)


def run(port=None):
    if port:
        run_ci_server(port=port)
    else:
        run_ci()


def main():
    server_port = get_ci_server_port()
    import log
    with log.ging():
        run(server_port)


if __name__ == "__main__":
    main()

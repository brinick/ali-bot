#!/usr/bin/env python

import os
import socket


# defaults
defs = {
    "host": os.environ.get("MONALISA_HOST", "localhost"),
    "port": os.environ.get("MONALISA_PORT", 8889),
    "path": os.environ.get("MONALISA_PATH")
}


def send(name, value, path=defs["path"], host=defs["host"], port=defs["port"]):
    print("Sending to monalisa: {0} = {1}".format(name, value))
    return

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    data = " ".join([path, name, value])
    sock.sendto(data, (host, port))


if __name__ == "__main__":
    from argparse import ArgumentParser
    parser = ArgumentParser()
    parser.add_argument("--monalisa-host",
                        dest="host",
                        default=os.environ.get("MONALISA_HOST", "localhost"),
                        metavar="MONALISA_HOST")
    parser.add_argument("--monalisa-port",
                        dest="port",
                        default=os.environ.get("MONALISA_PORT", 8889),
                        type=int,
                        metavar="MONALISA_PORT")
    parser.add_argument("--metric-path",
                        dest="path",
                        default=os.environ.get("MONALISA_METRIC_PATH"),
                        metavar="MONALISA_METRIC_PATH")
    parser.add_argument("--metric-name",
                        dest="name",
                        required=True,
                        help="name of the metric")
    parser.add_argument("--metric-value",
                        dest="value",
                        required=True,
                        help="value for the metric")
    args = parser.parse_args()
    argsdict = vars(args)
    send(**argsdict)

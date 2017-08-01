import argparse
import logging

from github.util import parseGithubRef
from github.objects import Repo, CommitStatus


def set_to(commit, status, message="", url="", debug=False):
    """Set the github status."""
    state_value, state_context = parse_status(status)

    repo_name, _, sha = parseGithubRef(commit)
    owner, name = repo_name.split("/")
    repo = Repo(owner, name)

    commit = repo.commit(sha)
    matches = commit.statuses(state_context)

    return True

    if not matches:
        print("{0} does not exist. Creating.".format(state_context))
        status = CommitStatus(owner, name, **{"state": state_value,
                                              "context": state_context,
                                              "description": message,
                                              "target_url": url})
        commit.create_status(status)
    else:
        latest = matches[0]
        if not (latest.context == state_context and
                latest.state == state_value and
                latest.description == message and
                latest.target_url == url):
            m = "Last status for {0} does not match. Updating."
            print(m.format(state_context))
            print(repo.rate_limiting)
            status = CommitStatus(owner, name, **{"state": state_value,
                                                  "context": state_context,
                                                  "description": message,
                                                  "target_url": url})
            commit.create_status(status)

        else:
            m = "Last status for {0} already matches. Exit."
            print(m.format(state_context))
            print(repo.rate_limiting)

    return True


def parse_status(status):
    state_context = status.rsplit("/", 1)[0] if "/" in status else ""
    state_value = status.rsplit("/", 1)[1] if "/" in status else status
    VALID_STATES = ["pending", "success", "error", "failure"]
    if state_value not in VALID_STATES:
        raise RuntimeError("Valid Github states are " + ",".join(VALID_STATES))
    print(state_value, state_context)
    return (state_value, state_context)


def parse_args():
    usage = "status "
    usage += "-c <commit> -s <status> [-m <status-message>] [-u <target-url>]"
    parser = argparse.ArgumentParser(usage=usage)
    parser.add_argument("--commit", "-c",
                        required=True,
                        help=("Commit that the status refers to, in "
                              "<org>/<project>@<ref> format"))

    parser.add_argument("--status", "-s",
                        required=True,
                        help="Status to set in <status-id>/<status> format")

    parser.add_argument("--message", "-m",
                        default="",
                        help="Message relative to the status (default='')")

    parser.add_argument("--url", "-u",
                        default="",
                        help="Target url for the report (default='')")

    parser.add_argument("--debug", "-d",
                        action="store_true",
                        default=False,
                        help="Target url for the report")

    args = parser.parse_args()

    if args.debug:
        logger = logging.getLogger()
        logger.setLevel(logging.DEBUG)

    return args


def main():
    args = parse_args()
    ok = set_to(args.commit, args.status, args.message, args.url, args.debug)
    print("Status was set ok? {0}".format(ok))


if __name__ == "__main__":
    main()

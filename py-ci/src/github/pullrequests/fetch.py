#!/usr/bin/env python
from __future__ import print_function

from argparse import ArgumentParser, Namespace
import sys

from github.objects import Organisation, Repo, Teams


def argparse_defaults():
    return {
        "check_name": "build/AliPhysics/release",
        "show_main_branch": False,
        "status": "review",
        "script": "",
        "max_wait_no_new_prs": 1200,
        "poll_time": 30,
        "trusted": "review",
        "trust_collaborators": False,
        "trusted_team": None,
        "worker_index": 0,
        "workers_pool_size": 1
    }


def create_parser():
    parser = ArgumentParser(usage="pullrequests.py <repo>@<branch>")

    defs = argparse_defaults()

    parser.add_argument("--branch",
                        dest="branch",
                        help="Branch of which to list hashes for open prs")

    parser.add_argument("--check-name",
                        dest="check_name",
                        default=defs["check_name"],
                        help="Name of the check which we want to perform")

    parser.add_argument("--show-main-branch",
                        dest="show_main_branch",
                        default=defs["show_main_branch"],
                        action="store_true",
                        help=("Also show reference for the main branch, "
                              " not only for the PRs"))

    parser.add_argument("--status",
                        default=defs["status"],
                        help="Commit status that is considered trustworthy")

    parser.add_argument("--script",
                        dest="script",
                        default=defs["script"],
                        help="Execute a script on the resulting PR")

    parser.add_argument("--poll-time", "--timeout",
                        dest="poll_time",
                        default=defs["poll_time"],
                        type=int,
                        help=("DEPRECATED: Timeout between "
                              "one run and the other"))

    parser.add_argument("--max-wait-no-new-prs",
                        default=defs["max_wait_no_new_prs"],
                        dest="max_wait_no_new_prs",
                        type=int,
                        help=("Max seconds to wait for new PRs "
                              "before returning whatever PRs we already "
                              "have (default: 1200)"))

    parser.add_argument("--trusted",
                        default=defs["trusted"],
                        help="Users whose request you trust")

    parser.add_argument("--trusted-team",
                        dest="trusted_team",
                        help="Trust provided team")

    parser.add_argument("--trust-collaborators",
                        dest="trust_collaborators",
                        action="store_true",
                        help="Trust all collaborators")

    parser.add_argument("--worker-index",
                        dest="worker_index",
                        type=int,
                        default=defs["worker_index"],
                        help="Index for the current worker")

    parser.add_argument("--workers-pool-size",
                        dest="workers_pool_size",
                        type=int,
                        default=defs["workers_pool_size"],
                        help="Total number of workers")
    return parser


def parse_args(namespace=None):
    """Parse arguments from the command line, or if an
    argparse.Namespace instance is passed to the function,
    use that instead.
    """
    parser = create_parser()
    argsdict = {
        "args": None if namespace else sys.argv[1:],
        "namespace": namespace
    }

    # parse args from the command line, or from a namespace
    args = parser.parse_args(**argsdict)
    if args.max_wait_no_new_prs < 0:
        parser.error("--max-wait-no-new-prs should be positive")

    return derive_from_args(args)


def derive_from_args(args):
    """Construct some extra args from the command line args."""
    args.repo_name = args.branch.split("@")[0].strip()
    args.org = args.repo_name.split("/")[0].strip()

    branch_ref = "master"
    if "@" in args.branch:
        branch_ref = args.branch.split("@")[1].strip()
    args.branch_ref = branch_ref

    args.trusted = [t.strip() for t in args.trusted.split(",")]
    return args


class PRFetcher(object):
    def __init__(self, args):
        self.args = args
        self.repo = Repo(*args.repo_name.split("/"))

    def fetch(self):
        pulls = self.get_pulls()
        pulls = [self.process_pull(pull) for pull in pulls]
        # remove pull requests that were dropped
        pulls = [pull for pull in pulls if pull]

        if self.args.show_main_branch:
            pull = self.process_main_branch()
            if pull:
                pulls.append(pull)

        return self.prioritise(self.categorise(pulls))

    def process_main_branch(self):
        item = {}
        branch = self.repo.branch(self.args.branch_ref)
        if self.should_process(branch.head.sha):
            item = {
                "number": self.args.branch_ref,
                "sha": branch.head.sha,
                "created": branch.head.created_at,
                "updated": None
            }
            item.update(self.getStatusInfo(branch))
        return item

    def get_pulls(self):
        for pull in self.repo.pulls(branch=self.args.branch_ref):
            if self.should_process(pull.head.sha):
                yield pull

    def process_pull(self, pull):
        """Convert the pull object into a dictionary."""
        d = {
            "number": pull.number,
            "sha": pull.head.sha,
            "created": pull.opened_at,
            "updated": pull.updated_at,
        }

        try:
            d.update(self.getStatusInfo(pull))
            d["reviewed"] = d["reviewed"] or self.should_trust(pull.author)
        except RuntimeError as e:
            print("Error, will drop pull request:")
            print(str(e))
            d = {}

        return d

    def should_trust(self, pull_author):
        """If we specified a list of trusted users, a trusted team
        or if we trust collaborators, we need to check if this
        is the case for the given pull request. Notice that given that
        these will actually consume API calls, you need to be
        careful about what you enable.
        """
        def author_is_trusted():
            return pull_author in self.args.trusted

        def author_in_trusted_team():
            teamID = self.args.trusted_team
            return teamID and pull_author in Teams().find(teamID)

        def author_in_trusted_collaboration():
            return (self.args.trust_collaborators and
                    self.repo.is_collaborator(pull_author))

        return any([
            author_is_trusted(),
            author_in_trusted_team(),
            author_in_trusted_collaboration()
        ])

    def getStatusInfo(self, pull_or_branch):
        """If we specified a status to approve changes to tests,
        or if we specified a check name to prioritize pull request
        building, then we need to retrieve all the statuses.
        """
        item = {
            "tested": False,
            "success": False,
            "reviewed": False
            # "random": random.random()
        }

        if self.args.status or self.args.check_name:
            all_statuses = pull_or_branch.head.statuses()

            for s in all_statuses:
                state = s.state
                context = s.context
                if self.args.check_name and context == self.args.check_name:
                    item["reviewed"] = True
                    item["tested"] = state in ["success", "error", "failure"]
                    item["success"] = state in ["success"]
                    break

                if context == self.args.status and state == "success":
                    item["reviewed"] = True

        return item

    def should_process(self, sha):
        """Decide whether this worker should handle this request."""
        return True
        index = int(sha[0], 16) % self.args.workers_pool_size
        return index == self.args.worker_index

    def get_trusted_team_id(self, trusted_team_name):
        """Get the team ID which we consider safe for test."""
        teams = Organisation(self.args.org).teams
        if not teams:
            m = "You do not have permission to fetch team info. "
            m += "Is the env var $GITHUB_TOKEN set correctly?"
            raise SystemExit(m)

        team_id = None
        for team in teams:
            if team.name == trusted_team_name:
                team_id = team.id
                break
        return team_id

    def categorise(self, pulls):
        """Group the pulls into untested, tested but failed,
        and tested successfully.
        """
        not_tested = []
        tested_fail = []
        tested_success = []
        unreviewed = []
        for p in pulls:
            if not p["reviewed"]:
                unreviewed.append(str(p["number"]))
                continue

            if not p["tested"]:
                not_tested.append(p)
            else:
                if p["success"]:
                    tested_success.append(p)
                else:
                    tested_fail.append(p)

        if unreviewed:
            print(
                "Ignoring unreviewed PRs: {0}".format(", ".join(unreviewed))
            )

        return {
            "not_tested": not_tested,
            "tested_fail": tested_fail,
            "tested_success": tested_success
        }

    def prioritise(self, grouped):
        """Return a list of 2-tuples of the form: (priority, pr) where a pr
        is just a dict. Priority of a pull request is (in decreasing priority):
            untested
            tested_and_failed
            tested_and_succeeded
        """
        prioritised = []
        priorities = {"not_tested": 0, "tested_fail": 1, "tested_success": 2}
        for category, prs in grouped.items():
            for pr in prs:
                item = (priorities[category], pr)
                prioritised.append(item)
        return prioritised


def fetch(**args):
    defs = argparse_defaults()
    defs.update(args)
    args = parse_args(Namespace(**defs))
    return PRFetcher(args).fetch()


if __name__ == "__main__":
    args = parse_args()
    prs = PRFetcher(args).fetch()
    for priority, value in prs:
        print("----- Priority {0} -------".format(priority))
        print(value)

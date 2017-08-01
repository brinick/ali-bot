#!/usr/bin/env python
from __future__ import print_function

from argparse import ArgumentParser, Namespace
from commands import getstatusoutput
from glob import glob
from os.path import dirname, join
import sys

from github.objects import Repo
import github.status as ghstatus
from github.util import parseGithubRef, calculateMessageHash


def report(**args):
    # If we pass in nothing, args will be looked for in sys.argv
    args = None if not args else Namespace(**args)
    outcome = False
    try:
        args = parse_args(args)
    except SystemExit:
        # catches any parser.error calls
        pass
    else:

        return
        logs = Logs(args)

        repo_name, pr_id, pr_commit = parseGithubRef(args.pr)

        owner, name = repo_name.split("/")
        repo = Repo(owner, name)

        func = handle_pr if pr_id.isdigit() else handle_branch
        outcome = func(repo, pr_id, args, logs)
    return outcome


def create_parser():
    parser = ArgumentParser()
    parser.add_argument("--work-dir", "-w", default="sw", dest="workDir")

    parser.add_argument("--default", default="release")

    parser.add_argument("--devel-prefix", "-z",
                        dest="develPrefix",
                        default="")

    parser.add_argument("--pr",
                        required=True,
                        help=("Pull request which was checked in "
                              "<org>/<project>#<nr>@ref format"))

    parser.add_argument("--status", "-s",
                        required=True,
                        help="Check which had the error")

    parser.add_argument("--dry-run", "-n",
                        action="store_true",
                        default=False,
                        help="Do not actually comment")

    parser.add_argument("--limit", "-l",
                        default=50,
                        help="Max number of lines from the report")

    parser.add_argument("--message", "-m",
                        dest="message",
                        help="Message to be posted")

    parser.add_argument("--logs-dest",
                        dest="logsDest",
                        default="rsync://repo.marathon.mesos/store/logs",
                        help="Destination path for logs")

    parser.add_argument("--log-url",
                        dest="logsUrl",
                        default="https://ali-ci.cern.ch/repo/logs",
                        help="Destination path for logs")

    parser.add_argument("--debug", "-d",
                        action="store_true",
                        default=False,
                        help="Turn on debug output")

    return parser


def parse_args(namespace=None):
    parser = create_parser()
    argsdict = {
        "args": None if namespace else sys.argv[1:],
        "namespace": namespace
    }

    # parse args from the command line, or from a namespace
    args = parser.parse_args(**argsdict)

    if "#" not in args.pr:
        parser.error("You need to specify a pull request")
    if "@" not in args.pr:
        parser.error("You need to specify a commit this error refers to")
    return args


class Logs(object):
    def __init__(self, args):
        self.work_dir = args.workDir
        self.develPrefix = args.develPrefix
        self.limit = args.limit
        self.full_log = self.constructFullLogName(args.pr)
        self.rsync_dest = args.logsDest
        self.url = join(args.logsUrl, self.full_log)
        if not args.message:
            self.parse()

    def parse(self):
        self.find()
        self.grep()
        self.cat(self.full_log)
        self.rsync(self.rsync_dest)

    def constructFullLogName(self, pr):
        # file to which we cat all the individual logs
        pr = parse_pr(pr)
        return join(pr.repo_name, pr.id, pr.commit, "fullLog.txt")

    def find(self):
        search_path = join(self.work_dir, "BUILD/*/log")
        print("Searching all logs matching: %s" % search_path, file=sys.stderr)
        globbed = glob(search_path)

        suffix = ("latest" + "-" + self.develPrefix).strip("-")
        logs = [x for x in globbed if dirname(x).endswith(suffix)]

        print("Found:\n%s" % "\n".join(logs), file=sys.stderr)
        self.logs = logs

    def grep(self):
        """Grep for errors in the build logs, or, if none are found,
        return the last N lines where N is the limit argument.
        """
        error_log = ""
        for log in self.logs:
            cmd = "cat %s | grep -e ': error:' -A 3 -B 3 " % log
            cmd += "|| tail -n %s %s" % (self.limit, log)
            err, out = getstatusoutput(cmd)
            if err:
                print("Error while parsing logs", file=sys.stderr)
                print(out, file=sys.stderr)
                continue

            error_log += log + "\n"
            error_log += out

        error_log = "\n".join(error_log.split("\n")[0:self.limit])
        error_log.strip(" \n\t")
        self.error_log = error_log

    def cat(self, tgtFile):
        cmd = "rm -fr copy-logs && mkdir -p `dirname copy-logs/%s`" % tgtFile
        getstatusoutput(cmd)

        for log in self.logs:
            cmd = "cat %s >> copy-logs/%s" % (log, tgtFile)
            print(cmd, file=sys.stderr)
            err, out = getstatusoutput(cmd)
            print(out, file=sys.stderr)

    def rsync(self, dest):
        err, out = getstatusoutput("cd copy-logs && rsync -av ./ %s" % dest)
        if err:
            print("Error while copying logs to store.", file=sys.stderr)
            print(out, file=sys.stderr)


def handle_branch(repo, branch_name, args, logs):
    # If the branch is not a PR, we should look for open issues
    # for the branch. This should really folded as a special case
    # of the PR case.
    sha = repo.branch(branch_name).sha

    message = "Error while checking %s for %s:\n" % (args.status, sha)
    if args.message:
        message += args.message
    else:
        message += "```\n%s\n```\nFull log [here](%s).\n" % (logs.error_log,
                                                             logs.url)

    messageSha = calculateMessageHash(message)

    # Look for open issues:
    # - If we find one which was opened for the same
    #   branch / sha / error message sha triplet, we consider
    #   having already commented.
    # - If we find one which was opened for the same branch / sha,
    #   but with a different error message sha, we close it
    #   (as we assume the error message is now different).
    # - If we find one which was opened for a different branch / sha
    #   pair, close it (as we assume it's now obsolete since the
    #   branch points to something else).
    # - If no issue was found for the given branch, create one
    #   and add a comment about the failure.

    prefix = "Error while checking branch "
    still_valid = "{0}{1}@{2}:{3}".format(prefix, branch_name, sha, messageSha)
    different_issue = "{0}{1}@{2}".format(prefix, branch_name, sha)
    branch_updated = "{0}{1}@".format(prefix, branch_name)

    open_issues = repo.issues()
    for issue in open_issues:
        title = issue.title

        if title.startwith(still_valid):
            msg = "Issue still valid. Exiting."
            print(msg, file=sys.stderr)
            return True

        # -----------------------------------------------------------------
        if title.startswith(different_issue):
            msg = "Issue is about something different. Updating."
            print(msg, file=sys.stderr)

            tpl = "Error for commit {0} has changed.\n"
            newBody = tpl.format(sha + message)
            issue.create_comment({
                "body": newBody
            })

            issue.update({
                "title": still_valid
            })
            return True

        # -----------------------------------------------------------------
        if title.startswith(branch_updated):
            issue.create_comment({
                "body": "Branch was updated. Closing issue."
            })

            issue.update({
                "state": "closed"
            })

            continue

    # The first time we report an issue with a commit, we do so as issue body.
    # Subsequent changes will be reported as comments.
    ghstatus.set_to(args.pr, join(args.status, "error"))

    repo.create_issue({
        "body": message,
        "title": still_valid
    })

    return True


def handle_pr(repo, pr, args, logs):
    sha = repo.commit(pr.commit).sha

    message = "Error while checking %s for %s:\n" % (args.status, sha)
    if args.message:
        message += args.message
    else:
        message += "```\n%s\n```\nFull log [here](%s).\n" % (logs.error_log,
                                                             logs.url)

    if args.dry_run:
        print("Will annotate {0}".format(sha))
        print(message)
        return True

    messageHash = calculateMessageHash(message)
    issue = repo.issue(pr.number)
    for comment in issue.comments:
        m = "Error while checking {0} for {1}".format(args.status, sha)
        if comment.body.startswith(m):
            if calculateMessageHash(comment.body) != messageHash:
                print("Comment was different. Updating", file=sys.stderr)
                comment.update({
                    "body": message
                })
                return True

            print("Found same comment for the same commit", file=sys.stderr)
            return True

    ghstatus.set_to(args.pr, join(args.status, "error"))

    issue.create_comment({
        "body": message
    })
    return True


def parse_pr(pr):
    repo_name, pr_id, pr_commit = parseGithubRef(pr)
    return Namespace(repo_name=repo_name,
                     id=pr_id,
                     commit=pr_commit)


if __name__ == "__main__":
    sys.exit(0 if report() else 1)

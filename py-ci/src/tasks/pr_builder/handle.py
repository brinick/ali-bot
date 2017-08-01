"""Handle a single pull request.

Try merging it, then run aliDoctor and aliBuild, each step only being
performed if the previous step succeeded. Failures are reported to
github, whilst failure to report failure(!) is pushed to the analytics.
aliDoctor and aliBuild commands are each run in their own process.
"""

import multiprocessing
import Queue
import os
import time

import analytics
import cmds
import github.status as ghstatus
import github.pullrequests.error as pr_error
from tasks import Task
import tasks.pr_builder.metrics as metrics
import tasks.pr_builder.config as cfg

from sh import ErrorReturnCode
from sh import awk, du, find, git, hostname, mkdir, nproc, printf, pushd


class PRHandlerTask(Task):
    def __init__(self, name, pr, message_queue_pair=None):
        # pr is a dict with keys sha, number, created, updated
        super(PRHandlerTask, self).__init__(name, message_queue_pair)
        self.pr = pr

    def run(self):
        self.setStart()
        self.prepare()

        mergedOK = self.do_merge()
        if mergedOK:
            doctorOK = self.run_alidoctor()

            if doctorOK:
                self.run_alibuild()
                metrics.send("pr_build_time", self.active_since)

    def prepare(self):
        """In preparation for merging and building, set some env vars,
        some attributes, update the local git repositories and report
        a pending status to github.
        """
        # analytics.report_screenview("pr_processing_start")
        # metrics.send("state", "pr_processing_start")
        self.setAliBotEnvVars()
        self.pr_number = self.pr["number"]
        self.pr_sha = self.pr["sha"]
        self.pr_repo = cfg.pr["repo"]
        self.pr_repo_checkout = cfg.pr["repo_checkout"]

        m = "Using pr_repo_checkout: {0}".format(self.pr_repo_checkout)
        self.log.debug(m)

        self.mirror = cfg.pr_handle["mirror"]
        self.package = cfg.pr_handle["package"]

        self.alibuild_defaults = cfg.alibuild["defaults"]

        self.check_name = cfg.pr_handle["check_name"]
        self.build_suffix = cfg.build_suffix

        with pushd("alidist"):
            self.alidist_ref = git("rev-parse", "--verify", "HEAD").strip()

        commit = "alisw/alidist@{0}".format(self.alidist_ref)
        ghstatus.set_to(commit, self.status("pending"))
        self.update_git_repos()

    def run_alidoctor(self):
        """Run aliDoctor in its own process, and return a boolean
        indicating outcome. If it fails, we can move on to the next
        pull request since we know that the build will not work.
        We do not report success because that's a given.
        """
        args = self._construct_alidoctor_args()
        doctor = cmds.AliDoctor(*args)
        result = self._run_in_process(doctor)
        doctorOK = result["ok"]
        if not doctorOK:
            self._report_alidoctor_failure()
        return doctorOK

    def run_alibuild(self):
        """Run aliBuild in its own process and return a boolean
        indicating outcome. Each time this is executed we delete
        any "latest" symlinks to avoid reporting errors from
        previous builds. In any case they will be recreated if
        needed when we build.
        """
        self._prepare_build_dirs()
        self._prepare_gitlab_credentials()
        args = self._construct_alibuild_args()
        self.log.debug("alibuild args are:\n{0}".format(args))
        return True

        alibuild = cmds.AliBuild("alibuild/aliBuild",
                                 *args,
                                 ALIBUILD_HEAD_HASH=self.pr_sha,
                                 ALIBUILD_BASE_HASH=self.upstream_sha,
                                 # reset from the process environment
                                 GITLAB_USER="",
                                 GITLAB_PASS="",
                                 GITLAB_TOKEN="")

        result = self._run_in_process(alibuild)
        buildOK = result["ok"]
        if not buildOK:
            self._report_alibuild_failure()
        else:
            self._report_alibuild_success()
        return buildOK

    def status(self, value=""):
        return os.path.join(self.check_name, value)

    def setAliBotEnvVars(self):
        """A few common environment variables when reporting status
        to analytics. In analytics we use screenviews to indicate different
        states of the processing and events to indicate all the things we
        would consider as fatal in a non-daemon process but that here simply
        make us go to the next step.
        """
        return

        setenv("ALIBOT_ANALYTICS_ID", getenv("ALIBOT_ANALYTICS_ID"))

        ci_name = cfg.ci_name
        hn = hostname("-s").strip()
        wi = cfg.worker_index
        user_uuid = "-".join([hn, wi])
        if ci_name:
            user_uuid += "-" + ci_name
        setenv("ALIBOT_ANALYTICS_USER_UUID", user_uuid)

        # Hardcode for now
        setenv("ALIBOT_ANALYTICS_ARCHITECTURE", "slc7_x86-64")
        setenv("ALIBOT_ANALYTICS_APP_NAME", "continuous-builder.py")

    def update_git_repos(self):
        """Run a git pull origin on all local git repositories if they
        are on a branch.
        """
        return

        git_dirs = find(".", "-maxdepth", "2", "-name", ".git").stdout.split()
        git_dirs = [os.path.dirname(d) for d in git_dirs]
        git_dirs = [g for g in git_dirs if 'ali-bot' not in g]
        for git_dir in git_dirs:
            with pushd(git_dir):
                # Only update projects on a branch
                if self.isBranchRepo():
                    git.pull("origin")

    def status_ref(self):
        commit = self.pr_repo
        commit += "@"
        commit += self.pr_sha or self.alidist_ref
        return commit

    def isBranchRepo(self):
        return git("rev-parse", "--abbrev-ref", "HEAD").strip() != "HEAD"

    def report_merge_fail(self):
        """If the git merge failed, set github status and
        stop processing this pull request. If the github status set
        fails, report analytics exception.
        """
        ref = self.status_ref()
        message = "Cannot merge PR into test area"
        if not ghstatus.set_to(ref, self.status("error"), message):
            analytics.report_exception(
                "github/status.set_to() failed on cannot merge PR"
            )

    def report_merge_diffsize_too_big(self):
        """If the git merge succeeded but the difference in size
        is too large between pre- and post- merge repositories, then
        set the github status and stop processing this pull request.
        If the github status set fails, report analytics exception.
        """
        ref = self.status_ref()

        # First: set the github status
        message = "Diff too big, rejecting."
        if not ghstatus.set_to(ref, self.status("error"), message):
            analytics.report_exception(
                "ghstatus.set_to() failed on merge too big"
            )

        # Second: report the pull request error
        message = ("Your pull request exceeded the allowed size. "
                   "If you need to commit large files, please refer to "
                   "<http://alisw.github.io/git-advanced/"
                   "#how-to-use-large-data-files-for-analysis>")

        pr = "{0}#{1}@{2}".format(self.pr_repo, self.pr_number, self.pr_sha)

        if not pr_error.report(default=self.build_suffix,
                               pr=pr,
                               status=self.check_name,
                               message=message):
            analytics.report_exception(
                "pr_error.report() failed on merge diff too big"
            )

    def do_merge(self):
        if not self.pr_repo:
            return True

        max_merge_diff_size = cfg.pr["max_merge_diff_size"]

        with pushd(self.pr_repo_checkout):
            mergedOK, sizeDiff = self.exec_git_merge()
            sizeOK = (sizeDiff <= max_merge_diff_size)

            if not mergedOK:
                self.log.error("Git merge was not ok")
                self.report_merge_fail()
            elif not sizeOK:
                self.log.error("Merge diff size was not ok")
                self.report_merge_diffsize_too_big()
            return mergedOK and sizeOK

    def exec_git_merge(self):
        """Try and perform a git merge, and return a 2-tuple of
        merged ok boolean and the size diff in bytes between pre- and
        post-merge repositories.
        """
        merged_ok = True
        pr_branch = cfg.pr["branch"]
        git.reset("--hard", "origin/{0}".format(pr_branch))
        git.config("--add",
                   "remote.origin.fetch",
                   "+refs/pull/*/head:refs/remotes/origin/pr/*")
        git.fetch("origin")
        git.clean("-fxd")

        oldSize = self._get_size()
        self.upstream_sha = git("rev-parse", "--verify", "HEAD").strip()
        try:
            m = "Merging pull request {0} ({1})".format(self.pr_number,
                                                        self.pr_sha)
            self.log.info(m)
            git.merge("{0}".format(self.pr_sha))
        except ErrorReturnCode as e:
            # git merge returned a non-zero exitcode
            m = "git merge failed, exitcode: {0}, stderr: {1}"
            m = m.format(e.exit_code, e.stderr)
            self.log.error(m)
            merged_ok = False

        # clean in case the merge fails
        git.reset("--hard", "HEAD")
        git.clean("-fxd")

        sizeDiff = 0
        if merged_ok:
            newSize = self._get_size()
            sizeDiff = newSize - oldSize

        return (merged_ok, sizeDiff)

    def message_shutdown(self):
        # this list will in principle be only one element:
        # the alidoctor or alibuild process
        for task in self.child_tasks:
            task.terminate()
            task.join()

    def _construct_alidoctor_args(self):
        args = []
        defs = [] if not self.alibuild_defaults else ["--defaults",
                                                      self.alibuild_defaults]
        args.extend(defs)
        args.append(self.package)
        return args

    def _report_alidoctor_failure(self):
        ref = self.status_ref()
        error = self.status("error")
        if not ghstatus.set_to(ref, error, message="aliDoctor error"):
            analytics.report_exception(
                "github/status.set_to() failed on aliDoctor error"
            )

    def _report_alibuild_success(self):
        ref = self.status_ref()
        success = self.status("success")
        if not ghstatus.set_to(ref, success):
            analytics.report_exception(
                "ghstatus.set_to() failed on alibuild success"
            )

    def _report_alibuild_failure(self):
        pr = "{0}#{1}@{2}"
        pr = pr.format(self.pr_repo, self.pr_number, self.pr_sha)
        if not pr_error.report(default=self.build_suffix,
                               pr=pr,
                               status=self.status()):
            analytics.report_exception(
                "github/pullrequests/error.report() failed on alibuild error"
            )

    def _prepare_build_dirs(self):
        """In preparation for running aliBuild, create if necessary
        the base build directory. If already existing from a previous
        build, delete any latest symlinks to avoid confusion.
        """
        mkdir("-p", "sw/BUILD")
        find("sw/BUILD/", "-maxdepth", "1", "-name", "*latest*", "-delete")

    def _prepare_gitlab_credentials(self):
        """Setup Gitlab credentials for private ALICE repositories."""
        return
        txt = "\n".join([
            "protocol=https",
            "host=gitlab.cern.ch",
            "username={0}".format(getenv("GITLAB_USER")),
            "password={0}\n".format(getenv("GITLAB_PASS"))
        ])
        git(printf(txt), "credential-store", "--file", "~/.git-creds", "store")
        git.config("--global",
                   "credential.helper",
                   "store --file ~/.git-creds")
        delenv("GITLAB_USER")
        delenv("GITLAB_PASS")
        delenv("GITLAB_TOKEN")

    def _construct_alibuild_args(self):
        # njobs
        njobs = ["-j", (cfg.alibuild["jobs"] or nproc().strip())]

        defs = [] if not self.alibuild_defaults else ["--defaults",
                                                      self.alibuild_defaults]

        debug = [] if not cfg.alibuild["debug"] else ["--debug"]

        rs = cfg.alibuild["remote_store"]
        remoteStore = [] if not rs else ["--remote-store", rs]

        # externals
        no_consistent_externals = cfg.alibuild["no_consistent_externals"]
        externals = []
        if no_consistent_externals:
            externals = ["-z", self.pr_number.replace("-", "_")]

        # mirror
        refSources = ["--reference-sources", self.mirror]

        args = []
        args.extend(njobs)
        args.extend(defs)
        args.extend(debug)
        args.extend(refSources)
        args.extend(remoteStore)
        args.extend(externals)
        args.extend(["build", self.package])
        return args

    def _run_in_process(self, cmd, timeout=None):
        """Run the cmd, which is a sh.Command instance, in its own process.
        Communication of results is via a multiprocessing.Queue.
        If a timeout is provided (in seconds), kill the process after
        the alloted time has passed, otherwise wait indefinetly.
        """
        results_queue = multiprocessing.Queue()

        child_proc_name = "pr_builder.handler." + cmd.name
        queue_pair = self.messages.create_queue_pair(name=child_proc_name)

        forked_cmd = cmds.ForkedCommand(cmd,
                                        results_queue,
                                        message_queue_pair=queue_pair)
        self.child_tasks.append(forked_cmd)
        forked_cmd.start()

        result = None

        if timeout and is_positive_int(timeout):
            forked_cmd.join(int(timeout))
            if forked_cmd.is_alive():
                try:
                    result = results_queue.get(timeout=3)
                except Queue.Empty:
                    pass

                forked_cmd.terminate()
        else:
            # No timeout - potentially run forever
            while forked_cmd.is_alive() and not result:
                try:
                    result = results_queue.get(timeout=3)
                except Queue.Empty:
                    pass

                # watch out for incoming messages
                # e.g. telling us to kill this forked cmd
                self.handle_parent_message()

            if not result:
                # TODO: This should not be possible, and should be reported.
                # Even if the command was sigkill'ed, as it's run in a
                # sub-process the parent cmds.Forkedcommand should be able
                # to get some info and put it on the results queue.
                pass

        # Clean up
        results_queue.close()
        results_queue.join_thread()

        # Give a little leeway for the forked command process to finish
        time.sleep(3)

        # But if this was not enough, too bad, we terminate it
        if forked_cmd.is_alive():
            forked_cmd.terminate()
            forked_cmd.join()

        self.remove_child_task(forked_cmd)
        return result

    def _get_size(self):
        """Get the total size of the directory tree below current directory,
        excluding .git dirs
        """
        size = awk(du("--exclude=.git", "-sb", "."), "{print $1}").strip()
        return int(size)

# -------------------------------------------------------------------


def getenv(key, default=None):
    return os.environ.get(key, default)


def setenv(key, value):
    os.environ[key] = value


def delenv(key):
    del os.environ[key]


def is_positive_int(val):
    try:
        return int(val) > 0
    except:
        return False

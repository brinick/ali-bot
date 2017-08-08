"""Configuration and defaults for pull request building variables."""

import os


def env(key, default=None):
    return os.environ.get(key, default)


ci_name = env("CI_NAME", "")

pr = {
    "branch": env("PR_BRANCH", "master"),
    "max_merge_diff_size": env("MAX_DIFF_SIZE", 20000000),
    "repo": env("PR_REPO", "alisw/alidist")
}
pr["repo_checkout"] = env("PR_REPO_CHECKOUT", os.path.basename(pr["repo"]))

# ------------------------------------------------------
# pull request fetching
# ------------------------------------------------------
pr_fetch = {
    # how long to wait between two PR fetch attempts
    "delay_between_fetches": 30,
    "show_main_branch": False,

    # seconds to wait with no prs (old or new) before shutting down
    "max_wait_no_prs": env("MAX_WAIT_NO_PRS", 1200),

    # seconds to wait with no new prs before returning old ones
    "max_wait_no_new_prs": env("MAX_WAIT_NO_NEW_PRS", env("DELAY", 1200))
}

# ------------------------------------------------------
# pull request handling
# ------------------------------------------------------
alibuild = {
    "executable": "alibuild/aliBuild",
    "repo": env("ALIBUILD_REPO"),
    "defaults": env("ALIBUILD_DEFAULTS", "release"),
    "o2_tests": env("ALIBUILD_O2_TESTS", 0),
    "jobs": env("JOBS"),
    "debug": env("DEBUG"),
    "remote_store": env("REMOTE_STORE"),
    "no_consistent_externals": env("NO_ASSUME_CONSISTENT_EXTERNALS", "")
}

alidoctor = {
    "executable": "alibuild/aliDoctor"
}

pr_handle = {
    "mirror": env("MIRROR", "/build/mirror"),
    "package": env("PACKAGE", "AliPhysics"),
}
pr_handle["check_name"] = env("CHECK_NAME",
                              "build/{0}/{1}".format(pr_handle["package"],
                                                     alibuild["defaults"]))


build_suffix = env("BUILD_SUFFIX")

trust = {
    "collaborators": env("TRUST_COLLABORATORS", False),
    "users": env("TRUSTED_USERS", "review")
}

worker_index = env("WORKER_INDEX", 0)
workers_pool_size = env("WORKERS_POOL_SIZE", 1)


# ------------------------------------------------------
# reporting
# ------------------------------------------------------
monalisa = {
    "host": env("MONALISA_HOST"),
    "port": env("MONALISA_PORT")
}

# ------------------------------------------------------
# process timeouts
# ------------------------------------------------------
# Max wait for the given child processes, after which we will
# terminate them
process_timeout = {
    "alidoctor": env("ALIDOCTOR_PROCESS_TIMEOUT", 120),
    "alibuild": env("ALIBUILD_PROCESS_TIMEOUT", 3600),
    "git_pull": env("GIT_PULL_TIMEOUT", 120)
}

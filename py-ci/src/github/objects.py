"""Object representation of various Git(hub) concepts,
to facilitate working with a Github repository. To reduce
Github API calls, a local cache is used. Most objects are
not intended to be instantiated directly (although you can),
rather you should instantiate a Repo object and make calls on
that.
"""

from collections import namedtuple
import os

from github.util import github_token
from github.client import GithubCachedClient, PickledCache


CACHE_NAME = os.environ.get("GITHUB_CACHE_CLIENT", ".github-cache")
CLIENT = GithubCachedClient(token=github_token(),
                            cache=PickledCache(CACHE_NAME))


def switch_cache(name):
    """Change the cache name, and update the client to use it."""
    global CACHE_NAME
    global CLIENT
    CLIENT.close()
    CACHE_NAME = name
    CLIENT = GithubCachedClient(token=github_token(),
                                cache=PickledCache(name))


class IGithub(object):
    def __init__(self):
        self._client = CLIENT
        self._cache_name = CACHE_NAME

    def url_create(self, *paths):
        paths = [str(p) for p in paths]
        return os.path.join(self.url_base, *paths)

    @property
    def cache_name(self):
        return self._cache_name

    @cache_name.setter
    def cache_name(self, name):
        """Switch to the cache with the given name."""
        switch_cache(name)
        self._cache_name = name
        self._client = CLIENT

    @property
    def rate_limiting(self):
        return self._client.rate_limiting

    def get(self, url):
        return self._client.get(url)

    def post(self, url, data):
        return self._client.post(url, data)


class RepoObject(IGithub):
    def __init__(self, owner, name):
        self._owner = owner
        self._name = name
        super(RepoObject, self).__init__()

    @property
    def url_base(self):
        return os.path.join("/repos", self._owner, self._name)

    @property
    def repo(self):
        return os.path.join(self._owner, self._name)

    @property
    def repo_owner(self):
        return self._owner

    @repo_owner.setter
    def repo_owner(self, the_owner):
        self._owner = the_owner

    @property
    def repo_name(self):
        return self._name

    @repo_name.setter
    def repo_name(self, the_name):
        self._name = the_name


class Repo(RepoObject):
    def __init__(self, repo_owner, repo_name):
        super(Repo, self).__init__(repo_owner, repo_name)

    def pulls(self, branch="master", state="open", author=None):
        """Get the list of pull requests in this repo for the given
        branch and state (defaults: master and open). Filter the
        returned list for a given author, if provided, else return all.
        """
        if state not in PullRequest.legal_states:
            return None

        u = self.url_create("pulls?base={0}&state={1}".format(branch, state))
        data = self.get(u) or []
        prs = [
            PullRequest(self.repo_owner, self.repo_name, **pr) for pr in data
        ]
        if author is not None:
            prs = [pr for pr in prs if pr.author == author]
        return prs

    def pull(self, number):
        """Get a specific PullRequest object with the given number."""
        u = self.url_create("pulls", number)
        pr = self.get(u)
        return PullRequest(self.repo_owner,
                           self.repo_name,
                           **pr) if pr else None

    def issues(self, state="open", author=None, assignee="*", pr_issues=True):
        """Get the list of repository issues. Note that every pull request
        is an issue, but not all issues are pull requests. By default,
        the Github API will return both types. To fetch only issues that
        are not pull requests, pr_issues should be set to False.
        To get unassigned issues, pass None as the assignee value.
        """
        params = [
            "state={0}".format(state),
            "assignee={0}".format("none" if assignee is None else assignee)
        ]
        if author:
            params.append("creator={0}".format(author))

        params = "&".join(params)
        u = self.url_create("issues?{0}".format(params))

        data = self.get(u) or []

        instances = []
        for d in data:
            if pr_issues or "pull_request" not in d:
                instances.append(Issue(self.repo_owner, self.repo_name, **d))
        return instances

    def issue(self, number):
        """Get the issue with the given number."""
        u = self.url_create("issues", number)
        iss = self.get(u)
        return Issue(self.repo_owner, self.repo_name, **iss) if iss else None

    def branches(self, limit=None):
        """Get a stream of Branch objects for this repository."""
        u = self.url_create("branches")
        branches_ = self.get(u) or []
        for branch in branches_:
            yield self.branch(branch["name"])

    def branch(self, branch_name):
        """Get the given branch for this repository."""
        u = self.url_create("branches", branch_name)
        b = self.get(u)
        return Branch(self.repo_owner, self.repo_name, **b) if b else None

    def commits(self, branch="master"):
        """Get a stream of Commit objects in a given branch
        (master, by default)
        """
        u = self.url_create("commits?sha={0}".format(branch))
        commits_data = self.get(u) or []
        for c in commits_data:
            yield Commit(self.repo_owner, self.repo_name, **c)

    def commit(self, sha):
        """Return a Commit object with the given sha."""
        u = self.url_create("commits", sha)
        c = self.get(u)
        return Commit(self.repo_owner, self.repo_name, **c) if c else None

    def is_collaborator(self, login):
        """Return a boolean indicating if the given login is a
        collaborator on the repository.
        """
        u = self.url_create("collaborators", login, "permission")
        return self.get(u) is not None

    def __repr__(self):
        return self.repo


class Branch(RepoObject):
    def __init__(self, repo_owner, repo_name, **kws):
        super(Branch, self).__init__(repo_owner, repo_name)
        self.name = kws.get("name", "")
        self.head = Commit(self.repo_owner, self.repo_name, **kws["commit"])

    def __repr__(self):
        return self.name


class PullRequest(RepoObject):
    legal_states = ["open", "closed", "all"]

    def __init__(self, repo_owner, repo_name, **kws):
        super(PullRequest, self).__init__(repo_owner, repo_name)
        self.title = kws.get("title", "")
        self.state = kws.get("state")
        self.number = kws.get("number")
        self.author = kws.get("user", {}).get("login", "")
        self.opened_at = kws.get("created_at")
        self.updated_at = kws.get("updated_at")
        self.url = kws.get("html_url")
        self.is_open = (self.state == "open")
        self.closed_at = kws.get("closed_at")
        self._head = None

    def commits(self):
        """Get the list of commits in this pull request in time order.
        Setting chronological to False means that the first item in
        the list will be the latest/head commit.
        """
        u = self.url_create("pulls", self.number, "commits")
        data = self.get(u) or []
        for c in data:
            yield Commit(self.repo_owner, self.repo_name, **c)

    @property
    def head(self):
        """Get the head commit of this pull request."""
        c = None
        for c in self.commits():
            pass
        return c

    def files_changed(self):
        """Return the set of files changed in the commits that comprise
        this pull request.
        """
        changed = set()
        for commit in self.commits():
            changed.update(commit.files)
        return list(changed)

    def __repr__(self):
        return "[{0}:{1}:{2}] {3}".format(self.number,
                                          self.state,
                                          self.author,
                                          self.title)


class Commit(RepoObject):
    def __init__(self, repo_owner, repo_name, **kws):
        super(Commit, self).__init__(repo_owner, repo_name)
        self.sha = kws.get("sha")
        self.author = kws["commit"]["author"]["name"]
        self.author_email = kws["commit"]["author"]["email"]
        self.message = kws["commit"]["message"]
        self.created_at = kws["commit"]["author"]["date"]
        self._files = None

    def statuses(self, context=None):
        """Get all commit statuses, of if context is not None,
        only statuses matching the context.
        """
        u = self.url_create("commits", self.sha, "statuses")
        data = self.get(u)
        commit_statuses = data or []
        statii = []
        for cs in commit_statuses:
            if context in (None, cs["context"]):
                statii.append(
                    CommitStatus(self.repo_owner, self.repo_name, **cs)
                )
        return statii

    @property
    def files_changed(self):
        """Get the list of changed files in this commit."""
        if self._files is None:
            u = self.url_create("commits", self.sha)
            CommitFile = namedtuple("CommitFile", "name additions deletions")

            data = self.get(u) or {"files": []}
            changed = []
            for changed_file in data["files"]:
                name = changed_file["filename"]
                adds = changed_file["additions"]
                dels = changed_file["deletions"]
                changed.append(CommitFile(name, adds, dels))
            self._files = changed
        return self._files

    def create_status(self, status):
        """Post a new status for this commit."""
        u = self.url_create("statuses", self.sha)
        status_code = self.post(u, status.to_dict())
        return status_code

    def __repr__(self):
        return "[{0}:{1}] {2}".format(self.sha, self.author, self.message)


class CommitStatus(RepoObject):
    def __init__(self, repo_owner, repo_name, **kws):
        super(CommitStatus, self).__init__(repo_owner, repo_name)
        self.state = kws.get("state")
        self.context = kws.get("context")
        self.target_url = kws.get("target_url", "")
        self.description = kws.get("description", "")
        creator = kws.get("creator", {})
        self.author = creator.get("login")
        self.created_at = kws.get("created_at")
        self.updated_at = kws.get("updated_at")
        self.commit_sha = os.path.basename(kws.get("url", ""))

    def to_dict(self):
        """Get a dictionary representation of this status that
        can be used for updating the parent commit in the repository.
        """
        return {
            "state": self.state,
            "target_url": self.target_url,
            "description": self.description,
            "context": self.context
        }

    def __eq__(self, other):
        return (
            self.context == other.context and
            self.state == other.state and
            self.target_url == other.target_url and
            self.description == other.description
        )

    def __str__(self):
        m = [
            "Context: {0}".format(self.context),
            "State: {0}".format(self.state),
            "Description: {0}".format(self.description),
            "URL: {0}".format(self.target_url)
        ]
        return ", ".join(m)


class Issue(RepoObject):
    """A repository issue."""

    def __init__(self, repo_owner, repo_name, **kws):
        super(Issue, self).__init__(repo_owner, repo_name)
        assignees = [a.get("login") for a in kws.get("assignees")]
        self.assignees = [a for a in assignees if a]
        self.author = kws.get("user", {}).get("login")
        self.body = kws.get("body")
        self.closed_at = kws.get("closed_at")
        self.created_at = kws.get("created_at")
        self.updated_at = kws.get("updated_at")
        self.number = kws.get("number")
        self.state = kws.get("state")
        self.title = kws.get("title")

    @property
    def comments(self):
        """Get the comments for this issue."""
        u = self.url_create("issues", self.number, "comments")
        data = self.get(u) or []
        return [IssueComment(self.repo_owner,
                             self.repo_name,
                             **ic) for ic in data]

    def __repr__(self):
        return "[{0}:{1}:{2}] {3}".format(self.number,
                                          self.author,
                                          self.state,
                                          self.title)


class IssueComment(RepoObject):
    def __init__(self, repo_owner, repo_name, **kws):
        super(IssueComment, self).__init__(repo_owner, repo_name)
        self.body = kws.get("body", "")
        self.id = kws.get("id")
        self.created_at = kws.get("created_at")
        self.updated_at = kws.get("updated_at")
        self.author = kws.get("user", {}).get("login")
        self.url = kws.get("url")

    def update(self, **kws):
        u = self.url_create("issues", "comments", self.id)
        status_code = self._client.post(u, kws)
        return status_code

    def __repr__(self):
        return "{0}: {1}".format(self.id, self.author)


class Organisation(IGithub):
    def __init__(self, name):
        self.name = name
        self.url_base = "/orgs/{0}".format(name)
        super(Organisation, self).__init__()

    @property
    def teams(self):
        u = self.url_create("teams")
        t = self.get(u)
        return [Team(**tt) for tt in t] if t else None


class Teams(IGithub):
    def __init__(self):
        self.url_base = "/teams"
        super(Teams, self).__init__()

    def find(self, team_id):
        u = self.url_create(team_id)
        t = self.get(u)
        return Team(**t) if t else None


class Team(IGithub):
    def __init__(self, **kws):
        self.name = kws.get("name")
        self.id = kws.get("id")
        self.url_base = "/teams/{0}".format(self.id)
        super(Team, self).__init__()

    @property
    def members(self):
        u = self.url_create("members")
        data = self.get(u) or []
        return [TeamMember(**mm) for mm in data]

    def __contains__(self, username):
        """Is user username in this team?"""
        u = self.url_create("memberships", username)
        return self.get(u) is not None

    def __repr__(self):
        return "{0}({1})".format(self.name, self.id)


class TeamMember(object):
    def __init__(self, **kws):
        self.name = kws.get("login")
        self.id = kws.get("id")

    def __repr__(self):
        return "{0}({1})".format(self.name, self.id)

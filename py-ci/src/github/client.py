from __future__ import print_function
from collections import OrderedDict
import json
import cPickle as pickle
import os
import sys

import requests

from github.util import generateCacheId, trace


class PickledCache(object):
    def __init__(self, filename):
        self.filename = filename
        self.load()

    def update(self, d, serialize=True):
        """Update the in-memory cache and then, if requested
        or if there is a problem, serialize to disk.
        """
        try:
            self.cache.update(d)
            if serialize:
                self.dump()
        except:
            self.dump()

    def load(self):
        message = ""
        try:
            with open(self.filename, "r+") as f:
                self.cache = pickle.load(f)
                return
        except IOError:
            pass
        except EOFError:
            message = "Malformed cache file"
        except pickle.PickleError:
            message = "Could not read commit cache"

        message and print(message, file=sys.stderr)
        self.cache = OrderedDict()

    def dump(self, limit=1000):
        message = ""
        try:
            with open(self.filename, "w") as f:
                pickle.dump(OrderedDict(self.cache.items()[-limit:]), f, 2)
        except IOError:
            message = "Unable to write cache file %s" % self.filename
        except EOFError:
            message = "Malformed cache file %s" % self.filename
        except pickle.PickleError:
            message = "Could not write to cache %s" % self.filename

        print(message, file=sys.stderr)

    def __getitem__(self, key):
        return self.cache.get(key, {})

    def __delitem__(self, key):
        try:
            del self.cache[key]
        except KeyError:
            pass
        finally:
            self.dump()


class GithubCachedClient(object):
    def __init__(self, token, cache, api="https://api.github.com"):
        self.token = token
        self.api = api
        self.cache = cache
        self.cache.load()
        self.printStats()

    def __enter__(self):
        self.cache.load()
        return self

    def __exit__(self, excType, excValue, traceback):
        self.cache.dump()
        self.printStats()
        return False

    def close(self):
        """Dump the cache."""
        self.cache.dump()

    @property
    def cache_name(self):
        return os.path.basename(self.cache.filename)

    @property
    def cache_path(self):
        return self.cache.filename

    @property
    def rate_limiting(self):
        """Get the Github rate limit: requests allowed, left and when
        the quota will be reset.
        """
        url = self.makeURL("/rate_limit")
        response = requests.get(url=url, headers=self.baseHeaders())
        limits = (-1, -1)
        if response.status_code == 200:
            headers = response.headers
            remaining = int(headers.get("X-RateLimit-Remaining", -1))
            limit = int(headers.get("X-RateLimit-Limit", -1))
            limits = (remaining, limit)
        return limits

    def printStats(self):
        print("Github API used %s/%s" % self.rate_limiting, file=sys.stderr)

    def makeURL(self, template, **kwds):
        template = template[1:] if template.startswith('/') else template
        return os.path.join(self.api, template.format(**kwds))

    def baseHeaders(self, stable_api=True):
        stableAPI = "application/vnd.github.v3+json"
        unstableAPI = "application/vnd.github.korra-preview"
        headers = {
            "Accept": stableAPI if stable_api else unstableAPI,
            "Authorization": "token %s" % self.token.strip()
        }
        return headers

    def getHeaders(self, stable_api=True, etag=None, lastModified=None):
        headers = self.baseHeaders(stable_api)
        if etag:
            headers.update({"If-None-Match": etag})
        if lastModified:
            headers.update({"If-Modified-Since": lastModified})
        return headers

    def postHeaders(self, stable_api=True):
        return self.baseHeaders(stable_api)

    @trace
    def post(self, url, data, stable_api=True, **kwds):
        headers = self.postHeaders(stable_api)
        url = self.makeURL(url, **kwds)
        data = json.dumps(data) if type(data) == dict else data
        response = requests.post(url=url, data=data, headers=headers)
        sc = response.status_code
        return sc

    @trace
    def patch(self, url, data, stable_api=True, **kwds):
        headers = self.postHeaders(stable_api)
        url = self.makeURL(url, **kwds)
        data = json.dumps(data) if type(data) == dict else data
        response = requests.patch(url=url, data=data, headers=headers)
        return response.status_code

    @trace
    def get(self, url, stable_api=True, **kwds):
        # If we have a cache getter we use it to obtain an
        # entry in the cachedcache_item etags
        cacheKey = generateCacheId([("url", url)] + kwds.items())
        cacheValue = self.cache[cacheKey]
        headers = self.getHeaders(stable_api,
                                  cacheValue.get("ETag"),
                                  cacheValue.get("Last-Modified"))

        url = self.makeURL(url, **kwds)
        r = requests.get(url=url, headers=headers)

        if r.status_code == 304:
            nextLinkStr = cacheValue.get("Link")
            payload = cacheValue.get("payload")
            if nextLinkStr:
                nextLink = parseLinks(nextLinkStr)
                # if type(cacheValue["payload"]) == list:
                payload = pagination(cacheValue,
                                     nextLink,
                                     self.api,
                                     self,
                                     stable_api)

            return payload

        # If we are here, it means we had some sort of cache miss.
        # Therefore we pop the cacheHash from the cache.
        del self.cache[cacheKey]

        if r.status_code == 404:
            return None

        if r.status_code == 403:
            print("Forbidden", file=sys.stderr)
            return None

        if r.status_code == 200:
            nextLink = r.headers.get("Link")
            payload = r.json()
            cacheValue = {
                "payload": payload,
                "ETag": r.headers.get("ETag"),
                "Last-Modified": r.headers.get("Last-Modified"),
                "Link": nextLink
            }
            self.cache.update({cacheKey: cacheValue})

            if nextLink:  # type(cacheValue["payload"]) == list:
                payload = pagination(cacheValue,
                                     nextLink,
                                     self.api,
                                     self,
                                     stable_api)
            return payload

        # no content
        if r.status_code == 204:
            cacheValue = {
                "payload": None,
                "ETag": r.headers.get("ETag"),
                "Last-Modified": r.headers.get("Last-Modified")
            }
            self.cache.update({cacheKey: cacheValue})
            return None

        print(r.status_code)
        assert(False)


def parseLinks(linkString):
    """Parses the Link header string and gets the url for the next page.
    If the next page is not found, returns None.
    """
    if not linkString:
        return None

    links = linkString.split(",")
    for x in links:
        url, what = x.split(";")
        if what.strip().startswith("rel=\"next\""):
            sanitized = url.strip().strip("<>")
            return sanitized


def pagination(cache_item, nextLink, api, self, stable_api):
    # We should be mindful that, depending on the URL retrieved,
    # this function can return a _large_ number of items in the stream.
    # AliPhysics for example has 36K commits in the master branch.
    for x in cache_item["payload"]:
        yield x

    if nextLink:
        for x in self.get(nextLink.replace(api, ""), stable_api):
            yield x

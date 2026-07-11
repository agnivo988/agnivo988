#!/usr/bin/env python3
"""
Generates a GitHub profile stats SVG: repo count, commit count, star count,
follower count, and total lines of code added/deleted across your repos.

Env vars required:
  GH_TOKEN     - a GitHub Personal Access Token (repo, read:user scopes)
  GH_USERNAME  - the GitHub username to report on (falls back to GITHUB_ACTOR)
"""

import os
import json
import time
from datetime import datetime, timezone

import requests

GRAPHQL_API = "https://api.github.com/graphql"
REST_API = "https://api.github.com"

TOKEN = os.environ["GH_TOKEN"]
USERNAME = os.environ.get("GH_USERNAME") or os.environ["GITHUB_ACTOR"]
HEADERS = {"Authorization": f"bearer {TOKEN}"}

CACHE_DIR = "cache"
CACHE_FILE = os.path.join(CACHE_DIR, f"{USERNAME}.json")


def graphql(query, variables=None):
    resp = requests.post(
        GRAPHQL_API, headers=HEADERS,
        json={"query": query, "variables": variables or {}},
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(data["errors"])
    return data["data"]


def get_user_overview():
    query = """
    query($login: String!) {
      user(login: $login) {
        name
        followers { totalCount }
        repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                      privacy: PUBLIC) {
          totalCount
          nodes { name stargazers { totalCount } }
        }
        repositoriesContributedTo(first: 1, contributionTypes: [COMMIT]) {
          totalCount
        }
        contributionsCollection {
          totalCommitContributions
          restrictedContributionsCount
        }
      }
    }
    """
    data = graphql(query, {"login": USERNAME})["user"]
    repos = data["repositories"]["nodes"]
    stars = sum(r["stargazers"]["totalCount"] for r in repos)
    return {
        "name": data["name"] or USERNAME,
        "followers": data["followers"]["totalCount"],
        "public_repos": data["repositories"]["totalCount"],
        "repos_contributed_to": data["repositoriesContributedTo"]["totalCount"],
        "stars": stars,
        "commits": (
            data["contributionsCollection"]["totalCommitContributions"]
            + data["contributionsCollection"]["restrictedContributionsCount"]
        ),
        "repo_names": [r["name"] for r in repos],
    }


def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f, indent=2)


def fetch_contributor_stats(repo):
    """Fetch the contributors-stats payload for one repo, or None if it's
    unavailable (empty repo, still computing after retries, rate limited,
    or any other non-2xx/empty response)."""
    url = f"{REST_API}/repos/{USERNAME}/{repo}/stats/contributors"
    for _ in range(4):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
        except requests.RequestException as exc:
            print(f"  [{repo}] request failed: {exc}")
            return None

        if r.status_code == 202:
            # GitHub is still computing stats for this repo — wait and retry
            time.sleep(3)
            continue
        if r.status_code == 204 or not r.text.strip():
            # Empty repo / genuinely no content to report
            return None
        if r.status_code == 403 and "rate limit" in r.text.lower():
            print(f"  [{repo}] rate limited, skipping for this run")
            return None
        if not r.ok:
            print(f"  [{repo}] unexpected status {r.status_code}, skipping")
            return None

        try:
            return r.json()
        except ValueError:
            print(f"  [{repo}] non-JSON response, skipping")
            return None

    # Exhausted retries while GitHub was still computing stats
    print(f"  [{repo}] stats still computing after retries, skipping")
    return None


def lines_of_code(repo_names, cache):
    """Sum additions/deletions credited to USERNAME across owned repos,
    using the REST contributor-stats endpoint. Results are cached per repo
    so re-runs don't redo work GitHub has already computed, and a repo that
    can't be read this run falls back to its last cached value."""
    added, deleted = 0, 0
    for repo in repo_names:
        data = fetch_contributor_stats(repo)

        if data is None:
            cached = cache.get(repo, {"added": 0, "deleted": 0})
            added += cached["added"]
            deleted += cached["deleted"]
            continue

        repo_added = repo_deleted = 0
        for contributor in data:
            if contributor.get("author", {}).get("login") != USERNAME:
                continue
            for week in contributor.get("weeks", []):
                repo_added += week.get("a", 0)
                repo_deleted += week.get("d", 0)
        cache[repo] = {"added": repo_added, "deleted": repo_deleted}
        added += repo_added
        deleted += repo_deleted
    return added, deleted


def render_svg(template_path, out_path, stats):
    with open(template_path) as f:
        svg = f.read()
    for key, value in stats.items():
        svg = svg.replace(f"{{{{ {key} }}}}", str(value))
    with open(out_path, "w") as f:
        f.write(svg)


def main():
    overview = get_user_overview()
    cache = load_cache()
    added, deleted = lines_of_code(overview["repo_names"], cache)
    save_cache(cache)

    stats = {
        "handle": USERNAME,
        "repos": overview["public_repos"],
        "contributed": overview["repos_contributed_to"],
        "commits": overview["commits"],
        "stars": overview["stars"],
        "followers": overview["followers"],
        "loc_added": f"{added:,}",
        "loc_deleted": f"{deleted:,}",
        "loc_net": f"{added - deleted:,}",
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        # Bio fields — edit these directly, or set them as env vars / repo
        # variables (Settings → Secrets and variables → Actions → Variables)
        # and read them with os.environ.get(...) instead.
        "editor": os.environ.get("BIO_EDITOR", "VS Code"),
        "languages": os.environ.get("BIO_LANGUAGES", "Python, JavaScript"),
        "interests": os.environ.get("BIO_INTERESTS", "open source, robotics"),
        "location": os.environ.get("BIO_LOCATION", "Earth"),
        "email": os.environ.get("BIO_EMAIL", "you@example.com"),
        "website": os.environ.get("BIO_WEBSITE", "example.com"),
        "linkedin": os.environ.get("BIO_LINKEDIN", "your-linkedin-handle"),
    }

    render_svg("light_mode_template.svg", "light_mode.svg", stats)
    render_svg("dark_mode_template.svg", "dark_mode.svg", stats)
    print("Stats:", json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

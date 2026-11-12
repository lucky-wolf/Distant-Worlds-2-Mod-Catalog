#!/usr/bin/env python3
"""Builds index.json: every DW2 mod release found on GitHub, newest version of each mod.

XL and any other mod released on GitLab: pointers.json "gitlab" lists projects; their newest release
is read from the public GitLab API (no manifest asset needed, the pointer carries the name and
description). GitLab keeps no public download counts, so those entries have downloads null.

Discovery: repositories carrying the topic "dw2-mod", plus the repos listed in pointers.json
("repos"), minus pointers.json "blocked". Each release of those repos may carry a
<tag>.manifest.json asset; releases without one are ignored. Download counts and publish dates
come from GitHub, so nothing here is maintained by hand.

Usage: python build-index.py [--out index.json]   (GITHUB_TOKEN raises the API rate limit)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOPIC = "dw2-mod"
API = "https://api.github.com"


def get(url: str, accept: str = "application/vnd.github+json") -> bytes:
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "dw2-mod-catalog"})
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API):
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def get_json(url: str):
    return json.loads(get(url))


def topic_repos() -> list[str]:
    found: list[str] = []
    page = 1
    while True:
        query = urllib.parse.urlencode({"q": f"topic:{TOPIC}", "per_page": 100, "page": page})
        items = get_json(f"{API}/search/repositories?{query}")["items"]
        found += [item["full_name"] for item in items]
        if len(items) < 100:
            return found
        page += 1


def releases(repo: str) -> list[dict]:
    result: list[dict] = []
    page = 1
    while True:
        batch = get_json(f"{API}/repos/{repo}/releases?per_page=100&page={page}")
        result += batch
        if len(batch) < 100:
            return result
        page += 1


def parse_time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def gitlab_entry(pointer: dict, previous: dict[str, dict], newer_than: str | None) -> dict | None:
    """The newest release of a GitLab project, or None when it is not newer than `newer_than` (the same mod's listing elsewhere,
    so the zip is not fetched for nothing). The zip's hash is computed once per tag and then carried over from the last index."""
    project = pointer["project"]
    encoded = urllib.parse.quote(project, safe="")
    release = get_json(f"https://gitlab.com/api/v4/projects/{encoded}/releases?per_page=1")[0]
    link = next((l for l in release["assets"]["links"] if l["name"].endswith(".zip")), None)
    if not link:
        return None
    if newer_than and parse_time(release["released_at"]) <= parse_time(newer_than):
        return None
    tag = release["tag_name"]
    version = tag.lstrip("v")
    old = previous.get(pointer["id"])
    if old and old.get("tag") == tag and old.get("sha256"):
        size, digest = old["size"], old["sha256"]
    else:
        sha = hashlib.sha256()
        size = 0
        request = urllib.request.Request(link["url"], headers={"User-Agent": "dw2-mod-catalog"})
        with urllib.request.urlopen(request, timeout=300) as response:
            while chunk := response.read(1 << 20):
                sha.update(chunk)
                size += len(chunk)
        digest = sha.hexdigest()
    return {
        "schema": 1,
        "id": pointer["id"],
        "name": pointer["name"],
        "kind": pointer.get("kind", "data"),
        "version": version,
        "tag": tag,
        "author": pointer.get("author", ""),
        "shortDescription": pointer.get("shortDescription", ""),
        "workshopId": pointer.get("workshopId"),
        "minLauncherVersion": pointer.get("minLauncherVersion", ""),
        "dependencies": pointer.get("dependencies", []),
        "zip": link["name"],
        "size": size,
        "sha256": digest,
        "publishedUtc": release["released_at"],
        "repo": f"gitlab.com/{project}",
        "releaseUrl": f"https://gitlab.com/{project}/-/releases/{tag}",
        "downloadUrl": link["url"],
        "publishedAt": release["released_at"],
        "latestDownloads": None,
        "downloads": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="index.json")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    pointers = json.loads((here / "pointers.json").read_text(encoding="utf-8")) if (here / "pointers.json").is_file() else {}
    blocked = {r.lower() for r in pointers.get("blocked", [])}
    repos = sorted({r for r in topic_repos() + pointers.get("repos", []) if r.lower() not in blocked}, key=str.lower)

    mods: dict[tuple[str, str], dict] = {}
    for repo in repos:
        for release in releases(repo):
            if release.get("draft") or release.get("prerelease"):
                continue
            assets = {a["name"]: a for a in release.get("assets", [])}
            manifest_asset = next((a for n, a in assets.items() if n.endswith(".manifest.json")), None)
            if not manifest_asset:
                continue
            try:
                manifest = json.loads(get(manifest_asset["browser_download_url"], "application/octet-stream"))
            except Exception as error:  # a broken manifest hides one release, not the catalog
                print(f"skipped {repo} {release['tag_name']}: {error}", file=sys.stderr)
                continue
            zip_asset = assets.get(manifest.get("zip", ""))
            if not zip_asset:
                continue
            entry = mods.setdefault((repo, manifest["id"]), {"downloads": 0, "latest": None})
            entry["downloads"] += zip_asset["download_count"]
            published = release["published_at"]
            if entry["latest"] is None or published > entry["latest"]["publishedAt"]:
                entry["latest"] = {
                    **manifest,
                    "repo": repo,
                    "releaseUrl": release["html_url"],
                    "downloadUrl": zip_asset["browser_download_url"],
                    "publishedAt": published,
                    "latestDownloads": zip_asset["download_count"],
                }

    out_path = Path(args.out)
    previous: dict[str, dict] = {}
    if out_path.is_file():
        previous = {m["id"]: m for m in json.loads(out_path.read_text(encoding="utf-8")).get("mods", [])}
    # One listing per mod id, whichever host released it most recently (a mod may move between GitHub, GitLab or repos).
    # On equal times the earlier-found listing stays.
    chosen: dict[str, dict] = {}

    def offer(entry: dict) -> None:
        current = chosen.get(entry["id"])
        if current is None or parse_time(entry["publishedAt"]) > parse_time(current["publishedAt"]):
            chosen[entry["id"]] = entry

    for e in mods.values():
        offer({**e["latest"], "downloads": e["downloads"]})
    gitlab_count = 0
    for pointer in pointers.get("gitlab", []):
        gitlab_count += 1
        current = chosen.get(pointer["id"])
        try:
            entry = gitlab_entry(pointer, previous, current["publishedAt"] if current else None)
        except Exception as error:
            print(f"skipped gitlab {pointer.get('project')}: {error}", file=sys.stderr)
            old = previous.get(pointer["id"])  # keep the last good GitLab listing rather than dropping the mod
            entry = old if old and old.get("repo", "").startswith("gitlab.com/") else None
        if entry:
            offer(entry)

    index = {
        "schema": 1,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mods": sorted(chosen.values(), key=lambda m: m["name"].lower()),
    }
    Path(args.out).write_text(json.dumps(index, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(index['mods'])} mod(s) from {len(repos)} GitHub repo(s) and {gitlab_count} GitLab project(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Builds index.json: every DW2 mod release found on GitHub, newest version of each mod.

XL and any other mod released on GitLab: pointers.json "gitlab" lists projects; their newest release
is read from the public GitLab API (no manifest asset needed, the pointer carries the name and
description). GitLab keeps no public download counts, so those entries have downloads and peakDownloads null. `downloads` totals every release of a mod; `peakDownloads` is its most downloaded single release.

Discovery: repositories carrying the topic "dw2-mod", plus the repos listed in pointers.json
("repos"), minus pointers.json "blocked". Each release of those repos may carry a
<tag>.manifest.json asset; releases without one are ignored. Download counts and publish dates
come from GitHub, so nothing here is maintained by hand.

Identity: a mod is its "uid", the UUID the launcher writes into the mod's dw2modlauncher.json where the mod is made or first
published, and copies into the manifest. A release whose manifest has no valid uid or no author is rejected (reported on stderr,
never listed), as is a GitLab pointer without them. The first repo to claim a uid owns it: the index remembers each uid's repo, and a
later release of the same uid from another repo is refused. A deliberate move is approved by a line in pointers.json:
"approved": {"<uid>": "owner/repo"} (a GitLab project is "gitlab.com/group/project"). This is a guard against casual takeover, not
proof of authorship; a signature would be needed for that.

Usage: python build-index.py [--out index.json]   (GITHUB_TOKEN raises the API rate limit)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TOPIC = "dw2-mod"
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
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


def identity_problem(uid: object, author: object) -> str | None:
    """Why a mod cannot be listed, or None: it needs an author and a uid that is a lower-case hyphenated UUID."""
    if not isinstance(uid, str) or not uid.strip():
        return "no uid"
    if not UUID.match(uid.strip().lower()):
        return f"uid {uid!r} is not a UUID"
    if not isinstance(author, str) or not author.strip():
        return "no author"
    return None


def gitlab_entry(pointer: dict, previous: dict[str, dict]) -> dict | None:
    """The newest release of a GitLab project. The zip's hash is computed once per tag and then carried over from the last index."""
    project = pointer["project"]
    encoded = urllib.parse.quote(project, safe="")
    release = get_json(f"https://gitlab.com/api/v4/projects/{encoded}/releases?per_page=1")[0]
    link = next((l for l in release["assets"]["links"] if l["name"].endswith(".zip")), None)
    if not link:
        return None
    tag = release["tag_name"]
    version = tag.lstrip("v")
    old = previous.get(pointer["uid"].strip().lower())
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
        "uid": pointer["uid"].strip().lower(),
        "name": pointer["name"],
        "kinds": pointer["kinds"],
        "tags": [t for t in dict.fromkeys(re.sub(r"\s+", " ", str(x).strip()).lower() for x in pointer.get("tags", [])) if t],
        "version": version,
        "tag": tag,
        "author": pointer["author"].strip(),
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
        "previewUrl": pointer.get("previewUrl"),
        "publishedAt": release["released_at"],
        "latestDownloads": None,
        "downloads": None,
        "peakDownloads": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default="index.json")
    args = parser.parse_args()

    here = Path(__file__).resolve().parent
    pointers = json.loads((here / "pointers.json").read_text(encoding="utf-8")) if (here / "pointers.json").is_file() else {}
    blocked = {r.lower() for r in pointers.get("blocked", [])}
    repos = sorted({r for r in topic_repos() + pointers.get("repos", []) if r.lower() not in blocked}, key=str.lower)

    approved = {k.strip().lower(): v for k, v in pointers.get("approved", {}).items()}

    out_path = Path(args.out)
    previous: dict[str, dict] = {}  # the last index's entries by uid (those that have one)
    if out_path.is_file():
        previous = {m["uid"]: m for m in json.loads(out_path.read_text(encoding="utf-8")).get("mods", []) if m.get("uid")}

    # Every listing found this run: the newest release per (repo, uid) is the entry, the oldest says who claimed the uid first.
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
            problem = identity_problem(manifest.get("uid"), manifest.get("author"))
            if problem:
                print(f"rejected {repo} {release['tag_name']}: {problem}", file=sys.stderr)
                continue
            uid = manifest["uid"].strip().lower()
            zip_asset = assets.get(manifest.get("zip", ""))
            if not zip_asset:
                continue
            entry = mods.setdefault((repo, uid), {"downloads": 0, "peak": 0, "latest": None, "first": release["published_at"]})
            entry["downloads"] += zip_asset["download_count"]
            entry["peak"] = max(entry["peak"], zip_asset["download_count"])
            entry["first"] = min(entry["first"], release["published_at"])
            preview_asset = next((a for n, a in assets.items() if re.search(r"\.preview\.(png|jpe?g)$", n, re.IGNORECASE)), None)
            published = release["published_at"]
            if entry["latest"] is None or published > entry["latest"]["publishedAt"]:
                entry["latest"] = {
                    **manifest,
                    "uid": uid,
                    "repo": repo,
                    "releaseUrl": release["html_url"],
                    "downloadUrl": zip_asset["browser_download_url"],
                    "previewUrl": preview_asset["browser_download_url"] if preview_asset else None,
                    "publishedAt": published,
                    "latestDownloads": zip_asset["download_count"],
                }

    # (listing, when its repo first released this uid)
    offers: list[tuple[dict, str]] = [({**e["latest"], "downloads": e["downloads"], "peakDownloads": e["peak"]}, e["first"]) for e in mods.values()]
    gitlab_count = 0
    for pointer in pointers.get("gitlab", []):
        gitlab_count += 1
        problem = identity_problem(pointer.get("uid"), pointer.get("author"))
        if problem:
            print(f"rejected gitlab {pointer.get('project')}: {problem}", file=sys.stderr)
            continue
        try:
            entry = gitlab_entry(pointer, previous)
        except Exception as error:
            print(f"skipped gitlab {pointer.get('project')}: {error}", file=sys.stderr)
            old = previous.get(pointer["uid"].strip().lower())  # keep the last good GitLab listing rather than dropping the mod
            entry = old if old and old.get("repo", "").startswith("gitlab.com/") else None
        if entry:
            offers.append((entry, entry["publishedAt"]))

    # One listing per uid, from the repo that owns it: the one approved in pointers.json, else the one the last index listed it from,
    # else the one that released it first. Another repo's release of the same uid is refused (and reported), never listed.
    chosen: dict[str, dict] = {}
    by_uid: dict[str, list[tuple[dict, str]]] = {}
    for entry, first in offers:
        by_uid.setdefault(entry["uid"], []).append((entry, first))
    for uid, group in by_uid.items():
        owner = approved.get(uid) or (previous[uid]["repo"] if uid in previous else min(group, key=lambda g: parse_time(g[1]))[0]["repo"])
        mine = [e for e, _ in group if e["repo"].lower() == owner.lower()]
        for e, _ in group:
            if e["repo"].lower() != owner.lower():
                print(f"refused {e['repo']} {e.get('tag')}: uid {uid} belongs to {owner} (approve a move with \"approved\" in pointers.json)", file=sys.stderr)
        if mine:
            chosen[uid] = max(mine, key=lambda e: parse_time(e["publishedAt"]))
        elif uid in previous:
            chosen[uid] = previous[uid]  # the owning repo gave nothing this run (an outage, say): keep its last listing

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

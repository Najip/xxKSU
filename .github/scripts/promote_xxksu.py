#!/usr/bin/env python3
"""Promote the newest xxKSU pre-release that built on the A32 kernel.

The A32 kernel (KERNEL_REPO) builds the newest xxKSU release. Its releases
carry a sources json for the xxKSU kernel (source "aigis-xxksu") with the
xxKSU commit used. When the newest xxKSU failed there, that build used the
last good xxKSU instead and records the failed one in ksu.fallback_from.

  promote_xxksu.py [--dry-run] [--tag TAG]

  - newest xxKSU pre-release (newer than the Latest release) that an A32
    release built without falling back -> the stable branch is moved to its
    commit and the release becomes the Latest release
  - an xxKSU pre-release the A32 build had to fall back from -> listed in
    failed (the workflow opens an issue)
  --tag TAG  promote that release by hand (no A32 check)

Writes promoted=<tag>, failed=<json list> to $GITHUB_OUTPUT.
"""
import argparse
import json
import os
import re
import subprocess
import sys

REPO = os.environ.get("GITHUB_REPOSITORY", "Najip/xxKSU")
KERNEL_REPO = os.environ.get("KERNEL_REPO", "Najip/KernelSU_SUSFS_Samsung_SM-A325X")
STABLE = os.environ.get("STABLE", "sync-mojito")
SOURCE = "aigis-xxksu"


def gh(*args, token=None):
    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
    res = subprocess.run(["gh", *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    if res.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)}: {res.stderr.strip()}")
    return res.stdout


def api(path):
    return json.loads(gh("api", path))


def tag_commits(repo):
    """tag -> commit, from git ls-remote (peeled for annotated tags)."""
    out = subprocess.check_output(["git", "ls-remote", "--tags", f"https://github.com/{repo}"], text=True)
    tags = {}
    for line in out.splitlines():
        sha, ref = line.split("\t")
        name = ref.removeprefix("refs/tags/")
        if name.endswith("^{}"):
            tags[name[:-3]] = sha
        else:
            tags.setdefault(name, sha)
    return tags


def xxksu_builds():
    """A32 releases, newest first -> list of (release, ksu commit, fallback_from commit)."""
    out = []
    for rel in api(f"repos/{KERNEL_REPO}/releases?per_page=30"):
        if rel.get("draft"):
            continue
        for asset in rel.get("assets", []):
            if not re.fullmatch(r"sources(-[\w-]+)?\.json", asset["name"]):
                continue
            try:
                data = json.loads(gh("api", asset["url"], "-H", "Accept: application/octet-stream"))
            except (RuntimeError, json.JSONDecodeError):
                continue
            if data.get("source") != SOURCE:
                continue
            ksu = data.get("ksu") or {}
            fb = (ksu.get("fallback_from") or {}).get("commit", "")
            out.append((rel, ksu.get("commit", ""), fb))
    return out


def write_outputs(pairs):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in pairs.items():
                f.write(f"{k}={v}\n")


def promote(tag, sha, dry):
    token = os.environ.get("XXKSU_PUSH_TOKEN") or None
    print(f"Promote {tag} ({sha[:12]}) -> {STABLE}")
    if dry:
        return
    if not token:
        # The rebased commits change .github/workflows; the default token may not move a branch to them.
        print("::warning::XXKSU_PUSH_TOKEN is not set; trying with the default token.")
    gh("api", "-X", "PATCH", f"repos/{REPO}/git/refs/heads/{STABLE}", "-f", f"sha={sha}", "-F", "force=true",
       token=token)
    gh("release", "edit", tag, "--repo", REPO, "--prerelease=false", "--latest")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    tags = tag_commits(REPO)
    rels = [r for r in api(f"repos/{REPO}/releases?per_page=50") if not r.get("draft")]
    latest = next((r for r in rels if not r["prerelease"]), None)
    stable_sha = api(f"repos/{REPO}/git/ref/heads/{STABLE}")["object"]["sha"]

    if args.tag:
        rel = next((r for r in rels if r["tag_name"] == args.tag), None)
        if not rel or args.tag not in tags:
            sys.exit(f"No release {args.tag}")
        promote(args.tag, tags[args.tag], args.dry_run)
        write_outputs({"promoted": args.tag, "failed": "[]"})
        return

    # Pre-releases newer than the Latest release, newest first.
    newer = [r for r in rels if r["prerelease"] and r["tag_name"] in tags
             and (not latest or r["created_at"] > latest["created_at"])]
    by_commit = {tags[r["tag_name"]]: r for r in newer}
    print(f"Latest release: {latest['tag_name'] if latest else 'none'}; {STABLE} at {stable_sha[:12]}; "
          f"{len(newer)} newer pre-release(s)")
    if not newer:
        write_outputs({"promoted": "", "failed": "[]"})
        return

    good, failed = None, []
    for krel, commit, fallback_from in xxksu_builds():
        if fallback_from in by_commit:
            r = by_commit[fallback_from]
            if all(f["tag"] != r["tag_name"] for f in failed):
                failed.append({"tag": r["tag_name"], "commit": fallback_from, "kernel": krel["html_url"],
                               "kernel_tag": krel["tag_name"]})
        elif commit in by_commit and not fallback_from:
            r = by_commit[commit]
            if good is None or r["created_at"] > good[0]["created_at"]:
                good = (r, commit, krel)
    if good:
        r, commit, krel = good
        print(f"Built on the A32 in {krel['tag_name']}: {krel['html_url']}")
        if commit != stable_sha:
            promote(r["tag_name"], commit, args.dry_run)
        elif not args.dry_run:
            gh("release", "edit", r["tag_name"], "--repo", REPO, "--prerelease=false", "--latest")
    for f in failed:
        print(f"Failed on the A32 (fell back): {f['tag']} in {f['kernel']}")
    write_outputs({"promoted": good[0]["tag_name"] if good else "",
                   "kernel_release": good[2]["html_url"] if good else "",
                   "failed": json.dumps(failed, separators=(",", ":"))})


if __name__ == "__main__":
    main()

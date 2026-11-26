#!/usr/bin/env python3
"""Keep xxKSU on top of tiann/KernelSU, the way backslashxx did.

Two sources:
  1. tiann/KernelSU (main): the base. When it has new commits, every xxKSU
     commit on this branch is rebased onto it. The upstream commits keep their
     own SHAs, authors and committers; the rebased commits keep their authors.
  2. ximi-mojito/android_kernel_xiaomi_mojito (personal, drivers/kernelsu):
     where backslashxx still publishes kernel-side xxKSU changes. A changed
     drivers/kernelsu is stored as a snapshot on the mojito-kernelsu branch,
     and the change since the previous snapshot is applied on top as one
     commit credited to the mojito commit's author (drivers/kernelsu -> kernel).

Nothing is pushed here: the workflow pushes when this exits cleanly.
Conflicts are reported (issue body file) and the branch is left as it was.

No state file: the upstream base is the merge-base with tiann/KernelSU, and
the last mojito state is the newest snapshot on mojito-kernelsu.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

UPSTREAM = "tiann/KernelSU"
UPSTREAM_BRANCH = "main"
MOJITO = "ximi-mojito/android_kernel_xiaomi_mojito"
MOJITO_BRANCH = "personal"
MOJITO_DIR = "drivers/kernelsu"
GONE = "backslashxx/KernelSU"


def git(*args, check=True, capture=True):
    res = subprocess.run(["git", *args], text=True,
                         stdout=subprocess.PIPE if capture else None,
                         stderr=subprocess.PIPE if capture else None)
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{res.stderr}")
    return res.stdout.strip() if capture else ""


def gh_api(path, accept=None):
    cmd = ["gh", "api", path]
    if accept:
        cmd += ["-H", f"Accept: {accept}"]
    res = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        m = re.search(r"HTTP (\d{3})", res.stderr)
        return int(m.group(1)) if m else 500, None
    return 200, res.stdout if accept else json.loads(res.stdout)


def short(sha):
    return sha[:8]


# ---------- 1. tiann/KernelSU ----------

def rebase_on_upstream(report):
    git("fetch", "-q", "--tags", os.environ.get("XXKSU_UPSTREAM_URL", f"https://github.com/{UPSTREAM}.git"),
        f"+refs/heads/{UPSTREAM_BRANCH}:refs/remotes/upstream/{UPSTREAM_BRANCH}")
    up = git("rev-parse", f"upstream/{UPSTREAM_BRANCH}")
    base = git("merge-base", "HEAD", up)
    if base == up:
        report["upstream"] = {"new": 0, "head": up}
        return False
    new = git("log", "--format=%H%x09%an%x09%s", f"{base}..{up}").splitlines()
    stack = int(git("rev-list", "--count", f"{base}..HEAD"))
    old_head = git("rev-parse", "HEAD")
    res = subprocess.run(["git", "rebase", "-q", "--onto", up, base],
                         text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        stopped = git("log", "-1", "--format=%h %an: %s", "REBASE_HEAD", check=False)
        files = git("diff", "--name-only", "--diff-filter=U", check=False).splitlines()
        subprocess.run(["git", "rebase", "--abort"])
        git("reset", "-q", "--hard", old_head)
        report["conflicts"].append({
            "what": f"Rebasing {stack} xxKSU commits onto {UPSTREAM}@{short(up)}",
            "commit": stopped, "files": files,
        })
        report["upstream"] = {"new": len(new), "head": up, "base": base, "failed": True}
        return False
    report["upstream"] = {"new": len(new), "head": up, "base": base, "stack": stack,
                          "commits": [c.split("\t", 2) for c in new]}
    return True


# ---------- 2. mojito drivers/kernelsu ----------
# backslashxx keeps xxKSU in the mojito tree as one "KernelSU: import <ver>"
# commit and force-pushes that branch, so its SHAs change on every rebase.
# Each new drivers/kernelsu is therefore stored as a snapshot (kernel/ only)
# on SNAP_BRANCH here, and only the change between the previous snapshot and
# the new one is applied (three-way) on top of this branch.

SNAP_BRANCH = "mojito-kernelsu"
IDENT = ["-c", "user.name=Najip Ali Munajat",
         "-c", "user.email=8416702+Najip@users.noreply.github.com"]


def mojito_head():
    """(commit, drivers/kernelsu tree sha, subject, "Name <email>") of mojito now."""
    code, data = gh_api(f"repos/{MOJITO}/commits?sha={MOJITO_BRANCH}&path={MOJITO_DIR}&per_page=1")
    if code != 200 or not data:
        raise RuntimeError(f"Could not read {MOJITO}@{MOJITO_BRANCH} (HTTP {code})")
    c = data[0]
    parent, name = MOJITO_DIR.rsplit("/", 1)
    code, items = gh_api(f"repos/{MOJITO}/contents/{parent}?ref={c['sha']}")
    if code != 200:
        raise RuntimeError(f"Could not list {parent} in {MOJITO}@{short(c['sha'])} (HTTP {code})")
    tree = next((i["sha"] for i in items if i["name"] == name and i["type"] == "dir"), "")
    a = c["commit"]["author"]
    return c["sha"], tree, c["commit"]["message"].splitlines()[0], f"{a['name']} <{a['email']}>"


def snapshot_commit(sha, subject, author, parent, base=""):
    """Commit mojito's drivers/kernelsu at `sha` as kernel/ on SNAP_BRANCH."""
    with tempfile.TemporaryDirectory(prefix="mojito-") as tmp:
        src = os.path.join(tmp, "src")
        subprocess.run(["git", "init", "-q", src], check=True)
        for cmd in (["remote", "add", "origin", f"https://github.com/{MOJITO}.git"],
                    ["sparse-checkout", "set", "--no-cone", f"/{MOJITO_DIR}/"],
                    ["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", sha],
                    ["checkout", "-q", "FETCH_HEAD"]):
            subprocess.run(["git", "-C", src, *cmd], check=True)
        stage = os.path.join(tmp, "stage")
        os.makedirs(stage)
        os.rename(os.path.join(src, MOJITO_DIR), os.path.join(stage, "kernel"))
        env = {**os.environ, "GIT_INDEX_FILE": os.path.join(tmp, "index")}
        gd = git("rev-parse", "--absolute-git-dir")
        subprocess.run(["git", f"--git-dir={gd}", f"--work-tree={stage}", "add", "-A", "kernel"],
                       env=env, check=True)
        tree = subprocess.run(["git", f"--git-dir={gd}", "write-tree"], env=env, check=True,
                              text=True, stdout=subprocess.PIPE).stdout.strip()
    name, email = re.match(r"(.*) <(.*)>", author).groups()
    env = {**os.environ, "GIT_AUTHOR_NAME": name, "GIT_AUTHOR_EMAIL": email}
    msg = f"{subject}\n\nSnapshot of {MOJITO_DIR} from https://github.com/{MOJITO}/commit/{sha}\n"
    if base:
        # The tiann/KernelSU commit this branch was on when the snapshot was taken:
        # tiann changes after it explain differences from this snapshot (drift report).
        msg += f"Tiann-Base: {base}\n"
    args = ["commit-tree", tree, "-m", msg] + (["-p", parent] if parent else [])
    new = subprocess.run(["git", *IDENT, *args], env=env, check=True, text=True,
                         stdout=subprocess.PIPE).stdout.strip()
    git("update-ref", f"refs/heads/{SNAP_BRANCH}", new)
    return new


def apply_mojito(report, bootstrap=False, accept=False):
    sha, tree, subject, author = mojito_head()
    url = f"https://github.com/{MOJITO}/commit/{sha}"
    git("fetch", "-q", "origin", f"+refs/heads/{SNAP_BRANCH}:refs/heads/{SNAP_BRANCH}", check=False)
    prev = git("rev-parse", "-q", "--verify", f"refs/heads/{SNAP_BRANCH}", check=False)
    prev_tree = git("rev-parse", f"{prev}:kernel", check=False) if prev else ""
    report["mojito"] = {"head": sha, "tree": tree, "applied": [], "skipped": []}
    if prev_tree == tree:
        return 0
    if not prev and not bootstrap:
        raise RuntimeError(f"No {SNAP_BRANCH} branch yet; run once with --bootstrap-mojito.")
    base = git("merge-base", "HEAD", f"upstream/{UPSTREAM_BRANCH}", check=False)
    new = snapshot_commit(sha, subject, author, prev, base)
    report["mojito"]["snapshot"] = new
    if not prev or accept:
        why = "first snapshot (baseline only)" if not prev else "marked as applied by hand (--mojito-accept)"
        report["mojito"]["skipped"].append({"sha": sha, "subject": subject, "why": why})
        return 0
    diff = subprocess.run(["git", "diff", "--binary", prev, new, "--", "kernel"], check=True,
                          text=True, stdout=subprocess.PIPE).stdout  # not stripped: patches need it
    if not diff.strip():
        return 0
    res = subprocess.run(["git", "apply", "--3way", "--index", "--whitespace=nowarn"],
                         input=diff, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if res.returncode != 0:
        files = git("diff", "--name-only", "--diff-filter=U", check=False).splitlines()
        git("reset", "-q", "--hard", "HEAD")
        git("update-ref", f"refs/heads/{SNAP_BRANCH}", prev)  # retry the same snapshot next time
        report["conflicts"].append({"what": f"Applying {url} ({subject})",
                                    "commit": f"{short(sha)} {author}: {subject}",
                                    "files": files or [res.stderr.strip()[:400]]})
        return 0
    if not git("diff", "--cached", "--name-only"):
        report["mojito"]["skipped"].append({"sha": sha, "subject": subject, "why": "already in the tree"})
        return 0
    name, email = re.match(r"(.*) <(.*)>", author).groups()
    msg = f"kernel: sync {MOJITO}@{short(sha)} ({subject})\n\nSynced from {url}\n"
    git(*IDENT, "commit", "-q", f"--author={name} <{email}>", "-m", msg)
    report["mojito"]["applied"].append({"sha": sha, "subject": subject, "author": name})
    return 1


# ---------- drift report ----------
# How far kernel/ here is from backslashxx's newest mojito drivers/kernelsu.
# Each differing file is explained by tiann (changed upstream after the
# snapshot's Tiann-Base), local (changed by one of OWN_AUTHORS' commits on top
# of tiann), or unexplained (worth a look).

OWN_AUTHORS = ("8416702+Najip@users.noreply.github.com", "najipmunajat22@gmail.com")


def drift(report):
    snap = git("rev-parse", "-q", "--verify", f"refs/heads/{SNAP_BRANCH}", check=False)
    if not snap:
        return
    files = git("diff", "--name-only", snap, "HEAD", "--", "kernel").splitlines()
    m = re.search(r"^Tiann-Base: ([0-9a-f]{40})", git("log", "-1", "--format=%B", snap), re.M)
    base = m.group(1) if m else ""
    up = f"upstream/{UPSTREAM_BRANCH}"
    by_tiann = set(git("diff", "--name-only", base, up, "--", "kernel").splitlines()) if base else set()
    own = set()
    merge_base = git("merge-base", "HEAD", up, check=False)
    for email in OWN_AUTHORS:
        own |= set(git("log", "--format=", "--name-only", f"--author={email}",
                       f"{merge_base}..HEAD", "--", "kernel").splitlines())
    out = {"tiann": [], "local": [], "unexplained": []}
    for f in files:
        out["tiann" if f in by_tiann else "local" if f in own else "unexplained"].append(f)
    report["drift"] = {"snapshot": snap, "mojito": report.get("mojito", {}).get("head", ""),
                       "base": base, "files": len(files), **out}


def drift_lines(report):
    d = report.get("drift")
    if not d:
        return []
    mj = d["mojito"]
    ref = f"[`{short(mj)}`](https://github.com/{MOJITO}/commit/{mj})" if mj else "the newest snapshot"
    if not d["files"]:
        return ["", f"Drift from backslashxx's newest mojito kernel ({ref}): none, `kernel/` is identical."]
    lines = ["", f"Drift from backslashxx's newest mojito kernel ({ref}): {d['files']} file(s) differ in `kernel/`."]
    for key, label in (("tiann", "newer tiann/KernelSU changes"), ("local", "changed here on purpose"),
                       ("unexplained", "**unexplained, worth a look**")):
        if d[key]:
            lines.append(f"- {label}: " + ", ".join(f"`{f}`" for f in d[key][:20])
                         + (f" and {len(d[key]) - 20} more" if len(d[key]) > 20 else ""))
    return lines


# ---------- report ----------

def backslashxx_online():
    code, data = gh_api(f"repos/{GONE}")
    return code == 200 and isinstance(data, dict) and not data.get("archived")


def notes(report, tag):
    up = report.get("upstream", {})
    mj = report.get("mojito", {})
    lines = []
    if up.get("commits"):
        lines.append(f"Rebased {up['stack']} xxKSU commits onto [{UPSTREAM}@{short(up['head'])}]"
                     f"(https://github.com/{UPSTREAM}/commit/{up['head']}) "
                     f"({up['new']} new upstream commits, "
                     f"[compare](https://github.com/{UPSTREAM}/compare/{short(up['base'])}...{short(up['head'])})):")
        for sha, author, subject in up["commits"][:30]:
            lines.append(f"- [`{short(sha)}`](https://github.com/{UPSTREAM}/commit/{sha}) {subject} ({author})")
        if len(up["commits"]) > 30:
            lines.append(f"- ...and {len(up['commits']) - 30} more")
    else:
        lines.append(f"Based on [{UPSTREAM}@{short(up.get('head', ''))}]"
                     f"(https://github.com/{UPSTREAM}/commit/{up.get('head', '')}).")
    if mj.get("applied"):
        lines += ["", f"Applied from [{MOJITO}](https://github.com/{MOJITO}/tree/{MOJITO_BRANCH}) "
                      f"(`{MOJITO_DIR}` → `kernel/`):"]
        for a in mj["applied"]:
            lines.append(f"- [`{short(a['sha'])}`](https://github.com/{MOJITO}/commit/{a['sha']}) "
                         f"{a['subject']} ({a['author']})")
    for s in mj.get("skipped", []):
        lines.append(f"- Skipped [`{short(s['sha'])}`](https://github.com/{MOJITO}/commit/{s['sha']}): {s['why']}")
    lines += drift_lines(report)
    if report["conflicts"]:
        lines += ["", "Not included (conflict, see the open issue):"]
        lines += [f"- {c['what']}" for c in report["conflicts"]]
    return "\n".join(lines) + "\n"


def issue_body(report):
    lines = ["The daily xxKSU sync stopped on a conflict. The branch was not changed for this part.", ""]
    for c in report["conflicts"]:
        lines += [f"### {c['what']}", f"Stopped at: `{c['commit']}`", ""]
        lines += [f"- `{f}`" for f in c["files"]] or ["- (no file list)"]
        lines.append("")
    lines += ["Fix it by hand on a local clone and push. A rebase conflict: rebase this branch onto "
              f"`{UPSTREAM}` `{UPSTREAM_BRANCH}` yourself. A mojito conflict: apply the change yourself, then run "
              "the Sync xxKSU workflow once with **mojito_accept** ticked so that snapshot is not applied again.",
              "", "Until then the daily run retries and comments here."]
    return "\n".join(lines) + "\n"


def tag_name(head):
    t = git("describe", "--tags", "--abbrev=0", f"upstream/{UPSTREAM_BRANCH}", check=False) or "v3.3.1"
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"{t}-xx-{day}-{short(head)}"


def write_outputs(pairs):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in pairs.items():
                f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-release", action="store_true")
    ap.add_argument("--bootstrap-mojito", action="store_true",
                    help=f"first run: store the current mojito drivers/kernelsu as the {SNAP_BRANCH} baseline")
    ap.add_argument("--mojito-accept", action="store_true",
                    help="store the new mojito snapshot without applying it (after fixing a conflict by hand)")
    ap.add_argument("--out-dir", default=tempfile.gettempdir())
    args = ap.parse_args()

    report = {"conflicts": []}
    old_head = git("rev-parse", "HEAD")
    rebased = rebase_on_upstream(report)
    try:
        applied = apply_mojito(report, args.bootstrap_mojito, args.mojito_accept)
    except RuntimeError as exc:
        report["conflicts"].append({"what": "Checking the mojito branch", "commit": str(exc), "files": []})
        applied = 0
    head = git("rev-parse", "HEAD")
    changed = head != old_head
    drift(report)

    tag = tag_name(head) if (changed or args.force_release) else ""
    notes_file = os.path.join(args.out_dir, "xxksu-release-notes.md")
    issue_file = os.path.join(args.out_dir, "xxksu-conflict-issue.md")
    with open(notes_file, "w", encoding="utf-8") as f:
        f.write(notes(report, tag))
    if report["conflicts"]:
        with open(issue_file, "w", encoding="utf-8") as f:
            f.write(issue_body(report))

    outs = {
        "old_head": old_head, "head": head,
        "changed": str(changed).lower(), "rebased": str(rebased).lower(),
        "mojito_applied": str(applied),
        "conflict": str(bool(report["conflicts"])).lower(),
        "snapshot": report.get("mojito", {}).get("snapshot", ""),
        "release_tag": tag, "notes_file": notes_file, "issue_file": issue_file,
        "backslashxx_online": str(backslashxx_online()).lower(),
        "drift_unexplained": str(len(report.get("drift", {}).get("unexplained", []))),
    }
    write_outputs(outs)
    print(json.dumps({**outs, "report": report}, indent=2))


if __name__ == "__main__":
    sys.exit(main())

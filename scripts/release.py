#!/usr/bin/env python3
"""Maintainer release tool — NOT part of the skill runtime.

Makes cutting a release one atomic motion instead of a manual choreography:

    python3 scripts/release.py v0.9.0            # edit install lines, commit, tag
    python3 scripts/release.py v0.9.0 --dry-run  # show what would change, touch nothing
    python3 scripts/release.py v0.9.0 --push     # also push main + the tag

Steps (all-or-nothing; any check failure aborts before anything is written):
1. Preconditions: valid vX.Y.Z tag name, tag doesn't already exist, clean working
   tree, on branch main.
2. Rewrites every `--branch vX.Y.Z` install line in README.md to the new tag.
3. Warns (does not abort) if CHANGELOG.md has no entry for the new version.
4. Commits the README bump and creates an annotated tag pointing at that commit,
   so the tag's own install text names the tag — by construction, not by sweep.
5. Pushes only with --push; otherwise prints the push command (pushes are
   owner-gated in this project).

CI backstop: .github/workflows/tests.yml re-checks install-line/tag agreement on
every tag push, so a hand-cut tag that skips this script still fails loudly.

Python 3 stdlib only, like everything in scripts/.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")
INSTALL_RE = re.compile(r"--branch v\d+\.\d+\.\d+")
REPO_ROOT = Path(__file__).resolve().parent.parent


def run(*args: str) -> str:
    return subprocess.run(
        args, cwd=REPO_ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def fail(msg: str) -> None:
    print(f"release.py: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tag", help="new release tag, e.g. v0.9.0")
    ap.add_argument("--dry-run", action="store_true", help="show changes, write nothing")
    ap.add_argument("--push", action="store_true", help="push main + tag after tagging")
    args = ap.parse_args()
    tag = args.tag

    if not TAG_RE.match(tag):
        fail(f"tag {tag!r} does not match vX.Y.Z")
    if tag in run("git", "tag", "-l", tag).splitlines():
        fail(f"tag {tag} already exists (published tags never move)")
    if run("git", "status", "--porcelain"):
        fail("working tree not clean — commit or stash first")
    if run("git", "rev-parse", "--abbrev-ref", "HEAD") != "main":
        fail("not on branch main")

    readme = REPO_ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    old_pins = sorted(set(INSTALL_RE.findall(text)))
    if not old_pins:
        fail("no `--branch vX.Y.Z` install line found in README.md")
    new_text, n = INSTALL_RE.subn(f"--branch {tag}", text)
    print(f"README.md: {n} install line(s): {', '.join(old_pins)} -> --branch {tag}")

    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"[{tag[1:]}]" not in changelog:
        print(f"WARNING: CHANGELOG.md has no [{tag[1:]}] entry", file=sys.stderr)

    if args.dry_run:
        print("dry run — nothing written, committed, or tagged")
        return 0

    readme.write_text(new_text, encoding="utf-8")
    run("git", "add", "README.md")
    run(
        "git", "commit", "-m",
        f"release: {tag} — install lines bumped\n\n"
        "Cut by scripts/release.py (bump + commit + tag in one motion).\n\n"
        "Co-Authored-By: Claude <noreply@anthropic.com>",
    )
    run("git", "tag", "-a", tag, "-m", f"{tag} — cut by scripts/release.py")
    print(f"committed {run('git', 'rev-parse', '--short', 'HEAD')} and tagged {tag}")

    if args.push:
        print(run("git", "push", "origin", "main", tag))
        print("pushed main and", tag)
    else:
        print(f"not pushed — run: git push origin main {tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

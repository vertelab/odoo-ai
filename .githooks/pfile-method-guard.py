#!/usr/bin/env python3
"""pfile-method-guard -- a p-file rewrite must not lose methods.

WHY
---
`ai_coworker.p.py` lost methods three times in one week:

  cafff7e5  re-added the p-file from a stale source      48 methods gone
  14e2a9fd  rewrote the file to add heartbeat fields     35 methods gone
            (also dropped the monthly_used_mtokens field, so every budget
            read raised AttributeError -> 500 in every channel)

Each time the file compiled, expanded cleanly and passed pfiles-check. The
gate measures that the output matches the source -- not that the source still
contains what it contained yesterday.

WHAT IT CHECKS
--------------
Every `def` and every `fields.X(` assignment that existed in the p-file at
HEAD must still exist in the working copy. A rewrite may add, rename or move
things; it may not silently drop a name.

Renames are legitimate. They are declared in the commit's own guard file:

    # .pfile-method-guard
    removed ai_coworker.p.py::_token_totals_map   superseded by the SQL
                                                  aggregation in
                                                  _compute_budget_state

The guard file is per-repo and read from the working tree, so a rename is
reviewable in the same diff as the code that does it.

USAGE
-----
    pfile-method-guard.py --repo . --base HEAD [--version 18.0]

Exit 0 = nothing lost. Exit 1 = names disappeared without a declaration.
Exit 2 = bad invocation.

DESIGN NOTES
------------
* Reads the p-file at the base revision with `git show` -- no checkout, no
  stash, no risk to the working tree.
* Only p-files are compared. The expansion is generated and may legitimately
  differ in formatting.
* Names are collected with a regex, not with `ast`: a p-file contains
  `# #if` directives that are valid comments but make the file's structure
  edition-dependent. Parsing it as Python would fail on the branch that is
  not being expanded.
* Class-qualified names (`Class::name`) are used so a method moving between
  classes is caught as a change rather than as a silent move.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# `def name(` and `name = fields.X(` -- the two forms that carry behaviour.
DEF_RE = re.compile(r"^[ \t]*def[ \t]+(?P<name>\w+)", re.MULTILINE)
FIELD_RE = re.compile(
    r"^[ \t]*(?P<name>\w+)[ \t]*=[ \t]*fields\.[A-Za-z_]", re.MULTILINE)
CLASS_RE = re.compile(r"^class[ \t]+(?P<name>\w+)", re.MULTILINE)

#: Comment in the guard file that declares an intended removal.
REMOVED_RE = re.compile(
    r"^[ \t]*removed[ \t]+(?P<path>[^\s:]+)::(?P<name>\w+)", re.MULTILINE)

GUARD_FILE = ".pfile-method-guard"
PFILE_RE = re.compile(r"\.p\.(py|xml|js|csv|sh|html|htm|kpf|xul)$")


def pfiles_at(repo, base):
    """Every p-file path at `base`, relative to the repo root."""
    proc = subprocess.run(
        ["git", "-C", repo, "ls-tree", "-r", "--name-only", base],
        capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit("pfile-method-guard: cannot read %s: %s"
                 % (base, proc.stderr.strip()[:200]))
    return [p for p in proc.stdout.splitlines() if PFILE_RE.search(p)]


def read_at(repo, base, path):
    proc = subprocess.run(
        ["git", "-C", repo, "show", "%s:%s" % (base, path)],
        capture_output=True, text=True)
    return proc.stdout if proc.returncode == 0 else None


def names_in(text):
    """{Class::name} for every def and every fields.X assignment."""
    out = set()
    current_class = ""
    # Walk the file once, tracking the most recent `class` line: a method
    # belongs to the class above it. Good enough for Odoo models, where a
    # class body is not nested.
    marks = [(m.start(), "class", m.group("name"))
             for m in CLASS_RE.finditer(text)]
    marks += [(m.start(), "def", m.group("name"))
              for m in DEF_RE.finditer(text)]
    marks += [(m.start(), "field", m.group("name"))
              for m in FIELD_RE.finditer(text)]
    marks.sort()
    for _pos, kind, name in marks:
        if kind == "class":
            current_class = name
            continue
        out.add("%s::%s" % (current_class, name) if current_class else name)
    return out


def declared_removals(repo):
    """{path::name} declared in .pfile-method-guard."""
    path = os.path.join(repo, GUARD_FILE)
    if not os.path.isfile(path):
        return set()
    with open(path, encoding="utf-8") as fh:
        return {"%s::%s" % (m.group("path"), m.group("name"))
                for m in REMOVED_RE.finditer(fh.read())}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Fail when a p-file rewrite drops a method or a field.")
    ap.add_argument("--repo", default=".",
                    help="repository root (default: .)")
    ap.add_argument("--base", default="HEAD",
                    help="revision to compare against (default: HEAD)")
    ap.add_argument("--quiet", action="store_true",
                    help="only print the losses")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    if not os.path.isdir(os.path.join(repo, ".git")):
        sys.exit("pfile-method-guard: %s is not a git repository" % repo)

    declared = declared_removals(repo)
    losses = []
    checked = 0

    for path in pfiles_at(repo, args.base):
        before = read_at(repo, args.base, path)
        if before is None:
            continue
        full = os.path.join(repo, path)
        if not os.path.isfile(full):
            # The file itself is gone: every name in it is gone. That is a
            # deletion, and it must be declared like any other removal.
            for name in sorted(names_in(before)):
                key = "%s::%s" % (path, name.split("::")[-1])
                if key not in declared:
                    losses.append((path, name, "file removed"))
            continue
        with open(full, encoding="utf-8") as fh:
            after = fh.read()
        checked += 1
        lost = names_in(before) - names_in(after)
        for name in sorted(lost):
            key = "%s::%s" % (path, name.split("::")[-1])
            if key in declared:
                continue
            losses.append((path, name, "no longer defined"))

    if losses:
        print("pfile-method-guard: %d name(s) disappeared from p-files"
              % len(losses), file=sys.stderr)
        for path, name, why in losses:
            print("  ✗ %s  %s  (%s)" % (path, name, why), file=sys.stderr)
        print("\nIf the removal is intended, declare it in %s:\n"
              "    removed <p-file>::<name>   <reason>\n" % GUARD_FILE,
              file=sys.stderr)
        return 1

    if not args.quiet:
        print("✓ pfile-method-guard: %d p-file(s) compared against %s, "
              "nothing lost" % (checked, args.base))
    return 0


if __name__ == "__main__":
    sys.exit(main())

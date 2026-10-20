"""Check that a release tag matches the package version.

Usage: check_release_tag.py TAG [--root DIR]
       check_release_tag.py --versions-only [--root DIR]

The tag must be "v" plus the version in pyproject.toml, and
src/pipeprof/__init__.py must carry the same __version__. Exits 1 with a
plain message when they differ, so a wrong tag stops the release before CI.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path


def versions(root: Path) -> tuple[str, str]:
    """The version in pyproject.toml and the __version__ in the package."""
    with (root / "pyproject.toml").open("rb") as f:
        project = str(tomllib.load(f)["project"]["version"])
    init = (root / "src" / "pipeprof" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init, re.MULTILINE)
    if match is None:
        raise SystemExit("src/pipeprof/__init__.py has no __version__ line")
    return project, match.group(1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("tag", nargs="?", help="the pushed tag, for example v1.1.0")
    parser.add_argument(
        "--versions-only", action="store_true", help="only compare the two versions"
    )
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    if not args.versions_only and not args.tag:
        parser.error("give a tag, or --versions-only")

    project, package = versions(args.root)
    if project != package:
        print(
            f"pyproject.toml has version {project} but pipeprof.__version__ is {package}",
            file=sys.stderr,
        )
        return 1
    if args.versions_only:
        print(f"versions agree: {project}")
        return 0
    if args.tag != f"v{project}":
        print(
            f"tag {args.tag} does not match the package version {project}; expected v{project}",
            file=sys.stderr,
        )
        return 1
    print(f"tag {args.tag} matches the package version")
    return 0


if __name__ == "__main__":
    sys.exit(main())
